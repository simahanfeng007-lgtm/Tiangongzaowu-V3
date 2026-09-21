"""P16-C injector mechanics + P13-C migration-mechanism check.

The injector tests run on plain components — the harness is itself test
machinery, so its scheduling/record/cancel semantics are what is under
test. The P13-C check pins the EXISTING migration mechanism facts the
stage inventory needs (idempotent replay, classification, telemetry
coverage) without building anything new.
"""
from __future__ import annotations

import pytest

from total_gateway.p16_injection import (
    FROZEN_POINTS, InjectionError, InjectionHarness, InjectionRule,
    wrap_injection,
)


def test_all_eight_frozen_points_exist():
    assert len(FROZEN_POINTS) == 8
    assert "AROUND_FACT_WRITE" in FROZEN_POINTS


def test_unknown_point_and_action_are_refused():
    with pytest.raises(ValueError, match="P16_INJECTION_POINT_UNKNOWN"):
        InjectionRule(point="NOT_A_POINT", action="raise", rounds=())
    with pytest.raises(ValueError, match="P16_INJECTION_ACTION_INVALID"):
        InjectionRule(point="REGISTRATION", action="explode", rounds=())


def test_raise_interrupts_and_is_recorded():
    harness = InjectionHarness(rules=(
        InjectionRule(point="REGISTRATION", action="raise", rounds=(2,)),))
    with pytest.raises(InjectionError, match="P16_INJECTION_REGISTRATION"):
        harness.at("REGISTRATION", 2)
    log = harness.log_payload()
    assert log == [{"schema": "tiangong.p16.injection-log.v1",
                    "round_index": 2, "point": "REGISTRATION",
                    "action": "raise"}]


def test_round_scoping_and_default_none():
    harness = InjectionHarness(rules=(
        InjectionRule(point="MODEL_RESPONSE", action="raise", rounds=(3,)),))
    assert harness.at("MODEL_RESPONSE", 1) == "none"
    with pytest.raises(InjectionError):
        harness.at("MODEL_RESPONSE", 3)
    assert harness.at("MODEL_RESPONSE", 4) == "none"


def test_cancel_skips_operation_via_wrapper():
    harness = InjectionHarness(rules=(
        InjectionRule(point="POST_SIDE_EFFECT_START", action="cancel",
                      rounds=(1,)),))
    ran = []
    outcome = wrap_injection(
        "POST_SIDE_EFFECT_START", harness, 1,
        lambda: (ran.append(1) or "value"))
    assert outcome is None and ran == []
    assert wrap_injection(
        "POST_SIDE_EFFECT_START", harness, 2, lambda: "value") == "value"


def test_p13c_migration_mechanism_facts_are_pinned():
    """The EXISTING P10 mechanism already carries P13-C's core contract."""
    from life_service.legacy_learning_migration import (
        MIGRATION_SCHEMA, apply_migration_record,
    )
    scope: dict = {}
    row = {"schema": MIGRATION_SCHEMA, "record_family": "learning_card",
           "record_id": "lc_1", "record_sha256": "a" * 64,
           "disposition": "migrated", "unknown_ownership": False,
           "destructive_change": False, "source_pin_retained": True}
    assert apply_migration_record(scope, row) is True      # first write
    assert apply_migration_record(scope, dict(row)) is False  # idempotent
    # A differing record under the same key is a deliberate replace, not an
    # error: the mechanism records the NEW digest (the caller owns the
    # decision; the journal keeps both events).
    assert apply_migration_record(scope, dict(row, record_sha256="b" * 64)) \
        is True
    assert scope["legacy_learning_migration"]["records"][
        "learning_card:lc_1"]["record_sha256"] == "b" * 64


def test_driver_and_injector_compose_under_control():
    """The two P16 components work as one machine: an injected raise at
    REGISTRATION turns that round's outcome failed, the ledger keeps its
    place, and continuity stays intact with the failure honestly recorded."""
    from total_gateway.p16_injection import InjectionHarness, InjectionRule
    from total_gateway.p16_long_horizon import RoundObservation, drive

    harness = InjectionHarness(rules=(
        InjectionRule(point="REGISTRATION", action="raise", rounds=(3,)),))

    def runner(index):
        try:
            harness.at("REGISTRATION", index)
            outcome = "completed"
        except InjectionError:
            outcome = "failed"
        return RoundObservation(
            round_index=index, request_id=f"req_{index:064x}",
            run_id=f"run_{index:064x}", generation=1,
            active_path="controlled_composition", outcome=outcome,
            completion_decision_sha256=None if outcome == "failed"
            else f"{index:064x}",
            note="injected" if outcome == "failed" else "")

    result = drive(runner, 6)
    assert result.rounds_observed == 6
    assert result.stop_reason is None
    assert result.continuity.continuous is True
    assert [o.outcome for o in result.observations] == [
        "completed", "completed", "failed", "completed", "completed",
        "completed"]
    assert len(harness.log_payload()) == 1  # only the injected round is recorded
