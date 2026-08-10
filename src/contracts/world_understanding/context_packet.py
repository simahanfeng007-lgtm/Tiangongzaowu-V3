"""L8 projection contracts. These objects are context-only and cannot authorize execution."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import EpistemicState, ExpansionHandleId, HashedWorldContract, PrivacyScope, TruthState, WorldPacketId, WorldRecordRef, sorted_unique_refs
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

def derive_expansion_handle_id(*, target_refs: tuple[WorldRecordRef, ...], allowed_depth: str, scope_hash: str, principal_scope_hash: str, privacy_scope: str, expires_at_ms: int) -> str:
    return "wexp_" + canonical_sha256({"domain":"tiangong.world.expansion-handle-id.v1","target_refs":[x.model_dump(mode="json") for x in target_refs],"allowed_depth":allowed_depth,"scope_hash":scope_hash,"principal_scope_hash":principal_scope_hash,"privacy_scope":privacy_scope,"expires_at_ms":expires_at_ms})

def derive_world_packet_id(*, world_scope_hash: str, frame_ref: WorldRecordRef, basis_world_state_ref: WorldRecordRef | None, task_ref: str, task_sha256: str, generated_at_ms: int, projection_policy_sha256: str) -> str:
    return "wcp_" + canonical_sha256({"domain":"tiangong.world.context-packet-id.v1","world_scope_hash":world_scope_hash,"frame_ref":frame_ref.model_dump(mode="json"),"basis_world_state_ref":None if basis_world_state_ref is None else basis_world_state_ref.model_dump(mode="json"),"task_ref":task_ref,"task_sha256":task_sha256,"generated_at_ms":generated_at_ms,"projection_policy_sha256":projection_policy_sha256})

class ExpansionHandle(HashedWorldContract):
    _hash_field="handle_sha256"
    handle_id: ExpansionHandleId
    target_refs: tuple[WorldRecordRef,...]=Field(min_length=1,max_length=4096)
    allowed_depth: Literal["L0","L1","L2"]
    scope_hash: Sha256
    principal_scope_hash: Sha256
    privacy_scope: PrivacyScope
    expires_at_ms: int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    context_only: Literal[True]=True
    authorizes: Literal[False]=False
    may_execute: Literal[False]=False
    handle_sha256: Sha256
    _refs=field_validator("target_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def check(self)->Self:
        if self.handle_id != derive_expansion_handle_id(target_refs=self.target_refs,allowed_depth=self.allowed_depth,scope_hash=self.scope_hash,principal_scope_hash=self.principal_scope_hash,privacy_scope=self.privacy_scope,expires_at_ms=self.expires_at_ms): raise ValueError("expansion handle id mismatch")
        return self

class WorldContextItem(HashedWorldContract):
    _hash_field="item_sha256"
    item_id: OpaqueId
    item_kind: OpaqueId
    summary: str=Field(min_length=1,max_length=20_000)
    referenced_world_records: tuple[WorldRecordRef,...]=Field(min_length=1,max_length=4096)
    truth_state: TruthState|None=None
    epistemic_state: EpistemicState|None=None
    cognition_stability: OpaqueId|None=None
    task_relevance_milli: int=Field(ge=0,le=1000,strict=True)
    impact_milli: int=Field(ge=0,le=1000,strict=True)
    freshness_need_milli: int=Field(ge=0,le=1000,strict=True)
    mandatory: bool=False
    expansion_handle_id: ExpansionHandleId|None=None
    context_only: Literal[True]=True
    authorizes: Literal[False]=False
    empirical_evidence_weight_milli: Literal[0]=0
    item_sha256: Sha256
    _refs=field_validator("referenced_world_records")(sorted_unique_refs)

class WorldContextPacket(HashedWorldContract):
    _hash_field="packet_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"]="tiangong.world-understanding.contracts.v1"
    packet_id: WorldPacketId
    scope: WorldScope
    frame_ref: WorldRecordRef
    basis_world_state_ref: WorldRecordRef|None=None
    task_ref: OpaqueId
    task_sha256: Sha256
    generated_at_ms: int=Field(ge=0,le=9_007_199_254_740_991,strict=True)
    token_budget: int=Field(ge=128,le=1_000_000,strict=True)
    mandatory_items: tuple[WorldContextItem,...]=()
    ranked_items: tuple[WorldContextItem,...]=()
    uncertainty_items: tuple[WorldContextItem,...]=()
    prediction_items: tuple[WorldContextItem,...]=()
    evidence_digest: tuple[WorldRecordRef,...]=()
    expansion_handles: tuple[ExpansionHandle,...]=()
    overflow_state: Literal["NONE","BUDGET_TRUNCATED","MANDATORY_OVERFLOW"]="NONE"
    projection_policy_ref: OpaqueId
    projection_policy_sha256: Sha256
    projection_authority: Literal["context_only"]="context_only"
    context_only: Literal[True]=True
    authorizes: Literal[False]=False
    confirms: Literal[False]=False
    changes_risk: Literal[False]=False
    may_execute: Literal[False]=False
    empirical_evidence_weight_milli: Literal[0]=0
    packet_sha256: Sha256
    _evidence=field_validator("evidence_digest")(sorted_unique_refs)
    @model_validator(mode="after")
    def check(self)->Self:
        if any(not x.mandatory for x in self.mandatory_items): raise ValueError("mandatory item flag missing")
        if any(x.mandatory for x in self.ranked_items): raise ValueError("ranked item cannot be mandatory")
        handles={x.handle_id for x in self.expansion_handles}
        for group in (self.mandatory_items,self.ranked_items,self.uncertainty_items,self.prediction_items):
            for item in group:
                if item.expansion_handle_id is not None and item.expansion_handle_id not in handles: raise ValueError("missing expansion handle")
        if self.packet_id != derive_world_packet_id(world_scope_hash=self.scope.world_scope_hash,frame_ref=self.frame_ref,basis_world_state_ref=self.basis_world_state_ref,task_ref=self.task_ref,task_sha256=self.task_sha256,generated_at_ms=self.generated_at_ms,projection_policy_sha256=self.projection_policy_sha256): raise ValueError("world context packet id mismatch")
        return self
