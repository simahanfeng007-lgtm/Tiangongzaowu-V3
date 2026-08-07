import { LIFE_API_ROUTES } from "./life-api.mjs";
import { projectionToProgressSteps } from "./gateway-ui-projection.mjs";
import { createBackendInstanceBridge } from "./backend-instance.mjs";
import {
  AVATAR_CAMERA_DEFAULTS,
  AVATAR_LIGHTING_DEFAULTS,
  normalizeAvatarPresentation,
} from "../avatar/presentation-settings.mjs";

// P6a §15.4：后端实例标识桥（会话级单例；window 惰性解析，测试可经导出重置）。
const backendInstanceBridge = createBackendInstanceBridge();

const SETTINGS_KEY = "tiangong_frontend_settings";
const DEFAULT_API_BASE = "http://127.0.0.1:7184";
const DEFAULT_API_TIMEOUT_MS = 30000;
// A complex engineering turn can legitimately spend several minutes inside one
// model call while it prepares a large file.write payload.  Treat this as an
// inactivity watchdog, not a short HTTP deadline. Progress polling still resets
// the timer on every new run step, and the user can always stop the run.
const CHAT_API_TIMEOUT_MS = 900000;
const SSE_STATUS_RETRY_MS = 750;
const SSE_STATUS_MISS_LIMIT = 80;
const RUNTIME_STATE_RETRY_LIMIT = 12;
const RUNTIME_STATE_RETRY_MS = 750;
const SSE_MAX_BUFFER_CHARS = 2 * 1024 * 1024;
const SSE_MAX_EVENT_CHARS = 1024 * 1024;
const SSE_MAX_SEEN_EVENT_IDS = 4096;
const PROGRESS_PRESENTATION_DWELL_MS = 550;
const PROGRESS_TEXT_DWELL_MS = 180;
const PROGRESS_PRESENTATION_MAX_WAIT_MS = 6000;
let frontendKernel = null;
let workspaceAuthorityRevision = null;

const defaultSettings = {
  workspace: "",
  workspace_mode: "workspace",
  mode: "chat",
  personaName: "起源",
  soulPrompt: "",
  personaAvatarDataUrl: "",
  bodyPreset: "standard",
  bodyCamera: { ...AVATAR_CAMERA_DEFAULTS },
  bodyLighting: { ...AVATAR_LIGHTING_DEFAULTS },
  bodyPresentationConfigured: false,
  bodyVoiceReplyEnabled: false,
  bodyVoicePreset: "qiyuan_clear",
  bodyVoiceName: "",
  bodyVoiceCustomName: "",
  bodyVoiceCustomPath: "",
  bodyVoiceOutputMode: "auto",
  bodyVoiceNativeId: "",
  bodyVoiceSampleConsent: false,
  bodyVoiceLang: "zh-CN",
  bodyVoiceRate: 1,
  bodyVoicePitch: 1.04,
  bodyVoiceVolume: 1,
  bodyVoicePresets: [],
  bodyVoiceCustomState: "empty",
  userName: "",
  userDisplayName: "",
  userCallsign: "",
  userTitle: "",
  userWork: "",
  userAvatarDataUrl: "",
  userProfileSummary: "",
  userContextEnabled: true,
  themeStyle: "ink_teal",
  modelService: "custom",
  modelProvider: "",
  modelBaseUrl: "",
  modelName: "",
  modelApiKey: "",
  modelMatchedProvider: "",
  modelProviderMatch: null,
  modelProviderPresets: [],
  modelProviderProfiles: {},
  knowledgeRoot: "",
  permissionMode: "full_access",
  permissionRiskMax: "A4",
  permissionStatus: null
};

const USER_PERMISSION_MODES = new Set(["request_approval", "auto_approval", "full_access", "custom"]);
const AUTONOMY_RISK_LEVELS = new Set(["A0", "A1", "A2", "A3", "A4"]);
const LOCAL_AVATAR_DATA_URL_MAX = 6 * 1024 * 1024;

function boundedSettingText(value, maxLength = 4096) {
  return String(value ?? "").replace(/\u0000/g, "").slice(0, maxLength);
}

function safeLocalAvatarDataUrl(value) {
  const source = boundedSettingText(value, LOCAL_AVATAR_DATA_URL_MAX);
  return /^data:image\/(?:png|jpeg|jpg|gif|webp|bmp|x-icon);base64,/i.test(source) ? source : "";
}

function normalizeUserPermissionMode(value, fallback = "full_access") {
  const mode = String(value || "").trim();
  return USER_PERMISSION_MODES.has(mode) ? mode : fallback;
}

function normalizeAutonomyRiskMax(value, fallback = "A4") {
  const risk = String(value || "").trim().toUpperCase();
  return AUTONOMY_RISK_LEVELS.has(risk) ? risk : fallback;
}

// The desktop settings panel speaks the four desktop permission modes, while
// the life settings endpoint only accepts permission_mode in
// {autonomous_low_risk, confirm_high_risk, confirm_all} plus an
// autonomous_risk_max cap.  These two helpers translate between the
// vocabularies so the panel never submits an unrecognised mode.
const DESKTOP_MODE_RISK_CAPS = {
  request_approval: "A0",
  auto_approval: "A2",
  full_access: "A4"
};

function lifePermissionPayload(mode, riskMax) {
  const desktopMode = normalizeUserPermissionMode(mode);
  if (desktopMode === "custom") {
    // Keep the authoritative life permission_mode untouched; only the risk
    // cap is user-controlled in custom mode.
    return { autonomous_risk_max: normalizeAutonomyRiskMax(riskMax) };
  }
  return {
    permission_mode: "autonomous_low_risk",
    autonomous_risk_max: DESKTOP_MODE_RISK_CAPS[desktopMode] || "A4"
  };
}

function desktopPermissionFromLife(mode, riskMax, fallbackMode = "full_access", fallbackRisk = "A4") {
  const lifeMode = String(mode || "").trim();
  const risk = String(riskMax || "").trim().toUpperCase();
  const validRisk = AUTONOMY_RISK_LEVELS.has(risk) ? risk : "";
  if (lifeMode === "confirm_all") {
    return { permissionMode: "request_approval", permissionRiskMax: validRisk || fallbackRisk };
  }
  if (lifeMode === "autonomous_low_risk" || lifeMode === "confirm_high_risk") {
    if (validRisk === "A0") return { permissionMode: "request_approval", permissionRiskMax: validRisk };
    if (validRisk === "A1" || validRisk === "A2") return { permissionMode: "auto_approval", permissionRiskMax: validRisk };
    if (validRisk === "A3" || validRisk === "A4") return { permissionMode: "full_access", permissionRiskMax: validRisk };
    return { permissionMode: normalizeUserPermissionMode(fallbackMode), permissionRiskMax: normalizeAutonomyRiskMax(fallbackRisk) };
  }
  return {
    permissionMode: normalizeUserPermissionMode(lifeMode, normalizeUserPermissionMode(fallbackMode)),
    permissionRiskMax: normalizeAutonomyRiskMax(validRisk, normalizeAutonomyRiskMax(fallbackRisk))
  };
}

function sanitizeLocalSettings(settings = {}) {
  const next = settings && typeof settings === "object" && !Array.isArray(settings) ? { ...settings } : {};
  // Credentials and credential-management intents must never survive in
  // renderer localStorage, regardless of which alias a caller used.
  for (const key of ["modelApiKey", "api_key", "clear_api_key"]) delete next[key];
  next.workspace = boundedSettingText(next.workspace);
  next.knowledgeRoot = boundedSettingText(next.knowledgeRoot);
  next.storageRoot = boundedSettingText(next.storageRoot);
  next.personaAvatarDataUrl = safeLocalAvatarDataUrl(next.personaAvatarDataUrl);
  next.userAvatarDataUrl = safeLocalAvatarDataUrl(next.userAvatarDataUrl);
  const presentation = normalizeAvatarPresentation({
    camera: next.bodyCamera,
    lighting: next.bodyLighting,
  });
  if (Object.prototype.hasOwnProperty.call(next, "bodyCamera")) next.bodyCamera = presentation.camera;
  if (Object.prototype.hasOwnProperty.call(next, "bodyLighting")) next.bodyLighting = presentation.lighting;
  const service = String(next.modelService || "").trim();
  const provider = String(next.modelProvider || "").trim();
  if (next.modelMatchedProvider === "gpt_5_5" && service !== "gpt_5_5" && provider !== "gpt_5_5") {
    next.modelMatchedProvider = "";
  }
  return next;
}

function readLocalSettings() {
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    return sanitizeLocalSettings(raw);
  } catch {
    return {};
  }
}

function writeLocalSettings(settings) {
  const existing = sanitizeLocalSettings(readLocalSettings());
  const incoming = sanitizeLocalSettings(settings);
  const next = sanitizeLocalSettings({ ...existing, ...incoming });
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(next)); } catch {}
  return { ...defaultSettings, ...next };
}

function readLegacyAvatarPresentation() {
  const read = (key) => {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  };
  return normalizeAvatarPresentation({
    camera: read("tiangong-v3-main-camera"),
    lighting: read("tiangong-v3-lighting"),
  });
}

function endpoint(base, path) {
  return `${base}${String(path || "").startsWith("/") ? "" : "/"}${path}`;
}

function normalizeApiBase(value) {
  const raw = String(value || "").trim();
  return raw ? raw.replace(/\/+$/, "") : "";
}

function modelServiceFromLlmStatus(llm, fallback = "custom") {
  const matchReason = String(llm?.provider_match?.reason || "").trim();
  const provider = String(llm?.provider || llm?.matched_provider || "").trim();
  const configuredProvider = String(llm?.configured_provider || "").trim();
  if (matchReason === "unmatched_openai_compatible_fallback" && configuredProvider) {
    return "custom";
  }
  return provider || fallback || "custom";
}

function modelMatchedProviderFromLlmStatus(llm, fallback = "") {
  const matchReason = String(llm?.provider_match?.reason || "").trim();
  const configuredProvider = String(llm?.configured_provider || "").trim();
  if (matchReason === "unmatched_openai_compatible_fallback" && configuredProvider) {
    return configuredProvider;
  }
  return String(llm?.provider || llm?.matched_provider || fallback || "").trim();
}

function backendAuthHeaders() {
  if (frontendKernel?.authHeaders) return frontendKernel.authHeaders();
  const bridge = typeof window !== "undefined" ? window.tiangongDesktop : null;
  const headers = typeof bridge?.getGatewayHeaders === "function"
    ? bridge.getGatewayHeaders()
    : typeof bridge?.getBackendHeaders === "function"
      ? bridge.getBackendHeaders()
      : {};
  return headers && typeof headers === "object" ? headers : {};
}

function apiBases() {
  if (frontendKernel?.baseUrl) {
    const kernelBase = normalizeApiBase(frontendKernel.baseUrl());
    if (kernelBase) return [kernelBase];
  }
  const bridge = typeof window !== "undefined" ? window.tiangongDesktop : null;
  const bridged = normalizeApiBase(
    typeof bridge?.getGatewayUrl === "function"
      ? bridge.getGatewayUrl()
      : typeof bridge?.getBackendUrl === "function"
        ? bridge.getBackendUrl()
        : bridge?.gatewayUrl || bridge?.backendUrl
  );
  const browserBase = typeof window !== "undefined" && /^https?:$/.test(window.location?.protocol || "")
    ? normalizeApiBase(window.location.origin)
    : "";
  const base = bridged || browserBase || DEFAULT_API_BASE;
  return [base];
}

function controlError(message, code) {
  const error = new Error(message);
  error.tiangongControl = true;
  error.code = code;
  return error;
}

function isControlError(error) {
  return Boolean(error?.tiangongControl);
}

function timeoutText(timeoutMs) {
  return `请求超时（${Math.round(Number(timeoutMs || 0) / 1000)} 秒）`;
}

function timeoutAbortError(timeoutMs) {
  const error = new Error("request_timeout");
  error.timeoutMs = timeoutMs;
  return error;
}

function controlErrorFromAbort(signal, fallbackTimeoutMs = 0) {
  const reason = signal?.reason;
  if (String(reason?.message || reason || "") === "request_timeout") {
    return controlError(timeoutText(reason?.timeoutMs || fallbackTimeoutMs), "timeout");
  }
  return controlError("请求已中断。", "aborted");
}

async function fetchWithTimeout(url, options = {}, timeoutMs = DEFAULT_API_TIMEOUT_MS) {
  const externalSignal = options.signal;
  const controller = new AbortController();
  let timedOut = false;
  let timer = null;

  const abortFromExternal = () => controller.abort(externalSignal?.reason || new Error("request_aborted"));
  if (externalSignal?.aborted) {
    throw controlErrorFromAbort(externalSignal, timeoutMs);
  }
  externalSignal?.addEventListener?.("abort", abortFromExternal, { once: true });
  if (timeoutMs > 0) {
    timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort(new Error("request_timeout"));
    }, timeoutMs);
  }

  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (timedOut) throw controlError(timeoutText(timeoutMs), "timeout");
    if (externalSignal?.aborted) throw controlErrorFromAbort(externalSignal, timeoutMs);
    throw error;
  } finally {
    if (timer) window.clearTimeout(timer);
    externalSignal?.removeEventListener?.("abort", abortFromExternal);
  }
}

async function apiJson(path, options = {}) {
  const _diag = typeof window !== "undefined" && window.tiangongDesktop?.writeDiagnostic;
  if (_diag) _diag("apiJson", JSON.stringify({ path, method: options.method || "GET" }));
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, signal, query, ...fetchOptions } = options;
  // P2-10: callers that pass a query object must have it appended to the URL;
  // passing it through as a fetch option is silently ignored by the browser.
  let requestPath = path;
  if (query && typeof query === "object" && !Array.isArray(query)) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      params.set(key, String(value));
    }
    const encoded = params.toString();
    if (encoded) {
      requestPath = `${requestPath}${requestPath.includes("?") ? "&" : "?"}${encoded}`;
    }
  }
  if (frontendKernel?.request) return frontendKernel.request(requestPath, options);
  let lastError = null;
  for (const base of apiBases()) {
    try {
      const response = await fetchWithTimeout(endpoint(base, requestPath), {
        ...fetchOptions,
        signal,
        headers: {
          "Content-Type": "application/json",
          ...backendAuthHeaders(),
          ...(fetchOptions.headers || {})
        }
      }, timeoutMs);
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!response.ok) {
        if (typeof data === "string") {
          throw new Error(`后端返回了网页错误页或非 JSON 响应（状态码 ${response.status}）。`);
        }
        const detail = data?.detail || data?.error?.message || data?.message || data?.cuowu || text || response.statusText;
        throw new Error(`${response.status} ${detail}`);
      }
      if (typeof data === "string") {
        const preview = data.replace(/\s+/g, " ").slice(0, 500);
        throw new Error(`backend_non_json_response: ${preview || "empty response"}`);
      }
      return data;
    } catch (error) {
      if (isControlError(error)) throw error;
      lastError = error;
    }
  }
  throw lastError || new Error("backend unavailable");
}

export async function requestVoiceOutput(payload = {}) {
  return apiJson("/api/v1/body/voice/synthesize", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: 45_000,
  });
}

function normalizedGatewayArtifactCard(item, gatewayRequestId) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const sha = /^[0-9a-f]{64}$/;
  if (
    item.artifact_schema !== "tiangong.gateway.artifact-card.v1"
    || item.gateway_request_id !== gatewayRequestId
    || !/^req_[0-9a-f]{64}$/.test(String(item.gateway_request_id || ""))
    || !/^run_[0-9a-f]{64}$/.test(String(item.run_id || ""))
    || !Number.isInteger(item.generation)
    || item.generation < 0
    || !/^art_[0-9a-f]{64}$/.test(String(item.artifact_id || ""))
    || !/^arv_[0-9a-f]{64}$/.test(String(item.artifact_revision_id || ""))
    || !sha.test(String(item.manifest_sha256 || ""))
    || !sha.test(String(item.content_sha256 || ""))
    || !sha.test(String(item.card_sha256 || ""))
    || item.qc_state !== "PASSED"
    || item.open_capability !== "gateway_artifact_revision"
    || !String(item.filename || "").trim()
    || !Number.isInteger(item.revision)
    || item.revision < 1
    || !Number.isInteger(item.size_bytes)
    || item.size_bytes < 1
    || item.size_bytes > 2147483648
    || !Array.isArray(item.qc_checks)
    || item.qc_checks.length < 1
  ) return null;
  return {
    artifact_schema: item.artifact_schema,
    gateway_request_id: item.gateway_request_id,
    run_id: item.run_id,
    generation: item.generation,
    artifact_id: item.artifact_id,
    artifact_revision_id: item.artifact_revision_id,
    revision: item.revision,
    filename: String(item.filename),
    size_bytes: item.size_bytes,
    mime: String(item.mime || ""),
    artifact_kind: String(item.artifact_kind || ""),
    format_id: String(item.format_id || ""),
    content_sha256: item.content_sha256,
    manifest_sha256: item.manifest_sha256,
    qc_state: item.qc_state,
    qc_checks: item.qc_checks.map((value) => String(value || "")).filter(Boolean).slice(0, 64),
    created_at_ms: Number(item.created_at_ms || 0),
    open_capability: item.open_capability,
    card_sha256: item.card_sha256,
    name: String(item.filename),
    size: item.size_bytes,
    kind: "gateway_artifact",
    type: String(item.mime || ""),
    status: "qc_passed",
  };
}

// Deterministic transport QA surface. Production currently completes chat via
// gateway status polling, but the legacy SSE decoder must remain fail-closed if
// it is selected again by a future compatibility route.
export { fetchSse };
export { backendInstanceBridge }; // P6a §15.4：测试与诊断可观察实例标识状态

