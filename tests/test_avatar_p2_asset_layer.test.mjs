// P2a 安全资源层测试：validator（真实内置模型 + 合成 GLB 负例）、许可门、
// AssetRegistry 版本语义、ValidatedAssetToken、Quarantine、PendingLoadJournal、存储原子性。

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import {
  AdmissionState,
  AssetScope,
  DEFAULT_ADMISSION_LIMITS,
  DEFAULT_QUARANTINE_POLICY,
  PENDING_LOAD_JOURNAL_SCHEMA_VERSION,
  QUARANTINE_POLICY_SCHEMA_VERSION,
  QuarantineCategory,
  REGISTRY_SCHEMA_VERSION,
  VALIDATOR_VERSION,
  admitVrmModel,
  assessRedistributionPermission,
  computeAuthorizationFingerprint,
  createAssetRegistry,
  createMemoryStorageBackend,
  createPendingLoadJournal,
  createQuarantineTracker,
  createTokenIssuer,
  createTokenValidator,
  customImportDisplaySummary,
  engineQuarantineKey,
  evaluateReleaseGate,
  normalizeGpuFingerprint,
  quarantineKeyForFailure,
  runtimeQuarantineKey,
  sha256HexSync,
  structuralQuarantineKey,
  validateAdmissionLimits,
  writeJsonAtomic,
} from "../app/frontend-v2/renderer/avatar/index.mjs";

const nodeSha256 = (bytes) =>
  createHash("sha256").update(Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength)).digest("hex");

const FIXTURE_Z1_URL = new URL("../app/assets/avatars/imported/天工造物z1.vrm", import.meta.url);
const FIXTURE_V2_URL = new URL("../app/assets/avatars/imported/造物v2.vrm", import.meta.url);
const HAS_RESTRICTED_FIXTURES = existsSync(FIXTURE_Z1_URL) && existsSync(FIXTURE_V2_URL);

function syntheticRestrictedVrm(title) {
  return buildGlb({
    chunks: [jsonChunk(minimalVrmJson({
      meta: {
        title,
        author: "test-suite",
        licenseName: "Redistribution_Prohibited",
        commercialUssageName: "Allow",
      },
    }))],
  });
}

// 发布源码必须排除两份无再分发权的模型。通用安全层测试使用合成容器，
// 只有验证真实素材结构的用例在专有 fixture 可用时执行。
const FIXTURE_Z1 = HAS_RESTRICTED_FIXTURES
  ? new Uint8Array(readFileSync(FIXTURE_Z1_URL))
  : syntheticRestrictedVrm("synthetic-z1");
const FIXTURE_V2 = HAS_RESTRICTED_FIXTURES
  ? new Uint8Array(readFileSync(FIXTURE_V2_URL))
  : syntheticRestrictedVrm("synthetic-v2");

// ── 合成 GLB 构造器 ─────────────────────────────────────────

function ascii4(text) {
  return (text.charCodeAt(0) | (text.charCodeAt(1) << 8) | (text.charCodeAt(2) << 16) | (text.charCodeAt(3) << 24)) >>> 0;
}

function pad4(length) {
  return (4 - (length % 4)) % 4;
}

// chunks: [{ type: "JSON"|"BIN"|string, data: Uint8Array, declaredLengthOverride? }]
function buildGlb({ chunks, declaredDelta = 0, magic = "glTF", version = 2 }) {
  let total = 12;
  for (const chunk of chunks) total += 8 + chunk.data.length + pad4(chunk.data.length);
  const declared = total + declaredDelta;
  const out = new Uint8Array(Math.max(total, declared));
  const dv = new DataView(out.buffer);
  dv.setUint32(0, ascii4(magic), true);
  dv.setUint32(4, version, true);
  dv.setUint32(8, declared, true);
  let offset = 12;
  for (const chunk of chunks) {
    const padded = chunk.data.length + pad4(chunk.data.length);
    dv.setUint32(offset, chunk.declaredLengthOverride ?? padded, true);
    dv.setUint32(offset + 4, ascii4(chunk.type), true);
    out.set(chunk.data, offset + 8);
    // glTF 规范：JSON chunk 用空格（0x20）补齐，BIN 用 0x00。
    if (chunk.type === "JSON") {
      for (let i = chunk.data.length; i < padded; i++) out[offset + 8 + i] = 0x20;
    }
    offset += 8 + padded;
  }
  return out.subarray(0, total);
}

function jsonChunk(jsonValue) {
  const text = typeof jsonValue === "string" ? jsonValue : JSON.stringify(jsonValue);
  return { type: "JSON", data: new TextEncoder().encode(text) };
}

function binChunk(data) {
  return { type: "BIN\0", data };
}

