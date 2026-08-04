// §11.1 资源估算：B/A/T/M/F/k/ε 参数化 predictedPeak，与 safetyFactor × resourceBudget 比较。
// 估算输入来自 ModelAdmissionGate 同源结构统计（节点/网格/纹理/Accessor/动画）+ 纹理像素估算。
// 纯函数、引擎无关：只读 GLB JSON chunk 头做结构统计，不依赖 three.js 与 DOM/时钟。

import { deepFreeze } from "./canonical-hash.mjs";

export const RESOURCE_ESTIMATOR_SCHEMA_VERSION = 1;

export class ResourceEstimatorError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ResourceEstimatorError";
    this.code = code;
  }
}

// §11.1/§11.2 初始系数（版本化，随 P0-B 设备矩阵校准；safetyFactor=0.70 为建议初始值）。
export const DEFAULT_RESOURCE_ESTIMATE_PARAMS = Object.freeze({
  schemaVersion: RESOURCE_ESTIMATOR_SCHEMA_VERSION,
  safetyFactor: 0.70,
  kCopyFactor: 1.0, // k：数据跨边界副本系数
  epsilonBytes: 8 * 1024 * 1024, // ε：驱动、对象和 GC 延迟
  baseResidentBytes: 16 * 1024 * 1024, // Mbase：运行时基线驻留
  framebufferBytes: 32 * 1024 * 1024, // F：Framebuffer 与渲染目标
  geometryBytesPerVertex: 32, // A：position/normal/uv/skin 等顶点属性合计
  accessorOverheadBytes: 256, // A：每 Accessor 元数据开销
  nodeOverheadBytes: 512, // A：每节点场景图开销
  textureBytesPerPixel: 4, // T：RGBA8 解码
  mipmapNumerator: 4, // T：Mipmap 放大 4/3
  mipmapDenominator: 3,
  morphBytesPerBinding: 48 * 1024, // M：每 morph target binding 平均开销
  skinBytesPerJoint: 64, // M：骨骼矩阵与蒙皮索引
  animationBytesPerKeyframe: 16, // M：动画采样
  unverifiableTextureFactor: 8, // 纹理尺寸不可核验时按压缩字节 ×8 兜底（fail-closed 方向）
});

const POSITIVE_NUMBER_KEYS = Object.freeze([
  "kCopyFactor",
  "epsilonBytes",
  "baseResidentBytes",
  "framebufferBytes",
  "geometryBytesPerVertex",
  "accessorOverheadBytes",
  "nodeOverheadBytes",
  "textureBytesPerPixel",
  "morphBytesPerBinding",
  "skinBytesPerJoint",
  "animationBytesPerKeyframe",
  "unverifiableTextureFactor",
]);

export function validateResourceEstimateParams(params) {
  if (params === null || typeof params !== "object") {
    throw new ResourceEstimatorError("params_invalid", "resource estimate params 必须是对象");
  }
  if (!Number.isInteger(params.schemaVersion) || params.schemaVersion < 1) {
    throw new ResourceEstimatorError("params_schema_invalid", "params 需要正整数 schemaVersion");
  }
  if (params.schemaVersion > RESOURCE_ESTIMATOR_SCHEMA_VERSION) {
    throw new ResourceEstimatorError(
      "params_schema_unsupported",
      `params schemaVersion=${params.schemaVersion} 高于已知 ${RESOURCE_ESTIMATOR_SCHEMA_VERSION}，安全失败`,
    );
  }
  for (const key of POSITIVE_NUMBER_KEYS) {
    if (!Number.isFinite(params[key]) || params[key] <= 0) {
      throw new ResourceEstimatorError("params_invalid", `params.${key} 必须为正数`);
    }
  }
  if (!Number.isInteger(params.mipmapNumerator) || !Number.isInteger(params.mipmapDenominator) || params.mipmapDenominator <= 0) {
    throw new ResourceEstimatorError("params_invalid", "params.mipmap 比例必须为正整数比");
  }
  if (!Number.isFinite(params.safetyFactor) || params.safetyFactor <= 0 || params.safetyFactor > 1) {
    throw new ResourceEstimatorError("params_invalid", "params.safetyFactor 必须在 (0,1] 区间");
  }
  return params;
}

// ── GLB JSON chunk 头读取（引擎无关最小实现）──────────────────
// 语义与 admission gate 的 GLB 布局一致（magic/version/declaredLength/首 chunk JSON），
// 但只服务估算，不做准入门判定；容器非法时抛错由调用方归为结构类失败。
const GLB_MAGIC = 0x46546c67; // "glTF"
const GLB_CHUNK_JSON = 0x4e4f534a; // "JSON"
const GLB_CHUNK_BIN = 0x004e4942; // "BIN\0"

