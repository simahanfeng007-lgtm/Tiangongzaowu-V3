"""P10 projection feedback telemetry; never enters World Data or authorization."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ProjectionFeedbackObservation:
    life_id: str
    world_scope_hash: str
    query_id: str
    token_budget: int
    estimated_tokens: int
    overflow_state: str
    optional_item_count: int
    expansion_handle_count: int
    expansion_use_count: int
    created_at_ms: int
    telemetry_only: bool = True
    empirical_evidence_weight_milli: int = 0
    def __post_init__(self) -> None:
        if not self.life_id or len(self.world_scope_hash) != 64 or not self.query_id or min(self.token_budget, self.estimated_tokens, self.optional_item_count, self.expansion_handle_count, self.expansion_use_count, self.created_at_ms) < 0:
            raise ValueError("PROJECTION_FEEDBACK_INVALID")
        if self.expansion_use_count > self.expansion_handle_count:
            raise ValueError("PROJECTION_EXPANSION_USE_INVALID")

@dataclass(frozen=True, slots=True)
class ProjectionFeedbackProfile:
    life_id: str
    world_scope_hash: str
    sample_count: int
    mean_token_utilization_milli: int
    truncation_rate_milli: int
    expansion_use_rate_milli: int
    mean_optional_items: int
    recommended_optional_scale_milli: int
    telemetry_only: bool = True
    empirical_evidence_weight_milli: int = 0


def build_projection_feedback(observations: tuple[ProjectionFeedbackObservation, ...]) -> ProjectionFeedbackProfile:
    if not observations:
        raise ValueError("PROJECTION_FEEDBACK_REQUIRED")
    n = len(observations)
    life_id = observations[0].life_id
    world_scope_hash = observations[0].world_scope_hash
    if any(item.life_id != life_id or item.world_scope_hash != world_scope_hash for item in observations):
        raise ValueError("PROJECTION_FEEDBACK_SCOPE_MISMATCH")
    utilization = []
    trunc = 0
    handles = 0
    used = 0
    optional = 0
    for item in observations:
        if not item.telemetry_only or item.empirical_evidence_weight_milli != 0:
            raise ValueError("PROJECTION_FEEDBACK_AUTHORITY_INVALID")
        utilization.append(0 if item.token_budget == 0 else min(1000, (item.estimated_tokens * 1000) // item.token_budget))
        trunc += int(item.overflow_state in {"BUDGET_TRUNCATED", "MANDATORY_OVERFLOW"})
        handles += item.expansion_handle_count
        used += item.expansion_use_count
        optional += item.optional_item_count
    trunc_rate = (trunc * 1000) // n
    expansion_rate = 0 if handles == 0 else (used * 1000) // handles
    # Low expansion usage plus frequent truncation is a measured signal to shrink optional projection work.
    scale = 1000
    if trunc_rate >= 500 and expansion_rate <= 250:
        scale = 500
    elif trunc_rate >= 250 and expansion_rate <= 400:
        scale = 750
    return ProjectionFeedbackProfile(life_id, world_scope_hash, n, sum(utilization)//n, trunc_rate, expansion_rate, optional//n, scale)

__all__ = ["ProjectionFeedbackObservation", "ProjectionFeedbackProfile", "build_projection_feedback"]
