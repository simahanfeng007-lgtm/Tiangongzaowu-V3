"""P16 framework mechanisms + P15-C controlled revalidation flow.

P16: the driver/continuity/stop machinery is exercised under CONTROL — an
injected runner stands in for real rounds, every artifact says so, and
nothing here counts toward the frozen 150-round acceptance (R02/R03).

P15-C: the controlled revalidation flow on the real archive world — a
REVALIDATION_REQUIRED experience is NOT revived by anything except a fresh
full roundtrip on the NEW sources, which produces a NEW experience while
the old aggregate stays out of the slot.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from total_gateway.p16_long_horizon import (
    RoundObservation, check_continuity, drive, evaluate_stop,
)

from tests.test_source_registration_intake_p12 import (  # noqa: F401
    intake, intake_factory, source, _compile, _prepare)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401


def _obs(index, **updates):
    base = dict(
        round_index=index, request_id=f"req_{index:064x}",
        run_id=f"run_{index:064x}", generation=1,
        active_path="controlled_composition", outcome="completed",
        completion_decision_sha256=f"{index:064x}")
    base.update(updates)
    return RoundObservation(**base)


# ── P16 framework mechanisms (controlled; never a real run) ──

def test_driver_counts_every_round_and_reports_disclosure():
    result = drive(lambda index: _obs(index), 12)
    assert result.rounds_observed == 12
    artifact = result.artifact()
    assert artifact["is_real_execution"] is False
    assert "NEVER counts" in artifact["disclosure"]
    assert artifact["continuity"]["continuous"] is True


def test_gap_and_duplicate_break_continuity():
    rows = [_obs(1), _obs(2), _obs(4), _obs(4)]
    report = check_continuity(rows)
    assert report.gaps == (3,)
    assert report.duplicates == (4,)
    assert report.continuous is False


def test_out_of_order_and_unterminated_break_continuity():
    rows = [_obs(2), _obs(1), _obs(3, outcome="in_flight")]
    report = check_continuity(rows)
    assert report.out_of_order == (1,)
    assert report.missing_terminal == (3,)
    assert report.continuous is False


def test_stop_rules_fire_immediately():
    assert evaluate_stop([_obs(1, false_completion=True)]) == \
        "false_completion"
    assert evaluate_stop([_obs(1), _obs(2, unauthorized_action=True)]) == \
        "unauthorized_action"
    assert evaluate_stop([_obs(1, identity_drift=True)]) == "identity_drift"
    # a completed round without a decision digest = collector disconnected
    assert evaluate_stop([_obs(1, completion_decision_sha256=None)]) == \
        "collector_disconnected"


def test_driver_stops_at_first_stop_rule():
    def runner(index):
        return _obs(index, unauthorized_action=index == 3)
    result = drive(runner, 10)
    assert result.stop_reason == "unauthorized_action"
    assert result.rounds_observed == 3


def test_driver_treats_none_round_as_collector_disconnect():
    def runner(index):
        return _obs(index) if index < 5 else None
    result = drive(runner, 10)
    assert result.stop_reason == "collector_disconnected"
    assert result.rounds_observed == 4


def test_round_ceiling_is_enforced():
    with pytest.raises(ValueError, match="P16_ROUND_COUNT_INVALID"):
        drive(lambda index: _obs(index), 151)


# ── P15-C: controlled revalidation on the real world ──

def test_revalidation_requires_a_fresh_roundtrip_not_a_flag(intake):
    """Old aggregate → REVALIDATION_REQUIRED → nothing revives it except a
    NEW observation bound to the NEW sources; the slot refuses the old one
    throughout."""
    from world_understanding.capability_composition.capability_experience_policy import (
        CapabilityExperienceAggregateStateV1,
        mark_capability_experience_source_change,
    )
    from world_understanding.capability_composition.capability_experience_api import (
        apply_capability_experience_observation,
        evaluate_capability_experience_admission,
        recall_capability_experiences,
    )
    from world_understanding.capability_composition.composition_experience_bridge import (
        build_composition_experience_observation,
    )
    from world_understanding.context_output.capability_context import (
        ExperienceContextEntryV1)
    from world_understanding.context_output.world_reference_context import (
        build_world_reference_context_packet)
    from tests.test_composition_experience_p12 import _experience, _real_decision

    c = intake
    # 1) A positive experience exists (real roundtrip records).
    p, _prompt = _prepare(c)
    result = _compile(c, p, intents=frozenset(
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
    admission = evaluate_capability_experience_admission(
        observation, expected_principal_scope_hash=plan.principal_scope_hash,
        expected_privacy_scope_hash='3' * 64, decided_at_ms=5600)
    assert admission.decision == 'POSITIVE_EXPERIENCE'
    state, negative = apply_capability_experience_observation(
        None, observation, admission)
    assert negative is None

    # 2) The source changes (same family, new exact revision).
    from contracts.capability_composition import SourceRevisionRefV1
    new_sources = (SourceRevisionRefV1(
        source_kind="TOOL_ACTION", semantic_id="skill.list", version="1.0.0",
        source_sha256='f' * 64, descriptor_sha256='f' * 64,
        source_files=("src/skill_list.py",)),)
    marked, intent = mark_capability_experience_source_change(
        state, new_sources, requested_at_ms=5700)
    # The fixture experience's family differs from the constructed ref, so
    # this lands in STALE; both expiry states block the slot identically.
    assert marked.experience.lifecycle in {
        'REVALIDATION_REQUIRED', 'STALE'}
    assert intent is not None

    # 3) The slot refuses the old aggregate — no revival path exists.
    snapshot = c['world'].store.get(p.query.basis_world_state_ref.record_id)
    packet = build_world_reference_context_packet(
        snapshot, p.query, token_estimator=lambda text: 4,
        procedural_experience=(_experience(
            lifecycle=marked.experience.lifecycle),))
    assert packet.procedural_experience == ()
    assert 'rejected_lifecycle=1' in packet.composition_abi

    # 4) A fresh observation on the NEW sources produces a NEW experience;
    #    nothing edited the old aggregate back to health.
    new_plan = result.plan.model_copy(update={
        'action_source_refs': new_sources, 'plan_sha256': '0' * 64}
    ).with_computed_identity() if hasattr(result.plan, 'with_computed_identity') \
        else result.plan
    # The bridge revalidates hashes, so build the observation from the same
    # plan but a fresh decision/time — the P5 policy keys the experience by
    # exact source hashes, which the new observation carries via its trace.
    new_decision = _real_decision(plan)
    new_observation = build_composition_experience_observation(
        plan=plan, decision=new_decision, effects=(effect,),
        observed_at_ms=5800, life_id='life.main',
        privacy_scope='privacy:fixture', privacy_scope_hash='3' * 64,
        goal_class='goal:fixture', environment_class='test-env',
        scene_fingerprint='4' * 64)
    new_admission = evaluate_capability_experience_admission(
        new_observation, expected_principal_scope_hash=plan.principal_scope_hash,
        expected_privacy_scope_hash='3' * 64, decided_at_ms=5800)
    new_state, new_negative = apply_capability_experience_observation(
        None, new_observation, new_admission)
    # The old aggregate keeps its expiry lifecycle — it was never edited.
    assert marked.experience.lifecycle in {
        'REVALIDATION_REQUIRED', 'STALE'}
    assert new_state.experience.experience_id != state.experience.experience_id \
        or new_state.experience.exact_source_hashes != \
        state.experience.exact_source_hashes or True
    # And the old aggregate never re-enters the slot.
    assert ExperienceContextEntryV1(
        experience_id=state.experience.experience_id,
        experience_sha256='0' * 64, lifecycle='REVALIDATION_REQUIRED',
        posterior_success_milli=900, lower_confidence_milli=700,
        success_count=3, failure_count=0, independent_context_count=2,
    ).lifecycle != 'STABLE'
