"""Memory layer / derivation contracts for the P15 memory SSoT closure.

``MemoryAssertionV3`` continues to describe what a memory assertion *means*.
This module adds the append-only derivation metadata that records why a
memory revision exists at a particular maturity layer (L1..L5), for which
semantic domain and principal, and how it inherits lineage from its parents.
The derivation graph is deterministic: every digest is canonical JSON and
every set-like field is sorted and unique, so hashes replay identically on
Windows and Linux.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .life import LifeEventId
from .memory import MemoryId
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256


MEMORY_DERIVATION_SCHEMA_VERSION = "tiangong.life.memory-derivation.v1"

_MAX = 9_007_199_254_740_991


MemoryLayer = Literal[
    "L1_STREAM",
    "L2_DIARY",
    "L3_EXPERIENCE",
    "L4_EXPLICIT",
    "L5_CORE",
]

MemorySemanticDomain = Literal[
    "SELF_IDENTITY",
    "SELF_BEHAVIOR_PATTERN",
    "USER_PROFILE",
    "USER_PREFERENCE",
    "RELATIONSHIP",
    "OPERATING_RULE",
    "LONG_TERM_GOAL",
    "TASK",
    "CAPABILITY_SELF",
    "CAPABILITY_KNOWLEDGE",
    "WORLD",
    "REPOSITORY",
    "SYSTEM",
    "OTHER",
]

MemoryDerivationOrigin = Literal[
    "LIFE_EVENT",
    "PROMOTION",
    "USER_EXPLICIT",
    "LEARNING_RESULT",
    "MIGRATION",
]

MemoryInvalidationReason = Literal[
    "corrected",
    "superseded",
    "stale",
    "privacy_erasure",
    "invalidated",
]

MemoryDerivationId = Annotated[
    str, StringConstraints(pattern=r"^mdr_[0-9a-f]{64}$")
]
MemoryInvalidationId = Annotated[
    str, StringConstraints(pattern=r"^miv_[0-9a-f]{64}$")
]

_SELF_COGNITION_DOMAINS = frozenset(
    {"SELF_IDENTITY", "CAPABILITY_SELF", "LONG_TERM_GOAL", "OPERATING_RULE"}
)
_WORLD_CANDIDATE_LAYERS = frozenset({"L3_EXPERIENCE", "L4_EXPLICIT", "L5_CORE"})
_PROMOTED_LAYERS = frozenset({"L2_DIARY", "L3_EXPERIENCE", "L5_CORE"})


def _text(value: str, *, label: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError(f"{label} must be NFC and contain no NUL")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValueError(f"{label} contains a control character")
    return value


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError(
            "set-like memory derivation fields must be sorted and unique"
        )
    return value


def derive_promotion_key(
    *,
    policy_version: str,
    life_id: str,
    target_layer: str,
    parent_assertion_sha256: tuple[str, ...],
    semantic_domain: str,
    claim_key: str,
    lineage_root_event_ids: tuple[str, ...],
) -> str:
    """Deterministic promotion key (P15 invariant I08).

    Set-like inputs are sorted and de-duplicated before hashing so the key is
    stable regardless of caller ordering or platform.  Only canonical JSON
    participates; floating-point values are rejected by ``canonical_sha256``.
    """

    return canonical_sha256(
        {
            "policy_version": policy_version,
            "life_id": life_id,
            "target_layer": target_layer,
            "parent_assertion_sha256": tuple(sorted(set(parent_assertion_sha256))),
            "semantic_domain": semantic_domain,
            "claim_key": claim_key,
            "lineage_root_event_ids": tuple(sorted(set(lineage_root_event_ids))),
        }
    )


class MemoryParentRef(ContractModel):
    """One parent memory revision from which a derivation was promoted."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:MemoryParentRef",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.memory-parent-ref.v1"] = (
        "tiangong.life.memory-parent-ref.v1"
    )
    parent_derivation_id: MemoryDerivationId | None = None
    memory_id: MemoryId
    memory_revision: int = Field(ge=1, le=_MAX)
    assertion_sha256: Sha256
    parent_ref_sha256: Sha256

    def computed_parent_ref_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"parent_ref_sha256"})
        )

    def has_valid_parent_ref_sha256(self) -> bool:
        return self.parent_ref_sha256 == self.computed_parent_ref_sha256()

    def with_computed_parent_ref_sha256(self) -> Self:
        return self.model_copy(
            update={"parent_ref_sha256": self.computed_parent_ref_sha256()}
        )


