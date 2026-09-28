"""Exercise the Electron window lifecycle with a deliberately stalled gateway."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
WINDOW_FUNCTIONS = MAIN[
    MAIN.index("async function maybeMarkHealthyUpdate") : MAIN.index("function revealMainWindow")
]
WAIT_FUNCTION = MAIN[
    MAIN.index("async function waitForTotalGateway(") : MAIN.index("async function waitForTotalGatewayReadiness")
]
MODEL_SETTINGS_FUNCTION = MAIN[
    MAIN.index("async function desktopModelSettingsRequest(") : MAIN.index("function sha256File(")
]


@pytest.mark.parametrize(
    "outcome", ["pending", "ready", "degraded", "rejected", "closed", "renderer_unready", "late_ready"]
)
def test_frontend_opens_before_gateway_finishes_and_update_requires_readiness(outcome: str) -> None:
    script = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const events = [], diagnostics = [], healthyTokens = [];
let settle;
let snapshot = {"total-gateway": {running: false, ready: false}};
const startPromise = new Promise((resolve, reject) => { settle = {resolve, reject}; });
const window = {
  destroyed: false,
  rendererReady: OUTCOME !== "renderer_unready",
  events: {},
  loadURL: async () => { events.push("splash"); },
  loadFile: async () => { events.push("frontend"); },
  isDestroyed() { return this.destroyed; },
  on(name, callback) { this.events[name] = callback; },
  webContents: {
    setWindowOpenHandler() {},
    on() {},
    executeJavaScript: async () => window.rendererReady,
  },
};
const ctx = {
  mainWindow: null,
  frontendLoadedWindow: null,
  healthyUpdateTokenCommitted: "",
  healthyUpdateCheckPromise: null,
  BrowserWindow: class { constructor() { return window; } },
  session: {defaultSession: {setPermissionRequestHandler() {}}},
  process: {env: {}},
  SOURCE_MODE: true,
  PRODUCT_LABEL: "source test",
  APP_ICON_FILE: "icon",
  PRELOAD_FILE: "preload",
  PRIMARY_FRONTEND_FILE: "frontend",
  exists: () => true,
  applyWorkspacePreference() {},
  hydrateProviderApiKeys() {},
  applyWindowTheme() {},
  installEditContextMenu() {},
  isTrustedAppUrl: () => true,
  isTrustedAppFrameUrl: () => true,
  writeDesktopDiagnostic: (kind, detail) => diagnostics.push({kind, detail}),
  setTimeout: () => 1,
  startBackendWatchdog: () => events.push("watchdog"),
  stopBackendWatchdog: () => events.push("stop-watchdog"),
  postUpdateToken: () => "update-token",
  getSecureUpdater: () => ({markHealthy(token) { healthyTokens.push(token); return true; }}),
  withTimeout: promise => promise,
  serviceSupervisor: {
    startAll() { events.push("start-service"); return startPromise; },
    snapshot: () => snapshot,
  },
  console: {warn() {}},
};
vm.createContext(ctx);
vm.runInContext(WINDOW_FUNCTIONS, ctx);
(async () => {
  await Promise.race([
    ctx.createWindow(),
    new Promise((_resolve, reject) => setTimeout(() => reject(new Error("window blocked on gateway")), 500)),
  ]);
  assert.deepEqual(events.slice(0, 3), ["splash", "frontend", "start-service"]);
  assert.equal(ctx.mainWindow, window);
  assert.deepEqual(healthyTokens, []);
  if (OUTCOME === "pending") return;
  if (OUTCOME === "closed") {
    window.destroyed = true;
    window.events.closed();
  }
  if (OUTCOME === "rejected") settle.reject(new Error("gateway launch rejected"));
  else {
    snapshot = {"total-gateway": {running: true, ready: !["degraded", "late_ready"].includes(OUTCOME)}};
    settle.resolve(snapshot);
  }
  await new Promise(setImmediate);
  assert.deepEqual(healthyTokens, OUTCOME === "ready" ? ["update-token"] : []);
  if (OUTCOME === "closed") assert.equal(events.includes("watchdog"), false);
  else assert.equal(events.includes("watchdog"), true);
  if (OUTCOME === "rejected") {
    assert.equal(diagnostics.some(item => item.kind === "application-services-start-failed"), true);
  }
  if (OUTCOME === "renderer_unready") {
    window.rendererReady = true;
    await ctx.maybeMarkHealthyUpdate(window);
    assert.deepEqual(healthyTokens, ["update-token"]);
  }
  if (OUTCOME === "late_ready") {
    snapshot = {"total-gateway": {running: true, ready: true}};
    await ctx.maybeMarkHealthyUpdate(window);
    assert.deepEqual(healthyTokens, ["update-token"]);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    script = script.replace("WINDOW_FUNCTIONS", json.dumps(WINDOW_FUNCTIONS)).replace(
        "OUTCOME", json.dumps(outcome)
    )
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("phase", ["splash", "frontend"])
@pytest.mark.parametrize("action", ["close", "replace"])
def test_window_change_during_load_cannot_start_services_for_stale_window(phase: str, action: str) -> None:
    script = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
let finishSplash, finishFrontend, starts = 0, stopWatchdog = 0;
const window = {
  destroyed: false, events: {},
  loadURL: () => new Promise(resolve => { finishSplash = resolve; }),
  loadFile: () => new Promise(resolve => { finishFrontend = resolve; }),
  isDestroyed() { return this.destroyed; },
  on(name, callback) { this.events[name] = callback; },
  webContents: {setWindowOpenHandler() {}, on() {}},
};
const replacement = {isDestroyed: () => false};
const ctx = {
  mainWindow: null, frontendLoadedWindow: null,
  BrowserWindow: class { constructor() { return window; } },
  session: {defaultSession: {setPermissionRequestHandler() {}}},
  process: {env: {}}, SOURCE_MODE: true, PRODUCT_LABEL: "test",
  APP_ICON_FILE: "icon", PRELOAD_FILE: "preload", PRIMARY_FRONTEND_FILE: "frontend",
  exists: () => true,
  applyWorkspacePreference() {}, hydrateProviderApiKeys() {}, applyWindowTheme() {},
  installEditContextMenu() {}, isTrustedAppUrl: () => true,
  isTrustedAppFrameUrl: () => true, writeDesktopDiagnostic() {}, setTimeout() {},
  stopBackendWatchdog() { stopWatchdog++; },
  serviceSupervisor: {startAll() { starts++; return Promise.resolve({}); }},
};
vm.createContext(ctx);
vm.runInContext(WINDOW_FUNCTIONS, ctx);
(async () => {
  const launch = ctx.createWindow();
  assert.equal(typeof window.events.closed, "function");
  if (PHASE === "frontend") {
    finishSplash();
    await new Promise(setImmediate);
    assert.equal(typeof finishFrontend, "function");
  }
  if (ACTION === "close") {
    window.destroyed = true;
    window.events.closed();
  } else {
    ctx.mainWindow = replacement;
  }
  if (PHASE === "splash") finishSplash();
  else finishFrontend();
  await launch;
  assert.equal(starts, 0);
  assert.equal(ctx.frontendLoadedWindow, null);
  assert.equal(ctx.mainWindow, ACTION === "close" ? null : replacement);
  assert.equal(stopWatchdog, ACTION === "close" ? 1 : 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    script = script.replace("WINDOW_FUNCTIONS", json.dumps(WINDOW_FUNCTIONS)).replace(
        "PHASE", json.dumps(phase)
    ).replace("ACTION", json.dumps(action))
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gateway_wait_stops_when_window_quit_drains_services() -> None:
    script = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
let probes = 0;
const ctx = {
  SOURCE_MODE: true,
  SERVICE_START_ATTEMPTS: 2400,
  serviceSupervisor: {draining: true},
  totalGatewayHealthCheck: async () => { probes++; return false; },
  setTimeout,
};
vm.createContext(ctx);
vm.runInContext(WAIT_FUNCTION, ctx);
ctx.waitForTotalGateway().then(result => {
  assert.equal(result, false);
  assert.equal(probes, 0);
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", script.replace("WAIT_FUNCTION", json.dumps(WAIT_FUNCTION))],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_model_settings_read_does_not_join_gateway_launch() -> None:
    script = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
let releaseStart, started = 0, serviceReady = false;
const pendingStart = new Promise(resolve => { releaseStart = () => { serviceReady = true; resolve(); }; });
const ctx = {
  normalizedModelSettingsPayload: value => value,
  backendControlJsonRequest: async () => serviceReady
    ? {statusCode: 200, payload: {ok: true}} : {statusCode: 0, payload: null, error: "offline"},
  serviceSupervisor: {start: () => { started++; return pendingStart; }},
  modelRuntimeServiceName: () => "total-gateway",
};
vm.createContext(ctx);
vm.runInContext(MODEL_SETTINGS_FUNCTION, ctx);
(async () => {
  const read = await Promise.race([
    ctx.desktopModelSettingsRequest("GET"),
    new Promise((_resolve, reject) => setTimeout(() => reject(new Error("GET joined gateway launch")), 500)),
  ]);
  assert.equal(read.ok, false);
  assert.equal(started, 0);
  const write = ctx.desktopModelSettingsRequest("POST", {provider: "test"});
  await new Promise(setImmediate);
  assert.equal(started, 1);
  releaseStart();
  assert.equal((await write).ok, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", script.replace("MODEL_SETTINGS_FUNCTION", json.dumps(MODEL_SETTINGS_FUNCTION))],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("ready_during_boot", [False, True])
def test_frontend_refreshes_when_gateway_becomes_ready(ready_during_boot: bool) -> None:
    script = r"""