function minimalVrmJson(overrides = {}) {
  return {
    asset: { version: "2.0" },
    extensionsUsed: ["VRM"],
    extensions: {
      VRM: {
        version: "0.0",
        meta: {
          title: "合成模型",
          author: "测试",
          licenseName: "CC0",
          allowedUserName: "Everyone",
          commercialUssageName: "Allow",
          ...(overrides.meta ?? {}),
        },
      },
    },
    buffers: [],
    ...("json" in overrides ? overrides.json : {}),
  };
}

function admitSynthetic(jsonValue, binData = null, options = {}) {
  const chunks = [jsonChunk(jsonValue)];
  if (binData !== null) chunks.push(binChunk(binData));
  const glb = buildGlb({ chunks, ...("glb" in options ? options.glb : {}) });
  const { glb: _ignored, ...rest } = options;
  return admitVrmModel(glb, { sha256: nodeSha256, ...rest });
}

// ── 1. 真实内置模型 ─────────────────────────────────────────

test(
  "两个真实内置模型均 ADMITTED 且 receipt 完整",
  { skip: HAS_RESTRICTED_FIXTURES ? false : "可再分发源码不包含受限 VRM fixture" },
  () => {
  for (const bytes of [FIXTURE_Z1, FIXTURE_V2]) {
    const receipt = admitVrmModel(bytes, { sha256: nodeSha256 });
    assert.equal(receipt.verdict, "ADMITTED");
    assert.equal(receipt.violations.length, 0);
    assert.match(receipt.contentHash, /^[0-9a-f]{64}$/);
    assert.equal(receipt.contentHash, nodeSha256(bytes));
    assert.equal(receipt.byteLength, bytes.length);
    assert.equal(receipt.validatorVersion, VALIDATOR_VERSION);
    assert.match(receipt.receiptId, /^arec_[0-9a-f]{64}$/);
    assert.ok(receipt.checks.length >= 10);
    assert.ok(receipt.checks.every((check) => check.verdict === "pass"));
    assert.equal(receipt.vrmSpecVersion, "0.x");
  }
  },
);

test("同一字节 receiptId 恒定；字节变化 receiptId 变化", () => {
  const a = admitVrmModel(FIXTURE_Z1, { sha256: nodeSha256 });
  const b = admitVrmModel(new Uint8Array(FIXTURE_Z1), { sha256: nodeSha256 });
  assert.equal(a.receiptId, b.receiptId);
  const mutated = new Uint8Array(FIXTURE_Z1);
  mutated[mutated.length - 1] ^= 0xff;
  const c = admitVrmModel(mutated, { sha256: nodeSha256 });
  assert.notEqual(c.receiptId, a.receiptId);
});

// ── 2. GLB 结构负例 ─────────────────────────────────────────

test("declaredLength 大于/小于实际长度均拒绝（尾随字节默认拒绝）", () => {
  for (const delta of [4, -4]) {
    const glb = buildGlb({ chunks: [jsonChunk(minimalVrmJson())], declaredDelta: delta });
    const receipt = admitVrmModel(glb, { sha256: nodeSha256 });
    assert.equal(receipt.verdict, "REJECTED");
    assert.ok(receipt.violations.some((v) => v.code === "glb_length_mismatch"));
  }
});

test("未知 chunk 在 declaredLength 范围内按规范忽略", () => {
  const receipt = admitSynthetic(minimalVrmJson(), null, {
    glb: {},
  });
  assert.equal(receipt.verdict, "ADMITTED");
  const withUnknown = buildGlb({
    chunks: [
      jsonChunk(minimalVrmJson()),
      { type: "XTRA", data: new Uint8Array([1, 2, 3, 4]) },
    ],
  });
  const receipt2 = admitVrmModel(withUnknown, { sha256: nodeSha256 });
  assert.equal(receipt2.verdict, "ADMITTED");
  assert.ok(receipt2.checks.find((c) => c.id === "glb-chunk-layout").detail.includes("忽略 1 个"));
});

test("未知 chunk 超出 declaredLength 范围拒绝", () => {
  const glb = buildGlb({
    chunks: [
      jsonChunk(minimalVrmJson()),
      { type: "XTRA", data: new Uint8Array([1, 2, 3, 4]), declaredLengthOverride: 4096 },
    ],
  });
  const receipt = admitVrmModel(glb, { sha256: nodeSha256 });
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "glb_chunk_overflow"));
});