class MemoryDerivationV1(ContractModel):
    """Append-only derivation metadata above one MemoryAssertionV3 revision."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:MemoryDerivationV1",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.memory-derivation.v1"] = (
        MEMORY_DERIVATION_SCHEMA_VERSION
    )
    derivation_id: MemoryDerivationId
    life_id: OpaqueId
    memory_id: MemoryId
    memory_revision: int = Field(ge=1, le=_MAX)
    memory_assertion_sha256: Sha256
    layer: MemoryLayer
    semantic_domain: MemorySemanticDomain
    origin: MemoryDerivationOrigin
    principal_ref: str
    workspace_ref: str | None = None
    privacy_scope: OpaqueId
    claim_key: str
    parent_memory_refs: tuple[MemoryParentRef, ...] = Field(
        default=(), max_length=256
    )
    source_event_ids: tuple[LifeEventId, ...] = Field(
        default=(), max_length=4096
    )
    lineage_root_event_ids: tuple[LifeEventId, ...] = Field(
        default=(), max_length=4096
    )
    external_evidence_refs: tuple[str, ...] = Field(
        default=(), max_length=1024
    )
    promotion_policy_version: str
    promotion_reason_codes: tuple[str, ...] = Field(
        default=(), max_length=256
    )
    valid_from_ms: int = Field(ge=0, le=_MAX)
    expires_at_ms: int | None = Field(default=None, ge=0, le=_MAX)
    context_eligible: bool
    learning_eligible: bool
    temperament_eligible: bool
    self_cognition_eligible: bool
    world_candidate_eligible: bool
    created_at_ms: int = Field(ge=0, le=_MAX)
    derivation_sha256: Sha256

    @field_validator("source_event_ids", "lineage_root_event_ids")
    @classmethod
    def _sorted_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value)

    @field_validator("external_evidence_refs", "promotion_reason_codes")
    @classmethod
    def _sorted_unique_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value)

    @field_validator("parent_memory_refs")
    @classmethod
    def _sorted_unique_parents(
        cls, value: tuple[MemoryParentRef, ...]
    ) -> tuple[MemoryParentRef, ...]:
        keyed = tuple(
            sorted(
                value,
                key=lambda item: (
                    item.parent_derivation_id or "",
                    item.memory_id,
                    item.memory_revision,
                ),
            )
        )
        if keyed != value:
            raise ValueError(
                "memory parent refs must be sorted and unique"
            )
        seen: set[tuple[object, str, int]] = set()
        for item in keyed:
            marker = (item.parent_derivation_id, item.memory_id, item.memory_revision)
            if marker in seen:
                raise ValueError("memory parent refs must be unique")
            seen.add(marker)
        return keyed

    @field_validator("principal_ref", "claim_key", "promotion_policy_version")
    @classmethod
    def _text_fields(cls, value: str) -> str:
        return _text(value, label="memory derivation text field")

    @field_validator("workspace_ref")
    @classmethod
    def _workspace_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _text(value, label="workspace_ref")

    @model_validator(mode="after")
    def validate_derivation(self) -> Self:
        if (
            self.expires_at_ms is not None
            and self.expires_at_ms <= self.valid_from_ms
        ):
            raise ValueError(
                "memory derivation expiry must follow validity start"
            )
        for parent in self.parent_memory_refs:
            if not parent.has_valid_parent_ref_sha256():
                raise ValueError("memory parent ref digest is invalid")
        if self.layer == "L1_STREAM" and self.origin != "MIGRATION" and (
            self.origin != "LIFE_EVENT" or not self.source_event_ids
        ):
            raise ValueError(
                "L1 stream requires a LIFE_EVENT origin with source events "
                "(or a MIGRATION origin)"
            )
        if self.layer == "L4_EXPLICIT" and (
            self.origin != "USER_EXPLICIT" or not self.source_event_ids
        ):
            raise ValueError(
                "L4 explicit requires USER_EXPLICIT origin bound to a user message event"
            )
        if self.origin != "MIGRATION" and not self.lineage_root_event_ids:
            raise ValueError(
                "non-migration derivations must carry lineage roots"
            )
        if self.temperament_eligible and not (
            self.layer == "L5_CORE"
            and self.semantic_domain == "SELF_BEHAVIOR_PATTERN"
        ):
            raise ValueError(
                "temperament eligibility requires L5 SELF_BEHAVIOR_PATTERN"
            )
        if self.self_cognition_eligible and not (
            self.layer == "L5_CORE"
            and self.semantic_domain in _SELF_COGNITION_DOMAINS
        ):
            raise ValueError(
                "self-cognition eligibility requires an L5 self-domain"
            )
        if self.world_candidate_eligible and not (
            self.layer in _WORLD_CANDIDATE_LAYERS
            and self.semantic_domain == "WORLD"
        ):
            raise ValueError(
                "world-candidate eligibility requires a mature WORLD-domain layer"
            )
        return self

    def computed_derivation_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"derivation_sha256"})
        )

    def has_valid_derivation_sha256(self) -> bool:
        return self.derivation_sha256 == self.computed_derivation_sha256()

    def with_computed_derivation_sha256(self) -> Self:
        return self.model_copy(
            update={"derivation_sha256": self.computed_derivation_sha256()}
        )


class MemoryPromotionDisposition(ContractModel):
    """Deterministic outcome of one promotion evaluation (no probabilities)."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:MemoryPromotionDisposition",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.memory-promotion-disposition.v1"] = (
        "tiangong.life.memory-promotion-disposition.v1"
    )
    promotion_key: str = Field(min_length=64, max_length=64)
    life_id: OpaqueId
    principal_ref: str
    target_layer: MemoryLayer
    claim_key: str
    semantic_domain: MemorySemanticDomain
    policy_version: str
    parent_assertion_sha256: tuple[Sha256, ...] = Field(
        default=(), max_length=4096
    )
    lineage_root_event_ids: tuple[LifeEventId, ...] = Field(
        default=(), max_length=4096
    )
    allowed: bool
    reason_codes: tuple[str, ...] = Field(default=(), max_length=256)
    support_milli: int = Field(ge=0, le=_MAX)
    counter_milli: int = Field(ge=0, le=_MAX)
    independence_group_count: int = Field(ge=0, le=1_000_000)
    recurrence_count: int = Field(ge=0, le=1_000_000)
    valid_from_ms: int = Field(ge=0, le=_MAX)
    created_at_ms: int = Field(ge=0, le=_MAX)
    disposition_sha256: Sha256

    @field_validator(
        "parent_assertion_sha256", "lineage_root_event_ids", "reason_codes"
    )
    @classmethod
    def _sorted_unique_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value)

    @field_validator("principal_ref", "policy_version", "claim_key")
    @classmethod
    def _text_fields(cls, value: str) -> str:
        return _text(value, label="promotion disposition text field")

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if (
            self.allowed
            and self.target_layer in _PROMOTED_LAYERS
            and not self.parent_assertion_sha256
        ):
            raise ValueError(
                "allowed promotion requires at least one parent assertion"
            )
        return self

    def computed_disposition_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"disposition_sha256"})
        )

    def has_valid_disposition_sha256(self) -> bool:
        return self.disposition_sha256 == self.computed_disposition_sha256()

    def with_computed_disposition_sha256(self) -> Self:
        return self.model_copy(
            update={"disposition_sha256": self.computed_disposition_sha256()}
        )


