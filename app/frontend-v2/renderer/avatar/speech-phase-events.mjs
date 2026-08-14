// P6a §17 TTS 事件源补发：回复音频/TTS 播放位置的唯一 dispatch 助手。
//
// 纪律：
//   - 本模块只做"补事件"——不创建/不改变任何播放逻辑（§17：TTS 播放仍归
//     conversation-panel 单一所有）；每个 phase 一行 dispatch 的调用点都在
//     播放所有者侧（speakWithBrowser / playGeneratedVoice）。
//   - 事件形态（与 speech-event-forwarder attachWindowBridge 的消费端对齐）：
//       window.dispatchEvent(new CustomEvent("tiangong-speech", {
//         detail: { phase: "start" | "boundary" | "energy" | "stop", text?, speechPlan?, boundary?, energy?, at },
//       }))
//   - at 一律取注入的本地单调时钟（默认 performance.now()），事件自身不携带
//     任何系统墙钟语义（§17：避免系统时间变化导致口型错位）。
//   - 播放环境缺失（无 window/无 dispatchEvent）时安全返回 false，不抛错——
//     补事件永远不能击穿回复播放链。

export const SPEECH_PHASE_EVENT_NAME = "tiangong-speech";

export const SpeechPhase = Object.freeze({
  START: "start",
  BOUNDARY: "boundary",
  ENERGY: "energy",
  STOP: "stop",
});

const KNOWN_PHASES = Object.freeze(Object.values(SpeechPhase));

function clampEnergy(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  return Math.max(0, Math.min(1, num));
}

function defaultTarget() {
  return typeof window !== "undefined" ? window : null;
}

function defaultNow() {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

// phase 非法返回 false（不 dispatch）；可选载荷严格限制为口型/时序所需字段。
export function dispatchSpeechPhase(phase, {
  energy = null,
  text = null,
  speechPlan = null,
  boundary = null,
  durationMs = null,
  reason = null,
  target = null,
  nowMonotonic = null,
} = {}) {
  const normalized = String(phase ?? "").toLowerCase();
  if (!KNOWN_PHASES.includes(normalized)) return false;
  const sink = target ?? defaultTarget();
  if (sink === null || typeof sink.dispatchEvent !== "function") return false;
  const at = typeof nowMonotonic === "function" ? nowMonotonic() : defaultNow();
  const detail = { phase: normalized, at };
  if (typeof text === "string" && text.trim()) detail.text = text.slice(0, 10_000);
  if (speechPlan && typeof speechPlan === "object") detail.speechPlan = speechPlan;
  if (boundary && typeof boundary === "object") detail.boundary = { ...boundary };
  if (Number.isFinite(Number(durationMs)) && Number(durationMs) > 0) detail.durationMs = Number(durationMs);
  if (typeof reason === "string" && reason) detail.reason = reason;
  const clamped = clampEnergy(energy);
  if (normalized === SpeechPhase.ENERGY && clamped !== null) detail.energy = clamped;
  const EventCtor = sink.CustomEvent ?? (typeof CustomEvent !== "undefined" ? CustomEvent : null);
  if (EventCtor === null) return false;
  try {
    sink.dispatchEvent(new EventCtor(SPEECH_PHASE_EVENT_NAME, { detail }));
    return true;
  } catch (_error) {
    return false; // 宿主 dispatch 失败不阻断播放
  }
}
