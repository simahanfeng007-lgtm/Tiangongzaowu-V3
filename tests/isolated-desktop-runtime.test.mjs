import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const main = fs.readFileSync(new URL("../app/main.js", import.meta.url), "utf8");

function functionsBetween(start, end) {
  const first = main.indexOf(start);
  const last = main.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `missing function boundaries: ${start}`);
  return main.slice(first, last);
}

const gatewaySource = functionsBetween("async function waitForTotalGateway(", "async function waitForTotalGatewayReadiness(")
  + functionsBetween("async function startTotalGateway()", "function sameWindowsPath(");
const updaterSource = functionsBetween("function getSecureUpdater()", "function postUpdateToken()");

function gatewayFixture(isolated = true) {
  const calls = { legacy: [], killed: [], stopped: [], spawned: [], health: 0, listeners: 0 };
  const child = Object.assign(new EventEmitter(), { pid: 777, exitCode: null, signalCode: null });
  const context = vm.createContext({
    SOURCE_MODE: false,
    ISOLATED_INSTALLATION: isolated,
    SERVICE_START_ATTEMPTS: 1,
    DEFAULT_TOTAL_GATEWAY_PORT: "7184",
    serviceSupervisor: { draining: false },
    totalGatewayStarting: false,
    totalGatewayProcess: null,
    adoptedTotalGatewayPids: new Set(),
    path,
    fs: { mkdirSync: () => {} },
    console: { warn: () => {}, log: () => {} },
    setTimeout,
    runtimeStateRoot: () => path.resolve("isolated-test-state"),
    backendDir: () => path.resolve("isolated-test-backend"),
    stopBackendGatewayAsync: async () => { calls.legacy.push("backend"); },
    stopLifeServiceAsync: async () => { calls.legacy.push("life"); },
    stopCommunicationServiceAsync: async () => { calls.legacy.push("communication"); },
    totalGatewayHealthCheck: async () => { calls.health += 1; return true; },
    totalGatewayListenerPids: () => { calls.listeners += 1; return [48184]; },
    totalGatewayPortOccupied: async () => true,
    writeDesktopDiagnostic: () => {},
    totalGatewayEntries: () => [{ command: "bundled-gateway.exe", args: [], cwd: path.resolve("isolated-test-runtime") }],
    totalGatewayEnvironment: () => ({ TIANGONG_DESKTOP_STATE_DIR: "isolated-state" }),
    spawnLoggedProcess: (...args) => { calls.spawned.push(args); return child; },
    stopChildGracefully: async (owned) => { calls.stopped.push(owned.pid); },
    killProcessTreeSync: (pid) => { calls.killed.push(pid); },
  });
  const actions = vm.runInContext(gatewaySource + "\n({start: startTotalGateway, stop: stopTotalGateway, stopSync: stopTotalGatewaySync})", context);
  return { calls, child, context, actions };
}

test("isolated installation refuses a preexisting gateway without adopting, sweeping or stopping it", async () => {
  const f = gatewayFixture();
  assert.equal(await f.actions.start(), false);
  assert.equal(f.context.adoptedTotalGatewayPids.size, 0);
  assert.equal(f.calls.health, 0);
  assert.deepEqual(f.calls.spawned, []);
  await f.actions.stop("isolated-conflict");
  f.actions.stopSync("isolated-conflict-failsafe");
  assert.deepEqual(f.calls.stopped, []);
  assert.deepEqual(f.calls.killed, []);
  assert.deepEqual(f.calls.legacy, []);

  // A socket conflict also blocks startup when the platform has no PID scan.
  f.context.totalGatewayListenerPids = () => [];
  assert.equal(await f.actions.start(), false);
  assert.equal(f.context.adoptedTotalGatewayPids.size, 0);
  assert.deepEqual(f.calls.spawned, []);
  assert.deepEqual(f.calls.legacy, []);
});

