"""Stable entity identity and identity-resolution candidate contracts."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import EntityCandidateId, EntityId, EpistemicState, HashedWorldContract, TruthState, WorldRecordRef, WorldValue, sorted_unique_refs, sorted_unique_strings
from .scope import WorldScope
from .time import WorldTime
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

EntityLifecycle = Literal["ACTIVE", "MERGED", "SPLIT", "RETIRED"]
EntityResolutionState = Literal["RESOLVED", "AMBIGUOUS", "MERGE_CANDIDATE", "SPLIT_CANDIDATE"]

class WorldAttribute(HashedWorldContract):
    _hash_field = "attribute_sha256"
    key: OpaqueId
    value: WorldValue
    attribute_sha256: Sha256

def derive_entity_id(*, life_id: str, domain_id: str, identity_anchor_hash: str) -> str:
    return "went_" + canonical_sha256({"domain": "tiangong.world.entity-id.v1", "life_id": life_id, "domain_id": domain_id, "identity_anchor_hash": identity_anchor_hash})

class WorldEntity(HashedWorldContract):
    _hash_field = "entity_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    entity_id: EntityId
    scope: WorldScope
    entity_type: OpaqueId
    identity_anchor_hash: Sha256
    canonical_name: str = Field(min_length=1, max_length=4096)
    aliases: tuple[str, ...] = Field(default=(), max_length=4096)
    attributes: tuple[WorldAttribute, ...] = Field(default=(), max_length=4096)
    location_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    source_observation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    truth_state: TruthState
    epistemic_state: EpistemicState
    lifecycle: EntityLifecycle
    replacement_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=256)
    revision: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    supersedes_entity_sha256: Sha256 | None = None
    time: WorldTime
    entity_sha256: Sha256
    _validate_aliases = field_validator("aliases")(sorted_unique_strings)
    _validate_locations = field_validator("location_refs")(sorted_unique_refs)
    _validate_observations = field_validator("source_observation_refs")(sorted_unique_refs)
    _validate_replacements = field_validator("replacement_refs")(sorted_unique_refs)
    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: tuple[WorldAttribute, ...]) -> tuple[WorldAttribute, ...]:
        keys = tuple(item.key for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("entity attribute keys must be sorted and unique")
        return value
    @model_validator(mode="after")
    def validate_entity(self) -> Self:
        if self.entity_id != derive_entity_id(life_id=self.scope.life_id, domain_id=self.scope.domain_id, identity_anchor_hash=self.identity_anchor_hash):
            raise ValueError("entity stable id mismatch")
        if (self.revision == 1) != (self.supersedes_entity_sha256 is None):
            raise ValueError("entity revision lineage invalid")
        if self.lifecycle in {"MERGED", "SPLIT"} and not self.replacement_refs:
            raise ValueError("merged/split entity requires replacement refs")
        if self.lifecycle == "ACTIVE" and self.replacement_refs:
            raise ValueError("active entity cannot have replacement refs")
        return self

def derive_entity_candidate_id(*, world_scope_hash: str, state: str, basis_refs: tuple[WorldRecordRef, ...], candidate_entity_refs: tuple[WorldRecordRef, ...]) -> str:
    return "werc_" + canonical_sha256({"domain": "tiangong.world.entity-resolution-candidate-id.v1", "world_scope_hash": world_scope_hash, "state": state, "basis_refs": [item.model_dump(mode="json") for item in basis_refs], "candidate_entity_refs": [item.model_dump(mode="json") for item in candidate_entity_refs]})

class EntityResolutionCandidate(HashedWorldContract):
    _hash_field = "candidate_sha256"
    candidate_id: EntityCandidateId
    scope: WorldScope
    state: EntityResolutionState
    basis_refs: tuple[WorldRecordRef, ...] = Field(min_length=1, max_length=4096)
    candidate_entity_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=256)
    resolution_score_milli: int = Field(ge=0, le=1000, strict=True)
    reason_codes: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    empirical_evidence_weight_milli: Literal[0] = 0
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    candidate_sha256: Sha256
    _validate_basis = field_validator("basis_refs")(sorted_unique_refs)
    _validate_candidates = field_validator("candidate_entity_refs")(sorted_unique_refs)
    _validate_reasons = field_validator("reason_codes")(sorted_unique_strings)
    @model_validator(mode="after")
    def validate_candidate_id(self) -> Self:
        if self.candidate_id != derive_entity_candidate_id(world_scope_hash=self.scope.world_scope_hash, state=self.state, basis_refs=self.basis_refs, candidate_entity_refs=self.candidate_entity_refs):
            raise ValueError("entity resolution candidate id mismatch")
        return self
