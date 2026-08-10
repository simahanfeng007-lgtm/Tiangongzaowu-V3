"""Compact L6 manifests. They contain record references and small lineage metadata only."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef


def _normalize_refs(refs: tuple[WorldRecordRef, ...]) -> tuple[WorldRecordRef, ...]:
    by_key = {ref.sort_key(): ref for ref in refs}
    if len(by_key) != len(refs):
        raise ValueError("WORLD_STATE_DUPLICATE_REF")
    return tuple(by_key[key] for key in sorted(by_key))


def _manifest_ref(kind: str, digest: str) -> WorldRecordRef:
    return WorldRecordRef(record_type=f"world_{kind}_manifest", record_id=f"wmf.{kind}.{digest[:32]}", revision=None, sha256=digest)

@dataclass(frozen=True, slots=True)
class HeadManifest:
    kind: str
    refs: tuple[WorldRecordRef, ...]
    manifest_sha256: str
    @classmethod
    def build(cls, kind: str, refs: tuple[WorldRecordRef, ...], *, max_items: int) -> "HeadManifest":
        if kind not in {"entity_heads", "relation_heads", "cognition_heads", "active_hypotheses", "uncertainty"}:
            raise ValueError("WORLD_STATE_MANIFEST_KIND_INVALID")
        refs = _normalize_refs(refs)
        if len(refs) > max_items:
            raise ValueError("WORLD_STATE_SNAPSHOT_LIMIT")
        digest = canonical_sha256({"domain":"tiangong.world.state-head-manifest.v1","kind":kind,"refs":[r.model_dump(mode="json") for r in refs]})
        return cls(kind, refs, digest)
    @property
    def ref(self) -> WorldRecordRef:
        return _manifest_ref(self.kind, self.manifest_sha256)

@dataclass(frozen=True, slots=True)
class DependencyBinding:
    ref: WorldRecordRef
    source_keys: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    def __post_init__(self) -> None:
        keys = tuple(sorted(set(self.source_keys)))
        if keys != self.source_keys or any(not key or len(key) > 180 for key in keys):
            raise ValueError("WORLD_STATE_DEPENDENCY_KEYS_INVALID")
        evidence = tuple(sorted(set(self.evidence_ids)))
        if evidence != self.evidence_ids or any(len(item) != 68 or not item.startswith("cev_") for item in evidence):
            raise ValueError("WORLD_STATE_DEPENDENCY_EVIDENCE_IDS_INVALID")
        if evidence and self.ref.record_type != "world_cognition":
            raise ValueError("WORLD_STATE_DEPENDENCY_EVIDENCE_ONLY_FOR_COGNITION")

@dataclass(frozen=True, slots=True)
class DependencyManifest:
    bindings: tuple[DependencyBinding, ...]
    manifest_sha256: str
    @classmethod
    def build(cls, bindings: tuple[DependencyBinding, ...], *, max_items: int) -> "DependencyManifest":
        bindings = tuple(sorted(bindings, key=lambda b: b.ref.sort_key()))
        keys = tuple(b.ref.sort_key() for b in bindings)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("WORLD_STATE_DUPLICATE_DEPENDENCY_REF")
        if len(bindings) > max_items:
            raise ValueError("WORLD_STATE_DEPENDENCY_LIMIT")
        digest = canonical_sha256({"domain":"tiangong.world.state-dependency-manifest.v1","bindings":[{"ref":b.ref.model_dump(mode="json"),"source_keys":b.source_keys,"evidence_ids":b.evidence_ids} for b in bindings]})
        return cls(bindings, digest)
    @property
    def ref(self) -> WorldRecordRef:
        return _manifest_ref("dependency", self.manifest_sha256)
    def binding_for(self, ref: WorldRecordRef) -> DependencyBinding | None:
        key = ref.sort_key()
        for binding in self.bindings:
            if binding.ref.sort_key() == key:
                return binding
        return None
    def source_keys_for(self, ref: WorldRecordRef) -> tuple[str, ...]:
        binding = self.binding_for(ref)
        return () if binding is None else binding.source_keys

@dataclass(frozen=True, slots=True)
class DeltaManifest:
    previous_state_ref: WorldRecordRef | None
    changed_source_keys: tuple[str, ...]
    added_refs: tuple[WorldRecordRef, ...]
    removed_refs: tuple[WorldRecordRef, ...]
    changed_refs: tuple[WorldRecordRef, ...]
    invalidated_refs: tuple[WorldRecordRef, ...]
    revalidated_cognition_refs: tuple[WorldRecordRef, ...]
    uncertainty_manifest_ref: WorldRecordRef | None
    dependency_manifest_ref: WorldRecordRef
    manifest_sha256: str
    @classmethod
    def build(cls, *, previous_state_ref: WorldRecordRef | None, changed_source_keys: tuple[str, ...], added_refs: tuple[WorldRecordRef, ...], removed_refs: tuple[WorldRecordRef, ...], changed_refs: tuple[WorldRecordRef, ...], invalidated_refs: tuple[WorldRecordRef, ...], revalidated_cognition_refs: tuple[WorldRecordRef, ...], uncertainty_manifest_ref: WorldRecordRef | None, dependency_manifest_ref: WorldRecordRef) -> "DeltaManifest":
        changed_source_keys = tuple(sorted(set(changed_source_keys)))
        normalized = [_normalize_refs(value) for value in (added_refs, removed_refs, changed_refs, invalidated_refs, revalidated_cognition_refs)]
        payload={
            "domain":"tiangong.world.state-delta-manifest.v1",
            "previous_state_ref":None if previous_state_ref is None else previous_state_ref.model_dump(mode="json"),
            "changed_source_keys":changed_source_keys,
            "added_refs":[r.model_dump(mode="json") for r in normalized[0]],
            "removed_refs":[r.model_dump(mode="json") for r in normalized[1]],
            "changed_refs":[r.model_dump(mode="json") for r in normalized[2]],
            "invalidated_refs":[r.model_dump(mode="json") for r in normalized[3]],
            "revalidated_cognition_refs":[r.model_dump(mode="json") for r in normalized[4]],
            "uncertainty_manifest_ref":None if uncertainty_manifest_ref is None else uncertainty_manifest_ref.model_dump(mode="json"),
            "dependency_manifest_ref":dependency_manifest_ref.model_dump(mode="json"),
        }
        digest=canonical_sha256(payload)
        return cls(previous_state_ref, changed_source_keys, *normalized, uncertainty_manifest_ref, dependency_manifest_ref, digest)
    @property
    def ref(self) -> WorldRecordRef:
        return _manifest_ref("delta", self.manifest_sha256)

__all__=["HeadManifest","DependencyBinding","DependencyManifest","DeltaManifest"]
