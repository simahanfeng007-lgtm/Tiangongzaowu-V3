from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

try:
    from .runtime_adaptive_control import (
        AdaptiveHorizonDecision,
        AdaptiveHorizonState,
        HorizonControlMetrics,
        ResourceBudget,
        ResourceGovernorDecision,
        ResourceUsage,
        evaluate_resource_governor,
    )
    from .runtime_adaptive_governance import (
        SemanticDriftDecision,
        SemanticDriftSignals,
        evaluate_semantic_drift,
    )
except ImportError:
    # P17 retained a direct spec_from_file_location loader for this pure
    # coordination module. Keep that legacy inspection path valid without
    # changing the production package authority or creating another runtime.
    from v3.runtime_adaptive_control import (
        AdaptiveHorizonDecision,
        AdaptiveHorizonState,
        HorizonControlMetrics,
        ResourceBudget,
        ResourceGovernorDecision,
        ResourceUsage,
        evaluate_resource_governor,
    )
    from v3.runtime_adaptive_governance import (
        SemanticDriftDecision,
        SemanticDriftSignals,
        evaluate_semantic_drift,
    )


@dataclass(frozen=True)
class TurnBudgetDecision:
    exhausted: bool
    reasons: tuple[str, ...]


class EpochBudgetDisposition(str, Enum):
    """Decision for a tool batch inside a long-running execution chain.

    ``CHECKPOINT_CONTINUE`` is deliberately non-terminal: the current epoch is
    full, but the request/run may continue after a durable checkpoint and
    bounded-context rollover. ``GLOBAL_EXHAUSTED`` remains the inherited hard
    run budget. ``CONTROL_BLOCKED`` is an M3 governance stop (for example a
    runaway/resource guard) and must not be confused with an epoch rollover.
    """

    CONTINUE = "continue"
    CHECKPOINT_CONTINUE = "checkpoint_continue"
    GLOBAL_EXHAUSTED = "global_exhausted"
    CONTROL_BLOCKED = "control_blocked"


@dataclass(frozen=True)
class EpochBudgetDecision:
    disposition: EpochBudgetDisposition
    epoch_exhausted: bool
    global_exhausted: bool
    reasons: tuple[str, ...]

    @property
    def can_schedule(self) -> bool:
        return self.disposition is EpochBudgetDisposition.CONTINUE

    @property
    def should_checkpoint_continue(self) -> bool:
        return self.disposition is EpochBudgetDisposition.CHECKPOINT_CONTINUE

    @property
    def terminal(self) -> bool:
        return self.disposition in {
            EpochBudgetDisposition.GLOBAL_EXHAUSTED,
            EpochBudgetDisposition.CONTROL_BLOCKED,
        }


@dataclass(frozen=True)
class PreparedStep:
    name: str
    arguments: dict[str, object]
    action: str
    observations: tuple[str, ...]
    identity_key: str
    reuse_prior_fact: bool = False
    artifact_guard_hits: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParallelCoordination:
    ready: tuple[PreparedStep, ...]
    reused: tuple[PreparedStep, ...]
    guarded: tuple[PreparedStep, ...]


