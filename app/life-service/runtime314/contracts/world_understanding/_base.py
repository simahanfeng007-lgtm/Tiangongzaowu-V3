"""Shared immutable value objects for World Understanding contracts.

This module is deliberately side-effect free.  It reuses the repository's
canonical serializer and gateway ContractModel policy instead of introducing a
second contract stack.
"""
from __future__ import annotations

import unicodedata
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..canonical import canonical_json_bytes, canonical_sha256
from ..models import ContractModel, OpaqueId, Sha256

WORLD_SCHEMA_VERSION = "tiangong.world-understanding.contracts.v1"
WORLD_SCHEMA_BASE = "urn:tiangong:world-understanding:contracts:v1"
MAX_SAFE_INTEGER = 9_007_199_254_740_991

WorldId = Annotated[str, StringConstraints(pattern=r"^wld_[0-9a-f]{64}$")]
WorldCutId = Annotated[str, StringConstraints(pattern=r"^wcut_[0-9a-f]{64}$")]
WorldEventId = Annotated[str, StringConstraints(pattern=r"^wevt_[0-9a-f]{64}$")]
KnownId = Annotated[str, StringConstraints(pattern=r"^wkn_[0-9a-f]{64}$")]
EntityId = Annotated[str, StringConstraints(pattern=r"^went_[0-9a-f]{64}$")]
EntityCandidateId = Annotated[str, StringConstraints(pattern=r"^werc_[0-9a-f]{64}$")]
RelationId = Annotated[str, StringConstraints(pattern=r"^wrel_[0-9a-f]{64}$")]
HypothesisId = Annotated[str, StringConstraints(pattern=r"^whyp_[0-9a-f]{64}$")]
WorldStateId = Annotated[str, StringConstraints(pattern=r"^wst_[0-9a-f]{64}$")]
PredictionId = Annotated[str, StringConstraints(pattern=r"^wprd_[0-9a-f]{64}$")]
PredictionOutcomeId = Annotated[str, StringConstraints(pattern=r"^wpout_[0-9a-f]{64}$")]
WorldQueryId = Annotated[str, StringConstraints(pattern=r"^wqry_[0-9a-f]{64}$")]
WorldPacketId = Annotated[str, StringConstraints(pattern=r"^wcp_[0-9a-f]{64}$")]
ExpansionHandleId = Annotated[str, StringConstraints(pattern=r"^wexp_[0-9a-f]{64}$")]
CuriosityId = Annotated[str, StringConstraints(pattern=r"^wcur_[0-9a-f]{64}$")]
KnowledgeGapId = Annotated[str, StringConstraints(pattern=r"^wgap_[0-9a-f]{64}$")]
InquiryId = Annotated[str, StringConstraints(pattern=r"^winq_[0-9a-f]{64}$")]
InquiryOutcomeId = Annotated[str, StringConstraints(pattern=r"^wiout_[0-9a-f]{64}$")]
DerivationId = Annotated[str, StringConstraints(pattern=r"^wdrv_[0-9a-f]{64}$")]
DerivationEdgeId = Annotated[str, StringConstraints(pattern=r"^wdge_[0-9a-f]{64}$")]
IngressEnvelopeId = Annotated[str, StringConstraints(pattern=r"^wing_[0-9a-f]{64}$")]

TruthState = Literal["TRUE", "FALSE", "UNKNOWN", "CONFLICTED"]
EpistemicState = Literal["CURRENT", "STALE", "CHALLENGED", "REVERIFYING", "RETIRED"]
PrivacyScope = Literal["public", "relationship", "private", "secret", "system"]


def normalized_text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("world contract text must be NFC and contain no NUL")
    return value


