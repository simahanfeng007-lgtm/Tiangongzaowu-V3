import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  createRendererErrorBoundary,
  isBenignResizeObserverMessage,
} from "../app/frontend-v2/renderer/runtime/renderer-error-boundary.mjs";
import {
  runSettingsMutationWithRecovery,
} from "../app/frontend-v2/renderer/plugins/settings-panel.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));

test("ResizeObserver 浏览器布局告警只记一次诊断且不显示致命面板", () => {
  const fatals = [];
  const diagnostics = [];
  let prevented = 0;
  const boundary = createRendererErrorBoundary({
    showFatal: (detail) => fatals.push(detail),
    writeDiagnostic: (kind, detail) => diagnostics.push([kind, detail]),
  });
  const event = {
    message: "ResizeObserver loop completed with undelivered notifications.",
    preventDefault: () => {
      prevented += 1;
    },
  };

  assert.deepEqual(boundary.onWindowError(event), {
    fatal: false,
    code: "resize_observer_warning",
  });
  boundary.onWindowError(event);

  assert.equal(prevented, 2);
  assert.deepEqual(fatals, []);
  assert.deepEqual(diagnostics, [[
    "renderer-resize-observer-warning",
    "ResizeObserver loop completed with undelivered notifications.",
  ]]);
});

test("只豁免 Chromium 两种精确 ResizeObserver 消息", () => {
  assert.equal(
    isBenignResizeObserverMessage("ResizeObserver loop completed with undelivered notifications."),
    true,
  );
  assert.equal(isBenignResizeObserverMessage("ResizeObserver loop limit exceeded"), true);
  assert.equal(isBenignResizeObserverMessage("ResizeObserver failed"), false);
  assert.equal(
    isBenignResizeObserverMessage("ResizeObserver loop limit exceeded: application error"),
    false,
  );
});

test("普通脚本错误和未处理 Promise 拒绝仍进入致命错误边界", () => {
  const fatals = [];
  const boundary = createRendererErrorBoundary({
    showFatal: (detail) => fatals.push(detail),
  });

  assert.deepEqual(
    boundary.onWindowError({ message: "real renderer failure" }),
    { fatal: true, code: "renderer_error" },
  );
  assert.deepEqual(
    boundary.onUnhandledRejection({ reason: new Error("async failure") }),
    { fatal: true, code: "unhandled_rejection" },
  );
  assert.equal(fatals[0], "real renderer failure");
  assert.match(fatals[1], /async failure/);
});

test("界面设置保存：网关短暂重启时有界重试并最终成功", async () => {
  const waits = [];
  const retries = [];
  let attempts = 0;
  const saved = await runSettingsMutationWithRecovery(
    async () => {
      attempts += 1;
      if (attempts < 6) {
        const error = new Error("无法连接后端：Failed to fetch");
        error.code = "network_error";
        throw error;
      }
      return { themeStyle: "jade_light" };
    },
    {
      wait: async (ms) => waits.push(ms),
      onRetry: (detail) => retries.push(detail.attempt),
    },
  );

  assert.deepEqual(saved, { themeStyle: "jade_light" });
  assert.equal(attempts, 6);
  assert.deepEqual(waits, [500, 1000, 2000, 4000, 8000]);
  assert.deepEqual(retries, [1, 2, 3, 4, 5]);
});

test("界面设置保存：非网络错误与重试耗尽均返回业务层处理", async () => {
  const validationError = Object.assign(new Error("主题值非法"), { code: "validation_error" });
  let validationAttempts = 0;
  await assert.rejects(
    runSettingsMutationWithRecovery(async () => {
      validationAttempts += 1;
      throw validationError;
    }, { wait: async () => {} }),
    validationError,
  );
  assert.equal(validationAttempts, 1);

  let networkAttempts = 0;
  await assert.rejects(
    runSettingsMutationWithRecovery(async () => {
      networkAttempts += 1;
      throw Object.assign(new Error("Failed to fetch"), { code: "network_error" });
    }, { retryDelaysMs: [0], wait: async () => {} }),
    /Failed to fetch/,
  );
  assert.equal(networkAttempts, 2);
});

test("界面设置按钮消费保存异常，不把业务断连泄漏给全局致命边界", () => {
  const settingsPanel = readFileSync(
    `${root}/app/frontend-v2/renderer/plugins/settings-panel.mjs`,
    "utf8",
  );
  assert.match(
    settingsPanel,
    /saveUi\.addEventListener\("click", async \(\) => \{[\s\S]*?runSettingsMutationWithRecovery[\s\S]*?\} catch \(error\) \{[\s\S]*?\} finally \{[\s\S]*?saveUi\.disabled = false;/,
  );
});

test("bootstrap 使用分类错误边界且真正写入致命诊断", () => {
  const bootstrap = readFileSync(
    `${root}/app/frontend-v2/renderer/bootstrap.mjs`,
    "utf8",
  );

  assert.match(bootstrap, /createRendererErrorBoundary/);
  assert.match(bootstrap, /writeRendererDiagnostic\("renderer-fatal", fatalDetail\)/);
  assert.match(
    bootstrap,
    /window\.addEventListener\("error", rendererErrorBoundary\.onWindowError\)/,
  );
  assert.doesNotMatch(
    bootstrap,
    /window\.addEventListener\("error", \(event\) => showFatal/,
  );
});