@dataclass
class TurnLoopState:
    """Mutable counters/control state for one authoritative request/run.

    ``action_rounds`` and ``iteration_count`` remain the global counters for
    backwards compatibility. Epoch counters are a bounded execution window
    inside the *same* request/run; advancing an epoch must never reset global
    authority or make previously committed work disappear.

    M3 deliberately upgrades this same state machine rather than introducing a
    second Scheduler. Generic M1 epoch rollover remains semantics-preserving;
    the production checkpoint seam explicitly calls ``activate_adaptive_control``
    only after durable checkpoint persistence succeeds.
    """

    action_rounds: int = 0
    iteration_count: int = 0
    repeat_counts: dict[str, int] = field(default_factory=dict)
    epoch_index: int = 0
    epoch_action_rounds: int = 0
    epoch_iteration_count: int = 0
    adaptive_horizon: AdaptiveHorizonState = field(default_factory=AdaptiveHorizonState)
    adaptive_execution_active: bool = False
    last_semantic_drift: SemanticDriftDecision | None = None
    last_resource_governor: ResourceGovernorDecision | None = None

    def bump_iteration(self) -> int:
        self.iteration_count += 1
        self.epoch_iteration_count += 1
        return self.iteration_count

    def bump_repeat(self, key: str) -> int:
        normalized = str(key or "")
        count = self.repeat_counts.get(normalized, 0) + 1
        self.repeat_counts[normalized] = count
        return count

    def can_schedule(self, requested: int, max_rounds: int) -> bool:
        """Legacy single-budget check with unchanged global-counter behaviour."""

        return self.action_rounds + max(0, int(requested)) <= max(0, int(max_rounds))

    def _decide_schedule_with_limit(
        self,
        requested: int,
        *,
        epoch_limit: int,
        global_limit: int,
    ) -> EpochBudgetDecision:
        requested_count = max(0, int(requested))
        epoch_limit = max(0, int(epoch_limit))
        global_limit = max(0, int(global_limit))
        projected_epoch = self.epoch_action_rounds + requested_count
        projected_global = self.action_rounds + requested_count

        governor = self.last_resource_governor
        if governor is not None and not governor.allowed:
            reasons: list[str] = []
            if governor.runaway_guard:
                reasons.append("[runaway_guard] regeneration cost is rising without frontier progress")
            reasons.extend(
                f"[resource_budget_exhausted] {name} exhausted"
                for name in governor.exhausted_dimensions
            )
            return EpochBudgetDecision(
                disposition=EpochBudgetDisposition.CONTROL_BLOCKED,
                epoch_exhausted=False,
                global_exhausted=False,
                reasons=tuple(reasons or ("[resource_governor_blocked] execution blocked",)),
            )

        drift = self.last_semantic_drift
        if requested_count > 0 and drift is not None and drift.high_risk:
            return EpochBudgetDecision(
                disposition=EpochBudgetDisposition.CHECKPOINT_CONTINUE,
                epoch_exhausted=False,
                global_exhausted=False,
                reasons=("[semantic_drift_audit_replan] checkpoint and reality audit required",),
            )

        if projected_global > global_limit:
            return EpochBudgetDecision(
                disposition=EpochBudgetDisposition.GLOBAL_EXHAUSTED,
                epoch_exhausted=projected_epoch > epoch_limit,
                global_exhausted=True,
                reasons=("[global_tool_budget_exhausted] global tool budget exhausted",),
            )
        if projected_epoch > epoch_limit:
            return EpochBudgetDecision(
                disposition=EpochBudgetDisposition.CHECKPOINT_CONTINUE,
                epoch_exhausted=True,
                global_exhausted=False,
                reasons=("[epoch_tool_budget_exhausted] execution epoch tool budget exhausted",),
            )
        return EpochBudgetDecision(
            disposition=EpochBudgetDisposition.CONTINUE,
            epoch_exhausted=False,
            global_exhausted=False,
            reasons=(),
        )

    def decide_schedule(
        self,
        requested: int,
        *,
        max_epoch_rounds: int,
        max_global_rounds: int,
    ) -> EpochBudgetDecision:
        """Evaluate the one production scheduler, adaptive only when admitted."""

        epoch_limit = max(0, int(max_epoch_rounds))
        if self.adaptive_execution_active:
            epoch_limit = self.current_epoch_round_limit(epoch_limit)
        return self._decide_schedule_with_limit(
            requested,
            epoch_limit=epoch_limit,
            global_limit=max_global_rounds,
        )

    def activate_adaptive_control(self) -> None:
        """Admit M3 control after the authoritative production checkpoint seam."""

        self.adaptive_execution_active = True

    def current_epoch_round_limit(self, configured_max: int) -> int:
        """Return the M3 local horizon bounded by the configured hard ceiling."""

        hard_ceiling = max(1, int(configured_max))
        return min(hard_ceiling, int(self.adaptive_horizon.current_epoch_steps))

    def decide_adaptive_schedule(
        self,
        requested: int,
        *,
        configured_max_epoch_rounds: int,
        max_global_rounds: int,
    ) -> EpochBudgetDecision:
        """Explicit M3 entry used by focused policy/integration tests."""

        return self._decide_schedule_with_limit(
            requested,
            epoch_limit=self.current_epoch_round_limit(configured_max_epoch_rounds),
            global_limit=max_global_rounds,
        )

    def observe_epoch_metrics(self, metrics: HorizonControlMetrics) -> AdaptiveHorizonDecision:
        """Feed one bounded reality-derived Epoch sample into the M3 controller."""

        self.activate_adaptive_control()
        return self.adaptive_horizon.observe_epoch(metrics)

    def observe_semantic_drift(
        self,
        signals: SemanticDriftSignals,
        *,
        base_metrics: HorizonControlMetrics | None = None,
    ) -> SemanticDriftDecision:
        """Attach M3 semantic drift to the same adaptive execution authority."""

        decision = evaluate_semantic_drift(signals)
        self.last_semantic_drift = decision
        self.activate_adaptive_control()
        sample = base_metrics or HorizonControlMetrics()
        self.adaptive_horizon.observe_epoch(
            HorizonControlMetrics(
                **{
                    **sample.__dict__,
                    "semantic_drift_score": max(
                        float(sample.semantic_drift_score),
                        float(decision.score),
                    ),
                }
            )
        )
        return decision

    def clear_semantic_drift_after_replan(self) -> None:
        """Clear only after the authoritative reality-audit/replan completes."""

        self.last_semantic_drift = None

    def observe_resource_governor(
        self,
        *,
        usage: ResourceUsage,
        budget: ResourceBudget,
        progress_delta: float,
        regeneration_streak: int,
    ) -> ResourceGovernorDecision:
        """Bind the pure Resource Governor to this one scheduling state."""

        decision = evaluate_resource_governor(
            usage=usage,
            budget=budget,
            progress_delta=progress_delta,
            regeneration_streak=regeneration_streak,
        )
        self.last_resource_governor = decision
        self.activate_adaptive_control()
        return decision

    def reserve_one(self) -> int:
        self.action_rounds += 1
        self.epoch_action_rounds += 1
        return self.action_rounds

    def record_batch_result(self) -> int:
        self.action_rounds += 1
        self.epoch_action_rounds += 1
        return self.action_rounds

    def begin_next_epoch(self) -> int:
        """Roll the bounded execution window while preserving run totals."""

        self.epoch_index += 1
        self.epoch_action_rounds = 0
        self.epoch_iteration_count = 0
        self.repeat_counts.clear()
        return self.epoch_index

    def project_live(self, run_state: dict[str, object] | None, loop_started_at: float) -> None:
        if not isinstance(run_state, dict):
            return
        live = run_state.get("_live")
        if not isinstance(live, dict):
            live = {}
            run_state["_live"] = live
        live["iteration_count"] = self.iteration_count
        live["tool_rounds"] = self.action_rounds
        live["loop_started_at"] = float(loop_started_at)
        live["global_iteration_count"] = self.iteration_count
        live["global_tool_rounds"] = self.action_rounds
        live["epoch_index"] = self.epoch_index
        live["epoch_iteration_count"] = self.epoch_iteration_count
        live["epoch_tool_rounds"] = self.epoch_action_rounds
        live["adaptive_control_active"] = bool(self.adaptive_execution_active)
        live["adaptive_epoch_tool_limit"] = int(self.adaptive_horizon.current_epoch_steps)
        live["adaptive_ewma_risk"] = round(float(self.adaptive_horizon.ewma_risk), 6)
        live["semantic_drift_score"] = round(
            float(self.last_semantic_drift.score if self.last_semantic_drift else 0.0),
            6,
        )
        live["resource_governor_allowed"] = bool(
            self.last_resource_governor.allowed if self.last_resource_governor else True
        )


