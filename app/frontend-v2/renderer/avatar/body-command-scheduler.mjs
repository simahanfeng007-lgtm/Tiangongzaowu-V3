// P5 §15.2/§15.3/§15.4 BodyCommandScheduler：业务侧动作进入 BodyRuntimeState 的唯一入口。
//
// 位置：biaoxian/TTS/面板语义命令 →【本调度器】→ AvatarRuntime.applyPerformance
// （BodyRuntimeState 唯一写入者仍在 AvatarRuntime 内，本调度器是其唯一业务入口）。
//
// 分类策略（§15.2 / H0）：复合 biaoxian 先展开为通道原子动作。
//   gaze/expression：latest-wins（每类只保留最新一条待执行）
//   posture/tail/conversation-state：状态型，只保留最新有效状态
//   gesture：有界 FIFO，必须带 TTL（缺 ttlMs 视为立即过期拒绝入队）
//   model-load：最新命令取代前一个待执行加载（旧的记 superseded）
//   speech-energy：按频率降采样，最多保留一条待转发，禁止无限排队
//   stop：最高优先级，立即清空全部待执行并直接转发
//
// 队列约束（§15.3）：Q(t) <= Qmax=32；溢出丢弃最低优先级项；
//   TTL 一律由注入的前端本地单调时钟扩展为 ScheduledBodyAction（contracts.scheduleBodyAction），
//   后端 sourceCreatedAt 仅诊断，禁止参与过期判定、禁止跨进程持久化后继续比较。
//
// 幂等去重（§15.4）：backendInstanceId+turnId+sequence；缺失 backendInstanceId 时
//   sessionEpoch 降级。legacy 降级会话建立后进入 awaitingSnapshot：
//   清空未执行动作、拒绝旧 epoch 补发，markSnapshotReceived() 后才接受动作。
//
// 时钟纪律：所有时间点来自注入 nowMonotonic；系统墙钟不参与者。

import {
  DEFAULT_MAX_ALLOWED_TTL_MS,
  actionIdempotencyKey,
  isScheduledActionExpired,
  scheduleBodyAction,
} from "./contracts.mjs";
import { deepFreeze } from "./canonical-hash.mjs";

export const BODY_COMMAND_SCHEDULER_SCHEMA_VERSION = 2;
export const SCHEDULER_QMAX = 32;
export const DEFAULT_SPEECH_ENERGY_MIN_INTERVAL_MS = 50;
export const DEFAULT_SEEN_KEY_CAPACITY = 256;

export class BodyCommandSchedulerError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BodyCommandSchedulerError";
    this.code = code;
  }
}

// 类别优先级（溢出丢弃与 flush 次序共用；stop 不入队、立即处理）。
const CATEGORY_RANK = Object.freeze({
  "model-load": 80,
  posture: 60,
  tail: 55,
  gesture: 50,
  expression: 40,
  gaze: 30,
  "conversation-state": 25,
  "speech-plan": 20,
  "speech-energy": 10,
});

// wire 显式 priority 对类别 rank 的微调（high +5 / low -5 / normal 0）。
function priorityAdjust(priority) {
  const text = String(priority ?? "").toLowerCase();
  if (text === "high") return 5;
  if (text === "low") return -5;
  return 0;
}

function isStopAction(wire) {
  return wire.stop === true || wire.type === "stop";
}

function classify(wire) {
  if (typeof wire.channel === "string" && CATEGORY_RANK[wire.channel] !== undefined) return wire.channel;
  if (wire.type === "model-load") return "model-load";
  if (wire.type === "conversation-state" || typeof wire.conversationState === "string") return "conversation-state";
  if (wire.type === "speech-boundary" || wire.speechPlan || wire.speechBoundary) return "speech-plan";
  if (wire.type === "speech-energy" || Number.isFinite(wire.speechEnergy)) return "speech-energy";
  if (typeof wire.speaking === "boolean") return "speech-energy"; // 说话状态与能量同通道降采样
  if (wire.gesture !== undefined && wire.gesture !== null) return "gesture";
  if (typeof wire.posture === "string" && wire.posture.length > 0) return "posture";
  if (wire.expression !== null && typeof wire.expression === "object") return "expression";
  if (wire.gaze !== null && typeof wire.gaze === "object") return "gaze";
  if (typeof wire.extras?.tail === "string" || typeof wire.tail === "string") return "tail";
  return "gesture"; // 兜底：无类别语义的动作按离散动作处理（必须有 TTL）
}

const BODY_CHANNEL_FIELDS = Object.freeze({
  posture: "posture",
  gesture: "gesture",
  gaze: "gaze",
  expression: "expression",
  "conversation-state": "conversationState",
});

