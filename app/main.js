const { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeImage, nativeTheme, protocol, safeStorage, session, shell } = require("electron");
const fs = require("fs");
const crypto = require("crypto");
const dns = require("dns");
const http = require("http");
const https = require("https");
const net = require("net");
const path = require("path");
const { execFile, execFileSync, spawn } = require("child_process");
const { fileURLToPath } = require("url");
const { ServiceSupervisor } = require("./service-supervisor");
const { resolveWritableRuntimeRoot } = require("./runtime-root");
const { discoverVerifiedReleaseBindings } = require("./lib/release-binding");
const { SecureUpdater } = require("./secure-updater");
const { preflightVrcAvatarSource } = require("./vrc-import");
const { createCandidateGrantIssuer, installAvatarAssetProtocol, registerAvatarAssetScheme, chooseAvatarImportFile, commitCandidate, deleteModelFile } = require("./avatar-asset-host.cjs");
const { createAvatarStorageHost } = require("./avatar-storage-host.cjs");

// HOTFIX-20260728: VRM 等打包资产在 webSecurity+sandbox 下无法经 file:// fetch
// 读取，且 preload 不在桌宠 iframe 内运行（IPC 桥拿不到）。特权自定义协议
// tiangong-asset 供渲染进程直接 fetch，由主进程读文件；必须在 app ready 之前、
// 全进程仅一次注册特权。P2b 起 privileges 由 avatar-asset-host 按方案 §8.3 BOM
// 统一显式锁定（standard/secure/supportFetchAPI/corsEnabled/stream=true；
// bypassCSP 省略=false）。legacy <app>/assets 通道行为不变（并存到 P7）。
registerAvatarAssetScheme();

const SOURCE_MODE = app.isPackaged === false;
const SOURCE_PRODUCT_LABEL = `天工造物 v${String(app.getVersion() || "3.0")} 源码工作版`;
const PACKAGED_PRODUCT_LABEL = "天工造物 v3.0 完整版";
const PRODUCT_LABEL = SOURCE_MODE ? SOURCE_PRODUCT_LABEL : PACKAGED_PRODUCT_LABEL;

function ensureSourceIsolationDirectory(value, label) {
  const raw = String(value || "").trim();
  if (!raw || !path.isAbsolute(raw)) {
    throw new Error(`source isolation ${label} must be an absolute path`);
  }
  const resolved = path.resolve(raw);
  if (resolved === path.parse(resolved).root) {
    throw new Error(`source isolation ${label} may not be a volume root`);
  }
  fs.mkdirSync(resolved, { recursive: true });
  fs.accessSync(resolved, fs.constants.R_OK | fs.constants.W_OK);
  return resolved;
}

function configureSourceIsolation() {
  if (!SOURCE_MODE) return null;
  const localStateRoot = String(process.env.LOCALAPPDATA || "").trim()
    || path.join(app.getPath("home"), ".tiangong-v3-source-work");
  const profileRoot = ensureSourceIsolationDirectory(
    process.env.TIANGONG_SOURCE_PROFILE_ROOT
      || path.join(localStateRoot, "TiangongV3-SourceWork"),
    "profile root",
  );
  const userData = ensureSourceIsolationDirectory(
    process.env.TIANGONG_SOURCE_USER_DATA
      || path.join(profileRoot, "electron-user-data"),
    "user data",
  );
  const runtimeRoot = ensureSourceIsolationDirectory(path.join(profileRoot, "runtime"), "runtime");
  const stateRoot = ensureSourceIsolationDirectory(path.join(runtimeRoot, "state"), "state");
  const workspaceRoot = ensureSourceIsolationDirectory(path.join(profileRoot, "workspace"), "workspace");
  const homeRoot = ensureSourceIsolationDirectory(path.join(profileRoot, "home"), "home");
  const lifeDataRoot = ensureSourceIsolationDirectory(path.join(runtimeRoot, "life-data"), "life data");
  const lifeRuntimeRoot = ensureSourceIsolationDirectory(path.join(runtimeRoot, "complete-life"), "life runtime");
  const lifeKernelRoot = ensureSourceIsolationDirectory(path.join(stateRoot, "life_kernel"), "life kernel");
  const lifeTransactionRoot = ensureSourceIsolationDirectory(path.join(stateRoot, "life_transaction"), "life transaction");

  Object.assign(process.env, {
    TIANGONG_SOURCE_MODE: "1",
    TIANGONG_SOURCE_ROOT: path.resolve(__dirname, ".."),
    TIANGONG_SOURCE_PROFILE_ROOT: profileRoot,
    TIANGONG_SOURCE_USER_DATA: userData,
    TIANGONG_DESKTOP_RUNTIME_ROOT: runtimeRoot,
    TIANGONG_DESKTOP_STATE_DIR: stateRoot,
    TIANGONG_RUN_STATE_DIR: stateRoot,
    TIANGONG_V3_STATE_DIR: stateRoot,
    TIANGONG_DESKTOP_WORKSPACE_ROOT: workspaceRoot,
    TIANGONG_WORKSPACE_ROOT: workspaceRoot,
    TIANGONG_FORCE_WORKSPACE_ROOT: workspaceRoot,
    TIANGONG_OMNI_BODY_WORKSPACE: workspaceRoot,
    TIANGONG_HOME_PATH: homeRoot,
    TIANGONG_LIFE_DATA_ROOT: lifeDataRoot,
    TIANGONG_LIFE_RUNTIME_ROOT: lifeRuntimeRoot,
    TIANGONG_LIFE_KERNEL_ROOT: lifeKernelRoot,
    TIANGONG_LIFE_ROOT: lifeTransactionRoot,
    TIANGONG_EXECUTION_RUNTIME_ROOT: lifeKernelRoot,
    TIANGONG_EXECUTION_LIFE_ROOT: lifeTransactionRoot,
  });
  app.setName(SOURCE_PRODUCT_LABEL);
  app.setPath("userData", userData);
  return Object.freeze({ profileRoot, userData, runtimeRoot, workspaceRoot });
}

const SOURCE_ISOLATION = configureSourceIsolation();

// 工作区写入模式：workspace（默认，写边界=工作区）/ full（全盘，硬禁区除外）。
// 由后端保存在 workspace_settings.json，启动时注入子进程；切换模式后重启应用生效。
(function resolveWorkspaceMode() {
  try {
    const preferencePath = workspacePreferencePath();
    if (fs.existsSync(preferencePath)) {
      const raw = JSON.parse(fs.readFileSync(preferencePath, "utf-8"));
      const mode = String(raw?.workspace_mode || "").trim().toLowerCase();
      if (mode === "full" || mode === "workspace") {
        process.env.TIANGONG_WORKSPACE_MODE = mode;
        return;
      }
    }
    const settingsHome = process.env.TIANGONG_HOME_PATH || app.getPath("home");
    const settingsPath = path.join(settingsHome, ".tiangong", "v3", "workspace_settings.json");
    if (fs.existsSync(settingsPath)) {
      const raw = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
      const mode = String(raw?.workspace_mode || "").trim().toLowerCase();
      if (mode === "full" || mode === "workspace") {
        process.env.TIANGONG_WORKSPACE_MODE = mode;
      }
    }
  } catch (_error) {
    // 设置文件缺失/损坏：按默认工作区模式。
  }
  process.env.TIANGONG_WORKSPACE_MODE ||= "workspace";
})();

/*
 * RELEASE CONTRACT (keep this trace for future upgrades)
 * - One-command Windows release: npm run release:win
 * - Packaging authority: package.json + scripts/release-win.mjs + build/installer.nsh
 * - Stable identity: com.tiangong.v3.qiyuan / NSIS 8a691210-edfd-57d2-ac97-15799e433fcd / per-machine
 * - v3.0 complete execution policy: A0-A4 run continuously; A5 remains the sovereign hard gate.
 * - Long-chain UX: recoverable incomplete leases auto-continue with bounded time/run/stall fuses.
 * - Execution fusion stage 1: authenticated observe-only receipts live under runtime state;
 *   they never create organism identity or write lifecycle, memory, affect, or learning state.
 * Do not change identity or scope during a normal version bump. The release script validates them.
 */
const RELEASE_CONTRACT_VERSION = "tiangong.windows.release.v1";
const EXECUTION_POLICY_VERSION = "v3.0-complete:A0-A4-auto;A5-hard-gate";
const EXECUTION_SHADOW_AUDIT_VERSION = "tiangong.execution.shadow-receipt.v1:observe-only";
const WEB_QA_TARGET = String(process.env.TIANGONG_WEB_QA_TARGET || "").trim();
const WEB_QA_WORKSPACE = String(process.env.TIANGONG_WEB_QA_WORKSPACE || "").trim();
const WEB_QA_MODE = Boolean(WEB_QA_TARGET && WEB_QA_WORKSPACE);
// The desktop has exactly one application runtime: Total Gateway on 7184.
// 7174/7175/7176 are retired listener ports, never a selectable deployment
// mode.  Startup only clears a verified stale listener left by an old build.

const portableExecutableDir = SOURCE_MODE
  ? ""
  : String(process.env.PORTABLE_EXECUTABLE_DIR || "").trim();
if (portableExecutableDir) {
  const portableUserData = path.join(portableExecutableDir, "TiangongData");
  try {
    fs.mkdirSync(portableUserData, { recursive: true });
    fs.accessSync(portableUserData, fs.constants.R_OK | fs.constants.W_OK);
    app.setPath("userData", portableUserData);
    process.env.TIANGONG_PORTABLE_MODE = "1";
  } catch (_error) {
    // Read-only/removing portable media must not crash before the first
    // window.  The normal per-user data path remains the authority.
    delete process.env.TIANGONG_PORTABLE_MODE;
  }
}

// The application runtime (and legacy diagnostic child processes, when explicitly enabled)
// must call provider APIs directly. Proxy variables inherited from the host shell can
// break TLS handshakes and loopback connections.
for (const key of ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]) {
  delete process.env[key];
}
process.env.NO_PROXY = "*";
process.env.no_proxy = "*";

let mainWindow = null;
let backendProcess = null;
let backendStopping = false;
let backendStarting = false;
let backendHealthFailures = 0;
let lifeProcess = null;
let communicationProcess = null;
let totalGatewayProcess = null;
let totalGatewayStarting = false;
const adoptedTotalGatewayPids = new Set();
let serviceShutdownPromise = null;
let serviceShutdownComplete = false;
let workspaceCommittedRoot = "";
let workspaceChangeRevision = 0;
let workspaceChangePending = 0;
let workspaceChangeTail = Promise.resolve();
let modelSettingsChangeTail = Promise.resolve();
let resolvedRuntimeStateRoot = "";
let verifiedReleaseBindingsCache = null;
let restoreWindowBounds = null;
let secureUpdater = null;
let soulRestoreInProgress = false;
let avatarAssetHost = null; // P2b 受控资产宿主（app ready 后安装，见 avatar-asset-host.cjs）
let avatarStorageHost = null; // P2 userData 状态宿主；renderer 只持固定枚举 key
let maximizedByTitlebar = false;
const singleInstanceLock = app.requestSingleInstanceLock();

const DEFAULT_BACKEND_PORT = "7174";
const DESKTOP_API_TOKEN = process.env.TIANGONG_DESKTOP_TOKEN || crypto.randomBytes(48).toString("base64url");
process.env.TIANGONG_DESKTOP_TOKEN = DESKTOP_API_TOKEN;
// The life data root is bound to Electron's OS-known Documents folder after
// app.whenReady(). Redirected, OneDrive-backed and localized profiles do not
// necessarily use a literal "Documents" child under USERPROFILE.
// The renderer receives only DESKTOP_API_TOKEN.  Legacy services get separate
// per-launch credentials so renderer code cannot bypass 7184 by calling their
// loopback ports directly.
const BACKEND_INTERNAL_TOKEN = crypto.randomBytes(48).toString("base64url");
const LIFE_INTERNAL_TOKEN = crypto.randomBytes(48).toString("base64url");
const ARTIFACT_OPEN_TOKEN = process.env.TIANGONG_ARTIFACT_OPEN_TOKEN || crypto.randomBytes(48).toString("base64url");
process.env.TIANGONG_ARTIFACT_OPEN_TOKEN = ARTIFACT_OPEN_TOKEN;
// P2b 受控资产层：进程级 issuer epoch（grant 跨进程重启即失效，方案 §8.5）。
const AVATAR_ASSET_ISSUER_EPOCH = crypto.randomInt(1, 2 ** 31 - 1);
const SHADOW_API_TOKEN = crypto.randomBytes(48).toString("base64url");
const COMMUNICATION_GATEWAY_TOKEN = crypto.randomBytes(48).toString("base64url");
const LIFE_ACTION_INTENT_TOKEN = crypto.randomBytes(48).toString("base64url");
const LEGACY_COMMUNICATION_EXE_SHA256 = "613f569ee889b1f365b4678f02a2f2dc12507a52858a91d6b8a553880e2d11f6";
const BACKEND_URL = `http://127.0.0.1:${DEFAULT_BACKEND_PORT}`;
const DEFAULT_LIFE_PORT = "7175";
const LIFE_URL = `http://127.0.0.1:${DEFAULT_LIFE_PORT}`;
const DEFAULT_COMMUNICATION_PORT = "7176";
const COMMUNICATION_URL = `http://127.0.0.1:${DEFAULT_COMMUNICATION_PORT}`;
delete process.env.TIANGONG_LIFE_URL;
delete process.env.TIANGONG_COMMUNICATION_URL;
const DEFAULT_TOTAL_GATEWAY_PORT = "7184";
const TOTAL_GATEWAY_URL = `http://127.0.0.1:${DEFAULT_TOTAL_GATEWAY_PORT}`;
process.env.TIANGONG_TOTAL_GATEWAY_URL = TOTAL_GATEWAY_URL;
const BUILD_INFO = (() => {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "build-info.json"), "utf8"));
  } catch (_error) {
    return {};
  }
})();
const EXPECTED_BACKEND_BUILD_ID = BUILD_INFO.backend_build_id || "tiangong-backend-build-missing";
const BACKEND_API_CONTRACT = BUILD_INFO.api_contract_version || "tiangong.desktop.backend.v3";
const BACKEND_HEALTH_FAILURE_LIMIT = 3;
// First launch is where Windows Defender and installer-origin scans are most
// expensive.  Give each native service a full minute to become healthy before
// declaring it unavailable; subsequent watchdog probes remain fast.
const SERVICE_START_ATTEMPTS = 240;
const FRONTEND_DIR_NAME = ["frontend", "v" + "2"].join("-");
const PRIMARY_FRONTEND_FILE = path.join(app.getAppPath(), FRONTEND_DIR_NAME, "index.html");
const PRELOAD_FILE = path.join(app.getAppPath(), "preload.js");
const APP_ID = SOURCE_MODE
  ? "com.tiangong.v3.qiyuan.source"
  : "com.tiangong.v3.qiyuan";
const APP_ICON_FILE = path.join(app.getAppPath(), "assets", "tiangong-logo.ico");
const WINDOW_THEMES = {
  ink_teal: {
    source: "dark",
    background: "#0C0E11",
    titlebar: "#030507",
    symbol: "#AAB3BE",
  },
  bronze_gear: {
    source: "dark",
    background: "#0E0B08",
    titlebar: "#120E0A",
    symbol: "#CBB99D",
  },
  jade_light: {
    source: "light",
    background: "#F2EFE7",
    titlebar: "#F8F4EA",
    symbol: "#44514A",
  },
};

function normalizeThemeStyle(value) {
  return Object.prototype.hasOwnProperty.call(WINDOW_THEMES, value) ? value : "ink_teal";
}

function applyWindowTheme(themeStyle = "ink_teal") {
  const theme = WINDOW_THEMES[normalizeThemeStyle(themeStyle)];
  nativeTheme.themeSource = theme.source;
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setBackgroundColor(theme.background);
    if (typeof mainWindow.setTitleBarOverlay === "function") {
      try {
        mainWindow.setTitleBarOverlay({
          color: theme.titlebar,
          symbolColor: theme.symbol,
          height: 36,
        });
      } catch (_error) {
        // Older frameless-window combinations may ignore titleBarOverlay.
      }
    }
  }
}

function installEditContextMenu(contents) {
  if (!contents || contents.isDestroyed?.() || contents.__tiangongEditContextMenuInstalled) return;
  contents.__tiangongEditContextMenuInstalled = true;
  contents.on("context-menu", (_event, params = {}) => {
    const template = [];
    if (params.selectionText) {
      template.push({ role: "copy", label: "复制" });
    }
    if (params.isEditable) {
      if (template.length) template.push({ type: "separator" });
      template.push(
        { role: "cut", label: "剪切" },
        { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" },
        { role: "selectAll", label: "全选" },
      );
    }
    if (!template.length) return;
    Menu.buildFromTemplate(template).popup({ window: BrowserWindow.fromWebContents(contents) || mainWindow });
  });
}

function exists(p) {
  try {
    return !!p && fs.existsSync(p);
  } catch (_error) {
    return false;
  }
}

function writeDesktopDiagnostic(kind, detail = "") {
  try {
    let diagnosticRoot = resolvedRuntimeStateRoot;
    if (!diagnosticRoot) {
      try { diagnosticRoot = path.join(app.getPath("userData"), "runtime"); } catch (_error) {}
    }
    if (!diagnosticRoot) return;
    const dir = path.join(diagnosticRoot, "logs");
    fs.mkdirSync(dir, { recursive: true });
    // P2-23: rotate diagnostics by local date instead of appending to one
    // unbounded file, and prune files older than the retention window.
    const now = new Date();
    const stamp = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
    ].join("-");
    const fileName = `desktop_renderer.${stamp}.jsonl`;
    const retentionMs = 30 * 24 * 60 * 60 * 1000;
    const cutoff = now.getTime() - retentionMs;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isFile()) continue;
      const match = /^desktop_renderer\.(\d{4}-\d{2}-\d{2})\.jsonl$/.exec(entry.name);
      if (!match) continue;
      const stampMs = Date.parse(`${match[1]}T00:00:00Z`);
      if (Number.isFinite(stampMs) && stampMs < cutoff) {
        try { fs.unlinkSync(path.join(dir, entry.name)); } catch (_error) {}
      }
    }
    const row = JSON.stringify({ at: new Date().toISOString(), kind, detail: String(detail || "").slice(0, 4000) });
    fs.appendFileSync(path.join(dir, fileName), `${row}\n`, "utf8");
  } catch (_error) {}
}

function isFile(p) {
  try {
    return !!p && fs.statSync(p).isFile();
  } catch (_error) {
    return false;
  }
}

function isDirectory(p) {
  try {
    return !!p && fs.statSync(p).isDirectory();
  } catch (_error) {
    return false;
  }
}

function releaseManifestCandidatePaths() {
  const fromList = String(process.env.TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES || "")
    .split(path.delimiter)
    .map((item) => item.trim())
    .filter(Boolean);
  return Array.from(new Set([
    String(process.env.TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH || "").trim(),
    ...fromList,
    path.join(process.resourcesPath || "", "release", "release-manifest.json"),
    path.resolve(__dirname, "release", "release-manifest.json"),
  ].filter(Boolean).map((item) => path.resolve(item))));
}

function verifiedReleaseBindings() {
  if (verifiedReleaseBindingsCache) return verifiedReleaseBindingsCache;
  const discovered = discoverVerifiedReleaseBindings(releaseManifestCandidatePaths());
  // A desktop cannot hot-swap itself.  Select the newest verified native set
  // only among release authorities that exactly match this running desktop;
  // this prevents both old-env rollback and newer-native/older-desktop mixes.
  const productVersion = String(BUILD_INFO.product_version || app.getVersion() || "");
  const desktopBuildId = String(BUILD_INFO.build_id || "");
  let runningDesktopPath = "";
  if (app.isPackaged) {
    try {
      runningDesktopPath = fs.realpathSync.native(
        path.join(process.resourcesPath || "", "app.asar"),
      );
    } catch (error) {
      writeDesktopDiagnostic(
        "release-binding-running-desktop-unavailable",
        error?.message || error,
      );
    }
  }
  verifiedReleaseBindingsCache = Object.freeze(discovered.filter((binding) => (
    binding.productVersion === productVersion
    && binding.desktopBuildId === desktopBuildId
    && (
      !app.isPackaged
      || (
        runningDesktopPath
        && binding.desktopPath === runningDesktopPath
      )
    )
  )));
  if (discovered.length && !verifiedReleaseBindingsCache.length) {
    writeDesktopDiagnostic(
      "release-binding-desktop-mismatch",
      JSON.stringify(discovered.map((item) => ({
        productVersion: item.productVersion,
        desktopBuildId: item.desktopBuildId,
        manifestPath: item.manifestPath,
      }))),
    );
  }
  return verifiedReleaseBindingsCache;
}

function boundComponentExecutable(componentId) {
  return verifiedReleaseBindings()[0]?.componentPaths?.[componentId] || "";
}

function knownFolderPath(name, envName = "") {
  const candidates = [];
  if (envName && process.env[envName]) candidates.push(process.env[envName]);
  try {
    candidates.push(app.getPath(name));
  } catch (error) {
    writeDesktopDiagnostic("known_folder_unavailable", `${name}: ${error?.message || error}`);
  }
  try {
    candidates.push(app.getPath("userData"));
    candidates.push(app.getPath("home"));
  } catch (_error) {}
  candidates.push(process.env.TIANGONG_HOME_PATH || "");
  for (const candidate of candidates) {
    const raw = String(candidate || "").trim();
    if (!raw || !path.isAbsolute(raw)) continue;
    const resolved = path.resolve(raw);
    if (resolved !== path.parse(resolved).root) return resolved;
  }
  throw new Error(`known_folder_unresolved:${name}`);
}

function safeKnownFolder(name, envName = "") {
  let candidate = knownFolderPath(name, envName);
  while (!isDirectory(candidate)) {
    const parent = path.dirname(candidate);
    if (parent === candidate) throw new Error(`known_folder_parent_missing:${name}`);
    candidate = parent;
  }
  return candidate;
}

function bindRuntimeKnownFolders() {
  const bindings = [
    ["TIANGONG_DESKTOP_PATH", "desktop"],
    ["TIANGONG_DOWNLOADS_PATH", "downloads"],
    ["TIANGONG_DOCUMENTS_PATH", "documents"],
    ["TIANGONG_PICTURES_PATH", "pictures"],
    ["TIANGONG_VIDEOS_PATH", "videos"],
    ["TIANGONG_MUSIC_PATH", "music"],
  ];
  for (const [envName, folderName] of bindings) {
    if (!String(process.env[envName] || "").trim()) {
      process.env[envName] = knownFolderPath(folderName);
    }
  }
  if (!String(process.env.TIANGONG_LIFE_DATA_ROOT || "").trim()) {
    process.env.TIANGONG_LIFE_DATA_ROOT = path.join(
      process.env.TIANGONG_DOCUMENTS_PATH,
      "天工造物生命数据",
    );
  }
}

function normalizeDialogDefaultPath(payload = {}) {
  const raw = payload && typeof payload === "object"
    ? (payload.knowledgeRoot || payload.workspace || payload.defaultPath || "")
    : "";
  if (raw && exists(raw)) return raw;
  if (payload?.purpose === "lifeIdentity") {
    const dataRoot = String(process.env.TIANGONG_LIFE_DATA_ROOT || "").trim();
    const livesRoot = dataRoot ? path.join(dataRoot, "lives") : "";
    if (isDirectory(livesRoot)) return livesRoot;
    if (isDirectory(dataRoot)) return dataRoot;
    const defaultDataRoot = path.join(safeKnownFolder("documents", "TIANGONG_DOCUMENTS_PATH"), "天工造物生命数据");
    const defaultLivesRoot = path.join(defaultDataRoot, "lives");
    if (isDirectory(defaultLivesRoot)) return defaultLivesRoot;
    if (isDirectory(defaultDataRoot)) return defaultDataRoot;
  }
  return safeKnownFolder("documents", "TIANGONG_DOCUMENTS_PATH");
}

function canonicalPathBoundary(value) {
  const raw = String(value || "");
  if (!raw || raw.includes("\0") || !path.isAbsolute(raw)) throw new Error("path_invalid");
  const resolved = path.resolve(raw);
  const missingSegments = [];
  let cursor = resolved;
  while (!exists(cursor)) {
    const parent = path.dirname(cursor);
    if (parent === cursor) throw new Error("path_parent_missing");
    missingSegments.unshift(path.basename(cursor));
    cursor = parent;
  }
  return path.join(fs.realpathSync.native(cursor), ...missingSegments);
}

function isPathWithin(rootPath, candidatePath) {
  try {
    const root = canonicalPathBoundary(rootPath);
    const candidate = canonicalPathBoundary(candidatePath);
    const relative = path.relative(root, candidate);
    return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
  } catch {
    return false;
  }
}

