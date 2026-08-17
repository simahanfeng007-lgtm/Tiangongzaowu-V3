// LegacyPerformanceDriver —— 自 桌面宠物.html 移植的肖像表现驱动（§15 行为 parity）。
//
// 职责：自然站姿（呼吸/微动/情绪 lean/手势）、表情（qinggan + biaoxian + 口型）、
// 说话口型（viseme 计划或 speech-energy 驱动）、尾巴微动。
// 只写 vrm.humanoid 归一化骨骼与 expressionManager；不触碰 IPC/文件/UI/业务状态。
//
// parity 纪律：常量与算式逐值来自 桌面宠物.html（applyNaturalPose/updateExpressions/
// applyTailMotion/sanitizePerformance/speechMouthTargets），除：
//   - 去掉房间/自主 roam 分支（聊天肖像固定 PORTRAIT_STAND_MODE=true、autonomyDemo=false）；
//   - 无 desire 输入时 rest 情绪项按 0 处理；
//   - 口型在无文字计划时用 speechEnergy 做能量驱动兜底。

import * as THREE from "three";
import { createGestureProsodyPlan, gestureMotionAt } from "../gesture-prosody-planner.mjs";
import { createHumanIdleDynamics, createSocialBlinkController } from "../human-idle-dynamics.mjs";

export const LEGACY_PERFORMANCE_DRIVER_VERSION = "legacy-performance-driver-1.2.0";

export const VRMA_GESTURE_KEYS = Object.freeze([
  "thinking",
  "relax",
  "sad",
  "surprised",
  "lookAround",
  "angry",
  "clapping",
]);

export const EMOTION_KEYS = Object.freeze([
  "joy",
  "anger",
  "worry",
  "thoughtfulness",
  "sadness",
  "fear",
  "surprise",
]);

// CHARACTER_PRESETS.default.expression（桌面宠物.html 默认档逐值）。
const EXPRESSION_PROFILE = Object.freeze({
  smileScale: 1,
  baseCalm: 0.04,
  angerScale: 1,
  alertLookScale: 1,
});

const ALLOWED_PERFORMANCE = Object.freeze({
  expression: ["soft", "happy", "thinking", "worried", "surprised", "shy"],
  gaze: ["user", "down", "left", "right", "away"],
  posture: ["relaxed", "attentive", "bashful", "thoughtful", "steady"],
  gesture: ["co_speech", "nod", "tilt", "greet_wave", "small_wave", "hand_to_chest", "sway", "none"],
  tail: ["calm", "curious", "happy", "alert"],
});

const SPEAKING_NOMINAL_SECONDS = 5;
const SPEAKING_ENERGY_EXTEND_SECONDS = 0.5;
const VISEME_CYCLE = Object.freeze(["aa", "ih", "ou", "ee", "oh"]);

function clamp01(value) {
  return THREE.MathUtils.clamp(Number(value) || 0, 0, 1);
}

function aboveBase(value, base) {
  return clamp01((clamp01(value) - base) / (1 - base));
}

function smoothstep(a, b, x) {
  const t = THREE.MathUtils.clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
}

function cleanSpeechText(text) {
  return String(text || "").replace(/\[[^\]]+\]/g, "").replace(/\s+/g, "").trim();
}

function speechDurationForText(text) {
  const clean = cleanSpeechText(text);
  return THREE.MathUtils.clamp(clean.length * 0.085, 1.0, 8.0);
}

