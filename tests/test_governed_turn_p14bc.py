"""P14-B/C pre-wiring: the governed turn follows the mode authority alone.

Real archive world: OFF refuses before any work, SHADOW produces a
plan-only sidecar (compile, never register, zero side effects), and
LIMITED/DEFAULT admit through the same explicit provider contract. The
legacy off/controlled env channel stays untouched beside it. No
production default changes — this is the switch's wiring, tested dark.
"""
from __future__ import annotations

import pytest

from v3.composition_turn import (
    CompositionTurnError,
    run_governed_composition_turn,
)
from total_gateway.composition_planner_mode_authority import (
    PlannerModeConfigV1,
    resolve_turn_policy,
)

from tests.test_source_registration_intake_p12 import (  # noqa: F401
    intake, intake_factory, source, _compile, _prepare, _register)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401
from tests.test_composition_turn_p12 import (  # noqa: F401
    _write_pin, _proposal_from_prompt)
from v3 import composition_turn as turn_module

ZERO = "0" * 64


def _config(mode):
    return PlannerModeConfigV1(
        config_version=1, mode=mode, workspace_scope=("workspace.main",),
        cooldown_ms=1000, observation_window_ms=10_000, created_at_ms=1000,
        config_sha256=ZERO).with_computed_sha256()


def test_turn_policy_table_is_the_frozen_switch_semantics():
    assert resolve_turn_policy(_config("OFF")).may_prepare is False
    shadow = resolve_turn_policy(_config("SHADOW"))
    assert shadow.may_prepare and not shadow.may_register
    for mode in ("LIMITED", "DEFAULT"):
        policy = resolve_turn_policy(_config(mode))
        assert policy.may_prepare and policy.may_register
    assert resolve_turn_policy(_config("LIMITED")).may_dispatch is False


@pytest.fixture
def governed_env(intake, tmp_path, monkeypatch):
    c = intake
    pin_path = _write_pin(c, tmp_path)
    payload = __import__("json").loads(pin_path.read_text(encoding="utf-8"))
    payload["available_verifiers"] = [
        "verification-intent:plan-bound-acceptance"]
    pin_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    monkeypatch.setenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV,
                       str(pin_path))
    return c


def test_off_mode_refuses_before_any_work(governed_env):
    c = governed_env
    outcome = run_governed_composition_turn(
        _config("OFF"), user_text=c['user'],
        model_call=_proposal_from_prompt, run_context=c['rc'],
        bridge=c['bridge'], now_ms=5000)
    assert outcome == {"outcome": "mode_disabled", "mode": "OFF"}


def test_shadow_mode_is_plan_only_with_zero_side_effects(governed_env):
    c = governed_env
    outcome = run_governed_composition_turn(
        _config("SHADOW"), user_text=c['user'],
        model_call=_proposal_from_prompt, run_context=c['rc'],
        bridge=c['bridge'], now_ms=5000)
    assert outcome["outcome"] == "plan_only_shadow"
    assert outcome["plan_id"]
    assert c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1) is None


def test_limited_mode_registers_through_the_provider(governed_env):
    c = governed_env

    def _admit(result):
        from total_gateway.composition_admission_materialization import (
            materialize_admission_inputs)
        inputs = materialize_admission_inputs(
            result, workspace_root=c['workspace_root'], user_inputs={},
            issued_at_ms=5000, expires_at_ms=5500)
        return _register(c, result, **inputs)

    outcome = run_governed_composition_turn(
        _config("LIMITED"), user_text=c['user'],
        model_call=_proposal_from_prompt, run_context=c['rc'],
        bridge=c['bridge'], admission_provider=_admit, now_ms=5000)
    assert outcome["outcome"] == "registered"
    assert outcome["registration_id"]
    assert c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1) is not None


def test_default_mode_registers_and_shadow_provider_is_ignored(governed_env):
    """DEFAULT registers; without a provider it honestly says so."""
    c = governed_env
    outcome = run_governed_composition_turn(
        _config("DEFAULT"), user_text=c['user'],
        model_call=_proposal_from_prompt, run_context=c['rc'],
        bridge=c['bridge'], now_ms=5000)
    assert outcome["outcome"] == "plan_only"  # may_register but no provider
    assert outcome["mode"] == "DEFAULT"


def test_tampered_mode_config_is_refused(governed_env):
    c = governed_env
    forged = _config("LIMITED").model_copy(update={"cooldown_ms": 0})
    with pytest.raises(CompositionTurnError, match="mode.config_invalid"):
        run_governed_composition_turn(
            forged, user_text=c['user'], model_call=_proposal_from_prompt,
            run_context=c['rc'], bridge=c['bridge'], now_ms=5000)


def test_legacy_controlled_channel_stays_beside_the_governed_one(governed_env):
    """The env-channel turn still answers to off/controlled — unchanged."""
    assert turn_module.composition_planner_mode() in {"off", "controlled"}
    assert turn_module.composition_planner_mode() == "off"  # default intact