test("GLB magic/version 错误拒绝", () => {
  const bad1 = buildGlb({ chunks: [jsonChunk(minimalVrmJson())], magic: "XXXX" });
  assert.equal(admitVrmModel(bad1, { sha256: nodeSha256 }).verdict, "REJECTED");
  const bad2 = buildGlb({ chunks: [jsonChunk(minimalVrmJson())], version: 1 });
  const receipt2 = admitVrmModel(bad2, { sha256: nodeSha256 });
  assert.equal(receipt2.verdict, "REJECTED");
  assert.ok(receipt2.violations.some((v) => v.code === "glb_version_invalid"));
});

test("extensionsRequired 未知扩展拒绝", () => {
  const receipt = admitSynthetic(minimalVrmJson({ json: { extensionsRequired: ["EVIL_extension"] } }));
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "extensions_required_unknown"));
});

test("BufferView 越界拒绝", () => {
  const json = minimalVrmJson({
    json: {
      buffers: [{ byteLength: 16 }],
      bufferViews: [{ buffer: 0, byteOffset: 8, byteLength: 16 }],
    },
  });
  const receipt = admitSynthetic(json, new Uint8Array(16));
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "bufferview_overrun"));
});

test("Accessor 越界拒绝（含 byteStride 计算）", () => {
  const json = minimalVrmJson({
    json: {
      buffers: [{ byteLength: 32 }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 32 }],
      accessors: [{ bufferView: 0, componentType: 5126, type: "VEC3", count: 4, byteOffset: 0 }],
    },
  });
  // 4 个 VEC3 float32 = 48 字节 > 32
  const receipt = admitSynthetic(json, new Uint8Array(32));
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "accessor_overrun"));

  const strided = minimalVrmJson({
    json: {
      buffers: [{ byteLength: 40 }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 40, byteStride: 16 }],
      accessors: [{ bufferView: 0, componentType: 5126, type: "VEC3", count: 3 }],
    },
  });
  // stride 16*(3-1)+12 = 44 > 40
  const receipt2 = admitSynthetic(strided, new Uint8Array(40));
  assert.equal(receipt2.verdict, "REJECTED");
  assert.ok(receipt2.violations.some((v) => v.code === "accessor_overrun"));

  const stridedOk = minimalVrmJson({
    json: {
      buffers: [{ byteLength: 44 }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 44, byteStride: 16 }],
      accessors: [{ bufferView: 0, componentType: 5126, type: "VEC3", count: 3 }],
    },
  });
  assert.equal(admitSynthetic(stridedOk, new Uint8Array(44)).verdict, "ADMITTED");
});

test("外部 URI 默认全 deny", () => {
  for (const uri of ["https://evil.example.com/a.bin", "http://evil.example.com/a.bin", "file:///etc/passwd", "C:\\Windows\\x.bin", "/abs/path.bin", "textures/a.png"]) {
    const receipt = admitSynthetic(minimalVrmJson({ json: { buffers: [{ byteLength: 4, uri }] } }));
    assert.equal(receipt.verdict, "REJECTED", uri);
    assert.ok(receipt.violations.some((v) => v.code === "uri_policy_denied"), uri);
  }
});

test("元数据 NaN/Infinity 拒绝；非法四元数拒绝", () => {
  const jsonText = JSON.stringify(minimalVrmJson()).replace(
    `"buffers":[]`,
    `"buffers":[],"nodes":[{"translation":[1e999,0,0]}]`,
  );
  const receipt = admitSynthetic(jsonText);
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "metadata_non_finite"));

  const badQuat = admitSynthetic(minimalVrmJson({ json: { nodes: [{ rotation: [0, 0, 0, 0] }] } }));
  assert.equal(badQuat.verdict, "REJECTED");
  assert.ok(badQuat.violations.some((v) => v.code === "node_rotation_not_normalized"));

  const goodQuat = admitSynthetic(minimalVrmJson({ json: { nodes: [{ rotation: [0, 0, 0, 1] }] } }));
  assert.equal(goodQuat.verdict, "ADMITTED");
});

test("资源上限配置化：超限拒绝且 limits 带 schemaVersion", () => {
  const json = minimalVrmJson({
    json: {
      buffers: [{ byteLength: 48 }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 48 }],
      accessors: [{ bufferView: 0, componentType: 5126, type: "VEC3", count: 4 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    },
  });
  const bin = new Uint8Array(48);
  assert.equal(admitSynthetic(json, bin).verdict, "ADMITTED");
  const tight = { ...DEFAULT_ADMISSION_LIMITS, maxVertices: 3 };
  const receipt = admitSynthetic(json, bin, { limits: tight });
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "limit_vertices"));
  // 未知更高 schemaVersion 的 limits 安全失败
  assert.throws(() => validateAdmissionLimits({ ...tight, schemaVersion: 99 }), /安全失败/);
});

