"""Provenance integrity and evidence-independence primitives."""
from __future__ import annotations
from collections.abc import Iterable
from contracts.world_understanding.source import WorldSourceRef

class ProvenanceBroken(ValueError):
    pass


def merge_provenance(ref_groups: Iterable[tuple[WorldSourceRef, ...]]) -> tuple[WorldSourceRef, ...]:
    by_key: dict[tuple, WorldSourceRef] = {}
    for refs in ref_groups:
        for ref in refs:
            by_key[ref.sort_key()] = ref
    return tuple(by_key[key] for key in sorted(by_key))


def require_provenance(refs: tuple[WorldSourceRef, ...], *, empirical_weight_milli: int) -> None:
    if empirical_weight_milli > 0 and not refs:
        raise ProvenanceBroken("PROVENANCE_BROKEN")


def independence_family(ref: WorldSourceRef) -> tuple[str, str]:
    """Different revisions/hashes of the same native source are not independent evidence."""
    return (ref.source_kind, ref.object_id)


def independent_evidence_count(ref_groups: Iterable[tuple[WorldSourceRef, ...]]) -> int:
    families: set[tuple[str, str]] = set()
    for refs in ref_groups:
        for ref in refs:
            families.add(independence_family(ref))
    return len(families)

__all__ = ["ProvenanceBroken", "merge_provenance", "require_provenance", "independence_family", "independent_evidence_count"]
