"""Evidence contracts for the world cognition system.

Evidence is immutable, provenance-carrying material offered to cognition
consolidation. Repeated copies retain lineage and independence metadata so
derived memories cannot masquerade as independent observations.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .life import EvidenceClass
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256
from .cognition_prior import COGNITION_CONTRACT_SCHEMA_VERSION, CognitionDomain

CognitionEvidenceId = Annotated[str, StringConstraints(pattern=r"^cev_[0-9a-f]{64}$")]
CognitionAncestorId = Annotated[str, StringConstraints(pattern=r"^cog_[0-9a-f]{64}$")]
CognitionEvidenceSourceKind = Literal["memory", "fact_execution", "code_perception", "user_instruction", "system_authority", "model_synthesis", "migration"]
CognitionObservationMode = Literal["positive", "negative", "aggregate"]
CognitionExtractorKind = Literal["deterministic", "direct_tool", "memory_projection", "llm_synthesis", "migration"]
CognitionVolatilityClass = Literal["transient", "short", "medium", "long", "structural"]
CognitionPrivacyScope = Literal["public", "relationship", "private", "secret", "system"]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like cognition evidence fields must be sorted and unique")
    return value


def _text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("cognition evidence text must be NFC and contain no NUL")
    return value


class CognitionSourceRef(ContractModel):
    source_kind: CognitionEvidenceSourceKind
    object_id: OpaqueId
    object_revision: int = Field(ge=1, le=9_007_199_254_740_991)
    sha256: Sha256
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("cognition source span must be all-or-none")
        if self.span_start is not None and self.span_start > self.span_end:
            raise ValueError("cognition source span is inverted")
        return self


def derive_cognition_evidence_id(*, life_id: str, domain: str, world_scope_hash: str, principal_scope_hash: str, privacy_scope: str, source_ref: CognitionSourceRef, evidence_class: str, source_credibility_milli: int, authority_ceiling_milli: int, provenance_integrity_milli: int, observation_mode: str, observation: str, coverage_milli: int, search_scope_hash: str | None, independence_group_hash: str, lineage_root_hashes: tuple[str, ...], derived_from_evidence_ids: tuple[str, ...], ancestor_cognition_ids: tuple[str, ...], content_object_id: str, content_sha256: str, extractor_kind: str, observed_at_ms: int, valid_from_ms: int, valid_until_ms: int | None, volatility_class: str) -> str:
    return "cev_" + canonical_sha256({
        "domain": "tiangong.cognition.evidence-id.v1", "life_id": life_id,
        "cognition_domain": domain, "world_scope_hash": world_scope_hash,
        "principal_scope_hash": principal_scope_hash, "privacy_scope": privacy_scope,
        "source_ref": source_ref, "evidence_class": evidence_class,
        "source_credibility_milli": source_credibility_milli,
        "authority_ceiling_milli": authority_ceiling_milli,
        "provenance_integrity_milli": provenance_integrity_milli,
        "observation_mode": observation_mode, "observation": observation,
        "coverage_milli": coverage_milli, "search_scope_hash": search_scope_hash,
        "independence_group_hash": independence_group_hash,
        "lineage_root_hashes": lineage_root_hashes,
        "derived_from_evidence_ids": derived_from_evidence_ids,
        "ancestor_cognition_ids": ancestor_cognition_ids,
        "content_object_id": content_object_id, "content_sha256": content_sha256,
        "extractor_kind": extractor_kind, "observed_at_ms": observed_at_ms,
        "valid_from_ms": valid_from_ms, "valid_until_ms": valid_until_ms,
        "volatility_class": volatility_class,
    })


class CognitionEvidence(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, json_schema_extra={"$id": f"{SCHEMA_BASE}:CognitionEvidence", "$schema": "https://json-schema.org/draft/2020-12/schema"})
    schema_version: Literal["tiangong.cognition.contracts.v1"] = COGNITION_CONTRACT_SCHEMA_VERSION
    evidence_id: CognitionEvidenceId
    life_id: OpaqueId
    domain: CognitionDomain
    world_scope_hash: Sha256
    principal_scope_hash: Sha256
    privacy_scope: CognitionPrivacyScope
    source_ref: CognitionSourceRef
    evidence_class: EvidenceClass
    source_credibility_milli: int = Field(ge=0, le=1000, strict=True)
    authority_ceiling_milli: int = Field(ge=0, le=1000, strict=True)
    provenance_integrity_milli: int = Field(ge=0, le=1000, strict=True)
    observation_mode: CognitionObservationMode
    observation: str = Field(min_length=1, max_length=50_000)
    coverage_milli: int = Field(ge=0, le=1000, strict=True)
    search_scope_hash: Sha256 | None = None
    independence_group_hash: Sha256
    lineage_root_hashes: tuple[Sha256, ...] = Field(min_length=1, max_length=256)
    derived_from_evidence_ids: tuple[CognitionEvidenceId, ...] = Field(default=(), max_length=256)
    ancestor_cognition_ids: tuple[CognitionAncestorId, ...] = Field(default=(), max_length=256)
    content_object_id: OpaqueId
    content_sha256: Sha256
    extractor_kind: CognitionExtractorKind
    observed_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    valid_from_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    valid_until_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    volatility_class: CognitionVolatilityClass
    evidence_sha256: Sha256

    _validate_sets = field_validator("lineage_root_hashes", "derived_from_evidence_ids", "ancestor_cognition_ids")(_sorted_unique)
    _validate_observation = field_validator("observation")(_text)

    @model_validator(mode="after")
    def validate_provenance_and_scope(self) -> Self:
        if self.source_credibility_milli > self.authority_ceiling_milli:
            raise ValueError("derived cognition evidence cannot exceed its provenance authority ceiling")
        if self.valid_until_ms is not None and self.valid_until_ms < self.valid_from_ms:
            raise ValueError("cognition evidence validity interval is inverted")
        if self.evidence_class != "prospective" and self.observed_at_ms < self.valid_from_ms:
            raise ValueError("non-prospective cognition evidence cannot be observed before it is valid")
        if self.observation_mode in {"negative", "aggregate"}:
            if self.search_scope_hash is None or self.coverage_milli <= 0:
                raise ValueError("negative or aggregate cognition evidence requires scoped positive coverage")
        elif self.search_scope_hash is not None:
            raise ValueError("positive cognition evidence must not claim a negative-search scope")
        if self.evidence_id in self.derived_from_evidence_ids:
            raise ValueError("cognition evidence cannot derive from itself")
        if self.extractor_kind == "llm_synthesis" and self.evidence_class not in {"model_inference", "reflection"}:
            raise ValueError("LLM-synthesized cognition evidence must remain inference or reflection")
        if self.source_ref.source_kind == "model_synthesis" and self.evidence_class not in {"model_inference", "reflection"}:
            raise ValueError("model-synthesis sources cannot be relabeled as direct cognition evidence")
        return self

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = derive_cognition_evidence_id(
            life_id=self.life_id, domain=self.domain, world_scope_hash=self.world_scope_hash,
            principal_scope_hash=self.principal_scope_hash, privacy_scope=self.privacy_scope,
            source_ref=self.source_ref, evidence_class=self.evidence_class,
            source_credibility_milli=self.source_credibility_milli,
            authority_ceiling_milli=self.authority_ceiling_milli,
            provenance_integrity_milli=self.provenance_integrity_milli,
            observation_mode=self.observation_mode, observation=self.observation,
            coverage_milli=self.coverage_milli, search_scope_hash=self.search_scope_hash,
            independence_group_hash=self.independence_group_hash,
            lineage_root_hashes=self.lineage_root_hashes,
            derived_from_evidence_ids=self.derived_from_evidence_ids,
            ancestor_cognition_ids=self.ancestor_cognition_ids,
            content_object_id=self.content_object_id, content_sha256=self.content_sha256,
            extractor_kind=self.extractor_kind, observed_at_ms=self.observed_at_ms,
            valid_from_ms=self.valid_from_ms, valid_until_ms=self.valid_until_ms,
            volatility_class=self.volatility_class,
        )
        if self.evidence_id != expected:
            raise ValueError("cognition evidence ID does not match its immutable evidence payload")
        return self

    def computed_evidence_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))

    def has_valid_evidence_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_evidence_sha256()

    def with_computed_evidence_sha256(self) -> Self:
        return self.model_copy(update={"evidence_sha256": self.computed_evidence_sha256()})


__all__ = ["CognitionAncestorId", "CognitionEvidence", "CognitionEvidenceId", "CognitionEvidenceSourceKind", "CognitionExtractorKind", "CognitionObservationMode", "CognitionPrivacyScope", "CognitionSourceRef", "CognitionVolatilityClass", "derive_cognition_evidence_id"]
