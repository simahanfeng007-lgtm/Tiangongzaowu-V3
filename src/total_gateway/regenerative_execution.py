"""Durable regenerative-execution contracts for P18-M2.

This module is deliberately authority-free: it defines immutable identities,
frontier/checkpoint records, and hash-chain event construction.  Persistence
remains owned by the existing :class:`GatewayStateStore`; model/runtime code
must never treat these records as a second task or authority store.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import canonical_sha256


ZERO_HASH = "0" * 64
LEDGER_SCHEMA_VERSION = "tiangong.gateway.execution-ledger.v1"
FRONTIER_SCHEMA_VERSION = "tiangong.gateway.execution-frontier.v1"
CHECKPOINT_SCHEMA_VERSION = "tiangong.gateway.regenerative-checkpoint.v1"

EXECUTION_EVENT_TYPES = frozenset({
    "chain.started",
    "epoch.started",
    "step.planned",
    "step.prepared",
    "step.dispatched",
    "step.observed",
    "step.verified",
    "step.committed",
    "step.failed",
    "step.ambiguous",
    "step.reconciled",
    "step.compensated",
    "fact.observed",
    "fact.verified",
    "fact.revoked",
    "fact.stale",
    "frontier.updated",
    "checkpoint.prepared",
    "checkpoint.audited",
    "checkpoint.committed",
    "context.compacted",
    "run.resumed",
    "epoch.completed",
    "completion.proposed",
    "completion.verified",
    "completion.rejected",
    "chain.completed",
    "chain.failed",
})


def _hash64(value: str, *, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{label} must be a lowercase sha256")
    return text


def derive_logical_effect_id(
    *,
    request_id: str,
    run_id: str,
    generation: int,
    obligation_key: str,
    effect_namespace: str,
    normalized_target: str,
    desired_postcondition_sha256: str,
) -> str:
    """Derive one stable logical side-effect identity.

    The identity intentionally excludes Epoch, model/provider, context window,
    retry strategy, and attempt number.  The same logical action therefore
    survives regeneration and process restart without becoming executable a
    second time after commit.
    """

    if generation < 0:
        raise ValueError("generation is invalid")
    postcondition = _hash64(desired_postcondition_sha256, label="postcondition")
    return "lef_" + canonical_sha256({
        "domain": "tiangong.gateway.logical-effect-id.v1",
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "obligation_key": str(obligation_key or "").strip(),
        "effect_namespace": str(effect_namespace or "").strip(),
        "normalized_target": str(normalized_target or "").strip(),
        "desired_postcondition_sha256": postcondition,
    })


def derive_attempt_id(*, logical_effect_id: str, attempt: int) -> str:
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return "att_" + canonical_sha256({
        "domain": "tiangong.gateway.logical-effect-attempt.v1",
        "logical_effect_id": logical_effect_id,
        "attempt": attempt,
    })


def derive_step_id(
    *, request_id: str, run_id: str, generation: int, global_step: int, logical_effect_id: str | None = None
) -> str:
    if generation < 0 or global_step < 0:
        raise ValueError("step identity counters are invalid")
    return "stp_" + canonical_sha256({
        "domain": "tiangong.gateway.execution-step-id.v1",
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "global_step": global_step,
        "logical_effect_id": logical_effect_id,
    })


class ExecutionFrontier(BaseModel):
    """Bounded snapshot of where one authoritative Run currently is."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.execution-frontier.v1"] = FRONTIER_SCHEMA_VERSION
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    life_id: str = Field(min_length=1, max_length=160)
    root_goal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    global_step: int = Field(ge=0)
    epoch_index: int = Field(ge=0)
    epoch_step: int = Field(ge=0)
    completed_obligation_ids: tuple[str, ...] = Field(default=(), max_length=512)
    active_obligation_id: str | None = Field(default=None, max_length=200)
    pending_obligation_ids: tuple[str, ...] = Field(default=(), max_length=512)
    verified_fact_head: str | None = Field(default=None, max_length=200)
    artifact_revision_head: str | None = Field(default=None, max_length=200)
    pending_effect_ids: tuple[str, ...] = Field(default=(), max_length=512)
    ambiguous_effect_ids: tuple[str, ...] = Field(default=(), max_length=512)
    active_blockers: tuple[str, ...] = Field(default=(), max_length=128)
    failed_strategy_ids: tuple[str, ...] = Field(default=(), max_length=256)
    latest_safe_step: str = Field(min_length=1, max_length=1000)
    next_action_hint: str = Field(min_length=1, max_length=1000)
    provider_turn_state_ref: str | None = Field(default=None, max_length=500)
    frontier_version: int = Field(ge=1)
    frontier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_frontier(self) -> Self:
        for field_name in (
            "completed_obligation_ids",
            "pending_obligation_ids",
            "pending_effect_ids",
            "ambiguous_effect_ids",
            "active_blockers",
            "failed_strategy_ids",
        ):
            values = getattr(self, field_name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must be sorted and unique")
        completed = set(self.completed_obligation_ids)
        pending = set(self.pending_obligation_ids)
        if completed & pending:
            raise ValueError("completed and pending obligations overlap")
        if self.active_obligation_id is not None and self.active_obligation_id in completed:
            raise ValueError("active obligation is already completed")
        if set(self.pending_effect_ids) & set(self.ambiguous_effect_ids):
            raise ValueError("pending and ambiguous effects overlap")
        return self

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"frontier_hash"}))

    def has_valid_hash(self) -> bool:
        return self.frontier_hash == self.computed_hash()

    def with_computed_hash(self) -> Self:
        return self.model_copy(update={"frontier_hash": self.computed_hash()})


