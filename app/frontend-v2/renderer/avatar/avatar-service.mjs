// P5 §24 渲染模式与 AvatarRuntime 服务注册封装（P1 service-registry 单例）。
//
// 模式（§24）：direct（主前端 AvatarRuntime）| legacy-iframe（迁移期应急回退）| off（禁用 3D）。
//   规则 1：direct 与 legacy 互斥，同时只能一个运行（startMode 冲突报错；setMode 统一先清理再启动）。
//   规则 3：legacy 开关只允许受控构建或本地诊断——本 flag 为本地诊断 flag，
//           默认 legacy-iframe 直到 P6 生产切换；渲染面板据此决定显隐。
//
// 单例证明：创建时经 P1 service-registry 登记 "avatar-service" 外观，
//   重复创建（同 registry）由 registry 拒绝重复注册而抛错。
//
// Surface rehost（§14.3）：direct 模式下经 rehostSurface(nextHost) 迁移同一 lease 宿主，
//   只移动 Surface，不重解析模型（不触发 selectModel/loadCandidate）。

import { deepFreeze } from "./canonical-hash.mjs";

export const AVATAR_SERVICE_SCHEMA_VERSION = 1;
export const AVATAR_SERVICE_ID = "avatar-service";
export const AVATAR_MODE_FLAG_KEY = "tiangong.avatar.renderMode";

export const AvatarRenderMode = Object.freeze({
  DIRECT: "direct",
  LEGACY_IFRAME: "legacy-iframe",
  OFF: "off",
});

export const AVATAR_RENDER_MODES = Object.freeze(Object.values(AvatarRenderMode));

// §24/§26 P6：direct 已经源码 E2E 验证（双内置模型 FIRST_VISIBLE_FRAME + 纹理齐备），
// 成为默认渲染模式；legacy-iframe 保留为应急回退（仅本地诊断 flag 可达）。
export const DEFAULT_AVATAR_RENDER_MODE = AvatarRenderMode.DIRECT;

export class AvatarServiceError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AvatarServiceError";
    this.code = code;
  }
}

export function sanitizeRenderMode(raw) {
  return AVATAR_RENDER_MODES.includes(raw) ? raw : DEFAULT_AVATAR_RENDER_MODE;
}

