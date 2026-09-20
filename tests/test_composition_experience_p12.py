"""P12-R1F: capability experience reaches the one slot, facts write back.

Recall side: real P5 aggregate states are recalled by the system provider and
admitted into the capability context packet — STABLE only, counted refusals,
DATA-only ABI markers, digest-covered. Write-back side: the durable records
of a completed composition roundtrip become one P5 observation whose
attribution integrity is evaluated by the ORIGINAL P5 evaluator, then flows
through the ORIGINAL admission/statistics policy. Nothing here admits or
promotes experience by itself.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from contracts import canonical_sha256
from world_understanding.capability_composition.capability_experience_api import (
    apply_capability_experience_observation,
    evaluate_capability_experience_admission,
)
from world_understanding.capability_composition.capability_experience_policy import (
    CapabilityExperienceAggregateStateV1,
    CapabilityExperienceRecallQueryV1,
)
from world_understanding.capability_composition.capability_experience_api import (
    recall_capability_experiences,
)
from world_understanding.capability_composition.composition_experience_bridge import (
    build_composition_experience_observation,
    composition_outcome_from_decision,
)
from world_understanding.context_output.capability_context import (
    ExperienceContextEntryV1,
    NegativeEvidenceContextEntryV1,
)

from tests.test_source_registration_intake_p12 import (  # noqa: F401  fixtures
    intake, intake_factory, source, _compile, _prepare)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401

ZERO = "0" * 64


def _experience(lifecycle="STABLE", **updates):
    base = dict(
        experience_id="cex_" + "a" * 60, experience_sha256=ZERO,
        lifecycle=lifecycle, posterior_success_milli=900,
        lower_confidence_milli=700, success_count=3, failure_count=0,
        independent_context_count=2)
    base.update(updates)
    return ExperienceContextEntryV1(**base)


def _negative():
    return NegativeEvidenceContextEntryV1(
        evidence_id="nev_" + "b" * 60, evidence_sha256=ZERO,
        failure_category="EXECUTION_FAILURE", reason_codes=("r1",),
        source_revision_family="family:fixture",
        exact_source_hashes=(ZERO,))


def test_stable_experience_is_admitted_into_the_context_packet(intake):
    """The system provider's STABLE experience lands in the one slot."""
    from world_understanding.context_output.world_reference_context import (
        build_world_reference_context_packet)
    c = intake
    p, _prompt = _prepare(c)
    query = p.query
    snapshot = c['world'].store.get(query.basis_world_state_ref.record_id)
    packet = build_world_reference_context_packet(
        snapshot, query, token_estimator=lambda text: 4,
        procedural_experience=(_experience(),),
        negative_evidence=(_negative(),))
    assert packet is not None
    assert len(packet.procedural_experience) == 1
    assert packet.procedural_experience[0].experience_id.startswith('cex_')
    assert len(packet.negative_evidence) == 1
    assert packet.has_valid_sha256()
    assert 'experience_data_only=true' in packet.composition_abi
    assert 'stable_experience=1' in packet.composition_abi
    assert 'rejected_lifecycle=0' in packet.composition_abi


def test_non_stable_lifecycle_is_refused_and_counted(intake):
    from world_understanding.context_output.world_reference_context import (
        build_world_reference_context_packet)
    c = intake
    p, _prompt = _prepare(c)
    query = p.query
    snapshot = c['world'].store.get(query.basis_world_state_ref.record_id)
    packet = build_world_reference_context_packet(
        snapshot, query, token_estimator=lambda text: 4,
        procedural_experience=(_experience('STALE'),
                               _experience('REVALIDATION_REQUIRED'),
                               _experience('RETIRED'),
                               _experience('STABLE')))
    assert packet is not None
    assert len(packet.procedural_experience) == 1
    assert 'rejected_lifecycle=3' in packet.composition_abi


def test_cold_start_keeps_the_packet_byte_compatible(intake):
    from world_understanding.context_output.world_reference_context import (
        build_world_reference_context_packet)
    c = intake
    p, _prompt = _prepare(c)
    query = p.query
    snapshot = c['world'].store.get(query.basis_world_state_ref.record_id)
    legacy = build_world_reference_context_packet(
        snapshot, query, token_estimator=lambda text: 4)
    fresh = build_world_reference_context_packet(
        snapshot, query, token_estimator=lambda text: 4,
        procedural_experience=(), negative_evidence=())
    assert legacy.procedural_experience == fresh.procedural_experience == ()
    assert 'stable_experience=0' in fresh.composition_abi


