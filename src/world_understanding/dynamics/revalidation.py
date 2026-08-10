"""Cost-aware adaptive revalidation over P5/P9 telemetry; never executes validation itself."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from world_understanding.common.budgets import WorkCost
from world_understanding.common.event import RhythmEvent
from world_understanding.common.rhythm import AdmissionDecision, RhythmPlane, WorkItem

RevalidationDisposition = Literal["SKIP", "DEFERRED", "ADMITTED", "COALESCED", "REJECTED", "BACKPRESSURE"]

@dataclass(frozen=True, slots=True)
class RevalidationSignals:
    stale_hazard_milli: int
    impact_milli: int
    need_milli: int
    uncertainty_milli: int
    dirty_milli: int
    validation_cost_milli: int | None
    def __post_init__(self) -> None:
        for value in (self.stale_hazard_milli, self.impact_milli, self.need_milli, self.uncertainty_milli, self.dirty_milli):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
                raise ValueError("REVALIDATION_SIGNAL_INVALID")
        if self.validation_cost_milli is not None and (isinstance(self.validation_cost_milli, bool) or self.validation_cost_milli < 0):
            raise ValueError("REVALIDATION_COST_INVALID")

@dataclass(frozen=True, slots=True)
class RevalidationPolicy:
    priority_floor_milli: int = 25
    epsilon_cost_milli: int = 1
    min_delay_ms: int = 1_000
    max_delay_ms: int = 3_600_000
    def __post_init__(self) -> None:
        if not 0 <= self.priority_floor_milli <= 1000 or self.epsilon_cost_milli <= 0 or self.min_delay_ms < 0 or self.max_delay_ms < self.min_delay_ms:
            raise ValueError("REVALIDATION_POLICY_INVALID")

@dataclass(frozen=True, slots=True)
class RevalidationPlan:
    disposition: RevalidationDisposition
    reason_code: str
    priority_milli: int
    recommended_delay_ms: int
    rhythm_decision: AdmissionDecision | None = None


def revalidation_priority_milli(signals: RevalidationSignals, *, epsilon_cost_milli: int = 1) -> int:
    if signals.validation_cost_milli is None:
        raise ValueError("REVALIDATION_COST_TELEMETRY_UNAVAILABLE")
    product = 1000
    for value in (signals.stale_hazard_milli, signals.impact_milli, signals.need_milli, signals.uncertainty_milli, signals.dirty_milli):
        product = (product * value) // 1000
    return min(1000, (product * 1000) // (signals.validation_cost_milli + epsilon_cost_milli))

class RevalidationPlanner:
    def __init__(self, *, policy: RevalidationPolicy | None = None, rhythm: RhythmPlane | None = None) -> None:
        self.policy = policy or RevalidationPolicy()
        self.rhythm = rhythm

    def plan(self, signals: RevalidationSignals, *, event: RhythmEvent | None = None, work_cost: WorkCost = WorkCost()) -> RevalidationPlan:
        if signals.stale_hazard_milli == 0 and signals.dirty_milli == 0:
            return RevalidationPlan("SKIP", "REVALIDATION_STABLE_WORLD", 0, self.policy.max_delay_ms)
        if signals.validation_cost_milli is None:
            return RevalidationPlan("DEFERRED", "REVALIDATION_COST_TELEMETRY_UNAVAILABLE", 0, self.policy.max_delay_ms)
        priority = revalidation_priority_milli(signals, epsilon_cost_milli=self.policy.epsilon_cost_milli)
        span = self.policy.max_delay_ms - self.policy.min_delay_ms
        delay = self.policy.max_delay_ms - (span * priority) // 1000
        if priority < self.policy.priority_floor_milli:
            return RevalidationPlan("DEFERRED", "REVALIDATION_PRIORITY_FLOOR", priority, delay)
        if self.rhythm is None:
            return RevalidationPlan("ADMITTED", "OK", priority, delay)
        if event is None or event.boundary.queue_class != "REVALIDATION":
            return RevalidationPlan("REJECTED", "REVALIDATION_QUEUE_REQUIRED", priority, delay)
        event_priority = max(event.priority, priority)
        if event_priority != event.priority:
            event = RhythmEvent(event.event_id, event.coalesce_key, event.boundary, event.arrived_at_ms, event.payload_sha256, event_priority)
        decision = self.rhythm.submit(WorkItem(event=event, cost=work_cost, revalidation=True))
        return RevalidationPlan(decision.disposition, decision.reason_code, priority, delay, decision)

__all__ = ["RevalidationSignals", "RevalidationPolicy", "RevalidationPlan", "RevalidationPlanner", "revalidation_priority_milli"]
