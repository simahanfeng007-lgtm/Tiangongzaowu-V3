// H5 Human Idle Dynamics
//
// Low-frequency posture variation and blinking are stochastic at event level,
// then smoothed continuously.  This avoids both mechanical loops and per-frame
// random jitter.  Conversation state changes the policy instead of selecting a
// canned animation.

export const HUMAN_IDLE_DYNAMICS_SCHEMA_VERSION = 1;

const STATE_PROFILE = Object.freeze({
  IDLE: Object.freeze({ motion: 0.72, retargetMin: 2.8, retargetSpan: 3.8, blinkMin: 2.8, blinkSpan: 2.8 }),
  LISTENING: Object.freeze({ motion: 0.34, retargetMin: 4.0, retargetSpan: 3.5, blinkMin: 4.2, blinkSpan: 2.4 }),
  THINKING: Object.freeze({ motion: 0.52, retargetMin: 3.0, retargetSpan: 3.2, blinkMin: 3.4, blinkSpan: 2.6 }),
  TURN_ACQUIRING: Object.freeze({ motion: 0.62, retargetMin: 2.6, retargetSpan: 2.8, blinkMin: 3.0, blinkSpan: 2.2 }),
  SPEAKING: Object.freeze({ motion: 0.82, retargetMin: 2.4, retargetSpan: 2.6, blinkMin: 2.4, blinkSpan: 2.2 }),
  TURN_YIELDING: Object.freeze({ motion: 0.42, retargetMin: 3.4, retargetSpan: 3.2, blinkMin: 2.8, blinkSpan: 2.0 }),
});

