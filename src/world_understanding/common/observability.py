"""Open-world observability and negative-evidence eligibility."""
from __future__ import annotations
from contracts.world_understanding.observability import ObservabilityState, compute_observability_quality_milli

class NegativeEvidenceInsufficient(ValueError):
    pass


def intersect_observability(states: tuple[ObservabilityState, ...]) -> ObservabilityState:
    if not states:
        raise ValueError("observability intersection requires inputs")
    access = min(item.access_milli for item in states)
    scope = min(item.scope_coverage_milli for item in states)
    time = min(item.time_coverage_milli for item in states)
    adapter = min(item.adapter_quality_milli for item in states)
    measurement = min(item.measurement_quality_milli for item in states)
    combined = compute_observability_quality_milli(
        access_milli=access,
        scope_coverage_milli=scope,
        time_coverage_milli=time,
        adapter_quality_milli=adapter,
        measurement_quality_milli=measurement,
    )
    if combined == 0:
        mode = "NOT_OBSERVED"
    elif all(item.mode == "OBSERVED" for item in states):
        mode = "OBSERVED"
    else:
        mode = "PARTIAL"
    search_hashes = {item.search_scope_hash for item in states if item.search_scope_hash is not None}
    search_scope_hash = next(iter(search_hashes)) if len(search_hashes) == 1 else None
    return ObservabilityState(
        mode=mode,
        access_milli=access,
        scope_coverage_milli=scope,
        time_coverage_milli=time,
        adapter_quality_milli=adapter,
        measurement_quality_milli=measurement,
        combined_quality_milli=combined,
        search_scope_hash=search_scope_hash,
    )


def effective_coverage_milli(record: object) -> int:
    explicit = getattr(record, "coverage_milli", None)
    if explicit is not None:
        return int(explicit)
    state = getattr(record, "observability_state", None)
    return 0 if state is None else int(state.scope_coverage_milli)


def is_negative_known(record: object) -> bool:
    proposition = str(getattr(record, "proposition_type", ""))
    value = getattr(record, "object_value", None)
    text = getattr(value, "string_value", None) if value is not None else None
    if proposition == "FILE_EXISTS" and text == "false":
        return True
    upper = proposition.upper()
    return upper.startswith(("NOT_", "NO_", "MISSING_", "ABSENT_"))


def require_negative_evidence_coverage(record: object, *, min_coverage_milli: int = 1) -> None:
    if not is_negative_known(record):
        return
    state = getattr(record, "observability_state", None)
    coverage = effective_coverage_milli(record)
    if state is None or state.mode not in {"OBSERVED", "PARTIAL"} or coverage < min_coverage_milli:
        raise NegativeEvidenceInsufficient("NEGATIVE_EVIDENCE_REQUIRES_COVERAGE")

__all__ = ["NegativeEvidenceInsufficient", "intersect_observability", "effective_coverage_milli", "is_negative_known", "require_negative_evidence_coverage"]
