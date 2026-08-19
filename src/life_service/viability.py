"""Deterministic viability deficit computation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from contracts import (
    ViabilityDimension,
    ViabilityObservation,
    ViabilityState,
    canonical_json_bytes,
    canonical_sha256,
)


CRITICAL_DIMENSIONS = frozenset(
    {
        "recoverability",
        "identity_continuity",
        "data_integrity",
        "security_margin",
    }
)
VIABILITY_DIMENSIONS = (
    "runtime_availability",
    "recoverability",
    "identity_continuity",
    "data_integrity",
    "memory_integrity",
    "context_continuity",
    "resource_headroom",
    "cognitive_certainty",
    "trust_and_authorization",
    "commitment_continuity",
    "security_margin",
)
SOURCE_CONFIDENCE_CEILINGS = {
    "execution_verified": 1000,
    "migration_verified": 950,
    "observed": 900,
    "user_asserted": 800,
    "reflection": 600,
    "model_inference": 400,
    "prospective": 250,
}


@dataclass(frozen=True, slots=True)
class ViabilityCollectionResult:
    state: ViabilityState
    effective_source_confidences: tuple[tuple[str, int], ...]
    stale_dimensions: tuple[str, ...]
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class ViabilityDeficit:
    dimension_deficits: tuple[tuple[str, int], ...]
    weighted_deficit_milli: int
    critical_deficit_milli: int
    critical_weight_milli: int
    total_deficit_milli: int
    input_state_sha256: str
    policy_sha256: str


def _strict_milli(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
        raise ValueError(f"{label} must be an integer milli value")
    return value


def collect_viability_state(
    observations: Iterable[ViabilityObservation],
    *,
    target_bands: Mapping[str, tuple[int, int]],
    revision: int,
    now_ms: int,
) -> ViabilityCollectionResult:
    """Aggregate source-bound measurements without accepting model-owned confidence."""

    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("viability revision is invalid")
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("viability collection time is invalid")
    if set(target_bands) != set(VIABILITY_DIMENSIONS):
        raise ValueError("viability target bands do not cover the exact dimension set")
    normalized_bands: dict[str, tuple[int, int]] = {}
    for name, band in target_bands.items():
        if not isinstance(band, tuple) or len(band) != 2:
            raise ValueError("viability target band shape is invalid")
        low = _strict_milli(band[0], label="viability target low")
        high = _strict_milli(band[1], label="viability target high")
        if low > high:
            raise ValueError("viability target band is inverted")
        normalized_bands[name] = (low, high)

    parsed: list[ViabilityObservation] = []
    for observation in observations:
        try:
            item = ViabilityObservation.model_validate_json(canonical_json_bytes(observation))
        except Exception as exc:
            raise ValueError("viability observation contract is invalid") from exc
        if not item.has_valid_observation_sha256():
            raise ValueError("viability observation digest is invalid")
        if item.measured_at_ms > now_ms:
            raise ValueError("future viability observation is forbidden")
        parsed.append(item)
    if not parsed:
        raise ValueError("viability collection requires observations")
    identities = tuple(item.observation_id for item in parsed)
    if len(identities) != len(set(identities)):
        raise ValueError("viability observation identity is duplicated")
    life_ids = {item.life_id for item in parsed}
    if len(life_ids) != 1:
        raise ValueError("viability observations cross life identities")
    by_dimension = {
        name: tuple(item for item in parsed if item.dimension == name)
        for name in VIABILITY_DIMENSIONS
    }
    if any(not values for values in by_dimension.values()):
        raise ValueError("viability collection lacks a dimension")

    effective: dict[str, int] = {
        item.observation_id: min(
            item.declared_confidence_milli,
            SOURCE_CONFIDENCE_CEILINGS[item.evidence_class],
        )
        for item in parsed
    }
    dimensions: dict[str, ViabilityDimension] = {}
    stale: list[str] = []
    for name in VIABILITY_DIMENSIONS:
        values = by_dimension[name]
        active = tuple(item for item in values if item.stale_after_ms >= now_ms)
        selected = active or (
            max(values, key=lambda item: (item.measured_at_ms, item.observation_id)),
        )
        weights = tuple(effective[item.observation_id] if active else 0 for item in selected)
        weight_total = sum(weights)
        if weight_total:
            value_milli = sum(
                item.value_milli * weight
                for item, weight in zip(selected, weights, strict=True)
            ) // weight_total
            average_confidence = weight_total // len(selected)
            disagreement = max(item.value_milli for item in selected) - min(
                item.value_milli for item in selected
            )
            confidence_milli = max(0, average_confidence - disagreement)
        else:
            value_milli = selected[0].value_milli
            confidence_milli = 0
            stale.append(name)
        low, high = normalized_bands[name]
        dimensions[name] = ViabilityDimension(
            value_milli=value_milli,
            target_low_milli=low,
            target_high_milli=high,
            confidence_milli=confidence_milli,
            source_event_ids=tuple(sorted({item.source_event_id for item in selected})),
            measured_at_ms=max(item.measured_at_ms for item in selected),
            stale_after_ms=min(item.stale_after_ms for item in selected),
        )

    policy_sha256 = canonical_sha256(
        {
            "domain": "tiangong.life.viability-collector-policy.v1",
            "source_confidence_ceilings": SOURCE_CONFIDENCE_CEILINGS,
            "target_bands": dict(sorted(normalized_bands.items())),
        }
    )
    state = ViabilityState(
        life_id=next(iter(life_ids)),
        revision=revision,
        **dimensions,
        created_at_ms=now_ms,
        state_sha256="0" * 64,
    ).with_computed_state_sha256()
    return ViabilityCollectionResult(
        state=state,
        effective_source_confidences=tuple(sorted(effective.items())),
        stale_dimensions=tuple(sorted(stale)),
        policy_sha256=policy_sha256,
    )


def compute_viability_deficit(
    state: ViabilityState,
    *,
    weights: Mapping[str, int],
    critical_weight_milli: int,
) -> ViabilityDeficit:
    try:
        state = ViabilityState.model_validate_json(canonical_json_bytes(state))
    except Exception as exc:
        raise ValueError("viability state contract is invalid") from exc
    if not state.has_valid_state_sha256():
        raise ValueError("viability state digest is invalid")
    dimensions = state.dimensions()
    if set(weights) != set(dimensions):
        raise ValueError("viability weights do not cover the exact dimension set")
    if (
        isinstance(critical_weight_milli, bool)
        or not isinstance(critical_weight_milli, int)
        or not 0 <= critical_weight_milli <= 1000
    ):
        raise ValueError("critical viability weight is invalid")
    normalized_weights: dict[str, int] = {}
    for name, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, int) or not 0 <= weight <= 1000:
            raise ValueError("viability dimension weight is invalid")
        normalized_weights[name] = weight
    weight_total = sum(normalized_weights.values())
    if weight_total <= 0:
        raise ValueError("viability weights cannot all be zero")
    deficits = tuple(
        sorted(
            (
                name,
                max(0, dimension.target_low_milli - dimension.value_milli),
            )
            for name, dimension in dimensions.items()
        )
    )
    by_name = dict(deficits)
    weighted = sum(
        by_name[name] * normalized_weights[name]
        for name in normalized_weights
    ) // weight_total
    critical = max(by_name[name] for name in CRITICAL_DIMENSIONS)
    # Keep the total inside the milli convention [0, 1000]: weighted and the
    # critical contribution are each independently bounded by 1000.
    total = min(1000, weighted + (critical * critical_weight_milli // 1000))
    policy_sha256 = canonical_sha256(
        {
            "critical_dimensions": sorted(CRITICAL_DIMENSIONS),
            "critical_weight_milli": critical_weight_milli,
            "domain": "tiangong.life.viability-deficit-policy.v1",
            "weights": dict(sorted(normalized_weights.items())),
        }
    )
    return ViabilityDeficit(
        dimension_deficits=deficits,
        weighted_deficit_milli=weighted,
        critical_deficit_milli=critical,
        critical_weight_milli=critical_weight_milli,
        total_deficit_milli=total,
        input_state_sha256=state.state_sha256,
        policy_sha256=policy_sha256,
    )


__all__ = [
    "CRITICAL_DIMENSIONS",
    "SOURCE_CONFIDENCE_CEILINGS",
    "VIABILITY_DIMENSIONS",
    "ViabilityCollectionResult",
    "ViabilityDeficit",
    "collect_viability_state",
    "compute_viability_deficit",
]
