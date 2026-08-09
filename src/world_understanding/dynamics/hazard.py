"""Telemetry-only change hazard mathematics for P12 L7 dynamics."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

@dataclass(frozen=True, slots=True)
class ChangeHazardWindow:
    life_id: str
    world_scope_hash: str
    change_count: int
    window_start_ms: int
    window_end_ms: int
    source_key: str = "world"
    def __post_init__(self) -> None:
        if not self.life_id or len(self.world_scope_hash) != 64:
            raise ValueError("CHANGE_HAZARD_SCOPE_INVALID")
        if isinstance(self.change_count, bool) or self.change_count < 0:
            raise ValueError("CHANGE_HAZARD_COUNT_INVALID")
        if self.window_start_ms < 0 or self.window_end_ms <= self.window_start_ms:
            raise ValueError("CHANGE_HAZARD_WINDOW_INVALID")
        if not self.source_key:
            raise ValueError("CHANGE_HAZARD_SOURCE_KEY_REQUIRED")
    @property
    def exposure_ms(self) -> int:
        return self.window_end_ms - self.window_start_ms

@dataclass(frozen=True, slots=True)
class StaleHazardEstimate:
    life_id: str
    world_scope_hash: str
    source_key: str
    change_count: int
    exposure_ms: int
    age_ms: int
    hazard_milli: int
    telemetry_available: bool = True


def stale_hazard_milli(*, change_count: int, exposure_ms: int, age_ms: int) -> int:
    """P_stale = 1-exp(-lambda*dt), with lambda=n/exposure from observed changes only."""
    if isinstance(change_count, bool) or isinstance(exposure_ms, bool) or isinstance(age_ms, bool):
        raise ValueError("STALE_HAZARD_INPUT_INVALID")
    if change_count < 0 or exposure_ms <= 0 or age_ms < 0:
        raise ValueError("STALE_HAZARD_INPUT_INVALID")
    if change_count == 0 or age_ms == 0:
        return 0
    with localcontext() as ctx:
        ctx.prec = 50
        x = (Decimal(change_count) * Decimal(age_ms)) / Decimal(exposure_ms)
        hazard = Decimal(1) - (-x).exp()
        milli = (hazard * Decimal(1000)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return max(0, min(1000, int(milli)))


def estimate_stale_hazard(window: ChangeHazardWindow, *, now_ms: int, last_validated_at_ms: int) -> StaleHazardEstimate:
    if now_ms < 0 or last_validated_at_ms < 0 or now_ms < last_validated_at_ms:
        raise ValueError("STALE_HAZARD_TIME_INVALID")
    age = now_ms - last_validated_at_ms
    return StaleHazardEstimate(
        life_id=window.life_id,
        world_scope_hash=window.world_scope_hash,
        source_key=window.source_key,
        change_count=window.change_count,
        exposure_ms=window.exposure_ms,
        age_ms=age,
        hazard_milli=stale_hazard_milli(change_count=window.change_count, exposure_ms=window.exposure_ms, age_ms=age),
    )

__all__ = ["ChangeHazardWindow", "StaleHazardEstimate", "stale_hazard_milli", "estimate_stale_hazard"]
