"""Dictionary release failures and actual execution binding regressions."""
import dataclasses
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from capability_dictionary import DictionaryError, load_dictionary
from v3.duihua_qiaojie import _render_context_envelope
from v3.execution_integrity import _local_artifact_delivery
from v3.jineng.model_transport_contract import StreamState
from v3.jineng.model_transport_registry import get_model_transport
from tests.test_dictionary_model_lifecycle import endpoint


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def dictionary(tmp_path):
    root = tmp_path / "dictionaries"
    shutil.copytree(ROOT / "dictionaries", root)
    return root


def update_json(path, change):
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_release_marker_rejects_partial_publication(dictionary):
    release = load_dictionary(dictionary)
    release.verify_published()
    (dictionary / "registry/actions.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DictionaryError, match="published_view_stale"):
        release.verify_published()


def test_skill_unknown_action_blocks_dictionary_load(dictionary):
    row = json.loads((dictionary / "skills/catalog.json").read_text(encoding="utf-8"))["skills"][0]
    path = dictionary / row["file"]
    path.write_text(path.read_text(encoding="utf-8") + '\n{"action":"missing.action"}', encoding="utf-8")
    with pytest.raises(DictionaryError, match="unknown_actions"):
        load_dictionary(dictionary)


def test_alias_cycle_blocks_load(dictionary):
    update_json(dictionary / "tools/catalog.json", lambda doc: doc["tools"]["file.read"].update(
        binding={"kind": "alias", "target": "file.read"}))
    with pytest.raises(DictionaryError, match="alias_cycle"):
        load_dictionary(dictionary)


def test_optional_dependency_does_not_disable_action(dictionary):
    update_json(dictionary / "tools/catalog.json", lambda doc: doc["tools"]["file.read"].update(
        optional_dependencies=["python:tiangong_missing_test_module"]))
    readiness = load_dictionary(dictionary).readiness("file.read")
    assert readiness["ready"]
    assert readiness["optional_unavailable"] == ["python:tiangong_missing_test_module"]


def test_required_dependency_disables_action_with_reason(dictionary):
    update_json(dictionary / "tools/catalog.json", lambda doc: doc["tools"]["file.read"].update(
        required_dependencies=["python:tiangong_missing_test_module"]))
    readiness = load_dictionary(dictionary).readiness("file.read")
    assert not readiness["ready"]
    assert "missing:python:tiangong_missing_test_module" in readiness["reasons"]


def test_running_release_keeps_loaded_procedure_bytes(dictionary):
    release = load_dictionary(dictionary)
    row = release.skills["skills"][0]
    before = release.skill_bodies[row["id"]]
    (dictionary / row["file"]).write_text("new version", encoding="utf-8")
    assert release.skill_bodies[row["id"]] == before
    load_dictionary.cache_clear()
    assert load_dictionary(dictionary).sha256 != release.sha256


def test_full_selected_procedure_survives_context_compaction():
    content = "procedure-step\n" * 1000 + "LAST_PROCEDURE_STEP"
    wire = _render_context_envelope({"current_user_text": "do this", "skill_routing": {
        "loaded_skills": [{"skill_id": "skill.test", "content": content}]},
        "summary": "old context " * 5000}, context_limit=4000)
    assert "LAST_PROCEDURE_STEP" in wire
    assert "do this" in wire


@pytest.mark.parametrize("text,local", [
    ("制作 PPT 并交付到本地目录", True),
    ("参考 https://example.org 制作网页文件", True),
    ("生成报告并发送给客户邮箱", False),
    ("上传视频到平台", False),
])
def test_local_artifacts_and_external_handoff_are_distinct(text, local):
    assert _local_artifact_delivery(text) == local


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "anthropic_messages", "openai_responses"])
def test_three_native_rounds_preserve_pairs_and_non_tool_observation(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    transport = get_model_transport(protocol)
    history = []
    for i in range(3):
        state = StreamState(tool_items={str(i): {"id": f"call_{i}", "provider_item_id": f"item_{i}",
            "name": "omni_body", "arguments_text": json.dumps({"action": "file.read", "target": f"{i}.txt"}),
            "sequence_index": 0}}, finish_reason="tool_calls")
        history.append({"turn": transport.finalize_turn(ep, state), "results": [{"ok": True, "value": f"read-{i}"}]})
    payload = {"messages": [{"role": "system", "content": "instructions"},
        {"role": "user", "content": "read unknown input then act"},
        {"role": "assistant", "content": "KEEP_ARTIFACT_VERIFICATION"}], "__provider_history": history,
        "__provider_turn": history[-1]["turn"], "__provider_tool_results": history[-1]["results"]}
    wire = transport.build_request(ep, "test", payload).payload
    serialized = json.dumps(wire)
    assert "__provider" not in serialized
    assert "KEEP_ARTIFACT_VERIFICATION" in serialized
    for i in range(3):
        assert serialized.count(f'"call_{i}"') == 2
        assert f"read-{i}" in serialized
    other = transport.build_request(dataclasses.replace(ep, provider_identity="other"), "test", payload).payload
    assert "read-0" not in json.dumps(other)


def test_dictionary_bindings_produce_real_file_evidence(tmp_path):
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="dictionary-binding-test"))
    result = runtime.run("file.write", "actual.txt", {"content": "executed by dictionary binding"})
    assert result["success"], result
    assert (tmp_path / "actual.txt").read_text(encoding="utf-8") == "executed by dictionary binding"
    assert result["dictionary_sha256"] == load_dictionary().sha256
    assert not runtime.run("non_dictionary.write", "must-not-exist.txt", {"content": "no"})["success"]
    assert not (tmp_path / "must-not-exist.txt").exists()