test("许可元数据缺失拒绝（§9.1.12）", () => {
  const json = minimalVrmJson();
  delete json.extensions.VRM.meta;
  const receipt = admitSynthetic(json);
  assert.equal(receipt.verdict, "REJECTED");
  assert.ok(receipt.violations.some((v) => v.code === "license_meta_missing"));
});

// ── 3. 许可映射与许可门 ──────────────────────────────────────

test("VRM 0.x 许可映射：commercialUssageName→commercialUsage，rawMeta 保留原值", () => {
  const receipt = admitVrmModel(FIXTURE_Z1, { sha256: nodeSha256 });
  const license = receipt.licenseRecord;
  assert.equal(license.commercialUsage, "allow");
  assert.equal(license.rawMeta.commercialUssageName, "Allow"); // 原拼写原值
  assert.equal(license.licenseName, "Redistribution_Prohibited");
  assert.equal(license.vrmSpecVersion, "0.x");

  const synthetic = admitSynthetic(minimalVrmJson({ meta: { commercialUssageName: "Disallow", licenseName: "Redistribution_Prohibited" } }));
  assert.equal(synthetic.licenseRecord.commercialUsage, "disallow");
  const summary = customImportDisplaySummary(synthetic.licenseRecord);
  assert.equal(summary.redistributionProhibited, true);
  assert.ok(summary.warnings.includes("redistribution_prohibited_declared"));
  assert.ok(summary.warnings.includes("redistribution_unverified"));
});

test("VRM 1.0 字段映射到同一 LicenseRecord", () => {
  const json = {
    asset: { version: "2.0" },
    extensionsUsed: ["VRMC_vrm"],
    extensions: {
      VRMC_vrm: {
        meta: {
          name: "v1模型",
          authors: ["作者甲", "作者乙"],
          licenseUrl: "https://example.com/license",
          avatarPermission: "onlyAuthor",
          commercialUsage: "personalNonProfit",
          creditNotation: "required",
        },
      },
    },
    buffers: [],
  };
  const receipt = admitSynthetic(json);
  assert.equal(receipt.verdict, "ADMITTED");
  assert.equal(receipt.vrmSpecVersion, "1.0");
  assert.equal(receipt.licenseRecord.title, "v1模型");
  assert.equal(receipt.licenseRecord.author, "作者甲, 作者乙");
  assert.equal(receipt.licenseRecord.commercialUsage, "nonprofit");
  assert.equal(receipt.licenseRecord.attributionRequirement, "required");
  assert.equal(receipt.licenseRecord.rawMeta.commercialUsage, "personalNonProfit");
});

test("releaseGate：redistributionPermission != verified → fail", () => {
  const receipt = admitVrmModel(FIXTURE_V2, { sha256: nodeSha256 });
  assert.equal(assessRedistributionPermission(receipt.licenseRecord), "unverified");
  const gate = evaluateReleaseGate(receipt.licenseRecord);
  assert.equal(gate.pass, false);
  assert.equal(gate.reason, "redistribution_not_verified");
});

// ── 4. AssetRegistry 版本语义 ────────────────────────────────

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const HASH_C = "c".repeat(64);

async function makeRegistry(storage = createMemoryStorageBackend()) {
  const registry = await createAssetRegistry({ storage, issuerEpoch: 7 });
  return { registry, storage };
}

function registerInput(overrides = {}) {
  return {
    assetId: "asset-1",
    scope: AssetScope.MODEL,
    contentHash: HASH_A,
    byteLength: 1024,
    validationReceiptId: "arec_1",
    validatorVersion: VALIDATOR_VERSION,
    authorizationFingerprint: "afp_1",
    displayName: "模型甲",
    ...overrides,
  };
}

test("登记成功 registryEntryVersion=1；重复 assetId（含 tombstone）拒绝", async () => {
  const { registry } = await makeRegistry();
  const record = await registry.registerAsset(registerInput());
  assert.equal(record.registryEntryVersion, 1);
  await assert.rejects(() => registry.registerAsset(registerInput()), /已存在/);
});

test("VERSIONED_FIELDS 变化恰好 +1；一次事务多字段变化只 +1；旧 Token 失效", async () => {
  const { registry } = await makeRegistry();
  await registry.registerAsset(registerInput());
  const issuer = createTokenIssuer({ registry, issuerEpoch: 7 });
  const validator = createTokenValidator({ registry, issuerEpoch: 7 });
  const token = issuer.issueToken("asset-1");

  const updated = await registry.updateAssetFields("asset-1", {
    contentHash: HASH_B,
    byteLength: 2048,
    validationReceiptId: "arec_2",
  });
  assert.equal(updated.registryEntryVersion, 2); // 三个白名单字段同事务只 +1
  const result = validator.validateAndConsume(token);
  assert.equal(result.ok, false);
  assert.ok(result.errors.includes("registry_entry_version_mismatch"));
  assert.ok(result.errors.includes("content_hash_mismatch"));
});

