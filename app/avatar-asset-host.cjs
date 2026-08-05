// P2b 受控资产传输层（主进程侧）：tiangong-asset 协议注册、scope 路径裁决、
// CandidateReadGrant 主进程记录、MessagePort 有界分块流宿主。
// 方案依据：§8.3（受控协议/scope 根目录）、§8.4（IPC 备选：MessagePort 分块拷贝、
// 背压、取消、顺序、超时）、§8.5（CandidateReadGrant：exactResolvedPath 只存主进程）、
// §21（安全边界：renderer 只见 opaque 字段，审计不含绝对路径）。
//
// CommonJS；Electron 只在函数内惰性 require，纯逻辑可在 Node 测试环境独立加载。

"use strict";

const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { Readable } = require("node:stream");

const ASSET_SCHEME = "tiangong-asset";

// §8.3 / BOM（Electron 43.1.1）：privileges 显式锁定；bypassCSP 必须省略（=false）。
const AVATAR_PROTOCOL_PRIVILEGES = Object.freeze({
  standard: true,
  secure: true,
  supportFetchAPI: true,
  corsEnabled: true,
  stream: true,
});

const ASSET_SCOPES = Object.freeze({
  BUILTIN: "builtin",
  MODEL: "model",
  CANDIDATE: "candidate",
  QUARANTINE: "quarantine",
});
const KNOWN_SCOPES = Object.freeze(Object.values(ASSET_SCOPES));
// quarantine 默认拒绝（§8.3：只允许隔离诊断流程在用户明确操作后取得一次性诊断 grant，P2b 不开放）。
const SERVABLE_SCOPES = Object.freeze([ASSET_SCOPES.BUILTIN, ASSET_SCOPES.MODEL, ASSET_SCOPES.CANDIDATE]);

const DEFAULT_CHUNK_SIZE = 1024 * 1024; // §8.4：1 MiB 有界分块
const DEFAULT_HIGH_WATER_CHUNKS = 4;
const DEFAULT_STREAM_TIMEOUT_MS = 60_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;
const MAX_PULL_CREDIT = 1024;
const MAX_ASSET_ID_LENGTH = 512;
const AUDIT_LOG_LIMIT = 200;

const HEX64 = /^[0-9a-f]{64}$/;

class AssetHostError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AssetHostError";
    this.code = code;
  }
}

function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const key of Object.keys(value)) deepFreeze(value[key]);
    Object.freeze(value);
  }
  return value;
}

// ── 有界审计（§21：不含绝对路径，不出主进程）────────────────

function createAuditLog({ limit = AUDIT_LOG_LIMIT, now = () => Date.now() } = {}) {
  const entries = [];
  return {
    record(entry) {
      entries.push(Object.freeze({ at: now(), ...entry }));
      if (entries.length > limit) entries.splice(0, entries.length - limit);
    },
    snapshot() {
      return entries.slice();
    },
    get size() {
      return entries.length;
    },
  };
}

// ── scheme 注册（app ready 前，全进程仅一次）─────────────────

let schemeRegistrationDone = false;

function registerAvatarAssetScheme({ protocolModule } = {}) {
  if (schemeRegistrationDone) {
    throw new AssetHostError(
      "scheme_already_registered",
      "registerSchemesAsPrivileged 全进程仅允许调用一次（§8.3.4）",
    );
  }
  const proto = protocolModule ?? require("electron").protocol;
  proto.registerSchemesAsPrivileged([
    { scheme: ASSET_SCHEME, privileges: { ...AVATAR_PROTOCOL_PRIVILEGES } },
  ]);
  schemeRegistrationDone = true;
}

// ── URL 解析与 scope 路径裁决（纯函数，可测试）────────────────

// tiangong-asset://<scope>/<idOrHash>；id 必须是单段（不含分隔符），解码失败即拒绝。
function parseAssetUrl(urlString) {
  let parsed;
  try {
    parsed = new URL(String(urlString));
  } catch (_error) {
    throw new AssetHostError("url_invalid", "资产 URL 无法解析");
  }
  if (parsed.protocol !== `${ASSET_SCHEME}:`) {
    throw new AssetHostError("scheme_not_allowed", `只允许 ${ASSET_SCHEME}: 协议`);
  }
  const scope = String(parsed.host || "").toLowerCase();
  let id;
  try {
    id = decodeURIComponent(String(parsed.pathname || "").replace(/^\/+/, ""));
  } catch (_error) {
    throw new AssetHostError("url_invalid", "资产 URL id 解码失败");
  }
  return { scope, id };
}

// resolve 后必须仍落在 root 内（防目录穿越，§8.3.12）。
function assertInsideRoot(rootDir, resolvedPath) {
  const root = path.resolve(String(rootDir));
  const full = path.resolve(String(resolvedPath));
  if (full !== root && !full.startsWith(root + path.sep)) {
    throw new AssetHostError("path_escape", "解析路径越出允许的根目录");
  }
  return full;
}