class ExecutionLedgerEvent(BaseModel):
    """One canonical, hash-chained, append-only execution event."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.execution-ledger.v1"] = LEDGER_SCHEMA_VERSION
    ledger_seq: int = Field(ge=1)
    event_id: str = Field(pattern=r"^lge_[0-9a-f]{64}$")
    event_key: str = Field(min_length=1, max_length=500)
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    epoch_index: int = Field(ge=0)
    event_type: str = Field(min_length=1, max_length=80)
    created_at_ms: int = Field(ge=0)
    payload: dict[str, Any]
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prev_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_effect_id: str | None = Field(default=None, pattern=r"^lef_[0-9a-f]{64}$")
    attempt_id: str | None = Field(default=None, pattern=r"^att_[0-9a-f]{64}$")
    step_id: str | None = Field(default=None, pattern=r"^stp_[0-9a-f]{64}$")
    effect_id: str | None = Field(default=None, pattern=r"^eff_[0-9a-f]{64}$")
    causal_parent_event_id: str | None = Field(default=None, pattern=r"^lge_[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.event_type not in EXECUTION_EVENT_TYPES:
            raise ValueError("unsupported execution ledger event type")
        if self.payload_hash != canonical_sha256(self.payload):
            raise ValueError("execution ledger payload digest is invalid")
        expected_event_id = "lge_" + canonical_sha256({
            "domain": "tiangong.gateway.execution-ledger-event-id.v1",
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "event_key": self.event_key,
        })
        if self.event_id != expected_event_id:
            raise ValueError("execution ledger event id is not bound to its idempotency key")
        return self

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"event_hash"}))

    def has_valid_hash(self) -> bool:
        return self.event_hash == self.computed_hash()

    def with_computed_hash(self) -> Self:
        return self.model_copy(update={"event_hash": self.computed_hash()})


class RegenerativeCheckpoint(BaseModel):
    """Durable execution-recovery anchor linked to canonical continuity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.regenerative-checkpoint.v1"] = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: str = Field(pattern=r"^rgc_[0-9a-f]{64}$")
    checkpoint_seq: int = Field(ge=1)
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    life_id: str = Field(min_length=1, max_length=160)
    root_goal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    epoch_index: int = Field(ge=0)
    global_step: int = Field(ge=0)
    frontier_version: int = Field(ge=1)
    frontier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frontier: ExecutionFrontier
    continuity_capsule_id: str = Field(min_length=1, max_length=160)
    ledger_head_seq: int = Field(ge=0)
    ledger_head_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pending_effect_ids: tuple[str, ...] = Field(default=(), max_length=512)
    ambiguous_effect_ids: tuple[str, ...] = Field(default=(), max_length=512)
    recovery_preconditions: tuple[str, ...] = Field(default=(), max_length=128)
    provider_continuation_ref: str | None = Field(default=None, max_length=500)
    runtime_version: str = Field(min_length=1, max_length=160)
    provider_version: str = Field(min_length=1, max_length=160)
    model_version: str = Field(min_length=1, max_length=160)
    tool_contract_version: str = Field(min_length=1, max_length=160)
    skill_contract_version: str = Field(min_length=1, max_length=160)
    task_contract_version: str = Field(min_length=1, max_length=160)
    semantic_handoff: str = Field(default="", max_length=12_000)
    previous_checkpoint_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_checkpoint(self) -> Self:
        if (
            self.frontier.request_id != self.request_id
            or self.frontier.run_id != self.run_id
            or self.frontier.generation != self.generation
            or self.frontier.life_id != self.life_id
            or self.frontier.root_goal_hash != self.root_goal_hash
            or self.frontier.task_contract_hash != self.task_contract_hash
            or self.frontier.authority_hash != self.authority_hash
            or self.frontier.frontier_version != self.frontier_version
            or self.frontier.frontier_hash != self.frontier_hash
            or not self.frontier.has_valid_hash()
        ):
            raise ValueError("checkpoint frontier binding is invalid")
        if self.pending_effect_ids != tuple(sorted(set(self.pending_effect_ids))):
            raise ValueError("checkpoint pending effect ids must be sorted and unique")
        if self.ambiguous_effect_ids != tuple(sorted(set(self.ambiguous_effect_ids))):
            raise ValueError("checkpoint ambiguous effect ids must be sorted and unique")
        if set(self.pending_effect_ids) & set(self.ambiguous_effect_ids):
            raise ValueError("checkpoint pending and ambiguous effects overlap")
        if self.recovery_preconditions != tuple(dict.fromkeys(self.recovery_preconditions)):
            raise ValueError("recovery preconditions must be stable and unique")
        return self

    def computed_checksum(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"checksum_sha256", "checkpoint_hash"}))

    def computed_hash(self) -> str:
        return canonical_sha256({
            "domain": "tiangong.gateway.regenerative-checkpoint-hash.v1",
            "checkpoint_id": self.checkpoint_id,
            "checksum_sha256": self.computed_checksum(),
            "previous_checkpoint_hash": self.previous_checkpoint_hash,
        })

    def has_valid_hashes(self) -> bool:
        return self.checksum_sha256 == self.computed_checksum() and self.checkpoint_hash == self.computed_hash()

    def with_computed_hashes(self) -> Self:
        checksum = self.computed_checksum()
        updated = self.model_copy(update={"checksum_sha256": checksum})
        return updated.model_copy(update={"checkpoint_hash": updated.computed_hash()})


