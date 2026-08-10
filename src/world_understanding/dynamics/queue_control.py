"""Telemetry-derived queue rho/capacity control over the existing P5 RhythmPlane."""
from __future__ import annotations
from dataclasses import dataclass
from world_understanding.common.rhythm import QueueTelemetry, RhythmPlane, adaptive_debounce_ms

@dataclass(frozen=True, slots=True)
class QueueControlPolicy:
    rho_target_milli: int = 800
    max_debounce_ms: int = 30_000
    min_service_rate_milli_per_sec: int = 1
    def __post_init__(self) -> None:
        if not 1 <= self.rho_target_milli < 1000 or self.max_debounce_ms < 0 or self.min_service_rate_milli_per_sec <= 0:
            raise ValueError("QUEUE_CONTROL_POLICY_INVALID")

@dataclass(frozen=True, slots=True)
class QueueControlPlan:
    queue_class: str
    telemetry_available: bool
    rho_milli: int | None
    overload: bool
    preserve_interactive: bool
    recommended_debounce_ms: int
    reason_code: str


def derive_queue_control(
    telemetry: QueueTelemetry,
    *,
    semantic_ratio_milli: int,
    policy: QueueControlPolicy | None = None,
) -> QueueControlPlan:
    policy = policy or QueueControlPolicy()
    if not 0 <= semantic_ratio_milli <= 1000:
        raise ValueError("QUEUE_SEMANTIC_RATIO_INVALID")
    if telemetry.arrival_rate_milli_per_sec < 0 or telemetry.service_rate_milli_per_sec < 0 or telemetry.queue_depth < 0:
        raise ValueError("QUEUE_TELEMETRY_INVALID")
    service = telemetry.service_rate_milli_per_sec
    if service < policy.min_service_rate_milli_per_sec:
        if telemetry.arrival_rate_milli_per_sec <= 0:
            return QueueControlPlan(telemetry.queue_class, False, None, False, telemetry.queue_class != "INTERACTIVE", 0, "QUEUE_TELEMETRY_INSUFFICIENT")
        return QueueControlPlan(telemetry.queue_class, False, None, True, telemetry.queue_class != "INTERACTIVE", policy.max_debounce_ms, "QUEUE_SERVICE_UNAVAILABLE")
    rho = telemetry.rho_milli
    if rho is None:
        rho = (telemetry.arrival_rate_milli_per_sec * 1000) // service
    raw = adaptive_debounce_ms(
        arrival_rate_milli_per_sec=telemetry.arrival_rate_milli_per_sec,
        service_rate_milli_per_sec=service,
        semantic_ratio_milli=semantic_ratio_milli,
        rho_target_milli=policy.rho_target_milli,
    )
    debounce = min(policy.max_debounce_ms, max(0, int(raw)))
    overload = rho >= policy.rho_target_milli
    reason = "QUEUE_OVERLOAD" if overload else "QUEUE_WITHIN_CAPACITY"
    return QueueControlPlan(telemetry.queue_class, True, rho, overload, telemetry.queue_class != "INTERACTIVE", debounce, reason)


def apply_queue_control(rhythm: RhythmPlane, plan: QueueControlPlan) -> int:
    if plan.queue_class == "INTERACTIVE":
        return rhythm.debounce_ms
    rhythm.set_debounce_ms(plan.recommended_debounce_ms, queue_class=plan.queue_class)
    return rhythm.debounce_ms_for(plan.queue_class)

__all__ = ["QueueControlPolicy", "QueueControlPlan", "derive_queue_control", "apply_queue_control"]