class MemoryInvalidationRecord(ContractModel):
    """Audit record for a derivation invalidation cascade."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:MemoryInvalidationRecord",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.memory-invalidation.v1"] = (
        "tiangong.life.memory-invalidation.v1"
    )
    invalidation_id: MemoryInvalidationId
    life_id: OpaqueId
    principal_ref: str
    derivation_id: MemoryDerivationId
    memory_id: MemoryId
    memory_revision: int = Field(ge=1, le=_MAX)
    assertion_sha256: Sha256
    reason: MemoryInvalidationReason
    source_trigger_ref: str | None = None
    invalidated_at_ms: int = Field(ge=0, le=_MAX)
    descendant_derivation_ids: tuple[MemoryDerivationId, ...] = Field(
        default=(), max_length=4096
    )
    invalidation_sha256: Sha256

    @field_validator("descendant_derivation_ids")
    @classmethod
    def _sorted_unique_descendants(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _sorted_unique(value)

    @field_validator("principal_ref")
    @classmethod
    def _principal_ref(cls, value: str) -> str:
        return _text(value, label="invalidation principal_ref")

    @field_validator("source_trigger_ref")
    @classmethod
    def _source_trigger_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _text(value, label="source_trigger_ref")

    def computed_invalidation_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"invalidation_sha256"})
        )

    def has_valid_invalidation_sha256(self) -> bool:
        return self.invalidation_sha256 == self.computed_invalidation_sha256()

    def with_computed_invalidation_sha256(self) -> Self:
        return self.model_copy(
            update={"invalidation_sha256": self.computed_invalidation_sha256()}
        )


__all__ = [
    "MEMORY_DERIVATION_SCHEMA_VERSION",
    "MemoryDerivationId",
    "MemoryDerivationOrigin",
    "MemoryDerivationV1",
    "MemoryInvalidationId",
    "MemoryInvalidationReason",
    "MemoryInvalidationRecord",
    "MemoryLayer",
    "MemoryParentRef",
    "MemoryPromotionDisposition",
    "MemorySemanticDomain",
    "derive_promotion_key",
]
