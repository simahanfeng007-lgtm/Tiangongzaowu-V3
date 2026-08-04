"""Deterministic score, risk floor, ranking, and autonomy state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from contracts import (
    ActionCandidate,
    ActionImpact,
    AgencyDecision,
    AgencyScoreBreakdown,
    AutonomyActionUsage,
    AutonomyPolicySnapshot,
    AutonomyUsageSnapshot,
    ViabilityState,
    canonical_json_bytes,
    canonical_sha256,
)


_RISK_ORDER = ("A0", "A1", "A2", "A3", "A4", "A5")
_LEVEL_RISK_CEILING = {
    "L0": "A0",
    "L1": "A0",
    "L2": "A0",
    "L3": "A1",
    "L4": "A3",
    "L5": "A0",
}


@dataclass(frozen=True, slots=True)
class RankedActionCandidate:
    candidate: ActionCandidate
    impact: ActionImpact
    score: AgencyScoreBreakdown
    computed_risk: str
    decision_confidence_milli: int


def _risk_from_milli(value: int) -> str:
    if value == 0:
        return "A0"
    if value <= 200:
        return "A1"
    if value <= 400:
        return "A2"
    if value <= 600:
        return "A3"
    if value <= 800:
        return "A4"
    return "A5"


def compute_action_risk_floor(impact: ActionImpact) -> str:
    try:
        impact = ActionImpact.model_validate_json(canonical_json_bytes(impact))
    except Exception as exc:
        raise ValueError("action impact contract is invalid") from exc
    if not impact.has_valid_impact_sha256():
        raise ValueError("action impact digest is invalid")
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
    if impact.touches_core_code:
        critical = max(critical, 900)
    if (
        impact.touches_identity
        or impact.touches_soul
        or impact.touches_memory_keys
        or impact.touches_policy
    ):
        critical = 1000
    if impact.credential_scope_milli:
        critical = max(critical, 900)
    return _risk_from_milli(critical)


def max_risk_class(*risk_classes: str) -> str:
    if not risk_classes or any(item not in _RISK_ORDER for item in risk_classes):
        raise ValueError("risk class set is invalid")
    return max(risk_classes, key=_RISK_ORDER.index)


def min_risk_class(*risk_classes: str) -> str:
    if not risk_classes or any(item not in _RISK_ORDER for item in risk_classes):
        raise ValueError("risk class set is invalid")
    return min(risk_classes, key=_RISK_ORDER.index)


def compute_agency_score(
    *,
    goal_gain_milli: int,
    viability_gain_milli: int,
    information_gain_milli: int,
    relationship_value_milli: int,
    resource_cost_milli: int,
    expected_harm_milli: int,
    uncertainty_penalty_milli: int,
    irreversibility_penalty_milli: int,
) -> AgencyScoreBreakdown:
    values = (
        goal_gain_milli,
        viability_gain_milli,
        information_gain_milli,
        relationship_value_milli,
        resource_cost_milli,
        expected_harm_milli,
        uncertainty_penalty_milli,
        irreversibility_penalty_milli,
    )
    if any(
        isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 1000
        for item in values
    ):
        raise ValueError("agency score inputs must be integer milli values")
    expected = (
        goal_gain_milli
        + viability_gain_milli
        + information_gain_milli
        + relationship_value_milli
        - resource_cost_milli
        - expected_harm_milli
        - irreversibility_penalty_milli
    )
    return AgencyScoreBreakdown(
        goal_gain_milli=goal_gain_milli,
        viability_gain_milli=viability_gain_milli,
        information_gain_milli=information_gain_milli,
        relationship_value_milli=relationship_value_milli,
        resource_cost_milli=resource_cost_milli,
        expected_harm_milli=expected_harm_milli,
        uncertainty_penalty_milli=uncertainty_penalty_milli,
        irreversibility_penalty_milli=irreversibility_penalty_milli,
        expected_utility_milli=expected,
        utility_lcb_milli=expected - uncertainty_penalty_milli,
    )


def _revalidate(value, model, label: str):
    try:
        parsed = model.model_validate_json(canonical_json_bytes(value))
    except Exception as exc:
        raise ValueError(f"{label} contract is invalid") from exc
    digest_checks = {
        ActionCandidate: "has_valid_candidate_sha256",
        ActionImpact: "has_valid_impact_sha256",
        AgencyDecision: "has_valid_decision_sha256",
        AutonomyPolicySnapshot: "has_valid_policy_sha256",
        AutonomyUsageSnapshot: "has_valid_usage_sha256",
        ViabilityState: "has_valid_state_sha256",
    }
    if not getattr(parsed, digest_checks[model])():
        raise ValueError(f"{label} digest is invalid")
    return parsed


def advance_autonomy_usage(
    previous: AutonomyUsageSnapshot,
    *,
    policy: AutonomyPolicySnapshot,
    decision: AgencyDecision,
    candidate: ActionCandidate,
    impact: ActionImpact,
) -> AutonomyUsageSnapshot:
    """Derive the only valid next usage fact for one executing decision."""

    previous = _revalidate(previous, AutonomyUsageSnapshot, "autonomy usage")
    policy = _revalidate(policy, AutonomyPolicySnapshot, "autonomy policy")
    decision = _revalidate(decision, AgencyDecision, "agency decision")
    candidate = _revalidate(candidate, ActionCandidate, "action candidate")
    impact = _revalidate(impact, ActionImpact, "action impact")
    if decision.outcome != "execute":
        raise ValueError("only an executing decision consumes autonomy budget")
    if not (
        previous.life_id
        == policy.life_id
        == decision.life_id
        == candidate.life_id
        == impact.life_id
    ):
        raise ValueError("autonomy usage update crosses life identities")
    if (
        previous.policy_snapshot_hash != policy.policy_sha256
        or decision.policy_snapshot_hash != policy.policy_sha256
    ):
        raise ValueError("autonomy usage update crosses policy snapshots")
    if decision.selected_candidate_id != candidate.candidate_id:
        raise ValueError("autonomy decision is not bound to the candidate")
    if (
        decision.action_impact_sha256 != impact.impact_sha256
        or candidate.action_id != impact.action_id
    ):
        raise ValueError("autonomy decision is not bound to the impact")
    if not previous.day_start_ms <= decision.created_at_ms < previous.day_end_ms:
        raise ValueError("autonomy decision is outside the usage day")
    if decision.decision_sha256 in previous.source_decision_hashes:
        raise ValueError("autonomy decision budget was already consumed")

    by_action = {item.action_id: item for item in previous.action_usage}
    old = by_action.get(candidate.action_id)
    by_action[candidate.action_id] = AutonomyActionUsage(
        action_id=candidate.action_id,
        execution_count=1 if old is None else old.execution_count + 1,
        last_executed_at_ms=decision.created_at_ms,
    )
    execution_count = previous.execution_count + 1
    resource_cost = previous.resource_cost_milli + impact.estimated_resource_cost_milli
    if execution_count > policy.daily_execution_budget:
        raise ValueError("autonomy execution budget would be exceeded")
    if resource_cost > policy.daily_resource_budget_milli:
        raise ValueError("autonomy resource budget would be exceeded")
    if by_action[candidate.action_id].execution_count > policy.per_action_daily_limit:
        raise ValueError("autonomy action frequency would be exceeded")
    return AutonomyUsageSnapshot(
        life_id=previous.life_id,
        policy_snapshot_hash=previous.policy_snapshot_hash,
        revision=previous.revision + 1,
        supersedes_usage_sha256=previous.usage_sha256,
        day_start_ms=previous.day_start_ms,
        day_end_ms=previous.day_end_ms,
        execution_count=execution_count,
        resource_cost_milli=resource_cost,
        action_usage=tuple(by_action[name] for name in sorted(by_action)),
        source_decision_hashes=tuple(
            sorted((*previous.source_decision_hashes, decision.decision_sha256))
        ),
        created_at_ms=decision.created_at_ms,
        usage_sha256="0" * 64,
    ).with_computed_usage_sha256()


def rank_action_candidate(
    candidate: ActionCandidate,
    impact: ActionImpact,
    viability: ViabilityState,
) -> RankedActionCandidate:
    candidate = _revalidate(candidate, ActionCandidate, "action candidate")
    impact = _revalidate(impact, ActionImpact, "action impact")
    viability = _revalidate(viability, ViabilityState, "viability state")
    if candidate.life_id != impact.life_id or candidate.life_id != viability.life_id:
        raise ValueError("agency inputs cross life identities")
    if candidate.action_id != impact.action_id:
        raise ValueError("action candidate and impact refer to different actions")

    dimensions = viability.dimensions()
    viability_gain = 0
    predicted_harm = 0
    prediction_confidences: list[int] = []
    for delta in impact.predicted_viability_deltas:
        dimension = dimensions[delta.dimension]
        deficit = max(0, dimension.target_low_milli - dimension.value_milli)
        prediction_confidences.append(delta.confidence_milli)
        if delta.delta_milli > 0:
            # Healthy dimensions retain a small maintenance value; active deficits dominate.
            relevance = max(100, deficit)
            viability_gain += (
                delta.delta_milli * delta.confidence_milli * relevance // 1_000_000
            )
        elif delta.delta_milli < 0:
            predicted_harm += (-delta.delta_milli) * delta.confidence_milli // 1000
    viability_gain = min(1000, viability_gain)
    state_confidence = min(
        dimension.confidence_milli for dimension in dimensions.values()
    )
    decision_confidence = min(
        candidate.benefit_confidence_milli,
        state_confidence,
        min(prediction_confidences, default=1000 - impact.uncertainty_milli),
        1000 - impact.uncertainty_milli,
    )
    benefit_confidence = candidate.benefit_confidence_milli
    goal_gain = candidate.goal_gain_milli * benefit_confidence // 1000
    information_gain = candidate.information_gain_milli * benefit_confidence // 1000
    relationship_value = candidate.relationship_value_milli * benefit_confidence // 1000
    deterministic_harm = max(
        impact.workspace_scope_milli,
        impact.credential_scope_milli,
        impact.privacy_scope_milli,
        impact.blast_radius_milli,
    )
    expected_harm = min(1000, max(deterministic_harm, predicted_harm))
    uncertainty_penalty = max(
        impact.uncertainty_milli,
        1000 - candidate.benefit_confidence_milli,
        1000 - state_confidence,
    )
    score = compute_agency_score(
        goal_gain_milli=goal_gain,
        viability_gain_milli=viability_gain,
        information_gain_milli=information_gain,
        relationship_value_milli=relationship_value,
        resource_cost_milli=impact.estimated_resource_cost_milli,
        expected_harm_milli=expected_harm,
        uncertainty_penalty_milli=uncertainty_penalty,
        irreversibility_penalty_milli=impact.irreversibility_milli,
    )
    return RankedActionCandidate(
        candidate=candidate,
        impact=impact,
        score=score,
        computed_risk=compute_action_risk_floor(impact),
        decision_confidence_milli=decision_confidence,
    )


def _in_active_window(policy: AutonomyPolicySnapshot, now_ms: int) -> bool:
    start = policy.active_window_start_minute_utc
    end = policy.active_window_end_minute_utc
    if start == end:
        return True
    minute = (now_ms // 60_000) % 1440
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _usage_for_action(usage: AutonomyUsageSnapshot, action_id: str):
    return next((item for item in usage.action_usage if item.action_id == action_id), None)


def decide_autonomy(
    candidates: Iterable[ActionCandidate],
    *,
    impacts_by_action: Mapping[str, ActionImpact],
    viability: ViabilityState,
    policy: AutonomyPolicySnapshot,
    usage: AutonomyUsageSnapshot,
    now_ms: int,
    skill_activation_ref: str | None = None,
) -> AgencyDecision:
    """Select and classify an action; it never executes or grants authority."""

    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("autonomy decision time is invalid")
    policy = _revalidate(policy, AutonomyPolicySnapshot, "autonomy policy")
    usage = _revalidate(usage, AutonomyUsageSnapshot, "autonomy usage")
    viability = _revalidate(viability, ViabilityState, "viability state")
    if not (policy.life_id == usage.life_id == viability.life_id):
        raise ValueError("autonomy state crosses life identities")
    if usage.policy_snapshot_hash != policy.policy_sha256:
        raise ValueError("autonomy usage is not bound to the active policy")
    if not usage.day_start_ms <= now_ms < usage.day_end_ms:
        raise ValueError("autonomy usage does not cover the decision time")

    parsed_candidates: list[ActionCandidate] = []
    for candidate in candidates:
        parsed_candidates.append(_revalidate(candidate, ActionCandidate, "action candidate"))
    if not parsed_candidates:
        raise ValueError("autonomy decision requires candidates")
    ids = tuple(item.candidate_id for item in parsed_candidates)
    if len(ids) != len(set(ids)):
        raise ValueError("action candidate identity is duplicated")
    if any(item.life_id != policy.life_id for item in parsed_candidates):
        raise ValueError("action candidates cross life identities")
    if len({item.episode_id for item in parsed_candidates}) != 1:
        raise ValueError("action candidates cross causal episodes")

    ranked: list[RankedActionCandidate] = []
    for candidate in parsed_candidates:
        impact = impacts_by_action.get(candidate.action_id)
        if impact is None:
            raise ValueError("action candidate lacks machine impact evidence")
        ranked.append(rank_action_candidate(candidate, impact, viability))
    ranked.sort(key=lambda item: (-item.score.utility_lcb_milli, item.candidate.candidate_id))
    candidate_set_sha256 = canonical_sha256(
        {
            "candidate_sha256s": sorted(item.candidate.candidate_sha256 for item in ranked),
            "domain": "tiangong.life.action-candidate-set.v1",
        }
    )

    def hard_scope_reasons(item: RankedActionCandidate) -> tuple[str, ...]:
        reasons: list[str] = []
        candidate = item.candidate
        if candidate.expires_at_ms <= now_ms:
            reasons.append("agency.candidate_expired")
        if candidate.action_id not in policy.allowed_action_ids:
            reasons.append("agency.action_out_of_scope")
        if candidate.workspace_id not in policy.allowed_workspace_ids:
            reasons.append("agency.workspace_out_of_scope")
        return tuple(reasons)

    selectable = [item for item in ranked if not hard_scope_reasons(item)]
    selected = selectable[0] if selectable else ranked[0]
    candidate = selected.candidate
    impact = selected.impact
    score = selected.score
    risk = selected.computed_risk
    reasons = list(hard_scope_reasons(selected))
    outcome = "execute"

    if not policy.effective_at_ms <= now_ms < policy.expires_at_ms:
        outcome, reasons = "reject", ["agency.policy_inactive"]
    elif policy.shutdown_requested:
        outcome, reasons = "reject", ["agency.user_shutdown"]
    elif policy.privacy_lockdown:
        outcome, reasons = "reject", ["agency.privacy_lockdown"]
    elif reasons:
        outcome = "reject"
    elif policy.user_paused:
        outcome, reasons = "wait", ["agency.user_paused"]
    elif not _in_active_window(policy, now_ms):
        outcome, reasons = "wait", ["agency.outside_time_window"]
    elif policy.autonomy_level == "L0":
        outcome, reasons = "wait", ["agency.autonomy_disabled"]
    elif policy.autonomy_level == "L1":
        outcome, reasons = "observe", ["agency.level_observe_only"]
    elif policy.autonomy_level == "L2":
        outcome, reasons = "reflect", ["agency.level_reflect_only"]
    elif risk == "A5":
        outcome, reasons = "reject", ["agency.a5_forbidden"]
    elif policy.autonomy_level == "L5":
        outcome, reasons = "ask_user", ["agency.l5_human_governance_required"]
    else:
        effective_ceiling = min_risk_class(
            policy.risk_ceiling,
            _LEVEL_RISK_CEILING[policy.autonomy_level],
        )
        if _RISK_ORDER.index(risk) > _RISK_ORDER.index(effective_ceiling) or risk == "A4":
            outcome, reasons = "ask_user", ["agency.risk_requires_user"]
        elif usage.execution_count >= policy.daily_execution_budget:
            outcome, reasons = "wait", ["agency.daily_execution_budget_exhausted"]
        elif (
            usage.resource_cost_milli + impact.estimated_resource_cost_milli
            > policy.daily_resource_budget_milli
        ):
            outcome, reasons = "wait", ["agency.daily_resource_budget_exhausted"]
        else:
            action_usage = _usage_for_action(usage, candidate.action_id)
            if action_usage and action_usage.execution_count >= policy.per_action_daily_limit:
                outcome, reasons = "wait", ["agency.action_frequency_exhausted"]
            elif (
                action_usage
                and action_usage.last_executed_at_ms is not None
                and now_ms - action_usage.last_executed_at_ms < policy.minimum_interval_ms
            ):
                outcome, reasons = "wait", ["agency.minimum_interval_active"]
            elif candidate.candidate_kind == "observation":
                outcome, reasons = "observe", ["agency.observation_candidate"]
            elif candidate.candidate_kind == "reflection":
                outcome, reasons = "reflect", ["agency.reflection_candidate"]
            elif selected.decision_confidence_milli < policy.minimum_execute_confidence_milli:
                if (
                    candidate.candidate_kind == "minimal_probe"
                    and policy.allow_minimal_probes
                    and _RISK_ORDER.index(risk) <= _RISK_ORDER.index("A1")
                ):
                    outcome, reasons = "execute", ["agency.low_confidence_minimal_probe"]
                elif candidate.requires_user_preference or _RISK_ORDER.index(risk) >= 2:
                    outcome, reasons = "ask_user", ["agency.low_confidence_requires_user"]
                else:
                    outcome, reasons = "observe", ["agency.low_confidence_observe"]
            elif score.utility_lcb_milli <= 0:
                if candidate.information_gain_milli >= 500:
                    outcome, reasons = "observe", ["agency.information_before_action"]
                else:
                    outcome, reasons = "wait", ["agency.utility_lcb_nonpositive"]
            elif candidate.requires_user_preference:
                outcome, reasons = "ask_user", ["agency.user_preference_required"]
            elif candidate.required_skill_id is not None and skill_activation_ref is None:
                outcome, reasons = "wait", ["agency.skill_activation_missing"]
            else:
                reasons = ["agency.utility_lcb_positive"]

    effective_ceiling = min_risk_class(
        policy.risk_ceiling,
        _LEVEL_RISK_CEILING[policy.autonomy_level],
    )
    use_skill_ref = skill_activation_ref if outcome == "execute" else None
    state_hashes = tuple(
        sorted(
            {
                viability.state_sha256,
                usage.usage_sha256,
                candidate_set_sha256,
                impact.impact_sha256,
            }
        )
    )
    fields = {
        "life_id": policy.life_id,
        "episode_id": candidate.episode_id,
        "candidate_set_sha256": candidate_set_sha256,
        "selected_candidate_id": candidate.candidate_id,
        "action_impact_sha256": impact.impact_sha256,
        "score_breakdown": score,
        "computed_risk": risk,
        "policy_ceiling": effective_ceiling,
        "required_confirmation": risk == "A4",
        "confirmation_grant_ref": None,
        "required_skill_activation": candidate.required_skill_id is not None,
        "skill_activation_ref": use_skill_ref,
        "outcome": outcome,
        "reason_codes": tuple(sorted(set(reasons))),
        "state_revision_hashes": state_hashes,
        "policy_snapshot_hash": policy.policy_sha256,
        "created_at_ms": now_ms,
    }
    decision_id = "agd_" + canonical_sha256(
        {
            "domain": "tiangong.life.agency-decision-id.v1",
            **{
                key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for key, value in fields.items()
            },
        }
    )
    return AgencyDecision(
        decision_id=decision_id,
        **fields,
        decision_sha256="0" * 64,
    ).with_computed_decision_sha256()


__all__ = [
    "RankedActionCandidate",
    "advance_autonomy_usage",
    "compute_action_risk_floor",
    "compute_agency_score",
    "decide_autonomy",
    "max_risk_class",
    "min_risk_class",
    "rank_action_candidate",
]
