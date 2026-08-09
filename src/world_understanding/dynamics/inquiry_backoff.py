"""Repeated zero-information-gain InquiryOutcome backoff; no scheduler or authority."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding.inquiry import InquiryOutcome

@dataclass(frozen=True, slots=True)
class InquiryBackoffPolicy:
    zero_gain_threshold_milli: int = 25
    reset_gain_milli: int = 100
    base_backoff_ms: int = 60_000
    max_backoff_ms: int = 3_600_000
    def __post_init__(self) -> None:
        if not 0 <= self.zero_gain_threshold_milli < self.reset_gain_milli <= 1000:
            raise ValueError("INQUIRY_BACKOFF_GAIN_POLICY_INVALID")
        if self.base_backoff_ms <= 0 or self.max_backoff_ms < self.base_backoff_ms:
            raise ValueError("INQUIRY_BACKOFF_TIME_POLICY_INVALID")

@dataclass(frozen=True, slots=True)
class InquiryGainObservation:
    life_id: str
    world_scope_hash: str
    family_key: str
    outcome_sha256: str
    closed_at_ms: int
    information_gain_milli: int
    resolved: bool
    @classmethod
    def from_outcome(cls, outcome: InquiryOutcome, *, family_key: str) -> "InquiryGainObservation":
        if not family_key:
            raise ValueError("INQUIRY_BACKOFF_FAMILY_REQUIRED")
        if not outcome.has_valid_hash() or outcome.empirical_evidence_weight_milli != 0:
            raise ValueError("INQUIRY_OUTCOME_INVALID_FOR_BACKOFF")
        return cls(outcome.scope.life_id, outcome.scope.world_scope_hash, family_key, outcome.outcome_sha256, outcome.closed_at_ms, outcome.information_gain_milli, outcome.resolved)

@dataclass(frozen=True, slots=True)
class InquiryBackoffState:
    life_id: str
    world_scope_hash: str
    family_key: str
    consecutive_zero_gain: int
    backoff_until_ms: int
    backoff_remaining_ms: int
    reason_code: str


def derive_inquiry_backoff(
    observations: tuple[InquiryGainObservation, ...],
    *,
    family_key: str,
    life_id: str,
    world_scope_hash: str,
    now_ms: int,
    policy: InquiryBackoffPolicy | None = None,
) -> InquiryBackoffState:
    policy = policy or InquiryBackoffPolicy()
    if now_ms < 0 or not family_key or not life_id or len(world_scope_hash) != 64:
        raise ValueError("INQUIRY_BACKOFF_INPUT_INVALID")
    family = tuple(item for item in observations if item.family_key == family_key)
    if any(item.life_id != life_id or item.world_scope_hash != world_scope_hash for item in family):
        raise ValueError("INQUIRY_BACKOFF_SCOPE_MISMATCH")
    relevant = sorted(family, key=lambda x: (x.closed_at_ms, x.outcome_sha256))
    if not relevant:
        return InquiryBackoffState(life_id, world_scope_hash, family_key, 0, 0, 0, "INQUIRY_BACKOFF_CLEAR")
    count = 0
    for item in reversed(relevant):
        if item.information_gain_milli >= policy.reset_gain_milli:
            break
        if item.information_gain_milli <= policy.zero_gain_threshold_milli and not item.resolved:
            count += 1
            continue
        break
    if count == 0:
        return InquiryBackoffState(life_id, world_scope_hash, family_key, 0, 0, 0, "INQUIRY_BACKOFF_CLEAR")
    exponent = min(30, count - 1)
    delay = min(policy.max_backoff_ms, policy.base_backoff_ms * (1 << exponent))
    until = relevant[-1].closed_at_ms + delay
    remaining = max(0, until - now_ms)
    return InquiryBackoffState(life_id, world_scope_hash, family_key, count, until, remaining, "INQUIRY_ZERO_GAIN_BACKOFF" if remaining else "INQUIRY_BACKOFF_ELAPSED")

__all__ = ["InquiryBackoffPolicy", "InquiryGainObservation", "InquiryBackoffState", "derive_inquiry_backoff"]
