// §9.4 Quarantine：三类隔离键、计数窗口（单会话 2 次 / 滚动 24h 3 次）、
// 带 schemaVersion 的 QuarantinePolicy。
// 键构成即语义：gpuFingerprint 变化只影响 runtime 键（旧计数不继承、记录保留），
// structural 键不含 GPU/引擎信息，驱动或引擎升级均不影响结构性隔离。

import { canonicalSha256, deepFreeze } from "./canonical-hash.mjs";
import {
  AVATAR_STORAGE_LAYOUT,
  assertSchemaVersionSupported,
  readJsonFile,
  writeJsonAtomic,
} from "./storage-adapter.mjs";

export const QUARANTINE_POLICY_SCHEMA_VERSION = 1;

// V2.1.2 初始阈值（§9.4：必须放入带版本的 QuarantinePolicy，不得硬编码散落）。
export const DEFAULT_QUARANTINE_POLICY = Object.freeze({
  schemaVersion: QUARANTINE_POLICY_SCHEMA_VERSION,
  sessionCrashThreshold: 2, // 单会话内同一 runtimeQuarantineKey 崩溃达到 2 次
  rollingWindowMs: 24 * 3600 * 1000, // 滚动窗口 24h
  rollingCrashThreshold: 3, // 滚动窗口内同一 runtimeQuarantineKey 崩溃达到 3 次
  engineFailureThreshold: 2, // 同一 engineQuarantineKey 确定性解析失败/超时重复次数
  contextLostThreshold: 2, // 受控复现条件下连续 context lost 阈值
});

export const QuarantineCategory = Object.freeze({
  STRUCTURAL: "structural",
  ENGINE: "engine",
  RUNTIME: "runtime",
});
export const QUARANTINE_CATEGORIES = Object.freeze(Object.values(QuarantineCategory));

export class QuarantineError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "QuarantineError";
    this.code = code;
  }
}

const HEX64 = /^[0-9a-f]{64}$/;

function assertContentHash(contentHash) {
  if (!HEX64.test(contentHash ?? "")) {
    throw new QuarantineError("content_hash_invalid", "quarantine 键需要 64 位小写 hex contentHash");
  }
}

// §9.4 gpuFingerprint 五要素，缺一直接拒绝（不得退化为弱指纹）。
export function normalizeGpuFingerprint(gpuFingerprint) {
  if (gpuFingerprint === null || typeof gpuFingerprint !== "object") {
    throw new QuarantineError("gpu_fingerprint_invalid", "gpuFingerprint 必须是对象");
  }
  const fields = ["gpuVendorId", "gpuDeviceId", "driverVersion", "angleBackend", "osGraphicsBuild"];
  const normalized = {};
  for (const field of fields) {
    const value = gpuFingerprint[field];
    if (value === undefined || value === null || value === "") {
      throw new QuarantineError("gpu_fingerprint_invalid", `gpuFingerprint.${field} 缺失`);
    }
    normalized[field] = String(value);
  }
  return normalized;
}

export function structuralQuarantineKey({ contentHash, validatorVersion }) {
  assertContentHash(contentHash);
  if (typeof validatorVersion !== "string" || validatorVersion.length === 0) {
    throw new QuarantineError("validator_version_invalid", "structural 键需要 validatorVersion");
  }
  return `sq_${canonicalSha256({ kind: "structural", contentHash, validatorVersion })}`;
}

export function engineQuarantineKey({ contentHash, engineVersion }) {
  assertContentHash(contentHash);
  if (typeof engineVersion !== "string" || engineVersion.length === 0) {
    throw new QuarantineError("engine_version_invalid", "engine 键需要 engineVersion");
  }
  return `eq_${canonicalSha256({ kind: "engine", contentHash, engineVersion })}`;
}

export function runtimeQuarantineKey({ contentHash, engineVersion, gpuFingerprint }) {
  assertContentHash(contentHash);
  if (typeof engineVersion !== "string" || engineVersion.length === 0) {
    throw new QuarantineError("engine_version_invalid", "runtime 键需要 engineVersion");
  }
  return `rq_${canonicalSha256({
    kind: "runtime",
    contentHash,
    engineVersion,
    gpuFingerprint: normalizeGpuFingerprint(gpuFingerprint),
  })}`;
}

// 按失败类别选键（§9.4）：结构预检/哈希/URI/格式 → structural；
// 确定性解析失败与解析超时 → engine；renderer 崩溃/context lost/GPU 可见性失败 → runtime。
export function quarantineKeyForFailure(category, context) {
  switch (category) {
    case QuarantineCategory.STRUCTURAL:
      return structuralQuarantineKey(context);
    case QuarantineCategory.ENGINE:
      return engineQuarantineKey(context);
    case QuarantineCategory.RUNTIME:
      return runtimeQuarantineKey(context);
    default:
      throw new QuarantineError("quarantine_category_invalid", `未知失败类别: ${category}`);
  }
}