export function createLegacyPerformanceDriver({ vrm, applyExpression, mapViseme, random = Math.random }) {
  if (vrm === null || typeof vrm !== "object") {
    throw new TypeError("legacy-performance-driver 需要 vrm 对象");
  }
  if (typeof applyExpression !== "function") {
    throw new TypeError("legacy-performance-driver 需要 applyExpression(name, value) 回调");
  }
  const visemeForChar = typeof mapViseme === "function"
    ? (ch, index = 0) => mapViseme(ch, index)
    : (ch, index = 0) => "aa";

  const state = {
    idleTime: 0,
    idleShiftSeed: clamp01(random()) * 6.28,
    qinggan: Object.fromEntries(EMOTION_KEYS.map((key) => [key, 0])),
    performance: null,
    conversationState: "IDLE",
    talkUntil: 0,
    speechPlan: null,
    speechText: "",
    gestureProsody: null,
    gestureLastStrokeAt: Number.NEGATIVE_INFINITY,
    gestureLastSemantic: null,
    gestureSuppressedCount: 0,
    speaking: false,
    speakingUntil: 0,
    speechEnergy: 0,
    expression: {
      happy: 0,
      angry: 0,
      sad: 0,
      relaxed: 0,
      surprised: 0,
      blink: 0,
      lookUp: 0,
      lookDown: 0,
      lookLeft: 0,
      lookRight: 0,
      aa: 0,
      ih: 0,
      ou: 0,
      ee: 0,
      oh: 0,
    },
    idleDynamics: createHumanIdleDynamics({ random }),
    idleMotion: Object.freeze({ shift: 0, sway: 0, headYaw: 0, headRoll: 0, facialDrift: 0 }),
    socialBlink: createSocialBlinkController({ random }),
    tailRig: Object.freeze({ bones: [], baseRotations: [] }),
    tailDynamics: { amplitude: 0.72, tempo: 0.78, phase: 0, velocity: [] },
  };

  function currentGestureMotion() {
    const aligned = state.gestureProsody;
    if (!aligned) return null;
    const elapsedMs = (state.idleTime - aligned.startedAt) * 1000;
    const motion = gestureMotionAt(aligned.timeline, elapsedMs);
    if (!motion && elapsedMs > aligned.timeline.durationMs + 500) {
      state.gestureProsody = null;
      return null;
    }
    if (motion && elapsedMs >= motion.strokeAtMs && aligned.recordedStrokeAtMs !== motion.strokeAtMs) {
      aligned.recordedStrokeAtMs = motion.strokeAtMs;
      state.gestureLastStrokeAt = state.idleTime;
      state.gestureLastSemantic = motion.gesture;
    }
    return motion;
  }

  function rebuildGestureProsody(text, plan) {
    const gesture = state.performance?.gesture;
    if (!["co_speech", "nod", "tilt", "hand_to_chest"].includes(gesture)) {
      state.gestureProsody = null;
      return null;
    }
    const timing = state.performance?.channelTiming?.gesture ?? {};
    const timeline = createGestureProsodyPlan({
      text,
      speechPlan: plan,
      gesture,
      intensity: timing.intensity ?? 0.55,
    });
    if (!timeline) {
      state.gestureProsody = null;
      state.gestureSuppressedCount += 1;
      return null;
    }
    const refractorySeconds = state.gestureLastSemantic === gesture ? 3.8 : 1.35;
    const firstStrokeAt = state.idleTime + timeline.items[0].strokeAtMs / 1000;
    if (firstStrokeAt - state.gestureLastStrokeAt < refractorySeconds) {
      state.gestureProsody = null;
      state.gestureSuppressedCount += 1;
      return null;
    }
    state.gestureProsody = {
      timeline,
      startedAt: state.idleTime,
      recordedStrokeAtMs: null,
    };
    return state.gestureProsody;
  }

  function activePerformance() {
    const stored = state.performance;
    if (stored === null) return null;
    if (!stored.channelUntil) return state.idleTime < stored.until ? stored : null;
    const active = { source: stored.source, channelUntil: stored.channelUntil };
    let activeCount = 0;
    for (const channel of ["expression", "gaze", "posture", "gesture", "tail"]) {
      if (state.idleTime >= Number(stored.channelUntil[channel] || 0)) continue;
      active[channel] = stored[channel];
      activeCount += 1;
    }
    const alignable = ["co_speech", "nod", "tilt", "hand_to_chest"].includes(active.gesture);
    const gestureAge = state.idleTime - Number(stored.channelTiming?.gesture?.startedAt ?? state.idleTime);
    // 2026-08-16 修复：等待窗口只在语音链真实活跃时有意义。无 TTS 场景
    // speaking/speechPlan/gestureProsody 恒为空，0.75s 抑制纯属空白等待；
    // 有 TTS 时韵律计划到达后 prosody 分支仍会接管（下方条件含 state.gestureProsody）。
    const speechChainLive = state.speaking || state.speechPlan !== null || state.gestureProsody !== null;
    const waitingForSpeech = alignable && speechChainLive && gestureAge < 0.75;
    if (alignable && (waitingForSpeech || state.gestureProsody || state.speaking || state.speechPlan)) {
      delete active.gesture;
      activeCount -= 1;
      const motion = currentGestureMotion();
      if (motion) {
        active.gesture = motion.gesture;
        active.gestureEnvelope = motion.envelope;
        active.gesturePhase = motion.phase;
        active.gestureProminenceAtMs = motion.prominenceAtMs;
        active.startedAt = state.gestureProsody.startedAt + motion.startMs / 1000;
        active.duration = Math.max(0.1, (motion.endMs - motion.startMs) / 1000);
        active.until = active.startedAt + active.duration;
        active.intensity = motion.strength;
        activeCount += 1;
      }
    }
    if (!activeCount) return null;
    const timingChannel = active.gesture ? "gesture" : active.gaze ? "gaze" : active.posture ? "posture" : active.expression ? "expression" : "tail";
    const timing = stored.channelTiming?.[timingChannel] ?? {};
    if (!Number.isFinite(active.gestureEnvelope)) {
      active.startedAt = timing.startedAt ?? state.idleTime;
      active.duration = timing.duration ?? 1;
      active.until = stored.channelUntil[timingChannel];
      active.intensity = timing.intensity ?? 0.45;
    }
    return active;
  }

  // ── 尾巴骨骼识别（桌面宠物.html refreshTailRig parity）────────────────
  function refreshTailRig(root) {
    const bones = [];
    const baseRotations = [];
    if (root) {
      root.traverse((obj) => {
        if (!obj.isBone) return;
        const name = (obj.name || "").toLowerCase();
        const compact = name.replace(/[\s._-]/g, "");
        const jpTail = String.fromCharCode(0x3057, 0x3063, 0x307d);
        const cnTail = String.fromCharCode(0x5c3e);
        const cnButt = String.fromCharCode(0x5c3b);
        if (compact.includes("tail") || compact.includes("sippo") || compact.includes("sipo")
          || name.includes(jpTail) || name.includes(cnTail) || name.includes(cnButt)) {
          bones.push(obj);
        }
      });
      bones.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
      baseRotations.push(...bones.map((bone) => bone.rotation.clone()));
    }
    state.tailRig = Object.freeze({ bones, baseRotations });
    state.tailDynamics.velocity = bones.map(() => ({ x: 0, y: 0, z: 0 }));
  }

  // ── 表演语义（biaoxian）────────────────────────────────────────
  function sanitizePerformance(data, text = "") {
    const source = data && typeof data === "object" ? data : {};
    const pick = (key, fallback) => {
      const value = String(source[key] || fallback).trim();
      return ALLOWED_PERFORMANCE[key].includes(value) ? value : fallback;
    };
    const textString = String(text || "");
    const hasBang = textString.includes("!") || textString.includes(String.fromCharCode(0xff01));
    const hasQuestion = textString.includes("?") || textString.includes(String.fromCharCode(0xff1f));
    const isGreeting = /你好|您好|早上好|晚上好|打招呼|挥手|招手|hello|hi/i.test(textString);
    const fallbackExpression = hasBang ? "happy" : (hasQuestion ? "thinking" : "soft");
    let fallbackGesture = isGreeting ? "greet_wave" : "co_speech";
    let gesture = pick("gesture", fallbackGesture);
    if (gesture === "none" && textString.trim()) gesture = "co_speech";
    if (gesture === "small_wave" && isGreeting) gesture = "greet_wave";
    const baseDuration = speechDurationForText(text);
    const requestedDuration = Number(source.duration) || baseDuration;
    const duration = THREE.MathUtils.clamp(
      Math.max(requestedDuration, baseDuration + 0.18, isGreeting ? 2.8 : 1.4),
      1,
      8,
    );
    return {
      expression: pick("expression", isGreeting ? "happy" : fallbackExpression),
      gaze: pick("gaze", "user"),
      posture: pick("posture", isGreeting ? "attentive" : "relaxed"),
      gesture,
      tail: pick("tail", isGreeting || fallbackExpression === "happy" ? "happy" : "calm"),
      intensity: THREE.MathUtils.clamp(Number(source.intensity) || (isGreeting ? 0.72 : 0.55), 0, 1),
      duration,
      source: source.source || "client",
    };
  }

  function applyBodyPerformance(data, text = "") {
    const requestedChannel = typeof data?.channel === "string" ? data.channel : null;
    if (["expression", "gaze", "posture", "gesture", "tail"].includes(requestedChannel)) {
      const field = requestedChannel;
      const raw = field === "gesture" && typeof data?.gesture === "object" ? data.gesture.semanticId : data?.[field];
      if (typeof raw !== "string" || !ALLOWED_PERFORMANCE[field].includes(raw)) return false;
      // §15.1 wire 契约时长字段是 durationMs（毫秒）；旧代码只读 duration（秒），
      // 导致 biaoxian 经 adapter 进入本路径时 LLM 自报时长被丢弃、永远回退 2.0s。
      const wireDurationMs = Number(data?.durationMs);
      const durationSeconds = Number.isFinite(wireDurationMs) && wireDurationMs > 0
        ? wireDurationMs / 1000
        : Number(data?.duration);
      const duration = THREE.MathUtils.clamp(durationSeconds || 2.0, 0.1, 8);
      const previous = state.performance?.channelUntil ? state.performance : {
        source: data?.source || "client",
        channelUntil: {},
        channelTiming: {},
      };
      state.performance = {
        ...previous,
        [field]: raw,
        source: data?.source || previous.source || "client",
        channelUntil: { ...previous.channelUntil, [field]: state.idleTime + duration },
        channelTiming: {
          ...previous.channelTiming,
          [field]: { startedAt: state.idleTime, duration, intensity: clamp01(data?.intensity ?? 0.45) },
        },
      };
      return state.performance;
    }
    const performance = sanitizePerformance(data, text);
    performance.startedAt = state.idleTime;
    performance.until = state.idleTime + performance.duration;
    state.performance = performance;
    return performance;
  }

  // ── 说话口型计划（桌面宠物.html startSpeechPlan/speechMouthTargets parity）──
  function startSpeechPlan(text) {
    const clean = cleanSpeechText(text);
    const duration = speechDurationForText(clean);
    const chars = Array.from(clean).filter((ch) => !/[\u3000\s,.;:!?，。！？、；：'"“”‘’\-]/u.test(ch));
    const items = (chars.length ? chars : Array.from("um")).map((ch, index) => ({
      time: duration * (index + 0.35) / Math.max(1, chars.length),
      viseme: visemeForChar(ch, index),
      strength: 0.55 + ((ch.charCodeAt(0) + index) % 5) * 0.06,
    }));
    state.speechText = String(text ?? "");
    state.speechPlan = { startedAt: state.idleTime, duration, items };
    state.talkUntil = Math.max(state.talkUntil, state.idleTime + duration);
    rebuildGestureProsody(state.speechText, {
      durationMs: duration * 1000,
      items: items.map((item) => ({ atMs: item.time * 1000, viseme: item.viseme, strength: item.strength })),
    });
  }

  function setSpeechPlan(plan, text = "") {
    if (plan === null || typeof plan !== "object" || !Array.isArray(plan.items) || !plan.items.length) {
      startSpeechPlan(text);
      return state.speechPlan;
    }
    const duration = THREE.MathUtils.clamp((Number(plan.durationMs) || 1000) / 1000, 0.12, 120);
    const items = plan.items
      .map((item) => ({
        time: Math.max(0, Number(item?.atMs) || 0) / 1000,
        viseme: VISEME_CYCLE.includes(String(item?.viseme)) ? String(item.viseme) : "aa",
        strength: clamp01(item?.strength ?? 0.7),
      }))
      .sort((a, b) => a.time - b.time);
    state.speechText = String(text ?? "");
    state.speechPlan = { startedAt: state.idleTime, duration, items, source: String(plan.source || "provider") };
    state.talkUntil = Math.max(state.talkUntil, state.idleTime + duration);
    rebuildGestureProsody(state.speechText, plan);
    return state.speechPlan;
  }

  function applySpeechBoundary(boundary = {}) {
    if (state.speechPlan === null && typeof boundary.text === "string") startSpeechPlan(boundary.text);
    if (state.speechPlan !== null && Number.isFinite(Number(boundary.elapsedMs))) {
      state.speechPlan.startedAt = state.idleTime - Math.max(0, Number(boundary.elapsedMs)) / 1000;
      if (state.gestureProsody) state.gestureProsody.startedAt = state.speechPlan.startedAt;
    }
    state.socialBlink.noteBoundary(boundary);
    return state.speechPlan !== null;
  }

  function markTalking(text) {
    const active = activePerformance() !== null;
    if (!active) {
      applyBodyPerformance({
        gesture: "co_speech",
        expression: "soft",
        gaze: "user",
        posture: "relaxed",
        tail: "calm",
        intensity: 0.36,
        duration: speechDurationForText(text),
        source: "speech",
      }, text);
    }
    startSpeechPlan(text);
  }

  function speechMouthTargets() {
    const out = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    const plan = state.speechPlan;
    if (!plan) {
      if (state.speaking && state.speechEnergy > 0) {
        const e = state.speechEnergy;
        out.aa = 0.08 + e * 0.22 + Math.sin(state.idleTime * 22) * 0.03;
        out.ih = e * 0.12 + Math.sin(state.idleTime * 17 + 1) * 0.02;
        out.ou = e * 0.08 + Math.sin(state.idleTime * 19 + 2) * 0.02;
        out.ee = e * 0.10 + Math.sin(state.idleTime * 16 + 3) * 0.02;
        out.oh = e * 0.06 + Math.sin(state.idleTime * 18 + 4) * 0.02;
      }
      return out;
    }
    const t = state.idleTime - plan.startedAt;
    if (t < 0 || t > plan.duration + 0.2) {
      state.speechPlan = null;
      return out;
    }
    const windowSize = Math.max(0.09, plan.duration / Math.max(12, plan.items.length * 2.8));
    plan.items.forEach((item) => {
      const d = Math.abs(t - item.time);
      if (d > windowSize * 1.65) return;
      const w = Math.pow(Math.max(0, 1 - d / (windowSize * 1.65)), 2);
      out[item.viseme] = Math.max(out[item.viseme], w * item.strength);
    });
    if (t < plan.duration) out.aa = Math.max(out.aa, 0.08 + Math.sin(state.idleTime * 22) * 0.035);
    return out;
  }

  // ── 表情（桌面宠物.html updateExpressions parity）────────────────
  function updateExpressions(dt) {
    const manager = vrm?.expressionManager;
    if (!manager) return;
    const q = state.qinggan || {};
    const talk = state.idleTime < state.talkUntil ? 1 : 0;
    const mouth = speechMouthTargets();
    const perf = activePerformance();
    const pInt = perf ? perf.intensity : 0;
    const target = {
      happy: Math.max(aboveBase(q.joy, 0.36) * 0.78, perf?.expression === "happy" ? 0.55 : 0, perf?.expression === "shy" ? 0.40 : 0),
      angry: aboveBase(q.anger, 0.12) * 0.88,
      sad: Math.max(aboveBase(q.sadness, 0.12) * 0.86, aboveBase(q.worry, 0.18) * 0.34, aboveBase(q.fear, 0.18) * 0.30, perf?.expression === "worried" ? 0.50 : 0),
      relaxed: Math.max(0.04, aboveBase(q.thoughtfulness, 0.28) * 0.30, perf?.expression === "soft" ? 0.25 : 0),
      surprised: Math.max(aboveBase(q.surprise, 0.16) * 0.84, perf?.expression === "surprised" ? 0.65 : 0),
      // 目光由 VRM LookAt 接管，look* 保持 0（parity）。
      lookUp: 0,
      lookDown: 0,
      lookLeft: 0,
      lookRight: 0,
      aa: talk * Math.max(0, mouth.aa),
      ih: talk * Math.max(0, mouth.ih),
      ou: talk * Math.max(0, mouth.ou),
      ee: talk * Math.max(0, mouth.ee),
      oh: talk * Math.max(0, mouth.oh),
    };
    const expressionProfile = EXPRESSION_PROFILE;
    target.happy *= expressionProfile.smileScale ?? 1;
    target.relaxed = Math.max(target.relaxed, expressionProfile.baseCalm ?? 0.04);
    target.relaxed = Math.max(target.relaxed, 0.04 + Math.max(0, state.idleMotion.facialDrift) * 0.018);
    target.angry = Math.min(1, target.angry * (expressionProfile.angerScale ?? 1));

    target.blink = state.socialBlink.update(dt, state.conversationState).amount;

    for (const [name, value] of Object.entries(target)) {
      const current = state.expression[name] || 0;
      const mouth = VISEME_CYCLE.includes(name);
      const speed = name === "blink" ? 24 : mouth ? 13 : (value > current ? 6.4 : 4.1);
      const k = 1 - Math.exp(-dt * speed);
      state.expression[name] = THREE.MathUtils.lerp(current, value, k);
      try {
        applyExpression(name, state.expression[name]);
      } catch (_error) {
        // 单条表情失败不阻断驱动（引擎 alias 降级已覆盖未知名）
      }
    }
  }

  // ── 自然站姿（桌面宠物.html applyNaturalPose 肖像分支 parity）────────
  function resetPortraitPose() {
    if (!vrm?.humanoid) return;
    try {
      if (vrm.humanoid.resetNormalizedPose) vrm.humanoid.resetNormalizedPose();
      else if (vrm.humanoid.resetPose) vrm.humanoid.resetPose();
    } catch (_error) { /* 幂等 */ }
  }

  function applyNaturalPose() {
    if (!vrm?.humanoid) return;
    try {
      const humanoid = vrm.humanoid;
      const hips = humanoid.getNormalizedBoneNode("hips");
      const spine = humanoid.getNormalizedBoneNode("spine");
      const chest = humanoid.getNormalizedBoneNode("chest");
      const neck = humanoid.getNormalizedBoneNode("neck");
      const head = humanoid.getNormalizedBoneNode("head");
      const rua = humanoid.getNormalizedBoneNode("rightUpperArm");
      const lua = humanoid.getNormalizedBoneNode("leftUpperArm");
      const rla = humanoid.getNormalizedBoneNode("rightLowerArm");
      const lla = humanoid.getNormalizedBoneNode("leftLowerArm");
      const rha = humanoid.getNormalizedBoneNode("rightHand");
      const lha = humanoid.getNormalizedBoneNode("leftHand");
      const breath = Math.sin(state.idleTime * 1.05);
      const armEase = Math.sin(state.idleTime * 0.62) * 0.018;
      const q = state.qinggan || {};
      const joy = clamp01(q.joy);
      const anger = clamp01(q.anger);
      const worry = clamp01(q.worry);
      const thought = clamp01(q.thoughtfulness);
      const sad = clamp01(q.sadness);
      const fear = clamp01(q.fear);
      const surprise = clamp01(q.surprise);

      // 肖像固定模式：PORTRAIT_STAND_MODE=true、autonomyDemo=false（聊天肖像）。
      const softTalk = state.idleTime < state.talkUntil ? 1 : 0;
      const perf = activePerformance();
      const pInt = perf ? perf.intensity : 0;
      const pAge = perf ? state.idleTime - perf.startedAt : 0;
      const pNorm = perf ? THREE.MathUtils.clamp(pAge / perf.duration, 0, 1) : 0;
      const pWave = perf ? (Number.isFinite(perf.gestureEnvelope) ? perf.gestureEnvelope : Math.sin(pNorm * Math.PI)) : 0;
      const gesture = perf?.gesture || "none";
      const speechPulse = softTalk * (0.5 + 0.5 * Math.sin(state.idleTime * 5.2 + state.idleShiftSeed));
      const coSpeech = (gesture === "co_speech" ? pWave : 0) * (0.24 + (perf ? pInt : 0.55) * 0.42);
      const motionScale = { LISTENING: 0.42, THINKING: 0.58, TURN_ACQUIRING: 0.72, SPEAKING: 1, TURN_YIELDING: 0.5, IDLE: 0.68 }[state.conversationState] ?? 0.68;
      const shift = (Math.sin(state.idleTime * 0.33 + state.idleShiftSeed) * 0.32 + state.idleMotion.shift * 0.68) * motionScale;
      const sway = (Math.sin(state.idleTime * 0.21 + state.idleShiftSeed * 0.7) * 0.28 + state.idleMotion.sway * 0.72) * motionScale;
      const breath2 = Math.sin(state.idleTime * 0.74 + 1.2);
      const microLook = Math.sin(state.idleTime * 0.13 + state.idleShiftSeed * 0.5) * 0.35 + state.idleMotion.headYaw * 0.65;
      const microSettle = Math.sin(state.idleTime * 0.17 + 2.1) * 0.35 + state.idleMotion.headRoll * 0.65;
      const postureLean = {
        attentive: -0.012,
        bashful: 0.012,
        thoughtful: 0.002,
        steady: -0.004,
        relaxed: 0,
      }[perf?.posture || "relaxed"] || 0;
      let headX = -0.012 + breath * 0.003 + softTalk * 0.004 + speechPulse * 0.006 * coSpeech + (perf?.gaze === "down" ? 0.026 * pInt : 0);
      // 目光跟随镜头：headY 只保留自然微动（VRM LookAt 接管 gaze）
      let headY = Math.sin(state.idleTime * 0.26) * 0.014 * motionScale + microLook * 0.010 * motionScale;
      if (perf?.gaze === "left") headY -= 0.045 * Math.max(0.35, pInt);
      if (perf?.gaze === "right") headY += 0.045 * Math.max(0.35, pInt);
      if (perf?.gaze === "away") headY -= 0.060 * Math.max(0.35, pInt);
      let headZ = Math.sin(state.idleTime * 0.21) * 0.006 + microSettle * 0.004;
      if (gesture === "nod") headX += Math.sin(pNorm * Math.PI * 2.0) * 0.18 * pWave * Math.max(0.55, pInt);
      if (gesture === "tilt" || perf?.posture === "bashful") headZ += 0.14 * pWave * Math.max(0.50, pInt);
      // 自然站姿基准（2026-08-05 生物学标定 v2，JOSR 中位数）。
      // 来源 JOSR 2022 上肢站立中立位（doi:10.1186/s13018-022-03113-5，upright CT）：
      //   盂肱外展 4.5°（IQR 0.9–7.8）、内旋 9.0°（2.2–19）、
      //   肘屈 15.5°（13.2–18.1）、外翻 9.8°、旋前 90.2°。
      // 在真实模型上按中位数扫描实测反推（手不悬空、贴近大腿）：
      // 角色面朝方向以头部网格几何实测确认 = world +Z（chest 骨骼本地 +Z 是背面）。
      //   rightUpperArm = (0.0129, -0.1526, -1.4828)  → 外展 ~5° + 内旋 ~8.7°
      //   rightLowerArm = (0.3000,  0.1800,  0.0000)  → 肘前屈 ~10° + 外翻 10.3°
      //   leftLowerArm  = (0.3000, -0.2000, -0.0200)  → 肘前屈 ~10.2° + 外翻 10.4°
      //     （2026-08-05 幅度收小：用户反馈前屈 15° 偏大，按实景微调至 ~10°；
      //       方向以头部几何实测=world +Z，前臂 ly 正值=前屈）
      //   rightHand     = (-0.4233, 0, 0)              → 掌心朝身体（0.98），手指沿前臂（0°）
      //   leftHand      = (-0.4404, 0, 0)              → 同上（左臂独立求解）
      // 左臂 X 同号、Y/Z 反号（含左右不对称微调）。
      let rUpperZ = -1.4828 + breath * 0.004 + armEase * 0.05 + sway * 0.006;
      let lUpperZ = 1.4828 - breath * 0.004 - armEase * 0.05 + sway * 0.005;
      let rLowerZ = armEase * 0.018;
      let lLowerZ = -0.020 - armEase * 0.018;
      let rUpperX = 0.0129 + armEase * 0.035;
      let lUpperX = 0.0129 - armEase * 0.030;
      let rUpperY = -0.1526 + shift * 0.004;
      let lUpperY = 0.1526 + shift * 0.004;
      let rLowerX = 0.300 + softTalk * 0.008 + speechPulse * 0.006 * coSpeech;
      let lLowerX = 0.300 + softTalk * 0.007 + Math.sin(state.idleTime * 3.0 + 0.9) * 0.003 * coSpeech;
      let rLowerY = 0.180;
      let lLowerY = -0.200;
      let rHandX = -0.4233 + Math.sin(state.idleTime * 0.82 + 0.4) * 0.005;
      let lHandX = -0.4404 + Math.sin(state.idleTime * 0.76 + 1.1) * 0.005;
      let rHandY = 0 + Math.sin(state.idleTime * 0.53 + 0.2) * 0.005;
      let lHandY = 0 + Math.sin(state.idleTime * 0.49 + 1.0) * 0.005;
      let rHandZ = 0 + armEase * 0.024 + Math.sin(state.idleTime * 0.9) * 0.005;
      let lHandZ = 0 - armEase * 0.024 + Math.sin(state.idleTime * 0.78 + 1) * 0.005;
      if (coSpeech) {
        rUpperX += speechPulse * 0.010 * coSpeech;
        lUpperX += Math.sin(state.idleTime * 4.1 + 1.1) * 0.006 * coSpeech;
        rLowerZ += Math.sin(state.idleTime * 3.4) * 0.012 * coSpeech;
        lLowerZ -= Math.sin(state.idleTime * 2.8 + 0.6) * 0.010 * coSpeech;
        rHandX += Math.sin(state.idleTime * 4.3 + 0.3) * 0.008 * coSpeech;
        lHandX += Math.sin(state.idleTime * 3.9 + 1.2) * 0.006 * coSpeech;
      }
      if (coSpeech && !softTalk) {
        // 2026-08-16 修复：无 TTS 时 speechPulse=0，上方语音同步项全部失效
        // （残余 ≤0.3°，肉眼不可见）——这就是"回复时身体没有动作"的根因。
        // 这里给 co_speech 一个自激励的可见前臂/手腕摆动，幅度随 pWave 包络
        // 起落，手势结束自然归位；有语音时本分支不启用（行为与 parity 一致）。
        const g = coSpeech * (0.55 + 0.45 * Math.sin(state.idleTime * 4.3 + state.idleShiftSeed));
        rLowerX += g * 0.34;
        lLowerX += g * 0.30;
        rUpperZ += g * 0.10;
        lUpperZ -= g * 0.08;
        rHandZ += Math.sin(state.idleTime * 5.1 + 0.4) * g * 0.18;
        lHandZ -= Math.sin(state.idleTime * 4.7 + 1.0) * g * 0.15;
      }
      if (gesture === "greet_wave" || gesture === "small_wave") {
        const greetT = THREE.MathUtils.clamp(pAge / Math.min(3.1, Math.max(2.45, perf?.duration || 2.8)), 0, 1);
        const raise = smoothstep(0.06, 0.28, greetT) * (1 - smoothstep(0.84, 0.98, greetT));
        const waveWindow = smoothstep(0.26, 0.38, greetT) * (1 - smoothstep(0.76, 0.92, greetT));
        const power = gesture === "greet_wave"
          ? THREE.MathUtils.clamp(0.55 + pInt * 0.55, 0.68, 1.0)
          : THREE.MathUtils.clamp(0.28 + pInt * 0.32, 0.34, 0.62);
        const wave = Math.sin(pAge * 10.5) * waveWindow;
        const big = gesture === "greet_wave" ? 1 : 0.58;
        rUpperX += 0.30 * raise * power * big;
        rUpperY += -0.04 * raise * power * big;
        rUpperZ += 0.68 * raise * power * big;
        rLowerX += 0.08 * raise * power * big;
        rLowerY += -0.04 * raise * power * big;
        rLowerZ += 1.02 * raise * power * big + wave * 0.20 * power * big;
        rHandX += 0.02 * raise * power * big;
        rHandZ += wave * 0.26 * power * big;
        headY += 0.018 * raise * power * big;
      } else if (gesture === "hand_to_chest") {
        rUpperX += 0.48 * pWave * Math.max(0.55, pInt);
        rUpperZ += 0.42 * pWave * Math.max(0.55, pInt);
        rLowerZ += 0.65 * pWave * Math.max(0.55, pInt);
        rLowerX += 0.12 * pWave * Math.max(0.55, pInt);
        rHandX += -0.20 * pWave * Math.max(0.55, pInt);
      } else if (gesture === "sway") {
        rUpperZ += Math.sin(state.idleTime * 0.9) * 0.026 * pInt;
        lUpperZ += Math.sin(state.idleTime * 0.9 + 0.8) * 0.026 * pInt;
      }
      if (hips) {
        hips.rotation.x = breath * 0.002 + postureLean * 0.25 + Math.sin(state.idleTime * 0.47 + 1.0) * 0.004;
        hips.rotation.y = shift * 0.006;
        hips.rotation.z = shift * 0.012 + sway * 0.010;
      }
      if (spine) {
        spine.rotation.x = -0.006 + breath * 0.003 + postureLean + Math.sin(state.idleTime * 0.53 + 2.0) * 0.003;
        spine.rotation.y = -shift * 0.008;
        spine.rotation.z = -shift * 0.008 + microSettle * 0.003;
      }
      if (chest) {
        chest.rotation.x = 0.012 + breath * 0.007 + breath2 * 0.002 + softTalk * 0.005 + postureLean * 0.55;
        chest.rotation.y = shift * 0.010;
        chest.rotation.z = Math.sin(state.idleTime * 0.32) * 0.005 + shift * 0.007;
      }
      if (neck) {
        neck.rotation.x = -0.006 + breath * 0.002 + postureLean * 0.35;
        neck.rotation.y = headY * 0.45;
        neck.rotation.z = headZ * 0.35;
      }
      if (head) {
        head.rotation.x = headX;
        head.rotation.y = headY;
        head.rotation.z = headZ;
      }
      if (rua) {
        rua.rotation.x = rUpperX;
        rua.rotation.y = rUpperY;
        rua.rotation.z = rUpperZ;
      }
      if (lua) {
        lua.rotation.x = lUpperX;
        lua.rotation.y = lUpperY;
        lua.rotation.z = lUpperZ;
      }
      if (rla) {
        rla.rotation.x = rLowerX;
        rla.rotation.y = rLowerY;
        rla.rotation.z = rLowerZ;
      }
      if (lla) {
        lla.rotation.x = lLowerX;
        lla.rotation.y = lLowerY;
        lla.rotation.z = lLowerZ;
      }
      if (rha) {
        rha.rotation.x = rHandX;
        rha.rotation.y = rHandY;
        rha.rotation.z = rHandZ;
      }
      if (lha) {
        lha.rotation.x = lHandX;
        lha.rotation.y = lHandY;
        lha.rotation.z = lHandZ;
      }
      const fingerPulse = Math.sin(state.idleTime * 0.72 + state.idleShiftSeed) * 0.006 + Math.sin(state.idleTime * 4.4) * 0.008 * coSpeech;
      // 手指休息位（Lee & Jung 2014）：MCP≈30°/PIP≈30°/DIP≈10°，小指侧自然梯度加深。
      // 2026-08-05 轴实测修正：该模型手指骨骼 本地 +X=长轴、rotation.z=屈曲轴、
      // rotation.y=分指、rotation.x=扭转。旧代码误把 rotation.x 当屈曲，实际只产生微扭，
      // 手指一直是直的——这就是“手型不自然”的根因。
      // 左右手本地系镜像：右手 rotation.z 负值=卷向掌心；左手手指骨本地 +X 指向手腕，
      // 需再乘 sign（右 +1 / 左 -1）镜像屈曲方向（蒙皮顶点实测验证）。
      const fingerCurl = (side, finger, base, mid, tip, spread = 0) => {
        const sign = side === "right" ? 1 : -1;
        const proximal = humanoid.getNormalizedBoneNode(`${side}${finger}Proximal`);
        const intermediate = humanoid.getNormalizedBoneNode(`${side}${finger}Intermediate`);
        const distal = humanoid.getNormalizedBoneNode(`${side}${finger}Distal`);
        if (proximal) {
          proximal.rotation.x = 0;
          proximal.rotation.y = spread * sign;
          proximal.rotation.z = -(base + fingerPulse) * sign;
        }
        if (intermediate) {
          intermediate.rotation.x = 0;
          intermediate.rotation.y = 0;
          intermediate.rotation.z = -(mid + fingerPulse * 0.8) * sign;
        }
        if (distal) {
          distal.rotation.x = 0;
          distal.rotation.y = 0;
          distal.rotation.z = -(tip + fingerPulse * 0.55) * sign;
        }
      };
      // 拇指休息位（2026-08-05 蒙皮顶点扫描 + 解剖标定）：
      // 自然放松时拇指沿食指下垂，CMC 关键参数是旋前角（metaX）——旧版指腹
      // 上倾 27° 导致指根“向外拧”；现右 metaX=-0.45 / 左 metaX=-0.40，
      // 实测指腹上倾 0°、完全平贴掌心，掌骨方向归中不外撇
      // （右 +0.004 / 左 +0.001），指尖距食指线 ~29mm、低于指根 22mm。
      // 注意：模型拇指本地系左右非纯镜像（metaX 同号），左右独立标定。
      const thumbCurl = (side) => {
        const right = side === "right";
        const metacarpal = humanoid.getNormalizedBoneNode(`${side}ThumbMetacarpal`);
        const proximal = humanoid.getNormalizedBoneNode(`${side}ThumbProximal`);
        const distal = humanoid.getNormalizedBoneNode(`${side}ThumbDistal`);
        if (metacarpal) {
          metacarpal.rotation.x = (right ? -0.45 : -0.40) + fingerPulse * 0.03;
          metacarpal.rotation.y = (right ? -0.35 : 0.35) + fingerPulse * 0.05;
          metacarpal.rotation.z = (right ? -0.20 : 0.25) + fingerPulse * 0.10;
        }
        if (proximal) {
          proximal.rotation.x = 0;
          proximal.rotation.y = right ? 0.03 : -0.03;
          proximal.rotation.z = (right ? -0.025 : 0.00) + fingerPulse * 0.10;
        }
        if (distal) {
          distal.rotation.x = 0;
          distal.rotation.y = 0;
          distal.rotation.z = (right ? -0.25 : 0.20) + fingerPulse * 0.10;
        }
      };
      for (const side of ["right", "left"]) {
        thumbCurl(side);
        fingerCurl(side, "Index", 0.4363, 0.4189, 0.1745, 0.030);
        fingerCurl(side, "Middle", 0.5236, 0.4712, 0.1745, 0.000);
        fingerCurl(side, "Ring", 0.5760, 0.4887, 0.1920, -0.020);
        fingerCurl(side, "Little", 0.6632, 0.5236, 0.2094, -0.035);
      }
    } catch (_error) {
      // 单帧姿态失败不阻断驱动
    }
  }

  // ── 尾巴（桌面宠物.html applyTailMotion parity）────────────────
  function applyTailMotion(dt) {
    const { bones, baseRotations } = state.tailRig;
    if (!bones.length) return;
    const talking = state.idleTime < state.talkUntil ? 1 : 0;
    const mood = clamp01(state.qinggan.joy) + clamp01(state.qinggan.surprise) * 0.5;
    const performance = activePerformance();
    const tail = performance?.tail ?? "calm";
    const tailTiming = state.performance?.channelTiming?.tail ?? {};
    const tailIntensity = tailTiming.intensity ?? 0.4;
    const targetAmplitude = { calm: 0.72, curious: 1.05, happy: 1.35, alert: 1.18 }[tail] ?? 0.72;
    const targetTempo = { calm: 0.78, curious: 1.08, happy: 1.28, alert: 1.42 }[tail] ?? 0.78;
    const transition = 1 - Math.exp(-Math.max(0, dt) * 2.4);
    state.tailDynamics.amplitude = THREE.MathUtils.lerp(state.tailDynamics.amplitude, targetAmplitude, transition);
    state.tailDynamics.tempo = THREE.MathUtils.lerp(state.tailDynamics.tempo, targetTempo, transition);
    state.tailDynamics.phase += Math.max(0, dt) * 0.86 * state.tailDynamics.tempo;
    bones.forEach((bone, index) => {
      const base = baseRotations[index];
      if (!bone || !base) return;
      const phase = state.tailDynamics.phase * (1 + index * 0.05) - index * 0.46;
      const fade = THREE.MathUtils.clamp(1 - index * 0.075, 0.38, 1);
      const amp = (0.045 + talking * 0.030 + mood * 0.018) * fade * state.tailDynamics.amplitude * (0.72 + tailIntensity * 0.45);
      const target = {
        x: base.x + Math.sin(phase * 0.72 + 0.8) * amp * 0.28,
        y: base.y + Math.sin(phase) * amp,
        z: base.z + Math.cos(phase * 0.82 + index * 0.55) * amp * 0.62,
      };
      const velocity = state.tailDynamics.velocity[index] ?? { x: 0, y: 0, z: 0 };
      state.tailDynamics.velocity[index] = velocity;
      for (const axis of ["x", "y", "z"]) {
        velocity[axis] += ((target[axis] - bone.rotation[axis]) * 20 - velocity[axis] * 7.5) * Math.max(0, dt);
        bone.rotation[axis] += velocity[axis] * Math.max(0, dt);
      }
    });
  }

  // ── 帧驱动 ──────────────────────────────────────────────────
  function update(dt, { gestureActive = false } = {}) {
    const seconds = Math.max(0, Number(dt) || 0);
    state.idleTime += seconds;
    state.idleMotion = state.idleDynamics.update(seconds, state.conversationState);
    if (state.speaking && state.idleTime >= state.speakingUntil) {
      state.speaking = false;
      state.speechEnergy = 0;
    }
    if (!gestureActive) {
      // parity：VRMA 动作播放时由动作驱动全身；无动作时自然站姿。
      resetPortraitPose();
      applyNaturalPose();
    }
    applyTailMotion(seconds);
    updateExpressions(seconds);
  }

  function setQinggan(qinggan = {}) {
    for (const key of EMOTION_KEYS) {
      state.qinggan[key] = clamp01(qinggan[key]);
    }
    return true;
  }

  function setSpeaking(speaking) {
    state.speaking = !!speaking;
    if (state.speaking) {
      state.speakingUntil = state.idleTime + SPEAKING_NOMINAL_SECONDS;
      state.talkUntil = Math.max(state.talkUntil, state.speakingUntil);
    } else {
      state.speakingUntil = 0;
      state.talkUntil = 0;
      state.speechEnergy = 0;
      state.speechPlan = null;
      state.speechText = "";
      state.gestureProsody = null;
      if (state.performance?.channelUntil?.gesture) state.performance.channelUntil.gesture = state.idleTime;
    }
    return true;
  }

  function setSpeechEnergy(energy) {
    state.speechEnergy = clamp01(energy);
    if (state.speaking && state.speechEnergy > 0) {
      state.speakingUntil = state.idleTime + SPEAKING_ENERGY_EXTEND_SECONDS;
      state.talkUntil = Math.max(state.talkUntil, state.speakingUntil);
    }
    return true;
  }

  function setConversationState(conversationState) {
    const normalized = String(conversationState ?? "").trim().toUpperCase();
    if (!["IDLE", "LISTENING", "THINKING", "TURN_ACQUIRING", "SPEAKING", "TURN_YIELDING"].includes(normalized)) return false;
    state.conversationState = normalized;
    return true;
  }

  refreshTailRig(vrm.scene);

  return Object.freeze({
    version: LEGACY_PERFORMANCE_DRIVER_VERSION,
    update,
    setQinggan,
    applyBodyPerformance,
    markTalking,
    setSpeechPlan,
    applySpeechBoundary,
    setSpeaking,
    setSpeechEnergy,
    setConversationState,
    snapshot: () => Object.freeze({
      idleTime: state.idleTime,
      performance: state.performance === null ? null : Object.freeze({ ...state.performance }),
      conversationState: state.conversationState,
      qinggan: Object.freeze({ ...state.qinggan }),
      speaking: state.speaking,
      speechEnergy: state.speechEnergy,
      talkUntil: state.talkUntil,
      gestureProsody: state.gestureProsody ? Object.freeze({
        startedAt: state.gestureProsody.startedAt,
        timeline: state.gestureProsody.timeline,
      }) : null,
      gestureLastStrokeAt: state.gestureLastStrokeAt,
      gestureSuppressedCount: state.gestureSuppressedCount,
      idleDynamics: state.idleDynamics.snapshot(),
      blink: state.socialBlink.snapshot(),
      tailDynamics: Object.freeze({
        amplitude: state.tailDynamics.amplitude,
        tempo: state.tailDynamics.tempo,
        phase: state.tailDynamics.phase,
      }),
      expression: Object.freeze({ ...state.expression }),
      tailBones: state.tailRig.bones.map((bone) => bone.name),
    }),
  });
}
