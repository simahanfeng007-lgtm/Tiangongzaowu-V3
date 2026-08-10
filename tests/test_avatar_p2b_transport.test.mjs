// P2b 受控资产传输层测试：协议裁决纯函数、CandidateReadGrant（主进程/渲染侧）、
// MessagePort 分块流（内存 channel 注入）——重组顺序、逐块校验、最终复核、
// 背压、取消、单次消费、epoch、路径隔离。

import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  AdmissionState,
  AssetProviderError,
  CandidateGrantError,
  assetHandleForBuiltin,
  assetHandleForCandidate,
  assetHandleForModel,
  assertGrantForAdmissionPrecheck,
  createAssetProvider,
  createAssetRegistry,
  createCandidateGrantTracker,
  createMemoryStorageBackend,
  createTokenIssuer,
  sha256HexSync,
  validateCandidateGrantView,
} from "../app/frontend-v2/renderer/avatar/index.mjs";

const require = createRequire(import.meta.url);
const host = require("../app/avatar-asset-host.cjs");
const nodeFs = require("node:fs");
const nodeFsp = require("node:fs/promises");

const nodeSha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const flush = async (rounds = 8) => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

// gate 全量排空：宿主 EOF/final 可能在异步读盘完成后才入队，
// 循环"排空 → 让出事件轮 → 再查"，直到队列稳定为空。
async function drainGateFully(gate, { isSettled = null, timeoutMs = 2_000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    gate.drainAll();
    await new Promise((resolve) => setImmediate(resolve));
    if (typeof isSettled === "function") {
      if (isSettled() && gate.pending === 0) return;
      continue;
    }
    if (gate.pending === 0) {
      await new Promise((resolve) => setImmediate(resolve));
      if (gate.pending === 0) return;
    }
  }
  throw new Error(`gate 未在 ${timeoutMs}ms 内完成排空`);
}

