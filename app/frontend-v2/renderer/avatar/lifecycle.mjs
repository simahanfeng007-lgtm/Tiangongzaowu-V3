// §20.2 插件生命周期统一管理。
// mount 返回 cleanup；统一登记并释放 bus 订阅、state 订阅、DOM 事件、
// ResizeObserver、RAF、Object URL 与自定义清理。计数可审计，重复 mount/unmount 不泄漏。
// DOM/RAF 能力由宿主注入，Node 测试环境可传入 fake。

export function createLifecycleScope({
  requestAnimationFrame: rafImpl,
  cancelAnimationFrame: cancelRafImpl,
  ResizeObserver: resizeObserverImpl,
} = {}) {
  const raf = rafImpl ?? globalThis.requestAnimationFrame?.bind(globalThis);
  const cancelRaf = cancelRafImpl ?? globalThis.cancelAnimationFrame?.bind(globalThis);
  const ResizeObserverCtor = resizeObserverImpl ?? globalThis.ResizeObserver;

  let mounted = false;
  let released = false;
  const subscriptions = new Set(); // bus/state 订阅的退订函数
  const domListeners = new Set(); // { target, type, handler, options }
  const observers = new Set();
  const rafIds = new Set();
  const objectUrls = new Set();
  const cleanups = new Set(); // 插件 mount 返回的 cleanup 与其他自定义清理

  function assertActive(operation) {
    if (released) throw new Error(`lifecycle 已释放，禁止${operation}`);
  }

  // 登记 bus/state 订阅：传入订阅函数，返回退订函数句柄。
  function trackSubscription(unsubscribe) {
    assertActive("登记订阅");
    if (typeof unsubscribe !== "function") throw new Error("订阅退订必须是函数");
    subscriptions.add(unsubscribe);
    return () => {
      if (subscriptions.delete(unsubscribe)) unsubscribe();
    };
  }

  // 登记 DOM 事件：立即 addEventListener，unmount 时统一 removeEventListener。
  function trackDomListener(target, type, handler, options) {
    assertActive("登记 DOM 事件");
    if (!target?.addEventListener) throw new Error("目标不支持 addEventListener");
    target.addEventListener(type, handler, options);
    const record = { target, type, handler, options };
    domListeners.add(record);
    return () => {
      if (domListeners.delete(record)) {
        target.removeEventListener(type, handler, options);
      }
    };
  }

  // 登记 ResizeObserver：立即 observe，unmount 时统一 disconnect。
  function trackResizeObserver(target, callback) {
    assertActive("登记 ResizeObserver");
    if (typeof ResizeObserverCtor !== "function") {
      throw new Error("当前环境无 ResizeObserver");
    }
    const observer = new ResizeObserverCtor(callback);
    observer.observe(target);
    observers.add(observer);
    return () => {
      if (observers.delete(observer)) observer.disconnect();
    };
  }

  // 登记 RAF：回调执行后自动从计数中移除；unmount 时统一 cancel。
  function trackRaf(callback) {
    assertActive("登记 RAF");
    if (typeof raf !== "function") throw new Error("当前环境无 requestAnimationFrame");
    const holder = { id: null };
    holder.id = raf((timestamp) => {
      rafIds.delete(holder.id);
      callback(timestamp);
    });
    rafIds.add(holder.id);
    return () => {
      if (rafIds.delete(holder.id) && typeof cancelRaf === "function") {
        cancelRaf(holder.id);
      }
    };
  }

  // 登记 Object URL（§20.2 统一释放项）。
  function trackObjectUrl(url, revokeImpl) {
    assertActive("登记 Object URL");
    const revoke = revokeImpl ?? globalThis.URL?.revokeObjectURL?.bind(globalThis.URL);
    objectUrls.add({ url, revoke });
    return () => {
      for (const record of [...objectUrls]) {
        if (record.url === url) {
          objectUrls.delete(record);
          record.revoke?.(url);
        }
      }
    };
  }

  // 登记任意自定义清理（模型资源、disposeModel 等）。
  function trackCleanup(cleanup) {
    assertActive("登记清理");
    if (typeof cleanup !== "function") throw new Error("cleanup 必须是函数");
    cleanups.add(cleanup);
    return () => {
      if (cleanups.delete(cleanup)) cleanup();
    };
  }

  // 挂载插件：插件 mount(context) 可返回 cleanup，统一纳入登记。
  function mount(plugin, context) {
    assertActive("挂载");
    if (mounted) throw new Error("lifecycle 重复 mount，请先 unmount");
    mounted = true;
    const returned = plugin?.mount?.(context ?? createMountContext());
    if (typeof returned === "function") trackCleanup(returned);
    return unmount;
  }

  // 提供给插件的登记上下文。
  function createMountContext() {
    return Object.freeze({
      trackSubscription,
      trackDomListener,
      trackResizeObserver,
      trackRaf,
      trackObjectUrl,
      trackCleanup,
    });
  }

  // 统一释放：幂等，重复 unmount 不产生二次副作用。
  function unmount() {
    if (released) return counts();
    released = true;
    mounted = false;
    for (const unsubscribe of [...subscriptions].reverse()) {
      subscriptions.delete(unsubscribe);
      unsubscribe();
    }
    for (const record of [...domListeners]) {
      domListeners.delete(record);
      record.target.removeEventListener(record.type, record.handler, record.options);
    }
    for (const observer of [...observers]) {
      observers.delete(observer);
      observer.disconnect();
    }
    if (typeof cancelRaf === "function") {
      for (const id of [...rafIds]) {
        rafIds.delete(id);
        cancelRaf(id);
      }
    } else {
      rafIds.clear();
    }
    for (const record of [...objectUrls]) {
      objectUrls.delete(record);
      record.revoke?.(record.url);
    }
    for (const cleanup of [...cleanups].reverse()) {
      cleanups.delete(cleanup);
      cleanup();
    }
    return counts();
  }

  // 可审计计数：全部归零表示无泄漏。
  function counts() {
    return Object.freeze({
      subscriptions: subscriptions.size,
      listeners: domListeners.size,
      observers: observers.size,
      raf: rafIds.size,
      objectUrls: objectUrls.size,
      cleanups: cleanups.size,
    });
  }

  return Object.freeze({
    mount,
    unmount,
    counts,
    trackSubscription,
    trackDomListener,
    trackResizeObserver,
    trackRaf,
    trackObjectUrl,
    trackCleanup,
    get isMounted() { return mounted; },
    get isReleased() { return released; },
  });
}
