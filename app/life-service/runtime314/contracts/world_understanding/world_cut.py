"""WorldCut: a consistent set of source watermarks used by WorldState."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, WorldCutId
from .scope import WorldScope
from .source import SourceKind
from .time import WorldTime
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

class SourceWatermark(HashedWorldContract):
    _hash_field = "watermark_sha256"
    source_kind: SourceKind
    watermark_type: OpaqueId
    watermark_value: str = Field(min_length=1, max_length=4096)
    sequence: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991, strict=True)
    watermark_sha256: Sha256
    def sort_key(self) -> tuple[str, str, str, int]:
        return (self.source_kind, self.watermark_type, self.watermark_value, self.sequence or -1)

def derive_world_cut_id(*, world_scope_hash: str, watermarks: tuple[SourceWatermark, ...]) -> str:
    return "wcut_" + canonical_sha256({"domain": "tiangong.world.cut-id.v1", "world_scope_hash": world_scope_hash, "watermarks": [item.model_dump(mode="json") for item in watermarks]})

class WorldCut(HashedWorldContract):
    _hash_field = "cut_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    cut_id: WorldCutId
    scope: WorldScope
    source_watermarks: tuple[SourceWatermark, ...] = Field(default=(), max_length=256)
    time: WorldTime
    cut_sha256: Sha256
    @field_validator("source_watermarks")
    @classmethod
    def validate_watermarks(cls, value: tuple[SourceWatermark, ...]) -> tuple[SourceWatermark, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("source watermarks must be sorted and unique")
        return value
    @model_validator(mode="after")
    def validate_id(self) -> Self:
        if self.cut_id != derive_world_cut_id(world_scope_hash=self.scope.world_scope_hash, watermarks=self.source_watermarks):
            raise ValueError("WorldCut id mismatch")
        return self