function assertRegistryPaths(registryPaths) {
  if (registryPaths === null || typeof registryPaths !== "object") {
    throw new AssetHostError("registry_paths_invalid", "需要注入 registryPaths");
  }
  for (const key of ["builtinRoot", "modelRoot", "candidateRoot"]) {
    if (typeof registryPaths[key] !== "string" || registryPaths[key].length === 0) {
      throw new AssetHostError("registry_paths_invalid", `registryPaths.${key} 必须是非空字符串`);
    }
  }
}

function normalizeBuiltinMap(builtinModelMap) {
  if (builtinModelMap instanceof Map) return builtinModelMap;
  const map = new Map();
  if (builtinModelMap && typeof builtinModelMap === "object") {
    for (const [modelId, entry] of Object.entries(builtinModelMap)) {
      map.set(modelId, entry);
    }
  }
  return map;
}

function lookupBuiltinEntry(builtinModelMap, modelId) {
  const entry = normalizeBuiltinMap(builtinModelMap).get(modelId);
  if (!entry) return null;
  if (typeof entry === "string") return { file: entry, contentHash: null };
  if (typeof entry === "object" && typeof entry.file === "string") {
    return { file: entry.file, contentHash: HEX64.test(entry.contentHash || "") ? entry.contentHash : null };
  }
  return null;
}

// scope 裁决：返回 { scope, assetRef, resolvedPath, expectedContentHash, grant? }。
// assetRef 用于审计，绝不携带绝对路径。candidate scope 在此单次消费 grant。
function resolveScopedAsset({ scope, id, registryPaths, builtinModelMap, grantIssuer }) {
  assertRegistryPaths(registryPaths);
  if (scope === ASSET_SCOPES.QUARANTINE) {
    throw new AssetHostError("scope_quarantine_denied", "quarantine 默认拒绝读取（§8.3）");
  }
  if (!SERVABLE_SCOPES.includes(scope)) {
    throw new AssetHostError("scope_not_allowed", `不允许的 scope: ${scope}`);
  }
  if (typeof id !== "string" || id.length === 0 || id.length > MAX_ASSET_ID_LENGTH) {
    throw new AssetHostError("asset_id_invalid", "资产 id 必须是非空有界字符串");
  }
  // 单段 id：含路径分隔符/NUL 即拒绝（目录穿越防线第一刀，第二刀在 assertInsideRoot）。
  if (id.includes("/") || id.includes("\\") || id.indexOf(String.fromCharCode(0)) !== -1 || id === ".." || id === ".") {
    throw new AssetHostError("asset_id_invalid", "资产 id 不允许包含路径分隔符");
  }
  if (scope === ASSET_SCOPES.BUILTIN) {
    const entry = lookupBuiltinEntry(builtinModelMap, id);
    if (entry === null) {
      throw new AssetHostError("builtin_model_unregistered", `内置逻辑 modelId 未登记: ${id}`);
    }
    const rel = entry.file;
    if (path.isAbsolute(rel) || rel.split(/[\\/]+/).some((part) => part === "..")) {
      throw new AssetHostError("builtin_model_unregistered", `内置 modelId 映射的文件名不安全: ${id}`);
    }
    const resolvedPath = assertInsideRoot(registryPaths.builtinRoot, path.resolve(registryPaths.builtinRoot, rel));
    return { scope, assetRef: `builtin:${id}`, resolvedPath, expectedContentHash: entry.contentHash };
  }
  if (scope === ASSET_SCOPES.MODEL) {
    if (!HEX64.test(id)) {
      throw new AssetHostError("model_id_not_hash", "model scope 只允许 <contentHash>（64 位小写 hex）");
    }
    const resolvedPath = assertInsideRoot(registryPaths.modelRoot, path.resolve(registryPaths.modelRoot, `${id}.vrm`));
    return { scope, assetRef: `model:${id}`, resolvedPath, expectedContentHash: id };
  }
  // candidate：只允许凭 CandidateReadGrant 映射的单个不可变快照（§8.3/§8.5）。
  if (!grantIssuer || typeof grantIssuer.consumeGrant !== "function") {
    throw new AssetHostError("candidate_grant_required", "candidate scope 需要 CandidateReadGrant");
  }
  const grant = grantIssuer.consumeGrant(id); // 单次消费；内部记录含 exactResolvedPath
  const resolvedPath = assertInsideRoot(registryPaths.candidateRoot, grant.exactResolvedPath);
  return {
    scope,
    assetRef: `candidate:${grant.candidateId}`,
    resolvedPath,
    expectedContentHash: grant.contentHash,
    grant,
  };
}

// ── CandidateReadGrant 主进程侧登记（§8.5）───────────────────
// exactResolvedPath 只存在于本模块内部记录；issueGrant/getGrantView 返回的
// opaque 视图只含 grantId/attemptId/candidateId/contentHash/byteLength/issuerEpoch/nonce/singleUse。

