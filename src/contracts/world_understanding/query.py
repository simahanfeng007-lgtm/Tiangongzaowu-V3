"""Control-only query contract carried inside CONTEXT_REQUEST ingress."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, WorldQueryId, WorldRecordRef, sorted_unique_refs
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

def derive_world_query_id(*, world_scope_hash: str, correlation_id: str, task_ref: str, task_sha256: str, focus: str, created_at_ms: int) -> str:
    return "wqry_" + canonical_sha256({"domain": "tiangong.world.query-id.v1", "world_scope_hash": world_scope_hash, "correlation_id": correlation_id, "task_ref": task_ref, "task_sha256": task_sha256, "focus": focus, "created_at_ms": created_at_ms})

class WorldQuery(HashedWorldContract):
    _hash_field = "query_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    query_id: WorldQueryId
    correlation_id: OpaqueId
    scope: WorldScope
    frame_ref: WorldRecordRef | None = None
    basis_world_state_ref: WorldRecordRef | None = None
    task_ref: OpaqueId
    task_sha256: Sha256
    focus: str = Field(min_length=1, max_length=20_000)
    required_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    token_budget: int = Field(ge=128, le=1_000_000, strict=True)
    requested_depth: Literal["L0", "L1", "L2"] = "L0"
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    context_only: Literal[True] = True
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    empirical_evidence_weight_milli: Literal[0] = 0
    query_sha256: Sha256
    _validate_refs = field_validator("required_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def validate_query_id(self) -> Self:
        if self.query_id != derive_world_query_id(world_scope_hash=self.scope.world_scope_hash, correlation_id=self.correlation_id, task_ref=self.task_ref, task_sha256=self.task_sha256, focus=self.focus, created_at_ms=self.created_at_ms):
            raise ValueError("world query id mismatch")
        return self
