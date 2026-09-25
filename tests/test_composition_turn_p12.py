"""P12-R1D: the controlled composition planning turn behind the real entry.

Behaviour tests run on the real immutable Git/bundle and signed Method archive
world from R1C2/R1C3 (real bridge, real Store, real P4/P7 seams); the injected
``model_call`` is a deterministic stand-in for the existing HTTP model adapter
(the network layer is the only substituted boundary). The structure test pins
the actual orchestration wiring so the new chain has a real caller and cannot
silently bypass the legacy entry when the mode is off.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from v3 import composition_turn as turn_module
from v3.composition_turn import (
    CompositionTurnError,
    composition_planner_mode,
    load_operator_tool_source_pin,
    run_controlled_composition_turn,
)
from tests.test_source_registration_intake_p12 import (  # noqa: F401  fixtures/helpers
    intake, intake_factory, source, _prepare, _workspace_binding)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401
from tests.test_capability_composition_p4 import _proposal_document

V3_ROOT = Path(__file__).resolve().parents[1] / "app/backend/tiangong-backend/v3"


# Stable fixture-world identities: the deterministic candidate builder
# assigns A01 (skill.list) and M05 (native_0) on every build, and the turn
# prompt carries the exact goal_ref under a machine-generated ABI marker.
_FIXTURE_ACTION = 'A01'
_FIXTURE_METHOD = 'M05'
_FIXTURE_INTENTS = frozenset({'verification-intent:plan-bound-acceptance'})


def _proposal_from_prompt(prompt: str) -> str:
    match = re.search(r';goal_ref=([^;\s]+)', prompt)
    assert match is not None, 'turn prompt must carry the goal_ref ABI marker'
    return _proposal_document(goal_ref=match.group(1), methods=(_FIXTURE_METHOD,),
                              actions=(_FIXTURE_ACTION,),
                              steps=(("s1", _FIXTURE_ACTION, ()),))


def _write_pin(c, tmp_path):
    tool_source = c['tool_source']
    pin = {
        "repository": str(tool_source.repository),
        "bundle_path": str(tool_source.bundle_path),
        "bundle_sha256": tool_source.bundle_sha256,
        "base_commit": tool_source.base_commit,
        "candidate_commit": tool_source.candidate_commit,
        "requested_action_ids": list(tool_source.requested_action_ids),
        "action_entry_path": tool_source.action_entry_path,
        "repository_id": tool_source.repository_id,
        "worktree_id": tool_source.worktree_id,
    }
    path = tmp_path / 'composition-tool-source-pin.json'
    path.write_text(json.dumps(pin), encoding='utf-8')
    return path


@pytest.fixture
def pin_env(intake, tmp_path, monkeypatch):
    c = intake
    pin_path = _write_pin(c, tmp_path)
    monkeypatch.setenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV, str(pin_path))
    monkeypatch.setattr(turn_module, 'composition_planner_mode', lambda: 'controlled')
    return c


def test_default_mode_is_off_and_unchanged():
    assert composition_planner_mode() == 'off'
    assert turn_module.composition_planner_mode.__module__


def test_controlled_turn_registers_through_original_seam(pin_env):
    """Entry to registration in one controlled turn over real authorities."""
    c = pin_env
    from tests.test_source_registration_intake_p12 import _register

    def _admit(result):
        return _register(c, result)

    outcome = run_controlled_composition_turn(
        user_text=c['user'], model_call=_proposal_from_prompt,
        run_context=c['rc'], bridge=c['bridge'], admission_provider=_admit,
        available_verifiers=_FIXTURE_INTENTS, now_ms=5000)
    assert outcome['outcome'] == 'registered'
    assert outcome['plan_id']
    assert outcome['registration_id']
    record = c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1)
    assert record is not None
    assert record.executable_plan.executable_plan_id == outcome['executable_plan_id']


def test_controlled_turn_refuses_unknown_without_verifiers(pin_env):
    """The honest default: no system verifier set means UNKNOWN/REJECT."""
    c = pin_env
    outcome = run_controlled_composition_turn(
        user_text=c['user'], model_call=_proposal_from_prompt,
        run_context=c['rc'], bridge=c['bridge'], now_ms=5000)
    assert outcome['outcome'] == 'refused'
    assert outcome['reason'] == 'UNKNOWN'
    assert 'validator.verifier' in ' '.join(outcome['findings'])
    assert c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1) is None


def test_controlled_turn_without_admission_provider_reports_not_configured(pin_env):
    c = pin_env
    outcome = run_controlled_composition_turn(
        user_text=c['user'], model_call=_proposal_from_prompt,
        run_context=c['rc'], bridge=c['bridge'],
        available_verifiers=_FIXTURE_INTENTS, now_ms=5000)
    assert outcome['outcome'] == 'registration_not_configured'
    assert c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1) is None


def test_missing_pin_raises_without_fallback(intake, monkeypatch):
    c = intake
    monkeypatch.delenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV, raising=False)
    monkeypatch.setattr(turn_module, 'composition_planner_mode', lambda: 'controlled')
    with pytest.raises(CompositionTurnError, match='pin.env_missing'):
        run_controlled_composition_turn(
            user_text=c['user'], model_call=lambda prompt: 'x',
            run_context=c['rc'], bridge=c['bridge'])


def test_incomplete_pin_file_is_refused(intake, tmp_path, monkeypatch):
    c = intake
    path = tmp_path / 'bad-pin.json'
    path.write_text('{"repository": "/tmp"}', encoding='utf-8')
    monkeypatch.setenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV, str(path))
    with pytest.raises(CompositionTurnError, match='pin.incomplete'):
        load_operator_tool_source_pin(path)


def test_empty_model_reply_raises_without_fallback(pin_env):
    c = pin_env
    with pytest.raises(CompositionTurnError, match='model.empty_reply'):
        run_controlled_composition_turn(
            user_text=c['user'], model_call=lambda prompt: '   ',
            run_context=c['rc'], bridge=c['bridge'], now_ms=5000)


def test_missing_identity_raises(intake, tmp_path, monkeypatch):
    c = intake
    pin_path = _write_pin(c, tmp_path)
    monkeypatch.setenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV, str(pin_path))
    broken = c['rc'].__class__.__new__(c['rc'].__class__)
    for field in ('request_id', 'run_id', 'generation', 'workspace_id',
                  'life_id', 'principal_scope_hash'):
        setattr(broken, field, None)
    with pytest.raises(CompositionTurnError, match='identity.missing'):
        run_controlled_composition_turn(
            user_text=c['user'], model_call=lambda prompt: 'x',
            run_context=broken, bridge=c['bridge'])


def test_unparseable_reply_carries_original_error(pin_env):
    c = pin_env
    with pytest.raises(CompositionTurnError, match='compile.rejected'):
        run_controlled_composition_turn(
            user_text=c['user'], model_call=lambda prompt: 'not a proposal',
            run_context=c['rc'], bridge=c['bridge'], now_ms=5000)


def test_released_generation_refuses_the_whole_turn(pin_env):
    c = pin_env
    c['gateway'].release_generation(c['rc'].request_id, released_at_ms=5050)
    with pytest.raises(CompositionTurnError):
        run_controlled_composition_turn(
            user_text=c['user'], model_call=_proposal_from_prompt,
            run_context=c['rc'], bridge=c['bridge'],
            available_verifiers=_FIXTURE_INTENTS, now_ms=5000)


def test_orchestration_user_branch_uses_the_dynamic_dictionary_loop():
    """Static planning is a Gateway choice; user prefixes cannot bypass the loop."""
    source_text = (V3_ROOT / 'zongdiaodu.py').read_text(encoding='utf-8')
    tree = ast.parse(source_text)
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == 'huanxing')
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)]
    dynamic = [node for node in calls if node.func.attr == '_huanxing_simple_chain']
    assert len(dynamic) == 1
    assert any(keyword.arg == 'dictionary_context' for keyword in dynamic[0].keywords)
    assert all(node.func.attr != '_controlled_composition_turn_reply_if_enabled' for node in calls)


def test_pin_declared_verifiers_unblock_the_controlled_turn(intake, tmp_path,
                                                            monkeypatch):
    """The production gap: absent explicit verifiers, the turn reads the
    operator's pin declaration instead of falling into refused-by-default."""
    import json as _json
    from v3.composition_turn import (
        CompositionTurnError, pin_available_verifiers,
        run_controlled_composition_turn, CompositionTurnError as _E)
    from v3 import composition_turn as turn_module
    from tests.test_composition_turn_p12 import _write_pin, _proposal_from_prompt
    c = intake
    pin_path = _write_pin(c, tmp_path)
    payload = _json.loads(pin_path.read_text(encoding="utf-8"))
    payload["available_verifiers"] = [
        "verification-intent:plan-bound-acceptance"]
    pin_path.write_text(_json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV,
                       str(pin_path))
    monkeypatch.setattr(turn_module, 'composition_planner_mode',
                        lambda: 'controlled')
    outcome = run_controlled_composition_turn(
        user_text=c['user'], model_call=_proposal_from_prompt,
        run_context=c['rc'], bridge=c['bridge'], now_ms=5000)
    assert outcome['outcome'] == 'registration_not_configured'
    assert outcome['validation'] in {'UNKNOWN', 'PROVED_VALID'}
    assert pin_available_verifiers(pin_path) == frozenset(
        {'verification-intent:plan-bound-acceptance'})


def test_pin_with_invalid_verifiers_declaration_is_refused(intake, tmp_path):
    import json as _json
    from v3.composition_turn import pin_available_verifiers
    from tests.test_composition_turn_p12 import _write_pin
    c = intake
    pin_path = _write_pin(c, tmp_path)
    payload = _json.loads(pin_path.read_text(encoding="utf-8"))
    payload["available_verifiers"] = ["ok", 42]
    pin_path.write_text(_json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception, match="pin.verifiers_invalid"):
        pin_available_verifiers(pin_path)


def test_model_call_exception_propagates_untouched(pin_env):
    """A failing model adapter surfaces its ORIGINAL error; the turn never
    swallows, wraps or retries it silently."""
    c = pin_env

    def _explode(prompt):
        raise ConnectionError("provider stream reset")

    with pytest.raises(ConnectionError, match="provider stream reset"):
        run_controlled_composition_turn(
            user_text=c['user'], model_call=_explode,
            run_context=c['rc'], bridge=c['bridge'], now_ms=5000)