function createCandidateGrantIssuer({ issuerEpoch = 0, now = () => Date.now(), nonceGenerator } = {}) {
  if (!Number.isInteger(issuerEpoch) || issuerEpoch < 0) {
    throw new AssetHostError("issuer_epoch_invalid", "issuerEpoch 必须是非负整数");
  }
  const records = new Map();
  let counter = 0;
  const nextNonce =
    nonceGenerator ??
    (() => {
      counter += 1;
      return `${crypto.randomBytes(12).toString("hex")}_${counter}`;
    });

  function opaqueView(record) {
    return deepFreeze({
      grantId: record.grantId,
      attemptId: record.attemptId,
      candidateId: record.candidateId,
      contentHash: record.contentHash,
      byteLength: record.byteLength,
      issuerEpoch: record.issuerEpoch,
      nonce: record.nonce,
      singleUse: record.singleUse,
    });
  }

  return {
    get issuerEpoch() {
      return issuerEpoch;
    },

    issueGrant({ attemptId, candidateId, contentHash, byteLength, exactResolvedPath, singleUse = true } = {}) {
      for (const [field, value] of [["attemptId", attemptId], ["candidateId", candidateId]]) {
        if (typeof value !== "string" || value.length === 0 || value.length > MAX_ASSET_ID_LENGTH) {
          throw new AssetHostError("grant_identity_invalid", `${field} 必须是非空有界字符串`);
        }
      }
      if (!HEX64.test(contentHash ?? "")) {
        throw new AssetHostError("content_hash_invalid", "grant 需要 64 位小写 hex contentHash");
      }
      if (!Number.isInteger(byteLength) || byteLength <= 0) {
        throw new AssetHostError("byte_length_invalid", "grant 需要正整数 byteLength");
      }
      if (typeof exactResolvedPath !== "string" || !path.isAbsolute(exactResolvedPath)) {
        throw new AssetHostError("grant_path_invalid", "exactResolvedPath 必须是主进程内的绝对路径");
      }
      if (typeof singleUse !== "boolean") {
        throw new AssetHostError("single_use_invalid", "singleUse 必须是布尔值");
      }
      const grantId = `crg_${nextNonce()}`;
      const record = Object.freeze({
        grantId,
        attemptId,
        candidateId,
        contentHash,
        byteLength,
        exactResolvedPath: path.resolve(exactResolvedPath),
        issuerEpoch,
        nonce: nextNonce(),
        singleUse,
        state: "active",
        createdAtMs: now(),
      });
      records.set(grantId, record);
      return opaqueView(record);
    },

    getGrantView(grantId) {
      const record = records.get(String(grantId || ""));
      return record ? opaqueView(record) : null;
    },

    // 主进程内部解析用：返回含 exactResolvedPath 的内部记录；singleUse 消费即失效。
    consumeGrant(grantId) {
      const record = records.get(String(grantId || ""));
      if (!record) throw new AssetHostError("grant_not_found", "CandidateReadGrant 不存在");
      if (record.issuerEpoch !== issuerEpoch) {
        throw new AssetHostError("grant_epoch_mismatch", "CandidateReadGrant 不属于当前进程 epoch");
      }
      if (record.state === "consumed") {
        throw new AssetHostError("grant_consumed", "CandidateReadGrant 单次消费后已失效");
      }
      if (record.state === "revoked") {
        throw new AssetHostError("grant_revoked", "CandidateReadGrant 已撤销");
      }
      if (record.singleUse) {
        records.set(record.grantId, Object.freeze({ ...record, state: "consumed" }));
      }
      return record;
    },

    revokeGrant(grantId) {
      const record = records.get(String(grantId || ""));
      if (!record || record.state !== "active") return false;
      records.set(record.grantId, Object.freeze({ ...record, state: "revoked" }));
      return true;
    },

    // 进程 epoch 轮换/导入流程终止时整体失效（§8.5：取消、失败、超时或 epoch 变化后 grant 立即失效）。
    revokeAll() {
      for (const [grantId, record] of records) {
        if (record.state === "active") {
          records.set(grantId, Object.freeze({ ...record, state: "revoked" }));
        }
      }
    },

    get activeCount() {
      let count = 0;
      for (const record of records.values()) if (record.state === "active") count += 1;
      return count;
    },
  };
}

// ── MessagePort 规范化（Electron MessagePortMain / 测试内存 port）──

function normalizePort(port) {
  if (!port || typeof port.postMessage !== "function") {
    throw new AssetHostError("port_invalid", "需要可 postMessage 的 MessagePort");
  }
  if (typeof port.start === "function") port.start();
  if (typeof port.on === "function") {
    // Electron MessagePortMain / Node EventEmitter 风格
    return {
      postMessage: (message) => port.postMessage(message),
      onMessage: (callback) => port.on("message", (event) => callback(event && event.data !== undefined ? event.data : event)),
      close: () => port.close(),
    };
  }
  if (typeof port.onMessage === "function") {
    return {
      postMessage: (message) => port.postMessage(message),
      onMessage: (callback) => port.onMessage(callback),
      close: () => (typeof port.close === "function" ? port.close() : undefined),
    };
  }
  if (typeof port.addEventListener === "function") {
    return {
      postMessage: (message) => port.postMessage(message),
      onMessage: (callback) => port.addEventListener("message", (event) => callback(event.data)),
      close: () => port.close(),
    };
  }
  throw new AssetHostError("port_invalid", "MessagePort 缺少消息订阅接口");
}

