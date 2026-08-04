// §8.2 ValidatedAssetToken：签发与验证。
// 只对 admissionState=admitted 且已提交的记录签发；验证逐项比对（含当前
// registryEntryVersion），singleUse 消费后失效，issuerEpoch 不一致拒绝。
// Token 只在签发它的前端运行实例内有效，不携带任何绝对路径。

import { validateAssetTokenForUse, validateAssetTokenShape } from "./contracts.mjs";
import { deepFreeze } from "./canonical-hash.mjs";
import { AdmissionState } from "./asset-registry.mjs";

export class AssetTokenError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AssetTokenError";
    this.code = code;
  }
}

let nonceCounter = 0;
function defaultNonceGenerator() {
  nonceCounter += 1;
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `non_${random}_${nonceCounter}`;
}

// 签发侧：registry 只暴露已提交记录——登记未提交（orphan）时 getRecord 为 null，拒绝签发。
export function createTokenIssuer({ registry, issuerEpoch = 0, nonceGenerator = defaultNonceGenerator }) {
  if (registry === null || typeof registry.getRecord !== "function") {
    throw new AssetTokenError("registry_invalid", "TokenIssuer 需要 AssetRegistry");
  }
  if (!Number.isInteger(issuerEpoch) || issuerEpoch < 0) {
    throw new AssetTokenError("issuer_epoch_invalid", "issuerEpoch 必须是非负整数");
  }
  return deepFreeze({
    issueToken(assetId, { singleUse = true } = {}) {
      const record = registry.getRecord(assetId);
      if (record === null) {
        // §8.5.5：原子移动成功但 registry 提交失败的 orphan 不得签发 Token。
        throw new AssetTokenError("asset_not_found", `assetId=${assetId} 未登记或提交失败（orphan），拒绝签发 Token`);
      }
      if (record.admissionState !== AdmissionState.ADMITTED) {
        throw new AssetTokenError(
          "asset_not_admitted",
          `assetId=${assetId} admissionState=${record.admissionState}，只对 admitted 签发 Token`,
        );
      }
      if (typeof singleUse !== "boolean") {
        throw new AssetTokenError("single_use_invalid", "singleUse 必须是布尔值");
      }
      return deepFreeze({
        assetId: record.assetId,
        contentHash: record.contentHash,
        byteLength: record.byteLength,
        validationReceiptId: record.validationReceiptId,
        registryEntryVersion: record.registryEntryVersion,
        issuerEpoch,
        nonce: nonceGenerator(),
        singleUse,
      });
    },
  });
}

// 验证侧：消费即失效（singleUse）；版本不一致/epoch 不一致/非 admitted 均拒绝。
export function createTokenValidator({ registry, issuerEpoch = 0 }) {
  if (registry === null || typeof registry.getRecord !== "function") {
    throw new AssetTokenError("registry_invalid", "TokenValidator 需要 AssetRegistry");
  }
  if (!Number.isInteger(issuerEpoch) || issuerEpoch < 0) {
    throw new AssetTokenError("issuer_epoch_invalid", "issuerEpoch 必须是非负整数");
  }
  const consumedNonces = new Set();
  return {
    // 返回 { ok, errors }；ok=true 时 singleUse Token 已被消费。
    validateAndConsume(token) {
      const shapeErrors = validateAssetTokenShape(token);
      if (shapeErrors.length > 0) return { ok: false, errors: shapeErrors };
      if (token.issuerEpoch !== issuerEpoch) {
        return { ok: false, errors: ["issuer_epoch_mismatch"] };
      }
      const record = registry.getRecord(token.assetId);
      if (record === null) {
        return { ok: false, errors: ["registry_record_missing"] };
      }
      if (record.admissionState !== AdmissionState.ADMITTED) {
        return { ok: false, errors: ["asset_not_admitted"] };
      }
      const use = validateAssetTokenForUse(token, { ...record, issuerEpoch });
      if (!use.ok) return use;
      if (token.singleUse) {
        if (consumedNonces.has(token.nonce)) {
          return { ok: false, errors: ["single_use_consumed"] };
        }
        consumedNonces.add(token.nonce);
      }
      return { ok: true, errors: [] };
    },
    // 已消费 nonce 数（诊断用）。
    get consumedCount() {
      return consumedNonces.size;
    },
  };
}
