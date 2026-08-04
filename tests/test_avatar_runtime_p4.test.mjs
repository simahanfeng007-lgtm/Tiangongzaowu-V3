// Avatar P4 运行时测试（方案 §4/§7.1/§11/§14/§18/§19/§20/§23，阶段 P4 退出条件）。
// mock 引擎/引擎2 + 注入单调时钟/RAF/DOM 替身；不依赖真实 WebGL/DOM。
// 运行：node --test tests/test_avatar_runtime_p4.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  AttemptKind,
  LoadAttemptState,
  RuntimeState,
} from "../app/frontend-v2/renderer/avatar/contracts.mjs";
import { LoadAttempt } from "../app/frontend-v2/renderer/avatar/load-attempt.mjs";
import { createServiceRegistry } from "../app/frontend-v2/renderer/avatar/service-registry.mjs";
import {
  EngineEvent,
  createEngineEventSink,
} from "../app/frontend-v2/renderer/avatar/engines/avatar-engine-contract.mjs";
import {
  DEFAULT_RESOURCE_ESTIMATE_PARAMS,
  computeStructuralStats,
  estimateModelResources,
  evaluateSwitchBudget,
} from "../app/frontend-v2/renderer/avatar/model-resource-estimator.mjs";
import { createRenderSurfaceController } from "../app/frontend-v2/renderer/avatar/render-surface-controller.mjs";
import { createVisibilityProbeSession } from "../app/frontend-v2/renderer/avatar/visibility-probe.mjs";
import { createSuspensionGuard } from "../app/frontend-v2/renderer/avatar/suspension-guard.mjs";
import {
  assembleMigrationSnapshot,
  createBodyRuntimeState,
  createTransitionActionBuffer,
} from "../app/frontend-v2/renderer/avatar/body-runtime-state.mjs";
import { DiagnosticEvent, createDiagnostics } from "../app/frontend-v2/renderer/avatar/diagnostics.mjs";
import { createQuarantineTracker } from "../app/frontend-v2/renderer/avatar/model-quarantine.mjs";
import { createPendingLoadJournal } from "../app/frontend-v2/renderer/avatar/pending-load-journal.mjs";
import { createMemoryStorageBackend } from "../app/frontend-v2/renderer/avatar/storage-adapter.mjs";
import {
  AVATAR_RUNTIME_SERVICE_ID,
  AvatarRuntimeError,
  createAvatarRuntime,
} from "../app/frontend-v2/renderer/avatar/avatar-runtime.mjs";

// ── 测试用具 ────────────────────────────────────────────────

function createClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: (ms) => { t += ms; return t; },
    set: (ms) => { t = ms; },
  };
}

// 假 RAF：队列计数即活动 RAF 数（N_activeRAF=1 审计）。
function createFakeRaf() {
  const queue = new Map();
  let seq = 0;
  return {
    request: (cb) => { const id = (seq += 1); queue.set(id, cb); return id; },
    cancel: (id) => { queue.delete(id); },
    pump(timestamp) {
      for (const [id, cb] of [...queue.entries()]) {
        if (queue.delete(id)) cb(timestamp);
      }
    },
    pendingCount: () => queue.size,
  };
}

const tick0 = () => new Promise((resolve) => setImmediate(resolve));

// 驱动主循环：推进时钟 → 执行一帧 RAF → 冲刷微任务（async 证据/journal）。
async function drive(ctx, cond, { maxFrames = 400, stepMs = 16, advanceClock = true } = {}) {
  for (let i = 0; i < maxFrames; i += 1) {
    if (advanceClock) ctx.clock.advance(stepMs);
    ctx.raf.pump(ctx.clock.now());
    await tick0();
    await tick0();
    if (cond()) return true;
  }
  return false;
}

function settleWatch(done) {
  const box = { settled: false, result: null };
  done.then((r) => { box.settled = true; box.result = r; }, (e) => { box.settled = true; box.error = e; });
  return box;
}

// ── GLB 字节构造（最小合法容器）──────────────────────────────
function buildGlb(json, binBytes = null) {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const jsonPad = (4 - (jsonBytes.length % 4)) % 4;
  const jsonLen = jsonBytes.length + jsonPad;
  const hasBin = binBytes !== null && binBytes.byteLength > 0;
  const binPad = hasBin ? (4 - (binBytes.byteLength % 4)) % 4 : 0;
  const binLen = hasBin ? binBytes.byteLength + binPad : 0;
  const total = 12 + 8 + jsonLen + (hasBin ? 8 + binLen : 0);
  const buffer = new ArrayBuffer(total);
  const dv = new DataView(buffer);
  dv.setUint32(0, 0x46546c67, true);
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, jsonLen, true);
  dv.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(buffer).set(jsonBytes, 20);
  for (let i = 0; i < jsonPad; i += 1) dv.setUint8(20 + jsonBytes.length + i, 0x20);
  if (hasBin) {
    const off = 20 + jsonLen;
    dv.setUint32(off, binLen, true);
    dv.setUint32(off + 4, 0x004e4942, true);
    new Uint8Array(buffer).set(binBytes, off + 8);
  }
  return buffer;
}

function pngBytes(width, height) {
  const bytes = new Uint8Array(33);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  const dv = new DataView(bytes.buffer);
  dv.setUint32(8, 13);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12); // "IHDR"
  dv.setUint32(16, width);
  dv.setUint32(20, height);
  return bytes;
}

