// P5 主题接入：AvatarRuntime presentation 配置随主题切换（相机/灯光 profile），不重载模型。
// 主题来源：应用设置 settings.themeStyle（ink_teal / bronze_gear / jade_light），
// 经 state.on("settings") 订阅后调用 applyTheme；只走 runtime.setPresentation 公共接口（§7.1），
// 不触发 selectModel/loadCandidate（模型不重解析）。

import { deepFreeze } from "./canonical-hash.mjs";

export const THEME_PRESENTATION_SCHEMA_VERSION = 1;

export const AVATAR_THEME_IDS = Object.freeze(["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"]);
export const DEFAULT_AVATAR_THEME = "ink_teal";

// 每主题的相机/灯光呈现 profile（呈现层参数，不含模型语义）。
export const THEME_PRESENTATION_PROFILES = Object.freeze({
  ink_teal: deepFreeze({
    themeId: "ink_teal",
    camera: deepFreeze({ distance: 1, height: 0, side: 0, fov: 32 }),
    lighting: deepFreeze({ key: 1, ambient: 1, exposure: 1, temperature: "neutral-cool" }),
  }),
  bronze_gear: deepFreeze({
    themeId: "bronze_gear",
    camera: deepFreeze({ distance: 1.05, height: 0.02, side: 0, fov: 32 }),
    lighting: deepFreeze({ key: 1.1, ambient: 0.9, exposure: 1.05, temperature: "warm" }),
  }),
  jade_light: deepFreeze({
    themeId: "jade_light",
    camera: deepFreeze({ distance: 0.95, height: 0, side: 0, fov: 32 }),
    lighting: deepFreeze({ key: 0.95, ambient: 1.15, exposure: 1.1, temperature: "neutral-warm" }),
  }),
  cosmos_dark: deepFreeze({
    themeId: "cosmos_dark",
    camera: deepFreeze({ distance: 1.04, height: 0.02, side: 0, fov: 32 }),
    lighting: deepFreeze({ key: 1.1, ambient: 0.85, exposure: 1.02, temperature: "cool" }),
  }),
  ink_wash: deepFreeze({
    themeId: "ink_wash",
    camera: deepFreeze({ distance: 0.98, height: 0, side: 0, fov: 32 }),
    lighting: deepFreeze({ key: 0.9, ambient: 1.2, exposure: 1.06, temperature: "neutral" }),
  }),
  nordic_light: deepFreeze({
    themeId: "nordic_light",
    camera: deepFreeze({ distance: 1, height: 0, side: 0, fov: 32 }),
    lighting: deepFreeze({ key: 1, ambient: 1.1, exposure: 1.05, temperature: "neutral-cool" }),
  }),
});

export function sanitizeThemeId(themeStyle) {
  return AVATAR_THEME_IDS.includes(themeStyle) ? themeStyle : DEFAULT_AVATAR_THEME;
}

export function presentationForTheme(themeStyle) {
  return THEME_PRESENTATION_PROFILES[sanitizeThemeId(themeStyle)];
}

// getRuntime：() → AvatarRuntime|null（direct 未激活时静默跳过）。
export function createThemePresentationSync({ getRuntime } = {}) {
  if (typeof getRuntime !== "function") {
    throw new Error("createThemePresentationSync 需要注入 getRuntime()");
  }
  let lastAppliedTheme = null;

  // 应用主题：仅 setPresentation（相机/灯光 profile），模型不重载。
  // 返回 { applied, themeId }；direct 未激活时 applied=false。
  function applyTheme(themeStyle) {
    const themeId = sanitizeThemeId(themeStyle);
    const runtime = getRuntime();
    if (runtime === null || typeof runtime.setPresentation !== "function") {
      return deepFreeze({ applied: false, themeId });
    }
    const profile = presentationForTheme(themeId);
    runtime.setPresentation({
      camera: { ...profile.camera },
      lighting: { ...profile.lighting },
      themeId: profile.themeId,
    });
    lastAppliedTheme = themeId;
    return deepFreeze({ applied: true, themeId });
  }

  return deepFreeze({
    applyTheme,
    get lastAppliedTheme() {
      return lastAppliedTheme;
    },
  });
}