async function verifyLocalWebProject(payload = {}) {
  let workspace;
  let projectRoot;
  try {
    workspace = fs.realpathSync.native(path.resolve(String(payload.workspace || "")));
    projectRoot = fs.realpathSync.native(path.resolve(String(payload.projectRoot || "")));
  } catch {
    return { ok: false, error: "project_root_missing", issues: ["工作区或项目根不存在。"] };
  }
  if (!isPathWithin(workspace, projectRoot) || projectRoot === workspace) {
    return { ok: false, error: "project_root_outside_workspace", issues: ["活跃项目根必须是工作区内的独立子目录。"] };
  }
  const indexPath = path.join(projectRoot, "index.html");
  if (!exists(indexPath) || !fs.statSync(indexPath).isFile() || fs.statSync(indexPath).size === 0) {
    return { ok: false, error: "missing_index", issues: ["项目根缺少非空 index.html。"] };
  }
  if (!isPathWithin(projectRoot, fs.realpathSync.native(indexPath))) {
    return { ok: false, error: "unsafe_index", issues: ["index.html 不能通过联接指向项目外部。"] };
  }

  const consoleErrors = [];
  const loadErrors = [];
  let qaWindow = null;
  try {
    qaWindow = new BrowserWindow({
      show: false,
      width: 1280,
      height: 800,
      webPreferences: {
        preload: path.join(__dirname, "qa-web-preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        partition: `temporary:tiangong-web-qa-${Date.now()}`,
      },
    });
    qaWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    qaWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
    qaWindow.webContents.session.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*"] }, (_details, callback) => callback({ cancel: true }));
    qaWindow.webContents.on("console-message", (details) => {
      const rendered = String(details?.message || "");
      const severity = String(details?.level || "").toLowerCase();
      if (["warning", "error"].includes(severity) && !/Electron Security Warning/i.test(rendered)) {
        consoleErrors.push(rendered.slice(0, 500));
      }
    });
    qaWindow.webContents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
      if (isMainFrame) loadErrors.push(`${code}:${description}:${url}`.slice(0, 500));
    });

    await Promise.race([
      qaWindow.loadFile(indexPath),
      new Promise((_resolve, reject) => setTimeout(() => reject(new Error("web QA load timeout")), 12000)),
    ]);
    await new Promise((resolve) => setTimeout(resolve, 250));
    const snapshot = await qaWindow.webContents.executeJavaScript(
      "window.__tiangongCollectWebQa ? window.__tiangongCollectWebQa() : null",
      true
    );
    if (!snapshot) {
      return { ok: false, error: "qa_preload_unavailable", issues: ["Web 验收探针未能进入页面。"] };
    }

    const issues = [];
    if (snapshot.readyState !== "complete") issues.push(`页面未完成加载：${snapshot.readyState || "unknown"}`);
    if (Number(snapshot.bodyChars || 0) < 50) issues.push("页面可见内容过少，疑似空白页。");
    if (Array.isArray(snapshot.runtimeErrors) && snapshot.runtimeErrors.length) {
      issues.push(`页面运行错误：${snapshot.runtimeErrors.slice(0, 3).join(" | ")}`);
    }
    if (loadErrors.length) issues.push(`页面加载错误：${loadErrors.slice(0, 3).join(" | ")}`);
    if (consoleErrors.length) issues.push(`控制台错误：${consoleErrors.slice(0, 3).join(" | ")}`);
    if (Array.isArray(snapshot.unboundButtons) && snapshot.unboundButtons.length) {
      const labels = snapshot.unboundButtons.slice(0, 8).map((item) => item.label || item.id || "未命名按钮");
      issues.push(`可见按钮没有点击/提交契约：${labels.join("、")}`);
    }
    if (Array.isArray(snapshot.placeholderMatches) && snapshot.placeholderMatches.length) {
      issues.push(`页面残留异常占位文本：${snapshot.placeholderMatches.join("、")}`);
    }
    const missingAssets = [];
    for (const asset of Array.isArray(snapshot.localAssets) ? snapshot.localAssets : []) {
      const clean = decodeURIComponent(String(asset).split(/[?#]/)[0]).replace(/^[/\\]+/, "");
      const resolved = path.resolve(projectRoot, clean);
      if (!isPathWithin(projectRoot, resolved) || !exists(resolved)) missingAssets.push(asset);
    }
    if (missingAssets.length) issues.push(`本地资源缺失：${missingAssets.slice(0, 8).join("、")}`);

    return {
      ok: issues.length === 0,
      schema: "tiangong.web_product_acceptance.v1",
      projectRoot,
      issues,
      evidence: {
        title: snapshot.title,
        bodyChars: snapshot.bodyChars,
        visibleButtonCount: Array.isArray(snapshot.buttons) ? snapshot.buttons.length : 0,
        boundButtonCount: Array.isArray(snapshot.buttons) ? snapshot.buttons.filter((item) => item.bound).length : 0,
        localAssetCount: Array.isArray(snapshot.localAssets) ? snapshot.localAssets.length : 0,
      },
    };
  } catch (error) {
    return { ok: false, error: error?.message || String(error), issues: [`Web 验收执行失败：${error?.message || String(error)}`] };
  } finally {
    if (qaWindow && !qaWindow.isDestroyed()) qaWindow.destroy();
  }
}

function documentFileFilters() {
  return [
    { name: "Documents", extensions: ["txt", "md", "markdown", "csv", "json", "jsonl", "html", "htm", "xml", "yaml", "yml", "toml", "docx", "xlsx", "pptx", "pdf"] },
    { name: "Media", extensions: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "mp4", "webm", "mov", "mkv", "avi", "mp3", "wav", "ogg", "m4a", "flac"] },
    { name: "All Files", extensions: ["*"] },
  ];
}

function imageFileFilters() {
  return [
    { name: "Images", extensions: ["png", "jpg", "jpeg", "webp", "gif", "bmp", "ico"] },
  ];
}

function audioFileFilters() {
  return [
    { name: "Audio", extensions: ["wav", "mp3", "m4a", "ogg", "opus", "flac", "aac", "wma", "webm"] },
    { name: "All Files", extensions: ["*"] },
  ];
}

const CHAT_TEXT_EXTENSIONS = [
  "txt", "md", "markdown", "csv", "json", "jsonl", "html", "htm", "xml", "yaml", "yml", "toml",
  "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "css", "scss", "less", "vue", "svelte",
  "java", "c", "cc", "cpp", "h", "hpp", "cs", "go", "rs", "php", "rb", "swift", "kt", "kts",
  "sql", "ini", "conf", "cfg", "log", "bat", "cmd", "ps1", "sh"
];
const CHAT_DOCUMENT_EXTENSIONS = [...CHAT_TEXT_EXTENSIONS, "docx", "xlsx", "pptx", "pdf"];
const CHAT_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif"];
const CHAT_VIDEO_EXTENSIONS = ["mp4"];
const CHAT_AUDIO_EXTENSIONS = ["silk"];
const CHAT_ARCHIVE_EXTENSIONS = ["zip"];
const RENDERER_OPEN_FILE_EXTENSIONS = new Set([
  "txt", "md", "markdown", "csv", "json", "jsonl", "xml", "yaml", "yml", "toml",
  "docx", "xlsx", "pptx", "pdf",
  ...CHAT_IMAGE_EXTENSIONS, ...CHAT_VIDEO_EXTENSIONS, ...CHAT_AUDIO_EXTENSIONS,
]);
const CHAT_ATTACHMENT_MIME = Object.freeze({
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  pdf: "application/pdf",
  zip: "application/zip",
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
  mp4: "video/mp4", silk: "audio/silk",
  md: "text/markdown", markdown: "text/markdown", csv: "text/csv", json: "application/json",
});
const MAX_REMOTE_DOWNLOAD_BYTES = 256 * 1024 * 1024;
const MAX_AVATAR_BYTES = 4 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_BYTES = 512 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_TOTAL_BYTES = 512 * 1024 * 1024;
const MAX_CHAT_ATTACHMENTS = 20;

function chatFileFilters() {
  return [
    { name: "All Files", extensions: ["*"] },
  ];
}

function chatAttachmentMime(filename, declared = "") {
  const ext = path.extname(String(filename || "")).toLowerCase().replace(/^\./, "");
  if (CHAT_ATTACHMENT_MIME[ext]) return CHAT_ATTACHMENT_MIME[ext];
  if (CHAT_TEXT_EXTENSIONS.includes(ext)) return "text/plain";
  const value = String(declared || "").split(";", 1)[0].trim().toLowerCase();
  if (value && /^(text|application|image|audio|video)\/[a-z0-9!#$&^_.+-]+$/.test(value)) return value;
  // Attachment admission is intentionally format-agnostic.  Unknown file
  // types remain opaque bytes for the selected model/tool to interpret.
  return "application/octet-stream";
}

function dataUrlForFile(filePath) {
  const ext = path.extname(filePath).toLowerCase().replace(".", "");
  const mime = {
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    webp: "image/webp",
    gif: "image/gif",
    bmp: "image/bmp",
    ico: "image/x-icon",
  }[ext];
  if (!mime) throw new Error("avatar_file_type_invalid");
  const stat = fs.statSync(filePath);
  if (!stat.isFile() || stat.size <= 0 || stat.size > MAX_AVATAR_BYTES) {
    throw new Error("avatar_file_size_invalid");
  }
  return `data:${mime};base64,${fs.readFileSync(filePath).toString("base64")}`;
}

function safeDownloadName(value, fallback = "download.bin") {
  const clean = path.basename(String(value || "")).replace(/[<>:"/\\|?*\x00-\x1F]/g, "_").trim();
  return clean && clean !== "." && clean !== ".." ? clean.slice(0, 180) : fallback;
}

function localPathFromTarget(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (/^file:\/\//i.test(raw)) {
    try {
      return fileURLToPath(raw);
    } catch (_error) {
      return "";
    }
  }
  if (/^[a-zA-Z]:[\\/]/.test(raw) || /^\\\\/.test(raw)) return path.resolve(raw);
  return "";
}

function targetImageExtension(value) {
  const localPath = localPathFromTarget(value);
  const rawPath = localPath || String(value || "");
  try {
    const parsed = /^https?:\/\//i.test(rawPath) ? new URL(rawPath).pathname : rawPath;
    return path.extname(decodeURIComponent(parsed || "")).replace(/^\./, "").toLowerCase();
  } catch (_error) {
    return path.extname(rawPath).replace(/^\./, "").toLowerCase();
  }
}

function downloadNameFromTarget(target, payload = {}) {
  const explicit = safeDownloadName(payload.name || payload.fileName || payload.filename || "");
  if (explicit !== "download.bin") return explicit;
  const localPath = localPathFromTarget(target);
  if (localPath) return safeDownloadName(path.basename(localPath));
  try {
    const url = new URL(String(target || ""));
    return safeDownloadName(decodeURIComponent(path.basename(url.pathname || "")));
  } catch (_error) {
    return safeDownloadName(String(target || ""));
  }
}

function defaultDownloadPath(name) {
  return path.join(app.getPath("downloads"), safeDownloadName(name));
}

async function chooseDownloadDestination(name, payload = {}) {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "Save file",
    defaultPath: payload.defaultPath || defaultDownloadPath(name),
    buttonLabel: "Save",
  });
  if (result.canceled || !result.filePath) return "";
  return result.filePath;
}

function httpClientForUrl(url) {
  return /^https:/i.test(String(url || "")) ? https : http;
}

function isPrivateNetworkAddress(address) {
  const value = String(address || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (!value) return true;
  const mapped = value.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/)?.[1];
  if (mapped) return isPrivateNetworkAddress(mapped);
  if (net.isIP(value) === 4) {
    const [a, b] = value.split(".").map(Number);
    return a === 0
      || a === 10
      || a === 127
      || (a === 100 && b >= 64 && b <= 127)
      || (a === 169 && b === 254)
      || (a === 172 && b >= 16 && b <= 31)
      || (a === 192 && b === 168)
      || (a === 198 && (b === 18 || b === 19))
      || a >= 224;
  }
  if (net.isIP(value) === 6) {
    return value === "::"
      || value === "::1"
      || value.startsWith("fc")
      || value.startsWith("fd")
      || /^fe[89ab]/.test(value)
      || value.startsWith("ff");
  }
  return true;
}

async function assertSafeRemoteUrl(rawUrl) {
  const url = new URL(String(rawUrl || ""));
  if (!/^https?:$/i.test(url.protocol) || url.username || url.password) {
    throw new Error("unsafe_remote_url");
  }
  const hostname = String(url.hostname || "").toLowerCase().replace(/\.$/, "").replace(/^\[|\]$/g, "");
  if (!hostname || hostname === "localhost" || hostname.endsWith(".localhost") || hostname.endsWith(".local")) {
    throw new Error("unsafe_remote_host");
  }
  const literal = net.isIP(hostname)
    ? [{ address: hostname, family: net.isIP(hostname) }]
    : await dns.promises.lookup(hostname, { all: true, verbatim: true });
  if (!literal.length || literal.some((item) => isPrivateNetworkAddress(item.address))) {
    throw new Error("unsafe_remote_host");
  }
  return { url, addresses: literal };
}

async function downloadHttpToFile(rawUrl, destination, redirects = 0) {
  const { url, addresses } = await assertSafeRemoteUrl(rawUrl);
  try {
    return await new Promise((resolve, reject) => {
      const lookup = (_hostname, options, callback) => {
        if (options?.all) callback(null, addresses.map((item) => ({ address: item.address, family: item.family })));
        else callback(null, addresses[0].address, addresses[0].family);
      };
      const request = httpClientForUrl(url.href).get(url, { lookup }, (response) => {
      const status = Number(response.statusCode || 0);
      const location = response.headers.location;
      if ([301, 302, 303, 307, 308].includes(status) && location && redirects < 5) {
        response.resume();
        const nextUrl = new URL(location, url).href;
        downloadHttpToFile(nextUrl, destination, redirects + 1).then(resolve, reject);
        return;
      }
      if (status < 200 || status >= 300) {
        response.resume();
        reject(new Error(`download_failed:${status}`));
        return;
      }
      const declaredBytes = Number(response.headers["content-length"] || 0);
      if (Number.isFinite(declaredBytes) && declaredBytes > MAX_REMOTE_DOWNLOAD_BYTES) {
        response.resume();
        reject(new Error("download_too_large"));
        return;
      }
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      const out = fs.createWriteStream(destination);
      let bytes = 0;
      response.on("data", (chunk) => {
        bytes += chunk.length;
        if (bytes > MAX_REMOTE_DOWNLOAD_BYTES) {
          response.destroy(new Error("download_too_large"));
          out.destroy(new Error("download_too_large"));
        }
      });
      response.pipe(out);
      out.on("finish", () => {
        out.close(() => resolve({ bytes, statusCode: status }));
      });
      out.on("error", reject);
      response.on("error", reject);
      });
      request.setTimeout(120000, () => {
        request.destroy(new Error("download_timeout"));
      });
      request.on("error", reject);
    });
  } catch (error) {
    try { if (isFile(destination)) fs.unlinkSync(destination); } catch {}
    throw error;
  }
}

async function saveTargetAs(payload = {}) {
  const target = String(payload.target || payload.path || payload.url || "").trim();
  if (!target) return { ok: false, error: "missing_target" };
  const name = downloadNameFromTarget(target, payload);
  const destination = await chooseDownloadDestination(name, payload);
  if (!destination) return { ok: true, canceled: true, path: "" };

  const localSource = localPathFromTarget(target);
  try {
    if (localSource) {
      let canonicalSource;
      try { canonicalSource = canonicalExistingPath(localSource); } catch {
        return { ok: false, error: "source_not_found", source: localSource };
      }
      const sourceExtension = path.extname(canonicalSource).slice(1).toLowerCase();
      if (!isFile(canonicalSource) || !RENDERER_OPEN_FILE_EXTENSIONS.has(sourceExtension)) {
        return { ok: false, error: "source_type_not_allowed", source: canonicalSource };
      }
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      if (path.resolve(canonicalSource).toLowerCase() !== path.resolve(destination).toLowerCase()) {
        await fs.promises.copyFile(canonicalSource, destination);
      }
      const stat = fs.statSync(destination);
      return { ok: true, source: canonicalSource, path: destination, bytes: stat.size, mode: "copy" };
    }
    if (/^https?:\/\//i.test(target)) {
      const result = await downloadHttpToFile(target, destination);
      return { ok: true, source: target, path: destination, bytes: result.bytes, statusCode: result.statusCode, mode: "download" };
    }
    return { ok: false, error: "unsupported_target", source: target };
  } catch (error) {
    try {
      if (destination && exists(destination)) fs.unlinkSync(destination);
    } catch (_cleanupError) {
      // Partial cleanup is best-effort.
    }
    return { ok: false, error: error?.message || String(error), source: target, path: destination };
  }
}

function runtimeStateRoot() {
  if (resolvedRuntimeStateRoot) return resolvedRuntimeStateRoot;
  const optionalAppPath = (name) => {
    try { return app.getPath(name); } catch (_error) { return ""; }
  };
  const selected = resolveWritableRuntimeRoot({
    explicitRoot: process.env.TIANGONG_DESKTOP_RUNTIME_ROOT,
    userData: optionalAppPath("userData"),
    appData: optionalAppPath("appData"),
    tempRoot: optionalAppPath("temp"),
  });
  resolvedRuntimeStateRoot = selected.root;
  process.env.TIANGONG_DESKTOP_RUNTIME_ROOT = selected.root;
  if (selected.rejected.length) {
    writeDesktopDiagnostic(
      "runtime-root-fallback",
      JSON.stringify({ selected: selected.root, rejected: selected.rejected }),
    );
  }
  return resolvedRuntimeStateRoot;
}

const WORKSPACE_PREFERENCE_SCHEMA = "tiangong.desktop.workspace-preference.v1";

function workspacePreferencePath() {
  return path.join(runtimeStateRoot(), "workspace-preference.json");
}

function validateWorkspaceRoot(value) {
  const raw = String(value || "").trim();
  if (!raw || raw.includes("\0") || !path.isAbsolute(raw)) throw new Error("workspace_path_invalid");
  const resolved = path.resolve(raw);
  if (resolved === path.parse(resolved).root || !isDirectory(resolved)) {
    throw new Error("workspace_directory_invalid");
  }
  const canonical = fs.realpathSync.native(resolved);
  if (canonical === path.parse(canonical).root || !isDirectory(canonical)) {
    throw new Error("workspace_directory_invalid");
  }
  return canonical;
}

function readWorkspacePreference() {
  const filePath = workspacePreferencePath();
  if (!isFile(filePath)) return "";
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (
      !parsed
      || parsed.schema !== WORKSPACE_PREFERENCE_SCHEMA
      || typeof parsed.workspace !== "string"
    ) {
      throw new Error("workspace_preference_invalid");
    }
    return validateWorkspaceRoot(parsed.workspace);
  } catch (error) {
    // HOTFIX-20260728: 历史上出现过反斜杠未转义的损坏文件，会在启动时反复
    // 触发 workspace-preference-invalid 并卡死启动链。这里用正则抢救
    // workspace 字段，成功则以正确转义重写文件，一次性止损。
    try {
      const raw = fs.readFileSync(filePath, "utf8");
      const match = raw.match(/"workspace"\s*:\s*"([^"]*)"/);
      if (match && match[1]) {
        const salvaged = validateWorkspaceRoot(match[1].replace(/\\\\/g, "\\"));
        writeWorkspacePreference(salvaged);
        writeDesktopDiagnostic("workspace-preference-salvaged", salvaged);
        return salvaged;
      }
    } catch (_salvageError) {
      // 抢救失败则走原有诊断路径
    }
    writeDesktopDiagnostic("workspace-preference-invalid", error?.message || error);
    return "";
  }
}

function writeWorkspacePreference(workspace, workspaceMode = "") {
  const filePath = workspacePreferencePath();
  const directory = path.dirname(filePath);
  fs.mkdirSync(directory, { recursive: true });
  const payload = `${JSON.stringify({
    schema: WORKSPACE_PREFERENCE_SCHEMA,
    workspace: validateWorkspaceRoot(workspace),
    workspace_mode: workspaceMode === "full" ? "full" : "workspace",
  }, null, 2)}\n`;
  const temporary = path.join(
    directory,
    `${path.basename(filePath)}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`,
  );
  let descriptor = null;
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, payload, "utf8");
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = null;
    fs.renameSync(temporary, filePath);
    try {
      const directoryDescriptor = fs.openSync(directory, "r");
      try { fs.fsyncSync(directoryDescriptor); } finally { fs.closeSync(directoryDescriptor); }
    } catch {
      // Directory fsync is unavailable on some Windows filesystems; the file
      // itself has already been flushed and atomically renamed.
    }
  } finally {
    if (descriptor !== null) fs.closeSync(descriptor);
    try { if (isFile(temporary)) fs.unlinkSync(temporary); } catch {}
  }
}

function applyWorkspacePreference() {
  const configured = String(process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT || "").trim();
  const persisted = readWorkspacePreference();
  // Source mode installs a safe bootstrap workspace before Electron starts.
  // That bootstrap is only a default; a user-committed repository must survive
  // an application restart. Explicit non-bootstrap environments keep priority.
  const sourceBootstrap = (
    typeof SOURCE_MODE !== "undefined"
    && SOURCE_MODE
    && typeof SOURCE_ISOLATION !== "undefined"
    && SOURCE_ISOLATION
    && configured
    && sameWindowsPath(configured, SOURCE_ISOLATION.workspaceRoot)
  );
  if (sourceBootstrap && persisted) {
    process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_FORCE_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_OMNI_BODY_WORKSPACE = persisted;
    workspaceCommittedRoot = persisted;
    return;
  }
  if (configured) {
    try {
      const validated = validateWorkspaceRoot(configured);
      process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = validated;
      process.env.TIANGONG_WORKSPACE_ROOT = validated;
      process.env.TIANGONG_FORCE_WORKSPACE_ROOT = validated;
      process.env.TIANGONG_OMNI_BODY_WORKSPACE = validated;
      workspaceCommittedRoot = validated;
      return;
    } catch (error) {
      writeDesktopDiagnostic("workspace-environment-invalid", error?.message || error);
      delete process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT;
      delete process.env.TIANGONG_WORKSPACE_ROOT;
      delete process.env.TIANGONG_FORCE_WORKSPACE_ROOT;
      delete process.env.TIANGONG_OMNI_BODY_WORKSPACE;
      workspaceCommittedRoot = "";
    }
  }
  if (persisted) {
    process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_FORCE_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_OMNI_BODY_WORKSPACE = persisted;
    workspaceCommittedRoot = persisted;
  }
}

function committedWorkspaceRoot() {
  if (workspaceCommittedRoot) return workspaceCommittedRoot;
  const configured = String(process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT || "").trim();
  if (configured) {
    try {
      workspaceCommittedRoot = validateWorkspaceRoot(configured);
      process.env.TIANGONG_WORKSPACE_ROOT = workspaceCommittedRoot;
      process.env.TIANGONG_FORCE_WORKSPACE_ROOT = workspaceCommittedRoot;
      process.env.TIANGONG_OMNI_BODY_WORKSPACE = workspaceCommittedRoot;
      return workspaceCommittedRoot;
    } catch (error) {
      writeDesktopDiagnostic("workspace-environment-invalid", error?.message || error);
      delete process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT;
      delete process.env.TIANGONG_WORKSPACE_ROOT;
      delete process.env.TIANGONG_FORCE_WORKSPACE_ROOT;
      delete process.env.TIANGONG_OMNI_BODY_WORKSPACE;
    }
  }
  const persisted = readWorkspacePreference();
  if (persisted) {
    workspaceCommittedRoot = persisted;
    process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_FORCE_WORKSPACE_ROOT = persisted;
    process.env.TIANGONG_OMNI_BODY_WORKSPACE = persisted;
    return workspaceCommittedRoot;
  }
  // 默认工作区收窄：用户 Documents 下的专用目录，而不是应用运行时状态目录。
  // env 与 workspace-preference.json 的优先级不变（见上方分支）。
  const fallback = path.join(app.getPath("documents"), "天工工作区");
  fs.mkdirSync(fallback, { recursive: true });
  workspaceCommittedRoot = validateWorkspaceRoot(fallback);
  process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = workspaceCommittedRoot;
  process.env.TIANGONG_WORKSPACE_ROOT = workspaceCommittedRoot;
  process.env.TIANGONG_FORCE_WORKSPACE_ROOT = workspaceCommittedRoot;
  process.env.TIANGONG_OMNI_BODY_WORKSPACE = workspaceCommittedRoot;
  return workspaceCommittedRoot;
}

function workspaceRootStatus() {
  try {
    return {
      ok: true,
      workspace: committedWorkspaceRoot(),
      revision: workspaceChangeRevision,
      changing: workspaceChangePending > 0,
    };
  } catch (error) {
    return {
      ok: false,
      error: error?.message || "workspace_path_invalid",
      workspace: "",
      revision: workspaceChangeRevision,
      changing: workspaceChangePending > 0,
    };
  }
}