// 结构统计可控的最小 VRM0 模型 JSON（含 1 张可嗅探 PNG 纹理 + morph/skin/animation）。
function makeModelBytes({ nodeCount = 4, vertexCount = 120, texture = { width: 64, height: 64 } } = {}) {
  const nodes = Array.from({ length: nodeCount }, (_, i) => ({ name: `n${i}` }));
  const images = texture ? [{ mimeType: "image/png", bufferView: 0 }] : [];
  const json = {
    asset: { version: "2.0" },
    extensions: { VRM: { meta: { title: "mock", commercialUssageName: "Allow" } } },
    extensionsUsed: ["VRM"],
    nodes,
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, targets: [{ POSITION: 0 }] }] }],
    accessors: [{ componentType: 5126, count: vertexCount, type: "VEC3", bufferView: 0 }],
    skins: [{ joints: [0, 1] }],
    animations: [{ samplers: [{ input: 0 }] }],
    buffers: [{ byteLength: 33 }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 33 }],
    images,
  };
  const bin = texture ? pngBytes(texture.width, texture.height) : new Uint8Array(33);
  return new Uint8Array(buildGlb(json, bin));
}

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function makeCatalog() {
  const models = new Map();
  for (const [modelId, hash] of [["model-a", HASH_A], ["model-b", HASH_B]]) {
    models.set(modelId, { modelId, contentHash: hash, bytes: makeModelBytes() });
  }
  return {
    models,
    openCalls: [],
    describeCalls: [],
    failOpenFor: new Set(),
    async describeModel(modelId) {
      this.describeCalls.push(modelId);
      const entry = this.models.get(modelId);
      if (!entry) throw Object.assign(new Error(`unknown model ${modelId}`), { code: "model_not_found" });
      return Object.freeze({ modelId, contentHash: entry.contentHash, byteLength: entry.bytes.byteLength });
    },
    async openModelBytes({ modelId }) {
      this.openCalls.push(modelId);
      if (this.failOpenFor.has(modelId)) {
        throw Object.assign(new Error(`open failed ${modelId}`), { code: "asset_open_failed" });
      }
      const entry = this.models.get(modelId);
      return entry.bytes.slice();
    },
  };
}

// ── mock 引擎适配器（每个候选一个 mock engine 实例）───────────
function createMockEngineAdapter() {
  const sink = createEngineEventSink();
  const adapter = {
    engineVersion: "mock-engine-1.0.0",
    engines: [], // mock engine / mock engine2 ...
    orderLog: [],
    renderer: { disposeCount: 0, dispose() { this.disposeCount += 1; } },
    disposedEngineCount: 0,
    drawCalls: 5,
    failNextLoad: false,
    deferLoad: null, // 设置为 promise 时 loadCandidate 等待它（journal 观察窗）
    gestureLog: [],
    contextLost: false,
    recreated: 0,
    on: (event, listener) => sink.on(event, listener),
    off: (event, listener) => sink.off(event, listener),
    async loadCandidate(bytes, { label, attemptId }) {
      if (adapter.deferLoad) await adapter.deferLoad;
      if (adapter.failNextLoad) {
        adapter.failNextLoad = false;
        throw Object.assign(new Error("parse boom"), { code: "vrm-parse-failed" });
      }
      const engine = {
        label, attemptId, byteLength: bytes.byteLength,
        uploaded: 0, stagingFrames: 0, presented: 0, concealed: 0, promoted: 0, restored: 0,
        disposed: 0, discarded: 0,
      };
      adapter.engines.push(engine);
      adapter.orderLog.push(`load:${label}`);
      return engine; // handle 即 mock engine 实例
    },
    async uploadCandidate(handle) { handle.uploaded += 1; },
    renderCandidateFrame(handle) {
      handle.stagingFrames += 1;
      sink.emit(EngineEvent.FIRST_RENDERABLE_FRAME, { attemptId: handle.attemptId });
      return { drawCalls: adapter.drawCalls };
    },
    presentCandidate(handle) { handle.presented += 1; adapter.orderLog.push(`present:${handle.label}`); },
    concealCandidate(handle) { handle.concealed += 1; adapter.orderLog.push(`conceal:${handle.label}`); },
    restorePresented(handle) { handle.restored += 1; adapter.orderLog.push(`restore:${handle.label}`); },
    promoteCandidate(handle) { handle.promoted += 1; adapter.orderLog.push(`promote:${handle.label}`); },
    disposeModel(handle) { handle.disposed += 1; adapter.orderLog.push(`dispose:${handle.label}`); },
    discardInvalidatedModel(handle) { handle.discarded += 1; adapter.orderLog.push(`discard:${handle.label}`); },
    renderFrame() { return true; },
    update() {},
    getStats() { return { drawCalls: adapter.drawCalls }; },
    candidateBoundsIntersectViewport: () => true,
    hasFatalRendererError: () => false,
    isContextLost() { return adapter.contextLost; },
    recreateRenderer() { adapter.recreated += 1; adapter.contextLost = false; },
    attachSurface() {},
    detachSurface() {},
    disposeEngine() { adapter.disposedEngineCount += 1; adapter.renderer.dispose(); },
    playGesture(key) { adapter.gestureLog.push(key); return true; },
    applyPosture() {},
    applyExpression() {},
    applyGaze() {},
    setSpeaking() {},
    applyVisemeTarget() {},
    emitContextLost() { adapter.contextLost = true; sink.emit(EngineEvent.CONTEXT_LOST, {}); },
    emitContextRestored() { adapter.contextLost = false; sink.emit(EngineEvent.CONTEXT_RESTORED, {}); },
  };
  return adapter;
}

function createHost({ width = 640, height = 480 } = {}) {
  const host = {
    id: "host-1",
    width,
    height,
    dpr: 1,
    visible: true,
    isVisible: () => host.visible,
    getViewport: () => ({ width: host.width, height: host.height, dpr: host.dpr }),
  };
  return host;
}

// 运行时组装：注入假时钟/RAF/Surface/诊断/资产目录。
function setup({
  catalog = makeCatalog(),
  env = {},
  evidenceSource = null,
  journal = null,
  quarantineTracker = null,
  suspensionGuard = null,
  suspensionBudgets,
  provisionalLimits,
  resourceBudgetBytes,
  validateCandidateHook = null,
  gpuFingerprint = null,
  registry = null,
} = {}) {
  const clock = createClock();
  const raf = createFakeRaf();
  const adapter = createMockEngineAdapter();
  const surface = createRenderSurfaceController({ nowMonotonic: clock.now, sizeStableWindowMs: 0 });
  const diagnostics = createDiagnostics({ nowMonotonic: clock.now });
  const runtime = createAvatarRuntime({
    engineAdapter: adapter,
    assetSource: catalog,
    nowMonotonic: clock.now,
    requestAnimationFrame: raf.request,
    cancelAnimationFrame: raf.cancel,
    surfaceController: surface,
    diagnostics,
    journal,
    quarantineTracker,
    suspensionGuard,
    env,
    evidenceSource,
    suspensionBudgets,
    provisionalLimits,
    resourceBudgetBytes,
    validateCandidateHook,
    gpuFingerprint,
    registry,
  });
  const host = createHost();
  runtime.attachSurface({ host, mode: "primary" });
  return { clock, raf, adapter, surface, diagnostics, runtime, host, catalog };
}

