"""P15-B: source-change invalidation bridging into the experience memory.

One system entry that connects a published Source change to BOTH existing
authorities: the P5 policy re-marks every affected aggregate (same-family
exact-revision change -> REVALIDATION_REQUIRED, family change -> STALE) and
the append-only memory cascade clears the active head of the corresponding
L3_EXPERIENCE derivations for STALE aggregates, preserving all history.

Nothing here stores an experience by itself, revives a lifecycle from
textual similarity, or touches unaffected aggregates: CURRENT is a no-op
(replays stay idempotent), and only a fresh full roundtrip on the new
sources can ever produce new positive evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from contracts import MemoryDerivationV1, MemoryInvalidationRecord
from world_understanding.capability_composition.capability_experience_policy import (
    CapabilityExperienceAggregateStateV1,
    CapabilityExperienceInvalidationIntentV1,
    mark_capability_experience_source_change,
)
from life_service.memory_invalidation import invalidate_cascade
from life_service.store import LifeShadowStore


@dataclass(frozen=True, slots=True)
class ExperienceInvalidationOutcome:
    """Per-aggregate result; carries the original P5 semantics verbatim."""

    experience_id: str
    freshness: str  # CURRENT | REVALIDATION_REQUIRED | STALE
    updated_state: CapabilityExperienceAggregateStateV1 | None
    intent: CapabilityExperienceInvalidationIntentV1 | None
    invalidation_records: tuple[MemoryInvalidationRecord, ...] = ()


def propagate_source_change_to_experiences(
    *,
    shadow_store: LifeShadowStore,
    states: Iterable[CapabilityExperienceAggregateStateV1],
    derivations_by_experience: Mapping[str, MemoryDerivationV1],
    current_sources: tuple,
    requested_at_ms: int,
    source_trigger_ref: str,
    current_sources_by_experience: Mapping[str, tuple] | None = None,
) -> tuple[ExperienceInvalidationOutcome, ...]:
    """Propagate ONE published source change; append-only, replay-safe.

    CURRENT aggregates are returned untouched with no store writes, so a
    duplicate or out-of-order re-notification of the same change is a no-op.
    ``current_sources_by_experience`` optionally supplies each aggregate's
    own current source set (a publication reaches several domains at once);
    absent entries fall back to the shared ``current_sources``.
    REVALIDATION_REQUIRED aggregates keep their memory derivation active
    (the history stays; only the aggregate lifecycle blocks reuse until a
    fresh roundtrip on the new sources re-admits it). STALE aggregates
    cascade through the ORIGINAL invalidate_cascade, clearing the active
    head while preserving the full causal history.
    """

    outcomes = []
    for state in states:
        if not state.has_valid_sha256():
            raise ValueError("experience state hash is invalid")
        sources_for_state = current_sources
        if current_sources_by_experience is not None:
            override = current_sources_by_experience.get(
                state.experience.experience_id)
            if override is not None:
                sources_for_state = override
        updated, intent = mark_capability_experience_source_change(
            state, sources_for_state, requested_at_ms=requested_at_ms)
        if intent is None:
            outcomes.append(ExperienceInvalidationOutcome(
                experience_id=state.experience.experience_id,
                freshness="CURRENT", updated_state=None, intent=None))
            continue
        records: tuple[MemoryInvalidationRecord, ...] = ()
        if intent.freshness == "STALE":
            derivation = derivations_by_experience.get(
                state.experience.experience_id)
            if derivation is None:
                raise ValueError(
                    "stale experience has no memory derivation to cascade")
            records = invalidate_cascade(
                shadow_store,
                derivation_id=derivation.derivation_id,
                reason="stale",
                invalidated_at_ms=requested_at_ms,
                source_trigger_ref=source_trigger_ref,
            )
        outcomes.append(ExperienceInvalidationOutcome(
            experience_id=state.experience.experience_id,
            freshness=intent.freshness,
            updated_state=updated,
            intent=intent,
            invalidation_records=records))
    return tuple(outcomes)


__all__ = [
    "ExperienceInvalidationOutcome",
    "propagate_source_change_to_experiences",
]