def test_application_discovery_is_migrated_with_dictionary_references(dictionary):
    release = load_dictionary(dictionary)
    assert len(release.applications["apps"]) == 100
    assert all(set(app["actions"]) <= release.tools.keys() for app in release.applications["apps"])
    update_json(dictionary / "tools/apps.json", lambda doc: doc["apps"][0]["actions"].append("missing.action"))
    load_dictionary.cache_clear()
    with pytest.raises(DictionaryError, match="application_reference"):
        load_dictionary(dictionary)


def test_declared_effect_survives_projection_and_controls_gateway_floor(dictionary):
    from v3.fact_kernel import compile_manifest
    from total_gateway.action_registry import compile_action_registry
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, DELIVERY_ACTIONS
    release = load_dictionary(dictionary)
    metadata = release.action_metadata()
    assert all(metadata[key]["effect"] == row["effect"] for key, row in release.tools.items())
    manifest = compile_manifest(metadata, BodyRuntime, dynamic_actions=set(DELIVERY_ACTIONS)).to_gateway_dict()
    registry = compile_action_registry(manifest, generated_at_ms=0)
    row = next(x for x in registry.permissions if x.action_id == "adobe.photoshop.document.open")
    assert row.effect == "execute" and row.registry_risk == "A0" and row.effective_risk == "A3"


