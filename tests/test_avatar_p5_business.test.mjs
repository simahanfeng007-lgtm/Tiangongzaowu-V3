// Avatar P5 业务接入测试（方案 §8.5/§14.3/§15/§16/§17/§24，阶段 P5 退出条件）。
// 覆盖：adapter（biaoxian→WireBodyAction/sessionEpoch 降级）、scheduler（TTL 前端时钟/
// latest-wins/gesture FIFO+TTL/Qmax 溢出/stop/幂等/降采样/legacy 会话）、profile（语义边界/
// 持久化往返）、avatar-service（模式互斥/切换清理）、rehost 不重解析、TTS 单调时钟/单一所有者、
// import（Redistribution_Prohibited 提示/登记后签发 Token/grant 不作引擎输入）。
// 运行：node --test tests/test_avatar_p5_business.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { actionIdempotencyKey } from "../app/frontend-v2/renderer/avatar/contracts.mjs";
import { createBiaoxianAdapter, INSTANCE_KEY_SOURCE, WIRE_BODY_ACTION_SCHEMA } from "../app/frontend-v2/renderer/avatar/body-performance-adapter.mjs";
import { createBodyCommandScheduler, SCHEDULER_QMAX } from "../app/frontend-v2/renderer/avatar/body-command-scheduler.mjs";
import { createBodyRuntimeProfileStore, sanitizeProfile, PROFILE_DEFAULTS } from "../app/frontend-v2/renderer/avatar/body-runtime-profile.mjs";
import { createServiceRegistry } from "../app/frontend-v2/renderer/avatar/service-registry.mjs";
import {
  AVATAR_MODE_FLAG_KEY,
  AVATAR_SERVICE_ID,
  AvatarRenderMode,
  createAvatarService,
} from "../app/frontend-v2/renderer/avatar/avatar-service.mjs";
import { createAvatarStore } from "../app/frontend-v2/renderer/avatar/avatar-store.mjs";
import { createThemePresentationSync, presentationForTheme } from "../app/frontend-v2/renderer/avatar/theme-presentation.mjs";
import { SpeechEventKind, createSpeechEventForwarder } from "../app/frontend-v2/renderer/avatar/speech-event-forwarder.mjs";
import { createAvatarImportController, REDISTRIBUTION_PROHIBITED_NOTICE } from "../app/frontend-v2/renderer/avatar/avatar-import-controller.mjs";
import { createRenderSurfaceController } from "../app/frontend-v2/renderer/avatar/render-surface-controller.mjs";
import { createDiagnostics } from "../app/frontend-v2/renderer/avatar/diagnostics.mjs";
import { createAvatarRuntime } from "../app/frontend-v2/renderer/avatar/avatar-runtime.mjs";
import { EngineEvent, createEngineEventSink } from "../app/frontend-v2/renderer/avatar/engines/avatar-engine-contract.mjs";
import { createAssetRegistry } from "../app/frontend-v2/renderer/avatar/asset-registry.mjs";
import { createTokenIssuer } from "../app/frontend-v2/renderer/avatar/validated-asset-token.mjs";
import { createMemoryStorageBackend } from "../app/frontend-v2/renderer/avatar/storage-adapter.mjs";
import { canonicalSha256, sha256HexSync } from "../app/frontend-v2/renderer/avatar/canonical-hash.mjs";

// ── 测试用具 ────────────────────────────────────────────────

function createClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    advance: (ms) => { t += ms; return t; },
    set: (ms) => { t = ms; },
  };
}

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

async function drive(ctx, cond, { maxFrames = 400, stepMs = 16 } = {}) {
  for (let i = 0; i < maxFrames; i += 1) {
    ctx.clock.advance(stepMs);
    ctx.raf.pump(ctx.clock.now());
    await tick0();
    await tick0();
    if (cond()) return true;
  }
  return false;
}

function createMemoryFlagStorage() {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
    removeItem: (key) => { map.delete(key); },
  };
}

// 记录型 sink（scheduler 的出口 = AvatarRuntime.applyPerformance 的替身）
function createRecordingSink() {
  const received = [];
  return {
    received,
    applyPerformance(wire) { received.push(wire); },
  };
}

// ── GLB 字节构造（最小合法容器，与 P4 测试同形同源）──────────
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
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  dv.setUint32(16, width);
  dv.setUint32(20, height);
  return bytes;
}

function makeModelBytes(meta = { title: "mock", author: "tester", licenseName: "CC0", commercialUssageName: "Allow", allowedUserName: "Everyone" }) {
  const json = {
    asset: { version: "2.0" },
    extensions: { VRM: { meta } },
    extensionsUsed: ["VRM"],
    nodes: [{ name: "n0" }, { name: "n1" }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, targets: [{ POSITION: 0 }] }] }],
    accessors: [{ componentType: 5126, count: 2, type: "VEC3", bufferView: 0 }],
    skins: [{ joints: [0, 1] }],
    animations: [{ samplers: [{ input: 0 }] }],
    buffers: [{ byteLength: 33 }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 33 }],
    images: [{ mimeType: "image/png", bufferView: 0 }],
  };
  return new Uint8Array(buildGlb(json, pngBytes(64, 64)));
}

// ═══ A. body-performance-adapter ═══════════════════════════