async function selectAndSettle(ctx, modelId) {
  const { done } = ctx.runtime.selectModel(modelId);
  const watch = settleWatch(done);
  const ok = await drive(ctx, () => watch.settled);
  assert.equal(ok, true, `selectModel(${modelId}) 未在帧预算内进入终态`);
  return watch.result;
}

// ── 1. 完整 switch 事务：双模型经探针链 committed，旧模型 committed 后才 dispose ──

test("switch 事务：renderability-probe→provisional-present→committed；旧模型 committed 后才 dispose；共享 renderer 不销毁", async () => {
  const ctx = setup();
  const first = await selectAndSettle(ctx, "model-a");
  assert.equal(first.outcome, "committed");
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-a");
  assert.equal(ctx.adapter.engines.length, 1);
  const engineA = ctx.adapter.engines[0];

  const second = await selectAndSettle(ctx, "model-b");
  assert.equal(second.outcome, "committed");
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-b");
  assert.equal(ctx.adapter.engines.length, 2, "候选走独立 mock engine2");
  const engineB = ctx.adapter.engines[1];

  // 探针链完整：staging 帧 → 呈现 → 提升。
  assert.equal(engineB.stagingFrames, 1);
  assert.equal(engineB.presented, 1);
  assert.equal(engineB.promoted, 1);
  // 旧模型在 committed 后才 dispose（§18.3.6）。
  assert.equal(engineA.disposed, 1);
  const promoteIndex = ctx.adapter.orderLog.indexOf("promote:model-b");
  const disposeIndex = ctx.adapter.orderLog.indexOf("dispose:model-a");
  assert.ok(promoteIndex >= 0 && disposeIndex > promoteIndex, `顺序应为 promote→dispose: ${ctx.adapter.orderLog}`);
  // 普通切换不销毁共享 renderer（§11.4）。
  assert.equal(ctx.adapter.renderer.disposeCount, 0);
  assert.equal(ctx.runtime.snapshot().state, RuntimeState.RUNNING);
  ctx.runtime.dispose();
});

// ── 2. provisional 失败回滚 rollbackTarget ──────────────────

test("候选解析失败：FAILED + 回滚 rollbackTarget，旧 current 保留且重新呈现", async () => {
  const ctx = setup();
  await selectAndSettle(ctx, "model-a");
  const engineA = ctx.adapter.engines[0];

  ctx.adapter.failNextLoad = true;
  const failed = await selectAndSettle(ctx, "model-b");
  assert.equal(failed.outcome, LoadAttemptState.FAILED);
  assert.equal(failed.reason, "vrm-parse-failed");
  // rollbackTarget 恢复：旧 current 未 dispose、重新呈现、Runtime 回到 running（§18.3.4/§18.3.7）。
  assert.equal(engineA.disposed, 0);
  assert.equal(engineA.restored, 1);
  const snap = ctx.runtime.snapshot();
  assert.equal(snap.state, RuntimeState.RUNNING);
  assert.equal(snap.current.modelId, "model-a");
  assert.equal(snap.pending, null);
  ctx.runtime.dispose();
});

test("maxProvisionalPresentMs 硬上限超时：回滚 rollbackTarget，不计 quarantine", async () => {
  const tracker = await createQuarantineTracker({ storage: null, nowWallClock: () => 0 });
  const recordFailureCalls = [];
  const spyTracker = {
    isQuarantined: (key) => tracker.isQuarantined(key),
    async recordFailure(input) { recordFailureCalls.push(input); return tracker.recordFailure(input); },
  };
  const ctx = setup({
    quarantineTracker: spyTracker,
    gpuFingerprint: {
      gpuVendorId: "0x0000",
      gpuDeviceId: "0x0000",
      driverVersion: "test-driver-1.0",
      angleBackend: "swiftshader",
      osGraphicsBuild: "test-build",
    },
    provisionalLimits: { schemaVersion: 1, maxProvisionalPresentMs: 50, targetProvisionalPresentFrames: 2, minGestureResumeMs: 250 },
    // model-a 正常给证据（先 committed），model-b 像素证据永不到达
    evidenceSource: ({ attemptId, modelId }) =>
      modelId === "model-a"
        ? Promise.resolve({ attemptId, nonBackgroundPixels: 1 })
        : new Promise(() => {}),
  });
  const initial = await selectAndSettle(ctx, "model-a");
  assert.equal(initial.outcome, "committed");
  const engineA = ctx.adapter.engines[0];

  const failed = await selectAndSettle(ctx, "model-b");
  assert.equal(failed.outcome, LoadAttemptState.FAILED);
  assert.equal(failed.reason, "provisional-present-timeout");
  assert.equal(engineA.disposed, 0);
  assert.equal(engineA.restored, 1);
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-a");
  // 超时按 runtime 类别计数一次（阈值未达不隔离，§19.4）。
  assert.equal(recordFailureCalls.length, 1);
  assert.equal(recordFailureCalls[0].category, "runtime");
  ctx.runtime.dispose();
});

// ── 3. context lost 恢复链 ──────────────────────────────────

