// Avatar P6a 集成缺口测试（P5 → P6a 五项闭环）：
//   A. §17 TTS 事件源：speech-phase-events dispatch 形态 + forwarder phase 消费 + conversation-panel 接线
//   B. §15.4 backendInstanceId：backend-instance 桥 + http-runtime SSE 透传接线
//   C. §8.5 导入 IPC：avatar-asset-host chooseAvatarImportFile/commitCandidate 纯函数 + 真实桥全链 + orphan 不可发现
//   D. biaoxian→引擎映射校准：ThreeVrmEngine.applyPerformanceSemantics（expression/gaze/posture + 降级诊断）
//   E. §14.3 rehost：ThreeVrmEngine.attachSurface DOM 迁移（不重建 renderer/不重解析）+ detach+attach 降级
// 运行：node --test tests/test_avatar_p6a_integration.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import * as THREE from "../app/node_modules/three/build/three.module.js";

import {
  SPEECH_PHASE_EVENT_NAME,
  dispatchSpeechPhase,
} from "../app/frontend-v2/renderer/avatar/speech-phase-events.mjs";
import { createSpeechEventForwarder } from "../app/frontend-v2/renderer/avatar/speech-event-forwarder.mjs";
import {
  BACKEND_INSTANCE_EVENT_NAME,
  BACKEND_INSTANCE_WINDOW_KEY,
  BackendInstanceSource,
  backendInstanceIdFromPayload,
  createBackendInstanceBridge,
} from "../app/frontend-v2/renderer/runtime/backend-instance.mjs";
import { backendInstanceBridge, fetchSse } from "../app/frontend-v2/renderer/runtime/http-runtime.mjs";
import {
  createAvatarImportBridge,
  installAvatarImportBridge,
} from "../app/frontend-v2/renderer/avatar/avatar-import-controller.mjs";
import { createAssetRegistry } from "../app/frontend-v2/renderer/avatar/asset-registry.mjs";
import { createTokenIssuer } from "../app/frontend-v2/renderer/avatar/validated-asset-token.mjs";
import { createMemoryStorageBackend } from "../app/frontend-v2/renderer/avatar/storage-adapter.mjs";
import { sha256HexSync } from "../app/frontend-v2/renderer/avatar/canonical-hash.mjs";
import { createThreeVrmEngine } from "../app/frontend-v2/renderer/avatar/engines/three-vrm-engine.mjs";

const require = createRequire(import.meta.url);
const host = require("../app/avatar-asset-host.cjs");

const nodeSha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

function throwsCode(fn, code) {
  return assert.throws(
    fn,
    (error) => {
      assert.equal(error.code, code, `期望错误码 ${code}，实际 ${error.code}（${error.message}）`);
      return true;
    },
  );
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

// ── 通用测试用具 ─────────────────────────────────────────────

function createClock(start = 0) {
  let t = start;
  return { now: () => t, advance: (ms) => { t += ms; return t; } };
}

function createMockWindow() {
  const dispatched = [];
  const listeners = new Map();
  function MockCustomEvent(name, options = {}) {
    return { type: name, detail: options.detail ?? null };
  }
  return {
    dispatched,
    CustomEvent: MockCustomEvent,
    dispatchEvent(event) { dispatched.push(event); return true; },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type, event) { for (const fn of [...(listeners.get(type) ?? [])]) fn(event); },
  };
}

// 扫描对象中任何字符串值，断言不携带绝对路径（§8.5/§21 opaque 纪律）。
function collectStrings(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) for (const item of value) collectStrings(item, out);
  else if (value !== null && typeof value === "object") for (const key of Object.keys(value)) collectStrings(value[key], out);
  return out;
}
const ABS_PATH_PATTERN = /^(\/|\\\\|[a-zA-Z]:[\\/])/;

function makeTmpDir(label) {
  return mkdtempSync(path.join(tmpdir(), `tiangong-p6a-${label}-`));
}

// ── GLB 字节构造（与 P5 测试同形）────────────────────────────
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

// ═══ A. 缺口1：TTS 事件源（§17）══════════════════════════════

test("P6a-A1 dispatchSpeechPhase：start/energy/stop 各一行 dispatch 且 detail 形态正确", () => {
  const win = createMockWindow();
  const clock = createClock(1234);
  assert.equal(dispatchSpeechPhase("start", { target: win, nowMonotonic: clock.now }), true);
  assert.equal(dispatchSpeechPhase("energy", { energy: 1.7, target: win, nowMonotonic: clock.now }), true);
  assert.equal(dispatchSpeechPhase("stop", { target: win, nowMonotonic: clock.now }), true);
  assert.equal(win.dispatched.length, 3);
  const [start, energy, stop] = win.dispatched;
  for (const event of [start, energy, stop]) assert.equal(event.type, SPEECH_PHASE_EVENT_NAME);
  assert.deepEqual(start.detail, { phase: "start", at: 1234 });
  assert.deepEqual(energy.detail, { phase: "energy", at: 1234, energy: 1 }); // energy 钳到 [0,1]
  assert.deepEqual(stop.detail, { phase: "stop", at: 1234 });
  // 非法 phase 不 dispatch；无 window 环境安全返回 false。
  assert.equal(dispatchSpeechPhase("pause", { target: win }), false);
  assert.equal(win.dispatched.length, 3);
  assert.equal(dispatchSpeechPhase("start", { target: null }), typeof window === "undefined" ? false : true);
});

