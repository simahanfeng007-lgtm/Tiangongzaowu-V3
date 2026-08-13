from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class TurnBudgetDecision:
    exhausted: bool
    reasons: tuple[str, ...]


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
    action_rounds: int = 0
    iteration_count: int = 0
    repeat_counts: dict[str, int] = field(default_factory=dict)

    def bump_iteration(self) -> int:
        self.iteration_count += 1
        return self.iteration_count

    def bump_repeat(self, key: str) -> int:
        normalized = str(key or "")
        count = self.repeat_counts.get(normalized, 0) + 1
        self.repeat_counts[normalized] = count
        return count

    def can_schedule(self, requested: int, max_rounds: int) -> bool:
        return self.action_rounds + max(0, int(requested)) <= max(0, int(max_rounds))

    def reserve_one(self) -> int:
        self.action_rounds += 1
        return self.action_rounds

    def record_batch_result(self) -> int:
        self.action_rounds += 1
        return self.action_rounds

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
