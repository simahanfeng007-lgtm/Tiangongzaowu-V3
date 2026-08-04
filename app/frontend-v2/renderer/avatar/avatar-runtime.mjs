// P4 AvatarRuntime（方案 §7.1/§11/§14/§18/§19/§20/§23）。
// 组合 P1 契约（LoadAttempt/AvatarRuntimeSlots/fixed-step/contracts）与 P4 模块：
// 资源估算、RenderSurface、VisibilityProbe、SuspensionGuard、BodyRuntimeState、
// Diagnostics、RecoveryController、PendingLoadJournal、Quarantine。
//
// 不变量：
//   N_activeAvatarRuntime = 1（注入 service registry 时登记 "avatar-runtime" 单例，§20.1）
//   N_authoritativeSimulation = 1（pending 只读影子投影不回写，§4.2/§18.1）
//   N_primaryGpuContext <= 1（RenderSurface 单租约，§4.3/§14.1）
//   N_activeRAF = 1（单 RAF 链，重复 selectModel 不增 RAF，§4.4）
//
// ── 引擎适配器鸭子类型（P4 引擎无关接口；ThreeVrmEngine 经薄适配层接入，测试用替身）──
//   engineVersion: string
//   on(event, listener)/off(event, listener)：EngineEvent；
//     FIRST_RENDERABLE_FRAME payload 必须带 attemptId（§19 候选像素按 attemptId 归因）
//   async loadCandidate(bytes, { label, attemptId }) → handle        staging，不呈现
//   async uploadCandidate(handle)                                     GPU 上传（可选）
//   renderCandidateFrame(handle) → { drawCalls }                      离屏首帧（renderability-probe）
//   presentCandidate(handle) / concealCandidate(handle)               provisional-present 上屏/撤下
//   restorePresented(handle)                                          回滚：恢复 rollbackTarget 呈现
//   promoteCandidate(handle)                                          committed 后候选成为正式
//   disposeModel(handle)                                              模型级释放（幂等）
//   discardInvalidatedModel(handle)                                   GPU 失效对象引用级剔除（不触 GL，可选）
//   renderFrame()/update(dtSeconds)/getStats()/isContextLost()/recreateRenderer()
//   attachSurface({ host, leaseId })/detachSurface()（可选）
//   语义命令（可选）：applyPosture/applyExpression/applyGaze/playGesture/setSpeaking/applyVisemeTarget
//
// 时钟纪律：所有时间点一律来自注入的 nowMonotonic（单调毫秒），禁止墙钟。

import {
  AttemptKind,
  LoadAttemptState,
  RuntimeState,
  DEFAULT_MAX_CONTINUOUS_SUSPENDED_MS,
  DEFAULT_MAX_CUMULATIVE_SUSPENDED_MS,
  PHYSICS_STEPS_DROPPED_METRIC,
  actionIdempotencyKey,
  isScheduledActionExpired,
  scheduleBodyAction,
} from "./contracts.mjs";
import { LoadAttempt } from "./load-attempt.mjs";
import { AvatarRuntimeSlots } from "./runtime-state.mjs";
import { createFixedStepper } from "./fixed-step.mjs";
import { deepFreeze } from "./canonical-hash.mjs";
import { VALIDATOR_VERSION } from "./model-admission-gate.mjs";
import { QuarantineCategory, quarantineKeyForFailure } from "./model-quarantine.mjs";
import {
  DEFAULT_RESOURCE_ESTIMATE_PARAMS,
  computeStructuralStats,
  estimateModelResources,
  evaluateSwitchBudget,
} from "./model-resource-estimator.mjs";
import { createRenderSurfaceController } from "./render-surface-controller.mjs";
import { createVisibilityProbeSession } from "./visibility-probe.mjs";
import { createSuspensionGuard } from "./suspension-guard.mjs";
import {
  assembleMigrationSnapshot,
  createBodyRuntimeState,
  createTransitionActionBuffer,
} from "./body-runtime-state.mjs";
import { DiagnosticEvent, createDiagnostics } from "./diagnostics.mjs";
import { createRecoveryController } from "./recovery-controller.mjs";
import { EngineEvent } from "./engines/avatar-engine-contract.mjs";

export const AVATAR_RUNTIME_SCHEMA_VERSION = 1;

// §18.5 provisional-present 版本化常量（V2.1.2 初始值）。
export const PROVISIONAL_PRESENT_LIMITS = Object.freeze({
  schemaVersion: 1,
  maxProvisionalPresentMs: 1000, // 硬上限：超过按探针失败处理
  targetProvisionalPresentFrames: 2, // 软性能 SLO：只记录分布，不做逐次硬门
  minGestureResumeMs: 250, // 剩余 TTL 低于此值不续播（§18.5）
});

export const DEFAULT_ACTIVE_PHASE_BUDGET_MS = 30_000;
export const DEFAULT_RESOURCE_BUDGET_BYTES = 512 * 1024 * 1024;
export const AVATAR_RUNTIME_SERVICE_ID = "avatar-runtime";

export class AvatarRuntimeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AvatarRuntimeError";
    this.code = code;
  }
}

function isAvatarUsable(avatar) {
  return avatar !== null && typeof avatar === "object" && avatar.gpuInvalidated !== true;
}

