// H4 Gesture Prosody Planner
//
// Converts a gesture intent into a sparse preparation -> stroke -> retraction
// timeline.  The stroke is anchored to a linguistically/prosodically prominent
// point, never to the beginning of the sentence merely because TTS started.

export const GESTURE_PROSODY_SCHEMA_VERSION = 1;

const GENERIC_GESTURES = new Set(["co_speech", "nod", "tilt", "hand_to_chest"]);
const PROMINENCE_MARKERS = Object.freeze([
  [/(真正|关键|重点|核心|必须|务必|尤其|最重要)/gu, 1.0],
  [/(不是|而是|但是|不过|然而|相反)/gu, 0.92],
  [/(首先|其次|最后|因此|所以|结论|原因)/gu, 0.82],
  [/\b(not|but|however|rather|key|must|therefore|first|finally)\b/giu, 0.88],
]);

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function speechDurationMs(speechPlan, text) {
  const provided = Number(speechPlan?.durationMs);
  if (Number.isFinite(provided) && provided > 0) return clamp(provided, 120, 120_000);
  const count = Array.from(String(text ?? "").replace(/\s/gu, "")).length;
  return clamp(count * 92, 700, 120_000);
}

function timeForCharIndex(charIndex, speechPlan, text, durationMs) {
  const items = Array.isArray(speechPlan?.items) ? speechPlan.items : [];
  const indexed = items
    .filter((item) => Number.isFinite(Number(item?.charIndex)) && Number.isFinite(Number(item?.atMs)))
    .sort((a, b) => Math.abs(Number(a.charIndex) - charIndex) - Math.abs(Number(b.charIndex) - charIndex));
  if (indexed.length) return clamp(Number(indexed[0].atMs), 0, durationMs);
  const length = Math.max(1, String(text ?? "").length);
  return clamp(durationMs * (charIndex + 0.5) / length, 0, durationMs);
}

function collectProminences(text, speechPlan, durationMs) {
  const source = String(text ?? "");
  const candidates = [];
  for (const [pattern, score] of PROMINENCE_MARKERS) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      candidates.push({
        charIndex: match.index,
        atMs: timeForCharIndex(match.index, speechPlan, source, durationMs),
        score,
        reason: "lexical-prominence",
      });
      if (match[0].length === 0) pattern.lastIndex += 1;
    }
  }
  Array.from(source).forEach((character, charIndex) => {
    if (!/[!?！？]/u.test(character)) return;
    candidates.push({
      charIndex,
      atMs: timeForCharIndex(charIndex, speechPlan, source, durationMs),
      score: 0.86,
      reason: "intonational-boundary",
    });
  });
  return candidates.sort((a, b) => b.score - a.score || a.atMs - b.atMs);
}

function fallbackProminence(text, speechPlan, durationMs) {
  const source = String(text ?? "");
  const charIndex = Math.max(0, Math.floor(source.length * 0.58));
  return {
    charIndex,
    atMs: timeForCharIndex(charIndex, speechPlan, source, durationMs),
    score: 0.62,
    reason: "semantic-intent-fallback",
  };
}

export function createGestureProsodyPlan({
  text = "",
  speechPlan = null,
  gesture = "co_speech",
  intensity = 0.55,
  maxItems = 2,
  minStrokeGapMs = 1_250,
} = {}) {
  const semantic = String(gesture ?? "").trim();
  const source = String(text ?? "").trim();
  if (!GENERIC_GESTURES.has(semantic) || !source) return null;
  const durationMs = speechDurationMs(speechPlan, source);
  const candidates = collectProminences(source, speechPlan, durationMs);
  // Generic beat gestures require an actual prominence.  More meaningful
  // explicit gestures may use one conservative mid-utterance fallback.
  if (!candidates.length && semantic === "co_speech") return null;
  if (!candidates.length) candidates.push(fallbackProminence(source, speechPlan, durationMs));

  const selected = [];
  const limit = semantic === "co_speech" ? 1 : clamp(Math.floor(maxItems), 1, 2);
  for (const candidate of candidates) {
    if (selected.some((item) => Math.abs(item.atMs - candidate.atMs) < minStrokeGapMs)) continue;
    selected.push(candidate);
    if (selected.length >= limit) break;
  }
  selected.sort((a, b) => a.atMs - b.atMs);
  const strength = clamp(Number(intensity) || 0.55, 0.28, 0.88);
  const items = selected.map((candidate) => {
    const preparationMs = semantic === "nod" ? 150 : 220;
    const strokeMs = semantic === "nod" ? 210 : 260;
    const retractionMs = semantic === "nod" ? 280 : 420;
    const startMs = Math.max(0, candidate.atMs - preparationMs - strokeMs * 0.45);
    return Object.freeze({
      gesture: semantic,
      charIndex: candidate.charIndex,
      prominenceAtMs: candidate.atMs,
      startMs,
      strokeAtMs: candidate.atMs,
      endMs: Math.min(durationMs + 350, candidate.atMs + strokeMs * 0.55 + retractionMs),
      strength: clamp(strength * (0.82 + candidate.score * 0.18), 0.28, 0.9),
      reason: candidate.reason,
    });
  });
  if (!items.length) return null;
  return Object.freeze({
    schemaVersion: GESTURE_PROSODY_SCHEMA_VERSION,
    source: "speech-prominence",
    durationMs,
    minStrokeGapMs,
    items: Object.freeze(items),
  });
}

export function gestureMotionAt(plan, elapsedMs) {
  if (!plan || !Array.isArray(plan.items)) return null;
  const now = Number(elapsedMs);
  if (!Number.isFinite(now)) return null;
  const item = plan.items.find((candidate) => now >= candidate.startMs && now <= candidate.endMs);
  if (!item) return null;
  const beforeStroke = Math.max(1, item.strokeAtMs - item.startMs);
  const afterStroke = Math.max(1, item.endMs - item.strokeAtMs);
  const envelope = now <= item.strokeAtMs
    ? clamp((now - item.startMs) / beforeStroke, 0, 1)
    : clamp(1 - (now - item.strokeAtMs) / afterStroke, 0, 1);
  return Object.freeze({
    ...item,
    elapsedMs: now,
    envelope,
    phase: now < item.strokeAtMs ? "preparation" : (now === item.strokeAtMs ? "stroke" : "retraction"),
  });
}
