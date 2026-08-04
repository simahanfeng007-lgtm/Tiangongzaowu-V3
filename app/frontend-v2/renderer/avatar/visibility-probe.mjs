// §19 受控可见性探针（VisibilityProbe）。
// 前提检查（§19.1.1-7）：Surface 已挂载可见且尺寸稳定、文档未 hidden、默认相机锁定、
// 暂停用户输入、DPI 稳定、已有 FIRST_RENDERABLE_FRAME、有 rollbackTarget 时必须保留、
// recovery（rollbackTarget=null）豁免回滚前提。
// FIRST_VISIBLE_FRAME 判定输入装配：firstFrame + drawCalls>0 + attemptId 归属像素 +
// 包围盒与视口相交 + surfaceVisible + 尺寸下限 + 无致命错误。
// 挂起条件（隐藏/离屏/尺寸不足/最小化/DPI 迁移）→ suspended-probe（暂停 activePhaseDeadline）；
// 挂起预算由 LoadAttempt 判定，超预算 → cancelled（不计失败不计 quarantine）。
// 异步像素证据延迟单独计 visibilityEvidenceLatencyMs，不在同步关键路径强塞 readPixels（§18.5）。
//
// 探针只给出判定建议（advisory），LoadAttempt 的实际迁移由持有方（AvatarRuntime）执行，
// 保证 P1 迁移表的唯一执行入口。

import { LoadAttemptState, AttemptKind } from "./contracts.mjs";

export const VISIBILITY_PROBE_SCHEMA_VERSION = 1;
export const MIN_PROBE_SURFACE_SIZE = Object.freeze({ width: 32, height: 32 });

export class VisibilityProbeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "VisibilityProbeError";
    this.code = code;
  }
}

