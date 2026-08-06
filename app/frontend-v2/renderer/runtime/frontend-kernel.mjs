import { LIFE_API_ROUTES } from "./life-api.mjs";

const desktopMetadata = globalThis.tiangongDesktop?.getFrontendMetadata?.()
  || globalThis.tiangongDesktop?.frontendMetadata
  || {};

export const FRONTEND_KERNEL_VERSION = String(desktopMetadata.kernelVersion || desktopMetadata.version || "3.0");
export const FRONTEND_CONTRACT_ID = "tiangong.frontend.kernel.v3.0-complete";
export const PRODUCT_BUILD_ID = String(desktopMetadata.buildId || "tiangong-v3.0-complete-life-transaction");
export const BACKEND_CONTRACT_ID = "tiangong.total-gateway.api.v1";

const DEFAULT_API_BASE = "http://127.0.0.1:7184";
const DEFAULT_TIMEOUT_MS = 30000;

function normalizeBase(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function endpoint(base, path) {
  return `${base}${String(path || "").startsWith("/") ? "" : "/"}${path}`;
}

function errorDetail(data, fallback = "") {
  if (data && typeof data === "object") {
    return String(
      data?.detail
      || data?.error?.message
      || data?.error
      || data?.message
      || data?.cuowu
      || fallback
    ).trim();
  }
  return String(data || fallback).replace(/\s+/g, " ").slice(0, 800).trim();
}

function errorCode(data, status) {
  const candidate = String(data?.error_code || data?.error?.code || data?.code || "").trim();
  if (/^[A-Za-z0-9_.:-]{1,160}$/.test(candidate)) return candidate;
  return status === 401 ? "backend_unauthorized" : `backend_http_${status}`;
}

function immutableSnapshot(value) {
  if (typeof structuredClone === "function") {
    try { return structuredClone(value); } catch {}
  }
  return JSON.parse(JSON.stringify(value));
}

export class FrontendKernelError extends Error {
  constructor(message, { code = "frontend_kernel_error", status = 0, path = "", cause = null, data = null } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = "FrontendKernelError";
    this.code = code;
    this.status = Number(status || 0);
    this.path = String(path || "");
    this.data = data;
  }
}

export function createFrontendKernel({
  bridge = typeof window !== "undefined" ? window.tiangongDesktop : null,
  fetchImpl = typeof window !== "undefined" ? window.fetch?.bind(window) : null,
  locationRef = typeof window !== "undefined" ? window.location : null,
  setTimeoutImpl = typeof window !== "undefined" ? window.setTimeout.bind(window) : setTimeout,
  clearTimeoutImpl = typeof window !== "undefined" ? window.clearTimeout.bind(window) : clearTimeout,
  now = () => Date.now(),
} = {}) {
  const listeners = new Set();
  let state = {
    schema: FRONTEND_CONTRACT_ID,
    version: FRONTEND_KERNEL_VERSION,
    phase: "created",
    generation: 0,
    compatible: null,
    backend: {
      baseUrl: "",
      buildId: "",
      apiContract: "",
      schemaVersion: "",
      connected: false,
    },
    life: {
      ready: null,
      available: null,
      degraded: false,
      error: "",
      warning: "",
      phase: "unknown",
    },
    lastProbeAt: 0,
    lastSuccessAt: 0,
    lastError: null,
  };

  function baseUrl() {
    const bridged = normalizeBase(
      typeof bridge?.getGatewayUrl === "function"
        ? bridge.getGatewayUrl()
        : typeof bridge?.getBackendUrl === "function"
          ? bridge.getBackendUrl()
          : bridge?.gatewayUrl || bridge?.backendUrl
    );
    const browserBase = locationRef && /^https?:$/.test(String(locationRef.protocol || ""))
      ? normalizeBase(locationRef.origin)
      : "";
    return bridged || browserBase || DEFAULT_API_BASE;
  }

  function lifeBaseUrl() {
    return baseUrl();
  }

  function communicationBaseUrl() {
    return baseUrl();
  }

  function authHeaders() {
    const headers = typeof bridge?.getGatewayHeaders === "function"
      ? bridge.getGatewayHeaders()
      : typeof bridge?.getBackendHeaders === "function"
        ? bridge.getBackendHeaders()
        : {};
    return headers && typeof headers === "object" ? { ...headers } : {};
  }

  function snapshot() {
    return immutableSnapshot(state);
  }

  function emit(next) {
    state = { ...state, ...next };
    const value = snapshot();
    for (const listener of listeners) {
      try { listener(value); } catch {}
    }
    return value;
  }

  function onState(listener) {
    if (typeof listener !== "function") return () => {};
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  async function open(path, options = {}) {
    if (typeof fetchImpl !== "function") {
      throw new FrontendKernelError("浏览器网络接口不可用", { code: "fetch_unavailable", path });
    }
    const {
      timeoutMs = DEFAULT_TIMEOUT_MS,
      signal: externalSignal,
      headers = {},
      body,
      ...fetchOptions
    } = options;
    const controller = new AbortController();
    let timedOut = false;
    let timer = null;
    const abortFromExternal = () => controller.abort(externalSignal?.reason || new Error("request_aborted"));
    if (externalSignal?.aborted) abortFromExternal();
    externalSignal?.addEventListener?.("abort", abortFromExternal, { once: true });
    if (Number(timeoutMs) > 0) {
      timer = setTimeoutImpl(() => {
        timedOut = true;
        controller.abort(new Error("request_timeout"));
      }, Number(timeoutMs));
    }
    try {
      return await fetchImpl(endpoint(baseUrl(), path), {
        ...fetchOptions,
        body,
        signal: controller.signal,
        headers: {
          "Accept": "application/json",
          ...authHeaders(),
          ...headers,
        },
      });
    } catch (cause) {
      const code = timedOut ? "request_timeout" : externalSignal?.aborted ? "request_aborted" : "network_error";
      const message = timedOut
        ? `请求超时（${Math.round(Number(timeoutMs || 0) / 1000)} 秒）`
        : externalSignal?.aborted
          ? "请求已中断。"
          : `无法连接后端：${cause?.message || cause}`;
      throw new FrontendKernelError(message, { code, path, cause });
    } finally {
      if (timer) clearTimeoutImpl(timer);
      externalSignal?.removeEventListener?.("abort", abortFromExternal);
    }
  }

  async function request(path, options = {}) {
    const body = options.body && typeof options.body === "object" && !(options.body instanceof ArrayBuffer)
      ? JSON.stringify(options.body)
      : options.body;
    const headers = {
      ...(body ? { "Content-Type": "application/json; charset=utf-8" } : {}),
      ...(options.headers || {}),
    };
    const response = await open(path, { ...options, body, headers });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!response.ok) {
      if (typeof data === "string") {
        throw new FrontendKernelError(`后端返回了网页错误页或非 JSON 响应（状态码 ${response.status}）。`, {
          code: "backend_non_json",
          status: response.status,
          path,
        });
      }
      const detail = errorDetail(data, response.statusText || `HTTP ${response.status}`);
      throw new FrontendKernelError(detail || `HTTP ${response.status}`, {
        code: errorCode(data, response.status),
        status: response.status,
        path,
        data,
      });
    }
    if (typeof data === "string") {
      throw new FrontendKernelError(`后端返回了网页错误页或非 JSON 响应（状态码 ${response.status}）。`, {
        code: "backend_non_json",
        status: response.status,
        path,
      });
    }
    return data;
  }

  async function optional(path, options = {}) {
    try {
      return { ok: true, value: await request(path, options), error: null };
    } catch (error) {
      return { ok: false, value: null, error };
    }
  }

  async function probe() {
    const generation = Number(state.generation || 0) + 1;
    emit({ phase: "connecting", generation, lastProbeAt: now(), lastError: null });
    try {
      const health = await request("/health", { timeoutMs: 5000 });
      const [v3State, lifePanel] = await Promise.all([
        // 本地生命内核的 panel/state 在调度器写入时会偶发数秒响应；
        // UI 就绪探测超时必须宽于接口实际耗时，否则发送门被误锁。
        optional(LIFE_API_ROUTES.state.path, { timeoutMs: 20000 }),
        optional(LIFE_API_ROUTES.panel.path, { timeoutMs: 20000 }),
      ]);
      const apiContract = String(health?.api_contract || "");
      const compatible = health?.component_id === "tiangong-total-gateway"
        && apiContract === BACKEND_CONTRACT_ID;
      const uiLifecycle = v3State.value?.ui?.lifecycle || {};
      const lifeSetupRequired = v3State.value?.setup_required === true || lifePanel.value?.setup_required === true;
      const lifeError = String(health?.life_error || lifePanel.error?.message || "");
      const lifeWarning = String(uiLifecycle?.metrics_unavailable_reason || "");
      const lifeReady = !lifeSetupRequired && health?.life_ready !== false && lifePanel.ok;
      const lifeAvailable = !lifeSetupRequired && lifePanel.ok && uiLifecycle.available !== false;
      const degraded = Boolean(!lifeSetupRequired && (health?.degraded || !lifeReady || !lifeAvailable || lifeError));
      const phase = !compatible ? "incompatible" : lifeSetupRequired ? "setup_required" : degraded ? "degraded" : "ready";
      return emit({
        phase,
        compatible,
        backend: {
          baseUrl: baseUrl(),
          buildId: PRODUCT_BUILD_ID,
          apiContract,
          schemaVersion: String(health?.schema_version || ""),
          connected: true,
        },
        life: {
          ready: lifeReady,
          available: lifeAvailable,
          degraded,
          error: lifeError,
          warning: lifeWarning,
          phase: lifeSetupRequired ? "unbound" : String(uiLifecycle?.phase || (lifeReady ? "alive" : "unavailable")),
        },
        lastSuccessAt: now(),
        lastError: !compatible
          ? { code: "backend_contract_mismatch", message: `后端契约不兼容：${apiContract || "unknown"}` }
          : lifeSetupRequired
            ? { code: "life_setup_required", message: "生命身份需要迁移或绑定；发送已暂停以保护原身份。" }
          : degraded
            ? { code: "life_kernel_degraded", message: lifeError || "生命内核尚未就绪" }
            : null,
      });
    } catch (error) {
      return emit({
        phase: "offline",
        compatible: null,
        backend: { ...state.backend, baseUrl: baseUrl(), connected: false },
        life: { ...state.life, ready: false, available: false, degraded: true, error: error?.message || String(error) },
        lastError: { code: error?.code || "probe_failed", message: error?.message || String(error) },
      });
    }
  }

  async function boot() {
    emit({ phase: "booting" });
    return probe();
  }

  return Object.freeze({
    version: FRONTEND_KERNEL_VERSION,
    contractId: FRONTEND_CONTRACT_ID,
    backendContractId: BACKEND_CONTRACT_ID,
    baseUrl,
    lifeBaseUrl,
    communicationBaseUrl,
    authHeaders,
    snapshot,
    onState,
    open,
    request,
    optional,
    probe,
    boot,
  });
}
