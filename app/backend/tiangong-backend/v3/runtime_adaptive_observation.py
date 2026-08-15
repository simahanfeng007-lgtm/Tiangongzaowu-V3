"""Pure adapter from existing runtime observations into P18-M3 control signals.

No probing, persistence, scheduling, Store access, tool execution, or synthetic
telemetry lives here.  Callers must provide values already observed by the
existing authoritative runtime.  Unknown dimensions remain neutral.
"""
from __future__ import annotations

from dataclasses import dataclass

from .runtime_adaptive_control import HorizonControlMetrics, ResourceBudget, ResourceUsage
from .runtime_adaptive_governance import SemanticDriftSignals


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class EpochRealityObservation:
    successful_tools: int = 0
    failed_tools: int = 0
    read_only_successes: int = 0
    mutating_successes: int = 0
    repeat_peak: int = 0
    ambiguous_effects: int = 0
    pending_effects: int = 0
    pending_obligations: int = 0
    completed_obligations: int = 0
    blockers: int = 0
    progress_delta: float = 0.0
    context_pressure: float = 0.0
    checkpoint_commit_latency_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    global_steps: int = 0
    epoch_index: int = 0
    root_goal_match: bool = True
    task_contract_match: bool = True
    authority_reference_match: bool = True
    frontier_contradiction: bool = False
    semantic_handoff_contradiction: bool = False
    critical_fact_verified: bool = True

    @property
    def tool_total(self) -> int:
        return max(0, int(self.successful_tools)) + max(0, int(self.failed_tools))

    @property
    def readonly_stable(self) -> bool:
        return bool(
            self.successful_tools > 0
            and self.failed_tools <= 0
            and self.mutating_successes <= 0
            and self.read_only_successes >= self.successful_tools
            and self.ambiguous_effects <= 0
        )


def horizon_metrics_from_observation(observation: EpochRealityObservation) -> HorizonControlMetrics:
    """Convert only observed runtime facts to the existing adaptive controller."""

    total = max(1, observation.tool_total)
    effect_total = max(1, int(observation.pending_effects) + int(observation.ambiguous_effects))
    frontier_units = (
        max(0, int(observation.pending_obligations))
        + max(0, int(observation.blockers))
        + max(0, int(observation.pending_effects))
        + max(0, int(observation.ambiguous_effects))
    )
    checkpoint_cost = _unit(float(observation.checkpoint_commit_latency_seconds) / 5.0)
    progress_velocity = _unit(float(observation.progress_delta) / max(1.0, float(total)))
    return HorizonControlMetrics(
        tool_failure_rate=_unit(float(observation.failed_tools) / float(total)),
        context_pressure=_unit(observation.context_pressure),
        repeat_risk=_unit(float(observation.repeat_peak) / 3.0),
        ambiguous_effect_rate=_unit(float(observation.ambiguous_effects) / float(effect_total)),
        checkpoint_cost=checkpoint_cost,
        recovery_cost=0.0,
        frontier_complexity=_unit(float(frontier_units) / 20.0),
        progress_velocity=progress_velocity,
        checkpoint_commit_latency=checkpoint_cost,
        readonly=observation.readonly_stable,
    )


def semantic_signals_from_observation(observation: EpochRealityObservation) -> SemanticDriftSignals:
    """Derive deterministic drift signals from existing contract/frontier facts."""

    return SemanticDriftSignals(
        root_goal_similarity=1.0 if observation.root_goal_match else 0.0,
        task_contract_match=bool(observation.task_contract_match),
        active_obligation_consistency=0.0 if observation.frontier_contradiction else 1.0,
        authority_reference_match=bool(observation.authority_reference_match),
        frontier_contradiction=bool(observation.frontier_contradiction),
        semantic_handoff_contradiction=bool(observation.semantic_handoff_contradiction),
        repeated_strategy_collapse=_unit(float(observation.repeat_peak) / 3.0),
        unverified_claim_accumulation=0.0 if observation.critical_fact_verified else 1.0,
    )


def resource_usage_from_observation(observation: EpochRealityObservation) -> ResourceUsage:
    """Populate only resource dimensions that the current runtime actually observes."""

    return ResourceUsage(
        wall_clock_seconds=max(0.0, float(observation.wall_clock_seconds)),
        retries=max(0, int(observation.failed_tools)),
        regenerations=max(0, int(observation.epoch_index)),
    )


def resource_budget_from_runtime(*, wall_clock_budget_seconds: float) -> ResourceBudget:
    """Reuse the existing wall-clock ceiling; other unobserved dimensions stay unlimited."""

    return ResourceBudget(wall_clock_budget=max(0.0, float(wall_clock_budget_seconds)))


__all__ = [
    "EpochRealityObservation",
    "horizon_metrics_from_observation",
    "semantic_signals_from_observation",
    "resource_usage_from_observation",
    "resource_budget_from_runtime",
]
