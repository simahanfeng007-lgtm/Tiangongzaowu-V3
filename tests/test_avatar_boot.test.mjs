// P6b 启动组装根测试（avatar-boot / three-vrm-runtime-adapter / builtin-asset-source）：
//   1. 组合根在注入 stub window/document 下创建并 startMode("direct")；registry 含两条
//      builtin 记录且 admitted；describeModel 正确
//   2. builtin 记录缺失时按清单原子登记（registryEntryVersion=1）；清单漂移按白名单 +1
//   3. 适配器鸭子方法全部存在且 FIRST_RENDERABLE_FRAME 事件带 attemptId
//   4. 启动失败回退 legacy-iframe 且不抛
//   5. openModelBytes 对 hash/长度不一致中止（内存 channel 注入）+ 正确字节成功路径
// 运行：node --test tests/test_avatar_boot.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import * as THREE from "../app/node_modules/three/build/three.module.js";

import { bootstrapAvatar, getBootstrappedAvatarService } from "../app/frontend-v2/renderer/avatar/avatar-boot.mjs";
import { createThreeVrmRuntimeAdapter } from "../app/frontend-v2/renderer/avatar/three-vrm-runtime-adapter.mjs";
import { createBuiltinAssetSource } from "../app/frontend-v2/renderer/avatar/builtin-asset-source.mjs";
import { createAssetRegistry, AssetScope, AdmissionState, computeAuthorizationFingerprint } from "../app/frontend-v2/renderer/avatar/asset-registry.mjs";
import { createMemoryStorageBackend } from "../app/frontend-v2/renderer/avatar/storage-adapter.mjs";
import { createServiceRegistry } from "../app/frontend-v2/renderer/avatar/service-registry.mjs";
import { createTokenIssuer } from "../app/frontend-v2/renderer/avatar/validated-asset-token.mjs";
import { createAssetProvider } from "../app/frontend-v2/renderer/avatar/asset-provider.mjs";
import { createThreeVrmEngine } from "../app/frontend-v2/renderer/avatar/engines/three-vrm-engine.mjs";
import { EngineEvent } from "../app/frontend-v2/renderer/avatar/engines/avatar-engine-contract.mjs";
import { VALIDATOR_VERSION } from "../app/frontend-v2/renderer/avatar/model-admission-gate.mjs";
import { sha256HexSync } from "../app/frontend-v2/renderer/avatar/canonical-hash.mjs";
import {
  AVATAR_SELECTED_MODEL_FLAG_KEY,
  AvatarRenderMode,
} from "../app/frontend-v2/renderer/avatar/avatar-service.mjs";
import { mergeAvatarCatalog } from "../app/frontend-v2/renderer/plugins/avatar-panel.mjs";

const nodeSha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

// ── 通用测试用具 ─────────────────────────────────────────────

function createClock(start = 0) {
  let t = start;
  return { now: () => t, advance: (ms) => { t += ms; return t; } };
}

function createFlagStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
    dump: () => Object.fromEntries(map),
  };
}

function buildGlb(json, binBytes = null) {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const jsonPad = (4 - (jsonBytes.length % 4)) % 4;
  const jsonLen = jsonBytes.length + jsonPad;
  const hasBin = binBytes !== null && binBytes.byteLength > 0;
  const binPad = hasBin ? (4 - (binBytes.length % 4)) % 4 : 0;
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

function makeVrm1Bytes(label = "stub") {
  return buildGlb({
    asset: { version: "2.0" },
    extensions: { VRMC_vrm: { specVersion: "1.0", meta: { name: label } } },
    nodes: [],
    meshes: [],
  });
}

function makeAdmittedVrm0Bytes() {
  const png = new Uint8Array(33);
  png.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  const pngView = new DataView(png.buffer);
  pngView.setUint32(8, 13);
  png.set([0x49, 0x48, 0x44, 0x52], 12);
  pngView.setUint32(16, 64);
  pngView.setUint32(20, 64);
  return new Uint8Array(buildGlb({
    asset: { version: "2.0" },
    extensions: {
      VRM: {
        meta: {
          title: "custom",
          author: "tester",
          licenseName: "CC0",
          commercialUssageName: "Allow",
          allowedUserName: "Everyone",
        },
      },
    },
    extensionsUsed: ["VRM"],
    nodes: [{ name: "n0" }, { name: "n1" }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, targets: [{ POSITION: 0 }] }] }],
    accessors: [{ componentType: 5126, count: 2, type: "VEC3", bufferView: 0 }],
    skins: [{ joints: [0, 1] }],
    animations: [{ samplers: [{ input: 0 }] }],
    buffers: [{ byteLength: 33 }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 33 }],
    images: [{ mimeType: "image/png", bufferView: 0 }],
  }, png));
}