export function createVisibilityProbeSession({
  attempt,
  surface,
  env = {},
  sampleFrameEvidence,
  minSurfaceSize = MIN_PROBE_SURFACE_SIZE,
  onEvent = null,
} = {}) {
  if (attempt === null || typeof attempt !== "object" || typeof attempt.attemptId !== "string") {
    throw new VisibilityProbeError("attempt_invalid", "VisibilityProbe 需要 LoadAttempt");
  }
  if (surface === null || typeof surface !== "object") {
    throw new VisibilityProbeError("surface_invalid", "VisibilityProbe 需要 RenderSurfaceController");
  }
  if (typeof sampleFrameEvidence !== "function") {
    throw new VisibilityProbeError("evidence_invalid", "VisibilityProbe 需要 sampleFrameEvidence() 同步帧证据采样");
  }
  const isDocumentHidden = typeof env.isDocumentHidden === "function" ? env.isDocumentHidden : () => false;
  const isWindowMinimized = typeof env.isWindowMinimized === "function" ? env.isWindowMinimized : () => false;

  let started = false;
  let ended = false;
  let firstRenderableSeen = false;
  let pixelEvidence = null;
  let evidenceLatencyMs = null;
  let cameraLocked = false;
  let userInputPaused = false;

  function notify(type, detail = {}) {
    onEvent?.(Object.freeze({ type, attemptId: attempt.attemptId, ...detail }));
  }

  // FIRST_RENDERABLE_FRAME 输入信号（§19.1.6）：按 attemptId 归属，串号不采信。
  function recordFirstRenderableFrame({ attemptId } = {}) {
    if (attemptId === attempt.attemptId) {
      firstRenderableSeen = true;
      notify("first-renderable-frame");
      return true;
    }
    return false;
  }

  // 异步像素证据：{ attemptId, nonBackgroundPixels }；延迟单独计时（§18.5/§23）。
  function submitPixelEvidence(evidence, { latencyMs = null } = {}) {
    if (evidence === null || typeof evidence !== "object") {
      throw new VisibilityProbeError("evidence_invalid", "像素证据必须是对象");
    }
    pixelEvidence = evidence;
    evidenceLatencyMs = Number.isFinite(latencyMs) ? latencyMs : evidenceLatencyMs;
    notify("pixel-evidence", { latencyMs: evidenceLatencyMs });
    return true;
  }

  function collectEnvBlocked(nowMonotonic) {
    const blocked = [];
    if (!surface.hasActiveLease()) blocked.push("surface-not-attached");
    else {
      if (!surface.isVisible()) blocked.push("surface-hidden");
      if (!surface.isAboveMinimum(minSurfaceSize)) blocked.push("surface-size-below-minimum");
      if (!surface.isSizeStable(nowMonotonic)) blocked.push("surface-size-unstable");
      if (surface.isDpiTransitioning()) blocked.push("dpi-transitioning");
    }
    if (isDocumentHidden()) blocked.push("document-hidden");
    if (isWindowMinimized()) blocked.push("window-minimized");
    return blocked;
  }

  // 前提检查（§19.1）：envBlocked 走挂起路径；hardFailures 是探针硬失败。
  // rollbackTarget 存在时必须可用（未被 GPU 失效）；rollbackTarget=null 豁免回滚前提
  // （recovery 必然如此；低资源切换同样无回滚目标，一并标注）。
  function checkPreconditions(nowMonotonic) {
    const hardFailures = [];
    if (!firstRenderableSeen) hardFailures.push("first-renderable-frame-missing");
    const rollback = attempt.rollbackTarget ?? null;
    if (rollback !== null && rollback.gpuInvalidated === true) {
      hardFailures.push("rollback-target-invalidated");
    }
    const rollbackExempt = rollback === null;
    return Object.freeze({
      ok: hardFailures.length === 0,
      envBlocked: Object.freeze(collectEnvBlocked(nowMonotonic)),
      hardFailures: Object.freeze(hardFailures),
      rollbackExempt,
      recoveryExempt: rollbackExempt && attempt.attemptKind === AttemptKind.RECOVERY,
    });
  }

  // 探针开始：锁定默认相机/取景、暂停用户输入，直到探针成功、取消或超时（§19.1.3/4）。
  function begin(nowMonotonic) {
    if (ended) throw new VisibilityProbeError("probe_ended", "探针已结束，禁止复用会话");
    const pre = checkPreconditions(nowMonotonic);
    if (pre.hardFailures.length > 0) {
      return Object.freeze({ status: "precondition-failed", ...pre });
    }
    if (pre.envBlocked.length > 0) {
      return Object.freeze({ status: "env-blocked", ...pre });
    }
    started = true;
    cameraLocked = true;
    userInputPaused = true;
    notify("probe-start");
    return Object.freeze({ status: "started", ...pre });
  }

  function evaluateFirstVisibleFrame(nowMonotonic) {
    const frame = sampleFrameEvidence() ?? {};
    const surfaceVisible = surface.hasActiveLease() && surface.isVisible();
    const sizeOk = surface.isAboveMinimum(minSurfaceSize);
    const candidatePixelsOk =
      pixelEvidence !== null &&
      pixelEvidence.attemptId === attempt.attemptId && // 候选像素必须按 attemptId 归属（§19.1.7）
      Number(pixelEvidence.nonBackgroundPixels) > 0;
    return (
      firstRenderableSeen &&
      frame.firstFrame === true &&
      Number(frame.drawCalls) > 0 &&
      candidatePixelsOk &&
      frame.boundsIntersectViewport === true &&
      surfaceVisible &&
      sizeOk &&
      frame.fatalRendererError !== true
    );
  }

  // 每帧判定（由 RAF pump 驱动）。返回建议状态，实际迁移由持有方执行。
  function poll(nowMonotonic) {
    if (attempt.isTerminal) return "terminal";
    if (attempt.state === LoadAttemptState.SUSPENDED_PROBE) {
      // 挂起预算判定（连续 5 分钟/累计 15 分钟默认，可配置）→ cancelled 不计失败不计隔离。
      if (attempt.checkSuspensionBudget(nowMonotonic)) return "cancelled";
      return collectEnvBlocked(nowMonotonic).length === 0 ? "resume" : "suspended";
    }
    if (attempt.state !== LoadAttemptState.VISIBILITY_PROBE) return "probing";
    const envBlocked = collectEnvBlocked(nowMonotonic);
    if (envBlocked.length > 0) return "suspend";
    if (!started) return "probing";
    if (evaluateFirstVisibleFrame(nowMonotonic)) return "passed";
    if (attempt.isActivePhaseExpired(nowMonotonic)) return "timeout";
    return "probing";
  }

  function end() {
    if (ended) return;
    ended = true;
    started = false;
    cameraLocked = false;
    userInputPaused = false;
    notify("probe-end");
  }

  return Object.freeze({
    recordFirstRenderableFrame,
    submitPixelEvidence,
    checkPreconditions,
    begin,
    poll,
    end,
    get started() { return started; },
    get ended() { return ended; },
    get cameraLocked() { return cameraLocked; },
    get userInputPaused() { return userInputPaused; },
    get firstRenderableSeen() { return firstRenderableSeen; },
    get pixelEvidenceReceived() { return pixelEvidence !== null; },
    get visibilityEvidenceLatencyMs() { return evidenceLatencyMs; },
    get isSuspended() { return attempt.isSuspended; },
  });
}
