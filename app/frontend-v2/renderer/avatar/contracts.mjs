// Avatar P1 冻结公共契约（方案 §7/§8.2/§15/§18）。
// 纯数据、常量与校验函数：无副作用、无 DOM/时钟依赖，时钟一律由调用方注入。

// ── §18.1 RuntimeState ──────────────────────────────────────

export const RuntimeState = Object.freeze({
  UNINITIALIZED: "uninitialized",
  RUNNING: "running",
  DEGRADED: "degraded",
  CONTEXT_LOST: "context-lost",
  RECOVERING: "recovering",
  DISPOSING: "disposing",
  DISPOSED: "disposed",
});

export const RUNTIME_STATES = Object.freeze(Object.values(RuntimeState));

// RuntimeState 合法迁移表（§18.1/§18.4/§20.3）。
const RUNTIME_TRANSITIONS = Object.freeze({
  uninitialized: Object.freeze(["running", "degraded", "disposing"]),
  running: Object.freeze(["degraded", "context-lost", "disposing"]),
  degraded: Object.freeze(["running", "disposing"]),
  "context-lost": Object.freeze(["recovering", "disposing"]),
  recovering: Object.freeze(["running", "degraded", "disposing"]),
  disposing: Object.freeze(["disposed"]),
  disposed: Object.freeze([]),
});

export function isRuntimeTransitionAllowed(from, to) {
  return (RUNTIME_TRANSITIONS[from] || []).includes(to);
}

// ── §18.2 LoadAttemptState ──────────────────────────────────

export const LoadAttemptState = Object.freeze({
  SELECTING: "selecting",
  VALIDATING: "validating",
  ADMITTED: "admitted",
  LOADING: "loading",
  PARSING: "parsing",
  UPLOADING: "uploading",
  RENDERABILITY_PROBE: "renderability-probe",
  PROVISIONAL_PRESENT: "provisional-present",
  VISIBILITY_PROBE: "visibility-probe",
  SUSPENDED_PROBE: "suspended-probe",
  COMMITTED: "committed",
  REJECTED: "rejected",
  FAILED: "failed",
  CANCELLED: "cancelled",
  QUARANTINED: "quarantined",
});

export const LOAD_ATTEMPT_STATES = Object.freeze(Object.values(LoadAttemptState));

// 终态（§4.6）：进入后不再迁出。
export const TERMINAL_LOAD_ATTEMPT_STATES = Object.freeze([
  LoadAttemptState.COMMITTED,
  LoadAttemptState.REJECTED,
  LoadAttemptState.FAILED,
  LoadAttemptState.CANCELLED,
  LoadAttemptState.QUARANTINED,
]);

const S = LoadAttemptState;
// 主链严格按 §18.2；任何活动态可因错误/取消/隔离进入相应终态。
const CHAIN_FAILURE_TARGETS = Object.freeze([S.FAILED, S.CANCELLED, S.QUARANTINED]);
const LOAD_ATTEMPT_TRANSITIONS = Object.freeze({
  selecting: Object.freeze([S.VALIDATING, S.REJECTED, ...CHAIN_FAILURE_TARGETS]),
  validating: Object.freeze([S.ADMITTED, S.REJECTED, ...CHAIN_FAILURE_TARGETS]),
  admitted: Object.freeze([S.LOADING, S.REJECTED, ...CHAIN_FAILURE_TARGETS]),
  loading: Object.freeze([S.PARSING, ...CHAIN_FAILURE_TARGETS]),
  parsing: Object.freeze([S.UPLOADING, ...CHAIN_FAILURE_TARGETS]),
  uploading: Object.freeze([S.RENDERABILITY_PROBE, ...CHAIN_FAILURE_TARGETS]),
  "renderability-probe": Object.freeze([S.PROVISIONAL_PRESENT, ...CHAIN_FAILURE_TARGETS]),
  "provisional-present": Object.freeze([S.VISIBILITY_PROBE, ...CHAIN_FAILURE_TARGETS]),
  // committed 只能来自 visibility-probe（§18.2：FIRST_VISIBLE_FRAME 通过并完成原子交换）。
  "visibility-probe": Object.freeze([S.SUSPENDED_PROBE, S.COMMITTED, ...CHAIN_FAILURE_TARGETS]),
  // suspended-probe 只与 visibility-probe 互转；挂起预算耗尽只允许进 cancelled（§18.3.9/§19.1）。
  "suspended-probe": Object.freeze([S.VISIBILITY_PROBE, S.CANCELLED]),
  committed: Object.freeze([]),
  rejected: Object.freeze([]),
  failed: Object.freeze([]),
  cancelled: Object.freeze([]),
  quarantined: Object.freeze([]),
});

