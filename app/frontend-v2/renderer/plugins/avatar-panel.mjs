// P5 §24 direct 模式 Avatar 面板：右侧"当前身体"区域挂 RenderSurface。
//
// 与 vrm-inspector-panel 并存：本面板只在 direct 模式渲染（本地诊断 flag
// tiangong.avatar.renderMode === "direct"）；legacy-iframe 模式由 vrm-inspector-panel 承担。
//
// 纪律：
//   - 不直接触碰 THREE 对象/VRM 实例（§7.2 禁止项），只发语义命令（service/runtime 公共接口）。
//   - 展示状态一律读 AvatarStore 只读投影（runtime→store→面板单向链）。
//   - Surface 页面切换走 service.rehostSurface（§14.3 同一 lease 迁移宿主，不重解析模型）。
//   - TTS 事件经 speech-event-forwarder window 桥订阅（§17 单一所有者转发，不播放音频）。
//   - 主题切换经 theme-presentation 只调 presentation，不重载模型。

import { getService, hasService } from "../avatar/service-registry.mjs";
import { AVATAR_MODE_FLAG_KEY, AvatarRenderMode, sanitizeRenderMode } from "../avatar/avatar-service.mjs";
import { createAvatarStore } from "../avatar/avatar-store.mjs";
import { createThemePresentationSync, sanitizeThemeId } from "../avatar/theme-presentation.mjs";
import { createSpeechEventForwarder } from "../avatar/speech-event-forwarder.mjs";
import { createBodyCommandScheduler } from "../avatar/body-command-scheduler.mjs";
import { createBiaoxianAdapter } from "../avatar/body-performance-adapter.mjs";
import { createLifecycleScope } from "../avatar/lifecycle.mjs";
import {
  AVATAR_CAMERA_DEFAULTS,
  AVATAR_CAMERA_LIMITS,
  AVATAR_LIGHTING_DEFAULTS,
  AVATAR_LIGHTING_LIMITS,
  normalizeAvatarPresentation,
} from "../avatar/presentation-settings.mjs";

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function signed(value, forceSign = true) {
  const number = Number(value) || 0;
  const prefix = forceSign && number >= 0 ? "+" : "";
  return `${prefix}${number.toFixed(2)}`;
}

function controlRow({ group, key, label, limits, value, signedValue = true }) {
  return `
    <label class="vrm-control-row">
      <span>${escHtml(label)}</span>
      <input data-avatar-presentation="${group}" data-key="${key}" type="range"
        min="${limits[0]}" max="${limits[1]}" step="0.01" value="${value}" />
      <strong data-avatar-presentation-value="${group}:${key}">${signed(value, signedValue)}</strong>
    </label>
  `;
}

function presentationControlsMarkup(presentation) {
  const { camera, lighting } = presentation;
  return `
    <section class="vrm-side-section">
      <h3>主镜头调整</h3>
      <div class="vrm-control-list">
        ${controlRow({ group: "camera", key: "focus", label: "核心", limits: AVATAR_CAMERA_LIMITS.focus, value: camera.focus })}
        ${controlRow({ group: "camera", key: "height", label: "高低", limits: AVATAR_CAMERA_LIMITS.height, value: camera.height })}
        ${controlRow({ group: "camera", key: "distance", label: "远近", limits: AVATAR_CAMERA_LIMITS.distance, value: camera.distance })}
        ${controlRow({ group: "camera", key: "side", label: "左右", limits: AVATAR_CAMERA_LIMITS.side, value: camera.side })}
      </div>
    </section>
    <section class="vrm-side-section">
      <h3>灯光调整</h3>
      <div class="vrm-control-list">
        ${controlRow({ group: "lighting", key: "key", label: "主光", limits: AVATAR_LIGHTING_LIMITS.key, value: lighting.key, signedValue: false })}
        ${controlRow({ group: "lighting", key: "angle", label: "角度", limits: AVATAR_LIGHTING_LIMITS.angle, value: lighting.angle })}
        ${controlRow({ group: "lighting", key: "ambient", label: "柔光", limits: AVATAR_LIGHTING_LIMITS.ambient, value: lighting.ambient, signedValue: false })}
        ${controlRow({ group: "lighting", key: "exposure", label: "曝光", limits: AVATAR_LIGHTING_LIMITS.exposure, value: lighting.exposure, signedValue: false })}
      </div>
    </section>
    <div class="vrm-command-row">
      <button type="button" data-avatar-presentation-action="reset">恢复默认</button>
      <button type="button" data-avatar-presentation-action="save">保存显示设置</button>
    </div>
    <div class="vrm-import-status" data-avatar-presentation-status role="status" aria-live="polite"></div>
  `;
}

