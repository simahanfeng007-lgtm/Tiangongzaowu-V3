// §18.1/§18.5 BodyRuntimeState：conversation/posture/gaze/expression/speaking/speech-energy/watermark 唯一逻辑权威。
// provisional-present 期间同态双投影：current 可写、pending 只读不回写（§4.2 N_authoritativeSimulation=1）。
// transitionActionBuffer：有界 Qmax=32、TTL、latest-wins 去重；提交/回滚确定 winner 后
// gesture 凭 actionId 至多执行一次，过期记 TRANSITION_ACTION_EXPIRED；stop 最高优先级清缓冲并投影停止。
// 所有时间点为前端本地单调毫秒，由调用方注入。

import { isScheduledActionExpired } from "./contracts.mjs";
import { deepFreeze } from "./canonical-hash.mjs";

export const BODY_RUNTIME_STATE_SCHEMA_VERSION = 2;
export const MIGRATION_SNAPSHOT_SCHEMA_VERSION = 2;
export const TRANSITION_BUFFER_QMAX = 32;

export class BodyStateError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BodyStateError";
    this.code = code;
  }
}

function assertClock(nowMonotonic) {
  if (typeof nowMonotonic !== "function") {
    throw new BodyStateError("clock_required", "BodyRuntimeState 需要注入单调时钟 nowMonotonic");
  }
}

function clonePlain(value) {
  return value === null || value === undefined ? value : structuredClone(value);
}