function canonicalExistingPath(value) {
  const raw = String(value || "").trim();
  if (!raw || raw.includes("\0") || !path.isAbsolute(raw) || /^\\\\[.?]\\/i.test(raw)) {
    throw new Error("path_invalid");
  }
  const resolved = path.resolve(raw);
  if (!exists(resolved)) throw new Error("path_not_found");
  return fs.realpathSync.native(resolved);
}

function canonicalPathWithin(rootPath, candidatePath) {
  const root = fs.realpathSync.native(path.resolve(rootPath));
  const candidate = fs.realpathSync.native(path.resolve(candidatePath));
  const relative = path.relative(root, candidate);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

async function openRendererPath(targetPath) {
  let target;
  try {
    target = canonicalExistingPath(targetPath);
  } catch (error) {
    return { ok: false, error: error?.message || "path_invalid", path: "" };
  }
  const stat = fs.statSync(target);
  if (stat.isDirectory()) {
    const allowedRoots = [committedWorkspaceRoot(), runtimeStateRoot()].filter(isDirectory);
    if (!allowedRoots.some((root) => canonicalPathWithin(root, target))) {
      return { ok: false, error: "path_outside_allowed_roots", path: target };
    }
  } else if (!stat.isFile() || !RENDERER_OPEN_FILE_EXTENSIONS.has(path.extname(target).slice(1).toLowerCase())) {
    return { ok: false, error: "path_type_not_allowed", path: target };
  }
  const error = await shell.openPath(target);
  return { ok: !error, error: error || "", path: target };
}

async function stopServicesForWorkspaceChange(reason) {
  stopBackendWatchdog();
  // A workspace is an execution concern owned by 7174 and 7184.  The life and
  // communication stores are workspace-independent and must remain alive so a
  // path change cannot tear down login sessions, credentials, or the life UI.
  await serviceSupervisor.stop("total-gateway", reason);
}

async function startServicesForWorkspaceChange() {
  await serviceSupervisor.start("total-gateway");
  const snapshot = serviceSupervisor.snapshot();
  const totalGatewayReady = snapshot["total-gateway"]?.ready === true;
  const backendReady = totalGatewayReady;
  const lifeReady = totalGatewayReady;
  const communicationReady = totalGatewayReady;
  if (mainWindow && !mainWindow.isDestroyed()) startBackendWatchdog();
  return { backendReady, lifeReady, totalGatewayReady, communicationReady, snapshot };
}

async function applyWorkspaceRootChange(workspace, expectedRevision, workspaceMode = "") {
  const previousWorkspace = committedWorkspaceRoot();
  const previousMode = process.env.TIANGONG_WORKSPACE_MODE || "workspace";
  const nextMode = workspaceMode === "full" ? "full" : "workspace";
  const modeChanged = nextMode !== previousMode;
  if (expectedRevision !== workspaceChangeRevision) {
    return {
      ok: false,
      error: "workspace_revision_conflict",
      workspace: previousWorkspace,
      workspace_mode: previousMode,
      revision: workspaceChangeRevision,
      changing: false,
    };
  }
  if (sameWindowsPath(previousWorkspace, workspace) && !modeChanged) {
    return {
      ok: true,
      workspace: previousWorkspace,
      workspace_mode: previousMode,
      revision: workspaceChangeRevision,
      restarted: false,
      changing: false,
    };
  }

  await stopServicesForWorkspaceChange("workspace-root-change");
  process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = workspace;
  // The embedded tool/grant path resolves FORCE/WORKSPACE before the desktop
  // aliases.  Keep every execution authority on the same committed root
  // before restarting 7184, otherwise the gateway issues a grant for the new
  // workspace while the tool executor validates it against the old one.
  process.env.TIANGONG_WORKSPACE_ROOT = workspace;
  process.env.TIANGONG_FORCE_WORKSPACE_ROOT = workspace;
  process.env.TIANGONG_OMNI_BODY_WORKSPACE = workspace;
  process.env.TIANGONG_WORKSPACE_MODE = nextMode;
  try {
    const services = await startServicesForWorkspaceChange();
    if (
      !services.backendReady
      || !services.totalGatewayReady
    ) {
      throw new Error("workspace_service_restart_failed");
    }
    writeWorkspacePreference(workspace, nextMode);
    workspaceCommittedRoot = workspace;
    workspaceChangeRevision += 1;
    writeDesktopDiagnostic(
      "workspace-root-changed",
      JSON.stringify({ workspace, workspace_mode: nextMode, mode_changed: modeChanged }),
    );
    return {
      ok: true,
      workspace,
      workspace_mode: nextMode,
      revision: workspaceChangeRevision,
      restarted: true,
      changing: false,
      services,
    };
  } catch (error) {
    await stopServicesForWorkspaceChange("workspace-root-rollback");
    process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = previousWorkspace;
    process.env.TIANGONG_WORKSPACE_ROOT = previousWorkspace;
    process.env.TIANGONG_FORCE_WORKSPACE_ROOT = previousWorkspace;
    process.env.TIANGONG_OMNI_BODY_WORKSPACE = previousWorkspace;
    process.env.TIANGONG_WORKSPACE_MODE = previousMode;
    const rollbackServices = await startServicesForWorkspaceChange();
    const rolledBack = sameWindowsPath(
      process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT || previousWorkspace,
      previousWorkspace,
    );
    writeDesktopDiagnostic(
      "workspace-root-change-failed",
      JSON.stringify({ error: error?.message || String(error), rolledBack, rollbackServices }),
    );
    return {
      ok: false,
      error: error?.message || "workspace_service_restart_failed",
      workspace: previousWorkspace,
      workspace_mode: previousMode,
      revision: workspaceChangeRevision,
      changing: false,
      rolledBack,
      rollbackServices,
    };
  }
}

async function setWorkspaceRoot(request) {
  const payload = request && typeof request === "object" && !Array.isArray(request)
    ? request
    : { workspace: request };
  let workspace;
  try {
    workspace = validateWorkspaceRoot(payload.workspace);
  } catch (error) {
    return { ...workspaceRootStatus(), ok: false, error: error?.message || "workspace_path_invalid" };
  }
  const rawMode = String(payload.workspace_mode || "").trim().toLowerCase();
  const workspaceMode = rawMode === "full" ? "full" : "workspace";
  const suppliedRevision = Number(payload.expectedRevision);
  // Capture the revision when the request enters the queue. Two overlapping
  // renderer requests therefore cannot both commit against the same workspace.
  const expectedRevision = Number.isInteger(suppliedRevision) && suppliedRevision >= 0
    ? suppliedRevision
    : workspaceChangeRevision;
  workspaceChangePending += 1;
  const operation = workspaceChangeTail.then(
    () => applyWorkspaceRootChange(workspace, expectedRevision, workspaceMode),
    () => applyWorkspaceRootChange(workspace, expectedRevision, workspaceMode),
  );
  workspaceChangeTail = operation.catch(() => {});
  try {
    return await operation;
  } finally {
    workspaceChangePending = Math.max(0, workspaceChangePending - 1);
  }
}

function desktopProviderCredentialsPath() {
  const stateRoot = String(process.env.TIANGONG_DESKTOP_STATE_DIR || "").trim()
    || path.join(runtimeStateRoot(), "state");
  return path.join(path.resolve(stateRoot), "desktop_provider_credentials.json");
}

function readDesktopCredentialEnvelope(filePath) {
  if (!isFile(filePath)) return { schema: "tiangong.desktop-provider-credentials.v1", providers: {} };
  const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("desktop_credential_store_invalid");
  const providers = parsed.providers && typeof parsed.providers === "object" && !Array.isArray(parsed.providers)
    ? { ...parsed.providers }
    : {};
  return { ...parsed, schema: "tiangong.desktop-provider-credentials.v1", providers };
}

function writeDesktopCredentialEnvelope(filePath, envelope) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.writeFileSync(tempPath, `${JSON.stringify(envelope, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tempPath, filePath);
    try { fs.chmodSync(filePath, 0o600); } catch (_error) {}
  } finally {
    try { if (isFile(tempPath)) fs.unlinkSync(tempPath); } catch (_error) {}
  }
}

function normalizeProviderId(value) {
  const provider = String(value || "").trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9_.-]{0,95}$/.test(provider)) throw new Error("invalid_provider_id");
  return provider;
}

function providerApiKeyEnvName(provider) {
  return `TIANGONG_${String(provider || "").toUpperCase().replace(/-/g, "_")}_API_KEY`;
}

const OFFICIAL_MODEL_HOSTS = Object.freeze({
  deepseek_v4: new Set(["api.deepseek.com"]),
  deepseek: new Set(["api.deepseek.com"]),
  glm_5_2: new Set(["open.bigmodel.cn"]),
  glm_5_1: new Set(["open.bigmodel.cn"]),
  zhipu: new Set(["open.bigmodel.cn"]),
  gpt_5_6: new Set(["api.openai.com"]),
  openai: new Set(["api.openai.com"]),
  anthropic: new Set(["api.anthropic.com"]),
  minimax_m3: new Set(["api.minimaxi.com"]),
  minimax: new Set(["api.minimaxi.com"]),
  google: new Set(["generativelanguage.googleapis.com"]),
  mimo: new Set(["api.xiaomimimo.com"]),
});

function canonicalModelOrigin(baseUrl) {
  const parsed = new URL(String(baseUrl || "").trim());
  if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash) {
    throw new Error("model_endpoint_invalid");
  }
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  const defaultPort = (parsed.protocol === "https:" && (!parsed.port || parsed.port === "443"))
    || (parsed.protocol === "http:" && (!parsed.port || parsed.port === "80"));
  return `${parsed.protocol}//${host}${defaultPort ? "" : `:${parsed.port}`}`;
}

function modelCredentialBindingId(providerValue, baseUrl) {
  const provider = normalizeProviderId(providerValue || "openai");
  const origin = canonicalModelOrigin(baseUrl);
  const parsed = new URL(origin);
  if (parsed.protocol === "https:" && OFFICIAL_MODEL_HOSTS[provider]?.has(parsed.hostname)) return provider;
  return `endpoint_${crypto.createHash("sha256").update(origin, "utf8").digest("hex")}`;
}

async function secureModelSettingsUpdate(payload = {}) {
  const settings = { ...(payload || {}) };
  const apiKey = String(settings.modelApiKey || settings.api_key || "").trim();
  const clear = Boolean(settings.clear_api_key);
  const provider = String(settings.provider || settings.modelProvider || "openai").trim().toLowerCase();
  const baseUrl = String(settings.base_url || settings.modelBaseUrl || "").trim();
  delete settings.modelApiKey;
  delete settings.api_key;
  delete settings.clear_api_key;

  const changesCredential = Boolean(apiKey || clear);
  if (changesCredential) {
    if (!baseUrl) throw new Error("model_endpoint_required_for_credential_binding");
  }

  // Validate and persist the non-secret model contract first.  A malformed
  // endpoint/model must never mutate the OS credential vault or restart the
  // application runtime.  Preserve the previous contract so a failed runtime
  // restart can roll back both halves of the transaction.
  const previous = changesCredential ? await desktopModelSettingsRequest("GET") : null;
  const result = await desktopModelSettingsRequest("POST", settings);
  if (!result || typeof result !== "object" || result.ok === false) return result;

  if (changesCredential) {
    // The backend owns provider alias normalization (for example, `deepseek`
    // becomes `deepseek_v4`). Bind the OS-vault item to that authoritative
    // provider id so the environment variable injected into 7184 is exactly
    // the variable its model configuration reads.
    const authoritativeProvider = String(result.configured_provider || provider).trim().toLowerCase();
    const authoritativeBaseUrl = String(result.configured_base_url || baseUrl).trim();
    const credentialId = modelCredentialBindingId(authoritativeProvider, authoritativeBaseUrl);
    try {
      if (apiKey) await setProviderApiKey({ provider: credentialId, apiKey });
      else await deleteProviderApiKey({ provider: credentialId });
    } catch (error) {
      if (previous && previous.ok !== false) {
        const rollback = {
          provider: String(previous.configured_provider || ""),
          base_url: String(previous.configured_base_url || ""),
          model_name: String(previous.configured_model_name || ""),
        };
        try {
          const rollbackResult = await desktopModelSettingsRequest("POST", rollback);
          if (!rollbackResult || rollbackResult.ok === false) {
            writeDesktopDiagnostic("model-settings-rollback-failed", rollbackResult?.error || "rollback_rejected");
          }
        } catch (rollbackError) {
          writeDesktopDiagnostic("model-settings-rollback-failed", rollbackError?.message || rollbackError);
        }
      }
      throw error;
    }
    result.credential_state = apiKey ? "configured" : "missing";
    result.api_key = apiKey ? "configured" : "missing";
    result.credential_vault = "electron-safe-storage-v1";
    result.credential_binding = credentialId;
  }
  return result;
}

function queueSecureModelSettingsUpdate(payload = {}) {
  const immutablePayload = { ...(payload || {}) };
  const operation = modelSettingsChangeTail.then(() => secureModelSettingsUpdate(immutablePayload));
  modelSettingsChangeTail = operation.catch(() => undefined);
  return operation;
}

function applyProviderApiKey(provider, apiKey) {
  process.env[providerApiKeyEnvName(provider)] = String(apiKey || "");
}

function hydrateProviderApiKeys() {
  const filePath = desktopProviderCredentialsPath();
  if (!isFile(filePath)) return { ok: true, count: 0 };
  if (!safeStorage.isEncryptionAvailable()) throw new Error("os_credential_encryption_unavailable");
  const envelope = readDesktopCredentialEnvelope(filePath);
  let count = 0;
  for (const [rawProvider, item] of Object.entries(envelope.providers)) {
    const provider = normalizeProviderId(rawProvider);
    if (!item || item.scheme !== "electron-safe-storage-v1" || !item.value) continue;
    const apiKey = safeStorage.decryptString(Buffer.from(String(item.value), "base64"));
    if (!apiKey) continue;
    applyProviderApiKey(provider, apiKey);
    count += 1;
  }
  return { ok: true, count };
}

function safeProviderErrorCode(value, fallback = "provider_request_failed") {
  const normalized = String(value || "").replace(/[^A-Za-z0-9_.:-]/g, "").slice(0, 120);
  return normalized || fallback;
}

function providerProbeEndpoint(baseUrl, suffix) {
  const target = new URL(String(baseUrl || "").trim());
  target.pathname = `${target.pathname.replace(/\/+$/, "")}/${String(suffix || "").replace(/^\/+/, "")}`;
  target.search = "";
  target.hash = "";
  return target;
}

function requestProviderProbe(url, { method = "GET", apiKey = "", payload = null } = {}) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const body = payload ? JSON.stringify(payload) : "";
    const transport = url.protocol === "http:" ? http : https;
    const request = transport.request(url, {
      method,
      headers: {
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
        ...(payload ? {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        } : {}),
      },
      timeout: 12_000,
    }, (response) => {
      let responseBody = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        const remaining = 256 * 1024 - responseBody.length;
        if (remaining > 0) responseBody += String(chunk).slice(0, remaining);
      });
      response.on("end", () => resolve({
        statusCode: response.statusCode || 0,
        body: responseBody,
        latencyMs: Date.now() - started,
      }));
    });
    request.on("timeout", () => request.destroy(Object.assign(new Error("request_timeout"), { code: "ETIMEDOUT" })));
    request.on("error", reject);
    if (payload) request.write(body);
    request.end();
  });
}

// The probe deliberately accepts no renderer-supplied URL or credential.
// Both come from the committed configuration and OS vault; only bounded,
// non-secret status is returned to the renderer.
async function probeProviderApiConnection() {
  let settings;
  try {
    settings = await desktopModelSettingsRequest("GET");
  } catch (_error) {
    return { ok: false, stage: "configuration", error_code: "model_configuration_unavailable" };
  }
  const provider = String(settings?.configured_provider || "").trim().toLowerCase();
  const baseUrl = String(settings?.configured_base_url || "").trim();
  const modelName = String(settings?.configured_model_name || "").trim();
  let origin;
  try {
    origin = canonicalModelOrigin(baseUrl);
  } catch (_error) {
    return { ok: false, stage: "configuration", provider, error_code: "model_endpoint_invalid" };
  }
  const originUrl = new URL(origin);
  const loopback = ["127.0.0.1", "localhost", "::1"].includes(originUrl.hostname);
  if (originUrl.protocol === "http:" && !loopback) {
    return { ok: false, stage: "configuration", provider, error_code: "plaintext_http_forbidden" };
  }
  // The settings response is served inside 7184.  It reads the effective
  // credential from its own process environment, so this guards the
  // Electron-vault -> child-process injection boundary before any provider
  // request is made.
  if (String(settings?.credential_state || "").toLowerCase() !== "configured") {
    return { ok: false, stage: "gateway_credential", provider, error_code: "gateway_credential_not_injected" };
  }
  const credentialId = modelCredentialBindingId(provider, baseUrl);
  try {
    const envelope = readDesktopCredentialEnvelope(desktopProviderCredentialsPath());
    const item = envelope.providers?.[credentialId];
    if (!item || item.scheme !== "electron-safe-storage-v1" || !item.value) {
      return { ok: false, stage: "credential_lookup", provider, error_code: "credential_missing" };
    }
    if (!safeStorage.isEncryptionAvailable()) {
      return { ok: false, stage: "credential_decrypt", provider, error_code: "safe_storage_unavailable" };
    }
    const apiKey = safeStorage.decryptString(Buffer.from(String(item.value), "base64"));
    if (!apiKey) return { ok: false, stage: "credential_decrypt", provider, error_code: "credential_empty" };
    let response;
    try {
      response = await requestProviderProbe(providerProbeEndpoint(baseUrl, "models"), { apiKey });
    } catch (error) {
      return {
        ok: false,
        stage: "provider_models",
        provider,
        error_code: error?.code === "ETIMEDOUT" ? "request_timeout" : "provider_transport_failed",
      };
    }
    let payload = {};
    try { payload = JSON.parse(response.body || "{}"); } catch (_error) { /* status remains authoritative */ }
    const modelIds = Array.isArray(payload?.data)
      ? payload.data.map((entry) => String(entry?.id || "")).filter(Boolean)
      : [];
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return {
        ok: true,
        stage: "provider_models",
        provider,
        model_name: modelName,
        http_status: response.statusCode,
        latency_ms: response.latencyMs,
        models_count: modelIds.length,
        configured_model_available: modelIds.length ? modelIds.includes(modelName) : null,
      };
    }
    if ([404, 405, 501].includes(response.statusCode) && modelName) {
      let chat;
      try {
        chat = await requestProviderProbe(providerProbeEndpoint(baseUrl, "chat/completions"), {
          method: "POST",
          apiKey,
          payload: {
            model: modelName,
            messages: [{ role: "user", content: "ping" }],
            max_tokens: 1,
            stream: false,
          },
        });
      } catch (error) {
        return {
          ok: false,
          stage: "provider_chat",
          provider,
          error_code: error?.code === "ETIMEDOUT" ? "request_timeout" : "provider_transport_failed",
        };
      }
      let chatPayload = {};
      try { chatPayload = JSON.parse(chat.body || "{}"); } catch (_error) { /* status remains authoritative */ }
      return {
        ok: chat.statusCode >= 200 && chat.statusCode < 300,
        stage: "provider_chat",
        provider,
        model_name: modelName,
        http_status: chat.statusCode,
        latency_ms: chat.latencyMs,
        error_code: safeProviderErrorCode(chatPayload?.error?.code || chatPayload?.code, null),
      };
    }
    return {
      ok: false,
      stage: "provider_models",
      provider,
      model_name: modelName,
      http_status: response.statusCode,
      latency_ms: response.latencyMs,
      error_code: safeProviderErrorCode(payload?.error?.code || payload?.code, null),
    };
  } catch (_error) {
    return {
      ok: false,
      stage: "credential_decrypt",
      provider,
      error_code: "credential_decrypt_failed",
    };
  }
}

function modelRuntimeServiceName() {
  return "total-gateway";
}

// Credential saves restart the 7184 gateway so it can inherit the new env
// credential. /ready collection takes 20-60s in source mode (HOTFIX-20260728),
// so the restart budget must match the ready probe's 90s window; the old 45s
// caused "保存中" rollback loops whenever a slow boot passed 45s.
const CREDENTIAL_RESTART_TIMEOUT_MS = 90000;

function withTimeout(promise, ms, message) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    Promise.resolve(promise).then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

async function restartBackendForCredentialChange() {
  stopBackendWatchdog();
  const serviceName = modelRuntimeServiceName();
  // In the default single-process topology the model client lives inside
  // 7184, so the total gateway must be restarted to inherit the updated
  // credential environment.
  await serviceSupervisor.stop(serviceName, "provider-credential-updated");
  const runtime = await serviceSupervisor.start(serviceName);
  if (mainWindow && !mainWindow.isDestroyed()) startBackendWatchdog();
  if (!runtime.ready) throw new Error("model_runtime_restart_after_credential_update_failed");
}

async function setProviderApiKey(payload = {}) {
  const provider = normalizeProviderId(payload.provider);
  const apiKey = String(payload.apiKey || payload.api_key || "").trim();
  if (!apiKey) throw new Error("api_key_required");
  if (apiKey.length > 65536) throw new Error("api_key_too_large");
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("os_credential_encryption_unavailable");
  }
  const encrypted = safeStorage.encryptString(apiKey);
  const filePath = desktopProviderCredentialsPath();
  const before = readDesktopCredentialEnvelope(filePath);
  const previousEnv = process.env[providerApiKeyEnvName(provider)];
  const envelope = JSON.parse(JSON.stringify(before));
  envelope.providers[provider] = {
    scheme: "electron-safe-storage-v1",
    value: encrypted.toString("base64"),
  };
  writeDesktopCredentialEnvelope(filePath, envelope);
  applyProviderApiKey(provider, apiKey);
  // The gateway inherits env vars at startup.  When the exact credential is
  // already bound to the running process (e.g. injected at launch), restarting
  // the whole backend is unnecessary and can leave the UI stuck in "保存中"
  // while the service comes back up.
  if (typeof previousEnv === "string" && previousEnv === apiKey) {
    return { ok: true, provider, credential_state: "configured", backend_restarted: false, env_already_bound: true };
  }
  try {
    await withTimeout(restartBackendForCredentialChange(), CREDENTIAL_RESTART_TIMEOUT_MS, "model_runtime_restart_timeout");
  } catch (error) {
    writeDesktopCredentialEnvelope(filePath, before);
    if (typeof previousEnv === "string") process.env[providerApiKeyEnvName(provider)] = previousEnv;
    else delete process.env[providerApiKeyEnvName(provider)];
    try { await withTimeout(restartBackendForCredentialChange(), CREDENTIAL_RESTART_TIMEOUT_MS, "model_runtime_restart_timeout"); } catch (rollbackError) {
      writeDesktopDiagnostic("model-credential-rollback-restart-failed", rollbackError?.message || rollbackError);
    }
    throw error;
  }
  return { ok: true, provider, credential_state: "configured", backend_restarted: true };
}

async function deleteProviderApiKey(payload = {}) {
  const provider = normalizeProviderId(payload.provider);
  const filePath = desktopProviderCredentialsPath();
  const before = readDesktopCredentialEnvelope(filePath);
  const previousEnv = process.env[providerApiKeyEnvName(provider)];
  const envelope = JSON.parse(JSON.stringify(before));
  if (Object.prototype.hasOwnProperty.call(envelope.providers, provider)) {
    delete envelope.providers[provider];
    writeDesktopCredentialEnvelope(filePath, envelope);
  }
  delete process.env[providerApiKeyEnvName(provider)];
  try {
    await withTimeout(restartBackendForCredentialChange(), CREDENTIAL_RESTART_TIMEOUT_MS, "model_runtime_restart_timeout");
  } catch (error) {
    writeDesktopCredentialEnvelope(filePath, before);
    if (typeof previousEnv === "string") process.env[providerApiKeyEnvName(provider)] = previousEnv;
    else delete process.env[providerApiKeyEnvName(provider)];
    try { await withTimeout(restartBackendForCredentialChange(), CREDENTIAL_RESTART_TIMEOUT_MS, "model_runtime_restart_timeout"); } catch (rollbackError) {
      writeDesktopDiagnostic("model-credential-rollback-restart-failed", rollbackError?.message || rollbackError);
    }
    throw error;
  }
  return { ok: true, provider, credential_state: "missing", backend_restarted: true };
}

function canonicalIdentityHash(value) {
  const ordered = {};
  for (const key of Object.keys(value || {}).sort()) ordered[key] = value[key];
  return crypto.createHash("sha256").update(JSON.stringify(ordered), "utf8").digest("hex");
}

