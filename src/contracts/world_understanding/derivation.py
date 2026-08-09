"""Derivation DAG contracts. These edges are never World Graph relations."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import DerivationEdgeId, DerivationId, HashedWorldContract, WorldRecordRef, sorted_unique_refs, sorted_unique_strings
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

def derive_derivation_id(*, source_refs: tuple[WorldRecordRef, ...], target_refs: tuple[WorldRecordRef, ...], transform_type: str, transform_version: str) -> str:
    return "wdrv_" + canonical_sha256({"domain":"tiangong.world.derivation-id.v1","source_refs":[x.model_dump(mode="json") for x in source_refs],"target_refs":[x.model_dump(mode="json") for x in target_refs],"transform_type":transform_type,"transform_version":transform_version})

def derive_derivation_edge_id(*, derivation_ref: WorldRecordRef, source_ref: WorldRecordRef, target_ref: WorldRecordRef, edge_kind: str) -> str:
    return "wdge_" + canonical_sha256({"domain":"tiangong.world.derivation-edge-id.v1","derivation_ref":derivation_ref.model_dump(mode="json"),"source_ref":source_ref.model_dump(mode="json"),"target_ref":target_ref.model_dump(mode="json"),"edge_kind":edge_kind})

class DerivationRef(HashedWorldContract):
    _hash_field="derivation_sha256"
    derivation_id:DerivationId
    source_refs:tuple[WorldRecordRef,...]=Field(min_length=1,max_length=4096)
    target_refs:tuple[WorldRecordRef,...]=Field(min_length=1,max_length=4096)
    transform_type:OpaqueId
    transform_version:OpaqueId
    model_assisted:bool=False
    lineage_root_hashes:tuple[Sha256,...]=Field(min_length=1,max_length=4096)
    authority_ceiling_milli:int=Field(ge=0,le=1000,strict=True)
    created_at_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    empirical_evidence_weight_milli:Literal[0]=0
    derivation_sha256:Sha256
    _sources=field_validator("source_refs")(sorted_unique_refs)
    _targets=field_validator("target_refs")(sorted_unique_refs)
    _roots=field_validator("lineage_root_hashes")(sorted_unique_strings)
    @model_validator(mode="after")
    def check(self)->Self:
        if {x.sort_key() for x in self.source_refs}&{x.sort_key() for x in self.target_refs}: raise ValueError("derivation cannot target the same exact record revision it consumes")
        if self.derivation_id!=derive_derivation_id(source_refs=self.source_refs,target_refs=self.target_refs,transform_type=self.transform_type,transform_version=self.transform_version): raise ValueError("derivation id mismatch")
        return self

class DerivationEdge(HashedWorldContract):
    _hash_field="edge_sha256"
    edge_id:DerivationEdgeId
    derivation_ref:WorldRecordRef
    source_ref:WorldRecordRef
    target_ref:WorldRecordRef
    edge_kind:Literal["SOURCE_TO_DERIVATION","DERIVATION_TO_TARGET"]
    created_at_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    empirical_evidence_weight_milli:Literal[0]=0
    edge_sha256:Sha256
    @model_validator(mode="after")
    def check(self)->Self:
        if self.edge_id!=derive_derivation_edge_id(derivation_ref=self.derivation_ref,source_ref=self.source_ref,target_ref=self.target_ref,edge_kind=self.edge_kind): raise ValueError("derivation edge id mismatch")
        return self
