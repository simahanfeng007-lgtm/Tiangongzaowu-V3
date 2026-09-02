"""P5 bridge contracts for the existing MemoryCoordinator materialization seam.

This module never opens or mutates a Store itself. It deterministically converts
an admitted capability-experience DATA record into the existing
MemoryPromotionDisposition and delegates materialization to the one current
MemoryCoordinator authority.
"""

from __future__ import annotations

import hashlib
from typing import Any

from contracts import (
    MemoryDerivationV1,
    MemoryPromotionDisposition,
    derive_promotion_key,
)

from .capability_experience_policy import (
    CAPABILITY_EXPERIENCE_POLICY_VERSION,
    CapabilityExperienceAggregateStateV1,
    CapabilityExperienceMemoryIntentV1,
)


def build_memory_coordinator_disposition(
    state: CapabilityExperienceAggregateStateV1,
    intent: CapabilityExperienceMemoryIntentV1,
    parents: tuple[MemoryDerivationV1, ...],
) -> MemoryPromotionDisposition:
    """Build the existing promotion contract without touching persistence."""

    if not state.has_valid_sha256():
        raise ValueError("capability experience state hash is invalid")
    if not intent.has_valid_sha256():
        raise ValueError("capability experience memory intent hash is invalid")
    if intent.experience_state_sha256 != state.state_sha256:
        raise ValueError("memory intent is not bound to the experience state")
    if intent.policy_version != CAPABILITY_EXPERIENCE_POLICY_VERSION:
        raise ValueError("capability experience policy version drifted")
    if intent.created_at_ms < state.last_observed_at_ms:
        raise ValueError("memory intent predates the admitted experience")
    if not parents:
        raise ValueError("capability experience requires Memory parents")
    parents = tuple(sorted(parents, key=lambda item: item.derivation_id))
    if tuple(item.derivation_id for item in parents) != intent.parent_derivation_ids:
        raise ValueError("memory intent parent identities do not match")
    if any(
        not parent.has_valid_derivation_sha256()
        or parent.life_id != state.life_id
        or parent.principal_ref != state.principal_ref
        or parent.privacy_scope != state.privacy_scope
        for parent in parents
    ):
        raise ValueError("capability experience parent scope or hash is invalid")

    parent_assertion_sha256 = tuple(
        sorted({parent.memory_assertion_sha256 for parent in parents})
    )
    lineage_root_event_ids = tuple(
        sorted(
            {
                event_id
                for parent in parents
                for event_id in parent.lineage_root_event_ids
            }
        )
    )
    if not lineage_root_event_ids:
        raise ValueError("capability experience parents have no Reality lineage")
    experience = state.experience
    total = experience.success_count + experience.failure_count
    counter_milli = (
        (experience.failure_count * 1000) // total if total else 0
    )
    reason_codes = tuple(
        sorted(
            {
                "capability_experience.policy_admitted",
                "capability_experience.data_only",
                "capability_experience.lifecycle."
                + experience.lifecycle.casefold(),
            }
        )
    )
    promotion_key = derive_promotion_key(
        policy_version=intent.policy_version,
        life_id=state.life_id,
        target_layer="L3_EXPERIENCE",
        parent_assertion_sha256=parent_assertion_sha256,
        semantic_domain="CAPABILITY_KNOWLEDGE",
        claim_key=intent.claim_key,
        lineage_root_event_ids=lineage_root_event_ids,
    )
    return MemoryPromotionDisposition(
        promotion_key=promotion_key,
        life_id=state.life_id,
        principal_ref=state.principal_ref,
        target_layer="L3_EXPERIENCE",
        claim_key=intent.claim_key,
        semantic_domain="CAPABILITY_KNOWLEDGE",
        policy_version=intent.policy_version,
        parent_assertion_sha256=parent_assertion_sha256,
        lineage_root_event_ids=lineage_root_event_ids,
        allowed=True,
        reason_codes=reason_codes,
        support_milli=experience.lower_confidence_milli,
        counter_milli=counter_milli,
        independence_group_count=experience.independent_context_count,
        recurrence_count=total,
        valid_from_ms=state.last_observed_at_ms,
        created_at_ms=intent.created_at_ms,
        disposition_sha256="0" * 64,
    ).with_computed_disposition_sha256()


def commit_capability_experience_via_memory_coordinator(
    coordinator: Any,
    state: CapabilityExperienceAggregateStateV1,
    intent: CapabilityExperienceMemoryIntentV1,
    parents: tuple[MemoryDerivationV1, ...],
    plaintext: bytes,
):
    """Validate DATA bytes, then delegate the sole write to MemoryCoordinator.

    This is an adapter, not a second coordinator. It refuses arbitrary writer
    objects and never obtains a Store handle. The existing coordinator's
    materialization method remains the only persistence call.
    """

    coordinator_type = type(coordinator)
    if (
        coordinator_type.__name__ != "MemoryCoordinator"
        or not coordinator_type.__module__.endswith(
            "life_service.memory_coordinator"
        )
    ):
        raise TypeError("existing MemoryCoordinator authority is required")
    if not isinstance(plaintext, bytes) or not plaintext:
        raise ValueError("capability experience plaintext must be non-empty bytes")
    if hashlib.sha256(plaintext).hexdigest() != intent.plaintext_sha256:
        raise ValueError("capability experience plaintext digest is invalid")
    disposition = build_memory_coordinator_disposition(state, intent, parents)
    materialize = getattr(coordinator, "_materialize_promotion", None)
    if not callable(materialize):
        raise TypeError("MemoryCoordinator materialization seam is unavailable")
    result = materialize(
        disposition=disposition,
        parents=tuple(sorted(parents, key=lambda item: item.derivation_id)),
        principal_ref=state.principal_ref,
        privacy_scope=state.privacy_scope,
        plaintext=plaintext,
        created_at_ms=intent.created_at_ms,
        policy_version=intent.policy_version,
    )
    assertion, derivation, _created = result
    if (
        derivation.layer != "L3_EXPERIENCE"
        or derivation.semantic_domain != "CAPABILITY_KNOWLEDGE"
        or derivation.world_candidate_eligible
        or derivation.principal_ref != state.principal_ref
        or derivation.privacy_scope != state.privacy_scope
    ):
        raise RuntimeError("MemoryCoordinator materialized an invalid capability experience")
    if assertion.life_id != state.life_id:
        raise RuntimeError("materialized capability experience crossed Life identity")
    return result


__all__ = [
    "build_memory_coordinator_disposition",
    "commit_capability_experience_via_memory_coordinator",
]
