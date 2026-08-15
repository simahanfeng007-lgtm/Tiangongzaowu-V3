"""Pure adaptive-control policy for P18-M3.

This module owns no persistence, Runtime, Scheduler, Gateway, Authority, Fact,
Effect, or tool dispatch.  It only evaluates bounded execution-control signals
for the existing authoritative run.  Production wiring remains in the existing
TurnLoopState/Zongdiaodu/Gateway path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _non_negative(value: float) -> float:
    return max(0.0, float(value))


@dataclass(frozen=True)
class HorizonControlMetrics:
    """Bounded risk/progress sample for one authoritative Run."""

    mtbf_model_steps: float = 64.0
    mtbf_tool_steps: float = 64.0
    provider_timeout_rate: float = 0.0
    tool_failure_rate: float = 0.0
    context_pressure: float = 0.0
    semantic_drift_score: float = 0.0
    repeat_risk: float = 0.0
    ambiguous_effect_rate: float = 0.0
    checkpoint_cost: float = 1.0
    recovery_cost: float = 1.0
    frontier_complexity: float = 0.0
    progress_velocity: float = 1.0
    cache_hit_rate: float = 1.0
    latency_pressure: float = 0.0
    ledger_write_latency_pressure: float = 0.0
    checkpoint_commit_latency_pressure: float = 0.0
    readonly_fraction: float = 0.0

    def normalized(self) -> "HorizonControlMetrics":
        return HorizonControlMetrics(
            mtbf_model_steps=max(1.0, float(self.mtbf_model_steps)),
            mtbf_tool_steps=max(1.0, float(self.mtbf_tool_steps)),
            provider_timeout_rate=_unit(self.provider_timeout_rate),
            tool_failure_rate=_unit(self.tool_failure_rate),
            context_pressure=_unit(self.context_pressure),
            semantic_drift_score=_unit(self.semantic_drift_score),
            repeat_risk=_unit(self.repeat_risk),
            ambiguous_effect_rate=_unit(self.ambiguous_effect_rate),
            checkpoint_cost=max(0.001, float(self.checkpoint_cost)),
            recovery_cost=max(0.001, float(self.recovery_cost)),
            frontier_complexity=_unit(self.frontier_complexity),
            progress_velocity=_unit(self.progress_velocity),
            cache_hit_rate=_unit(self.cache_hit_rate),
            latency_pressure=_unit(self.latency_pressure),
            ledger_write_latency_pressure=_unit(self.ledger_write_latency_pressure),
            checkpoint_commit_latency_pressure=_unit(self.checkpoint_commit_latency_pressure),
            readonly_fraction=_unit(self.readonly_fraction),
        )

    def instantaneous_risk(self) -> float:
        sample = self.normalized()
        reliability_risk = min(
            1.0,
            0.5 * (1.0 / sample.mtbf_model_steps) * 16.0
            + 0.5 * (1.0 / sample.mtbf_tool_steps) * 16.0,
        )
        cost_pressure = _unit(
            0.55 * (sample.recovery_cost / (sample.recovery_cost + sample.checkpoint_cost))
            + 0.25 * sample.ledger_write_latency_pressure
            + 0.20 * sample.checkpoint_commit_latency_pressure
        )
        lack_of_progress = 1.0 - sample.progress_velocity
        cache_pressure = 1.0 - sample.cache_hit_rate
        risk = (
            0.10 * reliability_risk
            + 0.09 * sample.provider_timeout_rate
            + 0.11 * sample.tool_failure_rate
            + 0.16 * sample.context_pressure
            + 0.16 * sample.semantic_drift_score
            + 0.10 * sample.repeat_risk
            + 0.10 * sample.ambiguous_effect_rate
            + 0.07 * sample.frontier_complexity
            + 0.05 * lack_of_progress
            + 0.02 * cache_pressure
            + 0.02 * sample.latency_pressure
            + 0.02 * cost_pressure
        )
        readonly_discount = 0.10 * sample.readonly_fraction * (
            1.0
            - max(
                sample.semantic_drift_score,
                sample.context_pressure,
                sample.ambiguous_effect_rate,
            )
        )
        return _unit(risk - readonly_discount)


@dataclass(frozen=True)
class AdaptiveHorizonDecision:
    epoch_steps: int
    ewma_risk: float
    action: str
    reasons: tuple[str, ...]
    changed: bool


@dataclass
class AdaptiveHorizonState:
    """Adaptive Epoch controller with EWMA, hysteresis and dwell time."""

    min_epoch_steps: int = 16
    default_epoch_steps: int = 48
    max_epoch_steps: int = 96
    ewma_lambda: float = 0.8
    high_risk_threshold: float = 0.62
    low_risk_threshold: float = 0.24
    high_risk_epochs_required: int = 2
    low_risk_epochs_required: int = 3
    min_dwell_epochs: int = 2
    current_epoch_steps: int = 48
    ewma_risk: float = 0.0
    high_risk_streak: int = 0
    low_risk_streak: int = 0
    epochs_since_change: int = 0
    samples_observed: int = 0

    def __post_init__(self) -> None:
        self.min_epoch_steps = max(1, int(self.min_epoch_steps))
        self.max_epoch_steps = max(self.min_epoch_steps, int(self.max_epoch_steps))
        self.default_epoch_steps = min(
            self.max_epoch_steps,
            max(self.min_epoch_steps, int(self.default_epoch_steps)),
        )
        self.current_epoch_steps = min(
            self.max_epoch_steps,
            max(self.min_epoch_steps, int(self.current_epoch_steps)),
        )
        if not 0.0 <= float(self.ewma_lambda) < 1.0:
            raise ValueError("ewma_lambda must be in [0, 1)")
        if not 0.0 <= float(self.low_risk_threshold) < float(self.high_risk_threshold) <= 1.0:
            raise ValueError("risk thresholds are invalid")
        self.high_risk_epochs_required = max(1, int(self.high_risk_epochs_required))
        self.low_risk_epochs_required = max(1, int(self.low_risk_epochs_required))
        self.min_dwell_epochs = max(0, int(self.min_dwell_epochs))

    def _heuristic_target(self, metrics: HorizonControlMetrics, ewma_risk: float) -> int:
        sample = metrics.normalized()
        effective_mtbf = max(1.0, min(sample.mtbf_model_steps, sample.mtbf_tool_steps))
        # HPC checkpoint theory is only a baseline. Semantic/context/effect
        # risk, progress, and recovery/checkpoint cost then shape the horizon.
        hpc = sqrt(2.0 * effective_mtbf * sample.checkpoint_cost)
        base = max(float(self.default_epoch_steps), hpc)
        risk_factor = max(0.34, 1.0 - 0.72 * ewma_risk)
        progress_factor = 0.80 + 0.35 * sample.progress_velocity
        context_factor = max(0.45, 1.0 - 0.55 * sample.context_pressure)
        effect_factor = max(0.45, 1.0 - 0.60 * sample.ambiguous_effect_rate)
        recovery_factor = max(
            0.72,
            min(1.20, 1.0 + 0.08 * (sample.recovery_cost - sample.checkpoint_cost)),
        )
        readonly_factor = 1.0 + 0.20 * sample.readonly_fraction * (1.0 - ewma_risk)
        target = round(
            base
            * risk_factor
            * progress_factor
            * context_factor
            * effect_factor
            * recovery_factor
            * readonly_factor
        )
        return min(self.max_epoch_steps, max(self.min_epoch_steps, int(target)))

    def observe_epoch(self, metrics: HorizonControlMetrics) -> AdaptiveHorizonDecision:
        instantaneous = metrics.instantaneous_risk()
        if self.samples_observed == 0:
            # Bootstrap from reality rather than biasing the first epochs toward
            # zero risk and delaying high-risk contraction.
            self.ewma_risk = instantaneous
        else:
            self.ewma_risk = _unit(
                float(self.ewma_lambda) * self.ewma_risk
                + (1.0 - float(self.ewma_lambda)) * instantaneous
            )
        self.samples_observed += 1
        self.epochs_since_change += 1
        reasons: list[str] = []

        if self.ewma_risk >= self.high_risk_threshold:
            self.high_risk_streak += 1
            self.low_risk_streak = 0
        elif self.ewma_risk <= self.low_risk_threshold:
            self.low_risk_streak += 1
            self.high_risk_streak = 0
        else:
            self.high_risk_streak = 0
            self.low_risk_streak = 0

        target = self._heuristic_target(metrics, self.ewma_risk)
        prior = self.current_epoch_steps
        can_change = self.epochs_since_change >= self.min_dwell_epochs
        action = "hold"

        if (
            can_change
            and self.high_risk_streak >= self.high_risk_epochs_required
            and target < prior
        ):
            # Bound one adjustment so a single bad episode cannot collapse
            # 48/60 straight to 16.
            step = max(4, prior // 4)
            self.current_epoch_steps = max(self.min_epoch_steps, max(target, prior - step))
            self.high_risk_streak = 0
            self.epochs_since_change = 0
            action = "shrink"
            reasons.append("sustained_high_risk")
        elif (
            can_change
            and self.low_risk_streak >= self.low_risk_epochs_required
            and target > prior
        ):
            step = max(4, prior // 6)
            self.current_epoch_steps = min(self.max_epoch_steps, min(target, prior + step))
            self.low_risk_streak = 0
            self.epochs_since_change = 0
            action = "grow"
            reasons.append("sustained_low_risk")

        sample = metrics.normalized()
        if sample.context_pressure >= 0.90:
            reasons.append("context_pressure_regeneration")
        if sample.semantic_drift_score >= 0.80:
            reasons.append("semantic_drift_audit")
        if sample.ambiguous_effect_rate >= 0.50:
            reasons.append("ambiguous_effect_reconcile")

        return AdaptiveHorizonDecision(
            epoch_steps=self.current_epoch_steps,
            ewma_risk=self.ewma_risk,
            action=action,
            reasons=tuple(reasons),
            changed=self.current_epoch_steps != prior,
        )

    def should_regenerate_early(self, metrics: HorizonControlMetrics) -> bool:
        sample = metrics.normalized()
        return (
            sample.context_pressure >= 0.90
            or sample.semantic_drift_score >= 0.80
            or sample.ambiguous_effect_rate >= 0.50
        )


@dataclass(frozen=True)
class FrontierProgressSample:
    completed_obligations: int = 0
    active_obligation_revision: str = ""
    artifact_revision_head: str = ""
    verified_fact_head: str = ""
    blocker_signature: str = ""
    failure_signature: str = ""
    strategy_id: str = ""
    pending_effects: int = 0
    ambiguous_effects: int = 0

    def frontier_signature(self) -> tuple[object, ...]:
        return (
            max(0, int(self.completed_obligations)),
            str(self.active_obligation_revision or ""),
            str(self.artifact_revision_head or ""),
            str(self.verified_fact_head or ""),
            str(self.blocker_signature or ""),
            max(0, int(self.pending_effects)),
            max(0, int(self.ambiguous_effects)),
        )


@dataclass(frozen=True)
class FrontierProgressDecision:
    progressed: bool
    strategy_exhausted: bool
    fatal_exhaustion: bool
    unchanged_streak: int
    failed_strategy_count: int


@dataclass
class FrontierProgressMonitor:
    """Detect strategy exhaustion from Frontier semantics, not tool repetition."""

    exhaustion_epochs: int = 3
    fatal_strategy_count: int = 3
    unchanged_streak: int = 0
    failed_strategy_ids: set[str] = field(default_factory=set)
    _previous: FrontierProgressSample | None = None

    def observe(self, sample: FrontierProgressSample) -> FrontierProgressDecision:
        if self._previous is None:
            self._previous = sample
            return FrontierProgressDecision(True, False, False, 0, len(self.failed_strategy_ids))

        previous = self._previous
        frontier_changed = sample.frontier_signature() != previous.frontier_signature()
        strategy_changed = str(sample.strategy_id or "") != str(previous.strategy_id or "")
        failure_changed = str(sample.failure_signature or "") != str(previous.failure_signature or "")
        progressed = frontier_changed or strategy_changed or failure_changed

        if not frontier_changed and not strategy_changed and not failure_changed:
            self.unchanged_streak += 1
        else:
            self.unchanged_streak = 0

        strategy_exhausted = self.unchanged_streak >= max(1, int(self.exhaustion_epochs))
        if strategy_exhausted and sample.strategy_id:
            self.failed_strategy_ids.add(str(sample.strategy_id))
            self.unchanged_streak = 0
        fatal = (
            strategy_exhausted
            and len(self.failed_strategy_ids) >= max(1, int(self.fatal_strategy_count))
        )
        self._previous = sample
        return FrontierProgressDecision(
            progressed=progressed,
            strategy_exhausted=strategy_exhausted,
            fatal_exhaustion=fatal,
            unchanged_streak=self.unchanged_streak,
            failed_strategy_count=len(self.failed_strategy_ids),
        )


@dataclass(frozen=True)
class ExecutionPotential:
    unfinished_obligations: int
    blockers: int
    ambiguous_effects: int
    context_pressure: float
    repeat_risk: float
    unresolved_failures: int

    def value(self) -> float:
        return (
            1.0 * max(0, int(self.unfinished_obligations))
            + 1.4 * max(0, int(self.blockers))
            + 2.0 * max(0, int(self.ambiguous_effects))
            + 2.0 * _unit(self.context_pressure)
            + 1.2 * _unit(self.repeat_risk)
            + 1.6 * max(0, int(self.unresolved_failures))
        )


@dataclass(frozen=True)
class ResourceUsage:
    tokens: float = 0.0
    api_cost: float = 0.0
    tool_seconds: float = 0.0
    wall_clock_seconds: float = 0.0
    retries: float = 0.0
    regenerations: float = 0.0
    storage_bytes: float = 0.0


@dataclass(frozen=True)
class ResourceBudget:
    token_budget: float = float("inf")
    api_cost_budget: float = float("inf")
    tool_time_budget: float = float("inf")
    wall_clock_budget: float = float("inf")
    retry_budget: float = float("inf")
    regeneration_budget: float = float("inf")
    storage_budget: float = float("inf")


@dataclass(frozen=True)
class ResourceGovernorDecision:
    allowed: bool
    runaway_guard: bool
    exhausted_dimensions: tuple[str, ...]
    progress_per_cost: float


def evaluate_resource_governor(
    *,
    usage: ResourceUsage,
    budget: ResourceBudget,
    progress_delta: float,
    regeneration_streak: int,
    min_progress_per_cost: float = 0.01,
    runaway_regeneration_streak: int = 4,
) -> ResourceGovernorDecision:
    dimensions = (
        ("token_budget", _non_negative(usage.tokens), _non_negative(budget.token_budget)),
        ("api_cost_budget", _non_negative(usage.api_cost), _non_negative(budget.api_cost_budget)),
        ("tool_time_budget", _non_negative(usage.tool_seconds), _non_negative(budget.tool_time_budget)),
        ("wall_clock_budget", _non_negative(usage.wall_clock_seconds), _non_negative(budget.wall_clock_budget)),
        ("retry_budget", _non_negative(usage.retries), _non_negative(budget.retry_budget)),
        ("regeneration_budget", _non_negative(usage.regenerations), _non_negative(budget.regeneration_budget)),
        ("storage_budget", _non_negative(usage.storage_bytes), _non_negative(budget.storage_budget)),
    )
    exhausted = tuple(name for name, used, limit in dimensions if used > limit)
    finite_costs = [
        used / limit
        for _name, used, limit in dimensions
        if limit not in {0.0, float("inf")} and limit > 0.0
    ]
    normalized_cost = sum(finite_costs) / len(finite_costs) if finite_costs else 0.0
    progress_per_cost = max(0.0, float(progress_delta)) / max(0.001, normalized_cost)
    runaway = (
        int(regeneration_streak) >= max(1, int(runaway_regeneration_streak))
        and progress_per_cost < max(0.0, float(min_progress_per_cost))
    )
    return ResourceGovernorDecision(
        allowed=not exhausted and not runaway,
        runaway_guard=runaway,
        exhausted_dimensions=exhausted,
        progress_per_cost=progress_per_cost,
    )


__all__ = [
    "AdaptiveHorizonDecision",
    "AdaptiveHorizonState",
    "ExecutionPotential",
    "FrontierProgressDecision",
    "FrontierProgressMonitor",
    "FrontierProgressSample",
    "HorizonControlMetrics",
    "ResourceBudget",
    "ResourceGovernorDecision",
    "ResourceUsage",
    "evaluate_resource_governor",
]