def test_adapter_schema_matches_dictionary_and_standalone_legacy_root_is_rejected(tmp_path, monkeypatch):
    from omni_body_skill.api.v1.v3.tools import omni_body as wrapper
    from v3.jineng.guge_ceng import OMNI_BODY_PARAMETERS
    release = load_dictionary()
    assert wrapper.TOOL_DESCRIPTION["parameters"] == release.host_protocol["parameters"] == OMNI_BODY_PARAMETERS
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(ROOT / "src/omni_body_skill"))
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ALLOW_USER_ROOT", "1")
    monkeypatch.setattr(wrapper, "__file__", str(tmp_path / "standalone/wrapper.py"))
    monkeypatch.setattr(wrapper, "_PINNED_SKILL_ROOT", None)
    assert wrapper._find_skill_root() is None


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "anthropic_messages", "openai_responses"])
def test_native_observation_dedup_keeps_results_warnings_and_other_messages(endpoint, protocol):
    from v3.jineng.model_transport_contract import extract_native_roundtrip_history, compact_native_observations
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    transport = get_model_transport(protocol)
    history, observations, messages = [], [], []
    for i in range(3):
        state = StreamState(tool_items={str(i): {"id": f"call_{i}", "provider_item_id": f"item_{i}",
            "name": "omni_body", "arguments_text": json.dumps({"action": "file.read", "target": f"{i}.txt"}),
            "sequence_index": 0}}, finish_reason="tool_calls")
        result = {"ok": True, "content": f"UNIQUE_FILE_{i}" + ("x" * 5000)}
        history.append({"turn": transport.finalize_turn(ep, state), "results": [result]})
        observations.append({"tool_result": result, "tool_action": "file.read", "ok": True,
            "gaps": [f"KEEP_WARNING_{i}"], "summary": "read input"})
        messages.append(json.dumps(observations[-1]))
    validated = extract_native_roundtrip_history({"__provider_history": history}, ep)
    compacted, changed = compact_native_observations(messages, observations, validated)
    assert changed and len("".join(compacted)) < len("".join(messages)) / 4
    payload = {"messages": [{"role": "user", "content": "read inputs"},
        {"role": "assistant", "content": "KEEP_UNRELATED_NOTE"},
        *[{"role": "assistant", "content": x} for x in compacted]],
        "__provider_history": history, "__native_observations_compacted": changed}
    wire = json.dumps(transport.build_request(ep, "test", payload).payload)
    assert "__native_observations_compacted" not in wire
    assert "KEEP_UNRELATED_NOTE" in wire
    for i in range(3):
        assert wire.count(f"UNIQUE_FILE_{i}") == 1
        assert f"KEEP_WARNING_{i}" in wire
        assert wire.count(f'"call_{i}"') == 2
    # A provider/model change cannot discard fallback observations.
    other = extract_native_roundtrip_history({"__provider_history": history},
        dataclasses.replace(ep, model_name="another-model"))
    assert compact_native_observations(messages, observations, other) == (messages, False)
    unmatched = [{**row, "tool_result": {"different": True}} for row in observations]
    assert compact_native_observations(messages, unmatched, validated) == (messages, False)


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "anthropic_messages", "openai_responses"])
def test_repair_instruction_follows_complete_native_history(endpoint, protocol, monkeypatch):
    from v3.jineng import model_transport_executor as executor
    from tests.test_dictionary_model_lifecycle import Client
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    transport = get_model_transport(protocol)
    state = StreamState(tool_items={"0": {"id": "call_prior", "provider_item_id": "item_prior",
        "name": "omni_body", "arguments_text": '{"action":"file.read","args":{}}',
        "sequence_index": 0}}, finish_reason="tool_calls")
    payload = {"messages": [{"role": "user", "content": "original goal"}],
        "__provider_history": [{"turn": transport.finalize_turn(ep, state), "results": [{"ok": True}]}],
        "__turn_repair_instruction": "LATEST_REPAIR"}
    captured = []
    monkeypatch.setattr(executor, "_pinned_request", lambda url, binding: (url, {}, "example.test"))
    monkeypatch.setattr(executor, "validate_model_endpoint", lambda *a, **k: None)
    with pytest.raises(executor.TransportExecutionError):
        executor.execute_streaming_turn(client=Client(lambda: iter(())), endpoint=ep, api_key="test",
            canonical_payload=payload, on_request_built=captured.append)
    wire = captured[0]
    items = wire["input" if protocol == "openai_responses" else "messages"]
    assert items[-1] == {"role": "user", "content": "LATEST_REPAIR"}
    assert json.dumps(wire).count('"call_prior"') == 2
    assert "__turn_repair_instruction" not in wire


