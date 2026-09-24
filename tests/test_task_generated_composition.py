"""Task-generated Tool/Skill programs, durable admission, real file effects."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from capability_dictionary import DictionaryError, load_dictionary
from capability_dictionary.composition import CompositionCursor, compile_task_composition
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority
from total_gateway.store import StoreConflictError
from tests import test_p18_m2_regenerative_provider as provider_fixtures
from v3.runtime_regenerative_boundary import tool_effect_descriptor


def proposal(content="model supplied content"):
    return {"tools": [
        {"id": "write_and_inspect", "description": "Create and inspect the requested file",
         "actions": [{"action": "file.write", "target": "result.txt", "args": {"content": content}},
                     {"action": "file.read", "target": "result.txt", "args": {}}]},
        {"id": "fingerprint", "description": "Record actual output identity",
         "actions": [{"action": "file.hash", "target": "result.txt", "args": {}}]},
    ], "skill": {"id": "deliver_requested_file", "description": "Compose the generated Tools",
        "steps": [{"id": "verify", "tool": "fingerprint", "depends_on": ["produce"]},
                  {"id": "produce", "tool": "write_and_inspect", "depends_on": []}]}}


@pytest.fixture
def gateway(tmp_path):
    case = provider_fixtures.RegenerativeProviderTests(methodName="runTest")
    case.setUp()
    case.provider = RegenerativeExecutionAuthority(case.store, workspace_root=tmp_path, require_compositions=True)
    try:
        yield case
    finally:
        case.tearDown()


def register(case, value=None):
    return case.provider(case.payload("register_composition", proposal=value or proposal(), now_ms=1500))


def prepare(case, registered, leaf, ordinal):
    call = leaf["invocation"]
    descriptor = tool_effect_descriptor(request_id=case.request_id, run_id=case.run_id,
        generation=case.generation, tool_name="omni_body", tool_args=call, attempted_action=call["action"])
    return case.provider(case.payload("prepare_effect", now_ms=2000 + ordinal * 10,
        global_step=ordinal, attempt=ordinal, **descriptor,
        composition_ref={"composition_id": registered["composition_id"],
            "program_sha256": registered["program_sha256"], "leaf_id": leaf["id"]}))


def test_task_program_is_generated_with_no_fixed_skills():
    release = load_dictionary()
    assert release.skills["skills"] == [] and release.skill_bodies == {}
    assert not list((release.root / "skills").glob("*.md"))
    program = compile_task_composition(proposal())
    assert [row["id"] for row in program["leaves"]] == ["produce.1", "produce.2", "verify.1"]
    assert compile_task_composition(proposal("different goal output"))["program_sha256"] != program["program_sha256"]


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "openai_responses", "anthropic_messages"])
@pytest.mark.parametrize("empty_defaults", [False, True])
def test_model_program_survives_native_adapter_parser_and_gateway(protocol, empty_defaults, gateway):
    from v3.jineng.http_kehuduan import _canonicalize_provider_turn
    from v3.gutong.gutong_ceng import GutongCeng
    from v3.model_protocol_contract import ProviderTurnEnvelope, ToolCallBinding
    envelope = {"composition": proposal()}
    wire_envelope = {**envelope, "action": "", "target": "", "args": {}} if empty_defaults else envelope
    binding = ToolCallBinding(canonical_call_id="native-call", provider_call_id="provider-call",
        tool_name="omni_body", protocol_family=protocol, binding_type="native")
    native = ProviderTurnEnvelope("", protocol_family=protocol, visible_text="Creating the requested file.",
        tool_calls=[{"id": "native-call", "name": "omni_body", "arguments": wire_envelope}],
        tool_call_bindings=[binding], finish_reason="tool_calls")
    adapted = _canonicalize_provider_turn(native)
    assert adapted.tool_calls[0]["arguments"] == envelope
    assert adapted.tool_call_bindings == native.tool_call_bindings
    parsed = GutongCeng.jiexi_duogongju(adapted)
    assert parsed == [("omni_body", envelope)]
    registered = register(gateway, parsed[0][1]["composition"])
    assert registered["program_sha256"] == compile_task_composition(proposal())["program_sha256"]


def test_observation_compaction_retains_generated_program_binding(monkeypatch):
    from v3.simple_chain import kernel
    monkeypatch.setattr(kernel, "_simple_chain_save_run_state", lambda state: None)
    reference = {"composition_id": "cmp_test", "program_sha256": "a" * 64, "leaf_id": "read.1"}
    state = {"active_composition_ref": reference}
    rich_result = {f"detail_{i}": "value" for i in range(30)}
    rich_result.update(ok=True, tool_action="file.read", tool_name="omni_body")
    kernel._simple_chain_record_observation(state, rich_result)
    assert state["observations"][0]["composition_ref"] == reference


@pytest.mark.parametrize("change,code", [
    (lambda p: p["tools"][0]["actions"][0].update(action="skill.get"), "fixed_skill"),
    (lambda p: p["tools"][0]["actions"][0].update(action="unavailable.fake"), "unavailable"),
    (lambda p: p["skill"]["steps"][1].update(depends_on=["verify"]), "cycle"),
    (lambda p: p["skill"]["steps"][1].update(depends_on=["missing"]), "missing"),
    (lambda p: p["tools"].append(deepcopy(p["tools"][0])), "duplicate"),
    (lambda p: p["tools"][0]["actions"][0]["args"].update(__grant="forged"), "authority"),
    (lambda p: p.update(approved=True), "fields_invalid"),
])
def test_invalid_program_has_no_partial_execution(change, code, gateway):
    value = proposal()
    change(value)
    with pytest.raises(DictionaryError, match=code):
        register(gateway, value)
    assert not gateway.store.list_effects_for_request(gateway.request_id,
        run_id=gateway.run_id, generation=gateway.generation)


def test_registered_program_is_durable_and_fences_leaf_order_and_arguments(gateway, tmp_path):
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    program = compile_task_composition(proposal())
    registered = register(gateway)
    with pytest.raises(StoreConflictError, match="predecessor_not_committed"):
        prepare(gateway, registered, program["leaves"][1], 1)
    modified = deepcopy(program["leaves"][0])
    modified["invocation"]["args"]["content"] = "changed after registration"
    with pytest.raises(StoreConflictError, match="invocation_changed"):
        prepare(gateway, registered, modified, 1)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="composition-test"))
    for ordinal, leaf in enumerate(program["leaves"], 1):
        prepared = prepare(gateway, registered, leaf, ordinal)
        effect = {key: prepared[key] for key in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
        started = gateway.provider(gateway.payload("start_effect", now_ms=2100 + ordinal * 10, **effect))
        assert started["dispatch_permitted"]
        call = leaf["invocation"]
        result = runtime.run(call["action"], call["target"], call["args"])
        assert result["success"], result
        gateway.provider(gateway.payload("finish_effect", now_ms=2200 + ordinal * 10,
            outcome="succeeded", result_summary={"ok": True, "dictionary_sha256": result["dictionary_sha256"]}, **effect))
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "model supplied content"
    # Reopening the provider cannot turn a registered/committed leaf into a new effect.
    gateway.provider = RegenerativeExecutionAuthority(gateway.store, workspace_root=tmp_path, require_compositions=True)
    repeated = prepare(gateway, registered, program["leaves"][0], 4)
    assert repeated["disposition"] == "already_committed"
    events = gateway.store.list_execution_events(gateway.request_id, run_id=gateway.run_id, generation=gateway.generation)
    saved = next(e for e in events if e.event_type == "composition.registered")
    assert json.loads(saved.payload["program_json"]) == program
    assert len([e for e in events if e.event_type == "step.committed"]) == 3


def test_raw_task_action_cannot_skip_composition(gateway):
    with pytest.raises(StoreConflictError, match="registration_required"):
        gateway.provider(gateway.effect_payload("prepare_effect", global_step=1, now_ms=2000))


def test_later_bad_arguments_block_whole_program_before_first_write(gateway, tmp_path):
    value = proposal()
    value["tools"][1]["actions"] = [{"action": "python.run", "target": "work.py", "args": {"argv": 7}}]
    with pytest.raises(ValueError, match="argument_schema_invalid"):
        register(gateway, value)
    assert not (tmp_path / "result.txt").exists()


def test_numeric_json_arguments_preserve_bytes_in_signed_ledger(gateway):
    value = proposal()
    value["tools"][1]["actions"] = [{"action": "qc.ppt.delivery_check", "target": "report.pptx",
                                    "args": {"min_visual_coverage": 0.4}}]
    registered = register(gateway, value)
    event = next(e for e in gateway.store.list_execution_events(gateway.request_id,
        run_id=gateway.run_id, generation=gateway.generation) if e.event_type == "composition.registered")
    program = json.loads(event.payload["program_json"])
    assert program["program_sha256"] == registered["program_sha256"]
    assert program["leaves"][-1]["invocation"]["args"]["min_visual_coverage"] == 0.4


def test_failed_leaf_returns_actual_result_and_does_not_run_remaining_steps():
    program = compile_task_composition(proposal())
    cursor = CompositionCursor(program, {"composition_id": "registered"}, object())
    assert cursor.observe({"ok": True}, success=True)
    assert not cursor.observe({"ok": False, "error": "real failure"}, success=False)
    result = cursor.result()
    assert not result["ok"] and result["not_executed"] == ["verify.1"]
    assert result["results"][-1]["result"]["error"] == "real failure"


def test_compiler_never_retains_mutable_model_arguments():
    value = proposal()
    program = compile_task_composition(value)
    value["tools"][0]["actions"][0]["args"]["content"] = "mutated"
    assert program["leaves"][0]["invocation"]["args"]["content"] == "model supplied content"
