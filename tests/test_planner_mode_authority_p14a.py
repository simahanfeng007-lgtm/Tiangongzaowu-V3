"""P14-A draft: the mode authority refuses what the switch must refuse.

Contract tests over the pure state machine: one-step forward progression,
named prerequisites (the P11 formal exit is machine-checked), cooldowns,
rollback paths, an A0 ceiling that cannot be written away, model deciders
rejected, tampering and restart drift fail closed. This is a PRE-STUDY:
nothing here changes the shipped production default.
"""
from __future__ import annotations

import pytest

from total_gateway.composition_planner_mode_authority import (
    PlannerModeAuthorityError,
    PlannerModeConfigV1,
    PlannerModeTransitionV1,
    check_restart_consistency,
    validate_mode_transition,
)

ZERO = "0" * 64


def _config(mode="OFF", version=1, cooldown=1000, scope=("workspace.main",)):
    return PlannerModeConfigV1(
        config_version=version, mode=mode, workspace_scope=scope,
        cooldown_ms=cooldown, observation_window_ms=10_000,
        created_at_ms=1_000, config_sha256=ZERO).with_computed_sha256()


def _transition(current, target, *, at=5_000, by="operator",
                version=None, reason="staged rollout step"):
    return PlannerModeTransitionV1(
        from_mode=current.mode, to_mode=target, decided_by=by, reason=reason,
        decided_at_ms=at,
        resulting_config_version=current.config_version + 1
        if version is None else version,
        transition_sha256=ZERO).with_computed_sha256()


def _full_prerequisites():
    return {name: True for names in (
        ("p11_shadow_observations",),
        ("p11_formal_exit", "source_credentials", "verifier_coverage"),
        ("p11_formal_exit", "limited_observation_window",
         "source_credentials", "verifier_coverage")) for name in names}


def test_forward_progression_is_one_step_at_a_time():
    off = _config("OFF")
    shadow = validate_mode_transition(
        off, _transition(off, "SHADOW"), prerequisites=_full_prerequisites(),
        last_transition_at_ms=1_000, now_ms=5_000)
    assert shadow.mode == "SHADOW" and shadow.config_version == 2
    limited = validate_mode_transition(
        shadow, _transition(shadow, "LIMITED", at=9_000),
        prerequisites=_full_prerequisites(),
        last_transition_at_ms=5_000, now_ms=9_000)
    assert limited.mode == "LIMITED"


def test_forward_skips_are_refused():
    off = _config("OFF")
    for target in ("LIMITED", "DEFAULT"):
        with pytest.raises(PlannerModeAuthorityError,
                           match="forward_skip_forbidden"):
            validate_mode_transition(
                off, _transition(off, target),
                prerequisites=_full_prerequisites(),
                last_transition_at_ms=0, now_ms=99_000)
    shadow = _config("SHADOW", version=2)
    with pytest.raises(PlannerModeAuthorityError,
                       match="forward_skip_forbidden"):
        validate_mode_transition(
            shadow, _transition(shadow, "DEFAULT"),
            prerequisites=_full_prerequisites(),
            last_transition_at_ms=0, now_ms=99_000)


def test_rollback_paths_are_legal_without_prerequisites():
    limited = _config("LIMITED", version=3)
    home = validate_mode_transition(
        limited, _transition(limited, "OFF"),
        prerequisites={}, last_transition_at_ms=0, now_ms=5_000)
    assert home.mode == "OFF"
    default = _config("DEFAULT", version=4)
    one_step_back = validate_mode_transition(
        default, _transition(default, "LIMITED"),
        prerequisites={}, last_transition_at_ms=0, now_ms=5_000)
    assert one_step_back.mode == "LIMITED"


def test_missing_prerequisites_are_named_and_include_p11_exit():
    shadow = _config("SHADOW", version=2)
    with pytest.raises(PlannerModeAuthorityError,
                       match="prerequisite_missing: p11_formal_exit"):
        validate_mode_transition(
            shadow, _transition(shadow, "LIMITED"),
            prerequisites={"source_credentials": True,
                           "verifier_coverage": True},
            last_transition_at_ms=0, now_ms=99_000)


def test_cooldown_blocks_rushed_forward_transition():
    off = _config("OFF", cooldown=10_000)
    with pytest.raises(PlannerModeAuthorityError, match="cooldown_active"):
        validate_mode_transition(
            off, _transition(off, "SHADOW", at=5_000),
            prerequisites=_full_prerequisites(),
            last_transition_at_ms=1_000, now_ms=5_000)


def test_model_can_never_be_the_decider():
    off = _config("OFF")
    with pytest.raises(Exception):
        PlannerModeTransitionV1(
            from_mode="OFF", to_mode="SHADOW", decided_by="model",
            reason="i think it is ready", decided_at_ms=5_000,
            resulting_config_version=2, transition_sha256=ZERO)


def test_a0_ceiling_is_structural():
    with pytest.raises(Exception):
        PlannerModeConfigV1(
            config_version=1, mode="LIMITED", workspace_scope=("w",),
            risk_ceiling="A1",  # type: ignore[arg-type]
            cooldown_ms=0, observation_window_ms=0, created_at_ms=0,
            config_sha256=ZERO)


def test_tampered_hashes_fail_closed():
    off = _config("OFF")
    forged = off.model_copy(update={"cooldown_ms": 0})
    with pytest.raises(PlannerModeAuthorityError, match="config.hash_invalid"):
        validate_mode_transition(
            forged, _transition(off, "SHADOW"),
            prerequisites=_full_prerequisites(),
            last_transition_at_ms=0, now_ms=99_000)


def test_noop_and_source_mismatch_are_refused():
    off = _config("OFF")
    with pytest.raises(PlannerModeAuthorityError, match="noop_forbidden"):
        validate_mode_transition(
            off, _transition(off, "OFF"),
            prerequisites=_full_prerequisites(),
            last_transition_at_ms=0, now_ms=99_000)
    with pytest.raises(PlannerModeAuthorityError, match="source_mismatch"):
        validate_mode_transition(
            off, _transition(_config("SHADOW", version=1), "LIMITED"),
            prerequisites=_full_prerequisites(),
            last_transition_at_ms=0, now_ms=99_000)


def test_restart_drift_is_detected():
    stored = _config("SHADOW", version=2)
    drifted = _config("SHADOW", version=3)
    with pytest.raises(PlannerModeAuthorityError, match="restart.drift"):
        check_restart_consistency(stored, drifted)
    check_restart_consistency(stored, stored)