// 等待 gate 积累至少 count 条消息（宿主的 open/stat/read 是异步 I/O，
// 全量运行时事件轮时序不可假设，禁止用固定 flush 轮数赌时序）。
async function waitForGate(gate, count, rounds = 200) {
  for (let i = 0; i < rounds; i += 1) {
    if (gate.pending >= count) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  throw new Error(`gate 未在预期内积累 ${count} 条消息（当前 ${gate.pending}）`);
}

// 错误一律按 code 断言（message 是诊断文本，不是契约）。
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

// ── 测试夹具：真实临时目录三棵根 ──────────────────────────────

const TMP = mkdtempSync(path.join(tmpdir(), "tiangong-avatar-p2b-"));
const builtinRoot = path.join(TMP, "assets");
const modelRoot = path.join(TMP, "avatar-models", "models");
const candidateRoot = path.join(TMP, "avatar-models", "temp");
for (const dir of [builtinRoot, modelRoot, candidateRoot]) mkdirSync(dir, { recursive: true });
const registryPaths = { builtinRoot, modelRoot, candidateRoot };
test.after(() => rmSync(TMP, { recursive: true, force: true }));

// 内容带位置编码：任何乱序重组都会破坏相等性与哈希。
function patternedBytes(size) {
  const bytes = Buffer.alloc(size);
  for (let i = 0; i < size; i += 1) bytes[i] = (i * 31 + Math.floor(i / 256)) % 251;
  return bytes;
}

const modelBytes = patternedBytes(10 * 1024 + 3);
const modelHash = nodeSha256(modelBytes);
writeFileSync(path.join(modelRoot, `${modelHash}.vrm`), modelBytes);

const builtinBytes = patternedBytes(5 * 1024 + 7);
const builtinHash = nodeSha256(builtinBytes);
writeFileSync(path.join(builtinRoot, "pet.vrm"), builtinBytes);
const builtinModelMap = new Map([["pet", { file: "pet.vrm", contentHash: builtinHash }]]);
const builtinMapNoHash = new Map([["pet", { file: "pet.vrm" }]]);

const candidateBytes = patternedBytes(3 * 1024 + 1);
const candidateHash = nodeSha256(candidateBytes);
const candidatePath = path.join(candidateRoot, `${candidateHash}.vrm`);
writeFileSync(candidatePath, candidateBytes);

const ISSUER_EPOCH = 7;

function makeGrantIssuer(epoch = ISSUER_EPOCH) {
  return host.createCandidateGrantIssuer({ issuerEpoch: epoch });
}

function issueCandidateGrant(grantIssuer, overrides = {}) {
  return grantIssuer.issueGrant({
    attemptId: "attempt_1",
    candidateId: "candidate_1",
    contentHash: candidateHash,
    byteLength: candidateBytes.byteLength,
    exactResolvedPath: candidatePath,
    ...overrides,
  });
}

// ── 内存 channel（可注入 gate 控制 host→renderer 送达）─────────

function createGate() {
  return {
    queue: [],
    enqueue(fn) {
      this.queue.push(fn);
    },
    drain(count = 1) {
      for (let i = 0; i < count && this.queue.length > 0; i += 1) this.queue.shift()();
    },
    drainAll() {
      while (this.queue.length > 0) this.queue.shift()();
    },
    get pending() {
      return this.queue.length;
    },
  };
}

function createMemoryPair({ gate = null, transform = null } = {}) {
  let rendererCb = null;
  let hostCb = null;
  // 与真实 MessagePort 语义一致：回调注册前的消息排队，注册后补投。
  const pendingForRenderer = [];
  const pendingForHost = [];
  const deliverToRenderer = (message) => {
    const delivered = transform ? transform(message) : message;
    if (rendererCb) rendererCb(delivered);
    else pendingForRenderer.push(delivered);
  };
  const rendererPort = {
    postMessage: (message) => {
      if (hostCb) hostCb(message);
      else pendingForHost.push(message);
    },
    onMessage: (cb) => {
      rendererCb = cb;
      while (pendingForRenderer.length > 0) rendererCb(pendingForRenderer.shift());
    },
    close: () => {},
  };
  const hostPort = {
    postMessage: (message) => (gate ? gate.enqueue(() => deliverToRenderer(message)) : deliverToRenderer(message)),
    onMessage: (cb) => {
      hostCb = cb;
      while (pendingForHost.length > 0) hostCb(pendingForHost.shift());
    },
    close: () => {},
  };
  return { rendererPort, hostPort };
}

// provider ↔ host 接线：channelFactory 返回内存 port，host 控制器可观测。
function wireProviderHost(assetHost, providerOptions = {}, pairOptions = {}) {
  const controllers = [];
  const channelFactory = (descriptor) => {
    const pair = createMemoryPair(pairOptions);
    controllers.push(assetHost.openStream(pair.hostPort, descriptor));
    return pair.rendererPort;
  };
  const provider = createAssetProvider({ channelFactory, timeoutMs: 10_000, ...providerOptions });
  return { provider, controllers, channelFactory };
}

function makeHost(extra = {}) {
  return host.createAvatarAssetHost({
    registryPaths,
    builtinModelMap,
    grantIssuer: makeGrantIssuer(),
    chunkSize: 1024,
    ...extra,
  });
}

async function makeModelToken({ byteLength = modelBytes.byteLength, contentHash = modelHash } = {}) {
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: ISSUER_EPOCH });
  const record = await registry.registerAsset({
    assetId: "asset_model_1",
    scope: "model",
    contentHash,
    byteLength,
    validationReceiptId: "vr_test_1",
    validatorVersion: "vrm-validator/1",
    authorizationFingerprint: "afp_test_1",
  });
  const issuer = createTokenIssuer({ registry, issuerEpoch: ISSUER_EPOCH });
  return { registry, token: issuer.issueToken(record.assetId) };
}

// ── scheme 注册 ───────────────────────────────────────────────

test("registerAvatarAssetScheme：privileges 显式锁定、无 bypassCSP、全进程仅一次", () => {
  const calls = [];
  const mock = { registerSchemesAsPrivileged: (list) => calls.push(list) };
  host.registerAvatarAssetScheme({ protocolModule: mock });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0].scheme, "tiangong-asset");
  assert.deepEqual(calls[0][0].privileges, {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    corsEnabled: true,
    stream: true,
  });
  assert.equal("bypassCSP" in calls[0][0].privileges, false);
  throwsCode(() => host.registerAvatarAssetScheme({ protocolModule: mock }), "scheme_already_registered");
});

// ── scope/路径裁决纯函数 ─────────────────────────────────────

test("parseAssetUrl 解析 scope/id", () => {
  assert.deepEqual(host.parseAssetUrl(`tiangong-asset://model/${modelHash}`), { scope: "model", id: modelHash });
  throwsCode(() => host.parseAssetUrl("https://model/x"), "scheme_not_allowed");
  throwsCode(() => host.parseAssetUrl("not a url"), "url_invalid");
});