test("displayName/lastUsedAt/UI/遥测变化不递增 registryEntryVersion", async () => {
  const { registry } = await makeRegistry();
  await registry.registerAsset(registerInput());
  const issuer = createTokenIssuer({ registry, issuerEpoch: 7 });
  const validator = createTokenValidator({ registry, issuerEpoch: 7 });
  const token = issuer.issueToken("asset-1");

  const updated = await registry.updateAssetFields("asset-1", {
    displayName: "新名字",
    lastUsedAt: 12345,
    isFavorite: true,
    sortOrder: 3,
    accessCount: 9,
    telemetry: { loads: 2 },
  });
  assert.equal(updated.registryEntryVersion, 1);
  assert.equal(updated.displayName, "新名字");
  assert.deepEqual(validator.validateAndConsume(token), { ok: true, errors: [] });
  // 非白名单变化写入审计日志
  assert.ok(updated.auditTrail.some((entry) => entry.action === "update" && entry.versioned === false));
  // 未列入白名单的字段直接拒绝
  await assert.rejects(() => registry.updateAssetFields("asset-1", { hackerField: 1 }), /白名单/);
});

test("准入迁移白名单：每条迁移恰好 +1；deleted tombstone 终态", async () => {
  const { registry } = await makeRegistry();
  await registry.registerAsset(registerInput());
  const q = await registry.transitionAdmissionState("asset-1", AdmissionState.QUARANTINED);
  assert.equal(q.registryEntryVersion, 2);
  // quarantined→admitted 无重验证凭据直接拒绝
  await assert.rejects(() => registry.transitionAdmissionState("asset-1", AdmissionState.ADMITTED), /重跑验证/);
  // 凭据与旧 receipt 相同也拒绝
  await assert.rejects(
    () => registry.transitionAdmissionState("asset-1", AdmissionState.ADMITTED, { revalidationReceiptId: "arec_1" }),
    /重跑验证/,
  );
  const back = await registry.transitionAdmissionState("asset-1", AdmissionState.ADMITTED, { revalidationReceiptId: "arec_2" });
  assert.equal(back.registryEntryVersion, 3);
  assert.equal(back.validationReceiptId, "arec_2");
  const del = await registry.transitionAdmissionState("asset-1", AdmissionState.DELETED);
  assert.equal(del.registryEntryVersion, 4);
  await assert.rejects(() => registry.transitionAdmissionState("asset-1", AdmissionState.ADMITTED, { revalidationReceiptId: "arec_3" }), /tombstone/);
  await assert.rejects(() => registry.updateAssetFields("asset-1", { displayName: "x" }), /tombstone/);
  // 非法迁移（不在白名单）
  const { registry: registry2 } = await makeRegistry();
  await registry2.registerAsset(registerInput({ assetId: "asset-2" }));
  await assert.rejects(() => registry2.transitionAdmissionState("asset-2", AdmissionState.ADMITTED), /非白名单/);
});

test("registry 提交失败：内存状态不变且不得签发 Token（orphan 规则）", async () => {
  const storage = createMemoryStorageBackend();
  const registry = await createAssetRegistry({ storage, issuerEpoch: 7 });
  storage.failNextWrites(1);
  await assert.rejects(() => registry.registerAsset(registerInput()), /提交失败/);
  assert.equal(registry.getRecord("asset-1"), null);
  const issuer = createTokenIssuer({ registry, issuerEpoch: 7 });
  assert.throws(() => issuer.issueToken("asset-1"), /orphan|未登记/);
  // 存储恢复后可重试登记
  const record = await registry.registerAsset(registerInput());
  assert.equal(record.registryEntryVersion, 1);
});

test("registry 未知更高 schemaVersion 安全失败不覆盖", async () => {
  const storage = createMemoryStorageBackend();
  await writeJsonAtomic(storage, "avatar-models/registry-v1.json", { schemaVersion: 99, revision: 1, records: {} });
  await assert.rejects(() => createAssetRegistry({ storage }), /安全失败/);
  const after = await storage.readBytes("avatar-models/registry-v1.json");
  assert.ok(JSON.parse(new TextDecoder().decode(after)).schemaVersion === 99);
});

