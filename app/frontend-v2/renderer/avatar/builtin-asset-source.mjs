// P6b 启动组装：AvatarRuntime 的内置模型 assetSource（方案 §8.3/§8.4/§8.6）。
//
// describeModel(modelId)：内置清单（app/assets/avatar/builtin-models.json）的逻辑 id 投影，
//   未知 id 明确失败（model_unknown），绝不静默回退。
// openModelBytes({ modelId, contentHash, byteLength, attemptId })：
//   1. 入参与内置清单逐项复核（descriptor_manifest_mismatch 即失败，纵深防御）；
//   2. 凭 AssetRegistry 的 admitted 记录签发 ValidatedAssetToken（§8.5）；
//   3. 经 AssetProvider.openValidatedStream + channelFactory 注入的 MessagePort 通道拉取字节：
//      ready 复述校验 → seq 有序分块 → final 全长复核 → 重组 SHA-256 复核（§8.6 字节同一性，
//      任一不一致即中止并通知宿主停读停发）；
//   4. 交回前在本地再做一次 byteLength+contentHash 复核（双保险），不一致即失败。
//
// 纪律：渲染端永不见绝对路径（descriptor 只含 scope/locator/hash）；hash 复核失败
// 绝不把字节交给引擎（fail-closed）。

import { assetHandleForBuiltin, assetHandleForModel } from "./asset-provider.mjs";
import { sha256HexSync } from "./canonical-hash.mjs";
import { AdmissionState, AssetScope } from "./asset-registry.mjs";

export const BUILTIN_ASSET_SOURCE_VERSION = "builtin-asset-source-1.1.0";

export class BuiltinAssetSourceError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BuiltinAssetSourceError";
    this.code = code;
  }
}

const HEX64 = /^[0-9a-f]{64}$/;

// 清单形态归一：数组（规范，builtin-models.json）或对象映射（防御性兼容）。
export function normalizeBuiltinManifest(doc) {
  const raw = doc?.models;
  const list = Array.isArray(raw)
    ? raw
    : raw !== null && typeof raw === "object"
      ? Object.entries(raw).map(([id, entry]) => ({ id, ...entry }))
      : [];
  const models = [];
  for (const item of list) {
    if (item === null || typeof item !== "object") continue;
    if (typeof item.id !== "string" || item.id.length === 0) continue;
    if (!HEX64.test(item.contentHash ?? "")) continue;
    if (!Number.isInteger(item.byteLength) || item.byteLength <= 0) continue;
    models.push(Object.freeze({
      id: item.id,
      displayName: typeof item.displayName === "string" ? item.displayName : item.id,
      relativePath: typeof item.relativePath === "string" ? item.relativePath : null,
      contentHash: item.contentHash,
      byteLength: item.byteLength,
      vrmSpecVersion: item.vrmSpecVersion === "1.0" ? "1.0" : "0.x",
    }));
  }
  return Object.freeze(models);
}