function makeManifest(bytesA, bytesB) {
  return {
    schema: "tiangong.avatar.builtin-models.v1",
    models: [
      {
        id: "tiangong-z1",
        displayName: "天工造物 z1",
        relativePath: "assets/avatars/imported/z1.vrm",
        contentHash: nodeSha256(new Uint8Array(bytesA)),
        byteLength: bytesA.byteLength,
        vrmSpecVersion: "0.x",
      },
      {
        id: "zaowu-v2",
        displayName: "造物 v2",
        relativePath: "assets/avatars/imported/v2.vrm",
        contentHash: nodeSha256(new Uint8Array(bytesB)),
        byteLength: bytesB.byteLength,
        vrmSpecVersion: "0.x",
      },
    ],
  };
}

function createCanvasStub() {
  const listeners = new Map();
  return {
    listeners,
    parentNode: null,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    style: {},
    width: 320,
    height: 240,
  };
}

function createRendererStub(canvas, { drawCalls = 0 } = {}) {
  return {
    domElement: canvas,
    shadowMap: {},
    toneMapping: null,
    toneMappingExposure: 1,
    outputColorSpace: null,
    disposeCount: 0,
    info: { render: { calls: drawCalls, triangles: 0 }, memory: { geometries: 0, textures: 0 }, programs: [] },
    setPixelRatio() {},
    setSize() {},
    render() {},
    dispose() { this.disposeCount += 1; },
  };
}

function createStubVrm() {
  const scene = new THREE.Group();
  scene.name = "stub-vrm-scene";
  return {
    scene,
    expressionManager: { expressionMap: { happy: {}, neutral: {} }, setValue() {} },
    lookAt: null,
    humanoid: null,
    update() {},
  };
}

function createStubDocument() {
  return {
    visibilityState: "visible",
    createElement: (tag) => (tag === "canvas" ? createCanvasStub() : { tag }),
  };
}

function createStubWindow({ openChannel = null } = {}) {
  const rafQueue = new Map();
  let rafSeq = 0;
  return {
    performance: { now: () => clockRef.now() },
    requestAnimationFrame: (cb) => { rafSeq += 1; rafQueue.set(rafSeq, cb); return rafSeq; },
    cancelAnimationFrame: (id) => { rafQueue.delete(id); },
    __pumpRaf: (count = 1) => {
      const cbs = [...rafQueue.values()].slice(0, count);
      rafQueue.clear();
      for (const cb of cbs) cb(clockRef.now());
    },
    localStorage: createFlagStorage(),
    tiangongDesktop: {
      avatarAsset: openChannel ? { openChannel } : undefined,
      writeDiagnostic: () => {},
    },
  };
}

let clockRef = { now: () => 0 };

// 真实 ThreeVrmEngine + stub 渲染依赖（与 P6a 测试同一形态）。
function createStubEngineFactory({ drawCalls = 5 } = {}) {
  return () => {
    const canvas = createCanvasStub();
    const renderer = createRendererStub(canvas, { drawCalls });
    return createThreeVrmEngine({
      canvas,
      viewport: { width: 320, height: 240 },
      deps: {
        rendererFactory: () => renderer,
        parseGltf: async () => ({ userData: { vrm: createStubVrm() } }),
      },
    });
  };
}

// ── 内存 channel：模拟主进程宿主（ready/pull/chunk/final 协议）─────────────

function createMemoryPair() {
  let rendererCb = null;
  let hostCb = null;
  const pendingForRenderer = [];
  const pendingForHost = [];
  const deliverToRenderer = (message) => {
    if (rendererCb) rendererCb(message);
    else pendingForRenderer.push(message);
  };
  return {
    rendererPort: {
      postMessage: (message) => { if (hostCb) hostCb(message); else pendingForHost.push(message); },
      onMessage: (cb) => { rendererCb = cb; while (pendingForRenderer.length > 0) rendererCb(pendingForRenderer.shift()); },
      close: () => {},
    },
    hostPort: {
      postMessage: (message) => deliverToRenderer(message),
      onMessage: (cb) => { hostCb = cb; while (pendingForHost.length > 0) hostCb(pendingForHost.shift()); },
      close: () => {},
    },
  };
}

