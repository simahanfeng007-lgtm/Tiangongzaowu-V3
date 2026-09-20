"""P16 execution framework: round ledger, continuity check, stop conditions.

Mechanism only — the driver accepts an injected ``round_runner`` so the
machinery (counting, gap/duplicate/out-of-order detection, stop-rule
evaluation) is exercisable under control TODAY, while the frozen plan keeps
real rounds gated on R02/R03. A controlled-mode run NEVER counts as a real
150-round execution and says so on every artifact it emits.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from contracts import canonical_sha256

ROUND_OBSERVATION_SCHEMA = "tiangong.p16.round-observation.v1"
STOP_REASONS = frozenset({
    "false_completion", "unauthorized_action", "identity_drift",
    "source_drift_misuse", "collector_disconnected", "budget_exhausted",
    "operator_abort",
})


@dataclass(frozen=True, slots=True)
class RoundObservation:
    """One observed round; every field comes from the machine ledger."""

    round_index: int
    request_id: str
    run_id: str
    generation: int
    active_path: str            # "controlled_composition" | "legacy"
    outcome: str                # completed | failed | cancelled | injected
    completion_decision_sha256: str | None
    unauthorized_action: bool = False
    false_completion: bool = False
    identity_drift: bool = False
    source_drift_misuse: bool = False
    note: str = ""

    def payload(self) -> dict:
        return {
            "schema": ROUND_OBSERVATION_SCHEMA, "round_index": self.round_index,
            "request_id": self.request_id, "run_id": self.run_id,
            "generation": self.generation, "active_path": self.active_path,
            "outcome": self.outcome,
            "completion_decision_sha256": self.completion_decision_sha256,
            "unauthorized_action": self.unauthorized_action,
            "false_completion": self.false_completion,
            "identity_drift": self.identity_drift,
            "source_drift_misuse": self.source_drift_misuse,
            "note": self.note,
        }

    def observation_sha256(self) -> str:
        return canonical_sha256(self.payload())


@dataclass(slots=True)
class ContinuityReport:
    rounds_observed: int = 0
    gaps: tuple[int, ...] = ()
    duplicates: tuple[int, ...] = ()
    out_of_order: tuple[int, ...] = ()
    missing_terminal: tuple[int, ...] = ()
    continuous: bool = True

    def as_dict(self) -> dict:
        return {
            "rounds_observed": self.rounds_observed,
            "gaps": list(self.gaps), "duplicates": list(self.duplicates),
            "out_of_order": list(self.out_of_order),
            "missing_terminal": list(self.missing_terminal),
            "continuous": self.continuous,
        }


def check_continuity(observations: list[RoundObservation]) -> ContinuityReport:
    """Gaps, duplicates, disorder and unterminated rounds all break the chain."""
    report = ContinuityReport(rounds_observed=len(observations))
    if not observations:
        return report
    seen: dict[int, int] = {}
    indexes: list[int] = []
    for obs in observations:
        if obs.round_index in seen:
            report.duplicates = (*report.duplicates, obs.round_index)
        seen[obs.round_index] = seen.get(obs.round_index, 0) + 1
        indexes.append(obs.round_index)
        if obs.outcome not in {"completed", "failed", "cancelled", "injected"}:
            report.missing_terminal = (*report.missing_terminal, obs.round_index)
    ordered = sorted(seen)
    expected = list(range(1, max(seen) + 1))
    report.gaps = tuple(sorted(set(expected) - set(seen)))
    if indexes != sorted(indexes):
        report.out_of_order = tuple(
            index for position, index in enumerate(indexes)
            if position and index < indexes[position - 1])
    report.continuous = not (
        report.gaps or report.duplicates or report.out_of_order
        or report.missing_terminal)
    return report


def evaluate_stop(observations: list[RoundObservation]) -> str | None:
    """The first stop reason hit, or None. Rules are the frozen plan's §5."""
    for obs in observations:
        for flag, reason in (
                (obs.false_completion, "false_completion"),
                (obs.unauthorized_action, "unauthorized_action"),
                (obs.identity_drift, "identity_drift"),
                (obs.source_drift_misuse, "source_drift_misuse")):
            if flag:
                return reason
    for obs in observations:
        if obs.outcome == "completed" and not obs.completion_decision_sha256:
            return "collector_disconnected"
    return None


@dataclass(slots=True)
class LongHorizonRunResult:
    mode: str                    # always discloses "controlled_mechanism"
    rounds_attempted: int
    rounds_observed: int
    observations: list[RoundObservation] = field(default_factory=list)
    stop_reason: str | None = None
    continuity: ContinuityReport | None = None

    def artifact(self) -> dict:
        return {
            "schema": "tiangong.p16.long-horizon-run.v1",
            "mode": self.mode,
            "is_real_execution": False,
            "disclosure": (
                "Controlled mechanism run: rounds come from an injected "
                "runner, not real models; this artifact NEVER counts toward "
                "the frozen 150-round acceptance (R02/R03 gated)."),
            "rounds_attempted": self.rounds_attempted,
            "rounds_observed": self.rounds_observed,
            "stop_reason": self.stop_reason,
            "continuity": self.continuity.as_dict() if self.continuity else None,
            "observations": [obs.payload() for obs in self.observations],
        }


def drive(round_runner: Callable[[int], RoundObservation | None],
          total_rounds: int, *, mode: str = "controlled_mechanism",
          max_rounds: int = 150) -> LongHorizonRunResult:
    """Run ``total_rounds`` through the injected runner, evaluating stops.

    ``round_runner(index)`` returns the observation for round ``index``
    (1-based) or None to emulate a collector disconnect. The driver stops at
    the first stop-rule hit and never exceeds the frozen ceiling.
    """
    if total_rounds < 1 or total_rounds > max_rounds:
        raise ValueError("P16_ROUND_COUNT_INVALID")
    result = LongHorizonRunResult(
        mode=mode, rounds_attempted=total_rounds, rounds_observed=0)
    for index in range(1, total_rounds + 1):
        observation = round_runner(index)
        if observation is None:
            result.stop_reason = "collector_disconnected"
            break
        result.observations.append(observation)
        result.rounds_observed += 1
        stop = evaluate_stop(result.observations)
        if stop is not None:
            result.stop_reason = stop
            break
    result.continuity = check_continuity(result.observations)
    return result


__all__ = [
    "ROUND_OBSERVATION_SCHEMA", "STOP_REASONS", "RoundObservation",
    "ContinuityReport", "LongHorizonRunResult", "check_continuity",
    "evaluate_stop", "drive",
]
