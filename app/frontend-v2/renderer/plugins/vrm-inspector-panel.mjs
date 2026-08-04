import {
  AVATAR_MODE_FLAG_KEY,
  AvatarRenderMode,
  sanitizeRenderMode,
} from "../avatar/avatar-service.mjs";

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function compact(value, fallback = "未读取") {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text || fallback;
}

const CAMERA_PREFS_KEY = "tiangong-v3-main-camera";
const LIGHTING_PREFS_KEY = "tiangong-v3-lighting";
const CAMERA_LIMITS = {
  focus: [-0.5, 0.5],
  height: [-0.5, 0.5],
  distance: [-2.0, 2.0],
  side: [-1.0, 1.0]
};
const LIGHTING_LIMITS = {
  key: [0.15, 3],
  angle: [-1.8, 1.8],
  ambient: [0.15, 2.4],
  exposure: [0.55, 1.9]
};
const CAMERA_DEFAULTS = { focus: 0, height: 0, distance: 0, side: 0 };
const LIGHTING_DEFAULTS = { key: 1, angle: 0, ambient: 1, exposure: 1 };
const VRM_BRIDGE_VERSION = 1;

export function shouldMountLegacyVrmPanel(storage = globalThis.localStorage) {
  try {
    return sanitizeRenderMode(storage?.getItem(AVATAR_MODE_FLAG_KEY)) === AvatarRenderMode.LEGACY_IFRAME;
  } catch {
    // 与 AvatarService/readRenderMode 保持同一缺省：存储缺失或不可用都按 direct。
    return sanitizeRenderMode(null) === AvatarRenderMode.LEGACY_IFRAME;
  }
}

function readPrefs(storageKey, defaults, limits) {
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey) || "{}");
    const out = { ...defaults };
    for (const key of Object.keys(defaults)) {
      const value = Number(raw[key]);
      const [min, max] = limits[key];
      out[key] = Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : defaults[key];
    }
    return out;
  } catch {
    return { ...defaults };
  }
}

