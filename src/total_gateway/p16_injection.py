"""P16-C interruption injector: the eight frozen injection points.

A minimal, side-effect-free harness the frozen long-horizon plan named for
its boundary-event matrix: BEFORE_PLANNING, MODEL_RESPONSE, REGISTRATION,
POST_TICKET_PRE_EFFECT, POST_SIDE_EFFECT_START, AROUND_FACT_WRITE,
DURING_VERIFICATION, AROUND_DELIVERY. The harness schedules an action at a
point for chosen rounds, records what it actually did, and never invents
authority — actions are raise/cancel/none (the plan's interruption verbs),
applied to components the CALLER supplies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from contracts import canonical_sha256

INJECTION_LOG_SCHEMA = "tiangong.p16.injection-log.v1"

InjectionPoint = str  # one of the frozen eight
FROZEN_POINTS: tuple[str, ...] = (
    "BEFORE_PLANNING", "MODEL_RESPONSE", "REGISTRATION",
    "POST_TICKET_PRE_EFFECT", "POST_SIDE_EFFECT_START",
    "AROUND_FACT_WRITE", "DURING_VERIFICATION", "AROUND_DELIVERY",
)
ACTIONS = frozenset({"raise", "cancel", "none"})


class InjectionError(RuntimeError):
    """Raised by the 'raise' action at the injected point."""


@dataclass(frozen=True, slots=True)
class InjectionRule:
    point: InjectionPoint
    action: str            # raise | cancel | none
    rounds: tuple[int, ...]  # 1-based round indexes; () = every round

    def __post_init__(self) -> None:
        if self.point not in FROZEN_POINTS:
            raise ValueError("P16_INJECTION_POINT_UNKNOWN")
        if self.action not in ACTIONS:
            raise ValueError("P16_INJECTION_ACTION_INVALID")
        if any(index < 1 for index in self.rounds):
            raise ValueError("P16_INJECTION_ROUND_INVALID")

    def applies(self, round_index: int) -> bool:
        return not self.rounds or round_index in self.rounds


@dataclass(frozen=True, slots=True)
class InjectionRecord:
    round_index: int
    point: InjectionPoint
    action: str

    def payload(self) -> dict:
        return {
            "schema": INJECTION_LOG_SCHEMA, "round_index": self.round_index,
            "point": self.point, "action": self.action}

    def record_sha256(self) -> str:
        return canonical_sha256(self.payload())


@dataclass(slots=True)
class InjectionHarness:
    """Scheduled interruptions with a full append-only record."""

    rules: tuple[InjectionRule, ...] = ()
    records: list[InjectionRecord] = field(default_factory=list)

    def at(self, point: InjectionPoint, round_index: int) -> str:
        """Apply the FIRST matching rule; returns the effective action."""
        for rule in self.rules:
            if rule.point == point and rule.applies(round_index):
                self.records.append(InjectionRecord(
                    round_index=round_index, point=point, action=rule.action))
                if rule.action == "raise":
                    raise InjectionError(
                        f"P16_INJECTION_{point}_ROUND_{round_index}")
                return rule.action
        return "none"

    def cancelled(self, point: InjectionPoint, round_index: int) -> bool:
        return self.at(point, round_index) == "cancel"

    def log_payload(self) -> list[dict]:
        return [record.payload() for record in self.records]


def wrap_injection(point: InjectionPoint, harness: InjectionHarness,
                   round_index: int,
                   operation: Callable[[], object]) -> object:
    """Run ``operation`` under the harness at one frozen point.

    'raise' interrupts the operation before it starts; 'cancel' skips it
    and returns None; 'none' runs it unchanged.
    """
    action = harness.at(point, round_index)
    if action == "cancel":
        return None
    return operation()


__all__ = [
    "ACTIONS", "FROZEN_POINTS", "InjectionError", "InjectionHarness",
    "InjectionRecord", "InjectionRule", "wrap_injection",
]