async function gatewayArtifactCards(presentationRequestId) {
  const requestId = String(presentationRequestId || "").trim();
  if (!requestId || requestId.length > 160) return [];
  try {
    const data = await apiJson(`/api/v1/artifacts?request_id=${encodeURIComponent(requestId)}`, {
      timeoutMs: 10000,
    });
    if (
      data?.schema !== "tiangong.gateway.artifact-cards.v1"
      || data?.presentation_request_id !== requestId
      || !/^req_[0-9a-f]{64}$/.test(String(data?.gateway_request_id || ""))
    ) return [];
    return (Array.isArray(data.artifacts) ? data.artifacts : [])
      .map((item) => normalizedGatewayArtifactCard(item, data.gateway_request_id))
      .filter(Boolean)
      .slice(0, ATTACHMENT_CONTEXT_LIMIT);
  } catch {
    return [];
  }
}

function mergeGatewayArtifactCards(items, cards) {
  const result = [];
  const seen = new Set();
  for (const item of [...(Array.isArray(items) ? items : []), ...(Array.isArray(cards) ? cards : [])]) {
    const key = String(
      item?.artifact_revision_id
      || item?.path
      || item?.url
      || item?.dataUrl
      || item?.documentId
      || item?.document_id
      || item?.name
      || ""
    );
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(item);
    if (result.length >= ATTACHMENT_CONTEXT_LIMIT) break;
  }
  return result;
}

async function readModelSettings() {
  const bridge = typeof window !== "undefined" ? window.tiangongDesktop : null;
  let directError = null;
  if (typeof bridge?.getModelSettings === "function") {
    try {
      const result = await bridge.getModelSettings();
      if (result && typeof result === "object" && result.ok !== false) return result;
      directError = new Error(result?.error || "模型配置读取失败");
    } catch (error) {
      directError = error;
    }
  }
  try {
    return await apiJson("/api/v1/llm/settings");
  } catch (error) {
    throw directError || error;
  }
}

async function writeModelSettings(payload) {
  const body = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  const containsCredential = ["modelApiKey", "api_key", "clear_api_key"]
    .some((key) => Object.prototype.hasOwnProperty.call(body, key));
  const bridge = typeof window !== "undefined" ? window.tiangongDesktop : null;
  let directError = null;
  if (typeof bridge?.setModelSettings === "function") {
    try {
      const result = await bridge.setModelSettings(body);
      if (result && typeof result === "object" && result.ok !== false) return result;
      directError = new Error(result?.error || "模型配置保存失败");
    } catch (error) {
      directError = error;
    }
    // Secrets and credential deletion intents are accepted only by the trusted
    // Electron main process.  Never downgrade them to renderer -> HTTP traffic,
    // even though 7184 would reject plaintext, because diagnostics/proxies may
    // already have observed the request body.
    if (containsCredential) {
      throw directError || new Error("模型凭据安全存储不可用");
    }
  } else if (containsCredential) {
    throw new Error("模型凭据只能通过桌面安全存储保存");
  }
  try {
    return await apiJson("/api/v1/llm/settings", {
      method: "POST",
      body: JSON.stringify(body)
    });
  } catch (error) {
    throw directError || error;
  }
}

function responseErrorDetail(text, fallback = "") {
  const raw = String(text || "").trim();
  if (!raw) return fallback;
  try {
    const data = JSON.parse(raw);
    return String(
      data?.detail
      || data?.error?.message
      || data?.error
      || data?.message
      || data?.cuowu
      || fallback
    ).trim();
  } catch {
    return raw.replace(/\s+/g, " ").slice(0, 800) || fallback;
  }
}

function messageId(prefix = "tg") {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

const ATTACHMENT_CONTEXT_LIMIT = 20;

const SSE_SUCCESS_TERMINALS = new Set(["done", "complete", "completed", "finish", "finished", "success", "succeeded", "response_end"]);
const SSE_FAILURE_TERMINALS = new Set(["error", "failed", "failure", "cancelled", "canceled", "aborted"]);

function decodeSsePart(part) {
  let eventType = "";
  let eventId = "";
  const dataLines = [];
  for (const rawLine of String(part || "").split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    const separator = rawLine.indexOf(":");
    const field = (separator >= 0 ? rawLine.slice(0, separator) : rawLine).trim();
    let value = separator >= 0 ? rawLine.slice(separator + 1) : "";
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventType = value.trim().toLowerCase();
    else if (field === "id") eventId = value.trim().slice(0, 256);
    else if (field === "data") dataLines.push(value);
  }
  if (!dataLines.length) return null;
  const rawData = dataLines.join("\n").trim();
  if (rawData === "[DONE]") return { id: eventId, type: "done", data: {} };
  let data;
  try {
    data = JSON.parse(rawData);
  } catch {
    data = rawData;
  }
  const declaredType = data && typeof data === "object"
    ? String(data.type || data.event || data.event_type || "").trim().toLowerCase()
    : "";
  const type = (!eventType || eventType === "message") && declaredType ? declaredType : (eventType || declaredType || "message");
  return { id: eventId, type, data };
}

function sseTerminalKind(type, data) {
  const cleanType = String(type || "").trim().toLowerCase();
  const status = data && typeof data === "object"
    ? String(data.status || data.zhuangtai || data.phase || "").trim().toLowerCase()
    : "";
  if (SSE_FAILURE_TERMINALS.has(cleanType) || SSE_FAILURE_TERMINALS.has(status)) return "error";
  if (SSE_SUCCESS_TERMINALS.has(cleanType) || SSE_SUCCESS_TERMINALS.has(status)) return "done";
  return "";
}

function terminalPayloadText(payload) {
  if (payload == null) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload !== "object") return String(payload);
  const candidates = [
    payload.reply,
    payload.final_response,
    payload.finalResponse,
    payload.response,
    payload.output,
    payload.content,
    payload.text,
    payload.message,
    payload.data?.reply,
    payload.data?.final_response,
    payload.data?.response,
    payload.data?.content,
    payload.data?.text,
    payload.data?.message,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) return candidate;
    if (candidate && typeof candidate === "object") return JSON.stringify(candidate);
  }
  return "";
}

function waitForRuntimeRetry(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason || new Error("request_aborted"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener?.("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener?.("abort", onAbort);
      reject(signal.reason || new Error("request_aborted"));
    };
    signal?.addEventListener?.("abort", onAbort, { once: true });
  });
}

async function boundedResponseText(response, maxChars = SSE_MAX_BUFFER_CHARS) {
  const declared = Number(response?.headers?.get?.("content-length") || 0);
  if (Number.isFinite(declared) && declared > maxChars * 4) throw new Error("response_too_large");
  if (!response?.body?.getReader) {
    const text = await response.text();
    if (String(text || "").length > maxChars) throw new Error("response_too_large");
    return String(text || "");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
    if (text.length > maxChars) {
      await reader.cancel("response_too_large").catch(() => {});
      throw new Error("response_too_large");
    }
  }
  text += decoder.decode();
  if (text.length > maxChars) throw new Error("response_too_large");
  return text;
}

async function fetchSse(path, body, { onText, onToolCall, onToolResult, onBiaoxian, onDone, onError, signal }) {
  const base = apiBases()[0];
  if (!base) {
    onError?.("SSE backend URL is not configured");
    return;
  }
  const url = endpoint(base, path);
  try {
    let response = null;
    for (let attempt = 0; attempt <= RUNTIME_STATE_RETRY_LIMIT; attempt += 1) {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Accept": "text/event-stream, application/json",
          ...backendAuthHeaders(),
        },
        body: JSON.stringify({ ...body, stream: true }),
        signal,
      });
      if (response.ok) break;
      const detail = responseErrorDetail(await boundedResponseText(response), response.statusText || "request failed");
      const runtimeStarting = response.status === 503
        && /authoritative runtime state is unavailable|RUNTIME_STATE_UNAVAILABLE/i.test(detail);
      if (runtimeStarting && attempt < RUNTIME_STATE_RETRY_LIMIT) {
        backendInstanceBridge.noteReconnect(); // P6a §15.4：后端运行态重启=重连，轮换会话级标识
        await waitForRuntimeRetry(RUNTIME_STATE_RETRY_MS, signal);
        continue;
      }
      onError?.(`SSE ${response.status}: ${detail} (${url})`);
      return;
    }
    if (!response?.ok) {
      onError?.(`SSE 503: 生命运行状态仍在启动，请稍后重试 (${url})`);
      return;
    }
    const contentType = String(response.headers?.get?.("content-type") || "").toLowerCase();
    if (contentType.includes("application/json")) {
      const raw = await boundedResponseText(response);
      let data;
      try { data = raw ? JSON.parse(raw) : {}; } catch { data = raw; }
      backendInstanceBridge.notePayload(data); // P6a §15.4：JSON 响应路径透传后端实例标识
      const kind = sseTerminalKind(data?.type || data?.event || "", data);
      if (kind === "error") onError?.(data?.message || data?.error || raw || "stream error");
      else onDone?.(data);
      return;
    }
    if (!response.body?.getReader) {
      onError?.(`SSE ${response.status}: backend returned no readable stream (${url})`);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const seenEventIds = new Map();

    function handleSsePart(part) {
      if (String(part || "").length > SSE_MAX_EVENT_CHARS) {
        onError?.(`SSE event exceeded the safe event limit (${url})`);
        return true;
      }
      const decoded = decodeSsePart(part);
      if (!decoded) return false;
      try {
        const { id: eventId, type: etype, data } = decoded;
        if (eventId) {
          if (seenEventIds.has(eventId)) return false;
          seenEventIds.set(eventId, true);
          if (seenEventIds.size > SSE_MAX_SEEN_EVENT_IDS) {
            const oldest = seenEventIds.keys().next().value;
            seenEventIds.delete(oldest);
          }
        }
        const payload = data && typeof data === "object" ? data : { content: String(data || "") };
        backendInstanceBridge.notePayload(payload); // P6a §15.4：SSE 载荷透传后端实例标识
        const terminalKind = sseTerminalKind(etype, payload);
        if (terminalKind === "done") { onDone?.(payload); return true; }
        if (terminalKind === "error") { onError?.(payload.message || payload.error || "stream error"); return true; }
        switch (etype) {
          case "text":
          case "delta":
          case "token": onText?.(payload.content || payload.delta || payload.text || ""); break;
          case "tool_call": onToolCall?.(payload); break;
          case "tool_result": onToolResult?.(payload); break;
          case "biaoxian": onBiaoxian?.(payload); break;
          case "parallel_start": {
            const callId = payload.call_id || messageId("parallel");
            const parallelPayload = { ...payload, call_id: callId, name: `并行×${payload.count}`, action: (payload.tools || []).join(","), label: `并行执行 ${payload.count} 个工具` };
            onToolCall?.(parallelPayload);
            onToolResult?.({ ...parallelPayload, ok: true, summary: "并行批次已分发" });
            break;
          }
        }
      } catch (_) { /* malformed events are ignored; a missing terminal event is reported below */ }
      return false;
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop();
      if (buffer.length > SSE_MAX_BUFFER_CHARS) {
        await reader.cancel("sse_buffer_too_large").catch(() => {});
        onError?.(`SSE event exceeded the safe buffer limit (${url})`);
        return;
      }
      for (const part of parts) {
        if (handleSsePart(part)) {
          await reader.cancel("sse_terminal_received").catch(() => {});
          return;
        }
      }
    }

    buffer += decoder.decode();
    if (buffer.trim() && handleSsePart(buffer)) {
      await reader.cancel("sse_terminal_received").catch(() => {});
      return;
    }
    onError?.(`SSE disconnected before a terminal event (${url})`);
  } catch (error) {
    if (isControlError(error)) throw error;
    if (signal?.aborted) throw controlErrorFromAbort(signal, CHAT_API_TIMEOUT_MS);
    backendInstanceBridge.noteReconnect(); // P6a §15.4：连接失败后的下次成功即重连，轮换会话级标识
    const detail = String(error?.cause?.message || error?.message || error || "connection failed");
    onError?.(`SSE connection failed: ${detail} (${url})`);
  }
}

function gatewayText(data) {
  return data?.huifu
    || data?.data?.huifu
    || data?.data?.text
    || data?.data?.message
    || data?.gateway_result?.outputs?.find?.((item) => item?.type === "text" && item?.text)?.text
    || data?.outbound?.parts?.find?.((item) => item?.type === "text" && item?.text)?.text
    || data?.text
    || data?.message
    || "";
}

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function countStatus(counts = {}, names = []) {
  return names.reduce((total, name) => total + numberValue(counts?.[name]), 0);
}

function normalizeLearningState(state = {}) {
  const learning = state.zizhu_xuexi && typeof state.zizhu_xuexi === "object" ? state.zizhu_xuexi : {};
  const summary = learning.summary && typeof learning.summary === "object" ? learning.summary : {};
  const counts = summary.counts && typeof summary.counts === "object" ? summary.counts : {};
  const rawLatest = Array.isArray(learning.latest)
    ? learning.latest
    : Array.isArray(summary.top)
      ? summary.top
      : [];
  const latest = rawLatest.map((item) => ({
    card_id: item?.card_id || "",
    title: item?.title || "未命名学习卡",
    summary: item?.summary || item?.task_preview || item?.learning_result || item?.title || "",
    kind: item?.kind || "learning_route",
    priority: item?.priority || "",
    score: numberValue(item?.score ?? item?.priority_score),
    status: item?.status || "candidate",
    promotion_stage: item?.promotion_stage || item?.stage || item?.status || "candidate",
    risk_level: item?.risk_level || "",
    risk_label: item?.risk_label || "",
    ability_id: item?.ability_id || "",
    next_action: item?.next_action || "",
    human_action_label: item?.human_action_label || "",
    can_confirm_learning: Boolean(item?.can_confirm_learning),
    can_process_learning: Boolean(item?.can_process_learning),
    can_request_activation: Boolean(item?.can_request_activation),
    can_activate_learning: Boolean(item?.can_activate_learning),
    can_release_learning: Boolean(item?.can_release_learning),
    can_discard_learning: Boolean(item?.can_discard_learning),
    auto_learn_allowed: Boolean(item?.auto_learn_allowed),
    auto_activation_allowed: Boolean(item?.auto_activation_allowed),
    activation_allowed: Boolean(item?.activation_allowed),
    release_allowed: Boolean(item?.release_allowed),
    auto_drafted: Boolean(item?.auto_drafted),
    auto_drafted_at: item?.auto_drafted_at || "",
    governance_note: item?.governance_note || "",
    candidate_only: item?.candidate_only !== false,
    review_before_activation: item?.review_before_activation !== false,
    writes_skill_registry: Boolean(item?.writes_skill_registry),
    activates_skill: Boolean(item?.activates_skill),
    registers_tool: Boolean(item?.registers_tool),
    tool_callable: Boolean(item?.tool_callable),
    tool_release_review_required: Boolean(item?.tool_release_review_required),
    tool_release_state: item?.tool_release_state || "",
    tool_name: item?.tool_name || "",
    tool_names: Array.isArray(item?.tool_names) ? item.tool_names.slice(0, 8) : [],
    invokes_tool: Boolean(item?.invokes_tool),
    evidence_refs: Array.isArray(item?.evidence_refs) ? item.evidence_refs.slice(0, 12) : [],
    source: item?.source || "zizhu_xuexi"
  }));
  const total = numberValue(summary.total, latest.length);
  const reviewReady = countStatus(counts, ["review_ready"]);
  const candidate = countStatus(counts, ["candidate"]);
  const accepted = countStatus(counts, ["accepted", "learned"]);
  const modelReview = countStatus(counts, ["model_review"]);
  const processingApproved = countStatus(counts, ["approved", "processing_approved"]);
  const draft = countStatus(counts, ["building", "draft", "draft_ready"]);
  const sandboxed = countStatus(counts, ["tested", "sandbox_passed"]);
  const failed = countStatus(counts, ["failed", "discarded", "duplicate_removed", "no_value"]);
  const status = learning.status
    || (modelReview > 0 ? "model_review" : reviewReady > 0 ? "review_ready" : candidate > 0 ? "candidate" : total > 0 ? "candidate_ready" : "empty");
  const engineState = learning.state && typeof learning.state === "object" ? learning.state : {};
  const lastTickAt = numberValue(engineState.last_tick_at);
  const secondsUntilNext = lastTickAt > 0
    ? Math.max(0, Math.round(300 - ((Date.now() / 1000) - lastTickAt)))
    : null;
  return {
    schema: learning.schema || "tiangong.v3.autonomous_learning.v1",
    status,
    reason: engineState.last_reason || learning.reason || "",
    root: learning.root || "",
    candidate_only: learning.candidate_only !== false,
    review_before_activation: learning.review_before_activation !== false,
    event_count: numberValue(learning.event_count),
    autonomy_policy: learning.autonomy_policy && typeof learning.autonomy_policy === "object" ? learning.autonomy_policy : {},
    state: engineState,
    summary,
    cards: {
      total,
      pending_learning: reviewReady + candidate + modelReview,
      processing_approved: processingApproved,
      candidate_ready: reviewReady,
      candidate,
      model_review: modelReview,
      draft,
      sandbox_passed: sandboxed,
      learned: accepted,
      learned_no_asset: 0,
      failed,
      latest,
      counts,
      stage_counts: summary.stage_counts || {},
      risk_counts: summary.risk_counts || {},
      last_tick_iso: engineState.last_tick_iso || "",
      last_reason: engineState.last_reason || "",
      last_changed: numberValue(engineState.last_changed),
      seconds_until_next: secondsUntilNext
    }
  };
}

export function normalizeFreeWillState(state = {}) {
  const raw = state.free_will && typeof state.free_will === "object" ? state.free_will : {};
  const qinggan = state.qinggan || {};
  const qudong = state.qudong || {};
  const yali = qudong.qudong_yali || {};
  const curiosity = numberValue(raw.curiosity, numberValue(yali.curiosity, numberValue(qinggan.curiosity, 0)));
  const threshold = numberValue(raw.curiosity_threshold, 0.5);
  const skipReason = raw.skip_reason || "";
  const skipDetail = raw.skip_detail || "";
  return {
    schema: raw.schema || "tiangong.v3.free_will_state.v1",
    enabled: raw.enabled !== false,
    heartbeat_state: raw.heartbeat_state || "unknown",
    heartbeat_running: Boolean(raw.heartbeat_running),
    heartbeat_interval_seconds: numberValue(raw.heartbeat_interval_seconds, 30),
    ready_for_action: Boolean(raw.ready_for_action),
    current_mode: raw.current_mode || raw.autonomy_policy?.mode || "",
    autonomy_policy: raw.autonomy_policy && typeof raw.autonomy_policy === "object" ? raw.autonomy_policy : {},
    skip_reason: skipReason,
    skip_detail: skipDetail,
    curiosity,
    curiosity_threshold: threshold,
    consecutive_actions: numberValue(raw.consecutive_actions),
    max_consecutive_actions: numberValue(raw.max_consecutive_actions, 5),
    user_active_recently: Boolean(raw.user_active_recently),
    seconds_since_user_message: raw.seconds_since_user_message ?? null,
    latest_autonomous_action: raw.latest_autonomous_action && typeof raw.latest_autonomous_action === "object"
      ? raw.latest_autonomous_action
      : {}
  };
}