function signed(value) {
  const number = Number(value) || 0;
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}`;
}

function controlRow({ group, key, label, min, max, step = 0.01, value = 0 }) {
  return `
    <label class="vrm-control-row">
      <span>${escHtml(label)}</span>
      <input data-vrm-control="${group}" data-key="${key}" type="range" min="${min}" max="${max}" step="${step}" value="${value}" />
      <strong data-vrm-value="${group}:${key}">${signed(value)}</strong>
    </label>
  `;
}

function readVrmState() {
  try {
    const raw = JSON.parse(localStorage.getItem("tiangong_v3_state") || "{}");
    const state = raw && typeof raw === "object" ? raw : {};
    const installedLabel = localStorage.getItem("tiangong-v3-installed-vrm-label") || "";
    const customLabel = localStorage.getItem("tiangong-v3-custom-vrm-label") || "";
    const diyName = localStorage.getItem("tiangong_name") || "";
    const source = localStorage.getItem("tiangong-v3-vrm-source") || "";
    return {
      ...state,
      source,
      name: diyName || state.name,
      vrmModelName: source === "custom" ? customLabel : installedLabel || state.vrmModelName
    };
  } catch {
    return {};
  }
}

function currentModelLabel(vrmState) {
  const item = Array.isArray(vrmState.vrmModels)
    ? vrmState.vrmModels.find((model) => model.id === vrmState.currentVRMModelId) || vrmState.vrmModels[0]
    : null;
  return item?.name || item?.fileName || vrmState.vrmModelName || "M9 Local 3D";
}

function sourceLabel(vrmState) {
  if (vrmState.source === "custom") return "自定义 VRM";
  if (vrmState.source === "installed") return "本地模型库";
  return "默认身体";
}

function callFrame(frame, fnName, ...args) {
  try {
    const target = frame?.contentWindow;
    const fn = target?.[fnName];
    if (typeof fn === "function") return fn(...args);
  } catch {
    // Cross-frame helpers are best-effort only.
  }
  return null;
}

function compactSpeechText(value) {
  return String(value || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[[^\]]+\]\([^)]+\)/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 1200);
}

function latestSpeakableAssistant(messages) {
  const list = Array.isArray(messages) ? messages : [];
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const item = list[index];
    if (item?.role !== "assistant" || item?.error) continue;
    const text = compactSpeechText(item.content);
    if (text) return { ...item, content: text };
  }
  return null;
}

function messageKey(item) {
  if (!item) return "";
  return `${item.at || ""}:${String(item.content || "").slice(0, 80)}`;
}

function currentThemeStyle(state) {
  const theme = String(state.snapshot().settings?.themeStyle || "").trim();
  return ["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"].includes(theme) ? theme : "ink_teal";
}

function bodyVoiceFrameSettings(settings = {}) {
  return {
    bodyVoiceReplyEnabled: Boolean(settings.bodyVoiceReplyEnabled),
    bodyVoicePreset: settings.bodyVoicePreset || "qiyuan_clear",
    bodyVoiceName: settings.bodyVoiceName || "",
      bodyVoiceCustomName: settings.bodyVoiceCustomName || "",
      bodyVoiceCustomPath: settings.bodyVoiceCustomPath || "",
      bodyVoiceOutputMode: settings.bodyVoiceOutputMode || "auto",
      bodyVoiceNativeId: settings.bodyVoiceNativeId || "",
      bodyVoiceSampleConsent: Boolean(settings.bodyVoiceSampleConsent),
      bodyVoiceLang: settings.bodyVoiceLang || "zh-CN",
    bodyVoiceRate: Number.isFinite(Number(settings.bodyVoiceRate)) ? Number(settings.bodyVoiceRate) : 1,
    bodyVoicePitch: Number.isFinite(Number(settings.bodyVoicePitch)) ? Number(settings.bodyVoicePitch) : 1.04,
    bodyVoiceVolume: Number.isFinite(Number(settings.bodyVoiceVolume)) ? Number(settings.bodyVoiceVolume) : 1,
    bodyVoicePresets: Array.isArray(settings.bodyVoicePresets) ? settings.bodyVoicePresets : []
  };
}

function viewportMarkup({ mode, name, themeStyle, title }) {
  const src = `../桌面宠物.html?embed=vrm-panel&theme=${encodeURIComponent(themeStyle)}`;
  return `
    <div class="vrm-viewport-card" data-vrm-viewport="${mode}" aria-label="${escHtml(name)} ${escHtml(title)}">
      <iframe data-vrm-frame="${mode}" title="${escHtml(name)} ${escHtml(title)}" src="${escHtml(src)}"></iframe>
    </div>
  `;
}

function controlsMarkup(cameraPrefs, lightPrefs) {
  return `
    <div class="vrm-command-row">
      <button type="button" data-vrm-action="camera">主镜头</button>
      <button type="button" data-vrm-action="choose">选择模型</button>
    </div>
    <div class="vrm-import-status" data-vrm-import-status role="status" aria-live="polite"></div>

    <section class="vrm-side-section">
      <h3>镜头调整</h3>
      <div class="vrm-control-list">
        ${controlRow({ group: "camera", key: "focus", label: "核心", min: CAMERA_LIMITS.focus[0], max: CAMERA_LIMITS.focus[1], value: cameraPrefs.focus })}
        ${controlRow({ group: "camera", key: "height", label: "高低", min: CAMERA_LIMITS.height[0], max: CAMERA_LIMITS.height[1], value: cameraPrefs.height })}
        ${controlRow({ group: "camera", key: "distance", label: "远近", min: CAMERA_LIMITS.distance[0], max: CAMERA_LIMITS.distance[1], value: cameraPrefs.distance })}
        ${controlRow({ group: "camera", key: "side", label: "左右", min: CAMERA_LIMITS.side[0], max: CAMERA_LIMITS.side[1], value: cameraPrefs.side })}
      </div>
    </section>

    <section class="vrm-side-section">
      <h3>灯光调整</h3>
      <div class="vrm-control-list">
        ${controlRow({ group: "lighting", key: "key", label: "主光", min: LIGHTING_LIMITS.key[0], max: LIGHTING_LIMITS.key[1], value: lightPrefs.key })}
        ${controlRow({ group: "lighting", key: "angle", label: "角度", min: LIGHTING_LIMITS.angle[0], max: LIGHTING_LIMITS.angle[1], value: lightPrefs.angle })}
        ${controlRow({ group: "lighting", key: "ambient", label: "柔光", min: LIGHTING_LIMITS.ambient[0], max: LIGHTING_LIMITS.ambient[1], value: lightPrefs.ambient })}
        ${controlRow({ group: "lighting", key: "exposure", label: "曝光", min: LIGHTING_LIMITS.exposure[0], max: LIGHTING_LIMITS.exposure[1], value: lightPrefs.exposure })}
      </div>
    </section>
  `;
}

function metaMarkup() {
  return `
    <section class="vrm-side-section vrm-readonly-meta">
      <h3>身体映射</h3>
      <div class="vrm-meta-list">
        <div class="vrm-meta-row"><span>角色</span><strong data-vrm-meta="name">起源</strong></div>
        <div class="vrm-meta-row"><span>模型</span><strong data-vrm-meta="model">M9 Local 3D</strong></div>
        <div class="vrm-meta-row"><span>来源</span><strong data-vrm-meta="source">默认身体</strong></div>
      </div>
    </section>
  `;
}

export const vrmInspectorPanelPlugin = {
  id: "vrm-inspector-panel",
  slot: "inspector",
  order: 5,
  mount({ slot, state }) {
    // P5 §24：direct 模式下本 legacy 面板让位给 avatar-panel（本阶段不删，P7 才清理 legacy）。
    if (!shouldMountLegacyVrmPanel()) return;
    const vrmState = readVrmState();
    const snap = state.snapshot();
    const name = compact(vrmState.name || snap.settings?.personaName, "起源");
    const themeStyle = currentThemeStyle(state);
    const cameraPrefs = readPrefs(CAMERA_PREFS_KEY, CAMERA_DEFAULTS, CAMERA_LIMITS);
    const lightPrefs = readPrefs(LIGHTING_PREFS_KEY, LIGHTING_DEFAULTS, LIGHTING_LIMITS);

    slot.insertAdjacentHTML("beforeend", `
      <section class="vrm-inspector-panel vrm-home-panel" data-vrm-panel="chat">
        <header class="vrm-panel-header">
          <div class="vrm-panel-title">
            <span>当前身体</span>
            <h2 data-vrm-role-name>${escHtml(name)}</h2>
          </div>
          <div data-vrm-live-pill class="vrm-live-pill">展示</div>
        </header>
        ${viewportMarkup({ mode: "chat", name, themeStyle, title: "身体展示" })}
        ${metaMarkup()}
      </section>

      <section class="vrm-inspector-panel vrm-body-panel" data-vrm-panel="body">
        <div class="vrm-top-fixed">
        <header class="vrm-panel-header">
          <div class="vrm-panel-title">
            <span>身体模型</span>
            <h2 data-vrm-role-name>${escHtml(name)}</h2>
          </div>
          <div data-vrm-live-pill class="vrm-live-pill">${escHtml(currentModelLabel(vrmState))}</div>
        </header>
        ${viewportMarkup({ mode: "body", name, themeStyle, title: "角色预览" })}
        </div>
        <div class="vrm-scroll-divider"></div>
        <div class="vrm-scroll-area">
        ${controlsMarkup(cameraPrefs, lightPrefs)}
        </div>
      </section>
    `);

    const homePanel = slot.querySelector('[data-vrm-panel="chat"]');
    const bodyPanel = slot.querySelector('[data-vrm-panel="body"]');
    const homeFrame = homePanel?.querySelector('[data-vrm-frame="chat"]');
    const bodyFrame = bodyPanel?.querySelector('[data-vrm-frame="body"]');
    const frames = [homeFrame, bodyFrame].filter(Boolean);
    const speechBridgeStartedAt = Date.now();
    let spokenAssistantKey = messageKey(latestSpeakableAssistant(state.snapshot().messages));
    let hotBodyVoiceSettings = null;
    let savedVrmRevision = 0;
    const frameVrmRevision = new WeakMap();
    let pendingVrmImportId = "";

    function primeSpeech() {
      callFrame(homeFrame, "primeSpeechSynthesis");
    }

    function syncFrameTheme() {
      const theme = currentThemeStyle(state);
      for (const frame of frames) {
        callFrame(frame, "setEmbeddedTheme", theme);
        try {
          frame.contentWindow?.postMessage({ type: "tiangong-theme", themeStyle: theme }, "*");
        } catch {
          // The iframe may still be loading.
        }
      }
    }

    function syncFrameVoice(settingsOverride = null) {
      const settings = bodyVoiceFrameSettings(settingsOverride || hotBodyVoiceSettings || state.snapshot().settings);
      for (const frame of frames) {
        callFrame(frame, "setBodyVoiceSettings", settings);
        try {
          frame.contentWindow?.postMessage({ type: "tiangong-body-voice-settings", settings }, "*");
        } catch {
          // The iframe may still be loading.
        }
      }
    }

    function markFrameVrmCurrent(frame, revision = savedVrmRevision) {
      if (frame) frameVrmRevision.set(frame, revision);
    }

    function reloadSavedVrm(frame, attempt = 0, revision = savedVrmRevision) {
      const result = callFrame(frame, "reloadSavedVRM");
      if (result === null && attempt < 10) {
        window.setTimeout(() => reloadSavedVrm(frame, attempt + 1, revision), 250);
        return;
      }
      if (result && typeof result.then === "function") {
        result.then(() => markFrameVrmCurrent(frame, revision)).catch(() => {});
      } else if (result !== null) {
        markFrameVrmCurrent(frame, revision);
      }
    }

    function syncSavedVrmToFrames(sourceWindow = null) {
      savedVrmRevision += 1;
      for (const frame of frames) {
        if (sourceWindow && frame.contentWindow === sourceWindow) {
          markFrameVrmCurrent(frame, savedVrmRevision);
          continue;
        }
        reloadSavedVrm(frame, 0, savedVrmRevision);
      }
    }

    function applyFramePrefs(frame) {
      const latestCamera = readPrefs(CAMERA_PREFS_KEY, CAMERA_DEFAULTS, CAMERA_LIMITS);
      const latestLight = readPrefs(LIGHTING_PREFS_KEY, LIGHTING_DEFAULTS, LIGHTING_LIMITS);
      for (const [key, value] of Object.entries(latestCamera)) callFrame(frame, "setMainCameraSetting", key, value);
      for (const [key, value] of Object.entries(latestLight)) callFrame(frame, "setLightingSetting", key, value);
    }

    function applyFramePrefsToAll() {
      for (const frame of frames) applyFramePrefs(frame);
    }

    function ensureFrameMapped(frame) {
      if (!frame) return;
      applyFramePrefs(frame);
      if ((frameVrmRevision.get(frame) || 0) < savedVrmRevision) {
        reloadSavedVrm(frame, 0, savedVrmRevision);
      }
    }

    function callFrames(fnName, ...args) {
      for (const frame of frames) callFrame(frame, fnName, ...args);
    }

    function setVrmImportStatus(message = "", stateName = "") {
      const status = bodyPanel?.querySelector("[data-vrm-import-status]");
      const button = bodyPanel?.querySelector('[data-vrm-action="choose"]');
      if (status) {
        status.textContent = String(message || "");
        status.dataset.state = stateName;
      }
      if (button) {
        button.disabled = stateName === "pending";
        button.textContent = stateName === "pending" ? "正在导入…" : "选择模型";
      }
    }

    function postVrmCommand(frame, command, payload = {}, transfer = []) {
      const target = frame?.contentWindow;
      if (!target) return false;
      try {
        target.postMessage({
          type: "tiangong-vrm-command",
          version: VRM_BRIDGE_VERSION,
          command,
          ...payload
        }, "*", transfer);
        return true;
      } catch {
        return false;
      }
    }

    function transferableVrmBuffer(value) {
      if (value instanceof ArrayBuffer) return value.slice(0);
      if (ArrayBuffer.isView(value)) {
        return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
      }
      if (Array.isArray(value?.data)) return Uint8Array.from(value.data).buffer;
      return null;
    }

    async function chooseVrmModel() {
      const bridge = window.tiangongDesktop;
      if (!bridge || typeof bridge.chooseVrmModel !== "function") {
        const opened = postVrmCommand(bodyFrame, "open-file-picker");
        setVrmImportStatus(opened ? "请选择本地 .vrm 文件" : "模型预览尚未就绪，请稍后重试", opened ? "pending" : "error");
        return;
      }

      setVrmImportStatus("请选择本地 .vrm 文件", "pending");
      let report;
      try {
        report = await bridge.chooseVrmModel();
      } catch (error) {
        setVrmImportStatus(error?.message || "模型选择器打开失败", "error");
        return;
      }
      if (report?.canceled) {
        setVrmImportStatus("已取消选择", "");
        return;
      }
      if (!report?.ok) {
        setVrmImportStatus(report?.error || "模型文件读取失败", "error");
        return;
      }

      const buffer = transferableVrmBuffer(report.bytes);
      if (!buffer || buffer.byteLength <= 0) {
        setVrmImportStatus("模型文件为空或传输失败", "error");
        return;
      }
      pendingVrmImportId = `vrm-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const sent = postVrmCommand(bodyFrame, "import-model", {
        requestId: pendingVrmImportId,
        name: report.name || "自定义.vrm",
        size: Number(report.size || buffer.byteLength),
        buffer
      }, [buffer]);
      if (!sent) {
        pendingVrmImportId = "";
        setVrmImportStatus("模型预览尚未就绪，请稍后重试", "error");
        return;
      }
      setVrmImportStatus(`正在校验并载入 ${report.name || "VRM 模型"}…`, "pending");
    }

    function requestSpeech(text, attempt = 0) {
      const result = callFrame(homeFrame, "speakText", text);
      if (result === null && attempt < 10) {
        window.setTimeout(() => requestSpeech(text, attempt + 1), 250);
      }
    }

    function speakLatestAssistant(messages) {
      const item = latestSpeakableAssistant(messages);
      const key = messageKey(item);
      if (!item || !key || key === spokenAssistantKey) return;
      if (Number(item.at || 0) < speechBridgeStartedAt - 500) {
        spokenAssistantKey = key;
        return;
      }
      spokenAssistantKey = key;
      primeSpeech();
      syncFrameVoice();
      requestSpeech(item.content);
    }

    function syncControls() {
      if (!bodyPanel) return;
      const latestCamera = readPrefs(CAMERA_PREFS_KEY, CAMERA_DEFAULTS, CAMERA_LIMITS);
      const latestLight = readPrefs(LIGHTING_PREFS_KEY, LIGHTING_DEFAULTS, LIGHTING_LIMITS);
      const groups = { camera: latestCamera, lighting: latestLight };
      for (const input of bodyPanel.querySelectorAll("[data-vrm-control]")) {
        const group = input.dataset.vrmControl;
        const key = input.dataset.key;
        const value = groups[group]?.[key] ?? 0;
        input.value = String(value);
        const label = bodyPanel.querySelector(`[data-vrm-value="${group}:${key}"]`);
        if (label) label.textContent = signed(value);
      }
    }

    function render() {
      const latestState = readVrmState();
      const latestSnap = state.snapshot();
      const nextName = compact(latestState.name || latestSnap.settings?.personaName, "起源");
      const nextModel = compact(currentModelLabel(latestState), "M9 Local 3D");
      const nextSource = sourceLabel(latestState);

      for (const panel of [homePanel, bodyPanel].filter(Boolean)) {
        const roleName = panel.querySelector('[data-vrm-role-name]');
        const livePill = panel.querySelector('[data-vrm-live-pill]');
        const metaName = panel.querySelector('[data-vrm-meta="name"]');
        const metaModel = panel.querySelector('[data-vrm-meta="model"]');
        const metaSource = panel.querySelector('[data-vrm-meta="source"]');
        if (roleName) roleName.textContent = nextName;
        if (livePill) livePill.textContent = panel === bodyPanel ? nextModel : "展示";
        if (metaName) metaName.textContent = nextName;
        if (metaModel) metaModel.textContent = nextModel;
        if (metaSource) metaSource.textContent = nextSource;
      }

      for (const frame of frames) {
        const title = frame.dataset.vrmFrame === "body" ? "角色预览" : "身体展示";
        const viewport = frame.closest(".vrm-viewport-card");
        viewport?.setAttribute("aria-label", `${nextName} ${title}`);
        frame.setAttribute("title", `${nextName} ${title}`);
      }

      syncControls();
      syncFrameTheme();
      syncFrameVoice();
      applyFramePrefsToAll();
    }

    function renderPage(page) {
      if (homePanel) homePanel.hidden = page !== "chat";
      if (bodyPanel) bodyPanel.hidden = page !== "body";
      ensureFrameMapped(page === "chat" ? homeFrame : bodyFrame);
    }

    bodyPanel.addEventListener("input", (event) => {
      const input = event.target.closest("[data-vrm-control]");
      if (!input) return;
      const group = input.dataset.vrmControl;
      const key = input.dataset.key;
      const value = input.value;
      const label = bodyPanel.querySelector(`[data-vrm-value="${group}:${key}"]`);
      if (label) label.textContent = signed(value);
      if (group === "camera") callFrames("setMainCameraSetting", key, value);
      if (group === "lighting") callFrames("setLightingSetting", key, value);
    });

    bodyPanel.addEventListener("click", (event) => {
      const button = event.target.closest("[data-vrm-action]");
      if (!button) return;
      if (button.dataset.vrmAction === "camera") callFrames("restoreMainCamera");
      if (button.dataset.vrmAction === "choose") {
        chooseVrmModel();
      }
    });

    for (const frame of frames) {
      frame.addEventListener("load", () => {
        if (frame === homeFrame) primeSpeech();
        applyFramePrefs(frame);
        syncControls();
        syncFrameTheme();
        syncFrameVoice();
        markFrameVrmCurrent(frame);
      });
    }

    window.addEventListener("pointerdown", primeSpeech, { capture: true, passive: true });
    window.addEventListener("keydown", primeSpeech, { capture: true });
    state.on("settings", render);
    // Conversation panel is the sole reply-audio owner.  The frame only receives settings,
    // preventing the previous duplicate browser speech race.
    window.addEventListener("storage", render);
    window.addEventListener("tiangong-body-hot-preview", (event) => {
      hotBodyVoiceSettings = event.detail || {};
      syncFrameVoice(hotBodyVoiceSettings);
    });
    let lastBiaoxian = null;
    window.addEventListener("tiangong-biaoxian", (event) => {
      const data = event.detail;
      if (!data || typeof data !== "object") return;
      lastBiaoxian = data;
      for (const frame of frames) {
        try { frame.contentWindow?.postMessage({ type: "tiangong-biaoxian", ...data }, "*"); } catch {}
      }
    });
    window.addEventListener("message", (event) => {
      // P2-18: only the owned VRM iframes may drive the bridge.  Any other
      // sender must be ignored, including the ready replay path.
      const trustedFrame = event.source === bodyFrame?.contentWindow
        || frames.some((frame) => frame.contentWindow === event.source);
      if (event.data?.type === "vrm.ready" || event.data?.type === "tiangong-vrm-ready") {
        if (!trustedFrame) return;
        // iframe 就绪后重放上次动作
        if (lastBiaoxian && event.source) {
          try { event.source.postMessage({ type: "tiangong-biaoxian", ...lastBiaoxian }, "*"); } catch {}
        }
        return;
      }
      if (event.data?.type === "tiangong-vrm-import-result") {
        if (event.source !== bodyFrame?.contentWindow) return;
        if (pendingVrmImportId && event.data.requestId && event.data.requestId !== pendingVrmImportId) return;
        if (event.data.ok) {
          pendingVrmImportId = "";
          setVrmImportStatus(`已导入 ${event.data.label || "VRM 模型"}`, "success");
          render();
        } else {
          pendingVrmImportId = "";
          setVrmImportStatus(event.data.error || "VRM 模型导入失败", "error");
        }
        return;
      }
      if (event.data?.type !== "tiangong-vrm-model-changed") return;
      if (!trustedFrame) return;
      render();
      syncSavedVrmToFrames(event.source || null);
      setTimeout(render, 300);
    });
    // iframe onload 兜底重放
    for (const frame of frames) {
      frame.addEventListener("load", () => {
        if (lastBiaoxian) {
          try { frame.contentWindow?.postMessage({ type: "tiangong-biaoxian", ...lastBiaoxian }, "*"); } catch {}
        }
      });
    }
    state.on("page", renderPage);
    renderPage(state.snapshot().activePage);
    render();
  }
};
