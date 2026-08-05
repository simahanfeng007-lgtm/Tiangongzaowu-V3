// 肩宽标定（shoulder-calibrator）测试：
// AvatarSample_A 窄肩模型应重建为 VRoid 标准女性体型（±0.0224 / ±0.0862），
// 宽肩/缺骨/VRM1 模型必须原样返回；GLB 重建后 BIN chunk 与总长度保持一致。

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

import {
  NARROW_SHOULDER_SPAN_THRESHOLD,
  VRM0_STANDARD_FEMALE_SHOULDER_X,
  VRM0_STANDARD_FEMALE_UPPER_ARM_X,
  analyzeShoulderSpan,
  calibrateVrm0FemaleShoulderWidth,
} from "../app/frontend-v2/renderer/avatar/engines/shoulder-calibrator.mjs";
import { sniffGltfJsonBytes } from "../app/frontend-v2/renderer/avatar/engines/three-vrm-engine.mjs";

const FIXTURE_A_URL = new URL("../app/assets/avatars/imported/AvatarSample_A.vrm", import.meta.url);
const HAS_FIXTURE_A = existsSync(FIXTURE_A_URL);

function buildGlbWithJson(json, binBytes = null) {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const jsonChunkLength = jsonBytes.length + pad;
  const bin = binBytes ? binBytes : new Uint8Array(0);
  const binChunkLength = bin.length;
  const total = 12 + 8 + jsonChunkLength + (bin.length > 0 ? 8 + binChunkLength : 0);
  const buffer = new ArrayBuffer(total);
  const view = new DataView(buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonChunkLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(buffer).set(jsonBytes, 20);
  for (let i = 0; i < pad; i += 1) view.setUint8(20 + jsonBytes.length + i, 0x20);
  if (bin.length > 0) {
    const off = 20 + jsonChunkLength;
    view.setUint32(off, binChunkLength, true);
    view.setUint32(off + 4, 0x004e4942, true);
    new Uint8Array(buffer).set(bin, off + 8);
  }
  return buffer;
}

function makeVrm0NarrowModel(span = 0.16) {
  const per = span / 4;
  return buildGlbWithJson({
    asset: { version: "2.0" },
    nodes: [
      { name: "root" },
      { name: "J_Bip_L_Shoulder", translation: [-per, 0.1, 0] },
      { name: "J_Bip_R_Shoulder", translation: [per, 0.1, 0] },
      { name: "J_Bip_L_UpperArm", translation: [-per, 0, 0] },
      { name: "J_Bip_R_UpperArm", translation: [per, 0, 0] },
    ],
    meshes: [],
    extensions: {
      VRM: {
        specVersion: "0.0",
        meta: { title: "narrow", author: "test", commercialUssageName: "Allow" },
        humanoid: {
          humanBones: [
            { bone: "leftShoulder", node: 1, useDefaultValues: true },
            { bone: "rightShoulder", node: 2, useDefaultValues: true },
            { bone: "leftUpperArm", node: 3, useDefaultValues: true },
            { bone: "rightUpperArm", node: 4, useDefaultValues: true },
          ],
        },
      },
    },
  });
}

test("shoulder-calibrator: 窄肩 VRM0 模型重建为标准女性肩宽且 BIN 无损", () => {
  const binBytes = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  const input = makeVrm0NarrowModel(0.16);
  // 手工重建带 BIN chunk 的 GLB，验证 BIN 逐字节保留。
  const gltfJson = sniffGltfJsonBytes(input).json;
  const withBinBuf = buildGlbWithJson(gltfJson, binBytes);
  const before = analyzeShoulderSpan(withBinBuf);
  assert.equal(before.span, 0.16);

  const out = calibrateVrm0FemaleShoulderWidth(withBinBuf);
  assert.notEqual(out, withBinBuf, "窄肩模型必须返回重建后的新缓冲");
  const after = analyzeShoulderSpan(out);
  assert.equal(after.span, +(2 * VRM0_STANDARD_FEMALE_SHOULDER_X + 2 * VRM0_STANDARD_FEMALE_UPPER_ARM_X).toFixed(4));
  assert.equal(after.bones.leftShoulder, -VRM0_STANDARD_FEMALE_SHOULDER_X);
  assert.equal(after.bones.rightShoulder, VRM0_STANDARD_FEMALE_SHOULDER_X);
  assert.equal(after.bones.leftUpperArm, -VRM0_STANDARD_FEMALE_UPPER_ARM_X);
  assert.equal(after.bones.rightUpperArm, VRM0_STANDARD_FEMALE_UPPER_ARM_X);

  // GLB 头部总长度与实际一致；JSON chunk 可再次解析；BIN chunk 字节原样保留。
  const outView = new Uint8Array(out);
  const dv = new DataView(out);
  assert.equal(dv.getUint32(0, true), 0x46546c67);
  assert.equal(dv.getUint32(8, true), out.byteLength);
  assert.equal(dv.getUint32(16, true), 0x4e4f534a);
  const jsonLen = dv.getUint32(12, true);
  const binStart = 20 + jsonLen;
  assert.equal(dv.getUint32(binStart + 4, true), 0x004e4942);
  const binOut = outView.subarray(binStart + 8, binStart + 8 + binBytes.length);
  assert.deepEqual([...binOut], [...binBytes], "BIN chunk 必须逐字节保留");

  // 幂等：已标定的缓冲再次标定返回同一引用。
  assert.equal(calibrateVrm0FemaleShoulderWidth(out), out, "二次标定必须幂等");
});

test("shoulder-calibrator: 宽肩模型原样返回（不覆盖男性/宽肩女性设定）", () => {
  const input = makeVrm0NarrowModel(0.26);
  assert.equal(calibrateVrm0FemaleShoulderWidth(input), input);
  const analysis = analyzeShoulderSpan(input);
  assert.ok(analysis.span >= NARROW_SHOULDER_SPAN_THRESHOLD);
});

test("shoulder-calibrator: 缺骨/无 VRM0/非 GLB 输入原样返回", () => {
  const missingBones = buildGlbWithJson({
    asset: { version: "2.0" },
    nodes: [{ name: "root" }],
    meshes: [],
    extensions: { VRM: { specVersion: "0.0", humanoid: { humanBones: [] } } },
  });
  assert.equal(calibrateVrm0FemaleShoulderWidth(missingBones), missingBones);

  const vrm1 = buildGlbWithJson({
    asset: { version: "2.0" },
    nodes: [{ name: "root" }],
    meshes: [],
    extensions: { VRMC_vrm: { specVersion: "1.0", humanoid: { humanBones: {} } } },
  });
  assert.equal(calibrateVrm0FemaleShoulderWidth(vrm1), vrm1);

  const notGlb = new Uint8Array([0xde, 0xad, 0xbe, 0xef]);
  assert.equal(calibrateVrm0FemaleShoulderWidth(notGlb), notGlb);
});

test(
  "shoulder-calibrator: AvatarSample_A 实测跨距 0.164 → 标定后 0.217（VRoid 标准女性）",
  { skip: HAS_FIXTURE_A ? false : "缺少 AvatarSample_A.vrm fixture" },
  () => {
    const bytes = readFileSync(FIXTURE_A_URL);
    const before = analyzeShoulderSpan(bytes);
    assert.equal(before.span, 0.1645);
    const out = calibrateVrm0FemaleShoulderWidth(bytes);
    assert.notEqual(out, bytes);
    const after = analyzeShoulderSpan(out);
    assert.equal(after.span, 0.2172);
    assert.equal(after.bones.leftShoulder, -0.0224);
    assert.equal(after.bones.rightUpperArm, 0.0862);
    // 重建后仍能被引擎 sniff 解析为合法 GLB。
    const sniffed = sniffGltfJsonBytes(out);
    assert.equal(sniffed.container, "glb");
    assert.equal(sniffed.json.nodes.length, sniffGltfJsonBytes(bytes).json.nodes.length);
  },
);
