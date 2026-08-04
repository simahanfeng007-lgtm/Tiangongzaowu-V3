// §8.2/§22.3 AssetRegistry：资产记录、原子提交、registryEntryVersion 白名单语义、
// authorizationFingerprint 规范化哈希。
// 版本比较与记录提交在同一串行事务内完成；提交失败则内存状态不变，调用方不得签发 Token（orphan 规则）。

import { canonicalSha256, deepFreeze } from "./canonical-hash.mjs";
import {
  AVATAR_STORAGE_LAYOUT,
  assertSchemaVersionSupported,
  readJsonFile,
  writeJsonAtomic,
} from "./storage-adapter.mjs";

export const REGISTRY_SCHEMA_VERSION = 1;
export const AUTHORIZATION_FINGERPRINT_SCHEMA_VERSION = 1;

export const AssetScope = Object.freeze({
  BUILTIN: "builtin",
  MODEL: "model",
  CANDIDATE: "candidate",
  QUARANTINE: "quarantine",
});
export const ASSET_SCOPES = Object.freeze(Object.values(AssetScope));

// 安全准入状态（§8.2：与 RuntimeState/LoadAttemptState 严格分离）。
export const AdmissionState = Object.freeze({
  ADMITTED: "admitted",
  QUARANTINED: "quarantined",
  REVOKED: "revoked",
  DELETED: "deleted",
});
export const ADMISSION_STATES = Object.freeze(Object.values(AdmissionState));

// §8.2 白名单：只有这些字段变化才递增 registryEntryVersion。
export const VERSIONED_FIELDS = Object.freeze([
  "contentHash",
  "byteLength",
  "validationReceiptId",
  "validatorVersion",
  "authorizationFingerprint",
]);

// §8.2 白名单：只有这些准入状态迁移才递增 registryEntryVersion。
export const VERSIONED_ADMISSION_TRANSITIONS = Object.freeze([
  "admitted->quarantined",
  "admitted->revoked",
  "admitted->deleted",
  "quarantined->admitted",
  "quarantined->revoked",
  "quarantined->deleted",
  "revoked->admitted",
  "revoked->deleted",
]);

// 非版本化字段白名单（displayName/UI/统计/遥测等，变化一律不递增版本）。
const NON_VERSIONED_FIELDS = Object.freeze([
  "displayName",
  "title",
  "sortOrder",
  "isFavorite",
  "lastUsedAt",
  "accessCount",
  "diagnosticNote",
  "telemetry",
]);

const HEX64 = /^[0-9a-f]{64}$/;

export class AssetRegistryError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AssetRegistryError";
    this.code = code;
  }
}

// §8.2 authorizationFingerprint：带 schemaVersion 的规范化哈希，
// 只覆盖实际参与加载授权判定的许可、安全策略与验证输入。
export function computeAuthorizationFingerprint({
  licenseRecord = null,
  admissionLimits,
  uriPolicy = null,
  validatorVersion,
  contentHash,
  byteLength,
}) {
  if (!HEX64.test(contentHash ?? "")) {
    throw new AssetRegistryError("content_hash_invalid", "authorizationFingerprint 需要 64 位小写 hex contentHash");
  }
  if (!Number.isInteger(byteLength) || byteLength <= 0) {
    throw new AssetRegistryError("byte_length_invalid", "authorizationFingerprint 需要正整数 byteLength");
  }
  const licenseProjection = licenseRecord === null
    ? null
    : {
        licenseName: licenseRecord.licenseName ?? null,
        commercialUsage: licenseRecord.commercialUsage ?? null,
        allowedUser: licenseRecord.allowedUser ?? null,
        redistributionPermission: licenseRecord.redistributionPermission ?? null,
        attributionRequirement: licenseRecord.attributionRequirement ?? null,
      };
  return `afp_${canonicalSha256({
    schemaVersion: AUTHORIZATION_FINGERPRINT_SCHEMA_VERSION,
    license: licenseProjection,
    admissionLimits: admissionLimits ?? null,
    uriPolicy: uriPolicy ?? null,
    validatorVersion: validatorVersion ?? null,
    contentHash,
    byteLength,
  })}`;
}