def test_absent_task_context_preserves_pre_upgrade_envelope_identity():
    from contracts.models import InboundEnvelope, TaskInputContext
    from contracts.canonical import canonical_json_bytes, canonical_sha256
    from total_gateway.store import _parse_inbound_envelope
    values = {name: "id_test" for name in ("inbound_id", "tenant_id", "link_account_id", "conversation_ref",
        "channel_message_ref", "sender_ref")}
    values.update({name: "a" * 64 for name in ("conversation_scope_hash", "principal_scope_hash", "message_scope_hash",
        "idempotency_key", "channel_metadata_hash")})
    envelope = InboundEnvelope(**values, channel="desktop", received_at_ms=1, text="existing request")
    raw = envelope.model_dump(mode="json")
    assert "task_context" not in raw
    assert _parse_inbound_envelope(canonical_json_bytes(raw).decode(), canonical_sha256(raw)) == envelope
    changed = envelope.model_copy(update={"task_context": TaskInputContext(raw_user_text="existing request")})
    assert canonical_sha256(changed) != canonical_sha256(envelope)


def test_media_file_budget_is_separate_from_process_log_budget():
    release = load_dictionary()
    video = release.execution_profiles[release.tools["video.slideshow"]["budget"]["profile"]]
    code = release.execution_profiles[release.tools["python.run"]["budget"]["profile"]]
    assert video["max_artifact_bytes"] >= 64 * 1024 * 1024
    assert video["max_log_bytes"] <= 4 * 1024 * 1024
    assert video["timeout_seconds"] > 60
    assert code["network"] == "denied"


def test_program_receipt_survives_unrelated_failed_test_and_test_repair():
    from v3.execution_integrity import obligation_is_satisfied, update_run_state_obligations
    def result(target, code, stderr=""):
        return {"ok": code == 0, "tool_action": "python.run", "tool_args": {"target": target, "args": {}},
            "tool_result": {"result": {"command": ["python.exe", target], "execution": {
                "ok": code == 0, "returncode": code, "stderr": stderr}}},
            "tool_result_contract": {"paths": [target], "observed_write_effect": False}}
    program = result("summarize.py", 0)
    bad_test = result("test_summary.py", 2, "unrecognized arguments: -m")
    repaired_test = result("test_summary.py", 0, "Ran 2 tests in 0.001s\n\nOK\n")
    obligation = {"kind": "execution", "actionable": True, "status": "pending",
        "evidence_predicate": "program_execution", "evidence_dependency_paths": ["summarize.py", "input.json"]}
    history = [program, bad_test, repaired_test]
    assert obligation_is_satisfied(obligation, history)
    state = {"obligations": [dict(obligation)], "round": 1}
    for payload in history:
        update_run_state_obligations(state, payload)
    assert state["obligations"][0]["status"] == "satisfied"
    assert not obligation_is_satisfied(obligation, history + [result("summarize.py", 1)])
    changed = {"ok": True, "tool_action": "file.write", "tool_args": {"target": "summarize.py"},
        "tool_result_contract": {"observed_write_effect": True, "paths": ["summarize.py"]}}
    assert not obligation_is_satisfied(obligation, history + [changed])


def test_pre_upgrade_skill_candidate_keeps_canonical_bytes():
    from contracts.models import SkillCandidate
    from contracts.canonical import canonical_json_bytes
    old = dict(skill_id="old", version="v1", sha256="a" * 64, source_ref="source", score_millis=1,
        required_actions=("file.read",), missing_actions=(), incompatible_reasons=(), compatible=True)
    assert canonical_json_bytes(SkillCandidate(**old)) == canonical_json_bytes(old)


