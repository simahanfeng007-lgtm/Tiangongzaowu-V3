const { contextBridge, ipcRenderer } = require("electron");

// Sandboxed Electron preloads do not receive arbitrary environment variables.
// Ask the trusted main process for the per-launch desktop credential instead
// of silently exposing an empty header on every fresh packaged install.
const gatewayBootstrap = (() => {
  try {
    const value = ipcRenderer.sendSync("gateway:getBootstrap");
    return value && typeof value === "object" ? value : {};
  } catch (_error) {
    return {};
  }
})();
const gatewayUrl = String(gatewayBootstrap.gatewayUrl || "http://127.0.0.1:7184");
// Compatibility aliases deliberately resolve to 7184.  The renderer has no
// business path to the legacy service ports after P7.2.
const backendUrl = gatewayUrl;
const lifeUrl = gatewayUrl;
const communicationUrl = gatewayUrl;
const backendToken = String(gatewayBootstrap.desktopToken || "");
const backendHeaders = Object.freeze(backendToken ? { "X-Tiangong-Token": backendToken } : {});
const bootstrapMetadata = gatewayBootstrap.frontendMetadata
  && typeof gatewayBootstrap.frontendMetadata === "object"
  ? gatewayBootstrap.frontendMetadata
  : {};
const frontendMetadata = Object.freeze({
  version: String(bootstrapMetadata.version || ""),
  productLabel: String(bootstrapMetadata.productLabel || "天工造物 v3.0 完整版"),
  sourceMode: bootstrapMetadata.sourceMode === true,
  kernelVersion: String(bootstrapMetadata.kernelVersion || bootstrapMetadata.version || ""),
  buildId: String(bootstrapMetadata.buildId || ""),
  contractId: "tiangong.frontend.kernel.v3.0-complete",
  backendContractId: "tiangong.total-gateway.api.v1",
});

const WINDOW_ACTIONS = new Map([
  ["minimize", "window:minimize"],
  ["maximize", "window:maximize"],
  ["close", "window:close"],
]);

function sendWindowAction(action) {
  const channel = WINDOW_ACTIONS.get(action);
  if (!channel) return false;
  ipcRenderer.send(channel);
  return true;
}

// P2b 受控资产通道（方案 §8.4）：MessagePort 分块流。preload 侧只暴露窄 API——
// 返回 { postMessage, onMessage, close } 形态的 port 门面，不暴露 ipcRenderer、
// 不暴露任何绝对路径；descriptor 仅含 scope/locator/hash 等 opaque 字段。
function openAvatarAssetChannel(descriptor) {
  const channel = new MessageChannel();
  ipcRenderer.postMessage("avatar:openChunkedStream", descriptor || {}, [channel.port2]);
  const port = channel.port1;
  return Object.freeze({
    postMessage: (message) => port.postMessage(message),
    onMessage: (callback) => {
      port.onmessage = (event) => callback(event.data);
    },
    close: () => port.close(),
  });
}

function setThemeStyle(themeStyle) {
  return ipcRenderer.invoke("theme:set", themeStyle);
}

const AVATAR_STORAGE_LIMITS = Object.freeze({
  "registry-v1": 4 * 1024 * 1024,
  "pending-load-v1": 64 * 1024,
  "quarantine-state-v1": 2 * 1024 * 1024,
});

function requireAvatarStorageKey(key) {
  if (typeof key !== "string" || !Object.hasOwn(AVATAR_STORAGE_LIMITS, key)) {
    throw new TypeError("avatar storage key 不在固定枚举内");
  }
  return key;
}

function requireAvatarStorageBytes(key, bytes) {
  if (
    bytes === null
    || typeof bytes !== "object"
    || !Number.isInteger(bytes.byteLength)
    || bytes.byteLength <= 0
    || bytes.byteLength > AVATAR_STORAGE_LIMITS[key]
  ) {
    throw new TypeError("avatar storage bytes 类型或大小非法");
  }
  return bytes;
}

