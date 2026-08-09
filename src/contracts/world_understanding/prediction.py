"""L7 prediction and post-reality outcome contracts."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import HashedWorldContract, PredictionId, PredictionOutcomeId, WorldClaim, WorldRecordRef, sorted_unique_refs
from .scope import WorldScope
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

PredictionStatus = Literal["PENDING", "RESOLVED", "EXPIRED", "CANCELLED"]
PredictionOutcomeKind = Literal["SUPPORTED", "CONTRADICTED", "INCONCLUSIVE", "EXPIRED"]

def derive_prediction_id(*, world_scope_hash: str, basis_world_state_ref: WorldRecordRef, predicted_claim: WorldClaim, condition_claim: WorldClaim | None, horizon_start_ms: int, horizon_end_ms: int) -> str:
    return "wprd_" + canonical_sha256({"domain": "tiangong.world.prediction-slot-id.v1", "world_scope_hash": world_scope_hash, "basis_world_state_ref": basis_world_state_ref.model_dump(mode="json"), "predicted_claim": predicted_claim.model_dump(mode="json"), "condition_claim": None if condition_claim is None else condition_claim.model_dump(mode="json"), "horizon_start_ms": horizon_start_ms, "horizon_end_ms": horizon_end_ms})

class WorldPrediction(HashedWorldContract):
    _hash_field = "prediction_sha256"
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    prediction_id: PredictionId
    scope: WorldScope
    basis_world_state_ref: WorldRecordRef
    condition_claim: WorldClaim | None = None
    predicted_claim: WorldClaim
    prediction_kind: OpaqueId
    horizon_start_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    horizon_end_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    prediction_score_milli: int = Field(ge=0, le=1000, strict=True)
    model_ref: OpaqueId | None = None
    model_sha256: Sha256 | None = None
    basis_refs: tuple[WorldRecordRef, ...] = Field(min_length=1, max_length=4096)
    status: PredictionStatus = "PENDING"
    outcome_observation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    resolution_score_milli: int | None = Field(default=None, ge=0, le=1000, strict=True)
    revision: int = Field(default=1, ge=1, le=9_007_199_254_740_991, strict=True)
    supersedes_prediction_sha256: Sha256 | None = None
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    evidence_authority: Literal["none"] = "none"
    empirical_evidence_weight_milli: Literal[0] = 0
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    prediction_sha256: Sha256
    _validate_basis = field_validator("basis_refs")(sorted_unique_refs)
    _validate_outcomes = field_validator("outcome_observation_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def validate_prediction(self) -> Self:
        if self.horizon_end_ms < self.horizon_start_ms:
            raise ValueError("prediction horizon is inverted")
        if (self.model_ref is None) != (self.model_sha256 is None):
            raise ValueError("prediction model binding must be all-or-none")
        if (self.revision == 1) != (self.supersedes_prediction_sha256 is None):
            raise ValueError("prediction revision lineage invalid")
        if self.status == "RESOLVED" and not self.outcome_observation_refs:
            raise ValueError("resolved prediction requires real observation refs")
        if self.status != "RESOLVED" and self.resolution_score_milli is not None:
            raise ValueError("unresolved prediction cannot have resolution score")
        if self.prediction_id != derive_prediction_id(world_scope_hash=self.scope.world_scope_hash, basis_world_state_ref=self.basis_world_state_ref, predicted_claim=self.predicted_claim, condition_claim=self.condition_claim, horizon_start_ms=self.horizon_start_ms, horizon_end_ms=self.horizon_end_ms):
            raise ValueError("prediction stable id mismatch")
        return self

def derive_prediction_outcome_id(*, prediction_id: str, outcome: str, resolved_at_ms: int, outcome_observation_refs: tuple[WorldRecordRef, ...]) -> str:
    return "wpout_" + canonical_sha256({"domain": "tiangong.world.prediction-outcome-id.v1", "prediction_id": prediction_id, "outcome": outcome, "resolved_at_ms": resolved_at_ms, "outcome_observation_refs": [item.model_dump(mode="json") for item in outcome_observation_refs]})

class PredictionOutcome(HashedWorldContract):
    _hash_field = "outcome_sha256"
    outcome_id: PredictionOutcomeId
    prediction_id: PredictionId
    prediction_family: OpaqueId
    horizon_class: OpaqueId
    prediction_score_milli: int = Field(ge=0, le=1000, strict=True)
    outcome: PredictionOutcomeKind
    resolved_at_ms: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    outcome_observation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4096)
    calibration_bucket: int | None = Field(default=None, ge=0, le=1000, strict=True)
    brier_component_millionths: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)
    empirical_evidence_weight_milli: Literal[0] = 0
    evidence_authority: Literal["none"] = "none"
    outcome_sha256: Sha256
    _validate_refs = field_validator("outcome_observation_refs")(sorted_unique_refs)
    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.outcome in {"SUPPORTED", "CONTRADICTED"} and not self.outcome_observation_refs:
            raise ValueError("resolved prediction outcome requires reality observation refs")
        if self.outcome_id != derive_prediction_outcome_id(prediction_id=self.prediction_id, outcome=self.outcome, resolved_at_ms=self.resolved_at_ms, outcome_observation_refs=self.outcome_observation_refs):
            raise ValueError("prediction outcome id mismatch")
        return self
