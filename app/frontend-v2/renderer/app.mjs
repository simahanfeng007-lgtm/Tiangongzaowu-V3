window.__appEvalAt = performance.now();
import { createAppCore } from "./core/app-core.mjs";
import { plugins } from "./plugins/index.mjs";
import { createFrontendKernel } from "./runtime/frontend-kernel.mjs";
import { createHttpRuntime } from "./runtime/http-runtime.mjs";
// direct 面板注册依赖 avatar-service：跨 module 脚本的执行顺序不保证等待（实测），
// 必须显式等待启动组装完成（avatarBootReady 自身容错，不会阻断前端）。
import { avatarBootReady } from "./bootstrap.mjs";

await avatarBootReady;

const kernel = window.tiangongFrontendKernel || createFrontendKernel();
window.tiangongFrontendKernel = kernel;
const runtime = window.tiangongRuntime || createHttpRuntime({ kernel });
window.tiangongRuntime = runtime;
const app = createAppCore({ runtime, kernel });

for (const plugin of plugins) {
  app.registerPlugin(plugin);
}

document.documentElement.dataset.tiangongCoreLoaded = "true";
document.documentElement.dataset.tiangongReady = "booting";
window.addEventListener("error", (event) => kernel?.probe?.().catch?.(() => {}), { once: true });
window.addEventListener("unhandledrejection", (event) => {
  console.error("Unhandled frontend rejection", event.reason);
});

app.boot()
  .then(() => {
    const failures = app.pluginFailures || [];
    document.documentElement.dataset.tiangongReady = failures.length ? "degraded" : "true";
    if (failures.length) {
      window.__tiangongShowFatal?.(`插件挂载失败：${failures.map((item) => item.id).join("、")}`);
    } else {
      window.__tiangongHideFatal?.();
    }
  })
  .catch((error) => {
    document.documentElement.dataset.tiangongReady = "failed";
    console.error(error);
    window.__tiangongShowFatal?.(error?.stack || error?.message || error);
  });