test("P6a-A2 forwarder window 桥消费 phase 事件：start/energy/stop 全链（§17 单一所有者）", () => {
  const clock = createClock(500);
  const submitted = [];
  const forwarder = createSpeechEventForwarder({ nowMonotonic: clock.now, submit: (wire) => submitted.push(wire) });
  const win = createMockWindow();
  const detach = forwarder.attachWindowBridge({ target: win, ownerId: "tts-owner" });
  win.emit(SPEECH_PHASE_EVENT_NAME, { detail: { phase: "start", at: 999 } });
  win.emit(SPEECH_PHASE_EVENT_NAME, { detail: { phase: "energy", energy: 0.7 } });
  win.emit(SPEECH_PHASE_EVENT_NAME, { detail: { phase: "stop" } });
  assert.deepEqual(submitted.map((w) => w.type), ["speech-start", "speech-energy", "speech-stop"]);
  assert.equal(submitted[0].speaking, true);
  assert.equal(submitted[1].speechEnergy, 0.7);
  assert.equal(submitted[2].speaking, false);
  // 事件戳一律本地单调时钟（payload 自带 at 不采信，§17）。
  assert.equal(submitted[0].speechEventAtMonotonic, 500);
  // stop 后所有权释放，下一段可重新 claim（不抛 speech_owner_conflict）。
  win.emit(SPEECH_PHASE_EVENT_NAME, { detail: { phase: "start" } });
  assert.equal(submitted.length, 4);
  // 旧 kind 写法仍兼容。
  win.emit(SPEECH_PHASE_EVENT_NAME, { detail: { kind: "stop" } });
  assert.equal(submitted.length, 5);
  detach();
});

test("P6a-A3 conversation-panel 接线：单一播放所有者转发文本/边界/终态，不伪造 energy", () => {
  const source = readFileSync(
    new URL("../app/frontend-v2/renderer/plugins/conversation-panel.mjs", import.meta.url),
    "utf8",
  );
  assert.ok(source.includes('from "../avatar/speech-phase-events.mjs"'), "conversation-panel 必须引入 dispatchSpeechPhase");
  assert.equal((source.match(/dispatchSpeechPhase\("start",/g) || []).length, 2);
  assert.equal((source.match(/dispatchSpeechPhase\("stop",/g) || []).length, 3); // browser end/error + generated audio terminal
  assert.equal((source.match(/dispatchSpeechPhase\("boundary",/g) || []).length, 1);
  assert.equal((source.match(/dispatchSpeechPhase\("energy"/g) || []).length, 0, "无 analyser 时不得把 timeupdate 伪造成能量");
  assert.ok(source.includes("speechPlan: result?.speech_plan ?? result?.viseme_timeline ?? null"));
  // 播放逻辑本身未被改动：speak/speakSynth 调用保持原样。
  assert.ok(source.includes("window.speechSynthesis.speak(utterance);"));
  assert.ok(source.includes("await audio.play();"));
});

// ═══ B. 缺口2：backendInstanceId 下发（§15.4）═══════════════

test("P6a-B1 后端载荷提供实例标识：透传 + window 属性 + 事件；变化即轮换", () => {
  const win = createMockWindow();
  const clock = createClock(42);
  const bridge = createBackendInstanceBridge({ target: win, nowMonotonic: clock.now });
  bridge.notePayload({ backendInstanceId: "backend-1", content: "x" });
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "backend-1");
  assert.equal(win.dispatched.length, 1);
  assert.equal(win.dispatched[0].type, BACKEND_INSTANCE_EVENT_NAME);
  assert.deepEqual(win.dispatched[0].detail, { backendInstanceId: "backend-1", source: BackendInstanceSource.BACKEND, at: 42 });
  // 相同标识重复载荷不重复发布。
  bridge.notePayload({ backendInstanceId: "backend-1" });
  assert.equal(win.dispatched.length, 1);
  // 后端进程重启 → 新标识 → 轮换。
  bridge.notePayload({ backendInstanceId: "backend-2" });
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "backend-2");
  assert.equal(win.dispatched.length, 2);
  // 后端标识由后端自己轮换：前端 noteReconnect 不覆盖。
  bridge.noteReconnect();
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "backend-2");
  assert.equal(win.dispatched.length, 2);
});

test("P6a-B2 载荷无字段：前端会话级 UUID 同一连接期稳定、重连更换（§15.4 legacy 降级）", () => {
  const win = createMockWindow();
  let seq = 0;
  const bridge = createBackendInstanceBridge({ target: win, idGenerator: () => `uuid-${(seq += 1)}` });
  const first = bridge.notePayload({ content: "hello" });
  assert.equal(first, "uuid-1");
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "uuid-1");
  assert.equal(win.dispatched[0].detail.source, BackendInstanceSource.FRONTEND_EPOCH);
  // 同一后端连接期稳定：后续无字段载荷不轮换。
  assert.equal(bridge.notePayload({ content: "more" }), "uuid-1");
  assert.equal(win.dispatched.length, 1);
  // 重连更换。
  const rotated = bridge.noteReconnect();
  assert.equal(rotated, "uuid-2");
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "uuid-2");
  assert.equal(win.dispatched.length, 2);
  // 后端标识一旦出现即接管（正式 backendInstanceId 优先）。
  bridge.notePayload({ backend_instance_id: "backend-x" });
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "backend-x");
  assert.equal(backendInstanceIdFromPayload({ meta: { backendInstanceId: "m-1" } }), "m-1");
  assert.equal(backendInstanceIdFromPayload({}), null);
});