export function createBuiltinAssetSource({
  manifest,
  provider,
  tokenIssuer,
  registry = null,
  sha256 = sha256HexSync,
} = {}) {
  if (provider === null || typeof provider !== "object" || typeof provider.openValidatedStream !== "function") {
    throw new BuiltinAssetSourceError("provider_invalid", "BuiltinAssetSource 需要 AssetProvider（openValidatedStream）");
  }
  if (tokenIssuer === null || typeof tokenIssuer !== "object" || typeof tokenIssuer.issueToken !== "function") {
    throw new BuiltinAssetSourceError("token_issuer_invalid", "BuiltinAssetSource 需要 TokenIssuer（admitted 记录签发）");
  }
  if (typeof sha256 !== "function") {
    throw new BuiltinAssetSourceError("sha256_invalid", "BuiltinAssetSource 需要 sha256 函数");
  }
  if (
    registry !== null &&
    (typeof registry !== "object" ||
      typeof registry.getRecord !== "function" ||
      typeof registry.listRecords !== "function")
  ) {
    throw new BuiltinAssetSourceError("registry_invalid", "registry 必须提供 getRecord/listRecords");
  }
  const entries = new Map(normalizeBuiltinManifest(manifest).map((model) => [model.id, model]));

  function customEntryFor(modelId) {
    if (registry === null) return null;
    const record = registry.getRecord(modelId);
    if (
      record === null ||
      record.scope !== AssetScope.MODEL ||
      record.admissionState !== AdmissionState.ADMITTED
    ) {
      return null;
    }
    return Object.freeze({
      id: record.assetId,
      displayName: record.displayName || record.assetId,
      relativePath: null,
      contentHash: record.contentHash,
      byteLength: record.byteLength,
      vrmSpecVersion: record.licenseRecord?.vrmSpecVersion === "1.0" ? "1.0" : "0.x",
      scope: AssetScope.MODEL,
    });
  }

  function listCustomEntries() {
    if (registry === null) return [];
    return registry
      .listRecords()
      .filter((record) =>
        record.scope === AssetScope.MODEL &&
        record.admissionState === AdmissionState.ADMITTED &&
        !entries.has(record.assetId))
      .map((record) => customEntryFor(record.assetId))
      .filter(Boolean);
  }

  function requireEntry(modelId) {
    const builtin = entries.get(modelId);
    if (builtin !== undefined) {
      return Object.freeze({ scope: AssetScope.BUILTIN, entry: builtin });
    }
    const custom = customEntryFor(modelId);
    if (custom !== null) {
      return Object.freeze({ scope: AssetScope.MODEL, entry: custom });
    }
    throw new BuiltinAssetSourceError(
      "model_unknown",
      `不存在 admitted 模型 modelId=${String(modelId)}，明确失败不回退`,
    );
  }

  return Object.freeze({
    version: BUILTIN_ASSET_SOURCE_VERSION,

    listModels() {
      return Object.freeze([
        ...[...entries.values()].map((entry) => Object.freeze({ ...entry, scope: AssetScope.BUILTIN })),
        ...listCustomEntries(),
      ]);
    },

    // AvatarRuntime startLoad 的 descriptor 来源（§7.1：modelId/contentHash/byteLength 必备）。
    async describeModel(modelId) {
      const { scope, entry } = requireEntry(modelId);
      return Object.freeze({
        modelId: entry.id,
        contentHash: entry.contentHash,
        byteLength: entry.byteLength,
        displayName: entry.displayName,
        vrmSpecVersion: entry.vrmSpecVersion,
        scope,
      });
    },

    // 受控字节通道：MessagePort 分块流 + 全链复核（§8.4/§8.6）。
    async openModelBytes({ modelId, contentHash, byteLength, attemptId } = {}) {
      const { scope, entry } = requireEntry(modelId);
      // 入参（runtime 经 describeModel 获得）与清单逐项复核：任一不一致即失败。
      if (contentHash !== undefined && contentHash !== entry.contentHash) {
        throw new BuiltinAssetSourceError(
          "descriptor_manifest_mismatch",
          `modelId=${modelId} 入参 contentHash 与内置清单不一致`,
        );
      }
      if (byteLength !== undefined && byteLength !== entry.byteLength) {
        throw new BuiltinAssetSourceError(
          "descriptor_manifest_mismatch",
          `modelId=${modelId} 入参 byteLength=${byteLength} 与清单 ${entry.byteLength} 不一致`,
        );
      }
      // §8.5：凭 admitted registry 记录签发 Token（未登记/非 admitted 由 issuer 拒绝）。
      const token = tokenIssuer.issueToken(modelId);
      const handle =
        scope === AssetScope.MODEL
          ? assetHandleForModel(token)
          : assetHandleForBuiltin(token, modelId);
      const stream = provider.openValidatedStream(handle);
      const bytes = await stream.done; // ready/chunk/final 全链复核（含 hash），任一不一致即中止
      const view = bytes instanceof ArrayBuffer ? new Uint8Array(bytes) : new Uint8Array(bytes?.buffer ?? bytes);
      // 本地双保险复核：交回前 byteLength + contentHash 必须与清单一致。
      if (view.byteLength !== entry.byteLength) {
        throw new BuiltinAssetSourceError(
          "byte_identity_mismatch",
          `字节长度 ${view.byteLength} 与清单 ${entry.byteLength} 不一致（attemptId=${attemptId ?? "n/a"}）`,
        );
      }
      if (sha256(view) !== entry.contentHash) {
        throw new BuiltinAssetSourceError(
          "byte_identity_mismatch",
          `重组字节 SHA-256 与清单 contentHash 不一致（attemptId=${attemptId ?? "n/a"}）`,
        );
      }
      return bytes instanceof ArrayBuffer ? bytes : view.buffer;
    },
  });
}