test("P5-A1 adapter: biaoxian→WireBodyAction 字段完整（§15.1）", () => {
  const adapter = createBiaoxianAdapter({ getBackendInstanceId: () => "backend-1" });
  const wire = adapter.wireFromBiaoxian(
    { expression: "soft", gaze: "user", posture: "attentive", gesture: "co_speech", tail: "calm", intensity: 0.45, duration: 3.5, source: "llm" },
    { turnId: "turn-9", sequence: 7, sourceCreatedAt: 1785000000000 },
  );
  assert.equal(wire.schema, WIRE_BODY_ACTION_SCHEMA);
  assert.equal(wire.backendInstanceId, "backend-1");
  assert.equal(wire.sessionEpoch, null);
  assert.equal(wire.turnId, "turn-9");
  assert.equal(wire.sequence, 7);
  assert.equal(wire.sourceCreatedAt, 1785000000000); // 仅诊断透传
  assert.equal(wire.ttlMs, 30_000);
  assert.equal(wire.priority, "normal");
  assert.equal(wire.posture, "attentive");
  assert.equal(wire.gesture, "co_speech");
  assert.deepEqual(wire.gaze, { target: "user" });
  assert.deepEqual(wire.expression, { name: "soft", intensity: 0.45 });
  assert.equal(wire.durationMs, 3500); // biaoxian.duration 秒 → 毫秒
  assert.equal(wire.intensity, 0.45);
  assert.equal(wire.extras.tail, "calm");
  assert.equal(wire.extras.instanceKeySource, INSTANCE_KEY_SOURCE.BACKEND);
  assert.equal(actionIdempotencyKey(wire), "backend:backend-1:turn-9:7");
  assert.ok(Object.isFrozen(wire));
});

test("P5-A2 adapter: 缺 backendInstanceId 时 sessionEpoch 降级（§15.4）", () => {
  const legacy = createBiaoxianAdapter({
    getBackendInstanceId: () => null,
    getSessionEpoch: () => "epoch-5",
  });
  const wire = legacy.wireFromBiaoxian({ expression: "soft", source: "llm" }, { turnId: "turn-9", sequence: 7 });
  assert.equal(wire.backendInstanceId, null);
  assert.equal(wire.sessionEpoch, "epoch-5");
  assert.equal(wire.extras.instanceKeySource, INSTANCE_KEY_SOURCE.SESSION_EPOCH_LEGACY);
  assert.equal(actionIdempotencyKey(wire), "legacy:epoch-5:turn-9:7");

  const missing = createBiaoxianAdapter({ getBackendInstanceId: () => null, getSessionEpoch: () => null });
  assert.throws(
    () => missing.wireFromBiaoxian({ expression: "soft" }, {}),
    (error) => error.code === "instance_identity_missing",
  );
});

// ═══ B. body-command-scheduler ═════════════════════════════

test("P5-B1 scheduler: TTL 由前端单调时钟计算，后端 sourceCreatedAt 不影响过期（§15.1/§15.3）", () => {
  const clock = createClock(1000);
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  // sourceCreatedAt 极早（后端时钟偏差/重放），仍按前端时钟给 deadline → 不过期
  const ancient = scheduler.submit({ posture: "stand", ttlMs: 100, sourceCreatedAt: -1_000_000_000 });
  assert.equal(ancient.accepted, true);
  scheduler.pump();
  assert.equal(sink.received.length, 1);
  assert.equal(sink.received[0].sourceCreatedAt, -1_000_000_000); // 诊断透传保留
  // ttlMs=50 的动作在前端时钟走过 51ms 后过期丢弃
  scheduler.submit({ posture: "sit", ttlMs: 50 });
  clock.advance(51);
  scheduler.pump();
  assert.equal(sink.received.length, 1); // sit 未执行
  assert.equal(scheduler.counters.expired, 1);
});

test("P5-B2 scheduler: gaze/expression latest-wins、posture 状态型只留最新（§15.2）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  scheduler.submit({ gaze: { target: "a" }, ttlMs: 1000 });
  scheduler.submit({ gaze: { target: "b" }, ttlMs: 1000 });
  scheduler.submit({ expression: { name: "e1" }, ttlMs: 1000 });
  scheduler.submit({ expression: { name: "e2" }, ttlMs: 1000 });
  scheduler.submit({ posture: "p1", ttlMs: 1000 });
  scheduler.submit({ posture: "p2", ttlMs: 1000 });
  const result = scheduler.pump();
  assert.equal(result.executed, 3); // 每类只执行最新一条
  const gazes = sink.received.filter((w) => w.gaze);
  const exprs = sink.received.filter((w) => w.expression);
  const postures = sink.received.filter((w) => w.posture);
  assert.deepEqual(gazes.map((w) => w.gaze.target), ["b"]);
  assert.deepEqual(exprs.map((w) => w.expression.name), ["e2"]);
  assert.deepEqual(postures.map((w) => w.posture), ["p2"]);
  assert.equal(scheduler.counters.superseded, 3);
});

test("P5-B3 scheduler: gesture 有界 FIFO + TTL（§15.2/§15.3）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  scheduler.submit({ gesture: "g1", ttlMs: 10_000 });
  scheduler.submit({ gesture: "g2", ttlMs: 5 });
  scheduler.submit({ gesture: "g3", ttlMs: 10_000 });
  clock.advance(10); // g2 过期
  const result = scheduler.pump();
  assert.equal(result.executed, 2);
  assert.equal(result.expired, 1);
  assert.deepEqual(sink.received.map((w) => w.gesture), ["g1", "g3"]); // FIFO 次序保持
});

