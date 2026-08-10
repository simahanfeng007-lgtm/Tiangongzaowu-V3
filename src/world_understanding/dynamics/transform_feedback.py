"""Bounded aggregation over real transform cost/quality telemetry."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding.transform_metrics import TransformCostObservation, TransformQualityProfile

@dataclass(frozen=True, slots=True)
class TransformFeedbackProfile:
    transform_id: str
    transform_version: str
    sample_count: int
    success_count: int
    success_rate_milli: int
    mean_wall_time_ms: int
    p95_wall_time_ms: int
    mean_token_cost: int
    mean_io_bytes: int
    validation_cost_milli: int | None
    downstream_challenge_rate_milli: int | None
    quality_sample_count: int
    telemetry_only: bool = True
    empirical_evidence_weight_milli: int = 0


def _nearest_rank_p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, (95 * len(ordered) + 99) // 100)
    return ordered[min(len(ordered), rank) - 1]


def build_transform_feedback(
    observations: tuple[TransformCostObservation, ...],
    *,
    quality: TransformQualityProfile | None = None,
) -> TransformFeedbackProfile:
    if not observations:
        raise ValueError("TRANSFORM_COST_TELEMETRY_REQUIRED")
    first = observations[0]
    transform_id = first.transform_id
    transform_version = first.transform_version
    for item in observations:
        if item.transform_id != transform_id or item.transform_version != transform_version:
            raise ValueError("TRANSFORM_TELEMETRY_IDENTITY_MISMATCH")
        if not item.telemetry_only or item.empirical_evidence_weight_milli != 0:
            raise ValueError("TRANSFORM_COST_TELEMETRY_AUTHORITY_INVALID")
    if quality is not None:
        if quality.transform_id != transform_id or quality.transform_version != transform_version:
            raise ValueError("TRANSFORM_QUALITY_IDENTITY_MISMATCH")
        if not quality.telemetry_only or quality.empirical_evidence_weight_milli != 0:
            raise ValueError("TRANSFORM_QUALITY_AUTHORITY_INVALID")
    count = len(observations)
    success = sum(1 for item in observations if item.success)
    return TransformFeedbackProfile(
        transform_id=transform_id,
        transform_version=transform_version,
        sample_count=count,
        success_count=success,
        success_rate_milli=(success * 1000) // count,
        mean_wall_time_ms=sum(item.wall_time_ms for item in observations) // count,
        p95_wall_time_ms=_nearest_rank_p95([item.wall_time_ms for item in observations]),
        mean_token_cost=sum(item.token_cost for item in observations) // count,
        mean_io_bytes=sum(item.io_bytes for item in observations) // count,
        validation_cost_milli=None if quality is None or quality.sample_count <= 0 else int(quality.mean_cost_milli),
        downstream_challenge_rate_milli=None if quality is None or quality.sample_count <= 0 else int(quality.downstream_challenge_rate_milli),
        quality_sample_count=0 if quality is None else int(quality.sample_count),
    )

__all__ = ["TransformFeedbackProfile", "build_transform_feedback"]