export function splitBodyActionChannels(wire) {
  if (wire === null || typeof wire !== "object" || typeof wire.channel === "string") return Object.freeze([wire]);
  if (wire.type === "model-load" || wire.type?.startsWith?.("speech-") || isStopAction(wire)) return Object.freeze([wire]);
  const present = Object.entries(BODY_CHANNEL_FIELDS)
    .filter(([, field]) => wire[field] !== null && wire[field] !== undefined)
    .map(([channel, field]) => ({ channel, field }));
  const tail = typeof wire.extras?.tail === "string" ? wire.extras.tail : typeof wire.tail === "string" ? wire.tail : null;
  if (tail !== null) present.push({ channel: "tail", field: "tail" });
  if (present.length === 0) return Object.freeze([wire]);
  const bodyFields = new Set([...Object.values(BODY_CHANNEL_FIELDS), "tail"]);
  const base = Object.fromEntries(Object.entries(wire).filter(([key]) => !bodyFields.has(key)));
  return Object.freeze(present.map(({ channel, field }) => {
    const atomic = { ...base, channel };
    if (channel === "tail") {
      atomic.extras = { ...(wire.extras ?? {}), tail };
    } else {
      atomic[field] = wire[field];
      if (wire.extras) atomic.extras = { ...wire.extras, tail: null };
    }
    return deepFreeze(atomic);
  }));
}

