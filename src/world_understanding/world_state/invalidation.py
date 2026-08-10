"""WorldCut delta and precise ref invalidation helpers."""
from __future__ import annotations
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.world_cut import WorldCut
from world_understanding.common.world_cut import compare_world_cuts
from .manifests import DependencyManifest


def watermark_key(source_kind: str, watermark_type: str) -> str:
    return f"{source_kind}:{watermark_type}"

def changed_watermark_keys(previous: WorldCut | None, current: WorldCut) -> tuple[str, ...]:
    if previous is None:
        return tuple(watermark_key(w.source_kind, w.watermark_type) for w in current.source_watermarks)
    relation=compare_world_cuts(current, previous)
    if relation == "INCOMPATIBLE":
        raise ValueError("WORLD_CUT_INCOMPATIBLE")
    old={(w.source_kind,w.watermark_type):(w.watermark_value,w.sequence) for w in previous.source_watermarks}
    new={(w.source_kind,w.watermark_type):(w.watermark_value,w.sequence) for w in current.source_watermarks}
    keys=[]
    for key in sorted(set(old)|set(new)):
        if old.get(key)!=new.get(key): keys.append(watermark_key(*key))
    return tuple(keys)

def precise_invalidations(*, previous_dependencies: DependencyManifest | None, changed_source_keys: tuple[str, ...], current_refs: tuple[WorldRecordRef, ...], refreshed_identity_keys: frozenset[tuple[str,str]]) -> tuple[WorldRecordRef, ...]:
    if previous_dependencies is None or not changed_source_keys:
        return ()
    changed=set(changed_source_keys)
    current_by_identity={(r.record_type,r.record_id):r for r in current_refs}
    out=[]
    for binding in previous_dependencies.bindings:
        identity=(binding.ref.record_type,binding.ref.record_id)
        current=current_by_identity.get(identity)
        if current is None or identity in refreshed_identity_keys:
            continue
        if changed.intersection(binding.source_keys): out.append(current)
    return tuple(sorted({r.sort_key():r for r in out}.values(), key=lambda r:r.sort_key()))

__all__=["watermark_key","changed_watermark_keys","precise_invalidations"]