test("scope 裁决：目录穿越拒绝", () => {
  throwsCode(
    () => host.resolveScopedAsset({ scope: "builtin", id: "../../secret", registryPaths, builtinModelMap }),
    "asset_id_invalid",
  );
  throwsCode(
    () => host.resolveScopedAsset({ scope: "builtin", id: "..\\..\\secret", registryPaths, builtinModelMap }),
    "asset_id_invalid",
  );
  throwsCode(() => host.assertInsideRoot(modelRoot, path.join(modelRoot, "..", "evil.vrm")), "path_escape");
  // map 值本身带 .. 也拒绝（注入配置不可信）
  const evilMap = new Map([["evil", { file: "../escape.vrm" }]]);
  throwsCode(
    () => host.resolveScopedAsset({ scope: "builtin", id: "evil", registryPaths, builtinModelMap: evilMap }),
    "builtin_model_unregistered",
  );
});

test("scope 裁决：quarantine 默认拒绝、未知 scope 拒绝", () => {
  throwsCode(() => host.resolveScopedAsset({ scope: "quarantine", id: "x", registryPaths }), "scope_quarantine_denied");
  throwsCode(() => host.resolveScopedAsset({ scope: "assets", id: "x", registryPaths }), "scope_not_allowed");
});

test("scope 裁决：builtin 只允许已登记逻辑 modelId", () => {
  throwsCode(
    () => host.resolveScopedAsset({ scope: "builtin", id: "unknown", registryPaths, builtinModelMap }),
    "builtin_model_unregistered",
  );
  const resolved = host.resolveScopedAsset({ scope: "builtin", id: "pet", registryPaths, builtinModelMap });
  assert.equal(resolved.resolvedPath, path.join(builtinRoot, "pet.vrm"));
  assert.equal(resolved.expectedContentHash, builtinHash);
});

test("scope 裁决：model 只允许 <contentHash>.vrm hash 形态", () => {
  const badCases = [
    ["abc", "model_id_not_hash"],
    ["ABCDEF", "model_id_not_hash"],
    [modelHash.toUpperCase(), "model_id_not_hash"],
    [`${modelHash}.vrm`, "model_id_not_hash"],
    [`${modelHash}/x`, "asset_id_invalid"],
  ];
  for (const [bad, code] of badCases) {
    throwsCode(() => host.resolveScopedAsset({ scope: "model", id: bad, registryPaths }), code);
  }
  const resolved = host.resolveScopedAsset({ scope: "model", id: modelHash, registryPaths });
  assert.equal(resolved.resolvedPath, path.join(modelRoot, `${modelHash}.vrm`));
});

test("scope 裁决：candidate 无 grant 拒绝", () => {
  throwsCode(() => host.resolveScopedAsset({ scope: "candidate", id: "crg_x", registryPaths }), "candidate_grant_required");
});

// ── 协议处理器 ────────────────────────────────────────────────

test("protocol handler：只允许 GET", async () => {
  const assetHost = makeHost();
  const response = await assetHost.handleProtocolRequest({ method: "POST", url: `tiangong-asset://model/${modelHash}`, headers: new Headers() });
  assert.equal(response.status, 405);
});

test("protocol handler：quarantine 403、builtin 未登记 404、穿越 400、非 hash 400", async () => {
  const assetHost = makeHost();
  const cases = [
    [`tiangong-asset://quarantine/anything`, 403, "scope_quarantine_denied"],
    [`tiangong-asset://builtin/unknown`, 404, "builtin_model_unregistered"],
    [`tiangong-asset://builtin/..%2F..%2Fsecret`, 400, "asset_id_invalid"],
    [`tiangong-asset://model/notahash`, 400, "model_id_not_hash"],
  ];
  for (const [url, status, code] of cases) {
    const response = await assetHost.handleProtocolRequest({ method: "GET", url, headers: new Headers() });
    assert.equal(response.status, status, url);
    assert.equal(await response.text(), code, url);
  }
});