function migrateLegacyLifeIdentity(stateDir) {
  const lifeRoot = path.join(stateDir, "life_kernel");
  const identityPath = path.join(lifeRoot, "identity.json");
  if (!isFile(identityPath)) return { ok: true, status: "identity_absent" };
  try {
    const identity = JSON.parse(fs.readFileSync(identityPath, "utf8"));
    const stored = String(identity?.identity_hash || "");
    const currentImmutable = {
      organism_id: identity?.organism_id,
      lineage_id: identity?.lineage_id,
      born_at: identity?.born_at,
    };
    const currentHash = canonicalIdentityHash(currentImmutable);
    if (stored === currentHash) return { ok: true, status: "identity_current_ready", organism_id: identity.organism_id };

    const legacyImmutable = {
      schema: identity?.schema,
      organism_id: identity?.organism_id,
      lineage_id: identity?.lineage_id,
      born_at: identity?.born_at,
      aliases: Array.isArray(identity?.aliases) ? identity.aliases : [],
    };
    const legacyHash = canonicalIdentityHash(legacyImmutable);
    if (!stored || stored !== legacyHash) {
      return {
        ok: false,
        status: "identity_unrecognized",
        error: "Persisted identity does not match the current immutable identity contract",
      };
    }

    const backupPath = path.join(lifeRoot, "identity.pre-current-migration.json");
    if (!isFile(backupPath)) fs.copyFileSync(identityPath, backupPath, fs.constants.COPYFILE_EXCL);
    const migrated = {
      ...identity,
      build_id_at_birth: String(identity.build_id_at_birth || identity.created_by_build || "legacy-identity"),
      identity_hash: currentHash,
      migrated_to_current_at: new Date().toISOString(),
      migration_source: "validated-legacy-identity-hash",
    };
    const tempPath = `${identityPath}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(tempPath, `${JSON.stringify(migrated, null, 2)}\n`, "utf8");
    fs.renameSync(tempPath, identityPath);
    return {
      ok: true,
      status: "identity_migrated_to_current",
      organism_id: migrated.organism_id,
      backupPath,
    };
  } catch (error) {
    return { ok: false, status: "identity_migration_failed", error: error?.message || String(error) };
  }
}

function runtimeLogDir() {
  return path.join(runtimeStateRoot(), "logs");
}

function isoDateFromStat(stat, name = "") {
  // P2-23: prefer the explicit date embedded in the rotated filename; the
  // file mtime is only a fallback for legacy non-rotated logs.
  const match = /(\d{4}-\d{2}-\d{2})\.(?:log|jsonl)$/i.exec(String(name || ""));
  if (match) return match[1];
  return new Date(stat.mtimeMs || Date.now()).toISOString().slice(0, 10);
}

function dailyLogFiles() {
  const dir = runtimeLogDir();
  if (!isDirectory(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => {
      const filePath = path.join(dir, entry.name);
      const stat = fs.statSync(filePath);
      return {
        name: entry.name,
        path: filePath,
        date: isoDateFromStat(stat, entry.name),
        sizeBytes: stat.size,
        mtimeMs: stat.mtimeMs,
      };
    })
    .filter((item) => /\.(?:log|jsonl)$/i.test(item.name));
}

function listDailyLogs() {
  try {
    const groups = new Map();
    for (const item of dailyLogFiles()) {
      const existing = groups.get(item.date) || { date: item.date, count: 0, sizeBytes: 0, path: item.path, latestMtimeMs: 0 };
      existing.count += 1;
      existing.sizeBytes += item.sizeBytes;
      if (item.mtimeMs >= existing.latestMtimeMs) {
        existing.latestMtimeMs = item.mtimeMs;
        existing.path = item.path;
      }
      groups.set(item.date, existing);
    }
    const logs = [...groups.values()].sort((a, b) => String(b.date).localeCompare(String(a.date)));
    return { ok: true, logs, logDir: runtimeLogDir() };
  } catch (error) {
    return { ok: false, error: error?.message || String(error), logs: [], logDir: runtimeLogDir() };
  }
}

function logFilesForDate(date) {
  const targetDate = String(date || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) return [];
  return dailyLogFiles().filter((item) => item.date === targetDate);
}

async function openDailyLog(date) {
  try {
    const files = logFilesForDate(date);
    if (!files.length) return { ok: false, error: "log_not_found", date };
    const target = files.length === 1 ? files[0].path : runtimeLogDir();
    const error = await shell.openPath(target);
    return { ok: !error, error: error || "", date, path: target };
  } catch (error) {
    return { ok: false, error: error?.message || String(error), date };
  }
}

function deleteDailyLog(date) {
  try {
    const files = logFilesForDate(date);
    if (!files.length) return { ok: false, error: "log_not_found", date, logs: listDailyLogs().logs };
    for (const item of files) {
      const resolved = path.resolve(item.path);
      if (!resolved.startsWith(path.resolve(runtimeLogDir()) + path.sep)) {
        throw new Error("unsafe_log_path");
      }
      fs.unlinkSync(resolved);
    }
    return { ...listDailyLogs(), deleted: files.length, date };
  } catch (error) {
    return { ok: false, error: error?.message || String(error), date, logs: listDailyLogs().logs };
  }
}

async function materializeMediaTarget(target) {
  const localSource = localPathFromTarget(target);
  if (localSource) return { path: localSource, temporary: false };
  if (!/^https?:\/\//i.test(String(target || ""))) return { path: "", temporary: false };
  const name = safeDownloadName(downloadNameFromTarget(target), `media-${Date.now()}.bin`);
  const destination = path.join(app.getPath("temp"), "tiangong-media-cache", `${Date.now()}-${name}`);
  await downloadHttpToFile(target, destination);
  return { path: destination, temporary: true };
}

async function copyMediaToClipboard(payload = {}) {
  const target = String(payload.target || payload.path || payload.url || "").trim();
  const copyAs = String(payload.copyAs || "").toLowerCase();
  if (!target) return { ok: false, error: "missing_target" };
  if (copyAs === "path" || copyAs === "text") {
    clipboard.writeText(target);
    return { ok: true, copiedAs: "path", target };
  }
  try {
    const materialized = await materializeMediaTarget(target);
    const ext = targetImageExtension(materialized.path || target);
    if (materialized.path && CHAT_IMAGE_EXTENSIONS.includes(ext) && ext !== "svg") {
      const image = nativeImage.createFromPath(materialized.path);
      if (!image.isEmpty()) {
        clipboard.writeImage(image);
        return { ok: true, copiedAs: "image", target, path: materialized.path };
      }
    }
    clipboard.writeText(target);
    return { ok: true, copiedAs: "path", target, warning: "media_clipboard_fell_back_to_path" };
  } catch (error) {
    return { ok: false, error: error?.message || String(error), target };
  }
}

function backendDir() {
  const boundExecutable = boundComponentExecutable("tiangong-backend");
  if (boundExecutable) return path.dirname(boundExecutable);
  const candidates = [
    path.join(process.resourcesPath || "", "app.asar.unpacked", "backend", "tiangong-backend"),
    path.join(process.resourcesPath || "", "backend", "tiangong-backend"),
    path.resolve(__dirname, "backend", "tiangong-backend"),
    process.env.TIANGONG_BACKEND_DIR,
    path.join(process.resourcesPath || "", "app.asar.unpacked", "backend"),
    path.join(process.resourcesPath || "", "backend"),
    path.resolve(__dirname, "backend"),
    path.resolve(__dirname, "..", "backend"),
    path.resolve(__dirname),
  ].filter(Boolean);

  for (const item of candidates) {
    if (isFile(path.join(item, "tiangong-backend.exe"))) return item;
    if (isFile(path.join(item, "tiangong-backend"))) return item;
    if (exists(path.join(item, "_qidong.py"))) return item;
    if (exists(path.join(item, "start_qiyuan.py"))) return item;
    if (exists(path.join(item, "v3", "zongdiaodu.py"))) return item;
    if (exists(path.join(item, "scripts", "run_m10_1_daemon.py"))) return item;
  }
  return null;
}

function isSourceBackendDir(dir) {
  return !!dir && exists(path.join(dir, "v3", "zongdiaodu.py")) && !isFile(path.join(dir, "tiangong-backend.exe"));
}

function frozenBackendExecutablePath(dir) {
  if (!dir) return "";
  const exeName = process.platform === "win32" ? "tiangong-backend.exe" : "tiangong-backend";
  const exePath = path.join(dir, exeName);
  return isFile(exePath) ? exePath : "";
}

function normalizeFsPath(value) {
  return String(value || "").replace(/\//g, "\\").toLowerCase();
}

function processExecutablePath(pid) {
  if (process.platform !== "win32" || !pid) return "";
  try {
    const output = execFileSync("wmic", ["process", "where", `ProcessId=${pid}`, "get", "ExecutablePath", "/value"], {
      encoding: "utf8",
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const match = output.match(/ExecutablePath=(.*)/i);
    return (match?.[1] || "").trim();
  } catch (_error) {
    return "";
  }
}

function isReplaceableBackendListener(pid, dir) {
  if (!pid || pid === backendProcess?.pid) return false;
  const imageName = processImageName(pid).toLowerCase();
  if (!imageName.includes("python") && !imageName.includes("tiangong-backend")) return false;
  const expectedExe = frozenBackendExecutablePath(dir);
  if (!expectedExe) return isSourceBackendDir(dir) && imageName.includes("tiangong-backend");
  const actualExe = processExecutablePath(pid);
  return !actualExe || normalizeFsPath(actualExe) !== normalizeFsPath(expectedExe);
}

function shouldReplaceExistingBackend(dir) {
  return backendListenerPids().some((pid) => isReplaceableBackendListener(pid, dir));
}

function backendEntry(dir) {
  const exeName = process.platform === "win32" ? "tiangong-backend.exe" : "tiangong-backend";
  const exePath = path.join(dir, exeName);
  if (isFile(exePath)) return { command: exePath, args: [], cwd: dir };
  if (exists(path.join(dir, "_qidong.py"))) return { command: pythonCommand(), args: ["_qidong.py"], cwd: dir };
  if (exists(path.join(dir, "start_qiyuan.py"))) return { command: pythonCommand(), args: ["start_qiyuan.py"], cwd: dir };
  if (exists(path.join(dir, "v3", "zongdiaodu.py"))) {
    // Source and frozen launches must share the same durable gateway entry.
    // ZongDiaoDu is the transaction scheduler, not a process/server launcher.
    return { command: pythonCommand(), args: ["-m", "v3.desktop_daemon"], cwd: dir };
  }
  return { command: pythonCommand(), args: ["scripts/run_m10_1_daemon.py"], cwd: dir };
}

const TRUSTED_LOCAL_FRAME_FILES = new Set([
  normalizeFsPath(PRIMARY_FRONTEND_FILE),
  normalizeFsPath(path.join(__dirname, "桌面宠物.html")),
]);

function isTrustedAppFrameUrl(rawUrl) {
  if (!rawUrl) return false;
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol === "about:") return parsed.href === "about:blank";
    if (parsed.protocol !== "file:") return false;
    return TRUSTED_LOCAL_FRAME_FILES.has(normalizeFsPath(fileURLToPath(parsed)));
  } catch (_error) {
    return false;
  }
}

function isTrustedAppUrl(rawUrl) {
  if (!rawUrl) return false;
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol === "file:") {
      return normalizeFsPath(fileURLToPath(parsed)) === normalizeFsPath(PRIMARY_FRONTEND_FILE);
    }
    if (parsed.protocol === "about:") return parsed.href === "about:blank";
    if (parsed.protocol === "devtools:") return !app.isPackaged;
    // The only data document is the process-owned startup/fatal page.  It has
    // no preload authority until the primary local frontend is loaded.
    if (parsed.protocol === "data:") {
      const current = String(mainWindow?.webContents?.getURL?.() || "");
      return !current || current.startsWith("data:");
    }
    return false;
  } catch (_error) {
    return false;
  }
}

function isTrustedRendererEvent(event) {
  if (!event || !mainWindow || mainWindow.isDestroyed()) return false;
  if (event.sender !== mainWindow.webContents) return false;
  const senderUrl = String(event.senderFrame?.url || event.sender?.getURL?.() || "");
  try {
    return normalizeFsPath(fileURLToPath(senderUrl)) === normalizeFsPath(PRIMARY_FRONTEND_FILE);
  } catch (_error) {
    return false;
  }
}

function handleTrusted(channel, handler) {
  ipcMain.handle(channel, async (event, ...args) => {
    if (!isTrustedRendererEvent(event)) throw new Error("untrusted_renderer_ipc");
    return handler(event, ...args);
  });
}

function onTrusted(channel, handler) {
  ipcMain.on(channel, (event, ...args) => {
    if (!isTrustedRendererEvent(event)) {
      event.returnValue = {};
      return;
    }
    return handler(event, ...args);
  });
}

// Trusted local frames (e.g. the 桌面宠物.html VRM iframe) share the main
// window's webContents but are not the primary frontend document, so the
// strict PRIMARY_FRONTEND_FILE check above would reject them.  Read-only
// asset IPC uses this frame-level trust gate instead.
function isTrustedLocalFrameEvent(event) {
  if (!event || !mainWindow || mainWindow.isDestroyed()) return false;
  if (event.sender !== mainWindow.webContents) return false;
  const senderUrl = String(event.senderFrame?.url || event.sender?.getURL?.() || "");
  try {
    return TRUSTED_LOCAL_FRAME_FILES.has(normalizeFsPath(fileURLToPath(senderUrl)));
  } catch (_error) {
    return false;
  }
}

// Resolve a renderer-supplied project asset path strictly inside
// <app>/assets.  Anything absolute, URL-schemed, or containing ".." is
// rejected before touching the filesystem.
function projectAssetPath(relativePath) {
  const raw = String(relativePath || "").trim().replace(/\\/g, "/");
  if (!raw || raw.startsWith("/") || /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return null;
  const parts = raw.split("/").filter((part) => part && part !== ".");
  if (parts.length < 2 || parts[0] !== "assets" || parts.some((part) => part === "..")) return null;
  const assetsRoot = path.resolve(app.getAppPath(), "assets");
  const resolved = path.resolve(assetsRoot, ...parts.slice(1));
  if (!resolved.startsWith(assetsRoot + path.sep)) return null;
  return resolved;
}

// HOTFIX-20260728 legacy 资产读取（行为与原 protocol.handle 内联体完全一致）：
// 仅放行 <app>/assets 内文件。P2b 起作为 installAvatarAssetProtocol 的
// legacyHandler 注入并存到 P7——单一 scheme+session 只允许一个 handler。
async function legacyProjectAssetResponse(request) {
  try {
    const parsed = new URL(request.url);
    const rel = decodeURIComponent(String(parsed.host || "") + String(parsed.pathname || ""));
    const target = projectAssetPath(rel);
    if (!target || !isFile(target)) return new Response("asset_not_found", { status: 404 });
    const data = await fs.promises.readFile(target);
    return new Response(data, {
      headers: {
        "content-type": "application/octet-stream",
        "cache-control": "no-cache",
        // file:// 页面 origin 为 null，跨源 fetch 必须带 CORS 头
        "access-control-allow-origin": "*",
        // standard scheme 跨源子资源加载需要 CORP，否则 ERR_BLOCKED_BY_RESPONSE
        "cross-origin-resource-policy": "cross-origin",
      },
    });
  } catch (error) {
    return new Response(String((error && error.message) || error), { status: 500 });
  }
}

// P2b builtin scope 的逻辑 modelId 映射（注入式）：assets/avatar/builtin-models.json
// 存在时读取 {"models": {"<modelId>": {"file": "<相对文件名>", "contentHash": "<hex64>"}}}；
// 缺失/损坏 → 空映射（builtin scope 全部拒绝，待后续阶段接入真实内置模型清单）。
function loadBuiltinAvatarModelMap() {
  try {
    const manifestPath = path.join(app.getAppPath(), "assets", "avatar", "builtin-models.json");
    if (!isFile(manifestPath)) return new Map();
    const doc = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    const map = new Map();
    // 清单两种形态归一：规范数组形 [{id, relativePath, contentHash, ...}]（当前
    // builtin-models.json）与对象映射形 {modelId: {file, contentHash}}。
    // relativePath 相对应用根（"assets/..."），builtinRoot 是 <app>/assets，需剥前缀。
    const entries = Array.isArray(doc && doc.models)
      ? doc.models.map((item) => [item && item.id, item && item.relativePath, item && item.contentHash])
      : Object.entries((doc && doc.models) || {}).map(([modelId, entry]) => [
          modelId,
          entry && entry.file,
          entry && entry.contentHash,
        ]);
    for (const [modelId, fileRaw, hashRaw] of entries) {
      if (typeof modelId !== "string" || !modelId || typeof fileRaw !== "string") continue;
      const file = fileRaw.replace(/^\.\/+/, "").replace(/^assets[\\/]+/, "");
      if (!file || path.isAbsolute(file) || file.split(/[\\/]+/).some((part) => part === "..")) continue;
      // 发布包允许按许可硬门排除无再分发权的 VRM。清单中的条目只有在
      // 当前 app 根下的文件真实存在时才进入 builtin scope，避免“有清单、
      // 无模型”的悬空映射把 direct 启动带入确定失败。
      const resolved = path.resolve(app.getAppPath(), "assets", file);
      const assetsRoot = path.resolve(app.getAppPath(), "assets");
      if (!resolved.startsWith(`${assetsRoot}${path.sep}`) || !isFile(resolved)) continue;
      map.set(modelId, {
        file,
        contentHash: typeof hashRaw === "string" ? hashRaw : null,
      });
    }
    return map;
  } catch (error) {
    writeDesktopDiagnostic("avatar-builtin-manifest-failed", error?.message || error);
    return new Map();
  }
}

function pythonCommand() {
  const candidates = [
    process.env.TIANGONG_BACKEND_PYTHON,
    path.join(app.getAppPath(), "runtime", "python312", "python.exe"),
    path.join(app.getAppPath(), ".venv", "Scripts", "python.exe"),
    path.join(process.resourcesPath || "", "python", "python.exe"),
    "python",
  ].filter(Boolean);

  for (const item of candidates) {
    if (item === "python" || exists(item)) return item;
  }
  return "python";
}

function healthCheck(timeoutMs = 2000) {
  return new Promise((resolve) => {
    const url = `${BACKEND_URL.replace(/\/$/, "")}/health`;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(Boolean(value));
    };
    const req = http.get(url, {
      timeout: timeoutMs,
      headers: { "X-Tiangong-Token": BACKEND_INTERNAL_TOKEN },
    }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        if (body.length < 64 * 1024) body += chunk;
      });
      res.on("end", () => {
        if (res.statusCode !== 200) {
          finish(false);
          return;
        }
        try {
          const payload = JSON.parse(body || "{}");
          finish(
            payload?.ok === true
            && payload?.bridge_ready === true
            // A compatible API contract is not enough after an overwrite
            // upgrade: an older daemon can expose the same contract while
            // carrying stale runtime/data bindings.  Accept only this desktop
            // package's exact build identity.
            && payload?.build_id === EXPECTED_BACKEND_BUILD_ID
            && payload?.api_contract_version === BACKEND_API_CONTRACT
          );
        } catch (_error) {
          finish(false);
        }
      });
    });
    req.on("timeout", () => {
      req.destroy();
      finish(false);
    });
    req.on("error", () => finish(false));
  });
}

function lifeServiceDir() {
  const boundExecutable = boundComponentExecutable("tiangong-life-service");
  if (boundExecutable) return path.dirname(path.dirname(boundExecutable));
  const candidates = [
    path.join(process.resourcesPath || "", "life-service"),
    path.resolve(__dirname, "life-service"),
    process.env.TIANGONG_LIFE_SERVICE_DIR,
    path.resolve(__dirname, "..", "life-fusion"),
  ].filter(Boolean);
  for (const item of candidates) {
    if (isFile(path.join(item, "tiangong-life-service.exe"))) return item;
    if (isFile(path.join(item, "life_server.py"))) return item;
  }
  return null;
}

function serviceListenerPids(rawUrl, fallbackPort) {
  if (process.platform !== "win32") return [];
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch (_error) {
    return [];
  }
  if (!["127.0.0.1", "localhost", "::1"].includes(parsed.hostname)) return [];
  const port = String(parsed.port || fallbackPort);
  try {
    const output = execFileSync("netstat", ["-ano", "-p", "tcp"], {
      encoding: "utf8",
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const pids = new Set();
    for (const line of output.split(/\r?\n/)) {
      const parts = line.trim().split(/\s+/);
      if (parts.length < 5 || parts[0] !== "TCP") continue;
      const localAddress = parts[1] || "";
      const state = (parts[3] || "").toUpperCase();
      const pid = Number(parts[4] || 0);
      if (state === "LISTENING" && pid > 0 && localAddress.endsWith(`:${port}`)) pids.add(pid);
    }
    return [...pids];
  } catch (_error) {
    return [];
  }
}

function lifeServiceListenerPids() {
  return serviceListenerPids(LIFE_URL, DEFAULT_LIFE_PORT);
}

function lifeServiceHealthCheck(timeoutMs = 2000) {
  return new Promise((resolve) => {
    const url = `${LIFE_URL.replace(/\/$/, "")}/health`;
    const req = http.get(url, { timeout: timeoutMs, headers: { "X-Tiangong-Token": LIFE_INTERNAL_TOKEN } }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { if (body.length < 64 * 1024) body += chunk; });
      res.on("end", () => {
        try {
          const payload = JSON.parse(body || "{}");
          resolve(res.statusCode === 200 && payload?.ok === true && payload?.api_contract === "tiangong.life.api.v2");
        } catch (_error) { resolve(false); }
      });
    });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
  });
}

async function startLifeService() {
  if (await lifeServiceHealthCheck(1000)) return true;
  const dir = lifeServiceDir();
  if (!dir) {
    console.warn("Tiangong complete life service was not found.");
    return false;
  }
  if (lifeServiceListenerPids().length) {
    stopLifeServiceSync("replace-stale-life-listener", { includeListeners: true });
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  const exe = path.join(dir, "tiangong-life-service.exe");
  const script = path.join(dir, "life_server.py");
  const patchedRuntimeDir = path.join(dir, "runtime314");
  const patchedRuntimeExe = path.join(patchedRuntimeDir, "tiangong-life-service-runtime.exe");
  const patchedRuntimeBootstrap = path.join(patchedRuntimeDir, "tiangong_life_bootstrap.py");
  const usePatchedRuntime = isFile(patchedRuntimeExe) && isFile(patchedRuntimeBootstrap);
  const runtimeRoot = process.env.TIANGONG_LIFE_RUNTIME_ROOT || path.join(runtimeStateRoot(), "complete-life");
  const logDir = path.join(runtimeRoot, "logs");
  fs.mkdirSync(logDir, { recursive: true });
  const command = usePatchedRuntime ? patchedRuntimeExe : (isFile(exe) ? exe : pythonCommand());
  const args = usePatchedRuntime ? [patchedRuntimeBootstrap] : (isFile(exe) ? [] : [script]);
  const isolatedHome = process.env.TIANGONG_HOME_PATH || app.getPath("home");
  const env = {
    ...process.env,
    HOME: isolatedHome,
    USERPROFILE: isolatedHome,
    TIANGONG_LIFE_HOST: "127.0.0.1",
    TIANGONG_LIFE_PORT: (() => { try { return new URL(LIFE_URL).port || DEFAULT_LIFE_PORT; } catch { return DEFAULT_LIFE_PORT; } })(),
    TIANGONG_LIFE_RUNTIME_ROOT: runtimeRoot,
    TIANGONG_LIFE_DATA_ROOT: process.env.TIANGONG_LIFE_DATA_ROOT || path.join(safeKnownFolder("documents", "TIANGONG_DOCUMENTS_PATH"), "天工造物生命数据"),
    // The V3.0 complete execution chain owns both projections. Complete-life reads
    // runtime state separately from independently verified facts and never writes back.
    TIANGONG_EXECUTION_RUNTIME_ROOT: process.env.TIANGONG_EXECUTION_RUNTIME_ROOT || process.env.TIANGONG_LIFE_KERNEL_ROOT || path.join(runtimeStateRoot(), "state", "life_kernel"),
    TIANGONG_EXECUTION_LIFE_ROOT: process.env.TIANGONG_EXECUTION_LIFE_ROOT || process.env.TIANGONG_LIFE_ROOT || path.join(runtimeStateRoot(), "state", "life_transaction"),
    TIANGONG_BACKEND_URL: BACKEND_URL,
    TIANGONG_DESKTOP_TOKEN: LIFE_INTERNAL_TOKEN,
    TIANGONG_GATEWAY_URL: TOTAL_GATEWAY_URL,
    TIANGONG_GATEWAY_LIFE_INTENT_TOKEN: LIFE_ACTION_INTENT_TOKEN,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
  };
  const p11Root = process.env.TIANGONG_LIFE_P11_ROOT || path.join(runtimeRoot, "p11");
  let p11ArtifactRoot = p11Root;
  const p11ActivePointer = path.join(p11Root, "active.json");
  if (isFile(p11ActivePointer)) {
    try {
      const active = JSON.parse(fs.readFileSync(p11ActivePointer, "utf8"));
      const releaseId = typeof active?.release_id === "string" ? active.release_id.trim() : "";
      const candidate = path.join(p11Root, "releases", releaseId);
      if (/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(releaseId)
          && isDirectory(candidate)
          && isFile(path.join(candidate, "bundle.json"))) {
        p11ArtifactRoot = candidate;
      }
    } catch {
      p11ArtifactRoot = p11Root;
    }
  }
  const p11FinalManifest = process.env.TIANGONG_LIFE_P11_FINAL_MANIFEST || path.join(p11ArtifactRoot, "cow_final.json");
  const p11Overlay = process.env.TIANGONG_LIFE_P11_OVERLAY || path.join(p11ArtifactRoot, "life-overlay.shadow.sqlite3");
  const p11Handoff = process.env.TIANGONG_LIFE_P11_HANDOFF || path.join(p11ArtifactRoot, "writer_handoff.json");
  const p11PublicKey = process.env.TIANGONG_LIFE_P11_PUBLIC_KEY || path.join(p11ArtifactRoot, "cutover_authority.pub");
  const p11TrustPath = path.join(p11Root, "trust.json");
  let p11TrustedPublicKeySha256 = "";
  if (isFile(p11TrustPath) && isFile(p11PublicKey)) {
    try {
      const trust = JSON.parse(fs.readFileSync(p11TrustPath, "utf8"));
      const claimed = typeof trust?.public_key_sha256 === "string"
        ? trust.public_key_sha256.trim()
        : "";
      const observed = crypto.createHash("sha256").update(fs.readFileSync(p11PublicKey)).digest("hex");
      if (trust?.schema === "tiangong.life.cutover-root-trust.v1"
          && /^[0-9a-f]{64}$/.test(claimed)
          && claimed === observed) {
        p11TrustedPublicKeySha256 = claimed;
      }
    } catch {
      p11TrustedPublicKeySha256 = "";
    }
  }
  const p11CutoverRequired = String(process.env.TIANGONG_LIFE_P11_CUTOVER_REQUIRED || "").trim() === "1"
    || isFile(p11ActivePointer)
    || isFile(p11Handoff);
  let p11Snapshot = String(process.env.TIANGONG_LIFE_P11_SNAPSHOT || "").trim();
  if (!p11Snapshot && isFile(path.join(p11ArtifactRoot, "bundle.json"))) {
    try {
      const bundle = JSON.parse(fs.readFileSync(path.join(p11ArtifactRoot, "bundle.json"), "utf8"));
      const relative = typeof bundle?.base_snapshot_relative_path === "string"
        ? bundle.base_snapshot_relative_path.trim()
        : "";
      const candidate = path.resolve(p11Root, relative);
      const escaped = path.relative(p11Root, candidate);
      if (relative && !escaped.startsWith("..") && !path.isAbsolute(escaped) && isDirectory(candidate)) {
        p11Snapshot = candidate;
      }
    } catch {
      p11Snapshot = "";
    }
  }
  if (!p11Snapshot && isFile(p11FinalManifest)) {
    try {
      const finalImport = JSON.parse(fs.readFileSync(p11FinalManifest, "utf8"));
      p11Snapshot = typeof finalImport?.source_snapshot === "string"
        ? finalImport.source_snapshot.trim()
        : "";
    } catch {
      p11Snapshot = "";
    }
  }
  const p11Ready = isDirectory(p11Snapshot)
    && Boolean(p11TrustedPublicKeySha256)
    && [p11FinalManifest, p11Overlay, p11Handoff, p11PublicKey].every(isFile);
  if (p11Ready) {
    Object.assign(env, {
      TIANGONG_LIFE_P11_SNAPSHOT: p11Snapshot,
      TIANGONG_LIFE_P11_FINAL_MANIFEST: p11FinalManifest,
      TIANGONG_LIFE_P11_OVERLAY: p11Overlay,
      TIANGONG_LIFE_P11_HANDOFF: p11Handoff,
      TIANGONG_LIFE_P11_PUBLIC_KEY: p11PublicKey,
      TIANGONG_LIFE_P11_CUTOVER_REQUIRED: "1",
      TIANGONG_LIFE_P11_TRUSTED_PUBLIC_KEY_SHA256: p11TrustedPublicKeySha256,
    });
  } else {
    // A partial/stale cutover can never suppress the verified compatibility
    // runtime or accidentally create a second writer.
    for (const key of [
      "TIANGONG_LIFE_P11_SNAPSHOT",
      "TIANGONG_LIFE_P11_FINAL_MANIFEST",
      "TIANGONG_LIFE_P11_OVERLAY",
      "TIANGONG_LIFE_P11_HANDOFF",
      "TIANGONG_LIFE_P11_PUBLIC_KEY",
      "TIANGONG_LIFE_P11_TRUSTED_PUBLIC_KEY_SHA256",
    ]) delete env[key];
    if (p11CutoverRequired) {
      env.TIANGONG_LIFE_P11_CUTOVER_REQUIRED = "1";
      if (isDirectory(p11Snapshot)) env.TIANGONG_LIFE_P11_SNAPSHOT = p11Snapshot;
    } else {
      delete env.TIANGONG_LIFE_P11_CUTOVER_REQUIRED;
    }
  }
  // 7175 may propose actions to 7184, but must never inherit a credential
  // accepted by the 7174 execution boundary.
  delete env.TIANGONG_BACKEND_EXECUTION_TOKEN;
  delete env.TIANGONG_BACKEND_INTERNAL_TOKEN;
  const port = env.TIANGONG_LIFE_PORT;
  const child = spawnLoggedProcess(command, args, {
    cwd: usePatchedRuntime ? patchedRuntimeDir : dir,
    env,
    outPath: path.join(logDir, `life_${port}.out.log`),
    errPath: path.join(logDir, `life_${port}.err.log`),
  });
  lifeProcess = child;
  child.on("exit", () => { if (lifeProcess === child) lifeProcess = null; });
  for (let i = 0; i < SERVICE_START_ATTEMPTS; i += 1) {
    if (await lifeServiceHealthCheck(1000)) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (lifeProcess?.pid) killProcessTreeSync(lifeProcess.pid);
  lifeProcess = null;
  return false;
}

function stopLifeServiceSync(reason = "app-exit", options = {}) {
  const pids = new Set();
  if (lifeProcess?.pid && !lifeProcess.killed) pids.add(lifeProcess.pid);
  if (options.includeListeners || reason === "app-exit" || reason === "window-all-closed" || reason === "before-quit") {
    for (const pid of lifeServiceListenerPids()) {
      if (processImageName(pid).toLowerCase().includes("tiangong-life-service")) pids.add(pid);
    }
  }
  for (const pid of pids) {
    console.log(`Stopping Tiangong complete life service (${reason}): ${pid}`);
    killProcessTreeSync(pid);
  }
  lifeProcess = null;
}

async function communicationServiceEntry() {
  const boundExecutable = boundComponentExecutable("tiangong-communication-service");
  if (boundExecutable) {
    return { command: boundExecutable, args: [], cwd: path.dirname(boundExecutable), pythonPath: "" };
  }
  const executableCandidates = [
    process.resourcesPath
      ? path.join(process.resourcesPath, "communication-service", "tiangong-communication-service.exe")
      : "",
    path.resolve(__dirname, "communication-service", "tiangong-communication-service.exe"),
    process.env.TIANGONG_COMMUNICATION_EXE,
  ].filter(Boolean);
  for (const executable of executableCandidates) {
    if (!isFile(executable)) continue;
    try {
      const digest = await sha256File(executable);
      if (digest.toLowerCase() === LEGACY_COMMUNICATION_EXE_SHA256) {
        writeDesktopDiagnostic("legacy-communication-executable-refused", executable);
        continue;
      }
      return { command: executable, args: [], cwd: path.dirname(executable), pythonPath: "" };
    } catch (error) {
      writeDesktopDiagnostic("communication-executable-hash-failed", error?.message || executable);
    }
  }
  const sourceCandidates = [
    process.env.TIANGONG_COMMUNICATION_SOURCE_ROOT,
    path.resolve(__dirname, "..", "src"),
  ].filter(Boolean);
  for (const sourceRoot of sourceCandidates) {
    const resolvedRoot = path.resolve(sourceRoot);
    if (isFile(path.join(resolvedRoot, "communication_service", "__main__.py"))) {
      return {
        command: pythonCommand(),
        args: ["-m", "communication_service"],
        cwd: path.dirname(resolvedRoot),
        pythonPath: resolvedRoot,
      };
    }
  }
  return null;
}

function communicationListenerPids() {
  return serviceListenerPids(COMMUNICATION_URL, DEFAULT_COMMUNICATION_PORT);
}

function communicationHealthCheck(timeoutMs = 2000) {
  return new Promise((resolve) => {
    const url = `${COMMUNICATION_URL.replace(/\/$/, "")}/health`;
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { if (body.length < 64 * 1024) body += chunk; });
      res.on("end", () => {
        try {
          const payload = JSON.parse(body || "{}");
          resolve(
            res.statusCode === 200
            && payload?.ok === true
            && payload?.api_contract === "tiangong.communication.api.v1"
            && payload?.component_id === "tiangong-communication-service"
            && payload?.authority === "transport_only"
            && payload?.delivery_ticket_required === true
            && payload?.legacy_business_dependencies_permitted === false
            && payload?.total_gateway_origin === TOTAL_GATEWAY_URL
            && payload?.shadow_effects_permitted === false
          );
        } catch (_error) { resolve(false); }
      });
    });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
  });
}

function communicationReadyCheck(timeoutMs = 2000) {
  return new Promise((resolve) => {
    const url = `${COMMUNICATION_URL.replace(/\/$/, "")}/ready`;
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { if (body.length < 64 * 1024) body += chunk; });
      res.on("end", () => {
        try {
          const payload = JSON.parse(body || "{}");
          resolve(
            res.statusCode === 200
            && payload?.status === "READY"
            && payload?.component_id === "tiangong-communication-service"
            && payload?.delivery_ticket_required === true
            && payload?.legacy_business_dependencies_permitted === false
            && payload?.shadow_effects_permitted === false
          );
        } catch (_error) { resolve(false); }
      });
    });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
  });
}

function communicationServiceEnvironment(entry, port) {
  // Construct an allowlisted child environment instead of copying Electron's
  // provider credentials, desktop token, backend/life addresses, or workspace.
  const env = {};
  for (const name of [
    "APPDATA",
    "COMSPEC",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SystemDrive",
    "SystemRoot",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERDOMAIN",
    "WINDIR",
  ]) {
    if (process.env[name]) env[name] = process.env[name];
  }
  Object.assign(env, {
    HOME: process.env.TIANGONG_HOME_PATH || app.getPath("home"),
    USERPROFILE: process.env.TIANGONG_HOME_PATH || app.getPath("home"),
    TIANGONG_COMMUNICATION_ENVIRONMENT: "production",
    TIANGONG_COMMUNICATION_HOST: "127.0.0.1",
    TIANGONG_COMMUNICATION_PORT: port,
    TIANGONG_COMMUNICATION_STATE_ROOT: path.join(runtimeStateRoot(), "communication"),
    TIANGONG_COMMUNICATION_TOTAL_GATEWAY_URL: TOTAL_GATEWAY_URL,
    TIANGONG_COMMUNICATION_SHADOW_TOKEN: SHADOW_API_TOKEN,
    TIANGONG_COMMUNICATION_GATEWAY_TOKEN: COMMUNICATION_GATEWAY_TOKEN,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
  });
  if (entry.pythonPath) {
    env.PYTHONPATH = entry.pythonPath;
  }
  return env;
}

async function startCommunicationService() {
  if (await communicationHealthCheck(1000)) return true;
  const entry = await communicationServiceEntry();
  if (!entry) {
    console.warn("Ticket-gated Tiangong communication service was not found.");
    return false;
  }
  if (communicationListenerPids().length) {
    stopCommunicationServiceSync("replace-stale-communication-listener", { includeListeners: true });
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  const port = (() => { try { return new URL(COMMUNICATION_URL).port || DEFAULT_COMMUNICATION_PORT; } catch { return DEFAULT_COMMUNICATION_PORT; } })();
  const logDir = path.join(runtimeStateRoot(), "logs");
  fs.mkdirSync(logDir, { recursive: true });
  const child = spawnLoggedProcess(entry.command, entry.args, {
    cwd: entry.cwd,
    env: communicationServiceEnvironment(entry, port),
    outPath: path.join(logDir, `communication_${port}.out.log`),
    errPath: path.join(logDir, `communication_${port}.err.log`),
  });
  communicationProcess = child;
  child.on("exit", () => { if (communicationProcess === child) communicationProcess = null; });
  for (let i = 0; i < SERVICE_START_ATTEMPTS; i += 1) {
    if (await communicationHealthCheck(1000)) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (communicationProcess?.pid) killProcessTreeSync(communicationProcess.pid);
  communicationProcess = null;
  return false;
}

function stopCommunicationServiceSync(reason = "app-exit", options = {}) {
  const pids = new Set();
  if (communicationProcess?.pid && !communicationProcess.killed) pids.add(communicationProcess.pid);
  if (options.includeListeners || reason === "app-exit" || reason === "window-all-closed" || reason === "before-quit") {
    for (const pid of communicationListenerPids()) {
      if (processImageName(pid).toLowerCase().includes("tiangong-communication-service")) pids.add(pid);
    }
  }
  for (const pid of pids) {
    console.log(`Stopping Tiangong communication service (${reason}): ${pid}`);
    killProcessTreeSync(pid);
  }
  communicationProcess = null;
}

function totalGatewayListenerPids() {
  return serviceListenerPids(TOTAL_GATEWAY_URL, DEFAULT_TOTAL_GATEWAY_PORT);
}

function totalGatewayRequest(relativePath, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const url = `${TOTAL_GATEWAY_URL}${String(relativePath || "").startsWith("/") ? relativePath : `/${relativePath}`}`;
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { if (body.length < 64 * 1024) body += chunk; });
      res.on("end", () => {
        try {
          resolve({ statusCode: Number(res.statusCode || 0), payload: JSON.parse(body || "{}") });
        } catch (_error) {
          resolve({ statusCode: Number(res.statusCode || 0), payload: null });
        }
      });
    });
    req.on("timeout", () => { req.destroy(); resolve({ statusCode: 0, payload: null }); });
    req.on("error", () => resolve({ statusCode: 0, payload: null }));
  });
}

function totalGatewayJsonRequest(method, relativePath, payload, timeoutMs = 125000) {
  return new Promise((resolve) => {
    const normalizedMethod = String(method || "GET").toUpperCase();
    const hasBody = normalizedMethod !== "GET" && normalizedMethod !== "HEAD";
    const body = hasBody ? Buffer.from(JSON.stringify(payload || {}), "utf8") : null;
    const headers = {
      Accept: "application/json",
      "X-Tiangong-Token": DESKTOP_API_TOKEN,
      "X-Tiangong-Artifact-Open-Token": ARTIFACT_OPEN_TOKEN,
    };
    if (body) {
      headers["Content-Type"] = "application/json; charset=utf-8";
      headers["Content-Length"] = String(body.length);
    }
    const request = http.request(`${TOTAL_GATEWAY_URL}${relativePath}`, {
      method: normalizedMethod,
      timeout: timeoutMs,
      headers,
    }, (response) => {
      const chunks = [];
      let total = 0;
      let oversized = false;
      response.on("data", (chunk) => {
        total += chunk.length;
        if (total > 1024 * 1024) {
          oversized = true;
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => {
        if (oversized) {
          resolve({ statusCode: 0, payload: null, error: "gateway_response_too_large" });
          return;
        }
        try {
          resolve({
            statusCode: Number(response.statusCode || 0),
            payload: JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"),
            error: "",
          });
        } catch (_error) {
          resolve({ statusCode: Number(response.statusCode || 0), payload: null, error: "gateway_response_invalid" });
        }
      });
    });
    request.on("timeout", () => {
      request.destroy();
      resolve({ statusCode: 0, payload: null, error: "gateway_request_timeout" });
    });
    request.on("error", (error) => resolve({ statusCode: 0, payload: null, error: error?.message || "gateway_unavailable" }));
    request.end(body || undefined);
  });
}

function canonicalAttachmentMetadata(payload) {
  const ordered = {
    content_sha256: String(payload.content_sha256 || ""),
    created_at_ms: Number(payload.created_at_ms || 0),
    filename: String(payload.filename || ""),
    mime: String(payload.mime || ""),
    session_id: String(payload.session_id || ""),
    size_bytes: Number(payload.size_bytes || 0),
  };
  return Buffer.from(JSON.stringify(ordered), "utf8").toString("base64url");
}

function hashFileSha256(filePath) {
  return new Promise((resolve, reject) => {
    const digest = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath, { highWaterMark: 256 * 1024 });
    stream.on("data", (chunk) => digest.update(chunk));
    stream.on("error", reject);
    stream.on("end", () => resolve(digest.digest("hex")));
  });
}

function gatewayUploadChatSource(source, metadata, timeoutMs = 125000) {
  return new Promise((resolve) => {
    const request = http.request(`${TOTAL_GATEWAY_URL}/api/v1/gateway/desktop/attachments`, {
      method: "POST",
      timeout: timeoutMs,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/octet-stream",
        "Content-Length": String(metadata.size_bytes),
        "X-Tiangong-Token": DESKTOP_API_TOKEN,
        "X-Tiangong-Attachment-Metadata": canonicalAttachmentMetadata(metadata),
      },
    }, (response) => {
      const chunks = [];
      let total = 0;
      response.on("data", (chunk) => {
        total += chunk.length;
        if (total <= 1024 * 1024) chunks.push(chunk);
      });
      response.on("end", () => {
        if (total > 1024 * 1024) {
          resolve({ ok: false, error: "gateway_response_too_large" });
          return;
        }
        let payload = null;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch (_error) {}
        if (Number(response.statusCode || 0) !== 200 || payload?.ok !== true || !payload?.attachment) {
          resolve({ ok: false, error: payload?.reason_code || "desktop_attachment_upload_failed" });
          return;
        }
        resolve({
          ok: true,
          attachment: {
            status: "uploaded",
            name: payload.attachment.filename,
            size: payload.attachment.size_bytes,
            object_id: payload.attachment.object_id,
            attachment: payload.attachment,
          },
        });
      });
    });
    request.on("timeout", () => { request.destroy(); resolve({ ok: false, error: "desktop_attachment_upload_timeout" }); });
    request.on("error", (error) => resolve({ ok: false, error: error?.message || "desktop_attachment_upload_unavailable" }));
    if (source.filePath) {
      const stream = fs.createReadStream(source.filePath, { highWaterMark: 256 * 1024 });
      stream.on("error", (error) => {
        request.destroy(error);
      });
      stream.pipe(request);
    } else {
      request.end(source.buffer);
    }
  });
}

async function loadChatAttachmentSource(item) {
  if (item?.path) {
    const filePath = path.resolve(String(item.path));
    const stat = await fs.promises.lstat(filePath);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > MAX_CHAT_ATTACHMENT_BYTES) {
      throw new Error("chat_attachment_source_invalid");
    }
    const sha256 = await hashFileSha256(filePath);
    const afterHash = await fs.promises.lstat(filePath);
    if (!afterHash.isFile() || afterHash.isSymbolicLink() || afterHash.size !== stat.size || afterHash.mtimeMs !== stat.mtimeMs) {
      throw new Error("chat_attachment_changed_during_hash");
    }
    return { filePath, size: stat.size, sha256, filename: path.basename(filePath), declaredMime: "" };
  }
  const dataUrl = String(item?.dataUrl || item?.data_url || "");
  const match = /^data:([^;,]{3,255});base64,([A-Za-z0-9+/=\r\n]+)$/.exec(dataUrl);
  if (!match) throw new Error("chat_attachment_data_url_invalid");
  const buffer = Buffer.from(match[2].replace(/[\r\n]/g, ""), "base64");
  if (buffer.length < 1 || buffer.length > MAX_CHAT_ATTACHMENT_BYTES) throw new Error("chat_attachment_size_invalid");
  if (Number(item?.size || buffer.length) !== buffer.length) throw new Error("chat_attachment_size_mismatch");
  return {
    buffer,
    size: buffer.length,
    sha256: crypto.createHash("sha256").update(buffer).digest("hex"),
    filename: path.basename(String(item?.name || "clipboard-file")),
    declaredMime: match[1],
  };
}

async function uploadChatFilesToGateway(payload = {}) {
  const sessionId = String(payload.session_id || payload.sessionId || payload.activeSessionId || "").trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$/.test(sessionId)) {
    return { ok: false, error: "chat_attachment_session_invalid", attachments: [], failed: [] };
  }
  const sources = [];
  for (const filePath of Array.isArray(payload.paths) ? payload.paths : []) sources.push({ path: filePath });
  for (const item of Array.isArray(payload.items) ? payload.items : []) sources.push(item || {});
  if (!sources.length) return { ok: true, canceled: true, attachments: [], failed: [] };
  if (sources.length > MAX_CHAT_ATTACHMENTS) return { ok: false, error: "chat_attachment_count_exceeded", attachments: [], failed: [] };
  const attachments = [];
  const failed = [];
  let total = 0;
  for (const source of sources) {
    try {
      const loaded = await loadChatAttachmentSource(source);
      total += loaded.size;
      if (total > MAX_CHAT_ATTACHMENT_TOTAL_BYTES) throw new Error("chat_attachment_total_size_exceeded");
      const mime = chatAttachmentMime(loaded.filename, loaded.declaredMime);
      const createdAt = Date.now();
      const result = await gatewayUploadChatSource(loaded, {
        content_sha256: loaded.sha256,
        created_at_ms: createdAt,
        filename: loaded.filename,
        mime,
        session_id: sessionId,
        size_bytes: loaded.size,
      });
      if (!result.ok) throw new Error(result.error || "desktop_attachment_upload_failed");
      attachments.push(result.attachment);
    } catch (error) {
      failed.push({ name: path.basename(String(source?.path || source?.name || "file")), error: error?.message || String(error) });
    }
  }
  return {
    ok: attachments.length > 0 && failed.length === 0,
    partial: attachments.length > 0 && failed.length > 0,
    attachments,
    imported: [],
    failed,
    error: attachments.length ? "" : (failed[0]?.error || "chat_attachment_upload_failed"),
  };
}


function updateTrustPath() {
  return path.join(app.getAppPath(), "update-trust.json");
}

function emitUpdaterEvent(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function recoveryRoot() {
  const root = path.join(runtimeStateRoot(), "recovery");
  fs.mkdirSync(root, { recursive: true });
  return root;
}

function storeRecoveryPassphrase(backupPath, passphrase) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("os_credential_encryption_unavailable");
  }
  const secretRoot = path.join(recoveryRoot(), "secrets");
  fs.mkdirSync(secretRoot, { recursive: true });
  const id = crypto.createHash("sha256").update(path.resolve(backupPath)).digest("hex");
  const target = path.join(secretRoot, `${id}.bin`);
  const encrypted = safeStorage.encryptString(String(passphrase));
  const temp = `${target}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temp, encrypted, { mode: 0o600 });
  fs.renameSync(temp, target);
  try { fs.chmodSync(target, 0o600); } catch (_error) {}
  return target;
}

