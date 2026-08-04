// P3 共享 AvatarEngine 契约测试：
// capabilities 声明、disposeModel 幂等与共享 Renderer 存活、加载入口与禁止职责静态断言、
// VRM 0.x/1.0 版本路由、0.x meta → LicenseRecord 投影、
// 两个真实内置模型的 headless 结构级加载路径（stub WebGL 上下文 + 依赖注入 parseGltf）。

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

import * as THREE from "../app/node_modules/three/build/three.module.js";

import {
  AVATAR_ENGINE_CONTRACT_VERSION,
  ENGINE_SEMANTIC_COMMANDS,
  EngineEvent,
  THREE_VRM_ENGINE_CAPABILITIES,
  validateEngineCapabilities,
} from "../app/frontend-v2/renderer/avatar/engines/avatar-engine-contract.mjs";
import {
  AvatarEngineError,
  createThreeVrmEngine,
  mapVisemeChar,
  sniffGltfJsonBytes,
} from "../app/frontend-v2/renderer/avatar/engines/three-vrm-engine.mjs";
import {
  analyzeVrm0GltfJson,
  detectVrmSpecVersion,
  projectVrm0LicenseRecord,
  vrm0ExpressionAliases,
} from "../app/frontend-v2/renderer/avatar/compatibility/vrm0-adapter.mjs";
import {
  analyzeVrm1GltfJson,
  applyVrm1ExpressionOverrides,
  configureVrm1NodeConstraints,
  configureVrm1SpringBone,
  projectVrm1LicenseRecord,
} from "../app/frontend-v2/renderer/avatar/compatibility/vrm1-adapter.mjs";

const FIXTURE_Z1_URL = new URL("../app/assets/avatars/imported/天工造物z1.vrm", import.meta.url);
const FIXTURE_V2_URL = new URL("../app/assets/avatars/imported/造物v2.vrm", import.meta.url);
const HAS_RESTRICTED_FIXTURES = existsSync(FIXTURE_Z1_URL) && existsSync(FIXTURE_V2_URL);

function syntheticVrm0Bytes(title) {
  return new Uint8Array(buildGlbWithJson({
    asset: { version: "2.0" },
    nodes: [{ name: "synthetic-root" }],
    meshes: [],
    extensions: {
      VRM: {
        specVersion: "0.0",
        meta: {
          title,
          author: "test-suite",
          commercialUssageName: "Allow",
          licenseName: "CC0",
        },
      },
    },
  }));
}

// 可再分发源码按发布许可不携带这两份模型；通用引擎测试使用合成 CC0
// 容器继续执行，仅“真实内置模型”结构断言在专有素材存在时运行。
const FIXTURE_Z1 = HAS_RESTRICTED_FIXTURES
  ? new Uint8Array(readFileSync(FIXTURE_Z1_URL))
  : syntheticVrm0Bytes("synthetic-z1");
const FIXTURE_V2 = HAS_RESTRICTED_FIXTURES
  ? new Uint8Array(readFileSync(FIXTURE_V2_URL))
  : syntheticVrm0Bytes("synthetic-v2");

const ENGINE_DIR = new URL("../app/frontend-v2/renderer/avatar/", import.meta.url);
const ENGINE_SOURCES = [
  "engines/avatar-engine-contract.mjs",
  "engines/three-vrm-engine.mjs",
  "engine-harness.mjs",
  "compatibility/vrm0-adapter.mjs",
  "compatibility/vrm1-adapter.mjs",
];

// ── headless 测试用具 ───────────────────────────────────────

function createCanvasStub() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      listeners.get(type)?.delete(fn);
    },
    dispatch(type, event = {}) {
      for (const fn of [...(listeners.get(type) ?? [])]) fn(event);
    },
    style: {},
    width: 320,
    height: 240,
  };
}

