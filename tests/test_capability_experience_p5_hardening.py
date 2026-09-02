from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from life_service.memory_context import classify_instruction_authority
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from world_understanding.capability_composition import (
    CapabilityExperienceRecallQueryV1,
    DEFAULT_CAPABILITY_EXPERIENCE_POLICY,
    apply_capability_experience_observation,
    build_capability_experience_memory_intent,
    commit_capability_experience_via_memory_coordinator,
    evaluate_capability_experience_admission,
    exact_source_hashes,
    mark_capability_experience_source_change,
    recall_capability_experiences,
    source_revision_family,
)

from tests.life_contract_support import LIFE_ID, event
from tests.test_capability_experience_p5 import (
    _admission,
    _observation,
)


def test_public_exact_source_hashes_are_duplicate_free() -> None:
    observation = _observation(70)
    source = observation.plan.action_source_refs[0]
    hashes = exact_source_hashes((source, source))
    assert len(hashes) == 1


def test_explicit_failure_reason_is_never_dropped() -> None:
    observation = _observation(
        71,
        outcome="FAILURE",
        quality_milli=0,
        failure_category="RUNTIME_FAILURE",
        failure_reason_codes=("runtime.explicit-machine-failure",),
    )
    admission = evaluate_capability_experience_admission(
        observation,
        expected_principal_scope_hash=observation.principal_scope_hash,
        expected_privacy_scope_hash=observation.privacy_scope_hash,
        decided_at_ms=observation.observed_at_ms + 1,
    )
    assert admission.negative_allowed is True
    assert "runtime.explicit-machine-failure" in admission.reason_codes


def test_attribution_time_inversion_forces_negative_admission() -> None:
    observation = _observation(72)
    inverted = observation.model_copy(
        update={
            "observed_at_ms": observation.attribution.checked_at_ms - 1,
            "observation_sha256": "0" * 64,
        }
    ).with_computed_sha256()
    admission = evaluate_capability_experience_admission(
        inverted,
        expected_principal_scope_hash=inverted.principal_scope_hash,
        expected_privacy_scope_hash=inverted.privacy_scope_hash,
        decided_at_ms=inverted.attribution.checked_at_ms + 1,
    )
    assert admission.decision == "NEGATIVE_EVIDENCE"
    assert admission.positive_allowed is False
    assert "capability_experience.attribution_time_inverted" in (
        admission.reason_codes
    )


def test_recall_excludes_decayed_experience() -> None:
    observation = _observation(73)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    query = CapabilityExperienceRecallQueryV1(
        principal_scope_hash=state.principal_scope_hash,
        privacy_scope_hash=state.privacy_scope_hash,
        goal_class=state.experience.goal_class,
        environment_class=state.experience.environment_class,
        current_source_revision_family=source_revision_family(sources),
        current_exact_source_hashes=exact_source_hashes(sources),
        now_ms=(
            state.experience.last_success_ms
            + DEFAULT_CAPABILITY_EXPERIENCE_POLICY.evidence_decay_window_ms
            + 1
        ),
        include_probation=True,
        limit=8,
    )
    assert recall_capability_experiences((state,), query) == ()


def test_source_change_and_memory_intent_are_time_monotonic() -> None:
    observation = _observation(74)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    with pytest.raises(ValueError, match="predates experience state"):
        mark_capability_experience_source_change(
            state,
            sources,
            requested_at_ms=state.last_observed_at_ms - 1,
        )
    with pytest.raises(ValueError, match="predates experience state"):
        build_capability_experience_memory_intent(
            state,
            parent_derivation_ids=("mdr_parent",),
            created_at_ms=state.last_observed_at_ms - 1,
        )


def test_memory_coordinator_adapter_binds_plaintext_and_single_authority() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "p5-adapter.shadow.sqlite3"
        with LifeShadowStore.open(path, create=True, now_ms=500) as store:
            coordinator = MemoryCoordinator(store)
            life_event = event(1, None, life_id=LIFE_ID)
            _assertion, parent, created = coordinator.commit_life_event_l1(
                life_event
            )
            assert created is True
            observation = _observation(
                75,
                life_id=LIFE_ID,
                principal_ref=life_event.principal_ref,
                privacy_scope=life_event.privacy_scope,
            )
            state, _ = apply_capability_experience_observation(
                None, observation, _admission(observation)
            )
            intent, plaintext = build_capability_experience_memory_intent(
                state,
                parent_derivation_ids=(parent.derivation_id,),
                created_at_ms=2_000,
            )
            with pytest.raises(ValueError, match="plaintext digest"):
                commit_capability_experience_via_memory_coordinator(
                    coordinator,
                    state,
                    intent,
                    (parent,),
                    plaintext + b"tamper",
                )
            with pytest.raises(TypeError, match="MemoryCoordinator"):
                commit_capability_experience_via_memory_coordinator(
                    object(), state, intent, (parent,), plaintext
                )

            assertion, derivation, stored = (
                commit_capability_experience_via_memory_coordinator(
                    coordinator,
                    state,
                    intent,
                    (parent,),
                    plaintext,
                )
            )
            assert stored is True
            assert derivation.layer == "L3_EXPERIENCE"
            assert derivation.semantic_domain == "CAPABILITY_KNOWLEDGE"
            assert derivation.world_candidate_eligible is False
            assert classify_instruction_authority(
                derivation, assertion, plaintext.decode("utf-8")
            ) == "DATA"