async function createPreUpdateSoulBackup() {
  const passphrase = crypto.randomBytes(32).toString("base64url");
  const destination = path.join(recoveryRoot(), `pre-update-${Date.now()}.tgsoul`);
  const response = await totalGatewayJsonRequest("POST", "/api/v1/gateway/soul-backup/create", {
    destination,
    passphrase,
  }, 15 * 60 * 1000);
  if (response.statusCode !== 200 || response.payload?.ok !== true) {
    throw new Error(response.payload?.reason_code || response.error || "pre_update_soul_backup_failed");
  }
  const secretPath = storeRecoveryPassphrase(destination, passphrase);
  return {
    path: destination,
    sha256: String(response.payload.sha256 || ""),
    manifest_sha256: String(response.payload.manifest_sha256 || ""),
    recovery_secret_path: secretPath,
  };
}

function getSecureUpdater() {
  if (secureUpdater) return secureUpdater;
  secureUpdater = new SecureUpdater({
    app,
    userData: app.getPath("userData"),
    currentVersion: app.getVersion(),
    trustPath: updateTrustPath(),
    onStatus: (status) => emitUpdaterEvent("update:status", status),
    onProgress: (progress) => emitUpdaterEvent("update:progress", progress),
    soulBackup: createPreUpdateSoulBackup,
  });
  return secureUpdater;
}

