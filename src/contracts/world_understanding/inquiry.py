"""The second and only other public semantic output: a non-executable inquiry."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, InquiryId, InquiryOutcomeId, KnowledgeGapId, CuriosityId, WorldRecordRef, sorted_unique_refs, sorted_unique_strings
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

InquiryStatus = Literal["PENDING","DEFERRED","ACCEPTED","DECLINED","SATISFIED","EXPIRED","CANCELLED"]
SelfWillDecision = Literal["ACCEPT","DEFER","DISMISS","EXPIRE"]

def derive_inquiry_id(*, world_scope_hash:str, question:str, knowledge_gap_id:str, subject_refs:tuple[WorldRecordRef,...])->str:
    return "winq_"+canonical_sha256({"domain":"tiangong.world.inquiry-slot-id.v1","world_scope_hash":world_scope_hash,"question":question,"knowledge_gap_id":knowledge_gap_id,"subject_refs":[x.model_dump(mode="json") for x in subject_refs]})

class WorldInquiry(HashedWorldContract):
    _hash_field="inquiry_sha256"
    schema_version:Literal["tiangong.world-understanding.contracts.v1"]="tiangong.world-understanding.contracts.v1"
    inquiry_id:InquiryId
    correlation_id:OpaqueId
    curiosity_id:CuriosityId
    knowledge_gap_id:KnowledgeGapId
    scope:WorldScope
    frame_ref:WorldRecordRef|None=None
    subject_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    question:str=Field(min_length=1,max_length=20_000)
    inquiry_kind:OpaqueId
    reason_codes:tuple[OpaqueId,...]=Field(default=(),max_length=256)
    missing_evidence_types:tuple[OpaqueId,...]=Field(default=(),max_length=256)
    supporting_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    conflict_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    expected_information_gain_milli:int=Field(ge=0,le=1000,strict=True)
    impact_milli:int=Field(ge=0,le=1000,strict=True)
    urgency_milli:int=Field(ge=0,le=1000,strict=True)
    estimated_cost_class:Literal["LOW","MEDIUM","HIGH","UNKNOWN"]="UNKNOWN"
    risk_hint:Literal["A0","A1","A2","A3","A4","A5","UNKNOWN"]="UNKNOWN"
    suggested_observation_modalities:tuple[OpaqueId,...]=Field(default=(),max_length=256)
    source_world_state_ref:WorldRecordRef|None=None
    source_cognition_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    dedup_key:Sha256
    parent_inquiry_id:InquiryId|None=None
    generation:int=Field(default=0,ge=0,le=1024,strict=True)
    inquiry_budget_remaining:int=Field(default=0,ge=0,le=1_000_000,strict=True)
    status:InquiryStatus="PENDING"
    self_will_decision_ref:WorldRecordRef|None=None
    resulting_observation_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    revision:int=Field(default=1,ge=1,le=9_007_199_254_740_991,strict=True)
    supersedes_inquiry_sha256:Sha256|None=None
    created_at_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    expires_at_ms:int|None=Field(default=None,ge=0,le=9_007_199_254_740_991,strict=True)
    authorization:Literal["NONE"]="NONE"
    may_execute:Literal[False]=False
    may_call_tools:Literal[False]=False
    may_authorize:Literal[False]=False
    empirical_evidence_weight_milli:Literal[0]=0
    inquiry_sha256:Sha256
    _subjects=field_validator("subject_refs")(sorted_unique_refs)
    _reasons=field_validator("reason_codes")(sorted_unique_strings)
    _missing=field_validator("missing_evidence_types")(sorted_unique_strings)
    _support=field_validator("supporting_refs")(sorted_unique_refs)
    _conflicts=field_validator("conflict_refs")(sorted_unique_refs)
    _mods=field_validator("suggested_observation_modalities")(sorted_unique_strings)
    _cog=field_validator("source_cognition_refs")(sorted_unique_refs)
    _results=field_validator("resulting_observation_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def check(self)->Self:
        if (self.revision==1)!=(self.supersedes_inquiry_sha256 is None): raise ValueError("inquiry revision lineage invalid")
        if self.expires_at_ms is not None and self.expires_at_ms<self.created_at_ms: raise ValueError("inquiry expiry precedes creation")
        if self.generation==0 and self.parent_inquiry_id is not None: raise ValueError("genesis inquiry cannot have a parent")
        if self.generation>0 and self.parent_inquiry_id is None: raise ValueError("child inquiry requires parent inquiry id")
        if self.inquiry_id!=derive_inquiry_id(world_scope_hash=self.scope.world_scope_hash,question=self.question,knowledge_gap_id=self.knowledge_gap_id,subject_refs=self.subject_refs): raise ValueError("inquiry stable id mismatch")
        return self

def derive_inquiry_outcome_id(*,inquiry_id:str,self_will_decision:str,closed_at_ms:int,resulting_source_envelope_refs:tuple[WorldRecordRef,...])->str:
    return "wiout_"+canonical_sha256({"domain":"tiangong.world.inquiry-outcome-id.v1","inquiry_id":inquiry_id,"self_will_decision":self_will_decision,"closed_at_ms":closed_at_ms,"resulting_source_envelope_refs":[x.model_dump(mode="json") for x in resulting_source_envelope_refs]})

class InquiryOutcome(HashedWorldContract):
    _hash_field="outcome_sha256"
    outcome_id:InquiryOutcomeId
    inquiry_id:InquiryId
    self_will_decision:SelfWillDecision
    autonomous_intent_id:OpaqueId|None=None
    run_id:OpaqueId|None=None
    execution_ticket_id:OpaqueId|None=None
    resulting_source_envelope_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    observation_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    evidence_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    resolved:bool
    residual_gap_milli:int=Field(ge=0,le=1000,strict=True)
    information_gain_milli:int=Field(ge=0,le=1000,strict=True)
    changed_cognition_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    changed_world_state_refs:tuple[WorldRecordRef,...]=Field(default=(),max_length=4096)
    closed_at_ms:int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    empirical_evidence_weight_milli:Literal[0]=0
    evidence_authority:Literal["none"]="none"
    may_authorize:Literal[False]=False
    may_execute:Literal[False]=False
    outcome_sha256:Sha256
    _source=field_validator("resulting_source_envelope_refs")(sorted_unique_refs)
    _obs=field_validator("observation_refs")(sorted_unique_refs)
    _evidence=field_validator("evidence_refs")(sorted_unique_refs)
    _cog=field_validator("changed_cognition_refs")(sorted_unique_refs)
    _state=field_validator("changed_world_state_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def check(self)->Self:
        if self.resolved and self.self_will_decision=="ACCEPT" and not (self.resulting_source_envelope_refs or self.observation_refs or self.evidence_refs): raise ValueError("resolved accepted inquiry requires independent reality results")
        if self.outcome_id!=derive_inquiry_outcome_id(inquiry_id=self.inquiry_id,self_will_decision=self.self_will_decision,closed_at_ms=self.closed_at_ms,resulting_source_envelope_refs=self.resulting_source_envelope_refs): raise ValueError("inquiry outcome id mismatch")
        return self
