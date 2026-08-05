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

test("legacy driver: 自然站姿把 T-pose 手臂放下（rightUpperArm.z≈-1.185 / left≈1.160）", () => {
  const { driver, bones } = makeDriver();
  driver.update(0.1, { gestureActive: false });
  assert.ok(Math.abs(bones.get("rightUpperArm").rotation.z + 1.185) < 0.02, "右手臂应下垂");
  assert.ok(Math.abs(bones.get("leftUpperArm").rotation.z - 1.160) < 0.02, "左手臂应下垂");
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
  assert.equal(LEGACY_PERFORMANCE_DRIVER_VERSION, "legacy-performance-driver-1.0.0");
  assert.equal(EMOTION_KEYS.length, 7);
  assert.equal(VRMA_GESTURE_KEYS.length, 7);
});
