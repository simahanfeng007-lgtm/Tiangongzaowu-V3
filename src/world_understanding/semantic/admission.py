"""P8 semantic admission: fixed-point attention + VOI gated through existing Λ rhythm plane."""
from __future__ import annotations
from dataclasses import dataclass, fields
from world_understanding.common.rhythm import RhythmPlane, WorkItem, AdmissionDecision
from world_understanding.common.event import RhythmEvent
from world_understanding.common.budgets import WorkCost

_FACTOR_NAMES = (
    "novelty_milli",
    "prediction_error_milli",
    "conflict_milli",
    "uncertainty_milli",
    "structural_impact_milli",
    "life_relevance_milli",
)

@dataclass(frozen=True, slots=True)
class SemanticFactors:
    novelty_milli: int = 0
    prediction_error_milli: int = 0
    conflict_milli: int = 0
    uncertainty_milli: int = 0
    structural_impact_milli: int = 0
    life_relevance_milli: int = 0
    def __post_init__(self) -> None:
        for item in fields(self):
            value = int(getattr(self, item.name))
            if value < 0 or value > 1000:
                raise ValueError(f"{item.name} out of range")

@dataclass(frozen=True, slots=True)
class SemanticAdmissionConfig:
    novelty_weight_milli: int = 650
    prediction_error_weight_milli: int = 850
    conflict_weight_milli: int = 900
    uncertainty_weight_milli: int = 750
    structural_impact_weight_milli: int = 800
    life_relevance_weight_milli: int = 700
    attention_threshold_milli: int = 350
    voi_threshold_milli: int = 250
    def __post_init__(self) -> None:
        for item in fields(self):
            value = int(getattr(self, item.name))
            if value < 0 or value > 1000:
                raise ValueError(f"{item.name} out of range")

    def weights(self) -> tuple[int, ...]:
        return (
            self.novelty_weight_milli,
            self.prediction_error_weight_milli,
            self.conflict_weight_milli,
            self.uncertainty_weight_milli,
            self.structural_impact_weight_milli,
            self.life_relevance_weight_milli,
        )

def attention_score_milli(factors: SemanticFactors, config: SemanticAdmissionConfig) -> int:
    """A = 1 - Π(1 - w_j x_j), calculated with deterministic integer milli arithmetic."""
    remaining = 1000
    values = tuple(int(getattr(factors, name)) for name in _FACTOR_NAMES)
    for weight, signal in zip(config.weights(), values):
        contribution = (weight * signal) // 1000
        remaining = (remaining * (1000 - contribution)) // 1000
    return max(0, min(1000, 1000 - remaining))

def voi_score_milli(*, expected_gap_reduction_milli: int, expected_cost_milli: int) -> int:
    """VOI = Expected Gap Reduction / Expected Cost, represented as milli ratio."""
    if expected_gap_reduction_milli < 0 or expected_cost_milli <= 0:
        raise ValueError("invalid semantic VOI inputs")
    # Keep enough headroom for high-VOI cases while remaining bounded/deterministic.
    return min(1_000_000, (int(expected_gap_reduction_milli) * 1000) // int(expected_cost_milli))

@dataclass(frozen=True, slots=True)
class SemanticAdmissionOutcome:
    admitted: bool
    attention_milli: int
    voi_milli: int
    disposition: str
    reason_code: str
    rhythm_decision: AdmissionDecision | None = None

class SemanticAdmissionController:
    __slots__ = ("config", "rhythm")
    def __init__(self, *, config: SemanticAdmissionConfig | None = None, rhythm: RhythmPlane | None = None) -> None:
        self.config = config or SemanticAdmissionConfig()
        self.rhythm = rhythm

    def admit(
        self,
        *,
        factors: SemanticFactors,
        expected_gap_reduction_milli: int,
        expected_cost_milli: int,
        event: RhythmEvent | None = None,
        cost: WorkCost = WorkCost(),
    ) -> SemanticAdmissionOutcome:
        attention = attention_score_milli(factors, self.config)
        voi = voi_score_milli(
            expected_gap_reduction_milli=expected_gap_reduction_milli,
            expected_cost_milli=expected_cost_milli,
        )
        if attention < self.config.attention_threshold_milli:
            return SemanticAdmissionOutcome(False, attention, voi, "REJECTED", "SEMANTIC_ATTENTION_FLOOR")
        if voi < self.config.voi_threshold_milli:
            return SemanticAdmissionOutcome(False, attention, voi, "REJECTED", "SEMANTIC_VOI_FLOOR")
        if self.rhythm is None:
            return SemanticAdmissionOutcome(True, attention, voi, "ADMITTED", "OK")
        if event is None:
            raise ValueError("rhythm-backed semantic admission requires event")
        if event.boundary.queue_class != "SEMANTIC":
            return SemanticAdmissionOutcome(False, attention, voi, "REJECTED", "SEMANTIC_QUEUE_REQUIRED")
        decision = self.rhythm.submit(WorkItem(event=event, cost=cost, semantic=True))
        admitted = decision.disposition in {"ADMITTED", "COALESCED"}
        return SemanticAdmissionOutcome(admitted, attention, voi, decision.disposition, decision.reason_code, decision)

__all__ = [
    "SemanticFactors", "SemanticAdmissionConfig", "SemanticAdmissionOutcome", "SemanticAdmissionController",
    "attention_score_milli", "voi_score_milli",
]
