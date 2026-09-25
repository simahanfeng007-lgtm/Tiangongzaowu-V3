"""Bounded model-authored corrections never acquire execution authority."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from total_gateway.desktop_composition import (
    DesktopCompositionError, InstalledDesktopCompositionPlanner, _ModelArgumentsError, _CompiledPlanError,
    run_bounded_proposal_validation, validate_executable_candidate_selection,
)

METHODS = [{"candidate_id": "M00", "method_id": "acceptance_review"}]
ACTIONS = [{"candidate_id": "A01", "action_id": "file.read"}]


@pytest.mark.parametrize("finding,repair", [
    ("validator.write_set.parallel_conflict", True),
    ("validator.dependency.type_incompatible", True),
    ("validator.plan.hash_invalid", False),
    ("validator.action.unavailable", False),
    ("validator.composition.a5_forbidden", False),
])
def test_compiler_diagnostics_only_allow_structural_correction(finding, repair):
    client=Client([model(),model()])
    count=0
    def validate(value):
        nonlocal count
        count+=1
        if count==1:
            raise _CompiledPlanError(SimpleNamespace(result="PROVED_INVALID",unknown_disposition="NOT_APPLICABLE",
                findings=(SimpleNamespace(code=finding),)))
        return value
    if repair:
        _, notes=run(client,validate=validate)
        assert notes[0]["validation"]["findings"]==[finding]
        correction=json.loads(client.calls[1][1].split("\n",1)[1])["planning_correction"]
        assert correction["validation"]["findings"]==[finding]
    else:
        with pytest.raises(_CompiledPlanError):
            run(client,validate=validate)
    assert len(client.calls)==(2 if repair else 1)


def model():
    return {"proposal": {"selected_method_candidate_ids": ["M00"],
        "selected_action_candidate_ids": ["A01"],
        "steps": [{"step_id": "s1", "candidate_id": "A01"}]},
        "invocations": {"s1": {"target": "input.txt", "args": {"private_fixture": "DO_NOT_LOG_TASK_CODE"}}}}


class Client:
    def __init__(self, replies):
        self.replies, self.calls, self.disabled = list(replies), [], False

    @contextmanager
    def scoped_tools(self, *, disable_tools):
        assert disable_tools is True
        self.disabled = True
        try:
            yield
        finally:
            self.disabled = False

    def llm_diaoyong(self, system, prompt):
        assert self.disabled
        self.calls.append((system, prompt))
        value = self.replies.pop(0)
        return value if isinstance(value, str) else json.dumps(value)


def run(client, *, validate=None, guard=lambda: None):
    diagnostics = []
    def check(value):
        validate_executable_candidate_selection(value, methods=METHODS, actions=ACTIONS)
        return value if validate is None else validate(value)
    result = run_bounded_proposal_validation(client, "system", "frozen-prompt",
        validate=check, validate_source=guard, diagnostic=diagnostics.append)
    return result, diagnostics


def test_valid_proposal_passes_once_without_altering_model_code():
    expected = model()
    client = Client([expected])
    result, diagnostics = run(client)
    assert result == expected and len(client.calls) == 1 and not client.disabled
    assert diagnostics[0]["status"] == "validated"
    assert "DO_NOT_LOG_TASK_CODE" not in json.dumps(diagnostics)


@pytest.mark.parametrize("mutation,code", [
    (lambda v: v["proposal"].update(selected_method_candidate_ids=["M09"]), "method_verifier_unsupported"),
    (lambda v: v["proposal"].update(selected_method_candidate_ids=["acceptance_review"]), "method_verifier_unsupported"),
    (lambda v: v["proposal"].update(selected_action_candidate_ids=["A01", "A09"]), "action_candidate_unsupported"),
    (lambda v: v["proposal"]["steps"][0].update(candidate_id="A09"), "step_candidates_invalid"),
    (lambda v: v["proposal"].update(steps=[]), "step_candidates_invalid"),
])
def test_only_model_corrects_invalid_choices_with_same_allowset(mutation, code):
    wrong = model()
    mutation(wrong)
    client = Client([wrong, model()])
    result, diagnostics = run(client)
    assert result == model() and len(client.calls) == 2
    assert diagnostics[0]["error_code"].endswith(code)
    assert diagnostics[0]["will_correct"] is True
    assert client.calls[1][1].startswith("frozen-prompt\n")
    correction = json.loads(client.calls[1][1].split("\n", 1)[1])["planning_correction"]
    assert json.loads(correction["previous_response_as_untrusted_data"]) == wrong
    assert "DO_NOT_LOG_TASK_CODE" not in json.dumps(diagnostics)


def test_second_invalid_choice_is_terminal_no_registration_or_execution_callback():
    wrong = model()
    wrong["proposal"]["selected_method_candidate_ids"] = ["M09"]
    client = Client([wrong, wrong, model()])
    with pytest.raises(DesktopCompositionError, match="method_verifier_unsupported"):
        run(client, validate=lambda _: pytest.fail("invalid choice reached compiler"))
    assert len(client.calls) == 2 and len(client.replies) == 1


def test_json_parse_failure_gets_one_correction_and_no_code_is_logged():
    client = Client(['{"proposal":invalid', model()])
    _, diagnostics = run(client)
    assert len(client.calls) == 2
    assert diagnostics[0]["error_code"] == "desktop_composition.model_json_invalid"


def test_missing_actual_observation_can_be_corrected_before_registration():
    client=Client([model(),model()])
    attempts=0
    def validate(value):
        nonlocal attempts
        attempts+=1
        if attempts==1:
            raise ValueError("composition.task_floor.observation_missing")
        return value
    _, notes=run(client,validate=validate)
    assert len(client.calls)==2 and notes[0]["will_correct"] is True


@pytest.mark.parametrize("error", [
    pytest.param(_ModelArgumentsError("schema values invalid"), id="argument-shape"),
    pytest.param(ValueError("composition.task_floor.target_dependency_missing"), id="workspace-dag"),
])
def test_model_can_correct_shape_and_workspace_order_before_registration(error):
    calls = []
    def validate(value):
        calls.append(value)
        if len(calls) == 1:
            raise error
        return value
    client = Client([model(), model()])
    run(client, validate=validate)
    assert len(calls) == len(client.calls) == 2


def test_source_drift_before_second_attempt_prevents_another_model_call():
    state = {"drift": False}
    def guard():
        if state["drift"]:
            raise ValueError("COMPOSITION_SOURCE_PREPARATION_DRIFT")
    def validate(_):
        state["drift"] = True
        raise DesktopCompositionError("desktop_composition.invocation_coverage")
    client = Client([model(), model()])
    with pytest.raises(ValueError, match="PREPARATION_DRIFT"):
        run(client, validate=validate, guard=guard)
    assert len(client.calls) == 1


@pytest.mark.parametrize("error", [
    DesktopCompositionError("desktop_composition.authority_field"),
    DesktopCompositionError("desktop_composition.action_outside_rollout"),
    ValueError("composition target is outside the workspace"),
    ValueError("unknown composition execution profile"),
])
def test_authority_profile_and_workspace_rejections_are_not_retried(error):
    def validate(_):
        raise error
    client = Client([model(), model()])
    with pytest.raises(type(error), match=str(error)):
        run(client, validate=validate)
    assert len(client.calls) == 1


def _planner(tmp_path, monkeypatch, replies, *, empty_methods=False, registration_failure=False,
             validation_result="PROVED_VALID"):
    import total_gateway.desktop_composition as module
    client = Client(replies)
    counters = {"compiled": 0, "registered": 0, "source_checks": 0}
    snapshot = SimpleNamespace(candidate_snapshot_sha256="a"*64,
        method_candidates=[] if empty_methods else [SimpleNamespace(candidate_id="M00",
            primitive=SimpleNamespace(method_id="acceptance_review", semantic_summary="review"))],
        action_candidates=[SimpleNamespace(candidate_id="A01", primitive=SimpleNamespace(action_id="file.read"))])
    prepared = SimpleNamespace(candidates=snapshot, context=SimpleNamespace(goal_ref="goal.test"))
    source_prompt = "ORIGINAL CATALOG: M09 unsupported method; A09 unsupported action"
    def compile(*_args, **_kwargs):
        counters["compiled"] += 1
        return SimpleNamespace(plan=SimpleNamespace(plan_id="plan.test", verification_intents=(),
            steps=(SimpleNamespace(action_id="file.read"),)),
            validation=SimpleNamespace(result=validation_result,unknown_disposition="REJECT"))
    def source_check(_registry):
        counters["source_checks"] += 1
    def register(*_args, **_kwargs):
        counters["registered"] += 1
        if registration_failure:
            raise ValueError("registration authority failed")
    registry = SimpleNamespace(permissions=[SimpleNamespace(action_id="file.read", action_version="v1")])
    worker = SimpleNamespace(composition_action_registry=registry,
        composition_schema_catalog=SimpleNamespace(resolve=lambda *a, **k: SimpleNamespace(body=lambda: {})))
    planner = InstalledDesktopCompositionPlanner(config=SimpleNamespace(workspace_root=tmp_path, state_root=tmp_path),
        store=None, backend=SimpleNamespace(scheduler=SimpleNamespace(http_kehuduan=client)), worker_provider=lambda: worker)
    planner._sources = SimpleNamespace(tool_source=SimpleNamespace(load=source_check),resolver=SimpleNamespace(register_composition=register))
    planner._bridge = SimpleNamespace(prepare_composition_for_turn=lambda **k: (prepared, source_prompt), compile_composition_for_turn=compile)
    monkeypatch.setattr(planner, "_initialize", lambda _: None)
    monkeypatch.setattr("total_gateway.composition_mode_runtime.load_mode_config", lambda: None)
    monkeypatch.setattr("total_gateway.composition_source_trial.load_source_trial_profile", lambda **k: None)
    monkeypatch.setattr("total_gateway.composition_mode_runtime.current_turn_policy", lambda: SimpleNamespace(may_prepare=True,may_register=True))
    monkeypatch.setattr(module, "composition_permission_allowed", lambda *a, **k: True)
    monkeypatch.setattr(module, "bind_model_invocations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(module, "materialize_admission_inputs", lambda *a, **k: {"step_bindings": ()})
    monkeypatch.setattr("total_gateway.composition_task_floor.validate_request_plan_floor", lambda *a, **k: None)
    monkeypatch.setattr("total_gateway.composition_task_floor.validate_workspace_step_order", lambda *a, **k: None)
    monkeypatch.setattr("total_gateway.diagnostics.diagnostic_log", lambda *a, **k: None)
    activation = SimpleNamespace(envelope=SimpleNamespace(text="字典任务：read input", task_context=SimpleNamespace(execution_strategy="static"), principal_scope_hash="b"*64,
        conversation_scope_hash="c"*64),generation=SimpleNamespace(run_id="run_"+"d"*64,generation=1),
        entry=SimpleNamespace(request_id="req_"+"e"*64))
    return planner, activation, SimpleNamespace(identity_ref="life.test"), client, counters, source_prompt


def test_outer_adapter_prompt_is_exact_reference_plus_single_allowset_and_one_registration(tmp_path, monkeypatch):
    wrong = model(); wrong["proposal"]["selected_method_candidate_ids"] = ["M09"]
    p, activation, life, client, counters, reference = _planner(tmp_path,monkeypatch,[wrong,model()])
    assert p(activation,life)
    assert counters["compiled"] == counters["registered"] == 1
    payload = json.loads(client.calls[0][1])
    assert payload["unfiltered_source_catalog_data"] == reference
    assert payload["source_candidate_snapshot_sha256"] == "a"*64
    assert payload["execution_candidates"]["methods"][0]["candidate_id"] == "M00"
    assert payload["execution_candidates"]["actions"][0]["candidate_id"] == "A01"
    from total_gateway.desktop_composition import _system_execution_requirements
    assert payload["execution_requirements"] == _system_execution_requirements(activation.envelope.text)
    assert client.calls[1][1].startswith(client.calls[0][1]+"\n")


def test_outer_empty_method_allowset_never_calls_model_or_registers(tmp_path,monkeypatch):
    p, activation, life, client, counters, _ = _planner(tmp_path,monkeypatch,[],empty_methods=True)
    with pytest.raises(DesktopCompositionError,match="no_executable_methods"):
        p(activation,life)
    assert not client.calls and counters["compiled"] == counters["registered"] == 0


def test_outer_registration_failure_is_outside_repair_loop(tmp_path,monkeypatch):
    p, activation, life, client, counters, _ = _planner(tmp_path,monkeypatch,[model(),model()],registration_failure=True)
    with pytest.raises(ValueError,match="registration authority failed"):
        p(activation,life)
    assert len(client.calls) == counters["registered"] == 1


def test_outer_second_invalid_response_has_zero_registration(tmp_path,monkeypatch):
    wrong = model(); wrong["proposal"]["steps"][0]["candidate_id"] = "A09"
    p, activation, life, client, counters, _ = _planner(tmp_path,monkeypatch,[wrong,wrong])
    with pytest.raises(DesktopCompositionError,match="step_candidates_invalid"):
        p(activation,life)
    assert len(client.calls) == 2 and counters["compiled"] == counters["registered"] == 0


@pytest.mark.parametrize("state",["PROVED_INVALID","UNKNOWN"])
def test_outer_compiler_rejection_cannot_enter_registration_or_correction(tmp_path,monkeypatch,state):
    p,activation,life,client,counters,_=_planner(tmp_path,monkeypatch,[model(),model()],validation_result=state)
    with pytest.raises(DesktopCompositionError,match="compiled_plan_rejected"):
        p(activation,life)
    assert len(client.calls)==1 and counters["registered"]==0