def evaluate_turn_budget(
    *,
    iteration_count: int,
    elapsed_seconds: float,
    max_iterations: int,
    max_wall_clock_seconds: float,
) -> TurnBudgetDecision:
    reasons: list[str] = []
    if int(iteration_count) > int(max_iterations):
        reasons.append("[loop_budget_exhausted] loop iteration budget exhausted")
    if float(elapsed_seconds) > float(max_wall_clock_seconds):
        reasons.append("[loop_budget_exhausted] wall-clock budget exhausted")
    return TurnBudgetDecision(exhausted=bool(reasons), reasons=tuple(reasons))


def coordinate_parallel_steps(candidates: Iterable[PreparedStep]) -> ParallelCoordination:
    seen: set[str] = set()
    ready: list[PreparedStep] = []
    reused: list[PreparedStep] = []
    guarded: list[PreparedStep] = []
    for candidate in candidates:
        key = str(candidate.identity_key or "")
        if key in seen:
            continue
        seen.add(key)
        if candidate.reuse_prior_fact:
            reused.append(candidate)
            continue
        if candidate.artifact_guard_hits:
            guarded.append(candidate)
            continue
        ready.append(candidate)
    return ParallelCoordination(
        ready=tuple(ready),
        reused=tuple(reused),
        guarded=tuple(guarded),
    )