export function createAvatarRuntime({
  engineAdapter,
  assetSource,
  nowMonotonic,
  requestAnimationFrame: rafImpl,
  cancelAnimationFrame: cancelRafImpl,
  registry = null,
  surfaceController = null,
  diagnostics = null,
  quarantineTracker = null,
  journal = null,
  suspensionGuard = null,
  env = {},
  evidenceSource = null,
  validateCandidateHook = null,
  resourceBudgetBytes = DEFAULT_RESOURCE_BUDGET_BYTES,
  estimateParams = DEFAULT_RESOURCE_ESTIMATE_PARAMS,
  provisionalLimits = PROVISIONAL_PRESENT_LIMITS,
  activePhaseBudgetMs = DEFAULT_ACTIVE_PHASE_BUDGET_MS,
  suspensionBudgets = {},
  gpuFingerprint = null,
  safeModelId = "tiangong-z1",
  idGenerator = null,
  ownsEngine = true,
} = {}) {
  if (engineAdapter === null || typeof engineAdapter !== "object") {
    throw new AvatarRuntimeError("engine_invalid", "AvatarRuntime 需要引擎适配器（见模块头鸭子类型）");
  }
  if (assetSource === null || typeof assetSource !== "object" || typeof assetSource.openModelBytes !== "function") {
    throw new AvatarRuntimeError("asset_source_invalid", "AvatarRuntime 需要 assetSource（openModelBytes/describeModel）");
  }
  if (typeof nowMonotonic !== "function") {
    throw new AvatarRuntimeError("clock_required", "AvatarRuntime 需要注入单调时钟 nowMonotonic");
  }
  if (typeof rafImpl !== "function" || typeof cancelRafImpl !== "function") {
    throw new AvatarRuntimeError("raf_required", "AvatarRuntime 需要注入 requestAnimationFrame/cancelAnimationFrame");
  }
  if (!Number.isFinite(activePhaseBudgetMs) || activePhaseBudgetMs <= 0) {
    throw new AvatarRuntimeError("phase_budget_invalid", "activePhaseBudgetMs 必须为正数");
  }
  const maxContinuousSuspendedMs = suspensionBudgets.maxContinuousSuspendedMs ?? DEFAULT_MAX_CONTINUOUS_SUSPENDED_MS;
  const maxCumulativeSuspendedMs = suspensionBudgets.maxCumulativeSuspendedMs ?? DEFAULT_MAX_CUMULATIVE_SUSPENDED_MS;
  const limits = { ...PROVISIONAL_PRESENT_LIMITS, ...(provisionalLimits ?? {}) };

  // ── 组装 ─────────────────────────────────────────────────
  const surface = surfaceController ?? createRenderSurfaceController({ nowMonotonic });
  const diag = diagnostics ?? createDiagnostics({ nowMonotonic });
  const guard = suspensionGuard ?? createSuspensionGuard({ nowMonotonic });
  const bodyState = createBodyRuntimeState({ nowMonotonic });
  const bodyWriter = bodyState.createWriter(); // 唯一写入者（§18.1）
  const currentProjection = bodyState.projectFor("current");
  currentProjection._bindWriter(bodyWriter);
  const pendingProjection = bodyState.projectFor("pending"); // 只读影子，不回写
  const slots = new AvatarRuntimeSlots({ bodyState });
  const stepper = createFixedStepper({
    onMetric: (name, value) => {
      if (name === PHYSICS_STEPS_DROPPED_METRIC) runtimeCounters.physicsStepsDropped += value;
    },
  });
  const runtimeCounters = { physicsStepsDropped: 0 };
  const transitionBuffer = createTransitionActionBuffer({ nowMonotonic });

  const listeners = new Set();
  const attemptRecords = new Map();
  let attemptSeq = 0;
  let rafHandle = null; // N_activeRAF=1：单 RAF 链句柄
  let loopActive = false;
  let paused = false;
  let disposed = false;
  let safeMode = null; // degraded 后的 2D safe mode 标记
  let lastRequestedModelId = null;
  let lastCommittedModelId = null;
  let lastMigrationSnapshot = null;
  let lostAvatar = null; // context-lost 后仅作引用级剔除（不触 GL）
  let presentation = Object.freeze({
    camera: null,
    lighting: null,
    rootTransform: null,
    profile: null,
  });

  const engineVersion = typeof engineAdapter.engineVersion === "string" ? engineAdapter.engineVersion : "unknown-engine";
  const nextAttemptId = () =>
    typeof idGenerator === "function" ? idGenerator() : `att_${(attemptSeq += 1)}`;

  function emitDiag(event, fields = {}) {
    diag.emit(event, { engineVersion, ...fields });
  }

  function snapshot() {
    const current = slots.hasCurrent() ? slots.current : null;
    const pending = slots.pending;
    return deepFreeze({
      state: slots.state,
      current: current
        ? { modelId: current.modelId, label: current.label, contentHash: current.contentHash }
        : null,
      pending: pending
        ? { attemptId: pending.attemptId, state: pending.state, attemptKind: pending.attemptKind }
        : null,
      bodyStateVersion: bodyState.version,
      paused,
      safeMode,
      lastRequestedModelId,
      lastCommittedModelId,
      probe: currentProbe()
        ? { cameraLocked: currentProbe().cameraLocked, userInputPaused: currentProbe().userInputPaused }
        : null,
    });
  }

  function notify() {
    const snap = snapshot();
    for (const listener of [...listeners]) {
      try {
        listener(snap);
      } catch (_error) {
        // 监听器异常不阻断运行时
      }
    }
  }

  function currentProbe() {
    const pending = slots.pending;
    return pending ? (attemptRecords.get(pending.attemptId)?.probe ?? null) : null;
  }

  // ── RAF 单链（§4.4 N_activeRAF=1）─────────────────────────
  function ensureLoop() {
    if (disposed || paused || !loopActive) return;
    if (rafHandle !== null) return; // 已有活动循环，重复 selectModel 不增 RAF
    const tick = () => {
      rafHandle = null;
      if (!loopActive || disposed || paused) return;
      pumpFrame();
      if (loopActive && !disposed && !paused) rafHandle = rafImpl(tick);
    };
    rafHandle = rafImpl(tick);
  }

  function startLoop() {
    if (loopActive) {
      ensureLoop();
      return;
    }
    loopActive = true;
    ensureLoop();
  }

  function stopLoop() {
    loopActive = false;
    if (rafHandle !== null) {
      cancelRafImpl(rafHandle);
      rafHandle = null;
    }
  }

  function pumpFrame() {
    const now = nowMonotonic();
    const pending = slots.pending;
    const record = pending ? attemptRecords.get(pending.attemptId) : null;
    // 1. renderer 恢复调度后先处理主进程 cancel 标记，再渲染/动作（§4.6）。
    if (pending && !pending.isTerminal && pending.isSuspended && guard.hasCancelFlag(pending.attemptId)) {
      guard.consumeCancelFlag({ attemptId: pending.attemptId });
      cancelSuspendedAttempt(record, "suspension-guard-cancel");
    }
    // 2. 权威模拟与渲染：仅 running 且 current 存在（context-lost/recovering 不渲染）。
    if (!paused && slots.state === RuntimeState.RUNNING && slots.hasCurrent()) {
      stepper.advance(now / 1000, (dt) => engineAdapter.update?.(dt));
      engineAdapter.renderFrame?.();
    }
    // 3. pending attempt 探针驱动。
    if (record && !record.attempt.isTerminal) driveAttempt(record, now);
  }

  // ── attempt 记录与探针 ────────────────────────────────────
  function createAttemptRecord(attempt, descriptor) {
    const record = {
      attempt,
      descriptor,
      stage: LoadAttemptState.SELECTING,
      estimate: null,
      budget: null,
      candidateAvatar: null,
      probe: null,
      presented: false,
      provisionalEnteredAt: null,
      provisionalFrames: 0,
      startedAt: nowMonotonic(),
      done: null,
      settle: null,
    };
    record.done = new Promise((resolve) => {
      record.settle = resolve;
    });
    record.probe = createVisibilityProbeSession({
      attempt,
      surface,
      env,
      sampleFrameEvidence: () => ({
        firstFrame: record.presented === true && record.provisionalFrames > 0,
        drawCalls: engineAdapter.getStats?.().drawCalls ?? 0,
        boundsIntersectViewport:
          engineAdapter.candidateBoundsIntersectViewport?.(record.candidateAvatar?.handle) ?? true,
        fatalRendererError: engineAdapter.hasFatalRendererError?.() ?? false,
      }),
    });
    attemptRecords.set(attempt.attemptId, record);
    return record;
  }

  function transition(record, to, reason = null) {
    record.attempt.transition(to, { nowMonotonic: nowMonotonic(), reason });
    record.stage = to;
    notify();
  }

  function createActiveAvatar({ descriptor, handle, estimate }) {
    const avatar = {
      modelId: descriptor.modelId,
      contentHash: descriptor.contentHash,
      label: descriptor.modelId,
      handle,
      estimate,
      gpuInvalidated: false,
      disposed: false,
      dispose() {
        if (avatar.disposed) return;
        avatar.disposed = true;
        engineAdapter.disposeModel?.(handle);
        emitDiag(DiagnosticEvent.MODEL_DISPOSED, { modelId: avatar.modelId, phase: "dispose", result: "disposed" });
      },
    };
    return avatar;
  }

  // ── 加载主链（§18.2）──────────────────────────────────────
  async function runAttempt(record) {
    const { attempt, descriptor } = record;
    const diagFields = () => ({ correlationId: attempt.attemptId, modelId: descriptor.modelId });
    try {
      // selecting → validating
      transition(record, LoadAttemptState.VALIDATING);
      emitDiag(DiagnosticEvent.MODEL_VALIDATE_START, { ...diagFields(), phase: "validating" });
      if (quarantineTracker) {
        // §9.4 加载前隔离预检：structural/engine（+ 有 GPU 指纹时 runtime）三类键全部检查。
        const precheckKeys = [
          quarantineKeyFor(record, QuarantineCategory.STRUCTURAL),
          quarantineKeyFor(record, QuarantineCategory.ENGINE),
          quarantineKeyFor(record, QuarantineCategory.RUNTIME),
        ].filter((key) => key !== null);
        if (precheckKeys.some((key) => quarantineTracker.isQuarantined(key))) {
          attempt.transition(LoadAttemptState.QUARANTINED, {
            nowMonotonic: nowMonotonic(),
            reason: "pre-quarantined",
          });
          await finalizeTerminalAttempt(record);
          return;
        }
      }
      if (validateCandidateHook !== null) {
        const verdict = await validateCandidateHook({ descriptor, attemptId: attempt.attemptId });
        if (verdict?.ok !== true) {
          await rejectAttempt(record, {
            code: verdict?.code ?? "validation-rejected",
            violations: verdict?.violations ?? null,
          });
          return;
        }
      }
      emitDiag(DiagnosticEvent.MODEL_VALIDATE_COMPLETE, { ...diagFields(), phase: "validating", result: "admitted" });
      emitDiag(DiagnosticEvent.MODEL_ADMITTED, { ...diagFields(), phase: "admitted", result: "admitted" });
      transition(record, LoadAttemptState.ADMITTED);

      // admitted → loading：受控字节通道（hash 由 provider 复核，§8.6）
      transition(record, LoadAttemptState.LOADING);
      emitDiag(DiagnosticEvent.ASSET_OPEN_START, { ...diagFields(), phase: "loading" });
      const bytes = await assetSource.openModelBytes({
        modelId: descriptor.modelId,
        contentHash: descriptor.contentHash,
        byteLength: descriptor.byteLength,
        attemptId: attempt.attemptId,
      });
      emitDiag(DiagnosticEvent.ASSET_OPEN_COMPLETE, {
        ...diagFields(),
        phase: "loading",
        result: "ok",
        durationMs: nowMonotonic() - record.startedAt,
      });
      if (attempt.isTerminal) return; // 加载期间被 latest-wins 取代

      // 结构统计 + §11.1 资源估算（估算输入与 ModelAdmissionGate 同源口径）
      const stats = computeStructuralStats(bytes);
      const estimate = estimateModelResources(stats, { params: estimateParams });
      record.estimate = estimate;
      // §4.8/§11.2 有条件事务切换：预算允许才保留旧模型双驻留
      const budget = evaluateSwitchBudget({
        candidate: estimate,
        resident: slots.current?.estimate ?? null,
        resourceBudgetBytes,
        params: estimateParams,
      });
      record.budget = budget;
      if (!budget.allowed && attempt.rollbackTarget) {
        // §11.3 低资源切换：暂停动作 → 释放旧模型 GPU 资源 → 加载新模型；rollbackTarget 失效
        attempt.rollbackTarget = null;
        releaseCurrentForLowResource(record);
      }

      // journal 纪律：进 parsing 前原子写入（§9.4）
      if (journal) {
        await journal.beginPhase({
          attemptId: attempt.attemptId,
          modelId: descriptor.modelId,
          contentHash: descriptor.contentHash,
          engineVersion,
          gpuFingerprint,
          phase: "parsing",
        });
      }
      transition(record, LoadAttemptState.PARSING);
      emitDiag(DiagnosticEvent.VRM_PARSE_START, { ...diagFields(), phase: "parsing" });
      const handle = await engineAdapter.loadCandidate(bytes, {
        label: descriptor.modelId,
        attemptId: attempt.attemptId,
      });
      emitDiag(DiagnosticEvent.VRM_PARSE_COMPLETE, {
        ...diagFields(),
        phase: "parsing",
        result: "ok",
        resourceEstimate: estimate,
      });
      if (attempt.isTerminal) {
        // 解析完成前被取代：candidate 未入槽，直接释放防泄漏
        engineAdapter.disposeModel?.(handle);
        return;
      }
      const candidate = createActiveAvatar({ descriptor, handle, estimate });
      attempt.candidate = candidate;
      record.candidateAvatar = candidate;

      if (journal) {
        await journal.beginPhase({
          attemptId: attempt.attemptId,
          modelId: descriptor.modelId,
          contentHash: descriptor.contentHash,
          engineVersion,
          gpuFingerprint,
          phase: "uploading",
        });
      }
      transition(record, LoadAttemptState.UPLOADING);
      emitDiag(DiagnosticEvent.GPU_UPLOAD_START, { ...diagFields(), phase: "uploading" });
      await engineAdapter.uploadCandidate?.(handle);
      emitDiag(DiagnosticEvent.GPU_UPLOAD_COMPLETE, { ...diagFields(), phase: "uploading", result: "ok" });
      if (attempt.isTerminal) return;

      // uploading → renderability-probe：staging 离屏首帧（§11.2.4）
      transition(record, LoadAttemptState.RENDERABILITY_PROBE);
      const staging = engineAdapter.renderCandidateFrame?.(handle) ?? { drawCalls: 0 };
      if (!(Number(staging.drawCalls) > 0)) {
        throw new AvatarRuntimeError("renderability_probe_failed", "staging 离屏首帧 drawCalls=0");
      }
      if (!record.probe.firstRenderableSeen) {
        throw new AvatarRuntimeError(
          "first_renderable_frame_missing",
          "缺少按 attemptId 归属的 FIRST_RENDERABLE_FRAME（§19.1.6）",
        );
      }

      // renderability-probe → provisional-present：Surface 呈现 pending，旧 current 保留可回滚（§11.2.5）
      transition(record, LoadAttemptState.PROVISIONAL_PRESENT);
      record.provisionalEnteredAt = nowMonotonic();
      record.provisionalFrames = 0;
      engineAdapter.presentCandidate?.(handle);
      record.presented = true;
      emitDiag(DiagnosticEvent.FIRST_FRAME, { ...diagFields(), phase: "provisional-present", result: "presented" });

      // provisional-present → visibility-probe：受控可见性探针开始（§19.1）
      transition(record, LoadAttemptState.VISIBILITY_PROBE);
      const begun = record.probe.begin(nowMonotonic());
      if (begun.status === "precondition-failed") {
        throw new AvatarRuntimeError(
          "probe_precondition_failed",
          `探针前提硬失败: ${begun.hardFailures.join(",")}`,
        );
      }
      // env-blocked 不报错：下一帧 poll 即进入 suspended-probe
      requestPixelEvidence(record);
      // 此后由 RAF pump 驱动 poll → passed/timeout/suspend
    } catch (error) {
      if (attempt.isTerminal) return;
      await failAttempt(record, classifyError(error, record));
    }
  }

  function requestPixelEvidence(record) {
    const source =
      evidenceSource ??
      (async ({ attemptId }) => ({
        attemptId,
        nonBackgroundPixels: (engineAdapter.getStats?.().drawCalls ?? 0) > 0 ? 1 : 0,
      }));
    const startAt = nowMonotonic();
    Promise.resolve(source({ attemptId: record.attempt.attemptId, modelId: record.descriptor.modelId })).then(
      (evidence) => {
        if (record.attempt.isTerminal || record.probe.ended) return;
        // 异步证据延迟单独计 visibilityEvidenceLatencyMs（§18.5：不在同步关键路径强塞 readPixels）
        record.probe.submitPixelEvidence(evidence, { latencyMs: nowMonotonic() - startAt });
      },
      () => {
        // 证据缺失不立即失败：探针持续 probing，由 activePhaseDeadline/挂起预算判定
      },
    );
  }

  function classifyError(error, record) {
    const code = typeof error?.code === "string" ? error.code : "load-failed";
    const stage = record.stage;
    if (stage === LoadAttemptState.PARSING) {
      // 确定性解析失败/解析超时 → engine 键（§9.4）
      return { code, category: QuarantineCategory.ENGINE, retryable: true };
    }
    if (stage === LoadAttemptState.VALIDATING || stage === LoadAttemptState.ADMITTED) {
      return { code, category: QuarantineCategory.STRUCTURAL, retryable: false };
    }
    if (stage === LoadAttemptState.LOADING && /hash|integrity/i.test(code)) {
      return { code, category: QuarantineCategory.STRUCTURAL, retryable: false };
    }
    // loading 其余错误、uploading/renderability/可见性失败、context lost → runtime 键（§9.4）
    return { code, category: QuarantineCategory.RUNTIME, retryable: true };
  }

  function quarantineKeyFor(record, category) {
    const contentHash = record.descriptor.contentHash;
    if (category === QuarantineCategory.STRUCTURAL) {
      return quarantineKeyForFailure(category, { contentHash, validatorVersion: VALIDATOR_VERSION });
    }
    if (category === QuarantineCategory.ENGINE) {
      return quarantineKeyForFailure(category, { contentHash, engineVersion });
    }
    if (gpuFingerprint === null) return null; // 无 GPU 指纹时 runtime 键不可构成，跳过计数（键语义不降级）
    return quarantineKeyForFailure(category, { contentHash, engineVersion, gpuFingerprint });
  }

  // 失败终态：按类别计 quarantine；达到阈值则 QUARANTINED，否则 FAILED（§9.4/§19.4）。
  async function failAttempt(record, { code, category, retryable = true }) {
    const { attempt } = record;
    if (attempt.isTerminal) return;
    record.probe?.end();
    let quarantined = false;
    let quarantineReason = null;
    if (quarantineTracker && category) {
      const key = quarantineKeyFor(record, category);
      if (key !== null) {
        try {
          const result = await quarantineTracker.recordFailure({ key, category, reason: code });
          quarantined = result.quarantined;
          quarantineReason = result.reason;
        } catch (_error) {
          // 隔离计数失败不阻断失败终态判定
        }
      }
    }
    emitDiag(DiagnosticEvent.FIRST_VISIBLE_FRAME, {
      correlationId: attempt.attemptId,
      modelId: record.descriptor.modelId,
      phase: record.stage,
      result: "failed",
      errorCode: code,
      retryable,
      resourceEstimate: record.estimate,
    });
    if (quarantined) {
      attempt.transition(LoadAttemptState.QUARANTINED, { nowMonotonic: nowMonotonic(), reason: quarantineReason ?? code });
      emitDiag(DiagnosticEvent.MODEL_QUARANTINED, {
        correlationId: attempt.attemptId,
        modelId: record.descriptor.modelId,
        phase: record.stage,
        result: "quarantined",
        errorCode: quarantineReason,
        retryable: false,
      });
    } else {
      attempt.transition(LoadAttemptState.FAILED, { nowMonotonic: nowMonotonic(), reason: code });
    }
    await finalizeTerminalAttempt(record);
  }

  async function rejectAttempt(record, { code, violations = null }) {
    const { attempt } = record;
    if (attempt.isTerminal) return;
    record.probe?.end();
    let quarantined = false;
    if (quarantineTracker) {
      // 结构预检失败：单次即隔离（§9.4 条件 1）
      const key = quarantineKeyFor(record, QuarantineCategory.STRUCTURAL);
      if (key !== null) {
        const result = await quarantineTracker.recordFailure({ key, category: QuarantineCategory.STRUCTURAL, reason: code });
        quarantined = result.quarantined;
      }
    }
    attempt.transition(quarantined ? LoadAttemptState.QUARANTINED : LoadAttemptState.REJECTED, {
      nowMonotonic: nowMonotonic(),
      reason: code,
    });
    if (quarantined) {
      emitDiag(DiagnosticEvent.MODEL_QUARANTINED, {
        correlationId: attempt.attemptId,
        modelId: record.descriptor.modelId,
        phase: "validating",
        result: "quarantined",
        errorCode: code,
        retryable: false,
        detail: violations ? { violations } : null,
      });
    }
    await finalizeTerminalAttempt(record);
  }

  // 挂起取消（预算超限或主进程守卫标记）：cancelled 不计失败不计 quarantine（§18.3.9/§19.1）。
  async function cancelSuspendedAttempt(record, reason) {
    const { attempt } = record;
    if (attempt.isTerminal) return;
    const now = nowMonotonic();
    if (attempt.isSuspended) {
      // 先把挂起段并入累计（suspended-probe → visibility-probe 合法迁移），再进 cancelled。
      attempt.transition(LoadAttemptState.VISIBILITY_PROBE, { nowMonotonic: now, reason: "suspension-end" });
    }
    attempt.transition(LoadAttemptState.CANCELLED, { nowMonotonic: now, reason });
    await finalizeTerminalAttempt(record);
  }

  // §18.3.6/§18.5 提交：FIRST_VISIBLE_FRAME 通过 → 单临界区 committed → 释放旧 current。
  async function commitAttempt(record, now) {
    const { attempt, candidateAvatar, descriptor } = record;
    attempt.commit(now); // committed 只能来自 visibility-probe（P1 强制）
    record.probe?.end();
    guard.consumeCancelFlag({ attemptId: attempt.attemptId }); // 挂起守卫记录随终态清理
    // §18.5 迁移快照：BodyRuntimeState 版本/watermark/posture/expression/gaze/speech/
    // root 归一化/相机 presentation/活动动画 normalizedTime+blend weights/待执行 gesture+剩余 TTL
    const migration = assembleMigrationSnapshot({
      bodyState,
      cameraPresentation: presentation.camera,
      rootTransform: presentation.rootTransform,
      pendingGestures: transitionBuffer.listPending(),
    });
    // winner 确定：缓冲 gesture 在新 current 上凭 actionId 至多执行一次（§18.5.5）
    transitionBuffer.resolveWinner({
      execute: (item) => engineAdapter.playGesture?.(item.semanticId, { attemptId: attempt.attemptId }),
    });
    engineAdapter.promoteCandidate?.(candidateAvatar.handle);
    // 单临界区 pending→current；旧 current 提交完成后才释放（§18.3.6）
    slots.commitPending(candidateAvatar, { nowMonotonic: now });
    lastMigrationSnapshot = migration;
    lastCommittedModelId = descriptor.modelId;
    safeMode = null;
    emitDiag(DiagnosticEvent.FIRST_VISIBLE_FRAME, {
      correlationId: attempt.attemptId,
      modelId: descriptor.modelId,
      phase: "visibility-probe",
      durationMs: now - record.startedAt,
      result: "committed",
      resourceEstimate: record.estimate,
      detail: {
        provisionalFrames: record.provisionalFrames,
        provisionalSlo: {
          targetFrames: limits.targetProvisionalPresentFrames,
          withinSlo: record.provisionalFrames <= limits.targetProvisionalPresentFrames, // 软 SLO 仅记录（§18.5）
        },
        visibilityEvidenceLatencyMs: record.probe?.visibilityEvidenceLatencyMs ?? null,
      },
    });
    if (journal) {
      try {
        await journal.clearJournal({ terminalState: "committed", attemptId: attempt.attemptId, reason: "first-visible-frame" });
      } catch (_error) {
        // journal 清除失败不影响已完成的提交临界区；证据以 diagnostics 为准
      }
    }
    record.settle({ attemptId: attempt.attemptId, outcome: "committed", modelId: descriptor.modelId });
    notify();
  }

  // 终态清理：释放 candidate、恢复 rollbackTarget 或 degraded（§18.3.4/§18.3.7）。
  async function finalizeTerminalAttempt(record) {
    const { attempt } = record;
    record.probe?.end();
    guard.consumeCancelFlag({ attemptId: attempt.attemptId }); // 挂起守卫记录随终态清理
    if (record.presented) {
      engineAdapter.concealCandidate?.(record.candidateAvatar?.handle);
      record.presented = false;
    }
    const rollback = attempt.rollbackTarget;
    const now = nowMonotonic();
    if (attempt.attemptKind === AttemptKind.RECOVERY && slots.state === RuntimeState.RECOVERING) {
      // §18.4：恢复失败 → degraded/2D safe mode；不回滚已失效 GPU 资源
      slots.recoveryFailed({ reason: attempt.terminalReason });
    } else {
      if (isAvatarUsable(rollback)) {
        // 下一渲染边界恢复 rollbackTarget 的 Surface 呈现（§18.3.7）
        engineAdapter.restorePresented?.(rollback.handle);
      }
      slots.resolveAfterPendingTerminal({ nowMonotonic: now });
    }
    // 回滚 winner=current：旧 current 继续原 gesture，凭 actionId 不重复消费（§18.5.5）
    transitionBuffer.resolveWinner({ execute: (item) => engineAdapter.playGesture?.(item.semanticId) });
    if (slots.state === RuntimeState.DEGRADED) {
      safeMode = deepFreeze({ mode: "2d", reason: attempt.terminalReason ?? "degraded" });
    }
    if (journal) {
      // journal 终态集合无 quarantined：映射为 failed 并以 reason 标注（§9.4 终态清除）
      const terminalForJournal =
        attempt.state === LoadAttemptState.REJECTED
          ? "rejected"
          : attempt.state === LoadAttemptState.CANCELLED
            ? "cancelled"
            : "failed";
      try {
        await journal.clearJournal({
          terminalState: terminalForJournal,
          attemptId: attempt.attemptId,
          reason: attempt.state === LoadAttemptState.QUARANTINED ? `quarantined:${attempt.terminalReason}` : attempt.terminalReason,
        });
      } catch (_error) {
        // journal 清除失败不改变已判定终态
      }
    }
    record.settle({
      attemptId: attempt.attemptId,
      outcome: attempt.state,
      reason: attempt.terminalReason,
      countsAsFailure: attempt.countsAsFailure,
    });
    notify();
  }

  // 被 latest-wins 取代的旧 pending：槽位已换主，只做轻量清理（不动 slots）。
  function settleSuperseded(record) {
    record.probe?.end();
    if (record.presented) {
      engineAdapter.concealCandidate?.(record.candidateAvatar?.handle);
      record.presented = false;
    }
    record.settle({ attemptId: record.attempt.attemptId, outcome: "cancelled", reason: "superseded", countsAsFailure: false });
  }

  // §11.3 低资源切换：暂停动作、完整释放旧模型 GPU 资源（保留最后一帧语义由 presentation 层负责）。
  function releaseCurrentForLowResource(record) {
    const old = slots.current;
    if (old) {
      old.dispose();
      slots.current = null;
    }
    bodyWriter.setActiveAnimation(null);
    emitDiag(DiagnosticEvent.MODEL_DISPOSED, {
      correlationId: record.attempt.attemptId,
      modelId: old?.modelId ?? null,
      phase: "loading",
      result: "low-resource-release",
      detail: { budgetMode: "low-resource", predictedPeakBytes: record.budget?.predictedPeakBytes ?? null },
    });
  }

  // ── 每帧 attempt 驱动 ─────────────────────────────────────
  function driveAttempt(record, now) {
    const { attempt, probe } = record;
    // §18.5 硬上限：按可见有效时间计（挂起段由 cumulativeSuspendedMs 顺延）
    if (
      (attempt.state === LoadAttemptState.PROVISIONAL_PRESENT || attempt.state === LoadAttemptState.VISIBILITY_PROBE) &&
      record.provisionalEnteredAt !== null
    ) {
      const effectiveElapsed = now - record.provisionalEnteredAt - attempt.cumulativeSuspendedMs;
      if (effectiveElapsed > limits.maxProvisionalPresentMs) {
        void failAttempt(record, {
          code: "provisional-present-timeout",
          category: QuarantineCategory.RUNTIME,
          retryable: true,
        });
        return;
      }
      record.provisionalFrames += 1;
    }
    const status = probe.poll(now);
    switch (status) {
      case "suspend":
        // 隐藏/离屏/尺寸不足/最小化/DPI 迁移 → suspended-probe，暂停 activePhaseDeadline（§19.1）
        attempt.transition(LoadAttemptState.SUSPENDED_PROBE, { nowMonotonic: now, reason: "probe-suspended" });
        guard.beginSuspension({ attemptId: attempt.attemptId, budgetMs: attempt.maxContinuousSuspendedMs });
        notify();
        break;
      case "resume": {
        // renderer 恢复：先处理主进程 cancel 标记，再恢复渲染/动作（§4.6）
        const flagged =
          guard.endSuspension({ attemptId: attempt.attemptId }) ||
          guard.consumeCancelFlag({ attemptId: attempt.attemptId });
        if (flagged) {
          void cancelSuspendedAttempt(record, "suspension-guard-cancel");
          return;
        }
        attempt.transition(LoadAttemptState.VISIBILITY_PROBE, { nowMonotonic: now, reason: "probe-resumed" });
        notify();
        break;
      }
      case "cancelled":
        // 挂起预算耗尽：LoadAttempt 已完成 cancelled 迁移（不计失败不计隔离）
        void finalizeTerminalAttempt(record);
        break;
      case "passed":
        void commitAttempt(record, now);
        break;
      case "timeout":
        void failAttempt(record, {
          code: "first-visible-frame-timeout",
          category: QuarantineCategory.RUNTIME,
          retryable: true,
        });
        break;
      default:
        break;
    }
  }

  // ── context lost / 恢复（§20.3）───────────────────────────
  const recoveryController = createRecoveryController({
    slots,
    diagnostics: diag,
    journal,
    quarantineTracker,
    nowMonotonic,
    engineVersionProvider: () => engineVersion,
    gpuFingerprint,
    getModelAgnosticState: () =>
      assembleMigrationSnapshot({
        bodyState,
        cameraPresentation: presentation.camera,
        rootTransform: presentation.rootTransform,
        pendingGestures: transitionBuffer.listPending(),
      }),
    runRecoveryAttempt: async ({ modelId }) => {
      const outcome = await startLoad(modelId, { attemptKind: AttemptKind.RECOVERY });
      return { ok: outcome.outcome === "committed", outcome: outcome.outcome, errorCode: outcome.errorCode ?? outcome.reason ?? null };
    },
    onSafeMode: ({ mode, reason }) => {
      safeMode = deepFreeze({ mode, reason });
    },
  });

  function handleEngineContextLost() {
    if (disposed) return;
    // P1 迁移表只允许 running→context-lost（degraded→context-lost 非法迁移）：
    // degraded 时已无有效 current，context-lost 事件按已处于安全态处理，不再迁移。
    if (slots.state !== RuntimeState.RUNNING) return;
    lostAvatar = slots.current; // 仅保留引用供 discardInvalidatedModel（不触 GL）
    stopLoop(); // §20.3.2 停 RAF
    stepper.reset();
    const pendingBefore = slots.pending && !slots.pending.isTerminal ? attemptRecords.get(slots.pending.attemptId) : null;
    recoveryController.handleContextLost({ reason: "webglcontextlost" });
    if (pendingBefore) settleSuperseded(pendingBefore); // context-lost 取消的 pending 结算
    notify();
  }

  async function handleEngineContextRestored() {
    if (disposed || slots.state !== RuntimeState.CONTEXT_LOST) return;
    startLoop();
    await recoveryController.initiateRecovery({ modelId: lastCommittedModelId ?? safeModelId });
  }

  engineAdapter.on?.(EngineEvent.CONTEXT_LOST, () => handleEngineContextLost());
  engineAdapter.on?.(EngineEvent.CONTEXT_RESTORED, () => {
    void handleEngineContextRestored();
  });
  engineAdapter.on?.(EngineEvent.FIRST_RENDERABLE_FRAME, (payload) => {
    const attemptId = payload?.attemptId;
    if (typeof attemptId !== "string") return; // 无 attemptId 归属的事件不采信（§19.1.7）
    const record = attemptRecords.get(attemptId);
    if (record) {
      record.probe.recordFirstRenderableFrame({ attemptId });
      emitDiag(DiagnosticEvent.FIRST_RENDERABLE_FRAME, {
        correlationId: attemptId,
        modelId: record.descriptor.modelId,
        phase: "renderability-probe",
        result: "ok",
      });
    }
  });

  // ── 加载入口（selectModel/importModel/recovery 共用）───────
  function assertUsableDescriptor(descriptor) {
    if (descriptor === null || typeof descriptor !== "object") {
      throw new AvatarRuntimeError("descriptor_invalid", "模型描述必须是对象");
    }
    if (typeof descriptor.modelId !== "string" || descriptor.modelId.length === 0) {
      throw new AvatarRuntimeError("descriptor_invalid", "descriptor.modelId 必须是非空字符串");
    }
    if (typeof descriptor.contentHash !== "string" || !/^[0-9a-f]{64}$/.test(descriptor.contentHash)) {
      throw new AvatarRuntimeError("descriptor_invalid", "descriptor.contentHash 必须是 64 位小写 hex");
    }
    if (!Number.isInteger(descriptor.byteLength) || descriptor.byteLength <= 0) {
      throw new AvatarRuntimeError("descriptor_invalid", "descriptor.byteLength 必须是正整数");
    }
  }

  async function startLoad(modelId, { attemptKind = null, descriptorOverride = null, attemptIdOverride = null } = {}) {
    if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
    const descriptor = descriptorOverride ?? (await assetSource.describeModel(modelId));
    assertUsableDescriptor(descriptor);
    const now = nowMonotonic();
    const kind = attemptKind ?? (slots.hasCurrent() ? AttemptKind.SWITCH : AttemptKind.INITIAL_LOAD);
    const attempt = new LoadAttempt({
      attemptId: attemptIdOverride ?? nextAttemptId(),
      attemptKind: kind,
      rollbackTarget: kind === AttemptKind.RECOVERY ? null : (slots.hasCurrent() ? slots.current : null),
      activePhaseDeadline: now + activePhaseBudgetMs,
      maxContinuousSuspendedMs,
      maxCumulativeSuspendedMs,
      nowMonotonic: now,
    });
    const record = createAttemptRecord(attempt, descriptor);
    if (kind === AttemptKind.RECOVERY) {
      // §18.4/§20.3：GPU 失效剔除（不触 GL）+ 重建 Renderer + RecoveryLoadAttempt 入槽
      if (lostAvatar) {
        engineAdapter.discardInvalidatedModel?.(lostAvatar.handle);
        lostAvatar = null;
      }
      engineAdapter.recreateRenderer?.();
      slots.beginRecovery(attempt);
    } else {
      const previous = slots.pending && !slots.pending.isTerminal ? attemptRecords.get(slots.pending.attemptId) : null;
      slots.beginLoad(attempt, { nowMonotonic: now }); // latest-wins 取消旧 pending，不动 current
      if (previous) settleSuperseded(previous);
    }
    lastRequestedModelId = descriptor.modelId;
    startLoop(); // N_activeRAF=1：重复 selectModel 不新增 RAF
    notify();
    await runAttempt(record);
    return record.done;
  }

  // ── §7.1 公开接口 ─────────────────────────────────────────
  const runtime = {
    attachSurface(surfaceInput) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      const host = surfaceInput?.host ?? surfaceInput;
      const mode = surfaceInput?.mode ?? "primary";
      const lease = surface.acquire(host, mode); // 单租约冲突由 controller 报错
      engineAdapter.attachSurface?.({ host, leaseId: lease.leaseId });
      startLoop();
      notify();
      return lease;
    },

    detachSurface(lease = null) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      const current = lease ?? surface.currentLease();
      if (current === null) return false;
      surface.release(current); // attach/detach 不重解析模型（§14.3）
      engineAdapter.detachSurface?.();
      notify();
      return true;
    },

    selectModel(modelId) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      if (typeof modelId !== "string" || modelId.length === 0) {
        throw new AvatarRuntimeError("model_id_invalid", "selectModel 需要非空 modelId");
      }
      if (slots.state === RuntimeState.CONTEXT_LOST || slots.state === RuntimeState.RECOVERING) {
        throw new AvatarRuntimeError("runtime_state_blocked", `当前 ${slots.state} 不接受 selectModel（走恢复链）`);
      }
      const attemptId = nextAttemptId();
      const done = startLoad(modelId, { attemptIdOverride: attemptId }).then((inner) => inner);
      done.catch(() => {}); // 预记录阶段失败不外溢为 unhandled rejection；调用方 await 仍能拿到拒绝
      return Object.freeze({ attemptId, done });
    },

    importModel(importRequest) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      if (importRequest === null || typeof importRequest !== "object") {
        throw new AvatarRuntimeError("import_invalid", "importModel 需要导入请求对象");
      }
      const attemptId = nextAttemptId();
      const started = (async () => {
        const descriptor =
          typeof assetSource.describeImport === "function"
            ? await assetSource.describeImport(importRequest)
            : importRequest;
        return startLoad(descriptor.modelId, { descriptorOverride: descriptor, attemptIdOverride: attemptId });
      })();
      const done = started.then((inner) => inner);
      done.catch(() => {});
      return Object.freeze({ attemptId, done });
    },

    applyPerformance(performanceInput) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      const actions = Array.isArray(performanceInput?.actions)
        ? performanceInput.actions
        : [performanceInput];
      const now = nowMonotonic();
      for (const wire of actions) {
        if (wire === null || typeof wire !== "object") continue;
        const scheduled = scheduleBodyAction(wire, { nowMonotonic: now }); // TTL 由本地单调时钟计算（§15.1）
        if (isScheduledActionExpired(scheduled, now)) continue; // 过期动作直接丢弃（§15.3）
        // 幂等键（§15.4）：缺 turnId/sequence/instanceId 的非标准动作不缓冲、不入 watermark
        let actionId = null;
        try {
          actionId = actionIdempotencyKey(wire);
        } catch (_error) {
          actionId = null;
        }
        if (wire.stop === true || wire.type === "stop") {
          // stop 最高优先级：清缓冲并投影停止到 current+pending（§18.5.6）
          transitionBuffer.stop();
          engineAdapter.playGesture?.(null);
          bodyWriter.setActiveAnimation(null);
          continue;
        }
        if (typeof wire.posture === "string" && wire.posture.length > 0) {
          bodyWriter.setPosture({ name: wire.posture });
          engineAdapter.applyPosture?.({ name: wire.posture });
        }
        if (wire.expression !== null && typeof wire.expression === "object") {
          bodyWriter.setExpressionTargets(wire.expression, { transitionMs: wire.durationMs ?? 0 });
          engineAdapter.applyExpression?.(wire.expression);
        }
        if (wire.gaze !== null && typeof wire.gaze === "object") {
          bodyWriter.setGazeTarget(wire.gaze);
          engineAdapter.applyGaze?.({ target: wire.gaze });
        }
        if (typeof wire.speaking === "boolean") {
          bodyWriter.setSpeaking(wire.speaking);
          engineAdapter.setSpeaking?.(wire.speaking);
        }
        if (Number.isFinite(wire.speechEnergy)) bodyWriter.setSpeechEnergy(wire.speechEnergy);
        if (typeof wire.viseme === "string") bodyWriter.setViseme(wire.viseme);
        if (wire.gesture !== undefined && wire.gesture !== null) {
          const semanticId = typeof wire.gesture === "string" ? wire.gesture : wire.gesture?.semanticId;
          const pending = slots.pending;
          const inTransition =
            pending &&
            !pending.isTerminal &&
            (pending.state === LoadAttemptState.PROVISIONAL_PRESENT ||
              pending.state === LoadAttemptState.VISIBILITY_PROBE ||
              pending.state === LoadAttemptState.SUSPENDED_PROBE);
          if (inTransition && actionId !== null) {
            // provisional-present 期间新到 gesture 进有界缓冲，不在 current/pending 提前执行（§18.5.4）
            transitionBuffer.push({ actionId, semanticId, scheduled });
          } else {
            engineAdapter.playGesture?.(semanticId);
            bodyWriter.setActiveAnimation({ semanticId, normalizedTime: 0, blendWeights: { [semanticId]: 1 } });
          }
        }
        if (Number.isInteger(wire.sequence)) {
          bodyWriter.consumeWatermark({ sequence: wire.sequence, actionId }); // 已消费 watermark（§18.5）
        }
      }
      notify();
    },

    applyProfile(profile) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      if (profile === null || typeof profile !== "object") {
        throw new AvatarRuntimeError("profile_invalid", "applyProfile 需要对象");
      }
      presentation = deepFreeze({
        ...presentation,
        profile,
        rootTransform: profile.rootTransform ?? presentation.rootTransform,
      });
      notify();
    },

    setPresentation(options) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      if (options === null || typeof options !== "object") {
        throw new AvatarRuntimeError("presentation_invalid", "setPresentation 需要对象");
      }
      const directCamera = ["focus", "height", "distance", "side", "fov"]
        .some((key) => Object.prototype.hasOwnProperty.call(options, key))
        ? options
        : null;
      const nextCamera = options.camera ?? directCamera;
      const nextLighting = options.lighting ?? null;
      presentation = deepFreeze({
        ...presentation,
        camera: nextCamera
          ? { ...(presentation.camera ?? {}), ...nextCamera }
          : presentation.camera,
        lighting: nextLighting
          ? { ...(presentation.lighting ?? {}), ...nextLighting }
          : presentation.lighting,
      });
      if (nextCamera) engineAdapter.applyCameraPresentation?.(presentation.camera);
      if (nextLighting) engineAdapter.applyLighting?.(presentation.lighting);
      notify();
    },

    pause(reason = null) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      if (paused) return false;
      paused = true;
      stopLoop();
      stepper.reset(); // 隐藏/暂停后不补算（§15.5）
      notify();
      return true;
    },

    resume(reason = null) {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      if (!paused) return false;
      paused = false;
      stepper.reset();
      startLoop();
      notify();
      return true;
    },

    retry() {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      const pending = slots.pending;
      if (pending && !pending.isTerminal) {
        const record = attemptRecords.get(pending.attemptId);
        return Object.freeze({ attemptId: pending.attemptId, done: record.done });
      }
      if (lastRequestedModelId === null) {
        throw new AvatarRuntimeError("retry_unavailable", "没有可重试的模型请求");
      }
      return runtime.selectModel(lastRequestedModelId);
    },

    resetToSafeModel() {
      if (disposed) throw new AvatarRuntimeError("runtime_disposed", "AvatarRuntime 已 dispose");
      return runtime.selectModel(safeModelId);
    },

    subscribe(listener) {
      if (typeof listener !== "function") throw new AvatarRuntimeError("listener_invalid", "subscribe 需要函数");
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    snapshot,

    getLastMigrationSnapshot() {
      return lastMigrationSnapshot;
    },

    // 诊断/审计只读视图
    diagnostics: diag,
    counters: runtimeCounters,
    get bodyState() {
      return bodyState;
    },
    get safeMode() {
      return safeMode;
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      stopLoop();
      for (const record of attemptRecords.values()) record.probe?.end();
      slots.dispose(); // current/pending/失效对象全量释放（幂等）
      if (surface.hasActiveLease()) {
        const lease = surface.currentLease();
        if (lease) surface.release(lease);
      }
      if (ownsEngine) engineAdapter.disposeEngine?.();
      listeners.clear();
    },
  };

  // N_activeAvatarRuntime=1：注入 registry 时登记单例，重复注册由 registry 拒绝（§20.1）。
  if (registry !== null) {
    registry.registerService(AVATAR_RUNTIME_SERVICE_ID, runtime);
  }

  emitDiag(DiagnosticEvent.ENGINE_INIT_START, { phase: "init", result: "start" });
  emitDiag(DiagnosticEvent.ENGINE_INIT_COMPLETE, { phase: "init", result: "ok" });

  return runtime;
}
