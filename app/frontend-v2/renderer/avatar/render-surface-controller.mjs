// §14 RenderSurface：acquire(host, mode)/release(lease) 租约。
// 当前版本单一 Canvas/单 GPU context 约束（§4.3/§14.1）：重复 acquire 报 lease 冲突错误。
// attach/detach/rehost 只移动 Surface 宿主，不重解析模型（§14.3）；尺寸/DPI 变化发事件。

export const SURFACE_CONTROLLER_SCHEMA_VERSION = 1;

// §19.1 探针尺寸下限的默认值（可随版本校准）。
export const MIN_SURFACE_SIZE = Object.freeze({ width: 32, height: 32 });

export class SurfaceLeaseError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "SurfaceLeaseError";
    this.code = code;
  }
}

function normalizeViewport(raw) {
  if (raw === null || typeof raw !== "object") return null;
  const width = Number(raw.width);
  const height = Number(raw.height);
  const dpr = Number.isFinite(raw.dpr) && raw.dpr > 0 ? raw.dpr : 1;
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  return { width: Math.max(0, width), height: Math.max(0, height), dpr };
}

// host 鸭子类型：{ id?, isVisible?() → boolean, getViewport?() → { width, height, dpr } }。
// DOM 元素/Electron webContents/测试替身均可；controller 不直接触碰 DOM。
export function createRenderSurfaceController({ nowMonotonic, sizeStableWindowMs = 250 } = {}) {
  if (typeof nowMonotonic !== "function") {
    throw new SurfaceLeaseError("clock_required", "RenderSurfaceController 需要注入单调时钟 nowMonotonic");
  }
  if (!Number.isFinite(sizeStableWindowMs) || sizeStableWindowMs < 0) {
    throw new SurfaceLeaseError("stable_window_invalid", "sizeStableWindowMs 必须是非负数");
  }

  let leaseSeq = 0;
  let activeLease = null;
  let viewport = null; // { width, height, dpr }
  let viewportChangedAtMonotonic = null;
  let dprTransitioning = false;
  const listeners = new Set();

  function emitChange(type, detail = {}) {
    const event = Object.freeze({ type, atMonotonic: nowMonotonic(), ...detail });
    for (const listener of [...listeners]) {
      try {
        listener(event);
      } catch (_error) {
        // 监听器异常不阻断 Surface 控制路径
      }
    }
  }

  function assertHost(host) {
    if (host === null || typeof host !== "object") {
      throw new SurfaceLeaseError("host_invalid", "acquire 需要 host 对象（{ id?, isVisible?, getViewport? }）");
    }
    if (host.isVisible !== undefined && typeof host.isVisible !== "function") {
      throw new SurfaceLeaseError("host_invalid", "host.isVisible 必须是函数");
    }
    if (host.getViewport !== undefined && typeof host.getViewport !== "function") {
      throw new SurfaceLeaseError("host_invalid", "host.getViewport 必须是函数");
    }
  }

  function readHostViewport(host) {
    return normalizeViewport(typeof host.getViewport === "function" ? host.getViewport() : null);
  }

  function acquire(host, mode = "primary") {
    assertHost(host);
    if (typeof mode !== "string" || mode.length === 0) {
      throw new SurfaceLeaseError("mode_invalid", "acquire 需要非空 mode（如 chat/body-editor/primary）");
    }
    if (activeLease !== null && !activeLease.released) {
      // §14.1/§4.3：单一 Canvas/单 GPU context；重复 acquire 是 lease 冲突错误，不静默接管。
      throw new SurfaceLeaseError(
        "lease_conflict",
        `Surface 已被 lease=${activeLease.leaseId} 持有（mode=${activeLease.mode}），当前版本只允许单一 Canvas/单 GPU context`,
      );
    }
    leaseSeq += 1;
    activeLease = {
      leaseId: `lease_${leaseSeq}`,
      host,
      mode,
      acquiredAtMonotonic: nowMonotonic(),
      released: false,
    };
    viewport = readHostViewport(host);
    viewportChangedAtMonotonic = nowMonotonic();
    emitChange("acquired", { leaseId: activeLease.leaseId, mode });
    const leaseId = activeLease.leaseId;
    const acquiredAt = activeLease.acquiredAtMonotonic;
    return Object.freeze({
      leaseId,
      mode,
      acquiredAtMonotonic: acquiredAt,
      get released() {
        return activeLease === null || activeLease.leaseId !== leaseId ? true : activeLease.released;
      },
    });
  }

  function assertCurrentLease(lease) {
    if (lease === null || typeof lease !== "object" || typeof lease.leaseId !== "string") {
      throw new SurfaceLeaseError("lease_invalid", "需要 acquire 返回的 lease");
    }
    if (activeLease === null || activeLease.leaseId !== lease.leaseId || activeLease.released) {
      throw new SurfaceLeaseError("lease_stale", "lease 已释放或不属于当前 Surface");
    }
    return activeLease;
  }

  function release(lease) {
    const current = assertCurrentLease(lease);
    current.released = true;
    activeLease = null;
    viewport = null;
    viewportChangedAtMonotonic = null;
    dprTransitioning = false;
    emitChange("released", { leaseId: current.leaseId });
    return true;
  }

  // §14.3 页面切换：同一 lease 更换宿主（聊天页 ↔ 身体页），不重新解析模型。
  function rehost(lease, nextHost) {
    const current = assertCurrentLease(lease);
    assertHost(nextHost);
    current.host = nextHost;
    const nextViewport = readHostViewport(nextHost);
    if (nextViewport !== null) {
      viewport = nextViewport;
      viewportChangedAtMonotonic = nowMonotonic();
    }
    emitChange("rehost", { leaseId: current.leaseId });
    return true;
  }

  // 尺寸/DPI 变化事件：由宿主（ResizeObserver/DPI 监听）驱动注入。
  function updateViewport(next) {
    if (activeLease === null || activeLease.released) {
      throw new SurfaceLeaseError("no_active_lease", "无活动 lease，拒绝 viewport 更新");
    }
    const normalized = normalizeViewport(next);
    if (normalized === null) {
      throw new SurfaceLeaseError("viewport_invalid", "viewport 需要有限 width/height（dpr 可选，默认 1）");
    }
    const changed =
      viewport === null ||
      viewport.width !== normalized.width ||
      viewport.height !== normalized.height ||
      viewport.dpr !== normalized.dpr;
    const dprChanged = viewport !== null && viewport.dpr !== normalized.dpr;
    viewport = normalized;
    if (changed) {
      viewportChangedAtMonotonic = nowMonotonic();
      emitChange("viewport", { viewport: Object.freeze({ ...viewport }), dprChanged });
    }
    return changed;
  }

  function isVisible() {
    if (activeLease === null || activeLease.released) return false;
    const hostVisible = typeof activeLease.host.isVisible === "function" ? activeLease.host.isVisible() : true;
    return hostVisible && viewport !== null && viewport.width > 0 && viewport.height > 0;
  }

  function isAboveMinimum(minimum = MIN_SURFACE_SIZE) {
    return viewport !== null && viewport.width >= minimum.width && viewport.height >= minimum.height;
  }

  function isSizeStable(now = nowMonotonic()) {
    if (viewport === null || viewportChangedAtMonotonic === null) return false;
    return now - viewportChangedAtMonotonic >= sizeStableWindowMs;
  }

  return Object.freeze({
    acquire,
    release,
    rehost,
    updateViewport,
    isVisible,
    isAboveMinimum,
    isSizeStable,
    hasActiveLease: () => activeLease !== null && !activeLease.released,
    currentLease: () =>
      activeLease === null
        ? null
        : Object.freeze({
            leaseId: activeLease.leaseId,
            mode: activeLease.mode,
            acquiredAtMonotonic: activeLease.acquiredAtMonotonic,
          }),
    getViewport: () => (viewport === null ? null : Object.freeze({ ...viewport })),
    // DPI 迁移标记（§19.1：DPI 切换期间探针挂起）。
    setDpiTransitioning(flag) {
      const next = flag === true;
      if (dprTransitioning !== next) {
        dprTransitioning = next;
        emitChange("dpi-transition", { transitioning: next });
      }
    },
    isDpiTransitioning: () => dprTransitioning,
    onDidChange(listener) {
      if (typeof listener !== "function") {
        throw new SurfaceLeaseError("listener_invalid", "onDidChange 需要函数");
      }
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    get listenerCount() {
      return listeners.size;
    },
  });
}
