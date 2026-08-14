import test from "node:test";
import assert from "node:assert/strict";

import { createGestureProsodyPlan, gestureMotionAt } from "../app/frontend-v2/renderer/avatar/gesture-prosody-planner.mjs";
import {
  adaptiveFramingForConversationState,
  createHumanIdleDynamics,
  createSocialBlinkController,
} from "../app/frontend-v2/renderer/avatar/human-idle-dynamics.mjs";
import { createSpeechMotorPlan } from "../app/frontend-v2/renderer/avatar/speech-motor-plan.mjs";
import { createLegacyPerformanceDriver } from "../app/frontend-v2/renderer/avatar/engines/legacy-performance-driver.mjs";

function fixedRandom(value = 0.5) {
  return () => value;
}

function makeDriver() {
  const boneNames = [
    "hips", "spine", "chest", "neck", "head",
    "rightUpperArm", "leftUpperArm", "rightLowerArm", "leftLowerArm",
    "rightHand", "leftHand",
  ];
  const bones = new Map(boneNames.map((name) => [name, { rotation: { x: 0, y: 0, z: 0 } }]));
  const tailBone = { isBone: true, name: "Tail01", rotation: { x: 0, y: 0, z: 0, clone() { return { x: this.x, y: this.y, z: this.z }; } } };
  const vrm = {
    humanoid: {
      getNormalizedBoneNode: (name) => bones.get(name) ?? null,
      resetNormalizedPose() {},
    },
    expressionManager: { expressionMap: { blink: true, relaxed: true, aa: true, ih: true, ou: true, ee: true, oh: true } },
    scene: { traverse(callback) { callback(tailBone); } },
  };
  const driver = createLegacyPerformanceDriver({
    vrm,
    random: fixedRandom(),
    applyExpression: () => true,
    mapViseme: () => "aa",
  });
  return { driver, bones, tailBone };
}

test("H4: gesture stroke aligns with lexical prominence instead of speech start", () => {
  const text = "这里真正的问题，不是模型，而是运行时。";
  const speechPlan = createSpeechMotorPlan(text, { durationMs: 2400 });
  const plan = createGestureProsodyPlan({ text, speechPlan, gesture: "co_speech" });
  assert.ok(plan);
  assert.equal(plan.items.length, 1, "generic beats are sparse");
  const item = plan.items[0];
  assert.ok(item.strokeAtMs > 0);
  assert.ok(item.startMs < item.strokeAtMs);
  assert.ok(item.endMs > item.strokeAtMs);
  assert.equal(gestureMotionAt(plan, item.strokeAtMs).envelope, 1);
});

test("H4: generic gesture without prominence is suppressed", () => {
  const text = "我已经收到你的消息。";
  assert.equal(createGestureProsodyPlan({ text, speechPlan: createSpeechMotorPlan(text), gesture: "co_speech" }), null);
});

test("H4: driver waits for speech timing and records one aligned stroke", () => {
  const { driver } = makeDriver();
  const text = "真正需要处理的重点是运行时。";
  const speechPlan = createSpeechMotorPlan(text, { durationMs: 1800 });
  driver.applyBodyPerformance({ channel: "gesture", gesture: "co_speech", duration: 4, intensity: 0.6 });
  driver.setSpeaking(true);
  driver.setSpeechPlan(speechPlan, text);
  assert.ok(driver.snapshot().gestureProsody);
  for (let i = 0; i < 35; i += 1) driver.update(0.05, { gestureActive: false });
  assert.ok(Number.isFinite(driver.snapshot().gestureLastStrokeAt));
});

test("H5: idle variation retargets at event level and remains smoothed", () => {
  const idle = createHumanIdleDynamics({ random: fixedRandom(0.75) });
  const first = idle.update(0.1, "IDLE");
  const later = idle.update(0.1, "IDLE");
  assert.ok(Math.abs(first.shift) > 0);
  assert.ok(Math.abs(later.shift) > Math.abs(first.shift));
  assert.ok(Math.abs(later.shift - first.shift) < 0.1, "no per-frame random jump");
});

test("H5: social blink favors speech boundaries and suppresses attentive-listening hazard", () => {
  const blink = createSocialBlinkController({ random: fixedRandom(0.5) });
  assert.equal(blink.noteBoundary({ text: "这是重点。", charIndex: 4, boundaryType: "sentence" }), true);
  for (let i = 0; i < 6; i += 1) blink.update(0.05, "SPEAKING");
  assert.equal(blink.snapshot().count, 1);
  assert.equal(blink.snapshot().lastReason, "speech-boundary");

  const listenerBlink = createSocialBlinkController({ random: fixedRandom(0.5) });
  for (let i = 0; i < 60; i += 1) listenerBlink.update(0.1, "LISTENING");
  assert.equal(listenerBlink.snapshot().count, 0);
  for (let i = 0; i < 20; i += 1) listenerBlink.update(0.1, "LISTENING");
  assert.equal(listenerBlink.snapshot().count, 1, "physiological ceiling still permits a blink");
});

test("H5: tail state changes use inertia and adaptive framing remains subtle", () => {
  const { driver, tailBone } = makeDriver();
  driver.applyBodyPerformance({ channel: "tail", tail: "happy", duration: 3, intensity: 0.8 });
  driver.update(0.05, { gestureActive: false });
  const snapshot = driver.snapshot();
  assert.ok(snapshot.tailDynamics.amplitude > 0.72 && snapshot.tailDynamics.amplitude < 1.35);
  assert.ok(Math.abs(tailBone.rotation.y) < 0.02, "spring inertia prevents an instantaneous tail snap");
  const idle = adaptiveFramingForConversationState("IDLE");
  const speaking = adaptiveFramingForConversationState("SPEAKING");
  assert.ok(Math.abs(speaking.distance - idle.distance) < 0.12);
  assert.ok(Math.abs(speaking.focus) < 0.03);
});