// 内存宿主：按 descriptor.locator 从 bytesByLocator 提供字节；可注入腐坏（截断/篡改）。
function createMemoryChannelFactory(bytesByLocator, { corrupt = null, chunkSize = 1024 } = {}) {
  const seen = [];
  const channelFactory = (descriptor) => {
    seen.push(descriptor);
    const pair = createMemoryPair();
    const served = (() => {
      const raw = bytesByLocator[descriptor.locator];
      if (!raw) return null;
      const bytes = new Uint8Array(raw);
      return corrupt === null ? bytes : corrupt(bytes, descriptor);
    })();
    if (served === null) {
      queueMicrotask(() => pair.hostPort.postMessage({ type: "error", code: "asset_not_found", message: "未知 locator" }));
      return pair.rendererPort;
    }
    const servedHash = nodeSha256(served);
    let dumped = false;
    pair.hostPort.onMessage((message) => {
      if (message?.type === "pull" && !dumped) {
        dumped = true; // 小字节测试：一次拉取即全量投完，后续 credit pull 不再重复投递
        queueMicrotask(() => {
          let seq = 0;
          for (let offset = 0; offset < served.byteLength; offset += chunkSize) {
            const slice = served.subarray(offset, Math.min(offset + chunkSize, served.byteLength));
            pair.hostPort.postMessage({ type: "chunk", seq: (seq += 1) - 1, length: slice.byteLength, bytes: slice });
          }
          pair.hostPort.postMessage({ type: "final", contentHash: servedHash, byteLength: served.byteLength });
        });
      }
    });
    queueMicrotask(() => pair.hostPort.postMessage({ type: "ready", byteLength: served.byteLength, contentHash: servedHash, chunkSize }));
    return pair.rendererPort;
  };
  return { channelFactory, seen };
}

async function rejectsCode(promise, code) {
  return assert.rejects(
    promise,
    (error) => {
      assert.equal(error.code, code, `期望错误码 ${code}，实际 ${error.code}（${error.message}）`);
      return true;
    },
  );
}

// ── 1. 组合根创建 + direct 启动 + 内置登记 ─────────────────

test("avatar-boot：组合根创建并 startMode(direct)；两条 builtin 记录 admitted/v1；describeModel 正确", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const bytesB = makeVrm1Bytes("v2");
  const manifest = makeManifest(bytesA, bytesB);
  const { channelFactory } = createMemoryChannelFactory({ "tiangong-z1": new Uint8Array(bytesA), "zaowu-v2": new Uint8Array(bytesB) });
  const serviceRegistry = createServiceRegistry();
  const flagStorage = createFlagStorage({ "tiangong.avatar.renderMode": "direct" });

  const handle = await bootstrapAvatar({
    document: createStubDocument(),
    window: createStubWindow({ openChannel: channelFactory }),
    flagStorage,
    storageBackend: createMemoryStorageBackend(),
    channelFactory,
    manifest,
    serviceRegistry,
    nowMonotonic: clockRef.now,
    engineModuleLoader: async () => ({
      createThreeVrmEngine: createStubEngineFactory(),
    }),
    autoSelectSafeModel: false,
  });

  assert.equal(handle.fallback, false, `组装不应回退: ${JSON.stringify(handle.bootLog)}`);
  // startMode("direct")：服务处于 direct 运行态
  assert.equal(handle.service.getActiveMode(), AvatarRenderMode.DIRECT);
  assert.equal(handle.avatarImportBridge, null, "测试环境缺少完整 desktop 桥时只跳过导入桥");
  assert.ok(handle.service.getRuntime() !== null, "direct 模式应暴露 runtime");
  // service registry：avatar-runtime + avatar-service 双登记（§20.1 单例）
  assert.ok(serviceRegistry.hasService("avatar-runtime"));
  assert.ok(serviceRegistry.hasService("avatar-service"));
  assert.equal(serviceRegistry.getService("avatar-service"), handle.service);
  // getBootstrappedAvatarService 诊断访问器
  assert.equal(getBootstrappedAvatarService(), handle.service);
  // asset registry：两条 builtin 记录、admitted、registryEntryVersion=1
  for (const model of manifest.models) {
    const record = handle.assetRegistry.getRecord(model.id);
    assert.ok(record !== null, `缺少 builtin 记录 ${model.id}`);
    assert.equal(record.scope, AssetScope.BUILTIN);
    assert.equal(record.admissionState, AdmissionState.ADMITTED);
    assert.equal(record.registryEntryVersion, 1);
    assert.equal(record.contentHash, model.contentHash);
    assert.equal(record.byteLength, model.byteLength);
    assert.equal(record.displayName, model.displayName);
  }
  // describeModel：逻辑 id → descriptor 投影
  const descriptor = await handle.assetSource.describeModel("zaowu-v2");
  assert.equal(descriptor.modelId, "zaowu-v2");
  assert.equal(descriptor.contentHash, manifest.models[1].contentHash);
  assert.equal(descriptor.byteLength, manifest.models[1].byteLength);
  assert.equal(descriptor.vrmSpecVersion, "0.x");
  // 未知 modelId 明确失败
  await rejectsCode(handle.assetSource.describeModel("no-such-model"), "model_unknown");

  handle.service.dispose();
});