export function normalizeAffectiveProjection(ui = {}) {
  const source = ui?.affective && typeof ui.affective === "object"
    ? ui.affective
    : ui?.affect && typeof ui.affect === "object"
      ? ui.affect
      : {};
  const state = source.state && typeof source.state === "object" ? source.state : {};
  const emotions = state.emotions && typeof state.emotions === "object" ? state.emotions : {};
  const expression = source.expression && typeof source.expression === "object"
    ? source.expression
    : state.expression && typeof state.expression === "object"
      ? state.expression
      : {};
  if (!Object.keys(state).length && !Object.keys(emotions).length) return source;
  return {
    ...source,
    ...emotions,
    schema: state.schema || source.schema || "tiangong.affective-state.v2",
    available: source.available !== false,
    dominant_emotion: source.dominant_emotion || expression.primary_emotion || "calm",
    allostatic_load: numberValue(state.allostatic_load, numberValue(source.allostatic_load)),
    regulation: numberValue(state.regulation, numberValue(source.regulation)),
    updated_at: state.updated_at || source.updated_at || "",
    source_event_ids: Array.isArray(state.source_event_ids) ? state.source_event_ids : (source.source_event_ids || []),
    expression,
    source: source.source || "canonical_life_affect_projection"
  };
}

function normalizeState(data) {
  const state = data?.state && typeof data.state === "object" ? data.state : {};
  const ui = data?.ui && typeof data.ui === "object" ? data.ui : {};
  const canonical = ui.schema === "tiangong.desktop.ui-projection.v1";
  const uiLifecycle = ui.lifecycle && typeof ui.lifecycle === "object" ? ui.lifecycle : {};
  const uiMemory = ui.memory && typeof ui.memory === "object" ? ui.memory : {};
  const uiEvolution = ui.evolution && typeof ui.evolution === "object" ? ui.evolution : {};
  const uiSecurity = ui.security && typeof ui.security === "object" ? ui.security : {};
  const uiOperational = ui.operational && typeof ui.operational === "object" ? ui.operational : {};
  const uiAffective = normalizeAffectiveProjection(ui);
  const uiFreeWill = ui.free_will && typeof ui.free_will === "object" ? ui.free_will : {};
  const qinggan = canonical ? uiAffective : state.qinggan || {};
  const jiyi = canonical ? {
    ...uiMemory,
    zongshu: numberValue(uiMemory.total),
    pending_proposals: numberValue(uiMemory.pending_proposals)
  } : state.jiyi_tongji || {};
  const jinhua = canonical ? uiEvolution : state.jinhua || {};
  const anquan = canonical ? uiSecurity : state.anquan || {};
  const learning = normalizeLearningState(state);
  const freeWill = canonical ? {
    schema: uiFreeWill.schema || "tiangong.v3.free_will_state.v1",
    available: uiFreeWill.available !== false,
    enabled: uiFreeWill.enabled !== false,
    heartbeat_state: uiFreeWill.heartbeat_state || "unknown",
    heartbeat_running: Boolean(uiFreeWill.heartbeat_running),
    heartbeat_interval_seconds: numberValue(uiFreeWill.heartbeat_interval_seconds, 900),
    ready_for_action: Boolean(uiFreeWill.ready_for_action),
    skip_reason: uiFreeWill.skip_reason || uiFreeWill.reason_code || "",
    skip_detail: uiFreeWill.skip_detail || uiFreeWill.reason || "",
    curiosity: uiFreeWill.curiosity ?? null,
    curiosity_threshold: uiFreeWill.curiosity_threshold ?? null,
    consecutive_actions: numberValue(uiFreeWill.consecutive_actions),
    max_consecutive_actions: numberValue(uiFreeWill.max_consecutive_actions),
    user_active_recently: Boolean(uiFreeWill.user_active_recently),
    seconds_since_user_message: uiFreeWill.seconds_since_user_message ?? null,
    latest_autonomous_action: uiFreeWill.latest_autonomous_action && typeof uiFreeWill.latest_autonomous_action === "object"
      ? uiFreeWill.latest_autonomous_action
      : {}
  } : normalizeFreeWillState(state);
  return {
    raw: state,
    ui,
    body: canonical ? (ui.body || state.body || {}) : state.body || state,
    qinggan,
    jiyi,
    jinhua,
    anquan,
    learning,
    freeWill,
    operational: canonical ? uiOperational : {
      available: true,
      source: "legacy_runtime_state",
      memory_total: numberValue(state.jiyi_tongji?.zongshu),
      task_total: 0,
      active_task_count: 0,
      completed_task_count: 0,
      execution_total: 0,
      completed_execution_count: 0,
      failed_execution_count: 0,
      latest_execution: {},
      scheduler: {
        running: false,
        interval_seconds: 0,
        tick_count: numberValue(state.zong_huanxing_cishu),
        last_error_type: ""
      }
    },
    lifecycle: canonical ? {
      available: uiLifecycle.available === true,
      phase: uiLifecycle.phase || "unknown",
      status: uiLifecycle.status || "UNKNOWN",
      growth: uiLifecycle.growth ?? null,
      vitality: uiLifecycle.vitality ?? null,
      wakeCount: uiLifecycle.wake_count ?? null,
      lastWake: uiLifecycle.last_wake || "",
      silenceSeconds: uiLifecycle.silence_seconds ?? null,
      activeRunCount: numberValue(uiLifecycle.active_run_count),
      completedRunCount: numberValue(uiLifecycle.completed_run_count),
      metricsUnavailableCode: uiLifecycle.metrics_unavailable_code || "",
      metricsUnavailableReason: uiLifecycle.metrics_unavailable_reason || ""
    } : {
      available: true,
      phase: state.zhouqi_jieduan || "unknown",
      growth: Number(state.chengzhang_jindu || 0),
      vitality: Number(state.shengmingli ?? 1),
      wakeCount: Number(state.zong_huanxing_cishu || 0),
      lastWake: state.zuihou_huanxing || "",
      silenceSeconds: Number(state.chenmo_shichang_miao || 0)
    }
  };
}

function statusPayload(health = {}, llmStatus = {}, v3State = {}, policyStatus = {}, toolsStatus = {}) {
  const normalized = normalizeState(v3State);
  const kernelState = frontendKernel?.snapshot?.() || null;
  const provider = llmStatus?.provider || llmStatus?.configured_provider || "";
  const model = llmStatus?.model || llmStatus?.configured_model_name || provider || "";
  const policy = policyStatus && typeof policyStatus === "object" ? policyStatus : {};
  const gatewayAlive = health?.component_id === "tiangong-total-gateway" && health?.status === "ALIVE";
  const endpointReady = Boolean(health?.ok || gatewayAlive);
  return {
    provider,
    model,
    base_url: llmStatus?.base_url || "",
    workspace: llmStatus?.workspace || policy.workspace || "",
    workspace_mode: llmStatus?.workspace_mode || policy.workspace_mode || "workspace",
    permission_mode: policy.permission_mode || "",
    permission_label: policy.mode_label || policy.permission_mode || "",
    policy,
    runtime_environment: policy.runtime || null,
    endpoint_state: endpointReady ? "ready" : "unknown",
    credential_state: llmStatus?.credential_state || llmStatus?.api_key || "unknown",
    kernel_importable: Boolean(health?.bridge_ready || endpointReady),
    life_kernel_ready: kernelState?.life?.ready ?? (health?.life_ready !== false),
    // GF 门：进程就绪与行动就绪分离上报。
    // process_ready：进程级就绪（优先后端显式字段，缺失时沿用既有生命内核派生）。
    // action_ready：行动级就绪；后端尚未提供该字段时保持 null，
    // 由 UI 显示"未提供"，绝不假装就绪（草案 §8 安全降级要求）。
    process_ready: health?.process_ready ?? (kernelState?.life?.ready ?? (health?.life_ready !== false)),
    action_ready: health?.action_ready ?? (v3State?.action_ready ?? null),
    degraded: Boolean(health?.degraded || kernelState?.phase === "degraded" || kernelState?.phase === "incompatible"),
    life_error: health?.life_error || kernelState?.life?.error || "",
    frontend_kernel: kernelState,
    service: health?.service || health?.component_id || "tiangong-total-gateway",
    chat_port: health?.gateway_port || 7184,
    core_result: {
      signal_kind: "total_gateway_7184",
      health_state: health?.degraded ? "degraded" : endpointReady ? "healthy" : "offline"
    },
    runtime: {
      body: {
        ...(normalized.body && typeof normalized.body === "object" ? normalized.body : {}),
        jiyi_tongji: normalized.jiyi
      },
      qinggan: normalized.qinggan,
      anquan: normalized.anquan,
      lifecycle: normalized.lifecycle,
      operational: normalized.operational,
      jinhua: normalized.jinhua,
      free_will: normalized.freeWill
    },
    lifecycle: {
      pending_updates: Array.isArray(normalized.jinhua.gaijin_houxuan) ? normalized.jinhua.gaijin_houxuan : [],
      runtime: {
        activation: {
          lifecycle_active: normalized.lifecycle.available === true,
          memory_recall_active: normalized.jiyi.available === true,
          affective_active: normalized.qinggan.available === true,
          // Forgetting/review is a real life-layer capability whenever both the
          // signed memory store and its lifecycle projection are available.
          forgetting_active: normalized.jiyi.available === true && normalized.lifecycle.available === true,
          free_will_heartbeat_active: normalized.freeWill.heartbeat_running,
          free_will_action_ready: normalized.freeWill.ready_for_action
        }
      },
      policy: {
        free_will_status: normalized.freeWill.heartbeat_running ? "heartbeat_monitoring" : "not_running",
        free_will_action_ready: normalized.freeWill.ready_for_action,
        free_will_skip_reason: normalized.freeWill.skip_reason || ""
      },
      free_will: normalized.freeWill
    },
    learning: {
      status: normalized.learning.status,
      reason: normalized.learning.reason,
      candidate_only: normalized.learning.candidate_only,
      review_before_activation: normalized.learning.review_before_activation,
      event_count: normalized.learning.event_count,
      root: normalized.learning.root,
      learning_cards: normalized.learning.cards,
      autonomy_policy: normalized.learning.autonomy_policy,
      skill_queue: {
        status: normalized.learning.candidate_only ? "candidate_only" : "ready",
        draft_versions: normalized.learning.cards.candidate_ready
      },
      tool_requests: {
        status: normalized.learning.candidate_only ? "candidate_only" : "ready",
        production_requests: 0
      }
    },
    abilities: {
      count: numberValue(toolsStatus?.summary?.runtimeToolCount ?? toolsStatus?.summary?.toolCount),
      declared: numberValue(toolsStatus?.summary?.total),
      unavailable: numberValue(toolsStatus?.summary?.unavailable),
      source: toolsStatus?.summary?.source || ""
    }
  };
}

function statusStdout(payload) {
  return [
    `provider: ${payload.provider}`,
    `model: ${payload.model}`,
    `endpoint_state: ${payload.endpoint_state}`,
    `credential_state: ${payload.credential_state}`,
    `workspace: ${payload.workspace || ""}`,
    `service: ${payload.service}`,
    `chat_port: ${payload.chat_port}`,
    "TIANGONG_STATUS_JSON_START",
    JSON.stringify(payload),
    "TIANGONG_STATUS_JSON_END"
  ].join("\n");
}

function desktopBridge() {
  return typeof window !== "undefined" ? window.tiangongDesktop : null;
}

async function readDesktopServiceSnapshot() {
  const bridge = desktopBridge();
  if (!bridge?.getServiceStatus) return {};
  const snapshot = await bridge.getServiceStatus();
  return snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
}

async function desktopDegradedStatus(originalError) {
  const bridge = desktopBridge();
  const [services, modelSettings, workspaceStatus] = await Promise.all([
    readDesktopServiceSnapshot().catch(() => ({})),
    readModelSettings().catch(() => ({})),
    bridge?.getWorkspaceRoot?.().catch?.(() => ({})) || Promise.resolve({})
  ]);
  const gatewayReady = services["total-gateway"]?.ready === true;
  const embeddedApplication = !services.backend && !services.life && !services.communication && Boolean(services["total-gateway"]);
  const backendReady = embeddedApplication ? gatewayReady : services.backend?.ready === true;
  const lifeReady = embeddedApplication ? gatewayReady : services.life?.ready === true;
  const message = originalError?.message || String(originalError || "total_gateway_unavailable");
  const llmStatus = modelSettings && typeof modelSettings === "object" ? { ...modelSettings } : {};
  if (workspaceStatus?.workspace) llmStatus.workspace = workspaceStatus.workspace;
  if (workspaceStatus?.workspace_mode) llmStatus.workspace_mode = workspaceStatus.workspace_mode;
  const payload = statusPayload(
    {
      component_id: gatewayReady ? "tiangong-total-gateway" : "tiangong-desktop-supervisor",
      status: gatewayReady ? "ALIVE" : "DEGRADED",
      gateway_port: 7184,
      degraded: true,
      life_ready: lifeReady,
      life_error: gatewayReady ? "" : message,
    },
    llmStatus,
    {},
    {},
    {},
  );
  payload.service_snapshot = services;
  payload.backend_control_ready = backendReady;
  payload.gateway_ready = gatewayReady;
  payload.endpoint_state = gatewayReady ? "ready" : "degraded";
  payload.core_result.health_state = gatewayReady ? "healthy" : backendReady ? "degraded" : "offline";
  return {
    ok: backendReady || gatewayReady,
    degraded: true,
    stdout: statusStdout(payload),
    stderr: gatewayReady ? "" : `总网关暂不可用：${message}`,
    code: gatewayReady ? "" : "total_gateway_unavailable",
  };
}

function rememberWorkspaceAuthority(status) {
  if (status?.ok && Number.isInteger(status.revision) && status.revision >= 0) {
    workspaceAuthorityRevision = status.revision;
  }
  return status;
}

async function readDesktopWorkspaceAuthority() {
  const bridge = desktopBridge();
  if (!bridge?.getWorkspaceRoot) return null;
  try {
    return rememberWorkspaceAuthority(await bridge.getWorkspaceRoot());
  } catch {
    return null;
  }
}

