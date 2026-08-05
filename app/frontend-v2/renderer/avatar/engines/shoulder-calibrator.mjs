// 肩宽标定（纯字节级，Node 可测；不触碰 three.js）。
//
// 依据：VRoid Studio 默认女性体型的骨骼设定（fem_vroid / Vita /
// HairSample_Female / Sendagaya_Shino 同款）：
//   J_Bip_L/R_Shoulder  local x = ±0.0224
//   J_Bip_L/R_UpperArm  local x = ±0.0862
//   → 盂肱关节世界跨距 ≈ 0.217m（占身高的正常女性比例）
// AvatarSample_A 只有 ±0.0201 / ±0.0622（世界跨距 ≈0.164m），比 VRoid 标准
// 女性窄约 24%，导致手臂像从胸口长出、肩部轮廓过窄。
//
// 原理：VRM 0.x 的骨骼平移量是 GLB JSON chunk 内联数组。重建 JSON chunk 后
// 蒙皮会跟随新关节位置，效果等价于 VRoid Studio“肩幅”滑块的骨骼位移，
// 且归一化骨骼/原始骨骼天然一致，无需重新 normalize。

const GLB_MAGIC = 0x46546c67; // "glTF"
const GLB_CHUNK_JSON = 0x4e4f534a; // "JSON"
const GLB_CHUNK_BIN = 0x004e4942; // "BIN"

// VRoid 标准女性体型目标值（±）。
export const VRM0_STANDARD_FEMALE_SHOULDER_X = 0.0224;
export const VRM0_STANDARD_FEMALE_UPPER_ARM_X = 0.0862;

// 小于该世界跨距视为“窄肩”，才执行加宽；男性/宽肩模型（masc 0.298、
// AvatarSample_C 0.257、fem 0.217）均不受影响。
export const NARROW_SHOULDER_SPAN_THRESHOLD = 0.19;

function readGlb(view) {
  if (view.byteLength < 12) return null;
  const header = new DataView(view.buffer, view.byteOffset, view.byteLength);
  if (header.getUint32(0, true) !== GLB_MAGIC) return null;
  const glbVersion = header.getUint32(4, true);
  const declaredLength = header.getUint32(8, true);
  if (declaredLength !== view.byteLength) return null;
  const jsonLength = header.getUint32(12, true);
  const jsonType = header.getUint32(16, true);
  if (jsonType !== GLB_CHUNK_JSON || 20 + jsonLength > view.byteLength) return null;
  let binStart = null;
  let binLength = 0;
  const afterJson = 20 + jsonLength;
  if (afterJson + 8 <= view.byteLength) {
    const binType = header.getUint32(afterJson + 4, true);
    if (binType === GLB_CHUNK_BIN) {
      binLength = header.getUint32(afterJson, true);
      binStart = afterJson + 8;
    }
  }
  return { glbVersion, jsonLength, binStart, binLength };
}

function vrm0HumanBoneMap(json) {
  const humanBones = json?.extensions?.VRM?.humanoid?.humanBones;
  if (!Array.isArray(humanBones)) return null;
  const map = {};
  for (const entry of humanBones) {
    if (entry && typeof entry.bone === "string" && Number.isInteger(entry.node)) {
      map[entry.bone] = entry.node;
    }
  }
  return map;
}

function nodeTranslation(json, nodeIndex) {
  const node = json.nodes?.[nodeIndex];
  if (!node || !Array.isArray(node.translation) || node.translation.length < 3) return null;
  const [x, y, z] = node.translation;
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return null;
  return { x, y, z, node };
}

