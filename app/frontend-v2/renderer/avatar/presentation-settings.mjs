// Durable, engine-agnostic avatar presentation contract.
// Values are shared by the direct renderer, the HTTP settings projection and
// the legacy HTML migration path.  Keep this module free of DOM/THREE imports
// so it can be exercised by the Node regression suite.

export const AVATAR_CAMERA_LIMITS = Object.freeze({
  focus: Object.freeze([-0.5, 0.5]),
  height: Object.freeze([-0.5, 0.5]),
  distance: Object.freeze([-2, 2]),
  side: Object.freeze([-1, 1]),
});

export const AVATAR_LIGHTING_LIMITS = Object.freeze({
  key: Object.freeze([0.15, 3]),
  angle: Object.freeze([-1.8, 1.8]),
  ambient: Object.freeze([0.15, 2.4]),
  exposure: Object.freeze([0.55, 1.9]),
});

export const AVATAR_CAMERA_DEFAULTS = Object.freeze({
  focus: 0,
  height: 0,
  distance: 0,
  side: 0,
});

export const AVATAR_LIGHTING_DEFAULTS = Object.freeze({
  key: 1,
  angle: 0,
  ambient: 1,
  exposure: 1,
});

function boundedNumber(value, limits, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(limits[0], Math.min(limits[1], number));
}

export function normalizeAvatarPresentationGroup(value, defaults, limits) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const normalized = {};
  for (const [key, fallback] of Object.entries(defaults)) {
    normalized[key] = boundedNumber(source[key], limits[key], fallback);
  }
  return normalized;
}

export function normalizeAvatarPresentation(value = {}) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    camera: normalizeAvatarPresentationGroup(
      source.camera,
      AVATAR_CAMERA_DEFAULTS,
      AVATAR_CAMERA_LIMITS,
    ),
    lighting: normalizeAvatarPresentationGroup(
      source.lighting,
      AVATAR_LIGHTING_DEFAULTS,
      AVATAR_LIGHTING_LIMITS,
    ),
  };
}

