// §8.4/§8.6 AssetProvider：AssetHandle 消费端。
// 经 MessagePort 分块拉流：credit 背压（高水位暂停请求）、按 seq 重组、每块长度校验、
// 全部完成后复核 byteLength 与 contentHash；token 句柄另与 ValidatedAssetToken 逐项
// 一致（registry 注入时含 admissionState/registryEntryVersion/issuerEpoch）才交出
// ArrayBuffer；任一不一致即中止并报错。channelFactory 可注入（测试用内存 channel）。
//
// port 契约（渲染侧）：{ postMessage(message), onMessage(callback), close() }。

import { validateAssetTokenForUse, validateAssetTokenShape } from "./contracts.mjs";
import { sha256HexSync } from "./canonical-hash.mjs";
import { AdmissionState } from "./asset-registry.mjs";

export class AssetProviderError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AssetProviderError";
    this.code = code;
  }
}

export const PROVIDER_DEFAULT_CHUNK_SIZE = 1024 * 1024;
export const PROVIDER_DEFAULT_HIGH_WATER_CHUNKS = 4;
export const PROVIDER_DEFAULT_TIMEOUT_MS = 60_000;
export const MAX_ACCEPTED_CHUNK_BYTES = 16 * 1024 * 1024;

// ── AssetHandle 构造（纯数据句柄；§8.6：任何重新打开都凭句柄完成）────

export function assetHandleForModel(token) {
  if (token === null || typeof token !== "object") {
    throw new AssetProviderError("token_invalid", "model 句柄需要 ValidatedAssetToken");
  }
  return Object.freeze({ kind: "asset-handle", scope: "model", locator: token.contentHash, token, grant: null });
}

export function assetHandleForBuiltin(token, modelId) {
  if (token === null || typeof token !== "object") {
    throw new AssetProviderError("token_invalid", "builtin 句柄需要 ValidatedAssetToken");
  }
  if (typeof modelId !== "string" || modelId.length === 0) {
    throw new AssetProviderError("handle_invalid", "builtin 句柄需要逻辑 modelId");
  }
  return Object.freeze({ kind: "asset-handle", scope: "builtin", locator: modelId, token, grant: null });
}

// candidate 尚无 ValidatedAssetToken（§8.5：Token 在准入通过后才签发）；
// 候选读取凭 grant 的 contentHash/byteLength 复核，产物只喂 ModelAdmissionGate 预检。
export function assetHandleForCandidate(grantView) {
  if (grantView === null || typeof grantView !== "object" || typeof grantView.grantId !== "string") {
    throw new AssetProviderError("handle_invalid", "candidate 句柄需要 CandidateReadGrant 视图");
  }
  return Object.freeze({
    kind: "asset-handle",
    scope: "candidate",
    locator: grantView.grantId,
    token: null,
    grant: Object.freeze({ contentHash: grantView.contentHash, byteLength: grantView.byteLength, grantId: grantView.grantId }),
  });
}

function expectationsFromHandle(handle) {
  if (handle.token) {
    return {
      contentHash: handle.token.contentHash,
      byteLength: handle.token.byteLength,
      guardKey: `token:${handle.token.assetId}:${handle.token.nonce}`,
    };
  }
  return {
    contentHash: handle.grant.contentHash,
    byteLength: handle.grant.byteLength,
    guardKey: `candidate:${handle.grant.grantId}`,
  };
}

// ── Provider ─────────────────────────────────────────────────

