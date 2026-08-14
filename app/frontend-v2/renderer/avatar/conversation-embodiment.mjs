// H3 Conversation Embodiment State: conversation role -> canonical body intent.
// It submits into the existing BodyCommandScheduler; it is not a second runtime
// or a second body-state authority.

import { deepFreeze } from "./canonical-hash.mjs";
import {
  ConversationEmbodimentState,
  gazeForConversationState,
  normalizeConversationEmbodimentState,
} from "./social-attention.mjs";

export { ConversationEmbodimentState };

export const EMBODIMENT_PHASE_EVENT_NAME = "tiangong-conversation-embodiment";

const POSTURE_BY_STATE = Object.freeze({
  IDLE: "relaxed",
  LISTENING: "attentive",
  THINKING: "thoughtful",
  TURN_ACQUIRING: "attentive",
  SPEAKING: "steady",
  TURN_YIELDING: "relaxed",
});

export function dispatchEmbodimentPhase(state, { target = null, meta = null } = {}) {
  const sink = target ?? (typeof window !== "undefined" ? window : null);
  if (sink === null || typeof sink.dispatchEvent !== "function") return false;
  const EventCtor = sink.CustomEvent ?? (typeof CustomEvent !== "undefined" ? CustomEvent : null);
  if (EventCtor === null) return false;
  const normalized = normalizeConversationEmbodimentState(state);
  try {
    sink.dispatchEvent(new EventCtor(EMBODIMENT_PHASE_EVENT_NAME, {
      detail: { state: normalized, meta: meta && typeof meta === "object" ? { ...meta } : null },
    }));
    return true;
  } catch (_error) {
    return false;
  }
}

export function createConversationEmbodimentController({
  nowMonotonic,
  submit,
  setTimer = (callback, delay) => setTimeout(callback, delay),
  clearTimer = (timer) => clearTimeout(timer),
  yieldingMs = 420,
  responseReadyMs = 700,
} = {}) {
  if (typeof nowMonotonic !== "function" || typeof submit !== "function") {
    throw new TypeError("ConversationEmbodimentController 需要 nowMonotonic 和 submit");
  }
  let state = ConversationEmbodimentState.IDLE;
  let sequence = 0;
  let timer = null;

  function cancelTimer() {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }

  function bodyIntent(next, reason) {
    sequence += 1;
    return {
      type: "conversation-state",
      conversationState: next,
      gaze: { target: gazeForConversationState(next) },
      posture: POSTURE_BY_STATE[next] ?? "relaxed",
      intensity: next === ConversationEmbodimentState.SPEAKING ? 0.42 : 0.3,
      durationMs: 650,
      ttlMs: 2_000,
      priority: "high",
      turnId: `embodiment-${sequence}`,
      sequence: 0,
      sourceCreatedAt: nowMonotonic(),
      extras: { source: "conversation-embodiment", reason: String(reason ?? "") },
    };
  }

  function transition(nextState, { reason = null, settle = true } = {}) {
    const next = normalizeConversationEmbodimentState(nextState);
    cancelTimer();
    state = next;
    submit(bodyIntent(next, reason));
    if (settle && next === ConversationEmbodimentState.TURN_YIELDING) {
      timer = setTimer(() => transition(ConversationEmbodimentState.IDLE, { reason: "yield-complete", settle: false }), yieldingMs);
    } else if (settle && next === ConversationEmbodimentState.TURN_ACQUIRING) {
      timer = setTimer(() => transition(ConversationEmbodimentState.TURN_YIELDING, { reason: "silent-response", settle: true }), responseReadyMs);
    }
    return state;
  }

  function handleSpeechPhase(phase) {
    const value = String(phase ?? "").toLowerCase();
    if (value === "start") return transition(ConversationEmbodimentState.SPEAKING, { reason: "speech-start", settle: false });
    if (value === "stop") return transition(ConversationEmbodimentState.TURN_YIELDING, { reason: "speech-stop" });
    return state;
  }

  function dispose() {
    cancelTimer();
  }

  return deepFreeze({
    transition,
    handleSpeechPhase,
    dispose,
    get state() { return state; },
  });
}