test("P5-B4 scheduler: Qmax 溢出丢弃最低优先级（§15.3）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  assert.equal(SCHEDULER_QMAX, 32);
  for (let i = 0; i < 32; i += 1) {
    scheduler.submit({ gesture: `g${i}`, ttlMs: 10_000, backendInstanceId: "b1", turnId: `t${i}`, sequence: 0 });
  }
  assert.equal(scheduler.pendingCount(), 32);
  // posture（rank 60）高于 gesture（rank 50）→ 溢出丢最旧 gesture g0
  scheduler.submit({ posture: "stand", ttlMs: 10_000, backendInstanceId: "b1", turnId: "tp", sequence: 0 });
  assert.equal(scheduler.pendingCount(), 32);
  assert.equal(scheduler.counters.overflowDropped, 1);
  scheduler.pump();
  const gestures = sink.received.filter((w) => w.gesture).map((w) => w.gesture);
  assert.equal(gestures.includes("g0"), false); // g0 被丢弃
  assert.equal(gestures.length, 31);
  assert.ok(sink.received.some((w) => w.posture === "stand"));
});

test("P5-B5 scheduler: stop 最高优先级清空待执行（§15.2）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  scheduler.submit({ gesture: "g1", ttlMs: 10_000 });
  scheduler.submit({ posture: "stand", ttlMs: 10_000 });
  const stop = scheduler.submit({ stop: true, ttlMs: 10_000 });
  assert.equal(stop.reason, "stop");
  assert.equal(scheduler.pendingCount(), 0);
  assert.equal(scheduler.counters.stopFlushed, 1);
  assert.equal(sink.received.length, 1); // stop 立即转发
  assert.equal(sink.received[0].stop, true);
  const result = scheduler.pump();
  assert.equal(result.executed, 0); // 队列已清空
});

test("P5-B6 scheduler: 幂等去重，同键不重复执行（§15.4）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  const wire = { posture: "stand", ttlMs: 10_000, backendInstanceId: "b1", turnId: "t1", sequence: 3 };
  assert.equal(scheduler.submit(wire).accepted, true);
  assert.equal(scheduler.submit({ ...wire }).reason, "duplicate");
  scheduler.pump();
  assert.equal(sink.received.length, 1);
  assert.equal(scheduler.counters.deduped, 1);
});

test("P5-B7 scheduler: speech-energy 按频率降采样，禁止无限排队（§15.2）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink, speechEnergyMinIntervalMs: 50 });
  const first = scheduler.submit({ type: "speech-energy", speechEnergy: 0.1, ttlMs: 1000 });
  assert.equal(first.reason, "forwarded"); // 首个立即转发
  for (let i = 2; i <= 10; i += 1) {
    assert.equal(scheduler.submit({ type: "speech-energy", speechEnergy: i / 10, ttlMs: 1000 }).reason, "downsampled-pending");
  }
  assert.equal(scheduler.pendingCount(), 1); // 只保留最新一条，不排队
  assert.equal(scheduler.counters.downsampled, 9);
  scheduler.pump();
  const energies = sink.received.map((w) => w.speechEnergy);
  assert.deepEqual(energies, [0.1, 1]); // 立即的 0.1 + 暂存最新 1.0
});

test("P5-B8 scheduler: legacy 降级会话——等快照、拒旧 epoch（§15.4）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink });
  scheduler.beginSession({ sessionEpoch: "ep1" });
  assert.equal(scheduler.session.awaitingSnapshot, true);
  // 等待全量快照期间一律拒绝
  assert.equal(scheduler.submit({ posture: "p", ttlMs: 1000, sessionEpoch: "ep1", turnId: "t", sequence: 0 }).reason, "stale-session");
  scheduler.markSnapshotReceived();
  assert.equal(scheduler.session.awaitingSnapshot, false);
  assert.equal(scheduler.submit({ posture: "p", ttlMs: 1000, sessionEpoch: "ep1", turnId: "t", sequence: 0 }).accepted, true);
  // 旧连接补发（旧 epoch）拒绝
  assert.equal(scheduler.submit({ posture: "q", ttlMs: 1000, sessionEpoch: "ep0", turnId: "t", sequence: 1 }).reason, "stale-session");
  scheduler.pump();
  assert.equal(sink.received.length, 1);
  assert.equal(sink.received[0].posture, "p");
  // backendInstanceId 正式会话：无需等快照；异 instance 拒绝
  scheduler.beginSession({ backendInstanceId: "b1" });
  assert.equal(scheduler.submit({ posture: "x", ttlMs: 1000, backendInstanceId: "b1", turnId: "t", sequence: 0 }).accepted, true);
  assert.equal(scheduler.submit({ posture: "y", ttlMs: 1000, backendInstanceId: "b0", turnId: "t", sequence: 0 }).reason, "stale-session");
});

// ═══ C. body-runtime-profile ═══════════════════════════════

test("P5-C1 profile: 只调幅度/节奏/物理感/镜头/质量，拒绝覆盖后端语义（§16.3）", () => {
  const clamped = sanitizeProfile({ motionScale: 99, gazeSpeed: 0.01, qualityTier: "extreme", unknownKey: 1 });
  assert.equal(clamped.motionScale, 2); // 钳制上限
  assert.equal(clamped.gazeSpeed, 0.1); // 钳制下限
  assert.equal(clamped.qualityTier, "auto"); // 非白名单回退
  assert.equal("unknownKey" in clamped, false);
  assert.throws(() => sanitizeProfile({ posture: "sit" }), (error) => error.code === "profile_semantic_override");
  assert.throws(() => sanitizeProfile({ gesture: "wave" }), (error) => error.code === "profile_semantic_override");
  assert.throws(() => sanitizeProfile({ expression: { name: "happy" } }), (error) => error.code === "profile_semantic_override");
});

