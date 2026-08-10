"""L4 candidate interpretation. A hypothesis can never self-promote to evidence."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, HypothesisId, WorldClaim, WorldRecordRef, sorted_unique_refs
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

HypothesisOrigin = Literal["deterministic_pattern", "rule_inference", "graph_inference", "llm_synthesis", "memory_consolidation", "migration"]

class WorldHypothesis(HashedWorldContract):
    _hash_field = "hypothesis_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    hypothesis_id: HypothesisId
    scope: WorldScope
    claim: WorldClaim
    hypothesis_kind: OpaqueId
    proposal_origin: HypothesisOrigin
    basis_refs: tuple[WorldRecordRef, ...] = Field(min_length=1, max_length=4096)
    counter_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    derivation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    interpretive_prior_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=1024)
    uncertainty_milli: int = Field(ge=0, le=1000, strict=True)
    proposal_model_ref: OpaqueId | None = None
    proposal_model_sha256: Sha256 | None = None
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    valid_until_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991, strict=True)
    projection_authority: Literal["hypothesis_only"] = "hypothesis_only"
    evidence_authority: Literal["none"] = "none"
    empirical_evidence_weight_milli: Literal[0] = 0
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    hypothesis_sha256: Sha256
    _validate_basis = field_validator("basis_refs")(sorted_unique_refs)
    _validate_counter = field_validator("counter_refs")(sorted_unique_refs)
    _validate_derivations = field_validator("derivation_refs")(sorted_unique_refs)
    _validate_priors = field_validator("interpretive_prior_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def validate_hypothesis(self) -> Self:
        if self.valid_until_ms is not None and self.valid_until_ms < self.created_at_ms:
            raise ValueError("hypothesis expiry precedes creation")
        if (self.proposal_model_ref is None) != (self.proposal_model_sha256 is None):
            raise ValueError("proposal model binding must be all-or-none")
        expected = "whyp_" + canonical_sha256({"domain": "tiangong.world.hypothesis-id.v1", "world_scope_hash": self.scope.world_scope_hash, "claim": self.claim.model_dump(mode="json"), "hypothesis_kind": self.hypothesis_kind, "proposal_origin": self.proposal_origin, "basis_refs": [x.model_dump(mode="json") for x in self.basis_refs], "created_at_ms": self.created_at_ms})
        if self.hypothesis_id != expected:
            raise ValueError("hypothesis id mismatch")
        return self