test("context lost → RecoveryLoadAttempt（rollbackTarget=null）重走完整链，新 FIRST_VISIBLE_FRAME 才 committed；不访问旧 GPU 对象", async () => {
  const evidenceGate = { current: Promise.resolve(1) };
  const ctx = setup({
    evidenceSource: ({ attemptId }) => evidenceGate.current.then((pixels) => ({ attemptId, nonBackgroundPixels: pixels })),
  });
  const initial = await selectAndSettle(ctx, "model-a");
  assert.equal(initial.outcome, "committed");
  const engineA = ctx.adapter.engines[0];
  const phases = [];
  ctx.runtime.subscribe((snap) => {
    if (snap.pending) phases.push(`${snap.pending.attemptKind}:${snap.pending.state}`);
  });

  ctx.adapter.emitContextLost();
  assert.equal(ctx.runtime.snapshot().state, RuntimeState.CONTEXT_LOST);
  assert.equal(ctx.runtime.snapshot().current, null);

  // 阻塞像素证据：恢复 attempt 不得在新 FIRST_VISIBLE_FRAME 之前 committed。
  let releaseEvidence;
  evidenceGate.current = new Promise((resolve) => { releaseEvidence = resolve; });
  ctx.adapter.emitContextRestored();
  const reachedProbe = await drive(ctx, () => ctx.runtime.snapshot().pending?.state === LoadAttemptState.VISIBILITY_PROBE);
  assert.equal(reachedProbe, true);
  assert.equal(ctx.runtime.snapshot().state, RuntimeState.RECOVERING, "无新 FIRST_VISIBLE_FRAME 不得回 running");

  releaseEvidence(1);
  const recovered = await drive(ctx, () => ctx.runtime.snapshot().state === RuntimeState.RUNNING);
  assert.equal(recovered, true);
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-a");

  // 恢复链完整重走（§18.4：不得从 loading 直接跳 running）。
  const recoveryPhases = phases
    .filter((entry) => entry.startsWith(`${AttemptKind.RECOVERY}:`))
    .map((entry) => entry.split(":")[1]);
  const expected = [
    "validating", "admitted", "loading", "parsing", "uploading",
    "renderability-probe", "provisional-present", "visibility-probe",
  ];
  let cursor = 0;
  for (const state of recoveryPhases) {
    if (state === expected[cursor]) cursor += 1;
  }
  assert.equal(cursor, expected.length, `恢复链缺阶段: ${recoveryPhases.join(",")}`);
  // GPU 失效剔除走引用级 discard，不触旧 GPU 对象 dispose（§18.4/§20.3）。
  assert.equal(engineA.discarded, 1);
  assert.equal(engineA.disposed, 0, "旧 GPU 失效对象不得 disposeModel");
  assert.equal(ctx.adapter.recreated, 1, "恢复重建 Renderer（§20.3）");
  ctx.runtime.dispose();
});

test("无 rollbackTarget 的 recovery 失败 → degraded/2D safe mode，不访问旧 GPU 对象", async () => {
  const ctx = setup();
  await selectAndSettle(ctx, "model-a");
  const engineA = ctx.adapter.engines[0];

  ctx.adapter.emitContextLost();
  ctx.catalog.failOpenFor.add("model-a"); // 恢复重取字节失败
  ctx.adapter.emitContextRestored();
  const degraded = await drive(ctx, () => ctx.runtime.snapshot().state === RuntimeState.DEGRADED);
  assert.equal(degraded, true);
  assert.equal(ctx.runtime.snapshot().current, null);
  assert.equal(ctx.runtime.safeMode.mode, "2d");
  assert.equal(engineA.disposed, 0, "失效旧 GPU 对象不得被访问/dispose");
  assert.equal(engineA.discarded, 1);
  ctx.runtime.dispose();
});

// ── 4. suspended-probe 超预算 → cancelled 不计失败/隔离 ──────

test("文档 hidden → suspended-probe；连续挂起超预算 → cancelled + 不计失败 + 不计 quarantine + 回滚保留", async () => {
  const flags = { hidden: false };
  const tracker = await createQuarantineTracker({ storage: null, nowWallClock: () => 0 });
  const recordFailureCalls = [];
  const spyTracker = {
    isQuarantined: (key) => tracker.isQuarantined(key),
    async recordFailure(input) { recordFailureCalls.push(input); return tracker.recordFailure(input); },
  };
  const ctx = setup({
    env: { isDocumentHidden: () => flags.hidden },
    quarantineTracker: spyTracker,
    suspensionBudgets: { maxContinuousSuspendedMs: 100, maxCumulativeSuspendedMs: 1_000 },
  });
  const initial = await selectAndSettle(ctx, "model-a");
  assert.equal(initial.outcome, "committed");
  const engineA = ctx.adapter.engines[0];

  flags.hidden = true; // committed 后再 hidden：新 attempt 的探针应挂起而非失败
  const { done } = ctx.runtime.selectModel("model-b");
  const watch = settleWatch(done);
  // hidden：探针应进入 suspended-probe 而非失败。
  const suspended = await drive(ctx, () => ctx.runtime.snapshot().pending?.state === LoadAttemptState.SUSPENDED_PROBE);
  assert.equal(suspended, true, "hidden 环境应挂起探针（§19.1）");
  assert.equal(ctx.runtime.snapshot().pending.attemptKind, AttemptKind.SWITCH);
  // 持续 hidden 推进时钟：连续挂起超 100ms → cancelled。
  const cancelled = await drive(ctx, () => watch.settled);
  assert.equal(cancelled, true);
  assert.equal(watch.result.outcome, LoadAttemptState.CANCELLED);
  assert.equal(watch.result.reason, "suspension-budget-exceeded");
  assert.equal(watch.result.countsAsFailure, false, "挂起超限不计模型失败（§18.3.9）");
  assert.equal(recordFailureCalls.length, 0, "cancelled 不进 quarantine（§19.1）");
  // rollbackTarget 恢复，Runtime 回到 running。
  assert.equal(engineA.disposed, 0);
  assert.equal(engineA.restored, 1);
  assert.equal(ctx.runtime.snapshot().state, RuntimeState.RUNNING);
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-a");
  ctx.runtime.dispose();
});