test("P5-C2 profile: 持久化往返（注入式 storage）", async () => {
  const storage = createMemoryFlagStorage();
  const store = createBodyRuntimeProfileStore({ storage });
  await store.save({ motionScale: 1.5, springDamping: 0.8 });
  const reopened = createBodyRuntimeProfileStore({ storage });
  const loaded = await reopened.load();
  assert.equal(loaded.motionScale, 1.5);
  assert.equal(loaded.springDamping, 0.8);
  assert.equal(loaded.gestureScale, PROFILE_DEFAULTS.gestureScale); // 未设置项回默认
});

test("P5-C3 profile: applyToRuntime 只经 applyProfile 公共接口，不带语义键", async () => {
  const storage = createMemoryFlagStorage();
  const store = createBodyRuntimeProfileStore({ storage });
  await store.save({ motionScale: 1.2 });
  const applied = [];
  store.applyToRuntime({ applyProfile: (profile) => applied.push(profile) });
  assert.equal(applied.length, 1);
  assert.equal(applied[0].motionScale, 1.2);
  for (const key of ["posture", "gesture", "expression", "gaze"]) {
    assert.equal(key in applied[0], false);
  }
});

// ═══ D. avatar-service（模式互斥/切换清理/单例/flag）════════

function createFakeDirectProduct(clock, { withController = true } = {}) {
  const surfaceController = createRenderSurfaceController({ nowMonotonic: clock.now, sizeStableWindowMs: 0 });
  const calls = { dispose: 0, engineAttach: [] };
  const engineAdapter = {
    attachSurface: ({ host }) => calls.engineAttach.push(host?.id ?? null),
    detachSurface: () => {},
  };
  const runtime = {
    attachSurface({ host, mode }) {
      const lease = surfaceController.acquire(host, mode);
      engineAdapter.attachSurface({ host, leaseId: lease.leaseId });
      return lease;
    },
    detachSurface(lease) {
      surfaceController.release(lease ?? surfaceController.currentLease());
      return true;
    },
    dispose() { calls.dispose += 1; },
    snapshot: () => ({ state: "running" }),
    subscribe: () => () => {},
  };
  return { runtime, surfaceController: withController ? surfaceController : null, engineAdapter, calls };
}

test("P5-D1 avatar-service: 模式互斥——direct 运行时 legacy 不可启动（§24）", () => {
  const clock = createClock();
  const registry = createServiceRegistry();
  const service = createAvatarService({
    registry,
    flagStorage: createMemoryFlagStorage(),
    nowMonotonic: clock.now,
    createDirectRuntime: () => createFakeDirectProduct(clock),
  });
  assert.equal(service.readFlagMode(), AvatarRenderMode.DIRECT); // §26 P6：direct 为默认渲染模式（legacy 仅诊断回退）
  service.setMode(AvatarRenderMode.DIRECT);
  assert.equal(service.isDirectActive(), true);
  assert.throws(() => service.startMode(AvatarRenderMode.LEGACY_IFRAME), (error) => error.code === "mode_conflict");
  assert.equal(service.getMode(), AvatarRenderMode.DIRECT);
  service.dispose();
});

test("P5-D2 avatar-service: 模式切换统一走生命周期清理，flag 持久化（§24）", () => {
  const clock = createClock();
  const flagStorage = createMemoryFlagStorage();
  const product = createFakeDirectProduct(clock);
  const service = createAvatarService({
    registry: createServiceRegistry(),
    flagStorage,
    nowMonotonic: clock.now,
    createDirectRuntime: () => product,
  });
  service.setMode(AvatarRenderMode.DIRECT);
  assert.equal(product.calls.dispose, 0);
  service.setMode(AvatarRenderMode.LEGACY_IFRAME);
  assert.equal(product.calls.dispose, 1); // direct 运行时经统一清理路径释放
  assert.equal(service.getRuntime(), null);
  assert.equal(flagStorage.getItem(AVATAR_MODE_FLAG_KEY), AvatarRenderMode.LEGACY_IFRAME);
  service.setMode(AvatarRenderMode.OFF);
  assert.equal(service.getMode(), AvatarRenderMode.OFF);
  service.dispose();
});

test("P5-D3 avatar-service: registry 单例——重复创建被拒绝（§20.1）", () => {
  const clock = createClock();
  const registry = createServiceRegistry();
  const deps = () => ({
    registry,
    flagStorage: createMemoryFlagStorage(),
    nowMonotonic: clock.now,
    createDirectRuntime: () => createFakeDirectProduct(clock),
  });
  const first = createAvatarService(deps());
  assert.ok(registry.hasService(AVATAR_SERVICE_ID));
  assert.throws(() => createAvatarService(deps()), /已注册/);
  first.dispose();
});

// ═══ E. rehost：Surface 迁移不重解析（§14.3）═══════════════

function createMockEngineForRehost() {
  const sink = createEngineEventSink();
  const adapter = {
    engineVersion: "mock-engine-rehost",
    loadCandidateCount: 0,
    attachHosts: [],
    on: (event, listener) => sink.on(event, listener),
    off: (event, listener) => sink.off(event, listener),
    async loadCandidate(bytes, { label, attemptId }) {
      adapter.loadCandidateCount += 1;
      const handle = { label, attemptId, byteLength: bytes.byteLength };
      return handle;
    },
    async uploadCandidate() {},
    renderCandidateFrame(handle) {
      sink.emit(EngineEvent.FIRST_RENDERABLE_FRAME, { attemptId: handle.attemptId });
      return { drawCalls: 5 };
    },
    presentCandidate() {},
    concealCandidate() {},
    restorePresented() {},
    promoteCandidate() {},
    disposeModel() {},
    discardInvalidatedModel() {},
    renderFrame() { return true; },
    update() {},
    getStats() { return { drawCalls: 5 }; },
    candidateBoundsIntersectViewport: () => true,
    hasFatalRendererError: () => false,
    attachSurface({ host }) { adapter.attachHosts.push(host?.id ?? null); },
    detachSurface() {},
    disposeEngine() {},
  };
  return adapter;
}