export function createBodyCommandScheduler({
  nowMonotonic,
  sink,
  onModelLoad = null,
  qmax = SCHEDULER_QMAX,
  maxAllowedTTL = DEFAULT_MAX_ALLOWED_TTL_MS,
  speechEnergyMinIntervalMs = DEFAULT_SPEECH_ENERGY_MIN_INTERVAL_MS,
  seenKeyCapacity = DEFAULT_SEEN_KEY_CAPACITY,
} = {}) {
  if (typeof nowMonotonic !== "function") {
    throw new BodyCommandSchedulerError("clock_required", "BodyCommandScheduler 需要注入单调时钟 nowMonotonic");
  }
  if (sink === null || typeof sink !== "object" || typeof sink.applyPerformance !== "function") {
    throw new BodyCommandSchedulerError("sink_invalid", "BodyCommandScheduler 需要 sink.applyPerformance（AvatarRuntime）");
  }
  if (!Number.isInteger(qmax) || qmax <= 0) {
    throw new BodyCommandSchedulerError("qmax_invalid", "qmax 必须为正整数");
  }
  if (!Number.isFinite(speechEnergyMinIntervalMs) || speechEnergyMinIntervalMs < 0) {
    throw new BodyCommandSchedulerError("interval_invalid", "speechEnergyMinIntervalMs 必须是非负数");
  }

  // 待执行槽位：latest-wins/状态类每类一格；gesture FIFO 一条队列；speech-energy 一格。
  const latestSlots = new Map(); // category → item
  let gestureQueue = []; // item[]
  let speechEnergyPending = null; // item | null
  let lastSpeechEnergyForwardedAt = null;

  // 幂等键（有界 LRU：容量满后淘汰最旧）。
  const seenKeys = new Map(); // key → true（Map 保持插入序）

  // 会话（§15.4）：backendInstanceId 或 legacy sessionEpoch；legacy 建立后等待全量快照。
  let session = null; // { backendInstanceId, sessionEpoch, legacy, awaitingSnapshot }

  const counters = {
    received: 0,
    executed: 0,
    deduped: 0,
    expired: 0,
    staleDropped: 0,
    overflowDropped: 0,
    downsampled: 0,
    superseded: 0,
    expanded: 0,
    stopFlushed: 0,
  };

  function pendingCount() {
    return latestSlots.size + gestureQueue.length + (speechEnergyPending === null ? 0 : 1);
  }

  // 待执行键扫描（队列有界 ≤qmax，线性扫描足够）：submit 期同键拒绝的执行期去重前置层。
  function hasPendingKey(key) {
    for (const item of latestSlots.values()) {
      if (item.key === key) return true;
    }
    if (gestureQueue.some((item) => item.key === key)) return true;
    return speechEnergyPending !== null && speechEnergyPending.key === key;
  }

  function rememberKey(key) {
    seenKeys.delete(key);
    seenKeys.set(key, true);
    while (seenKeys.size > seenKeyCapacity) {
      seenKeys.delete(seenKeys.keys().next().value);
    }
  }

  // 幂等键：缺 turnId/sequence/instance 标识的非标准动作（如本地 speech 事件）不参与去重。
  function idempotencyKeyOf(wire) {
    try {
      const base = actionIdempotencyKey(wire);
      return typeof wire.channel === "string" ? `${base}:${wire.channel}` : base;
    } catch (_error) {
      return null;
    }
  }

  // 会话归属校验：防止旧连接补发（§15.4）。
  function belongsToCurrentSession(wire) {
    if (session === null) return true; // 未显式 beginSession：向后兼容，直接接受
    if (session.backendInstanceId !== null) {
      return wire.backendInstanceId === session.backendInstanceId;
    }
    // legacy 降级：epoch 必须一致；等待快照期间一律拒绝。
    if (session.awaitingSnapshot) return false;
    return typeof wire.sessionEpoch === "string" && wire.sessionEpoch === session.sessionEpoch;
  }

  function evictOverflow() {
    while (pendingCount() > qmax) {
      // 找最低优先级项丢弃（rank + 微调；同级丢最旧）。
      let victim = null; // { where, category?, index? }
      let victimScore = Infinity;
      for (const [category, item] of latestSlots) {
        const score = CATEGORY_RANK[category] + priorityAdjust(item.wire.priority);
        if (score < victimScore) {
          victimScore = score;
          victim = { where: "slot", category };
        }
      }
      gestureQueue.forEach((item, index) => {
        const score = CATEGORY_RANK.gesture + priorityAdjust(item.wire.priority);
        if (score < victimScore) {
          victimScore = score;
          victim = { where: "gesture", index };
        }
      });
      if (speechEnergyPending !== null) {
        const score = CATEGORY_RANK["speech-energy"] + priorityAdjust(speechEnergyPending.wire.priority);
        if (score < victimScore) {
          victim = { where: "speech-energy" };
        }
      }
      if (victim === null) return;
      if (victim.where === "slot") latestSlots.delete(victim.category);
      else if (victim.where === "gesture") gestureQueue.splice(victim.index, 1);
      else speechEnergyPending = null;
      counters.overflowDropped += 1;
    }
  }

  function forward(item, now) {
    if (item.key !== null) rememberKey(item.key);
    counters.executed += 1;
    if (item.category === "model-load" && typeof onModelLoad === "function") {
      onModelLoad(item.wire);
      return;
    }
    sink.applyPerformance(item.wire);
  }

  // 提交一个 wire 动作。返回 { accepted, reason }；stop 立即转发并清空队列。
  function submitAtomic(wire) {
    if (wire === null || typeof wire !== "object") {
      throw new BodyCommandSchedulerError("wire_invalid", "submit 需要 WireBodyAction 对象");
    }
    counters.received += 1;
    const now = nowMonotonic();

    if (isStopAction(wire)) {
      // stop 最高优先级：清全部待执行（含 speech-energy 暂存），立即转发（§15.2）。
      const cleared = pendingCount();
      latestSlots.clear();
      gestureQueue = [];
      speechEnergyPending = null;
      counters.stopFlushed += 1;
      sink.applyPerformance({ ...wire, stop: true, clearedPending: cleared });
      return deepFreeze({ accepted: true, reason: "stop" });
    }

    if (!belongsToCurrentSession(wire)) {
      counters.staleDropped += 1;
      return deepFreeze({ accepted: false, reason: "stale-session" });
    }

    // TTL 由前端本地单调时钟扩展（§15.1）；sourceCreatedAt 不影响 deadline。
    const scheduled = scheduleBodyAction(wire, { nowMonotonic: now, maxAllowedTTL });
    if (isScheduledActionExpired(scheduled, now)) {
      counters.expired += 1;
      return deepFreeze({ accepted: false, reason: "expired" });
    }

    const key = idempotencyKeyOf(wire);
    // 幂等去重双层：执行期 seenKeys（跨帧）+ submit 期待执行键（同帧突发）（§15.4）。
    if (key !== null && (seenKeys.has(key) || hasPendingKey(key))) {
      counters.deduped += 1;
      return deepFreeze({ accepted: false, reason: "duplicate" });
    }

    const category = classify(wire);
    const item = { wire, scheduled, key, category, enqueuedAtMonotonic: now };

    switch (category) {
      case "gaze":
      case "expression":
      case "posture":
      case "tail":
      case "conversation-state":
      case "speech-plan":
        // latest-wins / 状态型：同类只保留最新（被取代的不计失败，语义即覆盖）。
        if (latestSlots.has(category)) counters.superseded += 1;
        latestSlots.set(category, item);
        break;
      case "gesture":
        // 有界 FIFO：必须带 TTL（scheduleBodyAction 已将非法 ttl 钳为 0 → 上面已过期拒绝）。
        gestureQueue.push(item);
        break;
      case "model-load":
        // 最新加载取代前一个待执行加载。
        if (latestSlots.has("model-load")) counters.superseded += 1;
        latestSlots.set("model-load", item);
        break;
      case "speech-energy": {
        // 降采样：距上次转发不足间隔 → 只保留最新暂存，不排队（§15.2 禁止无限排队）。
        if (
          lastSpeechEnergyForwardedAt !== null &&
          now - lastSpeechEnergyForwardedAt < speechEnergyMinIntervalMs
        ) {
          speechEnergyPending = item;
          counters.downsampled += 1;
          return deepFreeze({ accepted: true, reason: "downsampled-pending" });
        }
        lastSpeechEnergyForwardedAt = now;
        forward(item, now);
        return deepFreeze({ accepted: true, reason: "forwarded" });
      }
      default:
        gestureQueue.push(item);
        break;
    }

    evictOverflow();
    return deepFreeze({ accepted: true, reason: "queued" });
  }

  // A backend biaoxian is one intent but not one scheduling channel.  Expand it
  // before classification so a gesture can never drag stale gaze/expression/
  // posture through the FIFO.
  function submit(wire) {
    if (wire === null || typeof wire !== "object") {
      throw new BodyCommandSchedulerError("wire_invalid", "submit 需要 WireBodyAction 对象");
    }
    const actions = splitBodyActionChannels(wire);
    if (actions.length === 1) return submitAtomic(actions[0]);
    counters.expanded += 1;
    const results = actions.map((action) => submitAtomic(action));
    return deepFreeze({
      accepted: results.some((result) => result.accepted),
      reason: "expanded",
      channels: Object.freeze(actions.map((action) => action.channel)),
      results: Object.freeze(results),
    });
  }

  // 逐条结算：过期丢弃；执行后置入幂等键。返回各类计数。
  function drainItems(items, now, result) {
    for (const item of items) {
      if (isScheduledActionExpired(item.scheduled, now)) {
        counters.expired += 1;
        result.expired += 1;
        continue;
      }
      if (item.key !== null && seenKeys.has(item.key)) {
        counters.deduped += 1;
        result.deduped += 1;
        continue;
      }
      forward(item, now);
      result.executed += 1;
    }
  }

  // 帧驱动排水：确定性次序 model-load → posture → expression → gaze → gesture FIFO → speech-energy。
  function pump() {
    const now = nowMonotonic();
    const result = { executed: 0, expired: 0, deduped: 0 };
    const order = [];
    for (const category of ["model-load", "conversation-state", "posture", "expression", "gaze", "tail", "speech-plan"]) {
      const item = latestSlots.get(category);
      if (item) order.push([category, item]);
    }
    for (const [category, item] of order) {
      latestSlots.delete(category);
      drainItems([item], now, result);
    }
    const gestures = gestureQueue;
    gestureQueue = [];
    drainItems(gestures, now, result);
    if (speechEnergyPending !== null) {
      const pending = speechEnergyPending;
      speechEnergyPending = null;
      // 降采样暂存项转发时也刷新节流时钟。
      lastSpeechEnergyForwardedAt = now;
      drainItems([pending], now, result);
    }
    return deepFreeze(result);
  }

  // §15.4 会话管理：重连/检测到序列重置时调用。
  // backendInstanceId 缺失 → legacy 降级：清空未执行、拒绝旧 epoch、等待全量快照。
  function beginSession({ backendInstanceId = null, sessionEpoch = null } = {}) {
    const backend = typeof backendInstanceId === "string" && backendInstanceId.length > 0 ? backendInstanceId : null;
    const epoch = typeof sessionEpoch === "string" && sessionEpoch.length > 0 ? sessionEpoch : null;
    if (backend === null && epoch === null) {
      throw new BodyCommandSchedulerError(
        "session_identity_missing",
        "beginSession 需要 backendInstanceId 或 sessionEpoch（legacy 降级）",
      );
    }
    latestSlots.clear();
    gestureQueue = [];
    speechEnergyPending = null;
    seenKeys.clear(); // 新会话不继承旧幂等键（旧连接补发由 epoch/instance 校验拦截）
    session = deepFreeze({
      backendInstanceId: backend,
      sessionEpoch: backend === null ? epoch : null,
      legacy: backend === null,
      awaitingSnapshot: backend === null, // legacy：等待全量状态快照后才接受动作
    });
    return session;
  }

  function markSnapshotReceived() {
    if (session !== null && session.legacy && session.awaitingSnapshot) {
      session = deepFreeze({ ...session, awaitingSnapshot: false });
      return true;
    }
    return false;
  }

  function endSession() {
    latestSlots.clear();
    gestureQueue = [];
    speechEnergyPending = null;
    session = null;
  }

  return deepFreeze({
    submit,
    pump,
    beginSession,
    markSnapshotReceived,
    endSession,
    pendingCount,
    get counters() {
      return deepFreeze({ ...counters });
    },
    get session() {
      return session;
    },
    get seenKeyCount() {
      return seenKeys.size;
    },
  });
}
