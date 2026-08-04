import { createRendererErrorBoundary } from "./runtime/renderer-error-boundary.mjs";

const writeRendererDiagnostic = (kind, detail) => {
  try {
    window.tiangongDesktop?.writeDiagnostic?.(kind, String(detail || "").slice(0, 1200));
  } catch (_error) {
    // Diagnostics are best-effort and must never cause another renderer failure.
  }
};

const showFatal = (detail) => {
  if (document.querySelector("[data-ti-fatal]")) return;
  const fatalDetail = String(detail || "unknown renderer error").slice(0, 1200);
  writeRendererDiagnostic("renderer-fatal", fatalDetail);
  const panel = document.createElement("section");
  panel.dataset.tiFatal = "true";
  panel.style.cssText = "position:fixed;inset:52px 40px auto 40px;z-index:99999;padding:24px;border:1px solid #9b3a32;border-radius:14px;background:#211513;color:#ffd8d2;font:15px/1.6 system-ui;white-space:pre-wrap";
  panel.textContent = `桌面界面加载失败，已记录诊断，不再显示黑屏。\n${fatalDetail}`;
  document.body.appendChild(panel);
};
const hideFatal = () => document.querySelector("[data-ti-fatal]")?.remove();
const runtimeIdentity = window.tiangongDesktop?.getFrontendMetadata?.()
  || window.tiangongDesktop?.frontendMetadata
  || {};
const runtimeProductLabel = String(runtimeIdentity.productLabel || "天工造物 v3.0 完整版");
document.title = `${runtimeProductLabel} · 起源`;
const titlebarBrand = document.querySelector(".desktop-titlebar-brand span");
if (titlebarBrand) titlebarBrand.textContent = `${runtimeProductLabel} · 起源`;
document.documentElement.dataset.tiangongDistribution = runtimeIdentity.sourceMode === true
  ? "source"
  : "packaged";
const rendererErrorBoundary = createRendererErrorBoundary({
  showFatal,
  writeDiagnostic: writeRendererDiagnostic,
});
window.addEventListener("error", rendererErrorBoundary.onWindowError);
window.addEventListener("unhandledrejection", rendererErrorBoundary.onUnhandledRejection);
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-window-action]");
  if (!button) return;
  window.tiangongDesktop?.sendWindowAction?.(button.dataset.windowAction);
});
window.__tiangongShowFatal = showFatal;
window.__tiangongHideFatal = hideFatal;

// P6b 组装根：direct 模式所需服务（avatar-runtime/avatar-service）在 app.mjs 的
// plugins mount 前完成注册；任何失败回退 legacy-iframe，不阻断前端启动。
// module 脚本按序执行不保证跨文件等待（实测 app.mjs 先求值）：
// app.mjs 必须 `await avatarBootReady` 后再注册插件，否则 direct 面板抢跑。
import { bootstrapAvatar } from "./avatar/avatar-boot.mjs";
export const avatarBootReady = (async () => {
  try {
    // webSecurity 下 fetch(file://) 被禁：内置清单走主进程桥（无桥时回退模块内 fetch，
    // 供测试/非 Electron 环境注入 manifest 或 fetchImpl）。
    let manifest = null;
    try {
      manifest = await window.tiangongDesktop?.avatarAsset?.getBuiltinManifest?.();
    } catch (_error) {
      manifest = null;
    }
    await bootstrapAvatar({ document, window, navigator, flagStorage: window.localStorage, manifest });
    window.__avatarBootError = null;
    window.__bootSettledAt = performance.now();
  } catch (error) {
    // 启动失败必须可见可诊断（§23）：不阻断前端，但暴露原因供 E2E/诊断读取。
    window.__avatarBootError = String((error && (error.code || error.message)) || error).slice(0, 600);
    console.error("avatar boot failed", error);
    showFatal(`avatar boot failed: ${window.__avatarBootError}`);
  }
})();
