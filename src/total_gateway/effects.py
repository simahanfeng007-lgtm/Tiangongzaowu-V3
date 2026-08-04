"""Generic immutable effect claims and machine-fact results."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import CONTRACT_SCHEMA_VERSION, canonical_sha256, derive_effect_identity


class EffectClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    run_sequence: int = Field(ge=1)
    generation: int = Field(ge=0)
    effect_kind: Literal["execution", "artifact", "delivery", "control"]
    ordinal: int = Field(ge=0)
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pipeline_version: str = Field(default="unspecified", min_length=1, max_length=160)
    attempt: int = Field(default=1, ge=1)
    claim_revision: int = Field(default=1, ge=1)
    lease_epoch: int = Field(default=1, ge=1)
    supersedes_claim_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    owner_component_id: Literal[
        "tiangong-total-gateway",
        "tiangong-backend",
        "tiangong-life-service",
        "tiangong-communication-service",
    ]
    claimed_at_ms: int = Field(ge=0)
    claim_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_effect_identity(self) -> Self:
        expected = derive_effect_identity(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=self.run_sequence,
            generation=self.generation,
            effect_kind=self.effect_kind,
            ordinal=self.ordinal,
            intent_sha256=self.intent_sha256,
        )
        if expected.effect_id != self.effect_id:
            raise ValueError("effect ID is not bound to its immutable intent")
        return self

    @model_validator(mode="after")
    def validate_revision_chain(self) -> Self:
        if (self.claim_revision == 1) != (self.supersedes_claim_sha256 is None):
            raise ValueError("effect claim revision chain is invalid")
        return self

    _VOLD_FIELDS = frozenset({
        "pipeline_version", "attempt", "claim_revision",
        "lease_epoch", "supersedes_claim_sha256",
    })
    _VOLD_HASH_EXCLUDES = _VOLD_FIELDS | {"claim_sha256"}

    def _is_vold_shape(self) -> bool:
        """vNext 字段全部为默认值时按 vOld 字段集计算哈希（历史行兼容）。"""
        return (
            self.pipeline_version == "unspecified"
            and self.attempt == 1
            and self.claim_revision == 1
            and self.lease_epoch == 1
            and self.supersedes_claim_sha256 is None
        )

    def computed_sha256(self) -> str:
        if self._is_vold_shape():
            return canonical_sha256(self.model_dump(mode="json", exclude=self._VOLD_HASH_EXCLUDES))
        return canonical_sha256(self.model_dump(mode="json", exclude={"claim_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.claim_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"claim_sha256": self.computed_sha256()})


class EffectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    result_id: str = Field(min_length=1, max_length=160)
    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    status: Literal["SUCCEEDED", "FAILED_FINAL", "AMBIGUOUS", "RECONCILED"]
    fact_id: str = Field(min_length=1, max_length=160)
    result_object_id: str | None = Field(default=None, min_length=1, max_length=160)
    result_object_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, min_length=1, max_length=160)
    observed_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if (self.result_object_id is None) != (self.result_object_sha256 is None):
            raise ValueError("effect result object identity must be complete")
        if self.status in {"FAILED_FINAL", "AMBIGUOUS"} and self.error_code is None:
            raise ValueError("failed or ambiguous effect requires an error code")
        if self.status in {"SUCCEEDED", "RECONCILED"} and self.error_code is not None:
            raise ValueError("successful effect cannot carry an error code")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.result_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"result_sha256": self.computed_sha256()})


__all__ = ["EffectClaim", "EffectResult"]
