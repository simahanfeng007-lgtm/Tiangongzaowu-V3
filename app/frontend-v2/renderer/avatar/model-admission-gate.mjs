// §9.1/§9.2/§9.3 ModelAdmissionGate：GLB/VRM 结构预检器（唯一 validator core）。
// P0 脚本与本模块共享同一份语义，禁止另起简化版。纯函数同步实现；
// sha256 可注入（默认纯 JS 实现），生产环境注入的加速实现必须逐位一致。

import { canonicalSha256, deepFreeze, sha256HexSync } from "./canonical-hash.mjs";
import { extractLicenseRecord, LicenseGateError } from "./model-license-gate.mjs";

export const VALIDATOR_VERSION = "vrm-admission-gate-1.0.0";
export const ADMISSION_LIMITS_SCHEMA_VERSION = 1;
export const ADMISSION_RECEIPT_SCHEMA_VERSION = 1;
export const URI_POLICY_SCHEMA_VERSION = 1;

// §9.3 初始上限（带 schemaVersion，配置化版本管理）。
// maxJoints 为单个 skin 的 joints 上限（与主流引擎单 skeleton 限制同义）。
export const DEFAULT_ADMISSION_LIMITS = Object.freeze({
  schemaVersion: ADMISSION_LIMITS_SCHEMA_VERSION,
  maxFileBytes: 256 * 1024 * 1024,
  maxTextureDimension: 4096,
  maxTotalTexturePixels: 96_000_000,
  maxTextures: 64,
  maxNodes: 2048,
  maxMeshes: 256,
  maxPrimitives: 2048,
  maxAccessors: 4096,
  maxVertices: 2_000_000,
  maxMorphTargetBindings: 4096,
  maxSkins: 256,
  maxJoints: 512,
  maxAnimations: 256,
  maxTotalAnimationKeyframes: 2_000_000,
});

// §9.2 URI 策略默认全 deny：内置/导入 VRM 必须是完整单文件 GLB。
export const DEFAULT_URI_POLICY = Object.freeze({
  schemaVersion: URI_POLICY_SCHEMA_VERSION,
  externalHttpUri: "deny",
  externalHttpsUri: "deny",
  externalFileUri: "deny",
  absolutePath: "deny",
  relativeExternalUri: "deny",
});

// 已知扩展白名单；extensionsRequired 中未知扩展直接拒绝（§9.1.7）。
const KNOWN_EXTENSIONS = Object.freeze([
  "VRM",
  "VRMC_vrm",
  "VRMC_vrm_animation",
  "VRMC_materials_mtoon",
  "VRMC_materials_hdr_emissiveMultiplier",
  "VRMC_node_constraint",
  "VRMC_springBone",
  "KHR_materials_unlit",
  "KHR_materials_emissive_strength",
  "KHR_texture_transform",
  "KHR_mesh_quantization",
  "KHR_texture_basisu",
  "EXT_texture_webp",
]);

const CHUNK_JSON = 0x4e4f534a; // "JSON"
const CHUNK_BIN = 0x004e4942; // "BIN\0"

const COMPONENT_BYTES = Object.freeze({ 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 });
const TYPE_COMPONENTS = Object.freeze({ SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 });
const QUATERNION_NORM_TOLERANCE = 0.01;

export class AdmissionError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AdmissionError";
    this.code = code;
  }
}

export function validateAdmissionLimits(limits) {
  if (limits === null || typeof limits !== "object") {
    throw new AdmissionError("limits_invalid", "admission limits 必须是对象");
  }
  if (!Number.isInteger(limits.schemaVersion) || limits.schemaVersion < 1) {
    throw new AdmissionError("limits_schema_invalid", "admission limits 需要正整数 schemaVersion");
  }
  // 未知更高版本：安全失败，禁止按新配置放行（§22.3 同原则）。
  if (limits.schemaVersion > ADMISSION_LIMITS_SCHEMA_VERSION) {
    throw new AdmissionError(
      "limits_schema_unsupported",
      `admission limits schemaVersion=${limits.schemaVersion} 高于已知 ${ADMISSION_LIMITS_SCHEMA_VERSION}，安全失败`,
    );
  }
  for (const key of Object.keys(DEFAULT_ADMISSION_LIMITS)) {
    if (key === "schemaVersion") continue;
    if (!Number.isInteger(limits[key]) || limits[key] <= 0) {
      throw new AdmissionError("limits_invalid", `admission limits.${key} 必须为正整数`);
    }
  }
  return limits;
}