test("P6a-B3 http-runtime SSE 接线：载荷 backendInstanceId 透传到 window 并发事件", async (t) => {
  const win = createMockWindow();
  win.tiangongDesktop = {
    getGatewayUrl: () => "http://127.0.0.1:9",
    getGatewayHeaders: () => ({}),
  };
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  globalThis.window = win;
  backendInstanceBridge.reset();
  const sseText = "data: {\"type\":\"done\",\"backendInstanceId\":\"backend-sse-1\",\"content\":\"ok\"}\n\n";
  globalThis.fetch = async () =>
    new Response(sseText, { status: 200, headers: { "content-type": "text/event-stream" } });
  t.after(() => {
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
    backendInstanceBridge.reset();
  });
  let donePayload = null;
  await fetchSse("/v3/chat", { input: "hi" }, { onDone: (payload) => { donePayload = payload; } });
  assert.ok(donePayload !== null, "SSE done 载荷应到达");
  assert.equal(win[BACKEND_INSTANCE_WINDOW_KEY], "backend-sse-1");
  const instanceEvents = win.dispatched.filter((event) => event.type === BACKEND_INSTANCE_EVENT_NAME);
  assert.equal(instanceEvents.length, 1);
  assert.equal(instanceEvents[0].detail.backendInstanceId, "backend-sse-1");
  assert.equal(instanceEvents[0].detail.source, BackendInstanceSource.BACKEND);
});

// ═══ C. 缺口3：导入主进程 IPC 接线（§8.5）═══════════════════

test("P6a-C1 copyWithSha256：流式 SHA-256 与逐字节一致", async () => {
  const dir = makeTmpDir("copy");
  const bytes = new Uint8Array(5 * 1024 * 1024 + 7); // 跨多块（默认读流 64KiB chunk）
  for (let i = 0; i < bytes.length; i += 1) bytes[i] = i % 251;
  const src = path.join(dir, "source.vrm");
  const dst = path.join(dir, "target.tmp");
  writeFileSync(src, bytes);
  const { contentHash, byteLength } = await host.copyWithSha256(src, dst);
  assert.equal(contentHash, nodeSha256(bytes));
  assert.equal(byteLength, bytes.byteLength);
  assert.deepEqual(new Uint8Array(readFileSync(dst)), bytes); // flush 后内容一致
  rmSync(dir, { recursive: true, force: true });
});

test("P6a-C2 chooseAvatarImportFile：.vrm 选择+限额+不可变候选快照+opaque 结果（无绝对路径）", async () => {
  const dir = makeTmpDir("choose");
  const candidateRoot = path.join(dir, "temp");
  const bytes = makeModelBytes();
  const src = path.join(dir, "我的模型.vrm");
  writeFileSync(src, bytes);
  const dialogOptions = [];
  const dialogModule = {
    showOpenDialog: async (_window, options) => {
      dialogOptions.push(options);
      return { canceled: false, filePaths: [src] };
    },
  };
  const picked = await host.chooseAvatarImportFile({ dialogModule, candidateRoot, defaultPath: dir });
  assert.equal(dialogOptions[0].defaultPath, dir, "存在的目录应作为对话框默认路径");
  assert.equal(picked.canceled, false);
  assert.equal(picked.name, "我的模型.vrm");
  assert.equal(picked.contentHash, sha256HexSync(bytes));
  assert.equal(picked.byteLength, bytes.byteLength);
  assert.ok(picked.attemptId.length > 0 && picked.candidateId.length > 0);
  // opaque 纪律：返回值任何字符串都不得是绝对路径（§8.5/§21）。
  for (const text of collectStrings(picked)) {
    assert.equal(ABS_PATH_PATTERN.test(text), false, `结果不得携带绝对路径: ${text}`);
    assert.equal(text.includes(dir), false, `结果不得泄露宿主目录: ${text}`);
  }
  // 不可变候选快照：<candidateRoot>/<contentHash>.vrm 就位且字节一致。
  const snapshot = path.join(candidateRoot, `${picked.contentHash}.vrm`);
  assert.equal(existsSync(snapshot), true);
  assert.deepEqual(new Uint8Array(readFileSync(snapshot)), bytes);
  // 用户取消。
  const cancelled = await host.chooseAvatarImportFile({
    dialogModule: { showOpenDialog: async () => ({ canceled: true, filePaths: [] }) },
    candidateRoot,
  });
  assert.deepEqual(cancelled, { canceled: true });
  // 非 .vrm 拒绝。
  await rejectsCode(
    host.chooseAvatarImportFile({ dialogModule: { showOpenDialog: async () => ({ canceled: false, filePaths: [path.join(dir, "x.glb")] }) }, candidateRoot }),
    "import_ext_invalid",
  );
  // 限额（256MiB 上限的可注入覆盖）：超过即拒。
  await rejectsCode(
    host.chooseAvatarImportFile({ dialogModule, candidateRoot, maxBytes: 10 }),
    "import_too_large",
  );
  // 无效 defaultPath（不存在）回退系统默认目录，不传入对话框。
  await host.chooseAvatarImportFile({
    dialogModule,
    candidateRoot,
    defaultPath: path.join(dir, "no-such-desktop"),
  });
  assert.equal(dialogOptions[dialogOptions.length - 1].defaultPath, undefined);
  rmSync(dir, { recursive: true, force: true });
});

