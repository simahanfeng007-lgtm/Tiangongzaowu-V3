"""P14: the mode runtime becomes the default planner switch.

Tests the full switch lifecycle: implicit OFF → SHADOW initialization →
effective mode resolution → turn policy per mode → LIMITED transition with
prerequisites → DEFAULT → rollback. Also verifies the mode file is the
sole durable state and the env-var legacy override still works.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from total_gateway.composition_mode_runtime import (
    PlannerModeStateError,
    current_turn_policy,
    effective_planner_mode,
    initialize_shadow_mode,
    load_mode_config,
    save_mode_config,
    should_register_composition_result,
    should_run_composition_turn,
)
from total_gateway.composition_planner_mode_authority import (
    PlannerModeConfigV1,
    PlannerModeTransitionV1,
    resolve_turn_policy,
    validate_mode_transition,
)

ZERO = "0" * 64


@pytest.fixture
def mode_file(tmp_path, monkeypatch):
    path = tmp_path / "planner_mode.json"
    monkeypatch.setenv("TIANGONG_PLANNER_MODE_CONFIG", str(path))
    # Also reset the legacy env var
    monkeypatch.delenv("TIANGONG_COMPOSITION_PLANNER_MODE", raising=False)
    return path


def _full_prereqs():
    return {name: True for name in (
        "p11_shadow_observations", "p11_formal_exit",
        "source_credentials", "verifier_coverage",
        "limited_observation_window")}


def test_implicit_off_when_no_config(mode_file):
    assert load_mode_config() is None
    assert effective_planner_mode() == "off"
    assert should_run_composition_turn() is False


def test_shadow_initialization(mode_file):
    config = initialize_shadow_mode()
    assert config.mode == "SHADOW"
    assert config.config_version == 1
    # Reload from file
    loaded = load_mode_config()
    assert loaded is not None
    assert loaded.mode == "SHADOW"
    assert loaded.has_valid_sha256()
    # Effective mode and policy
    assert effective_planner_mode() == "shadow"
    assert should_run_composition_turn() is True
    assert should_register_composition_result() is False  # plan-only


def test_limited_transition_with_prerequisites(mode_file):
    shadow = initialize_shadow_mode()
    transition = PlannerModeTransitionV1(
        from_mode="SHADOW", to_mode="LIMITED", decided_by="operator",
        reason="prerequisites met, controlled admission",
        decided_at_ms=shadow.created_at_ms + 120_000,
        resulting_config_version=2,
        transition_sha256=ZERO).with_computed_sha256()
    limited = validate_mode_transition(
        shadow, transition, prerequisites=_full_prereqs(),
        last_transition_at_ms=shadow.created_at_ms,
        now_ms=shadow.created_at_ms + 120_000)
    save_mode_config(limited)
    assert effective_planner_mode() == "limited"
    assert should_run_composition_turn() is True
    assert should_register_composition_result() is True  # admission active


def test_default_transition_and_rollback(mode_file):
    shadow = initialize_shadow_mode()
    t1 = PlannerModeTransitionV1(
        from_mode="SHADOW", to_mode="LIMITED", decided_by="operator",
        reason="step 1", decided_at_ms=shadow.created_at_ms + 120_000,
        resulting_config_version=2,
        transition_sha256=ZERO).with_computed_sha256()
    limited = validate_mode_transition(
        shadow, t1, prerequisites=_full_prereqs(),
        last_transition_at_ms=shadow.created_at_ms,
        now_ms=shadow.created_at_ms + 120_000)
    t2 = PlannerModeTransitionV1(
        from_mode="LIMITED", to_mode="DEFAULT", decided_by="operator",
        reason="step 2", decided_at_ms=limited.created_at_ms + 120_000,
        resulting_config_version=3,
        transition_sha256=ZERO).with_computed_sha256()
    default = validate_mode_transition(
        limited, t2, prerequisites=_full_prereqs(),
        last_transition_at_ms=limited.created_at_ms,
        now_ms=limited.created_at_ms + 120_000)
    save_mode_config(default)
    assert effective_planner_mode() == "default"
    # Rollback DEFAULT→OFF (legal rollback path)
    t3 = PlannerModeTransitionV1(
        from_mode="DEFAULT", to_mode="OFF", decided_by="operator",
        reason="rollback", decided_at_ms=default.created_at_ms + 5_000,
        resulting_config_version=4,
        transition_sha256=ZERO).with_computed_sha256()
    off = validate_mode_transition(
        default, t3, prerequisites={},
        last_transition_at_ms=default.created_at_ms,
        now_ms=default.created_at_ms + 5_000)
    save_mode_config(off)
    assert effective_planner_mode() == "off"


def test_legacy_env_override_still_works(mode_file, monkeypatch):
    initialize_shadow_mode()  # file says SHADOW
    monkeypatch.setenv("TIANGONG_COMPOSITION_PLANNER_MODE", "controlled")
    assert effective_planner_mode() == "controlled"


def test_corrupt_config_fails_closed(mode_file):
    mode_file.write_text("not json at all")
    with pytest.raises(PlannerModeStateError, match="corrupt"):
        load_mode_config()
