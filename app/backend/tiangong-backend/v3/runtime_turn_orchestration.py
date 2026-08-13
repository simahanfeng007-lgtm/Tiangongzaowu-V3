from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class TurnBudgetDecision:
    exhausted: bool
    reasons: tuple[str, ...]


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