// ── BodyRuntimeState ─────────────────────────────────────────
export function createBodyRuntimeState({ nowMonotonic } = {}) {
  assertClock(nowMonotonic);

  const state = {
    version: 0,
    watermark: { sequence: 0, actionId: null, updatedAtMonotonic: 0 },
    posture: { name: "idle", transitionProgress: 1, updatedAtMonotonic: 0 },
    expression: { targets: {}, current: {}, remainingTransitionMs: 0, updatedAtMonotonic: 0 },
    gaze: { target: null, current: null, filter: { alpha: 0.35, settled: true }, updatedAtMonotonic: 0 },
    conversationState: "IDLE",
    speaking: false,
    speechEnergy: 0,
    viseme: null,
    activeAnimation: null, // { semanticId, normalizedTime, blendWeights }
  };

  let writerIssued = false;

  function touch(field) {
    state.version += 1;
    if (field && state[field] && typeof state[field] === "object") {
      state[field].updatedAtMonotonic = nowMonotonic();
    }
  }

  // 唯一写入者（§18.1）：writer 只签发一次，第二次申请直接抛错。
  function createWriter() {
    if (writerIssued) {
      throw new BodyStateError("writer_already_issued", "BodyRuntimeState 只允许一个写入者（§18.1 唯一逻辑权威）");
    }
    writerIssued = true;
    return Object.freeze({
      setPosture({ name, transitionProgress = 1 } = {}) {
        if (typeof name !== "string" || name.length === 0) {
          throw new BodyStateError("posture_invalid", "posture 需要非空 name");
        }
        state.posture = { name, transitionProgress, updatedAtMonotonic: nowMonotonic() };
        touch();
      },
      setExpressionTargets(targets, { transitionMs = 0 } = {}) {
        if (targets === null || typeof targets !== "object") {
          throw new BodyStateError("expression_invalid", "expression targets 必须是对象");
        }
        state.expression = {
          targets: clonePlain(targets),
          current: clonePlain(state.expression.targets ?? {}),
          remainingTransitionMs: Math.max(0, Number(transitionMs) || 0),
          updatedAtMonotonic: nowMonotonic(),
        };
        touch();
      },
      // 插值推进：由渲染帧驱动，缩短剩余过渡时间并收敛 current → targets。
      advanceExpressionInterpolation(elapsedMs) {
        if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return;
        const expr = state.expression;
        expr.remainingTransitionMs = Math.max(0, expr.remainingTransitionMs - elapsedMs);
        expr.current = clonePlain(expr.targets);
        touch("expression");
      },
      setGazeTarget(target, { filterAlpha = null } = {}) {
        if (target === null || typeof target !== "object") {
          throw new BodyStateError("gaze_invalid", "gaze target 必须是对象");
        }
        state.gaze = {
          target: clonePlain(target),
          current: clonePlain(state.gaze.target ?? target),
          filter: {
            alpha: Number.isFinite(filterAlpha) ? filterAlpha : state.gaze.filter.alpha,
            settled: false,
          },
          updatedAtMonotonic: nowMonotonic(),
        };
        touch();
      },
      markGazeSettled() {
        state.gaze = { ...state.gaze, current: clonePlain(state.gaze.target), filter: { ...state.gaze.filter, settled: true } };
        touch("gaze");
      },
      setConversationState(conversationState) {
        const normalized = String(conversationState ?? "").trim().toUpperCase();
        const allowed = ["IDLE", "LISTENING", "THINKING", "TURN_ACQUIRING", "SPEAKING", "TURN_YIELDING"];
        if (!allowed.includes(normalized)) {
          throw new BodyStateError("conversation_state_invalid", `未知会话具身状态 ${conversationState}`);
        }
        state.conversationState = normalized;
        touch();
      },
      setSpeaking(speaking) {
        state.speaking = speaking === true;
        if (!state.speaking) state.viseme = null;
        touch();
      },
      setSpeechEnergy(energy) {
        state.speechEnergy = Number.isFinite(energy) ? Math.max(0, energy) : 0;
        touch();
      },
      setViseme(viseme) {
        state.viseme = typeof viseme === "string" && viseme.length > 0 ? viseme : null;
        touch();
      },
      setActiveAnimation(animation) {
        state.activeAnimation = animation === null
          ? null
          : {
              semanticId: animation.semanticId ?? null,
              normalizedTime: Number.isFinite(animation.normalizedTime) ? animation.normalizedTime : 0,
              blendWeights: clonePlain(animation.blendWeights ?? {}),
            };
        touch();
      },
      // 已消费 watermark：sequence 只允许单调前进（§18.5 迁移快照据此去重）。
      consumeWatermark({ sequence, actionId = null } = {}) {
        if (!Number.isInteger(sequence) || sequence < 0) {
          throw new BodyStateError("watermark_invalid", "watermark 需要非负整数 sequence");
        }
        if (sequence < state.watermark.sequence) {
          throw new BodyStateError("watermark_regression", `watermark sequence 不允许回退 ${state.watermark.sequence} → ${sequence}`);
        }
        state.watermark = { sequence, actionId, updatedAtMonotonic: nowMonotonic() };
        touch();
      },
    });
  }

  function readSnapshot() {
    return deepFreeze(clonePlain({
      version: state.version,
      watermark: state.watermark,
      posture: state.posture,
      expression: state.expression,
      gaze: state.gaze,
      conversationState: state.conversationState,
      speaking: state.speaking,
      speechEnergy: state.speechEnergy,
      viseme: state.viseme,
      activeAnimation: state.activeAnimation,
    }));
  }

  // 同态双投影（§18.5）：同一逻辑状态投影到 current/pending。
  // current 投影可写（经唯一 writer 转发）；pending 投影只读，任何写尝试抛错，禁止回写。
  function projectFor(role) {
    if (role !== "current" && role !== "pending") {
      throw new BodyStateError("projection_role_invalid", "投影角色必须是 current|pending");
    }
    const readonlyGuard = () => {
      throw new BodyStateError("pending_read_only", "pending 只读影子投影，禁止向 BodyRuntimeState 回写（§4.2/§18.1）");
    };
    const readOnly = {
      get version() { return state.version; },
      getSnapshot: readSnapshot,
    };
    if (role === "pending") {
      return Object.freeze({
        ...readOnly,
        role,
        setPosture: readonlyGuard,
        setExpressionTargets: readonlyGuard,
        setGazeTarget: readonlyGuard,
        setConversationState: readonlyGuard,
        setSpeaking: readonlyGuard,
        setSpeechEnergy: readonlyGuard,
        setViseme: readonlyGuard,
        setActiveAnimation: readonlyGuard,
        consumeWatermark: readonlyGuard,
      });
    }
    // current：唯一可回写投影，转发到唯一 writer（_bindWriter 绑定后生效）。
    let writer = null;
    const requireWriter = () => {
      if (writer === null) throw new BodyStateError("writer_missing", "writer 尚未绑定，current 投影不可写");
      return writer;
    };
    const projection = {
      ...readOnly,
      role,
      _bindWriter(instance) { writer = instance; },
      setPosture: (...args) => requireWriter().setPosture(...args),
      setExpressionTargets: (...args) => requireWriter().setExpressionTargets(...args),
      setGazeTarget: (...args) => requireWriter().setGazeTarget(...args),
      setConversationState: (...args) => requireWriter().setConversationState(...args),
      setSpeaking: (...args) => requireWriter().setSpeaking(...args),
      setSpeechEnergy: (...args) => requireWriter().setSpeechEnergy(...args),
      setViseme: (...args) => requireWriter().setViseme(...args),
      setActiveAnimation: (...args) => requireWriter().setActiveAnimation(...args),
      consumeWatermark: (...args) => requireWriter().consumeWatermark(...args),
    };
    return Object.freeze(projection);
  }

  return Object.freeze({
    createWriter,
    projectFor,
    getSnapshot: readSnapshot,
    get version() { return state.version; },
    get writerIssued() { return writerIssued; },
  });
}

