from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


@dataclass(frozen=True)
class TurnBudgetDecision:
    exhausted: bool
    reasons: tuple[str, ...]


class EpochBudgetDisposition(str, Enum):
    """Decision for a tool batch inside a long-running execution chain.

    ``CHECKPOINT_CONTINUE`` is deliberately non-terminal: the current epoch is
    full, but the request/run may continue after a durable checkpoint and
    bounded-context rollover. ``GLOBAL_EXHAUSTED`` is terminal for the current
    run unless a higher authority grants a new global budget.
    """

    CONTINUE = "continue"
    CHECKPOINT_CONTINUE = "checkpoint_continue"
    GLOBAL_EXHAUSTED = "global_exhausted"


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
        return self.disposition is EpochBudgetDisposition.GLOBAL_EXHAUSTED


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
    """Mutable counters for one authoritative request/run.

    ``action_rounds`` and ``iteration_count`` remain the global counters for
    backwards compatibility. Epoch counters are a bounded execution window
    inside the *same* request/run; advancing an epoch must never reset global
    authority or make previously committed work disappear.
    """

    action_rounds: int = 0
    iteration_count: int = 0
    repeat_counts: dict[str, int] = field(default_factory=dict)
    epoch_index: int = 0
    epoch_action_rounds: int = 0
    epoch_iteration_count: int = 0

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
        """Legacy single-budget check.

        Existing callers still see exactly the old global-counter behaviour.
        Long-chain callers should migrate to :meth:`decide_schedule` so local
        epoch exhaustion can checkpoint/continue instead of force-stopping.
        """

        return self.action_rounds + max(0, int(requested)) <= max(0, int(max_rounds))

    def decide_schedule(
        self,
        requested: int,
        *,
        max_epoch_rounds: int,
        max_global_rounds: int,
    ) -> EpochBudgetDecision:
        """Evaluate local epoch and global run budgets without reserving work."""

        requested_count = max(0, int(requested))
        epoch_limit = max(0, int(max_epoch_rounds))
        global_limit = max(0, int(max_global_rounds))
        projected_epoch = self.epoch_action_rounds + requested_count
        projected_global = self.action_rounds + requested_count

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
        # Repeat/stuck observations are window-local. Durable step identity and
        # committed facts live outside this transient loop state.
        self.repeat_counts.clear()
        return self.epoch_index

    def project_live(self, run_state: dict[str, object] | None, loop_started_at: float) -> None:
        if not isinstance(run_state, dict):
            return
        live = run_state.get("_live")
        if not isinstance(live, dict):
            live = {}
            run_state["_live"] = live
        # Legacy keys remain for existing UI/tests.
        live["iteration_count"] = self.iteration_count
        live["tool_rounds"] = self.action_rounds
        live["loop_started_at"] = float(loop_started_at)
        # P18-M1 long-chain counters make local rollover explicit.
        live["global_iteration_count"] = self.iteration_count
        live["global_tool_rounds"] = self.action_rounds
        live["epoch_index"] = self.epoch_index
        live["epoch_iteration_count"] = self.epoch_iteration_count
        live["epoch_tool_rounds"] = self.epoch_action_rounds


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
