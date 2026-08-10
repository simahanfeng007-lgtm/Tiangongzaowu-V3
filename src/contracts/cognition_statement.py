"""Stable world-cognition statement contracts.

A cognition statement is an immutable, scoped belief-slot revision. The stable
cognition_id identifies subject+predicate+condition; the value is deliberately
excluded so a changed world view becomes a new revision of the same slot.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256
from .cognition_prior import COGNITION_CONTRACT_SCHEMA_VERSION, CognitionDomain, CognitionPriorId
from .cognition_evidence import CognitionEvidenceId, CognitionPrivacyScope

CognitionId = Annotated[str, StringConstraints(pattern=r"^cog_[0-9a-f]{64}$")]
CognitionStatus = Literal["CANDIDATE", "PROVISIONAL", "STABLE", "CORE", "CHALLENGED", "REVERIFYING", "RETIRED"]
CognitionStabilityLevel = Literal["C0", "C1", "C2", "C3", "C4"]
CognitionClaimKind = Literal["component_role", "structural_relation", "architecture_pattern", "execution_path", "boundary", "capability_fact", "dependency_relation", "test_relation"]
CognitionProposalOrigin = Literal["deterministic_extraction", "llm_synthesis", "memory_consolidation", "system_migration", "explicit_system_authority"]
CognitionValueKind = Literal["entity_ref", "string", "integer", "boolean"]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like cognition statement fields must be sorted and unique")
    return value


def _text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("cognition statement text must be NFC and contain no NUL")
    return value


class CognitionValue(ContractModel):
    kind: CognitionValueKind
    entity_ref: OpaqueId | None = None
    string_value: str | None = Field(default=None, max_length=20_000)
    integer_value: int | None = Field(default=None, strict=True)
    boolean_value: bool | None = Field(default=None, strict=True)

    _validate_string = field_validator("string_value")(lambda value: None if value is None else _text(value))

    @model_validator(mode="after")
    def validate_exact_value_branch(self) -> Self:
        populated = {"entity_ref": self.entity_ref is not None, "string": self.string_value is not None, "integer": self.integer_value is not None, "boolean": self.boolean_value is not None}
        expected = {"entity_ref": "entity_ref", "string": "string", "integer": "integer", "boolean": "boolean"}[self.kind]
        if sum(populated.values()) != 1 or not populated[expected]:
            raise ValueError("cognition value must populate exactly its declared kind")
        return self


def derive_cognition_id(*, life_id: str, domain: str, world_scope_hash: str, principal_scope_hash: str, claim_kind: str, subject_ref: str, predicate: str, condition_sha256: str | None) -> str:
    return "cog_" + canonical_sha256({"domain": "tiangong.cognition.slot-id.v1", "life_id": life_id, "cognition_domain": domain, "world_scope_hash": world_scope_hash, "principal_scope_hash": principal_scope_hash, "claim_kind": claim_kind, "subject_ref": subject_ref, "predicate": predicate, "condition_sha256": condition_sha256})


class CognitionStatement(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, json_schema_extra={"$id": f"{SCHEMA_BASE}:CognitionStatement", "$schema": "https://json-schema.org/draft/2020-12/schema"})
    schema_version: Literal["tiangong.cognition.contracts.v1"] = COGNITION_CONTRACT_SCHEMA_VERSION
    cognition_id: CognitionId
    life_id: OpaqueId
    domain: CognitionDomain
    world_scope_hash: Sha256
    principal_scope_hash: Sha256
    privacy_scope: CognitionPrivacyScope
    claim_kind: CognitionClaimKind
    subject_ref: OpaqueId
    predicate: OpaqueId
    value: CognitionValue
    condition_object_id: OpaqueId | None = None
    condition_sha256: Sha256 | None = None
    proposal_origin: CognitionProposalOrigin
    status: CognitionStatus
    stability_level: CognitionStabilityLevel
    confidence_milli: int = Field(ge=0, le=1000, strict=True)
    supporting_evidence_ids: tuple[CognitionEvidenceId, ...] = Field(default=(), max_length=4096)
    counterevidence_ids: tuple[CognitionEvidenceId, ...] = Field(default=(), max_length=4096)
    prior_ids: tuple[CognitionPriorId, ...] = Field(default=(), max_length=256)
    valid_from_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    valid_until_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    last_verified_at_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_statement_sha256: Sha256 | None = None
    projection_authority: Literal["context_only"] = "context_only"
    statement_sha256: Sha256

    _validate_sets = field_validator("supporting_evidence_ids", "counterevidence_ids", "prior_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_shape_and_lifecycle(self) -> Self:
        if (self.condition_object_id is None) != (self.condition_sha256 is None):
            raise ValueError("cognition statement condition binding must be all-or-none")
        if set(self.supporting_evidence_ids) & set(self.counterevidence_ids):
            raise ValueError("one cognition evidence item cannot support and contradict the same revision")
        if self.valid_until_ms is not None and self.valid_until_ms < self.valid_from_ms:
            raise ValueError("cognition statement validity interval is inverted")
        if self.last_verified_at_ms is not None and self.last_verified_at_ms < self.valid_from_ms:
            raise ValueError("cognition statement cannot be verified before its validity begins")
        if (self.revision == 1) != (self.supersedes_statement_sha256 is None):
            raise ValueError("cognition statement revision chain is invalid")

        minimum_support = {"CANDIDATE": 0, "PROVISIONAL": 1, "STABLE": 2, "CORE": 3, "CHALLENGED": 1, "REVERIFYING": 1, "RETIRED": 0}[self.status]
        if len(self.supporting_evidence_ids) < minimum_support:
            raise ValueError("cognition statement status lacks the minimum evidence references")

        allowed_levels = {
            "CANDIDATE": {"C0"}, "PROVISIONAL": {"C1"}, "STABLE": {"C2"},
            "CORE": {"C3", "C4"}, "CHALLENGED": {"C1", "C2", "C3", "C4"},
            "REVERIFYING": {"C1", "C2", "C3", "C4"}, "RETIRED": {"C0", "C1", "C2", "C3", "C4"},
        }[self.status]
        if self.stability_level not in allowed_levels:
            raise ValueError("cognition statement stability level is inconsistent with lifecycle status")
        if self.status in {"STABLE", "CORE"} and self.last_verified_at_ms is None:
            raise ValueError("stable cognition requires an explicit verification timestamp")
        if self.status in {"CHALLENGED", "REVERIFYING"} and not self.counterevidence_ids:
            raise ValueError("challenged or reverifying cognition requires counterevidence")
        if self.status == "RETIRED" and self.valid_until_ms is None:
            raise ValueError("retired cognition requires a closed validity interval")

        expected = derive_cognition_id(life_id=self.life_id, domain=self.domain, world_scope_hash=self.world_scope_hash, principal_scope_hash=self.principal_scope_hash, claim_kind=self.claim_kind, subject_ref=self.subject_ref, predicate=self.predicate, condition_sha256=self.condition_sha256)
        if self.cognition_id != expected:
            raise ValueError("cognition ID does not match its stable slot identity")
        return self

    def computed_statement_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"statement_sha256"}))

    def has_valid_statement_sha256(self) -> bool:
        return self.statement_sha256 == self.computed_statement_sha256()

    def with_computed_statement_sha256(self) -> Self:
        return self.model_copy(update={"statement_sha256": self.computed_statement_sha256()})


__all__ = ["CognitionClaimKind", "CognitionId", "CognitionProposalOrigin", "CognitionStabilityLevel", "CognitionStatement", "CognitionStatus", "CognitionValue", "CognitionValueKind", "derive_cognition_id"]
