"""Observability quality is explicit; absence is never inferred from no observation."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, model_validator
from ._base import WorldContractModel
from ..models import Sha256

ObservabilityMode = Literal["OBSERVED", "PARTIAL", "NOT_OBSERVED", "UNOBSERVABLE"]


def compute_observability_quality_milli(*, access_milli: int, scope_coverage_milli: int, time_coverage_milli: int, adapter_quality_milli: int, measurement_quality_milli: int) -> int:
    numerator = access_milli * scope_coverage_milli * time_coverage_milli * adapter_quality_milli * measurement_quality_milli
    return numerator // (1000 ** 4)


class ObservabilityState(WorldContractModel):
    mode: ObservabilityMode
    access_milli: int = Field(ge=0, le=1000, strict=True)
    scope_coverage_milli: int = Field(ge=0, le=1000, strict=True)
    time_coverage_milli: int = Field(ge=0, le=1000, strict=True)
    adapter_quality_milli: int = Field(ge=0, le=1000, strict=True)
    measurement_quality_milli: int = Field(ge=0, le=1000, strict=True)
    combined_quality_milli: int = Field(ge=0, le=1000, strict=True)
    search_scope_hash: Sha256 | None = None

    @model_validator(mode="after")
    def validate_quality(self) -> Self:
        expected = compute_observability_quality_milli(
            access_milli=self.access_milli,
            scope_coverage_milli=self.scope_coverage_milli,
            time_coverage_milli=self.time_coverage_milli,
            adapter_quality_milli=self.adapter_quality_milli,
            measurement_quality_milli=self.measurement_quality_milli,
        )
        if self.combined_quality_milli != expected:
            raise ValueError("combined observability quality must be deterministic")
        if self.mode in {"NOT_OBSERVED", "UNOBSERVABLE"} and self.combined_quality_milli != 0:
            raise ValueError("unobserved states cannot claim positive observation quality")
        return self