test("a concurrent isolated startup cannot accept a healthy foreign listener without an owned child", async () => {
  const f = gatewayFixture();
  f.context.totalGatewayStarting = true;
  assert.equal(await f.actions.start(), false);
  assert.equal(f.calls.health, 0);
  assert.equal(f.context.adoptedTotalGatewayPids.size, 0);
  assert.deepEqual(f.calls.spawned, []);
  assert.deepEqual(f.calls.legacy, []);
});

test("isolated startup spawns one attached child, reuses it and stops that child only", async () => {
  const f = gatewayFixture();
  f.context.totalGatewayListenerPids = () => [];
  f.context.totalGatewayPortOccupied = async () => false;
  assert.equal(await f.actions.start(), true);
  assert.equal(f.calls.spawned.length, 1);
  assert.equal(f.calls.spawned[0][2].detached, false);
  assert.equal(f.context.totalGatewayProcess, f.child);
  f.context.totalGatewayStarting = true;
  assert.equal(await f.actions.start(), true);
  f.context.totalGatewayStarting = false;
  assert.equal(await f.actions.start(), true);
  assert.equal(f.calls.spawned.length, 1);
  assert.equal(f.context.adoptedTotalGatewayPids.size, 0);
  assert.deepEqual(f.calls.legacy, []);
  await f.actions.stop("isolated-own-child");
  assert.deepEqual(f.calls.stopped, [777]);
  assert.deepEqual(f.calls.killed, []);
  assert.equal(f.context.totalGatewayProcess, null);
});

test("the isolated shutdown failsafe terminates its own child without stopping a foreign listener", () => {
  const f = gatewayFixture();
  f.context.totalGatewayProcess = f.child;
  f.actions.stopSync("isolated-own-child-failsafe");
  assert.deepEqual(f.calls.killed, [777]);
  assert.equal(f.context.totalGatewayProcess, null);
  assert.deepEqual(f.calls.legacy, []);
});

test("ordinary packaged installation retains healthy-listener adoption and cleanup", async () => {
  const f = gatewayFixture(false);
  assert.equal(await f.actions.start(), true);
  assert.equal(f.context.adoptedTotalGatewayPids.has(48184), true);
  assert.deepEqual(f.calls.legacy, ["backend", "life", "communication"]);
  assert.deepEqual(f.calls.spawned, []);
  await f.actions.stop("ordinary-packaged");
  assert.deepEqual(f.calls.killed, [48184]);
  assert.deepEqual(f.calls.stopped, []);
  assert.equal(f.context.adoptedTotalGatewayPids.size, 0);
});

const legacyStopSource = functionsBetween("function stopBackendGatewaySync(", "function execFileText(")
  + functionsBetween("function stopLifeServiceSync(", "async function communicationServiceEntry(")
  + functionsBetween("function stopCommunicationServiceSync(", "function totalGatewayListenerPids(")
  + functionsBetween("async function stopBackendGatewayAsync(", "async function waitForBackend(");

function legacyStopFixture(isolated) {
  const calls = { scanned: [], killed: [] };
  const images = new Map([[201, "tiangong-backend.exe"], [202, "tiangong-life-service.exe"], [203, "tiangong-communication-service.exe"]]);
  const context = vm.createContext({
    ISOLATED_INSTALLATION: isolated,
    backendStopping: false,
    backendProcess: { pid: 100, killed: false },
    lifeProcess: { pid: 101, killed: false },
    communicationProcess: { pid: 102, killed: false },
    LIFE_URL: "http://127.0.0.1:7175",
    COMMUNICATION_URL: "http://127.0.0.1:7176",
    DEFAULT_LIFE_PORT: "7175",
    DEFAULT_COMMUNICATION_PORT: "7176",
    console: { log: () => {} },
    processImageName: (pid) => images.get(pid),
    processImageNameAsync: async (pid) => images.get(pid),
    backendListenerPids: () => { calls.scanned.push("backend"); return [201]; },
    backendListenerPidsAsync: async () => { calls.scanned.push("backend"); return [201]; },
    lifeServiceListenerPids: () => { calls.scanned.push("life"); return [202]; },
    communicationListenerPids: () => { calls.scanned.push("communication"); return [203]; },
    serviceListenerPidsAsync: async (_url, port) => {
      const life = port === "7175";
      calls.scanned.push(life ? "life" : "communication");
      return life ? [202] : [203];
    },
    isReplaceableBackendListener: () => false,
    isReplaceableBackendListenerAsync: async () => false,
    killProcessTreeSync: (pid) => { calls.killed.push(pid); },
    killProcessTreeAsync: async (pid) => { calls.killed.push(pid); },
  });
  const actions = vm.runInContext(legacyStopSource + `\n({
    sync: [stopBackendGatewaySync, stopLifeServiceSync, stopCommunicationServiceSync],
    async: [stopBackendGatewayAsync, stopLifeServiceAsync, stopCommunicationServiceAsync],
  })`, context);
  return { calls, context, actions };
}

