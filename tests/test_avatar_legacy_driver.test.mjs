// Legacy 表现驱动测试：自然站姿/手势/表情/口型/说话能量的行为 parity。

import test from "node:test";
import assert from "node:assert/strict";

import {
  createLegacyPerformanceDriver,
  EMOTION_KEYS,
  LEGACY_PERFORMANCE_DRIVER_VERSION,
  VRMA_GESTURE_KEYS,
} from "../app/frontend-v2/renderer/avatar/engines/legacy-performance-driver.mjs";

function makeBone() {
  return { rotation: { x: 0, y: 0, z: 0 } };
}

function makeVrm() {
  const names = [
    "hips", "spine", "chest", "neck", "head",
    "rightUpperArm", "leftUpperArm", "rightLowerArm", "leftLowerArm",
    "rightHand", "leftHand",
  ];
  const bones = new Map(names.map((name) => [name, makeBone()]));
  const humanoid = {
    getNormalizedBoneNode: (name) => bones.get(name) ?? null,
    resetNormalizedPose() {
      for (const bone of bones.values()) {
        bone.rotation.x = 0;
        bone.rotation.y = 0;
        bone.rotation.z = 0;
      }
    },
  };
  const expressionMap = {
    happy: true, angry: true, sad: true, relaxed: true, surprised: true,
    blink: true, aa: true, ih: true, ou: true, ee: true, oh: true,
  };
  const expressionManager = {
    expressionMap,
    setValue() {},
  };
  const scene = { traverse() {} };
  return { vrm: { humanoid, expressionManager, scene }, bones };
}

function makeDriver() {
  const captured = {};
  const { vrm, bones } = makeVrm();
  const applyExpression = (name, value) => {
    captured[name] = value;
    return { matched: true, availableKeys: Object.keys(vrm.expressionManager.expressionMap), tried: name };
  };
  const mapViseme = () => "aa";
  const driver = createLegacyPerformanceDriver({ vrm, applyExpression, mapViseme });
  return { driver, bones, captured };
}

test("legacy driver: 自然站姿为生物学中立位（JOSR 2022 标定：外展13.7°/肘屈15.5°/旋前90.2°映射）", () => {
  const { driver, bones } = makeDriver();
  driver.update(0.1, { gestureActive: false });
  // 上臂：外展 7.5°（z 基准 -1.4399），左臂按镜像取反
  assert.ok(Math.abs(bones.get("rightUpperArm").rotation.z + 1.4399) < 0.03, `右上臂应处于外展中立位，实际 ${bones.get("rightUpperArm").rotation.z}`);
  assert.ok(Math.abs(bones.get("leftUpperArm").rotation.z - 1.4399) < 0.03, `左上臂应镜像外展中立位，实际 ${bones.get("leftUpperArm").rotation.z}`);
  // 前臂：前屈映射（0.400, 0.300, 0.100），手在肩平面前方
  assert.ok(Math.abs(bones.get("rightLowerArm").rotation.x - 0.400) < 0.05, `右前臂 x 应 ≈0.40，实际 ${bones.get("rightLowerArm").rotation.x}`);
  assert.ok(Math.abs(bones.get("rightLowerArm").rotation.y - 0.300) < 0.05, `右前臂 y 应 ≈0.30，实际 ${bones.get("rightLowerArm").rotation.y}`);
  assert.ok(Math.abs(bones.get("rightLowerArm").rotation.z - 0.100) < 0.05, `右前臂 z 应 ≈0.10，实际 ${bones.get("rightLowerArm").rotation.z}`);
  // 手腕：卷曲方向标定（0.550, 0.050, -0.050），手背朝外、掌心朝内贴大腿
  assert.ok(Math.abs(bones.get("rightHand").rotation.x - 0.550) < 0.05, `右手腕 x 应 ≈0.55，实际 ${bones.get("rightHand").rotation.x}`);
  assert.ok(Math.abs(bones.get("rightHand").rotation.y - 0.050) < 0.05, `右手腕 y 应 ≈0.05，实际 ${bones.get("rightHand").rotation.y}`);
  assert.ok(Math.abs(bones.get("rightHand").rotation.z + 0.050) < 0.05, `右手腕 z 应 ≈-0.05，实际 ${bones.get("rightHand").rotation.z}`);
  // 左右镜像约定：X 同号、Y/Z 反号（leftcheck 实测左右手外侧位移 0.13、手掌/手指朝向对称）
  assert.ok(Math.abs(bones.get("leftUpperArm").rotation.x - bones.get("rightUpperArm").rotation.x) < 0.01, "左右上臂 X 应同号");
  assert.ok(Math.abs(bones.get("leftUpperArm").rotation.y + bones.get("rightUpperArm").rotation.y) < 0.02, "左右上臂 Y 应反号");
  assert.ok(Math.abs(bones.get("leftUpperArm").rotation.z + bones.get("rightUpperArm").rotation.z) < 0.02, "左右上臂 Z 应反号");
  assert.equal(typeof bones.get("head").rotation.x, "number");
  assert.equal(typeof bones.get("hips").rotation.x, "number");
});

test("legacy driver: VRMA 动作播放时自然站姿让位（骨骼不被驱动覆盖）", () => {
  const { driver, bones } = makeDriver();
  driver.update(0.1, { gestureActive: true });
  assert.equal(bones.get("rightUpperArm").rotation.z, 0);
  assert.equal(bones.get("leftUpperArm").rotation.z, 0);
});

test("legacy driver: applyBodyPerformance 语义白名单与默认回退", () => {
  const { driver } = makeDriver();
  const good = driver.applyBodyPerformance({
    gesture: "greet_wave",
    expression: "happy",
    intensity: 0.8,
    duration: 3,
    source: "llm",
  });
  assert.equal(good.gesture, "greet_wave");
  assert.equal(good.expression, "happy");
  assert.equal(good.intensity, 0.8);
  assert.equal(good.duration, 3);

  const bad = driver.applyBodyPerformance({ gesture: "bogus", expression: "weird" });
  assert.equal(bad.gesture, "co_speech");
  assert.equal(bad.expression, "soft");
});

test("legacy driver: 说话计划驱动口型（markTalking 后 aa>0）", () => {
  const { driver, captured } = makeDriver();
  driver.markTalking("你好呀");
  for (let i = 0; i < 8; i += 1) driver.update(0.04, { gestureActive: false });
  assert.ok(captured.aa > 0, `口型 aa 应 >0，实际 ${captured.aa}`);
  const snapshot = driver.snapshot();
  assert.ok(snapshot.talkUntil > snapshot.idleTime);
});

test("legacy driver: speech-energy 无文字时兜底口型", () => {
  const { driver, captured } = makeDriver();
  driver.setSpeaking(true);
  driver.setSpeechEnergy(0.8);
  driver.update(0.1, { gestureActive: false });
  assert.ok(captured.aa > 0.05, `能量口型 aa 应 >0.05，实际 ${captured.aa}`);
});

test("legacy driver: qinggan 情绪驱动表情（joy→happy）", () => {
  const { driver, captured } = makeDriver();
  driver.setQinggan({ joy: 1 });
  for (let i = 0; i < 12; i += 1) driver.update(0.05, { gestureActive: false });
  assert.ok(captured.happy > 0.1, `happy 应 >0.1，实际 ${captured.happy}`);
});

test("legacy driver: 版本与常量导出稳定", () => {
  assert.equal(LEGACY_PERFORMANCE_DRIVER_VERSION, "legacy-performance-driver-1.1.0");
  assert.equal(EMOTION_KEYS.length, 7);
  assert.equal(VRMA_GESTURE_KEYS.length, 7);
});