test("protocol handler：流式 Response（createReadStream，禁 readFile 全量）+ Range", async () => {
  let createReadStreamCalls = 0;
  const fsModule = {
    ...nodeFs,
    createReadStream: (...args) => {
      createReadStreamCalls += 1;
      return nodeFs.createReadStream(...args);
    },
  };
  let readFileCalls = 0;
  const fspModule = {
    ...nodeFsp,
    readFile: (...args) => {
      readFileCalls += 1;
      return nodeFsp.readFile(...args);
    },
  };
  const assetHost = makeHost({ fsModule, fspModule });

  const full = await assetHost.handleProtocolRequest({ method: "GET", url: "tiangong-asset://builtin/pet", headers: new Headers() });
  assert.equal(full.status, 200);
  assert.equal(full.headers.get("content-type"), "application/octet-stream");
  assert.equal(full.headers.get("access-control-allow-origin"), null); // §8.3.10：不发 ACAO:*
  assert.deepEqual(new Uint8Array(await full.arrayBuffer()), new Uint8Array(builtinBytes));
  assert.ok(createReadStreamCalls >= 1, "必须走流式读取");
  assert.equal(readFileCalls, 0, "禁止 readFile 全量读入");

  const ranged = await assetHost.handleProtocolRequest({
    method: "GET",
    url: "tiangong-asset://builtin/pet",
    headers: new Headers({ range: "bytes=2-5" }),
  });
  assert.equal(ranged.status, 206);
  assert.equal(ranged.headers.get("content-range"), `bytes 2-5/${builtinBytes.byteLength}`);
  assert.deepEqual(new Uint8Array(await ranged.arrayBuffer()), new Uint8Array(builtinBytes.subarray(2, 6)));

  const badRange = await assetHost.handleProtocolRequest({
    method: "GET",
    url: "tiangong-asset://builtin/pet",
    headers: new Headers({ range: `bytes=${builtinBytes.byteLength + 10}-` }),
  });
  assert.equal(badRange.status, 416);
});

test("protocol handler：未知 scope 回落 legacyHandler（并存）且审计不含绝对路径", async () => {
  let legacyCalls = 0;
  const legacyHandler = async (request) => {
    legacyCalls += 1;
    return new Response(`legacy:${request.url}`, { status: 200 });
  };
  const assetHost = makeHost({ legacyHandler });
  const response = await assetHost.handleProtocolRequest({ method: "GET", url: "tiangong-asset://assets/foo.vrm", headers: new Headers() });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "legacy:tiangong-asset://assets/foo.vrm");
  assert.equal(legacyCalls, 1);
  const auditText = JSON.stringify(assetHost.auditLog.snapshot());
  assert.ok(!auditText.includes(TMP), "审计不得包含绝对路径");
  assert.ok(!auditText.includes(candidatePath), "审计不得包含候选绝对路径");
});

test("protocol handler：candidate 凭 grant 单次读取，二次拒绝", async () => {
  const grantIssuer = makeGrantIssuer();
  const assetHost = makeHost({ grantIssuer });
  const grant = issueCandidateGrant(grantIssuer);
  const ok = await assetHost.handleProtocolRequest({ method: "GET", url: `tiangong-asset://candidate/${grant.grantId}`, headers: new Headers() });
  assert.equal(ok.status, 200);
  assert.deepEqual(new Uint8Array(await ok.arrayBuffer()), new Uint8Array(candidateBytes));
  const second = await assetHost.handleProtocolRequest({ method: "GET", url: `tiangong-asset://candidate/${grant.grantId}`, headers: new Headers() });
  assert.equal(second.status, 403);
  assert.equal(await second.text(), "grant_consumed");
});

// ── CandidateReadGrant ────────────────────────────────────────

test("grant 主进程侧：opaque 视图无路径字段，singleUse 二次消费拒绝", () => {
  const grantIssuer = makeGrantIssuer();
  const view = issueCandidateGrant(grantIssuer);
  assert.deepEqual(Object.keys(view).sort(), [
    "attemptId",
    "byteLength",
    "candidateId",
    "contentHash",
    "grantId",
    "issuerEpoch",
    "nonce",
    "singleUse",
  ]);
  const viewText = JSON.stringify(view) + JSON.stringify(grantIssuer.getGrantView(view.grantId));
  assert.ok(!viewText.includes(candidatePath) && !viewText.includes(TMP), "renderer 视图不得含绝对路径");
  // 内部记录（主进程侧）持有 exactResolvedPath；消费单次即失效
  const internal = grantIssuer.consumeGrant(view.grantId);
  assert.equal(internal.exactResolvedPath, path.resolve(candidatePath));
  throwsCode(() => grantIssuer.consumeGrant(view.grantId), "grant_consumed");
  throwsCode(() => issueCandidateGrant(grantIssuer, { exactResolvedPath: "relative/x.vrm" }), "grant_path_invalid");
});