function createRendererStub(canvas) {
  return {
    domElement: canvas,
    shadowMap: {},
    toneMapping: null,
    toneMappingExposure: 1,
    outputColorSpace: null,
    renderCount: 0,
    disposeCount: 0,
    sizes: [],
    info: { render: { calls: 7, triangles: 12345 }, memory: { geometries: 5, textures: 9 }, programs: [] },
    setPixelRatio() {},
    setSize(width, height) { this.sizes.push([width, height]); },
    setClearColor() {},
    render() { this.renderCount += 1; },
    dispose() { this.disposeCount += 1; },
  };
}

// 用真实模型字节走结构级路径，用 stub VRM 实例代替 GLTFLoader.parse（Node 无 WebGL/DOM 纹理管线）。
function createStubVrm(expressionMap = { happy: {}, blink: {}, aa: {} }) {
  const scene = new THREE.Group();
  scene.name = "stub-vrm-scene";
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
  const material = new THREE.MeshStandardMaterial();
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = "stub-mesh";
  scene.add(mesh);
  const setValueCalls = [];
  return {
    scene,
    expressionManager: {
      expressionMap,
      setValue(name, value) { setValueCalls.push([name, value]); },
    },
    setValueCalls,
    lookAt: null,
    humanoid: null,
    update() {},
  };
}

function createHeadlessEngine({ viewport = { width: 320, height: 240 } } = {}) {
  const canvas = createCanvasStub();
  const renderer = createRendererStub(canvas);
  const engine = createThreeVrmEngine({
    canvas,
    viewport,
    deps: {
      rendererFactory: () => renderer,
      parseGltf: async () => ({ userData: { vrm: createStubVrm() } }),
    },
  });
  return { engine, canvas, renderer };
}

// ── capabilities ────────────────────────────────────────────

test("capabilities 声明完整且 multiView/webgpu/gaussian 为 false", () => {
  assert.deepEqual(validateEngineCapabilities(THREE_VRM_ENGINE_CAPABILITIES), []);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.humanoid, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.expression, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.lookAt, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.springBone, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.vrma, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.lipSync, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.relighting, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.webgl, true);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.multiView, false);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.webgpu, false);
  assert.equal(THREE_VRM_ENGINE_CAPABILITIES.gaussian, false);
});

// ── 加载入口与禁止职责静态断言 ──────────────────────────────

