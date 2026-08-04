// G3 typed frontend truth projection (T20).
// Unknown state never defaults to ready|completed|assistant; historical
// values remain historical.

const KNOWN_RUN_STATUSES = new Set(["ready", "completed", "running", "pending", "failed", "unknown"]);
const KNOWN_MESSAGE_KINDS = new Set(["user", "assistant", "system"]);

export function projectRunStatus(raw) {
  const value = String(raw ?? "");
  if (value === "") return "unknown";
  if (!KNOWN_RUN_STATUSES.has(value)) return "unknown";
  return value;
}

export function projectMessageKind(item) {
  const kind = String(item?.kind ?? item?.role ?? "");
  if (!KNOWN_MESSAGE_KINDS.has(kind)) return "unknown";
  return kind;
}

export function historicalKindRemainsHistorical(kind) {
  const projected = projectMessageKind({ kind });
  return projected === "unknown" ? "unknown" : projected;
}