def test_chat_bridge_passes_structured_dictionary_context_separately(monkeypatch):
    import threading
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock
    from v3 import duihua_qiaojie as bridge
    routing = {"candidates": [{"skill_id": "skill_code_project_delivery_worldclass_v1", "summary": "code"}]}
    context = {"request_id": "bridge-test", "session_id": "session-test", "skill_routing": routing}
    control = Mock(request_id="bridge-test")
    coordinator = Mock(zuihou_biaoxian={})
    def run(*args, **kwargs):
        assert isinstance(kwargs["duihua_shangxiawen"], str)
        assert kwargs["dictionary_context"] is routing
        return "已收到"
    coordinator.huanxing.side_effect = run
    instance = object.__new__(bridge.DuihuaQiaojie)
    instance._zd = coordinator
    instance._core_execution_lock = threading.RLock()
    instance._run_control = Mock()
    instance._run_control.claim.return_value = (control, "new", None)
    instance._run_control.status.return_value = {}
    monkeypatch.setattr(bridge, "buquan_conversation_context", lambda *a, **k: context)
    monkeypatch.setattr(bridge, "_duihua_shangxiawen", lambda *a: "rendered context with candidates")
    monkeypatch.setattr(bridge, "bind_run_context", lambda *a: nullcontext())
    for name in ("xie_duihua_xiaoxi", "xie_duihua_huifu"):
        monkeypatch.setattr(bridge, name, lambda *a: None)
    result = json.loads(instance.chuli_duihua("读取文件", conversation_context=context))
    assert result["huifu"] == "已收到", result
    coordinator.huanxing.assert_called_once()


def test_failed_optional_check_cannot_veto_real_artifact_but_uncertain_effect_can(tmp_path, monkeypatch):
    from v3.zongdiaodu import _simple_chain_evidence_check
    from tests.test_simple_chain_atomic_gates_20260806 import _write_contract_payload
    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "report.md"
    target.write_text("Actual saved report.", encoding="utf-8")
    written = _write_contract_payload(str(target))
    failed = {"ok": False, "tool_action": "shell.run", "failures": ["optional helper unavailable"],
        "tool_result_contract": {"may_mutate": True, "observed_write_effect": False},
        "tool_result": {"result": {"execution": {"ok": False, "returncode": 1,
            "commit_state": "discarded", "execution_state": "completed"}}}}
    message = "生成 report.md 到工作区。"
    assert _simple_chain_evidence_check(message, [written, failed], [], final_reply="已保存 report.md。")[0]
    failed["tool_result"]["result"]["execution"]["commit_state"] = "unknown"
    assert not _simple_chain_evidence_check(message, [written, failed], [], final_reply="已保存 report.md。")[0]
    failed["tool_result"]["result"]["execution"]["commit_state"] = "discarded"
    assert not _simple_chain_evidence_check("生成 report.md 并实际运行测试。", [written, failed], [], final_reply="完成。")[0]


def test_slideshow_fps_contract_matches_real_encoded_video(tmp_path):
    from PIL import Image
    import imageio_ffmpeg
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="video-contract-test"))
    frames = []
    for i in range(72):
        name = f"frame_{i:03d}.png"
        Image.new("RGB", (32, 32), (i * 3, 80, 120)).save(tmp_path / name)
        frames.append(name)
    schema = runtime.run("system.action_schema", "video.slideshow", {})
    assert "frame_rate" in schema["argument_contract"]["args"]
    result = runtime.run("video.slideshow", "animation.mp4", {"images": frames, "fps": 24})
    assert result["success"], result
    stream = imageio_ffmpeg.read_frames(str(tmp_path / "animation.mp4"))
    try:
        metadata = next(stream)
        assert metadata["fps"] == 24
        assert abs(metadata["duration"] - 3.0) < 0.1
        assert sum(1 for _ in stream) == 72
    finally:
        stream.close()
    for args in ({"images": frames, "fps": 24, "frame_rate": 12}, {"images": frames, "framerate": 24}):
        assert not runtime.run("video.slideshow", "invalid.mp4", args)["success"]
        assert not (tmp_path / "invalid.mp4").exists()