function readRenderMode() {
  try {
    return sanitizeRenderMode(localStorage.getItem(AVATAR_MODE_FLAG_KEY));
  } catch {
    return sanitizeRenderMode(null);
  }
}

// 内置模型目录：默认 fetch app/assets/avatar/builtin-models.json；失败回退空表。
async function loadBuiltinCatalog() {
  // webSecurity 下 fetch(file://) 被禁：内置清单走主进程桥（与 avatar-boot 同一来源）。
  try {
    const doc = await window.tiangongDesktop?.avatarAsset?.getBuiltinManifest?.();
    return Array.isArray(doc?.models) ? doc.models : [];
  } catch {
    return [];
  }
}

export function mergeAvatarCatalog(...catalogs) {
  const merged = new Map();
  for (const catalog of catalogs) {
    for (const model of Array.isArray(catalog) ? catalog : []) {
      const id =
        typeof model?.id === "string" && model.id.length > 0
          ? model.id
          : typeof model?.modelId === "string" && model.modelId.length > 0
            ? model.modelId
            : null;
      if (id === null || merged.has(id)) continue;
      merged.set(id, Object.freeze({ ...model, id }));
    }
  }
  return Object.freeze([...merged.values()]);
}

export function describeAvatarProjection(projection = {}) {
  const currentModel =
    projection?.currentModel && typeof projection.currentModel === "object"
      ? projection.currentModel
      : null;
  const currentModelId =
    typeof currentModel?.modelId === "string" && currentModel.modelId.length > 0
      ? currentModel.modelId
      : null;
  const lastCommittedModelId =
    typeof projection?.lastCommittedModelId === "string"
      && projection.lastCommittedModelId.length > 0
      ? projection.lastCommittedModelId
      : null;
  const loading = projection?.pending !== null && projection?.pending !== undefined;
  const lastRequestedModelId =
    loading
      && typeof projection?.lastRequestedModelId === "string"
      && projection.lastRequestedModelId.length > 0
      ? projection.lastRequestedModelId
      : null;
  const selectedModelId = lastRequestedModelId ?? currentModelId ?? lastCommittedModelId;
  const currentLabel =
    typeof currentModel?.label === "string" && currentModel.label.trim().length > 0
      ? currentModel.label.trim()
      : null;
  const hasRenderableModel = currentModelId !== null;
  const hasRecoverableModel = lastCommittedModelId !== null;

  let emptyTitle = "尚未添加身体模型";
  let emptyHint = "请在“身体”页导入本机 VRM 模型。";
  let stateText = "等待导入";
  if (loading) {
    emptyTitle = "正在加载身体模型";
    emptyHint = "模型正在校验并准备显示，请稍候。";
    stateText = "正在加载";
  } else if (!hasRenderableModel && hasRecoverableModel) {
    emptyTitle = "身体模型暂不可见";
    emptyHint = "模型正在等待运行时恢复。";
    stateText = "等待恢复";
  } else if (hasRenderableModel) {
    stateText = projection?.runtimeState ?? "运行中";
  }

  return Object.freeze({
    selectedModelId,
    modelText: currentLabel ?? selectedModelId ?? "等待导入",
    stateText,
    emptyVisible: !hasRenderableModel,
    emptyTitle,
    emptyHint,
  });
}

export function describeAvatarImportResult(result = {}) {
  if (result?.status === "cancelled" || result?.code === "user_cancelled") {
    return Object.freeze({ message: "已取消选择", state: "" });
  }
  if (result?.ok === true) {
    // P2-06: "import succeeded" is not the same as "renderer loaded the
    // model".  Keep the confirmation pending until the live projection
    // reports an actual runtime state.
    return Object.freeze({
      message: `已导入 ${result.modelId || "身体模型"}（等待渲染确认）`,
      state: "success",
    });
  }
  return Object.freeze({
    message:
      typeof result?.code === "string" && result.code.length > 0
        ? result.code
        : "导入失败",
    state: "error",
  });
}

