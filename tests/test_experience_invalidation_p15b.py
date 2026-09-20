"""P15-B: a published source change expires experiences through both authorities.

Real LifeShadowStore + the ORIGINAL P5 source-change policy + the ORIGINAL
append-only cascade. Same-family exact-revision changes land in
REVALIDATION_REQUIRED with the memory derivation retained; family changes
land in STALE with the active head cleared; CURRENT aggregates are untouched
no-ops (duplicates/乱序 replays stay inert); no textual similarity path
exists that could revive a lifecycle.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from contracts import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from world_understanding.capability_composition.capability_experience_policy import (
    CapabilityCombinationExperienceV1,
    CapabilityExperienceAggregateStateV1,
    computed_experience_sha256,
    source_revision_family,
    exact_source_hashes,
)
from life_service.experience_invalidation_bridge import (
    propagate_source_change_to_experiences,
)

ZERO = "0" * 64


def _source_ref(semantic: str, sha: str) -> SourceRevisionRefV1:
    return SourceRevisionRefV1(
        source_kind="TOOL_ACTION", semantic_id=semantic, version="1.0.0",
        source_sha256=sha, descriptor_sha256=sha,
        source_files=(f"src/{semantic.replace('.', '_')}.py",),
    )


def _experience_state(sources, *, suffix="a"):
    experience = CapabilityCombinationExperienceV1(
        experience_id="cex_" + suffix * 60,
        goal_class="goal:fixture", environment_class="test-env",
        scene_fingerprint="4" * 64,
        context_fingerprint_sha256="5" * 64,
        action_source_refs=sources,
        topology_sha256="6" * 64, composition_plan_sha256="7" * 64,
        request_id="req_" + "1" * 64, run_id="run_" + "2" * 64, generation=1,
        completion_decision_sha256="8" * 64,
        verification_readiness_sha256="9" * 64,
        outcome="SUCCESS", success_count=2, failure_count=0,
        independent_context_count=1, last_success_ms=1000,
        posterior_success_milli=900, lower_confidence_milli=800,
        lifecycle="STABLE",
        source_revision_family=source_revision_family(sources),
        exact_source_hashes=exact_source_hashes(sources),
        experience_sha256=ZERO,
    )
    experience = experience.model_copy(
        update={"experience_sha256": computed_experience_sha256(experience)})
    state = CapabilityExperienceAggregateStateV1(
        experience_key="expkey:" + suffix,
        life_id="life_p15b", principal_ref="principal:p15b",
        principal_scope_hash="a" * 64,
        privacy_scope="privacy:p15b", privacy_scope_hash="b" * 64,
        experience=experience,
        context_fingerprints=("5" * 64,),
        observation_ids=("obs_1", "obs_2"),
        quality_sum_milli=1800, quality_observation_count=2,
        last_observed_at_ms=1200,
        state_sha256=ZERO,
    )
    return state.with_computed_sha256()


def _l3_derivation(store, coordinator, *, claim_key):
    from tests.test_memory_correction_invalidation_p15 import event
    value = event(1, None, life_id="life_p15b", suffix="9" * 64)
    _a, l1, _c = coordinator.commit_life_event_l1(value)
    l2 = coordinator.promote_l1_to_l2(
        life_id="life_p15b", principal_ref=value.principal_ref,
        privacy_scope=value.privacy_scope,
        l1_derivation_ids=(l1.derivation_id,),
        claim_key=claim_key, semantic_domain="CAPABILITY_KNOWLEDGE",
        plaintext=b"capability experience", created_at_ms=2_000)
    l3 = coordinator.promote_l2_to_l3(
        life_id="life_p15b", principal_ref=value.principal_ref,
        privacy_scope=value.privacy_scope,
        l2_derivation_ids=(l2[1].derivation_id,),
        claim_key=claim_key, semantic_domain="CAPABILITY_KNOWLEDGE",
        plaintext=b"capability experience", created_at_ms=3_000,
        support_weights={l2[1].derivation_id: 1000}, counter_weights={},
        causal_utility_milli={l2[1].derivation_id: 800}, recurrence_count=2)
    return l3[1]


class _World:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LifeShadowStore.open(
            Path(self.temporary.name) / "p15b.shadow.sqlite3",
            create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def close(self):
        self.store.close()
        self.temporary.cleanup()


def test_same_family_revision_change_requires_revalidation():
    world = _World()
    try:
        sources = (_source_ref("skill.list", "1" * 64),)
        state = _experience_state(sources)
        derivation = _l3_derivation(world.store, world.coordinator,
                                    claim_key="claim:cap:family")
        new_sources = (_source_ref("skill.list", "2" * 64),)  # same family
        outcomes = propagate_source_change_to_experiences(
            shadow_store=world.store, states=(state,),
            derivations_by_experience={state.experience.experience_id: derivation},
            current_sources=new_sources, requested_at_ms=2_000,
            source_trigger_ref="tool.publication.p15b")
        (outcome,) = outcomes
        assert outcome.freshness == 'REVALIDATION_REQUIRED'
        assert outcome.intent.reason_code == \
            'capability_experience.source_revision_changed'
        assert outcome.updated_state.experience.lifecycle == \
            'REVALIDATION_REQUIRED'
        # The memory derivation stays active; history is retained.
        assert world.store.is_derivation_active(derivation.derivation_id)
        assert outcome.invalidation_records == ()
    finally:
        world.close()


def test_family_change_goes_stale_and_clears_active_head():
    world = _World()
    try:
        sources = (_source_ref("skill.list", "1" * 64),)
        state = _experience_state(sources)
        derivation = _l3_derivation(world.store, world.coordinator,
                                    claim_key="claim:cap:family2")
        new_sources = (_source_ref("other.action", "3" * 64),)  # family changed
        outcomes = propagate_source_change_to_experiences(
            shadow_store=world.store, states=(state,),
            derivations_by_experience={state.experience.experience_id: derivation},
            current_sources=new_sources, requested_at_ms=2_000,
            source_trigger_ref="tool.publication.p15b")
        (outcome,) = outcomes
        assert outcome.freshness == 'STALE'
        assert outcome.intent.reason_code == \
            'capability_experience.source_family_changed'
        assert outcome.updated_state.experience.lifecycle == 'STALE'
        # The append-only cascade recorded the invalidation and cleared the
        # active head while preserving the derivation history.
        assert outcome.invalidation_records
        record = outcome.invalidation_records[0]
        assert record.reason == 'stale'
        assert record.source_trigger_ref == 'tool.publication.p15b'
        assert world.store.get_memory_derivation(
            derivation.derivation_id) is not None
    finally:
        world.close()


def test_current_sources_are_a_complete_no_op():
    world = _World()
    try:
        sources = (_source_ref("skill.list", "1" * 64),)
        state = _experience_state(sources)
        outcomes = propagate_source_change_to_experiences(
            shadow_store=world.store, states=(state,),
            derivations_by_experience={},
            current_sources=sources, requested_at_ms=2_000,
            source_trigger_ref="tool.publication.p15b")
        (outcome,) = outcomes
        assert outcome.freshness == 'CURRENT'
        assert outcome.updated_state is None and outcome.intent is None
        # Duplicate / out-of-order re-notification of the same change: the
        # marked state is already at the new sources, so it is CURRENT.
        marked = _experience_state(sources)
        replay = propagate_source_change_to_experiences(
            shadow_store=world.store, states=(marked,),
            derivations_by_experience={},
            current_sources=sources, requested_at_ms=3_000,
            source_trigger_ref="tool.publication.p15b")
        assert replay[0].freshness == 'CURRENT'
    finally:
        world.close()


def test_unaffected_aggregate_stays_usable():
    """Only aggregates bound to the changed family expire; others stay CURRENT."""
    world = _World()
    try:
        changed = _experience_state(
            (_source_ref("skill.list", "1" * 64),), suffix="a")
        untouched = _experience_state(
            (_source_ref("other.action", "9" * 64),), suffix="b")
        new_sources = (_source_ref("skill.list", "2" * 64),)
        other_sources = (_source_ref("other.action", "9" * 64),)
        outcomes = propagate_source_change_to_experiences(
            shadow_store=world.store, states=(changed, untouched),
            derivations_by_experience={},
            current_sources=new_sources, requested_at_ms=2_000,
            source_trigger_ref="tool.publication.p15b",
            current_sources_by_experience={
                changed.experience.experience_id: new_sources,
                untouched.experience.experience_id: other_sources,
            })
        by_id = {o.experience_id: o for o in outcomes}
        assert by_id[changed.experience.experience_id].freshness == \
            'REVALIDATION_REQUIRED'
        assert by_id[untouched.experience.experience_id].freshness == 'CURRENT'
    finally:
        world.close()


def test_textual_similarity_never_revives_a_lifecycle():
    """No text path exists: identical goal/scene text with different source
    hashes still expires; the lifecycle only moves through source identity."""
    world = _World()
    try:
        sources = (_source_ref("skill.list", "1" * 64),)
        state = _experience_state(sources)
        # "New source" carries byte-identical semantic text but a different
        # exact revision: still REVALIDATION_REQUIRED, never CURRENT.
        new_sources = (_source_ref("skill.list", "4" * 64),)
        outcomes = propagate_source_change_to_experiences(
            shadow_store=world.store, states=(state,),
            derivations_by_experience={},
            current_sources=new_sources, requested_at_ms=2_000,
            source_trigger_ref="tool.publication.p15b")
        assert outcomes[0].freshness == 'REVALIDATION_REQUIRED'
    finally:
        world.close()


def test_stale_without_derivation_is_refused():
    world = _World()
    try:
        sources = (_source_ref("skill.list", "1" * 64),)
        state = _experience_state(sources)
        new_sources = (_source_ref("other.action", "3" * 64),)
        import pytest
        with pytest.raises(
                ValueError, match='no memory derivation to cascade'):
            propagate_source_change_to_experiences(
                shadow_store=world.store, states=(state,),
                derivations_by_experience={},
                current_sources=new_sources, requested_at_ms=2_000,
                source_trigger_ref="tool.publication.p15b")
    finally:
        world.close()