test("renderer 恢复后先处理主进程 cancel 标记（独立时钟域 SuspensionGuard）", async () => {
  const flags = { hidden: false };
  const guardClock = createClock(10_000); // 主进程独立时钟域
  const guard = createSuspensionGuard({ nowMonotonic: guardClock.now });
  const ctx = setup({
    env: { isDocumentHidden: () => flags.hidden },
    suspensionGuard: guard,
    suspensionBudgets: { maxContinuousSuspendedMs: 100, maxCumulativeSuspendedMs: 1_000 },
  });
  const initial = await selectAndSettle(ctx, "model-a");
  assert.equal(initial.outcome, "committed");

  flags.hidden = true;
  const { done } = ctx.runtime.selectModel("model-b");
  const watch = settleWatch(done);
  await drive(ctx, () => ctx.runtime.snapshot().pending?.state === LoadAttemptState.SUSPENDED_PROBE);
  assert.equal(guard.activeCount, 1, "挂起已通知主进程守卫（相对预算）");

  // renderer 冻结期间主进程时钟独自走过预算 → 置 cancel 标记。
  guardClock.advance(500);
  flags.hidden = false;
  // 恢复后第一帧：先处理 cancel 标记再渲染/动作（§4.6）——renderer 时钟未走，不计 renderer 域预算。
  const cancelled = await drive(ctx, () => watch.settled, { advanceClock: false });
  assert.equal(cancelled, true);
  assert.equal(watch.result.outcome, LoadAttemptState.CANCELLED);
  assert.equal(watch.result.reason, "suspension-guard-cancel");
  assert.equal(watch.result.countsAsFailure, false);
  assert.equal(guard.activeCount, 0, "cancel 标记一次性消费");
  ctx.runtime.dispose();
});

// ── 5. BodyRuntimeState 唯一写入者 / pending 只读 / 缓冲纪律 ──

test("BodyRuntimeState：writer 唯一签发；pending 投影只读不回写", () => {
  const clock = createClock();
  const body = createBodyRuntimeState({ nowMonotonic: clock.now });
  const writer = body.createWriter();
  assert.throws(() => body.createWriter(), (e) => e.code === "writer_already_issued");

  const pending = body.projectFor("pending");
  assert.equal(pending.role, "pending");
  assert.throws(() => pending.setSpeaking(true), (e) => e.code === "pending_read_only");
  assert.throws(() => pending.setPosture({ name: "lean" }), (e) => e.code === "pending_read_only");
  assert.throws(() => pending.consumeWatermark({ sequence: 1 }), (e) => e.code === "pending_read_only");
  assert.equal(body.version, 0, "pending 写尝试不得改变状态版本");

  const current = body.projectFor("current");
  current._bindWriter(writer);
  current.setSpeaking(true);
  current.setPosture({ name: "stand" });
  assert.equal(body.version, 2);
  assert.equal(pending.getSnapshot().speaking, true, "pending 只读投影可见同一逻辑状态（同态双投影）");
});

test("transitionActionBuffer：Qmax 有界 + TTL 过期 + winner 至多一次 + stop 清空", () => {
  const clock = createClock();
  const expired = [];
  const dropped = [];
  const buffer = createTransitionActionBuffer({
    nowMonotonic: clock.now,
    qmax: 4,
    onExpired: (item, reason) => expired.push([item.actionId, reason]),
    onDropped: (item, reason) => dropped.push([item.actionId, reason]),
  });
  const mk = (actionId, ttlMs = 1_000) => ({
    actionId,
    semanticId: `gesture-${actionId}`,
    scheduled: { deadlineMonotonic: clock.now() + ttlMs, receivedAtMonotonic: clock.now() },
  });

  assert.equal(buffer.push(mk("g-expired", 0)), false, "TTL 已到直接丢弃");
  assert.deepEqual(expired, [["g-expired", "TRANSITION_ACTION_EXPIRED"]]);

  for (const id of ["g1", "g2", "g3", "g4", "g5", "g6"]) buffer.push(mk(id));
  assert.equal(buffer.size(), 4, "Qmax=4 有界");
  assert.equal(dropped.length, 2, "溢出丢最旧");
  assert.deepEqual(dropped.map(([id]) => id), ["g1", "g2"]);

  // winner 至多一次：重复 actionId 第二次跳过。
  const played = [];
  const result1 = buffer.resolveWinner({ execute: (item) => played.push(item.actionId) });
  assert.deepEqual(played, ["g3", "g4", "g5", "g6"]);
  assert.equal(result1.executed, 4);
  buffer.push(mk("g5")); // 已执行过的 actionId
  const result2 = buffer.resolveWinner({ execute: (item) => played.push(`again:${item.actionId}`) });
  assert.equal(result2.skippedDuplicate, 1);
  assert.equal(played.filter((id) => id === "g5").length, 1, "凭 actionId 至多执行一次（§18.5.5）");

  // 过期项在 resolveWinner 时记 TRANSITION_ACTION_EXPIRED 而不执行。
  buffer.push(mk("g-short", 10));
  clock.advance(20);
  const result3 = buffer.resolveWinner({ execute: () => {} });
  assert.equal(result3.expired, 1);
  assert.ok(expired.some(([id]) => id === "g-short"));

  // stop 最高优先级：立即清空。
  buffer.push(mk("g7"));
  buffer.push(mk("g8"));
  assert.equal(buffer.stop(), 2);
  assert.equal(buffer.size(), 0);
  assert.equal(buffer.isStopped(), true);
});

// ── 6. 迁移快照（§18.5）──────────────────────────────────────