const CAMERA_PROFILE = Object.freeze({
  IDLE: Object.freeze({ focus: 0, height: 0, distance: 0.055, side: 0 }),
  LISTENING: Object.freeze({ focus: 0.008, height: 0, distance: -0.025, side: 0 }),
  THINKING: Object.freeze({ focus: -0.012, height: 0, distance: 0.075, side: 0.008 }),
  TURN_ACQUIRING: Object.freeze({ focus: 0.006, height: 0, distance: 0.015, side: 0 }),
  SPEAKING: Object.freeze({ focus: 0.012, height: 0.004, distance: -0.035, side: 0 }),
  TURN_YIELDING: Object.freeze({ focus: 0, height: 0, distance: 0.035, side: -0.006 }),
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function normalizedState(value) {
  const key = String(value ?? "IDLE").trim().toUpperCase();
  return STATE_PROFILE[key] ? key : "IDLE";
}

function centered(random) {
  return clamp(Number(random()) || 0, 0, 1) * 2 - 1;
}

export function adaptiveFramingForConversationState(conversationState) {
  return CAMERA_PROFILE[normalizedState(conversationState)];
}

export function createHumanIdleDynamics({ random = Math.random } = {}) {
  if (typeof random !== "function") throw new TypeError("human idle dynamics requires random()");
  const state = {
    elapsed: 0,
    retargetIn: 0,
    values: { shift: 0, sway: 0, headYaw: 0, headRoll: 0, facialDrift: 0 },
    targets: { shift: 0, sway: 0, headYaw: 0, headRoll: 0, facialDrift: 0 },
  };

  function retarget(profile) {
    const scale = profile.motion;
    state.targets = {
      shift: centered(random) * scale,
      sway: centered(random) * scale,
      headYaw: centered(random) * scale,
      headRoll: centered(random) * scale,
      facialDrift: centered(random) * Math.min(1, scale + 0.15),
    };
    state.retargetIn = profile.retargetMin + clamp(Number(random()) || 0, 0, 1) * profile.retargetSpan;
  }

  function update(dt, conversationState = "IDLE") {
    const seconds = clamp(Number(dt) || 0, 0, 0.25);
    const profile = STATE_PROFILE[normalizedState(conversationState)];
    state.elapsed += seconds;
    state.retargetIn -= seconds;
    if (state.retargetIn <= 0) retarget(profile);
    const response = 1 - Math.exp(-seconds * 0.72);
    for (const key of Object.keys(state.values)) {
      state.values[key] += (state.targets[key] - state.values[key]) * response;
    }
    return Object.freeze({ ...state.values });
  }

  return Object.freeze({
    update,
    snapshot: () => Object.freeze({
      elapsed: state.elapsed,
      retargetIn: state.retargetIn,
      values: Object.freeze({ ...state.values }),
      targets: Object.freeze({ ...state.targets }),
    }),
  });
}

export function createSocialBlinkController({ random = Math.random } = {}) {
  if (typeof random !== "function") throw new TypeError("social blink controller requires random()");
  const state = {
    elapsed: 0,
    lastBlinkAt: 0,
    nextBlinkIn: 3.2,
    scheduledIn: null,
    phaseRemaining: 0,
    duration: 0.16,
    count: 0,
    lastReason: null,
  };

  function reschedule(conversationState) {
    const profile = STATE_PROFILE[normalizedState(conversationState)];
    state.nextBlinkIn = profile.blinkMin + clamp(Number(random()) || 0, 0, 1) * profile.blinkSpan;
  }

  function noteBoundary({ text = "", charIndex = 0, boundaryType = "word" } = {}) {
    const source = String(text ?? "");
    const index = clamp(Math.floor(Number(charIndex) || 0), 0, Math.max(0, source.length - 1));
    const neighborhood = `${source[index - 1] ?? ""}${source[index] ?? ""}${source[index + 1] ?? ""}`;
    const phraseBoundary = /[，,。.!！?？;；:：]/u.test(neighborhood) || /sentence|pause|end/i.test(String(boundaryType));
    const sinceLast = state.elapsed - state.lastBlinkAt;
    if (phraseBoundary && (state.count === 0 || sinceLast >= 0.9)) {
      state.scheduledIn = 0.12 + clamp(Number(random()) || 0, 0, 1) * 0.16;
      state.lastReason = "speech-boundary";
      return true;
    }
    if (sinceLast >= 2.2 && Number(random()) < 0.14) {
      state.scheduledIn = 0.18;
      state.lastReason = "feedback-slot";
      return true;
    }
    return false;
  }

  function update(dt, conversationState = "IDLE") {
    const seconds = clamp(Number(dt) || 0, 0, 0.25);
    const normalized = normalizedState(conversationState);
    state.elapsed += seconds;
    state.nextBlinkIn -= seconds;
    if (state.scheduledIn !== null) state.scheduledIn -= seconds;
    const scheduledReady = state.scheduledIn !== null && state.scheduledIn <= 0;
    const physiologicalReady = state.elapsed - state.lastBlinkAt >= 7.5;
    // During attentive listening, suppress opportunistic blinks but never beyond
    // the physiological ceiling. Boundary-triggered feedback blinks are kept.
    const automaticReady = state.nextBlinkIn <= 0 && normalized !== "LISTENING";
    if (state.phaseRemaining <= 0 && (scheduledReady || physiologicalReady || automaticReady)) {
      state.duration = 0.145 + clamp(Number(random()) || 0, 0, 1) * 0.045;
      state.phaseRemaining = state.duration;
      state.lastBlinkAt = state.elapsed;
      state.count += 1;
      if (!scheduledReady) state.lastReason = physiologicalReady ? "physiological-ceiling" : "state-hazard";
      state.scheduledIn = null;
      reschedule(normalized);
    }
    state.phaseRemaining = Math.max(0, state.phaseRemaining - seconds);
    const amount = state.phaseRemaining > 0
      ? Math.sin((state.phaseRemaining / state.duration) * Math.PI)
      : 0;
    return Object.freeze({ amount, count: state.count, reason: state.lastReason });
  }

  return Object.freeze({
    noteBoundary,
    update,
    snapshot: () => Object.freeze({ ...state }),
  });
}
