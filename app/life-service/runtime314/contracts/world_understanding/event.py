"""Typed world event record. Event structure is not itself semantic cognition."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, WorldEventId, WorldRecordRef, sorted_unique_refs
from .scope import WorldScope
from .source import WorldSourceRef
from .time import WorldTime
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

def derive_world_event_id(*, world_scope_hash: str, event_kind: str, subject_refs: tuple[WorldRecordRef, ...], source_refs: tuple[WorldSourceRef, ...], sequence: int | None, time: WorldTime) -> str:
    return "wevt_" + canonical_sha256({"domain": "tiangong.world.event-id.v1", "world_scope_hash": world_scope_hash, "event_kind": event_kind, "subject_refs": [item.model_dump(mode="json") for item in subject_refs], "source_refs": [item.model_dump(mode="json") for item in source_refs], "sequence": sequence, "time": time.model_dump(mode="json")})

class WorldEvent(HashedWorldContract):
    _hash_field = "event_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    event_id: WorldEventId
    scope: WorldScope
    event_kind: OpaqueId
    subject_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    source_refs: tuple[WorldSourceRef, ...] = Field(default=(), max_length=4096)
    frame_ref: WorldRecordRef | None = None
    sequence: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991, strict=True)
    time: WorldTime
    empirical_evidence_weight_milli: int = Field(default=0, ge=0, le=1000, strict=True)
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    event_sha256: Sha256
    _validate_subjects = field_validator("subject_refs")(sorted_unique_refs)
    @field_validator("source_refs")
    @classmethod
    def validate_sources(cls, value: tuple[WorldSourceRef, ...]) -> tuple[WorldSourceRef, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("event source refs must be sorted and unique")
        return value
    @model_validator(mode="after")
    def validate_event_id(self) -> Self:
        if self.event_id != derive_world_event_id(world_scope_hash=self.scope.world_scope_hash, event_kind=self.event_kind, subject_refs=self.subject_refs, source_refs=self.source_refs, sequence=self.sequence, time=self.time):
            raise ValueError("world event id mismatch")
        return self