function assertRecordIdentityFields(input) {
  if (typeof input.assetId !== "string" || input.assetId.length === 0) {
    throw new AssetRegistryError("asset_id_invalid", "assetId 必须是非空字符串");
  }
  if (!ASSET_SCOPES.includes(input.scope)) {
    throw new AssetRegistryError("scope_invalid", `scope 必须是 ${ASSET_SCOPES.join("|")}`);
  }
  if (!HEX64.test(input.contentHash ?? "")) {
    throw new AssetRegistryError("content_hash_invalid", "contentHash 必须是 64 位小写 hex");
  }
  if (!Number.isInteger(input.byteLength) || input.byteLength <= 0) {
    throw new AssetRegistryError("byte_length_invalid", "byteLength 必须是正整数");
  }
  if (typeof input.validationReceiptId !== "string" || input.validationReceiptId.length === 0) {
    throw new AssetRegistryError("validation_receipt_id_invalid", "validationReceiptId 必须非空");
  }
  if (typeof input.validatorVersion !== "string" || input.validatorVersion.length === 0) {
    throw new AssetRegistryError("validator_version_invalid", "validatorVersion 必须非空");
  }
  if (typeof input.authorizationFingerprint !== "string" || input.authorizationFingerprint.length === 0) {
    throw new AssetRegistryError("authorization_fingerprint_invalid", "authorizationFingerprint 必须非空");
  }
  if (!ADMISSION_STATES.includes(input.admissionState)) {
    throw new AssetRegistryError("admission_state_invalid", `admissionState 必须是 ${ADMISSION_STATES.join("|")}`);
  }
}