export function createAssetProvider({
  channelFactory,
  registry = null,
  issuerEpoch = null,
  sha256 = sha256HexSync,
  timers = { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (t) => clearTimeout(t) },
  highWaterChunks = PROVIDER_DEFAULT_HIGH_WATER_CHUNKS,
  timeoutMs = PROVIDER_DEFAULT_TIMEOUT_MS,
} = {}) {
  if (typeof channelFactory !== "function") {
    throw new AssetProviderError("channel_factory_invalid", "AssetProvider 需要注入 channelFactory(descriptor) → port");
  }
  if (!Number.isInteger(highWaterChunks) || highWaterChunks < 1 || highWaterChunks > 64) {
    throw new AssetProviderError("high_water_invalid", "highWaterChunks 必须是 [1,64] 的整数");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new AssetProviderError("timeout_invalid", "timeoutMs 必须是正数");
  }
  const activeGuards = new Set();

  // 打开前校验：token 结构 + （注入 registry 时）逐项一致性 + admissionState。
  function precheckToken(token) {
    const shapeErrors = validateAssetTokenShape(token);
    if (shapeErrors.length > 0) {
      throw new AssetProviderError("token_invalid", `ValidatedAssetToken 结构非法: ${shapeErrors.join(",")}`);
    }
    if (registry !== null) {
      const record = typeof registry.getRecord === "function" ? registry.getRecord(token.assetId) : null;
      if (record === null) {
        throw new AssetProviderError("token_mismatch", "registry_record_missing");
      }
      if (record.admissionState !== AdmissionState.ADMITTED) {
        throw new AssetProviderError("token_mismatch", `asset_not_admitted:${record.admissionState}`);
      }
      const epoch = issuerEpoch ?? registry.issuerEpoch ?? token.issuerEpoch;
      const use = validateAssetTokenForUse(token, { ...record, issuerEpoch: epoch });
      if (!use.ok) {
        throw new AssetProviderError("token_mismatch", `ValidatedAssetToken 与 registry 不一致: ${use.errors.join(",")}`);
      }
    }
  }

  function openValidatedStream(handle, overrides = {}) {
    if (handle === null || typeof handle !== "object" || handle.kind !== "asset-handle") {
      throw new AssetProviderError("handle_invalid", "需要 assetHandleForModel/Builtin/Candidate 构造的 AssetHandle");
    }
    if (handle.token) precheckToken(handle.token);
    const expected = expectationsFromHandle(handle);
    const effectiveHighWater = overrides.highWaterChunks ?? highWaterChunks;
    const effectiveTimeout = overrides.timeoutMs ?? timeoutMs;

    // 单 handle 一次仅一个活动流。
    if (activeGuards.has(expected.guardKey)) {
      throw new AssetProviderError("stream_already_active", `handle 已有活动流: ${expected.guardKey}`);
    }
    activeGuards.add(expected.guardKey);

    const descriptor = Object.freeze({
      kind: "validated-stream-request",
      scope: handle.scope,
      locator: handle.locator,
      assetId: handle.token ? handle.token.assetId : null,
      contentHash: expected.contentHash,
      byteLength: expected.byteLength,
      registryEntryVersion: handle.token ? handle.token.registryEntryVersion : null,
      issuerEpoch: handle.token ? handle.token.issuerEpoch : null,
    });
    const port = channelFactory(descriptor);
    if (!port || typeof port.postMessage !== "function" || typeof port.onMessage !== "function") {
      activeGuards.delete(expected.guardKey);
      throw new AssetProviderError(
        "channel_invalid",
        "channelFactory 必须返回 { postMessage, onMessage, close } 形态的 port",
      );
    }

    let chunks = [];
    let receivedBytes = 0;
    let expectedSeq = 0;
    let settled = false;
    let readySeen = false;
    let timer = null;
    let rejectFn = null;
    let resolveFn = null;

    function releasePartial() {
      chunks = null; // 释放部分 buffer（§P2b：cancel 立即释放）
    }

    function cleanup() {
      if (timer !== null) {
        timers.clearTimeout(timer);
        timer = null;
      }
      activeGuards.delete(expected.guardKey);
      releasePartial();
      try {
        if (typeof port.close === "function") port.close();
      } catch (_error) {
        // port 关闭失败不影响结算
      }
    }

    function safePost(message) {
      try {
        port.postMessage(message);
      } catch (_error) {
        // 对端已销毁；结算路径不依赖该消息
      }
    }

    function settleReject(code, message) {
      if (settled) return;
      settled = true;
      safePost({ type: "cancel" }); // 任一不一致即中止：通知宿主停读停发
      const error = new AssetProviderError(code, message);
      cleanup();
      rejectFn(error);
    }

    function settleResolve(buffer) {
      if (settled) return;
      settled = true;
      cleanup();
      resolveFn(buffer);
    }

    function cancel() {
      if (settled) return;
      settled = true;
      safePost({ type: "cancel" });
      const error = new AssetProviderError("stream_cancelled", "流已被调用方取消");
      cleanup();
      rejectFn(error);
    }

    function onMessage(message) {
      if (settled || !message || typeof message !== "object") return;
      if (message.type === "ready") {
        readySeen = true;
        // 宿主复述的字节承载字段必须与期望逐项一致（§8.6 字节同一性前置校验）。
        if (message.byteLength !== expected.byteLength) {
          settleReject("host_descriptor_mismatch", `宿主 byteLength=${message.byteLength} 与期望 ${expected.byteLength} 不一致`);
          return;
        }
        if (message.contentHash != null && message.contentHash !== expected.contentHash) {
          settleReject("host_descriptor_mismatch", "宿主 contentHash 与期望不一致");
          return;
        }
        safePost({ type: "pull", credit: effectiveHighWater }); // 初始窗口
        return;
      }
      // error/cancelled 在任何相位都合法（宿主打开失败会先发 error，无 ready）。
      if (message.type === "error") {
        settleReject("host_stream_error", `宿主错误 ${message.code}: ${message.message || ""}`);
        return;
      }
      if (message.type === "cancelled") {
        settleReject("stream_cancelled", `宿主取消: ${message.reason || "unknown"}`);
        return;
      }
      if (!readySeen) {
        settleReject("protocol_violation", "未收到 ready 即收到其他消息");
        return;
      }
      if (message.type === "chunk") {
        if (message.seq !== expectedSeq) {
          settleReject("chunk_seq_mismatch", `块序号 ${message.seq} != 期望 ${expectedSeq}`);
          return;
        }
        const view =
          message.bytes instanceof Uint8Array
            ? message.bytes
            : message.bytes instanceof ArrayBuffer
              ? new Uint8Array(message.bytes)
              : null;
        if (view === null || view.byteLength !== message.length) {
          settleReject("chunk_length_mismatch", "块 length 字段与实际字节数不符");
          return;
        }
        if (message.length < 1 || message.length > MAX_ACCEPTED_CHUNK_BYTES) {
          settleReject("chunk_length_mismatch", `块长度越界: ${message.length}`);
          return;
        }
        expectedSeq += 1;
        receivedBytes += message.length;
        if (receivedBytes > expected.byteLength) {
          settleReject("stream_length_mismatch", "累计字节超出期望 byteLength");
          return;
        }
        chunks.push(view);
        safePost({ type: "pull", credit: 1 }); // 排空一块补一块：高水位背压
        return;
      }
      if (message.type === "final") {
        if (receivedBytes !== expected.byteLength || message.byteLength !== expected.byteLength) {
          settleReject("stream_length_mismatch", `最终 byteLength 复核失败: 收到 ${receivedBytes}, 宿主报 ${message.byteLength}, 期望 ${expected.byteLength}`);
          return;
        }
        const assembled = new Uint8Array(receivedBytes);
        let offset = 0;
        for (const chunk of chunks) {
          assembled.set(chunk, offset);
          offset += chunk.byteLength;
        }
        const digest = sha256(assembled);
        if (digest !== expected.contentHash) {
          settleReject("stream_hash_mismatch", "重组字节 SHA-256 与期望 contentHash 不一致");
          return;
        }
        if (message.contentHash !== expected.contentHash) {
          settleReject("stream_hash_mismatch", "宿主 final contentHash 与期望不一致");
          return;
        }
        releasePartial();
        settleResolve(assembled.buffer);
        return;
      }
    }

    const done = new Promise((resolve, reject) => {
      resolveFn = resolve;
      rejectFn = reject;
    });
    timer = timers.setTimeout(() => {
      settleReject("stream_timeout", `分块流 ${effectiveTimeout}ms 内未完成`);
    }, effectiveTimeout);
    if (timer && typeof timer.unref === "function") timer.unref();
    port.onMessage(onMessage);

    return {
      done,
      cancel,
      // 诊断：取消/完成后部分 buffer 已释放（返回 0）。
      bufferedBytes: () => (chunks === null ? 0 : receivedBytes),
    };
  }

  return Object.freeze({
    openValidatedStream,
    get activeStreamCount() {
      return activeGuards.size;
    },
  });
}