// ── 分块流宿主（§8.4）：credit 背压 + seq/length 校验 + 超时 + 取消 ──
// 协议（host → renderer）：ready{byteLength,contentHash,chunkSize} →
//   chunk{seq,length,bytes}* → final{contentHash,byteLength} | error{code} | cancelled。
// 协议（renderer → host）：pull{credit} | cancel。
// host 只在 credit>0 时读盘与发送：renderer 不补足 credit，host 即停（背压）。
// 每块 bytes 都是全新 ArrayBuffer 拷贝，不使用 transferable（§8.4.2）。

function openChunkedStream(
  port,
  {
    resolvedPath,
    expectedContentHash = null,
    chunkSize = DEFAULT_CHUNK_SIZE,
    timeoutMs = DEFAULT_STREAM_TIMEOUT_MS,
    signal = null,
    timers = { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (t) => clearTimeout(t) },
    fsModule = fs,
    fspModule = fsp,
  } = {},
) {
  if (typeof resolvedPath !== "string" || !path.isAbsolute(resolvedPath)) {
    throw new AssetHostError("resolved_path_invalid", "openChunkedStream 需要主进程内的绝对路径");
  }
  if (!Number.isInteger(chunkSize) || chunkSize < 1024 || chunkSize > 16 * 1024 * 1024) {
    throw new AssetHostError("chunk_size_invalid", "chunkSize 必须是 [1KiB, 16MiB] 的整数");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new AssetHostError("timeout_invalid", "timeoutMs 必须是正数");
  }
  const portApi = normalizePort(port);
  const stats = {
    state: "opening",
    chunksRead: 0,
    chunksSent: 0,
    bytesSent: 0,
    creditBalance: 0,
    startedAtMs: Date.now(),
  };
  let credit = 0;
  let seq = 0;
  let totalRead = 0;
  let cancelled = false;
  let finished = false;
  let pumping = false;
  let handle = null;
  let idleTimer = null;
  const hash = crypto.createHash("sha256");
  const readBuffer = Buffer.allocUnsafe(chunkSize);

  function safePost(message) {
    try {
      portApi.postMessage(message);
    } catch (_error) {
      // port 已被对端销毁：按取消处理（后续读盘循环会在标志位处停下）。
      cancelled = true;
      stats.state = "cancelled";
    }
  }

  function clearIdleTimer() {
    if (idleTimer !== null) {
      timers.clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  async function closeHandle() {
    clearIdleTimer();
    if (handle !== null) {
      const current = handle;
      handle = null;
      await current.close().catch(() => {});
    }
  }

  function armIdleTimer() {
    clearIdleTimer();
    idleTimer = timers.setTimeout(() => {
      fail("stream_timeout", "分块流空闲超时");
    }, timeoutMs);
    if (typeof idleTimer.unref === "function") idleTimer.unref();
  }

  function fail(code, message) {
    if (finished || cancelled) return;
    finished = true;
    stats.state = "error";
    safePost({ type: "error", code, message: String(message || code) });
    void closeHandle().finally(() => portApi.close());
  }

  function cancel(reason) {
    if (finished || cancelled) return;
    cancelled = true;
    stats.state = "cancelled";
    safePost({ type: "cancelled", reason: String(reason || "cancelled") });
    void closeHandle().finally(() => portApi.close());
  }

  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (credit > 0 && !cancelled && !finished && handle !== null) {
        const { bytesRead } = await handle.read(readBuffer, 0, chunkSize, null);
        if (cancelled || finished) return;
        if (bytesRead === 0) {
          finished = true;
          stats.state = "final";
          safePost({ type: "final", contentHash: hash.digest("hex"), byteLength: totalRead });
          await closeHandle();
          portApi.close();
          return;
        }
        credit -= 1;
        stats.creditBalance = credit;
        const chunk = readBuffer.subarray(0, bytesRead);
        hash.update(chunk);
        totalRead += bytesRead;
        const chunkSeq = seq; // seq 从 0 开始，与 provider 重组计数对齐
        seq += 1;
        stats.chunksRead += 1;
        stats.chunksSent += 1;
        stats.bytesSent += bytesRead;
        // 全新 ArrayBuffer 拷贝（§8.4.2：不宣称/不依赖 transferable）。
        const bytes = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + bytesRead);
        safePost({ type: "chunk", seq: chunkSeq, length: bytesRead, bytes });
        armIdleTimer();
      }
    } catch (error) {
      fail("read_failed", error && error.message ? error.message : error);
    } finally {
      pumping = false;
    }
  }

  portApi.onMessage((message) => {
    if (!message || typeof message !== "object") return;
    if (message.type === "pull") {
      const n = Number(message.credit);
      if (!Number.isInteger(n) || n < 1 || n > MAX_PULL_CREDIT) return;
      if (cancelled || finished) return;
      credit = Math.min(credit + n, MAX_PULL_CREDIT);
      stats.creditBalance = credit;
      armIdleTimer();
      void pump();
    } else if (message.type === "cancel") {
      cancel("renderer_cancel");
    }
  });

  if (signal) {
    if (signal.aborted) {
      cancel("aborted");
    } else if (typeof signal.addEventListener === "function") {
      signal.addEventListener("abort", () => cancel("aborted"), { once: true });
    }
  }

  void (async () => {
    try {
      handle = await fspModule.open(resolvedPath, "r");
      const stat = await handle.stat();
      if (!stat.isFile()) throw new AssetHostError("asset_not_found", "目标不是常规文件");
      stats.state = "streaming";
      safePost({ type: "ready", byteLength: stat.size, contentHash: expectedContentHash, chunkSize });
      armIdleTimer();
      // 不主动推送数据：等 renderer 的 pull credit（背压起点）。
    } catch (error) {
      const code = error instanceof AssetHostError ? error.code : "open_failed";
      fail(code, error && error.message ? error.message : error);
    }
  })();

  return {
    stats: () => ({ ...stats, creditBalance: credit }),
    cancel: (reason = "controller_cancel") => cancel(reason),
  };
}