test("P6a-C3 commitCandidate：复核 sha256+原子 rename 到 models/<hash>.vrm；篡改拒绝；幂等提交", async () => {
  const dir = makeTmpDir("commit");
  const candidateRoot = path.join(dir, "temp");
  const modelRoot = path.join(dir, "models");
  const bytes = makeModelBytes();
  const contentHash = sha256HexSync(bytes);
  mkdirSync(candidateRoot, { recursive: true });
  writeFileSync(path.join(candidateRoot, `${contentHash}.vrm`), bytes);
  const committed = await host.commitCandidate(
    { attemptId: "import-1", contentHash, byteLength: bytes.byteLength },
    { candidateRoot, modelRoot },
  );
  assert.deepEqual(committed, {
    assetId: `model:${contentHash}`,
    modelId: `model:${contentHash}`,
    contentHash,
    byteLength: bytes.byteLength,
  });
  // 原子就位：models/<hash>.vrm 存在且 temp 快照已移走。
  assert.deepEqual(new Uint8Array(readFileSync(path.join(modelRoot, `${contentHash}.vrm`))), bytes);
  assert.equal(existsSync(path.join(candidateRoot, `${contentHash}.vrm`)), false);
  // 幂等：正式文件已在位时重复提交复核复用（候选 temp 缺失也不报错）。
  const again = await host.commitCandidate(
    { attemptId: "import-1", contentHash, byteLength: bytes.byteLength },
    { candidateRoot, modelRoot },
  );
  assert.equal(again.contentHash, contentHash);
  // 篡改：候选 temp 内容与声明 contentHash 不一致（且正式区无该哈希文件）→ 复核拒绝。
  const otherBytes = makeModelBytes({ title: "other", author: "y", licenseName: "CC0" });
  const otherHash = sha256HexSync(otherBytes);
  writeFileSync(path.join(candidateRoot, `${otherHash}.vrm`), makeModelBytes({ title: "tampered", author: "x", licenseName: "CC0" }));
  await rejectsCode(
    host.commitCandidate({ attemptId: "import-2", contentHash: otherHash, byteLength: otherBytes.byteLength }, { candidateRoot, modelRoot }),
    "candidate_hash_mismatch",
  );
  // 非法入参。
  await rejectsCode(host.commitCandidate({ attemptId: "", contentHash, byteLength: 1 }, { candidateRoot, modelRoot }), "grant_identity_invalid");
  await rejectsCode(host.commitCandidate({ attemptId: "a", contentHash: "zz", byteLength: 1 }, { candidateRoot, modelRoot }), "content_hash_invalid");
  rmSync(dir, { recursive: true, force: true });
});

test("P6a-C3b deleteModelFile：按 contentHash 删正式文件；缺失幂等；非法 hash/路径拒绝", async () => {
  const dir = makeTmpDir("delete");
  const modelRoot = path.join(dir, "models");
  const bytes = makeModelBytes();
  const contentHash = sha256HexSync(bytes);
  mkdirSync(modelRoot, { recursive: true });
  writeFileSync(path.join(modelRoot, `${contentHash}.vrm`), bytes);

  const removed = await host.deleteModelFile({ contentHash }, { modelRoot });
  assert.deepEqual(removed, { contentHash, deleted: true, missing: false });
  assert.equal(existsSync(path.join(modelRoot, `${contentHash}.vrm`)), false);

  // 缺失文件幂等成功（missing=true）。
  const again = await host.deleteModelFile({ contentHash }, { modelRoot });
  assert.deepEqual(again, { contentHash, deleted: false, missing: true });

  // 非法 hash / 越界路径拒绝。
  await rejectsCode(host.deleteModelFile({ contentHash: "zz" }, { modelRoot }), "content_hash_invalid");
  await rejectsCode(host.deleteModelFile({ contentHash: contentHash.toUpperCase() }, { modelRoot }), "content_hash_invalid");
  await rejectsCode(host.deleteModelFile({ contentHash }, { modelRoot: "" }), "registry_paths_invalid");
  rmSync(dir, { recursive: true, force: true });
});

// fake MessagePort 通道：按 §8.4 协议伺候 ready/chunk/final（bridge 全链测试的 IPC 边界替身）。
function createFakeChannel(bytes) {
  const contentHash = sha256HexSync(bytes);
  const channel = {
    posted: [],
    _cb: null,
    _offset: 0,
    _seq: 0,
    postMessage(message) {
      this.posted.push(message);
      if (message.type === "pull") queueMicrotask(() => this._serve(message.credit));
    },
    onMessage(callback) {
      this._cb = callback;
      queueMicrotask(() => callback({ type: "ready", byteLength: bytes.byteLength, contentHash, chunkSize: 64 * 1024 }));
    },
    _serve(credit) {
      const chunkSize = 64 * 1024;
      for (let i = 0; i < credit && this._offset < bytes.byteLength; i += 1) {
        const end = Math.min(this._offset + chunkSize, bytes.byteLength);
        const chunk = bytes.subarray(this._offset, end);
        this._cb({
          type: "chunk",
          seq: this._seq,
          length: chunk.byteLength,
          bytes: chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength),
        });
        this._seq += 1;
        this._offset = end;
      }
      if (this._offset >= bytes.byteLength) {
        this._cb({ type: "final", contentHash, byteLength: bytes.byteLength });
      }
    },
    close() {},
  };
  return channel;
}

function makeGrantView(bytes, { attemptId = "import-test-1", candidateId = "candidate-test-1" } = {}) {
  return {
    grantId: "crg_test_1",
    attemptId,
    candidateId,
    contentHash: sha256HexSync(bytes),
    byteLength: bytes.byteLength,
    issuerEpoch: 0,
    nonce: "nonce_test_1",
    singleUse: true,
  };
}