export async function createAssetRegistry({
  storage,
  registryPath = AVATAR_STORAGE_LAYOUT.registryFile,
  issuerEpoch = 0,
  nowWallClock = () => Date.now(),
}) {
  if (storage === null || typeof storage !== "object") {
    throw new AssetRegistryError("storage_invalid", "AssetRegistry 需要注入 storage backend");
  }
  if (!Number.isInteger(issuerEpoch) || issuerEpoch < 0) {
    throw new AssetRegistryError("issuer_epoch_invalid", "issuerEpoch 必须是非负整数");
  }
  const existing = await readJsonFile(storage, registryPath);
  assertSchemaVersionSupported(existing, REGISTRY_SCHEMA_VERSION, "AssetRegistry");
  let doc = existing ?? { schemaVersion: REGISTRY_SCHEMA_VERSION, revision: 0, records: {} };
  if (doc.records === null || typeof doc.records !== "object") {
    throw new AssetRegistryError("registry_corrupted", "registry records 字段损坏");
  }

  // 提交串行化：版本比较→新记录→原子替换在同一事务内，禁止并发交错（§8.2 末尾）。
  let queue = Promise.resolve();
  const enqueue = (fn) => {
    const run = queue.then(fn);
    queue = run.catch(() => {});
    return run;
  };

  // 原子提交：先写存储，成功后才切换内存 doc；失败保持旧状态。
  async function commit(nextDoc, cause) {
    try {
      await writeJsonAtomic(storage, registryPath, nextDoc);
    } catch (error) {
      throw new AssetRegistryError(
        "registry_commit_failed",
        `registry 原子提交失败（${cause}）：${error.message}；调用方不得签发 Token`,
      );
    }
    doc = nextDoc;
  }

  function snapshotWith(records) {
    return { schemaVersion: REGISTRY_SCHEMA_VERSION, revision: doc.revision + 1, records };
  }

  const registry = {
    get issuerEpoch() {
      return issuerEpoch;
    },
    get revision() {
      return doc.revision;
    },

    // 只读视图：记录均已提交（提交失败的写入不会进入 doc）。
    getRecord(assetId) {
      return doc.records[assetId] ?? null;
    },
    listRecords() {
      return Object.freeze(Object.values(doc.records));
    },
    findByContentHash(contentHash) {
      return Object.freeze(Object.values(doc.records).filter((record) => record.contentHash === contentHash));
    },

    // 首次原子登记：registryEntryVersion=1，提交成功后返回（§22.3 登记顺序）。
    registerAsset(input) {
      return enqueue(async () => {
        const record = {
          assetId: input.assetId,
          scope: input.scope,
          contentHash: input.contentHash,
          byteLength: input.byteLength,
          validationReceiptId: input.validationReceiptId,
          validatorVersion: input.validatorVersion,
          authorizationFingerprint: input.authorizationFingerprint,
          admissionState: input.admissionState ?? AdmissionState.ADMITTED,
        };
        assertRecordIdentityFields(record);
        // tombstone 也占用 assetId：重新导入必须建立全新资产记录（§8.2）。
        if (doc.records[record.assetId] !== undefined) {
          throw new AssetRegistryError("asset_id_exists", `assetId=${record.assetId} 已存在（含 tombstone），拒绝重复登记`);
        }
        const now = nowWallClock();
        const frozen = deepFreeze({
          ...record,
          registryEntryVersion: 1,
          displayName: input.displayName ?? null,
          title: input.title ?? null,
          sortOrder: null,
          isFavorite: false,
          lastUsedAt: null,
          accessCount: 0,
          diagnosticNote: null,
          telemetry: null,
          licenseRecord: input.licenseRecord ?? null,
          auditTrail: [{ at: now, action: "register", versioned: true, reason: input.reason ?? null }],
          createdAtWallClock: now,
          updatedAtWallClock: now,
        });
        await commit(snapshotWith({ ...doc.records, [record.assetId]: frozen }), `register ${record.assetId}`);
        return frozen;
      });
    },

    // 字段更新：VERSIONED_FIELDS 变化恰好 +1；非白名单字段一律不递增但写审计日志。
    // 同一事务多字段变化只 +1（§8.2）。
    updateAssetFields(assetId, patch, { reason = null } = {}) {
      return enqueue(async () => {
        const current = doc.records[assetId];
        if (current === undefined) {
          throw new AssetRegistryError("asset_not_found", `assetId=${assetId} 不存在`);
        }
        if (current.admissionState === AdmissionState.DELETED) {
          throw new AssetRegistryError("deleted_tombstone", "deleted 是 tombstone 终态，禁止字段更新");
        }
        if (patch === null || typeof patch !== "object") {
          throw new AssetRegistryError("patch_invalid", "patch 必须是对象");
        }
        const allowed = new Set([...VERSIONED_FIELDS, ...NON_VERSIONED_FIELDS]);
        for (const key of Object.keys(patch)) {
          if (!allowed.has(key)) {
            throw new AssetRegistryError("field_not_allowed", `字段 ${key} 不在可更新白名单内`);
          }
        }
        // 版本化字段的形状校验放在事务内、递增之前。
        const merged = { ...current, ...patch };
        assertRecordIdentityFields({
          assetId: current.assetId,
          scope: current.scope,
          contentHash: merged.contentHash,
          byteLength: merged.byteLength,
          validationReceiptId: merged.validationReceiptId,
          validatorVersion: merged.validatorVersion,
          authorizationFingerprint: merged.authorizationFingerprint,
          admissionState: current.admissionState,
        });
        const versionedChanged = VERSIONED_FIELDS.some((field) => field in patch && patch[field] !== current[field]);
        const now = nowWallClock();
        const next = deepFreeze({
          ...merged,
          registryEntryVersion: current.registryEntryVersion + (versionedChanged ? 1 : 0),
          auditTrail: [
            ...current.auditTrail,
            { at: now, action: "update", fields: Object.keys(patch), versioned: versionedChanged, reason },
          ],
          updatedAtWallClock: now,
        });
        await commit(snapshotWith({ ...doc.records, [assetId]: next }), `update ${assetId}`);
        return next;
      });
    },

    // 准入状态迁移：仅白名单迁移，一次事务至多 +1；deleted 是终态；
    // quarantined/revoked→admitted 必须重跑验证与许可门（凭新 validationReceiptId 证明）。
    transitionAdmissionState(assetId, toState, { reason = null, revalidationReceiptId = null } = {}) {
      return enqueue(async () => {
        const current = doc.records[assetId];
        if (current === undefined) {
          throw new AssetRegistryError("asset_not_found", `assetId=${assetId} 不存在`);
        }
        const from = current.admissionState;
        if (from === AdmissionState.DELETED) {
          throw new AssetRegistryError("deleted_tombstone", "deleted 是 tombstone 终态，禁止迁出（重新导入须建新记录）");
        }
        const transitionKey = `${from}->${toState}`;
        if (!VERSIONED_ADMISSION_TRANSITIONS.includes(transitionKey)) {
          throw new AssetRegistryError("admission_transition_illegal", `非白名单准入迁移: ${transitionKey}`);
        }
        const updates = { admissionState: toState };
        if (toState === AdmissionState.ADMITTED) {
          if (
            typeof revalidationReceiptId !== "string" ||
            revalidationReceiptId.length === 0 ||
            revalidationReceiptId === current.validationReceiptId
          ) {
            throw new AssetRegistryError(
              "readmission_requires_revalidation",
              "回迁至 admitted 必须完整重跑验证和许可门，并提供新的 validationReceiptId",
            );
          }
          updates.validationReceiptId = revalidationReceiptId;
        }
        const now = nowWallClock();
        const next = deepFreeze({
          ...current,
          ...updates,
          registryEntryVersion: current.registryEntryVersion + 1,
          auditTrail: [
            ...current.auditTrail,
            { at: now, action: "admission-transition", transition: transitionKey, versioned: true, reason },
          ],
          updatedAtWallClock: now,
        });
        await commit(snapshotWith({ ...doc.records, [assetId]: next }), `transition ${assetId} ${transitionKey}`);
        return next;
      });
    },
  };
  return registry;
}