function sameWorkspacePath(left, right) {
  const normalize = (value) => String(value || "")
    .trim()
    .replace(/\//g, "\\")
    .replace(/\\+$/, "")
    .toLowerCase();
  return Boolean(normalize(left)) && normalize(left) === normalize(right);
}

async function commitDesktopWorkspace(workspaceRoot, workspaceMode = "") {
  let workspace = String(workspaceRoot || "").trim();
  const bridge = desktopBridge();
  if (!bridge?.setWorkspaceRoot) throw new Error("desktop_workspace_bridge_unavailable");
  if (!workspace) {
    const current = await readDesktopWorkspaceAuthority();
    workspace = String(current?.workspace || "").trim();
  }
  let expectedRevision = workspaceAuthorityRevision;
  if (!Number.isInteger(expectedRevision) || expectedRevision < 0) {
    const current = await readDesktopWorkspaceAuthority();
    if (!current?.ok || !Number.isInteger(current.revision)) {
      throw new Error(current?.error || "desktop_workspace_authority_unavailable");
    }
    expectedRevision = current.revision;
  }
  const saved = rememberWorkspaceAuthority(await bridge.setWorkspaceRoot({
    workspace,
    expectedRevision,
    workspace_mode: workspaceMode === "full" ? "full" : "workspace",
  }));
  if (saved?.error === "workspace_revision_conflict") {
    const current = await readDesktopWorkspaceAuthority();
    if (current?.ok && sameWorkspacePath(current.workspace, workspace)) return current;
    const error = new Error("工作区已被另一个操作更新，请重新选择后再试");
    error.code = "workspace_revision_conflict";
    throw error;
  }
  if (!saved?.ok || !saved?.workspace) throw new Error(saved?.error || "工作区切换失败");
  return saved;
}

function runtimePayload(payload = {}) {
  const local = { ...defaultSettings, ...readLocalSettings() };
  return {
    ...payload,
    workspace: payload.workspace || local.workspace || "",
    knowledgeRoot: payload.knowledgeRoot || local.knowledgeRoot || ""
  };
}

function learningRuntimePayload(payload = {}, item = {}) {
  const body = typeof payload === "string"
    ? { card_id: payload }
    : { ...(payload || {}) };
  if (!body.card_id && item && typeof item === "object") {
    body.card_id = item.card_id || item.id || "";
  }
  if (!body.actor) body.actor = "user";
  if (!body.card && item && typeof item === "object" && !Array.isArray(item)) {
    const candidateKind = String(item.capability_kind || item.kind || "").toLowerCase();
    const materializedKind = candidateKind === "tool" || item.registers_tool === true ? "tool" : "skill";
    body.card = {
      id: item.card_id || item.id || body.card_id || "",
      card_id: item.card_id || item.id || body.card_id || "",
      artifact_id: item.artifact_id || "",
      kind: materializedKind,
      name: item.name || item.title || "",
      title: item.title || item.name || "",
      version: item.version || item.target_version || "1.0.0",
      description: item.description || item.summary || item.learning_result || "",
      instructions: item.instructions || item.skill_markdown || item.procedure || item.learning_result || item.summary || "",
      runtime_binding: item.runtime_binding || (item.tool_name ? { tool_name: item.tool_name } : {}),
      upgrade_of: item.upgrade_of || item.previous_artifact_id || "",
      source_memory_ids: Array.isArray(item.source_memory_ids) ? item.source_memory_ids : []
    };
  }
  return runtimePayload(body);
}

function fileNameFromPath(value) {
  const text = String(value || "");
  return text.split(/[\\/]/).filter(Boolean).pop() || text || "file";
}

function fileExtension(name) {
  const match = String(name || "").match(/\.([^.\\/]+)$/);
  return match ? match[1].toLowerCase() : "";
}

function fileKindFromName(name, type = "") {
  const mime = String(type || "").toLowerCase();
  const ext = fileExtension(name);
  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg", "tif", "tiff"].includes(ext)) return "image";
  if (mime.startsWith("video/") || ["mp4", "webm", "ogv", "mov", "mkv", "avi", "m4v", "wmv", "flv", "mpeg", "mpg", "3gp", "ts", "m2ts"].includes(ext)) return "video";
  if (mime.startsWith("audio/") || ["mp3", "wav", "ogg", "m4a", "flac", "aac", "opus", "wma"].includes(ext)) return "audio";
  if (["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "dmg"].includes(ext)) return "archive";
  if (["txt", "md", "markdown", "csv", "json", "jsonl", "html", "htm", "xml", "yaml", "yml", "toml", "docx", "xlsx", "pptx", "pdf"].includes(ext)) return "document";
  if (["py", "js", "mjs", "ts", "tsx", "jsx", "css", "scss", "less", "vue", "svelte", "java", "c", "cc", "cpp", "h", "hpp", "cs", "go", "rs", "php", "rb", "swift", "kt", "kts", "sql", "ini", "conf", "log", "bat", "cmd", "ps1"].includes(ext)) return "code";
  return "file";
}

function fallbackAttachmentsFromPayload(payload = {}) {
  const attachments = [];
  const paths = Array.isArray(payload.paths) ? payload.paths : [];
  for (const filePath of paths) {
    const pathText = String(filePath || "");
    if (!pathText) continue;
    const name = fileNameFromPath(pathText);
    attachments.push({
      path: pathText,
      name,
      ext: fileExtension(name),
      kind: fileKindFromName(name),
      size: 0,
      status: "selected",
      source: "local"
    });
  }
  const items = Array.isArray(payload.items) ? payload.items : [];
  for (const item of items) {
    const name = String(item?.name || "clipboard-file");
    attachments.push({
      name,
      ext: fileExtension(name),
      kind: fileKindFromName(name, item?.type),
      type: String(item?.type || ""),
      size: Number(item?.size || 0),
      dataUrl: String(item?.dataUrl || ""),
      status: "selected",
      source: "inline"
    });
  }
  return attachments.slice(0, ATTACHMENT_CONTEXT_LIMIT);
}

function gatewayAttachmentRefs(items = []) {
  const result = [];
  for (const selected of Array.isArray(items) ? items : []) {
    const item = selected?.attachment && typeof selected.attachment === "object" ? selected.attachment : selected;
    if (!item || typeof item !== "object") continue;
    const reference = {
      schema_version: String(item.schema_version || "tiangong.gateway.contracts.v1"),
      object_id: String(item.object_id || ""),
      revision: Number(item.revision || 0),
      sha256: String(item.sha256 || ""),
      size_bytes: Number(item.size_bytes || 0),
      mime: String(item.mime || ""),
      filename: String(item.filename || ""),
      tenant_id: String(item.tenant_id || ""),
      link_account_id: String(item.link_account_id || ""),
      conversation_scope_hash: String(item.conversation_scope_hash || ""),
      source_message_ref: item.source_message_ref == null ? null : String(item.source_message_ref),
      created_at_ms: Number(item.created_at_ms || 0),
      acceptance: String(item.acceptance || ""),
      magic_verified: item.magic_verified === true,
    };
    if (
      /^oref_[0-9a-f]{64}$/.test(reference.object_id)
      && /^[0-9a-f]{64}$/.test(reference.sha256)
      && /^[0-9a-f]{64}$/.test(reference.conversation_scope_hash)
      && Number.isInteger(reference.revision) && reference.revision >= 1
      && Number.isInteger(reference.size_bytes) && reference.size_bytes >= 1
      && reference.acceptance === "accepted" && reference.magic_verified
    ) result.push(reference);
    if (result.length >= ATTACHMENT_CONTEXT_LIMIT) break;
  }
  return result;
}

function preserveSelectedAttachments(result = {}, payload = {}) {
  const attachments = Array.isArray(result?.attachments) ? result.attachments : [];
  if (attachments.length) return { ...result, attachments };
  const fallback = fallbackAttachmentsFromPayload(payload);
  if (!fallback.length) return { ...result, attachments: [] };
  return {
    ...result,
    ok: true,
    partial: Boolean(result?.error),
    warning: result?.warning || result?.error || "",
    attachments: fallback,
    imported: Array.isArray(result?.imported) ? result.imported : [],
    failed: Array.isArray(result?.failed) ? result.failed : []
  };
}

function parseFinalReplyPayload(value) {
  if (!value) return {};
  if (typeof value === "object") return value;
  try {
    const parsed = JSON.parse(String(value || ""));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

async function refreshBackendSnapshot() {
  // P1-16: a failed sub-fetch must not masquerade as "zero data"; mark it so
  // panels can keep the last trustworthy state instead of overwriting it.
  const failed = { load_error: "unavailable" };
  const [health, ready, llmStatus, v3State, workspaceStatus, policyStatus, toolsStatus] = await Promise.all([
    apiJson("/health"),
    // FE-03: action_ready only exists on /ready; the status sidebar must bind
    // to it with an observation timestamp and source instead of a stale label.
    apiJson("/ready").catch(() => failed),
    apiJson("/api/v1/llm/status").catch(() => failed),
    apiJson(LIFE_API_ROUTES.state.path).catch(() => failed),
    apiJson("/api/v1/workspace/settings").catch(() => failed),
    apiJson("/api/v1/policy/status").catch(() => failed),
    apiJson("/api/v1/v3/tools").catch(() => failed)
  ]);
  if (workspaceStatus?.workspace) llmStatus.workspace = workspaceStatus.workspace;
  const payload = statusPayload(health, llmStatus, v3State, policyStatus, toolsStatus);
  if (ready && typeof ready === "object" && !ready.load_error) {
    payload.process_ready = ready.process_ready ?? payload.process_ready;
    payload.action_ready = ready.action_ready ?? payload.action_ready;
    payload.readiness_observed_at = new Date().toISOString();
    payload.readiness_source = "/ready";
  }
  return payload;
}

// GF 门（草案 §8）：状态→相位映射是前端协议闸门的第一道防线。
// 原则：只有网关权威终态才能映射为成功相位；歧义/待对账/部分完成/矛盾/
// 未知一律是非成功相位，绝不落入 "running" 的假安心。
// signals 为可选的网关旁路信号：{ contradiction, needsReconciliation }。
export function runPhaseFromStatus(status, signals = {}) {
  const value = String(status || "").toUpperCase();
  // 矛盾结果优先于一切状态：映射为非成功 incident，绝不显示普通成功
  if (value === "CONTRADICTION" || signals?.contradiction === true) return "incident";
  // 网关标记需要对账（或车道状态为歧义/待对账）：禁止重试/重发
  if (["AMBIGUOUS", "RECONCILE_REQUIRED"].includes(value) || signals?.needsReconciliation === true) {
    return "reconcile_required";
  }
  if (["COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE", "WANCHENG"].includes(value)) return "finished";
  // 部分完成：网关裁决只达成一部分，单独成相位，不算成功
  if (value === "PARTIAL") return "partial";
  if (["FAILED_SAFE", "FAILED", "FAILURE", "BLOCKED", "ERROR", "SHIBAI"].includes(value)) return "failed";
  if (value === "FORCE_STOPPED") return "force_stopped";
  if (value === "WAITING_FOR_USER") return "awaiting_user";
  if (["CANCELLED", "CANCELED", "ABORTED", "INTERRUPTED", "YIZHONGDUAN", "SUPERSEDED"].includes(value)) return "cancelled";
  // 仅这些已知的进行中状态允许映射为 running
  // ACTIVE：网关 session_queue 已激活但聚合机尚未落第一帧的合法过渡态
  // （desktop/status 在快照缺失时回退队列态），必须按进行中等待，不得误判 unknown 终态。
  if ([
    "RUNNING", "IN_PROGRESS", "EXECUTING", "QUEUED", "RECEIVED", "PLANNING",
    "DELIVERING", "VALIDATING_ARTIFACTS", "WAITING_CONFIRMATION", "PENDING", "STARTED",
    "ACTIVE",
  ].includes(value)) return "running";
  // 未知状态：安全映射为非成功（UI 显示"状态未知，按未成功处理"）
  return "unknown";
}

// GF 门终态相位集合：到达这些相位即停止终态等待与轮询
const TERMINAL_RUN_PHASES = new Set([
  "finished", "failed", "force_stopped", "cancelled", "reconcile_required", "partial", "incident", "unknown",
]);

// 从网关 status 载荷提取对账/矛盾信号；字段缺失一律按 false 处理（不臆造）。
function terminalSignals(status = {}, run = {}) {
  const projection = status?.gateway_projection && typeof status.gateway_projection === "object"
    ? status.gateway_projection
    : null;
  const lanes = [projection?.execution, projection?.artifact, projection?.delivery];
  // 任一车道处于歧义/待对账，与 projection.needs_reconciliation 等价
  const laneReconcile = lanes.some((lane) => (
    ["AMBIGUOUS", "RECONCILE_REQUIRED"].includes(String(lane?.state || "").toUpperCase())
  ));
  const contradiction = Boolean(
    status?.contradiction || run?.contradiction || projection?.contradiction
  );
  const needsReconciliation = Boolean(
    projection?.needs_reconciliation
    || status?.needs_reconciliation
    || run?.needs_reconciliation
    || laneReconcile
    || String(projection?.overall_phase || "").trim().toLowerCase() === "reconcile_required"
  );
  return { contradiction, needsReconciliation, projection };
}

// GF 门唯一成功来源（草案不变量：由模型文本或 HTTP 200 显示成功 = 强制停止）：
// 1) 网关投影聚合相位为 completed/delivered/channel_accepted（投递态以执行
//    成功为前提，等价于 CompletionGate 裁决 COMPLETED）；或
// 2) 无投影时 run 终态为 SUCCEEDED/COMPLETED 且无矛盾、无对账标记。
// 模型自报 zhuangtai 不参与本函数的任何判定。
export function terminalSuccessVerdict({ reply = "", run = {}, status = {} } = {}) {
  const signals = terminalSignals(status, run);
  const phase = runPhaseFromStatus(run?.status || run?.phase || run?.stage, signals);
  // 矛盾/对账/部分完成/失败/取消/未知：一律非成功
  if (phase !== "finished" && phase !== "running" && phase !== "awaiting_user") {
    return { ok: false, phase };
  }
  if (signals.projection) {
    // 有网关投影时以投影聚合相位为准（它由核验过的事实链构建）
    const overall = String(signals.projection?.overall_phase || "").trim().toLowerCase();
    if (overall === "reconcile_required") return { ok: false, phase: "reconcile_required" };
    if (["partial", "failed", "cancelled"].includes(overall)) return { ok: false, phase: overall };
    const gatewayCompleted = ["completed", "delivered", "channel_accepted"].includes(overall);
    if (gatewayCompleted) return { ok: Boolean(reply), phase: "finished" };
    // 投影存在但未给出完成态：按未成功处理，不猜
    return { ok: false, phase: "unknown" };
  }
  // 无投影：仅 run 权威终态算成功
  if (phase === "finished") return { ok: Boolean(reply), phase: "finished" };
  return { ok: false, phase: "unknown" };
}

function isInternalProgressStep(step) {
  return String(
    step?.visibility
    || step?.meta?.visibility
    || step?.public?.visibility
    || ""
  ).trim().toLowerCase() === "internal";
}

export function runEventToProgressStep(event = {}, run = {}) {
  const type = String(event.type || "").toUpperCase();
  const detail = event.public && typeof event.public === "object" ? event.public : {};
  const seq = Number(event.seq || 0);
  const ts = Number.isFinite(Date.parse(event.at || "")) ? Date.parse(event.at) : Date.now();
  const visibility = String(
    event?.visibility
    || event?.meta?.visibility
    || detail?.visibility
    || ""
  ).trim().toLowerCase();
  const base = {
    id: `event_${seq || type.toLowerCase()}`,
    title: type,
    status: "running",
    summary: "",
    ts,
    meta: { seq, type, ...(visibility ? { visibility } : {}) }
  };
  if (type === "MODEL_TURN_STARTED") {
    const turn = Number(detail.turn || run.model_turns || 0);
    return { ...base, id: `model_turn_${turn}`, title: `模型回合 ${turn}`, summary: "正在规划下一步并生成工具调用" };
  }
  if (type === "MODEL_TURN_FINISHED") {
    const turn = Number(run.model_turns || 0);
    const context = detail.context && typeof detail.context === "object" ? detail.context : {};
    return {
      ...base,
      id: `model_turn_${turn}`,
      title: `模型回合 ${turn}`,
      status: "done",
      summary: `返回 ${Number(detail.tool_call_count || 0)} 个工具调用 · 输入估算 ${Number(context.estimated_input_units || 0)} · 归档 ${Number(context.blocks_archived || 0)}`
    };
  }
  if (type === "TOOL_EFFECT_PREPARED") {
    const callId = String(detail.call_id || seq);
    return { ...base, id: `tool_${callId}`, title: String(detail.action || "工具执行"), summary: `准备执行 ${String(detail.target || "")}`.trim() };
  }
  if (type === "TOOL_FINISHED") {
    const callId = String(detail.call_id || seq);
    return {
      ...base,
      id: `tool_${callId}`,
      title: String(detail.action || "工具执行"),
      status: detail.ok ? "done" : "failed",
      summary: String(detail.summary || (detail.ok ? "执行成功" : "执行失败"))
    };
  }
  if (type === "TOOL_ARGUMENTS_REJECTED") {
    const callId = String(detail.call_id || seq);
    const issues = Array.isArray(detail.issues) ? detail.issues : [];
    return {
      ...base,
      id: `tool_${callId}`,
      title: `${String(detail.action || "工具")} 参数修复`,
      status: "repairing",
      summary: issues.map((item) => item?.message).filter(Boolean).join("；") || "参数在副作用前被拒绝，等待模型修正"
    };
  }
  if (type === "MODEL_RETRY_SCHEDULED") {
    return { ...base, id: "model_retry", title: "模型请求重试", status: "retrying", summary: String(detail.error || detail.failure_class || "短暂错误，按预算重试") };
  }
  if (type === "COMPLETION_REJECTED") {
    return { ...base, id: "completion_gate", title: "完成验收", status: "repairing", summary: String(detail.reason || "尚未达到真实交付标准，继续修复") };
  }
  if (type === "RUN_COMPLETED") {
    return { ...base, id: "backend_complete", title: "后端执行结束", status: "done", summary: "运行已通过完成门" };
  }
  if (type === "RUN_FAILED_SAFE") {
    return { ...base, id: "backend_complete", title: "后端安全失败", status: "failed", summary: String(detail.error || "运行未完成") };
  }
  if (type === "RUN_CANCELLED") {
    return { ...base, id: "backend_complete", title: "运行已取消", status: "interrupted", summary: "后端已确认取消" };
  }
  return null;
}

export function runSnapshotStageText(run = {}) {
  return String(
    run.last_interim_reply_text
    || run.lastInterimReplyText
    || run.last_model_content
    || run.lastModelContent
    || ""
  ).trim();
}

export function createHttpRuntime({ kernel = null } = {}) {
  frontendKernel = kernel || frontendKernel || (typeof window !== "undefined" ? window.tiangongFrontendKernel : null);
  const activeRequests = new Map();
  const runStepListeners = new Set();
  const recoveredRuns = new Set();

  function emitRunStep(event = {}) {
    for (const listener of runStepListeners) {
      try {
        listener(event);
      } catch {
        // Progress listeners are UI-only; one bad listener must not break runtime IO.
      }
    }
  }

  function progressPresentationKey(step = {}) {
    const type = String(step?.meta?.type || "").toUpperCase();
    const id = String(step?.id || "").toLowerCase();
    if (type === "GATEWAY_STATE_PROJECTION") {
      return `gateway:${String(step?.meta?.machine || id || "state")}`;
    }
    const isTool = type === "TOOL_EFFECT_PREPARED"
      || type === "TOOL_FINISHED"
      || id.startsWith("tool_")
      || Boolean(step?.toolName || step?.tool_name);
    return isTool ? `tool:${String(step.id || step.title || "tool")}` : "thinking";
  }

  function progressStepSignature(step = {}) {
    const type = String(step?.meta?.type || "").toUpperCase();
    if (type === "GATEWAY_STATE_PROJECTION") {
      return [
        step.id || "",
        step.title || "",
        step.status || "",
        step.summary || "",
        step.meta?.machine || "",
        step.meta?.state || "",
        step.meta?.source || "",
        String(step.meta?.evidenceVerified ?? "")
      ].join("|");
    }
    return [
      step.id || "",
      step.title || "",
      step.status || "",
      step.summary || "",
      JSON.stringify(step.meta || {})
    ].join("|");
  }

  function resolvePresentationWaiters(active) {
    const waiters = Array.isArray(active?.presentationWaiters) ? active.presentationWaiters.splice(0) : [];
    for (const resolve of waiters) {
      try { resolve(); } catch {}
    }
  }

  function drainPresentationQueue(active) {
    if (!active || active.presentationTimer) return;
    const advance = () => {
      active.presentationTimer = null;
      const item = active.presentationQueue?.shift?.();
      if (!item) {
        resolvePresentationWaiters(active);
        return;
      }
      if (item.kind === "text") {
        try { active.onStageText?.(item.text, item.meta || {}); } catch {}
      } else {
        active.presentedProgressKey = item.key;
        emitRunStep(item.event);
      }
      const delay = item.kind === "text" ? PROGRESS_TEXT_DWELL_MS : PROGRESS_PRESENTATION_DWELL_MS;
      active.presentationTimer = window.setTimeout(advance, delay);
    };
    advance();
  }

  function enqueueProgressPresentation(active, event = {}) {
    if (active?.closed) return;
    if (!active?.presentationQueue) {
      emitRunStep(event);
      return;
    }
    const key = progressPresentationKey(event);
    if (key === active.presentedProgressKey && !active.presentationQueue.length) {
      emitRunStep(event);
      return;
    }
    const queued = { kind: "step", key, event };
    const last = active.presentationQueue[active.presentationQueue.length - 1];
    if (key.startsWith("tool:") || key.startsWith("gateway:")) {
      const existing = active.presentationQueue.findIndex((item) => item.kind === "step" && item.key === key);
      if (existing >= 0) active.presentationQueue[existing] = queued;
      else active.presentationQueue.push(queued);
    } else if (last?.kind === "step" && last.key === key) {
      active.presentationQueue[active.presentationQueue.length - 1] = queued;
    } else {
      active.presentationQueue.push(queued);
    }
    drainPresentationQueue(active);
  }

  function enqueueTextPresentation(active, text, meta = {}) {
    if (!text || !active?.onStageText || active.closed) return;
    // Model snapshots are replacement text, not progress cards.  Putting them
    // behind the dwell queue lets a busy tool run accumulate minutes of cards
    // before the first sentence becomes visible.  Deliver each newer snapshot
    // immediately so the current assistant bubble is continuously replaced.
    try { active.onStageText(text, meta); } catch {}
  }

  function emitStreamToolEvent(active, event = {}) {
    if (active?.closed) return;
    const type = String(event?.type || "").toUpperCase();
    const detail = event?.public && typeof event.public === "object" ? event.public : {};
    const payload = {
      ...detail,
      seq: Number(event?.seq || 0),
      type,
      name: detail.name || detail.action || "tool",
    };
    try {
      if (type === "TOOL_EFFECT_PREPARED") active?.onToolCall?.(payload);
      else if (type === "TOOL_FINISHED") active?.onToolResult?.({ ...payload, ok: detail.ok === true });
      else if (type === "TOOL_ARGUMENTS_REJECTED") active?.onToolResult?.({ ...payload, ok: false });
    } catch {
      // Presentation callbacks are isolated from runtime transport state.
    }
  }

  async function waitForPresentationQueue(active) {
    if (!active?.presentationQueue || (!active.presentationQueue.length && !active.presentationTimer)) return;
    await Promise.race([
      new Promise((resolve) => active.presentationWaiters.push(resolve)),
      new Promise((resolve) => window.setTimeout(resolve, PROGRESS_PRESENTATION_MAX_WAIT_MS))
    ]);
    if (active.presentationQueue.length || active.presentationTimer) {
      if (active.presentationTimer) window.clearTimeout(active.presentationTimer);
      active.presentationTimer = null;
      active.presentationQueue.length = 0;
      resolvePresentationWaiters(active);
    }
  }

  async function waitForProgressPollIdle(active, maxWaitMs = 1500) {
    const startedAt = Date.now();
    while (active?.pollInFlight && Date.now() - startedAt < maxWaitMs) {
      await new Promise((resolve) => window.setTimeout(resolve, 20));
    }
  }

  async function runStatus(requestId, afterSeq = 0) {
    const query = requestId
      ? `?request_id=${encodeURIComponent(requestId)}&after_seq=${encodeURIComponent(Number(afterSeq || 0))}`
      : "";
    return apiJson(`/api/v1/run/status${query}`, { timeoutMs: 5000 });
  }

  async function desktopRunStatus(requestId) {
    const active = activeRequests.get(String(requestId || ""));
    const gatewayRequestId = String(active?.gatewayRequestId || "").trim();
    if (!/^req_[0-9a-f]{64}$/.test(gatewayRequestId)) {
      throw new Error("desktop_gateway_request_not_registered");
    }
    return apiJson(
      `/api/v1/gateway/desktop/status?request_id=${encodeURIComponent(gatewayRequestId)}`,
      { timeoutMs: 5000 },
    );
  }

  async function desktopPresentationStatus(requestId, afterSeq = 0) {
    const active = activeRequests.get(String(requestId || ""));
    const gatewayRequestId = String(active?.gatewayRequestId || "").trim();
    if (!/^req_[0-9a-f]{64}$/.test(gatewayRequestId)) {
      throw new Error("desktop_gateway_request_not_registered");
    }
    // The native desktop status is the only terminal authority, but it does
    // not retain model/tool presentation events.  The reviewed read-only
    // status route projects those ephemeral backend events through the same
    // 7184 gateway origin so the renderer can keep replacing the current
    // work-card reply while execution is still running.
    return runStatus(gatewayRequestId, afterSeq);
  }

  function recoveryDelay(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(controlErrorFromAbort(signal, CHAT_API_TIMEOUT_MS));
        return;
      }
      const timer = window.setTimeout(() => {
        signal?.removeEventListener?.("abort", onAbort);
        resolve();
      }, ms);
      const onAbort = () => {
        window.clearTimeout(timer);
        signal?.removeEventListener?.("abort", onAbort);
        reject(controlErrorFromAbort(signal, CHAT_API_TIMEOUT_MS));
      };
      signal?.addEventListener?.("abort", onAbort, { once: true });
    });
  }

  async function recoverRunTerminal(requestId, signal) {
    let misses = 0;
    while (true) {
      if (signal?.aborted) throw controlErrorFromAbort(signal, CHAT_API_TIMEOUT_MS);
      try {
        const status = await desktopRunStatus(requestId);
        const run = status?.run;
        const expectedGatewayRequestId = String(activeRequests.get(String(requestId))?.gatewayRequestId || "");
        const actualRequestId = String(run?.gateway_request_id || status?.gateway_request_id || run?.request_id || "");
        if (!status?.ok || !run || actualRequestId !== expectedGatewayRequestId) {
          misses += 1;
          if (misses >= SSE_STATUS_MISS_LIMIT) return null;
        } else {
          misses = 0;
          // GF 门：相位判定带上网关投影/矛盾信号；待对账、部分完成、矛盾、
          // 未知全部视为终态（非成功），不再空转到超时。
          const phase = runPhaseFromStatus(run.status || run.phase || run.stage, terminalSignals(status, run));
          if (TERMINAL_RUN_PHASES.has(phase)) {
            return {
              phase,
              run,
              status,
              reply: terminalPayloadText({
                final_response: run.final_response ?? run.finalResponse,
                reply: run.reply,
                response: run.response,
                output: run.output,
                data: run.result,
              }),
            };
          }
        }
      } catch (error) {
        if (isControlError(error)) throw error;
        misses += 1;
        if (misses >= SSE_STATUS_MISS_LIMIT) return null;
      }
      await recoveryDelay(SSE_STATUS_RETRY_MS, signal);
    }
  }

  async function runControl(action, payload = {}) {
    return apiJson("/api/v1/run/control", {
      method: "POST",
      timeoutMs: 5000,
      body: JSON.stringify({ ...payload, action })
    });
  }

  function clearProgressPolling(requestId) {
    const active = activeRequests.get(requestId);
    if (active?.pollTimer) window.clearInterval(active.pollTimer);
    if (active) active.pollTimer = null;
  }

  function clearActivityTimer(active) {
    if (active?.activityTimer) window.clearTimeout(active.activityTimer);
    if (active) active.activityTimer = null;
  }

  function markRequestActivity(requestId) {
    const active = activeRequests.get(requestId);
    if (!active || !active.timeoutMs) return;
    clearActivityTimer(active);
    active.lastActivityAt = Date.now();
    active.activityTimer = window.setTimeout(() => {
      active.controller?.abort(timeoutAbortError(active.timeoutMs));
    }, active.timeoutMs);
  }

  function startProgressPolling(requestId) {
    const active = activeRequests.get(requestId);
    if (!active || active.pollTimer) return;
    active.pollInFlight = false;
    active.pollFailures = 0;
    active.nextPollAt = 0;
    markRequestActivity(requestId);
    const poll = async () => {
      const current = activeRequests.get(requestId);
      if (!current) return;
      if (current.pollInFlight || Date.now() < Number(current.nextPollAt || 0)) return;
      current.pollInFlight = true;
      try {
        // sendStream owns the native terminal-status loop while it awaits the
        // final result.  Running a second native loop here used to make the
        // same request hit /gateway/desktop/status every 750ms and every 1s.
        // Keep this poll presentation-only in that mode; the native loop
        // remains the sole terminal authority.
        const nativeStatusOwnedByTerminalWait = current.nativeStatusOwnedByTerminalWait === true;
        const results = await Promise.allSettled(
          nativeStatusOwnedByTerminalWait
            ? [desktopPresentationStatus(requestId, current.eventCursor || 0)]
            : [
              desktopRunStatus(requestId),
              desktopPresentationStatus(requestId, current.eventCursor || 0),
            ],
        );
        if (activeRequests.get(requestId) !== current || current.closed) return;
        const gatewayResult = nativeStatusOwnedByTerminalWait ? null : results[0];
        const presentationResult = nativeStatusOwnedByTerminalWait ? results[0] : results[1];
        if (presentationResult.status !== "fulfilled") throw presentationResult.reason;
        if (gatewayResult?.status === "rejected") throw gatewayResult.reason;
        const status = nativeStatusOwnedByTerminalWait
          ? presentationResult.value
          : gatewayResult.value;
        current.pollFailures = 0;
        current.nextPollAt = 0;
        const run = status?.run;
        // 严格校验：run 必须存在且 request_id 完全匹配
        if (!run || !status?.ok) return;
        const actualRequestId = String(run.gateway_request_id || status?.gateway_request_id || run.request_id || "");
        if (!actualRequestId || actualRequestId !== String(current.gatewayRequestId || "")) return;
        const runUpdatedAt = String(run.updated_at || "");
        if (runUpdatedAt && runUpdatedAt !== current.lastRunUpdatedAt) {
          current.lastRunUpdatedAt = runUpdatedAt;
          markRequestActivity(requestId);
        }
        const presentationStatus = presentationResult.value;
        const candidatePresentationRun = presentationStatus?.run;
        const presentationRequestId = String(
          candidatePresentationRun?.gateway_request_id
          || presentationStatus?.gateway_request_id
          || candidatePresentationRun?.request_id
          || ""
        );
        const presentationRun = candidatePresentationRun
          && presentationRequestId === String(current.gatewayRequestId || "")
          ? candidatePresentationRun
          : null;
        const sessionId = String(
          presentationRun?.session_id
          || presentationRun?.sessionId
          || run.session_id
          || run.sessionId
          || ""
        );
        const gatewaySteps = projectionToProgressSteps(status.gateway_projection, requestId);
        // GF 门：本轮轮询的对账/矛盾信号，随步骤相位一起下发
        const pollSignals = terminalSignals(status, run);
        for (const step of gatewaySteps) {
          const id = String(step.id || "");
          const signature = progressStepSignature(step);
          if (current.seenSteps.get(id) === signature) continue;
          current.seenSteps.set(id, signature);
          markRequestActivity(requestId);
          enqueueProgressPresentation(current, {
            ...step,
            requestId,
            sessionId,
            runPhase: runPhaseFromStatus(run.status || run.phase, pollSignals)
          });
        }
        const stageText = runSnapshotStageText(presentationRun || run);
        const projectedEvents = presentationRun && Array.isArray(presentationStatus?.events)
          ? presentationStatus.events
          : Array.isArray(status.events) ? status.events : [];
        const lastModelFinishedIndex = projectedEvents.findLastIndex((event) => String(event?.type || "").toUpperCase() === "MODEL_TURN_FINISHED");
        let stageTextQueued = false;
        for (let eventIndex = 0; eventIndex < projectedEvents.length; eventIndex += 1) {
          const event = projectedEvents[eventIndex];
          const step = runEventToProgressStep(event, presentationRun || run);
          if (step && !isInternalProgressStep(step)) {
            const id = String(step.id || `event_${event.seq || ""}`);
            const signature = progressStepSignature(step);
            if (current.seenSteps.get(id) !== signature) {
              current.seenSteps.set(id, signature);
              markRequestActivity(requestId);
              const eventType = String(event?.type || "").toUpperCase();
              emitStreamToolEvent(current, event);
              const terminalEvent = eventType === "RUN_COMPLETED" || eventType === "RUN_FAILED_SAFE";
              enqueueProgressPresentation(current, {
                ...step,
                requestId,
                sessionId,
                runPhase: terminalEvent ? runPhaseFromStatus(run.status, pollSignals) : "running"
              });
            }
          }
          if (eventIndex === lastModelFinishedIndex && stageText && stageText !== current.lastStageText) {
            current.lastStageText = stageText;
            stageTextQueued = true;
            markRequestActivity(requestId);
            enqueueTextPresentation(current, stageText, {
              requestId,
              sessionId,
              seq: Number(event?.seq || 0),
              type: "MODEL_SNAPSHOT"
            });
          }
        }
        if (!stageTextQueued && stageText && stageText !== current.lastStageText) {
          current.lastStageText = stageText;
          markRequestActivity(requestId);
          enqueueTextPresentation(current, stageText, { requestId, sessionId, type: "MODEL_SNAPSHOT" });
        }
        const nextCursor = Number(
          (presentationRun ? presentationStatus?.event_cursor?.next_seq : status?.event_cursor?.next_seq)
          || current.eventCursor
          || 0
        );
        if (nextCursor > Number(current.eventCursor || 0)) {
          current.eventCursor = nextCursor;
          markRequestActivity(requestId);
        }
        const steps = presentationRun && Array.isArray(presentationRun.steps)
          ? presentationRun.steps
          : Array.isArray(run.steps) ? run.steps : [];
        for (const step of steps) {
          if (isInternalProgressStep(step)) continue;
          const id = String(step.id || "");
          const signature = progressStepSignature(step);
          if (current.seenSteps.get(id) === signature) continue;
          current.seenSteps.set(id, signature);
          markRequestActivity(requestId);
          enqueueProgressPresentation(current, { ...step, requestId, sessionId });
        }
        // GF 门：到达任一终态相位（含待对账/部分完成/矛盾/未知）即停止轮询
        if (TERMINAL_RUN_PHASES.has(runPhaseFromStatus(run.status || run.phase, pollSignals))) {
          if (active) active.terminalRun = run;
          try {
            window.dispatchEvent(new CustomEvent("tiangong-terminal-run", {
              detail: {
                run,
                requestId,
                gatewayRequestId: String(current?.gatewayRequestId || active?.gatewayRequestId || ""),
                sessionId,
              },
            }));
          } catch (_error) {
            // 事件派发失败不影响轮询清理。
          }
          clearProgressPolling(requestId);
        }
      } catch {
        current.pollFailures = Number(current.pollFailures || 0) + 1;
        current.nextPollAt = Date.now() + Math.min(10000, 1000 * (2 ** Math.min(current.pollFailures, 4)));
      } finally {
        current.pollInFlight = false;
      }
    };
    active.pollTimer = window.setInterval(poll, 1000);
    poll();
  }

  async function replayLatestRun() {
    try {
      const status = await runStatus("");
      const run = status?.run || {};
      const requestId = String(run.request_id || run.requestId || "");
      if (!status?.ok || !requestId || recoveredRuns.has(requestId)) return;
      if (runPhaseFromStatus(run.status || run.phase) !== "running") return;
      const events = Array.isArray(status.events) ? status.events : [];
      const steps = [
        ...projectionToProgressSteps(status.gateway_projection, requestId),
        ...events.map((event) => runEventToProgressStep(event, run)).filter(Boolean),
        ...(Array.isArray(run.steps) ? run.steps : [])
      ].filter((step) => !isInternalProgressStep(step));
      if (!steps.length) return;
      recoveredRuns.add(requestId);
      const sessionId = String(run.session_id || run.sessionId || "");
      for (const step of steps) {
        emitRunStep({
          ...step,
          requestId,
          sessionId,
          runPhase: runPhaseFromStatus(run.status || run.phase),
          runStartedAt: Number(run.started_at || run.startedAt || 0) * 1000,
          recovered: Boolean(status.recovered)
        });
      }
      if (!activeRequests.has(requestId)) {
        activeRequests.set(requestId, {
          controller: null,
          closed: false,
          pollTimer: null,
          activityTimer: null,
          timeoutMs: 0,
          lastActivityAt: 0,
          lastRunUpdatedAt: String(run.updated_at || ""),
          gatewayRequestId: String(run.gateway_request_id || run.gatewayRequestId || ""),
          seenSteps: new Map(steps.map((step) => [String(step.id || ""), progressStepSignature(step)])),
          eventCursor: Number(status?.event_cursor?.next_seq || 0)
        });
        startProgressPolling(requestId);
      }
    } catch {
      // Recovery is opportunistic; normal send/polling still works.
    }
  }

  return {
    onRunStep(listener) {
      if (typeof listener !== "function") return () => {};
      runStepListeners.add(listener);
      replayLatestRun();
      return () => runStepListeners.delete(listener);
    },

    onLearningMessage(_listener) {
      return () => {};
    },

    async cancel(payload = {}) {
      const requestId = String(payload.requestId || "");
      const active = activeRequests.get(requestId);
      const controlRequestId = String(active?.gatewayRequestId || requestId);
      let stopResult = null;
      if (controlRequestId) {
        try {
          stopResult = await runControl("cancel", { request_id: controlRequestId });
        } catch (error) {
          stopResult = { ok: false, error: error?.message || String(error) };
        }
      }
      if (!active && !stopResult?.ok) return { ok: false, error: stopResult?.error || "没有正在运行的前端请求" };
      active?.controller?.abort(new Error("user_cancelled"));
      clearProgressPolling(requestId);
      clearActivityTimer(active);
      activeRequests.delete(requestId);
      return {
        ok: true,
        canceled: true,
        interrupted: true,
        summary: stopResult?.ok
          ? "停止指令已送达后端；前端已停止等待。"
          : "前端已停止等待；后端停止指令未确认。"
      };
    },

    async guide(payload = {}) {
      const requestId = String(payload.requestId || "");
      const message = String(payload.message || payload.text || "").trim();
      if (!requestId) return { ok: false, error: "missing_request_id" };
      if (!message) return { ok: false, error: "empty_guidance" };
      try {
        const active = activeRequests.get(requestId);
        const controlRequestId = String(active?.gatewayRequestId || requestId);
        return await runControl("guide", { request_id: controlRequestId, message });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async conversationEvents(payload = {}) {
      const sessionId = String(payload.sessionId || payload.session_id || payload.conversationId || payload.conversation_id || "");
      const action = String(payload.action || "").trim();
      if (!sessionId) return { ok: false, error: "missing_session_id" };
      if (!action) return { ok: false, error: "missing_action" };
      return apiJson("/api/v1/conversation/events", {
        method: "POST",
        timeoutMs: 5000,
        body: JSON.stringify({
          action,
          session_id: sessionId,
          conversation_id: sessionId,
          reason: payload.reason || ""
        })
      });
    },

    async getSettings() {
      const local = { ...defaultSettings, ...readLocalSettings() };
      try {
        const [
          llm,
          character,
          body,
          workspaceStatus,
          policyStatus,
          lifeSoulResult,
          lifePanel,
          knowledgeSettings,
          workspaceAuthority,
        ] = await Promise.all([
          readModelSettings().catch(() => null),
          apiJson("/api/v1/character/state").catch(() => null),
          apiJson("/api/v1/body/settings").catch(() => null),
          apiJson("/api/v1/workspace/settings").catch(() => null),
          apiJson("/api/v1/policy/settings").catch(() => null),
          apiJson(LIFE_API_ROUTES.soulGet.path).catch(() => null),
          apiJson(LIFE_API_ROUTES.panel.path).catch(() => null),
          apiJson("/api/v1/knowledge/settings").catch(() => null),
          readDesktopWorkspaceAuthority()
        ]);
        const bodyProfile = body?.profile || {};
        const bodyVoice = body?.voice || {};
        const bodyUser = body?.user || {};
        const bodyPresentation = body?.presentation && typeof body.presentation === "object" ? body.presentation : {};
        const bodyUi = body?.ui && typeof body.ui === "object" ? body.ui : {};
        const directLifeSoul = lifeSoulResult?.soul && typeof lifeSoulResult.soul === "object" ? lifeSoulResult.soul : null;
        const panelLifeSoul = lifePanel?.soul && typeof lifePanel.soul === "object" ? lifePanel.soul : null;
        const lifeSoul = directLifeSoul || panelLifeSoul || {};
        if (directLifeSoul || panelLifeSoul) {
          // The renderer copy is only a cache, but it must follow the signed
          // life authority after every successful read.  This keeps the body
          // summary correct across reloads and provides a last-known-good
          // projection during a transient backend restart.
          writeLocalSettings({
            personaName: typeof lifeSoul.name === "string" ? lifeSoul.name : local.personaName,
            soulPrompt: typeof lifeSoul.prompt === "string" ? lifeSoul.prompt : local.soulPrompt,
          });
        }
        const legacyPresentation = readLegacyAvatarPresentation();
        const authoritativePresentation = normalizeAvatarPresentation({
          camera: bodyPresentation.configured === true ? bodyPresentation.camera : legacyPresentation.camera,
          lighting: bodyPresentation.configured === true ? bodyPresentation.lighting : legacyPresentation.lighting,
        });
        const lifeSettings = lifePanel?.settings && typeof lifePanel.settings === "object" ? lifePanel.settings : {};
        const permissionFromLife = desktopPermissionFromLife(
          lifeSettings.permission_mode,
          lifeSettings.autonomous_risk_max,
          local.permissionMode,
          local.permissionRiskMax
        );
        const permissionMode = permissionFromLife.permissionMode;
        const permissionRiskMax = permissionFromLife.permissionRiskMax;
        const authoritativeWorkspace = workspaceAuthority?.ok && workspaceAuthority.workspace
          ? workspaceAuthority.workspace
          : workspaceStatus?.workspace || local.workspace || "";
        if (authoritativeWorkspace && !sameWorkspacePath(local.workspace, authoritativeWorkspace)) {
          writeLocalSettings({ workspace: authoritativeWorkspace });
        }
        return {
          ...local,
          workspace: authoritativeWorkspace,
          modelProvider: llm?.configured_provider ?? local.modelProvider ?? "",
          modelBaseUrl: llm?.configured_base_url ?? local.modelBaseUrl ?? "",
          modelName: llm?.configured_model_name ?? local.modelName ?? "",
          modelService: llm?.ok ? modelServiceFromLlmStatus(llm, local.modelService) : local.modelService || "custom",
          modelMatchedProvider: llm?.ok ? modelMatchedProviderFromLlmStatus(llm, local.modelMatchedProvider) : local.modelMatchedProvider || "",
          modelMatchedProviderDisplayName: llm?.matched_provider_display_name || llm?.provider_display_name || local.modelMatchedProviderDisplayName || "",
          modelProviderMatch: llm?.provider_match || local.modelProviderMatch || null,
          modelProviderPresets: Array.isArray(llm?.providers) ? llm.providers : local.modelProviderPresets,
          modelProviderProfiles: llm?.provider_profiles && typeof llm.provider_profiles === "object" && !Array.isArray(llm.provider_profiles)
            ? llm.provider_profiles
            : local.modelProviderProfiles || {},
          personaName: typeof lifeSoul.name === "string"
            ? lifeSoul.name
            : bodyProfile.name || character?.profile?.name || local.personaName,
          personaAvatarDataUrl: safeLocalAvatarDataUrl(bodyProfile.avatar_data_url || character?.profile?.avatar_data_url || local.personaAvatarDataUrl),
          soulPrompt: typeof lifeSoul.prompt === "string"
            ? lifeSoul.prompt
            : typeof bodyProfile.soul === "string"
              ? bodyProfile.soul
              : typeof character?.profile?.soul === "string"
                ? character.profile.soul
                : local.soulPrompt,
          bodyPreset: bodyProfile.body_preset || local.bodyPreset,
          bodyCamera: authoritativePresentation.camera,
          bodyLighting: authoritativePresentation.lighting,
          bodyPresentationConfigured: bodyPresentation.configured === true,
          bodyVoiceReplyEnabled: typeof bodyVoice.reply_read_aloud === "boolean" ? bodyVoice.reply_read_aloud : local.bodyVoiceReplyEnabled,
          bodyVoicePreset: bodyVoice.preset_id || local.bodyVoicePreset,
          bodyVoiceName: bodyVoice.system_voice_name || local.bodyVoiceName,
        bodyVoiceCustomName: bodyVoice.custom_voice_name || local.bodyVoiceCustomName,
        bodyVoiceCustomPath: bodyVoice.custom_voice_path || local.bodyVoiceCustomPath,
        bodyVoiceCustomState: bodyVoice.custom_voice_state || local.bodyVoiceCustomState,
        bodyVoiceOutputMode: bodyVoice.output_mode || local.bodyVoiceOutputMode,
        bodyVoiceNativeId: bodyVoice.native_voice_id || local.bodyVoiceNativeId,
        bodyVoiceSampleConsent: typeof bodyVoice.sample_consent === "boolean" ? bodyVoice.sample_consent : local.bodyVoiceSampleConsent,
        bodyVoiceLang: bodyVoice.lang || local.bodyVoiceLang,
          bodyVoiceRate: Number.isFinite(Number(bodyVoice.rate)) ? Number(bodyVoice.rate) : local.bodyVoiceRate,
          bodyVoicePitch: Number.isFinite(Number(bodyVoice.pitch)) ? Number(bodyVoice.pitch) : local.bodyVoicePitch,
          bodyVoiceVolume: Number.isFinite(Number(bodyVoice.volume)) ? Number(bodyVoice.volume) : local.bodyVoiceVolume,
          bodyVoicePresets: Array.isArray(body?.voice_presets) ? body.voice_presets : local.bodyVoicePresets,
          userName: bodyUser.name || local.userName || "",
          userDisplayName: bodyUser.display_name || bodyUser.name || local.userDisplayName || local.userName || "",
          userTitle: bodyUser.title || local.userTitle || "",
          userWork: bodyUser.work || bodyUser.title || local.userWork || local.userTitle || "",
          userAvatarDataUrl: safeLocalAvatarDataUrl(bodyUser.avatar_data_url || local.userAvatarDataUrl || ""),
          userCallsign: bodyUser.callsign || bodyUser.name || local.userCallsign || local.userName || "",
          userAlias: bodyUser.name || local.userName || local.userAlias || "",
          userProfileSummary: typeof bodyUser.profile_summary === "string" ? bodyUser.profile_summary : local.userProfileSummary || "",
          userContextEnabled: typeof bodyUser.context_enabled === "boolean" ? bodyUser.context_enabled : local.userContextEnabled !== false,
          themeStyle: ["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"].includes(bodyUi.theme_style)
            ? bodyUi.theme_style
            : local.themeStyle,
          knowledgeRoot: typeof knowledgeSettings?.knowledgeRoot === "string"
            ? knowledgeSettings.knowledgeRoot
            : local.knowledgeRoot,
          permissionMode,
          permissionRiskMax,
          permissionStatus: {
            permission_mode: permissionMode,
            autonomous_risk_max: permissionRiskMax,
            editable: lifePanel?.setup_required !== true,
            source: lifeSettings.source || "life_ip_user_overrides",
            execution_policy: policyStatus || null
          }
        };
      } catch {
        return local;
      }
    },

    async setSettings(next) {
      const hasWorkspace = Object.prototype.hasOwnProperty.call(next || {}, "workspace");
      const hasWorkspaceMode = Object.prototype.hasOwnProperty.call(next || {}, "workspace_mode");
      const hasPermissionSettings = Object.prototype.hasOwnProperty.call(next || {}, "permissionMode")
        || Object.prototype.hasOwnProperty.call(next || {}, "permission_mode")
        || Object.prototype.hasOwnProperty.call(next || {}, "permissionRiskMax")
        || Object.prototype.hasOwnProperty.call(next || {}, "autonomous_risk_max");
      const hasModelSettings = Object.prototype.hasOwnProperty.call(next || {}, "modelApiKey")
        || Object.prototype.hasOwnProperty.call(next || {}, "modelProvider")
        || Object.prototype.hasOwnProperty.call(next || {}, "modelBaseUrl")
        || Object.prototype.hasOwnProperty.call(next || {}, "modelName")
        || Object.prototype.hasOwnProperty.call(next || {}, "modelService");
      const hasLifeSoulSettings = ["personaName", "soulPrompt"]
        .some((key) => Object.prototype.hasOwnProperty.call(next || {}, key));
      const bodySettingKeys = [
        "personaName",
        "personaAvatarDataUrl",
        "bodyPreset",
        "bodyCamera",
        "bodyLighting",
        "themeStyle",
        "userName",
        "userDisplayName",
        "userCallsign",
        "userTitle",
        "userWork",
        "userAvatarDataUrl",
        "userProfileSummary",
        "userContextEnabled",
        "bodyVoiceReplyEnabled",
        "bodyVoicePreset",
        "bodyVoiceName",
        "bodyVoiceCustomName",
        "bodyVoiceCustomPath",
        "bodyVoiceOutputMode",
        "bodyVoiceNativeId",
        "bodyVoiceSampleConsent",
        "bodyVoiceLang",
        "bodyVoiceRate",
        "bodyVoicePitch",
        "bodyVoiceVolume"
      ];
      const hasBodySettings = bodySettingKeys
        .some((key) => Object.prototype.hasOwnProperty.call(next || {}, key));
      const hasExecutionPolicySettings = ["networkPolicy", "terminalPolicy"]
        .some((key) => Object.prototype.hasOwnProperty.call(next || {}, key));
      const localNext = { ...(next || {}) };
      if (hasWorkspace) delete localNext.workspace;
      if (hasWorkspaceMode) delete localNext.workspace_mode;
      if (hasPermissionSettings) {
        delete localNext.permissionMode;
        delete localNext.permission_mode;
        delete localNext.permissionRiskMax;
        delete localNext.autonomous_risk_max;
      }
      if (hasModelSettings) {
        // Model settings are committed to the authoritative runtime first.
        // Never persist a secret—or speculative endpoint/model values—in
        // renderer storage before the main-process transaction succeeds.
        for (const key of ["modelApiKey", "api_key", "clear_api_key", "modelProvider", "modelBaseUrl", "modelName", "modelService"]) {
          delete localNext[key];
        }
      }
      // These fields have durable backend authorities.  Keep renderer storage
      // as a cache only after the corresponding transaction succeeds.
      for (const key of new Set([
        ...(hasLifeSoulSettings ? ["personaName", "soulPrompt"] : []),
        ...(hasBodySettings ? bodySettingKeys : []),
        ...(hasExecutionPolicySettings ? ["networkPolicy", "terminalPolicy"] : []),
      ])) {
        delete localNext[key];
      }
      const saved = Object.keys(localNext).length ? writeLocalSettings(localNext) : { ...defaultSettings, ...readLocalSettings() };
      const tasks = [];
      if (hasModelSettings) {
        const hasModelProvider = Object.prototype.hasOwnProperty.call(next || {}, "modelProvider");
        const hasModelBaseUrl = Object.prototype.hasOwnProperty.call(next || {}, "modelBaseUrl");
        const hasModelName = Object.prototype.hasOwnProperty.call(next || {}, "modelName");
        const hasModelApiKey = Object.prototype.hasOwnProperty.call(next || {}, "modelApiKey");
        const llmBody = {
          provider: hasModelProvider ? next.modelProvider : saved.modelProvider,
          base_url: hasModelBaseUrl ? next.modelBaseUrl : saved.modelBaseUrl,
          model_name: hasModelName ? next.modelName : saved.modelName,
        };
        const apiKey = hasModelApiKey ? String(next.modelApiKey || "").trim() : "";
        // Provider aliases are insufficient for custom OpenAI-compatible endpoints:
        // the desktop main process must bind the secret to the canonical endpoint
        // origin before it restarts the backend.  Send the secret through the
        // single trusted model:setSettings IPC transaction and never persist it in
        // renderer storage or submit it to the HTTP fallback.
        const secureLlmBody = apiKey ? { ...llmBody, api_key: apiKey } : llmBody;
        tasks.push(writeModelSettings(secureLlmBody).then((data) => {
          if (!data || typeof data !== "object" || data.ok === false) throw new Error(data?.error || "模型配置保存失败");
          saved.modelService = modelServiceFromLlmStatus(data, saved.modelService);
          saved.modelProvider = data.configured_provider ?? saved.modelProvider ?? "";
          saved.modelBaseUrl = data.configured_base_url ?? saved.modelBaseUrl ?? "";
          saved.modelName = data.configured_model_name ?? saved.modelName ?? "";
          saved.modelMatchedProvider = modelMatchedProviderFromLlmStatus(data, saved.modelMatchedProvider);
          saved.modelMatchedProviderDisplayName = data.matched_provider_display_name || data.provider_display_name || saved.modelMatchedProviderDisplayName || "";
          saved.modelProviderMatch = data.provider_match || saved.modelProviderMatch || null;
          saved.modelProviderPresets = Array.isArray(data.providers) ? data.providers : saved.modelProviderPresets;
          saved.modelProviderProfiles = data.provider_profiles && typeof data.provider_profiles === "object" && !Array.isArray(data.provider_profiles)
            ? data.provider_profiles
            : saved.modelProviderProfiles || {};
          writeLocalSettings({
            modelService: saved.modelService,
            modelProvider: saved.modelProvider,
            modelBaseUrl: saved.modelBaseUrl,
            modelName: saved.modelName,
            modelMatchedProvider: saved.modelMatchedProvider,
            modelMatchedProviderDisplayName: saved.modelMatchedProviderDisplayName,
            modelProviderMatch: saved.modelProviderMatch,
            modelProviderPresets: saved.modelProviderPresets,
            modelProviderProfiles: saved.modelProviderProfiles,
          });
          return data;
        }));
      }
      if (hasWorkspace || hasWorkspaceMode) {
        tasks.push(Promise.resolve().then(async () => {
          const data = await commitDesktopWorkspace(
            next.workspace ?? saved.workspace,
            next.workspace_mode ?? saved.workspace_mode,
          );
          saved.workspace = data.workspace;
          saved.workspace_mode = data.workspace_mode === "full" ? "full" : "workspace";
          writeLocalSettings({ workspace: data.workspace, workspace_mode: saved.workspace_mode });
          return data;
        }));
      }
      if (hasPermissionSettings) {
        const permissionMode = normalizeUserPermissionMode(next.permissionMode ?? next.permission_mode ?? saved.permissionMode);
        const permissionRiskMax = normalizeAutonomyRiskMax(next.permissionRiskMax ?? next.autonomous_risk_max ?? saved.permissionRiskMax);
        const lifePayload = lifePermissionPayload(permissionMode, permissionRiskMax);
        tasks.push(apiJson(LIFE_API_ROUTES.settingsUpdate.path, {
          method: "POST",
          body: JSON.stringify({
            actor: "user",
            settings: lifePayload
          })
        }).then((data) => {
          const authoritative = data?.settings && typeof data.settings === "object" ? data.settings : null;
          if (!authoritative) throw new Error("生命权限设置未返回有效结果");
          const mapped = desktopPermissionFromLife(
            authoritative.permission_mode,
            authoritative.autonomous_risk_max,
            permissionMode,
            permissionRiskMax
          );
          saved.permissionMode = mapped.permissionMode;
          saved.permissionRiskMax = mapped.permissionRiskMax;
          saved.permissionStatus = {
            permission_mode: saved.permissionMode,
            autonomous_risk_max: saved.permissionRiskMax,
            editable: true,
            source: authoritative.source || "life_ip_user_overrides"
          };
          writeLocalSettings({
            permissionMode: saved.permissionMode,
            permissionRiskMax: saved.permissionRiskMax,
            permissionStatus: saved.permissionStatus
          });
          return data;
        }));
      }
      if (hasExecutionPolicySettings) {
        tasks.push(apiJson("/api/v1/policy/settings", {
          method: "POST",
          body: JSON.stringify({
            permission_mode: "A0-A4_AUTO_A5_SIGNED_LEASE",
            network_policy: next.networkPolicy,
            terminal_policy: next.terminalPolicy
          })
        }));
      }
      if (hasLifeSoulSettings) {
        tasks.push(Promise.resolve().then(async () => {
          const hasPersonaName = Object.prototype.hasOwnProperty.call(next || {}, "personaName");
          const hasSoulPrompt = Object.prototype.hasOwnProperty.call(next || {}, "soulPrompt");
          let currentSoul = null;
          if (!hasPersonaName || !hasSoulPrompt) {
            const current = await apiJson(LIFE_API_ROUTES.soulGet.path);
            currentSoul = current?.soul && typeof current.soul === "object" ? current.soul : null;
            if (!currentSoul) throw new Error("生命 Soul 权威值读取失败，已阻止不完整覆盖");
          }
          return apiJson(LIFE_API_ROUTES.soulUpdate.path, {
            method: "POST",
            body: JSON.stringify({
              actor: "user",
              soul: {
                name: hasPersonaName ? next.personaName : currentSoul.name,
                prompt: hasSoulPrompt ? next.soulPrompt : currentSoul.prompt
              }
            })
          });
        }).then((data) => {
          const authoritative = data?.soul && typeof data.soul === "object" ? data.soul : null;
          if (!authoritative) throw new Error("生命 Soul 设置未返回有效结果");
          saved.personaName = typeof authoritative.name === "string" ? authoritative.name : saved.personaName;
          saved.soulPrompt = typeof authoritative.prompt === "string" ? authoritative.prompt : saved.soulPrompt;
          writeLocalSettings({ personaName: saved.personaName, soulPrompt: saved.soulPrompt });
          return data;
        }));
      }
      if (hasBodySettings) {
        const desiredPresentation = normalizeAvatarPresentation({
          camera: next.bodyCamera ?? saved.bodyCamera,
          lighting: next.bodyLighting ?? saved.bodyLighting,
        });
        tasks.push(apiJson("/api/v1/body/settings", {
          method: "POST",
          body: JSON.stringify({
            profile: {
              name: next.personaName ?? saved.personaName ?? "起源",
              avatar_data_url: next.personaAvatarDataUrl ?? saved.personaAvatarDataUrl,
              body_preset: next.bodyPreset ?? saved.bodyPreset
            },
            user: {
              name: next.userName ?? saved.userName ?? "",
              display_name: next.userDisplayName ?? saved.userDisplayName ?? "",
              callsign: next.userCallsign ?? saved.userCallsign ?? "",
              title: next.userTitle ?? saved.userTitle ?? "",
              work: next.userWork ?? saved.userWork ?? "",
              avatar_data_url: next.userAvatarDataUrl ?? saved.userAvatarDataUrl ?? "",
              profile_summary: next.userProfileSummary ?? saved.userProfileSummary ?? "",
              context_enabled: next.userContextEnabled ?? saved.userContextEnabled ?? true
            },
            voice: {
              reply_read_aloud: next.bodyVoiceReplyEnabled ?? saved.bodyVoiceReplyEnabled,
              preset_id: next.bodyVoicePreset ?? saved.bodyVoicePreset,
              system_voice_name: next.bodyVoiceName ?? saved.bodyVoiceName,
              custom_voice_name: next.bodyVoiceCustomName ?? saved.bodyVoiceCustomName,
              custom_voice_path: next.bodyVoiceCustomPath ?? saved.bodyVoiceCustomPath,
              output_mode: next.bodyVoiceOutputMode ?? saved.bodyVoiceOutputMode,
              native_voice_id: next.bodyVoiceNativeId ?? saved.bodyVoiceNativeId,
              sample_consent: next.bodyVoiceSampleConsent ?? saved.bodyVoiceSampleConsent,
              lang: next.bodyVoiceLang ?? saved.bodyVoiceLang,
              rate: next.bodyVoiceRate ?? saved.bodyVoiceRate,
              pitch: next.bodyVoicePitch ?? saved.bodyVoicePitch,
              volume: next.bodyVoiceVolume ?? saved.bodyVoiceVolume
            },
            presentation: desiredPresentation,
            ui: {
              theme_style: next.themeStyle ?? saved.themeStyle ?? "ink_teal"
            },
          })
        }).then((data) => {
          if (!data || typeof data !== "object" || data.ok === false) throw new Error(data?.error || "身体与用户设置未返回有效结果");
          const profile = data.profile && typeof data.profile === "object" ? data.profile : {};
          const user = data.user && typeof data.user === "object" ? data.user : {};
          const voice = data.voice && typeof data.voice === "object" ? data.voice : {};
          const presentation = normalizeAvatarPresentation(data.presentation || desiredPresentation);
          const ui = data.ui && typeof data.ui === "object" ? data.ui : {};
          saved.personaAvatarDataUrl = safeLocalAvatarDataUrl(profile.avatar_data_url ?? saved.personaAvatarDataUrl);
          saved.bodyPreset = profile.body_preset || saved.bodyPreset;
          saved.bodyCamera = presentation.camera;
          saved.bodyLighting = presentation.lighting;
          saved.bodyPresentationConfigured = data.presentation?.configured === true;
          saved.userName = user.name ?? saved.userName ?? "";
          saved.userDisplayName = user.display_name ?? saved.userDisplayName ?? "";
          saved.userCallsign = user.callsign ?? saved.userCallsign ?? "";
          saved.userTitle = user.title ?? saved.userTitle ?? "";
          saved.userWork = user.work ?? saved.userWork ?? "";
          saved.userAvatarDataUrl = safeLocalAvatarDataUrl(user.avatar_data_url ?? saved.userAvatarDataUrl);
          saved.userProfileSummary = user.profile_summary ?? saved.userProfileSummary ?? "";
          saved.userContextEnabled = typeof user.context_enabled === "boolean"
            ? user.context_enabled
            : saved.userContextEnabled !== false;
          saved.bodyVoiceReplyEnabled = typeof voice.reply_read_aloud === "boolean" ? voice.reply_read_aloud : saved.bodyVoiceReplyEnabled;
          saved.bodyVoicePreset = voice.preset_id || saved.bodyVoicePreset;
          saved.bodyVoiceName = voice.system_voice_name ?? saved.bodyVoiceName;
          saved.bodyVoiceCustomName = voice.custom_voice_name ?? saved.bodyVoiceCustomName;
          saved.bodyVoiceCustomPath = voice.custom_voice_path ?? saved.bodyVoiceCustomPath;
          saved.bodyVoiceCustomState = voice.custom_voice_state || saved.bodyVoiceCustomState;
          saved.bodyVoiceOutputMode = voice.output_mode || saved.bodyVoiceOutputMode;
          saved.bodyVoiceNativeId = voice.native_voice_id ?? saved.bodyVoiceNativeId;
          saved.bodyVoiceSampleConsent = typeof voice.sample_consent === "boolean" ? voice.sample_consent : saved.bodyVoiceSampleConsent;
          saved.bodyVoiceLang = voice.lang || saved.bodyVoiceLang;
          saved.bodyVoiceRate = Number.isFinite(Number(voice.rate)) ? Number(voice.rate) : saved.bodyVoiceRate;
          saved.bodyVoicePitch = Number.isFinite(Number(voice.pitch)) ? Number(voice.pitch) : saved.bodyVoicePitch;
          saved.bodyVoiceVolume = Number.isFinite(Number(voice.volume)) ? Number(voice.volume) : saved.bodyVoiceVolume;
          saved.themeStyle = ["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"].includes(ui.theme_style)
            ? ui.theme_style
            : saved.themeStyle;
          writeLocalSettings({
            personaAvatarDataUrl: saved.personaAvatarDataUrl,
            bodyPreset: saved.bodyPreset,
            bodyCamera: saved.bodyCamera,
            bodyLighting: saved.bodyLighting,
            bodyPresentationConfigured: saved.bodyPresentationConfigured,
            userName: saved.userName,
            userDisplayName: saved.userDisplayName,
            userCallsign: saved.userCallsign,
            userTitle: saved.userTitle,
            userWork: saved.userWork,
            userAvatarDataUrl: saved.userAvatarDataUrl,
            userProfileSummary: saved.userProfileSummary,
            userContextEnabled: saved.userContextEnabled,
            bodyVoiceReplyEnabled: saved.bodyVoiceReplyEnabled,
            bodyVoicePreset: saved.bodyVoicePreset,
            bodyVoiceName: saved.bodyVoiceName,
            bodyVoiceCustomName: saved.bodyVoiceCustomName,
            bodyVoiceCustomPath: saved.bodyVoiceCustomPath,
            bodyVoiceCustomState: saved.bodyVoiceCustomState,
            bodyVoiceOutputMode: saved.bodyVoiceOutputMode,
            bodyVoiceNativeId: saved.bodyVoiceNativeId,
            bodyVoiceSampleConsent: saved.bodyVoiceSampleConsent,
            bodyVoiceLang: saved.bodyVoiceLang,
            bodyVoiceRate: saved.bodyVoiceRate,
            bodyVoicePitch: saved.bodyVoicePitch,
            bodyVoiceVolume: saved.bodyVoiceVolume,
            themeStyle: saved.themeStyle,
          });
          return data;
        }));
      }
      if (tasks.length) await Promise.all(tasks);
      return saved;
    },

    async deleteProviderApiKey(provider) {
      const bridge = desktopBridge();
      if (typeof bridge?.setModelSettings !== "function") {
        throw new Error("本机安全凭据删除通道不可用");
      }
      const current = await readModelSettings();
      const value = String(current?.configured_provider || provider || "").trim().toLowerCase();
      const baseUrl = String(current?.configured_base_url || "").trim();
      const modelName = String(current?.configured_model_name || "").trim();
      if (!value) throw new Error("请先选择或填写服务商");
      if (!baseUrl) throw new Error("请先填写并保存模型 Base URL");
      // Deletion must use the same canonical endpoint binding as creation.
      // A custom OpenAI-compatible endpoint is stored under endpoint_<sha256>,
      // not under the visible provider alias.
      const result = await bridge.setModelSettings({
        provider: value,
        base_url: baseUrl,
        model_name: modelName,
        clear_api_key: true,
      });
      if (!result?.ok) throw new Error(result?.error || "API Key 删除失败");
      await readModelSettings();
      return result;
    },

    async status() {
      try {
        if (frontendKernel?.probe) await frontendKernel.probe();
        const payload = await refreshBackendSnapshot();
        return {
          ok: true,
          degraded: Boolean(payload.degraded),
          stdout: statusStdout(payload),
          stderr: payload.degraded ? (payload.life_error || "生命内核处于降级状态") : ""
        };
      } catch (error) {
        try {
          return await desktopDegradedStatus(error);
        } catch (fallbackError) {
          return { ok: false, stdout: "", stderr: `后端未连接：${fallbackError?.message || error?.message || error}` };
        }
      }
    },

    async config() {
      try {
        const data = await readModelSettings();
        return { ok: true, stdout: JSON.stringify(data, null, 2), stderr: "" };
      } catch (error) {
        return { ok: false, stdout: "", stderr: `配置读取失败：${error?.message || error}` };
      }
    },

    async send(payload = {}) {
      // Keep the compatibility entry point on the same ticketed desktop route
      // as the product UI. The legacy internal route is intentionally closed
      // and would otherwise surface a misleading "gateway disconnected" error.
      return this.sendStream(payload);
    },

    async skillsList() {
      try {
        const [data, capabilities, tools] = await Promise.all([
          apiJson("/api/v1/v3/skills"),
          apiJson("/api/v1/v3/capabilities").catch(() => ({})),
          apiJson("/api/v1/v3/tools").catch(() => ({}))
        ]);
        const toolSummary = tools?.summary && typeof tools.summary === "object" ? tools.summary : {};
        const summary = {
          ...(data.summary || {}),
          ...(Number(toolSummary.runtimeToolCount ?? toolSummary.toolCount) > 0 ? {
            toolCount: Number(toolSummary.toolCount || toolSummary.runtimeToolCount),
            runtimeToolCount: Number(toolSummary.runtimeToolCount || toolSummary.toolCount),
            declaredToolCount: Number(toolSummary.total || 0),
            unavailableToolCount: Number(toolSummary.unavailable || 0)
          } : {})
        };
        return {
          ok: data?.ok !== false,
          categories: Array.isArray(data.categories) ? data.categories : [],
          abilities: Array.isArray(data.abilities) ? data.abilities : [],
          summary,
          capabilities: capabilities && typeof capabilities === "object" ? capabilities : {},
          toolsSummary: toolSummary,
          registryPath: data.registryPath || "",
          generatedIndexPath: data.generatedIndexPath || data.generated_index_path || ""
        };
      } catch (error) {
        return { ok: false, error: error?.message || String(error), categories: [], abilities: [], summary: {}, capabilities: {} };
      }
    },

    async deleteSkill(payload = {}) {
      try {
        if (payload && typeof payload === "object" && String(payload.artifact_id || "").trim()) {
          return await apiJson(LIFE_API_ROUTES.capabilityDiscard.path, {
            method: "POST",
            body: JSON.stringify({ artifact_id: String(payload.artifact_id).trim(), reason: "user_deleted" })
          });
        }
        return await apiJson("/api/v1/v3/skills/delete", {
          method: "POST",
          body: JSON.stringify(payload || {})
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async activateSkill(payload = {}) {
      try {
        const artifactId = String(payload?.artifact_id || "").trim();
        if (!artifactId) return { ok: false, error: "缺少技能资产编号" };
        return await apiJson(LIFE_API_ROUTES.capabilityActivate.path, {
          method: "POST",
          body: JSON.stringify({ artifact_id: artifactId, actor: "user" })
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async confirmLearningCard(payload = {}, item = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningConfirm.path, {
          method: "POST",
          body: JSON.stringify(learningRuntimePayload(payload, item))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async processLearningCard(payload = {}, item = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningProcessApproved.path, {
          method: "POST",
          body: JSON.stringify(learningRuntimePayload(payload, item))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async requestLearningActivation(payload = {}, item = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningRequestActivation.path, {
          method: "POST",
          body: JSON.stringify(learningRuntimePayload(payload, item))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async activateLearningCard(payload = {}, item = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningActivate.path, {
          method: "POST",
          body: JSON.stringify(learningRuntimePayload(payload, item))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async releaseLearningCard(payload = {}, item = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningRelease.path, {
          method: "POST",
          body: JSON.stringify(learningRuntimePayload(payload, item))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async discardLearningCard(payload = {}, item = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningDiscard.path, {
          method: "POST",
          body: JSON.stringify(learningRuntimePayload(payload, item))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async decideLearning(request, extra = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.learningDecide.path, {
          method: "POST",
          body: JSON.stringify({ request: String(request || ""), source: "user_direct", ...extra })
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async recordConversationTurn(userText, assistantText, extra = {}) {
      try {
        return await apiJson(LIFE_API_ROUTES.memoryTurn.path, {
          method: "POST",
          body: JSON.stringify({ user_text: String(userText || ""), assistant_text: String(assistantText || ""), actor: "frontend", ...extra })
        });
      } catch (error) {
        // Memory enrichment is asynchronous and must never hide a completed
        // conversation from the user when the Life service is temporarily busy.
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async learnLearningExperience(payload = {}, item = {}) {
      return this.confirmLearningCard(payload, item);
    },

    async confirmLifecycleUpdate(payload = {}, item = {}) {
      return this.confirmLearningCard(payload, item);
    },

    async denyLifecycleUpdate(payload = {}) {
      return this.discardLearningCard({ ...(payload || {}), reason: payload?.reason || "user_denied_lifecycle_update" });
    },

    async deleteLearningExperience(payload = {}) {
      return this.discardLearningCard({ ...(payload || {}), reason: payload?.reason || "user_deleted_learning_experience" });
    },

    async gatewayLinksStatus() {
      try {
        return await apiJson("/api/v1/gateway/links/status");
      } catch (error) {
        return { ok: false, error: error?.message || String(error), settings: {}, links: {} };
      }
    },

    async saveGatewayLinks(settings = {}) {
      try {
        // There is no renderer-to-transport settings sink. WeChat secrets are
        // produced by the authenticated QR flow and remain inside the gateway's
        // protected store; the renderer must never forward a historical form or
        // handle plaintext/partial credentials.
        const enabled = settings?.wechat?.enabled === true;
        let action = await this.gatewayLinksAction({
          action: enabled ? "wechat_direct_start" : "wechat_direct_stop"
        });
        if (enabled && action?.error === "missing_credentials") {
          action = await this.gatewayLinksAction({ action: "wechat_direct_login_start" });
        }
        const status = await this.gatewayLinksStatus();
        return {
          ...status,
          ok: status?.ok === true && action?.ok !== false,
          action_result: action,
          error: action?.ok === false ? (action.error || action.message || "wechat_action_failed") : status?.error
        };
      } catch (error) {
        return { ok: false, error: error?.message || String(error), settings: {}, links: {} };
      }
    },

    async gatewayLinksAction(payload = {}) {
      try {
        return await apiJson("/api/v1/gateway/links/action", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error), stdout: "", stderr: error?.message || String(error) };
      }
    },

    async listDailyLogs() {
      const bridge = desktopBridge();
      if (!bridge?.listDailyLogs) return { ok: false, error: "desktop_logs_unavailable", logs: [] };
      return bridge.listDailyLogs();
    },

    async verifyWebProject(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.verifyWebProject) {
        return { ok: false, error: "desktop_web_qa_unavailable", issues: ["桌面 Web 验收桥接不可用。"] };
      }
      return bridge.verifyWebProject(payload || {});
    },

    async messageChannelStatus() {
      const status = await this.gatewayLinksStatus().catch((error) => ({ ok: false, error: error?.message || String(error) }));
      return {
        ok: Boolean(status?.ok),
        channels: status?.links || status?.settings || {},
        status,
        error: status?.ok === false ? status.error : ""
      };
    },

    async connectMessageChannel(payload = {}) {
      const action = String(payload.action || "").trim();
      if (!action) return { ok: false, error: "missing_channel_action" };
      return this.gatewayLinksAction(payload);
    },

    async chooseKnowledgeRoot() {
      const bridge = desktopBridge();
      if (!bridge?.chooseKnowledgeRoot) return { ...defaultSettings, ...readLocalSettings(), canceled: true, error: "desktop_bridge_unavailable" };
      const result = await bridge.chooseKnowledgeRoot();
      if (result?.canceled || !result?.path) return { ...defaultSettings, ...readLocalSettings(), canceled: true };
      const settings = writeLocalSettings({ knowledgeRoot: result.path });
      await apiJson("/api/v1/knowledge/configure", {
        method: "POST",
        body: JSON.stringify(runtimePayload({ knowledgeRoot: result.path }))
      });
      return settings;
    },

    async chooseWorkspace() {
      const bridge = desktopBridge();
      if (!bridge?.chooseWorkspaceRoot) return { ...defaultSettings, ...readLocalSettings(), canceled: true, error: "desktop_bridge_unavailable" };
      const local = { ...defaultSettings, ...readLocalSettings() };
      const result = await bridge.chooseWorkspaceRoot({ workspace: local.workspace });
      if (result?.canceled || !result?.path) return { ...local, canceled: true };
      return this.chooseWorkspaceRoot(result.path);
    },

    async chooseWorkspaceRoot(root) {
      const workspace = String(root || "").trim();
      if (!workspace) return { ...defaultSettings, ...readLocalSettings(), canceled: true };
      const saved = await commitDesktopWorkspace(workspace);
      return writeLocalSettings({ workspace: saved.workspace });
    },

    async chooseStorageRoot() {
      const bridge = desktopBridge();
      if (!bridge?.chooseStorageRoot) return { ...defaultSettings, ...readLocalSettings(), canceled: true, error: "desktop_bridge_unavailable" };
      const local = { ...defaultSettings, ...readLocalSettings() };
      const result = await bridge.chooseStorageRoot({ workspace: local.workspace, defaultPath: local.storageRoot || local.workspace });
      if (result?.canceled || !result?.path) return { ...local, canceled: true };
      return writeLocalSettings({ storageRoot: result.path });
    },

    async choosePersonaAvatar() {
      const bridge = desktopBridge();
      if (!bridge?.choosePersonaAvatar) return { ...defaultSettings, ...readLocalSettings(), canceled: true, error: "desktop_bridge_unavailable" };
      const result = await bridge.choosePersonaAvatar();
      if (result?.canceled || !result?.personaAvatarDataUrl) return { ...defaultSettings, ...readLocalSettings(), canceled: true };
      return writeLocalSettings({ personaAvatarDataUrl: result.personaAvatarDataUrl });
    },

    async chooseUserAvatar() {
      const bridge = desktopBridge();
      if (!bridge?.chooseUserAvatar) return { ...defaultSettings, ...readLocalSettings(), canceled: true, error: "desktop_bridge_unavailable" };
      const result = await bridge.chooseUserAvatar();
      if (result?.canceled || !result?.userAvatarDataUrl) return { ...defaultSettings, ...readLocalSettings(), canceled: true };
      return writeLocalSettings({ userAvatarDataUrl: result.userAvatarDataUrl });
    },

    async chooseVoiceSample() {
      const bridge = desktopBridge();
      if (!bridge?.chooseVoiceSample) return { ...defaultSettings, ...readLocalSettings(), canceled: true, error: "desktop_bridge_unavailable" };
      const result = await bridge.chooseVoiceSample();
      if (result?.canceled || !result?.path) return { ...defaultSettings, ...readLocalSettings(), canceled: true };
      return writeLocalSettings({
        bodyVoiceCustomPath: result.path,
        bodyVoiceCustomName: result.name || ""
      });
    },

    async knowledgeList(payload = {}) {
      try {
        return await apiJson("/api/v1/knowledge/list", {
          method: "POST",
          body: JSON.stringify(runtimePayload(payload))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error), documents: [] };
      }
    },

    async chooseKnowledgeFiles(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.chooseKnowledgeFiles) return { ok: false, error: "desktop_file_dialog_unavailable", documents: [] };
      const selected = await bridge.chooseKnowledgeFiles(runtimePayload(payload));
      if (selected?.canceled || !Array.isArray(selected?.paths) || !selected.paths.length) {
        return this.knowledgeList(payload);
      }
      try {
        return await apiJson("/api/v1/knowledge/import", {
          method: "POST",
          body: JSON.stringify(runtimePayload({ ...payload, paths: selected.paths }))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error), documents: [], imported: [], failed: [] };
      }
    },

    async chooseChatFiles(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.chooseChatFiles) return { ok: false, error: "desktop_file_dialog_unavailable", attachments: [] };
      try {
        return await bridge.chooseChatFiles(runtimePayload(payload));
      } catch (error) {
        return { ok: false, error: error?.message || String(error), attachments: [], imported: [], failed: [] };
      }
    },

    async pasteChatFiles(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.uploadChatFiles) {
        return { ok: false, error: "desktop_file_upload_bridge_unavailable", attachments: [], imported: [], failed: [] };
      }
      try {
        return await bridge.uploadChatFiles(runtimePayload(payload));
      } catch (error) {
        return { ok: false, error: error?.message || String(error), attachments: [], imported: [], failed: [] };
      }
    },

    async knowledgeQuery(payload = {}) {
      try {
        return await apiJson("/api/v1/knowledge/query", {
          method: "POST",
          body: JSON.stringify(runtimePayload(payload))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async knowledgeSearch(payload = {}) {
      try {
        return await apiJson("/api/v1/knowledge/search", {
          method: "POST",
          body: JSON.stringify(runtimePayload(payload))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error), cards: [] };
      }
    },

    async knowledgeOrganize(payload = {}) {
      try {
        return await apiJson("/api/v1/knowledge/organize", {
          method: "POST",
          body: JSON.stringify(runtimePayload(payload))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error), documents: [] };
      }
    },

    async knowledgeExport(payload = {}) {
      try {
        return await apiJson("/api/v1/knowledge/export", {
          method: "POST",
          body: JSON.stringify(runtimePayload(payload))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },

    async knowledgeRemove(payload = {}) {
      try {
        return await apiJson("/api/v1/knowledge/remove", {
          method: "POST",
          body: JSON.stringify(runtimePayload(payload))
        });
      } catch (error) {
        return { ok: false, error: error?.message || String(error), documents: [] };
      }
    },

    async openPath(targetPath) {
      const bridge = desktopBridge();
      if (!bridge?.openPath || !targetPath) return { ok: false, error: "open_path_unavailable" };
      return bridge.openPath(targetPath);
    },

    async openArtifact(item = {}) {
      const bridge = desktopBridge();
      if (!bridge?.openArtifact) return { ok: false, error: "artifact_open_unavailable" };
      const payload = {
        artifact_schema: item.artifact_schema,
        gateway_request_id: item.gateway_request_id,
        run_id: item.run_id,
        generation: item.generation,
        artifact_revision_id: item.artifact_revision_id,
        manifest_sha256: item.manifest_sha256,
        card_sha256: item.card_sha256,
        content_sha256: item.content_sha256,
        size_bytes: item.size_bytes,
      };
      return bridge.openArtifact(payload);
    },

    async openDailyLog(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.openDailyLog) return { ok: false, error: "desktop_logs_unavailable" };
      return bridge.openDailyLog(payload);
    },

    async deleteDailyLog(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.deleteDailyLog) return { ok: false, error: "desktop_logs_unavailable", logs: [] };
      return bridge.deleteDailyLog(payload);
    },

    async saveTargetAs(target, payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.saveTargetAs || !target) return { ok: false, error: "save_target_unavailable" };
      return bridge.saveTargetAs(target, payload);
    },

    async copyMedia(payload = {}) {
      const bridge = desktopBridge();
      if (!bridge?.copyMedia) return { ok: false, error: "copy_media_unavailable" };
      return bridge.copyMedia(payload);
    },

    async sendStream(payload = {}, { onText, onStageText, onToolCall, onToolResult, onBiaoxian } = {}) {
      const settings = { ...defaultSettings, ...readLocalSettings(), ...(payload || {}) };
      const text = String(payload.message || payload.text || "").trim();
      if (!text) return { ok: false, stdout: "", stderr: "消息为空" };
      try {
        const llmStatus = await apiJson("/api/v1/llm/status", { timeoutMs: 5000 });
        const credentialState = String(llmStatus?.credential_state || "").trim().toLowerCase();
        if (credentialState && credentialState !== "configured") {
          return {
            ok: false,
            code: "model_credential_missing",
            stdout: "",
            stderr: "模型服务尚未配置。请先打开“设置 → 模型服务”，填写并保存 API Key，然后再发送消息。"
          };
        }
      } catch {
        // Backend startup errors are handled by the bounded runtime-state retry
        // below; do not misreport them as a missing credential.
      }
      const requestId = String(payload.requestId || messageId("run"));
      const sessionId = String(payload.sessionId || payload.activeSessionId || requestId);
      if (activeRequests.has(requestId)) {
        return { ok: false, code: "request_already_active", stdout: "", stderr: "同一请求正在执行，请等待完成后再试。" };
      }
      const controller = new AbortController();
      activeRequests.set(requestId, {
        controller,
        closed: false,
        pollTimer: null,
        activityTimer: null,
        timeoutMs: CHAT_API_TIMEOUT_MS,
        lastActivityAt: 0,
        lastRunUpdatedAt: "",
        seenSteps: new Map(),
        lastStageText: "",
        onStageText,
        onToolCall,
        onToolResult,
        onBiaoxian,
        presentationQueue: [],
        presentationTimer: null,
        presentationWaiters: [],
        presentedProgressKey: "thinking",
        eventCursor: 0
      });

      const personaName = String(settings.personaName || "起源").trim() || "起源";
      const attachments = gatewayAttachmentRefs(payload.attachments);
      const knowledgeReferences = Array.isArray(payload.knowledgeReferences) ? payload.knowledgeReferences.slice(0, 8) : [];

      let finalReply = "";
      let finalDonePayload = null;
      let streamError = "";
      let recoveredFromStatus = false;
      let gatewayRequestId = "";
      let streamTerminalRun = null;
      // GF 门：本轮的终态相位（reconcile_required/partial/incident/unknown/failed/finished）
      let terminalPhase = "";

      try {
        const acceptance = await apiJson("/api/v1/gateway/desktop/inbound", {
          method: "POST",
          // 本地网关在就绪探针/后台批处理瞬时繁忙时，请求可能排队数秒；
          // 入站是任务生命线的第一跳，超时必须宽于常规轮询（30s，仍远小于
          // 900s 活动预算），避免偶发连接池竞争直接杀死任务。
          timeoutMs: 30000,
          body: JSON.stringify({
          presentation_request_id: requestId,
          session_id: sessionId,
          // The presentation request is also the stable channel message
          // identity.  If the renderer loses the acceptance response and
          // retries the same request, the gateway journal returns the existing
          // entry instead of executing tools twice.
          message_id: requestId,
          submitted_at_ms: Date.now(),
          text,
          attachments,
          }),
        });
        gatewayRequestId = String(acceptance?.gateway_request_id || "");
        if (
          acceptance?.schema !== "tiangong.gateway.desktop-inbound-acceptance.v1"
          || acceptance?.ok !== true
          || !/^req_[0-9a-f]{64}$/.test(gatewayRequestId)
        ) {
          throw new Error("desktop_gateway_acceptance_invalid");
        }
        const active = activeRequests.get(requestId);
        if (active?.controller === controller) {
          active.gatewayRequestId = gatewayRequestId;
          // recoverRunTerminal below now owns the one native status loop for
          // this request.  Progress polling continues through /run/status for
          // stage text and tool cards, without duplicating that native probe.
          active.nativeStatusOwnedByTerminalWait = true;
          markRequestActivity(requestId);
          startProgressPolling(requestId);
        }
        const recovered = await recoverRunTerminal(requestId, controller.signal);
        if (recovered) {
          recoveredFromStatus = true;
          if (recovered.reply) finalReply = recovered.reply;
          finalDonePayload = {
            ...(recovered.run || {}),
            reply: recovered.reply || finalReply,
            recovered_from_status: true,
            // GF 门：网关投影随终态载荷传递，成功判定只信权威事实
            gateway_projection: recovered.status?.gateway_projection || null,
          };
          if (recovered.run && (recovered.run.terminal || recovered.run.simple_chain_status)) {
            try {
              window.dispatchEvent(new CustomEvent("tiangong-terminal-run", {
                detail: {
                  run: recovered.run,
                  requestId,
                  gatewayRequestId,
                  presentationRequestId: requestId,
                  sessionId: String(recovered.run.session_id || recovered.run.sessionId || ""),
                },
              }));
            } catch (_error) {
              // 事件派发失败不影响主流程。
            }
          }
          if (recovered.phase !== "finished") {
            // GF 门：区分待对账/部分完成/矛盾/未知，文案明确非成功；待对账禁止重试
            terminalPhase = ["reconcile_required", "partial", "incident", "unknown"].includes(recovered.phase)
              ? recovered.phase
              : "failed";
            const phaseNotice = {
              reconcile_required: "结果待对账，禁止重试。网关正在核对执行与投递事实，请等待对账完成。",
              partial: "部分完成。网关裁决本次任务只达成了一部分，未达成部分不得当作成功。",
              incident: "检测到矛盾结果，本轮按非成功事件处理，请不要据此前结果继续。",
              unknown: "状态未知，按未成功处理。网关未给出可核验的终态。",
            }[terminalPhase] || "";
            const detail = recovered.run?.error_detail;
            const code = String(detail?.code || recovered.run?.error || recovered.run?.last_error || (phaseNotice ? `gateway_${terminalPhase}` : "gateway_request_failed"));
            const message = phaseNotice || String(detail?.message || "网关执行未成功完成");
            const action = String(detail?.action || "").trim();
            streamError = `${message}${action ? `\n处理建议：${action}` : ""}\n错误码：${code}`;
          } else {
            terminalPhase = "finished";
          }
        } else {
          streamError = "网关未返回可验证的终态。";
          terminalPhase = "unknown";
        }
      } catch (error) {
        if (isControlError(error)) {
          const code = String(error.code || "");
          return {
            ok: false,
            code,
            canceled: code === "aborted",
            interrupted: code === "aborted",
            timedOut: code === "timeout",
            stdout: "",
            stderr: error.message || (code === "timeout" ? timeoutText(CHAT_API_TIMEOUT_MS) : "请求已中断。")
          };
        }
        streamError = error?.message || String(error);
      } finally {
        const active = activeRequests.get(requestId);
        if (active?.controller === controller) {
          streamTerminalRun = active?.terminalRun || null;
          clearProgressPolling(requestId);
          // A status request may already be in flight when the terminal chat
          // response arrives. Let it enqueue its last model/tool snapshots
          // before draining the visual playback queue.
          await waitForProgressPollIdle(active);
          // If a status fetch outlives the bounded wait, it must not publish a
          // stale stage snapshot after the terminal reply has replaced it.
          active.closed = true;
          await waitForPresentationQueue(active);
          if (active.activityTimer) window.clearTimeout(active.activityTimer);
          activeRequests.delete(requestId);
        }
      }

      const artifactCards = await gatewayArtifactCards(gatewayRequestId || requestId);

      if (streamError) {
        const parsed = parseFinalReplyPayload(finalReply);
        return {
          ok: false,
          // GF 门：把终态相位透传给 actions/会话面板，用于渲染非成功卡片
          phase: terminalPhase || "failed",
          code: String(finalDonePayload?.error_detail?.code || finalDonePayload?.error || "gateway_request_failed"),
          stdout: finalReply || "",
          stderr: streamError,
          attachments: Array.isArray(parsed.attachments) ? parsed.attachments : (Array.isArray(finalDonePayload?.attachments) ? finalDonePayload.attachments : []),
          generated_attachments: mergeGatewayArtifactCards(
            Array.isArray(parsed.generated_attachments) ? parsed.generated_attachments : (Array.isArray(finalDonePayload?.generated_attachments) ? finalDonePayload.generated_attachments : []),
            artifactCards,
          ),
          simple_chain_status: parsed.simple_chain_status || finalDonePayload?.simple_chain_status || streamTerminalRun?.simple_chain_status || "",
          terminal: finalDonePayload?.terminal || parsed.terminal || streamTerminalRun?.terminal || null,
          recovered_from_status: recoveredFromStatus
        };
      }
      const parsed = parseFinalReplyPayload(finalReply);
      // GF 门（草案 §8 不变量）：成功只能来自网关 CompletionGate 裁决
      // （COMPLETED），或 run 终态 SUCCEEDED 且无矛盾、无对账标记。
      // 模型自报 zhuangtai 仅作展示文本保留，绝不参与成功判定。
      const modelTerminalStatus = String(parsed.zhuangtai || "").trim().toLowerCase();
      const verdict = terminalSuccessVerdict({
        reply: finalReply,
        run: finalDonePayload || {},
        status: { gateway_projection: finalDonePayload?.gateway_projection || null },
      });
      return {
        ok: verdict.ok,
        phase: verdict.phase,
        model_terminal_status: modelTerminalStatus,
        stdout: finalReply,
        stderr: "",
        attachments: Array.isArray(parsed.attachments) ? parsed.attachments : (Array.isArray(finalDonePayload?.attachments) ? finalDonePayload.attachments : []),
        generated_attachments: mergeGatewayArtifactCards(
          Array.isArray(parsed.generated_attachments) ? parsed.generated_attachments : (Array.isArray(finalDonePayload?.generated_attachments) ? finalDonePayload.generated_attachments : []),
          artifactCards,
        ),
        simple_chain_status: parsed.simple_chain_status || finalDonePayload?.simple_chain_status || streamTerminalRun?.simple_chain_status || "",
        terminal: finalDonePayload?.terminal || parsed.terminal || streamTerminalRun?.terminal || null,
        recovered_from_status: recoveredFromStatus
      };
    }
  };
}