export function isLoadAttemptTransitionAllowed(from, to) {
  return (LOAD_ATTEMPT_TRANSITIONS[from] || []).includes(to);
}

export function isTerminalLoadAttemptState(state) {
  return TERMINAL_LOAD_ATTEMPT_STATES.includes(state);
}

// ── §18.2/§18.4 attemptKind 与 nullable rollbackTarget ─────

export const AttemptKind = Object.freeze({
  SWITCH: "switch",
  INITIAL_LOAD: "initial-load",
  RECOVERY: "recovery",
});

export const ATTEMPT_KINDS = Object.freeze(Object.values(AttemptKind));

// recovery attempt 的 rollbackTarget 必须为 null（§18.4：旧 GPU 资源已失效，不得回滚）。
export function isValidRollbackTargetForKind(attemptKind, rollbackTarget) {
  if (!ATTEMPT_KINDS.includes(attemptKind)) return false;
  if (attemptKind === AttemptKind.RECOVERY) return rollbackTarget === null;
  return rollbackTarget === null || (typeof rollbackTarget === "object" && rollbackTarget !== null);
}

// ── §8.2 ValidatedAssetToken ────────────────────────────────

const HEX64 = /^[0-9a-f]{64}$/;

function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

function isPositiveInteger(value) {
  return Number.isInteger(value) && value > 0;
}

// 结构校验：返回错误码列表，空数组表示通过。只做结构判定，不做签发。
export function validateAssetTokenShape(token) {
  const errors = [];
  if (token === null || typeof token !== "object") return ["token_not_object"];
  if (!isNonEmptyString(token.assetId)) errors.push("asset_id_invalid");
  // contentHash 必须为 64 位小写 hex（大写/短哈希直接拒绝）。
  if (!isNonEmptyString(token.contentHash) || !HEX64.test(token.contentHash)) {
    errors.push("content_hash_invalid");
  }
  if (!isPositiveInteger(token.byteLength)) errors.push("byte_length_invalid");
  if (!isNonEmptyString(token.validationReceiptId)) errors.push("validation_receipt_id_invalid");
  // registryEntryVersion 首次原子登记为 1，只按白名单递增（§8.2）。
  if (!isPositiveInteger(token.registryEntryVersion)) errors.push("registry_entry_version_invalid");
  if (!Number.isInteger(token.issuerEpoch) || token.issuerEpoch < 0) errors.push("issuer_epoch_invalid");
  if (!isNonEmptyString(token.nonce)) errors.push("nonce_invalid");
  if (typeof token.singleUse !== "boolean") errors.push("single_use_invalid");
  return errors;
}

export function isValidAssetTokenShape(token) {
  return validateAssetTokenShape(token).length === 0;
}

// 使用校验（§8.2）：hash、长度、receipt、registryEntryVersion、issuerEpoch 任一不一致即拒绝。
// registryRecord 为 AssetRegistry 中该资产当前记录。
export function validateAssetTokenForUse(token, registryRecord) {
  const shapeErrors = validateAssetTokenShape(token);
  if (shapeErrors.length > 0) return { ok: false, errors: shapeErrors };
  const errors = [];
  if (registryRecord === null || typeof registryRecord !== "object") {
    return { ok: false, errors: ["registry_record_missing"] };
  }
  if (registryRecord.assetId !== token.assetId) errors.push("asset_id_mismatch");
  if (registryRecord.contentHash !== token.contentHash) errors.push("content_hash_mismatch");
  if (registryRecord.byteLength !== token.byteLength) errors.push("byte_length_mismatch");
  if (registryRecord.validationReceiptId !== token.validationReceiptId) {
    errors.push("validation_receipt_id_mismatch");
  }
  if (registryRecord.registryEntryVersion !== token.registryEntryVersion) {
    errors.push("registry_entry_version_mismatch");
  }
  if (registryRecord.issuerEpoch !== token.issuerEpoch) errors.push("issuer_epoch_mismatch");
  return { ok: errors.length === 0, errors };
}