// ── 协议响应（§8.3：流式 Response，禁止先 readFile 全量读入）──────

function parseRangeHeader(rangeHeader, size) {
  if (typeof rangeHeader !== "string" || rangeHeader.length === 0) return null;
  const match = /^bytes=(\d+)-(\d*)$/.exec(rangeHeader.trim());
  if (!match) return null; // 无法解析的 Range 按完整响应处理
  const start = Number(match[1]);
  const end = match[2] === "" ? size - 1 : Number(match[2]);
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size) {
    throw new AssetHostError("range_not_satisfiable", "Range 越界");
  }
  return { start, end: Math.min(end, size - 1) };
}

const PROTOCOL_RESPONSE_HEADERS = Object.freeze({
  "content-type": "application/octet-stream", // §8.3.15：MIME 固定
  "cache-control": "no-cache",
  "accept-ranges": "bytes",
  // §8.3.10：不发 Access-Control-Allow-Origin:*；当前 file:// UI 为 opaque origin，
  // 受控协议不发任何 CORS 头（渲染侧 P2b 消费走 §8.4 MessagePort 通道）。
});

async function streamFileResponse(request, resolved, { fsModule, fspModule, timeoutMs }) {
  let stat;
  try {
    stat = await fspModule.stat(resolved.resolvedPath);
  } catch (_error) {
    throw new AssetHostError("asset_not_found", "资产文件不存在");
  }
  if (!stat.isFile()) throw new AssetHostError("asset_not_found", "资产不是常规文件");
  const size = stat.size;
  const rangeHeader =
    request && request.headers && typeof request.headers.get === "function" ? request.headers.get("range") : null;
  const range = parseRangeHeader(rangeHeader, size);

  const streamOptions = range ? { start: range.start, end: range.end } : {};
  const stream = fsModule.createReadStream(resolved.resolvedPath, streamOptions);
  // 请求中止与整体超时：销毁底层流（§8.3.14），禁止悬挂 fd。
  const timer = setTimeout(() => stream.destroy(new Error("stream_timeout")), timeoutMs);
  if (typeof timer.unref === "function") timer.unref();
  stream.on("close", () => clearTimeout(timer));
  if (request && request.signal && typeof request.signal.addEventListener === "function") {
    request.signal.addEventListener("abort", () => stream.destroy(), { once: true });
  }
  const body = Readable.toWeb(stream);
  if (range) {
    return new Response(body, {
      status: 206,
      headers: {
        ...PROTOCOL_RESPONSE_HEADERS,
        "content-range": `bytes ${range.start}-${range.end}/${size}`,
        "content-length": String(range.end - range.start + 1),
      },
    });
  }
  return new Response(body, {
    status: 200,
    headers: { ...PROTOCOL_RESPONSE_HEADERS, "content-length": String(size) },
  });
}

function denialStatusFor(code) {
  if (code === "scope_quarantine_denied" || code === "scope_not_allowed" || code.startsWith("grant_")) return 403;
  if (code === "builtin_model_unregistered" || code === "asset_not_found" || code === "candidate_grant_required") return 404;
  if (code === "range_not_satisfiable") return 416;
  return 400;
}

// ── 宿主装配：协议处理器 + 分块流入口共享裁决/审计/grant ────────

