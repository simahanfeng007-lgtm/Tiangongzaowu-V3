"""Canonical identity helpers shared by all World Understanding layers."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding.scope import WorldScope

@dataclass(frozen=True, slots=True)
class ScopeIdentity:
    life_id: str
    world_id: str
    world_scope_hash: str
    principal_scope_hash: str

    @classmethod
    def from_scope(cls, scope: WorldScope) -> "ScopeIdentity":
        return cls(scope.life_id, scope.world_id, scope.world_scope_hash, scope.principal_scope_hash)

    @property
    def identity_sha256(self) -> str:
        return canonical_sha256({
            "domain": "tiangong.world.scope-identity.v1",
            "life_id": self.life_id,
            "world_id": self.world_id,
            "world_scope_hash": self.world_scope_hash,
            "principal_scope_hash": self.principal_scope_hash,
        })


def same_scope_identity(left: WorldScope, right: WorldScope) -> bool:
    return ScopeIdentity.from_scope(left) == ScopeIdentity.from_scope(right)

__all__ = ["ScopeIdentity", "same_scope_identity"]