// 分析当前盂肱关节世界跨距（肩骨 local x + 上臂骨 local x，两侧求和）。
export function analyzeShoulderSpan(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const glb = readGlb(view);
  if (!glb) return { span: null, reason: "not-glb" };
  let json;
  try {
    json = JSON.parse(new TextDecoder().decode(view.subarray(20, 20 + glb.jsonLength)));
  } catch (_) {
    return { span: null, reason: "json-parse-failed" };
  }
  const map = vrm0HumanBoneMap(json);
  if (!map) return { span: null, reason: "no-vrm0-humanoid" };
  const lShoulder = nodeTranslation(json, map.leftShoulder);
  const rShoulder = nodeTranslation(json, map.rightShoulder);
  const lUpper = nodeTranslation(json, map.leftUpperArm);
  const rUpper = nodeTranslation(json, map.rightUpperArm);
  if (!lShoulder || !rShoulder || !lUpper || !rUpper) {
    return { span: null, reason: "bone-translation-missing" };
  }
  const span = Math.abs(lShoulder.x) + Math.abs(lUpper.x) + Math.abs(rShoulder.x) + Math.abs(rUpper.x);
  return {
    span: +span.toFixed(4),
    bones: {
      leftShoulder: lShoulder.x,
      rightShoulder: rShoulder.x,
      leftUpperArm: lUpper.x,
      rightUpperArm: rUpper.x,
    },
    reason: null,
  };
}

function rebuildGlb(view, json, glb) {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const newJsonLength = jsonBytes.length + pad;
  const binLength = glb.binStart === null ? 0 : glb.binLength;
  const total = 12 + 8 + newJsonLength + (binLength > 0 ? 8 + binLength : 0);
  const out = new Uint8Array(total);
  const header = new DataView(out.buffer);
  header.setUint32(0, GLB_MAGIC, true);
  header.setUint32(4, glb.glbVersion, true);
  header.setUint32(8, total, true);
  header.setUint32(12, newJsonLength, true);
  header.setUint32(16, GLB_CHUNK_JSON, true);
  out.set(jsonBytes, 20);
  for (let i = 0; i < pad; i += 1) out[20 + jsonBytes.length + i] = 0x20;
  if (binLength > 0) {
    const binHeaderOffset = 20 + newJsonLength;
    header.setUint32(binHeaderOffset, binLength, true);
    header.setUint32(binHeaderOffset + 4, GLB_CHUNK_BIN, true);
    out.set(view.subarray(glb.binStart, glb.binStart + binLength), binHeaderOffset + 8);
  }
  return out.buffer;
}

// 返回新的 ArrayBuffer；无需调整时返回原引用。
export function calibrateVrm0FemaleShoulderWidth(bytes, options = {}) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const glb = readGlb(view);
  if (!glb) return bytes;
  let json;
  try {
    json = JSON.parse(new TextDecoder().decode(view.subarray(20, 20 + glb.jsonLength)));
  } catch (_) {
    return bytes;
  }
  const map = vrm0HumanBoneMap(json);
  if (!map) return bytes;
  const lShoulder = nodeTranslation(json, map.leftShoulder);
  const rShoulder = nodeTranslation(json, map.rightShoulder);
  const lUpper = nodeTranslation(json, map.leftUpperArm);
  const rUpper = nodeTranslation(json, map.rightUpperArm);
  if (!lShoulder || !rShoulder || !lUpper || !rUpper) return bytes;
  const span = Math.abs(lShoulder.x) + Math.abs(lUpper.x) + Math.abs(rShoulder.x) + Math.abs(rUpper.x);
  if (span >= NARROW_SHOULDER_SPAN_THRESHOLD) return bytes;

  const targetShoulder = options.shoulderX ?? VRM0_STANDARD_FEMALE_SHOULDER_X;
  const targetUpperArm = options.upperArmX ?? VRM0_STANDARD_FEMALE_UPPER_ARM_X;
  lShoulder.node.translation[0] = -targetShoulder;
  rShoulder.node.translation[0] = targetShoulder;
  lUpper.node.translation[0] = -targetUpperArm;
  rUpper.node.translation[0] = targetUpperArm;
  return rebuildGlb(view, json, glb);
}