test("authorizationFingerprint：输入集变化则指纹变化，同事务恒定", async () => {
  const receipt = admitVrmModel(FIXTURE_Z1, { sha256: nodeSha256 });
  const base = computeAuthorizationFingerprint({
    licenseRecord: receipt.licenseRecord,
    admissionLimits: DEFAULT_ADMISSION_LIMITS,
    validatorVersion: VALIDATOR_VERSION,
    contentHash: receipt.contentHash,
    byteLength: receipt.byteLength,
  });
  assert.match(base, /^afp_[0-9a-f]{64}$/);
  const again = computeAuthorizationFingerprint({
    licenseRecord: receipt.licenseRecord,
    admissionLimits: DEFAULT_ADMISSION_LIMITS,
    validatorVersion: VALIDATOR_VERSION,
    contentHash: receipt.contentHash,
    byteLength: receipt.byteLength,
  });
  assert.equal(base, again);
  const changed = computeAuthorizationFingerprint({
    licenseRecord: receipt.licenseRecord,
    admissionLimits: { ...DEFAULT_ADMISSION_LIMITS, maxVertices: 1 },
    validatorVersion: VALIDATOR_VERSION,
    contentHash: receipt.contentHash,
    byteLength: receipt.byteLength,
  });
  assert.notEqual(base, changed);
});

// ── 5. ValidatedAssetToken ──────────────────────────────────

test("Token：非 admitted 不可签发；逐字段不一致拒绝；singleUse 消费后拒绝", async () => {
  const { registry } = await makeRegistry();
  await registry.registerAsset(registerInput());
  const issuer = createTokenIssuer({ registry, issuerEpoch: 7 });
  const validator = createTokenValidator({ registry, issuerEpoch: 7 });

  const token = issuer.issueToken("asset-1");
  assert.equal(token.registryEntryVersion, 1);
  assert.deepEqual(validator.validateAndConsume(token), { ok: true, errors: [] });
  // singleUse 消费后再次使用拒绝
  const replay = validator.validateAndConsume(token);
  assert.equal(replay.ok, false);
  assert.ok(replay.errors.includes("single_use_consumed"));

  // 逐字段不一致拒绝
  const fresh = issuer.issueToken("asset-1");
  for (const [field, bad] of [
    ["contentHash", HASH_C],
    ["byteLength", 4096],
    ["validationReceiptId", "arec_x"],
    ["registryEntryVersion", 9],
  ]) {
    const tampered = { ...fresh, [field]: bad };
    const result = validator.validateAndConsume(tampered);
    assert.equal(result.ok, false, field);
  }
  // issuerEpoch 不一致拒绝
  const wrongEpoch = createTokenValidator({ registry, issuerEpoch: 8 });
  const epochResult = wrongEpoch.validateAndConsume(fresh);
  assert.equal(epochResult.ok, false);
  assert.ok(epochResult.errors.includes("issuer_epoch_mismatch"));

  // 非 admitted 不可签发
  await registry.transitionAdmissionState("asset-1", AdmissionState.REVOKED);
  assert.throws(() => issuer.issueToken("asset-1"), /admitted/);
  // 已签发 Token 在吊销后也拒绝
  const revokedResult = validator.validateAndConsume(fresh);
  assert.equal(revokedResult.ok, false);
});

test("其他资产记录变化不影响本资产 Token", async () => {
  const { registry } = await makeRegistry();
  await registry.registerAsset(registerInput());
  await registry.registerAsset(registerInput({ assetId: "asset-2", contentHash: HASH_B }));
  const issuer = createTokenIssuer({ registry, issuerEpoch: 7 });
  const validator = createTokenValidator({ registry, issuerEpoch: 7 });
  const token1 = issuer.issueToken("asset-1");
  await registry.updateAssetFields("asset-2", { contentHash: HASH_C, byteLength: 8 });
  await registry.transitionAdmissionState("asset-2", AdmissionState.QUARANTINED);
  assert.deepEqual(validator.validateAndConsume(token1), { ok: true, errors: [] });
});

test("Token 不携带绝对路径字段", async () => {
  const { registry } = await makeRegistry();
  await registry.registerAsset(registerInput());
  const issuer = createTokenIssuer({ registry, issuerEpoch: 7 });
  const token = issuer.issueToken("asset-1");
  assert.deepEqual(
    Object.keys(token).sort(),
    ["assetId", "byteLength", "contentHash", "issuerEpoch", "nonce", "registryEntryVersion", "singleUse", "validationReceiptId"],
  );
});

// ── 6. Quarantine ───────────────────────────────────────────

const GPU_FP_1 = { gpuVendorId: "0x10de", gpuDeviceId: "0x1f82", driverVersion: "551.23", angleBackend: "d3d11", osGraphicsBuild: "22631" };
const GPU_FP_2 = { ...GPU_FP_1, driverVersion: "560.94" };