test("grant 主进程侧：revoke 后消费拒绝；resolveScopedAsset 单次消费语义", () => {
  const grantIssuer = makeGrantIssuer();
  const revoked = issueCandidateGrant(grantIssuer);
  assert.equal(grantIssuer.revokeGrant(revoked.grantId), true);
  throwsCode(() => grantIssuer.consumeGrant(revoked.grantId), "grant_revoked");
  const once = issueCandidateGrant(grantIssuer);
  const resolved = host.resolveScopedAsset({ scope: "candidate", id: once.grantId, registryPaths, grantIssuer });
  assert.equal(resolved.resolvedPath, path.resolve(candidatePath));
  throwsCode(
    () => host.resolveScopedAsset({ scope: "candidate", id: once.grantId, registryPaths, grantIssuer }),
    "grant_consumed",
  );
});

test("grant 渲染侧：opaque 校验、路径泄漏拒绝、预检哨兵", () => {
  const grantIssuer = makeGrantIssuer();
  const view = issueCandidateGrant(grantIssuer);
  assert.deepEqual(validateCandidateGrantView(view), []);
  assert.ok(validateCandidateGrantView({ ...view, exactResolvedPath: candidatePath }).includes("grant_path_leak"));
  assert.ok(validateCandidateGrantView({ ...view, nested: { note: "C:\\evil\\x.vrm" } }).includes("grant_path_leak"));
  const precheck = assertGrantForAdmissionPrecheck(view);
  assert.equal(precheck.forAdmissionPrecheckOnly, true);
  assert.equal(precheck.contentHash, candidateHash);
  assert.ok(!("locator" in precheck) && !("path" in precheck), "预检引用不得成为引擎输入");
  assert.throws(() => assertGrantForAdmissionPrecheck({ ...view, contentHash: "bad" }), CandidateGrantError);
});

test("grant 渲染侧：singleUse 二次消费拒绝、epoch 不符拒绝", () => {
  const grantIssuer = makeGrantIssuer();
  const view = issueCandidateGrant(grantIssuer);
  const tracker = createCandidateGrantTracker({ issuerEpoch: ISSUER_EPOCH });
  tracker.registerGrant(view);
  tracker.consumeGrant(view.grantId);
  throwsCode(() => tracker.consumeGrant(view.grantId), "grant_consumed");
  // epoch 不符（固定 epoch tracker 收到异 epoch grant）
  const otherEpoch = createCandidateGrantTracker({ issuerEpoch: 99 });
  throwsCode(() => otherEpoch.registerGrant(view), "grant_epoch_mismatch");
  // adopt-first 策略：先登记 epoch 7，后登记 epoch 8 拒绝（进程重启语义）
  const adopt = createCandidateGrantTracker();
  adopt.registerGrant(view);
  assert.equal(adopt.issuerEpoch, ISSUER_EPOCH);
  throwsCode(() => adopt.registerGrant({ ...view, grantId: "crg_other", issuerEpoch: 8 }), "grant_epoch_mismatch");
});

// ── 分块流：重组 / 校验 / 复核 ────────────────────────────────

test("分块流：多 chunk 按 seq 重组，内容与哈希一致后交出 ArrayBuffer", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  const { provider, controllers } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH, highWaterChunks: 3 });
  const stream = provider.openValidatedStream(assetHandleForModel(token));
  const buffer = await stream.done;
  assert.ok(buffer instanceof ArrayBuffer);
  assert.equal(buffer.byteLength, modelBytes.byteLength);
  assert.deepEqual(new Uint8Array(buffer), new Uint8Array(modelBytes));
  assert.equal(sha256HexSync(new Uint8Array(buffer)), modelHash);
  assert.equal(controllers[0].stats().state, "final");
  assert.ok(controllers[0].stats().chunksSent >= 10, `10KiB+3 按 1KiB 分块应 ≥10 块，实际 ${controllers[0].stats().chunksSent}`);
});

test("分块流：每块 length 不符即中止并通知宿主取消", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  let tampered = false;
  const transform = (message) => {
    if (!tampered && message.type === "chunk") {
      tampered = true;
      return { ...message, length: message.length + 1 };
    }
    return message;
  };
  const { provider, controllers } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH }, { transform });
  const stream = provider.openValidatedStream(assetHandleForModel(token));
  await rejectsCode(stream.done, "chunk_length_mismatch");
  await flush();
  assert.equal(controllers[0].stats().state, "cancelled", "宿主收到 cancel 后停止");
});