// ── §18.5 迁移快照组装 ───────────────────────────────────────
// committed 时输出：BodyRuntimeState 版本、已消费 watermark、posture/expression/gaze/speech 状态、
// root/model 归一化、相机 presentation、活动动画 semanticId+normalizedTime+blend weights、
// 有界待执行 gesture 及剩余 TTL。
export function assembleMigrationSnapshot({
  bodyState,
  cameraPresentation = null,
  rootTransform = null,
  pendingGestures = [],
}) {
  if (bodyState === null || typeof bodyState !== "object" || typeof bodyState.getSnapshot !== "function") {
    throw new BodyStateError("body_state_invalid", "assembleMigrationSnapshot 需要 BodyRuntimeState");
  }
  const snapshot = bodyState.getSnapshot();
  return deepFreeze({
    schemaVersion: MIGRATION_SNAPSHOT_SCHEMA_VERSION,
    bodyStateVersion: snapshot.version,
    watermark: clonePlain(snapshot.watermark),
    posture: clonePlain(snapshot.posture),
    expression: clonePlain(snapshot.expression),
    gaze: clonePlain(snapshot.gaze),
    conversationState: snapshot.conversationState,
    speaking: snapshot.speaking,
    speechEnergy: snapshot.speechEnergy,
    viseme: snapshot.viseme,
    rootTransform: clonePlain(rootTransform),
    cameraPresentation: clonePlain(cameraPresentation),
    activeAnimation: clonePlain(snapshot.activeAnimation),
    pendingGestures: pendingGestures.map((gesture) => clonePlain(gesture)),
  });
}

// ── transitionActionBuffer（§18.5 离散 gesture 有界缓冲）─────
export function createTransitionActionBuffer({
  nowMonotonic,
  qmax = TRANSITION_BUFFER_QMAX,
  onExpired = null,
  onDropped = null,
} = {}) {
  assertClock(nowMonotonic);
  if (!Number.isInteger(qmax) || qmax <= 0) {
    throw new BodyStateError("qmax_invalid", "transitionActionBuffer qmax 必须为正整数");
  }
  let items = []; // { actionId, semanticId, scheduled }
  const executedIds = new Set();
  let stopped = false;

  function expireAllBefore(now) {
    const kept = [];
    let expired = 0;
    for (const item of items) {
      if (isScheduledActionExpired(item.scheduled, now)) {
        expired += 1;
        onExpired?.(item, "TRANSITION_ACTION_EXPIRED");
      } else {
        kept.push(item);
      }
    }
    items = kept;
    return expired;
  }

  return Object.freeze({
    // 入队：TTL 已到直接丢弃并记录；幂等去重（同 actionId 后者替换，latest-wins）；
    // 超出 Qmax 丢最旧并记录（§4.5 有界队列）。
    push(item) {
      if (item === null || typeof item !== "object" || typeof item.actionId !== "string" || item.actionId.length === 0) {
        throw new BodyStateError("buffer_item_invalid", "缓冲动作需要非空 actionId");
      }
      if (item.scheduled === null || typeof item.scheduled !== "object") {
        throw new BodyStateError("buffer_item_invalid", "缓冲动作需要 scheduled（contracts.scheduleBodyAction 产物）");
      }
      stopped = false;
      const now = nowMonotonic();
      if (isScheduledActionExpired(item.scheduled, now)) {
        onExpired?.(item, "TRANSITION_ACTION_EXPIRED");
        return false;
      }
      const existing = items.findIndex((entry) => entry.actionId === item.actionId);
      if (existing >= 0) items.splice(existing, 1); // 幂等键去重：同 actionId 以最新为准
      items.push({ actionId: item.actionId, semanticId: item.semanticId ?? null, scheduled: item.scheduled });
      expireAllBefore(now);
      while (items.length > qmax) {
        const dropped = items.shift();
        onDropped?.(dropped, "qmax-overflow");
      }
      return true;
    },

    // stop 最高优先级（§18.5.6）：立即清空缓冲；投影停止由调用方（runtime）转发到 current+pending。
    stop() {
      const cleared = items.length;
      items = [];
      stopped = true;
      return cleared;
    },

    // 提交/回滚确定 winner 后统一结算：凭 actionId 至多执行一次；过期记 TRANSITION_ACTION_EXPIRED。
    resolveWinner({ execute } = {}) {
      if (typeof execute !== "function") {
        throw new BodyStateError("executor_invalid", "resolveWinner 需要 execute(item) 回调");
      }
      const now = nowMonotonic();
      const result = { executed: 0, expired: 0, skippedDuplicate: 0 };
      for (const item of items) {
        if (executedIds.has(item.actionId)) {
          result.skippedDuplicate += 1;
          continue;
        }
        if (isScheduledActionExpired(item.scheduled, now)) {
          result.expired += 1;
          onExpired?.(item, "TRANSITION_ACTION_EXPIRED");
          continue;
        }
        executedIds.add(item.actionId);
        execute(item);
        result.executed += 1;
      }
      items = [];
      return deepFreeze(result);
    },

    // 迁移快照用：未过期 gesture 及剩余 TTL（有界）。
    listPending() {
      const now = nowMonotonic();
      expireAllBefore(now);
      return items.map((item) =>
        deepFreeze({
          actionId: item.actionId,
          semanticId: item.semanticId,
          remainingTtlMs: Math.max(0, item.scheduled.deadlineMonotonic - now),
        }),
      );
    },

    size: () => items.length,
    isStopped: () => stopped,
    hasExecuted: (actionId) => executedIds.has(String(actionId ?? "")),
    get executedCount() { return executedIds.size; },
  });
}