test("P6a-C4 真实桥全链：chooseFile→grant→受控读取→预检→commit→登记→Token→selectModel，IPC 载荷无路径", async () => {
  const bytes = makeModelBytes();
  const ipc = { commitPayloads: [], grantPayloads: [], channels: 0 };
  const desktop = {
    avatarImport: {
      chooseFile: async () => ({
        canceled: false,
        name: "自定义.vrm",
        attemptId: "import-test-1",
        candidateId: "candidate-test-1",
        contentHash: sha256HexSync(bytes),
        byteLength: bytes.byteLength,
      }),
      commitCandidate: async (payload) => {
        ipc.commitPayloads.push(payload);
        return { assetId: `model:${sha256HexSync(bytes)}`, modelId: `model:${sha256HexSync(bytes)}` };
      },
      deleteModelFile: async (payload) => ({ deleted: true, missing: false, contentHash: payload.contentHash }),
    },
    avatarAsset: {
      issueCandidateGrant: async (payload) => {
        ipc.grantPayloads.push(payload);
        return makeGrantView(bytes);
      },
      openChannel: () => {
        ipc.channels += 1;
        return createFakeChannel(bytes);
      },
    },
  };
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: 0 });
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: 0 });
  const selected = [];
  const bridge = createAvatarImportBridge({
    desktop,
    registry,
    tokenIssuer,
    runtime: { selectModel: (modelId) => { selected.push(modelId); return Object.freeze({ attemptId: "a", done: Promise.resolve({}) }); } },
  });
  const result = await bridge.importCustomModel();
  assert.equal(result.status, "committed");
  assert.equal(result.ok, true);
  // grant IPC 载荷恰好是 opaque 四元组；commit IPC 载荷恰好是 opaque 三元组（无路径）。
  assert.deepEqual(ipc.grantPayloads, [{
    attemptId: "import-test-1",
    candidateId: "candidate-test-1",
    contentHash: sha256HexSync(bytes),
    byteLength: bytes.byteLength,
  }]);
  assert.deepEqual(ipc.commitPayloads, [{
    attemptId: "import-test-1",
    contentHash: sha256HexSync(bytes),
    byteLength: bytes.byteLength,
  }]);
  for (const payload of [...ipc.grantPayloads, ...ipc.commitPayloads]) {
    for (const text of collectStrings(payload)) assert.equal(ABS_PATH_PATTERN.test(text), false, `IPC 载荷不得携带路径: ${text}`);
  }
  // 候选字节经受控分块通道读取（同一 AssetProvider 复核链）。
  assert.equal(ipc.channels, 1);
  // 登记→签发→selectModel 次序与取值。
  assert.equal(result.token.contentHash, sha256HexSync(bytes));
  assert.deepEqual(selected, [`model:${sha256HexSync(bytes)}`]);
  assert.ok(registry.getRecord(`model:${sha256HexSync(bytes)}`) !== null);
});

test("P6a-C4b 许可确认续接同一候选：choose/grant/read 各一次，opaque token 一次性消费", async () => {
  const bytes = makeModelBytes({
    title: "受限模型",
    author: "tester",
    licenseName: "Redistribution_Prohibited",
    commercialUssageName: "Allow",
    allowedUserName: "Everyone",
  });
  const contentHash = sha256HexSync(bytes);
  const calls = { choose: 0, grant: 0, channel: 0, commit: 0 };
  const desktop = {
    avatarImport: {
      chooseFile: async () => {
        calls.choose += 1;
        return {
          canceled: false,
          name: "受限.vrm",
          attemptId: "import-restricted-1",
          candidateId: "candidate-restricted-1",
          contentHash,
          byteLength: bytes.byteLength,
        };
      },
      commitCandidate: async () => {
        calls.commit += 1;
        return { assetId: `model:${contentHash}`, modelId: `model:${contentHash}` };
      },
      deleteModelFile: async (payload) => ({ deleted: true, missing: false, contentHash: payload.contentHash }),
    },
    avatarAsset: {
      issueCandidateGrant: async () => {
        calls.grant += 1;
        return makeGrantView(bytes, {
          attemptId: "import-restricted-1",
          candidateId: "candidate-restricted-1",
        });
      },
      openChannel: () => {
        calls.channel += 1;
        return createFakeChannel(bytes);
      },
    },
  };
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: 0 });
  const bridge = createAvatarImportBridge({
    desktop,
    registry,
    tokenIssuer: createTokenIssuer({ registry, issuerEpoch: 0 }),
    runtime: { selectModel: () => Object.freeze({ attemptId: "a", done: Promise.resolve({}) }) },
  });

  const blocked = await bridge.importCustomModel();
  assert.equal(blocked.status, "license-blocked");
  assert.equal(typeof blocked.resumeToken, "string");
  assert.deepEqual(calls, { choose: 1, grant: 1, channel: 1, commit: 0 });
  const committed = await bridge.importCustomModel({
    acknowledgeLicense: true,
    resumeToken: blocked.resumeToken,
  });
  assert.equal(committed.status, "committed");
  assert.deepEqual(calls, { choose: 1, grant: 1, channel: 1, commit: 1 });
  assert.equal(bridge.listRegisteredModels()[0].id, `model:${contentHash}`);
  const replay = await bridge.importCustomModel({
    acknowledgeLicense: true,
    resumeToken: blocked.resumeToken,
  });
  assert.equal(replay.code, "resume_token_invalid");
  assert.equal(calls.commit, 1);
});

