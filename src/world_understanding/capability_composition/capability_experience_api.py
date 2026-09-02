"""Guarded public API over the pure P5 capability-experience policy engine.

The underlying policy module contains immutable contracts and deterministic
math. This facade adds cross-call invariants that require more than one record:
monotonic time, explicit failure-reason retention, policy-config integrity,
decay-aware recall, and memory-intent/negative-evidence consistency.
"""

from __future__ import annotations

from contracts import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1

from .capability_experience_policy import (
    DEFAULT_CAPABILITY_EXPERIENCE_POLICY,
    CapabilityExperienceAdmissionDecisionV1,
    CapabilityExperienceAggregateStateV1,
    CapabilityExperienceMemoryIntentV1,
    CapabilityExperienceObservationV1,
    CapabilityExperiencePolicyConfigV1,
    CapabilityExperienceRecallItemV1,
    CapabilityExperienceRecallQueryV1,
    NegativeCapabilityEvidenceV1,
    apply_capability_experience_observation as _apply_observation,
    build_capability_experience_memory_intent as _build_memory_intent,
    evaluate_capability_experience_admission as _evaluate_admission,
    mark_capability_experience_source_change as _mark_source_change,
    recall_capability_experiences as _recall_experiences,
)


def exact_source_hashes(
    sources: tuple[SourceRevisionRefV1, ...],
) -> tuple[str, ...]:
    """Return canonical, duplicate-free exact source revision hashes."""

    if not sources:
        raise ValueError("exact source revisions cannot be empty")
    return tuple(
        sorted(
            {
                canonical_sha256(source.model_dump(mode="json"))
                for source in sources
            }
        )
    )


def _rebuild_admission(
    admission: CapabilityExperienceAdmissionDecisionV1,
    *,
    decision: str | None = None,
    reason_codes: tuple[str, ...] | None = None,
    failure_category: str | None = None,
) -> CapabilityExperienceAdmissionDecisionV1:
    target = decision or admission.decision
    positive = target == "POSITIVE_EXPERIENCE"
    negative = target in {
        "NEGATIVE_EVIDENCE",
        "MARK_STALE",
        "REQUIRE_REVALIDATION",
    }
    value = admission.model_copy(
        update={
            "decision": target,
            "positive_allowed": positive,
            "negative_allowed": negative,
            "reason_codes": (
                admission.reason_codes
                if reason_codes is None
                else reason_codes
            ),
            "failure_category": (
                None if positive or target == "NO_MEMORY" else failure_category
            ),
            "decision_sha256": "0" * 64,
        }
    )
    return value.with_computed_sha256()


def evaluate_capability_experience_admission(
    observation: CapabilityExperienceObservationV1,
    *,
    expected_principal_scope_hash: str,
    expected_privacy_scope_hash: str,
    config: CapabilityExperiencePolicyConfigV1 = (
        DEFAULT_CAPABILITY_EXPERIENCE_POLICY
    ),
    decided_at_ms: int,
) -> CapabilityExperienceAdmissionDecisionV1:
    """Apply admission and preserve every explicit machine failure reason."""

    admission = _evaluate_admission(
        observation,
        expected_principal_scope_hash=expected_principal_scope_hash,
        expected_privacy_scope_hash=expected_privacy_scope_hash,
        config=config,
        decided_at_ms=decided_at_ms,
    )
    reasons = set(admission.reason_codes) | set(
        observation.failure_reason_codes
    )
    force_negative = False
    failure_category = admission.failure_category
    if observation.observed_at_ms < observation.attribution.checked_at_ms:
        reasons.add("capability_experience.attribution_time_inverted")
        failure_category = "ATTRIBUTION_FAILURE"
        force_negative = True
    if observation.outcome == "SUCCESS" and observation.failure_reason_codes:
        reasons.add("capability_experience.success_has_failure_evidence")
        failure_category = "ATTRIBUTION_FAILURE"
        force_negative = True
    if force_negative:
        return _rebuild_admission(
            admission,
            decision="NEGATIVE_EVIDENCE",
            reason_codes=tuple(sorted(reasons)),
            failure_category=failure_category,
        )
    if admission.negative_allowed:
        return _rebuild_admission(
            admission,
            reason_codes=tuple(sorted(reasons)),
            failure_category=admission.failure_category,
        )
    return admission


def apply_capability_experience_observation(
    prior: CapabilityExperienceAggregateStateV1 | None,
    observation: CapabilityExperienceObservationV1,
    admission: CapabilityExperienceAdmissionDecisionV1,
    *,
    config: CapabilityExperiencePolicyConfigV1 = (
        DEFAULT_CAPABILITY_EXPERIENCE_POLICY
    ),
) -> tuple[
    CapabilityExperienceAggregateStateV1,
    NegativeCapabilityEvidenceV1 | None,
]:
    if not config.has_valid_sha256():
        raise ValueError("capability experience policy config hash is invalid")
    return _apply_observation(
        prior, observation, admission, config=config
    )


def mark_capability_experience_source_change(
    state: CapabilityExperienceAggregateStateV1,
    current_sources: tuple[SourceRevisionRefV1, ...],
    *,
    requested_at_ms: int,
):
    if requested_at_ms < state.last_observed_at_ms:
        raise ValueError("source-change request predates experience state")
    return _mark_source_change(
        state, current_sources, requested_at_ms=requested_at_ms
    )


def build_capability_experience_memory_intent(
    state: CapabilityExperienceAggregateStateV1,
    *,
    parent_derivation_ids: tuple[str, ...],
    negative_evidence: NegativeCapabilityEvidenceV1 | None = None,
    created_at_ms: int,
) -> tuple[CapabilityExperienceMemoryIntentV1, bytes]:
    if created_at_ms < state.last_observed_at_ms:
        raise ValueError("memory intent predates experience state")
    if negative_evidence is not None and (
        negative_evidence.evidence_id not in state.negative_evidence_ids
    ):
        raise ValueError("negative evidence is not part of the experience state")
    return _build_memory_intent(
        state,
        parent_derivation_ids=parent_derivation_ids,
        negative_evidence=negative_evidence,
        created_at_ms=created_at_ms,
    )


def recall_capability_experiences(
    states: tuple[CapabilityExperienceAggregateStateV1, ...],
    query: CapabilityExperienceRecallQueryV1,
    *,
    config: CapabilityExperiencePolicyConfigV1 = (
        DEFAULT_CAPABILITY_EXPERIENCE_POLICY
    ),
) -> tuple[CapabilityExperienceRecallItemV1, ...]:
    """Recall only recent positive experience under the exact current scope."""

    if not config.has_valid_sha256():
        raise ValueError("capability experience policy config hash is invalid")
    recent = tuple(
        state
        for state in states
        if state.experience.last_success_ms is not None
        and state.experience.last_success_ms <= query.now_ms
        and query.now_ms - state.experience.last_success_ms
        <= config.evidence_decay_window_ms
    )
    return _recall_experiences(recent, query)


__all__ = [
    "apply_capability_experience_observation",
    "build_capability_experience_memory_intent",
    "evaluate_capability_experience_admission",
    "exact_source_hashes",
    "mark_capability_experience_source_change",
    "recall_capability_experiences",
]
