"""Deterministic P5 attribution integrity over verified composition outcomes.

This module is pure and non-authorizing.  It checks continuity across the
system-compiled plan, machine Completion decision, active Verification evidence,
Effect/Fact lineage, source revisions, principal scope, and privacy scope.  It
never executes an Action or writes Memory.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from contracts import canonical_sha256
from contracts.capability_composition import (
    AttributionIntegrityV1,
    CapabilityCompositionPlanV1,
    SourceRevisionRefV1,
)
from contracts.models import ContractModel, OpaqueId, RequestId, RunId, Sha256

from .compiler import plan_has_valid_sha256


ATTRIBUTION_TRACE_SCHEMA = "tiangong.capability-attribution-trace.v1"


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError("values must be sorted and unique")
    return values


def _source_sort_key(
    source: SourceRevisionRefV1,
) -> tuple[str, str, str, str, str]:
    return (
        source.source_kind,
        source.semantic_id,
        source.version,
        source.source_sha256,
        source.descriptor_sha256,
    )


class CompletionEvidenceV1(ContractModel):
    """Bounded projection of the authoritative machine CompletionDecision."""

    schema_version: Literal["tiangong.completion-evidence.v1"] = (
        "tiangong.completion-evidence.v1"
    )
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    outcome: Literal[
        "IN_PROGRESS",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "RECONCILE_REQUIRED",
    ]
    can_transition_request_completed: bool
    needs_reconciliation: bool
    verification_mode: Literal["NONE", "PLAN_BOUND"]
    verification_ready: bool
    verification_plan_sha256: Sha256 | None = None
    verification_readiness_sha256: Sha256 | None = None
    supporting_fact_ids: tuple[OpaqueId, ...] = ()
    decision_sha256: Sha256
    model_generated: Literal[False] = False
    decision_hash_verified: Literal[True] = True

    _facts = field_validator("supporting_fact_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_completion_flags(self) -> Self:
        if self.can_transition_request_completed != (self.outcome == "COMPLETED"):
            raise ValueError("completion transition flag disagrees with outcome")
        if self.needs_reconciliation != (
            self.outcome == "RECONCILE_REQUIRED"
        ):
            raise ValueError("completion reconciliation flag disagrees with outcome")
        if self.verification_mode == "PLAN_BOUND":
            if (
                self.verification_plan_sha256 is None
                or self.verification_readiness_sha256 is None
            ):
                raise ValueError("PLAN_BOUND completion evidence is incomplete")
        return self


class AttributionTraceV1(ContractModel):
    """Exact, machine-collected lineage facts evaluated by P5 attribution."""

    schema_version: Literal[ATTRIBUTION_TRACE_SCHEMA] = ATTRIBUTION_TRACE_SCHEMA
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    principal_scope_hash: Sha256
    privacy_scope_hash: Sha256
    composition_plan_sha256: Sha256
    completion: CompletionEvidenceV1
    active_verification_plan_sha256: Sha256 | None = None
    verification_record_refs: tuple[OpaqueId, ...] = ()
    terminal_effect_ids: tuple[OpaqueId, ...] = ()
    terminal_fact_ids: tuple[OpaqueId, ...] = ()
    terminal_fact_hashes: tuple[Sha256, ...] = ()
    observed_method_source_refs: tuple[SourceRevisionRefV1, ...] = ()
    observed_action_source_refs: tuple[SourceRevisionRefV1, ...] = Field(
        min_length=1
    )
    has_acceptance_obligations: bool
    active_verification_plan_complete: bool
    effect_fact_lineage_complete: bool
    source_refs_complete: bool
    source_revisions_continuous: bool
    request_scope_continuous: bool
    human_takeover: bool = False
    alternate_execution_chain: bool = False
    unknown_external_overwrite: bool = False
    unknown_side_effects: bool = False
    unresolved_reconciliation: bool = False
    secret_or_credential_present: bool = False
    prompt_injection_present: bool = False
    context_identity_truncated: bool = False
    collected_at_ms: int = Field(ge=0)
    trace_sha256: Sha256
    model_generated: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False

    _verification_refs = field_validator("verification_record_refs")(
        _sorted_unique
    )
    _effect_ids = field_validator("terminal_effect_ids")(_sorted_unique)
    _fact_ids = field_validator("terminal_fact_ids")(_sorted_unique)
    _fact_hashes = field_validator("terminal_fact_hashes")(_sorted_unique)

    @field_validator(
        "observed_method_source_refs", "observed_action_source_refs"
    )
    @classmethod
    def validate_source_order(
        cls, values: tuple[SourceRevisionRefV1, ...]
    ) -> tuple[SourceRevisionRefV1, ...]:
        keys = tuple(_source_sort_key(value) for value in values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("source revisions must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_scope_projection(self) -> Self:
        if (
            self.completion.request_id != self.request_id
            or self.completion.run_id != self.run_id
            or self.completion.generation != self.generation
        ):
            raise ValueError("completion evidence crosses trace scope")
        if len(self.terminal_fact_ids) != len(self.terminal_fact_hashes):
            raise ValueError("terminal fact identities and hashes must align")
        if (
            self.completion.verification_mode == "PLAN_BOUND"
            and self.active_verification_plan_sha256
            != self.completion.verification_plan_sha256
        ):
            raise ValueError("active VerificationPlan disagrees with completion")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"trace_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.trace_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "AttributionTraceV1":
        return self.model_copy(update={"trace_sha256": self.computed_sha256()})


def completion_evidence_from_decision(decision: object) -> CompletionEvidenceV1:
    """Project a real machine CompletionDecision after verifying its digest.

    P5 deliberately uses structural access instead of importing Total Gateway,
    avoiding a reverse dependency from World Understanding into the Gateway.
    The object must expose the frozen CompletionDecision fields and a working
    ``has_valid_sha256`` method.
    """

    validator = getattr(decision, "has_valid_sha256", None)
    if not callable(validator) or validator() is not True:
        raise ValueError("completion decision digest is invalid")
    if getattr(decision, "model_generated", None) is not False:
        raise ValueError("model-generated completion cannot enter attribution")
    return CompletionEvidenceV1(
        request_id=getattr(decision, "request_id"),
        run_id=getattr(decision, "run_id"),
        generation=getattr(decision, "generation"),
        outcome=getattr(decision, "outcome"),
        can_transition_request_completed=getattr(
            decision, "can_transition_request_completed"
        ),
        needs_reconciliation=getattr(decision, "needs_reconciliation"),
        verification_mode=getattr(decision, "verification_mode"),
        verification_ready=getattr(decision, "verification_ready"),
        verification_plan_sha256=getattr(
            decision, "verification_plan_sha256"
        ),
        verification_readiness_sha256=getattr(
            decision, "verification_readiness_sha256"
        ),
        supporting_fact_ids=tuple(
            getattr(decision, "supporting_fact_ids")
        ),
        decision_sha256=getattr(decision, "decision_sha256"),
    )


def computed_attribution_sha256(
    attribution: AttributionIntegrityV1,
) -> str:
    return canonical_sha256(
        attribution.model_dump(mode="json", exclude={"attribution_sha256"})
    )


def attribution_has_valid_sha256(
    attribution: AttributionIntegrityV1,
) -> bool:
    return attribution.attribution_sha256 == computed_attribution_sha256(
        attribution
    )


def _source_refs_match(
    expected: tuple[SourceRevisionRefV1, ...],
    observed: tuple[SourceRevisionRefV1, ...],
) -> bool:
    return tuple(sorted(expected, key=_source_sort_key)) == tuple(
        sorted(observed, key=_source_sort_key)
    )


def evaluate_attribution_integrity(
    plan: CapabilityCompositionPlanV1,
    trace: AttributionTraceV1,
    *,
    expected_principal_scope_hash: str,
    expected_privacy_scope_hash: str,
    checked_at_ms: int,
) -> AttributionIntegrityV1:
    """Return PASS only for an uninterrupted, source-continuous verified chain."""

    if checked_at_ms < 0:
        raise ValueError("attribution check time must be non-negative")
    reasons: set[str] = set()
    if not plan_has_valid_sha256(plan):
        reasons.add("attribution.plan_hash_invalid")
    if not trace.has_valid_sha256():
        reasons.add("attribution.trace_hash_invalid")
    if trace.composition_plan_sha256 != plan.plan_sha256:
        reasons.add("attribution.plan_mismatch")
    if (
        trace.request_id != plan.request_id
        or trace.run_id != plan.run_id
        or trace.generation != plan.generation
        or not trace.request_scope_continuous
    ):
        reasons.add("attribution.request_scope_discontinuous")
    if (
        trace.principal_scope_hash != expected_principal_scope_hash
        or trace.principal_scope_hash != plan.principal_scope_hash
    ):
        reasons.add("attribution.principal_scope_mismatch")
    if trace.privacy_scope_hash != expected_privacy_scope_hash:
        reasons.add("attribution.privacy_scope_mismatch")
    if checked_at_ms < max(plan.created_at_ms, trace.collected_at_ms):
        reasons.add("attribution.time_inverted")

    completion = trace.completion
    if not completion.decision_hash_verified:
        reasons.add("attribution.completion_hash_unverified")
    if completion.outcome != "COMPLETED" or not (
        completion.can_transition_request_completed
    ):
        reasons.add("attribution.completion_not_completed")
    if completion.needs_reconciliation or trace.unresolved_reconciliation:
        reasons.add("attribution.reconciliation_unresolved")
    if trace.has_acceptance_obligations:
        if completion.verification_mode != "PLAN_BOUND":
            reasons.add("attribution.verification_not_plan_bound")
        if not completion.verification_ready:
            reasons.add("attribution.verification_not_ready")
        if not trace.active_verification_plan_complete:
            reasons.add("attribution.verification_plan_incomplete")
        if not trace.verification_record_refs:
            reasons.add("attribution.verification_records_missing")
        if (
            trace.active_verification_plan_sha256
            != completion.verification_plan_sha256
        ):
            reasons.add("attribution.verification_plan_mismatch")
    if not trace.effect_fact_lineage_complete:
        reasons.add("attribution.effect_fact_lineage_incomplete")
    if not trace.terminal_fact_ids or not trace.terminal_fact_hashes:
        reasons.add("attribution.terminal_facts_missing")
    if not set(completion.supporting_fact_ids).issubset(
        set(trace.terminal_fact_ids)
    ):
        reasons.add("attribution.completion_fact_lineage_missing")
    if not trace.source_refs_complete:
        reasons.add("attribution.source_refs_incomplete")
    if not trace.source_revisions_continuous:
        reasons.add("attribution.source_revision_discontinuous")
    if not _source_refs_match(
        plan.method_source_refs, trace.observed_method_source_refs
    ):
        reasons.add("attribution.method_source_mismatch")
    if not _source_refs_match(
        plan.action_source_refs, trace.observed_action_source_refs
    ):
        reasons.add("attribution.action_source_mismatch")

    for active, reason in (
        (trace.human_takeover, "attribution.human_takeover"),
        (
            trace.alternate_execution_chain,
            "attribution.alternate_execution_chain",
        ),
        (
            trace.unknown_external_overwrite,
            "attribution.unknown_external_overwrite",
        ),
        (trace.unknown_side_effects, "attribution.unknown_side_effects"),
        (
            trace.secret_or_credential_present,
            "attribution.secret_or_credential_present",
        ),
        (
            trace.prompt_injection_present,
            "attribution.prompt_injection_present",
        ),
        (
            trace.context_identity_truncated,
            "attribution.context_identity_truncated",
        ),
    ):
        if active:
            reasons.add(reason)

    checked_lineage_sha256 = canonical_sha256(
        {
            "domain": "tiangong.capability-attribution-lineage.v1",
            "plan_sha256": plan.plan_sha256,
            "trace_sha256": trace.trace_sha256,
            "completion_decision_sha256": completion.decision_sha256,
            "verification_readiness_sha256": (
                completion.verification_readiness_sha256
            ),
            "terminal_fact_hashes": list(trace.terminal_fact_hashes),
            "method_source_refs": [
                item.model_dump(mode="json")
                for item in plan.method_source_refs
            ],
            "action_source_refs": [
                item.model_dump(mode="json")
                for item in plan.action_source_refs
            ],
            "expected_principal_scope_hash": expected_principal_scope_hash,
            "expected_privacy_scope_hash": expected_privacy_scope_hash,
        }
    )
    attribution = AttributionIntegrityV1(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        composition_plan_sha256=plan.plan_sha256,
        state="PASS" if not reasons else "FAIL",
        reason_codes=tuple(sorted(reasons)),
        checked_lineage_sha256=checked_lineage_sha256,
        checked_at_ms=checked_at_ms,
        attribution_sha256="0" * 64,
    )
    return attribution.model_copy(
        update={
            "attribution_sha256": computed_attribution_sha256(attribution)
        }
    )


__all__ = [
    "ATTRIBUTION_TRACE_SCHEMA",
    "AttributionTraceV1",
    "CompletionEvidenceV1",
    "attribution_has_valid_sha256",
    "completion_evidence_from_decision",
    "computed_attribution_sha256",
    "evaluate_attribution_integrity",
]