test("P5-E1 rehost: 聊天页↔身体页迁移同一 lease，不重解析模型（§14.3/§27.3.5）", async () => {
  const clock = createClock();
  const raf = createFakeRaf();
  const engine = createMockEngineForRehost();
  const surfaceController = createRenderSurfaceController({ nowMonotonic: clock.now, sizeStableWindowMs: 0 });
  const modelBytes = makeModelBytes();
  const contentHash = sha256HexSync(modelBytes);
  const catalog = {
    async describeModel(modelId) {
      return Object.freeze({ modelId, contentHash, byteLength: modelBytes.byteLength });
    },
    async openModelBytes() { return modelBytes.slice(); },
  };
  const runtime = createAvatarRuntime({
    engineAdapter: engine,
    assetSource: catalog,
    nowMonotonic: clock.now,
    requestAnimationFrame: raf.request,
    cancelAnimationFrame: raf.cancel,
    surfaceController,
    diagnostics: createDiagnostics({ nowMonotonic: clock.now }),
  });
  const service = createAvatarService({
    registry: createServiceRegistry(),
    flagStorage: createMemoryFlagStorage(),
    nowMonotonic: clock.now,
    createDirectRuntime: () => ({ runtime, surfaceController, engineAdapter: engine }),
  });
  service.setMode(AvatarRenderMode.DIRECT);
  const hostChat = { id: "chat-host", isVisible: () => true, getViewport: () => ({ width: 400, height: 300, dpr: 1 }) };
  const hostBody = { id: "body-host", isVisible: () => true, getViewport: () => ({ width: 800, height: 600, dpr: 1 }) };
  const lease = service.attachSurface(hostChat, "chat");
  runtime.selectModel("model-a");
  const committed = await drive({ clock, raf }, () => runtime.snapshot().current?.modelId === "model-a");
  assert.equal(committed, true);
  assert.equal(engine.loadCandidateCount, 1); // 初始解析一次

  // 聊天页 → 身体页：同一 lease 迁移宿主
  assert.equal(service.rehostSurface(hostBody), true);
  assert.equal(surfaceController.currentLease().leaseId, lease.leaseId); // 同一 lease 未换
  assert.equal(engine.attachHosts[engine.attachHosts.length - 1], "body-host"); // 引擎层移动 Canvas
  assert.equal(engine.loadCandidateCount, 1); // 不重解析（§14.3）
  assert.equal(runtime.snapshot().current?.modelId, "model-a");

  // 身体页 → 聊天页：再次迁移仍不重解析
  assert.equal(service.rehostSurface(hostChat), true);
  assert.equal(surfaceController.currentLease().leaseId, lease.leaseId);
  assert.equal(engine.loadCandidateCount, 1);

  runtime.dispose();
  service.dispose();
});

// ═══ F. TTS（§17 单调时钟/单一所有者/无第二播放器）═════════

test("P5-F1 TTS: speech 事件一律单调时钟，事件自带时间不采信（§17）", () => {
  const clock = createClock(500);
  const submitted = [];
  const forwarder = createSpeechEventForwarder({ nowMonotonic: clock.now, submit: (wire) => submitted.push(wire) });
  const owner = forwarder.claimOwner("tts-owner");
  const start = owner.speechStart({ at: -9999 }); // 事件自带时间字段被忽略
  assert.equal(start.atMonotonic, 500);
  assert.equal(submitted[0].speechEventAtMonotonic, 500);
  clock.advance(7);
  const energy = owner.speechEnergy(0.8);
  assert.equal(energy.atMonotonic, 507);
  assert.equal(submitted[1].speechEnergy, 0.8);
  assert.equal(submitted[1].speechEventAtMonotonic, 507);
  clock.advance(3);
  const stop = owner.speechStop();
  assert.equal(stop.atMonotonic, 510);
  assert.equal(submitted[2].speaking, false);
});

test("P5-F2 TTS: 单一所有者——第二所有者申领被拒；stop 释放后可重新 claim（§17）", () => {
  const clock = createClock();
  const forwarder = createSpeechEventForwarder({ nowMonotonic: clock.now, submit: () => {} });
  const owner = forwarder.claimOwner("tts-owner");
  assert.throws(() => forwarder.claimOwner("second-player"), (error) => error.code === "speech_owner_conflict");
  assert.equal(forwarder.activeOwnerId, "tts-owner");
  owner.speechStop();
  assert.equal(forwarder.activeOwnerId, null);
  const next = forwarder.claimOwner("second-player"); // stop 释放后可重新申领
  assert.equal(forwarder.activeOwnerId, "second-player");
  next.release();
  // 结构化证明：无第二 TTS 播放器——模块没有任何播放能力
  assert.equal(forwarder.ownsTtsPlayback, false);
  assert.equal(typeof forwarder.play, "undefined");
  assert.equal(typeof forwarder.speak, "undefined");
});