test("avatar-boot：空 builtin 清单保持 direct/import-only；真实导入桥登记后可按 model scope 读取并进入 catalog", async () => {
  clockRef = createClock();
  const bytes = makeAdmittedVrm0Bytes();
  const contentHash = nodeSha256(bytes);
  const grantId = "crg_boot_custom_1";
  const { channelFactory, seen } = createMemoryChannelFactory({
    [grantId]: bytes,
    [contentHash]: bytes,
  });
  const windowStub = createStubWindow({ openChannel: channelFactory });
  let chooseCalls = 0;
  let grantCalls = 0;
  let commitCalls = 0;
  windowStub.tiangongDesktop.avatarImport = {
    chooseFile: async () => {
      chooseCalls += 1;
      return {
        canceled: false,
        name: "会话自定义.vrm",
        attemptId: "att_boot_custom_1",
        candidateId: "cand_boot_custom_1",
        contentHash,
        byteLength: bytes.byteLength,
      };
    },
    commitCandidate: async () => {
      commitCalls += 1;
      return { assetId: `model:${contentHash}`, modelId: `model:${contentHash}` };
    },
    deleteModelFile: async (payload) => ({ deleted: true, missing: false, contentHash: payload.contentHash }),
  };
  windowStub.tiangongDesktop.avatarAsset.issueCandidateGrant = async () => {
    grantCalls += 1;
    return {
      grantId,
      attemptId: "att_boot_custom_1",
      candidateId: "cand_boot_custom_1",
      contentHash,
      byteLength: bytes.byteLength,
      issuerEpoch: 0,
      nonce: "nonce_boot_custom_1",
      singleUse: true,
    };
  };

  const handle = await bootstrapAvatar({
    document: createStubDocument(),
    window: windowStub,
    flagStorage: createFlagStorage({ "tiangong.avatar.renderMode": "direct" }),
    storageBackend: createMemoryStorageBackend(),
    channelFactory,
    manifest: { schema: "tiangong.avatar.builtin-models.v1", models: [] },
    serviceRegistry: createServiceRegistry(),
    nowMonotonic: clockRef.now,
    engineFactory: createStubEngineFactory(),
    autoSelectSafeModel: true,
  });

  assert.equal(handle.fallback, false, JSON.stringify(handle.bootLog));
  assert.equal(handle.service.getActiveMode(), AvatarRenderMode.DIRECT);
  assert.deepEqual(handle.assetSource.listModels(), []);
  assert.equal(handle.runtime.snapshot().lastRequestedModelId, null, "空清单不得自动选择不存在的 safe model");
  assert.ok(handle.bootLog.some((line) => line.stage === "auto-select" && String(line.detail).includes("absent")));
  assert.equal(windowStub.tiangongAvatarImport, handle.avatarImportBridge);
  assert.equal(typeof handle.avatarImportBridge?.importCustomModel, "function");

  const imported = await handle.avatarImportBridge.importCustomModel();
  assert.equal(imported.status, "committed");
  assert.equal(imported.modelId, `model:${contentHash}`);
  assert.equal(chooseCalls, 1);
  assert.equal(grantCalls, 1);
  assert.equal(commitCalls, 1);
  const registered = handle.avatarImportBridge.listRegisteredModels();
  assert.equal(registered.length, 1);
  assert.equal(registered[0].id, imported.modelId);

  const descriptor = await handle.assetSource.describeModel(imported.modelId);
  assert.equal(descriptor.scope, AssetScope.MODEL);
  assert.equal(descriptor.contentHash, contentHash);
  const opened = await handle.assetSource.openModelBytes({ ...descriptor, attemptId: "att_catalog_open" });
  assert.equal(nodeSha256(new Uint8Array(opened)), contentHash);
  assert.ok(
    seen.some((request) => request.scope === "model" && request.locator === contentHash),
    "自定义模型必须经 assetHandleForModel 的 hash locator 受控读取",
  );
  const catalog = mergeAvatarCatalog([], registered);
  assert.equal(catalog.length, 1);
  assert.equal(catalog[0].displayName, "会话自定义.vrm");
  assert.equal(handle.assetSource.listModels().some((model) => model.id === imported.modelId), true);

  handle.service.dispose();
});

