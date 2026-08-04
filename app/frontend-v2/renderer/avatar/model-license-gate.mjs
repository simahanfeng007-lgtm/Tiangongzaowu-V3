// §10 许可与合规门：LicenseRecord 提取/登记、redistributionPermission 判定、
// 自定义导入展示摘要、releaseGate 规则。
// 纯数据与判定函数；rawMeta 一律保留 VRM 原始字段（含 commercialUssageName 原拼写）。

import { deepFreeze } from "./canonical-hash.mjs";

export const LICENSE_RECORD_SCHEMA_VERSION = 1;

export const RedistributionPermission = Object.freeze({
  VERIFIED: "verified",
  UNVERIFIED: "unverified",
});

export class LicenseGateError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "LicenseGateError";
    this.code = code;
  }
}

// VRM 0.x commercialUssageName → 统一 commercialUsage；未映射值不落为 allow（fail-closed）。
const VRM0_COMMERCIAL_MAP = Object.freeze({ Allow: "allow", Disallow: "disallow" });
// VRM 1.0 meta.commercialUsage → 同一 LicenseRecord（§10.1 VRM1Adapter 投影）。
const VRM1_COMMERCIAL_MAP = Object.freeze({
  personalNonProfit: "nonprofit",
  personalProfit: "allow",
  corporation: "allow",
});

function strOrNull(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

// 从 VRM meta 提取 LicenseRecord（登记前的规范化投影）。meta 缺失/非对象即抛错，
// 由 ModelAdmissionGate 转成 license-metadata 违规（§9.1.12 许可元数据必须可读取）。
export function extractLicenseRecord({ vrmSpecVersion, meta }) {
  if (vrmSpecVersion !== "0.x" && vrmSpecVersion !== "1.0") {
    throw new LicenseGateError("vrm_spec_unknown", `未知 VRM 版本标识: ${vrmSpecVersion}`);
  }
  if (meta === null || typeof meta !== "object" || Array.isArray(meta)) {
    throw new LicenseGateError("license_meta_missing", "VRM meta 缺失或不是对象");
  }
  // rawMeta 深拷贝保留原值，统一字段只做投影，禁止覆盖原值（§10.1）。
  const rawMeta = deepFreeze(structuredClone(meta));
  if (vrmSpecVersion === "0.x") {
    return normalizeLicenseRecord({
      vrmSpecVersion,
      title: strOrNull(meta.title),
      author: strOrNull(meta.author),
      sourceUrl: strOrNull(meta.otherPermissionUrl) ?? strOrNull(meta.contactInformation) ?? strOrNull(meta.reference),
      licenseName: strOrNull(meta.licenseName),
      allowedUser: strOrNull(meta.allowedUserName),
      // 显式映射 commercialUssageName（保留原拼写于 rawMeta）。
      commercialUsage: VRM0_COMMERCIAL_MAP[meta.commercialUssageName] ?? "unknown",
      attributionRequirement: strOrNull(meta.otherLicenseUrl),
      rawMeta,
    });
  }
  // VRM 1.0：authors/creditNotation/licenseUrl 等字段映射到同一 LicenseRecord。
  const authors = Array.isArray(meta.authors) ? meta.authors.filter((a) => typeof a === "string") : [];
  return normalizeLicenseRecord({
    vrmSpecVersion,
    title: strOrNull(meta.name),
    author: authors.length > 0 ? authors.join(", ") : null,
    sourceUrl: strOrNull(meta.licenseUrl) ?? strOrNull(meta.contactInformation),
    licenseName: strOrNull(meta.licenseUrl),
    allowedUser: strOrNull(meta.avatarPermission),
    commercialUsage: VRM1_COMMERCIAL_MAP[meta.commercialUsage] ?? "unknown",
    attributionRequirement: strOrNull(meta.creditNotation),
    rawMeta,
  });
}

// 登记校验：schemaVersion 安全失败、字段类型归一、verified 必须有证据三件套。
export function normalizeLicenseRecord(raw) {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new LicenseGateError("license_record_invalid", "LicenseRecord 必须是对象");
  }
  const schemaVersion = raw.schemaVersion ?? LICENSE_RECORD_SCHEMA_VERSION;
  if (!Number.isInteger(schemaVersion) || schemaVersion < 1) {
    throw new LicenseGateError("license_schema_invalid", "LicenseRecord schemaVersion 非法");
  }
  if (schemaVersion > LICENSE_RECORD_SCHEMA_VERSION) {
    throw new LicenseGateError(
      "license_schema_unsupported",
      `LicenseRecord schemaVersion=${schemaVersion} 高于已知 ${LICENSE_RECORD_SCHEMA_VERSION}，安全失败`,
    );
  }
  const redistributionPermission =
    raw.redistributionPermission === RedistributionPermission.VERIFIED
      ? RedistributionPermission.VERIFIED
      : RedistributionPermission.UNVERIFIED;
  const verifiedAt = Number.isInteger(raw.verifiedAt) && raw.verifiedAt >= 0 ? raw.verifiedAt : null;
  const verifiedBy = strOrNull(raw.verifiedBy);
  const evidencePath = strOrNull(raw.evidencePath);
  // verified 必须绑定核验证据，否则视为伪造登记直接拒绝（不静默降级）。
  if (
    redistributionPermission === RedistributionPermission.VERIFIED &&
    (verifiedAt === null || verifiedBy === null || evidencePath === null)
  ) {
    throw new LicenseGateError(
      "verification_evidence_missing",
      "redistributionPermission=verified 需要 verifiedAt/verifiedBy/evidencePath",
    );
  }
  return deepFreeze({
    schemaVersion: LICENSE_RECORD_SCHEMA_VERSION,
    modelId: strOrNull(raw.modelId),
    vrmSpecVersion: raw.vrmSpecVersion === "1.0" ? "1.0" : "0.x",
    title: strOrNull(raw.title),
    author: strOrNull(raw.author),
    sourceUrl: strOrNull(raw.sourceUrl),
    purchaseEvidence: strOrNull(raw.purchaseEvidence),
    licenseName: strOrNull(raw.licenseName),
    allowedUser: strOrNull(raw.allowedUser),
    commercialUsage: strOrNull(raw.commercialUsage) ?? "unknown",
    redistributionPermission,
    attributionRequirement: strOrNull(raw.attributionRequirement),
    evidencePath,
    verifiedAt,
    verifiedBy,
    rawMeta: raw.rawMeta !== undefined ? deepFreeze(structuredClone(raw.rawMeta)) : null,
  });
}

