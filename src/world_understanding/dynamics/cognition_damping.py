"""Deterministic anti-oscillation damping over existing Cognition stability reports."""
from __future__ import annotations
from dataclasses import dataclass
from world_understanding.cognition.stability import StabilityReport, highest_eligible_level

_LEVEL_ORDER = {"C0": 0, "C1": 1, "C2": 2, "C3": 3, "C4": 4}

@dataclass(frozen=True, slots=True)
class CognitionDampingPolicy:
    promotion_confirmations: int = 2
    demotion_confirmations: int = 2
    min_dwell_ms: int = 60_000
    def __post_init__(self) -> None:
        if self.promotion_confirmations < 1 or self.demotion_confirmations < 1 or self.min_dwell_ms < 0:
            raise ValueError("COGNITION_DAMPING_POLICY_INVALID")

@dataclass(frozen=True, slots=True)
class TimedStabilityReport:
    life_id: str
    world_scope_hash: str
    report: StabilityReport
    observed_at_ms: int
    def __post_init__(self) -> None:
        if not self.life_id or len(self.world_scope_hash) != 64:
            raise ValueError("COGNITION_DAMPING_SCOPE_INVALID")
        if self.observed_at_ms < 0:
            raise ValueError("COGNITION_DAMPING_TIME_INVALID")

@dataclass(frozen=True, slots=True)
class CognitionDampingDecision:
    current_level: str
    candidate_level: str
    resulting_level: str
    confirmation_count: int
    changed: bool
    reason_code: str


def damp_cognition_level(
    *,
    current_level: str,
    life_id: str,
    world_scope_hash: str,
    recent_reports: tuple[TimedStabilityReport, ...],
    last_changed_at_ms: int,
    now_ms: int,
    policy: CognitionDampingPolicy | None = None,
) -> CognitionDampingDecision:
    policy = policy or CognitionDampingPolicy()
    if current_level not in _LEVEL_ORDER or now_ms < last_changed_at_ms or not life_id or len(world_scope_hash) != 64:
        raise ValueError("COGNITION_DAMPING_INPUT_INVALID")
    if current_level == "C4":
        return CognitionDampingDecision("C4", "C4", "C4", 0, False, "COGNITION_C4_RULES_UNCHANGED")
    if any(item.life_id != life_id or item.world_scope_hash != world_scope_hash for item in recent_reports):
        raise ValueError("COGNITION_DAMPING_SCOPE_MISMATCH")
    ordered = tuple(sorted(recent_reports, key=lambda x: x.observed_at_ms))
    if not ordered:
        return CognitionDampingDecision(current_level, current_level, current_level, 0, False, "COGNITION_NO_NEW_REPORT")
    candidate = highest_eligible_level(ordered[-1].report)
    if candidate == current_level:
        return CognitionDampingDecision(current_level, candidate, current_level, 1, False, "COGNITION_STABLE")
    direction = 1 if _LEVEL_ORDER[candidate] > _LEVEL_ORDER[current_level] else -1
    required = policy.promotion_confirmations if direction > 0 else policy.demotion_confirmations
    count = 0
    for item in reversed(ordered):
        level = highest_eligible_level(item.report)
        if level != candidate:
            break
        count += 1
    if now_ms - last_changed_at_ms < policy.min_dwell_ms:
        return CognitionDampingDecision(current_level, candidate, current_level, count, False, "COGNITION_DWELL_HOLD")
    if count < required:
        return CognitionDampingDecision(current_level, candidate, current_level, count, False, "COGNITION_CONFIRMATION_HOLD")
    return CognitionDampingDecision(current_level, candidate, candidate, count, True, "COGNITION_LEVEL_CHANGED")

__all__ = ["CognitionDampingPolicy", "TimedStabilityReport", "CognitionDampingDecision", "damp_cognition_level"]
