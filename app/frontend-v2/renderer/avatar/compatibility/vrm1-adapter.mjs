// VRM 1.0 兼容适配器（方案 §12.2/§12.4）。
// 覆盖 VRMC_vrm 识别、结构分析、meta → LicenseRecord 投影与 LookAt 兼容；
// VRMC_springBone / VRMC_node_constraint / Expression override 为占位接口（§12.4），
// three-vrm 3.5.5 已在加载层处理 1.0 坐标与 T-Pose 规则，适配器不重复实现。

import { VrmSpecVersion } from "../engines/avatar-engine-contract.mjs";
import { extractLicenseRecord } from "../model-license-gate.mjs";

function countArray(value) {
  return Array.isArray(value) ? value.length : 0;
}

// 1.0 统一 Expression 语义：VRMC_vrm 规定 preset 名，统一语义名即 preset 名本身。
export const VRM1_EXPRESSION_PRESETS = Object.freeze([
  "happy",
  "angry",
  "sad",
  "relaxed",
  "surprised",
  "neutral",
  "aa",
  "ih",
  "ou",
  "ee",
  "oh",
  "blink",
  "lookUp",
  "lookDown",
  "lookLeft",
  "lookRight",
]);

export function vrm1ExpressionAliases(semanticName) {
  // 1.0 preset 语义统一，候选即语义名本身。
  return VRM1_EXPRESSION_PRESETS.includes(semanticName) ? [semanticName] : [];
}

export function analyzeVrm1GltfJson(gltfJson) {
  const extensions = gltfJson?.extensions;
  if (extensions === null || typeof extensions !== "object" || extensions.VRMC_vrm === undefined) {
    throw new Error("analyzeVrm1GltfJson 需要 extensions.VRMC_vrm 标记的 1.0 模型");
  }
  const vrmExtension = extensions.VRMC_vrm;
  const meta = vrmExtension?.meta ?? null;
  return Object.freeze({
    kind: "vrm1",
    specVersion: VrmSpecVersion.VRM1,
    nodeCount: countArray(gltfJson.nodes),
    meshCount: countArray(gltfJson.meshes),
    skinCount: countArray(gltfJson.skins),
    materialCount: countArray(gltfJson.materials),
    textureCount: countArray(gltfJson.textures),
    expressionCount: countArray(vrmExtension?.expressions?.preset !== undefined ? Object.keys(vrmExtension.expressions.preset) : []),
    hasSpringBoneExtension: extensions.VRMC_springBone !== undefined,
    hasNodeConstraintExtension: extensions.VRMC_node_constraint !== undefined,
    meta,
  });
}

// 1.0 meta → 统一 LicenseRecord 投影（§12.4/§10.1：authors/commercialUsage 等字段映射）。
export function projectVrm1LicenseRecord(meta) {
  return extractLicenseRecord({ vrmSpecVersion: VrmSpecVersion.VRM1, meta });
}

// 1.0 运行时适配：坐标与 T-Pose 规则由 three-vrm 3.5.5 加载层保证（§12.4.5），
// 此处只负责 LookAt 四元数代理安装（幂等）。
export function adaptVrm1Runtime(vrm, deps = {}) {
  const { lookAtProxyClass } = deps;
  if (vrm === null || typeof vrm !== "object" || vrm.scene === null || typeof vrm.scene !== "object") {
    throw new Error("adaptVrm1Runtime 需要带 scene 的 VRM 实例");
  }
  let lookAtProxyInstalled = false;
  if (vrm.lookAt && typeof lookAtProxyClass === "function") {
    const exists = vrm.scene.children.some((o) => o.name === "VRMLookAtQuaternionProxy");
    if (!exists) {
      const proxy = new lookAtProxyClass(vrm.lookAt);
      proxy.name = "VRMLookAtQuaternionProxy";
      vrm.scene.add(proxy);
    }
    lookAtProxyInstalled = true;
  }
  return Object.freeze({
    kind: "vrm1",
    specVersion: VrmSpecVersion.VRM1,
    lookAtProxyInstalled,
  });
}

// ── §12.4 占位接口（暂未实现，显式声明而非缺失）────────────────
function notImplemented(feature) {
  return Object.freeze({ implemented: false, feature, reason: "vrm1-adapter placeholder (§12.4)" });
}

// VRMC_springBone 占位：three-vrm 3.5.5 加载层已处理标准 springBone；
// 自定义覆盖配置留待后续版本实现。
export function configureVrm1SpringBone(_vrm, _config = {}) {
  return notImplemented("VRMC_springBone");
}

// VRMC_node_constraint 占位。
export function configureVrm1NodeConstraints(_vrm, _config = {}) {
  return notImplemented("VRMC_node_constraint");
}

// 1.0 Expression override 规则占位（§12.4.4）。
export function applyVrm1ExpressionOverrides(_vrm, _overrides = {}) {
  return notImplemented("VRMC_vrm_expression_override");
}
