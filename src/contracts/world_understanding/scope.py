"""World scope partition contract."""
from __future__ import annotations
from typing import Self
from pydantic import Field, field_validator, model_validator
from ._base import PrivacyScope, WorldContractModel, WorldId, normalized_text
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

class ScopeBinding(WorldContractModel):
    key: OpaqueId
    value: str = Field(min_length=1, max_length=2048)
    _validate_value = field_validator("value")(normalized_text)


def derive_world_id(*, life_id: str, namespace_anchor: str) -> str:
    return "wld_" + canonical_sha256({
        "domain": "tiangong.world.namespace-id.v1",
        "life_id": life_id,
        "namespace_anchor": namespace_anchor,
    })


def derive_world_scope_hash(*, life_id: str, world_id: str, domain_id: str, scope_bindings: tuple[ScopeBinding, ...]) -> str:
    return canonical_sha256({
        "domain": "tiangong.world.scope.v1",
        "life_id": life_id,
        "world_id": world_id,
        "domain_id": domain_id,
        "scope_bindings": [item.model_dump(mode="json") for item in scope_bindings],
    })


class WorldScope(WorldContractModel):
    life_id: OpaqueId
    world_id: WorldId
    domain_id: OpaqueId
    scope_bindings: tuple[ScopeBinding, ...] = Field(default=(), max_length=64)
    world_scope_hash: Sha256
    principal_scope_hash: Sha256
    privacy_scope: PrivacyScope

    @field_validator("scope_bindings")
    @classmethod
    def validate_binding_keys(cls, value: tuple[ScopeBinding, ...]) -> tuple[ScopeBinding, ...]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("scope binding keys may not repeat")
        return value

    @model_validator(mode="after")
    def validate_scope_hash(self) -> Self:
        expected = derive_world_scope_hash(
            life_id=self.life_id,
            world_id=self.world_id,
            domain_id=self.domain_id,
            scope_bindings=self.scope_bindings,
        )
        if self.world_scope_hash != expected:
            raise ValueError("world_scope_hash does not match scope identity")
        return self
