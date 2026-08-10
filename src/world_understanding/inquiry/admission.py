"""P11 Lambda admission for WorldInquiry candidates. Synchronous and bounded."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from contracts.world_understanding.inquiry import WorldInquiry
from world_understanding.common.budgets import BudgetLedger, WorkCost

AdmissionDisposition = Literal["ADMITTED", "DEFERRED", "REJECTED"]


@dataclass(frozen=True, slots=True)
class InquiryAdmissionSignals:
    user_relevance_milli: int
    novelty_milli: int
    actionability_milli: int
    cost_milli: int
    risk_milli: int
    duplicate_milli: int
    privacy_cost_milli: int
    runtime_pressure_milli: int
    uncertainty_milli: int
    time_remaining_ms: int
    inquiry_count_remaining: int
    privacy_allowed: bool = True
    user_present: bool = False
    active_user_task: bool = False
    backoff_remaining_ms: int = 0
    prior_zero_gain_count: int = 0

    def __post_init__(self) -> None:
        milli = (
            self.user_relevance_milli, self.novelty_milli, self.actionability_milli,
            self.cost_milli, self.risk_milli, self.duplicate_milli,
            self.privacy_cost_milli, self.runtime_pressure_milli, self.uncertainty_milli,
        )
        if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1000 for v in milli):
            raise ValueError("WORLD_INQUIRY_ADMISSION_SIGNAL_INVALID")
        if self.time_remaining_ms < 0 or self.inquiry_count_remaining < 0 or self.backoff_remaining_ms < 0 or self.prior_zero_gain_count < 0:
            raise ValueError("WORLD_INQUIRY_ADMISSION_BUDGET_INVALID")


@dataclass(frozen=True, slots=True)
class InquiryAdmissionConfig:
    admit_threshold: int = 900
    defer_threshold: int = 300
    minimum_time_remaining_ms: int = 100


@dataclass(frozen=True, slots=True)
class InquiryAdmissionDecision:
    disposition: AdmissionDisposition
    reason_code: str
    score: int
    charged: bool = False


class InquiryAdmission:
    """Deterministic admission only; never authority, grant, or execution."""

    def __init__(self, *, config: InquiryAdmissionConfig | None = None, budget: BudgetLedger | None = None) -> None:
        self.config = config or InquiryAdmissionConfig()
        self.budget = budget
        self._admitted_dedup_keys: set[str] = set()

    @staticmethod
    def score(inquiry: WorldInquiry, signals: InquiryAdmissionSignals) -> int:
        if not inquiry.has_valid_hash():
            raise ValueError("WORLD_INQUIRY_HASH_INVALID")
        positive = (
            inquiry.expected_information_gain_milli
            + signals.user_relevance_milli
            + inquiry.impact_milli
            + signals.novelty_milli
            + signals.actionability_milli
        )
        negative = (
            signals.cost_milli
            + signals.risk_milli
            + signals.duplicate_milli
            + signals.privacy_cost_milli
            + signals.runtime_pressure_milli
            + signals.uncertainty_milli
        )
        if signals.user_present and signals.active_user_task:
            negative += 500
        return positive - negative

    def evaluate(
        self,
        inquiry: WorldInquiry,
        signals: InquiryAdmissionSignals,
        *,
        work_cost: WorkCost = WorkCost(),
        charge: bool = True,
    ) -> InquiryAdmissionDecision:
        if inquiry.authorization != "NONE" or inquiry.may_execute or inquiry.may_call_tools or inquiry.empirical_evidence_weight_milli != 0:
            return InquiryAdmissionDecision("REJECTED", "INQUIRY_AUTHORITY_INVALID", -10_000)
        if signals.inquiry_count_remaining <= 0:
            return InquiryAdmissionDecision("DEFERRED", "INQUIRY_COUNT_BUDGET", self.score(inquiry, signals))
        if signals.time_remaining_ms < self.config.minimum_time_remaining_ms:
            return InquiryAdmissionDecision("DEFERRED", "INQUIRY_TIME_BUDGET", self.score(inquiry, signals))
        if not signals.privacy_allowed:
            return InquiryAdmissionDecision("REJECTED", "INQUIRY_PRIVACY_FORBIDDEN", self.score(inquiry, signals))
        if signals.backoff_remaining_ms > 0:
            return InquiryAdmissionDecision("DEFERRED", "INQUIRY_ZERO_GAIN_BACKOFF", self.score(inquiry, signals))
        if signals.user_present and signals.active_user_task and signals.runtime_pressure_milli >= 500:
            return InquiryAdmissionDecision("DEFERRED", "INQUIRY_INTERACTIVE_PRIORITY", self.score(inquiry, signals))
        if inquiry.dedup_key in self._admitted_dedup_keys or signals.duplicate_milli >= 1000:
            return InquiryAdmissionDecision("REJECTED", "INQUIRY_DUPLICATE", self.score(inquiry, signals))
        if self.budget is not None and not self.budget.can_spend(work_cost, interactive=False):
            return InquiryAdmissionDecision("DEFERRED", "INQUIRY_RESOURCE_RESERVE", self.score(inquiry, signals))
        score = self.score(inquiry, signals)
        if score < self.config.defer_threshold:
            return InquiryAdmissionDecision("REJECTED", "INQUIRY_LOW_VALUE", score)
        if score < self.config.admit_threshold:
            return InquiryAdmissionDecision("DEFERRED", "INQUIRY_SCORE_DEFER", score)
        charged = False
        if charge:
            if self.budget is not None:
                self.budget.spend(work_cost, interactive=False)
            self._admitted_dedup_keys.add(inquiry.dedup_key)
            charged = True
        return InquiryAdmissionDecision("ADMITTED", "OK", score, charged)


__all__ = [
    "InquiryAdmission", "InquiryAdmissionConfig", "InquiryAdmissionDecision",
    "InquiryAdmissionSignals", "AdmissionDisposition",
]