function createAvatarAssetHost({
  registryPaths,
  builtinModelMap = new Map(),
  grantIssuer = null,
  auditLog = null,
  legacyHandler = null,
  chunkSize = DEFAULT_CHUNK_SIZE,
  streamTimeoutMs = DEFAULT_STREAM_TIMEOUT_MS,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  fsModule = fs,
  fspModule = fsp,
} = {}) {
  assertRegistryPaths(registryPaths);
  const audit = auditLog ?? createAuditLog();

  function resolveDescriptor(descriptor) {
    if (descriptor === null || typeof descriptor !== "object") {
      throw new AssetHostError("descriptor_invalid", "需要 {scope, locator} 描述符");
    }
    const scope = String(descriptor.scope || "").toLowerCase();
    const locator = String(descriptor.locator ?? descriptor.id ?? "");
    return resolveScopedAsset({ scope, id: locator, registryPaths, builtinModelMap, grantIssuer });
  }

  return {
    get grantIssuer() {
      return grantIssuer;
    },
    get auditLog() {
      return audit;
    },

    // tiangong-asset://<scope>/<id> 的 protocol.handle 处理器（只允许 GET）。
    async handleProtocolRequest(request) {
      const method = String((request && request.method) || "GET").toUpperCase();
      if (method !== "GET") {
        audit.record({ action: "protocol", scope: "-", outcome: "deny", reason: "method_not_allowed" });
        return new Response("method_not_allowed", { status: 405 });
      }
      let scope = "legacy";
      try {
        const parsed = parseAssetUrl(request.url);
        scope = parsed.scope;
        // 未知 scope（含 legacy 的 tiangong-asset://assets/... 形态）：交给注入的 legacy 通道（并存到 P7）。
        if (!KNOWN_SCOPES.includes(scope)) {
          if (typeof legacyHandler === "function") {
            audit.record({ action: "protocol", scope: "legacy", outcome: "allow", reason: "legacy_fallback" });
            return await legacyHandler(request);
          }
          throw new AssetHostError("scope_not_allowed", `未知 scope: ${scope}`);
        }
        const resolved = resolveScopedAsset({ scope, id: parsed.id, registryPaths, builtinModelMap, grantIssuer });
        const response = await streamFileResponse(request, resolved, { fsModule, fspModule, timeoutMs: requestTimeoutMs });
        audit.record({ action: "protocol", scope, assetRef: resolved.assetRef, outcome: "allow", reason: "ok" });
        return response;
      } catch (error) {
        const code = error instanceof AssetHostError ? error.code : "internal_error";
        audit.record({ action: "protocol", scope, outcome: "deny", reason: code });
        return new Response(code, { status: denialStatusFor(code) });
      }
    },

    // §8.4 IPC 备选：MessagePort 分块流。candidate grant 在此单次消费。
    openStream(port, descriptor) {
      try {
        const resolved = resolveDescriptor(descriptor);
        audit.record({ action: "port-stream", scope: resolved.scope, assetRef: resolved.assetRef, outcome: "allow", reason: "ok" });
        return openChunkedStream(port, {
          resolvedPath: resolved.resolvedPath,
          expectedContentHash: resolved.expectedContentHash,
          chunkSize,
          timeoutMs: streamTimeoutMs,
          fsModule,
          fspModule,
        });
      } catch (error) {
        const code = error instanceof AssetHostError ? error.code : "internal_error";
        audit.record({ action: "port-stream", scope: String(descriptor && descriptor.scope) || "-", outcome: "deny", reason: code });
        try {
          const portApi = normalizePort(port);
          portApi.postMessage({ type: "error", code, message: String((error && error.message) || code) });
          portApi.close();
        } catch (_error) {
          // port 不可用时仅落审计
        }
        return null;
      }
    },
  };
}

// ready 后在实际使用的 session/partition 上注册（§8.3.8）。
function installAvatarAssetProtocol({ session, protocolModule, ...hostOptions } = {}) {
  const host = createAvatarAssetHost(hostOptions);
  const proto = protocolModule ?? (session ?? require("electron").session.defaultSession).protocol;
  proto.handle(ASSET_SCHEME, (request) => host.handleProtocolRequest(request));
  return host;
}

// ── P6a §8.5 导入主流程（主进程侧）────────────────────────────
// 纪律：主进程只做文件选择、限额、受控复制和流式 SHA-256（§8.5），绝不解析 VRM
// 内容；返回给 renderer 的结果只含 opaque 字段（name/attemptId/candidateId/
// contentHash/byteLength），不含任何绝对路径（§8.5/§21）。

const MAX_VRM_IMPORT_BYTES = 256 * 1024 * 1024; // §9.3 maxFileBytes = 256 MiB

// 流式复制 + SHA-256：源文件分块读，边写边算哈希，结束后 fsync（flush 到盘）。
// 返回 { contentHash, byteLength }；任何一步失败都清理半成品目标文件。
async function copyWithSha256(sourcePath, targetPath, { fsModule = fs, fspModule = fsp } = {}) {
  const hash = crypto.createHash("sha256");
  let byteLength = 0;
  let handle = null;
  try {
    handle = await fspModule.open(targetPath, "wx"); // 独占创建，禁止覆盖既有快照
    const stream = fsModule.createReadStream(sourcePath);
    for await (const chunk of stream) {
      hash.update(chunk);
      byteLength += chunk.byteLength;
      await handle.write(chunk);
    }
    await handle.sync(); // flush：rename 之前内容必须落盘（§8.5）
    await handle.close();
    handle = null;
    return { contentHash: hash.digest("hex"), byteLength };
  } finally {
    if (handle !== null) {
      await handle.close().catch(() => {});
      await fspModule.unlink(targetPath).catch(() => {});
    }
  }
}

