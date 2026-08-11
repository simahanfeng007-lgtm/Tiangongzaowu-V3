"""Bounded post-commit projection of authoritative Life learning reality.

The record is deliberately small: it describes a committed Life artifact or
capability transition without copying private memory, credentials, prompts, or
artifact bodies into World Understanding.
"""
from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ..models import OpaqueId, Sha256
from ._base import HashedWorldContract, MAX_SAFE_INTEGER, normalized_text, sorted_unique_strings

LifeArtifactKind = Literal["knowledge", "skill", "tool"]
LifeLearningStatus = Literal[
    "published",
    "pending_activation",
    "activated",
    "patched",
    "patch_settled",
    "degraded",
    "rolled_back",
    "disabled",
]
LifeLearningEpistemicStatus = Literal["observed", "verified"]


class LifeLearningObservation(HashedWorldContract):
    """Safe self-reality emitted only after the native Life commit succeeds."""

    _hash_field = "observation_sha256"

    life_id: OpaqueId
    learning_id: OpaqueId | None = None
    artifact_id: OpaqueId
    artifact_kind: LifeArtifactKind
    lineage_id: OpaqueId
    status: LifeLearningStatus
    learned_subject_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=64)
    safe_summary: str = Field(default="", max_length=1_000)
    evidence_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    confidence_milli: int = Field(ge=0, le=1_000, strict=True)
    epistemic_status: LifeLearningEpistemicStatus
    prior_revision: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    new_revision: int = Field(ge=1, le=MAX_SAFE_INTEGER, strict=True)
    occurred_at_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    observation_sha256: Sha256

    _validate_summary = field_validator("safe_summary")(normalized_text)

    @field_validator("learned_subject_refs", "evidence_refs")
    @classmethod
    def validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return sorted_unique_strings(value)

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        if self.new_revision <= self.prior_revision:
            raise ValueError("life learning revision did not advance")
        return self


__all__ = [
    "LifeArtifactKind",
    "LifeLearningEpistemicStatus",
    "LifeLearningObservation",
    "LifeLearningStatus",
]
