import test from "node:test";
import assert from "node:assert/strict";

import {
  createBodyCommandScheduler,
  splitBodyActionChannels,
} from "../app/frontend-v2/renderer/avatar/body-command-scheduler.mjs";
import {
  ConversationEmbodimentState,
  gazeForConversationState,
  resolveSocialGazeTarget,
} from "../app/frontend-v2/renderer/avatar/social-attention.mjs";
import {
  createSpeechMotorPlan,
  normalizeSpeechMotorPlan,
} from "../app/frontend-v2/renderer/avatar/speech-motor-plan.mjs";
import { createSpeechEventForwarder } from "../app/frontend-v2/renderer/avatar/speech-event-forwarder.mjs";
import { createConversationEmbodimentController } from "../app/frontend-v2/renderer/avatar/conversation-embodiment.mjs";

function clock() {
  let value = 100;
  return { now: () => value, advance: (delta) => { value += delta; } };
}

test("H0: composite biaoxian expands into independent scheduling channels", () => {
  const source = {
    backendInstanceId: "backend-1",
    turnId: "turn-1",
    sequence: 0,
    posture: "attentive",
    expression: { name: "happy", intensity: 0.6 },
    gaze: { target: "user" },
    gesture: "co_speech",
    extras: { tail: "happy", source: "llm" },
    ttlMs: 10_000,
  };
  const split = splitBodyActionChannels(source);
  assert.deepEqual(split.map((wire) => wire.channel), ["posture", "gesture", "gaze", "expression", "tail"]);
  for (const wire of split) {
    const bodyKeys = ["posture", "gesture", "gaze", "expression"].filter((key) => wire[key] !== undefined);
    assert.equal(bodyKeys.length + (wire.channel === "tail" ? 1 : 0), 1);
  }
});

test("H0: latest state channels supersede while gestures retain FIFO", () => {
  const time = clock();
  const received = [];
  const scheduler = createBodyCommandScheduler({
    nowMonotonic: time.now,
    sink: { applyPerformance: (wire) => received.push(wire) },
  });
  const intent = (sequence, expression, gaze, gesture) => ({
    backendInstanceId: "backend-1",
    turnId: "turn-1",
    sequence,
    posture: sequence ? "thoughtful" : "attentive",
    expression: { name: expression, intensity: 0.5 },
    gaze: { target: gaze },
    gesture,
    extras: { tail: sequence ? "curious" : "happy", source: "llm" },
    ttlMs: 10_000,
  });
  assert.equal(scheduler.submit(intent(0, "happy", "user", "co_speech")).reason, "expanded");
  assert.equal(scheduler.submit(intent(1, "thinking", "left", "tilt")).reason, "expanded");
  scheduler.pump();
  assert.equal(received.filter((wire) => wire.channel === "expression").length, 1);
  assert.equal(received.find((wire) => wire.channel === "expression").expression.name, "thinking");
  assert.equal(received.find((wire) => wire.channel === "gaze").gaze.target, "left");
  assert.deepEqual(received.filter((wire) => wire.channel === "gesture").map((wire) => wire.gesture), ["co_speech", "tilt"]);
  assert.ok(received.filter((wire) => wire.channel === "gesture").every((wire) => wire.gaze === undefined && wire.expression === undefined));
});

test("H1: social gaze semantics resolve to distinct spatial targets", () => {
  const cameraPosition = { x: 0, y: 1.6, z: 2 };
  const user = resolveSocialGazeTarget("user", { cameraPosition });
  const left = resolveSocialGazeTarget("left", { cameraPosition });
  const right = resolveSocialGazeTarget("right", { cameraPosition });
  const down = resolveSocialGazeTarget("down", { cameraPosition });
  assert.notEqual(left.point.x, right.point.x);
  assert.ok(down.point.y < user.point.y);
  assert.equal(resolveSocialGazeTarget("unknown", { cameraPosition }).degraded, true);
  assert.equal(gazeForConversationState(ConversationEmbodimentState.THINKING), "down");
});

test("H2: text/provider timing produces a canonical viseme plan", () => {
  const estimated = createSpeechMotorPlan("这里真正的问题是运行时");
  assert.ok(estimated.items.length > 4);
  assert.ok(estimated.items.every((item) => ["aa", "ih", "ou", "ee", "oh"].includes(item.viseme)));
  const provider = normalizeSpeechMotorPlan({
    durationMs: 800,
    items: [{ atMs: 100, viseme: "aa" }, { atMs: 350, viseme: "ih", strength: 0.8 }],
  });
  assert.equal(provider.source, "provider");
  assert.equal(provider.items[1].strength, 0.8);
});

test("H2: the single speech owner forwards text plan and real boundary timing", () => {
  const time = clock();
  const submitted = [];
  const phases = [];
  const forwarder = createSpeechEventForwarder({
    nowMonotonic: time.now,
    submit: (wire) => submitted.push(wire),
    onPhase: (phase) => phases.push(phase),
  });
  const owner = forwarder.claimOwner("tts-owner");
  owner.speechStart({ text: "你好天工" });
  owner.speechBoundary({ text: "你好天工", charIndex: 2, elapsedMs: 180, boundaryType: "word" });
  owner.speechStop("ended");
  assert.equal(submitted[0].speechText, "你好天工");
  assert.ok(submitted[0].speechPlan.items.length > 0);
  assert.equal(submitted[1].speechBoundary.elapsedMs, 180);
  assert.deepEqual(phases, ["start", "boundary", "stop"]);
  assert.equal(forwarder.ownsTtsPlayback, false);
});

test("H3: conversation states use the canonical scheduler input and settle", () => {
  const time = clock();
  const submitted = [];
  const timers = [];
  const controller = createConversationEmbodimentController({
    nowMonotonic: time.now,
    submit: (wire) => submitted.push(wire),
    setTimer: (callback) => { timers.push(callback); return timers.length; },
    clearTimer: () => {},
  });
  controller.transition(ConversationEmbodimentState.LISTENING, { reason: "typing" });
  controller.transition(ConversationEmbodimentState.THINKING, { reason: "submitted" });
  controller.handleSpeechPhase("start");
  controller.handleSpeechPhase("stop");
  assert.deepEqual(submitted.slice(0, 4).map((wire) => wire.conversationState), ["LISTENING", "THINKING", "SPEAKING", "TURN_YIELDING"]);
  assert.equal(submitted[0].gaze.target, "user");
  assert.equal(submitted[1].gaze.target, "down");
  timers.at(-1)();
  assert.equal(controller.state, ConversationEmbodimentState.IDLE);
  assert.equal(submitted.at(-1).conversationState, "IDLE");
});