// ── §15.1 WireBodyAction → ScheduledBodyAction ─────────────

export const DEFAULT_MAX_ALLOWED_TTL_MS = 60_000;

export function clampTtlMs(ttlMs, maxAllowedTTL = DEFAULT_MAX_ALLOWED_TTL_MS) {
  const ttl = Number(ttlMs);
  const max = Number(maxAllowedTTL);
  if (!Number.isFinite(ttl) || ttl <= 0) return 0;
  if (!Number.isFinite(max) || max <= 0) return 0;
  return Math.min(ttl, max);
}

// TTL 必须由前端本地单调时钟计算（§15.1/§15.3）：
// receivedAtMonotonic/deadlineMonotonic 由调用方传入的本地单调时钟生成，
// 后端 sourceCreatedAt 仅用于诊断，禁止参与 deadline 计算，也不得跨进程持久化后继续比较。
export function scheduleBodyAction(wireAction, { nowMonotonic, maxAllowedTTL = DEFAULT_MAX_ALLOWED_TTL_MS } = {}) {
  if (!Number.isFinite(nowMonotonic)) {
    throw new Error("scheduleBodyAction 需要调用方提供本地单调时钟 nowMonotonic");
  }
  if (wireAction === null || typeof wireAction !== "object") {
    throw new Error("wireAction 必须是对象");
  }
  const receivedAtMonotonic = nowMonotonic;
  const deadlineMonotonic = receivedAtMonotonic + clampTtlMs(wireAction.ttlMs, maxAllowedTTL);
  return Object.freeze({
    ...wireAction,
    // sourceCreatedAt 原样保留仅供诊断，不参与过期判定。
    sourceCreatedAt: wireAction.sourceCreatedAt ?? null,
    receivedAtMonotonic,
    deadlineMonotonic,
  });
}

// 过期判定只比较前端单调时钟（§15.3：过期动作直接丢弃）。
export function isScheduledActionExpired(scheduledAction, nowMonotonic) {
  if (!Number.isFinite(nowMonotonic)) {
    throw new Error("isScheduledActionExpired 需要本地单调时钟 nowMonotonic");
  }
  return nowMonotonic >= scheduledAction.deadlineMonotonic;
}

// ── §15.4 幂等键 ────────────────────────────────────────────

// 正式幂等键：backendInstanceId + turnId + sequence。
// legacy 降级：后端无法提供 backendInstanceId 时使用 sessionEpoch + turnId + sequence；
// 重连必须更换 sessionEpoch 并清空未执行动作（§15.4）。
export function actionIdempotencyKey({ backendInstanceId, sessionEpoch, turnId, sequence } = {}) {
  if (!isNonEmptyString(turnId)) throw new Error("幂等键需要 turnId");
  if (!Number.isInteger(sequence) || sequence < 0) throw new Error("幂等键需要非负整数 sequence");
  if (isNonEmptyString(backendInstanceId)) {
    return `backend:${backendInstanceId}:${turnId}:${sequence}`;
  }
  if (isNonEmptyString(sessionEpoch)) {
    return `legacy:${sessionEpoch}:${turnId}:${sequence}`;
  }
  throw new Error("幂等键需要 backendInstanceId 或 sessionEpoch（legacy 降级）");
}

// ── §18.3.9/§19.1 挂起预算默认值（V2.1.2 初始值）────────────

export const DEFAULT_MAX_CONTINUOUS_SUSPENDED_MS = 5 * 60_000;
export const DEFAULT_MAX_CUMULATIVE_SUSPENDED_MS = 15 * 60_000;

// ── §15.5 固定时间步默认值 ──────────────────────────────────

export const FIXED_STEP_SECONDS = 1 / 60;
export const MAX_FRAME_DELTA_SECONDS = 0.1;
export const MAX_SUB_STEPS = 4;
export const PHYSICS_STEPS_DROPPED_METRIC = "PHYSICS_STEPS_DROPPED";
