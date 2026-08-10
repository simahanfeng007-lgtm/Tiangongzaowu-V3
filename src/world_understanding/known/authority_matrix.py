"""P4 authority intersection backed by the P5 shared common kernel."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding.observability import ObservabilityState
from contracts.world_understanding.source import WorldSourceRef
from contracts.world_understanding.time import WorldTime
from world_understanding.common.provenance import merge_provenance
from world_understanding.common.time import intersect_world_times, TimeIntersectionEmpty
from world_understanding.common.observability import intersect_observability
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
    ceiling = min(parent.authority_ceiling_milli for parent in parents)
    weight = min([ceiling, *(parent.empirical_evidence_weight_milli for parent in parents)])
    coverage_values = [parent.coverage_milli for parent in parents]
    coverage = None if any(value is None for value in coverage_values) else min(int(value) for value in coverage_values if value is not None)
    order = {"CURRENT": 0, "STALE": 1, "REVERIFYING": 2, "CHALLENGED": 3, "RETIRED": 4}
    epistemic = max((parent.epistemic_state for parent in parents), key=lambda value: order[value])
    try:
        merged_time = intersect_world_times(tuple(parent.time for parent in parents))
    except TimeIntersectionEmpty as exc:
        raise AuthorityIntersectionError(str(exc)) from exc
    return DerivedEnvelope(
        authority_domain=parent_domain,
        authority_ceiling_milli=ceiling,
        empirical_evidence_weight_milli=weight,
        provenance_refs=merge_provenance(tuple(parent.provenance_refs for parent in parents)),
        observability_state=intersect_observability(tuple(parent.observability_state for parent in parents)),
        coverage_milli=coverage,
        time=merged_time,
        epistemic_state=epistemic,
    )
