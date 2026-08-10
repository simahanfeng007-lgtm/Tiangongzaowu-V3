"""Telemetry pre-gate for P8 semantic admission; prevents noisy-world LLM pressure."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from contracts.world_understanding.transform_metrics import TransformQualityProfile
from world_understanding.common.rhythm import QueueTelemetry
from world_understanding.semantic.admission import SemanticAdmissionController, SemanticAdmissionOutcome

@dataclass(frozen=True, slots=True)
class SemanticThrottlePolicy:
    rho_target_milli: int = 800
    high_churn_hazard_milli: int = 500
    challenge_rate_milli: int = 400
    min_quality_samples: int = 20
    def __post_init__(self) -> None:
        if not 1 <= self.rho_target_milli < 1000 or not 0 <= self.high_churn_hazard_milli <= 1000 or not 0 <= self.challenge_rate_milli <= 1000 or self.min_quality_samples < 1:
            raise ValueError("SEMANTIC_THROTTLE_POLICY_INVALID")

@dataclass(frozen=True, slots=True)
class SemanticThrottleSnapshot:
    queue: QueueTelemetry
    stale_hazard_milli: int
    quality: TransformQualityProfile | None = None
    def __post_init__(self) -> None:
        if not 0 <= self.stale_hazard_milli <= 1000:
            raise ValueError("SEMANTIC_THROTTLE_HAZARD_INVALID")

@dataclass(frozen=True, slots=True)
class SemanticThrottleDecision:
    allowed: bool
    reason_code: str


def evaluate_semantic_throttle(snapshot: SemanticThrottleSnapshot, *, policy: SemanticThrottlePolicy | None = None) -> SemanticThrottleDecision:
    policy = policy or SemanticThrottlePolicy()
    queue = snapshot.queue
    if queue.queue_class != "SEMANTIC":
        return SemanticThrottleDecision(False, "SEMANTIC_QUEUE_REQUIRED")
    rho = queue.rho_milli
    if rho is None and queue.service_rate_milli_per_sec > 0:
        rho = (queue.arrival_rate_milli_per_sec * 1000) // queue.service_rate_milli_per_sec
    if rho is None and queue.arrival_rate_milli_per_sec > 0:
        return SemanticThrottleDecision(False, "SEMANTIC_SERVICE_UNAVAILABLE")
    if rho is not None and rho >= policy.rho_target_milli:
        return SemanticThrottleDecision(False, "SEMANTIC_QUEUE_OVERLOAD")
    quality = snapshot.quality
    if (
        quality is not None
        and quality.sample_count >= policy.min_quality_samples
        and snapshot.stale_hazard_milli >= policy.high_churn_hazard_milli
        and quality.downstream_challenge_rate_milli >= policy.challenge_rate_milli
    ):
        return SemanticThrottleDecision(False, "SEMANTIC_NOISY_WORLD")
    return SemanticThrottleDecision(True, "OK")

class TelemetrySemanticAdmissionController:
    """Drop-in P8 admission adapter. Snapshot provider is synchronous/read-only."""
    def __init__(
        self,
        *,
        base: SemanticAdmissionController,
        snapshot_provider: Callable[[], SemanticThrottleSnapshot],
        policy: SemanticThrottlePolicy | None = None,
    ) -> None:
        self.base = base
        self.snapshot_provider = snapshot_provider
        self.policy = policy or SemanticThrottlePolicy()
    @property
    def rhythm(self):
        return self.base.rhythm
    def admit(self, **kwargs) -> SemanticAdmissionOutcome:
        decision = evaluate_semantic_throttle(self.snapshot_provider(), policy=self.policy)
        if not decision.allowed:
            return SemanticAdmissionOutcome(False, 0, 0, "DEFERRED", decision.reason_code)
        return self.base.admit(**kwargs)

__all__ = [
    "SemanticThrottlePolicy", "SemanticThrottleSnapshot", "SemanticThrottleDecision",
    "evaluate_semantic_throttle", "TelemetrySemanticAdmissionController",
]
