// H2 Speech Motor Planner: text/provider timing -> canonical viseme timeline.
// Audio energy remains an amplitude modifier; it is not the mouth-shape source.

export const SPEECH_VISEMES = Object.freeze(["aa", "ih", "ou", "ee", "oh"]);

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function visemeForSpeechCharacter(character, index = 0) {
  const value = String(character ?? "").toLowerCase();
  if (/[a啊阿哈]/u.test(value)) return "aa";
  if (/[i一衣伊]/u.test(value)) return "ih";
  if (/[uü乌呜]/u.test(value)) return "ou";
  if (/[e诶额]/u.test(value)) return "ee";
  if (/[o哦喔]/u.test(value)) return "oh";
  const code = value.codePointAt(0) ?? index;
  return SPEECH_VISEMES[Math.abs(code + index) % SPEECH_VISEMES.length];
}

function speechCharacters(text) {
  return Array.from(String(text ?? ""))
    .map((character, charIndex) => ({ character, charIndex }))
    .filter(({ character }) => !/[\s,.;:!?，。！？、；：'"“”‘’\-]/u.test(character));
}

export function normalizeSpeechMotorPlan(plan) {
  if (plan === null || typeof plan !== "object" || !Array.isArray(plan.items)) return null;
  const items = plan.items
    .map((item) => ({
      atMs: Number(item?.atMs ?? item?.timeMs ?? item?.offsetMs),
      viseme: String(item?.viseme ?? "").toLowerCase(),
      strength: clamp(Number(item?.strength ?? 0.7) || 0.7, 0, 1),
      charIndex: Number.isFinite(Number(item?.charIndex)) ? Math.max(0, Number(item.charIndex)) : null,
    }))
    .filter((item) => Number.isFinite(item.atMs) && item.atMs >= 0 && SPEECH_VISEMES.includes(item.viseme))
    .sort((a, b) => a.atMs - b.atMs)
    .slice(0, 4096);
  if (!items.length) return null;
  const durationMs = clamp(Number(plan.durationMs) || items.at(-1).atMs + 120, 120, 120_000);
  return Object.freeze({
    source: String(plan.source || "provider"),
    durationMs,
    items: Object.freeze(items.map((item) => Object.freeze(item))),
  });
}

export function createSpeechMotorPlan(text, { durationMs = null } = {}) {
  const characters = speechCharacters(text);
  if (!characters.length) return null;
  const estimated = clamp(characters.length * 92, 700, 120_000);
  const duration = clamp(Number(durationMs) || estimated, 120, 120_000);
  const items = characters.map(({ character, charIndex }, index) => Object.freeze({
    atMs: duration * (index + 0.35) / characters.length,
    viseme: visemeForSpeechCharacter(character, index),
    strength: 0.56 + ((character.codePointAt(0) + index) % 5) * 0.06,
    charIndex,
  }));
  return Object.freeze({ source: "text-estimate", durationMs: duration, items: Object.freeze(items) });
}

export function speechPlanFromPayload({ text = "", speechPlan = null, durationMs = null } = {}) {
  return normalizeSpeechMotorPlan(speechPlan) ?? createSpeechMotorPlan(text, { durationMs });
}
