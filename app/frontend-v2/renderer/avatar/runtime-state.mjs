// §18.1/§18.3/§18.4 RuntimeState 槽位事务：current / pending / bodyState。
// 不变量：最多一个 pending；latest-wins 取消旧 pending 不动 current；
// committed 在单临界区内 pending→current，旧 current 提交后释放；
// "current 是否存在"由槽位非空且 GPU 未失效共同判定（§18.3.8）。

import {
  AttemptKind,
  LoadAttemptState,
  RuntimeState,
  isRuntimeTransitionAllowed,
} from "./contracts.mjs";

export class RuntimeStateError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "RuntimeStateError";
    this.code = code;
  }
}

// ActiveAvatar GPU 失效判定：失效对象不得满足"current 存在"（§18.4）。
function isAvatarUsable(avatar) {
  return avatar !== null && typeof avatar === "object" && avatar.gpuInvalidated !== true;
}

function releaseAvatar(avatar) {
  if (avatar && typeof avatar.dispose === "function") avatar.dispose();
}

export class AvatarRuntimeSlots {
  constructor({ bodyState = {} } = {}) {
    this.state = RuntimeState.UNINITIALIZED;
    this.current = null; // ActiveAvatar | null，唯一可回写的权威投影
    this.pending = null; // LoadAttempt | null，只读影子投影
    this.bodyState = bodyState; // BodyRuntimeState 唯一逻辑权威（由持有方写入）
    this.recoverySnapshot = null; // context-lost 时保存的模型无关状态
  }

  // §18.3.8：Runtime 是否可用由 current 是否存在与 RuntimeState 共同决定。
  hasCurrent() {
    return isAvatarUsable(this.current);
  }

  _transitionRuntime(toState) {
    if (!isRuntimeTransitionAllowed(this.state, toState)) {
      throw new RuntimeStateError(
        "runtime_transition_illegal",
        `RuntimeState 非法迁移 ${this.state} → ${toState}`,
      );
    }
    const from = this.state;
    this.state = toState;
    return from;
  }

  // §18.3.1/§18.3.2：一个 Runtime 最多一个 pending；latest-wins 取消旧 pending。
  // 取消只清理 pending，绝不动 current。
  beginLoad(attempt, { nowMonotonic } = {}) {
    if (this.state === RuntimeState.DISPOSING || this.state === RuntimeState.DISPOSED) {
      throw new RuntimeStateError("runtime_disposing", "disposing/disposed 状态禁止发起新加载");
    }
    if (attempt.attemptKind === AttemptKind.RECOVERY) {
      throw new RuntimeStateError("recovery_via_beginRecovery", "recovery attempt 必须走 beginRecovery");
    }
    if (this.pending && !this.pending.isTerminal) {
      this.pending.cancelSuperseded(nowMonotonic);
      releaseAvatar(this.pending.candidate ?? null);
    }
    // initial-load 期间 Runtime 保持原状态，直到 committed 后才进入 running。
    this.pending = attempt;
    return this.pending;
  }

  // §18.3.6：单临界区提交。pending 必须已进入 committed（FIRST_VISIBLE_FRAME 通过）。
  // JS 单线程内本函数无 await，即原子临界区：先交换槽位，再释放旧 current。
  commitPending(candidateAvatar, { nowMonotonic } = {}) {
    const attempt = this.pending;
    if (!attempt) throw new RuntimeStateError("no_pending", "没有可提交的 pending attempt");
    if (attempt.state !== LoadAttemptState.COMMITTED) {
      throw new RuntimeStateError(
        "commit_requires_visibility_probe",
        "committed 只能来自 visibility-probe，pending 尚未到达 committed",
      );
    }
    const candidate = candidateAvatar ?? attempt.candidate ?? null;
    if (!isAvatarUsable(candidate)) {
      throw new RuntimeStateError("candidate_invalid", "提交的候选 ActiveAvatar 缺失或已失效");
    }
    // ── 临界区开始：pending→current 原子交换 ──
    const oldCurrent = this.current;
    this.current = candidate;
    this.pending = null;
    // ── 临界区结束 ──
    // 旧 current 在提交完成后才释放（§18.3.6）。
    if (oldCurrent && oldCurrent !== candidate) releaseAvatar(oldCurrent);
    // initial-load / switch / recovery 提交后回到 running（§18.4：committed → running）。
    if (this.state !== RuntimeState.RUNNING) this._transitionRuntime(RuntimeState.RUNNING);
    return this.current;
  }

