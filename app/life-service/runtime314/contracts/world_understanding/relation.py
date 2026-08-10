"""World Graph relation contract; derivation lineage stays separate."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import EpistemicState, HashedWorldContract, RelationId, TruthState, WorldRecordRef, WorldValue, sorted_unique_refs
from .scope import WorldScope
from .time import WorldTime
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

RelationMaterializationClass = Literal["STRUCTURAL", "MATERIALIZED", "DERIVED_CACHE", "COGNIZED", "EPHEMERAL"]
RelationExtractionMode = Literal["deterministic", "observed", "inferred", "model_assisted", "migration"]

def derive_relation_id(*, world_scope_hash: str, subject_ref: WorldRecordRef, predicate: str, value: WorldValue, condition_sha256: str | None) -> str:
    return "wrel_" + canonical_sha256({"domain": "tiangong.world.relation-slot-id.v1", "world_scope_hash": world_scope_hash, "subject_ref": subject_ref.model_dump(mode="json"), "predicate": predicate, "value": value.model_dump(mode="json"), "condition_sha256": condition_sha256})

class WorldRelation(HashedWorldContract):
    _hash_field = "relation_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    relation_id: RelationId
    scope: WorldScope
    subject_ref: WorldRecordRef
    predicate: OpaqueId
    value: WorldValue
    condition_ref: WorldRecordRef | None = None
    condition_sha256: Sha256 | None = None
    extraction_mode: RelationExtractionMode
    materialization_class: RelationMaterializationClass
    source_observation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    derivation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    truth_state: TruthState
    epistemic_state: EpistemicState
    empirical_evidence_weight_milli: int = Field(default=0, ge=0, le=1000, strict=True)
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    revision: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    supersedes_relation_sha256: Sha256 | None = None
    time: WorldTime
    relation_sha256: Sha256
    _validate_observations = field_validator("source_observation_refs")(sorted_unique_refs)
    _validate_derivations = field_validator("derivation_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def validate_relation(self) -> Self:
        if (self.condition_ref is None) != (self.condition_sha256 is None):
            raise ValueError("relation condition binding must be all-or-none")
        if self.condition_ref is not None and self.condition_ref.sha256 != self.condition_sha256:
            raise ValueError("relation condition hash mismatch")
        if (self.revision == 1) != (self.supersedes_relation_sha256 is None):
            raise ValueError("relation revision lineage invalid")
        if self.extraction_mode == "model_assisted" and self.empirical_evidence_weight_milli != 0:
            raise ValueError("model-assisted relation has zero empirical evidence weight")
        if self.relation_id != derive_relation_id(world_scope_hash=self.scope.world_scope_hash, subject_ref=self.subject_ref, predicate=self.predicate, value=self.value, condition_sha256=self.condition_sha256):
            raise ValueError("relation stable slot id mismatch")
        return self
