"""L6 coherent materialized WorldState head bound to a WorldCut."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, WorldRecordRef, WorldStateId, sorted_unique_refs
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

class WorldState(HashedWorldContract):
    _hash_field = "state_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    world_state_id: WorldStateId
    scope: WorldScope
    frame_ref: WorldRecordRef
    world_cut_ref: WorldRecordRef
    world_sequence: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    observation_cutoff_ref: WorldRecordRef | None = None
    entity_head_manifest_ref: WorldRecordRef
    relation_head_manifest_ref: WorldRecordRef
    cognition_head_manifest_ref: WorldRecordRef | None = None
    active_hypothesis_manifest_ref: WorldRecordRef | None = None
    delta_manifest_ref: WorldRecordRef | None = None
    unresolved_conflict_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    stale_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    materialized_at_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    source_transaction_id: OpaqueId
    empirical_evidence_weight_milli: Literal[0] = 0
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    state_sha256: Sha256
    _validate_conflicts = field_validator("unresolved_conflict_refs")(sorted_unique_refs)
    _validate_stale = field_validator("stale_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def validate_state_id(self) -> Self:
        expected = "wst_" + canonical_sha256({"domain": "tiangong.world.state-id.v1", "world_scope_hash": self.scope.world_scope_hash, "world_cut_ref": self.world_cut_ref.model_dump(mode="json"), "world_sequence": self.world_sequence, "source_transaction_id": self.source_transaction_id})
        if self.world_state_id != expected:
            raise ValueError("world state id mismatch")
        return self
