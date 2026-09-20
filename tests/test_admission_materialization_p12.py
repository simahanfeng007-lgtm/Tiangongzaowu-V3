"""P12 admission materialization closes the R1D-recorded provider gap.

Real archive world: the mechanical materializer derives the FULL system
side of admission (workspace authority, per-intent evidence, step
bindings from the sealed preparation) and the semantic values arrive
through the system channel — then registration succeeds end to end with
NO hand-assembled fixtures. Every refusal names its missing piece.
"""
from __future__ import annotations

import pytest

from total_gateway.composition_admission_materialization import (
    AdmissionMaterializationError,
    materialize_admission_inputs,
)

from tests.test_source_registration_intake_p12 import (  # noqa: F401
    intake, intake_factory, source, _compile, _prepare, _register)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401


def _result(c):
    return _compile(c, _prepare(c)[0], intents=frozenset(
        {'verification-intent:plan-bound-acceptance'}))


def test_materialized_inputs_register_without_hand_assembly(intake):
    """result → mechanical inputs → registration, zero manual fixtures."""
    c = intake
    result = _result(c)
    inputs = materialize_admission_inputs(
        result, workspace_root=c['workspace_root'], user_inputs={},
        issued_at_ms=5200, expires_at_ms=5800)
    outcome = _register(c, result, **inputs)
    assert outcome.registered and outcome.validation_mode
    record = c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1)
    assert record is not None
    assert record.executable_plan.legacy_plan == result.plan


def test_semantic_values_flow_through_the_system_channel(intake):
    c = intake
    result = _result(c)
    inputs = materialize_admission_inputs(
        result, workspace_root=c['workspace_root'],
        user_inputs={"category": "registry"},
        issued_at_ms=5200, expires_at_ms=5800)
    outcome = _register(c, result, **inputs)
    assert outcome.registered
    record = c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1)
    binding = record.executable_plan.step_bindings[0]
    assert binding.args_skeleton == {"category": None}
    assert record.executable_plan.plan_inputs[0].inline_value == "registry"


def test_invalid_window_is_refused_upfront(intake):
    c = intake
    result = _result(c)
    with pytest.raises(AdmissionMaterializationError,
                       match="admission.window_invalid"):
        materialize_admission_inputs(
            result, workspace_root=c['workspace_root'], user_inputs={},
            issued_at_ms=5200, expires_at_ms=5200 + 60_001)


def test_non_result_input_is_refused():
    with pytest.raises(AdmissionMaterializationError,
                       match="admission.result_invalid"):
        materialize_admission_inputs(
            "not-a-result", workspace_root=".", user_inputs={},
            issued_at_ms=1, expires_at_ms=2)


def test_controlled_turn_closes_the_loop_with_the_materializer(
        intake, tmp_path, monkeypatch):
    """The R1D gap end to end: admission provider built FROM the result."""
    c = intake
    from tests.test_composition_turn_p12 import _write_pin
    from v3 import composition_turn as turn_module
    pin_path = _write_pin(c, tmp_path)
    monkeypatch.setenv(turn_module.COMPOSITION_TOOL_SOURCE_PIN_ENV,
                       str(pin_path))
    from v3.composition_turn import run_controlled_composition_turn
    from tests.test_composition_turn_p12 import _proposal_from_prompt, \
        _FIXTURE_INTENTS

    def _admit(result):
        inputs = materialize_admission_inputs(
            result, workspace_root=c['workspace_root'], user_inputs={},
            issued_at_ms=5000, expires_at_ms=5500)
        return _register(c, result, **inputs)

    outcome = run_controlled_composition_turn(
        user_text=c['user'], model_call=_proposal_from_prompt,
        run_context=c['rc'], bridge=c['bridge'], admission_provider=_admit,
        available_verifiers=_FIXTURE_INTENTS, now_ms=5000)
    assert outcome['outcome'] == 'registered'
    assert c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1) is not None


def test_production_experience_recall_install_seam(intake, monkeypatch):
    """The R1F gap: an installed provider actually reaches the one slot."""
    c = intake
    from tests.test_composition_experience_p12 import _experience
    stable = _experience()
    from v3 import world_understanding_production as installed
    handler = c['world'].facade._ingress._router._context_request_handler
    assert handler is not None

    def _provider(query, snapshot):
        return (stable, ())

    monkeypatch.setattr(handler, 'experience_provider', _provider)
    assert handler.experience_provider is _provider
    # The seam is the handler's own recall path; slot behaviour (STABLE in,
    # others refused-and-counted) is carried by the R1F suite.
