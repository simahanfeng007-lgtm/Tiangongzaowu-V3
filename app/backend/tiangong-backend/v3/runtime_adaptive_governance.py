"""P18-M3 adaptive governance policy.

This module is deliberately pure. It owns no persistence, Runtime, Scheduler,
Gateway, Authority, Memory store, Fact store, Effect dispatch, or tool dispatch.
Version-resume governance is re-exported from total_gateway.regenerative_governance
so M3 has one canonical checkpoint-compatibility policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from total_gateway.regenerative_governance import (
    CheckpointVersionVector,
    VersionCompatibilityDecision,
    evaluate_checkpoint_version_compatibility,
    version_vector_from_mapping,
)


UNTRUSTED_DATA = "UNTRUSTED_DATA"
TOOL_RESULT_DATA = "TOOL_RESULT_DATA"


class InstructionSourcePriority(IntEnum):
    """Higher numeric value means higher instruction authority."""

    TOOL_RESULT_DATA = 10
    VERIFIED_USER_INSTRUCTION = 20
    TASK_CONTRACT = 30
    SYSTEM_AUTHORITY = 40


@dataclass(frozen=True)
class FactFreshness:
    """Durability-neutral freshness metadata for a verified world fact."""

    observed_at_ms: int
    valid_until_ms: int = 0
    revalidation_policy: str = "ttl"
    source_version: str = ""
    volatile: bool = False


@dataclass(frozen=True)
class FactFreshnessDecision:
    reusable: bool
    requires_revalidation: bool
    reasons: tuple[str, ...]


def evaluate_fact_freshness(
    freshness: FactFreshness,
    *,
    now_ms: int,
    current_source_version: str = "",
    dependency_reuse: bool = True,
) -> FactFreshnessDecision:
    """Fail closed when a fact is stale or its volatile source changed."""

    reasons: list[str] = []
    policy = str(freshness.revalidation_policy or "ttl").strip().lower()
    current_version = str(current_source_version or "")
    source_version = str(freshness.source_version or "")

    if int(freshness.observed_at_ms) <= 0:
        reasons.append("missing_observed_at")
    if int(freshness.valid_until_ms) > 0 and int(now_ms) > int(freshness.valid_until_ms):
        reasons.append("validity_window_expired")
    if source_version and current_version and source_version != current_version:
        reasons.append("source_version_changed")
    if dependency_reuse and (freshness.volatile or policy in {"always", "on_reuse"}):
        reasons.append("volatile_dependency_revalidation")
    if policy not in {"stable", "ttl", "always", "on_reuse", "source_version"}:
        reasons.append("unknown_revalidation_policy")

    requires = bool(reasons)
    return FactFreshnessDecision(
        reusable=not requires,
        requires_revalidation=requires,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class LearningPromotionEvidence:
    """Evidence required before a factual candidate may become durable learning."""

    fact_status: str = "UNVERIFIED"
    verified: bool = False
    evidence_count: int = 0
    source_count: int = 0
    memory_promotion_eligible: bool = False
    conflict: bool = False
    revoked: bool = False
    requires_multi_source: bool = False
    explicit_user_memory: bool = False


@dataclass(frozen=True)
class LearningPromotionDecision:
    allowed: bool
    candidate_revoked: bool
    reasons: tuple[str, ...]


def evaluate_learning_promotion(
    evidence: LearningPromotionEvidence,
) -> LearningPromotionDecision:
    """Keep execution facts separate from long-term factual learning.

    Explicit user identity/preference/authority memory is intentionally routed
    through the existing Memory SSoT and is not downgraded merely because it is
    not a world-fact inference.
    """

    if evidence.explicit_user_memory:
        return LearningPromotionDecision(
            allowed=True,
            candidate_revoked=False,
            reasons=("explicit_user_memory_ssot",),
        )

    reasons: list[str] = []
    if evidence.revoked or str(evidence.fact_status or "").upper() == "REVOKED":
        reasons.append("fact_revoked")
    if not evidence.verified or str(evidence.fact_status or "").upper() != "VERIFIED":
        reasons.append("fact_not_verified")
    if int(evidence.evidence_count) < 2:
        reasons.append("insufficient_repeated_evidence")
    if not evidence.memory_promotion_eligible:
        reasons.append("memory_promotion_not_satisfied")
    if evidence.conflict:
        reasons.append("fact_conflict")
    if evidence.requires_multi_source and int(evidence.source_count) < 2:
        reasons.append("insufficient_multi_source_evidence")

    return LearningPromotionDecision(
        allowed=not reasons,
        candidate_revoked="fact_revoked" in reasons,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class SemanticDriftSignals:
    root_goal_similarity: float = 1.0
    task_contract_match: bool = True
    active_obligation_consistency: float = 1.0
    authority_reference_match: bool = True
    frontier_contradiction: bool = False
    semantic_handoff_contradiction: bool = False
    repeated_strategy_collapse: float = 0.0
    unverified_claim_accumulation: float = 0.0


@dataclass(frozen=True)
class SemanticDriftDecision:
    score: float
    high_risk: bool
    checkpoint_candidate: bool
    reality_audit: bool
    frontier_rebuild: bool
    replan: bool
    allow_horizon_growth: bool
    reasons: tuple[str, ...]


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def evaluate_semantic_drift(
    signals: SemanticDriftSignals,
    *,
    high_risk_threshold: float = 0.72,
) -> SemanticDriftDecision:
    """Detect semantic drift without creating a second planner or scheduler."""

    root_drift = 1.0 - _unit(signals.root_goal_similarity)
    obligation_drift = 1.0 - _unit(signals.active_obligation_consistency)
    task_mismatch = 0.0 if signals.task_contract_match else 1.0
    authority_mismatch = 0.0 if signals.authority_reference_match else 1.0
    frontier = 1.0 if signals.frontier_contradiction else 0.0
    handoff = 1.0 if signals.semantic_handoff_contradiction else 0.0
    strategy = _unit(signals.repeated_strategy_collapse)
    unverified = _unit(signals.unverified_claim_accumulation)

    score = _unit(
        0.18 * root_drift
        + 0.18 * task_mismatch
        + 0.14 * obligation_drift
        + 0.14 * authority_mismatch
        + 0.12 * frontier
        + 0.10 * handoff
        + 0.07 * strategy
        + 0.07 * unverified
    )
    high = score >= _unit(high_risk_threshold)
    reasons: list[str] = []
    if root_drift >= 0.45:
        reasons.append("root_goal_drift")
    if task_mismatch:
        reasons.append("task_contract_mismatch")
    if obligation_drift >= 0.45:
        reasons.append("active_obligation_drift")
    if authority_mismatch:
        reasons.append("authority_reference_mismatch")
    if frontier:
        reasons.append("frontier_contradiction")
    if handoff:
        reasons.append("semantic_handoff_contradiction")
    if strategy >= 0.65:
        reasons.append("repeated_strategy_collapse")
    if unverified >= 0.65:
        reasons.append("unverified_claim_accumulation")

    return SemanticDriftDecision(
        score=score,
        high_risk=high,
        checkpoint_candidate=high,
        reality_audit=high,
        frontier_rebuild=high,
        replan=high,
        allow_horizon_growth=not high,
        reasons=tuple(reasons),
    )


__all__ = [
    "UNTRUSTED_DATA",
    "TOOL_RESULT_DATA",
    "InstructionSourcePriority",
    "FactFreshness",
    "FactFreshnessDecision",
    "evaluate_fact_freshness",
    "LearningPromotionEvidence",
    "LearningPromotionDecision",
    "evaluate_learning_promotion",
    "SemanticDriftSignals",
    "SemanticDriftDecision",
    "evaluate_semantic_drift",
    "CheckpointVersionVector",
    "VersionCompatibilityDecision",
    "evaluate_checkpoint_version_compatibility",
    "version_vector_from_mapping",
]