def sorted_unique_strings(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like string fields must be sorted and unique")
    return value


def sorted_unique_refs(value: tuple["WorldRecordRef", ...]) -> tuple["WorldRecordRef", ...]:
    keys = tuple(item.sort_key() for item in value)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("set-like record refs must be sorted and unique")
    return value


class WorldContractModel(ContractModel):
    """Repository-native immutable model policy for World Understanding."""


class HashedWorldContract(WorldContractModel):
    """Common deterministic content-hash helpers.

    Contracts intentionally do not auto-rewrite caller data.  Callers can build
    a candidate with a placeholder hash, compute the canonical hash, then
    validate/persist it, matching the existing Cognition contract style.
    """

    _hash_field: ClassVar[str]

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={self._hash_field}))

    def has_valid_hash(self) -> bool:
        return getattr(self, self._hash_field) == self.computed_hash()

    def with_computed_hash(self) -> Self:
        return self.model_copy(update={self._hash_field: self.computed_hash()})


class WorldRecordRef(WorldContractModel):
    record_type: OpaqueId
    record_id: OpaqueId
    revision: int | None = Field(default=None, ge=1, le=MAX_SAFE_INTEGER, strict=True)
    sha256: Sha256

    def sort_key(self) -> tuple[str, str, int, str]:
        return (self.record_type, self.record_id, self.revision or 0, self.sha256)


class SmallObjectItem(WorldContractModel):
    key: OpaqueId
    value: str | int | bool | None

    _validate_value = field_validator("value")(
        lambda value: normalized_text(value) if isinstance(value, str) else value
    )


WorldValueKind = Literal[
    "entity_ref",
    "record_ref",
    "string",
    "integer",
    "boolean",
    "number_milli",
    "string_set",
    "record_ref_set",
    "small_object",
]


class WorldValue(WorldContractModel):
    kind: WorldValueKind
    entity_ref: OpaqueId | None = None
    record_ref: WorldRecordRef | None = None
    string_value: str | None = Field(default=None, max_length=20_000)
    integer_value: int | None = Field(default=None, ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER, strict=True)
    boolean_value: bool | None = Field(default=None, strict=True)
    number_milli: int | None = Field(default=None, ge=-1_000_000_000, le=1_000_000_000, strict=True)
    string_set: tuple[str, ...] | None = Field(default=None, max_length=4096)
    record_ref_set: tuple[WorldRecordRef, ...] | None = Field(default=None, max_length=4096)
    small_object: tuple[SmallObjectItem, ...] | None = Field(default=None, max_length=256)

    _validate_string = field_validator("string_value")(
        lambda value: None if value is None else normalized_text(value)
    )

    @field_validator("string_set")
    @classmethod
    def validate_string_set(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized = tuple(normalized_text(item) for item in value)
        return sorted_unique_strings(normalized)

    @field_validator("record_ref_set")
    @classmethod
    def validate_record_ref_set(cls, value: tuple[WorldRecordRef, ...] | None) -> tuple[WorldRecordRef, ...] | None:
        return None if value is None else sorted_unique_refs(value)

    @field_validator("small_object")
    @classmethod
    def validate_small_object(cls, value: tuple[SmallObjectItem, ...] | None) -> tuple[SmallObjectItem, ...] | None:
        if value is None:
            return None
        keys = tuple(item.key for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("small_object keys must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_exact_branch(self) -> Self:
        branches = {
            "entity_ref": self.entity_ref,
            "record_ref": self.record_ref,
            "string": self.string_value,
            "integer": self.integer_value,
            "boolean": self.boolean_value,
            "number_milli": self.number_milli,
            "string_set": self.string_set,
            "record_ref_set": self.record_ref_set,
            "small_object": self.small_object,
        }
        populated = {name: value is not None for name, value in branches.items()}
        if sum(populated.values()) != 1 or not populated[self.kind]:
            raise ValueError("WorldValue must populate exactly its declared kind")
        return self


class WorldClaim(WorldContractModel):
    subject_ref: WorldRecordRef
    predicate: OpaqueId
    value: WorldValue
    condition_ref: WorldRecordRef | None = None
    condition_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_condition_binding(self) -> Self:
        if (self.condition_ref is None) != (self.condition_sha256 is None):
            raise ValueError("claim condition binding must be all-or-none")
        if self.condition_ref is not None and self.condition_ref.sha256 != self.condition_sha256:
            raise ValueError("condition hash must match condition_ref")
        return self


def validate_inline_payload(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    encoded = canonical_json_bytes(value)
    if len(encoded) > 262_144:
        raise ValueError("inline world payload exceeds 256 KiB")
    return value
