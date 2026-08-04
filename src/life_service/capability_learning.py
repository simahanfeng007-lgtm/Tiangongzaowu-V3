"""Evidence-gated capability learning, confidence bounds, and rollback records."""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import Iterable

from contracts import (
    ActionImpact,
    CapabilityEvidence,
    CapabilityLearningDecision,
    CapabilityProfile,
    CapabilityRollbackRecord,
    EpisodeOutcomeEvidence,
    ReflectionCard,
    canonical_json_bytes,
    canonical_sha256,
)


LEARNING_COOLDOWN_MS = 7 * 86_400_000
PROFICIENCY_LCB_THRESHOLD_MILLI = 600
_RISK_ORDER = ("A0", "A1", "A2", "A3", "A4", "A5")
_THRESHOLDS = {
    "A0": (5, 3),
    "A1": (5, 3),
    "A2": (8, 4),
    "A3": (12, 6),
    "A4": (20, 10),
    "A5": (30, 15),
}


@dataclass(frozen=True, slots=True)
class CapabilityLearningResult:
    profile: CapabilityProfile
    decision: CapabilityLearningDecision


@dataclass(frozen=True, slots=True)
class CapabilityRollbackResult:
    profile: CapabilityProfile
    record: CapabilityRollbackRecord


def _validated(value, model, digest_method: str, label: str):
    try:
        parsed = model.model_validate_json(canonical_json_bytes(value))
    except Exception as exc:
        raise ValueError(f"{label} contract is invalid") from exc
    if not getattr(parsed, digest_method)():
        raise ValueError(f"{label} digest is invalid")
    return parsed


def build_capability_evidence(
    outcome: EpisodeOutcomeEvidence,
    reflection: ReflectionCard,
    impact: ActionImpact,
    *,
    capability_id: str,
    capability_version: str,
    now_ms: int,
) -> CapabilityEvidence:
    outcome = _validated(
        outcome, EpisodeOutcomeEvidence, "has_valid_evidence_sha256", "episode outcome"
    )
    reflection = _validated(
        reflection, ReflectionCard, "has_valid_reflection_sha256", "reflection"
    )
    impact = _validated(impact, ActionImpact, "has_valid_impact_sha256", "action impact")
    if not (outcome.life_id == reflection.life_id == impact.life_id):
        raise ValueError("capability evidence crosses life identities")
    if outcome.episode_id != reflection.episode_id:
        raise ValueError("capability evidence crosses causal episodes")
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < outcome.occurred_at_ms:
        raise ValueError("capability evidence time is invalid")
    if (
        outcome.supported_cause_ids
        and not outcome.counterevidence_refs
        and not outcome.alternative_explanation_refs
    ):
        causal_support = "supported"
    elif outcome.counterevidence_refs:
        causal_support = "refuted"
    elif outcome.alternative_explanation_refs:
        causal_support = "plausible"
    else:
        causal_support = "correlation_only"
    verified = bool(outcome.completion_decision_sha256 and outcome.terminal_fact_hashes)
    eligible_success = (
        verified
        and outcome.outcome_status == "success"
        and outcome.method_attribution == "capability"
        and causal_support == "supported"
        and outcome.observed_quality_milli >= 800
    )
    eligible_failure = (
        verified
        and outcome.outcome_status == "failure"
        and outcome.method_attribution == "capability"
    )
    evidence_fields = {
        "capability_id": capability_id,
        "capability_version": capability_version,
        "life_id": outcome.life_id,
        "episode_id": outcome.episode_id,
        "reflection_id": reflection.reflection_id,
        "context_fingerprint_sha256": outcome.context_fingerprint_sha256,
        "outcome": outcome.outcome_status,
        "attribution": outcome.method_attribution,
        "causal_support": causal_support,
        "verified": verified,
        "quality_milli": outcome.observed_quality_milli,
        "prediction_error_milli": reflection.prediction_error_milli,
        "terminal_fact_hashes": outcome.terminal_fact_hashes,
        "action_impact_sha256": impact.impact_sha256,
        "impact_floor": _impact_floor(impact),
        "touches_core_code": impact.touches_core_code,
        "eligible_success": eligible_success,
        "eligible_failure": eligible_failure,
        "created_at_ms": now_ms,
    }
    evidence_id = "cpe_" + canonical_sha256(
        {"domain": "tiangong.life.capability-evidence-id.v1", **evidence_fields}
    )
    return CapabilityEvidence(
        evidence_id=evidence_id,
        **evidence_fields,
        evidence_sha256="0" * 64,
    ).with_computed_evidence_sha256()


