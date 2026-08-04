// §18.2 LoadAttempt：每次模型选择/导入/切换/恢复创建独立 attemptId。
// 负责：状态迁移校验、activePhaseDeadline 挂起暂停/恢复、
// 挂起预算（连续/累计）超限 → cancelled 判定（不计失败、不进 quarantine）。
// 所有时间点为前端本地单调毫秒，由调用方注入，禁止墙钟。

import {
  AttemptKind,
  LoadAttemptState,
  DEFAULT_MAX_CONTINUOUS_SUSPENDED_MS,
  DEFAULT_MAX_CUMULATIVE_SUSPENDED_MS,
  isLoadAttemptTransitionAllowed,
  isTerminalLoadAttemptState,
  isValidRollbackTargetForKind,
} from "./contracts.mjs";

export class LoadAttemptError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "LoadAttemptError";
    this.code = code;
  }
}

export class LoadAttempt {
  constructor({
    attemptId,
    attemptKind,
    rollbackTarget = null,
    activePhaseDeadline,
    maxContinuousSuspendedMs = DEFAULT_MAX_CONTINUOUS_SUSPENDED_MS,
    maxCumulativeSuspendedMs = DEFAULT_MAX_CUMULATIVE_SUSPENDED_MS,
    nowMonotonic = 0,
  }) {
    if (typeof attemptId !== "string" || attemptId.length === 0) {
      throw new LoadAttemptError("attempt_id_invalid", "LoadAttempt 需要非空 attemptId");
    }
    // nullable rollbackTarget：recovery 必须为 null，其余允许为 null 或 ActiveAvatar（§18.2/§18.4）。
    if (!isValidRollbackTargetForKind(attemptKind, rollbackTarget)) {
      throw new LoadAttemptError(
        "rollback_target_invalid",
        `attemptKind=${attemptKind} 的 rollbackTarget 不合法（recovery 必须为 null）`,
      );
    }
    if (!Number.isFinite(activePhaseDeadline) || activePhaseDeadline < nowMonotonic) {
      throw new LoadAttemptError("active_phase_deadline_invalid", "activePhaseDeadline 必须是不早于当前单调时刻的有限值");
    }
    if (!Number.isFinite(maxContinuousSuspendedMs) || maxContinuousSuspendedMs <= 0) {
      throw new LoadAttemptError("suspension_budget_invalid", "maxContinuousSuspendedMs 必须为正");
    }
    if (!Number.isFinite(maxCumulativeSuspendedMs) || maxCumulativeSuspendedMs <= 0) {
      throw new LoadAttemptError("suspension_budget_invalid", "maxCumulativeSuspendedMs 必须为正");
    }
    this.attemptId = attemptId;
    this.attemptKind = attemptKind;
    this.rollbackTarget = rollbackTarget;
    this.activePhaseDeadline = activePhaseDeadline;
    this.maxContinuousSuspendedMs = maxContinuousSuspendedMs;
    this.maxCumulativeSuspendedMs = maxCumulativeSuspendedMs;
    this.state = LoadAttemptState.SELECTING;
    this.suspendedAtMonotonic = null;
    this.cumulativeSuspendedMs = 0;
    this.terminalReason = null; // 终态归因：superseded/suspension-budget/error...
    this.countsAsFailure = null; // cancelled(rejected 同) 不计失败；failed/quarantined 计
    this.history = Object.freeze([{ from: null, to: this.state, at: nowMonotonic }]);
    this._transitions = [{ from: null, to: this.state, at: nowMonotonic }];
  }

  get isTerminal() {
    return isTerminalLoadAttemptState(this.state);
  }

  get isSuspended() {
    return this.state === LoadAttemptState.SUSPENDED_PROBE;
  }