test("committed 迁移快照：watermark/expression 插值/gaze 滤波/动画 normalizedTime/gesture 剩余 TTL；provisional 期间 gesture 缓冲 winner 执行一次", async () => {
  let releaseEvidence;
  const ctx = setup({
    evidenceSource: () => new Promise((resolve) => { releaseEvidence = resolve; }),
  });
  await selectAndSettle(ctx, "model-a");

  // current 上的正式动作：watermark + posture/expression/gaze + 活动动画。
  ctx.runtime.applyPerformance({
    schema: "body-action/v1", backendInstanceId: "be-1", turnId: "t1", sequence: 7, ttlMs: 5_000,
    posture: "stand", expression: { happy: 0.8 }, gaze: { x: 0.1, y: 0.2 }, gesture: "wave",
  });
  assert.deepEqual(ctx.adapter.gestureLog, ["wave"]);

  const { done } = ctx.runtime.selectModel("model-b");
  const watch = settleWatch(done);
  const inProbe = await drive(ctx, () => ctx.runtime.snapshot().pending?.state === LoadAttemptState.VISIBILITY_PROBE);
  assert.equal(inProbe, true);

  // provisional-present 期间新到 gesture 进缓冲，不在 current/pending 提前执行（§18.5.4）。
  ctx.runtime.applyPerformance({
    schema: "body-action/v1", backendInstanceId: "be-1", turnId: "t1", sequence: 8, ttlMs: 3_000, gesture: "clap",
  });
  assert.deepEqual(ctx.adapter.gestureLog, ["wave"], "缓冲期间不得提前执行 gesture");

  releaseEvidence({ attemptId: ctx.runtime.snapshot().pending.attemptId, nonBackgroundPixels: 1 });
  const committed = await drive(ctx, () => watch.settled);
  assert.equal(committed, true);
  assert.equal(watch.result.outcome, "committed");
  // winner 确定后缓冲 gesture 在新 current 上至多执行一次。
  assert.deepEqual(ctx.adapter.gestureLog, ["wave", "clap"]);

  const snap = ctx.runtime.getLastMigrationSnapshot();
  assert.equal(snap.schemaVersion, 1);
  assert.equal(snap.watermark.sequence, 8, "已消费 watermark 迁移");
  assert.equal(snap.posture.name, "stand");
  assert.equal(snap.expression.targets.happy, 0.8);
  assert.ok("current" in snap.expression && "remainingTransitionMs" in snap.expression, "expression 插值状态");
  assert.ok(snap.gaze.filter && typeof snap.gaze.filter.alpha === "number", "gaze 滤波/平滑状态");
  assert.equal(snap.activeAnimation.semanticId, "wave");
  assert.equal(snap.activeAnimation.normalizedTime, 0);
  assert.equal(snap.pendingGestures.length, 1);
  assert.equal(snap.pendingGestures[0].semanticId, "clap");
  assert.ok(snap.pendingGestures[0].remainingTtlMs > 0 && snap.pendingGestures[0].remainingTtlMs <= 3_000, "gesture 剩余 TTL");
  ctx.runtime.dispose();
});

// ── 7. N_activeRAF=1 / N_activeAvatarRuntime=1 ───────────────

test("重复 selectModel 不增 RAF（N_activeRAF=1）；latest-wins 取消旧 pending；共享 renderer 存活", async () => {
  const ctx = setup();
  const first = ctx.runtime.selectModel("model-a");
  const firstWatch = settleWatch(first.done);
  const second = ctx.runtime.selectModel("model-b");
  const secondWatch = settleWatch(second.done);
  const settled = await drive(ctx, () => firstWatch.settled && secondWatch.settled);
  assert.equal(settled, true);
  assert.equal(firstWatch.result.outcome, "cancelled", "旧 pending 被 latest-wins 取代");
  assert.equal(firstWatch.result.reason, "superseded");
  assert.equal(secondWatch.result.outcome, "committed");
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-b");
  assert.equal(ctx.raf.pendingCount(), 1, "同一时刻只允许一个活动 RAF（§4.4）");
  assert.equal(ctx.adapter.renderer.disposeCount, 0, "普通切换不销毁共享 renderer（§11.4）");
  ctx.runtime.dispose();
  assert.equal(ctx.raf.pendingCount(), 0, "dispose 取消 RAF 链");
});

test("N_activeAvatarRuntime=1：registry 内单例，重复注册拒绝", async () => {
  const registry = createServiceRegistry();
  const ctx = setup({ registry });
  assert.equal(registry.getService(AVATAR_RUNTIME_SERVICE_ID), ctx.runtime);
  const clock2 = createClock();
  const raf2 = createFakeRaf();
  assert.throws(
    () =>
      createAvatarRuntime({
        engineAdapter: createMockEngineAdapter(),
        assetSource: makeCatalog(),
        nowMonotonic: clock2.now,
        requestAnimationFrame: raf2.request,
        cancelAnimationFrame: raf2.cancel,
        registry,
      }),
    /已注册/,
  );
  ctx.runtime.dispose();
});

// ── 8. journal 纪律：parsing 前写入、committed 清除 ──────────

test("PendingLoadJournal：进 parsing 前原子写入，committed 后清除并留 terminal 标记", async () => {
  const storage = createMemoryStorageBackend();
  const journal = await createPendingLoadJournal({ storage, nowWallClock: () => 0 });
  const ctx = setup({ journal });

  // 卡住解析：观察 parsing 前的 journal 落盘。
  let releaseLoad;
  ctx.adapter.deferLoad = new Promise((resolve) => { releaseLoad = resolve; });
  const { attemptId, done } = ctx.runtime.selectModel("model-a");
  const watch = settleWatch(done);
  const inParsing = await drive(ctx, () => ctx.runtime.snapshot().pending?.state === LoadAttemptState.PARSING);
  assert.equal(inParsing, true);
  const entry = journal.readPendingEntry();
  assert.equal(entry.attemptId, attemptId, "parsing 前已原子写入 journal（§9.4）");
  assert.equal(entry.phase, "parsing");
  assert.equal(entry.modelId, "model-a");

  releaseLoad();
  ctx.adapter.deferLoad = null;
  const committed = await drive(ctx, () => watch.settled);
  assert.equal(committed, true);
  assert.equal(watch.result.outcome, "committed");
  assert.equal(journal.readPendingEntry(), null, "committed 后 journal 清除");
  assert.equal(journal.readLastTerminal().terminalState, "committed");
  assert.equal(journal.readLastTerminal().attemptId, attemptId);
  ctx.runtime.dispose();
});

// ── 9. 资源估算器与低资源切换路径 ─────────────────────────────