export function validateUriPolicy(uriPolicy) {
  if (uriPolicy === null || typeof uriPolicy !== "object") {
    throw new AdmissionError("uri_policy_invalid", "uriPolicy 必须是对象");
  }
  if (uriPolicy.schemaVersion > URI_POLICY_SCHEMA_VERSION) {
    throw new AdmissionError(
      "uri_policy_schema_unsupported",
      `uriPolicy schemaVersion=${uriPolicy.schemaVersion} 高于已知 ${URI_POLICY_SCHEMA_VERSION}，安全失败`,
    );
  }
  for (const key of ["externalHttpUri", "externalHttpsUri", "externalFileUri", "absolutePath", "relativeExternalUri"]) {
    if (uriPolicy[key] !== "deny" && uriPolicy[key] !== "allow") {
      throw new AdmissionError("uri_policy_invalid", `uriPolicy.${key} 必须为 deny|allow`);
    }
  }
  return uriPolicy;
}

// URI 分类：data: 视为内嵌；其余按 §9.2 五类判定。
function classifyUri(uri) {
  if (typeof uri !== "string" || uri.length === 0) return "relativeExternalUri";
  const lower = uri.toLowerCase();
  if (lower.startsWith("data:")) return "embeddedData";
  if (lower.startsWith("http://")) return "externalHttpUri";
  if (lower.startsWith("https://")) return "externalHttpsUri";
  if (lower.startsWith("file://") || lower.startsWith("file:")) return "externalFileUri";
  if (lower.startsWith("/") || lower.startsWith("\\\\") || /^[a-z]:[\\/]/.test(lower)) return "absolutePath";
  return "relativeExternalUri";
}

// PNG/JPEG 尺寸嗅探（只读头部，不解码像素）；失败返回 null → fail-closed。
function sniffImageDimensions(bytes, start, end, mimeType) {
  if (mimeType === "image/png") {
    if (end - start < 24) return null;
    const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    for (let i = 0; i < 8; i++) if (bytes[start + i] !== sig[i]) return null;
    const dv = new DataView(bytes.buffer, bytes.byteOffset + start);
    return { width: dv.getUint32(16), height: dv.getUint32(20) };
  }
  if (mimeType === "image/jpeg") {
    if (end - start < 4 || bytes[start] !== 0xff || bytes[start + 1] !== 0xd8) return null;
    let p = start + 2;
    while (p + 9 <= end) {
      if (bytes[p] !== 0xff) return null;
      const marker = bytes[p + 1];
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) { p += 2; continue; }
      const dv = new DataView(bytes.buffer, bytes.byteOffset + p);
      const segLen = dv.getUint16(2);
      if (segLen < 2 || p + 2 + segLen > end) return null;
      const isSof =
        (marker >= 0xc0 && marker <= 0xc3) ||
        (marker >= 0xc5 && marker <= 0xc7) ||
        (marker >= 0xc9 && marker <= 0xcb) ||
        (marker >= 0xcd && marker <= 0xcf);
      if (isSof) return { height: dv.getUint16(5), width: dv.getUint16(7) };
      p += 2 + segLen;
    }
    return null;
  }
  return null;
}

function isNonNegativeInt(value) {
  return Number.isInteger(value) && value >= 0;
}

// 全部检查项 id 固定顺序，receipt 可审计；fatal 时后续项标记 aborted。
const CHECK_IDS = Object.freeze([
  "glb-header",
  "glb-declared-length",
  "glb-chunk-layout",
  "json-parse",
  "vrm-extension",
  "extensions-required-known",
  "uri-policy",
  "buffers-bounds",
  "bufferviews-bounds",
  "accessors-bounds",
  "metadata-finite",
  "transforms-valid",
  "resource-limits",
  "license-metadata",
]);