function postUpdateToken() {
  const prefix = "--tiangong-post-update-token=";
  const item = process.argv.find((value) => String(value).startsWith(prefix));
  return item ? String(item).slice(prefix.length) : "";
}

function spawnAndCapture(command, args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { ...options, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    const stdout = [];
    const stderr = [];
    let total = 0;
    const collect = (target, chunk) => {
      total += chunk.length;
      if (total <= 2 * 1024 * 1024) target.push(chunk);
    };
    child.stdout.on("data", (chunk) => collect(stdout, chunk));
    child.stderr.on("data", (chunk) => collect(stderr, chunk));
    child.on("error", (error) => resolve({ ok: false, code: -1, error: error?.message || String(error) }));
    child.on("exit", (code) => resolve({
      ok: code === 0,
      code: Number(code ?? -1),
      stdout: Buffer.concat(stdout).toString("utf8"),
      stderr: Buffer.concat(stderr).toString("utf8"),
    }));
  });
}

async function restoreSoulBackupOffline(payload = {}) {
  if (soulRestoreInProgress) return { ok: false, error: "soul_restore_already_running" };
  const backupPath = canonicalExistingPath(payload.path);
  if (!fs.statSync(backupPath).isFile() || path.extname(backupPath).toLowerCase() !== ".tgsoul") {
    return { ok: false, error: "soul_backup_path_invalid" };
  }
  const passphrase = String(payload.passphrase || "");
  if (passphrase.length < 12 || passphrase.length > 4096) return { ok: false, error: "soul_backup_passphrase_invalid" };
  const verify = await totalGatewayJsonRequest("POST", "/api/v1/gateway/soul-backup/verify", {
    path: backupPath,
    passphrase,
  }, 15 * 60 * 1000);
  if (verify.statusCode !== 200 || verify.payload?.ok !== true) {
    return { ok: false, error: verify.payload?.reason_code || verify.error || "soul_backup_verify_failed" };
  }
  soulRestoreInProgress = true;
  stopBackendWatchdog();
  try {
    await serviceSupervisor.drainAll("soul-restore");
    const entry = totalGatewayEntry();
    if (!entry) throw new Error("total_gateway_restore_entry_missing");
    const env = totalGatewayEnvironment(entry);
    env.TIANGONG_SOUL_BACKUP_PASSPHRASE = passphrase;
    const result = await spawnAndCapture(entry.command, [
      ...entry.args,
      "--soul-backup-restore",
      backupPath,
      "--passphrase-env",
      "TIANGONG_SOUL_BACKUP_PASSPHRASE",
    ], { cwd: entry.cwd, env });
    delete env.TIANGONG_SOUL_BACKUP_PASSPHRASE;
    if (!result.ok) throw new Error(result.stderr.trim() || result.stdout.trim() || "soul_restore_failed");
    serviceShutdownComplete = true;
    app.relaunch();
    app.exit(0);
    return { ok: true, restarting: true };
  } catch (error) {
    soulRestoreInProgress = false;
    writeDesktopDiagnostic("soul-restore-failed", error?.message || error);
    try { await serviceSupervisor.startAll(); startBackendWatchdog(); } catch (_restartError) {}
    return { ok: false, error: error?.message || String(error) };
  }
}

function backendControlJsonRequest(method, relativePath, payload = null, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const normalizedMethod = String(method || "GET").toUpperCase();
    const hasBody = normalizedMethod === "POST";
    const body = hasBody ? Buffer.from(JSON.stringify(payload || {}), "utf8") : null;
    const headers = {
      Accept: "application/json",
      "X-Tiangong-Token": DESKTOP_API_TOKEN,
    };
    if (body) {
      headers["Content-Type"] = "application/json; charset=utf-8";
      headers["Content-Length"] = String(body.length);
    }
    const controlUrl = TOTAL_GATEWAY_URL;
    const request = http.request(`${controlUrl}${relativePath}`, {
      method: normalizedMethod,
      timeout: timeoutMs,
      headers,
    }, (response) => {
      const chunks = [];
      let total = 0;
      let oversized = false;
      response.on("data", (chunk) => {
        total += chunk.length;
        if (total > 1024 * 1024) {
          oversized = true;
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => {
        if (oversized) {
          resolve({ statusCode: 0, payload: null, error: "backend_response_too_large" });
          return;
        }
        try {
          resolve({
            statusCode: Number(response.statusCode || 0),
            payload: JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"),
            error: "",
          });
        } catch (_error) {
          resolve({ statusCode: Number(response.statusCode || 0), payload: null, error: "backend_response_invalid" });
        }
      });
    });
    request.on("timeout", () => {
      request.destroy();
      resolve({ statusCode: 0, payload: null, error: "backend_request_timeout" });
    });
    request.on("error", (error) => resolve({ statusCode: 0, payload: null, error: error?.message || "backend_unavailable" }));
    request.end(body || undefined);
  });
}

function normalizedModelSettingsPayload(payload = {}) {
  const source = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  const allowed = new Set(["provider", "base_url", "model_name"]);
  if (Object.keys(source).some((key) => !allowed.has(key))) throw new Error("model_settings_fields_invalid");
  const clean = {
    provider: String(source.provider || "").trim(),
    base_url: String(source.base_url || "").trim(),
    model_name: String(source.model_name || "").trim(),
  };
  if (clean.provider.length > 128 || clean.base_url.length > 4096 || clean.model_name.length > 512) {
    throw new Error("model_settings_value_too_large");
  }
  if (Object.values(clean).some((value) => value.includes("\0"))) throw new Error("model_settings_value_invalid");
  return clean;
}

async function desktopModelSettingsRequest(method, payload = null) {
  const normalizedMethod = String(method || "GET").toUpperCase();
  if (!new Set(["GET", "POST"]).has(normalizedMethod)) return { ok: false, error: "model_settings_method_invalid" };
  let body = null;
  try {
    body = normalizedMethod === "POST" ? normalizedModelSettingsPayload(payload) : null;
  } catch (error) {
    return { ok: false, error: error?.message || "model_settings_invalid" };
  }
  let result = await backendControlJsonRequest(normalizedMethod, "/api/v1/llm/settings", body);
  if (result.statusCode === 0) {
    await serviceSupervisor.start(modelRuntimeServiceName());
    result = await backendControlJsonRequest(normalizedMethod, "/api/v1/llm/settings", body);
  }
  if (result.statusCode < 200 || result.statusCode >= 300 || !result.payload || typeof result.payload !== "object") {
    return {
      ok: false,
      error: String(result.payload?.error || result.payload?.detail || result.error || `backend_http_${result.statusCode || 0}`),
    };
  }
  return result.payload;
}

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", () => resolve(hash.digest("hex")));
  });
}

function validatedArtifactOpenPayload(value) {
  const payload = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const exactKeys = [
    "artifact_schema",
    "gateway_request_id",
    "run_id",
    "generation",
    "artifact_revision_id",
    "manifest_sha256",
    "card_sha256",
    "content_sha256",
    "size_bytes",
  ];
  if (Object.keys(payload).sort().join("\n") !== [...exactKeys].sort().join("\n")) {
    throw new Error("artifact_open_payload_invalid");
  }
  const sha = /^[0-9a-f]{64}$/;
  if (
    payload.artifact_schema !== "tiangong.gateway.artifact-card.v1"
    || !/^req_[0-9a-f]{64}$/.test(payload.gateway_request_id)
    || !/^run_[0-9a-f]{64}$/.test(payload.run_id)
    || !Number.isInteger(payload.generation)
    || payload.generation < 0
    || !/^arv_[0-9a-f]{64}$/.test(payload.artifact_revision_id)
    || !sha.test(payload.manifest_sha256)
    || !sha.test(payload.card_sha256)
    || !sha.test(payload.content_sha256)
    || !Number.isInteger(payload.size_bytes)
    || payload.size_bytes < 1
    || payload.size_bytes > 2147483648
  ) {
    throw new Error("artifact_open_payload_invalid");
  }
  return Object.fromEntries(exactKeys.map((key) => [key, payload[key]]));
}

async function openVerifiedArtifact(payload) {
  try {
    const card = validatedArtifactOpenPayload(payload);
    const response = await totalGatewayJsonRequest("POST", "/api/v1/artifacts/open", {
      gateway_request_id: card.gateway_request_id,
      run_id: card.run_id,
      generation: card.generation,
      artifact_revision_id: card.artifact_revision_id,
      manifest_sha256: card.manifest_sha256,
      card_sha256: card.card_sha256,
    });
    if (response.statusCode !== 200 || response.payload?.schema !== "tiangong.gateway.artifact-open-result.v1") {
      return {
        ok: false,
        error: response.payload?.reason_code || response.error || `artifact_gateway_${response.statusCode || "unavailable"}`,
      };
    }
    const returned = response.payload.artifact;
    if (
      !returned
      || returned.artifact_schema !== card.artifact_schema
      || returned.gateway_request_id !== card.gateway_request_id
      || returned.run_id !== card.run_id
      || returned.generation !== card.generation
      || returned.artifact_revision_id !== card.artifact_revision_id
      || returned.manifest_sha256 !== card.manifest_sha256
      || returned.card_sha256 !== card.card_sha256
      || returned.content_sha256 !== card.content_sha256
      || returned.size_bytes !== card.size_bytes
    ) {
      return { ok: false, error: "artifact_gateway_binding_invalid" };
    }
    const cacheRoot = path.resolve(runtimeStateRoot(), "gateway", "artifact-open");
    const target = path.resolve(String(response.payload.path || ""));
    if (!path.isAbsolute(target) || !exists(cacheRoot) || !exists(target)) {
      return { ok: false, error: "artifact_cache_path_missing" };
    }
    const rootReal = fs.realpathSync.native(cacheRoot);
    const targetLstat = fs.lstatSync(target);
    if (!targetLstat.isFile() || targetLstat.isSymbolicLink()) {
      return { ok: false, error: "artifact_cache_path_unsafe" };
    }
    const targetReal = fs.realpathSync.native(target);
    const relative = path.relative(rootReal, targetReal);
    if (!relative || relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
      return { ok: false, error: "artifact_cache_path_unsafe" };
    }
    const stat = fs.statSync(targetReal);
    if (stat.size !== card.size_bytes || await sha256File(targetReal) !== card.content_sha256) {
      return { ok: false, error: "artifact_cache_readback_invalid" };
    }
    shell.showItemInFolder(targetReal);
    return {
      ok: true,
      error: "",
      artifact_revision_id: card.artifact_revision_id,
    };
  } catch (error) {
    return { ok: false, error: error?.message || String(error) };
  }
}

async function totalGatewayHealthCheck(timeoutMs = 2000) {
  const result = await totalGatewayRequest("/health", timeoutMs);
  const structurallyValid = result.statusCode === 200
    && result.payload?.component_id === "tiangong-total-gateway"
    && result.payload?.status === "ALIVE"
    && Number.isInteger(result.payload?.gateway_epoch)
    && result.payload.gateway_epoch >= 1;
  if (!structurallyValid) return false;
  try {
    const epochPath = path.join(runtimeStateRoot(), "gateway", "gateway.epoch.json");
    const epoch = JSON.parse(fs.readFileSync(epochPath, "utf8"));
    return epoch?.instance_id === result.payload.instance_id
      && epoch?.gateway_epoch === result.payload.gateway_epoch;
  } catch (_error) {
    return false;
  }
}

async function totalGatewayReadyCheck(timeoutMs = 2000) {
  const result = await totalGatewayRequest("/ready", timeoutMs);
  return result.statusCode === 200
    && result.payload?.component_id === "tiangong-total-gateway"
    && result.payload?.status === "READY";
}

function totalGatewayEntries() {
  const entries = [];
  const seen = new Set();
  const add = (entry) => {
    if (!entry?.command || !entry?.cwd) return;
    const key = `${path.resolve(entry.command).toLowerCase()}\0${JSON.stringify(entry.args || [])}`;
    if (seen.has(key)) return;
    seen.add(key);
    entries.push(entry);
  };

  // A packaged release must prefer the executable whose bytes were bound into
  // release-manifest.json.  The embedded Python mirror is a compatibility
  // fallback for machines where the frozen image is quarantined or cannot
  // start; it must not silently become the only launch path.
  const boundExecutable = boundComponentExecutable("tiangong-total-gateway");
  if (boundExecutable) {
    add({
      command: boundExecutable,
      args: [],
      cwd: path.dirname(boundExecutable),
      pythonPath: "",
      kind: "release-bound-executable",
    });
  }
  const executableCandidates = [
    path.join(process.resourcesPath || "", "total-gateway", "tiangong-total-gateway.exe"),
    path.resolve(__dirname, "total-gateway", "tiangong-total-gateway.exe"),
    process.env.TIANGONG_TOTAL_GATEWAY_EXE,
  ].filter(Boolean);
  for (const executable of executableCandidates) {
    if (isFile(executable)) {
      add({
        command: executable,
        args: [],
        cwd: path.dirname(executable),
        pythonPath: "",
        kind: "packaged-executable-fallback",
      });
    }
  }

  const embeddedSourceRoot = path.join(process.resourcesPath || "", "python", "Lib", "site-packages");
  if (isFile(path.join(embeddedSourceRoot, "total_gateway", "__main__.py"))) {
    add({
      command: pythonCommand(),
      args: ["-m", "total_gateway"],
      cwd: path.join(process.resourcesPath || "", "total-gateway"),
      pythonPath: "",
      kind: "embedded-python-fallback",
    });
  }
  const sourceCandidates = [
    process.env.TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT,
    path.resolve(__dirname, "..", "src"),
  ].filter(Boolean);
  for (const sourceRoot of sourceCandidates) {
    if (isFile(path.join(sourceRoot, "total_gateway", "__main__.py"))) {
      const sourceBootstrap = path.join(path.dirname(sourceRoot), "scripts", "source-total-gateway-entry.py");
      add({
        command: pythonCommand(),
        args: isFile(sourceBootstrap) ? [sourceBootstrap] : ["-m", "total_gateway"],
        cwd: path.dirname(sourceRoot),
        pythonPath: sourceRoot,
        kind: "development-source",
      });
    }
  }
  return entries;
}

function totalGatewayEntry() {
  return totalGatewayEntries()[0] || null;
}

function totalGatewayEnvironment(entry) {
  // Make the child environment from a fresh vault hydration immediately
  // before spawn.  Startup hydration is retained, but this closes the race
  // where an already-open desktop process saved a key before 7184 restarted.
  try {
    hydrateProviderApiKeys();
  } catch (error) {
    writeDesktopDiagnostic("provider-credentials-gateway-inject-failed", error?.message || error);
  }
  const env = { ...process.env };
  const rawReleaseCandidates = releaseManifestCandidatePaths();
  const boundReleases = verifiedReleaseBindings();
  const explicitSkillRoot = String(process.env.TIANGONG_GATEWAY_SKILL_ROOT || "").trim();
  for (const name of Object.keys(env)) {
    if (name.startsWith("TIANGONG_GATEWAY_")) delete env[name];
  }
  const releaseManifestPaths = boundReleases.length
    ? boundReleases.map((binding) => binding.manifestPath)
    : Array.from(new Set(
      rawReleaseCandidates
        .filter((candidate) => isFile(candidate))
        .map((candidate) => path.resolve(candidate)),
    ));
  const releaseManifestPath = releaseManifestPaths[0] || "";
  const resolvedBackendDir = backendDir();
  const embeddedBackendDir = path.join(entry.cwd, "backend", "tiangong-backend");
  const skillCandidates = [
    explicitSkillRoot,
    resolvedBackendDir ? path.join(resolvedBackendDir, "_internal", "omni_body_skill") : "",
    path.join(embeddedBackendDir, "_internal", "omni_body_skill"),
    path.resolve(__dirname, "backend", "tiangong-backend", "_internal", "omni_body_skill"),
  ].filter(Boolean);
  const skillRoot = skillCandidates.find((candidate) => isDirectory(candidate)) || "";
  const executionWorkspace = path.resolve(
    String(process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT || "").trim()
      || path.join(runtimeStateRoot(), "workspaces"),
  );
  const stateRoot = runtimeStateRoot();
  const stateDir = process.env.TIANGONG_DESKTOP_STATE_DIR || path.join(stateRoot, "state");
  const omniBodyRoot = resolvedBackendDir
    ? (isDirectory(path.join(resolvedBackendDir, "omni_body_skill"))
      ? path.join(resolvedBackendDir, "omni_body_skill")
      : path.join(resolvedBackendDir, "_internal", "omni_body_skill"))
    : "";
  const omniBodyStateRoot = path.join(stateDir, "omni_body");
  const executionShadowRoot = path.join(stateDir, "execution_shadow");
  const isolatedHome = process.env.TIANGONG_HOME_PATH || app.getPath("home");
  for (const directory of [executionWorkspace, stateDir, omniBodyStateRoot, executionShadowRoot]) {
    fs.mkdirSync(directory, { recursive: true });
  }
  Object.assign(env, {
    TIANGONG_GATEWAY_ENVIRONMENT: entry.pythonPath ? "development" : "production",
    TIANGONG_GATEWAY_DEPLOYMENT_MODE: "embedded",
    TIANGONG_GATEWAY_PORT: DEFAULT_TOTAL_GATEWAY_PORT,
    TIANGONG_GATEWAY_STATE_ROOT: path.join(runtimeStateRoot(), "gateway"),
    TIANGONG_GATEWAY_SHADOW_TOKEN: SHADOW_API_TOKEN,
    TIANGONG_GATEWAY_COMMUNICATION_TOKEN: COMMUNICATION_GATEWAY_TOKEN,
    TIANGONG_GATEWAY_LIFE_INTENT_TOKEN: LIFE_ACTION_INTENT_TOKEN,
    TIANGONG_GATEWAY_WORKSPACE_ROOT: executionWorkspace,
    TIANGONG_BACKEND_INTERNAL_TOKEN: BACKEND_INTERNAL_TOKEN,
    TIANGONG_BACKEND_DIR: resolvedBackendDir || (isDirectory(embeddedBackendDir) ? embeddedBackendDir : ""),
    TIANGONG_LIFE_INTERNAL_TOKEN: LIFE_INTERNAL_TOKEN,
    TIANGONG_ARTIFACT_OPEN_TOKEN: ARTIFACT_OPEN_TOKEN,
    HOME: isolatedHome,
    USERPROFILE: isolatedHome,
    HTTP_PROXY: "",
    HTTPS_PROXY: "",
    http_proxy: "",
    https_proxy: "",
    NO_PROXY: "*",
    no_proxy: "*",
    TIANGONG_GATEWAY_URL: TOTAL_GATEWAY_URL,
    TIANGONG_DESKTOP_STATE_DIR: stateDir,
    TIANGONG_RUN_STATE_DIR: stateDir,
    TIANGONG_DESKTOP_WORKSPACE_ROOT: executionWorkspace,
    TIANGONG_LIFE_DATA_ROOT: process.env.TIANGONG_LIFE_DATA_ROOT,
    TIANGONG_LIFE_RUNTIME_ROOT: process.env.TIANGONG_LIFE_RUNTIME_ROOT || path.join(stateRoot, "complete-life"),
    TIANGONG_LIFE_KERNEL_ROOT: process.env.TIANGONG_LIFE_KERNEL_ROOT || path.join(stateDir, "life_kernel"),
    TIANGONG_LIFE_ROOT: process.env.TIANGONG_LIFE_ROOT || path.join(stateDir, "life_transaction"),
    TIANGONG_BUILD_ID: EXPECTED_BACKEND_BUILD_ID,
    TIANGONG_BACKEND_API_CONTRACT: BACKEND_API_CONTRACT,
    TIANGONG_OMNI_BODY_ROOT: omniBodyRoot,
    TIANGONG_OMNI_BODY_WORKSPACE: executionWorkspace,
    TIANGONG_OMNI_BODY_STATE_ROOT: omniBodyStateRoot,
    TIANGONG_EXECUTION_SHADOW_MODE: "observe-only",
    TIANGONG_EXECUTION_SHADOW_ROOT: executionShadowRoot,
    TIANGONG_EXECUTION_SHADOW_AUDIT_VERSION: EXECUTION_SHADOW_AUDIT_VERSION,
    TIANGONG_RELEASE_CONTRACT_VERSION: RELEASE_CONTRACT_VERSION,
    TIANGONG_EXECUTION_POLICY_VERSION: EXECUTION_POLICY_VERSION,
    TIANGONG_OMNI_BODY_ALLOW_SHELL: process.env.TIANGONG_OMNI_BODY_ALLOW_SHELL || "1",
    TIANGONG_OMNI_BODY_ALLOW_ABSOLUTE_PATHS: process.env.TIANGONG_OMNI_BODY_ALLOW_ABSOLUTE_PATHS || "1",
    TIANGONG_HOME_PATH: isolatedHome,
    TIANGONG_DESKTOP_PATH: process.env.TIANGONG_DESKTOP_PATH || (portableExecutableDir ? executionWorkspace : app.getPath("desktop")),
    TIANGONG_DOWNLOADS_PATH: process.env.TIANGONG_DOWNLOADS_PATH || app.getPath("downloads"),
    TIANGONG_DOCUMENTS_PATH: process.env.TIANGONG_DOCUMENTS_PATH || app.getPath("documents"),
    TIANGONG_PICTURES_PATH: process.env.TIANGONG_PICTURES_PATH || app.getPath("pictures"),
    TIANGONG_VIDEOS_PATH: process.env.TIANGONG_VIDEOS_PATH || app.getPath("videos"),
    TIANGONG_MUSIC_PATH: process.env.TIANGONG_MUSIC_PATH || app.getPath("music"),
    TIANGONG_VRM_FRONTEND_DIR: app.getAppPath(),
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
  });
  if (releaseManifestPath) env.TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH = path.resolve(releaseManifestPath);
  if (releaseManifestPaths.length) {
    env.TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES = releaseManifestPaths.join(path.delimiter);
  }
  if (entry.pythonPath) env.TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT = path.resolve(entry.cwd);
  if (entry.pythonPath) env.TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT = path.resolve(entry.pythonPath);
  if (skillRoot) env.TIANGONG_GATEWAY_SKILL_ROOT = path.resolve(skillRoot);
  if (entry.pythonPath) {
    env.PYTHONPATH = [entry.pythonPath, process.env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter);
  }
  return env;
}

