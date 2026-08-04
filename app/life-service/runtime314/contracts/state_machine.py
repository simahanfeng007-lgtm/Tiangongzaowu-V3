"""Pure state machines and aggregate presentation rules for gateway runs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .models import (
    ContractModel,
    OpaqueId,
    ReasonCode,
    RequestId,
    RunId,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
)


MachineName = Literal["request", "execution", "artifact", "delivery"]
StateValue = Literal[
    "RECEIVED",
    "QUEUED",
    "PLANNING",
    "WAITING_CONFIRMATION",
    "EXECUTING",
    "VALIDATING_ARTIFACTS",
    "DELIVERING",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
    "SUPERSEDED",
    "NOT_STARTED",
    "PLANNED",
    "TICKET_ISSUED",
    "CLAIMED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
    "AMBIGUOUS",
    "RECONCILE_REQUIRED",
    "FENCED",
    "NOT_REQUIRED",
    "PENDING",
    "CREATED",
    "QC_PENDING",
    "QC_PASSED",
    "QC_FAILED",
    "REJECTED",
    "NOT_PLANNED",
    "FETCHING",
    "UPLOADING",
    "SENDING",
    "CHANNEL_ACCEPTED",
    "DELIVERED",
]
RequestStateValue = Literal[
    "RECEIVED",
    "QUEUED",
    "PLANNING",
    "WAITING_CONFIRMATION",
    "EXECUTING",
    "VALIDATING_ARTIFACTS",
    "DELIVERING",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
    "SUPERSEDED",
]


STATE_VALUES: dict[str, frozenset[str]] = {
    "request": frozenset(
        {
            "RECEIVED",
            "QUEUED",
            "PLANNING",
            "WAITING_CONFIRMATION",
            "EXECUTING",
            "VALIDATING_ARTIFACTS",
            "DELIVERING",
            "COMPLETED",
            "PARTIAL",
            "FAILED",
            "CANCELLED",
            "SUPERSEDED",
        }
    ),
    "execution": frozenset(
        {
            "NOT_STARTED",
            "PLANNED",
            "TICKET_ISSUED",
            "CLAIMED",
            "RUNNING",
            "SUCCEEDED",
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
            "AMBIGUOUS",
            "RECONCILE_REQUIRED",
            "CANCELLED",
            "FENCED",
        }
    ),
    "artifact": frozenset(
        {
            "NOT_REQUIRED",
            "PENDING",
            "CREATED",
            "QC_PENDING",
            "QC_PASSED",
            "QC_FAILED",
            "REJECTED",
            "SUPERSEDED",
        }
    ),
    "delivery": frozenset(
        {
            "NOT_PLANNED",
            "PLANNED",
            "TICKET_ISSUED",
            "FETCHING",
            "UPLOADING",
            "SENDING",
            "CHANNEL_ACCEPTED",
            "DELIVERED",
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
            "AMBIGUOUS",
            "RECONCILE_REQUIRED",
            "CANCELLED",
            "FENCED",
        }
    ),
}

INITIAL_STATES: dict[str, str] = {
    "request": "RECEIVED",
    "execution": "NOT_STARTED",
    "artifact": "PENDING",
    "delivery": "NOT_PLANNED",
}

# ---------------------------------------------------------------------------
# attempt 级 reconciliation fact 类型（effect 台账 append-only 事实链，草案 §3.2）
#
# RECONCILE_REQUIRED 是机器级状态；其 attempt 级事实结论由下列三值承载：
# APPLIED（已证实施加）与 PROVEN_NOT_APPLIED（已证实未施加）对同一 attempt
# first-CAS-wins；INCONCLUSIVE 可被后续证据收敛。PNA 之后又出现真实 APPLIED
# 证据时不得改判，只能追加 CONTRADICTION fact 并全局 fence。
# ---------------------------------------------------------------------------
AttemptReconciliationVerdict = Literal["APPLIED", "PROVEN_NOT_APPLIED", "INCONCLUSIVE"]
ATTEMPT_RECONCILIATION_VERDICTS: frozenset[str] = frozenset(
    {"APPLIED", "PROVEN_NOT_APPLIED", "INCONCLUSIVE"}
)
# effect 台账 attempt 级事实链允许的事实种类（与 gateway store 的 CHECK 一致）。
EffectAttemptFactKind = Literal[
    "CLAIM",
    "STARTED",
    "DISPATCH_PERMIT",
    "RECEIPT",
    "RECONCILIATION",
    "CONTRADICTION",
    "FENCE",
    "AUTHORIZATION_FAILED",
]
EFFECT_ATTEMPT_FACT_KINDS: frozenset[str] = frozenset(
    {
        "CLAIM",
        "STARTED",
        "DISPATCH_PERMIT",
        "RECEIPT",
        "RECONCILIATION",
        "CONTRADICTION",
        "FENCE",
        "AUTHORIZATION_FAILED",
    }
)

TERMINAL_STATES: dict[str, frozenset[str]] = {
    "request": frozenset({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "SUPERSEDED"}),
    "execution": frozenset({"SUCCEEDED", "FAILED_FINAL", "CANCELLED", "FENCED"}),
    "artifact": frozenset({"NOT_REQUIRED", "QC_PASSED", "QC_FAILED", "REJECTED", "SUPERSEDED"}),
    "delivery": frozenset({"DELIVERED", "FAILED_FINAL", "CANCELLED", "FENCED"}),
}

TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    "request": {
        "RECEIVED": frozenset({"QUEUED", "PLANNING", "FAILED", "CANCELLED", "SUPERSEDED"}),
        "QUEUED": frozenset({"PLANNING", "FAILED", "CANCELLED", "SUPERSEDED"}),
        "PLANNING": frozenset(
            {"WAITING_CONFIRMATION", "EXECUTING", "DELIVERING", "FAILED", "CANCELLED", "SUPERSEDED"}
        ),
        "WAITING_CONFIRMATION": frozenset(
            {"PLANNING", "EXECUTING", "FAILED", "CANCELLED", "SUPERSEDED"}
        ),
        "EXECUTING": frozenset(
            {"VALIDATING_ARTIFACTS", "DELIVERING", "FAILED", "CANCELLED", "SUPERSEDED"}
        ),
        "VALIDATING_ARTIFACTS": frozenset(
            {"DELIVERING", "FAILED", "CANCELLED", "SUPERSEDED"}
        ),
        "DELIVERING": frozenset({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "SUPERSEDED"}),
    },
    "execution": {
        "NOT_STARTED": frozenset({"PLANNED", "CANCELLED", "FENCED"}),
        "PLANNED": frozenset({"TICKET_ISSUED", "FAILED_FINAL", "CANCELLED", "FENCED"}),
        "TICKET_ISSUED": frozenset(
            {"CLAIMED", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED", "FENCED"}
        ),
        "CLAIMED": frozenset(
            {
                "RUNNING",
                "SUCCEEDED",
                "FAILED_RETRYABLE",
                "FAILED_FINAL",
                "AMBIGUOUS",
                "CANCELLED",
                "FENCED",
            }
        ),
        "RUNNING": frozenset(
            {"SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED", "FENCED"}
        ),
        "FAILED_RETRYABLE": frozenset({"TICKET_ISSUED", "FAILED_FINAL", "CANCELLED", "FENCED"}),
        "AMBIGUOUS": frozenset({"RECONCILE_REQUIRED"}),
        "RECONCILE_REQUIRED": frozenset({"SUCCEEDED", "FAILED_FINAL"}),
    },
    "artifact": {
        "PENDING": frozenset({"CREATED", "REJECTED", "SUPERSEDED"}),
        "CREATED": frozenset({"QC_PENDING", "REJECTED", "SUPERSEDED"}),
        "QC_PENDING": frozenset({"QC_PASSED", "QC_FAILED", "REJECTED", "SUPERSEDED"}),
    },
    "delivery": {
        "NOT_PLANNED": frozenset({"PLANNED", "CANCELLED", "FENCED"}),
        "PLANNED": frozenset({"TICKET_ISSUED", "FAILED_FINAL", "CANCELLED", "FENCED"}),
        "TICKET_ISSUED": frozenset(
            {"FETCHING", "SENDING", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED", "FENCED"}
        ),
        "FETCHING": frozenset(
            {"UPLOADING", "SENDING", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED", "FENCED"}
        ),
        "UPLOADING": frozenset(
            {"SENDING", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED", "FENCED"}
        ),
        "SENDING": frozenset(
            {
                "CHANNEL_ACCEPTED",
                "DELIVERED",
                "FAILED_RETRYABLE",
                "FAILED_FINAL",
                "AMBIGUOUS",
                "CANCELLED",
                "FENCED",
            }
        ),
        "CHANNEL_ACCEPTED": frozenset({"DELIVERED", "AMBIGUOUS", "RECONCILE_REQUIRED"}),
        "FAILED_RETRYABLE": frozenset({"TICKET_ISSUED", "FAILED_FINAL", "CANCELLED", "FENCED"}),
        "AMBIGUOUS": frozenset({"RECONCILE_REQUIRED"}),
        "RECONCILE_REQUIRED": frozenset({"CHANNEL_ACCEPTED", "DELIVERED", "FAILED_FINAL"}),
    },
}

ALLOWED_OWNERS: dict[str, frozenset[str]] = {
    "request": frozenset({"tiangong-total-gateway"}),
    "artifact": frozenset({"tiangong-total-gateway"}),
    "execution": frozenset({"tiangong-backend", "tiangong-total-gateway"}),
    "delivery": frozenset({"tiangong-communication-service", "tiangong-total-gateway"}),
}

EVIDENCE_REQUIRED: dict[str, frozenset[str]] = {
    "request": frozenset({"COMPLETED", "PARTIAL", "FAILED"}),
    "execution": frozenset(
        {"SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "RECONCILE_REQUIRED"}
    ),
    "artifact": frozenset({"QC_PASSED", "QC_FAILED", "REJECTED"}),
    "delivery": frozenset(
        {
            "CHANNEL_ACCEPTED",
            "DELIVERED",
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
            "AMBIGUOUS",
            "RECONCILE_REQUIRED",
        }
    ),
}


class StateSnapshot(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:StateSnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    machine: MachineName
    entity_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    revision: int = Field(ge=0)
    state: StateValue
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    last_event_id: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_machine_state(self) -> Self:
        if self.state not in STATE_VALUES[self.machine]:
            raise ValueError("state does not belong to selected machine")
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("state snapshot update predates creation")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES[self.machine]


class TransitionEvent(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:TransitionEvent",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    event_id: OpaqueId
    event_type: ReasonCode
    source_component_id: OpaqueId
    machine: MachineName
    entity_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    to_state: StateValue
    occurred_at_ms: int = Field(ge=0)
    fact_id: OpaqueId | None = None
    evidence_sha256: Sha256 | None = None
    side_effect_started: bool = False
    event_sha256: Sha256

    @model_validator(mode="after")
    def validate_machine_state(self) -> Self:
        if self.to_state not in STATE_VALUES[self.machine]:
            raise ValueError("target state does not belong to selected machine")
        return self

    def computed_event_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"event_sha256"}))

    def has_valid_event_sha256(self) -> bool:
        return self.event_sha256 == self.computed_event_sha256()

    def with_computed_event_sha256(self) -> Self:
        return self.model_copy(update={"event_sha256": self.computed_event_sha256()})


class TransitionDecision(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:TransitionDecision",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    accepted: bool
    disposition: Literal[
        "APPLIED",
        "DUPLICATE",
        "LATE_IGNORED",
        "FUTURE_REJECTED",
        "REVISION_CONFLICT",
        "OWNER_REJECTED",
        "CONTEXT_REJECTED",
        "TERMINAL_REJECTED",
        "ILLEGAL_TRANSITION",
        "EVIDENCE_REJECTED",
        "EVENT_DIGEST_REJECTED",
        "AMBIGUOUS_REQUIRED",
    ]
    reason_code: ReasonCode
    previous: StateSnapshot
    current: StateSnapshot

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.accepted != (self.disposition == "APPLIED"):
            raise ValueError("only APPLIED transition is accepted")
        if not self.accepted and self.previous != self.current:
            raise ValueError("rejected transition may not alter state")
        return self


def new_state_snapshot(
    machine: MachineName,
    *,
    entity_id: str,
    request_id: RequestId,
    run_id: RunId,
    generation: int,
    created_at_ms: int,
    artifact_required: bool = True,
) -> StateSnapshot:
    state = INITIAL_STATES[machine]
    if machine == "artifact" and not artifact_required:
        state = "NOT_REQUIRED"
    return StateSnapshot(
        machine=machine,
        entity_id=entity_id,
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        revision=0,
        state=state,
        created_at_ms=created_at_ms,
        updated_at_ms=created_at_ms,
    )


def _rejected(
    snapshot: StateSnapshot,
    disposition: str,
    reason_code: str,
) -> TransitionDecision:
    return TransitionDecision(
        accepted=False,
        disposition=disposition,
        reason_code=reason_code,
        previous=snapshot,
        current=snapshot,
    )


def apply_transition(
    snapshot: StateSnapshot,
    event: TransitionEvent,
    *,
    event_already_applied: bool = False,
) -> TransitionDecision:
    if not event.has_valid_event_sha256():
        return _rejected(snapshot, "EVENT_DIGEST_REJECTED", "state.event.digest_invalid")
    if event_already_applied:
        return _rejected(snapshot, "DUPLICATE", "state.event.duplicate")
    if (
        event.machine != snapshot.machine
        or event.entity_id != snapshot.entity_id
        or event.request_id != snapshot.request_id
        or event.run_id != snapshot.run_id
    ):
        return _rejected(snapshot, "CONTEXT_REJECTED", "state.event.context_mismatch")
    if event.generation < snapshot.generation:
        return _rejected(snapshot, "LATE_IGNORED", "state.event.late_generation")
    if event.generation > snapshot.generation:
        return _rejected(snapshot, "FUTURE_REJECTED", "state.event.future_generation")
    if event.expected_revision != snapshot.revision:
        return _rejected(snapshot, "REVISION_CONFLICT", "state.event.revision_conflict")
    if event.occurred_at_ms < snapshot.updated_at_ms:
        return _rejected(snapshot, "LATE_IGNORED", "state.event.backdated")
    if event.source_component_id not in ALLOWED_OWNERS[snapshot.machine]:
        return _rejected(snapshot, "OWNER_REJECTED", "state.event.owner_rejected")
    if snapshot.is_terminal:
        return _rejected(snapshot, "TERMINAL_REJECTED", "state.transition.terminal")
    if event.to_state not in TRANSITIONS[snapshot.machine].get(snapshot.state, frozenset()):
        return _rejected(snapshot, "ILLEGAL_TRANSITION", "state.transition.illegal")

    if snapshot.machine in {"execution", "delivery"}:
        if event.side_effect_started and event.to_state in {"CANCELLED", "FENCED", "FAILED_RETRYABLE"}:
            return _rejected(snapshot, "AMBIGUOUS_REQUIRED", "state.side_effect.ambiguous_required")
    if event.to_state in EVIDENCE_REQUIRED[snapshot.machine] and not (
        event.fact_id or event.evidence_sha256
    ):
        return _rejected(snapshot, "EVIDENCE_REJECTED", "state.transition.evidence_required")

    current = snapshot.model_copy(
        update={
            "revision": snapshot.revision + 1,
            "state": event.to_state,
            "updated_at_ms": event.occurred_at_ms,
            "last_event_id": event.event_id,
        }
    )
    return TransitionDecision(
        accepted=True,
        disposition="APPLIED",
        reason_code="state.transition.applied",
        previous=snapshot,
        current=current,
    )


class AggregateStatus(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AggregateStatus",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    request_state: RequestStateValue
    recommended_request_state: RequestStateValue
    display_phase: Literal[
        "received",
        "planning",
        "waiting_confirmation",
        "executing",
        "validating",
        "delivering",
        "channel_accepted",
        "delivered",
        "partial",
        "failed",
        "cancelled",
        "superseded",
        "reconcile_required",
    ]
    summary_code: ReasonCode
    can_claim_complete: bool
    can_claim_delivered: bool
    needs_reconciliation: bool
    execution_count: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    delivery_count: int = Field(ge=0)


def _ensure_same_scope(request: StateSnapshot, snapshots: Iterable[StateSnapshot]) -> tuple[StateSnapshot, ...]:
    items = tuple(snapshots)
    for item in items:
        if (
            item.request_id != request.request_id
            or item.run_id != request.run_id
            or item.generation != request.generation
        ):
            raise ValueError("aggregate snapshot scope mismatch")
    return items


def aggregate_request_status(
    request: StateSnapshot,
    *,
    executions: Iterable[StateSnapshot] = (),
    artifacts: Iterable[StateSnapshot] = (),
    deliveries: Iterable[StateSnapshot] = (),
) -> AggregateStatus:
    if request.machine != "request":
        raise ValueError("aggregate root must be a request snapshot")
    execution_items = _ensure_same_scope(request, executions)
    artifact_items = _ensure_same_scope(request, artifacts)
    delivery_items = _ensure_same_scope(request, deliveries)
    if any(item.machine != "execution" for item in execution_items):
        raise ValueError("execution aggregate contains wrong machine")
    if any(item.machine != "artifact" for item in artifact_items):
        raise ValueError("artifact aggregate contains wrong machine")
    if any(item.machine != "delivery" for item in delivery_items):
        raise ValueError("delivery aggregate contains wrong machine")

    def status(
        recommended: str,
        phase: str,
        code: str,
        *,
        complete: bool = False,
        delivered: bool = False,
        reconcile: bool = False,
    ) -> AggregateStatus:
        return AggregateStatus(
            request_state=request.state,
            recommended_request_state=recommended,
            display_phase=phase,
            summary_code=code,
            can_claim_complete=complete,
            can_claim_delivered=delivered,
            needs_reconciliation=reconcile,
            execution_count=len(execution_items),
            artifact_count=len(artifact_items),
            delivery_count=len(delivery_items),
        )

    if request.state == "CANCELLED":
        return status("CANCELLED", "cancelled", "request.cancelled")
    if request.state == "SUPERSEDED":
        return status("SUPERSEDED", "superseded", "request.superseded")

    all_children = (*execution_items, *artifact_items, *delivery_items)
    if any(item.state in {"AMBIGUOUS", "RECONCILE_REQUIRED"} for item in all_children):
        return status(
            "DELIVERING" if delivery_items else "EXECUTING",
            "reconcile_required",
            "request.reconcile_required",
            reconcile=True,
        )
    if request.state == "FAILED":
        return status("FAILED", "failed", "request.failed")
    if request.state == "PARTIAL":
        return status("PARTIAL", "partial", "request.partial")

    if not all_children:
        if request.state in {"RECEIVED", "QUEUED"}:
            return status(request.state, "received", "request.received")
        if request.state == "PLANNING":
            return status("PLANNING", "planning", "request.planning")
        if request.state == "WAITING_CONFIRMATION":
            return status(
                "WAITING_CONFIRMATION",
                "waiting_confirmation",
                "request.waiting_confirmation",
            )
        if request.state == "EXECUTING":
            return status("EXECUTING", "executing", "request.execution_not_started")
        if request.state == "VALIDATING_ARTIFACTS":
            return status(
                "VALIDATING_ARTIFACTS",
                "validating",
                "request.artifact_validation_not_started",
            )

    successful_delivery_states = {"CHANNEL_ACCEPTED", "DELIVERED"}
    some_delivery_success = any(item.state in successful_delivery_states for item in delivery_items)
    if any(
        item.state in {"FAILED_FINAL", "FENCED", "CANCELLED"}
        for item in (*execution_items, *delivery_items)
    ) or any(
        item.state in {"QC_FAILED", "REJECTED"} for item in artifact_items
    ):
        if some_delivery_success:
            return status("PARTIAL", "partial", "request.partial_delivery")
        return status("FAILED", "failed", "request.child_failed")

    if any(item.state == "FAILED_RETRYABLE" for item in (*execution_items, *delivery_items)):
        return status(
            "DELIVERING" if any(item.state == "FAILED_RETRYABLE" for item in delivery_items) else "EXECUTING",
            "delivering" if delivery_items else "executing",
            "request.retry_pending",
        )

    execution_ready = all(item.state == "SUCCEEDED" for item in execution_items)
    if execution_items and not execution_ready:
        return status("EXECUTING", "executing", "request.execution_in_progress")

    artifact_ready = all(
        item.state in {"NOT_REQUIRED", "QC_PASSED", "SUPERSEDED"} for item in artifact_items
    )
    if artifact_items and not artifact_ready:
        return status("VALIDATING_ARTIFACTS", "validating", "request.artifact_validation_in_progress")

    if not delivery_items:
        if request.state == "COMPLETED":
            # 纯聊天等无 delivery 机的请求：request 机 COMPLETED 即交付（回复已在
            # presentation 层），聚合相位必须落在 delivered，不得卡在 delivering
            # 让前端无法核验完成态。
            return status(
                "COMPLETED",
                "delivered",
                "request.delivered",
                complete=True,
                delivered=True,
            )
        phase = "waiting_confirmation" if request.state == "WAITING_CONFIRMATION" else "delivering"
        recommended = "WAITING_CONFIRMATION" if request.state == "WAITING_CONFIRMATION" else "DELIVERING"
        return status(recommended, phase, "request.delivery_not_planned")

    if all(item.state == "DELIVERED" for item in delivery_items):
        return status(
            "COMPLETED",
            "delivered",
            "request.delivered",
            complete=True,
            delivered=True,
        )
    if all(item.state in successful_delivery_states for item in delivery_items):
        return status(
            "COMPLETED",
            "channel_accepted",
            "request.channel_accepted",
            complete=True,
            delivered=False,
        )
    return status("DELIVERING", "delivering", "request.delivery_in_progress")


__all__ = [
    "ATTEMPT_RECONCILIATION_VERDICTS",
    "AggregateStatus",
    "AttemptReconciliationVerdict",
    "EFFECT_ATTEMPT_FACT_KINDS",
    "EffectAttemptFactKind",
    "StateSnapshot",
    "TransitionDecision",
    "TransitionEvent",
    "aggregate_request_status",
    "apply_transition",
    "new_state_snapshot",
]
