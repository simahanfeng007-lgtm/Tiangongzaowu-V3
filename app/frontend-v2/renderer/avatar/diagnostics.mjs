// §23 诊断：事件目录（§23.1）、每事件字段（§23.2）、有界 ring buffer、
// 不记录模型二进制与绝对路径（§23.3）、重复错误采样合并。
// 纯内存实现：禁止逐帧写磁盘；时钟由调用方注入。

export const DIAGNOSTICS_SCHEMA_VERSION = 1;
export const DEFAULT_RING_CAPACITY = 256;

// §23.1 事件目录。
export const DiagnosticEvent = Object.freeze({
  ENGINE_INIT_START: "ENGINE_INIT_START",
  ENGINE_INIT_COMPLETE: "ENGINE_INIT_COMPLETE",
  ASSET_OPEN_START: "ASSET_OPEN_START",
  ASSET_FIRST_BYTE: "ASSET_FIRST_BYTE",
  ASSET_OPEN_COMPLETE: "ASSET_OPEN_COMPLETE",
  MODEL_VALIDATE_START: "MODEL_VALIDATE_START",
  MODEL_VALIDATE_COMPLETE: "MODEL_VALIDATE_COMPLETE",
  MODEL_ADMITTED: "MODEL_ADMITTED",
  VRM_PARSE_START: "VRM_PARSE_START",
  VRM_PARSE_COMPLETE: "VRM_PARSE_COMPLETE",
  GPU_UPLOAD_START: "GPU_UPLOAD_START",
  GPU_UPLOAD_COMPLETE: "GPU_UPLOAD_COMPLETE",
  FIRST_FRAME: "FIRST_FRAME",
  FIRST_RENDERABLE_FRAME: "FIRST_RENDERABLE_FRAME",
  FIRST_VISIBLE_FRAME: "FIRST_VISIBLE_FRAME",
  CONTEXT_LOST: "CONTEXT_LOST",
  RECOVERY_START: "RECOVERY_START",
  RECOVERY_COMPLETE: "RECOVERY_COMPLETE",
  MODEL_QUARANTINED: "MODEL_QUARANTINED",
  MODEL_DISPOSED: "MODEL_DISPOSED",
});
export const DIAGNOSTIC_EVENTS = Object.freeze(Object.values(DiagnosticEvent));

export class DiagnosticsError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "DiagnosticsError";
    this.code = code;
  }
}

// POSIX 绝对路径 / Windows 盘符路径 / UNC（与 candidate-read-grant 的口径一致）。
const ABSOLUTE_PATH_PATTERN = /^(\/|\\\\|[a-zA-Z]:[\\/])/;
// 值内嵌的绝对路径片段（如 "at C:\foo\bar"）。
const EMBEDDED_PATH_PATTERN = /([a-zA-Z]:[\\/][^\s]*|\\\\[^\s]+)/;

function assertDiagnosticValue(value, path) {
  if (value === null || value === undefined) return;
  const type = typeof value;
  if (type === "string") {
    if (ABSOLUTE_PATH_PATTERN.test(value) || EMBEDDED_PATH_PATTERN.test(value)) {
      throw new DiagnosticsError("diagnostic_path_forbidden", `诊断字段 ${path} 不得包含绝对路径（§23.3）`);
    }
    return;
  }
  if (type === "number" || type === "boolean") return;
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) {
    throw new DiagnosticsError("diagnostic_binary_forbidden", `诊断字段 ${path} 不得记录模型二进制（§23.3）`);
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertDiagnosticValue(item, `${path}[${index}]`));
    return;
  }
  if (type === "object") {
    for (const key of Object.keys(value)) assertDiagnosticValue(value[key], `${path}.${key}`);
  }
}

export function createDiagnostics({ nowMonotonic, ringCapacity = DEFAULT_RING_CAPACITY } = {}) {
  if (typeof nowMonotonic !== "function") {
    throw new DiagnosticsError("clock_required", "diagnostics 需要注入单调时钟 nowMonotonic");
  }
  if (!Number.isInteger(ringCapacity) || ringCapacity <= 0) {
    throw new DiagnosticsError("ring_capacity_invalid", "ringCapacity 必须为正整数");
  }

  const ring = [];
  let seq = 0;
  let droppedCount = 0;

  function emit(event, fields = {}) {
    if (!DIAGNOSTIC_EVENTS.includes(event)) {
      throw new DiagnosticsError("event_unknown", `未知诊断事件: ${event}（§23.1 目录外事件拒绝记录）`);
    }
    if (fields === null || typeof fields !== "object" || Array.isArray(fields)) {
      throw new DiagnosticsError("fields_invalid", "诊断字段必须是对象");
    }
    assertDiagnosticValue(fields, "fields");
    // §23.2 每事件字段（缺省 null，保持结构稳定可索引）。
    const entry = Object.freeze({
      seq: (seq += 1),
      event,
      atMonotonic: nowMonotonic(),
      correlationId: fields.correlationId ?? null,
      modelId: fields.modelId ?? null,
      phase: fields.phase ?? null,
      durationMs: fields.durationMs ?? null,
      result: fields.result ?? null,
      errorCode: fields.errorCode ?? null,
      retryable: fields.retryable ?? null,
      engineVersion: fields.engineVersion ?? null,
      resourceEstimate: fields.resourceEstimate ?? null,
      detail: fields.detail ?? null,
      repeatCount: 1,
    });
    // §23.3.6 重复错误采样合并：与尾事件同 event+modelId+phase+errorCode 时合并计数。
    const tail = ring[ring.length - 1];
    if (
      tail &&
      tail.event === entry.event &&
      tail.modelId === entry.modelId &&
      tail.phase === entry.phase &&
      tail.errorCode === entry.errorCode
    ) {
      ring[ring.length - 1] = Object.freeze({ ...tail, atMonotonic: entry.atMonotonic, repeatCount: tail.repeatCount + 1 });
      return ring[ring.length - 1];
    }
    ring.push(entry);
    if (ring.length > ringCapacity) {
      ring.shift();
      droppedCount += 1;
    }
    return entry;
  }

  function list({ event = null, limit = ringCapacity } = {}) {
    const filtered = event === null ? ring : ring.filter((entry) => entry.event === event);
    return Object.freeze(filtered.slice(-Math.max(0, limit)).map((entry) => Object.freeze({ ...entry })));
  }

  function latest(count = 1) {
    return Object.freeze(ring.slice(-Math.max(0, count)).map((entry) => Object.freeze({ ...entry })));
  }

  function countByEvent() {
    const counts = {};
    for (const entry of ring) counts[entry.event] = (counts[entry.event] ?? 0) + 1;
    return Object.freeze(counts);
  }

  return Object.freeze({
    emit,
    list,
    latest,
    countByEvent,
    get size() { return ring.length; },
    get capacity() { return ringCapacity; },
    get droppedCount() { return droppedCount; },
  });
}