export function readGlbJsonForEstimate(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (view.byteLength < 20) {
    throw new ResourceEstimatorError("glb_json_unavailable", "字节长度不足，无法读取 GLB JSON chunk");
  }
  const header = new DataView(view.buffer, view.byteOffset, view.byteLength);
  if (header.getUint32(0, true) !== GLB_MAGIC) {
    throw new ResourceEstimatorError("glb_json_unavailable", "缺少 glTF magic，估算只接受单文件 GLB");
  }
  if (header.getUint32(8, true) !== view.byteLength) {
    throw new ResourceEstimatorError("glb_json_unavailable", "GLB declaredLength 与实际长度不一致");
  }
  const chunkLength = header.getUint32(12, true);
  if (header.getUint32(16, true) !== GLB_CHUNK_JSON || 20 + chunkLength > view.byteLength) {
    throw new ResourceEstimatorError("glb_json_unavailable", "GLB 首个 chunk 不是 JSON");
  }
  return JSON.parse(new TextDecoder().decode(view.subarray(20, 20 + chunkLength)));
}

function locateBinChunk(view) {
  const header = new DataView(view.buffer, view.byteOffset, view.byteLength);
  let offset = 12;
  while (offset + 8 <= view.byteLength) {
    const chunkLength = header.getUint32(offset, true);
    const chunkType = header.getUint32(offset + 4, true);
    if (chunkType === GLB_CHUNK_BIN) return { start: offset + 8, length: chunkLength };
    offset += 8 + chunkLength;
  }
  return null;
}

// PNG/JPEG 尺寸嗅探（只读头部，不解码像素）；读不出返回 null → 走兜底系数。
function sniffImagePixels(bytes, start, end, mimeType) {
  if (mimeType === "image/png") {
    if (end - start < 24) return null;
    const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    for (let i = 0; i < 8; i += 1) if (bytes[start + i] !== sig[i]) return null;
    const dv = new DataView(bytes.buffer, bytes.byteOffset + start);
    return dv.getUint32(16) * dv.getUint32(20);
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
      if (isSof) return dv.getUint16(5) * dv.getUint16(7);
      p += 2 + segLen;
    }
    return null;
  }
  return null;
}

function countArray(value) {
  return Array.isArray(value) ? value.length : 0;
}

function isNonNegativeInt(value) {
  return Number.isInteger(value) && value >= 0;
}

// 结构统计：与 ModelAdmissionGate 同源的计数口径（节点/网格/纹理/Accessor/动画），
// 另加纹理像素估算（尺寸可核验逐张累计；不可核验记 compressedBytes 走兜底）。
export function computeStructuralStats(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const json = readGlbJsonForEstimate(view);
  const nodes = Array.isArray(json.nodes) ? json.nodes : [];
  const meshes = Array.isArray(json.meshes) ? json.meshes : [];
  const accessors = Array.isArray(json.accessors) ? json.accessors : [];
  const animations = Array.isArray(json.animations) ? json.animations : [];
  const images = Array.isArray(json.images) ? json.images : [];
  const skins = Array.isArray(json.skins) ? json.skins : [];
  const bufferViews = Array.isArray(json.bufferViews) ? json.bufferViews : [];

  let primitiveCount = 0;
  let vertexCount = 0;
  let morphTargetBindings = 0;
  for (const mesh of meshes) {
    for (const primitive of Array.isArray(mesh?.primitives) ? mesh.primitives : []) {
      primitiveCount += 1;
      const positionIndex = primitive?.attributes?.POSITION;
      if (Number.isInteger(positionIndex) && positionIndex >= 0 && positionIndex < accessors.length) {
        const count = accessors[positionIndex]?.count;
        if (isNonNegativeInt(count)) vertexCount += count;
      }
      if (Array.isArray(primitive?.targets)) morphTargetBindings += primitive.targets.length;
    }
  }

  let keyframeCount = 0;
  for (const animation of animations) {
    for (const sampler of Array.isArray(animation?.samplers) ? animation.samplers : []) {
      const input = sampler?.input;
      if (Number.isInteger(input) && input >= 0 && input < accessors.length) {
        const count = accessors[input]?.count;
        if (isNonNegativeInt(count)) keyframeCount += count;
      }
    }
  }

  let jointCount = 0;
  for (const skin of skins) jointCount += countArray(skin?.joints);

  // 纹理像素：优先从 PNG/JPEG 头逐张核验；无法核验的按压缩字节累计兜底。
  const binChunk = locateBinChunk(view);
  let texturePixels = 0;
  let unverifiableTextureBytes = 0;
  let textureBytesVerified = true;
  for (const image of images) {
    const viewIndex = image?.bufferView;
    const bufferView = Number.isInteger(viewIndex) && viewIndex >= 0 && viewIndex < bufferViews.length
      ? bufferViews[viewIndex]
      : null;
    const start = binChunk && bufferView ? binChunk.start + (bufferView.byteOffset ?? 0) : 0;
    const end = start + (bufferView && isNonNegativeInt(bufferView.byteLength) ? bufferView.byteLength : 0);
    const pixels =
      typeof image?.uri === "string" || bufferView === null || binChunk === null || end > view.byteLength
        ? null
        : sniffImagePixels(view, start, end, image.mimeType);
    if (pixels === null) {
      textureBytesVerified = false;
      unverifiableTextureBytes += bufferView && isNonNegativeInt(bufferView.byteLength) ? bufferView.byteLength : 0;
    } else {
      texturePixels += pixels;
    }
  }

  return deepFreeze({
    byteLength: view.byteLength,
    nodeCount: nodes.length,
    meshCount: meshes.length,
    primitiveCount,
    vertexCount,
    accessorCount: accessors.length,
    textureCount: images.length,
    texturePixels,
    textureBytesVerified,
    unverifiableTextureBytes,
    animationCount: animations.length,
    keyframeCount,
    morphTargetBindings,
    skinCount: skins.length,
    jointCount,
  });
}