test("avatar-boot：保存的上次模型优先于初始模型恢复；失效选择回退初始模型", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const bytesB = makeVrm1Bytes("saved");
  const manifest = makeManifest(bytesA, bytesB); // tiangong-z1 + zaowu-v2
  const savedId = "zaowu-v2";
  const bytesByLocator = {
    "tiangong-z1": new Uint8Array(bytesA),
    "zaowu-v2": new Uint8Array(bytesB),
  };
  const { channelFactory } = createMemoryChannelFactory(bytesByLocator);

  const bootOnce = async (flagStorage) => bootstrapAvatar({
    document: createStubDocument(),
    window: createStubWindow({ openChannel: channelFactory }),
    flagStorage,
    storageBackend: createMemoryStorageBackend(),
    channelFactory,
    manifest,
    serviceRegistry: createServiceRegistry(),
    nowMonotonic: clockRef.now,
    engineModuleLoader: async () => ({ createThreeVrmEngine: createStubEngineFactory() }),
    autoSelectSafeModel: true,
  });

  // 1) 保存的选择仍登记且 admitted → 恢复该模型，而不是初始模型。
  const handle = await bootOnce(createFlagStorage({
    "tiangong.avatar.renderMode": "direct",
    [AVATAR_SELECTED_MODEL_FLAG_KEY]: savedId,
  }));
  assert.equal(handle.fallback, false, JSON.stringify(handle.bootLog));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(handle.runtime.snapshot().lastRequestedModelId, savedId);
  assert.ok(
    handle.bootLog.some(
      (line) => line.stage === "auto-select" && String(line.detail).includes(`restore:${savedId}`),
    ),
    JSON.stringify(handle.bootLog),
  );
  handle.service.dispose();

  // 2) 保存的选择已删除/不存在 → 回退初始模型。
  const handle2 = await bootOnce(createFlagStorage({
    "tiangong.avatar.renderMode": "direct",
    [AVATAR_SELECTED_MODEL_FLAG_KEY]: "model:ghost",
  }));
  assert.equal(handle2.fallback, false, JSON.stringify(handle2.bootLog));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(handle2.runtime.snapshot().lastRequestedModelId, "tiangong-z1");
  handle2.service.dispose();
});

test("avatar-boot：已登记记录不重复登记；清单漂移按白名单字段刷新 +1", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const bytesB = makeVrm1Bytes("v2");
  const manifest = makeManifest(bytesA, bytesB);
  const { channelFactory } = createMemoryChannelFactory({ "tiangong-z1": new Uint8Array(bytesA) });
  const storage = createMemoryStorageBackend();
  const seedRegistry = await createAssetRegistry({ storage, issuerEpoch: 0 });
  const fingerprintOf = (m) => computeAuthorizationFingerprint({
    licenseRecord: null, admissionLimits: null, uriPolicy: null,
    validatorVersion: VALIDATOR_VERSION, contentHash: m.contentHash, byteLength: m.byteLength,
  });
  // z1 已登记且与清单一致：不得重复登记（版本保持 1）
  await seedRegistry.registerAsset({
    assetId: "tiangong-z1", scope: AssetScope.BUILTIN,
    contentHash: manifest.models[0].contentHash, byteLength: manifest.models[0].byteLength,
    validationReceiptId: "arec_builtin_manifest_v1", validatorVersion: VALIDATOR_VERSION,
    authorizationFingerprint: fingerprintOf(manifest.models[0]),
    admissionState: AdmissionState.ADMITTED, displayName: "旧名",
  });
  // v2 已登记但 hash 漂移：应刷新且 registryEntryVersion +1
  const staleBytes = makeVrm1Bytes("stale");
  await seedRegistry.registerAsset({
    assetId: "zaowu-v2", scope: AssetScope.BUILTIN,
    contentHash: nodeSha256(new Uint8Array(staleBytes)), byteLength: staleBytes.byteLength,
    validationReceiptId: "arec_builtin_manifest_v1", validatorVersion: VALIDATOR_VERSION,
    authorizationFingerprint: fingerprintOf({ contentHash: nodeSha256(new Uint8Array(staleBytes)), byteLength: staleBytes.byteLength }),
    admissionState: AdmissionState.ADMITTED,
  });

  const handle = await bootstrapAvatar({
    document: createStubDocument(),
    window: createStubWindow({ openChannel: channelFactory }),
    flagStorage: createFlagStorage(),
    storageBackend: storage,
    channelFactory,
    manifest,
    serviceRegistry: createServiceRegistry(),
    nowMonotonic: clockRef.now,
    engineFactory: createStubEngineFactory(),
    autoSelectSafeModel: false,
  });

  assert.equal(handle.fallback, false, JSON.stringify(handle.bootLog));
  const z1 = handle.assetRegistry.getRecord("tiangong-z1");
  assert.equal(z1.registryEntryVersion, 1, "一致记录不得递增版本");
  assert.equal(z1.displayName, "旧名", "一致记录不得被清单覆盖非版本化字段");
  const v2 = handle.assetRegistry.getRecord("zaowu-v2");
  assert.equal(v2.contentHash, manifest.models[1].contentHash, "漂移记录应刷新到清单 hash");
  assert.equal(v2.registryEntryVersion, 2, "版本化字段变化恰好 +1");
  handle.service.dispose();
});