// 受控复制到 candidateRoot 内的 staging 文件，flush 后原子 rename 为
// <candidateRoot>/<contentHash>.vrm（同目录 rename=同文件系统原子替换）。
// 目标已存在（同内容哈希的不可变快照）时幂等复用，不重复复制。
async function stageCandidateSnapshot({
  sourcePath,
  candidateRoot,
  fsModule = fs,
  fspModule = fsp,
  stagingNonce = crypto.randomBytes(8).toString("hex"),
} = {}) {
  const root = path.resolve(String(candidateRoot));
  await fspModule.mkdir(root, { recursive: true });
  const stagingPath = assertInsideRoot(root, path.join(root, `.staging-${stagingNonce}`));
  const { contentHash, byteLength } = await copyWithSha256(sourcePath, stagingPath, { fsModule, fspModule });
  const snapshotPath = assertInsideRoot(root, path.join(root, `${contentHash}.vrm`));
  const existing = await fspModule.stat(snapshotPath).catch(() => null);
  if (existing !== null && existing.isFile() && existing.size === byteLength) {
    await fspModule.unlink(stagingPath).catch(() => {}); // 幂等：同哈希快照已在位
    return { contentHash, byteLength, reused: true };
  }
  await fspModule.rename(stagingPath, snapshotPath);
  return { contentHash, byteLength, reused: false };
}

// §8.5 第 1 步：dialog 选 .vrm → 限额 → 受控复制 + 流式 SHA-256 → 不可变候选快照。
// 返回 opaque 结果（不含绝对路径）；硬违规（扩展名/大小/非普通文件）抛 AssetHostError。
async function chooseAvatarImportFile({
  dialogModule,
  browserWindow = null,
  candidateRoot,
  defaultPath = null,
  maxBytes = MAX_VRM_IMPORT_BYTES,
  fsModule = fs,
  fspModule = fsp,
  idGenerator = () => crypto.randomUUID(),
} = {}) {
  const dialog = dialogModule ?? require("electron").dialog;
  if (typeof candidateRoot !== "string" || candidateRoot.length === 0) {
    throw new AssetHostError("registry_paths_invalid", "chooseAvatarImportFile 需要 candidateRoot");
  }
  const dialogOptions = {
    title: "选择 VRM 身体模型",
    properties: ["openFile"],
    filters: [{ name: "VRM 模型", extensions: ["vrm"] }],
  };
  // 源码工作版会把 HOME/USERPROFILE 重定向到隔离目录，导致对话框默认打开
  // “假桌面”；有真实桌面目录时优先作为 defaultPath，避免用户找不到本机文件。
  if (typeof defaultPath === "string" && defaultPath.length > 0) {
    try {
      const stat = await fspModule.stat(defaultPath);
      if (stat.isDirectory()) dialogOptions.defaultPath = defaultPath;
    } catch (_error) {
      // defaultPath 无效（不存在/不可访问）时回退系统默认目录
    }
  }
  const picked = await dialog.showOpenDialog(browserWindow, dialogOptions);
  if (picked?.canceled || !Array.isArray(picked?.filePaths) || !picked.filePaths[0]) {
    return deepFreeze({ canceled: true });
  }
  const selected = path.resolve(String(picked.filePaths[0]));
  if (path.extname(selected).toLowerCase() !== ".vrm") {
    throw new AssetHostError("import_ext_invalid", "只允许导入 .vrm 模型文件");
  }
  const stat = await fspModule.stat(selected).catch(() => null);
  if (stat === null || !stat.isFile() || stat.size <= 0) {
    throw new AssetHostError("import_source_invalid", "所选文件为空或不是普通文件");
  }
  if (stat.size > maxBytes) {
    throw new AssetHostError("import_too_large", `VRM 文件超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 限额（§9.3）`);
  }
  const { contentHash, byteLength } = await stageCandidateSnapshot({ sourcePath: selected, candidateRoot, fsModule, fspModule });
  return deepFreeze({
    canceled: false,
    name: path.basename(selected),
    attemptId: `import-${idGenerator()}`,
    candidateId: `candidate-${contentHash.slice(0, 16)}`,
    contentHash,
    byteLength,
  });
}

