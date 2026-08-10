"""Three-time intersection and freshness helpers; STALE is epistemic, never truth."""
from __future__ import annotations
from contracts.world_understanding.time import WorldTime

class TimeIntersectionEmpty(ValueError):
    pass


def intersect_world_times(times: tuple[WorldTime, ...]) -> WorldTime:
    if not times:
        raise ValueError("time intersection requires inputs")
    valid_from = max(item.valid_from_ms for item in times)
    finite_ends = [item.valid_until_ms for item in times if item.valid_until_ms is not None]
    valid_until = min(finite_ends) if finite_ends else None
    if valid_until is not None and valid_until < valid_from:
        raise TimeIntersectionEmpty("TIME_INTERSECTION_EMPTY")
    observed_values = [item.observed_at_ms for item in times]
    observed_at = None if any(value is None for value in observed_values) else max(int(value) for value in observed_values if value is not None)
    recorded_at = max(item.recorded_at_ms for item in times)
    return WorldTime(valid_from_ms=valid_from, valid_until_ms=valid_until, observed_at_ms=observed_at, recorded_at_ms=recorded_at)


def epistemic_freshness(*, current_epistemic_state: str, recorded_at_ms: int, now_ms: int, stale_after_ms: int | None) -> str:
    if current_epistemic_state in {"CHALLENGED", "REVERIFYING", "RETIRED"}:
        return current_epistemic_state
    if stale_after_ms is None:
        return current_epistemic_state
    if stale_after_ms < 0 or now_ms < recorded_at_ms:
        raise ValueError("invalid freshness clock")
    return "STALE" if now_ms - recorded_at_ms > stale_after_ms else current_epistemic_state

__all__ = ["TimeIntersectionEmpty", "intersect_world_times", "epistemic_freshness"]