// ── 2. 适配器鸭子类型 + 事件归属 ───────────────────────────

test("three-vrm-runtime-adapter：鸭子方法全部存在；FIRST_RENDERABLE_FRAME 带 attemptId", async () => {
  clockRef = createClock();
  const engine = createStubEngineFactory({ drawCalls: 7 })();
  const adapter = createThreeVrmRuntimeAdapter({ engine });
  // 鸭子方法全盘点（avatar-runtime.mjs 头注释契约）
  for (const method of [
    "on", "off", "loadCandidate", "uploadCandidate", "renderCandidateFrame",
    "presentCandidate", "concealCandidate", "restorePresented", "promoteCandidate",
    "disposeModel", "discardInvalidatedModel", "renderFrame", "update", "getStats",
    "isContextLost", "recreateRenderer", "attachSurface", "detachSurface",
    "applyPosture", "applyExpression", "applyGaze", "playGesture", "setSpeaking",
    "applyVisemeTarget", "applyPerformanceSemantics",
    "candidateBoundsIntersectViewport", "hasFatalRendererError", "disposeEngine",
  ]) {
    assert.equal(typeof adapter[method], "function", `缺少鸭子方法 ${method}`);
  }
  assert.equal(adapter.engineVersion, "three-vrm-3.5.5");

  // 候选加载 → staging 不可见 → 探针帧：事件必须带 attemptId 归属（§19.1.6）
  const events = [];
  adapter.on(EngineEvent.FIRST_RENDERABLE_FRAME, (payload) => events.push(payload));
  const handle = await adapter.loadCandidate(makeVrm1Bytes("cand"), { label: "cand", attemptId: "att_probe" });
  assert.equal(handle.attemptId, "att_probe");
  const probe = adapter.renderCandidateFrame(handle);
  assert.equal(probe.drawCalls, 7, "探针帧应返回引擎 drawCalls");
  assert.equal(events.length, 1);
  assert.equal(events[0].attemptId, "att_probe", "FIRST_RENDERABLE_FRAME 必须按 attemptId 归属");

  // 事务链：present → promote → 再次加载取代 → 回滚经字节重建恢复
  adapter.presentCandidate(handle);
  adapter.promoteCandidate(handle);
  const candidate2 = await adapter.loadCandidate(makeVrm1Bytes("c2"), { label: "c2", attemptId: "att_2" });
  assert.equal(handle.stale, true, "旧模型应被单槽引擎释放并标 stale");
  adapter.concealCandidate(candidate2);
  adapter.restorePresented(handle); // 异步重建（fire-and-forget 队列）
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(handle.stale, false, "回滚重建后旧模型恢复活动");
  // 回滚重建触发的引擎 FIRST_RENDERABLE_FRAME 不带 attemptId（Runtime 不采信）
  assert.equal(events.length, 1, "重建帧不得冒名 attemptId");
  // disposeModel 幂等
  adapter.disposeModel(handle);
  adapter.disposeModel(handle);
  assert.equal(handle.disposed, true);
  adapter.disposeModel(candidate2);
  adapter.disposeEngine();
});