  // §18.3.4：cancelled/rejected/failed/quarantined 只清理 pending；
  // 存在有效 rollbackTarget 时恢复它并回到 running；无回滚目标或 current GPU 已失效 → degraded。
  resolveAfterPendingTerminal({ nowMonotonic } = {}) {
    const attempt = this.pending;
    if (!attempt) return this.state;
    if (!attempt.isTerminal || attempt.state === LoadAttemptState.COMMITTED) {
      throw new RuntimeStateError("pending_not_terminal", "pending 尚未进入可清理终态");
    }
    releaseAvatar(attempt.candidate ?? null);
    this.pending = null;
    const rollback = attempt.rollbackTarget;
    if (isAvatarUsable(rollback) && !this.hasCurrent()) {
      // 下一渲染边界恢复 rollbackTarget 的 Surface 呈现（§18.3.7）。
      this.current = rollback;
    }
    if (this.hasCurrent()) {
      if (this.state !== RuntimeState.RUNNING) this._transitionRuntime(RuntimeState.RUNNING);
    } else if (this.state !== RuntimeState.DEGRADED) {
      // 无有效回滚目标：进入 degraded / 2D safe mode（§18.3.7/§18.4）。
      this._transitionRuntime(RuntimeState.DEGRADED);
    }
    return this.state;
  }

  // §20.3：context lost。保存模型无关状态，标记并移除失效 current。
  contextLost({ recoverySnapshot = null } = {}) {
    if (this.state !== RuntimeState.RUNNING && this.state !== RuntimeState.DEGRADED) {
      throw new RuntimeStateError("context_lost_state", `当前 ${this.state} 不允许进入 context-lost`);
    }
    this._transitionRuntime(RuntimeState.CONTEXT_LOST);
    this.recoverySnapshot = recoverySnapshot ?? this.recoverySnapshot;
    if (this.current) {
      // 允许保留 modelId/contentHash 等诊断元数据，但失效对象不得满足"current 存在"。
      this.current.gpuInvalidated = true;
      this._lostCurrent = this.current;
      this.current = null;
    }
    if (this.pending && !this.pending.isTerminal) {
      this.pending.cancelSuperseded?.(undefined);
    }
    return this.recoverySnapshot;
  }

  // §18.4：recovering 创建 RecoveryLoadAttempt，rollbackTarget 必须为 null。
  beginRecovery(attempt) {
    if (this.state !== RuntimeState.CONTEXT_LOST) {
      throw new RuntimeStateError("recovery_state", "只有 context-lost 可以进入 recovering");
    }
    if (attempt.attemptKind !== AttemptKind.RECOVERY || attempt.rollbackTarget !== null) {
      throw new RuntimeStateError(
        "recovery_rollback_null",
        "RecoveryLoadAttempt 要求 attemptKind=recovery 且 rollbackTarget=null，不回滚已失效 GPU 资源",
      );
    }
    if (this.pending && !this.pending.isTerminal) {
      throw new RuntimeStateError("pending_exists", "已有活动 pending，违反单 pending 不变量");
    }
    this._transitionRuntime(RuntimeState.RECOVERING);
    this.pending = attempt;
    return this.pending;
  }

  // §18.4：恢复失败 → degraded → 2D safe mode；不得访问旧 GPU 资源。
  recoveryFailed({ reason = null } = {}) {
    if (this.state !== RuntimeState.RECOVERING) {
      throw new RuntimeStateError("recovery_state", "只有 recovering 可以走 recoveryFailed");
    }
    const attempt = this.pending;
    if (attempt && !attempt.isTerminal) {
      attempt.transition(LoadAttemptState.FAILED, { reason: reason ?? "recovery-failed" });
    }
    if (attempt) releaseAvatar(attempt.candidate ?? null);
    this.pending = null;
    this._transitionRuntime(RuntimeState.DEGRADED);
    return this.state;
  }

  // 释放：任意非终态 → disposing → disposed；current/pending 全量释放。
  dispose() {
    if (this.state === RuntimeState.DISPOSED) return this.state;
    if (this.state !== RuntimeState.DISPOSING) this._transitionRuntime(RuntimeState.DISPOSING);
    if (this.pending) {
      if (!this.pending.isTerminal) this.pending.cancelSuperseded?.(undefined);
      releaseAvatar(this.pending.candidate ?? null);
      this.pending = null;
    }
    releaseAvatar(this.current);
    this.current = null;
    releaseAvatar(this._lostCurrent ?? null);
    this._lostCurrent = null;
    this._transitionRuntime(RuntimeState.DISPOSED);
    return this.state;
  }
}