import assert from "node:assert/strict";
import {createAppCore} from APP_CORE_URL;
let onServiceStatus;
globalThis.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};
globalThis.window = {
  localStorage,
  addEventListener() {},
  tiangongDesktop: {
    setThemeStyle: async () => {},
    onServiceStatus(callback) { onServiceStatus = callback; },
  },
};
const documentRef = {documentElement: {dataset: {}, style: {}}, querySelector: () => null};
let statusCalls = 0, configCalls = 0;
let releaseInitialStatus;
const initialStatusGate = new Promise(resolve => { releaseInitialStatus = resolve; });
const runtime = {
  getSettings: async () => ({}),
  async status() {
    statusCalls++;
    if (READY_DURING_BOOT && statusCalls === 1) await initialStatusGate;
    return {ok: false, stderr: "offline"};
  },
  async config() { configCalls++; return {ok: true, stdout: "{}"}; },
};
const core = createAppCore({runtime, documentRef});
const boot = core.boot();
if (READY_DURING_BOOT) {
  await new Promise(setImmediate);
  onServiceStatus({"total-gateway": {ready: true}});
  releaseInitialStatus();
}
await boot;
assert.equal(statusCalls, READY_DURING_BOOT ? 2 : 1);
assert.equal(configCalls, READY_DURING_BOOT ? 2 : 1);
if (!READY_DURING_BOOT) {
  onServiceStatus({"total-gateway": {ready: true}});
  await new Promise(setImmediate);
  assert.equal(statusCalls, 2);
  assert.equal(configCalls, 2);
}
onServiceStatus({"total-gateway": {ready: true}});
await new Promise(setImmediate);
assert.equal(statusCalls, 2);
onServiceStatus({"total-gateway": {ready: false}});
await new Promise(setImmediate);
assert.equal(statusCalls, 3);
assert.equal(configCalls, 2);
"""
    script = script.replace(
        "APP_CORE_URL", json.dumps((ROOT / "app/frontend-v2/renderer/core/app-core.mjs").as_uri())
    ).replace("READY_DURING_BOOT", json.dumps(ready_during_boot))
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_renderer_service_refreshes_cannot_finish_out_of_order() -> None:
    script = r"""
