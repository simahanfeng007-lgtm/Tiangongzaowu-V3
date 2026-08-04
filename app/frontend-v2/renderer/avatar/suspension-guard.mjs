// §4.6/§19.1 主进程相对时限守卫（SuspensionGuard）。
// 独立时钟域：只接收相对预算（毫秒时长），不与 renderer 交换/比较绝对单调时间戳。
// renderer 挂起期间若被冻结，恢复调度后的第一步必须先 consumeCancelFlag 处理 cancel 标记，
// 再处理渲染或动作（§4.6/§19.1）。

export const SUSPENSION_GUARD_SCHEMA_VERSION = 1;

export class SuspensionGuardError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "SuspensionGuardError";
    this.code = code;
  }
}

// nowMonotonic：主进程自己的单调时钟（独立时钟域）。
// onCancelFlagged：置 cancel 标记时的通知钩子（诊断/日志）。
export function createSuspensionGuard({ nowMonotonic, onCancelFlagged = null } = {}) {
  if (typeof nowMonotonic !== "function") {
    throw new SuspensionGuardError("clock_required", "SuspensionGuard 需要注入主进程时钟域的单调时钟");
  }
  const records = new Map(); // attemptId → { budgetMs, beganAt, flagEvaluated, cancelFlagged }

  // 挂起开始：只接收相对预算 budgetMs，截止时刻在主进程时钟域内计算。
  function beginSuspension({ attemptId, budgetMs }) {
    if (typeof attemptId !== "string" || attemptId.length === 0) {
      throw new SuspensionGuardError("attempt_id_invalid", "beginSuspension 需要非空 attemptId");
    }
    if (!Number.isFinite(budgetMs) || budgetMs <= 0) {
      throw new SuspensionGuardError("budget_invalid", "budgetMs 必须是正数（相对毫秒）");
    }
    records.set(attemptId, {
      budgetMs,
      beganAt: nowMonotonic(),
      flagEvaluated: false,
      cancelFlagged: false,
    });
    return true;
  }

  // 挂起结束（renderer 恢复调度）：到期则置 cancel 标记；标记保留到被 consume。
  function endSuspension({ attemptId }) {
    const record = records.get(String(attemptId ?? ""));
    if (!record) return false;
    if (!record.flagEvaluated) {
      record.flagEvaluated = true;
      if (nowMonotonic() - record.beganAt >= record.budgetMs) {
        record.cancelFlagged = true;
        onCancelFlagged?.(Object.freeze({ attemptId: String(attemptId), budgetMs: record.budgetMs }));
      }
    }
    return record.cancelFlagged;
  }

  // renderer 恢复后的第一项工作：读取并清除 cancel 标记（先处理 cancel，再渲染/动作）。
  function consumeCancelFlag({ attemptId }) {
    const key = String(attemptId ?? "");
    const record = records.get(key);
    if (!record) return false;
    records.delete(key);
    return record.cancelFlagged;
  }

  return Object.freeze({
    beginSuspension,
    endSuspension,
    consumeCancelFlag,
    hasRecord: (attemptId) => records.has(String(attemptId ?? "")),
    hasCancelFlag: (attemptId) => records.get(String(attemptId ?? ""))?.cancelFlagged === true,
    get activeCount() { return records.size; },
  });
}
