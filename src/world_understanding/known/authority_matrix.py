"""Conservative P4 authority/provenance intersection. Full Γ arrives in P5."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding.observability import ObservabilityState
from contracts.world_understanding.source import WorldSourceRef
from contracts.world_understanding.time import WorldTime
from .set import KnownRecord
from .rule import RuleSpec

class AuthorityIntersectionError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class DerivedEnvelope:
    authority_domain: str
    authority_ceiling_milli: int
    empirical_evidence_weight_milli: int
    provenance_refs: tuple[WorldSourceRef, ...]
    observability_state: ObservabilityState
    coverage_milli: int | None
    time: WorldTime
    epistemic_state: str


def _merge_provenance(parents: tuple[KnownRecord, ...]) -> tuple[WorldSourceRef, ...]:
    by_key: dict[tuple, WorldSourceRef] = {}
    for parent in parents:
        for ref in parent.provenance_refs:
            by_key[ref.sort_key()] = ref
    return tuple(by_key[key] for key in sorted(by_key))


def _merge_time(parents: tuple[KnownRecord, ...]) -> WorldTime:
    valid_from = max(parent.time.valid_from_ms for parent in parents)
    finite_ends = [parent.time.valid_until_ms for parent in parents if parent.time.valid_until_ms is not None]
    valid_until = min(finite_ends) if finite_ends else None
    if valid_until is not None and valid_until < valid_from:
        raise AuthorityIntersectionError("TIME_INTERSECTION_EMPTY")
    observed_values = [parent.time.observed_at_ms for parent in parents]
    observed_at = None if any(value is None for value in observed_values) else max(int(value) for value in observed_values if value is not None)
    recorded_at = max(parent.time.recorded_at_ms for parent in parents)
    return WorldTime(valid_from_ms=valid_from, valid_until_ms=valid_until, observed_at_ms=observed_at, recorded_at_ms=recorded_at)


def _merge_observability(parents: tuple[KnownRecord, ...]) -> ObservabilityState:
    access = min(parent.observability_state.access_milli for parent in parents)
    scope = min(parent.observability_state.scope_coverage_milli for parent in parents)
    time = min(parent.observability_state.time_coverage_milli for parent in parents)
    adapter = min(parent.observability_state.adapter_quality_milli for parent in parents)
    measurement = min(parent.observability_state.measurement_quality_milli for parent in parents)
    combined = access * scope * time * adapter * measurement // (1000 ** 4)
    mode = "OBSERVED" if all(parent.observability_state.mode == "OBSERVED" for parent in parents) else "PARTIAL"
    if combined == 0:
        mode = "NOT_OBSERVED"
    return ObservabilityState(mode=mode, access_milli=access, scope_coverage_milli=scope, time_coverage_milli=time, adapter_quality_milli=adapter, measurement_quality_milli=measurement, combined_quality_milli=combined)


def intersect_authority(spec: RuleSpec, parents: tuple[KnownRecord, ...]) -> DerivedEnvelope:
    if not parents:
        raise AuthorityIntersectionError("DERIVATION_REQUIRES_PARENTS")
    domains = {parent.authority_domain for parent in parents}
    if spec.accepted_parent_domains and any(domain not in spec.accepted_parent_domains for domain in domains):
        raise AuthorityIntersectionError("AUTHORITY_DOMAIN_MISMATCH")
    if len(domains) != 1:
        raise AuthorityIntersectionError("AUTHORITY_DOMAIN_INTERSECTION_EMPTY")
    parent_domain = next(iter(domains))
    if spec.output_authority_domain is not None and spec.output_authority_domain != parent_domain:
        raise AuthorityIntersectionError("AUTHORITY_DOMAIN_WIDENING_FORBIDDEN")
    output_domain = parent_domain
    ceiling = min(parent.authority_ceiling_milli for parent in parents)
    weight = min([ceiling, *(parent.empirical_evidence_weight_milli for parent in parents)])
    coverage_values = [parent.coverage_milli for parent in parents]
    coverage = None if any(value is None for value in coverage_values) else min(int(value) for value in coverage_values if value is not None)
    order = {"CURRENT": 0, "STALE": 1, "REVERIFYING": 2, "CHALLENGED": 3, "RETIRED": 4}
    epistemic = max((parent.epistemic_state for parent in parents), key=lambda value: order[value])
    return DerivedEnvelope(
        authority_domain=output_domain,
        authority_ceiling_milli=ceiling,
        empirical_evidence_weight_milli=weight,
        provenance_refs=_merge_provenance(parents),
        observability_state=_merge_observability(parents),
        coverage_milli=coverage,
        time=_merge_time(parents),
        epistemic_state=epistemic,
    )