def _impact_floor(impact: ActionImpact) -> str:
    critical = max(
        impact.workspace_scope_milli,
        impact.credential_scope_milli,
        impact.privacy_scope_milli,
        impact.blast_radius_milli,
        impact.irreversibility_milli,
        impact.uncertainty_milli,
    )
    if impact.external_recipient_count:
        critical = max(critical, 700)
    if impact.touches_core_code or impact.credential_scope_milli:
        critical = max(critical, 900)
    if impact.touches_identity or impact.touches_soul or impact.touches_memory_keys or impact.touches_policy:
        critical = 1000
    if critical == 0:
        return "A0"
    if critical <= 200:
        return "A1"
    if critical <= 400:
        return "A2"
    if critical <= 600:
        return "A3"
    if critical <= 800:
        return "A4"
    return "A5"


def learn_capability(
    evidences: Iterable[CapabilityEvidence],
    *,
    scope: str,
    now_ms: int,
    previous_profile: CapabilityProfile | None = None,
    previous_decision: CapabilityLearningDecision | None = None,
) -> CapabilityLearningResult:
    parsed = tuple(
        _validated(item, CapabilityEvidence, "has_valid_evidence_sha256", "capability evidence")
        for item in evidences
    )
    if not parsed:
        raise ValueError("capability learning requires evidence")
    if len({item.evidence_id for item in parsed}) != len(parsed):
        raise ValueError("capability evidence identity is duplicated")
    identities = {
        (item.capability_id, item.capability_version, item.life_id) for item in parsed
    }
    if len(identities) != 1:
        raise ValueError("capability evidence crosses profiles")
    capability_id, version, life_id = next(iter(identities))
    if previous_profile is not None:
        previous_profile = _validated(
            previous_profile,
            CapabilityProfile,
            "has_valid_profile_sha256",
            "capability profile",
        )
        if (
            previous_profile.capability_id,
            previous_profile.version,
            previous_profile.life_id,
        ) != (capability_id, version, life_id):
            raise ValueError("previous capability profile has another identity")
    if previous_decision is not None:
        previous_decision = _validated(
            previous_decision,
            CapabilityLearningDecision,
            "has_valid_decision_sha256",
            "capability learning decision",
        )
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("capability learning time is invalid")

    successes = sum(item.eligible_success for item in parsed)
    failures = sum(item.eligible_failure for item in parsed)
    eligible = tuple(item for item in parsed if item.eligible_success or item.eligible_failure)
    contexts = len({item.context_fingerprint_sha256 for item in eligible})
    sample_count = successes + failures
    mean = 0 if sample_count == 0 else successes * 1000 // sample_count
    # One-sided 95% Hoeffding lower bound, implemented with integer square root.
    penalty = 1000 if sample_count == 0 else min(
        1000, isqrt((1_497_900 + sample_count - 1) // sample_count)
    )
    lower = max(0, mean - penalty)
    risk = max((item.impact_floor for item in parsed), key=_RISK_ORDER.index)
    core = any(item.touches_core_code for item in parsed)
    threshold_risk = "A5" if core else risk
    minimum_successes, minimum_contexts = _THRESHOLDS[threshold_risk]
    cooldown_active = (
        previous_decision is not None and now_ms < previous_decision.cooldown_until_ms
    )
    reasons: list[str] = []
    if cooldown_active:
        outcome, review = "hold", "OBSERVE"
        reasons.append("capability.cooldown_active")
    elif successes < minimum_successes:
        outcome, review = "hold", "OBSERVE"
        reasons.append("capability.success_samples_insufficient")
    elif contexts < minimum_contexts:
        outcome, review = "hold", "OBSERVE"
        reasons.append("capability.context_diversity_insufficient")
    elif lower < PROFICIENCY_LCB_THRESHOLD_MILLI:
        outcome, review = "hold", "OBSERVE"
        reasons.append("capability.lower_confidence_bound_insufficient")
    elif core or risk == "A5":
        outcome, review = "core_review", "CORE_REVIEW"
        reasons.append("capability.core_review_required")
    elif risk in {"A3", "A4"}:
        outcome, review = "human_review", "HUMAN_REVIEW"
        reasons.append("capability.human_review_required")
    else:
        outcome, review = "sandbox_candidate", "SANDBOX"
        reasons.append("capability.sandbox_candidate")

    evidence_refs = tuple(sorted(item.evidence_id for item in parsed))
    profile = CapabilityProfile(
        capability_id=capability_id,
        life_id=life_id,
        version=version,
        profile_revision=1 if previous_profile is None else previous_profile.profile_revision + 1,
        supersedes_profile_sha256=(
            None if previous_profile is None else previous_profile.profile_sha256
        ),
        scope=scope,
        verified_successes=successes,
        verified_failures=failures,
        independent_context_count=contexts,
        calibration_error_milli=(
            0
            if not parsed
            else sum(item.prediction_error_milli for item in parsed) // len(parsed)
        ),
        rollback_count=0 if previous_profile is None else previous_profile.rollback_count,
        last_regression_at_ms=(
            None if previous_profile is None else previous_profile.last_regression_at_ms
        ),
        proficiency_mean_milli=mean,
        proficiency_lower_bound_milli=lower,
        evidence_refs=evidence_refs,
        impact_floor=risk,
        review_level=review,
        updated_at_ms=now_ms,
        profile_sha256="0" * 64,
    ).with_computed_profile_sha256()
    evidence_set_sha256 = canonical_sha256(
        {"domain": "tiangong.life.capability-evidence-set.v1", "evidence_refs": evidence_refs}
    )
    fields = {
        "capability_id": capability_id,
        "capability_version": version,
        "life_id": life_id,
        "previous_profile_sha256": (
            None if previous_profile is None else previous_profile.profile_sha256
        ),
        "evidence_set_sha256": evidence_set_sha256,
        "eligible_successes": successes,
        "eligible_failures": failures,
        "independent_context_count": contexts,
        "minimum_successes": minimum_successes,
        "minimum_independent_contexts": minimum_contexts,
        "proficiency_mean_milli": mean,
        "proficiency_lower_bound_milli": lower,
        "outcome": outcome,
        "review_level": review,
        "reason_codes": tuple(sorted(reasons)),
        "cooldown_until_ms": now_ms + LEARNING_COOLDOWN_MS,
        "resulting_profile_sha256": profile.profile_sha256,
        "created_at_ms": now_ms,
    }
    decision_id = "cld_" + canonical_sha256(
        {"domain": "tiangong.life.capability-learning-decision-id.v1", **fields}
    )
    decision = CapabilityLearningDecision(
        learning_decision_id=decision_id,
        **fields,
        decision_sha256="0" * 64,
    ).with_computed_decision_sha256()
    return CapabilityLearningResult(profile, decision)


def rollback_capability(
    profile: CapabilityProfile,
    trigger_evidences: Iterable[CapabilityEvidence],
    *,
    invalidated_context_pack_ids: tuple[str, ...],
    invalidated_skill_activation_ids: tuple[str, ...],
    now_ms: int,
) -> CapabilityRollbackResult:
    profile = _validated(
        profile, CapabilityProfile, "has_valid_profile_sha256", "capability profile"
    )
    triggers = tuple(
        _validated(item, CapabilityEvidence, "has_valid_evidence_sha256", "rollback evidence")
        for item in trigger_evidences
    )
    if not triggers or any(
        item.capability_id != profile.capability_id
        or item.capability_version != profile.version
        or item.life_id != profile.life_id
        or not item.eligible_failure
        for item in triggers
    ):
        raise ValueError("capability rollback lacks verified attributed failure evidence")
    if tuple(sorted(set(invalidated_context_pack_ids))) != invalidated_context_pack_ids:
        raise ValueError("rollback context invalidations must be sorted and unique")
    if tuple(sorted(set(invalidated_skill_activation_ids))) != invalidated_skill_activation_ids:
        raise ValueError("rollback Skill invalidations must be sorted and unique")
    evidence_refs = tuple(
        sorted(set((*profile.evidence_refs, *(item.evidence_id for item in triggers))))
    )
    rolled_back = CapabilityProfile(
        **{
            **profile.model_dump(mode="python"),
            "profile_revision": profile.profile_revision + 1,
            "supersedes_profile_sha256": profile.profile_sha256,
            "verified_failures": profile.verified_failures + len(triggers),
            "rollback_count": profile.rollback_count + 1,
            "last_regression_at_ms": now_ms,
            "proficiency_mean_milli": 0,
            "proficiency_lower_bound_milli": 0,
            "evidence_refs": evidence_refs,
            "review_level": (
                "CORE_REVIEW"
                if profile.impact_floor in {"A4", "A5"}
                else "HUMAN_REVIEW"
            ),
            "updated_at_ms": now_ms,
            "profile_sha256": "0" * 64,
        }
    ).with_computed_profile_sha256()
    fields = {
        "capability_id": profile.capability_id,
        "capability_version": profile.version,
        "life_id": profile.life_id,
        "rolled_back_profile_sha256": profile.profile_sha256,
        "resulting_profile_sha256": rolled_back.profile_sha256,
        "trigger_evidence_ids": tuple(sorted(item.evidence_id for item in triggers)),
        "invalidated_context_pack_ids": invalidated_context_pack_ids,
        "invalidated_skill_activation_ids": invalidated_skill_activation_ids,
        "reason_codes": ("capability.verified_regression",),
        "created_at_ms": now_ms,
    }
    rollback_id = "crb_" + canonical_sha256(
        {"domain": "tiangong.life.capability-rollback-id.v1", **fields}
    )
    record = CapabilityRollbackRecord(
        rollback_id=rollback_id,
        **fields,
        rollback_sha256="0" * 64,
    ).with_computed_rollback_sha256()
    return CapabilityRollbackResult(rolled_back, record)


__all__ = [
    "CapabilityLearningResult",
    "CapabilityRollbackResult",
    "LEARNING_COOLDOWN_MS",
    "PROFICIENCY_LCB_THRESHOLD_MILLI",
    "build_capability_evidence",
    "learn_capability",
    "rollback_capability",
]
