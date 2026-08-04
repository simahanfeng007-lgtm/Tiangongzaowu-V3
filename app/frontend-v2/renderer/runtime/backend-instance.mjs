// P6a §15.4 backendInstanceId 下发：SSE/响应处理处的后端实例标识桥。
//
// 规则（§15.4）：
//   - 后端载荷提供 backendInstanceId（或 backend_instance_id / meta.backendInstanceId）时
//     透传为当前实例标识；标识变化即轮换（后端进程重启必须变化）。
//   - 后端载荷没有该字段时，在前端会话级生成 UUID（legacy sessionEpoch 语义）：
//     同一后端连接期稳定，检测到重连（noteReconnect）时更换。
//   - 每次生效都写入 window.tiangongBackendInstanceId 并 dispatch
//     "tiangong-backend-instance" 事件（detail: { backendInstanceId, source, at }）。
//   - window 不可用时只记账不报错（Node 测试/非浏览器环境安全）。

export const BACKEND_INSTANCE_EVENT_NAME = "tiangong-backend-instance";
export const BACKEND_INSTANCE_WINDOW_KEY = "tiangongBackendInstanceId";

export const BackendInstanceSource = Object.freeze({
  BACKEND: "backend",
  FRONTEND_EPOCH: "frontend-epoch",
});

function defaultUuid() {
  return globalThis.crypto?.randomUUID?.()
    ?? `epoch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function defaultTarget() {
  return typeof window !== "undefined" ? window : null;
}

function defaultNow() {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

function optionalString(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

// 后端实例标识字段提取：正式字段优先，snake_case 与 meta 嵌套兜底。
export function backendInstanceIdFromPayload(payload) {
  if (payload === null || typeof payload !== "object") return null;
  return (
    optionalString(payload.backendInstanceId)
    ?? optionalString(payload.backend_instance_id)
    ?? optionalString(payload?.meta?.backendInstanceId)
    ?? optionalString(payload?.meta?.backend_instance_id)
    ?? null
  );
}

export function createBackendInstanceBridge({
  target = null, // 默认惰性解析 window；测试注入 mock window
  idGenerator = defaultUuid,
  nowMonotonic = null,
} = {}) {
  const resolveTarget = () => (typeof target === "function" ? target() : target) ?? defaultTarget();
  const now = typeof nowMonotonic === "function" ? nowMonotonic : defaultNow;
  let current = null; // { backendInstanceId, source }

  function publish() {
    const sink = resolveTarget();
    if (sink === null) return;
    try {
      sink[BACKEND_INSTANCE_WINDOW_KEY] = current.backendInstanceId;
    } catch (_error) { /* 属性不可写时仅事件可观测 */ }
    const EventCtor = sink.CustomEvent ?? (typeof CustomEvent !== "undefined" ? CustomEvent : null);
    if (EventCtor === null || typeof sink.dispatchEvent !== "function") return;
    try {
      sink.dispatchEvent(new EventCtor(BACKEND_INSTANCE_EVENT_NAME, {
        detail: { backendInstanceId: current.backendInstanceId, source: current.source, at: now() },
      }));
    } catch (_error) { /* dispatch 失败不阻断运行时链 */ }
  }

  function adopt(backendInstanceId, source) {
    current = { backendInstanceId, source };
    publish();
    return current.backendInstanceId;
  }

  return Object.freeze({
    // SSE/JSON 载荷观察点：后端字段优先；无字段且无当前标识时生成会话级 UUID。
    notePayload(payload) {
      const fromBackend = backendInstanceIdFromPayload(payload);
      if (fromBackend !== null) {
        if (current === null || current.backendInstanceId !== fromBackend) {
          return adopt(fromBackend, BackendInstanceSource.BACKEND);
        }
        return current.backendInstanceId;
      }
      if (current === null) {
        return adopt(idGenerator(), BackendInstanceSource.FRONTEND_EPOCH);
      }
      return current.backendInstanceId;
    },
    // 重连检测点（503 运行态重试 / 连接失败）：后端标识由后端自己轮换，前端只轮换会话级 UUID。
    noteReconnect() {
      if (current === null || current.source === BackendInstanceSource.FRONTEND_EPOCH) {
        return adopt(idGenerator(), BackendInstanceSource.FRONTEND_EPOCH);
      }
      return current.backendInstanceId;
    },
    current() {
      return current === null ? null : Object.freeze({ ...current });
    },
    reset() {
      current = null;
    },
  });
}