function hostForElement(element, id) {
  return {
    id,
    element, // P6a §14.3：rehost DOM 迁移目标（引擎 attachSurface 对同一 canvas 做 move）
    isVisible: () => {
      if (!element || element.hidden) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    },
    getViewport: () => {
      const rect = element?.getBoundingClientRect?.();
      return {
        width: Math.max(0, Math.round(rect?.width ?? 0)),
        height: Math.max(0, Math.round(rect?.height ?? 0)),
        dpr: window.devicePixelRatio || 1,
      };
    },
  };
}

export const avatarPanelPlugin = {
  id: "avatar-panel",
  slot: "inspector",
  order: 6,
  async mount({ slot, state, actions }) {
    // §24：非 direct 模式本面板不渲染（legacy 由 vrm-inspector-panel 承担，P7 才清理）。
    if (readRenderMode() !== AvatarRenderMode.DIRECT) return undefined;

    const lifecycle = createLifecycleScope({});
    const nowMonotonic = () => performance.now();
    const initialPresentation = normalizeAvatarPresentation({
      camera: state.snapshot().settings?.bodyCamera,
      lighting: state.snapshot().settings?.bodyLighting,
    });

    slot.insertAdjacentHTML("beforeend", `
      <section class="vrm-inspector-panel avatar-direct-panel vrm-home-panel" data-avatar-panel="chat">
        <header class="vrm-panel-header">
          <div class="vrm-panel-title">
            <span>当前身体</span>
            <h2 data-avatar-role-name>—</h2>
          </div>
          <div data-avatar-live-pill class="vrm-live-pill">direct</div>
        </header>
        <div class="vrm-viewport-card" data-avatar-surface-host="chat" aria-label="身体展示">
          <div class="vrm-empty-state" data-avatar-empty-state>
            <strong data-avatar-empty-title>尚未添加身体模型</strong>
            <span data-avatar-empty-hint>请在“身体”页导入本机 VRM 模型。</span>
          </div>
        </div>
        <section class="vrm-side-section vrm-readonly-meta">
          <h3>身体映射</h3>
          <div class="vrm-meta-list">
            <div class="vrm-meta-row"><span>模型</span><strong data-avatar-meta="model">—</strong></div>
            <div class="vrm-meta-row"><span>状态</span><strong data-avatar-meta="state">—</strong></div>
          </div>
        </section>
      </section>

      <section class="vrm-inspector-panel avatar-direct-panel vrm-body-panel" data-avatar-panel="body">
        <div class="vrm-top-fixed">
        <header class="vrm-panel-header">
          <div class="vrm-panel-title">
            <span>身体模型</span>
            <h2 data-avatar-role-name>—</h2>
          </div>
          <div data-avatar-live-pill class="vrm-live-pill">direct</div>
        </header>
        <div class="vrm-viewport-card" data-avatar-surface-host="body" aria-label="角色预览">
          <div class="vrm-empty-state" data-avatar-empty-state>
            <strong data-avatar-empty-title>尚未添加身体模型</strong>
            <span data-avatar-empty-hint>使用下方按钮导入本机 VRM 模型。</span>
          </div>
        </div>
        </div>
        <div class="vrm-scroll-divider"></div>
        <div class="vrm-scroll-area">
        <div class="vrm-command-row">
          <select data-avatar-model-select aria-label="选择身体模型"></select>
          <button type="button" data-avatar-action="import">导入模型</button>
        </div>
        <div class="vrm-import-status" data-avatar-import-status role="status" aria-live="polite"></div>
        ${presentationControlsMarkup(initialPresentation)}
        </div>
      </section>
    `);

    const chatPanel = slot.querySelector('[data-avatar-panel="chat"]');
    const bodyPanel = slot.querySelector('[data-avatar-panel="body"]');
    const chatHost = slot.querySelector('[data-avatar-surface-host="chat"]');
    const bodyHost = slot.querySelector('[data-avatar-surface-host="body"]');
    const modelSelect = bodyPanel?.querySelector("[data-avatar-model-select]");
    const importStatus = bodyPanel?.querySelector("[data-avatar-import-status]");
    const presentationStatus = bodyPanel?.querySelector("[data-avatar-presentation-status]");

    if (!hasService("avatar-service")) {
      if (importStatus) importStatus.textContent = "AvatarService 未启动（direct 模式需要服务注册）";
      return () => lifecycle.unmount();
    }
    const avatarService = getService("avatar-service");

    // ── 状态链：runtime → store → 面板（只读投影 + 订阅）────────────
    const store = createAvatarStore({ nowMonotonic, mode: avatarService.getMode() });
    store.bindRuntime(avatarService.getRuntime());
    lifecycle.trackCleanup(() => store.dispose());

    const themeSync = createThemePresentationSync({ getRuntime: () => avatarService.getRuntime() });
    let presentationDraft = initialPresentation;
    let presentationDirty = false;
    let presentationSaving = false;

    function setPresentationStatus(message = "", stateName = "") {
      if (!presentationStatus) return;
      presentationStatus.textContent = String(message || "");
      presentationStatus.dataset.state = stateName;
    }

    function applyPresentation(presentation) {
      const normalized = normalizeAvatarPresentation(presentation);
      avatarService.getRuntime()?.setPresentation({
        camera: normalized.camera,
        lighting: normalized.lighting,
      });
      return normalized;
    }

    function syncPresentationControls() {
      for (const input of bodyPanel?.querySelectorAll("[data-avatar-presentation]") || []) {
        const group = input.dataset.avatarPresentation;
        const key = input.dataset.key;
        const value = presentationDraft[group]?.[key];
        if (!Number.isFinite(Number(value))) continue;
        input.value = String(value);
        const output = bodyPanel.querySelector(
          `[data-avatar-presentation-value="${group}:${key}"]`,
        );
        if (output) {
          const forceSign = group === "camera" || key === "angle";
          output.textContent = signed(value, forceSign);
        }
      }
    }

    function presentationFromSettings(settings = {}) {
      return normalizeAvatarPresentation({
        camera: settings.bodyCamera,
        lighting: settings.bodyLighting,
      });
    }

    // ── 动作链：biaoxian window 事件 → adapter → scheduler → runtime ──
    const scheduler = createBodyCommandScheduler({
      nowMonotonic,
      sink: { applyPerformance: (wire) => avatarService.getRuntime()?.applyPerformance(wire) },
      onModelLoad: (wire) => {
        if (typeof wire.modelId === "string") avatarService.getRuntime()?.selectModel(wire.modelId);
      },
    });
    const biaoxianAdapter = createBiaoxianAdapter({
      getBackendInstanceId: () => window.tiangongBackendInstanceId ?? null,
      getSessionEpoch: () => window.tiangongSessionEpoch ?? `page-${document.title}`,
    });
    const onBiaoxian = (event) => {
      try {
        scheduler.submit(biaoxianAdapter.wireFromBiaoxian(event.detail, { turnId: event.detail?.turnId }));
        scheduler.pump();
      } catch {
        // 单条动作异常不阻断聊天链
      }
    };
    lifecycle.trackDomListener(window, "tiangong-biaoxian", onBiaoxian);

    // ── TTS：§17 单一所有者转发的订阅桥（本面板不播放任何音频）──────
    const speechForwarder = createSpeechEventForwarder({
      nowMonotonic,
      submit: (wire) => { scheduler.submit(wire); scheduler.pump(); },
    });
    lifecycle.trackCleanup(speechForwarder.attachWindowBridge({ target: window, ownerId: "tts-owner" }));

    // ── Surface：挂当前页宿主；页面切换 rehost（§14.3 不重解析模型）──
    let activePage = state.snapshot().activePage === "body" ? "body" : "chat";
    const hostOf = (page) => hostForElement(page === "chat" ? chatHost : bodyHost, `avatar-${page}`);
    try {
      avatarService.attachSurface(hostOf(activePage), activePage);
    } catch (error) {
      if (importStatus) importStatus.textContent = `Surface 挂接失败：${error.message}`;
    }
    lifecycle.trackCleanup(() => {
      try { avatarService.detachSurface(); } catch { /* 服务已切换/释放 */ }
    });

    function renderPage(page) {
      const next = page === "body" ? "body" : "chat";
      if (chatPanel) chatPanel.hidden = next !== "chat";
      if (bodyPanel) bodyPanel.hidden = next !== "body";
      if (next !== activePage) {
        activePage = next;
        try {
          avatarService.rehostSurface(hostOf(next)); // 同一 lease 迁移，不重解析模型
        } catch {
          try { avatarService.attachSurface(hostOf(next), next); } catch { /* 下帧重试由诊断观测 */ }
        }
      }
    }
    lifecycle.trackSubscription(state.on("page", renderPage));
    renderPage(activePage);

    // 尺寸/DPI 变化 → surface viewport（探针与引擎 resize 依据）
    const surfaceController = avatarService.getSurfaceController();
    if (surfaceController) {
      const pushViewport = () => {
        const viewport = hostOf(activePage).getViewport();
        try { surfaceController.updateViewport(viewport); } catch { /* 无活动 lease 时忽略 */ }
      };
      if (chatHost) lifecycle.trackResizeObserver(chatHost, pushViewport);
      if (bodyHost) lifecycle.trackResizeObserver(bodyHost, pushViewport);
    }

    // ── 主题：settings.themeStyle → presentation（不重载模型）────────
    const applyPresentationFromState = () => {
      const settings = state.snapshot().settings ?? {};
      themeSync.applyTheme(sanitizeThemeId(settings.themeStyle));
      if (!presentationDirty && !presentationSaving) {
        presentationDraft = presentationFromSettings(settings);
        syncPresentationControls();
      }
      applyPresentation(presentationDirty ? presentationDraft : presentationFromSettings(settings));
      const personaName = String(settings.personaName || "").trim() || "起源";
      for (const panel of [chatPanel, bodyPanel].filter(Boolean)) {
        const roleName = panel.querySelector("[data-avatar-role-name]");
        if (roleName) roleName.textContent = personaName;
      }
    };
    lifecycle.trackSubscription(state.on("settings", applyPresentationFromState));
    applyPresentationFromState();

    if (bodyPanel) {
      lifecycle.trackDomListener(bodyPanel, "input", (event) => {
        const input = event.target.closest("[data-avatar-presentation]");
        if (!input) return;
        const group = input.dataset.avatarPresentation;
        const key = input.dataset.key;
        presentationDraft = normalizeAvatarPresentation({
          ...presentationDraft,
          [group]: {
            ...(presentationDraft[group] || {}),
            [key]: input.value,
          },
        });
        presentationDirty = true;
        syncPresentationControls();
        applyPresentation(presentationDraft);
        setPresentationStatus("待保存", "pending");
      });

      lifecycle.trackDomListener(bodyPanel, "click", async (event) => {
        const button = event.target.closest("[data-avatar-presentation-action]");
        if (!button || presentationSaving) return;
        const action = button.dataset.avatarPresentationAction;
        if (action === "reset") {
          presentationDraft = normalizeAvatarPresentation({
            camera: AVATAR_CAMERA_DEFAULTS,
            lighting: AVATAR_LIGHTING_DEFAULTS,
          });
          presentationDirty = true;
          syncPresentationControls();
          applyPresentation(presentationDraft);
          setPresentationStatus("已恢复默认，尚未保存", "pending");
          return;
        }
        if (action !== "save") return;
        if (typeof actions?.saveSettings !== "function") {
          setPresentationStatus("设置保存通道不可用", "error");
          return;
        }
        presentationSaving = true;
        button.disabled = true;
        setPresentationStatus("保存中", "pending");
        try {
          const saved = await actions.saveSettings({
            bodyCamera: presentationDraft.camera,
            bodyLighting: presentationDraft.lighting,
          });
          presentationDraft = presentationFromSettings(saved || state.snapshot().settings);
          presentationDirty = false;
          syncPresentationControls();
          applyPresentation(presentationDraft);
          setPresentationStatus("已保存到当前身体配置", "success");
        } catch (error) {
          setPresentationStatus(error?.message || "显示设置保存失败", "error");
        } finally {
          presentationSaving = false;
          button.disabled = false;
        }
      });
    }

    // ── 模型选择：内置目录 + 自定义导入入口 ─────────────────────────
    function setImportStatus(message, stateName = "") {
      if (!importStatus) return;
      importStatus.textContent = String(message || "");
      importStatus.dataset.state = stateName;
    }

    async function refreshCatalog() {
      const bridge = window.tiangongAvatarImport;
      const [builtinModels, customModels] = await Promise.all([
        loadBuiltinCatalog(),
        typeof bridge?.listRegisteredModels === "function"
          ? Promise.resolve(bridge.listRegisteredModels()).catch(() => [])
          : Promise.resolve([]),
      ]);
      const models = mergeAvatarCatalog(builtinModels, customModels);
      if (!modelSelect) return;
      const placeholder = models.length > 0 ? "请选择身体模型" : "尚未导入模型";
      modelSelect.innerHTML = [
        `<option value="" disabled>${escHtml(placeholder)}</option>`,
        ...models.map(
          (model) =>
            `<option value="${escHtml(model.id)}">${escHtml(model.displayName || model.id)}</option>`,
        ),
      ].join("");
      modelSelect.disabled = models.length === 0;
      const selectedModelId = describeAvatarProjection(store.projection()).selectedModelId;
      modelSelect.value = models.some((model) => model.id === selectedModelId)
        ? selectedModelId
        : "";
    }
    await refreshCatalog();

    if (modelSelect) {
      lifecycle.trackDomListener(modelSelect, "change", () => {
        const modelId = modelSelect.value;
        if (!modelId) return;
        try {
          avatarService.getRuntime()?.selectModel(modelId);
        } catch (error) {
          setImportStatus(error.message || "模型切换失败", "error");
        }
      });
    }

    const importButton = bodyPanel?.querySelector('[data-avatar-action="import"]');
    if (importButton) {
      lifecycle.trackDomListener(importButton, "click", async () => {
      const bridge = window.tiangongAvatarImport;
      if (!bridge || typeof bridge.importCustomModel !== "function") {
        setImportStatus("导入通道未就绪（需要 direct 导入桥）", "error");
        return;
      }
      setImportStatus("正在校验并导入…", "pending");
      try {
        const result = await bridge.importCustomModel();
        if (result?.status === "license-blocked") {
          // §10.3：Redistribution_Prohibited 明确提示，用户确认后带 acknowledge 重试
          const acknowledged = window.confirm(result.notice || "该模型声明禁止再分发，仅可本机使用。");
          if (acknowledged) {
            const retry = await bridge.importCustomModel({
              acknowledgeLicense: true,
              resumeToken: result.resumeToken,
            });
            if (retry?.ok) await refreshCatalog();
            const retryStatus = describeAvatarImportResult(retry);
            setImportStatus(retryStatus.message, retryStatus.state);
          } else {
            try { bridge.cancelPending?.(result.resumeToken); } catch { /* 已过期/旧桥均视作取消 */ }
            setImportStatus("已取消导入", "");
          }
          return;
        }
        if (result?.ok) await refreshCatalog();
        const resultStatus = describeAvatarImportResult(result);
        setImportStatus(resultStatus.message, resultStatus.state);
      } catch (error) {
        setImportStatus(error.message || "导入失败", "error");
      }
      });
    }

    // ── 状态渲染：AvatarStore 投影 → DOM ────────────────────────────
    function renderProjection(projection) {
      const presentation = describeAvatarProjection(projection);
      for (const panel of [chatPanel, bodyPanel].filter(Boolean)) {
        const pill = panel.querySelector("[data-avatar-live-pill]");
        if (pill) pill.textContent = projection.paused ? "已暂停" : "direct";
        const modelMeta = panel.querySelector('[data-avatar-meta="model"]');
        if (modelMeta) modelMeta.textContent = presentation.modelText;
        const stateMeta = panel.querySelector('[data-avatar-meta="state"]');
        if (stateMeta) stateMeta.textContent = presentation.stateText;
        const emptyState = panel.querySelector("[data-avatar-empty-state]");
        const surfaceHost = panel.querySelector("[data-avatar-surface-host]");
        if (surfaceHost) surfaceHost.classList.toggle("is-empty", Boolean(presentation.emptyVisible));
        if (emptyState) {
          emptyState.hidden = !presentation.emptyVisible;
          const emptyTitle = emptyState.querySelector("[data-avatar-empty-title]");
          const emptyHint = emptyState.querySelector("[data-avatar-empty-hint]");
          if (emptyTitle) emptyTitle.textContent = presentation.emptyTitle;
          if (emptyHint) {
            emptyHint.textContent =
              !presentation.selectedModelId && panel === bodyPanel
                ? "使用下方按钮导入本机 VRM 模型。"
                : presentation.emptyHint;
          }
        }
      }
      if (
        modelSelect
        && presentation.selectedModelId
        && [...modelSelect.options].some(
          (option) => option.value === presentation.selectedModelId,
        )
      ) {
        modelSelect.value = presentation.selectedModelId;
      } else if (modelSelect && !presentation.selectedModelId) {
        modelSelect.value = "";
      }
    }
    lifecycle.trackSubscription(store.subscribe(renderProjection));
    renderProjection(store.projection());

    return () => lifecycle.unmount();
  },
};