def test_invalid_experience_entry_is_refused(intake):
    from world_understanding.context_output.world_reference_context import (
        build_world_reference_context_packet)
    c = intake
    p, _prompt = _prepare(c)
    query = p.query
    snapshot = c['world'].store.get(query.basis_world_state_ref.record_id)
    with pytest.raises(ValueError,
                       match='CAPABILITY_CONTEXT_EXPERIENCE_ENTRY_INVALID'):
        build_world_reference_context_packet(
            snapshot, query, token_estimator=lambda text: 4,
            procedural_experience=('not-an-entry',))


def _real_decision(plan):
    from total_gateway.completion_gate import CompletionDecision
    decision = CompletionDecision(
        request_id=plan.request_id, run_id=plan.run_id,
        generation=plan.generation, outcome='COMPLETED',
        reason_code='completion.executed',
        text_ready=True, execution_ready=True, artifacts_ready=True,
        delivery_ready=True, can_transition_request_completed=True,
        can_claim_platform_delivered=True, needs_reconciliation=False,
        execution_effect_states=(('eff_' + '1' * 64, 'SUCCEEDED'),),
        artifact_revision_states=(), delivery_parts=(),
        supporting_fact_ids=('fact_1',),
        decision_sha256='0' * 64)
    return decision.with_computed_sha256()


def test_roundtrip_records_become_a_p5_observation_then_recall(intake):
    """Write-back and recall close the loop through the original P5 policy."""
    c = intake
    result = _compile(c, _prepare(c)[0], intents=frozenset(
        {'verification-intent:plan-bound-acceptance'}))
    plan = result.plan
    decision = _real_decision(plan)
    effect = SimpleNamespace(
        claim=SimpleNamespace(effect_id='eff_' + '1' * 64),
        result=SimpleNamespace(fact_id='fact_1', evidence_sha256='2' * 64,
                               observed_at_ms=5500))
    observation = build_composition_experience_observation(
        plan=plan, decision=decision, effects=(effect,),
        observed_at_ms=5600, life_id='life.main',
        privacy_scope='privacy:fixture', privacy_scope_hash='3' * 64,
        goal_class='goal:fixture', environment_class='test-env',
        scene_fingerprint='4' * 64)
    assert observation.outcome == 'SUCCESS'
    assert observation.trace.terminal_effect_ids == ('eff_' + '1' * 64,)
    assert observation.trace.terminal_fact_ids == ('fact_1',)
    assert observation.attribution.state == 'PASS'
    admission = evaluate_capability_experience_admission(
        observation,
        expected_principal_scope_hash=plan.principal_scope_hash,
        expected_privacy_scope_hash='3' * 64,
        decided_at_ms=5600)
    assert admission.decision == 'POSITIVE_EXPERIENCE', admission.reason_codes
    state, negative = apply_capability_experience_observation(
        None, observation, admission)
    assert negative is None
    assert state.experience.success_count == 1
    # First positive admission lands in PROBATION: the P5 contract requires
    # repeated real-world support before an experience edge may mature.
    assert state.experience.lifecycle == 'PROBATION'
    # Recall the written-back aggregate under its own exact scope.
    query = CapabilityExperienceRecallQueryV1(
        principal_scope_hash=plan.principal_scope_hash,
        privacy_scope_hash='3' * 64,
        goal_class='goal:fixture',
        environment_class='test-env',
        current_source_revision_family=state.experience.source_revision_family,
        current_exact_source_hashes=state.experience.exact_source_hashes,
        now_ms=5700)
    items = recall_capability_experiences((state,), query)
    assert [item.experience_id for item in items] == \
        [state.experience.experience_id]
    # P5 recalls recent positive experience (PROBATION included); the model
    # slot additionally refuses anything but STABLE — the layered contract.
    assert items[0].lifecycle == 'PROBATION'


def test_failed_decision_maps_to_failure_not_success():
    assert composition_outcome_from_decision(
        SimpleNamespace(outcome='FAILED')) == 'FAILURE'
    assert composition_outcome_from_decision(
        SimpleNamespace(outcome='RECONCILE_REQUIRED')) == 'RECONCILE'
    with pytest.raises(ValueError):
        composition_outcome_from_decision(SimpleNamespace(outcome='WEIRD'))
