"""Three-time world semantics: valid, observed, recorded."""
from __future__ import annotations
from typing import Self
from pydantic import Field, model_validator
from ._base import MAX_SAFE_INTEGER, WorldContractModel

class WorldTime(WorldContractModel):
    valid_from_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    valid_until_ms: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER, strict=True)
    observed_at_ms: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER, strict=True)
    recorded_at_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)

    @model_validator(mode="after")
    def validate_ordering(self) -> Self:
        if self.valid_until_ms is not None and self.valid_until_ms < self.valid_from_ms:
            raise ValueError("valid time interval is inverted")
        if self.observed_at_ms is not None and self.observed_at_ms > self.recorded_at_ms:
            raise ValueError("observation cannot occur after recording")
        return self
