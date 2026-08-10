"""Prediction resolution, error and calibration gate for P12 L7 dynamics."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.prediction import (
    PredictionOutcome,
    WorldPrediction,
    derive_prediction_outcome_id,
)

_BINARY_OUTCOMES = {"SUPPORTED": 1000, "CONTRADICTED": 0}


def prediction_error_milli(outcome: PredictionOutcome) -> int | None:
    target = _BINARY_OUTCOMES.get(outcome.outcome)
    if target is None:
        return None
    return abs(int(outcome.prediction_score_milli) - target)


def _bucket(score_milli: int) -> int:
    if score_milli == 1000:
        return 1000
    return (score_milli // 100) * 100


def resolve_prediction(
    prediction: WorldPrediction,
    *,
    outcome_kind: str,
    resolved_at_ms: int,
    outcome_observation_refs: tuple[WorldRecordRef, ...],
    prediction_family: str,
    horizon_class: str,
) -> tuple[WorldPrediction, PredictionOutcome]:
    if not prediction.has_valid_hash():
        raise ValueError("WORLD_PREDICTION_HASH_INVALID")
    if outcome_kind not in {"SUPPORTED", "CONTRADICTED", "INCONCLUSIVE", "EXPIRED"}:
        raise ValueError("PREDICTION_OUTCOME_KIND_INVALID")
    if resolved_at_ms < prediction.created_at_ms:
        raise ValueError("PREDICTION_RESOLUTION_TIME_INVALID")
    refs = tuple(sorted({r.sort_key(): r for r in outcome_observation_refs}.values(), key=lambda r: r.sort_key()))
    if outcome_kind in {"SUPPORTED", "CONTRADICTED", "INCONCLUSIVE"} and not refs:
        raise ValueError("PREDICTION_REALITY_OBSERVATION_REQUIRED")
    if outcome_kind == "EXPIRED" and refs:
        raise ValueError("EXPIRED_PREDICTION_CANNOT_BIND_OUTCOME_OBSERVATION")
    target = _BINARY_OUTCOMES.get(outcome_kind)
    error = None if target is None else abs(prediction.prediction_score_milli - target)
    outcome_id = derive_prediction_outcome_id(
        world_scope_hash=prediction.scope.world_scope_hash,
        prediction_id=prediction.prediction_id,
        outcome=outcome_kind,
        resolved_at_ms=resolved_at_ms,
        outcome_observation_refs=refs,
    )
    outcome = PredictionOutcome(
        outcome_id=outcome_id,
        scope=prediction.scope,
        prediction_id=prediction.prediction_id,
        prediction_family=prediction_family,
        horizon_class=horizon_class,
        prediction_score_milli=prediction.prediction_score_milli,
        outcome=outcome_kind,
        resolved_at_ms=resolved_at_ms,
        outcome_observation_refs=refs,
        calibration_bucket=None if target is None else _bucket(prediction.prediction_score_milli),
        brier_component_millionths=None if target is None else error * error,
        outcome_sha256="0" * 64,
    ).with_computed_hash()
    status = "EXPIRED" if outcome_kind == "EXPIRED" else "RESOLVED"
    revised = prediction.model_copy(update={
        "status": status,
        "outcome_observation_refs": refs,
        "resolution_score_milli": None if error is None else 1000 - error,
        "revision": prediction.revision + 1,
        "supersedes_prediction_sha256": prediction.prediction_sha256,
        "prediction_sha256": "0" * 64,
    }).with_computed_hash()
    return revised, outcome


@dataclass(frozen=True, slots=True)
class CalibrationGatePolicy:
    min_binary_samples: int = 60
    min_distinct_buckets: int = 3
    min_bucket_samples: int = 10
    max_expected_calibration_error_milli: int = 100
    def __post_init__(self) -> None:
        if self.min_binary_samples < 1 or self.min_distinct_buckets < 1 or self.min_bucket_samples < 1 or not 0 <= self.max_expected_calibration_error_milli <= 1000:
            raise ValueError("PREDICTION_CALIBRATION_POLICY_INVALID")

@dataclass(frozen=True, slots=True)
class CalibrationBucketProfile:
    bucket_milli: int
    sample_count: int
    mean_score_milli: int
    observed_support_rate_milli: int
    absolute_calibration_error_milli: int

@dataclass(frozen=True, slots=True)
class PredictionCalibrationProfile:
    life_id: str
    world_scope_hash: str
    prediction_family: str
    horizon_class: str
    binary_sample_count: int
    distinct_bucket_count: int
    expected_calibration_error_milli: int
    mean_brier_millionths: int
    buckets: tuple[CalibrationBucketProfile, ...]
    gate_open: bool
    telemetry_only: bool = True
    empirical_evidence_weight_milli: int = 0

@dataclass(frozen=True, slots=True)
class CalibratedProbabilityEstimate:
    life_id: str
    world_scope_hash: str
    prediction_family: str
    horizon_class: str
    input_prediction_score_milli: int
    calibration_bucket_milli: int
    calibrated_probability_milli: int
    calibration_sample_count: int
    telemetry_only: bool = True
    empirical_evidence_weight_milli: int = 0


def build_calibration_profile(
    outcomes: tuple[PredictionOutcome, ...],
    *,
    prediction_family: str,
    horizon_class: str,
    life_id: str,
    world_scope_hash: str,
    policy: CalibrationGatePolicy | None = None,
) -> PredictionCalibrationProfile:
    policy = policy or CalibrationGatePolicy()
    family_items = [item for item in outcomes if item.prediction_family == prediction_family and item.horizon_class == horizon_class]
    if any(item.scope.life_id != life_id or item.scope.world_scope_hash != world_scope_hash for item in family_items):
        raise ValueError("PREDICTION_CALIBRATION_SCOPE_MISMATCH")
    binary = [item for item in family_items if item.outcome in _BINARY_OUTCOMES]
    for item in binary:
        if not item.has_valid_hash() or item.empirical_evidence_weight_milli != 0 or item.evidence_authority != "none":
            raise ValueError("PREDICTION_OUTCOME_AUTHORITY_INVALID")
    grouped: dict[int, list[PredictionOutcome]] = {}
    for item in binary:
        grouped.setdefault(_bucket(item.prediction_score_milli), []).append(item)
    buckets: list[CalibrationBucketProfile] = []
    weighted_error = 0
    brier_total = 0
    for key in sorted(grouped):
        items = grouped[key]
        count = len(items)
        mean_score = sum(x.prediction_score_milli for x in items) // count
        support = sum(1 for x in items if x.outcome == "SUPPORTED")
        observed = (support * 1000) // count
        error = abs(mean_score - observed)
        weighted_error += error * count
        brier_total += sum(int(x.brier_component_millionths or 0) for x in items)
        buckets.append(CalibrationBucketProfile(key, count, mean_score, observed, error))
    n = len(binary)
    ece = 1000 if n == 0 else weighted_error // n
    brier = 1_000_000 if n == 0 else brier_total // n
    sufficiently_populated = sum(1 for item in buckets if item.sample_count >= policy.min_bucket_samples)
    gate = (
        n >= policy.min_binary_samples
        and sufficiently_populated >= policy.min_distinct_buckets
        and ece <= policy.max_expected_calibration_error_milli
    )
    return PredictionCalibrationProfile(
        life_id, world_scope_hash, prediction_family, horizon_class, n, len(buckets), ece, brier, tuple(buckets), gate
    )


def calibrated_probability_milli(
    prediction_score_milli: int,
    profile: PredictionCalibrationProfile,
    *,
    policy: CalibrationGatePolicy | None = None,
) -> CalibratedProbabilityEstimate | None:
    policy = policy or CalibrationGatePolicy()
    if not 0 <= prediction_score_milli <= 1000:
        raise ValueError("PREDICTION_SCORE_INVALID")
    if not profile.gate_open:
        return None
    key = _bucket(prediction_score_milli)
    candidates = [item for item in profile.buckets if item.sample_count >= policy.min_bucket_samples]
    if not candidates:
        return None
    bucket = min(candidates, key=lambda item: (abs(item.bucket_milli - key), item.bucket_milli))
    return CalibratedProbabilityEstimate(
        profile.life_id,
        profile.world_scope_hash,
        profile.prediction_family,
        profile.horizon_class,
        prediction_score_milli,
        bucket.bucket_milli,
        bucket.observed_support_rate_milli,
        bucket.sample_count,
    )

__all__ = [
    "CalibrationGatePolicy", "CalibrationBucketProfile", "PredictionCalibrationProfile",
    "CalibratedProbabilityEstimate", "prediction_error_milli", "resolve_prediction",
    "build_calibration_profile", "calibrated_probability_milli",
]