test("引擎源码无 raw URL 加载入口与禁止职责（§7.2/§8.2/§13.2/§29）", () => {
  for (const rel of ENGINE_SOURCES) {
    const source = readFileSync(new URL(rel, ENGINE_DIR), "utf8");
    assert.equal(/GLTFLoader\.load\s*\(/.test(source), false, `${rel} 不得调用 GLTFLoader.load`);
    assert.equal(/\.load\s*\(\s*['"`]https?:\/\//.test(source), false, `${rel} 不得拼接 http(s) URL 加载`);
    assert.equal(source.includes("ipcRenderer"), false, `${rel} 不得出现 ipcRenderer（§13.2）`);
    assert.equal(source.includes("localStorage"), false, `${rel} 不得出现 localStorage（§13.2）`);
    assert.equal(/(^|[^a-zA-Z])fetch\s*\(/.test(source), false, `${rel} 不得出现 fetch(`);
    assert.equal(source.includes("forceContextLoss"), false, `${rel} 不得实现 forceContextLoss 主动调用（§11.4/§29）`);
    assert.equal(/\bopenUrl\b/.test(source), false, `${rel} 不得提供 openUrl 入口`);
  }
});

test("loadModel 拒绝字符串/URL 形态输入", async () => {
  const { engine } = createHeadlessEngine();
  await assert.rejects(() => engine.loadModel("https://example.invalid/a.vrm"), AvatarEngineError);
  await assert.rejects(() => engine.loadModel("C:/models/a.vrm"), (err) => err.code === "model_bytes_invalid");
  await assert.rejects(() => engine.loadModel({ url: "./a.vrm" }), (err) => err.code === "model_bytes_invalid");
  engine.disposeEngine();
});

// ── 版本识别与适配器路由 ────────────────────────────────────

test("版本识别：extensions.VRM → 0.x，VRMC_vrm → 1.0，缺失 → null", () => {
  assert.equal(detectVrmSpecVersion({ extensions: { VRM: { specVersion: "0.0" } } }), "0.x");
  assert.equal(detectVrmSpecVersion({ extensions: { VRMC_vrm: { specVersion: "1.0" } } }), "1.0");
  assert.equal(detectVrmSpecVersion({ extensions: {} }), null);
  assert.equal(detectVrmSpecVersion({}), null);
  assert.equal(detectVrmSpecVersion(null), null);
});

function buildGlbWithJson(json) {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const chunkLength = jsonBytes.length + pad;
  const total = 12 + 8 + chunkLength;
  const buffer = new ArrayBuffer(total);
  const view = new DataView(buffer);
  view.setUint32(0, 0x46546c67, true); // "glTF"
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, chunkLength, true);
  view.setUint32(16, 0x4e4f534a, true); // "JSON"
  new Uint8Array(buffer).set(jsonBytes, 20);
  // GLB JSON chunk 以空格（0x20）补齐 4 字节对齐。
  for (let i = 0; i < pad; i += 1) view.setUint8(20 + jsonBytes.length + i, 0x20);
  return buffer;
}

test("1.0 模型路由到 vrm1 适配器并投影 LicenseRecord", async () => {
  const gltfJson = {
    asset: { version: "2.0" },
    nodes: [{ name: "n0" }, { name: "n1" }],
    meshes: [{ name: "m0" }],
    extensions: {
      VRMC_vrm: {
        specVersion: "1.0",
        meta: { name: "unit-vrm1", authors: ["tester"], commercialUsage: "personalProfit" },
      },
      VRMC_springBone: {},
    },
  };
  const { engine } = createHeadlessEngine();
  const result = await engine.loadModel(buildGlbWithJson(gltfJson), { label: "unit-vrm1" });
  assert.equal(result.specVersion, "1.0");
  assert.equal(result.adapterKind, "vrm1");
  assert.equal(result.structuralReport.nodeCount, 2);
  assert.equal(result.structuralReport.hasSpringBoneExtension, true);
  assert.equal(result.licenseRecord.title, "unit-vrm1");
  assert.equal(result.licenseRecord.commercialUsage, "allow"); // personalProfit → allow
  engine.disposeEngine();
});

test("0.x meta → LicenseRecord 投影（commercialUssageName→commercialUsage）", () => {
  const record = projectVrm0LicenseRecord({
    title: "t",
    author: "a",
    commercialUssageName: "Disallow",
    licenseName: "Other",
  });
  assert.equal(record.commercialUsage, "disallow");
  assert.equal(record.rawMeta.commercialUssageName, "Disallow"); // 原拼写保留
  const allow = projectVrm0LicenseRecord({ title: "t", author: "a", commercialUssageName: "Allow" });
  assert.equal(allow.commercialUsage, "allow");
  const unknown = projectVrm0LicenseRecord({ title: "t", author: "a" });
  assert.equal(unknown.commercialUsage, "unknown"); // fail-closed
  // 1.0 投影对照
  const v1 = projectVrm1LicenseRecord({ name: "x", authors: ["a", "b"], commercialUsage: "personalNonProfit" });
  assert.equal(v1.commercialUsage, "nonprofit");
  assert.equal(v1.author, "a, b");
  // 1.0 占位接口显式声明未实现
  assert.equal(configureVrm1SpringBone(null).implemented, false);
  assert.equal(configureVrm1NodeConstraints(null).implemented, false);
  assert.equal(applyVrm1ExpressionOverrides(null).implemented, false);
  assert.throws(() => analyzeVrm1GltfJson({ extensions: { VRM: {} } }));
});

// ── 真实内置模型 headless 结构级加载路径 ────────────────────

test(
  "真实内置模型：0.x 适配路径 + 节点/网格计数 + LicenseRecord",
  { skip: HAS_RESTRICTED_FIXTURES ? false : "可再分发源码不包含受限 VRM fixture" },
  async (t) => {
  const cases = [
    { name: "天工造物z1", bytes: FIXTURE_Z1, title: "ciel" },
    { name: "造物v2", bytes: FIXTURE_V2, title: "wolferia" },
  ];
  for (const item of cases) {
    await t.test(item.name, async () => {
      const { engine, renderer } = createHeadlessEngine();
      const events = [];
      engine.on(EngineEvent.MODEL_LOADED, (p) => events.push(p));
      const result = await engine.loadModel(item.bytes, { label: item.name });
      // 路由断言：走 0.x 适配路径而非 1.0。
      assert.equal(result.specVersion, "0.x");
      assert.equal(result.adapterKind, "vrm0");
      // 结构级计数来自真实字节。
      assert.ok(result.structuralReport.nodeCount > 100, `nodeCount=${result.structuralReport.nodeCount}`);
      assert.ok(result.structuralReport.meshCount > 5, `meshCount=${result.structuralReport.meshCount}`);
      assert.ok(result.structuralReport.humanBoneCount > 20);
      // LicenseRecord 来自真实 meta 投影。
      assert.equal(result.licenseRecord.title, item.title);
      assert.equal(result.licenseRecord.commercialUsage, "allow");
      assert.equal(events.length, 1);
      assert.equal(events[0].specVersion, "0.x");
      // FIRST_RENDERABLE_FRAME 输入信号只在首帧发一次。
      let firstFrame = 0;
      engine.on(EngineEvent.FIRST_RENDERABLE_FRAME, () => { firstFrame += 1; });
      engine.renderFrame();
      engine.renderFrame();
      assert.equal(firstFrame, 1);
      assert.ok(renderer.renderCount >= 2);
      engine.disposeEngine();
    });
  }
  },
);

// ── disposeModel 幂等与共享 Renderer 存活 ───────────────────

test("disposeModel 幂等；普通模型切换不销毁共享 Renderer；disposeEngine 才 dispose", async () => {
  const { engine, renderer } = createHeadlessEngine();
  await engine.loadModel(FIXTURE_Z1, { label: "z1" });
  engine.disposeModel();
  engine.disposeModel(); // 幂等：第二次不抛
  assert.equal(renderer.disposeCount, 0, "disposeModel 不得销毁共享 Renderer（§11.4）");
  // 普通切换：加载第二模型（内部先 dispose 前一模型），Renderer 仍存活。
  await engine.loadModel(FIXTURE_V2, { label: "v2" });
  assert.equal(renderer.disposeCount, 0, "普通模型切换不得销毁共享 Renderer");
  const stats = engine.getStats();
  assert.equal(stats.model.label, "v2");
  engine.disposeEngine();
  assert.equal(renderer.disposeCount, 1, "disposeEngine 必须额外 dispose WebGLRenderer（§11.4）");
  engine.disposeEngine(); // 幂等
  assert.equal(renderer.disposeCount, 1);
  await assert.rejects(() => engine.loadModel(FIXTURE_Z1), (err) => err.code === "engine_disposed");
});

// ── context lost/restored（§20.3）───────────────────────────

test("context lost：preventDefault + 停渲染 + 事件；restored 后恢复", async () => {
  const { engine, canvas, renderer } = createHeadlessEngine();
  await engine.loadModel(FIXTURE_Z1, { label: "z1" });
  const emitted = [];
  engine.on(EngineEvent.CONTEXT_LOST, () => emitted.push("lost"));
  engine.on(EngineEvent.CONTEXT_RESTORED, () => emitted.push("restored"));
  const before = renderer.renderCount;
  let prevented = false;
  canvas.dispatch("webglcontextlost", { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true, "§20.3.1 必须 preventDefault");
  assert.deepEqual(emitted, ["lost"]);
  assert.equal(engine.isContextLost(), true);
  assert.equal(engine.renderFrame(), false, "context lost 期间禁止提交 GPU 命令（§20.3.4）");
  assert.equal(renderer.renderCount, before);
  canvas.dispatch("webglcontextrestored");
  assert.deepEqual(emitted, ["lost", "restored"]);
  assert.equal(engine.isContextLost(), false);
  assert.equal(engine.renderFrame(), true);
  engine.disposeEngine();
});

// ── 语义命令与 viseme ───────────────────────────────────────

test("语义命令接口齐全且 viseme 映射与提取前一致", () => {
  const { engine } = createHeadlessEngine();
  for (const command of ["applyPosture", "applyExpression", "applyGaze", "playGesture", "setSpeaking", "attachSurface", "detachSurface", "loadModel", "disposeModel", "disposeEngine"]) {
    assert.equal(typeof engine[command], "function", `缺少语义命令 ${command}`);
  }
  assert.ok(ENGINE_SEMANTIC_COMMANDS.includes("viseme") || typeof engine.applyVisemeTarget === "function");
  assert.equal(engine.contractVersion, AVATAR_ENGINE_CONTRACT_VERSION);
  // viseme 字符映射 parity
  assert.equal(mapVisemeChar("a"), "aa");
  assert.equal(mapVisemeChar("I"), "ih");
  assert.equal(mapVisemeChar("u"), "ou");
  assert.equal(mapVisemeChar("e"), "ee");
  assert.equal(mapVisemeChar("o"), "oh");
  assert.ok(["aa", "ih", "ou", "ee", "oh"].includes(mapVisemeChar("你", 0)));
  assert.ok(["aa", "ih", "ou", "ee", "oh"].includes(mapVisemeChar("!", 3)));
  engine.disposeEngine();
});

test("applyExpression：统一语义名经 0.x 别名命中 Fcl_ 形态 blendShape", async () => {
  const stub = createStubVrm({ "Fcl_ALL_Joy": {}, blink: {} });
  const canvas = createCanvasStub();
  const renderer = createRendererStub(canvas);
  const engine = createThreeVrmEngine({
    canvas,
    viewport: { width: 320, height: 240 },
    deps: { rendererFactory: () => renderer, parseGltf: async () => ({ userData: { vrm: stub } }) },
  });
  await engine.loadModel(FIXTURE_Z1, { label: "z1" });
  const hit = engine.applyExpression("happy", 0.5);
  assert.equal(hit.matched, true);
  assert.deepEqual(stub.setValueCalls, [["Fcl_ALL_Joy", 0.5]]);
  const miss = engine.applyExpression("nonexistent-emotion", 1);
  assert.equal(miss.matched, false);
  assert.ok(miss.availableKeys.includes("blink"));
  assert.deepEqual(vrm0ExpressionAliases("happy")[0], "happy");
  engine.disposeEngine();
});

// ── GLB 容器结构读取 ────────────────────────────────────────

test("sniffGltfJsonBytes：GLB 严格等值与 JSON chunk 校验", () => {
  const sniffed = sniffGltfJsonBytes(FIXTURE_Z1);
  assert.equal(sniffed.container, "glb");
  assert.equal(sniffed.glbVersion, 2);
  assert.equal(analyzeVrm0GltfJson(sniffed.json).specVersion, "0.x");
  // declaredLength 不一致拒绝
  const tampered = new Uint8Array(FIXTURE_Z1.slice(0, 64));
  const view = new DataView(tampered.buffer, tampered.byteOffset, tampered.byteLength);
  view.setUint32(8, 64, true);
  assert.throws(() => sniffGltfJsonBytes(tampered), (err) => err.code === "glb_length_mismatch" || err.code === "glb_json_chunk_missing");
  assert.throws(() => sniffGltfJsonBytes(new Uint8Array(4)), (err) => err.code === "gltf_container_invalid");
});