def test_ppt_notes_are_outputs_and_font_size_is_not_novel_length():
    from v3.execution_integrity import request_target_bindings, build_action_obligations
    from v3.simple_chain.kernel import _novel_chapter_min_chars
    prompt = "读取 input.json，制作4页 report.pptx。标题至少24pt、正文至少16pt。notes.txt 逐页列出演讲要点。保存后读回确认页数。"
    bindings = {row["target_path"]: row for row in request_target_bindings(prompt)}
    assert bindings["input.json"]["role"] == "input"
    assert bindings["notes.txt"]["role"] == "output"
    assert not any(row.get("kind") == "observation" and row.get("target_path") == "notes.txt"
                   for row in build_action_obligations(prompt))
    assert _novel_chapter_min_chars(prompt, "file.write", {"target": "notes.txt"}) == 0
    assert request_target_bindings("读取 notes.txt，列出演讲要点")[0]["role"] == "input"


@pytest.mark.parametrize("message", [
    "继续\n\n【本轮活跃项目根】\nD:/workspace",
    "继续\n\n【必须继承且仍未完成的原始总目标】\n制作报告\n\n【本轮唯一默认工作区】\nD:/workspace",
])
def test_long_checkpoint_preserves_dictionary_and_continues_network_failure(tmp_path, monkeypatch, message):
    import os
    from v3 import duihua_qiaojie as bridge
    from v3.simple_chain.kernel import _simple_chain_recovery_checkpoint_from_context
    root = tmp_path / ".tiangong/v3/simple_chain_run_state"
    root.mkdir(parents=True)
    release = load_dictionary()
    skill_id = release.skills["skills"][0]["id"]
    state = {"request_id": "old", "session_id": "same", "status": "failed",
        "terminal_reason": "[terminal_model_error] transport_error", "round": 31,
        "original_user_goal": "制作报告" + "保留明确条件" * 120,
        "loaded_skill_ids": [skill_id], "dictionary_sha256": release.sha256,
        "dictionary_version": release.version,
        "completed_actions": [{"round": i} for i in range(31)]}
    old = root / "old.json"
    old.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(bridge.Path, "home", lambda: tmp_path)
    context = {"session_id": "same", "request_id": "current", "messages": [
        {"role": "assistant", "content": "old history" * 1000} for _ in range(31)]}
    envelope = bridge._build_context_envelope(context, message)
    wire = bridge._render_context_envelope(envelope, context_limit=3000)
    checkpoint = _simple_chain_recovery_checkpoint_from_context(wire)
    assert checkpoint["original_user_goal"] == state["original_user_goal"]
    assert checkpoint["loaded_skill_ids"] == [skill_id]
    assert checkpoint["dictionary_sha256"] == release.sha256
    assert bridge._latest_session_recovery_checkpoint(context, "继续制作另一份新的预算表") == {}
    newest = root / "newer.json"
    newest.write_text(json.dumps({**state, "request_id": "newer", "status": "complete"}), encoding="utf-8")
    os.utime(newest, (old.stat().st_mtime + 10, old.stat().st_mtime + 10))
    assert bridge._latest_session_recovery_checkpoint(context, "继续") == {}


def test_system_health_preserves_lock_bound_cancellation_callback(tmp_path):
    import threading
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    cancelled = threading.Event()
    callback = cancelled.is_set
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), cancel_check=callback))
    result = runtime.run("system.health", "", {})
    assert result["success"], result
    assert result["cancellation_enabled"] is True
    assert "cancel_check" not in result["config"]
    json.dumps(result, allow_nan=False)
    assert runtime.config.cancel_check is callback
    cancelled.set()
    assert runtime.config.cancel_check()


def test_retired_cli_wrapper_cannot_be_loaded_and_cli_uses_canonical_host():
    from omni_body_skill.tools import cli
    from omni_body_skill.api.v1.v3.tools.omni_body import run_omni_body
    assert cli.run_omni_body is run_omni_body
    for root in (ROOT / "src", ROOT / "readable-python-source", ROOT / "app/backend/tiangong-backend"):
        assert not (root / "omni_body_skill/tools/omni_body_v3.py").exists()
