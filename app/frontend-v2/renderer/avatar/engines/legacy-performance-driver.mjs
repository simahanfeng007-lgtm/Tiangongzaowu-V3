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

export const LEGACY_PERFORMANCE_DRIVER_VERSION = "legacy-performance-driver-1.1.0";

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

export function createLegacyPerformanceDriver({ vrm, applyExpression, mapViseme }) {
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
    idleShiftSeed: Math.random() * 6.28,
    qinggan: Object.fromEntries(EMOTION_KEYS.map((key) => [key, 0])),
    performance: null,
    talkUntil: 0,
    speechPlan: null,
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
    blinkTimer: 1.4,
    blinkPhase: 0,
    tailRig: Object.freeze({ bones: [], baseRotations: [] }),
  };

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
    state.speechPlan = { startedAt: state.idleTime, duration, items };
    state.talkUntil = Math.max(state.talkUntil, state.idleTime + duration);
  }

  function markTalking(text) {
    const active = state.performance !== null && state.idleTime < state.performance.until;
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
    const perf = state.performance !== null && state.idleTime < state.performance.until ? state.performance : null;
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
    target.angry = Math.min(1, target.angry * (expressionProfile.angerScale ?? 1));

    state.blinkTimer -= dt * (1 + clamp01(q.worry) * 0.45 + clamp01(q.fear) * 0.75);
    if (state.blinkTimer <= 0) {
      state.blinkPhase = 0.16;
      state.blinkTimer = 2.4 + Math.random() * 2.2 - clamp01(q.worry) * 0.75;
    }
    state.blinkPhase = Math.max(0, state.blinkPhase - dt);
    target.blink = state.blinkPhase > 0 ? Math.sin((state.blinkPhase / 0.16) * Math.PI) : 0;

    const k = 1 - Math.exp(-dt * 7);
    for (const [name, value] of Object.entries(target)) {
      state.expression[name] = THREE.MathUtils.lerp(state.expression[name] || 0, value, k);
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
      const perf = state.performance !== null && state.idleTime < state.performance.until ? state.performance : null;
      const pInt = perf ? perf.intensity : 0;
      const pAge = perf ? state.idleTime - perf.startedAt : 0;
      const pNorm = perf ? THREE.MathUtils.clamp(pAge / perf.duration, 0, 1) : 0;
      const pWave = perf ? Math.sin(pNorm * Math.PI) : 0;
      const gesture = perf?.gesture || (softTalk ? "co_speech" : "none");
      const speechPulse = softTalk * (0.5 + 0.5 * Math.sin(state.idleTime * 5.2 + state.idleShiftSeed));
      const coSpeech = (softTalk || gesture === "co_speech") * (0.24 + (perf ? pInt : 0.55) * 0.42);
      const shift = Math.sin(state.idleTime * 0.33 + state.idleShiftSeed);
      const sway = Math.sin(state.idleTime * 0.21 + state.idleShiftSeed * 0.7);
      const breath2 = Math.sin(state.idleTime * 0.74 + 1.2);
      const microLook = Math.sin(state.idleTime * 0.13 + state.idleShiftSeed * 0.5);
      const microSettle = Math.sin(state.idleTime * 0.17 + 2.1);
      const postureLean = {
        attentive: -0.012,
        bashful: 0.012,
        thoughtful: 0.002,
        steady: -0.004,
        relaxed: 0,
      }[perf?.posture || "relaxed"] || 0;
      let headX = -0.012 + breath * 0.003 + softTalk * 0.004 + speechPulse * 0.006 * coSpeech + (perf?.gaze === "down" ? 0.026 * pInt : 0);
      // 目光跟随镜头：headY 只保留自然微动（VRM LookAt 接管 gaze）
      let headY = Math.sin(state.idleTime * 0.26) * 0.014 + microLook * 0.010;
      let headZ = Math.sin(state.idleTime * 0.21) * 0.006 + microSettle * 0.004;
      if (gesture === "nod") headX += Math.sin(pNorm * Math.PI * 2.0) * 0.18 * pWave * Math.max(0.55, pInt);
      if (gesture === "tilt" || perf?.posture === "bashful") headZ += 0.14 * pWave * Math.max(0.50, pInt);
      // 自然站姿基准（2026-08-05 整臂重标定，修复前臂反向）。
      // 来源 JOSR 2022 上肢中立位（doi:10.1186/s13018-022-03113-5）：
      //   盂肱外展 4.5-10°、肘屈 15.5°、外翻 9.8°。
      // 在真实模型上以“前进方向”逐关节实测反推（前一次标定把前进方向取反，
      // 导致前臂后弯、整条胳膊看起来反了）：
      //   rightUpperArm = (0.000, 0.000, -1.4399)    → 外展 7.5°
      //   rightLowerArm = (0.400, 0.300, 0.100)      → 前臂向前屈 ~18°（手在肩平面前 7cm）
      //   rightHand     = (-0.200, -3.000, 3.100)    → 真实指尖对齐前臂（弯曲 0.39°），掌心朝内、拇指朝前
      // 左臂按镜像约定取反（X 同号、Y/Z 反号）。
      let rUpperZ = -1.4399 + breath * 0.004 + armEase * 0.05 + sway * 0.006;
      let lUpperZ = 1.4399 - breath * 0.004 - armEase * 0.05 + sway * 0.005;
      let rLowerZ = 0.100 + armEase * 0.018;
      let lLowerZ = -0.100 - armEase * 0.018;
      let rUpperX = 0 + armEase * 0.035;
      let lUpperX = 0 - armEase * 0.030;
      let rUpperY = 0 + shift * 0.004;
      let lUpperY = 0 + shift * 0.004;
      let rLowerX = 0.400 + softTalk * 0.008 + speechPulse * 0.006 * coSpeech;
      let lLowerX = 0.400 + softTalk * 0.007 + Math.sin(state.idleTime * 3.0 + 0.9) * 0.003 * coSpeech;
      let rLowerY = 0.300;
      let lLowerY = -0.300;
      let rHandX = -0.200 + Math.sin(state.idleTime * 0.82 + 0.4) * 0.005;
      let lHandX = -0.200 + Math.sin(state.idleTime * 0.76 + 1.1) * 0.005;
      let rHandY = -3.000 + Math.sin(state.idleTime * 0.53 + 0.2) * 0.005;
      let lHandY = 3.000 + Math.sin(state.idleTime * 0.49 + 1.0) * 0.005;
      let rHandZ = 3.100 + armEase * 0.024 + Math.sin(state.idleTime * 0.9) * 0.005;
      let lHandZ = -3.100 - armEase * 0.024 + Math.sin(state.idleTime * 0.78 + 1) * 0.005;
      if (coSpeech) {
        rUpperX += speechPulse * 0.010 * coSpeech;
        lUpperX += Math.sin(state.idleTime * 4.1 + 1.1) * 0.006 * coSpeech;
        rLowerZ += Math.sin(state.idleTime * 3.4) * 0.012 * coSpeech;
        lLowerZ -= Math.sin(state.idleTime * 2.8 + 0.6) * 0.010 * coSpeech;
        rHandX += Math.sin(state.idleTime * 4.3 + 0.3) * 0.008 * coSpeech;
        lHandX += Math.sin(state.idleTime * 3.9 + 1.2) * 0.006 * coSpeech;
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
      const fingerCurl = (side, finger, base, mid, tip, spread = 0) => {
        const sign = side === "right" ? 1 : -1;
        const proximal = humanoid.getNormalizedBoneNode(`${side}${finger}Proximal`);
        const intermediate = humanoid.getNormalizedBoneNode(`${side}${finger}Intermediate`);
        const distal = humanoid.getNormalizedBoneNode(`${side}${finger}Distal`);
        if (proximal) {
          proximal.rotation.x = base + fingerPulse;
          proximal.rotation.y = spread * sign;
          proximal.rotation.z = 0;
        }
        if (intermediate) {
          intermediate.rotation.x = mid + fingerPulse * 0.8;
          intermediate.rotation.y = 0;
          intermediate.rotation.z = 0;
        }
        if (distal) {
          distal.rotation.x = tip + fingerPulse * 0.55;
          distal.rotation.y = 0;
          distal.rotation.z = 0;
        }
      };
      const thumbCurl = (side) => {
        const sign = side === "right" ? 1 : -1;
        const metacarpal = humanoid.getNormalizedBoneNode(`${side}ThumbMetacarpal`);
        const proximal = humanoid.getNormalizedBoneNode(`${side}ThumbProximal`);
        const distal = humanoid.getNormalizedBoneNode(`${side}ThumbDistal`);
        if (metacarpal) {
          metacarpal.rotation.x = 0.060 + fingerPulse * 0.35;
          metacarpal.rotation.y = 0.055 * sign;
          metacarpal.rotation.z = -0.045 * sign;
        }
        if (proximal) {
          proximal.rotation.x = 0.105 + fingerPulse * 0.45;
          proximal.rotation.y = 0.030 * sign;
          proximal.rotation.z = 0;
        }
        if (distal) {
          distal.rotation.x = 0.070 + fingerPulse * 0.35;
          distal.rotation.y = 0;
          distal.rotation.z = 0;
        }
      };
      for (const side of ["right", "left"]) {
        thumbCurl(side);
        fingerCurl(side, "Index", 0.090, 0.145, 0.075, 0.018);
        fingerCurl(side, "Middle", 0.115, 0.170, 0.095, 0.000);
        fingerCurl(side, "Ring", 0.130, 0.165, 0.095, -0.010);
        fingerCurl(side, "Little", 0.115, 0.145, 0.085, -0.020);
      }
    } catch (_error) {
      // 单帧姿态失败不阻断驱动
    }
  }

  // ── 尾巴（桌面宠物.html applyTailMotion parity）────────────────
  function applyTailMotion() {
    const { bones, baseRotations } = state.tailRig;
    if (!bones.length) return;
    const talking = state.idleTime < state.talkUntil ? 1 : 0;
    const mood = clamp01(state.qinggan.joy) + clamp01(state.qinggan.surprise) * 0.5;
    bones.forEach((bone, index) => {
      const base = baseRotations[index];
      if (!bone || !base) return;
      const phase = state.idleTime * (0.86 + index * 0.05) - index * 0.46;
      const fade = THREE.MathUtils.clamp(1 - index * 0.075, 0.38, 1);
      const amp = (0.045 + talking * 0.030 + mood * 0.018) * fade;
      bone.rotation.x = base.x + Math.sin(phase * 0.72 + 0.8) * amp * 0.28;
      bone.rotation.y = base.y + Math.sin(phase) * amp;
      bone.rotation.z = base.z + Math.cos(phase * 0.82 + index * 0.55) * amp * 0.62;
    });
  }

  // ── 帧驱动 ──────────────────────────────────────────────────
  function update(dt, { gestureActive = false } = {}) {
    const seconds = Math.max(0, Number(dt) || 0);
    state.idleTime += seconds;
    if (state.speaking && state.idleTime >= state.speakingUntil) {
      state.speaking = false;
      state.speechEnergy = 0;
    }
    if (!gestureActive) {
      // parity：VRMA 动作播放时由动作驱动全身；无动作时自然站姿。
      resetPortraitPose();
      applyNaturalPose();
    }
    applyTailMotion();
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

  refreshTailRig(vrm.scene);

  return Object.freeze({
    version: LEGACY_PERFORMANCE_DRIVER_VERSION,
    update,
    setQinggan,
    applyBodyPerformance,
    markTalking,
    setSpeaking,
    setSpeechEnergy,
    snapshot: () => Object.freeze({
      idleTime: state.idleTime,
      performance: state.performance === null ? null : Object.freeze({ ...state.performance }),
      qinggan: Object.freeze({ ...state.qinggan }),
      speaking: state.speaking,
      speechEnergy: state.speechEnergy,
      talkUntil: state.talkUntil,
      expression: Object.freeze({ ...state.expression }),
      tailBones: state.tailRig.bones.map((bone) => bone.name),
    }),
  });
}