// ── 3. 启动失败回退 ────────────────────────────────────────

test("avatar-boot：引擎创建失败 → 回退 legacy-iframe 且不抛", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const manifest = makeManifest(bytesA, makeVrm1Bytes("v2"));
  const { channelFactory } = createMemoryChannelFactory({ "tiangong-z1": new Uint8Array(bytesA) });
  const serviceRegistry = createServiceRegistry();

  const flagStorage = createFlagStorage({ "tiangong.avatar.renderMode": "direct" });
  const handle = await bootstrapAvatar({
    document: createStubDocument(),
    window: createStubWindow({ openChannel: channelFactory }),
    flagStorage,
    storageBackend: createMemoryStorageBackend(),
    channelFactory,
    manifest,
    serviceRegistry,
    nowMonotonic: clockRef.now,
    engineFactory: () => { throw new Error("webgl_unavailable"); },
    autoSelectSafeModel: false,
  });

  assert.equal(handle.fallback, true);
  assert.ok(handle.service !== null, "回退仍应提供 avatar-service 外观");
  assert.equal(handle.service.getActiveMode(), AvatarRenderMode.LEGACY_IFRAME);
  assert.equal(handle.service.getRuntime(), null, "legacy 模式无 direct runtime");
  assert.ok(handle.bootLog.some((line) => line.stage === "boot" && line.ok === false));
  assert.ok(handle.bootLog.some((line) => line.stage === "fallback" && line.ok === true));
  assert.equal(
    flagStorage.dump()["tiangong.avatar.renderMode"],
    AvatarRenderMode.LEGACY_IFRAME,
    "回退必须经 setMode 持久化，避免下次启动再次进入坏 direct",
  );
  assert.equal(getBootstrappedAvatarService(), handle.service);
  handle.service.dispose();
});

test("avatar-boot：three-vrm-engine 依赖加载失败被 bootstrap 捕获，frontend 可持久回退 legacy", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const manifest = makeManifest(bytesA, makeVrm1Bytes("v2"));
  const { channelFactory } = createMemoryChannelFactory({ "tiangong-z1": new Uint8Array(bytesA) });
  const serviceRegistry = createServiceRegistry();
  const flagStorage = createFlagStorage({ "tiangong.avatar.renderMode": "direct" });
  let loadCalls = 0;

  const handle = await bootstrapAvatar({
    document: createStubDocument(),
    window: createStubWindow({ openChannel: channelFactory }),
    flagStorage,
    storageBackend: createMemoryStorageBackend(),
    channelFactory,
    manifest,
    serviceRegistry,
    nowMonotonic: clockRef.now,
    engineModuleLoader: async () => {
      loadCalls += 1;
      throw new Error("ERR_MODULE_NOT_FOUND: three/addons/loaders/GLTFLoader.js");
    },
    autoSelectSafeModel: false,
  });

  assert.equal(loadCalls, 1);
  assert.equal(handle.fallback, true);
  assert.ok(handle.service !== null, "依赖缺失不得拖垮 avatar-service 外观");
  assert.equal(handle.service.getActiveMode(), AvatarRenderMode.LEGACY_IFRAME);
  assert.equal(handle.service.getRuntime(), null);
  assert.equal(flagStorage.dump()["tiangong.avatar.renderMode"], AvatarRenderMode.LEGACY_IFRAME);
  assert.ok(handle.bootLog.some(
    (line) => line.stage === "boot" && line.ok === false &&
      String(line.detail).includes("ERR_MODULE_NOT_FOUND"),
  ));
  assert.ok(handle.bootLog.some((line) => line.stage === "fallback" && line.ok === true));

  handle.service.dispose();
});

// ── 4. openModelBytes 字节同一性（内存 channel）─────────────

