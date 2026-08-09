"""Internal curiosity and knowledge-gap contracts; neither is reality evidence."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import CuriosityId, HashedWorldContract, KnowledgeGapId, WorldRecordRef, sorted_unique_refs, sorted_unique_strings
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

def derive_knowledge_gap_id(*, world_scope_hash: str, subject_refs: tuple[WorldRecordRef, ...], missing_evidence_types: tuple[str, ...], basis_refs: tuple[WorldRecordRef, ...]) -> str:
    return "wgap_" + canonical_sha256({"domain":"tiangong.world.knowledge-gap-id.v1","world_scope_hash":world_scope_hash,"subject_refs":[x.model_dump(mode="json") for x in subject_refs],"missing_evidence_types":list(missing_evidence_types),"basis_refs":[x.model_dump(mode="json") for x in basis_refs]})

def derive_curiosity_id(*, world_scope_hash: str, frame_ref: WorldRecordRef | None, question: str, subject_refs: tuple[WorldRecordRef, ...], provenance_refs: tuple[WorldRecordRef, ...], created_at_ms: int) -> str:
    return "wcur_" + canonical_sha256({"domain":"tiangong.world.curiosity-id.v1","world_scope_hash":world_scope_hash,"frame_ref":None if frame_ref is None else frame_ref.model_dump(mode="json"),"question":question,"subject_refs":[x.model_dump(mode="json") for x in subject_refs],"provenance_refs":[x.model_dump(mode="json") for x in provenance_refs],"created_at_ms":created_at_ms})

class KnowledgeGap(HashedWorldContract):
    _hash_field="gap_sha256"
    gap_id: KnowledgeGapId
    scope: WorldScope
    subject_refs: tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    uncertainty_milli: int=Field(ge=0,le=1000,strict=True)
    conflict_milli: int=Field(ge=0,le=1000,strict=True)
    observability_gap_milli: int=Field(ge=0,le=1000,strict=True)
    staleness_milli: int=Field(ge=0,le=1000,strict=True)
    prediction_error_milli: int=Field(ge=0,le=1000,strict=True)
    impact_milli: int=Field(ge=0,le=1000,strict=True)
    relevance_milli: int=Field(ge=0,le=1000,strict=True)
    gap_score_milli: int=Field(ge=0,le=1000,strict=True)
    missing_evidence_types: tuple[OpaqueId,...]=Field(default=(),max_length=256)
    basis_refs: tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    empirical_evidence_weight_milli: Literal[0]=0
    may_execute: Literal[False]=False
    gap_sha256: Sha256
    _subjects=field_validator("subject_refs")(sorted_unique_refs)
    _basis=field_validator("basis_refs")(sorted_unique_refs)
    _missing=field_validator("missing_evidence_types")(sorted_unique_strings)
    @model_validator(mode="after")
    def check(self)->Self:
        if self.gap_id != derive_knowledge_gap_id(world_scope_hash=self.scope.world_scope_hash,subject_refs=self.subject_refs,missing_evidence_types=self.missing_evidence_types,basis_refs=self.basis_refs): raise ValueError("knowledge gap id mismatch")
        return self

class WorldCuriosity(HashedWorldContract):
    _hash_field="curiosity_sha256"
    curiosity_id: CuriosityId
    scope: WorldScope
    frame_ref: WorldRecordRef|None=None
    subject_refs: tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    question: str=Field(min_length=1,max_length=20_000)
    curiosity_kind: OpaqueId
    trigger_reasons: tuple[OpaqueId,...]=Field(default=(),max_length=256)
    uncertainty_milli: int=Field(ge=0,le=1000,strict=True)
    novelty_milli: int=Field(ge=0,le=1000,strict=True)
    prediction_error_milli: int=Field(ge=0,le=1000,strict=True)
    conflict_milli: int=Field(ge=0,le=1000,strict=True)
    impact_milli: int=Field(ge=0,le=1000,strict=True)
    task_relevance_milli: int=Field(ge=0,le=1000,strict=True)
    expected_information_gain_milli: int=Field(ge=0,le=1000,strict=True)
    expected_cost_milli: int=Field(ge=0,le=1_000_000,strict=True)
    missing_evidence_types: tuple[OpaqueId,...]=Field(default=(),max_length=256)
    provenance_refs: tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    created_at_ms: int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    expires_at_ms: int|None=Field(default=None,ge=0,le=9_007_199_254_740_991,strict=True)
    empirical_evidence_weight_milli: Literal[0]=0
    may_authorize: Literal[False]=False
    may_execute: Literal[False]=False
    may_call_tools: Literal[False]=False
    curiosity_sha256: Sha256
    _subjects=field_validator("subject_refs")(sorted_unique_refs)
    _reasons=field_validator("trigger_reasons")(sorted_unique_strings)
    _missing=field_validator("missing_evidence_types")(sorted_unique_strings)
    _provenance=field_validator("provenance_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def check(self)->Self:
        if self.expires_at_ms is not None and self.expires_at_ms < self.created_at_ms: raise ValueError("curiosity expiry precedes creation")
        if self.curiosity_id != derive_curiosity_id(world_scope_hash=self.scope.world_scope_hash,frame_ref=self.frame_ref,question=self.question,subject_refs=self.subject_refs,provenance_refs=self.provenance_refs,created_at_ms=self.created_at_ms): raise ValueError("curiosity id mismatch")
        return self