def build_execution_ledger_event(
    *,
    ledger_seq: int,
    event_key: str,
    request_id: str,
    run_id: str,
    generation: int,
    epoch_index: int,
    event_type: str,
    created_at_ms: int,
    payload: dict[str, Any],
    prev_event_hash: str,
    logical_effect_id: str | None = None,
    attempt_id: str | None = None,
    step_id: str | None = None,
    effect_id: str | None = None,
    causal_parent_event_id: str | None = None,
) -> ExecutionLedgerEvent:
    event_id = "lge_" + canonical_sha256({
        "domain": "tiangong.gateway.execution-ledger-event-id.v1",
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "event_key": event_key,
    })
    event = ExecutionLedgerEvent(
        ledger_seq=ledger_seq,
        event_id=event_id,
        event_key=event_key,
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        epoch_index=epoch_index,
        event_type=event_type,
        created_at_ms=created_at_ms,
        payload=dict(payload),
        payload_hash=canonical_sha256(payload),
        prev_event_hash=prev_event_hash,
        event_hash=ZERO_HASH,
        logical_effect_id=logical_effect_id,
        attempt_id=attempt_id,
        step_id=step_id,
        effect_id=effect_id,
        causal_parent_event_id=causal_parent_event_id,
    )
    return event.with_computed_hash()