// §10.2 判定：只有登记证据齐全的 verified 才算 verified，其余一律 unverified。
export function assessRedistributionPermission(licenseRecord) {
  if (licenseRecord === null || typeof licenseRecord !== "object") {
    return RedistributionPermission.UNVERIFIED;
  }
  return licenseRecord.redistributionPermission === RedistributionPermission.VERIFIED
    ? RedistributionPermission.VERIFIED
    : RedistributionPermission.UNVERIFIED;
}

// §10.3 自定义导入展示摘要：只含 UI 必需字段，不替用户推断版权。
export function customImportDisplaySummary(licenseRecord) {
  const permission = assessRedistributionPermission(licenseRecord);
  const redistributionProhibited = licenseRecord?.licenseName === "Redistribution_Prohibited";
  const warnings = [];
  if (redistributionProhibited) warnings.push("redistribution_prohibited_declared");
  if (permission === RedistributionPermission.UNVERIFIED) warnings.push("redistribution_unverified");
  if (!licenseRecord?.licenseName) warnings.push("license_name_missing");
  return deepFreeze({
    title: licenseRecord?.title ?? null,
    author: licenseRecord?.author ?? null,
    licenseName: licenseRecord?.licenseName ?? null,
    commercialUsage: licenseRecord?.commercialUsage ?? "unknown",
    allowedUser: licenseRecord?.allowedUser ?? null,
    redistributionPermission: permission,
    attributionRequirement: licenseRecord?.attributionRequirement ?? null,
    sourceUrl: licenseRecord?.sourceUrl ?? null,
    redistributionProhibited,
    warnings: Object.freeze(warnings),
  });
}

// §10.2 发布规则：redistributionPermission != verified → releaseGate = fail。
export function evaluateReleaseGate(licenseRecord) {
  const permission = assessRedistributionPermission(licenseRecord);
  if (permission !== RedistributionPermission.VERIFIED) {
    return deepFreeze({ pass: false, reason: "redistribution_not_verified", redistributionPermission: permission });
  }
  return deepFreeze({ pass: true, reason: null, redistributionPermission: permission });
}