test("P6a-C4c 删除：deleteModel → registry tombstone + IPC 只传 contentHash + 列表排除", async () => {
  const bytes = makeModelBytes();
  const contentHash = sha256HexSync(bytes);
  const ipc = { deletePayloads: [] };
  const desktop = {
    avatarImport: {
      chooseFile: async () => ({
        canceled: false,
        name: "删除测试.vrm",
        attemptId: "import-del-1",
        candidateId: "candidate-del-1",
        contentHash,
        byteLength: bytes.byteLength,
      }),
      commitCandidate: async () => ({ assetId: `model:${contentHash}`, modelId: `model:${contentHash}` }),
      deleteModelFile: async (payload) => {
        ipc.deletePayloads.push(payload);
        return { deleted: true, missing: false, contentHash: payload.contentHash };
      },
    },
    avatarAsset: {
      issueCandidateGrant: async () => makeGrantView(bytes, { attemptId: "import-del-1", candidateId: "candidate-del-1" }),
      openChannel: () => createFakeChannel(bytes),
    },
  };
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: 0 });
  const bridge = createAvatarImportBridge({
    desktop,
    registry,
    tokenIssuer: createTokenIssuer({ registry, issuerEpoch: 0 }),
    runtime: { selectModel: () => Object.freeze({ attemptId: "a", done: Promise.resolve({}) }) },
  });
  const imported = await bridge.importCustomModel();
  assert.equal(imported.status, "committed");
  assert.equal(bridge.listRegisteredModels().length, 1);

  const deleted = await bridge.deleteModel(`model:${contentHash}`);
  assert.equal(deleted.ok, true);
  assert.equal(deleted.status, "deleted");
  assert.equal(deleted.fileDeleted, true);
  // IPC 载荷只有 opaque contentHash（无 assetId/路径）。
  assert.deepEqual(ipc.deletePayloads, [{ contentHash }]);
  for (const payload of ipc.deletePayloads) {
    for (const text of collectStrings(payload)) {
      assert.equal(ABS_PATH_PATTERN.test(text), false, `删除 IPC 载荷不得携带路径: ${text}`);
    }
  }
  assert.equal(registry.getRecord(`model:${contentHash}`).admissionState, "deleted");
  assert.equal(bridge.listRegisteredModels().length, 0);
});

test("P6a-C5 orphan：commit 成功但登记失败 → 不签发 Token、orphan 不可发现（§8.5.5）", async () => {
  const bytes = makeModelBytes();
  const assetId = `model:${sha256HexSync(bytes)}`;
  let commitCalled = 0;
  const desktop = {
    avatarImport: {
      chooseFile: async () => ({
        canceled: false,
        name: "orphan.vrm",
        attemptId: "import-orphan-1",
        candidateId: "candidate-orphan-1",
        contentHash: sha256HexSync(bytes),
        byteLength: bytes.byteLength,
      }),
      commitCandidate: async () => {
        commitCalled += 1;
        return { assetId, modelId: assetId }; // 原子移动已成功（文件保留在模型区）
      },
      deleteModelFile: async (payload) => ({ deleted: true, missing: false, contentHash: payload.contentHash }),
    },
    avatarAsset: {
      issueCandidateGrant: async () => makeGrantView(bytes, { attemptId: "import-orphan-1", candidateId: "candidate-orphan-1" }),
      openChannel: () => createFakeChannel(bytes),
    },
  };
  const registryBase = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: 0 });
  const registry = Object.freeze({
    ...registryBase,
    registerAsset: async () => { throw new Error("registry_commit_failed"); }, // 登记原子提交失败
  });
  const tokenIssuer = createTokenIssuer({ registry, issuerEpoch: 0 });
  const bridge = createAvatarImportBridge({
    desktop,
    registry,
    tokenIssuer,
    runtime: { selectModel: () => Object.freeze({ attemptId: "a", done: Promise.resolve({}) }) },
  });
  await assert.rejects(() => bridge.importCustomModel(), /registry_commit_failed/);
  assert.equal(commitCalled, 1); // 文件已原子移动（orphan 文件保留）
  // 不可发现：登记记录不存在 → Token 拒绝签发（§8.5.5），正式资源通道无入口。
  assert.equal(registry.getRecord(assetId), null);
  throwsCode(() => tokenIssuer.issueToken(assetId), "asset_not_found");
});

// ═══ D. 缺口4：biaoxian→引擎映射校准 ═════════════════════════

function createCanvasStub() {
  const listeners = new Map();
  return {
    listeners,
    parentNode: null,
    addCalls: [],
    removeCalls: [],
    addEventListener(type, fn) {
      this.addCalls.push(type);
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      this.removeCalls.push(type);
      listeners.get(type)?.delete(fn);
    },
    listenerCount(type) { return listeners.get(type)?.size ?? 0; },
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
    disposeCount: 0,
    sizes: [],
    info: { render: { calls: 0, triangles: 0 }, memory: { geometries: 0, textures: 0 }, programs: [] },
    setPixelRatio() {},
    setSize(width, height) { this.sizes.push([width, height]); },
    render() {},
    dispose() { this.disposeCount += 1; },
  };
}

function makeVrm1Bytes() {
  return buildGlb({
    asset: { version: "2.0" },
    extensions: { VRMC_vrm: { specVersion: "1.0", meta: { name: "stub" } } },
    nodes: [],
    meshes: [],
  });
}

function createStubVrm() {
  const scene = new THREE.Group();
  scene.name = "stub-vrm-scene";
  const geometry = new THREE.BoxGeometry(0.45, 1.7, 0.28);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.position.y = 0.85;
  scene.add(mesh);
  const setValueCalls = [];
  return {
    scene,
    expressionManager: {
      expressionMap: { happy: {}, neutral: {}, aa: {} },
      setValue(name, value) { setValueCalls.push([name, value]); },
    },
    setValueCalls,
    lookAt: null,
    humanoid: null,
    update() {},
  };
}