test("三类隔离键按失败类别选择", () => {
  const ctx = { contentHash: HASH_A, validatorVersion: VALIDATOR_VERSION, engineVersion: "three-0.169.0", gpuFingerprint: GPU_FP_1 };
  assert.match(quarantineKeyForFailure(QuarantineCategory.STRUCTURAL, ctx), /^sq_[0-9a-f]{64}$/);
  assert.match(quarantineKeyForFailure(QuarantineCategory.ENGINE, ctx), /^eq_[0-9a-f]{64}$/);
  assert.match(quarantineKeyForFailure(QuarantineCategory.RUNTIME, ctx), /^rq_[0-9a-f]{64}$/);
  // 键构成：structural 不含 engineVersion/gpu；engine 不含 gpu
  const s1 = structuralQuarantineKey(ctx);
  const s2 = structuralQuarantineKey({ ...ctx, engineVersion: "other", gpuFingerprint: GPU_FP_2 });
  assert.equal(s1, s2);
  const e1 = engineQuarantineKey(ctx);
  const e2 = engineQuarantineKey({ ...ctx, gpuFingerprint: GPU_FP_2 });
  assert.equal(e1, e2);
  assert.notEqual(e1, engineQuarantineKey({ ...ctx, engineVersion: "other" }));
  assert.notEqual(runtimeQuarantineKey(ctx), runtimeQuarantineKey({ ...ctx, gpuFingerprint: GPU_FP_2 }));
  assert.throws(() => normalizeGpuFingerprint({ gpuVendorId: "1" }), /gpuFingerprint/);
});

test("计数窗口：structural 单次隔离；runtime 单会话 2 次 / 滚动 24h 3 次", async () => {
  const storage = createMemoryStorageBackend();
  const tracker = await createQuarantineTracker({ storage, nowWallClock: () => 1_000_000 });
  const sKey = structuralQuarantineKey({ contentHash: HASH_A, validatorVersion: VALIDATOR_VERSION });
  const r1 = await tracker.recordFailure({ key: sKey, category: QuarantineCategory.STRUCTURAL });
  assert.equal(r1.quarantined, true);
  assert.equal(tracker.isQuarantined(sKey), true);

  const rKey = runtimeQuarantineKey({ contentHash: HASH_B, engineVersion: "e1", gpuFingerprint: GPU_FP_1 });
  const f1 = await tracker.recordFailure({ key: rKey, category: QuarantineCategory.RUNTIME });
  assert.equal(f1.quarantined, false);
  const f2 = await tracker.recordFailure({ key: rKey, category: QuarantineCategory.RUNTIME });
  assert.equal(f2.quarantined, true);
  assert.equal(f2.reason, "session_crash_threshold");

  // 滚动 24h 3 次：三次失败分布在三个会话（每个 tracker 实例即一个会话），
  // 单会话阈值不触发，滚动窗口累计到 3 次触发。
  const rKey2 = runtimeQuarantineKey({ contentHash: HASH_C, engineVersion: "e1", gpuFingerprint: GPU_FP_1 });
  const session1 = await createQuarantineTracker({ storage, nowWallClock: () => 1_000_000 });
  await session1.recordFailure({ key: rKey2, category: QuarantineCategory.RUNTIME });
  const session2 = await createQuarantineTracker({ storage, nowWallClock: () => 1_000_000 + 1800_000 });
  await session2.recordFailure({ key: rKey2, category: QuarantineCategory.RUNTIME });
  const session3 = await createQuarantineTracker({ storage, nowWallClock: () => 1_000_000 + 3600_000 });
  const third = await session3.recordFailure({ key: rKey2, category: QuarantineCategory.RUNTIME });
  assert.equal(third.quarantined, true);
  assert.equal(third.reason, "rolling_crash_threshold");

  // 窗口外不计：两个事件相隔 48h（不同会话），滚动窗口只见最近一次，不触发滚动阈值
  const rKey3 = runtimeQuarantineKey({ contentHash: HASH_A, engineVersion: "e9", gpuFingerprint: GPU_FP_1 });
  const old = await createQuarantineTracker({ storage, nowWallClock: () => 1_000_000 });
  await old.recordFailure({ key: rKey3, category: QuarantineCategory.RUNTIME });
  const later = await createQuarantineTracker({ storage, nowWallClock: () => 1_000_000 + 48 * 3600_000 });
  const late = await later.recordFailure({ key: rKey3, category: QuarantineCategory.RUNTIME });
  assert.equal(late.quarantined, false);
  assert.equal(late.rollingCount, 1);
});