  // 状态迁移：非法迁移抛错，不静默吞掉。
  transition(toState, { nowMonotonic, reason = null } = {}) {
    if (!isLoadAttemptTransitionAllowed(this.state, toState)) {
      throw new LoadAttemptError(
        "transition_illegal",
        `LoadAttempt ${this.attemptId} 非法迁移 ${this.state} → ${toState}`,
      );
    }
    if (this.state === LoadAttemptState.SUSPENDED_PROBE && toState === LoadAttemptState.VISIBILITY_PROBE) {
      this._endSuspension(nowMonotonic);
    }
    if (toState === LoadAttemptState.SUSPENDED_PROBE) {
      this._beginSuspension(nowMonotonic);
    }
    if (isTerminalLoadAttemptState(toState)) {
      this.terminalReason = reason ?? this.terminalReason;
      // §18.3.4/§19.1：cancelled（含挂起超限）与 rejected 不记为模型失败。
      this.countsAsFailure = toState === LoadAttemptState.FAILED || toState === LoadAttemptState.QUARANTINED;
    }
    const from = this.state;
    this.state = toState;
    this._transitions.push({ from, to: toState, at: nowMonotonic ?? null, reason });
    this.history = Object.freeze([...this._transitions]);
    return this;
  }

  _beginSuspension(nowMonotonic) {
    if (!Number.isFinite(nowMonotonic)) {
      throw new LoadAttemptError("clock_required", "进入挂起需要本地单调时刻");
    }
    this.suspendedAtMonotonic = nowMonotonic;
  }

  // 恢复时只在同一 renderer 时钟域内累计挂起时长，并把 activePhaseDeadline 顺延（§4.6/§19.1）。
  _endSuspension(nowMonotonic) {
    if (!Number.isFinite(nowMonotonic) || this.suspendedAtMonotonic === null) {
      throw new LoadAttemptError("clock_required", "结束挂起需要本地单调时刻");
    }
    const segment = Math.max(0, nowMonotonic - this.suspendedAtMonotonic);
    this.cumulativeSuspendedMs += segment;
    this.activePhaseDeadline += segment;
    this.suspendedAtMonotonic = null;
  }

  // 当前连续挂起时长（未挂起为 0）。
  currentContinuousSuspendedMs(nowMonotonic) {
    if (this.suspendedAtMonotonic === null) return 0;
    return Math.max(0, nowMonotonic - this.suspendedAtMonotonic);
  }

  // 挂起预算判定（§18.3.9）：达到连续或累计上限 → cancelled，返回 true。
  // 不计 failed/quarantine 计数；旧 attemptId 不得复活，由调用方另起 attempt。
  checkSuspensionBudget(nowMonotonic) {
    if (this.isTerminal) return false;
    const continuous = this.currentContinuousSuspendedMs(nowMonotonic);
    const cumulative = this.cumulativeSuspendedMs + continuous;
    if (continuous >= this.maxContinuousSuspendedMs || cumulative >= this.maxCumulativeSuspendedMs) {
      if (this.isSuspended) {
        // 挂起段并入累计后再进终态，保证审计完整。
        this._endSuspension(nowMonotonic);
      }
      this.transition(LoadAttemptState.CANCELLED, { nowMonotonic, reason: "suspension-budget-exceeded" });
      return true;
    }
    return false;
  }

  // activePhaseDeadline 到期判定（挂起期间已暂停计时，不会误到期）。
  isActivePhaseExpired(nowMonotonic) {
    return !this.isTerminal && !this.isSuspended && nowMonotonic >= this.activePhaseDeadline;
  }

  // §18.2：committed 只能来自 visibility-probe（FIRST_VISIBLE_FRAME 通过后的原子交换入口）。
  commit(nowMonotonic) {
    return this.transition(LoadAttemptState.COMMITTED, { nowMonotonic, reason: "first-visible-frame" });
  }

  // latest-wins 取消旧 pending（§18.3.2）。
  cancelSuperseded(nowMonotonic) {
    if (this.isTerminal) return this;
    return this.transition(LoadAttemptState.CANCELLED, { nowMonotonic, reason: "superseded" });
  }
}