async function createLoadedEngine() {
  const canvas = createCanvasStub();
  const renderer = createRendererStub(canvas);
  const vrm = createStubVrm();
  const engine = createThreeVrmEngine({
    canvas,
    viewport: { width: 320, height: 240 },
    deps: {
      rendererFactory: () => renderer,
      parseGltf: async () => ({ userData: { vrm } }),
    },
  });
  await engine.loadModel(makeVrm1Bytes(), { label: "stub" });
  // 加载后注入视线/骨骼替身（adapt 阶段不需要它们）。
  const lookAt = { target: null };
  const boneNodes = {};
  vrm.lookAt = lookAt;
  vrm.humanoid = {
    getNormalizedBoneNode: (name) => boneNodes[name] ?? null,
  };
  return { engine, canvas, renderer, vrm, lookAt, boneNodes };
}

test("P6a-D1 expression 名称→expressionManager 目标（含 intensity）；切换先清旧目标（latest-wins）", async () => {
  const { engine, vrm } = await createLoadedEngine();
  const first = engine.applyPerformanceSemantics({ expression: { name: "happy", intensity: 0.35 } });
  assert.deepEqual(first.expression, { name: "happy", intensity: 0.35, matched: true, degraded: false });
  assert.deepEqual(vrm.setValueCalls, [["happy", 0.35]]);
  assert.deepEqual(first.diagnostics, []);
  // 切换：旧目标清零 → 新目标写入。
  const second = engine.applyPerformanceSemantics({ expression: "neutral" });
  assert.deepEqual(vrm.setValueCalls.slice(1), [["happy", 0], ["neutral", 1]]);
  assert.equal(second.expression.matched, true);
  engine.disposeEngine();
});

test("P6a-D2 未知 expression 降级 neutral 并记诊断（含引擎级诊断环）", async () => {
  const { engine, vrm } = await createLoadedEngine();
  const report = engine.applyPerformanceSemantics({ expression: { name: "nonexistent_mood", intensity: 0.9 } });
  assert.equal(report.expression.matched, false);
  assert.equal(report.expression.degraded, true);
  assert.equal(report.expression.degradedTo, "neutral");
  assert.deepEqual(vrm.setValueCalls, [["neutral", 1]]);
  assert.equal(report.diagnostics.length, 1);
  assert.deepEqual(report.diagnostics[0], {
    channel: "expression",
    reason: "unknown_expression",
    requested: "nonexistent_mood",
    degradedTo: "neutral",
  });
  const ring = engine.getPerformanceSemanticsDiagnostics();
  assert.equal(ring.length, 1);
  assert.equal(ring[0].reason, "unknown_expression");
  engine.disposeEngine();
});

test("P6a-D3 gaze 目标→lookAt：社会语义空间化、点目标=Vector3、未知名降级 camera", async () => {
  const { engine, vrm, lookAt } = await createLoadedEngine();
  const named = engine.applyPerformanceSemantics({ gaze: { target: "user" } });
  assert.equal(named.gaze.degraded, false);
  assert.equal(vrm.lookAt.target !== null, true); // 引擎相机绑定
  const left = engine.applyPerformanceSemantics({ gaze: { target: "left" } });
  const leftX = vrm.lookAt.target.position.x;
  const right = engine.applyPerformanceSemantics({ gaze: { target: "right" } });
  assert.equal(left.gaze.spatialTarget, "left");
  assert.equal(right.gaze.spatialTarget, "right");
  assert.notEqual(leftX, vrm.lookAt.target.position.x);
  const point = engine.applyPerformanceSemantics({ gaze: { target: { x: 1, y: 2, z: 3 } } });
  assert.equal(point.gaze.kind, "point");
  assert.equal(point.gaze.degraded, false);
  assert.equal(vrm.lookAt.target.isVector3, true);
  assert.deepEqual([vrm.lookAt.target.x, vrm.lookAt.target.y, vrm.lookAt.target.z], [1, 2, 3]);
  const unknown = engine.applyPerformanceSemantics({ gaze: "ceiling" });
  assert.equal(unknown.gaze.degraded, true);
  assert.equal(unknown.gaze.degradedTo, "camera");
  assert.equal(unknown.diagnostics[0].reason, "unknown_gaze_target");
  engine.disposeEngine();
});

test("P6a-D4 posture→姿态语义槽：显式 bones 直写、registerPostureSlot 自定义槽、未知名降级 neutral 并记诊断", async () => {
  const { engine, vrm, boneNodes } = await createLoadedEngine();
  const rotations = [];
  boneNodes.spine = { quaternion: { set: (...args) => rotations.push(["q", ...args]) }, rotation: { set: (...args) => rotations.push(["e", ...args]) } };
  // 显式 bones 直写。
  const explicit = engine.applyPerformanceSemantics({ posture: { name: "lean", bones: { spine: [0, 0, 0, 1] } } });
  assert.equal(explicit.posture.applied, true);
  assert.equal(explicit.posture.slot, "explicit-bones");
  assert.deepEqual(rotations, [["q", 0, 0, 0, 1]]);
  // 自定义语义槽。
  engine.registerPostureSlot("attentive", { bones: { spine: { x: 0.1, y: 0, z: 0 } } });
  const slotted = engine.applyPerformanceSemantics({ posture: "attentive" });
  assert.equal(slotted.posture.slot, "attentive");
  assert.equal(slotted.posture.degraded, false);
  assert.deepEqual(rotations[1], ["e", 0.1, 0, 0]);
  // 未知名 → neutral 槽（恒等不写骨骼）+ 诊断。
  const before = rotations.length;
  const unknown = engine.applyPerformanceSemantics({ posture: "moonwalk" });
  assert.equal(unknown.posture.degraded, true);
  assert.equal(unknown.posture.degradedTo, "neutral");
  assert.equal(rotations.length, before); // neutral 恒等槽不写任何骨骼
  assert.equal(unknown.diagnostics[0].reason, "unknown_posture_slot");
  engine.disposeEngine();
});