// 主入口：对确定字节做结构预检，返回 AdmissionReceipt（不抛模型错误，配置/用法错误抛 AdmissionError）。
export function admitVrmModel(input, options = {}) {
  const sha256 = typeof options.sha256 === "function" ? options.sha256 : sha256HexSync;
  const limits = validateAdmissionLimits(options.limits ?? DEFAULT_ADMISSION_LIMITS);
  const uriPolicy = validateUriPolicy(options.uriPolicy ?? DEFAULT_URI_POLICY);
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);

  const contentHash = sha256(bytes);
  const byteLength = bytes.length;
  const checks = [];
  const violations = [];
  let fatal = false;
  let cursor = 0;

  const pass = (id, detail) => { checks.push(Object.freeze(detail ? { id, verdict: "pass", detail } : { id, verdict: "pass" })); };
  const violation = (code, message, path) => {
    violations.push(Object.freeze(path ? { code, message, path } : { code, message }));
  };
  const fail = (id, code, message, path, { isFatal = false } = {}) => {
    checks.push(Object.freeze({ id, verdict: "fail", detail: message }));
    violation(code, message, path);
    if (isFatal) fatal = true;
  };
  const nextCheck = () => {
    const id = CHECK_IDS[cursor];
    cursor += 1;
    if (fatal) checks.push(Object.freeze({ id, verdict: "aborted" }));
    return fatal ? null : id;
  };

  let json = null;
  let binChunk = null;
  let vrmSpecVersion = null;
  let licenseRecord = null;

  // ── 1. GLB 头（§9.1.1/§9.1.2）────────────────────────────
  {
    const id = nextCheck();
    if (id) {
      if (byteLength < 12) {
        fail(id, "glb_header_truncated", "GLB 头不足 12 字节", null, { isFatal: true });
      } else {
        const dv = new DataView(bytes.buffer, bytes.byteOffset, byteLength);
        const magic = dv.getUint32(0, true);
        const version = dv.getUint32(4, true);
        if (magic !== 0x46546c67) { // "glTF"
          fail(id, "glb_magic_invalid", "GLB magic 不是 glTF", null, { isFatal: true });
        } else if (version !== 2) {
          fail(id, "glb_version_invalid", `GLB version=${version}，必须为 2`, null, { isFatal: true });
        } else {
          pass(id, "magic=glTF version=2");
        }
      }
    }
  }

  // ── 2. declaredLength 严格等值（§9.1.3：大于/小于均拒绝，尾随字节默认拒绝）──
  {
    const id = nextCheck();
    if (id) {
      const dv = new DataView(bytes.buffer, bytes.byteOffset, byteLength);
      const declared = dv.getUint32(8, true);
      if (declared !== byteLength) {
        fail(
          id,
          "glb_length_mismatch",
          `declaredLength=${declared} 与实际长度=${byteLength} 不一致（尾随字节默认拒绝）`,
          null,
          { isFatal: true },
        );
      } else {
        pass(id, `declaredLength=${declared}`);
      }
    }
  }

  // ── 3. chunk 边界（§9.1.4：JSON/BIN 边界有效；未知 chunk 仅在范围内忽略）──
  {
    const id = nextCheck();
    if (id) {
      const dv = new DataView(bytes.buffer, bytes.byteOffset, byteLength);
      const chunks = [];
      let offset = 12;
      let overflow = null;
      while (offset + 8 <= byteLength) {
        const chunkLength = dv.getUint32(offset, true);
        const chunkType = dv.getUint32(offset + 4, true);
        if (chunkLength % 4 !== 0) {
          overflow = `chunk@0x${offset.toString(16)} 长度 ${chunkLength} 未按 4 字节对齐`;
          break;
        }
        if (offset + 8 + chunkLength > byteLength) {
          overflow = `chunk@0x${offset.toString(16)} 声明长度 ${chunkLength} 超出 declaredLength 范围`;
          break;
        }
        chunks.push({ type: chunkType, start: offset + 8, length: chunkLength });
        offset += 8 + chunkLength;
      }
      if (overflow === null && offset !== byteLength) {
        overflow = "chunk 序列未覆盖全部字节（尾部残留不足 chunk 头）";
      }
      if (overflow !== null) {
        fail(id, "glb_chunk_overflow", overflow, null, { isFatal: true });
      } else if (chunks.length === 0 || chunks[0].type !== CHUNK_JSON) {
        fail(id, "glb_json_chunk_missing", "首个 chunk 必须是 JSON chunk", null, { isFatal: true });
      } else {
        const binChunks = chunks.filter((c) => c.type === CHUNK_BIN);
        if (binChunks.length > 1) {
          fail(id, "glb_bin_chunk_duplicated", "BIN chunk 至多一个", null, { isFatal: true });
        } else {
          binChunk = binChunks[0] ?? null;
          const ignored = chunks.filter((c) => c.type !== CHUNK_JSON && c.type !== CHUNK_BIN).length;
          pass(id, `chunks=${chunks.length}（未知 chunk 按规范忽略 ${ignored} 个）`);
        }
      }
    }
  }

  // ── 4. JSON 解析 ─────────────────────────────────────────
  {
    const id = nextCheck();
    if (id) {
      try {
        const jsonChunkStart = 20; // 12 头 + 8 chunk 头（首 chunk 必为 JSON，上一关已保证）
        const jsonChunkLength = new DataView(bytes.buffer, bytes.byteOffset, byteLength).getUint32(12, true);
        json = JSON.parse(new TextDecoder().decode(bytes.subarray(jsonChunkStart, jsonChunkStart + jsonChunkLength)));
        if (json === null || typeof json !== "object" || Array.isArray(json)) throw new Error("root not object");
        pass(id);
      } catch {
        fail(id, "glb_json_invalid", "JSON chunk 解析失败或根节点不是对象", null, { isFatal: true });
      }
    }
  }

  // ── 5. VRM 扩展存在与版本识别（§9.1.5/§9.1.6）─────────────
  {
    const id = nextCheck();
    if (id) {
      const extensions = json.extensions;
      const hasVrm0 = extensions !== null && typeof extensions === "object" && extensions.VRM !== null && typeof extensions.VRM === "object";
      const hasVrm1 = extensions !== null && typeof extensions === "object" && extensions.VRMC_vrm !== null && typeof extensions.VRMC_vrm === "object";
      const used = Array.isArray(json.extensionsUsed) ? json.extensionsUsed : [];
      if (hasVrm0 && hasVrm1) {
        fail(id, "vrm_extension_ambiguous", "VRM 与 VRMC_vrm 不得同时存在");
      } else if (!hasVrm0 && !hasVrm1) {
        fail(id, "vrm_extension_missing", "缺少 extensions.VRM / extensions.VRMC_vrm（§9.1.5）");
      } else {
        vrmSpecVersion = hasVrm0 ? "0.x" : "1.0";
        const extName = hasVrm0 ? "VRM" : "VRMC_vrm";
        if (!used.includes(extName)) {
          fail(id, "vrm_extension_not_declared", `${extName} 未列入 extensionsUsed`);
        } else {
          pass(id, `vrmSpecVersion=${vrmSpecVersion}`);
        }
      }
    }
  }

  // ── 6. extensionsRequired 未知扩展拒绝（§9.1.7）───────────
  {
    const id = nextCheck();
    if (id) {
      const required = Array.isArray(json.extensionsRequired) ? json.extensionsRequired : [];
      const unknown = required.filter((name) => !KNOWN_EXTENSIONS.includes(name));
      if (unknown.length > 0) {
        fail(id, "extensions_required_unknown", `extensionsRequired 含未知扩展: ${unknown.join(",")}`);
      } else {
        pass(id, required.length > 0 ? `required=${required.join(",")}` : "无 required 扩展");
      }
    }
  }

  // ── 7. URI 策略（§9.1.9/§9.2：五类外部 URI 默认全 deny）───
  {
    const id = nextCheck();
    if (id) {
      let denied = 0;
      const checkUri = (uri, path) => {
        const category = classifyUri(uri);
        if (category === "embeddedData") return;
        if (uriPolicy[category] === "deny") {
          denied += 1;
          violation("uri_policy_denied", `URI 类别 ${category} 默认拒绝`, path);
        }
      };
      (Array.isArray(json.buffers) ? json.buffers : []).forEach((buffer, i) => {
        if (typeof buffer?.uri === "string") checkUri(buffer.uri, `buffers[${i}].uri`);
      });
      (Array.isArray(json.images) ? json.images : []).forEach((image, i) => {
        if (typeof image?.uri === "string") checkUri(image.uri, `images[${i}].uri`);
      });
      if (denied > 0) {
        fail(id, "uri_policy_denied", `${denied} 个外部 URI 被策略拒绝`);
      } else {
        pass(id);
      }
    }
  }

  // ── 8. buffers 边界（§9.1.8 前置：GLB 内嵌 BIN 与声明一致）──
  const buffers = json && Array.isArray(json.buffers) ? json.buffers : [];
  const bufferViews = json && Array.isArray(json.bufferViews) ? json.bufferViews : [];
  const accessors = json && Array.isArray(json.accessors) ? json.accessors : [];
  {
    const id = nextCheck();
    if (id) {
      let bad = 0;
      buffers.forEach((buffer, i) => {
        if (buffer === null || typeof buffer !== "object") { bad += 1; return; }
        if (typeof buffer.uri === "string") return; // 外部 buffer 已由 URI 策略拒绝
        if (i !== 0) {
          bad += 1;
          violation("buffer_external_without_uri", `buffers[${i}] 无 uri 且不是 GLB 内嵌 buffer 0`, `buffers[${i}]`);
          return;
        }
        if (!isNonNegativeInt(buffer.byteLength)) {
          bad += 1;
          violation("buffer_length_invalid", `buffers[${i}].byteLength 非法`, `buffers[${i}].byteLength`);
          return;
        }
        if (binChunk === null) {
          bad += 1;
          violation("buffer_bin_missing", "buffers[0] 无 uri 但 GLB 缺少 BIN chunk", "buffers[0]");
          return;
        }
        if (buffer.byteLength > binChunk.length) {
          bad += 1;
          violation(
            "buffer_length_overrun",
            `buffers[0].byteLength=${buffer.byteLength} 超出 BIN chunk 长度=${binChunk.length}`,
            "buffers[0].byteLength",
          );
        }
      });
      if (bad > 0) fail(id, "buffers_bounds_invalid", `${bad} 个 buffer 边界违规`);
      else pass(id);
    }
  }

  // 内部 buffer 可读长度（外部 buffer 已由 URI 策略拒绝，视为 0 不可读）。
  const bufferReadableLength = (index) => {
    const buffer = buffers[index];
    if (buffer === null || typeof buffer !== "object") return 0;
    if (typeof buffer.uri === "string") return 0;
    if (!isNonNegativeInt(buffer.byteLength)) return 0;
    return binChunk ? Math.min(buffer.byteLength, binChunk.length) : 0;
  };

  // ── 9. bufferViews 边界（§9.1.8）──────────────────────────
  {
    const id = nextCheck();
    if (id) {
      let bad = 0;
      bufferViews.forEach((view, i) => {
        if (view === null || typeof view !== "object") { bad += 1; return; }
        const bufferIndex = view.buffer;
        if (!Number.isInteger(bufferIndex) || bufferIndex < 0 || bufferIndex >= buffers.length) {
          bad += 1;
          violation("bufferview_buffer_invalid", `bufferViews[${i}].buffer=${bufferIndex} 越界`, `bufferViews[${i}].buffer`);
          return;
        }
        const offset = view.byteOffset ?? 0;
        if (!isNonNegativeInt(offset) || !isNonNegativeInt(view.byteLength)) {
          bad += 1;
          violation("bufferview_offset_invalid", `bufferViews[${i}] byteOffset/byteLength 非法`, `bufferViews[${i}]`);
          return;
        }
        if (offset + view.byteLength > bufferReadableLength(bufferIndex)) {
          bad += 1;
          violation(
            "bufferview_overrun",
            `bufferViews[${i}] 区间 [${offset}, ${offset + view.byteLength}) 超出 buffer 可读长度 ${bufferReadableLength(bufferIndex)}`,
            `bufferViews[${i}]`,
          );
        }
        if (view.byteStride !== undefined) {
          const stride = view.byteStride;
          if (!Number.isInteger(stride) || stride < 4 || stride > 252 || stride % 4 !== 0) {
            bad += 1;
            violation("bufferview_stride_invalid", `bufferViews[${i}].byteStride=${stride} 非法（须 4..252 且为 4 的倍数）`, `bufferViews[${i}].byteStride`);
          }
        }
      });
      if (bad > 0) fail(id, "bufferviews_bounds_invalid", `${bad} 个 bufferView 边界违规`);
      else pass(id, `bufferViews=${bufferViews.length}`);
    }
  }

  // ── 10. accessors 边界（§9.1.8：含 byteStride 计算）───────
  const accessorElementSize = (accessor) => {
    const componentBytes = COMPONENT_BYTES[accessor.componentType];
    const components = TYPE_COMPONENTS[accessor.type];
    if (!componentBytes || !components) return null;
    return componentBytes * components;
  };
  {
    const id = nextCheck();
    if (id) {
      let bad = 0;
      accessors.forEach((accessor, i) => {
        if (accessor === null || typeof accessor !== "object") { bad += 1; return; }
        const elementSize = accessorElementSize(accessor);
        if (elementSize === null) {
          bad += 1;
          violation("accessor_type_invalid", `accessors[${i}] componentType/type 未知`, `accessors[${i}]`);
          return;
        }
        if (!isNonNegativeInt(accessor.count)) {
          bad += 1;
          violation("accessor_count_invalid", `accessors[${i}].count 非法`, `accessors[${i}].count`);
          return;
        }
        if (accessor.bufferView !== undefined && accessor.bufferView !== null) {
          const viewIndex = accessor.bufferView;
          if (!Number.isInteger(viewIndex) || viewIndex < 0 || viewIndex >= bufferViews.length) {
            bad += 1;
            violation("accessor_bufferview_invalid", `accessors[${i}].bufferView=${viewIndex} 越界`, `accessors[${i}].bufferView`);
            return;
          }
          const view = bufferViews[viewIndex] ?? {};
          const viewLength = isNonNegativeInt(view.byteLength) ? view.byteLength : 0;
          const stride = view.byteStride;
          const accessorOffset = accessor.byteOffset ?? 0;
          if (!isNonNegativeInt(accessorOffset)) {
            bad += 1;
            violation("accessor_offset_invalid", `accessors[${i}].byteOffset 非法`, `accessors[${i}].byteOffset`);
            return;
          }
          let needed;
          if (stride !== undefined && Number.isInteger(stride) && stride > 0) {
            if (stride < elementSize) {
              bad += 1;
              violation("accessor_stride_underrun", `accessors[${i}] byteStride=${stride} 小于元素大小 ${elementSize}`, `accessors[${i}]`);
              return;
            }
            needed = accessor.count === 0 ? 0 : stride * (accessor.count - 1) + elementSize;
          } else {
            needed = elementSize * accessor.count;
          }
          if (accessorOffset + needed > viewLength) {
            bad += 1;
            violation(
              "accessor_overrun",
              `accessors[${i}] 读取 ${needed} 字节（偏移 ${accessorOffset}）超出 bufferView 长度 ${viewLength}`,
              `accessors[${i}]`,
            );
          }
        }
        if (accessor.sparse !== undefined && accessor.sparse !== null) {
          const sparse = accessor.sparse;
          const sparseCount = sparse.count;
          if (!isNonNegativeInt(sparseCount)) {
            bad += 1;
            violation("accessor_sparse_invalid", `accessors[${i}].sparse.count 非法`, `accessors[${i}].sparse`);
            return;
          }
          const checkSparseView = (part, componentBytes, path) => {
            const viewIndex = part?.bufferView;
            if (!Number.isInteger(viewIndex) || viewIndex < 0 || viewIndex >= bufferViews.length) {
              bad += 1;
              violation("accessor_sparse_bufferview_invalid", `${path} 引用越界`, path);
              return;
            }
            const view = bufferViews[viewIndex] ?? {};
            const viewLength = isNonNegativeInt(view.byteLength) ? view.byteLength : 0;
            const offset = part.byteOffset ?? 0;
            if (!isNonNegativeInt(offset) || offset + componentBytes * sparseCount > viewLength) {
              bad += 1;
              violation("accessor_sparse_overrun", `${path} 越界读取`, path);
            }
          };
          const indexBytes = COMPONENT_BYTES[sparse.indices?.componentType];
          if (!indexBytes) {
            bad += 1;
            violation("accessor_sparse_indices_invalid", `accessors[${i}].sparse.indices componentType 未知`, `accessors[${i}].sparse.indices`);
          } else {
            checkSparseView(sparse.indices, indexBytes, `accessors[${i}].sparse.indices`);
          }
          checkSparseView(sparse.values, elementSize, `accessors[${i}].sparse.values`);
        }
      });
      if (bad > 0) fail(id, "accessors_bounds_invalid", `${bad} 个 accessor 边界违规`);
      else pass(id, `accessors=${accessors.length}`);
    }
  }

  // ── 11. 元数据 NaN/Infinity 全量扫描（§9.1.10）────────────
  {
    const id = nextCheck();
    if (id) {
      let bad = 0;
      const walk = (value, path) => {
        if (bad > 64) return; // 违规已够判 REJECTED，避免巨型树刷屏
        if (typeof value === "number") {
          if (!Number.isFinite(value)) {
            bad += 1;
            violation("metadata_non_finite", `元数据含非有限数（NaN/Infinity）`, path);
          }
          return;
        }
        if (Array.isArray(value)) value.forEach((item, i) => walk(item, `${path}[${i}]`));
        else if (value !== null && typeof value === "object") {
          for (const key of Object.keys(value)) walk(value[key], `${path}.${key}`);
        }
      };
      walk(json, "$");
      if (bad > 0) fail(id, "metadata_non_finite", `${bad} 处非有限数`);
      else pass(id);
    }
  }

  // ── 12. 矩阵/四元数合法性（§9.1.10）───────────────────────
  const nodes = json && Array.isArray(json.nodes) ? json.nodes : [];
  {
    const id = nextCheck();
    if (id) {
      let bad = 0;
      nodes.forEach((node, i) => {
        if (node === null || typeof node !== "object") return;
        if (node.matrix !== undefined) {
          if (!Array.isArray(node.matrix) || node.matrix.length !== 16) {
            bad += 1;
            violation("node_matrix_invalid", `nodes[${i}].matrix 必须是 16 元素数组`, `nodes[${i}].matrix`);
          }
        }
        if (node.rotation !== undefined) {
          const rotation = node.rotation;
          if (!Array.isArray(rotation) || rotation.length !== 4 || rotation.some((v) => typeof v !== "number" || !Number.isFinite(v))) {
            bad += 1;
            violation("node_rotation_invalid", `nodes[${i}].rotation 非法四元数`, `nodes[${i}].rotation`);
          } else {
            const norm = Math.hypot(rotation[0], rotation[1], rotation[2], rotation[3]);
            if (Math.abs(norm - 1) > QUATERNION_NORM_TOLERANCE) {
              bad += 1;
              violation("node_rotation_not_normalized", `nodes[${i}].rotation 四元数范数=${norm}，非单位四元数`, `nodes[${i}].rotation`);
            }
          }
        }
      });
      if (bad > 0) fail(id, "transform_invalid", `${bad} 处非法矩阵/四元数`);
      else pass(id);
    }
  }

  // ── 13. 资源上限（§9.1.11/§9.3）───────────────────────────
  const meshes = json && Array.isArray(json.meshes) ? json.meshes : [];
  const skins = json && Array.isArray(json.skins) ? json.skins : [];
  const animations = json && Array.isArray(json.animations) ? json.animations : [];
  const images = json && Array.isArray(json.images) ? json.images : [];
  {
    const id = nextCheck();
    if (id) {
      let bad = 0;
      const over = (code, message, path) => { bad += 1; violation(code, message, path); };
      if (byteLength > limits.maxFileBytes) over("limit_file_bytes", `文件 ${byteLength} 字节超过 maxFileBytes=${limits.maxFileBytes}`, null);
      if (nodes.length > limits.maxNodes) over("limit_nodes", `nodes=${nodes.length} 超过 maxNodes=${limits.maxNodes}`, "nodes");
      if (meshes.length > limits.maxMeshes) over("limit_meshes", `meshes=${meshes.length} 超过 maxMeshes=${limits.maxMeshes}`, "meshes");
      if (accessors.length > limits.maxAccessors) over("limit_accessors", `accessors=${accessors.length} 超过 maxAccessors=${limits.maxAccessors}`, "accessors");
      if (skins.length > limits.maxSkins) over("limit_skins", `skins=${skins.length} 超过 maxSkins=${limits.maxSkins}`, "skins");
      if (animations.length > limits.maxAnimations) over("limit_animations", `animations=${animations.length} 超过 maxAnimations=${limits.maxAnimations}`, "animations");

      let primitives = 0;
      let vertices = 0;
      let morphBindings = 0;
      meshes.forEach((mesh, mi) => {
        (Array.isArray(mesh?.primitives) ? mesh.primitives : []).forEach((primitive, pi) => {
          primitives += 1;
          const positionIndex = primitive?.attributes?.POSITION;
          if (Number.isInteger(positionIndex) && positionIndex >= 0 && positionIndex < accessors.length) {
            const count = accessors[positionIndex]?.count;
            if (isNonNegativeInt(count)) vertices += count;
          } else if (positionIndex !== undefined) {
            over("primitive_accessor_invalid", `meshes[${mi}].primitives[${pi}].attributes.POSITION 引用越界`, `meshes[${mi}].primitives[${pi}]`);
          }
          if (Array.isArray(primitive?.targets)) morphBindings += primitive.targets.length;
        });
      });
      if (primitives > limits.maxPrimitives) over("limit_primitives", `primitives=${primitives} 超过 maxPrimitives=${limits.maxPrimitives}`, "meshes");
      if (vertices > limits.maxVertices) over("limit_vertices", `vertices=${vertices} 超过 maxVertices=${limits.maxVertices}`, "meshes");
      if (morphBindings > limits.maxMorphTargetBindings) over("limit_morph_bindings", `morphTargetBindings=${morphBindings} 超过 maxMorphTargetBindings=${limits.maxMorphTargetBindings}`, "meshes");

      skins.forEach((skin, si) => {
        const joints = Array.isArray(skin?.joints) ? skin.joints : [];
        if (joints.length > limits.maxJoints) over("limit_joints", `skins[${si}].joints=${joints.length} 超过单 skin maxJoints=${limits.maxJoints}`, `skins[${si}].joints`);
        joints.forEach((nodeIndex, ji) => {
          if (!Number.isInteger(nodeIndex) || nodeIndex < 0 || nodeIndex >= nodes.length) {
            over("skin_joint_invalid", `skins[${si}].joints[${ji}]=${nodeIndex} 引用越界`, `skins[${si}].joints[${ji}]`);
          }
        });
      });

      let keyframes = 0;
      animations.forEach((animation, ai) => {
        (Array.isArray(animation?.samplers) ? animation.samplers : []).forEach((sampler, si) => {
          const input = sampler?.input;
          if (Number.isInteger(input) && input >= 0 && input < accessors.length) {
            const count = accessors[input]?.count;
            if (isNonNegativeInt(count)) keyframes += count;
          } else {
            over("animation_sampler_invalid", `animations[${ai}].samplers[${si}].input 引用越界`, `animations[${ai}].samplers[${si}]`);
          }
        });
      });
      if (keyframes > limits.maxTotalAnimationKeyframes) over("limit_animation_keyframes", `keyframes=${keyframes} 超过 maxTotalAnimationKeyframes=${limits.maxTotalAnimationKeyframes}`, "animations");

      // 贴图：数量、单张尺寸、总像素（尺寸从 PNG/JPEG 头嗅探，读不出即 fail-closed）。
      if (images.length > limits.maxTextures) over("limit_textures", `textures=${images.length} 超过 maxTextures=${limits.maxTextures}`, "images");
      let totalPixels = 0;
      images.forEach((image, ii) => {
        if (typeof image?.uri === "string") {
          // data: URI 不做尺寸校验将无法执行像素预算 → fail-closed。
          over("texture_dimension_unverified", `images[${ii}] 使用 data:/外部 URI，尺寸不可核验`, `images[${ii}]`);
          return;
        }
        const viewIndex = image?.bufferView;
        if (!Number.isInteger(viewIndex) || viewIndex < 0 || viewIndex >= bufferViews.length) {
          over("texture_bufferview_invalid", `images[${ii}].bufferView 引用越界`, `images[${ii}].bufferView`);
          return;
        }
        const view = bufferViews[viewIndex] ?? {};
        if (view.buffer !== 0 || binChunk === null) {
          over("texture_bufferview_invalid", `images[${ii}] 未指向 GLB 内嵌 BIN`, `images[${ii}]`);
          return;
        }
        const start = binChunk.start + (view.byteOffset ?? 0);
        const end = start + (isNonNegativeInt(view.byteLength) ? view.byteLength : 0);
        const dimensions = end <= byteLength ? sniffImageDimensions(bytes, start, end, image.mimeType) : null;
        if (dimensions === null) {
          over("texture_dimension_unverified", `images[${ii}] 尺寸无法从 ${image.mimeType ?? "未知 mimeType"} 头部读取（fail-closed）`, `images[${ii}]`);
          return;
        }
        if (dimensions.width > limits.maxTextureDimension || dimensions.height > limits.maxTextureDimension) {
          over("limit_texture_dimension", `images[${ii}] 尺寸 ${dimensions.width}x${dimensions.height} 超过 maxTextureDimension=${limits.maxTextureDimension}`, `images[${ii}]`);
        }
        totalPixels += dimensions.width * dimensions.height;
      });
      if (totalPixels > limits.maxTotalTexturePixels) over("limit_texture_pixels", `总贴图像素=${totalPixels} 超过 maxTotalTexturePixels=${limits.maxTotalTexturePixels}`, "images");

      if (bad > 0) fail(id, "resource_limits_exceeded", `${bad} 项资源上限/引用违规`);
      else pass(id, `vertices=${vertices} textures=${images.length} pixels=${totalPixels}`);
    }
  }

  // ── 14. 许可元数据提取（§9.1.12：必须可读取并登记）────────
  {
    const id = nextCheck();
    if (id) {
      if (vrmSpecVersion === null) {
        fail(id, "license_meta_missing", "VRM 扩展缺失，无法提取许可元数据");
      } else {
        const extensions = json.extensions ?? {};
        const meta = vrmSpecVersion === "0.x" ? extensions.VRM?.meta : extensions.VRMC_vrm?.meta;
        try {
          licenseRecord = extractLicenseRecord({ vrmSpecVersion, meta });
          pass(id, `licenseName=${licenseRecord.licenseName ?? "（未声明）"}`);
        } catch (error) {
          if (error instanceof LicenseGateError) {
            fail(id, "license_meta_missing", `许可元数据不可读取：${error.message}`);
          } else {
            throw error;
          }
        }
      }
    }
  }

  const verdict = violations.length === 0 ? "ADMITTED" : "REJECTED";
  const core = {
    schemaVersion: ADMISSION_RECEIPT_SCHEMA_VERSION,
    contentHash,
    byteLength,
    validatorVersion: VALIDATOR_VERSION,
    limitsSchemaVersion: limits.schemaVersion,
    uriPolicySchemaVersion: uriPolicy.schemaVersion,
    vrmSpecVersion,
    checks,
    licenseRecord,
    verdict,
    violations,
  };
  // receiptId 由内容规范化哈希派生，同一字节+同一 validator 恒定（冪等可审计）。
  return deepFreeze({ ...core, receiptId: `arec_${canonicalSha256(core)}` });
}
