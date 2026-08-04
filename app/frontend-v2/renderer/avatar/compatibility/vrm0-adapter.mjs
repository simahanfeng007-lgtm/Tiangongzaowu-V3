// VRM 0.x 兼容适配器（方案 §12.2/§12.3）。
// 版本识别只读 glTF JSON 的 extensions，禁止按文件名/模型名判断（§12.2）。
// 结构分析（analyzeVrm0GltfJson）为纯函数，可在 Node 端对真实字节跑 headless 结构级验证。

import { VrmSpecVersion } from "../engines/avatar-engine-contract.mjs";
import { extractLicenseRecord } from "../model-license-gate.mjs";

// §12.2 版本识别：VRMC_vrm 存在即 1.0；否则 extensions.VRM 存在即 0.x；都没有则不是 VRM。
export function detectVrmSpecVersion(gltfJson) {
  if (gltfJson === null || typeof gltfJson !== "object" || Array.isArray(gltfJson)) return null;
  const extensions = gltfJson.extensions;
  if (extensions === null || typeof extensions !== "object") return null;
  if (extensions.VRMC_vrm !== undefined) return VrmSpecVersion.VRM1;
  if (extensions.VRM !== undefined) return VrmSpecVersion.VRM0;
  return null;
}

function countArray(value) {
  return Array.isArray(value) ? value.length : 0;
}

// 0.x BlendShape → 统一 Expression 语义别名表（§12.3.2）。
// 与提取前 桌面宠物.html 的 EXPRESSION_MAP 逐项一致（行为 parity）。
export const VRM0_EXPRESSION_ALIASES = Object.freeze({
  happy: Object.freeze(["happy", "joy", "smile", "fun", "Fcl_ALL_Joy", "Fcl_Joy"]),
  sad: Object.freeze(["sad", "sorrow", "Fcl_ALL_Sorrow", "Fcl_Sorrow"]),
  angry: Object.freeze(["angry", "anger", "Fcl_ALL_Angry", "Fcl_Angry"]),
  surprised: Object.freeze(["surprised", "surprise", "Fcl_ALL_Surprised", "Fcl_Surprised"]),
  relaxed: Object.freeze(["relaxed", "neutral", "soft", "Fcl_ALL_Neutral"]),
});

// 统一 Expression 语义查询：返回某统一语义名在 0.x 下的候选别名列表。
export function vrm0ExpressionAliases(semanticName) {
  return VRM0_EXPRESSION_ALIASES[semanticName] ?? [];
}

// 结构级分析：只读 glTF JSON，不触碰 three.js 对象。
export function analyzeVrm0GltfJson(gltfJson) {
  if (detectVrmSpecVersion(gltfJson) !== VrmSpecVersion.VRM0) {
    throw new Error("analyzeVrm0GltfJson 需要 extensions.VRM 标记的 0.x 模型");
  }
  const vrmExtension = gltfJson.extensions.VRM;
  const meta = vrmExtension?.meta ?? null;
  const humanBones = countArray(vrmExtension?.humanoid?.humanBones);
  const blendShapeGroups = countArray(vrmExtension?.blendShapeMaster?.blendShapeGroups);
  const springBoneGroups = countArray(vrmExtension?.secondaryAnimation?.boneGroups);
  return Object.freeze({
    kind: "vrm0",
    specVersion: VrmSpecVersion.VRM0,
    nodeCount: countArray(gltfJson.nodes),
    meshCount: countArray(gltfJson.meshes),
    skinCount: countArray(gltfJson.skins),
    materialCount: countArray(gltfJson.materials),
    textureCount: countArray(gltfJson.textures),
    humanBoneCount: humanBones,
    blendShapeGroupCount: blendShapeGroups,
    springBoneGroupCount: springBoneGroups,
    meta,
  });
}

// 0.x meta → 统一 LicenseRecord 投影（§12.3.5/§10.1）：
// commercialUssageName → commercialUsage 等映射在 extractLicenseRecord 内完成，rawMeta 保留原拼写。
export function projectVrm0LicenseRecord(meta) {
  return extractLicenseRecord({ vrmSpecVersion: VrmSpecVersion.VRM0, meta });
}

// §12.3.1 正前方归一化：VRMUtils.rotateVRM0 把 0.x 模型旋转到与 1.0 一致朝向。
// 默认不启用——独立桌宠页的历史行为由业务层 yaw（portraitYawForVRMLabel）承担，
// 引擎默认保持 parity；需要 0.x/1.0 统一朝向的新调用方显式传 normalizeForward:true。
export function normalizeVrm0Forward(vrm, { VRMUtils }) {
  if (vrm === null || typeof vrm !== "object") throw new Error("normalizeVrm0Forward 需要 VRM 实例");
  VRMUtils.rotateVRM0(vrm);
  return true;
}

// §12.3 运行时适配：SpringBone/LookAt 由 three-vrm 3.x 统一接管，此处负责
// 可选的朝向归一化、可选的几何清理（VRMUtils 清理）与 LookAt 四元数代理安装（幂等）。
export function adaptVrm0Runtime(vrm, deps = {}) {
  const { VRMUtils, lookAtProxyClass, normalizeForward = false, cleanup = false } = deps;
  if (vrm === null || typeof vrm !== "object" || vrm.scene === null || typeof vrm.scene !== "object") {
    throw new Error("adaptVrm0Runtime 需要带 scene 的 VRM 实例");
  }
  let normalizedForward = false;
  let cleaned = false;
  if (normalizeForward) {
    normalizeVrm0Forward(vrm, { VRMUtils });
    normalizedForward = true;
  }
  if (cleanup) {
    // VRMUtils 清理：剔除无蒙皮顶点/关节引用（three-vrm 3.5.5 已无 combineVRMMaterials）。
    VRMUtils.removeUnnecessaryVertices(vrm.scene);
    VRMUtils.removeUnnecessaryJoints(vrm.scene);
    cleaned = true;
  }
  // LookAt 兼容（§12.3.3）：安装四元数代理，避免 lookAt 与动画旋转互相覆盖。幂等。
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
    kind: "vrm0",
    specVersion: VrmSpecVersion.VRM0,
    normalizedForward,
    cleaned,
    lookAtProxyInstalled,
  });
}