async function waitForTotalGateway(child = null, failed = null, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  for (let i = 0; i < SERVICE_START_ATTEMPTS; i += 1) {
    if (failed?.value === true || (child && child.exitCode !== null)) return false;
    if (Date.now() >= deadline) return false;
    // /health becomes available before the embedded runtime has finished
    // collecting the release/life/store evidence required by /ready.  A
    // credential transaction must not commit against that intermediate state:
    // the caller would interpret RUNNING-but-not-READY as a failed restart and
    // roll back a valid vault update.
    // HOTFIX-20260728: /ready 在真实数据下需 20-60 秒收集 release/life/store
    // 证据，5 秒超时会导致就绪检查永远失败、启动页卡死并重试循环。
    // /health 在网关预热期实测 0.7-1.3 秒波动，1 秒超时会偶发误判，放宽到 3 秒。
    if (await totalGatewayHealthCheck(3000) && await totalGatewayReadyCheck(90000)) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function startTotalGateway() {
  if (totalGatewayStarting) return waitForTotalGateway();
  totalGatewayStarting = true;
  try {
    // Remove stale listeners from pre-merge builds before acquiring the
    // embedded Life writer lease.  The retired 7174/7175/7176 ports are swept
    // asynchronously (fire-and-forget) so the netstat/tasklist scans no longer
    // block startup; the helper functions verify process image ownership and
    // never terminate an unrelated listener.
    Promise.all([
      stopBackendGatewayAsync("embedded-cutover", { expectedDir: backendDir(), includeReplaceableListeners: true }),
      stopLifeServiceAsync("embedded-cutover", { includeListeners: true }),
      stopCommunicationServiceAsync("embedded-cutover", { includeListeners: true }),
    ]).catch((error) => {
      writeDesktopDiagnostic("embedded-cutover-sweep-failed", error?.message || error);
    });
    if (await totalGatewayHealthCheck(1000)) {
      for (const pid of totalGatewayListenerPids()) adoptedTotalGatewayPids.add(pid);
      return waitForTotalGateway();
    }
    const listeners = totalGatewayListenerPids();
    if (listeners.length) {
      writeDesktopDiagnostic("total-gateway-port-conflict", listeners.join(","));
      return false;
    }
    const entries = totalGatewayEntries();
    if (!entries.length) {
      console.warn("Tiangong total gateway was not found.");
      return false;
    }
    const logDir = path.join(runtimeStateRoot(), "logs");
    fs.mkdirSync(logDir, { recursive: true });
    for (const [entryIndex, entry] of entries.entries()) {
      const failed = { value: false };
      writeDesktopDiagnostic("total-gateway-entry-attempt", JSON.stringify({
        index: entryIndex,
        kind: entry.kind || "unknown",
        command: entry.command,
      }));
      let child;
      try {
        child = spawnLoggedProcess(entry.command, entry.args, {
          cwd: entry.cwd,
          env: totalGatewayEnvironment(entry),
          outPath: path.join(logDir, `total_gateway_${DEFAULT_TOTAL_GATEWAY_PORT}.out.log`),
          errPath: path.join(logDir, `total_gateway_${DEFAULT_TOTAL_GATEWAY_PORT}.err.log`),
          // 7184 is an Electron-owned runtime, never an independent background
          // service. Keeping it attached lets process-tree termination cover it.
          detached: false,
        });
      } catch (error) {
        writeDesktopDiagnostic("total-gateway-spawn-threw", `${entry.kind || "unknown"}: ${error?.message || error}`);
        continue;
      }
      totalGatewayProcess = child;
      child.on("exit", (code, signal) => {
        if (code !== 0 && code !== null) {
          console.warn(`Tiangong total gateway exited: code=${code} signal=${signal || ""}`);
        }
        if (totalGatewayProcess === child) totalGatewayProcess = null;
      });
      child.once("error", (error) => {
        failed.value = true;
        writeDesktopDiagnostic("total-gateway-process-error", `${entry.kind || "unknown"}: ${error?.message || error}`);
      });
      if (await waitForTotalGateway(child, failed)) {
        writeDesktopDiagnostic("total-gateway-entry-selected", entry.kind || "unknown");
        return true;
      }
      await stopChildGracefully(child, `total-gateway-entry-failed:${entry.kind || "unknown"}`, 1000);
      if (totalGatewayProcess === child) totalGatewayProcess = null;
    }
    writeDesktopDiagnostic("total-gateway-start-exhausted", entries.map((entry) => entry.kind || "unknown").join(","));
    return false;
  } finally {
    totalGatewayStarting = false;
  }
}

async function stopTotalGateway(reason = "app-exit") {
  const child = totalGatewayProcess;
  if (child) await stopChildGracefully(child, reason);
  const pids = new Set(adoptedTotalGatewayPids);
  if (child?.pid) pids.delete(child.pid);
  for (const pid of pids) {
    console.log(`Stopping adopted Tiangong total gateway (${reason}): ${pid}`);
    killProcessTreeSync(pid);
  }
  adoptedTotalGatewayPids.clear();
  if (totalGatewayProcess === child) totalGatewayProcess = null;
}

function stopTotalGatewaySync(reason = "app-exit-failsafe") {
  // `before-quit` performs the normal async drain.  This synchronous guard is
  // deliberately retained for the final Electron shutdown path so an already
  // adopted 7184 process cannot outlive its only desktop owner.
  const pids = new Set(adoptedTotalGatewayPids);
  if (totalGatewayProcess?.pid) pids.add(totalGatewayProcess.pid);
  for (const pid of pids) {
    console.log(`Stopping Electron-owned Tiangong total gateway (${reason}): ${pid}`);
    killProcessTreeSync(pid);
  }
  adoptedTotalGatewayPids.clear();
  totalGatewayProcess = null;
}

function sameWindowsPath(left, right) {
  try {
    return path.resolve(String(left || "")).toLowerCase() === path.resolve(String(right || "")).toLowerCase();
  } catch (_error) {
    return false;
  }
}

async function ensurePortableWorkspace(workspaceRoot) {
  if (!portableExecutableDir) return true;
  const configured = String(process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT || "").trim();
  return sameWindowsPath(configured, workspaceRoot);
}

function backendPort() {
  try {
    return new URL(BACKEND_URL).port || DEFAULT_BACKEND_PORT;
  } catch (_error) {
    return DEFAULT_BACKEND_PORT;
  }
}

function isLocalBackendUrl() {
  try {
    const parsed = new URL(BACKEND_URL);
    return ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname);
  } catch (_error) {
    return false;
  }
}

function csvFirstField(line) {
  const match = String(line || "").match(/^\s*"([^"]+)"/);
  if (match) return match[1];
  return String(line || "").split(",")[0].trim();
}

function processImageName(pid) {
  if (process.platform !== "win32") return "";
  try {
    const output = execFileSync("tasklist", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"], {
      encoding: "utf8",
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return csvFirstField(output.split(/\r?\n/)[0] || "");
  } catch (_error) {
    return "";
  }
}

function backendListenerPids() {
  if (process.platform !== "win32" || !isLocalBackendUrl()) return [];
  const port = String(backendPort());
  try {
    const output = execFileSync("netstat", ["-ano", "-p", "tcp"], {
      encoding: "utf8",
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const pids = new Set();
    for (const line of output.split(/\r?\n/)) {
      const parts = line.trim().split(/\s+/);
      if (parts.length < 5 || parts[0] !== "TCP") continue;
      const localAddress = parts[1] || "";
      const state = (parts[3] || "").toUpperCase();
      const pid = Number(parts[4] || 0);
      if (state === "LISTENING" && pid > 0 && localAddress.endsWith(`:${port}`)) {
        pids.add(pid);
      }
    }
    return [...pids];
  } catch (_error) {
    return [];
  }
}

function killProcessTreeSync(pid) {
  if (!pid || pid === process.pid) return;
  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
    } else {
      process.kill(pid, "SIGTERM");
    }
  } catch (_error) {
    try {
      process.kill(pid);
    } catch (__error) {
      // The process may already be gone.
    }
  }
}

function spawnLoggedProcess(command, args, { cwd, env, outPath, errPath, detached = false }) {
  const out = fs.openSync(outPath, "a");
  const err = fs.openSync(errPath, "a");
  try {
    return spawn(command, args, {
      cwd,
      env,
      detached,
      windowsHide: true,
      stdio: ["ignore", out, err],
    });
  } finally {
    fs.closeSync(out);
    fs.closeSync(err);
  }
}

function waitForChildExit(child, timeoutMs = 2500) {
  if (!child || !child.pid || child.exitCode != null || child.signalCode != null) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeListener("exit", onExit);
      resolve(value);
    };
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    child.once("exit", onExit);
  });
}

async function stopChildGracefully(child, reason, timeoutMs = 2500) {
  if (!child?.pid || child.exitCode != null || child.signalCode != null) return;
  console.log(`Draining child process (${reason}): ${child.pid}`);
  try {
    child.kill(process.platform === "win32" ? "SIGBREAK" : "SIGTERM");
  } catch (_error) {
    // The bounded fallback below owns cleanup.
  }
  if (!(await waitForChildExit(child, timeoutMs))) killProcessTreeSync(child.pid);
}

function stopBackendGatewaySync(reason = "app-exit", options = {}) {
  if (backendStopping) return;
  backendStopping = true;
  const expectedDir = options.expectedDir || null;
  const includeReplaceableListeners = !!options.includeReplaceableListeners;
  const pids = new Set();
  if (backendProcess && backendProcess.pid && !backendProcess.killed) {
    pids.add(backendProcess.pid);
  }
  for (const pid of backendListenerPids()) {
    const imageName = processImageName(pid).toLowerCase();
    if (
      imageName.includes("tiangong-backend") ||
      pid === backendProcess?.pid ||
      (includeReplaceableListeners && isReplaceableBackendListener(pid, expectedDir))
    ) {
      pids.add(pid);
    }
  }
  for (const pid of pids) {
    console.log(`Stopping Tiangong backend gateway (${reason}): ${pid}`);
    killProcessTreeSync(pid);
  }
  backendProcess = null;
  backendStopping = false;
}

// Async fire-and-forget variants of the stale-listener sweep above.  They run
// the identical netstat/tasklist/taskkill logic through the non-blocking
// execFile so startup never freezes the main process on a full netstat table.
function execFileText(command, args) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { encoding: "utf8", windowsHide: true }, (error, stdout) => {
      if (error) reject(error);
      else resolve(String(stdout || ""));
    });
  });
}

async function processImageNameAsync(pid) {
  if (process.platform !== "win32") return "";
  try {
    const output = (await execFileText("tasklist", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"])).trim();
    return csvFirstField(output.split(/\r?\n/)[0] || "");
  } catch (_error) {
    return "";
  }
}

async function processExecutablePathAsync(pid) {
  if (process.platform !== "win32" || !pid) return "";
  try {
    const output = await execFileText("wmic", ["process", "where", `ProcessId=${pid}`, "get", "ExecutablePath", "/value"]);
    const match = output.match(/ExecutablePath=(.*)/i);
    return (match?.[1] || "").trim();
  } catch (_error) {
    return "";
  }
}

function parseNetstatListenerPids(output, port) {
  const pids = new Set();
  for (const line of String(output || "").split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 5 || parts[0] !== "TCP") continue;
    const localAddress = parts[1] || "";
    const state = (parts[3] || "").toUpperCase();
    const pid = Number(parts[4] || 0);
    if (state === "LISTENING" && pid > 0 && localAddress.endsWith(`:${port}`)) pids.add(pid);
  }
  return [...pids];
}

async function serviceListenerPidsAsync(rawUrl, fallbackPort) {
  if (process.platform !== "win32") return [];
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch (_error) {
    return [];
  }
  if (!["127.0.0.1", "localhost", "::1"].includes(parsed.hostname)) return [];
  const port = String(parsed.port || fallbackPort);
  try {
    return parseNetstatListenerPids(await execFileText("netstat", ["-ano", "-p", "tcp"]), port);
  } catch (_error) {
    return [];
  }
}

async function backendListenerPidsAsync() {
  if (process.platform !== "win32" || !isLocalBackendUrl()) return [];
  try {
    return parseNetstatListenerPids(await execFileText("netstat", ["-ano", "-p", "tcp"]), String(backendPort()));
  } catch (_error) {
    return [];
  }
}

async function isReplaceableBackendListenerAsync(pid, dir) {
  if (!pid || pid === backendProcess?.pid) return false;
  const imageName = (await processImageNameAsync(pid)).toLowerCase();
  if (!imageName.includes("python") && !imageName.includes("tiangong-backend")) return false;
  const expectedExe = frozenBackendExecutablePath(dir);
  if (!expectedExe) return isSourceBackendDir(dir) && imageName.includes("tiangong-backend");
  const actualExe = await processExecutablePathAsync(pid);
  return !actualExe || normalizeFsPath(actualExe) !== normalizeFsPath(expectedExe);
}

async function killProcessTreeAsync(pid) {
  if (!pid || pid === process.pid) return;
  try {
    if (process.platform === "win32") {
      await execFileText("taskkill", ["/PID", String(pid), "/T", "/F"]);
    } else {
      process.kill(pid, "SIGTERM");
    }
  } catch (_error) {
    try {
      process.kill(pid);
    } catch (__error) {
      // The process may already be gone.
    }
  }
}

async function stopBackendGatewayAsync(reason = "app-exit", options = {}) {
  if (backendStopping) return;
  backendStopping = true;
  const expectedDir = options.expectedDir || null;
  const includeReplaceableListeners = !!options.includeReplaceableListeners;
  const pids = new Set();
  if (backendProcess && backendProcess.pid && !backendProcess.killed) {
    pids.add(backendProcess.pid);
  }
  for (const pid of await backendListenerPidsAsync()) {
    const imageName = (await processImageNameAsync(pid)).toLowerCase();
    if (
      imageName.includes("tiangong-backend") ||
      pid === backendProcess?.pid ||
      (includeReplaceableListeners && await isReplaceableBackendListenerAsync(pid, expectedDir))
    ) {
      pids.add(pid);
    }
  }
  for (const pid of pids) {
    console.log(`Stopping Tiangong backend gateway (${reason}): ${pid}`);
    await killProcessTreeAsync(pid);
  }
  backendProcess = null;
  backendStopping = false;
}

async function stopLifeServiceAsync(reason = "app-exit", options = {}) {
  const pids = new Set();
  if (lifeProcess?.pid && !lifeProcess.killed) pids.add(lifeProcess.pid);
  if (options.includeListeners || reason === "app-exit" || reason === "window-all-closed" || reason === "before-quit") {
    for (const pid of await serviceListenerPidsAsync(LIFE_URL, DEFAULT_LIFE_PORT)) {
      if ((await processImageNameAsync(pid)).toLowerCase().includes("tiangong-life-service")) pids.add(pid);
    }
  }
  for (const pid of pids) {
    console.log(`Stopping Tiangong complete life service (${reason}): ${pid}`);
    await killProcessTreeAsync(pid);
  }
  lifeProcess = null;
}

async function stopCommunicationServiceAsync(reason = "app-exit", options = {}) {
  const pids = new Set();
  if (communicationProcess?.pid && !communicationProcess.killed) pids.add(communicationProcess.pid);
  if (options.includeListeners || reason === "app-exit" || reason === "window-all-closed" || reason === "before-quit") {
    for (const pid of await serviceListenerPidsAsync(COMMUNICATION_URL, DEFAULT_COMMUNICATION_PORT)) {
      if ((await processImageNameAsync(pid)).toLowerCase().includes("tiangong-communication-service")) pids.add(pid);
    }
  }
  for (const pid of pids) {
    console.log(`Stopping Tiangong communication service (${reason}): ${pid}`);
    await killProcessTreeAsync(pid);
  }
  communicationProcess = null;
}

async function waitForBackend() {
  for (let i = 0; i < SERVICE_START_ATTEMPTS; i += 1) {
    if (await healthCheck(1000)) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function startBackend() {
  if (backendStarting) return waitForBackend();
  backendStarting = true;
  const dir = backendDir();
  try {
    if (await healthCheck(2000)) {
      backendHealthFailures = 0;
      if (shouldReplaceExistingBackend(dir)) {
        stopBackendGatewaySync("replace-stale-backend-listener", {
          expectedDir: dir,
          includeReplaceableListeners: true,
        });
        await new Promise((resolve) => setTimeout(resolve, 500));
      } else {
        return true;
      }
    }

    if (backendListenerPids().length) {
      stopBackendGatewaySync("replace-unhealthy-backend-listener", {
        expectedDir: dir,
        includeReplaceableListeners: true,
      });
      await new Promise((resolve) => setTimeout(resolve, 500));
    }

    if (!dir) {
      console.warn("Tiangong v3 backend launcher was not found; loading the frontend with offline runtime status.");
      return false;
    }

    const stateRoot = runtimeStateRoot();
    const stateDir = process.env.TIANGONG_DESKTOP_STATE_DIR || path.join(stateRoot, "state");
    const workspaceRoot = process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT || path.join(stateRoot, "workspaces");
    const logDir = path.join(stateRoot, "logs");
    const sourceOmniBodyRoot = path.join(dir, "omni_body_skill");
    const frozenOmniBodyRoot = path.join(dir, "_internal", "omni_body_skill");
    const omniBodyRoot = exists(sourceOmniBodyRoot) ? sourceOmniBodyRoot : frozenOmniBodyRoot;
    const omniBodyStateRoot = path.join(stateDir, "omni_body");
    const executionShadowRoot = path.join(stateDir, "execution_shadow");
    const isolatedHome = process.env.TIANGONG_HOME_PATH || app.getPath("home");
    fs.mkdirSync(stateDir, { recursive: true });
    fs.mkdirSync(workspaceRoot, { recursive: true });
    fs.mkdirSync(logDir, { recursive: true });
    fs.mkdirSync(omniBodyStateRoot, { recursive: true });
    fs.mkdirSync(executionShadowRoot, { recursive: true });
    const lifeIdentityMigration = migrateLegacyLifeIdentity(stateDir);
    if (!lifeIdentityMigration.ok) {
      console.error("Tiangong life identity migration refused:", lifeIdentityMigration);
    } else if (lifeIdentityMigration.status === "identity_migrated_to_current") {
      console.log("Tiangong life identity migrated:", lifeIdentityMigration);
    }

    const env = {
      ...process.env,
      HOME: isolatedHome,
      USERPROFILE: isolatedHome,
      // Backend must call provider APIs directly; proxy variables inherited
      // from the host shell can break TLS handshakes (SSL: UNEXPECTED_EOF).
      HTTP_PROXY: "",
      HTTPS_PROXY: "",
      http_proxy: "",
      https_proxy: "",
      NO_PROXY: "*",
      no_proxy: "*",
      HOST: "127.0.0.1",
      PORT: backendPort(),
      TIANGONG_DESKTOP_STATE_DIR: stateDir,
      TIANGONG_DESKTOP_TOKEN: BACKEND_INTERNAL_TOKEN,
      // 7174 only consumes the already-authorized life context carried by the
      // request. It has no life-service credential and no context compiler URL.
      TIANGONG_DESKTOP_WORKSPACE_ROOT: workspaceRoot,
      TIANGONG_RUN_STATE_DIR: stateDir,
      TIANGONG_LIFE_KERNEL_ROOT: process.env.TIANGONG_LIFE_KERNEL_ROOT || path.join(stateDir, "life_kernel"),
      TIANGONG_LIFE_ROOT: process.env.TIANGONG_LIFE_ROOT || path.join(stateDir, "life_transaction"),
      TIANGONG_BUILD_ID: EXPECTED_BACKEND_BUILD_ID,
      TIANGONG_BACKEND_API_CONTRACT: BACKEND_API_CONTRACT,
      TIANGONG_OMNI_BODY_ROOT: omniBodyRoot,
      TIANGONG_OMNI_BODY_WORKSPACE: workspaceRoot,
      TIANGONG_OMNI_BODY_STATE_ROOT: omniBodyStateRoot,
      TIANGONG_EXECUTION_SHADOW_MODE: "observe-only",
      TIANGONG_EXECUTION_SHADOW_ROOT: executionShadowRoot,
      TIANGONG_EXECUTION_SHADOW_AUDIT_VERSION: EXECUTION_SHADOW_AUDIT_VERSION,
      TIANGONG_RELEASE_CONTRACT_VERSION: RELEASE_CONTRACT_VERSION,
      TIANGONG_EXECUTION_POLICY_VERSION: EXECUTION_POLICY_VERSION,
      TIANGONG_OMNI_BODY_ALLOW_SHELL: process.env.TIANGONG_OMNI_BODY_ALLOW_SHELL || "1",
      TIANGONG_OMNI_BODY_ALLOW_ABSOLUTE_PATHS: process.env.TIANGONG_OMNI_BODY_ALLOW_ABSOLUTE_PATHS || "1",
      TIANGONG_HOME_PATH: isolatedHome,
      // Portable/E2E runs must not silently use the host Desktop as their
      // default creation workspace. Installed builds keep normal Desktop
      // semantics; portable builds default to their isolated workspace root.
      TIANGONG_DESKTOP_PATH: process.env.TIANGONG_DESKTOP_PATH || (portableExecutableDir ? workspaceRoot : app.getPath("desktop")),
      TIANGONG_DOWNLOADS_PATH: process.env.TIANGONG_DOWNLOADS_PATH || app.getPath("downloads"),
      TIANGONG_DOCUMENTS_PATH: process.env.TIANGONG_DOCUMENTS_PATH || app.getPath("documents"),
      TIANGONG_PICTURES_PATH: process.env.TIANGONG_PICTURES_PATH || app.getPath("pictures"),
      TIANGONG_VIDEOS_PATH: process.env.TIANGONG_VIDEOS_PATH || app.getPath("videos"),
      TIANGONG_MUSIC_PATH: process.env.TIANGONG_MUSIC_PATH || app.getPath("music"),
      TIANGONG_VRM_FRONTEND_DIR: app.getAppPath(),
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONPATH: [
        path.join(dir, "._verify_deps"),
        dir,
        process.env.PYTHONPATH || "",
      ].filter(Boolean).join(path.delimiter),
    };

    const port = backendPort();
    const entry = backendEntry(dir);
    let spawnError = null;
    try {
      const child = spawnLoggedProcess(entry.command, entry.args, {
        cwd: entry.cwd,
        env,
        outPath: path.join(logDir, `server_${port}.out.log`),
        errPath: path.join(logDir, `server_${port}.err.log`),
      });
      backendProcess = child;
    } catch (error) {
      console.error("Tiangong backend spawn failed:", error);
      return false;
    }

    const child = backendProcess;
    child.once("error", (error) => {
      spawnError = error;
      console.error("Tiangong backend process error:", error);
    });

    child.on("exit", (code, signal) => {
      if (code !== 0 && code !== null) {
        console.warn(`Tiangong backend exited before readiness: code=${code} signal=${signal || ""}`);
      }
      if (backendProcess === child) backendProcess = null;
    });

    if (!(await waitForBackend())) {
      const reason = spawnError ? spawnError.message : "startup timed out";
      console.warn(`Tiangong backend was not ready (${reason}). Log directory: ${logDir}`);
      if (backendProcess && backendProcess.pid && !backendProcess.killed) {
        killProcessTreeSync(backendProcess.pid);
        backendProcess = null;
      }
      return false;
    }
    if (!(await ensurePortableWorkspace(workspaceRoot))) {
      console.warn(`Portable workspace could not be isolated: ${workspaceRoot}`);
      return false;
    }
    backendHealthFailures = 0;
    return true;
  } finally {
    backendStarting = false;
  }
}

function startBackendWatchdog() {
  serviceSupervisor.startMonitoring();
}

function stopBackendWatchdog() {
  serviceSupervisor.stopMonitoring();
}

const serviceSupervisor = new ServiceSupervisor({
  failureThreshold: 12,
  restartDelayMs: 750,
  monitorIntervalMs: 5000,
  services: [
    {
      name: "total-gateway", phase: 0, start: startTotalGateway,
      // HOTFIX-20260728: 就绪探测超时对齐 /ready 在真实数据下的实际耗时（20-60s）；
      // 健康探测同步放宽（预热期实测波动超过 1-2s）。
      health: () => totalGatewayHealthCheck(3000), ready: () => totalGatewayReadyCheck(90000), stop: stopTotalGateway,
    },
  ],
  onTransition: (event) => {
    writeDesktopDiagnostic("service-transition", JSON.stringify(event));
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("services:status", serviceSupervisor.snapshot());
    }
  },
});

async function createWindow() {
  applyWorkspacePreference();
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    const trusted = isTrustedAppUrl(webContents?.getURL?.() || "");
    callback(permission === "media" && trusted);
  });

  try {
    hydrateProviderApiKeys();
  } catch (error) {
    writeDesktopDiagnostic("provider-credentials-hydrate-failed", error?.message || error);
  }
  applyWindowTheme("ink_teal");

  mainWindow = new BrowserWindow({
    show: true,
    width: 1380,
    height: 840,
    minWidth: 1040,
    minHeight: 680,
    icon: APP_ICON_FILE,
    frame: false,
    transparent: false,
    backgroundColor: "#0C0E11",
    webPreferences: {
      preload: PRELOAD_FILE,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });
  applyWindowTheme("ink_teal");

  const splash = `<!doctype html><meta charset="utf-8"><title>${PRODUCT_LABEL}正在启动</title><style>
    html,body{height:100%;margin:0;background:#0c0e11;color:#dce9e5;font-family:"Microsoft YaHei UI",system-ui,sans-serif}
    body{display:grid;place-items:center}.box{text-align:center}.mark{font-size:48px;margin-bottom:18px}.title{font-size:22px;font-weight:700}
    .hint{margin-top:12px;color:#86aaa1;font-size:14px}.pulse{display:inline-block;animation:p 1.1s ease-in-out infinite}@keyframes p{50%{opacity:.35}}
  </style><div class="box"><div class="mark">◉</div><div class="title">${PRODUCT_LABEL}正在启动</div><div class="hint"><span class="pulse">正在唤醒生命与工具服务，请稍候…</span></div></div>`;
  await mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(splash)}`).catch(() => {});

  const servicesStartedAt = Date.now();
  const serviceSnapshot = await serviceSupervisor.startAll();
  const totalGatewayRunning = serviceSnapshot["total-gateway"]?.running === true;
  const totalGatewayReady = serviceSnapshot["total-gateway"]?.ready === true;
  const backendReady = totalGatewayReady;
  const lifeReady = totalGatewayReady;
  const communicationRunning = totalGatewayRunning;
  const communicationReady = totalGatewayReady;
  writeDesktopDiagnostic("application-services-start-ms", Date.now() - servicesStartedAt);
  writeDesktopDiagnostic("application-services-start-state", JSON.stringify(serviceSnapshot));
  if (!mainWindow || mainWindow.isDestroyed()) return;

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  installEditContextMenu(mainWindow.webContents);
  mainWindow.webContents.on("did-fail-load", (_event, code, description, validatedURL, isMainFrame) => {
    writeDesktopDiagnostic("did-fail-load", JSON.stringify({ code, description, validatedURL, isMainFrame }));
    if (isMainFrame && mainWindow && !mainWindow.isDestroyed()) {
      const safe = String(description || `load error ${code}`).replace(/[<>&]/g, "");
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`<!doctype html><meta charset="utf-8"><style>body{background:#0c0e11;color:#e6edf3;font:16px system-ui;padding:40px}code{color:#ffb4a8}</style><h1>天工桌面加载失败</h1><p>客户端已阻止黑屏并记录诊断。</p><code>${safe}</code>`)}`).catch(() => {});
    }
  });
  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    writeDesktopDiagnostic("render-process-gone", JSON.stringify(details || {}));
  });
  mainWindow.webContents.on("console-message", (details) => {
    const severity = String(details?.level || "").toLowerCase();
    if (["warning", "error"].includes(severity)) {
      writeDesktopDiagnostic("renderer-console", JSON.stringify({
        level: severity,
        message: String(details?.message || ""),
        line: Number(details?.lineNumber || 0),
        sourceId: String(details?.sourceId || ""),
      }));
    }
  });
  mainWindow.on("unresponsive", () => writeDesktopDiagnostic("window-unresponsive"));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isTrustedAppUrl(url)) event.preventDefault();
  });
  mainWindow.webContents.on("will-frame-navigate", (details) => {
    if (!isTrustedAppFrameUrl(details?.url || "")) details.preventDefault();
  });

  const loadLocalFrontend = () => {
    if (!exists(PRIMARY_FRONTEND_FILE)) throw new Error("primary_frontend_missing");
    return mainWindow && mainWindow.loadFile(PRIMARY_FRONTEND_FILE);
  };
  if (!backendReady) {
    console.warn("Tiangong backend daemon was not ready; loading the desktop frontend with offline runtime status.");
  }
  if (!lifeReady) {
    console.warn("Tiangong complete life service was not ready; identity and life organs will remain offline.");
  }
  if (!totalGatewayRunning) {
    console.warn("Tiangong total gateway was not running; business orchestration will remain offline.");
  } else if (!totalGatewayReady) {
    console.warn("Tiangong total gateway is alive but not ready; inspect the /ready reason_codes for the failed service or evidence check.");
  }
  if (!communicationRunning || !communicationReady) {
    console.warn("Tiangong communication service was not ready; WeChat and Feishu connections will remain offline.");
  }
  const frontendLoadStartedAt = Date.now();
  try {
    await loadLocalFrontend();
    writeDesktopDiagnostic("frontend-load-complete-ms", Date.now() - frontendLoadStartedAt);
    setTimeout(async () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      try {
        const ready = await mainWindow.webContents.executeJavaScript(
          'document.documentElement.dataset.tiangongCoreLoaded === "true" && document.documentElement.dataset.tiangongReady !== "failed"',
          true,
        );
        if (!ready) {
          writeDesktopDiagnostic("renderer-ready-marker-missing");
          await mainWindow.webContents.executeJavaScript(
            'window.__tiangongShowFatal?.("核心界面模块未完成加载，请重启客户端；诊断已保存。")',
            true,
          );
        }
      } catch (error) {
        writeDesktopDiagnostic("renderer-ready-probe-failed", error?.message || error);
      }
    }, 2500);
  } catch (error) {
    writeDesktopDiagnostic("load-frontend-rejected", error?.stack || error?.message || error);
    throw error;
  }
  startBackendWatchdog();

  const updateToken = postUpdateToken();
  if (updateToken && backendReady && lifeReady && totalGatewayReady && communicationReady) {
    try {
      getSecureUpdater().markHealthy(updateToken);
    } catch (error) {
      writeDesktopDiagnostic("update-health-commit-failed", error?.message || error);
    }
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
    stopBackendWatchdog();
  });
}

function revealMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  if (!mainWindow.isVisible()) mainWindow.show();
  if (mainWindow.isMinimized()) mainWindow.restore();
  try {
    mainWindow.moveTop();
  } catch (_error) {
    // Some platforms ignore moveTop for unfocused windows.
  }
  mainWindow.focus();
  return true;
}

if (!singleInstanceLock) {
  app.quit();
} else {
  if (process.platform === "win32") {
    app.setAppUserModelId(APP_ID);
  }

  app.on("web-contents-created", (_event, contents) => {
    const webQaContents = String(contents?.session?.getPartition?.() || "").startsWith("temporary:tiangong-web-qa-");
    contents.setWindowOpenHandler(() => ({ action: "deny" }));
    if (!webQaContents) installEditContextMenu(contents);
    contents.on("will-navigate", (event, url) => {
      if (!webQaContents && !isTrustedAppUrl(url)) event.preventDefault();
    });
    contents.on("will-attach-webview", (event) => {
      event.preventDefault();
    });
  });

  app.on("second-instance", () => {
    revealMainWindow();
  });

  app.on("activate", () => {
    if (WEB_QA_MODE) return;
    if (!revealMainWindow()) {
      createWindow().catch((error) => {
        console.error(error);
        app.quit();
      });
    }
  });

  app.whenReady().then(() => {
    bindRuntimeKnownFolders();
    avatarStorageHost = createAvatarStorageHost({ userDataRoot: app.getPath("userData") });
    // P2b 受控资产协议（方案 §8.3）：tiangong-asset://<scope>/<id>，scope ∈
    // {builtin, model, candidate}（quarantine 默认拒绝）；HOTFIX-20260728 的
    // legacy <app>/assets 读取以 legacyHandler 形式并存（行为不变，P7 清理）。
    const avatarRegistryPaths = {
      builtinRoot: path.join(app.getAppPath(), "assets"),
      modelRoot: path.join(app.getPath("userData"), "avatar-models", "models"),
      candidateRoot: path.join(app.getPath("userData"), "avatar-models", "temp"),
    };
    try {
      fs.mkdirSync(avatarRegistryPaths.modelRoot, { recursive: true });
      fs.mkdirSync(avatarRegistryPaths.candidateRoot, { recursive: true });
    } catch (error) {
      writeDesktopDiagnostic("avatar-asset-dirs-failed", error?.message || error);
    }
    avatarAssetHost = installAvatarAssetProtocol({
      session: session.defaultSession,
      registryPaths: avatarRegistryPaths,
      builtinModelMap: loadBuiltinAvatarModelMap(),
      grantIssuer: createCandidateGrantIssuer({ issuerEpoch: AVATAR_ASSET_ISSUER_EPOCH }),
      legacyHandler: legacyProjectAssetResponse,
    });
    if (WEB_QA_MODE) {
      verifyLocalWebProject({ workspace: WEB_QA_WORKSPACE, projectRoot: WEB_QA_TARGET })
        .then((result) => {
          console.log(`__TIANGONG_WEB_QA__${JSON.stringify(result)}__END__`);
          app.exit(result.ok ? 0 : 2);
        })
        .catch((error) => {
          console.error(`__TIANGONG_WEB_QA__${JSON.stringify({ ok: false, error: error?.message || String(error) })}__END__`);
          app.exit(3);
        });
      return;
    }
    createWindow().catch((error) => {
      console.error(error);
      app.quit();
    });
  });
}

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (WEB_QA_MODE || serviceShutdownComplete) return;
  event.preventDefault();
  if (serviceShutdownPromise) return;
  stopBackendWatchdog();
  serviceShutdownPromise = serviceSupervisor.drainAll("before-quit")
    .catch((error) => {
      writeDesktopDiagnostic("service-drain-failed", error?.message || error);
    })
    .finally(() => {
      serviceShutdownComplete = true;
      serviceShutdownPromise = null;
      app.quit();
    });
});

app.on("will-quit", () => {
  if (!WEB_QA_MODE) stopTotalGatewaySync("will-quit");
});

process.once("exit", () => {
  if (!WEB_QA_MODE) stopTotalGatewaySync("process-exit");
});

handleTrusted("services:getStatus", async () => serviceSupervisor.snapshot());

// Read-only project asset channel for sandboxed local frames: file:// fetch
// into app.asar is refused by Chromium, so VRM/animation bytes are read by
// the main process instead.  Paths are confined to <app>/assets.
ipcMain.handle("tiangong:read-asset", async (event, relativePath) => {
  if (!isTrustedLocalFrameEvent(event)) throw new Error("untrusted_renderer_ipc");
  const target = projectAssetPath(relativePath);
  if (!target || !isFile(target)) throw new Error("asset_not_found");
  return await fs.promises.readFile(target);
});

// P2b 候选读取 grant（方案 §8.5）：renderer 不提供路径——不可变候选快照由主进程
// 按 contentHash 在 candidateRoot 内解析；返回的 opaque 视图不含任何路径字段。
handleTrusted("avatar:issueCandidateGrant", async (_event, payload = {}) => {
  if (!avatarAssetHost || !avatarAssetHost.grantIssuer) throw new Error("avatar_host_not_ready");
  const contentHash = String(payload.contentHash || "");
  if (!/^[0-9a-f]{64}$/.test(contentHash)) throw new Error("content_hash_invalid");
  const byteLength = Number(payload.byteLength);
  if (!Number.isInteger(byteLength) || byteLength <= 0) throw new Error("byte_length_invalid");
  const attemptId = String(payload.attemptId || "").trim();
  const candidateId = String(payload.candidateId || "").trim();
  if (!attemptId || !candidateId) throw new Error("candidate_identity_invalid");
  const candidateRoot = path.resolve(app.getPath("userData"), "avatar-models", "temp");
  const exact = path.resolve(candidateRoot, `${contentHash}.vrm`);
  if (!exact.startsWith(candidateRoot + path.sep)) throw new Error("candidate_path_escape");
  if (!isFile(exact)) throw new Error("candidate_not_found");
  return avatarAssetHost.grantIssuer.issueGrant({
    attemptId,
    candidateId,
    contentHash,
    byteLength,
    exactResolvedPath: exact,
    singleUse: payload.singleUse !== false,
  });
});

// P6a §8.5 导入主流程（窄接线）：文件选择/限额/受控复制/流式 SHA-256 全部在
// avatar-asset-host.cjs 完成；此处只注入 dialog/window/根目录，不向 renderer
// 返回任何绝对路径（opaque 结果：name/attemptId/candidateId/contentHash/byteLength）。
handleTrusted("avatar:chooseImportFile", async () => {
  const candidateRoot = path.resolve(app.getPath("userData"), "avatar-models", "temp");
  // 源码工作版重定向 HOME/USERPROFILE：优先用启动器注入的真实桌面目录作为
  // 对话框默认路径，避免用户看到隔离的“假桌面”而找不到本机 VRM。
  const defaultPath =
    process.env.TIANGONG_DESKTOP_PATH || app.getPath("desktop");
  return chooseAvatarImportFile({
    dialogModule: dialog,
    browserWindow: mainWindow,
    candidateRoot,
    defaultPath,
  });
});

// P6b 内置清单桥（§8.3）：webSecurity 下渲染端 fetch(file://) 被禁，内置模型清单
// 由主进程读取并返回（安装相对路径，非用户绝对路径）。失败返回 {models: []}。
handleTrusted("avatar:getBuiltinManifest", async () => {
  try {
    const manifestPath = path.join(app.getAppPath(), "assets", "avatar", "builtin-models.json");
    if (!isFile(manifestPath)) return { models: [] };
    const doc = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    const appRoot = path.resolve(app.getAppPath());
    const models = Array.isArray(doc?.models)
      ? doc.models.filter((model) => {
          const relativePath = typeof model?.relativePath === "string"
            ? model.relativePath.replace(/^\.\/+/, "")
            : "";
          if (!relativePath || path.isAbsolute(relativePath) ||
              relativePath.split(/[\\/]+/).some((part) => part === "..")) {
            return false;
          }
          const resolved = path.resolve(appRoot, relativePath);
          return resolved.startsWith(`${appRoot}${path.sep}`) && isFile(resolved);
        })
      : [];
    return { ...doc, models };
  } catch (_error) {
    return { models: [] };
  }
});

// P6a §8.5 提交：temp 快照复核 sha256+flush+原子 rename 到 models/<contentHash>.vrm；
// orphan 规则由 host 注释与渲染侧 Token 签发纪律共同保证（登记失败保留文件但不可发现）。
handleTrusted("avatar:commitCandidate", async (_event, payload = {}) => {
  const candidateRoot = path.resolve(app.getPath("userData"), "avatar-models", "temp");
  const modelRoot = path.resolve(app.getPath("userData"), "avatar-models", "models");
  return commitCandidate(payload, { candidateRoot, modelRoot });
});

// §8.5 用户删除：渲染侧先完成 registry tombstone，再按 contentHash 删正式模型文件；
// 本通道不接收路径，只接收 64 位 hex 并由主进程限定在 modelRoot 内。
handleTrusted("avatar:deleteModelFile", async (_event, payload = {}) => {
  const modelRoot = path.resolve(app.getPath("userData"), "avatar-models", "models");
  return deleteModelFile(payload, { modelRoot });
});

// P2 §22.2/§22.3 renderer 状态持久化。renderer 只能传三个固定枚举 key；
// 绝对路径解析、大小/JSON/schema 校验及 temp→fsync→原子替换均由主进程宿主完成。
handleTrusted("avatarStorage:read", async (_event, key) => {
  if (!avatarStorageHost) throw new Error("avatar_storage_host_not_ready");
  return avatarStorageHost.read(key);
});

handleTrusted("avatarStorage:writeAtomic", async (_event, key, bytes) => {
  if (!avatarStorageHost) throw new Error("avatar_storage_host_not_ready");
  return avatarStorageHost.writeAtomic(key, bytes);
});

// P2b §8.4 MessagePort 分块流通道：port 经 event.ports 到达，宿主按 descriptor
// {scope, locator} 裁决（candidate grant 在此单次消费，消费即失效）。
onTrusted("avatar:openChunkedStream", (event, descriptor) => {
  const port = event && event.ports && event.ports[0];
  if (!port) return;
  if (!avatarAssetHost) {
    try {
      port.start();
      port.postMessage({ type: "error", code: "avatar_host_not_ready", message: "avatar host 尚未安装" });
      port.close();
    } catch (_error) { /* port 不可用时静默 */ }
    return;
  }
  avatarAssetHost.openStream(port, descriptor);
});

onTrusted("gateway:getBootstrap", (event) => {
  event.returnValue = {
        gatewayUrl: TOTAL_GATEWAY_URL,
        desktopToken: DESKTOP_API_TOKEN,
        frontendMetadata: {
          version: String(BUILD_INFO.product_version || app.getVersion() || ""),
          productLabel: PRODUCT_LABEL,
          sourceMode: SOURCE_MODE,
          kernelVersion: String(BUILD_INFO.frontend_kernel_version || BUILD_INFO.product_version || app.getVersion() || ""),
          buildId: String(BUILD_INFO.build_id || ""),
        },
      };
});

onTrusted("diagnostic:write", (_event, payload = {}) => {
  const kind = String(payload?.kind || "renderer").replace(/[^A-Za-z0-9._:-]/g, "_").slice(0, 120) || "renderer";
  const detail = String(payload?.detail || "").slice(0, 4000);
  writeDesktopDiagnostic(kind, detail);
});

onTrusted("window:minimize", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) win.minimize();
});

onTrusted("window:maximize", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win || win.isDestroyed()) return;
  if (win.isMaximized() || maximizedByTitlebar) {
    if (win.isMaximized()) win.unmaximize();
    else if (restoreWindowBounds) win.setBounds(restoreWindowBounds);
    maximizedByTitlebar = false;
    restoreWindowBounds = null;
    return;
  }
  restoreWindowBounds = win.getBounds();
  win.maximize();
  maximizedByTitlebar = true;
});

onTrusted("window:close", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) win.close();
});

handleTrusted("theme:set", (_event, themeStyle) => {
  const normalized = normalizeThemeStyle(themeStyle);
  applyWindowTheme(normalized);
  return { ok: true, themeStyle: normalized, nativeTheme: nativeTheme.themeSource };
});



handleTrusted("model:getSettings", async () => desktopModelSettingsRequest("GET"));

handleTrusted("model:setSettings", async (_event, payload = {}) => queueSecureModelSettingsUpdate(payload || {}));

handleTrusted("model:probeProviderApi", async () => probeProviderApiConnection());
handleTrusted("model:testDeepSeekConnection", async () => probeProviderApiConnection());

handleTrusted("qa:verifyWebProject", async (_event, payload = {}) => verifyLocalWebProject(payload || {}));

handleTrusted("dialog:chooseKnowledgeRoot", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择知识库位置",
    defaultPath: safeKnownFolder("documents", "TIANGONG_DOCUMENTS_PATH"),
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true, path: "" };
  return { ok: true, canceled: false, path: result.filePaths[0] };
});

handleTrusted("dialog:chooseWorkspaceRoot", async (_event, payload = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择工作区",
    defaultPath: normalizeDialogDefaultPath(payload),
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true, path: "" };
  return { ok: true, canceled: false, path: result.filePaths[0] };
});

handleTrusted("workspace:getRoot", async () => workspaceRootStatus());

handleTrusted("workspace:setRoot", async (_event, request) => setWorkspaceRoot(request));

handleTrusted("dialog:chooseStorageRoot", async (_event, payload = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose storage directory",
    defaultPath: normalizeDialogDefaultPath(payload),
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true, path: "" };
  return { ok: true, canceled: false, path: result.filePaths[0] };
});

handleTrusted("dialog:choosePersonaAvatar", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择角色头像",
    defaultPath: safeKnownFolder("pictures", "TIANGONG_PICTURES_PATH"),
    properties: ["openFile"],
    filters: imageFileFilters(),
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true, path: "" };
  const selected = result.filePaths[0];
  return {
    ok: true,
    canceled: false,
    path: selected,
    personaAvatarDataUrl: dataUrlForFile(selected),
  };
});

handleTrusted("dialog:chooseUserAvatar", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose user avatar",
    defaultPath: safeKnownFolder("pictures", "TIANGONG_PICTURES_PATH"),
    properties: ["openFile"],
    filters: imageFileFilters(),
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true, path: "" };
  const selected = result.filePaths[0];
  return {
    ok: true,
    canceled: false,
    path: selected,
    userAvatarDataUrl: dataUrlForFile(selected),
  };
});

handleTrusted("dialog:chooseVoiceSample", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择自定义声音样本",
    defaultPath: safeKnownFolder("music", "TIANGONG_MUSIC_PATH"),
    properties: ["openFile"],
    filters: audioFileFilters(),
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true, path: "" };
  const selected = result.filePaths[0];
  return {
    ok: true,
    canceled: false,
    path: selected,
    name: path.basename(selected),
  };
});

const MAX_VRM_IMPORT_BYTES = 256 * 1024 * 1024;

handleTrusted("dialog:chooseVrmModel", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择 VRM 身体模型",
    defaultPath: safeKnownFolder("documents", "TIANGONG_DOCUMENTS_PATH"),
    properties: ["openFile"],
    filters: [{ name: "VRM 模型", extensions: ["vrm"] }],
  });
  if (result.canceled || !result.filePaths[0]) {
    return { ok: true, canceled: true, name: "", size: 0, bytes: null };
  }

  const selected = path.resolve(result.filePaths[0]);
  if (path.extname(selected).toLowerCase() !== ".vrm") {
    return { ok: false, canceled: false, error: "请选择 .vrm 模型文件" };
  }

  let stat;
  try {
    stat = await fs.promises.stat(selected);
  } catch {
    return { ok: false, canceled: false, error: "无法读取所选模型文件" };
  }
  if (!stat.isFile() || stat.size <= 0) {
    return { ok: false, canceled: false, error: "所选 VRM 文件为空或不是普通文件" };
  }
  if (stat.size > MAX_VRM_IMPORT_BYTES) {
    return { ok: false, canceled: false, error: "VRM 文件超过 256 MB 上限" };
  }

  try {
    const bytes = await fs.promises.readFile(selected);
    return {
      ok: true,
      canceled: false,
      name: path.basename(selected),
      size: bytes.byteLength,
      bytes,
    };
  } catch {
    return { ok: false, canceled: false, error: "读取 VRM 模型失败" };
  }
});

async function chooseVrcAvatarSource(mode = "file") {
  const directory = mode === "project";
  const result = await dialog.showOpenDialog(mainWindow, {
    title: directory ? "选择 VRChat Unity 项目" : "选择 VRChat Unity 包",
    defaultPath: safeKnownFolder("documents", "TIANGONG_DOCUMENTS_PATH"),
    properties: directory ? ["openDirectory"] : ["openFile"],
    filters: directory ? undefined : [{ name: "Unity package", extensions: ["unitypackage"] }],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: true, canceled: true };
  return preflightVrcAvatarSource(result.filePaths[0]);
}

handleTrusted("vrcImport:chooseSource", async (_event, mode = "file") => chooseVrcAvatarSource(mode));
handleTrusted("vrcImport:preflight", async (_event, sourcePath = "") => preflightVrcAvatarSource(sourcePath));

handleTrusted("dialog:chooseKnowledgeFiles", async (_event, payload = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "添加到知识库",
    defaultPath: normalizeDialogDefaultPath(payload),
    properties: ["openFile", "multiSelections"],
    filters: documentFileFilters(),
  });
  if (result.canceled || !result.filePaths.length) return { ok: true, canceled: true, paths: [] };
  return { ok: true, canceled: false, paths: result.filePaths };
});

handleTrusted("dialog:chooseChatFiles", async (_event, payload = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "上传到本轮对话",
    defaultPath: normalizeDialogDefaultPath(payload),
    properties: ["openFile", "multiSelections"],
    filters: chatFileFilters(),
  });
  if (result.canceled || !result.filePaths.length) return { ok: true, canceled: true, attachments: [], failed: [] };
  return uploadChatFilesToGateway({ ...payload, paths: result.filePaths });
});

handleTrusted("chatFiles:upload", async (_event, payload = {}) => uploadChatFilesToGateway(payload || {}));

handleTrusted("shell:openPath", async (_event, targetPath) => openRendererPath(targetPath));

handleTrusted("artifact:open", async (_event, payload = {}) => openVerifiedArtifact(payload));

handleTrusted("shell:openExternal", async (_event, url) => {
  const target = String(url || "").trim();
  let parsed;
  try { parsed = new URL(target); } catch { return { ok: false, error: "invalid_url" }; }
  if (
    target.length > 4096
    || !["https:", "http:", "mailto:"].includes(parsed.protocol)
    || parsed.username
    || parsed.password
  ) return { ok: false, error: "invalid_url" };
  try {
    await shell.openExternal(parsed.href);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
});

handleTrusted("file:saveAs", async (_event, payload = {}) => saveTargetAs(payload || {}));

handleTrusted("file:copyMedia", async (_event, payload = {}) => copyMediaToClipboard(payload || {}));

handleTrusted("clipboard:writeText", async (_event, text = "") => {
  const value = String(text || "");
  if (value.length > 2 * 1024 * 1024) return { ok: false, error: "clipboard_text_too_large" };
  clipboard.writeText(value);
  return { ok: true };
});

handleTrusted("logs:listDaily", async () => listDailyLogs());

handleTrusted("logs:openDaily", async (_event, payload = {}) => openDailyLog(payload?.date || payload));

handleTrusted("logs:deleteDaily", async (_event, payload = {}) => deleteDailyLog(payload?.date || payload));


handleTrusted("lifeLog:verify", async () => {
  const result = await totalGatewayJsonRequest("GET", "/api/v1/gateway/life-log/verify", {}, 30000);
  return result.payload || { ok: false, error: result.error || `gateway_${result.statusCode}` };
});

handleTrusted("soulBackup:create", async (_event, payload = {}) => {
  const passphrase = String(payload.passphrase || "");
  if (passphrase.length < 12 || passphrase.length > 4096) return { ok: false, error: "soul_backup_passphrase_invalid" };
  const destination = String(payload.destination || "").trim();
  const response = await totalGatewayJsonRequest("POST", "/api/v1/gateway/soul-backup/create", { passphrase, destination }, 15 * 60 * 1000);
  return response.payload || { ok: false, error: response.error || `gateway_${response.statusCode}` };
});

handleTrusted("soulBackup:verify", async (_event, payload = {}) => {
  const response = await totalGatewayJsonRequest("POST", "/api/v1/gateway/soul-backup/verify", {
    passphrase: String(payload.passphrase || ""),
    path: String(payload.path || ""),
  }, 15 * 60 * 1000);
  return response.payload || { ok: false, error: response.error || `gateway_${response.statusCode}` };
});

handleTrusted("soulBackup:restore", async (_event, payload = {}) => restoreSoulBackupOffline(payload || {}));

handleTrusted("update:status", async () => getSecureUpdater().status());
handleTrusted("update:check", async () => {
  try { return await getSecureUpdater().check(); }
  catch (error) { return getSecureUpdater().recordError(error); }
});
handleTrusted("update:download", async () => {
  try { return await getSecureUpdater().download(); }
  catch (error) { return getSecureUpdater().recordError(error); }
});
handleTrusted("update:apply", async () => {
  try {
    const result = await getSecureUpdater().apply();
    setImmediate(() => app.quit());
    return result;
  } catch (error) {
    return getSecureUpdater().recordError(error);
  }
});
