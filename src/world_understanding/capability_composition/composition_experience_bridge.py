"""P12-R1F: bridge composition execution facts into the P5 experience policy.

Read-only over the durable Gateway records of one completed composition
roundtrip: this module builds a ``CapabilityExperienceObservationV1`` whose
attribution chain is exactly the machine-collected lineage (registration,
terminal Effects, Facts, CompletionGate decision). The P5 policy (admission,
statistics, memory intents) stays the sole writer; nothing here admits,
promotes or retires experience by itself.
"""
from __future__ import annotations

from typing import Iterable

from contracts.capability_composition import CapabilityCompositionPlanV1
from world_understanding.capability_composition.capability_experience_attribution import (
    AttributionTraceV1,
    CompletionEvidenceV1,
    completion_evidence_from_decision,
    evaluate_attribution_integrity,
)
from world_understanding.capability_composition.capability_experience_policy import (
    CapabilityExperienceObservationV1,
)

_OUTCOME_MAP = {
    "COMPLETED": "SUCCESS",
    "FAILED": "FAILURE",
    "PARTIAL": "PARTIAL",
    "RECONCILE_REQUIRED": "RECONCILE",
}


def composition_outcome_from_decision(decision: object) -> str:
    """Map the CompletionGate verdict onto the P5 observation outcome."""

    outcome = getattr(decision, "outcome", None)
    mapped = _OUTCOME_MAP.get(str(outcome))
    if mapped is None:
        raise ValueError("COMPOSITION_EXPERIENCE_DECISION_OUTCOME_INVALID")
    return mapped


def build_composition_experience_observation(
    *,
    plan: CapabilityCompositionPlanV1,
    decision: object,
    effects: Iterable[object],
    observed_at_ms: int,
    life_id: str,
    privacy_scope: str,
    privacy_scope_hash: str,
    goal_class: str,
    environment_class: str,
    scene_fingerprint: str,
    failure_reason_codes: tuple[str, ...] = (),
    failure_category: str | None = None,
) -> CapabilityExperienceObservationV1:
    """Assemble one P5 observation from the durable roundtrip records.

    ``effects`` are the terminal EffectLedgerRecord rows of the plan's steps;
    their ids, fact ids and fact hashes become the attribution trace. The
    completion evidence is derived from the CompletionGate decision through
    the original P5 helper — a model or exit code can never stand in for it.
    """

    if not isinstance(plan, CapabilityCompositionPlanV1):
        raise ValueError("COMPOSITION_EXPERIENCE_PLAN_INVALID")
    if observed_at_ms < 0:
        raise ValueError("COMPOSITION_EXPERIENCE_TIME_INVALID")
    records = tuple(effects)
    terminal_effect_ids = tuple(record.claim.effect_id for record in records)
    terminal_fact_ids = tuple(
        fact_id
        for record in records
        if record.result is not None
        for fact_id in (record.result.fact_id,)
    )
    terminal_fact_hashes = tuple(
        record.result.evidence_sha256
        for record in records
        if record.result is not None
    )
    completion: CompletionEvidenceV1 = completion_evidence_from_decision(decision)
    checked_at_ms = max(
        [observed_at_ms]
        + [getattr(record.result, "observed_at_ms", 0) for record in records
           if record.result is not None],
    )
    trace = AttributionTraceV1(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash,
        privacy_scope_hash=privacy_scope_hash,
        composition_plan_sha256=plan.plan_sha256,
        completion=completion,
        terminal_effect_ids=terminal_effect_ids,
        terminal_fact_ids=terminal_fact_ids,
        terminal_fact_hashes=terminal_fact_hashes,
        observed_method_source_refs=tuple(plan.method_source_refs),
        observed_action_source_refs=tuple(plan.action_source_refs),
        has_acceptance_obligations=bool(completion.verification_plan_sha256),
        active_verification_plan_complete=completion.verification_ready,
        effect_fact_lineage_complete=bool(terminal_effect_ids)
        and bool(terminal_fact_ids)
        and len(terminal_effect_ids) == len(terminal_fact_ids),
        source_refs_complete=True,
        source_revisions_continuous=True,
        request_scope_continuous=True,
        collected_at_ms=observed_at_ms,
        trace_sha256="0" * 64,
    ).with_computed_sha256()
    attribution = evaluate_attribution_integrity(
        plan, trace,
        expected_principal_scope_hash=plan.principal_scope_hash,
        expected_privacy_scope_hash=privacy_scope_hash,
        checked_at_ms=checked_at_ms)
    outcome = composition_outcome_from_decision(decision)
    quality_milli = 1000 if outcome == "SUCCESS" else 0
    draft = CapabilityExperienceObservationV1(
        observation_id="ceo_" + plan.plan_sha256[:60],
        life_id=life_id,
        principal_ref="principal:" + plan.principal_scope_hash[:32],
        principal_scope_hash=plan.principal_scope_hash,
        privacy_scope=privacy_scope,
        privacy_scope_hash=privacy_scope_hash,
        goal_class=goal_class,
        environment_class=environment_class,
        scene_fingerprint=scene_fingerprint,
        context_fingerprint_sha256=plan.context_fingerprint_sha256,
        composition_topology_sha256=plan.dependency_graph_sha256,
        plan=plan,
        trace=trace,
        attribution=attribution,
        outcome=outcome,
        quality_milli=quality_milli,
        failure_reason_codes=tuple(failure_reason_codes),
        failure_category=failure_category,
        observed_at_ms=observed_at_ms,
        observation_sha256="0" * 64,
    )
    return draft.with_computed_sha256()


__all__ = [
    "build_composition_experience_observation",
    "composition_outcome_from_decision",
]