for (const mode of ["sync", "async"]) {
  test(`isolated ${mode} legacy shutdown stops owned children without scanning foreign listeners`, async () => {
    const f = legacyStopFixture(true);
    for (const stop of f.actions[mode]) {
      await stop("app-exit", { includeListeners: true, includeReplaceableListeners: true });
    }
    assert.deepEqual(f.calls.killed, [100, 101, 102]);
    assert.deepEqual(f.calls.scanned, []);
    assert.equal(f.context.backendProcess, null);
    assert.equal(f.context.lifeProcess, null);
    assert.equal(f.context.communicationProcess, null);
  });

  test(`ordinary packaged ${mode} legacy shutdown retains matching-listener cleanup`, async () => {
    const f = legacyStopFixture(false);
    for (const stop of f.actions[mode]) {
      await stop("app-exit", { includeListeners: true, includeReplaceableListeners: true });
    }
    assert.deepEqual(f.calls.killed, [100, 201, 101, 202, 102, 203]);
    assert.deepEqual(f.calls.scanned, ["backend", "life", "communication"]);
  });
}

function updaterFixture(isolated = true) {
  const calls = { constructed: [], trust: 0, backup: 0 };
  const context = vm.createContext({
    ISOLATED_INSTALLATION: isolated,
    secureUpdater: null,
    app: { getVersion: () => "3.0.3", getPath: () => "test-user-data" },
    SecureUpdater: class {
      constructor(options) { calls.constructed.push(options); }
    },
    updateTrustPath: () => { calls.trust += 1; return "test-trust.json"; },
    createPreUpdateSoulBackup: () => { calls.backup += 1; },
    emitUpdaterEvent: () => {},
  });
  const get = vm.runInContext(updaterSource + "\ngetSecureUpdater", context);
  return { calls, get };
}

test("isolated updater stays disabled without constructing the network updater", async () => {
  const f = updaterFixture();
  const updater = f.get();
  assert.equal(f.get(), updater);
  assert.equal(Object.isFrozen(updater), true);
  for (const status of [updater.status(), await updater.check(), await updater.download(), updater.recordError(new Error("test"))]) {
    assert.equal(status.state, "DISABLED");
    assert.equal(status.currentVersion, "3.0.3");
    assert.match(status.error, /独立/);
  }
  await assert.rejects(updater.apply(), /isolated_installation_manual_update_required/);
  assert.equal(updater.markHealthy("test-token"), false);
  assert.deepEqual(f.calls.constructed, []);
  assert.equal(f.calls.trust, 0);
  assert.equal(f.calls.backup, 0);
});

test("ordinary packaged installation still constructs and caches its secure updater", () => {
  const f = updaterFixture(false);
  const updater = f.get();
  assert.equal(f.get(), updater);
  assert.equal(f.calls.constructed.length, 1);
  assert.equal(f.calls.constructed[0].userData, "test-user-data");
  assert.equal(f.calls.constructed[0].currentVersion, "3.0.3");
  assert.equal(f.calls.constructed[0].trustPath, "test-trust.json");
  assert.equal(f.calls.trust, 1);
});