test("估算器：B/A/T/M/F/k/ε 分量可诊断；predictedPeak 超预算 → allowed=false", () => {
  const bytes = makeModelBytes({ nodeCount: 6, vertexCount: 300, texture: { width: 128, height: 64 } });
  const stats = computeStructuralStats(bytes);
  assert.equal(stats.nodeCount, 6);
  assert.equal(stats.vertexCount, 300);
  assert.equal(stats.texturePixels, 128 * 64, "纹理像素从 PNG 头嗅探");
  assert.equal(stats.textureBytesVerified, true);

  const estimate = estimateModelResources(stats);
  assert.equal(estimate.bBytes, bytes.byteLength);
  assert.equal(estimate.copyBytes, DEFAULT_RESOURCE_ESTIMATE_PARAMS.kCopyFactor * bytes.byteLength);
  assert.ok(estimate.aBytes > 0 && estimate.tBytes > 0 && estimate.mBytes > 0, "A/T/M 分量");
  assert.equal(estimate.modelFootprintBytes, estimate.copyBytes + estimate.aBytes + estimate.tBytes + estimate.mBytes);

  const generous = evaluateSwitchBudget({
    candidate: estimate,
    resident: estimate,
    resourceBudgetBytes: 512 * 1024 * 1024,
  });
  assert.equal(generous.allowed, true);
  assert.equal(generous.mode, "transactional");
  assert.ok(generous.predictedPeakBytes <= generous.effectiveLimitBytes, "§4.8 predictedPeak <= safetyFactor × budget");

  const tight = evaluateSwitchBudget({ candidate: estimate, resident: estimate, resourceBudgetBytes: 1024 });
  assert.equal(tight.allowed, false);
  assert.equal(tight.mode, "low-resource");
  assert.equal(tight.reason, "predicted-peak-exceeds-budget");
});

test("predictedPeak 超预算 → §11.3 低资源切换：先释放旧模型再解析新模型（无回滚目标）", async () => {
  const ctx = setup({ resourceBudgetBytes: 1024 }); // 极小预算，必然低资源路径
  await selectAndSettle(ctx, "model-a");
  const engineA = ctx.adapter.engines[0];
  const logBefore = ctx.adapter.orderLog.length;

  const second = await selectAndSettle(ctx, "model-b");
  assert.equal(second.outcome, "committed");
  assert.equal(ctx.runtime.snapshot().current.modelId, "model-b");
  // 低资源路径：旧模型在新模型解析前释放（§11.3：暂停→释放旧→加载新）。
  const switchLog = ctx.adapter.orderLog.slice(logBefore);
  const disposeIndex = switchLog.indexOf("dispose:model-a");
  const loadIndex = switchLog.indexOf("load:model-b");
  assert.ok(disposeIndex >= 0 && loadIndex >= 0 && disposeIndex < loadIndex, `应先释放旧再加载新: ${switchLog}`);
  assert.equal(engineA.disposed, 1);
  assert.equal(ctx.adapter.renderer.disposeCount, 0);
  ctx.runtime.dispose();
});

// ── 10. visibility-probe 前提与判定输入 ──────────────────────

function makeProbeAttempt({ attemptKind = AttemptKind.SWITCH, rollbackTarget = null } = {}) {
  const attempt = new LoadAttempt({
    attemptId: `probe-${Math.random().toString(36).slice(2)}`,
    attemptKind,
    rollbackTarget,
    activePhaseDeadline: 10_000,
    nowMonotonic: 0,
  });
  for (const state of ["validating", "admitted", "loading", "parsing", "uploading", "renderability-probe", "provisional-present", "visibility-probe"]) {
    attempt.transition(state, { nowMonotonic: 0 });
  }
  return attempt;
}

test("探针前提：recovery（rollbackTarget=null）豁免回滚前提；GPU 失效回滚目标硬失败", () => {
  const clock = createClock();
  const surface = createRenderSurfaceController({ nowMonotonic: clock.now, sizeStableWindowMs: 0 });
  surface.acquire(createHost(), "primary");
  const evidence = () => ({ firstFrame: true, drawCalls: 3, boundsIntersectViewport: true, fatalRendererError: false });

  const recoveryAttempt = makeProbeAttempt({ attemptKind: AttemptKind.RECOVERY, rollbackTarget: null });
  const probe = createVisibilityProbeSession({ attempt: recoveryAttempt, surface, sampleFrameEvidence: evidence });
  probe.recordFirstRenderableFrame({ attemptId: recoveryAttempt.attemptId });
  const pre = probe.checkPreconditions(clock.now());
  assert.equal(pre.ok, true);
  assert.equal(pre.rollbackExempt, true, "recovery rollbackTarget=null 豁免回滚前提（§19.1.7）");
  assert.equal(pre.recoveryExempt, true);
  assert.equal(probe.begin(clock.now()).status, "started");

  const invalidated = { gpuInvalidated: true, dispose() {} };
  const switchAttempt = makeProbeAttempt({ attemptKind: AttemptKind.SWITCH, rollbackTarget: invalidated });
  const probe2 = createVisibilityProbeSession({ attempt: switchAttempt, surface, sampleFrameEvidence: evidence });
  probe2.recordFirstRenderableFrame({ attemptId: switchAttempt.attemptId });
  const pre2 = probe2.checkPreconditions(clock.now());
  assert.equal(pre2.ok, false);
  assert.ok(pre2.hardFailures.includes("rollback-target-invalidated"), "存在 rollbackTarget 时必须保留可用资源");
});

test("探针判定：attemptId 归属像素必需；串号证据不通过，归口证据通过", () => {
  const clock = createClock();
  const surface = createRenderSurfaceController({ nowMonotonic: clock.now, sizeStableWindowMs: 0 });
  surface.acquire(createHost(), "primary");
  const attempt = makeProbeAttempt({ rollbackTarget: { gpuInvalidated: false, dispose() {} } });
  const probe = createVisibilityProbeSession({
    attempt,
    surface,
    sampleFrameEvidence: () => ({ firstFrame: true, drawCalls: 3, boundsIntersectViewport: true, fatalRendererError: false }),
  });
  probe.recordFirstRenderableFrame({ attemptId: "other-attempt" }); // 串号 FIRST_RENDERABLE_FRAME 不采信
  assert.equal(probe.begin(clock.now()).status, "precondition-failed", "缺 attemptId 归属的 FIRST_RENDERABLE_FRAME");
  probe.recordFirstRenderableFrame({ attemptId: attempt.attemptId });
  assert.equal(probe.begin(clock.now()).status, "started");

  probe.submitPixelEvidence({ attemptId: "other-attempt", nonBackgroundPixels: 100 }, { latencyMs: 3 });
  assert.equal(probe.poll(clock.now()), "probing", "候选像素必须按 attemptId 归属（§19.1.7）");
  probe.submitPixelEvidence({ attemptId: attempt.attemptId, nonBackgroundPixels: 100 }, { latencyMs: 5 });
  assert.equal(probe.poll(clock.now()), "passed");
  assert.equal(probe.visibilityEvidenceLatencyMs, 5, "异步证据延迟单独计时");
});