test("分块流：块序号错乱即中止", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  let tampered = false;
  const transform = (message) => {
    if (!tampered && message.type === "chunk" && message.seq === 2) {
      tampered = true;
      return { ...message, seq: 99 };
    }
    return message;
  };
  const { provider } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH }, { transform });
  await rejectsCode(provider.openValidatedStream(assetHandleForModel(token)).done, "chunk_seq_mismatch");
});

test("分块流：最终重组哈希与 Token 不符即中止", async () => {
  const assetHost = makeHost({ builtinModelMap: builtinMapNoHash }); // ready 不带 hash → 延迟到 final 复核
  const wrongHash = nodeSha256(patternedBytes(64));
  const registry = await createAssetRegistry({ storage: createMemoryStorageBackend(), issuerEpoch: ISSUER_EPOCH });
  const record = await registry.registerAsset({
    assetId: "asset_builtin_1",
    scope: "builtin",
    contentHash: wrongHash, // registry 与 token 一致地错：最终字节复核必须兜住
    byteLength: builtinBytes.byteLength,
    validationReceiptId: "vr_test_2",
    validatorVersion: "vrm-validator/1",
    authorizationFingerprint: "afp_test_2",
  });
  const token = createTokenIssuer({ registry, issuerEpoch: ISSUER_EPOCH }).issueToken(record.assetId);
  const { provider } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH });
  await rejectsCode(provider.openValidatedStream(assetHandleForBuiltin(token, "pet")).done, "stream_hash_mismatch");
});

test("分块流：宿主 final contentHash 被篡改即中止", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  const transform = (message) => (message.type === "final" ? { ...message, contentHash: nodeSha256("tampered") } : message);
  const { provider } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH }, { transform });
  await rejectsCode(provider.openValidatedStream(assetHandleForModel(token)).done, "stream_hash_mismatch");
});

test("分块流：byteLength 与宿主不符即中止（ready 复核）", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken({ byteLength: modelBytes.byteLength + 1 });
  const { provider } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH });
  await rejectsCode(provider.openValidatedStream(assetHandleForModel(token)).done, "host_descriptor_mismatch");
});

test("分块流：Token 与 registry 任一字段不一致，开流前即拒绝", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  let factoryCalls = 0;
  const channelFactory = (descriptor) => {
    factoryCalls += 1;
    const pair = createMemoryPair();
    assetHost.openStream(pair.hostPort, descriptor);
    return pair.rendererPort;
  };
  const provider = createAssetProvider({ channelFactory, registry, issuerEpoch: ISSUER_EPOCH });
  throwsCode(() => provider.openValidatedStream(assetHandleForModel({ ...token, validationReceiptId: "vr_tampered" })), "token_mismatch");
  throwsCode(
    () => provider.openValidatedStream(assetHandleForModel({ ...token, registryEntryVersion: token.registryEntryVersion + 1 })),
    "token_mismatch",
  );
  throwsCode(() => provider.openValidatedStream(assetHandleForModel({ ...token, issuerEpoch: ISSUER_EPOCH + 1 })), "token_mismatch");
  throwsCode(() => provider.openValidatedStream(assetHandleForModel({ ...token, nonce: "" })), "token_invalid");
  assert.equal(factoryCalls, 0, "流前校验失败不得创建通道");
  // 非 admitted 记录同样流前拒绝
  await registry.transitionAdmissionState(token.assetId, AdmissionState.QUARANTINED, { reason: "test" });
  throwsCode(() => provider.openValidatedStream(assetHandleForModel(token)), "token_mismatch");
});

// ── 背压 / 取消 / 单活动流 / 超时 ────────────────────────────

