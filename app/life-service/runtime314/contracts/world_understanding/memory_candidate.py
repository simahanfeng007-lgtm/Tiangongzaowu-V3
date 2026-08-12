"""Memory -> World candidate contracts (P15 M7).

A MemoryWorldCandidate is candidate *evidence intake* for World Understanding,
never a fact upgrade and never a verified WorldPatch.  MEMORY DirectKnown
authority stays zero; only the existing World Cognition evidence/stability
machinery may promote stable candidates into WorldState.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from ._base import (
    MAX_SAFE_INTEGER,
    WORLD_SCHEMA_BASE,
    WorldContractModel,
    normalized_text,
    sorted_unique_strings,
)
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256


MemoryWorldCandidateId = Annotated[
    str, StringConstraints(pattern=r"^wmc_[0-9a-f]{64}$")
]

MemoryWorldCandidateEpistemicStatus = Literal[
    "observed", "user_asserted", "verified"
]
MemoryWorldCandidateVolatilityClass = Literal[
    "transient", "short", "medium", "long", "structural"
]
MemoryWorldCandidatePrivacyScope = Literal[
    "public", "relationship", "private", "secret", "system"
]


def derive_memory_world_candidate_id(
    *,
    life_id: str,
    derivation_id: str,
    policy_version: str,
) -> str:
    return "wmc_" + canonical_sha256(
        {
            "domain": "tiangong.world.memory-candidate-id.v1",
            "life_id": life_id,
            "derivation_id": derivation_id,
            "policy_version": policy_version,
        }
    )


def derive_memory_lineage_root_hash(event_id: str) -> str:
    """Map a Life event id onto the World cognition lineage-root space."""

    return canonical_sha256(
        {
            "domain": "tiangong.world.memory-lineage-root.v1",
            "event_id": event_id,
        }
    )


class MemoryWorldCandidate(WorldContractModel):
    """One candidate projection of a mature memory for World Understanding."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{WORLD_SCHEMA_BASE}:MemoryWorldCandidate",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.world.memory-candidate.v1"] = (
        "tiangong.world.memory-candidate.v1"
    )
    candidate_id: MemoryWorldCandidateId
    life_id: OpaqueId
    world_scope_hash: Sha256
    principal_scope_hash: Sha256
    source_memory_id: OpaqueId
    source_memory_revision: int = Field(ge=1, le=MAX_SAFE_INTEGER, strict=True)
    source_assertion_sha256: Sha256
    source_derivation_id: OpaqueId
    source_layer: Literal[
        "L3_EXPERIENCE", "L4_EXPLICIT", "L5_CORE"
    ]
    claim_key: str
    semantic_payload: str = Field(min_length=1, max_length=20_000)
    evidence_refs: tuple[Sha256, ...] = Field(default=(), max_length=256)
    lineage_root_hashes: tuple[Sha256, ...] = Field(
        min_length=1, max_length=256
    )
    epistemic_status: MemoryWorldCandidateEpistemicStatus
    confidence_milli: int = Field(ge=0, le=1000, strict=True)
    volatility_class: MemoryWorldCandidateVolatilityClass
    valid_from_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    valid_until_ms: int | None = Field(
        default=None, ge=0, le=MAX_SAFE_INTEGER, strict=True
    )
    privacy_scope: MemoryWorldCandidatePrivacyScope
    candidate_sha256: Sha256

    _lineage = field_validator("lineage_root_hashes")(sorted_unique_strings)
    _evidence = field_validator("evidence_refs")(sorted_unique_strings)

    @field_validator("claim_key", "semantic_payload")
    @classmethod
    def _text(cls, value: str) -> str:
        return normalized_text(value)

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if (
            self.valid_until_ms is not None
            and self.valid_until_ms < self.valid_from_ms
        ):
            raise ValueError(
                "memory world candidate validity interval is inverted"
            )
        if self.privacy_scope == "secret":
            raise ValueError("secret memory can never become a world candidate")
        return self

    def computed_candidate_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"candidate_sha256"})
        )

    def has_valid_candidate_sha256(self) -> bool:
        return self.candidate_sha256 == self.computed_candidate_sha256()

    def with_computed_candidate_sha256(self) -> Self:
        return self.model_copy(
            update={"candidate_sha256": self.computed_candidate_sha256()}
        )


__all__ = [
    "MemoryWorldCandidate",
    "MemoryWorldCandidateEpistemicStatus",
    "MemoryWorldCandidateId",
    "MemoryWorldCandidatePrivacyScope",
    "MemoryWorldCandidateVolatilityClass",
    "derive_memory_lineage_root_hash",
    "derive_memory_world_candidate_id",
]