// §8.5 提交：temp 快照复核 sha256+byteLength（§8.6 字节同一性），flush 后原子
// rename 到 <modelRoot>/<contentHash>.vrm。modelRoot 已有同哈希文件时幂等提交。
// orphan 规则（§8.5.5）：本函数只保证文件原子就位；若渲染侧 AssetRegistry 登记
// 随后失败，文件保留但不可发现——正式资源通道只凭 ValidatedAssetToken 服务，
// 而 Token 只对已提交登记记录签发（渲染侧 validated-asset-token 拒绝 orphan）。
async function commitCandidate(
  { attemptId, contentHash, byteLength } = {},
  { candidateRoot, modelRoot, fsModule = fs, fspModule = fsp } = {},
) {
  if (typeof attemptId !== "string" || attemptId.length === 0 || attemptId.length > MAX_ASSET_ID_LENGTH) {
    throw new AssetHostError("grant_identity_invalid", "commitCandidate 需要非空有界 attemptId");
  }
  if (!HEX64.test(String(contentHash ?? ""))) {
    throw new AssetHostError("content_hash_invalid", "commitCandidate 需要 64 位小写 hex contentHash");
  }
  if (!Number.isInteger(byteLength) || byteLength <= 0) {
    throw new AssetHostError("byte_length_invalid", "commitCandidate 需要正整数 byteLength");
  }
  if (typeof candidateRoot !== "string" || typeof modelRoot !== "string") {
    throw new AssetHostError("registry_paths_invalid", "commitCandidate 需要 candidateRoot 与 modelRoot");
  }
  const candidatePath = assertInsideRoot(candidateRoot, path.resolve(candidateRoot, `${contentHash}.vrm`));
  const modelDir = path.resolve(String(modelRoot));
  await fspModule.mkdir(modelDir, { recursive: true });
  const modelPath = assertInsideRoot(modelDir, path.join(modelDir, `${contentHash}.vrm`));

  // §8.6 复核：解析前重新读取不可变快照，立即复核 byteLength 和 SHA-256。
  const verify = async (targetPath) => {
    const hash = crypto.createHash("sha256");
    let total = 0;
    const stream = fsModule.createReadStream(targetPath);
    for await (const chunk of stream) {
      hash.update(chunk);
      total += chunk.byteLength;
    }
    if (total !== byteLength || hash.digest("hex") !== contentHash) {
      throw new AssetHostError("candidate_hash_mismatch", "候选快照复核失败（byteLength/SHA-256 不一致）");
    }
  };

  const existingModel = await fspModule.stat(modelPath).catch(() => null);
  if (existingModel !== null && existingModel.isFile()) {
    await verify(modelPath); // 幂等提交：同哈希正式文件已在位，复核后复用
    await fspModule.unlink(candidatePath).catch(() => {}); // 清理 temp 快照
  } else {
    await verify(candidatePath);
    await fspModule.rename(candidatePath, modelPath); // 原子 rename（同文件系统）
  }
  return deepFreeze({
    assetId: `model:${contentHash}`,
    modelId: `model:${contentHash}`,
    contentHash,
    byteLength,
  });
}

// §8.5 用户删除：按 contentHash 删除正式模型文件（<modelRoot>/<hash>.vrm）。
// 只接受 64 位小写 hex 并限制在 modelRoot 内；文件不存在视为幂等成功。
// 调用方（渲染侧）必须先完成 AssetRegistry admitted→deleted tombstone，
// 再调用本函数删文件：tombstone 后文件不可发现/不可加载，删文件失败只留孤儿字节。
async function deleteModelFile(
  { contentHash } = {},
  { modelRoot, fspModule = fsp } = {},
) {
  if (!HEX64.test(String(contentHash ?? ""))) {
    throw new AssetHostError("content_hash_invalid", "deleteModelFile 需要 64 位小写 hex contentHash");
  }
  if (typeof modelRoot !== "string" || modelRoot.length === 0) {
    throw new AssetHostError("registry_paths_invalid", "deleteModelFile 需要 modelRoot");
  }
  const modelDir = path.resolve(String(modelRoot));
  const modelPath = assertInsideRoot(modelDir, path.join(modelDir, `${contentHash}.vrm`));
  const stat = await fspModule.stat(modelPath).catch(() => null);
  if (stat === null || !stat.isFile()) {
    return deepFreeze({ contentHash, deleted: false, missing: true });
  }
  await fspModule.unlink(modelPath);
  return deepFreeze({ contentHash, deleted: true, missing: false });
}

module.exports = {
  ASSET_SCHEME,
  AVATAR_PROTOCOL_PRIVILEGES,
  ASSET_SCOPES,
  KNOWN_SCOPES,
  SERVABLE_SCOPES,
  DEFAULT_CHUNK_SIZE,
  DEFAULT_HIGH_WATER_CHUNKS,
  DEFAULT_STREAM_TIMEOUT_MS,
  MAX_VRM_IMPORT_BYTES,
  AssetHostError,
  createAuditLog,
  registerAvatarAssetScheme,
  parseAssetUrl,
  assertInsideRoot,
  resolveScopedAsset,
  createCandidateGrantIssuer,
  normalizePort,
  openChunkedStream,
  createAvatarAssetHost,
  installAvatarAssetProtocol,
  copyWithSha256,
  stageCandidateSnapshot,
  chooseAvatarImportFile,
  commitCandidate,
  deleteModelFile,
};
