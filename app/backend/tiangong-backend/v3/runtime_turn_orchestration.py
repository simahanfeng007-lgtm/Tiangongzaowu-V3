from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class TurnBudgetDecision:
    exhausted: bool
    reasons: tuple[str, ...]


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