test("P5-F3 TTS: 事件经 scheduler 进入 AvatarRuntime 链（speech-start/energy/stop）", () => {
  const clock = createClock();
  const sink = createRecordingSink();
  const scheduler = createBodyCommandScheduler({ nowMonotonic: clock.now, sink, speechEnergyMinIntervalMs: 0 });
  const forwarder = createSpeechEventForwarder({ nowMonotonic: clock.now, submit: (wire) => { scheduler.submit(wire); scheduler.pump(); } });
  const owner = forwarder.claimOwner("tts-owner");
  owner.speechStart();
  owner.speechEnergy(0.6);
  owner.speechStop();
  assert.equal(sink.received[0].speaking, true);
  assert.equal(sink.received[1].speechEnergy, 0.6);
  assert.equal(sink.received[2].speaking, false);
  assert.equal(sink.received[2].speechEnergy, 0);
});

test("P5-F4 TTS: window 桥订阅转发（非侵入），detach 后不再接收", () => {
  const clock = createClock();
  const submitted = [];
  const forwarder = createSpeechEventForwarder({ nowMonotonic: clock.now, submit: (wire) => submitted.push(wire) });
  const listeners = new Map();
  const target = {
    addEventListener: (type, fn) => listeners.set(type, [...(listeners.get(type) ?? []), fn]),
    removeEventListener: (type, fn) => listeners.set(type, (listeners.get(type) ?? []).filter((f) => f !== fn)),
    emit: (type, detail) => { for (const fn of listeners.get(type) ?? []) fn({ detail }); },
    count: (type) => (listeners.get(type) ?? []).length,
  };
  const detach = forwarder.attachWindowBridge({ target, ownerId: "tts-owner" });
  assert.equal(target.count("tiangong-speech"), 1);
  target.emit("tiangong-speech", { kind: "start" });
  target.emit("tiangong-speech", { kind: "energy", energy: 0.5 });
  target.emit("tiangong-speech", { kind: "stop" });
  assert.deepEqual(submitted.map((w) => w.type), [SpeechEventKind.START, SpeechEventKind.ENERGY, SpeechEventKind.STOP]);
  detach();
  assert.equal(target.count("tiangong-speech"), 0);
  target.emit("tiangong-speech", { kind: "start" });
  assert.equal(submitted.length, 3); // detach 后不再转发
});

// ═══ G. 自定义导入（§8.5）═══════════════════════════════════

function makeGrantView(bytes, overrides = {}) {
  return {
    grantId: "crg_test_1",
    attemptId: "att_imp_1",
    candidateId: "cand_imp_1",
    nonce: "nonce_imp_1",
    contentHash: sha256HexSync(bytes),
    byteLength: bytes.byteLength,
    issuerEpoch: 0,
    singleUse: true,
    ...overrides,
  };
}

async function setupImport({ bytes, chooseFileImpl = null, commitImpl = null } = {}) {
  const orderLog = [];
  const calls = { chooseFile: 0, readCandidate: [], commit: [], selectModel: [] };
  const storage = createMemoryStorageBackend();
  const registry = await createAssetRegistry({ storage, issuerEpoch: 0 });
  const baseRegister = registry.registerAsset.bind(registry);
  registry.registerAsset = (input) => { orderLog.push("register"); return baseRegister(input); };
  // tokenIssuer 是冻结对象：用闭包外观包装以记录调用次序，不改原对象。
  const issuerBase = createTokenIssuer({ registry, issuerEpoch: 0 });
  const tokenIssuer = Object.freeze({
    issueToken: (assetId, opts) => { orderLog.push("issue"); return issuerBase.issueToken(assetId, opts); },
  });
  const runtime = {
    selectModel: (modelId) => { calls.selectModel.push(modelId); return Object.freeze({ attemptId: "att_sel", done: Promise.resolve({}) }); },
  };
  const choose = chooseFileImpl ?? (async () => ({ name: "自定义.vrm", fileRef: "dlg://file/1" }));
  const importedModelId = `model:${sha256HexSync(bytes)}`;
  const commit = commitImpl ?? (async () => ({ assetId: importedModelId, modelId: importedModelId }));
  const controller = createAvatarImportController({
    chooseFile: async () => { calls.chooseFile += 1; return choose(); },
    issueCandidateGrant: async () => makeGrantView(bytes),
    readCandidateBytes: async (grantView) => { calls.readCandidate.push(grantView); return bytes.slice(); },
    commitCandidate: async (input) => { calls.commit.push(input); return commit(input); },
    registry,
    tokenIssuer,
    runtime,
  });
  return { controller, orderLog, calls, registry, tokenIssuer, runtime, importedModelId };
}

test("P5-G1 import: Redistribution_Prohibited 给明确提示且阻断，确认后放行（§10.3/§8.5）", async () => {
  const bytes = makeModelBytes({ title: "t", author: "a", licenseName: "Redistribution_Prohibited", commercialUssageName: "Allow" });
  const { controller, orderLog, calls } = await setupImport({ bytes });
  const blocked = await controller.importCustomModel();
  assert.equal(blocked.status, "license-blocked");
  assert.equal(blocked.code, "redistribution_prohibited");
  assert.ok(blocked.notice.includes("Redistribution_Prohibited")); // 明确提示文案出现
  assert.equal(blocked.notice, REDISTRIBUTION_PROHIBITED_NOTICE);
  assert.equal(blocked.licenseSummary.redistributionProhibited, true);
  assert.equal(typeof blocked.resumeToken, "string");
  assert.equal(controller.getPendingCount(), 1);
  assert.equal(JSON.stringify(blocked).includes("dlg://file/1"), false); // window 结果不带选择器引用/路径
  assert.deepEqual(orderLog, []); // 未登记未签发
  assert.deepEqual(calls.selectModel, []); // 未进入引擎
  // 用户确认后只续接同一已预检候选，不再 choose/read。
  const committed = await controller.importCustomModel({
    acknowledgeLicense: true,
    resumeToken: blocked.resumeToken,
  });
  assert.equal(committed.status, "committed");
  assert.deepEqual(orderLog, ["register", "issue"]);
  assert.equal(calls.chooseFile, 1);
  assert.equal(calls.readCandidate.length, 1);
  assert.equal(calls.commit.length, 1);
  assert.equal(controller.getPendingCount(), 0);
  // token 一次性消费，重复确认不得重复提交。
  const replay = await controller.importCustomModel({
    acknowledgeLicense: true,
    resumeToken: blocked.resumeToken,
  });
  assert.equal(replay.code, "resume_token_invalid");
  assert.equal(calls.commit.length, 1);
});

