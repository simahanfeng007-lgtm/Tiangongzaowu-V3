// §20.3/§20.4 RecoveryController：context lost 编排、renderer 崩溃重载上限（同会话 1 次）、
// journal 归因（崩溃后从 PendingLoadJournal 取 attemptId/contentHash/engineVersion/gpuFingerprint）、
// 按失败类别计 quarantine；禁止无限崩溃重启循环。
// 状态机迁移本身由 P1 AvatarRuntimeSlots 执行；attempt 全链由 AvatarRuntime 注入驱动。

import { RuntimeState } from "./contracts.mjs";
import { DiagnosticEvent } from "./diagnostics.mjs";
import { QuarantineCategory, quarantineKeyForFailure } from "./model-quarantine.mjs";

export const RECOVERY_CONTROLLER_SCHEMA_VERSION = 1;
export const DEFAULT_MAX_RENDERER_RELOADS_PER_SESSION = 1;

export class RecoveryControllerError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "RecoveryControllerError";
    this.code = code;
  }
}

export function createRecoveryController({
  slots,
  diagnostics = null,
  journal = null,
  quarantineTracker = null,
  nowMonotonic,
  engineVersionProvider,
  gpuFingerprint = null,
  maxRendererReloadsPerSession = DEFAULT_MAX_RENDERER_RELOADS_PER_SESSION,
  getModelAgnosticState,
  runRecoveryAttempt,
  onSafeMode = null,
} = {}) {
  if (slots === null || typeof slots !== "object") {
    throw new RecoveryControllerError("slots_invalid", "RecoveryController 需要 AvatarRuntimeSlots");
  }
  if (typeof nowMonotonic !== "function") {
    throw new RecoveryControllerError("clock_required", "RecoveryController 需要注入单调时钟");
  }
  if (typeof engineVersionProvider !== "function") {
    throw new RecoveryControllerError("engine_version_invalid", "RecoveryController 需要 engineVersionProvider()");
  }
  if (typeof getModelAgnosticState !== "function") {
    throw new RecoveryControllerError("snapshot_invalid", "RecoveryController 需要 getModelAgnosticState()");
  }
  if (typeof runRecoveryAttempt !== "function") {
    throw new RecoveryControllerError("recovery_runner_invalid", "RecoveryController 需要 runRecoveryAttempt()");
  }
  if (!Number.isInteger(maxRendererReloadsPerSession) || maxRendererReloadsPerSession < 0) {
    throw new RecoveryControllerError("reload_limit_invalid", "maxRendererReloadsPerSession 必须是非负整数");
  }

  let rendererCrashCount = 0;
  let lostModelMeta = null; // context-lost 时保留的模型无关诊断元数据（非 GPU 对象）

  function emit(event, fields = {}) {
    diagnostics?.emit(event, {
      engineVersion: engineVersionProvider(),
      ...fields,
    });
  }

  // §20.3 context lost 编排：保存模型无关状态、标记 GPU 失效、进入 context-lost。
  // 调用方（runtime）已保证 preventDefault 与停 RAF；本函数不触碰任何旧 GPU 对象。
  function handleContextLost({ reason = "webglcontextlost" } = {}) {
    if (slots.state !== RuntimeState.RUNNING && slots.state !== RuntimeState.DEGRADED) {
      throw new RecoveryControllerError(
        "context_lost_state",
        `当前 ${slots.state} 不允许进入 context-lost（§20.3）`,
      );
    }
    const current = slots.current;
    lostModelMeta = current
      ? Object.freeze({ modelId: current.modelId ?? null, contentHash: current.contentHash ?? null })
      : null;
    emit(DiagnosticEvent.CONTEXT_LOST, {
      modelId: lostModelMeta?.modelId ?? null,
      phase: "context-lost",
      result: "lost",
      errorCode: reason,
      retryable: true,
      detail: gpuFingerprint ? Object.freeze({ gpuFingerprint }) : null,
    });
    // §20.3.6：模型无关状态保存到 recoverySnapshot；失效 ActiveAvatar 由 slots 剔除。
    const snapshot = getModelAgnosticState();
    slots.contextLost({ recoverySnapshot: snapshot });
    return Object.freeze({ recoverySnapshot: snapshot, lostModelMeta });
  }

  // §18.4/§20.3 恢复：RecoveryLoadAttempt（rollbackTarget=null）重走完整链，
  // 新的 FIRST_VISIBLE_FRAME 通过才 committed；失败 → degraded / 2D safe mode。
  async function initiateRecovery({ modelId } = {}) {
    if (slots.state !== RuntimeState.CONTEXT_LOST) {
      throw new RecoveryControllerError("recovery_state", `只有 context-lost 可以发起恢复（当前 ${slots.state}）`);
    }
    const targetModelId = modelId ?? lostModelMeta?.modelId ?? null;
    if (typeof targetModelId !== "string" || targetModelId.length === 0) {
      throw new RecoveryControllerError("recovery_model_missing", "恢复需要可重载的 modelId");
    }
    emit(DiagnosticEvent.RECOVERY_START, { modelId: targetModelId, phase: "recovering", result: "start" });
    const outcome = await runRecoveryAttempt({
      modelId: targetModelId,
      recoverySnapshot: slots.recoverySnapshot,
    });
    if (outcome?.ok === true) {
      emit(DiagnosticEvent.RECOVERY_COMPLETE, { modelId: targetModelId, phase: "recovering", result: "committed" });
    } else {
      // 恢复失败：degraded → 2D safe mode（§18.4）；不回滚已失效 GPU 资源。
      onSafeMode?.(Object.freeze({ mode: "2d", reason: outcome?.errorCode ?? "recovery-failed", modelId: targetModelId }));
    }
    return outcome;
  }

  // §20.4 renderer 崩溃：同一会话最多自动重载一次；journal 归因 + quarantine 计数。
  async function recordRendererCrash({ reason = "render-process-gone" } = {}) {
    rendererCrashCount += 1;
    const pendingEntry = journal && typeof journal.readPendingEntry === "function" ? journal.readPendingEntry() : null;
    let quarantineResult = null;
    if (pendingEntry && quarantineTracker) {
      // §20.4.6：renderer 崩溃按 runtime 类别键计数（结构/引擎键由各自失败路径负责）。
      const key = quarantineKeyForFailure(QuarantineCategory.RUNTIME, {
        contentHash: pendingEntry.contentHash,
        engineVersion: pendingEntry.engineVersion,
        gpuFingerprint: pendingEntry.gpuFingerprint ?? gpuFingerprint,
      });
      quarantineResult = await quarantineTracker.recordFailure({
        key,
        category: QuarantineCategory.RUNTIME,
        reason,
      });
      if (quarantineResult.quarantined) {
        diagnostics?.emit(DiagnosticEvent.MODEL_QUARANTINED, {
          modelId: pendingEntry.modelId ?? null,
          engineVersion: pendingEntry.engineVersion,
          phase: "crash",
          result: "quarantined",
          errorCode: quarantineResult.reason,
          retryable: false,
        });
      }
    }
    const shouldReload = rendererCrashCount <= maxRendererReloadsPerSession;
    return Object.freeze({
      crashCount: rendererCrashCount,
      shouldReload, // 超过上限即 false：禁止无限崩溃重启循环（§20.4）
      attribution: pendingEntry
        ? Object.freeze({
            attemptId: pendingEntry.attemptId,
            modelId: pendingEntry.modelId,
            contentHash: pendingEntry.contentHash,
            engineVersion: pendingEntry.engineVersion,
            gpuFingerprint: pendingEntry.gpuFingerprint ?? null,
          })
        : null,
      quarantined: quarantineResult?.quarantined === true,
      quarantineReason: quarantineResult?.reason ?? null,
    });
  }

  return Object.freeze({
    handleContextLost,
    initiateRecovery,
    recordRendererCrash,
    get rendererCrashCount() { return rendererCrashCount; },
    get lostModelMeta() { return lostModelMeta; },
  });
}
