// P5 §15.1 业务接入适配器：后端 biaoxian/回复事实 → WireBodyAction。
// 只产出 wire 对象（纯函数、冻结），不负责执行；执行路径为
// BodyCommandScheduler → AvatarRuntime.applyPerformance（BodyRuntimeState 唯一写入链）。
//
// 接入点纪律（不侵入 http-runtime/conversation-panel）：
//   actions.mjs 已把 SSE biaoxian 转发为 window 事件 "tiangong-biaoxian"，
//   本适配器由订阅方（avatar-service/panel）以 addEventListener 方式接入，零侵入。
//
// biaoxian 事实形态（网关 final_response）：
//   { expression, gaze, posture, gesture, tail, intensity, duration, source }
//   duration 单位秒（LLM 自报），本适配器换算 durationMs；tail 不进入 §15.1 结构，仅透传到 extras 供诊断。
//
// 后端 instance 标识缺失时的降级规则（§15.4）：
//   backendInstanceId 缺失 → 使用注入 getSessionEpoch() 的 sessionEpoch（legacy 降级），
//   并标注 instanceKeySource；两者都缺失直接抛错（无法构成幂等键，拒绝产出）。

import { deepFreeze } from "./canonical-hash.mjs";

export const WIRE_BODY_ACTION_SCHEMA = "tiangong.body-action.v1";
export const BODY_PERFORMANCE_ADAPTER_SCHEMA_VERSION = 1;

export const INSTANCE_KEY_SOURCE = Object.freeze({
  BACKEND: "backend",
  SESSION_EPOCH_LEGACY: "session-epoch-legacy",
});

export class BodyPerformanceAdapterError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BodyPerformanceAdapterError";
    this.code = code;
  }
}

function clamp01(value, fallback = 0.5) {
  const num = Number(value);
  if (!Number.isFinite(num)) return fallback;
  return Math.max(0, Math.min(1, num));
}

function optionalString(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

// biaoxian.duration（秒）→ durationMs；非法值回退 null。
function durationToMs(duration) {
  const seconds = Number(duration);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  return Math.round(seconds * 1000);
}

// 来源 → 调度优先级提示（§15.2 分类策略之外的相对次序，scheduler 内再做类别裁决）。
function mapPriority(source) {
  const text = String(source ?? "").toLowerCase();
  if (text === "llm") return "normal";
  if (text === "system" || text === "user") return "high";
  return "normal";
}

export function createBiaoxianAdapter({
  getBackendInstanceId = null,
  getSessionEpoch = null,
  defaultTtlMs = 30_000,
  idGenerator = null,
} = {}) {
  if (!Number.isFinite(defaultTtlMs) || defaultTtlMs <= 0) {
    throw new BodyPerformanceAdapterError("ttl_invalid", "defaultTtlMs 必须是正数");
  }
  let localTurnSeq = 0;

  // 后端 instance 标识解析：正式 backendInstanceId 优先，缺失时 sessionEpoch 降级（§15.4）。
  function resolveInstanceIdentity() {
    const backendInstanceId =
      typeof getBackendInstanceId === "function" ? optionalString(getBackendInstanceId()) : null;
    if (backendInstanceId !== null) {
      return { backendInstanceId, sessionEpoch: null, instanceKeySource: INSTANCE_KEY_SOURCE.BACKEND };
    }
    const sessionEpoch = typeof getSessionEpoch === "function" ? optionalString(getSessionEpoch()) : null;
    if (sessionEpoch !== null) {
      return {
        backendInstanceId: null,
        sessionEpoch,
        instanceKeySource: INSTANCE_KEY_SOURCE.SESSION_EPOCH_LEGACY,
      };
    }
    throw new BodyPerformanceAdapterError(
      "instance_identity_missing",
      "缺少 backendInstanceId 且 sessionEpoch 不可用，无法构成幂等键（§15.4），拒绝产出 WireBodyAction",
    );
  }

  function nextLocalTurnId() {
    localTurnSeq += 1;
    return typeof idGenerator === "function" ? idGenerator() : `local-turn-${localTurnSeq}`;
  }

  // biaoxian + 回复上下文 → WireBodyAction（§15.1 字段全集；null 表示该通道无语义）。
  // turn = { turnId?, sequence?, sourceCreatedAt?, ttlMs? }；turnId 缺失时生成单调本地 turn 标识。
  function wireFromBiaoxian(biaoxian, turn = {}) {
    if (biaoxian === null || typeof biaoxian !== "object") {
      throw new BodyPerformanceAdapterError("biaoxian_invalid", "wireFromBiaoxian 需要 biaoxian 对象");
    }
    const identity = resolveInstanceIdentity();
    const turnId = optionalString(turn.turnId) ?? nextLocalTurnId();
    const sequence = Number.isInteger(turn.sequence) && turn.sequence >= 0 ? turn.sequence : 0;
    const intensity = clamp01(biaoxian.intensity);
    const expressionName = optionalString(biaoxian.expression);
    const gazeName = optionalString(biaoxian.gaze);
    const postureName = optionalString(biaoxian.posture);
    const gestureName = optionalString(biaoxian.gesture);
    return deepFreeze({
      schema: WIRE_BODY_ACTION_SCHEMA,
      backendInstanceId: identity.backendInstanceId,
      sessionEpoch: identity.sessionEpoch,
      turnId,
      sequence,
      // 后端产生时间仅作诊断，scheduler 禁止用它计算过期（§15.1/§15.3）。
      sourceCreatedAt: Number.isFinite(turn.sourceCreatedAt) ? turn.sourceCreatedAt : null,
      ttlMs: Number.isFinite(turn.ttlMs) && turn.ttlMs > 0 ? turn.ttlMs : defaultTtlMs,
      priority: mapPriority(biaoxian.source),
      posture: postureName,
      gesture: gestureName,
      gaze: gazeName === null ? null : { target: gazeName },
      expression:
        expressionName === null ? null : { name: expressionName, intensity },
      durationMs: durationToMs(biaoxian.duration),
      intensity,
      // 诊断透传（不进入 §15.1 标准结构字段）：tail/source/instance 标识来源。
      extras: deepFreeze({
        tail: optionalString(biaoxian.tail),
        source: optionalString(biaoxian.source),
        instanceKeySource: identity.instanceKeySource,
      }),
    });
  }

  // 流式片段与最终回复不能重复执行（§15.4）：调用方需让两者共享同一 turnId，
  // 由本适配器为最终回复分配递增 sequence，scheduler 幂等键据此去重。
  function wireSequenceForFinalReply(turnId) {
    return { turnId: optionalString(turnId) ?? nextLocalTurnId(), sequence: Number.MAX_SAFE_INTEGER - 1 };
  }

  return deepFreeze({
    wireFromBiaoxian,
    wireSequenceForFinalReply,
    schema: WIRE_BODY_ACTION_SCHEMA,
  });
}
