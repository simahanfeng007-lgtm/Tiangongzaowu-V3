"""P15 M4: Learning -> Memory closure helpers.

Learning may only consume active L3 refs plus bounded repository/world
evidence.  A Learning Result becomes a LifeEvent, an L1 audit record and a
refined L3 experience with inherited evidence roots; it never writes L5 or
Temperament directly.  All ids and backoff windows are deterministic.
"""

from __future__ import annotations

from typing import Mapping

from contracts import canonical_sha256


LEARNING_RESULT_EVENT_POLICY = "p15-learning-result-v1"
LEARNING_REFINED_POLICY = "p15-learning-refined-v1"
MAX_OPEN_LEARNING = 8
MAX_LEARNING_L3_REFS = 16
LEARNING_BACKOFF_BASE_MS = 60_000
LEARNING_BACKOFF_GROWTH = 2
LEARNING_BACKOFF_MAX_MS = 86_400_000


def zero_gain_backoff_ms(consecutive_zero_gain: int) -> int:
    """Exponential backoff for repeated zero-information-gain learning."""

    if isinstance(consecutive_zero_gain, bool) or not isinstance(
        consecutive_zero_gain, int
    ):
        raise ValueError("zero-gain count must be an integer")
    if consecutive_zero_gain < 0:
        raise ValueError("zero-gain count cannot be negative")
    if consecutive_zero_gain == 0:
        return 0
    return min(
        LEARNING_BACKOFF_MAX_MS,
        LEARNING_BACKOFF_BASE_MS
        * (LEARNING_BACKOFF_GROWTH ** (consecutive_zero_gain - 1)),
    )


def build_learning_scope(
    *,
    active_l3_refs: tuple[Mapping[str, object], ...] = (),
    repository_evidence: tuple[Mapping[str, object], ...] = (),
    world_evidence: tuple[Mapping[str, object], ...] = (),
) -> dict[str, object]:
    """Bound the learning input to active L3 refs plus capped evidence."""

    l3_refs = tuple(active_l3_refs[:MAX_LEARNING_L3_REFS])
    repository = tuple(repository_evidence[:8])
    world = tuple(world_evidence[:4])
    return {
        "active_l3_refs": l3_refs,
        "repository_evidence": repository,
        "world_evidence": world,
        "source_sha256": canonical_sha256(
            {
                "domain": "tiangong.life.learning-scope.v1",
                "active_l3_refs": tuple(
                    sorted(
                        str(item.get("derivation_id") or "")
                        for item in l3_refs
                    )
                ),
                "repository_evidence": tuple(
                    sorted(
                        str(item.get("frame_id") or "")
                        for item in repository
                    )
                ),
                "world_evidence": tuple(
                    sorted(
                        str(item.get("candidate_id") or "")
                        for item in world
                    )
                ),
            }
        ),
    }


def derive_learning_result_ids(
    *,
    life_id: str,
    learning_id: str,
    result_sha256: str,
    event_policy_version: str = LEARNING_RESULT_EVENT_POLICY,
    refined_policy_version: str = LEARNING_REFINED_POLICY,
) -> dict[str, str]:
    """Deterministic LifeEvent/L1/refined-L3 ids for one learning result."""

    event_id = "lev_" + canonical_sha256(
        {
            "domain": "tiangong.life.learning-result-event.v1",
            "life_id": life_id,
            "learning_id": learning_id,
            "result_sha256": result_sha256,
            "policy_version": event_policy_version,
        }
    )
    l1_memory_id = "mem_" + canonical_sha256(
        {
            "domain": "tiangong.life.learning-l1-memory.v1",
            "life_id": life_id,
            "learning_id": learning_id,
            "result_sha256": result_sha256,
            "policy_version": event_policy_version,
        }
    )
    l1_derivation_id = "mdr_" + canonical_sha256(
        {
            "domain": "tiangong.life.learning-l1-derivation.v1",
            "life_id": life_id,
            "learning_id": learning_id,
            "result_sha256": result_sha256,
            "policy_version": event_policy_version,
        }
    )
    refined_memory_id = "mem_" + canonical_sha256(
        {
            "domain": "tiangong.life.learning-refined-memory.v1",
            "life_id": life_id,
            "learning_id": learning_id,
            "result_sha256": result_sha256,
            "policy_version": refined_policy_version,
        }
    )
    refined_derivation_id = "mdr_" + canonical_sha256(
        {
            "domain": "tiangong.life.learning-refined-derivation.v1",
            "life_id": life_id,
            "learning_id": learning_id,
            "result_sha256": result_sha256,
            "policy_version": refined_policy_version,
        }
    )
    return {
        "event_id": event_id,
        "l1_memory_id": l1_memory_id,
        "l1_derivation_id": l1_derivation_id,
        "refined_memory_id": refined_memory_id,
        "refined_derivation_id": refined_derivation_id,
    }


__all__ = [
    "LEARNING_BACKOFF_BASE_MS",
    "LEARNING_BACKOFF_GROWTH",
    "LEARNING_BACKOFF_MAX_MS",
    "LEARNING_REFINED_POLICY",
    "LEARNING_RESULT_EVENT_POLICY",
    "MAX_LEARNING_L3_REFS",
    "MAX_OPEN_LEARNING",
    "build_learning_scope",
    "derive_learning_result_ids",
    "zero_gain_backoff_ms",
]