def build_regenerative_checkpoint(
    *,
    checkpoint_seq: int,
    frontier: ExecutionFrontier,
    continuity_capsule_id: str,
    ledger_head_seq: int,
    ledger_head_hash: str,
    recovery_preconditions: tuple[str, ...],
    runtime_version: str,
    provider_version: str,
    model_version: str,
    tool_contract_version: str,
    skill_contract_version: str,
    task_contract_version: str,
    previous_checkpoint_hash: str,
    created_at_ms: int,
    semantic_handoff: str = "",
) -> RegenerativeCheckpoint:
    checkpoint_id = "rgc_" + canonical_sha256({
        "domain": "tiangong.gateway.regenerative-checkpoint-id.v1",
        "request_id": frontier.request_id,
        "run_id": frontier.run_id,
        "generation": frontier.generation,
        "checkpoint_seq": checkpoint_seq,
        "frontier_hash": frontier.frontier_hash,
        "ledger_head_hash": ledger_head_hash,
    })
    checkpoint = RegenerativeCheckpoint(
        checkpoint_id=checkpoint_id,
        checkpoint_seq=checkpoint_seq,
        request_id=frontier.request_id,
        run_id=frontier.run_id,
        generation=frontier.generation,
        life_id=frontier.life_id,
        root_goal_hash=frontier.root_goal_hash,
        task_contract_hash=frontier.task_contract_hash,
        authority_hash=frontier.authority_hash,
        epoch_index=frontier.epoch_index,
        global_step=frontier.global_step,
        frontier_version=frontier.frontier_version,
        frontier_hash=frontier.frontier_hash,
        frontier=frontier,
        continuity_capsule_id=continuity_capsule_id,
        ledger_head_seq=ledger_head_seq,
        ledger_head_hash=ledger_head_hash,
        pending_effect_ids=frontier.pending_effect_ids,
        ambiguous_effect_ids=frontier.ambiguous_effect_ids,
        recovery_preconditions=tuple(recovery_preconditions),
        provider_continuation_ref=frontier.provider_turn_state_ref,
        runtime_version=runtime_version,
        provider_version=provider_version,
        model_version=model_version,
        tool_contract_version=tool_contract_version,
        skill_contract_version=skill_contract_version,
        task_contract_version=task_contract_version,
        semantic_handoff=str(semantic_handoff or "")[:12_000],
        previous_checkpoint_hash=previous_checkpoint_hash,
        created_at_ms=created_at_ms,
        checksum_sha256=ZERO_HASH,
        checkpoint_hash=ZERO_HASH,
    )
    return checkpoint.with_computed_hashes()


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "EXECUTION_EVENT_TYPES",
    "FRONTIER_SCHEMA_VERSION",
    "LEDGER_SCHEMA_VERSION",
    "ZERO_HASH",
    "ExecutionFrontier",
    "ExecutionLedgerEvent",
    "RegenerativeCheckpoint",
    "build_execution_ledger_event",
    "build_regenerative_checkpoint",
    "derive_attempt_id",
    "derive_logical_effect_id",
    "derive_step_id",
]