// §11.1 单模型估算明细：B（文件字节）、k×B（跨边界副本）、A/T/M 分量与模型自身驻留量。
// 输出全部可诊断：每个分量可回溯到结构统计与参数。
export function estimateModelResources(stats, options = {}) {
  const params = validateResourceEstimateParams(options.params ?? DEFAULT_RESOURCE_ESTIMATE_PARAMS);
  if (stats === null || typeof stats !== "object" || !Number.isInteger(stats.byteLength) || stats.byteLength <= 0) {
    throw new ResourceEstimatorError("stats_invalid", "estimateModelResources 需要 computeStructuralStats 的结构统计");
  }
  const bBytes = stats.byteLength;
  const copyBytes = params.kCopyFactor * bBytes;
  const aBytes =
    stats.vertexCount * params.geometryBytesPerVertex +
    stats.accessorCount * params.accessorOverheadBytes +
    stats.nodeCount * params.nodeOverheadBytes;
  const verifiedTextureBytes = stats.textureBytesVerified
    ? stats.texturePixels * params.textureBytesPerPixel * (params.mipmapNumerator / params.mipmapDenominator)
    : 0;
  const fallbackTextureBytes = stats.textureBytesVerified
    ? 0
    : (stats.texturePixels * params.textureBytesPerPixel * (params.mipmapNumerator / params.mipmapDenominator)) +
      stats.unverifiableTextureBytes * params.unverifiableTextureFactor;
  const tBytes = Math.ceil(verifiedTextureBytes + fallbackTextureBytes);
  const mBytes =
    stats.morphTargetBindings * params.morphBytesPerBinding +
    stats.jointCount * params.skinBytesPerJoint +
    stats.keyframeCount * params.animationBytesPerKeyframe;
  // 模型自身驻留量（不含 Mbase/F/ε——那些在切换预算判定时只计一次）。
  const modelFootprintBytes = copyBytes + aBytes + tBytes + mBytes;
  return deepFreeze({
    schemaVersion: RESOURCE_ESTIMATOR_SCHEMA_VERSION,
    bBytes,
    kFactor: params.kCopyFactor,
    copyBytes,
    aBytes,
    tBytes,
    mBytes,
    textureBytesVerified: stats.textureBytesVerified,
    modelFootprintBytes,
    stats,
  });
}

// §4.8/§11.2 切换预算判定：predictedPeak <= safetyFactor × resourceBudget 才允许
// 保留旧模型的事务切换（双驻留）；否则走 §11.3 低资源切换路径。
// Mpeak = Mbase + k×Bnew + Anew + Tnew + Mnew + Aold + Told + Mold + F + ε
export function evaluateSwitchBudget({ candidate, resident = null, resourceBudgetBytes, params: overrideParams = null }) {
  const params = validateResourceEstimateParams(overrideParams ?? DEFAULT_RESOURCE_ESTIMATE_PARAMS);
  if (candidate === null || typeof candidate !== "object" || !Number.isFinite(candidate.modelFootprintBytes)) {
    throw new ResourceEstimatorError("estimate_invalid", "evaluateSwitchBudget 需要 candidate 估算明细");
  }
  if (!Number.isFinite(resourceBudgetBytes) || resourceBudgetBytes <= 0) {
    throw new ResourceEstimatorError("budget_invalid", "resourceBudgetBytes 必须为正数");
  }
  const residentFootprint = resident === null
    ? 0
    : (resident.aBytes ?? 0) + (resident.tBytes ?? 0) + (resident.mBytes ?? 0);
  const predictedPeakBytes =
    params.baseResidentBytes +
    candidate.modelFootprintBytes +
    residentFootprint +
    params.framebufferBytes +
    params.epsilonBytes;
  const effectiveLimitBytes = params.safetyFactor * resourceBudgetBytes;
  const allowed = predictedPeakBytes <= effectiveLimitBytes;
  return deepFreeze({
    schemaVersion: RESOURCE_ESTIMATOR_SCHEMA_VERSION,
    allowed,
    mode: allowed ? "transactional" : "low-resource",
    predictedPeakBytes,
    resourceBudgetBytes,
    safetyFactor: params.safetyFactor,
    effectiveLimitBytes,
    marginBytes: effectiveLimitBytes - predictedPeakBytes,
    residentIncluded: resident !== null,
    components: {
      baseResidentBytes: params.baseResidentBytes,
      candidateFootprintBytes: candidate.modelFootprintBytes,
      residentFootprintBytes: residentFootprint,
      framebufferBytes: params.framebufferBytes,
      epsilonBytes: params.epsilonBytes,
    },
    reason: allowed ? "predicted-peak-within-budget" : "predicted-peak-exceeds-budget",
  });
}