test("P5-G2 import: 登记原子提交后才签发 Token；registryEntryVersion=1（§8.2/§8.5）", async () => {
  const bytes = makeModelBytes();
  const { controller, orderLog, calls, registry, importedModelId } = await setupImport({ bytes });
  const result = await controller.importCustomModel();
  assert.equal(result.status, "committed");
  assert.equal(result.ok, true);
  assert.deepEqual(orderLog, ["register", "issue"]); // 先登记后签发
  assert.equal(result.registryEntryVersion, 1);
  assert.equal(result.token.registryEntryVersion, 1);
  assert.equal(result.token.assetId, importedModelId);
  assert.equal(result.token.contentHash, sha256HexSync(bytes));
  assert.ok(registry.getRecord(importedModelId) !== null);
  assert.deepEqual(calls.selectModel, [importedModelId]);
});

test("P5-G2b import: 同哈希重复导入复用完全一致的 admitted 登记", async () => {
  const bytes = makeModelBytes();
  const { controller, orderLog, calls, registry, importedModelId } = await setupImport({ bytes });
  const first = await controller.importCustomModel();
  const revisionAfterFirst = registry.revision;
  const second = await controller.importCustomModel();
  assert.equal(first.reused, false);
  assert.equal(second.reused, true);
  assert.equal(second.modelId, importedModelId);
  assert.equal(registry.revision, revisionAfterFirst, "重复 hash 不得再次写 registry");
  assert.deepEqual(orderLog, ["register", "issue", "issue"]);
  assert.equal(calls.commit.length, 2, "主进程幂等 commit 仍复核第二个候选");
  assert.deepEqual(calls.selectModel, [importedModelId, importedModelId]);
});

test("P5-G3 import: grant 不能当引擎输入——selectModel 只收 modelId 字符串（§8.5 类型隔离）", async () => {
  const bytes = makeModelBytes();
  const { controller, calls } = await setupImport({ bytes });
  await controller.importCustomModel();
  // grant 只去了 readCandidateBytes / commitCandidate（其唯一合法用途）；
  // 引擎入口（runtime.selectModel）只拿到登记后的 modelId，不携带 grant 任何字段。
  assert.equal(calls.readCandidate.length, 1);
  assert.equal(calls.readCandidate[0].grantId, "crg_test_1");
  for (const arg of calls.selectModel) {
    assert.equal(typeof arg, "string");
    assert.ok(!arg.includes("crg_test_1"));
    assert.ok(!arg.includes("nonce_imp_1"));
  }
});

test("P5-G4 import: 结构预检拒绝 → rejected，不登记不签发（§9/§8.5）", async () => {
  const bad = new Uint8Array([1, 2, 3, 4]); // GLB 头不足/magic 非法
  const storage = createMemoryStorageBackend();
  const registry = await createAssetRegistry({ storage, issuerEpoch: 0 });
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: 0 });
  const selected = [];
  const controller = createAvatarImportController({
    chooseFile: async () => ({ name: "bad.vrm", fileRef: "dlg://file/2" }),
    issueCandidateGrant: async () => makeGrantView(bad),
    readCandidateBytes: async () => bad.slice(),
    commitCandidate: async () => ({ assetId: "asset_bad", modelId: "model-bad" }),
    registry,
    tokenIssuer,
    runtime: { selectModel: (id) => { selected.push(id); return Object.freeze({ attemptId: "x", done: Promise.resolve({}) }); } },
  });
  const result = await controller.importCustomModel();
  assert.equal(result.status, "rejected");
  assert.equal(result.code, "admission_rejected");
  assert.equal(registry.getRecord("asset_bad"), null); // 未登记
  assert.equal(tokenIssuer.consumedCount ?? 0, 0);
  assert.deepEqual(selected, []); // 未进入引擎
});

test("P5-G5 import: grant 视图路径泄漏/结构非法 → grant_invalid；用户取消 → cancelled（§8.5/§21）", async () => {
  const bytes = makeModelBytes();
  const storage = createMemoryStorageBackend();
  const registry = await createAssetRegistry({ storage, issuerEpoch: 0 });
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: 0 });
  const leaky = createAvatarImportController({
    chooseFile: async () => ({ name: "x.vrm", fileRef: "dlg://file/3" }),
    issueCandidateGrant: async () => makeGrantView(bytes, { exactResolvedPath: "C:\\abs\\x.vrm" }), // 路径泄漏
    readCandidateBytes: async () => bytes.slice(),
    commitCandidate: async () => ({ assetId: "a", modelId: "m" }),
    registry,
    tokenIssuer,
    runtime: { selectModel: () => Object.freeze({ attemptId: "x", done: Promise.resolve({}) }) },
  });
  const rejected = await leaky.importCustomModel();
  assert.equal(rejected.status, "failed");
  assert.equal(rejected.code, "grant_invalid");
  assert.ok(String(rejected.detail).includes("grant_path_leak"));

  const cancelling = createAvatarImportController({
    chooseFile: async () => ({ canceled: true }),
    issueCandidateGrant: async () => { throw new Error("不应到达"); },
    readCandidateBytes: async () => { throw new Error("不应到达"); },
    commitCandidate: async () => { throw new Error("不应到达"); },
    registry,
    tokenIssuer,
    runtime: { selectModel: () => Object.freeze({ attemptId: "x", done: Promise.resolve({}) }) },
  });
  const cancelled = await cancelling.importCustomModel();
  assert.equal(cancelled.status, "cancelled");
});