test("背压：高水位后宿主暂停发送，renderer 排空后恢复", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  const gate = createGate();
  const { provider, controllers } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH, highWaterChunks: 2 }, { gate });
  const stream = provider.openValidatedStream(assetHandleForModel(token));
  await waitForGate(gate, 1);
  assert.equal(gate.pending, 1, "ready 在 gate 中待送达");
  gate.drain(1); // ready → provider 发出 pull(2)
  await waitForGate(gate, 2);
  assert.equal(controllers[0].stats().chunksSent, 2, "credit=2 用完即暂停");
  assert.equal(gate.pending, 2, "2 块滞留在 gate（renderer 未排空）");
  await flush();
  assert.equal(controllers[0].stats().chunksSent, 2, "renderer 不排空，宿主不继续发送");
  gate.drain(1); // provider 消费 1 块 → top-up pull(1)
  await waitForGate(gate, 2);
  assert.equal(controllers[0].stats().chunksSent, 3, "排空一块补发一块");
  assert.equal(gate.pending, 2);
  await drainGateFully(gate, {
    isSettled: () => controllers[0].stats().state === "final",
  });
  const buffer = await stream.done;
  assert.deepEqual(new Uint8Array(buffer), new Uint8Array(modelBytes));
});

test("cancel：立即停流，宿主停止读/发，部分 buffer 被释放", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  const gate = createGate();
  const { provider, controllers } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH, highWaterChunks: 2 }, { gate });
  const handle = assetHandleForModel(token);
  const stream = provider.openValidatedStream(handle);
  await waitForGate(gate, 1); // ready
  gate.drain(1);
  await waitForGate(gate, 2); // 2 块在 gate 中
  gate.drain(1); // 第 1 块
  await flush();
  assert.ok(stream.bufferedBytes() > 0, "取消前已有部分 buffer");
  const sentBeforeCancel = controllers[0].stats().chunksSent;
  stream.cancel();
  await rejectsCode(stream.done, "stream_cancelled");
  assert.equal(stream.bufferedBytes(), 0, "部分 buffer 已释放");
  assert.equal(provider.activeStreamCount, 0, "handle 守卫已释放");
  await flush();
  assert.equal(controllers[0].stats().state, "cancelled");
  gate.drainAll();
  await flush();
  assert.equal(controllers[0].stats().chunksSent, sentBeforeCancel, "取消后宿主不再发送");
  // 守卫释放后同一 handle 可再次开流
  const retry = provider.openValidatedStream(handle);
  retry.cancel();
  await rejectsCode(retry.done, "stream_cancelled");
});

test("单 handle 一次仅一个活动流", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  const gate = createGate();
  const { provider } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH }, { gate });
  const handle = assetHandleForModel(token);
  const first = provider.openValidatedStream(handle);
  throwsCode(() => provider.openValidatedStream(handle), "stream_already_active");
  first.cancel();
  await rejectsCode(first.done, "stream_cancelled");
});

test("超时：整体时限内未完成即中止", async () => {
  const assetHost = makeHost();
  const { registry, token } = await makeModelToken();
  const pendingTimers = [];
  const fakeTimers = {
    setTimeout: (fn, ms) => {
      const timer = { fn, ms };
      pendingTimers.push(timer);
      return timer;
    },
    clearTimeout: () => {},
  };
  const gate = createGate(); // ready 永不送达 → 流悬挂
  const { provider } = wireProviderHost(assetHost, { registry, issuerEpoch: ISSUER_EPOCH, timers: fakeTimers, timeoutMs: 50 }, { gate });
  const stream = provider.openValidatedStream(assetHandleForModel(token));
  assert.equal(pendingTimers.length, 1);
  assert.equal(pendingTimers[0].ms, 50);
  pendingTimers[0].fn();
  await rejectsCode(stream.done, "stream_timeout");
});

// ── candidate 端到端（内存 channel + grant）────────────────────

test("candidate：凭 grant 读取候选快照，渲染侧与主进程侧均单次失效", async () => {
  const grantIssuer = makeGrantIssuer();
  const assetHost = makeHost({ grantIssuer });
  const grantView = issueCandidateGrant(grantIssuer);
  const tracker = createCandidateGrantTracker({ issuerEpoch: ISSUER_EPOCH });
  tracker.registerGrant(grantView);
  tracker.consumeGrant(grantView.grantId); // 渲染侧记账
  const { provider } = wireProviderHost(assetHost, {});
  const stream = provider.openValidatedStream(assetHandleForCandidate(grantView));
  const buffer = await stream.done;
  assert.deepEqual(new Uint8Array(buffer), new Uint8Array(candidateBytes));
  // 主进程侧 grant 已单次消费：再次开流被宿主拒绝
  const again = provider.openValidatedStream(assetHandleForCandidate({ ...grantView, grantId: grantView.grantId }));
  await assert.rejects(again.done, (error) => {
    assert.equal(error.code, "host_stream_error");
    assert.match(error.message, /grant_consumed/);
    return true;
  });
});