// ── 11. RenderSurface 租约 ───────────────────────────────────

test("RenderSurface：单租约冲突报错、release 后可再 acquire、rehost 不重解析、viewport 事件", () => {
  const clock = createClock();
  const events = [];
  const surface = createRenderSurfaceController({ nowMonotonic: clock.now, sizeStableWindowMs: 10 });
  surface.onDidChange((event) => events.push(event.type));

  const lease = surface.acquire(createHost(), "chat");
  assert.equal(surface.hasActiveLease(), true);
  assert.throws(() => surface.acquire(createHost(), "body-editor"), (e) => e.code === "lease_conflict");

  surface.rehost(lease, createHost({ width: 800, height: 600 }));
  assert.equal(surface.getViewport().width, 800, "同一 lease 换宿主不重解析（§14.3）");
  assert.equal(surface.isSizeStable(clock.now()), false, "尺寸变化后稳定窗口期内不稳定（§19.1）");
  clock.advance(11);
  assert.equal(surface.isSizeStable(clock.now()), true);

  surface.updateViewport({ width: 800, height: 600, dpr: 2 });
  assert.ok(events.includes("viewport"), "尺寸/DPI 变化事件");
  surface.release(lease);
  assert.equal(surface.hasActiveLease(), false);
  const again = surface.acquire(createHost(), "primary");
  assert.equal(again.released, false);
  assert.ok(events.includes("released") && events.includes("rehost") && events.includes("acquired"));
});

// ── 12. diagnostics 约束 ─────────────────────────────────────

test("diagnostics：目录外事件拒绝、绝对路径/二进制拒绝、ring 有界、重复合并", () => {
  const clock = createClock();
  const diag = createDiagnostics({ nowMonotonic: clock.now, ringCapacity: 3 });
  assert.throws(() => diag.emit("NOT_AN_EVENT"), (e) => e.code === "event_unknown");
  assert.throws(
    () => diag.emit(DiagnosticEvent.FIRST_FRAME, { detail: { note: "C:\\Users\\x\\model.vrm" } }),
    (e) => e.code === "diagnostic_path_forbidden",
  );
  assert.throws(
    () => diag.emit(DiagnosticEvent.FIRST_FRAME, { detail: { bytes: new Uint8Array(4) } }),
    (e) => e.code === "diagnostic_binary_forbidden",
  );

  diag.emit(DiagnosticEvent.VRM_PARSE_START, { correlationId: "c1", modelId: "m1", phase: "parsing" });
  diag.emit(DiagnosticEvent.FIRST_VISIBLE_FRAME, { correlationId: "c1", modelId: "m1", phase: "visibility-probe", result: "failed", errorCode: "timeout" });
  diag.emit(DiagnosticEvent.FIRST_VISIBLE_FRAME, { correlationId: "c1", modelId: "m1", phase: "visibility-probe", result: "failed", errorCode: "timeout" });
  assert.equal(diag.size, 2, "重复错误采样合并（§23.3.6）");
  assert.equal(diag.latest(1)[0].repeatCount, 2);

  diag.emit(DiagnosticEvent.CONTEXT_LOST, { modelId: "m1" });
  diag.emit(DiagnosticEvent.RECOVERY_START, { modelId: "m1" });
  assert.equal(diag.size, 3, "ring buffer 有界");
  assert.equal(diag.droppedCount, 1);
  const events = diag.list().map((entry) => entry.event);
  assert.deepEqual(events, [DiagnosticEvent.FIRST_VISIBLE_FRAME, DiagnosticEvent.CONTEXT_LOST, DiagnosticEvent.RECOVERY_START]);
});

// ── 13. quarantine 接入：engine 键阈值隔离 + 加载前预检 ──────

test("引擎解析失败按 engine 键计数，达阈值 → QUARANTINED；同模型再次选择被加载前预检拦截", async () => {
  const tracker = await createQuarantineTracker({ storage: null, nowWallClock: () => 0 });
  const ctx = setup({ quarantineTracker: tracker, gpuFingerprint: null });

  ctx.adapter.failNextLoad = true;
  const first = await selectAndSettle(ctx, "model-a");
  assert.equal(first.outcome, LoadAttemptState.FAILED, "engine 阈值 2 以下首次只计失败（§9.4）");
  ctx.adapter.failNextLoad = true;
  const second = await selectAndSettle(ctx, "model-a");
  assert.equal(second.outcome, LoadAttemptState.QUARANTINED, "达 engineFailureThreshold → quarantined");

  const opensBeforeThird = ctx.catalog.openCalls.filter((id) => id === "model-a").length;
  const third = await selectAndSettle(ctx, "model-a");
  assert.equal(third.outcome, LoadAttemptState.QUARANTINED, "加载前 structural/engine 预检直接拦截");
  assert.equal(
    ctx.catalog.openCalls.filter((id) => id === "model-a").length,
    opensBeforeThird,
    "被隔离模型不再开字节流",
  );
  ctx.runtime.dispose();
});

test("validating 钩子拒绝：无 tracker → REJECTED；有 tracker → 结构类单次即 QUARANTINED", async () => {
  const reject = { ok: false, code: "admission_violation", violations: ["limit_nodes"] };
  const plain = setup({ validateCandidateHook: async () => reject });
  const rejected = await selectAndSettle(plain, "model-a");
  assert.equal(rejected.outcome, LoadAttemptState.REJECTED);
  plain.runtime.dispose();

  const tracker = await createQuarantineTracker({ storage: null, nowWallClock: () => 0 });
  const ctx = setup({ validateCandidateHook: async () => reject, quarantineTracker: tracker });
  const quarantined = await selectAndSettle(ctx, "model-a");
  assert.equal(quarantined.outcome, LoadAttemptState.QUARANTINED);
  const quarantineEvents = ctx.diagnostics.list({ event: DiagnosticEvent.MODEL_QUARANTINED });
  assert.equal(quarantineEvents.length, 1);
  assert.equal(quarantineEvents[0].result, "quarantined");
  ctx.runtime.dispose();
});

console.log("test_avatar_runtime_p4: all assertions passed");
