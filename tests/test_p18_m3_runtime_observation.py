"""P18-M3 production observation adapter regressions."""
from __future__ import annotations

from v3.runtime_adaptive_observation import (
    EpochRealityObservation,
    horizon_metrics_from_observation,
    resource_budget_from_runtime,
    resource_usage_from_observation,
    semantic_signals_from_observation,
)


def test_stable_readonly_epoch_projects_low_risk_growth_signals() -> None:
    observation = EpochRealityObservation(
        successful_tools=12,
        failed_tools=0,
        read_only_successes=12,
        mutating_successes=0,
        progress_delta=6,
        completed_obligations=4,
    )
    metrics = horizon_metrics_from_observation(observation)
    assert metrics.readonly_fraction == 1.0
    assert metrics.tool_failure_rate == 0.0
    assert metrics.progress_velocity > 0.0
    assert metrics.ambiguous_effect_rate == 0.0


def test_failed_ambiguous_epoch_projects_high_risk_signals() -> None:
    observation = EpochRealityObservation(
        successful_tools=1,
        failed_tools=5,
        read_only_successes=0,
        mutating_successes=1,
        repeat_peak=3,
        ambiguous_effects=2,
        pending_effects=1,
        pending_obligations=5,
        blockers=3,
        progress_delta=0,
    )
    metrics = horizon_metrics_from_observation(observation)
    assert metrics.tool_failure_rate > 0.8
    assert metrics.repeat_risk == 1.0
    assert metrics.ambiguous_effect_rate > 0.6
    assert metrics.frontier_complexity > 0.0
    assert metrics.readonly_fraction == 0.0


def test_checkpoint_latency_uses_existing_pressure_dimension() -> None:
    metrics = horizon_metrics_from_observation(
        EpochRealityObservation(checkpoint_commit_latency_seconds=2.5)
    )
    assert metrics.checkpoint_cost == 2.5
    assert metrics.checkpoint_commit_latency_pressure == 0.5


def test_contract_and_frontier_mismatch_projects_semantic_drift() -> None:
    signals = semantic_signals_from_observation(
        EpochRealityObservation(
            root_goal_match=False,
            task_contract_match=False,
            authority_reference_match=False,
            frontier_contradiction=True,
            semantic_handoff_contradiction=True,
            repeat_peak=3,
            critical_fact_verified=False,
        )
    )
    assert signals.root_goal_similarity == 0.0
    assert signals.task_contract_match is False
    assert signals.authority_reference_match is False
    assert signals.frontier_contradiction is True
    assert signals.semantic_handoff_contradiction is True
    assert signals.repeated_strategy_collapse == 1.0
    assert signals.unverified_claim_accumulation == 1.0


def test_resource_projection_uses_only_observed_runtime_dimensions() -> None:
    observation = EpochRealityObservation(
        wall_clock_seconds=123.5,
        failed_tools=2,
        epoch_index=4,
    )
    usage = resource_usage_from_observation(observation)
    budget = resource_budget_from_runtime(wall_clock_budget_seconds=600.0)
    assert usage.wall_clock_seconds == 123.5
    assert usage.retries == 2
    assert usage.regenerations == 4
    assert usage.tokens == 0
    assert usage.api_cost == 0.0
    assert budget.wall_clock_budget == 600.0