export function validateQuarantinePolicy(policy) {
  if (policy === null || typeof policy !== "object") {
    throw new QuarantineError("policy_invalid", "QuarantinePolicy 必须是对象");
  }
  if (!Number.isInteger(policy.schemaVersion) || policy.schemaVersion < 1) {
    throw new QuarantineError("policy_schema_invalid", "QuarantinePolicy 需要正整数 schemaVersion");
  }
  if (policy.schemaVersion > QUARANTINE_POLICY_SCHEMA_VERSION) {
    throw new QuarantineError(
      "policy_schema_unsupported",
      `QuarantinePolicy schemaVersion=${policy.schemaVersion} 高于已知 ${QUARANTINE_POLICY_SCHEMA_VERSION}，安全失败`,
    );
  }
  for (const key of ["sessionCrashThreshold", "rollingWindowMs", "rollingCrashThreshold", "engineFailureThreshold", "contextLostThreshold"]) {
    if (!Number.isInteger(policy[key]) || policy[key] <= 0) {
      throw new QuarantineError("policy_invalid", `QuarantinePolicy.${key} 必须为正整数`);
    }
  }
  return policy;
}

// 计数器：runtime 事件持久化（滚动 24h 窗口需要跨会话历史），单会话计数仅存内存；
// structural/engine 记录同样持久化，结构性隔离不随驱动/引擎升级解除。
export async function createQuarantineTracker({
  policy = DEFAULT_QUARANTINE_POLICY,
  storage = null,
  statePath = AVATAR_STORAGE_LAYOUT.quarantineStateFile,
  nowWallClock = () => Date.now(),
}) {
  validateQuarantinePolicy(policy);
  let state = { schemaVersion: QUARANTINE_POLICY_SCHEMA_VERSION, records: {} };
  if (storage !== null) {
    const existing = await readJsonFile(storage, statePath);
    assertSchemaVersionSupported(existing, QUARANTINE_POLICY_SCHEMA_VERSION, "QuarantineState");
    if (existing !== null) state = existing;
  }
  const sessionCounts = new Map(); // key → 本会话失败次数（崩溃计数随会话重置）

  async function persist() {
    if (storage !== null) {
      await writeJsonAtomic(storage, statePath, state);
    }
  }

  return {
    get policy() {
      return policy;
    },

    isQuarantined(key) {
      return state.records[key]?.quarantined === true;
    },

    getRecord(key) {
      const record = state.records[key];
      return record ? deepFreeze(structuredClone(record)) : null;
    },

    sessionCountOf(key) {
      return sessionCounts.get(key) ?? 0;
    },

    // 记录一次失败证据；返回 { key, quarantined, reason, sessionCount, rollingCount }。
    async recordFailure({ key, category, reason = null, at = null }) {
      if (typeof key !== "string" || !/^(sq|eq|rq)_[0-9a-f]{64}$/.test(key)) {
        throw new QuarantineError("quarantine_key_invalid", `非法 quarantine 键: ${key}`);
      }
      if (!QUARANTINE_CATEGORIES.includes(category)) {
        throw new QuarantineError("quarantine_category_invalid", `未知失败类别: ${category}`);
      }
      if (!key.startsWith(category === "structural" ? "sq_" : category === "engine" ? "eq_" : "rq_")) {
        throw new QuarantineError("quarantine_key_category_mismatch", `键 ${key.slice(0, 3)} 与类别 ${category} 不一致`);
      }
      const now = Number.isInteger(at) ? at : nowWallClock();
      const previous = state.records[key] ?? { key, category, events: [], quarantined: false, quarantinedAt: null, reason: null };
      const events = [...previous.events, now];
      const sessionCount = (sessionCounts.get(key) ?? 0) + 1;
      sessionCounts.set(key, sessionCount);
      const rollingCount = events.filter((ts) => ts >= now - policy.rollingWindowMs).length;

      let { quarantined } = previous;
      let quarantineReason = previous.reason;
      if (!quarantined) {
        if (category === QuarantineCategory.STRUCTURAL) {
          // 结构预检失败/哈希不一致/非法 URI/格式违规：单次即隔离（§9.4 条件 1/7）。
          quarantined = true;
          quarantineReason = reason ?? "structural_failure";
        } else if (category === QuarantineCategory.ENGINE) {
          if (events.length >= policy.engineFailureThreshold) {
            quarantined = true;
            quarantineReason = "engine_failure_threshold";
          }
        } else if (sessionCount >= policy.sessionCrashThreshold) {
          quarantined = true;
          quarantineReason = "session_crash_threshold";
        } else if (rollingCount >= policy.rollingCrashThreshold) {
          quarantined = true;
          quarantineReason = "rolling_crash_threshold";
        }
      }
      state.records[key] = {
        key,
        category,
        events,
        quarantined,
        quarantinedAt: quarantined ? previous.quarantinedAt ?? now : null,
        reason: quarantineReason,
      };
      await persist();
      return deepFreeze({ key, quarantined, reason: quarantineReason, sessionCount, rollingCount });
    },
  };
}