import assert from "node:assert/strict";
import {createAppCore} from APP_CORE_URL;
let onServiceStatus, releaseReadyFetch;
const readyFetch = new Promise(resolve => { releaseReadyFetch = resolve; });
globalThis.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};
globalThis.window = {
  localStorage, addEventListener() {},
  tiangongDesktop: {setThemeStyle: async () => {}, onServiceStatus(callback) { onServiceStatus = callback; }},
};
const documentRef = {documentElement: {dataset: {}, style: {}}, querySelector: () => null};
const finished = [];
let statusCalls = 0;
const runtime = {
  getSettings: async () => ({}),
  async status() {
    statusCalls++;
    if (statusCalls === 2) { await readyFetch; finished.push("ready"); }
    if (statusCalls === 3) finished.push("degraded");
    return {ok: false, stderr: "offline"};
  },
  config: async () => ({ok: true, stdout: "{}"}),
};
const core = createAppCore({runtime, documentRef});
await core.boot();
onServiceStatus({"total-gateway": {ready: true}});
await new Promise(setImmediate);
assert.equal(statusCalls, 2);
onServiceStatus({"total-gateway": {ready: false}});
await new Promise(setImmediate);
assert.equal(statusCalls, 2, "DEGRADED refresh must wait for READY refresh");
releaseReadyFetch();
await new Promise(setImmediate);
assert.equal(statusCalls, 3);
assert.deepEqual(finished, ["ready", "degraded"]);
"""
    script = script.replace(
        "APP_CORE_URL", json.dumps((ROOT / "app/frontend-v2/renderer/core/app-core.mjs").as_uri())
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
