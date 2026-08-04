// P5 状态链：AvatarRuntime → AvatarStore → 面板/设置页/诊断页。
// 只读投影 + 订阅（lifecycle 释放）；store 不调用任何 runtime 变更接口，
// 业务侧变更一律经 BodyCommandScheduler / AvatarService，保证状态链单向。

import { deepFreeze } from "./canonical-hash.mjs";

export const AVATAR_STORE_SCHEMA_VERSION = 1;

export class AvatarStoreError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AvatarStoreError";
    this.code = code;
  }
}

export function createAvatarStore({ nowMonotonic, mode = "legacy-iframe" } = {}) {
  if (typeof nowMonotonic !== "function") {
    throw new AvatarStoreError("clock_required", "AvatarStore 需要注入单调时钟 nowMonotonic");
  }

  let runtime = null;
  let runtimeUnsubscribe = null;
  let currentMode = mode;
  let disposed = false;
  const listeners = new Set();

  function projection() {
    const snap = runtime === null ? null : runtime.snapshot();
    return deepFreeze({
      schemaVersion: AVATAR_STORE_SCHEMA_VERSION,
      mode: currentMode,
      runtimeState: snap?.state ?? null,
      currentModel: snap?.current ?? null,
      pending: snap?.pending ?? null,
      paused: snap?.paused ?? false,
      safeMode: snap?.safeMode ?? null,
      bodyStateVersion: snap?.bodyStateVersion ?? 0,
      lastRequestedModelId: snap?.lastRequestedModelId ?? null,
      lastCommittedModelId: snap?.lastCommittedModelId ?? null,
      updatedAtMonotonic: nowMonotonic(),
    });
  }

  function notify() {
    if (disposed) return;
    const snap = projection();
    for (const listener of [...listeners]) {
      try {
        listener(snap);
      } catch (_error) {
        // 监听器异常不阻断状态链
      }
    }
  }

  // 绑定/换绑 runtime（模式切换时由 service 驱动）；重复绑定同一实例为幂等。
  function bindRuntime(nextRuntime) {
    if (disposed) throw new AvatarStoreError("store_disposed", "AvatarStore 已 dispose");
    if (nextRuntime !== null && (typeof nextRuntime !== "object" || typeof nextRuntime.snapshot !== "function")) {
      throw new AvatarStoreError("runtime_invalid", "bindRuntime 需要 AvatarRuntime 或 null");
    }
    if (runtimeUnsubscribe !== null) {
      runtimeUnsubscribe();
      runtimeUnsubscribe = null;
    }
    runtime = nextRuntime;
    if (runtime !== null && typeof runtime.subscribe === "function") {
      runtimeUnsubscribe = runtime.subscribe(() => notify());
    }
    notify();
  }

  function setMode(nextMode) {
    if (disposed) throw new AvatarStoreError("store_disposed", "AvatarStore 已 dispose");
    if (typeof nextMode !== "string" || nextMode.length === 0) {
      throw new AvatarStoreError("mode_invalid", "setMode 需要非空模式字符串");
    }
    if (currentMode === nextMode) return;
    currentMode = nextMode;
    notify();
  }

  function subscribe(listener) {
    if (typeof listener !== "function") {
      throw new AvatarStoreError("listener_invalid", "subscribe 需要函数");
    }
    if (disposed) throw new AvatarStoreError("store_disposed", "AvatarStore 已 dispose");
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (runtimeUnsubscribe !== null) {
      runtimeUnsubscribe();
      runtimeUnsubscribe = null;
    }
    runtime = null;
    listeners.clear();
  }

  return deepFreeze({
    bindRuntime,
    setMode,
    subscribe,
    projection,
    refresh: notify,
    dispose,
    getMode: () => currentMode,
    get listenerCount() {
      return listeners.size;
    },
    get isBound() {
      return runtime !== null;
    },
  });
}
