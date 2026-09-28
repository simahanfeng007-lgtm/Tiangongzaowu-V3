import { createActions } from "./actions.mjs";
import { createBus } from "./bus.mjs";
import { createState } from "./state.mjs";

const THEME_STYLES = new Set(["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"]);
const LIGHT_THEMES = new Set(["jade_light", "ink_wash", "nordic_light"]);

function normalizeThemeStyle(value) {
  const theme = String(value || "").trim();
  return THEME_STYLES.has(theme) ? theme : "ink_teal";
}

function applyTheme(documentRef, settings) {
  const root = documentRef?.documentElement;
  if (!root) return;
  const theme = normalizeThemeStyle(settings?.themeStyle);
  root.dataset.theme = theme;
  root.style.colorScheme = LIGHT_THEMES.has(theme) ? "light" : "dark";
  try { window.localStorage?.setItem("tiangong-v3-theme", theme); } catch {}
  window.tiangongDesktop?.setThemeStyle?.(theme).catch?.(() => {});
}

export function createAppCore({ runtime, kernel = null, documentRef = document } = {}) {
  const state = createState();
  const bus = createBus();
  const plugins = [];
  const slotCache = new Map();
  const pluginFailures = [];
  let bootComplete = false;
  let pendingServiceRefresh = false;
  let observedGatewayReady = null;
  let serviceRefreshTail = Promise.resolve();
  const core = {
    runtime,
    kernel,
    state,
    bus,
    actions: null,
    pluginFailures,
    registerPlugin,
    getSlot,
    boot
  };

  core.actions = createActions({ runtime, state, kernel });
  if (kernel?.snapshot) state.setKernelStatus(kernel.snapshot());
  if (kernel?.onState) kernel.onState((next) => state.setKernelStatus(next));
  applyTheme(documentRef, state.snapshot().settings);
  state.on("settings", (settings) => applyTheme(documentRef, settings));
  window.tiangongDesktop?.onServiceStatus?.((snapshot) => {
    const ready = snapshot?.["total-gateway"]?.ready === true;
    if (ready === observedGatewayReady) return;
    observedGatewayReady = ready;
    if (!bootComplete) {
      pendingServiceRefresh = true;
      return;
    }
    void queueServiceRefresh();
  });

  function queueServiceRefresh() {
    // A slow READY fetch must not land after a newer DEGRADED observation and
    // overwrite the offline state. Apply transition refreshes in event order.
    serviceRefreshTail = serviceRefreshTail.then(refreshAfterServiceTransition, refreshAfterServiceTransition);
    return serviceRefreshTail;
  }

  async function refreshAfterServiceTransition() {
    await core.actions.refreshStatus().catch((error) => console.error("service status refresh failed", error));
    if (observedGatewayReady) {
      await core.actions.refreshConfig().catch((error) => console.error("service config refresh failed", error));
    }
  }

  function registerPlugin(plugin) {
    if (!plugin?.id || !plugin?.slot || typeof plugin.mount !== "function") {
      throw new Error("Plugin must provide id, slot, and mount(core).");
    }
    if (plugins.some((item) => item.id === plugin.id)) {
      throw new Error(`Plugin already registered: ${plugin.id}`);
    }
    plugins.push(plugin);
    return core;
  }

  function getSlot(name) {
    if (slotCache.has(name)) return slotCache.get(name);
    const slot = documentRef.querySelector(`[data-slot="${name}"]`);
    if (!slot) throw new Error(`Missing plugin slot: ${name}`);
    slotCache.set(name, slot);
    return slot;
  }

  async function boot() {
    for (const plugin of plugins.sort((a, b) => (a.order || 0) - (b.order || 0))) {
      try {
        await plugin.mount({ ...core, slot: getSlot(plugin.slot) });
      } catch (error) {
        // P1-15: a failed plugin must be observable; the shell must never
        // report ready when a mounted plugin did not come up.
        pluginFailures.push({ id: String(plugin.id || "unknown"), error: String(error?.message || error) });
        console.error(`Plugin mount failed: ${plugin.id}`, error);
      }
    }
    bus.emit("app:mounted");
    if (kernel?.boot) {
      const kernelState = await kernel.boot();
      bus.emit("kernel:state", kernelState);
    }
    await core.actions.loadSettings().catch((error) => console.error("loadSettings failed", error));
    await core.actions.refreshStatus().catch((error) => console.error("refreshStatus failed", error));
    await core.actions.refreshConfig().catch((error) => console.error("refreshConfig failed", error));
    bootComplete = true;
    if (pendingServiceRefresh) {
      pendingServiceRefresh = false;
      await queueServiceRefresh();
    }
  }

  return core;
}
