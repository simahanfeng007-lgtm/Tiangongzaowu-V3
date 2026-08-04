// §20.1 Core 服务注册表：单例语义、重复注册拒绝、dispose 全量清理。

export function createServiceRegistry() {
  const services = new Map();

  function registerService(id, service) {
    const key = String(id || "").trim();
    if (!key) throw new Error("registerService 需要非空 id");
    if (service === null || typeof service !== "object") {
      throw new Error(`服务 ${key} 必须是对象`);
    }
    if (services.has(key)) {
      throw new Error(`服务 ${key} 已注册，拒绝重复注册`);
    }
    services.set(key, service);
    return service;
  }

  function getService(id) {
    const key = String(id || "").trim();
    if (!services.has(key)) {
      throw new Error(`服务 ${key} 未注册`);
    }
    return services.get(key);
  }

  function hasService(id) {
    return services.has(String(id || "").trim());
  }

  // 按注册逆序释放，保证后注册者先清理；单个 dispose 抛错不中断其余清理。
  function disposeAllServices() {
    const entries = [...services.entries()].reverse();
    services.clear();
    const errors = [];
    for (const [key, service] of entries) {
      try {
        service.dispose?.();
      } catch (error) {
        errors.push({ id: key, error });
      }
    }
    return { disposed: entries.length, errors };
  }

  function serviceIds() {
    return [...services.keys()];
  }

  return Object.freeze({
    registerService,
    getService,
    hasService,
    disposeAllServices,
    serviceIds,
  });
}

// 进程内默认单例注册表；AvatarRuntime 是服务，不伪装成页面插件（§20.1）。
const defaultRegistry = createServiceRegistry();

export const registerService = defaultRegistry.registerService;
export const getService = defaultRegistry.getService;
export const hasService = defaultRegistry.hasService;
export const disposeAllServices = defaultRegistry.disposeAllServices;
