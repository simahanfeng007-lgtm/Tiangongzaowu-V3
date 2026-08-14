// H1 Social Attention: backend gaze semantics -> spatial targets.
// This module is deliberately Three.js-free so the semantic contract can be
// tested without creating a renderer.  The engine is responsible only for
// converting the returned point into its native lookAt target object.

export const SOCIAL_GAZE_TARGETS = Object.freeze([
  "camera",
  "user",
  "front",
  "down",
  "left",
  "right",
  "away",
  "reset",
]);

export const ConversationEmbodimentState = Object.freeze({
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  THINKING: "THINKING",
  TURN_ACQUIRING: "TURN_ACQUIRING",
  SPEAKING: "SPEAKING",
  TURN_YIELDING: "TURN_YIELDING",
});

const STATE_GAZE = Object.freeze({
  IDLE: "front",
  LISTENING: "user",
  THINKING: "down",
  TURN_ACQUIRING: "user",
  SPEAKING: "user",
  TURN_YIELDING: "away",
});

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function normalizeConversationEmbodimentState(value) {
  const normalized = String(value ?? "").trim().toUpperCase();
  return Object.values(ConversationEmbodimentState).includes(normalized)
    ? normalized
    : ConversationEmbodimentState.IDLE;
}

export function gazeForConversationState(value) {
  return STATE_GAZE[normalizeConversationEmbodimentState(value)] ?? "front";
}

// Offsets are relative to the current camera position and intentionally small:
// gaze semantics express social attention, not camera movement.
export function resolveSocialGazeTarget(name, { cameraPosition = null } = {}) {
  const requested = String(name ?? "").trim().toLowerCase();
  const semantic = SOCIAL_GAZE_TARGETS.includes(requested) ? requested : "camera";
  const base = {
    x: finite(cameraPosition?.x),
    y: finite(cameraPosition?.y),
    z: finite(cameraPosition?.z, 1),
  };
  const offsets = {
    camera: [0, 0, 0],
    user: [0, 0, 0],
    front: [0, 0, 0],
    reset: [0, 0, 0],
    down: [0, -0.52, -0.04],
    left: [-0.48, -0.03, -0.02],
    right: [0.48, -0.03, -0.02],
    away: [-0.62, -0.18, -0.08],
  };
  const [dx, dy, dz] = offsets[semantic];
  return Object.freeze({
    requested,
    semantic,
    degraded: requested !== semantic,
    point: Object.freeze({ x: base.x + dx, y: base.y + dy, z: base.z + dz }),
  });
}