contextBridge.exposeInMainWorld("tiangongDesktop", {
  backendUrl,
  lifeUrl,
  communicationUrl,
  gatewayUrl,
  frontendMetadata,
  getFrontendMetadata: () => ({ ...frontendMetadata }),
  getBackendUrl: () => backendUrl,
  getLifeUrl: () => lifeUrl,
  getCommunicationUrl: () => communicationUrl,
  getGatewayUrl: () => gatewayUrl,
  getBackendHeaders: () => ({ ...backendHeaders }),
  getGatewayHeaders: () => ({ ...backendHeaders }),
  sendWindowAction,
  setThemeStyle,
  writeDiagnostic: (kind, detail = "") => ipcRenderer.send("diagnostic:write", { kind, detail }),
  getModelSettings: () => ipcRenderer.invoke("model:getSettings"),
  setModelSettings: (payload) => ipcRenderer.invoke("model:setSettings", payload || {}),
  probeProviderApi: () => ipcRenderer.invoke("model:probeProviderApi"),
  testDeepSeekConnection: () => ipcRenderer.invoke("model:testDeepSeekConnection"),
  choosePersonaAvatar: () => ipcRenderer.invoke("dialog:choosePersonaAvatar"),
  chooseUserAvatar: () => ipcRenderer.invoke("dialog:chooseUserAvatar"),
  chooseVoiceSample: () => ipcRenderer.invoke("dialog:chooseVoiceSample"),
  chooseVrmModel: () => ipcRenderer.invoke("dialog:chooseVrmModel"),
  chooseVrcAvatarSource: (mode = "file") => ipcRenderer.invoke("vrcImport:chooseSource", mode),
  preflightVrcAvatarSource: (sourcePath) => ipcRenderer.invoke("vrcImport:preflight", sourcePath),
  chooseWorkspaceRoot: (payload) => ipcRenderer.invoke("dialog:chooseWorkspaceRoot", payload || {}),
  getWorkspaceRoot: () => ipcRenderer.invoke("workspace:getRoot"),
  setWorkspaceRoot: (request) => ipcRenderer.invoke("workspace:setRoot", request),
  getServiceStatus: () => ipcRenderer.invoke("services:getStatus"),
  chooseStorageRoot: (payload) => ipcRenderer.invoke("dialog:chooseStorageRoot", payload || {}),
  chooseKnowledgeRoot: () => ipcRenderer.invoke("dialog:chooseKnowledgeRoot"),
  chooseKnowledgeFiles: (payload) => ipcRenderer.invoke("dialog:chooseKnowledgeFiles", payload || {}),
  chooseChatFiles: (payload) => ipcRenderer.invoke("dialog:chooseChatFiles", payload || {}),
  uploadChatFiles: (payload) => ipcRenderer.invoke("chatFiles:upload", payload || {}),
  openPath: (targetPath) => ipcRenderer.invoke("shell:openPath", targetPath),
  openArtifact: (payload) => ipcRenderer.invoke("artifact:open", payload || {}),
  openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
  saveTargetAs: (target, payload) => ipcRenderer.invoke("file:saveAs", { ...(payload || {}), target }),
  copyMedia: (payload) => ipcRenderer.invoke("file:copyMedia", payload || {}),
  readProjectAsset: (relativePath) => ipcRenderer.invoke("tiangong:read-asset", relativePath),
  writeClipboardText: (text) => ipcRenderer.invoke("clipboard:writeText", String(text || "")),
  verifyWebProject: (payload) => ipcRenderer.invoke("qa:verifyWebProject", payload || {}),
  listDailyLogs: () => ipcRenderer.invoke("logs:listDaily"),
  openDailyLog: (payload) => ipcRenderer.invoke("logs:openDaily", payload || {}),
  deleteDailyLog: (payload) => ipcRenderer.invoke("logs:deleteDaily", payload || {}),
  verifyLifeLog: () => ipcRenderer.invoke("lifeLog:verify"),
  createSoulBackup: (payload) => ipcRenderer.invoke("soulBackup:create", payload || {}),
  verifySoulBackup: (payload) => ipcRenderer.invoke("soulBackup:verify", payload || {}),
  restoreSoulBackup: (payload) => ipcRenderer.invoke("soulBackup:restore", payload || {}),
  // Auto updater
  checkUpdate: () => ipcRenderer.invoke("update:check"),
  getUpdateStatus: () => ipcRenderer.invoke("update:status"),
  downloadUpdate: () => ipcRenderer.invoke("update:download"),
  applyUpdate: () => ipcRenderer.invoke("update:apply"),
  onUpdateStatus: (callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on("update:status", handler);
    return () => ipcRenderer.removeListener("update:status", handler);
  },
  onUpdateProgress: (callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on("update:progress", handler);
    return () => ipcRenderer.removeListener("update:progress", handler);
  },
  window: {
    minimize: () => sendWindowAction("minimize"),
    maximize: () => sendWindowAction("maximize"),
    close: () => sendWindowAction("close"),
  },
  // P2b 受控资产层（方案 §8.4/§8.5）：分块流通道 + 候选读取 grant 签发。
  // 不暴露 ipcRenderer 与绝对路径；grant 载荷只含 opaque 元数据。
  avatarAsset: Object.freeze({
    openChannel: (descriptor) => openAvatarAssetChannel(descriptor),
    issueCandidateGrant: (payload) => ipcRenderer.invoke("avatar:issueCandidateGrant", payload || {}),
    getBuiltinManifest: () => ipcRenderer.invoke("avatar:getBuiltinManifest"),
  }),
  // P6a §8.5 导入主流程（窄接线）：文件选择与提交都在主进程完成；
  // renderer 只收发 opaque 字段（name/attemptId/candidateId/contentHash/byteLength/
  // assetId/modelId），绝不传绝对路径。
  avatarImport: Object.freeze({
    chooseFile: () => ipcRenderer.invoke("avatar:chooseImportFile"),
    commitCandidate: (payload) => ipcRenderer.invoke("avatar:commitCandidate", payload || {}),
    deleteModelFile: (payload) => ipcRenderer.invoke("avatar:deleteModelFile", payload || {}),
  }),
  // P2 §22.2/§22.3 持久化状态窄桥。key 由 renderer adapter 限定为三个枚举，
  // 主进程再次校验并固定映射到 userData；不暴露路径、目录枚举或 ipcRenderer。
  avatarStorage: Object.freeze({
    read: (key) => ipcRenderer.invoke("avatarStorage:read", requireAvatarStorageKey(key)),
    writeAtomic: (key, bytes) => {
      const checkedKey = requireAvatarStorageKey(key);
      return ipcRenderer.invoke(
        "avatarStorage:writeAtomic",
        checkedKey,
        requireAvatarStorageBytes(checkedKey, bytes),
      );
    },
  }),
});

// HOTFIX-20260728: 桌宠 iframe 内 preload 不运行、拿不到本桥，
// 由主框架代发资产读取：iframe postMessage 请求 → IPC 读文件 → postMessage 回传。
// 路径安全由主进程 tiangong:read-asset 的 projectAssetPath 统一把关。
window.addEventListener("message", async (event) => {
  const request = event && event.data;
  if (!request || request.type !== "tiangong:read-asset-request") return;
  const reply = { type: "tiangong:read-asset-response", requestId: request.requestId, ok: false, data: null, error: "" };
  try {
    reply.data = await ipcRenderer.invoke("tiangong:read-asset", String(request.relPath || ""));
    reply.ok = true;
  } catch (error) {
    reply.error = String((error && error.message) || error);
  }
  try {
    if (event.source) event.source.postMessage(reply, "*");
  } catch (_error) { /* 跨框架回传失败时静默 */ }
});