test("P5-G6 import: 许可拒绝取消会释放 pending；续接表有界且旧 token 失效", async () => {
  const bytes = makeModelBytes({
    title: "t",
    author: "a",
    licenseName: "Redistribution_Prohibited",
    commercialUssageName: "Allow",
  });
  const storage = createMemoryStorageBackend();
  const registry = await createAssetRegistry({ storage, issuerEpoch: 0 });
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: 0 });
  let tokenSequence = 0;
  let chooseCalls = 0;
  let commitCalls = 0;
  const controller = createAvatarImportController({
    chooseFile: async () => {
      chooseCalls += 1;
      return { name: `custom-${chooseCalls}.vrm`, fileRef: `dlg://file/${chooseCalls}` };
    },
    issueCandidateGrant: async () => makeGrantView(bytes, {
      grantId: `crg_${chooseCalls}`,
      attemptId: `att_${chooseCalls}`,
      candidateId: `cand_${chooseCalls}`,
      nonce: `nonce_${chooseCalls}`,
    }),
    readCandidateBytes: async () => bytes.slice(),
    commitCandidate: async () => {
      commitCalls += 1;
      return { assetId: `asset_${commitCalls}`, modelId: `asset_${commitCalls}` };
    },
    registry,
    tokenIssuer,
    runtime: { selectModel: () => Object.freeze({ attemptId: "x", done: Promise.resolve({}) }) },
    pendingResumeLimit: 1,
    resumeTokenFactory: () => `resume_${++tokenSequence}`,
  });

  const first = await controller.importCustomModel();
  assert.equal(controller.getPendingCount(), 1);
  const second = await controller.importCustomModel();
  assert.equal(controller.getPendingCount(), 1, "pending 表不得超过上限");
  const evicted = await controller.importCustomModel({
    acknowledgeLicense: true,
    resumeToken: first.resumeToken,
  });
  assert.equal(evicted.code, "resume_token_invalid");
  assert.equal(controller.cancelPending(second.resumeToken), true);
  assert.equal(controller.getPendingCount(), 0);
  const cancelledResume = await controller.importCustomModel({
    acknowledgeLicense: true,
    resumeToken: second.resumeToken,
  });
  assert.equal(cancelledResume.code, "resume_token_invalid");
  assert.equal(commitCalls, 0);
  assert.equal(chooseCalls, 2);
});

// ═══ H. avatar-store / theme（辅助状态链）════════════════════

test("P5-H1 avatar-store: runtime→store 只读投影与订阅释放（单向状态链）", () => {
  const clock = createClock();
  const store = createAvatarStore({ nowMonotonic: clock.now, mode: "direct" });
  let snapshotCalls = 0;
  const fakeRuntime = {
    snapshot: () => { snapshotCalls += 1; return { state: "running", current: { modelId: "model-a" }, pending: null, paused: false, safeMode: null, bodyStateVersion: 3, lastRequestedModelId: "model-a", lastCommittedModelId: "model-a" }; },
    subscribe: (listener) => { fakeRuntime._listener = listener; return () => { fakeRuntime._listener = null; }; },
  };
  const seen = [];
  const unsubscribe = store.subscribe((projection) => seen.push(projection));
  store.bindRuntime(fakeRuntime);
  assert.equal(store.projection().currentModel.modelId, "model-a");
  fakeRuntime._listener(); // runtime 通知 → store 投影推送
  assert.ok(seen.length >= 2);
  assert.equal(seen[seen.length - 1].runtimeState, "running");
  assert.equal(seen[seen.length - 1].mode, "direct");
  unsubscribe();
  store.dispose();
  assert.equal(store.listenerCount, 0);
  assert.ok(snapshotCalls > 0);
});

test("P5-H2 theme: presentation 随主题切换，不重载模型（§7.1 setPresentation）", () => {
  const presentations = [];
  let selectModelCalls = 0;
  const fakeRuntime = {
    setPresentation: (options) => presentations.push(options),
    selectModel: () => { selectModelCalls += 1; },
  };
  const sync = createThemePresentationSync({ getRuntime: () => fakeRuntime });
  const applied = sync.applyTheme("bronze_gear");
  assert.equal(applied.applied, true);
  assert.equal(applied.themeId, "bronze_gear");
  assert.equal(presentations.length, 1);
  assert.equal(presentations[0].themeId, "bronze_gear");
  assert.deepEqual(presentations[0].camera, { ...presentationForTheme("bronze_gear").camera });
  assert.ok(presentations[0].lighting);
  assert.equal(selectModelCalls, 0); // 主题切换不重载模型
  const unknown = sync.applyTheme("not-a-theme"); // 未知主题回退默认
  assert.equal(unknown.themeId, "ink_teal");
  // direct 未激活：静默跳过
  const idle = createThemePresentationSync({ getRuntime: () => null });
  assert.equal(idle.applyTheme("jade_light").applied, false);
});

// canonicalSha256 引入校验（防止 tree-shaking 误删导入；同源哈希工具自检）
test("P5-Z1 工具自检: canonicalSha256 稳定", () => {
  assert.equal(canonicalSha256({ a: 1 }), canonicalSha256({ a: 1 }));
});
