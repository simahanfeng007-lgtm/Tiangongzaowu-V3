"""Direct Known K0 and deterministic Derived Known contracts."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, model_validator, field_validator
from ._base import EpistemicState, HashedWorldContract, KnownId, TruthState, WorldRecordRef, WorldValue, sorted_unique_refs
from .authority import AuthorityDomain
from .observability import ObservabilityState
from .scope import WorldScope
from .source import SourceKind, WorldSourceRef
from .time import WorldTime
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

DerivationType = Literal["DIRECT", "DETERMINISTIC_DERIVED"]

def _object_identity(*, object_value: WorldValue | None, object_ref: WorldRecordRef | None) -> dict:
    if (object_value is None) == (object_ref is None):
        raise ValueError("known proposition requires exactly one object value/ref")
    return {"object_value": None if object_value is None else object_value.model_dump(mode="json"), "object_ref": None if object_ref is None else object_ref.model_dump(mode="json")}

def derive_direct_known_id(*, world_scope_hash: str, proposition_type: str, subject_ref: str, predicate: str, object_value: WorldValue | None, object_ref: WorldRecordRef | None, source_envelope_id: str) -> str:
    return "wkn_" + canonical_sha256({"domain": "tiangong.world.direct-known-id.v1", "world_scope_hash": world_scope_hash, "proposition_type": proposition_type, "subject_ref": subject_ref, "predicate": predicate, **_object_identity(object_value=object_value, object_ref=object_ref), "source_envelope_id": source_envelope_id})

def derive_derived_known_id(*, world_scope_hash: str, proposition_type: str, subject_ref: str, predicate: str, object_value: WorldValue | None, object_ref: WorldRecordRef | None, transform_id: str, parent_known_refs: tuple[WorldRecordRef, ...]) -> str:
    return "wkn_" + canonical_sha256({"domain": "tiangong.world.derived-known-id.v1", "world_scope_hash": world_scope_hash, "proposition_type": proposition_type, "subject_ref": subject_ref, "predicate": predicate, **_object_identity(object_value=object_value, object_ref=object_ref), "transform_id": transform_id, "parent_known_refs": [item.model_dump(mode="json") for item in parent_known_refs]})

class _KnownBase(HashedWorldContract):
    _hash_field = "record_hash"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    known_id: KnownId
    proposition_type: OpaqueId
    subject_ref: OpaqueId
    predicate: OpaqueId
    object_value: WorldValue | None = None
    object_ref: WorldRecordRef | None = None
    world_scope: WorldScope
    time: WorldTime
    authority_domain: AuthorityDomain
    authority_ceiling_milli: int = Field(ge=0, le=1000, strict=True)
    observability_state: ObservabilityState
    coverage_milli: int | None = Field(default=None, ge=0, le=1000, strict=True)
    truth_state: TruthState
    epistemic_state: EpistemicState
    provenance_refs: tuple[WorldSourceRef, ...] = Field(default=(), max_length=4096)
    empirical_evidence_weight_milli: int = Field(default=0, ge=0, le=1000, strict=True)
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    record_hash: Sha256

    @model_validator(mode="after")
    def validate_known_base(self) -> Self:
        _object_identity(object_value=self.object_value, object_ref=self.object_ref)
        if self.empirical_evidence_weight_milli > self.authority_ceiling_milli:
            raise ValueError("known evidence weight cannot exceed authority ceiling")
        if self.coverage_milli is not None and self.coverage_milli > self.observability_state.scope_coverage_milli:
            raise ValueError("known coverage cannot exceed observability scope coverage")
        return self

class DirectKnownRecord(_KnownBase):
    derivation_type: Literal["DIRECT"] = "DIRECT"
    parent_known_refs: tuple[WorldRecordRef, ...] = ()
    source_envelope_id: OpaqueId
    source_kind: SourceKind
    source_native_id: OpaqueId
    source_payload_hash: Sha256
    compiler_id: OpaqueId
    compiler_version: OpaqueId

    @model_validator(mode="after")
    def validate_direct(self) -> Self:
        if self.parent_known_refs:
            raise ValueError("direct known cannot have parent known refs")
        if self.source_kind in {"CONTEXT_REQUEST", "UNCLASSIFIED_SOURCE"}:
            raise ValueError("control or unclassified ingress cannot become Direct Known")
        expected = derive_direct_known_id(world_scope_hash=self.world_scope.world_scope_hash, proposition_type=self.proposition_type, subject_ref=self.subject_ref, predicate=self.predicate, object_value=self.object_value, object_ref=self.object_ref, source_envelope_id=self.source_envelope_id)
        if self.known_id != expected:
            raise ValueError("direct known id mismatch")
        return self

class DerivedKnownRecord(_KnownBase):
    derivation_type: Literal["DETERMINISTIC_DERIVED"] = "DETERMINISTIC_DERIVED"
    parent_known_refs: tuple[WorldRecordRef, ...] = Field(min_length=1, max_length=4096)
    transform_id: OpaqueId
    transform_version: OpaqueId
    derivation_hash: Sha256
    _validate_parents = field_validator("parent_known_refs")(sorted_unique_refs)

    @model_validator(mode="after")
    def validate_derived(self) -> Self:
        expected = derive_derived_known_id(world_scope_hash=self.world_scope.world_scope_hash, proposition_type=self.proposition_type, subject_ref=self.subject_ref, predicate=self.predicate, object_value=self.object_value, object_ref=self.object_ref, transform_id=self.transform_id, parent_known_refs=self.parent_known_refs)
        if self.known_id != expected:
            raise ValueError("derived known id mismatch")
        if self.derivation_hash != canonical_sha256({"transform_id": self.transform_id, "transform_version": self.transform_version, "parent_known_refs": [item.model_dump(mode="json") for item in self.parent_known_refs]}):
            raise ValueError("derived known derivation_hash mismatch")
        return self
