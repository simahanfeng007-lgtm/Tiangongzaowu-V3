const PROJECTION_SCHEMA = "tiangong.gateway.ui-projection.v1";

const LANE_STATES = Object.freeze({
  execution: new Set([
    "NOT_STARTED", "PLANNED", "TICKET_ISSUED", "CLAIMED", "RUNNING",
    "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS",
    "RECONCILE_REQUIRED", "CANCELLED", "FENCED"
  ]),
  artifact: new Set([
    "NOT_REQUIRED", "PENDING", "CREATED", "QC_PENDING", "QC_PASSED",
    "QC_FAILED", "REJECTED", "SUPERSEDED"
  ]),
  delivery: new Set([
    "NOT_PLANNED", "PLANNED", "TICKET_ISSUED", "FETCHING", "UPLOADING",
    "SENDING", "CHANNEL_ACCEPTED", "DELIVERED", "FAILED_RETRYABLE",
    "FAILED_FINAL", "AMBIGUOUS", "RECONCILE_REQUIRED", "CANCELLED", "FENCED"
  ])
});

const DONE_STATES = Object.freeze({
  execution: new Set(["SUCCEEDED"]),
  artifact: new Set(["QC_PASSED"]),
  delivery: new Set(["CHANNEL_ACCEPTED", "DELIVERED"])
});

const FAILED_STATES = new Set(["FAILED_RETRYABLE", "FAILED_FINAL", "QC_FAILED", "REJECTED"]);
const BLOCKED_STATES = new Set(["AMBIGUOUS", "RECONCILE_REQUIRED", "CANCELLED", "FENCED", "SUPERSEDED"]);
const PENDING_STATES = new Set(["NOT_STARTED", "PLANNED", "PENDING", "NOT_PLANNED"]);
const SOURCES = new Set(["VERIFIED_LEDGER", "LEGACY_OBSERVATION", "ABSENT"]);

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function laneStatus(machine, state) {
  // FE-04: an artifact lane that never required QC must not render as a green
  // "QC PASSED"; it is a neutral terminal state.
  if (machine === "artifact" && state === "NOT_REQUIRED") return "neutral";
  if (DONE_STATES[machine].has(state)) return "done";
  if (FAILED_STATES.has(state)) return "failed";
  if (BLOCKED_STATES.has(state)) return "blocked";
  if (PENDING_STATES.has(state)) return "pending";
  return "running";
}

function laneSummary(lane) {
  if (lane.machine === "artifact" && lane.state === "NOT_REQUIRED") {
    return `${lane.label}（未要求质检，不构成质量证明）`;
  }
  if (["AMBIGUOUS", "RECONCILE_REQUIRED"].includes(lane.state)) {
    return `${lane.label}；禁止重发，等待网关对账`;
  }
  if (lane.source === "LEGACY_OBSERVATION") {
    return `${lane.label}（兼容执行观测，尚非网关完成事实）`;
  }
  if (lane.source === "ABSENT") {
    return `${lane.label}（尚无权威事实）`;
  }
  return lane.label;
}

function validLane(lane, expectedMachine) {
  const label = typeof lane?.label === "string" ? lane.label.trim() : "";
  const entityCount = Number(lane?.entity_count ?? 0);
  return isObject(lane)
    && lane.machine === expectedMachine
    && LANE_STATES[expectedMachine].has(String(lane.state || ""))
    && label.length > 0
    && label.length <= 500
    && String(lane.reason_code || "").length <= 256
    && Number.isSafeInteger(entityCount)
    && entityCount >= 0
    && entityCount <= 100000
    && SOURCES.has(String(lane.source || ""))
    && typeof lane.evidence_verified === "boolean"
    && lane.evidence_verified === (lane.source === "VERIFIED_LEDGER");
}

function projectionLaneStep(projection, machine, prefix) {
  const lane = projection[machine];
  return {
    id: `gateway-lane-${machine}`,
    title: `${prefix} · ${lane.label}`,
    status: laneStatus(machine, lane.state),
    summary: laneSummary(lane),
    ts: Number(projection.observed_at_ms || 0) / 1000,
    meta: {
      type: "GATEWAY_STATE_PROJECTION",
      machine,
      state: lane.state,
      source: lane.source,
      evidenceVerified: lane.evidence_verified,
      entityCount: Number(lane.entity_count || 0),
      reasonCode: String(lane.reason_code || ""),
      overallPhase: String(projection.overall_phase || ""),
      needsReconciliation: Boolean(projection.needs_reconciliation),
      gatewayRequestId: projection.gateway_request_id,
      presentationRequestId: projection.presentation_request_id,
      projectionSha256: projection.projection_sha256
    }
  };
}

export function projectionToProgressSteps(projection, expectedPresentationRequestId = "") {
  if (!isObject(projection) || projection.projection_schema !== PROJECTION_SCHEMA) return [];
  const gatewayRequestId = String(projection.gateway_request_id || "");
  const presentationRequestId = String(projection.presentation_request_id || "");
  const expected = String(expectedPresentationRequestId || "");
  const observedAt = Number(projection.observed_at_ms || 0);
  if (!/^req_[0-9a-f]{64}$/.test(gatewayRequestId) || !presentationRequestId || presentationRequestId.length > 256) return [];
  if (!Number.isSafeInteger(observedAt) || observedAt < 0) return [];
  if (expected && presentationRequestId !== expected) return [];
  if (!/^[0-9a-f]{64}$/.test(String(projection.projection_sha256 || ""))) return [];
  if (
    !validLane(projection.execution, "execution")
    || !validLane(projection.artifact, "artifact")
    || !validLane(projection.delivery, "delivery")
  ) return [];
  return [
    projectionLaneStep(projection, "execution", "执行"),
    projectionLaneStep(projection, "artifact", "产物 QC"),
    projectionLaneStep(projection, "delivery", "投递")
  ];
}

export { PROJECTION_SCHEMA };