test("驱动 fingerprint 变化只重置 runtime 计数，structural 隔离保持", async () => {
  const storage = createMemoryStorageBackend();
  const tracker = await createQuarantineTracker({ storage });
  const sKey = structuralQuarantineKey({ contentHash: HASH_A, validatorVersion: VALIDATOR_VERSION });
  await tracker.recordFailure({ key: sKey, category: QuarantineCategory.STRUCTURAL });
  const runtimeBefore = runtimeQuarantineKey({ contentHash: HASH_A, engineVersion: "e1", gpuFingerprint: GPU_FP_1 });
  await tracker.recordFailure({ key: runtimeBefore, category: QuarantineCategory.RUNTIME });
  assert.equal(tracker.sessionCountOf(runtimeBefore), 1);

  // 驱动升级 → 新 runtime 键，计数从零开始（旧记录保留用于审计）
  const runtimeAfter = runtimeQuarantineKey({ contentHash: HASH_A, engineVersion: "e1", gpuFingerprint: GPU_FP_2 });
  assert.notEqual(runtimeBefore, runtimeAfter);
  assert.equal(tracker.sessionCountOf(runtimeAfter), 0);
  assert.equal(tracker.isQuarantined(runtimeAfter), false);
  assert.ok(tracker.getRecord(runtimeBefore).events.length === 1);
  // 结构性隔离不受驱动升级影响
  assert.equal(tracker.isQuarantined(sKey), true);
});

test("QuarantinePolicy 未知更高 schemaVersion 安全失败", async () => {
  const storage = createMemoryStorageBackend();
  await writeJsonAtomic(storage, "avatar-models/state/quarantine-policy-state-v1.json", { schemaVersion: 99, records: {} });
  await assert.rejects(() => createQuarantineTracker({ storage }), /安全失败/);
  assert.ok(DEFAULT_QUARANTINE_POLICY.schemaVersion === QUARANTINE_POLICY_SCHEMA_VERSION);
});

// ── 7. PendingLoadJournal ───────────────────────────────────

test("journal：parsing 前已写入；终态清除；崩溃归因可读", async () => {
  const storage = createMemoryStorageBackend();
  const journal = await createPendingLoadJournal({ storage });
  assert.equal(journal.readPendingEntry(), null);

  const entry = await journal.beginPhase({
    attemptId: "att_1",
    modelId: "model-1",
    contentHash: HASH_A,
    engineVersion: "three-0.169.0",
    gpuFingerprint: GPU_FP_1,
    phase: "parsing",
  });
  assert.equal(entry.phase, "parsing");
  assert.ok(Number.isInteger(entry.startedAtWallClock));
  // 写入已落盘（崩溃归因：模拟重启后新实例仍可读到）
  const afterCrash = await createPendingLoadJournal({ storage });
  const recovered = afterCrash.readPendingEntry();
  assert.equal(recovered.attemptId, "att_1");
  assert.equal(recovered.contentHash, HASH_A);
  assert.equal(recovered.phase, "parsing");

  const terminal = await afterCrash.clearJournal({ terminalState: "committed" });
  assert.equal(terminal.attemptId, "att_1");
  assert.equal(afterCrash.readPendingEntry(), null);
  assert.equal(afterCrash.readLastTerminal().terminalState, "committed");

  // 非法 phase / 非法终态拒绝
  await assert.rejects(() => afterCrash.beginPhase({ attemptId: "a", modelId: "m", contentHash: HASH_A, engineVersion: "e", phase: "loading" }), /phase/);
  await assert.rejects(() => afterCrash.clearJournal({ terminalState: "exploded" }), /terminalState/);
});

test("journal 未知更高 schemaVersion 安全失败不覆盖", async () => {
  const storage = createMemoryStorageBackend();
  await writeJsonAtomic(storage, "avatar-models/state/pending-load-v1.json", { schemaVersion: 99, entry: null });
  await assert.rejects(() => createPendingLoadJournal({ storage }), /安全失败/);
  const raw = await storage.readBytes("avatar-models/state/pending-load-v1.json");
  assert.equal(JSON.parse(new TextDecoder().decode(raw)).schemaVersion, 99);
  assert.ok(PENDING_LOAD_JOURNAL_SCHEMA_VERSION === 1);
});

// ── 8. 存储原子性 ────────────────────────────────────────────

test("原子写：临时文件失败不留下半成品", async () => {
  const storage = createMemoryStorageBackend();
  await writeJsonAtomic(storage, "a/b.json", { v: 1 });
  storage.failNextWrites(1);
  await assert.rejects(() => writeJsonAtomic(storage, "a/b.json", { v: 2 }), /注入/);
  const raw = await storage.readBytes("a/b.json");
  assert.deepEqual(JSON.parse(new TextDecoder().decode(raw)), { v: 1 }); // 目标保持旧值
  assert.equal(storage.hasTempLeftovers(), false); // 无临时文件残留
});