// flagStorage：localStorage 形 { getItem, setItem }（注入式，测试用内存替身）。
export function createAvatarService({
  registry = null,
  flagStorage,
  nowMonotonic,
  createDirectRuntime = null,
  createLegacyBridge = null,
} = {}) {
  if (flagStorage === null || typeof flagStorage !== "object" ||
      typeof flagStorage.getItem !== "function" || typeof flagStorage.setItem !== "function") {
    throw new AvatarServiceError("flag_storage_invalid", "AvatarService 需要注入 flagStorage {getItem,setItem}");
  }
  if (typeof nowMonotonic !== "function") {
    throw new AvatarServiceError("clock_required", "AvatarService 需要注入单调时钟 nowMonotonic");
  }

  let disposed = false;
  // 当前运行中的模式实例：{ mode, handle, startedAtMonotonic }
  //   direct: handle = { runtime, surfaceController, engineAdapter, lease }
  //   legacy: handle = createLegacyBridge() 产物（{ stop?/dispose? } 鸭子类型）或 null
  let current = null;
  let legacyEpoch = 0;

  function readFlagMode() {
    let raw = null;
    try {
      raw = flagStorage.getItem(AVATAR_MODE_FLAG_KEY);
    } catch (_error) {
      raw = null; // storage 不可用：安全回退默认模式
    }
    return sanitizeRenderMode(raw);
  }

  function persistFlagMode(mode) {
    try {
      flagStorage.setItem(AVATAR_MODE_FLAG_KEY, mode);
    } catch (_error) {
      // flag 持久化失败不阻断运行态切换（下次启动回退默认）
    }
  }

  function assertNotDisposed() {
    if (disposed) throw new AvatarServiceError("service_disposed", "AvatarService 已 dispose");
  }

  // §24 规则 1：互斥——已有运行模式时禁止并行启动另一模式（不经 setMode 清理路径）。
  function assertNoConflict(mode) {
    if (current !== null && current.mode !== mode) {
      throw new AvatarServiceError(
        "mode_conflict",
        `当前 ${current.mode} 运行中，禁止并行启动 ${mode}（§24：direct 与 legacy 互斥；切换请走 setMode 生命周期清理）`,
      );
    }
  }

  function startDirect() {
    if (typeof createDirectRuntime !== "function") {
      throw new AvatarServiceError("direct_factory_missing", "direct 模式需要注入 createDirectRuntime 工厂");
    }
    const produced = createDirectRuntime();
    if (produced === null || typeof produced !== "object" || produced.runtime === null || typeof produced.runtime !== "object") {
      throw new AvatarServiceError("direct_factory_invalid", "createDirectRuntime 必须返回 { runtime, surfaceController?, engineAdapter? }");
    }
    return {
      runtime: produced.runtime,
      surfaceController: produced.surfaceController ?? null,
      engineAdapter: produced.engineAdapter ?? null,
      lease: null,
    };
  }

  function startLegacy() {
    legacyEpoch += 1;
    if (typeof createLegacyBridge === "function") {
      const bridge = createLegacyBridge({ epoch: legacyEpoch });
      return bridge === null || typeof bridge !== "object" ? { epoch: legacyEpoch } : bridge;
    }
    // legacy 实际 UI 由 vrm-inspector-panel（iframe）承担（P7 才清理）；
    // service 只记账模式状态，证明 direct/legacy 互斥。
    return { epoch: legacyEpoch };
  }

  function startMode(mode) {
    assertNotDisposed();
    const nextMode = sanitizeRenderMode(mode);
    assertNoConflict(nextMode);
    if (current !== null && current.mode === nextMode) return current.mode; // 幂等
    if (nextMode === AvatarRenderMode.DIRECT) {
      current = { mode: nextMode, handle: startDirect(), startedAtMonotonic: nowMonotonic() };
    } else if (nextMode === AvatarRenderMode.LEGACY_IFRAME) {
      current = { mode: nextMode, handle: startLegacy(), startedAtMonotonic: nowMonotonic() };
    } else {
      current = null; // off：禁用 3D，无运行实例
    }
    return nextMode;
  }

  // 统一生命周期清理：模式切换与 dispose 的唯一出口。
  function stopCurrent() {
    if (current === null) return false;
    const handle = current.handle;
    current = null;
    try {
      handle?.runtime?.dispose?.();
    } finally {
      handle?.dispose?.();
      handle?.stop?.();
    }
    return true;
  }

  // 模式切换：先清理（dispose/stop），再持久化 flag，再启动目标模式。
  function setMode(mode) {
    assertNotDisposed();
    const nextMode = sanitizeRenderMode(mode);
    if (current !== null && current.mode === nextMode) return current.mode;
    stopCurrent();
    persistFlagMode(nextMode);
    return startMode(nextMode);
  }

  function getRuntime() {
    return current !== null && current.mode === AvatarRenderMode.DIRECT ? current.handle.runtime : null;
  }

  // §14.2/§14.3：direct 模式 Surface 挂接（面板调用）；lease 由 service 持有以便 rehost。
  function attachSurface(host, mode = "primary") {
    assertNotDisposed();
    const runtime = getRuntime();
    if (runtime === null) {
      throw new AvatarServiceError("mode_not_direct", "attachSurface 仅 direct 模式可用");
    }
    if (current.handle.lease !== null) {
      throw new AvatarServiceError("surface_already_attached", "Surface 已挂接，先 detachSurface 或 rehostSurface");
    }
    const lease = runtime.attachSurface({ host, mode });
    current.handle.lease = lease;
    return lease;
  }

  function detachSurface() {
    const runtime = getRuntime();
    if (runtime === null || current.handle.lease === null) return false;
    const released = runtime.detachSurface(current.handle.lease);
    current.handle.lease = null;
    return released;
  }

  // §14.3 页面切换：同一 lease 迁移宿主（聊天页右侧 ↔ 身体页大编辑区），不重解析模型。
  function rehostSurface(nextHost) {
    assertNotDisposed();
    if (current === null || current.mode !== AvatarRenderMode.DIRECT || current.handle.lease === null) {
      throw new AvatarServiceError("surface_not_attached", "rehostSurface 需要 direct 模式且 Surface 已挂接");
    }
    const { surfaceController, engineAdapter, lease } = current.handle;
    if (surfaceController === null || typeof surfaceController.rehost !== "function") {
      throw new AvatarServiceError("rehost_unsupported", "当前 direct 运行时未注入可 rehost 的 surfaceController");
    }
    surfaceController.rehost(lease, nextHost); // 租约层迁移宿主（§14.3）
    // 引擎层移动 Canvas：只换宿主，不触发模型重解析（loadCandidate 计数不变）。
    engineAdapter?.attachSurface?.({ host: nextHost, leaseId: lease.leaseId });
    return true;
  }

  const service = {
    startMode,
    setMode,
    stopCurrent,
    // 有效模式：有运行实例以实例为准；off/未启动回退 flag（面板据此决定显隐）。
    getMode: () => (current === null ? readFlagMode() : current.mode),
    getActiveMode: () => (current === null ? null : current.mode),
    readFlagMode,
    isDirectActive: () => current !== null && current.mode === AvatarRenderMode.DIRECT,
    getRuntime,
    // direct 模式的 Surface 控制器（面板 ResizeObserver → updateViewport 用）；非 direct 为 null。
    getSurfaceController: () =>
      current !== null && current.mode === AvatarRenderMode.DIRECT ? current.handle.surfaceController : null,
    attachSurface,
    detachSurface,
    rehostSurface,
    get legacyEpoch() {
      return legacyEpoch;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stopCurrent();
    },
  };

  // P1 service-registry 单例：重复创建由 registry 拒绝（N_activeAvatarService=1）。
  if (registry !== null) {
    registry.registerService(AVATAR_SERVICE_ID, service);
  }
  return Object.freeze(service);
}