async function makeAssetSourceRig(bytesByLocator, { manifest = null, corrupt = null } = {}) {
  const entries = Object.entries(bytesByLocator);
  const effectiveManifest = manifest ?? {
    schema: "tiangong.avatar.builtin-models.v1",
    models: entries.map(([id, bytes]) => ({
      id,
      displayName: id,
      relativePath: null,
      contentHash: nodeSha256(bytes),
      byteLength: bytes.byteLength,
      vrmSpecVersion: "0.x",
    })),
  };
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: 0 });
  for (const model of effectiveManifest.models) {
    await registry.registerAsset({
      assetId: model.id, scope: AssetScope.BUILTIN,
      contentHash: model.contentHash, byteLength: model.byteLength,
      validationReceiptId: "arec_builtin_manifest_v1", validatorVersion: VALIDATOR_VERSION,
      authorizationFingerprint: computeAuthorizationFingerprint({
        licenseRecord: null, admissionLimits: null, uriPolicy: null,
        validatorVersion: VALIDATOR_VERSION, contentHash: model.contentHash, byteLength: model.byteLength,
      }),
      admissionState: AdmissionState.ADMITTED,
    });
  }
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: registry.issuerEpoch });
  const { channelFactory, seen } = createMemoryChannelFactory(bytesByLocator, { corrupt });
  const provider = createAssetProvider({ channelFactory, registry, issuerEpoch: registry.issuerEpoch, timeoutMs: 5_000 });
  const source = createBuiltinAssetSource({ manifest: effectiveManifest, provider, tokenIssuer, sha256: sha256HexSync });
  return { source, registry, seen, manifest: effectiveManifest };
}

test("builtin-asset-source：正确字节经内存 channel 成功交付", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const { source, manifest } = await makeAssetSourceRig({ "tiangong-z1": new Uint8Array(bytesA) });
  const descriptor = await source.describeModel("tiangong-z1");
  const bytes = await source.openModelBytes({ ...descriptor, attemptId: "att_ok" });
  assert.ok(bytes instanceof ArrayBuffer);
  assert.equal(bytes.byteLength, manifest.models[0].byteLength);
  assert.equal(nodeSha256(new Uint8Array(bytes)), manifest.models[0].contentHash);
});

test("builtin-asset-source：内容篡改 → hash 不一致中止（fail-closed）", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const tampered = new Uint8Array(bytesA.slice(0));
  tampered[tampered.byteLength - 1] ^= 0xff; // 等长篡改：长度一致、hash 不一致
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: 0 });
  const manifest = makeManifest(bytesA, makeVrm1Bytes("v2"));
  await registry.registerAsset({
    assetId: "tiangong-z1", scope: AssetScope.BUILTIN,
    contentHash: manifest.models[0].contentHash, byteLength: manifest.models[0].byteLength,
    validationReceiptId: "arec_builtin_manifest_v1", validatorVersion: VALIDATOR_VERSION,
    authorizationFingerprint: computeAuthorizationFingerprint({
      licenseRecord: null, admissionLimits: null, uriPolicy: null,
      validatorVersion: VALIDATOR_VERSION, contentHash: manifest.models[0].contentHash, byteLength: manifest.models[0].byteLength,
    }),
    admissionState: AdmissionState.ADMITTED,
  });
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: registry.issuerEpoch });
  // 宿主提供被篡改字节：ready 复述的 hash 与期望不一致 → host_descriptor_mismatch
  const { channelFactory } = createMemoryChannelFactory({ "tiangong-z1": tampered });
  const provider = createAssetProvider({ channelFactory, registry, issuerEpoch: registry.issuerEpoch, timeoutMs: 5_000 });
  const source = createBuiltinAssetSource({ manifest, provider, tokenIssuer, sha256: sha256HexSync });
  const descriptor = await source.describeModel("tiangong-z1");
  await rejectsCode(source.openModelBytes({ ...descriptor, attemptId: "att_tamper" }), "host_descriptor_mismatch");
});

test("builtin-asset-source：字节截断 → 长度复核失败中止", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const manifest = makeManifest(bytesA, makeVrm1Bytes("v2"));
  const truncate = (bytes) => bytes.subarray(0, bytes.byteLength - 7);
  const { source } = await makeAssetSourceRig(
    { "tiangong-z1": new Uint8Array(bytesA) },
    { manifest, corrupt: truncate },
  );
  const descriptor = await source.describeModel("tiangong-z1");
  // 宿主 ready 复述的 byteLength（截断后）与期望不一致即中止（§8.6 前置校验）
  await rejectsCode(source.openModelBytes({ ...descriptor, attemptId: "att_trunc" }), "host_descriptor_mismatch");
});

test("builtin-asset-source：未知 modelId 开流即失败，不发通道", async () => {
  clockRef = createClock();
  const bytesA = makeVrm1Bytes("z1");
  const { source, seen } = await makeAssetSourceRig({ "tiangong-z1": new Uint8Array(bytesA) });
  await rejectsCode(source.openModelBytes({ modelId: "ghost", attemptId: "att_ghost" }), "model_unknown");
  assert.equal(seen.length, 0, "未知 modelId 不得触碰通道");
});