test("H5 adaptive framing: conversation state uses settle delay and slow camera convergence", async () => {
  const { engine } = await createLoadedEngine();
  const camera = engine.debugInternals().camera;
  const initialZ = camera.position.z;
  engine.setConversationState("SPEAKING");
  engine.update(0.1);
  assert.equal(camera.position.z, initialZ, "state transition must not snap the camera");
  for (let i = 0; i < 40; i += 1) engine.update(0.05);
  const speakingZ = camera.position.z;
  assert.ok(speakingZ < initialZ, "speaking converges to a subtly closer portrait");
  engine.setConversationState("THINKING");
  for (let i = 0; i < 80; i += 1) engine.update(0.05);
  assert.ok(camera.position.z > speakingZ, "thinking slowly yields more visual space");
  engine.disposeEngine();
});

// ═══ E. 缺口5：rehost Canvas 迁移（§14.3）═══════════════════

test("P6a-E1 attachSurface(host)：同一 canvas DOM 迁移——不重建 renderer、不重解析模型", async () => {
  const canvas = createCanvasStub();
  const renderer = createRendererStub(canvas);
  let factoryCount = 0;
  let parseCount = 0;
  const engine = createThreeVrmEngine({
    canvas,
    viewport: { width: 320, height: 240 },
    deps: {
      rendererFactory: () => { factoryCount += 1; return renderer; },
      parseGltf: async () => { parseCount += 1; return { userData: { vrm: createStubVrm() } }; },
    },
  });
  await engine.loadModel(makeVrm1Bytes(), { label: "keep" });
  assert.equal(parseCount, 1);
  const movedHost = {
    children: [],
    appendChild(child) { child.parentNode = this; this.children.push(child); },
  };
  const result = engine.attachSurface({ host: movedHost, viewport: { width: 640, height: 480 } });
  assert.equal(result.moved, true);
  assert.equal(result.fallback, null);
  assert.equal(result.rendererRebuilt, false);
  assert.equal(factoryCount, 1); // renderer 未重建
  assert.equal(renderer.disposeCount, 0);
  assert.equal(parseCount, 1); // 模型未重解析
  assert.deepEqual(movedHost.children, [canvas]); // 同一 canvas 元素已 move
  assert.equal(canvas.parentNode, movedHost);
  assert.deepEqual(renderer.sizes.at(-1), [640, 480]); // 视口随迁宿主更新
  engine.disposeEngine();
});

test("P6a-E2 不能迁移时降级 detach+attach：renderer 单例保持、监听重绑、模型不重解析", async () => {
  const canvas = createCanvasStub();
  const renderer = createRendererStub(canvas);
  let factoryCount = 0;
  let parseCount = 0;
  const engine = createThreeVrmEngine({
    canvas,
    viewport: { width: 320, height: 240 },
    deps: {
      rendererFactory: () => { factoryCount += 1; return renderer; },
      parseGltf: async () => { parseCount += 1; return { userData: { vrm: createStubVrm() } }; },
    },
  });
  await engine.loadModel(makeVrm1Bytes(), { label: "keep" });
  const lostAddsBefore = canvas.addCalls.filter((t) => t === "webglcontextlost").length;
  const lostRemovesBefore = canvas.removeCalls.filter((t) => t === "webglcontextlost").length;
  // 宿主既不是 DOM 元素也无 element：无法 move → 降级。
  const result = engine.attachSurface({ host: { id: "lease-host-no-dom" } });
  assert.equal(result.moved, false);
  assert.equal(result.fallback, "detach-attach");
  assert.equal(result.rendererRebuilt, false);
  assert.equal(factoryCount, 1); // renderer 单例
  assert.equal(renderer.disposeCount, 0);
  assert.equal(parseCount, 1);
  // 降级路径调用序列：先 remove（detach）再 add（attach），同一 canvas 重绑监听。
  assert.equal(canvas.removeCalls.filter((t) => t === "webglcontextlost").length, lostRemovesBefore + 1);
  assert.equal(canvas.addCalls.filter((t) => t === "webglcontextlost").length, lostAddsBefore + 1);
  // appendChild 抛错同样落入降级。
  const throwingHost = { appendChild() { throw new Error("dom_denied"); } };
  const again = engine.attachSurface({ host: throwingHost });
  assert.equal(again.fallback, "detach-attach");
  assert.equal(factoryCount, 1);
  engine.disposeEngine();
});

test("P6a-E3 canvas 元素本身更换才重建 renderer；空调用幂等", async () => {
  const canvas = createCanvasStub();
  const first = createRendererStub(canvas);
  const nextCanvas = createCanvasStub();
  const second = createRendererStub(nextCanvas);
  const renderers = [first, second];
  let factoryCount = 0;
  const engine = createThreeVrmEngine({
    canvas,
    viewport: { width: 320, height: 240 },
    deps: { rendererFactory: () => renderers[(factoryCount += 1) - 1] ?? second },
  });
  const idle = engine.attachSurface({}); // 无 host 无新 canvas：幂等
  assert.equal(idle.rendererRebuilt, false);
  assert.equal(factoryCount, 1);
  const rebuilt = engine.attachSurface({ canvas: nextCanvas });
  assert.equal(rebuilt.fallback, "canvas-rebuild");
  assert.equal(rebuilt.rendererRebuilt, true);
  assert.equal(factoryCount, 2);
  assert.equal(first.disposeCount, 1); // 旧 renderer 释放
  // recreateRenderer 跟随当前 canvas（state.canvas 已切换）。
  engine.recreateRenderer();
  assert.equal(factoryCount, 3);
  engine.disposeEngine();
});
