// P5 §16 BodyRuntimeProfile：动作幅度/节奏/物理感/镜头/质量档位。
// 原则（§16.3）：只调 motionScale/gestureScale/gazeSpeed/springDamping/cameraDistance/qualityTier，
// 绝不覆盖后端输出的姿态、手势、表情语义——携带语义键的输入直接拒绝。
// 持久化：注入式 storage（localStorage 形 {getItem,setItem} 或异步 {read,write}），渲染端不持路径。

import { deepFreeze } from "./canonical-hash.mjs";

export const BODY_RUNTIME_PROFILE_SCHEMA_VERSION = 1;
export const PROFILE_STORAGE_KEY = "tiangong.avatar.body-runtime-profile";

export const QUALITY_TIERS = Object.freeze(["low", "medium", "high", "auto"]);

export const PROFILE_DEFAULTS = Object.freeze({
  motionScale: 1,
  gestureScale: 1,
  gazeSpeed: 1,
  springDamping: 0.5,
  cameraDistance: 1,
  qualityTier: "auto",
});

export const PROFILE_LIMITS = Object.freeze({
  motionScale: Object.freeze([0, 2]),
  gestureScale: Object.freeze([0, 2]),
  gazeSpeed: Object.freeze([0.1, 4]),
  springDamping: Object.freeze([0, 1]),
  cameraDistance: Object.freeze([0.5, 2]),
});

// §16.3 禁止项：这些键属于后端语义，profile 不得携带。
const FORBIDDEN_SEMANTIC_KEYS = Object.freeze([
  "posture",
  "gesture",
  "expression",
  "gaze",
  "viseme",
  "speaking",
  "speechEnergy",
]);

export class BodyRuntimeProfileError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BodyRuntimeProfileError";
    this.code = code;
  }
}

function clampNumber(value, [min, max], fallback) {
  const num = Number(value);
  if (!Number.isFinite(num)) return fallback;
  return Math.max(min, Math.min(max, num));
}

// 清洗：未知键丢弃、数值按量程钳制、qualityTier 白名单、语义键拒绝（§16.3）。
export function sanitizeProfile(input, { strictSemantics = true } = {}) {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    throw new BodyRuntimeProfileError("profile_invalid", "profile 必须是对象");
  }
  if (strictSemantics) {
    for (const key of Object.keys(input)) {
      if (FORBIDDEN_SEMANTIC_KEYS.includes(key)) {
        throw new BodyRuntimeProfileError(
          "profile_semantic_override",
          `profile 不得覆盖后端语义键 ${key}（§16.3：只调幅度/节奏/物理感/镜头/质量）`,
        );
      }
    }
  }
  const merged = { ...PROFILE_DEFAULTS, ...input };
  return deepFreeze({
    motionScale: clampNumber(merged.motionScale, PROFILE_LIMITS.motionScale, PROFILE_DEFAULTS.motionScale),
    gestureScale: clampNumber(merged.gestureScale, PROFILE_LIMITS.gestureScale, PROFILE_DEFAULTS.gestureScale),
    gazeSpeed: clampNumber(merged.gazeSpeed, PROFILE_LIMITS.gazeSpeed, PROFILE_DEFAULTS.gazeSpeed),
    springDamping: clampNumber(merged.springDamping, PROFILE_LIMITS.springDamping, PROFILE_DEFAULTS.springDamping),
    cameraDistance: clampNumber(merged.cameraDistance, PROFILE_LIMITS.cameraDistance, PROFILE_DEFAULTS.cameraDistance),
    qualityTier: QUALITY_TIERS.includes(merged.qualityTier) ? merged.qualityTier : PROFILE_DEFAULTS.qualityTier,
  });
}

// 注入式 storage 归一：
//   同步 localStorage 形 { getItem(key), setItem(key, value) }
//   异步形 { read(key) → string|null, write(key, value) }
function normalizeStorage(storage) {
  if (storage === null || typeof storage !== "object") {
    throw new BodyRuntimeProfileError("storage_invalid", "BodyRuntimeProfile 需要注入 storage");
  }
  if (typeof storage.getItem === "function" && typeof storage.setItem === "function") {
    return {
      read: (key) => storage.getItem(key),
      write: (key, value) => storage.setItem(key, value),
    };
  }
  if (typeof storage.read === "function" && typeof storage.write === "function") {
    return { read: (key) => storage.read(key), write: (key, value) => storage.write(key, value) };
  }
  throw new BodyRuntimeProfileError("storage_invalid", "storage 需要 {getItem,setItem} 或 {read,write} 形态");
}

export function createBodyRuntimeProfileStore({
  storage,
  storageKey = PROFILE_STORAGE_KEY,
} = {}) {
  const backend = normalizeStorage(storage);
  let current = sanitizeProfile({});

  async function load() {
    const raw = await backend.read(storageKey);
    if (raw === null || raw === undefined || raw === "") {
      current = sanitizeProfile({});
      return current;
    }
    let parsed = null;
    try {
      parsed = JSON.parse(String(raw));
    } catch (_error) {
      // 损坏的持久化：安全回退默认值，不抛给调用方（§16.1 保存项不得破坏启动）。
      current = sanitizeProfile({});
      return current;
    }
    // 持久化里的语义键按丢弃处理（非 strict），保证旧版本数据可载入。
    const cleaned = parsed === null || typeof parsed !== "object" ? {} : { ...parsed };
    for (const key of FORBIDDEN_SEMANTIC_KEYS) delete cleaned[key];
    current = sanitizeProfile(cleaned);
    return current;
  }

  async function save(next) {
    current = sanitizeProfile(next);
    await backend.write(storageKey, JSON.stringify(current));
    return current;
  }

  // 应用到运行时：只经 AvatarRuntime.applyProfile 公共接口（§7.1），不触碰引擎内部。
  function applyToRuntime(runtime) {
    if (runtime === null || typeof runtime !== "object" || typeof runtime.applyProfile !== "function") {
      throw new BodyRuntimeProfileError("runtime_invalid", "applyToRuntime 需要 AvatarRuntime.applyProfile");
    }
    runtime.applyProfile(current);
    return current;
  }

  return deepFreeze({
    load,
    save,
    applyToRuntime,
    get current() {
      return current;
    },
    get storageKey() {
      return storageKey;
    },
  });
}
