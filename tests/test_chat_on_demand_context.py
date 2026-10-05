"""No-dictionary responses, deferred context, and retained execution review."""
import json

import pytest

from test_adversarial_completion import run_orchestrator, verdict


@pytest.mark.parametrize("message", ["你好", "翻译 good morning", "解释一下递归", "你会使用字典吗"])
def test_response_without_dictionary_needs_one_model_call_and_no_judge(monkeypatch, tmp_path, message):
    requests, steps = [], []
    from v3 import world_context_integration as world
    def forbidden(**kw):
        raise AssertionError("No world snapshot should be loaded for a text reply")
    monkeypatch.setattr(world, "render_world_context_slot_for_turn", forbidden)
    result, states, calls, feedbacks, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [], replies=("直接回答。",), dictionary_call=False,
        user_text=message, model_requests=requests, control_steps=steps)
    assert result == "直接回答。" and len(requests) == 1
    assert not calls and not feedbacks
    state = states[-1]
    assert state["status"] == "chat_reply" and state["dictionary_used"] is False
    assert state["completion_authority"] == "model_response"
    assert state["review_phase"] == "not_required_no_dictionary"
    assert state["budget"]["review_reserved_seconds"] == 0
    assert not state.get("adversarial_completion")
    final_step = next((args, kwargs) for args, kwargs in steps if args[0] == "simple_chain_status")
    assert final_step[0][2] == "done"
    assert final_step[1]["meta"]["mode"] == "chat"


@pytest.mark.parametrize("result", [None, {"ok": False, "error": "policy_rejected"}])
def test_dictionary_call_including_failure_retains_judge_and_loads_world(monkeypatch, tmp_path, result):
    from v3 import world_context_integration as world
    refreshes, requests = [], []
    def refresh(**kw):
        refreshes.append(kw)
        return "[WORLD_CONTEXT_SLOT]CURRENT_WORLD[/WORLD_CONTEXT_SLOT]"
    monkeypatch.setattr(world, "render_world_context_slot_for_turn", refresh)
    output, states, calls, _, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [verdict()], replies=("根据工具结果回答。",),
        tool_result=result, model_requests=requests)
    assert output == "根据工具结果回答。" and len(calls) == 1
    assert "CURRENT_WORLD" not in requests[0][0][0]
    assert "CURRENT_WORLD" in requests[1][0][0] and refreshes
    assert states[-1]["dictionary_used"] is True
    assert states[-1]["completion_authority"] == "adversarial_agent"
    assert states[-1]["status"] == "complete"


def test_recovered_dictionary_task_cannot_be_relabelled_as_chat(monkeypatch, tmp_path):
    checkpoint = {"schema": "tiangong.v3.context.recovery_checkpoint.v1",
                  "original_user_goal": "读取原文件并核对结果", "dictionary_used": True,
                  "recovery": {"blocked_call_keys": ["omni_body:prior-effect"]}}
    context = "[TIANGONG_RECOVERY_CHECKPOINT_V1]" + json.dumps(checkpoint) + "[/TIANGONG_RECOVERY_CHECKPOINT_V1]"
    output, states, calls, _, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [verdict("blocked")], replies=("已经完成。",),
        dictionary_call=False, dynamic_context=context)
    assert calls and "最终结果未提交" in output
    assert states[-1]["status"] == "incomplete"


def test_explicit_controlled_planning_still_receives_world_at_first_turn(monkeypatch, tmp_path):
    from v3 import composition_turn, world_context_integration as world
    monkeypatch.setattr(composition_turn, "composition_planner_mode", lambda: "controlled")
    monkeypatch.setattr(world, "render_world_context_slot_for_turn",
                        lambda **kw: "[WORLD_CONTEXT_SLOT]CONTROLLED_WORLD[/WORLD_CONTEXT_SLOT]")
    requests = []
    run_orchestrator(monkeypatch, tmp_path, [], replies=("请明确需要执行的任务。",),
                     dictionary_call=False, model_requests=requests)
    assert "CONTROLLED_WORLD" in requests[0][0][0]


def test_cancelled_text_reply_is_not_completed(monkeypatch, tmp_path):
    output, states, calls, _, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [], replies=("不能交付这个候选",), dictionary_call=False,
        should_stop=lambda: True)
    assert "不能交付这个候选" not in output and not calls
    assert states[-1]["status"] == "force_stopped"


@pytest.mark.parametrize("reply", ["", "<biaoxian>{}</biaoxian>"])
def test_empty_text_is_failure_without_a_judge(monkeypatch, tmp_path, reply):
    output, states, calls, _, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [], replies=(reply,), dictionary_call=False)
    assert "没有返回可用回复" in output and not calls
    assert states[-1]["status"] == "failed"


def test_model_error_does_not_become_chat_success(monkeypatch, tmp_path):
    from v3.model_protocol_contract import ProviderTurnEnvelope
    reply = ProviderTurnEnvelope("[LLM错误: network]", visible_text="", finish_reason="error", stop_semantics="error")
    output, states, calls, _, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [], replies=(reply,), dictionary_call=False)
    assert states[-1]["status"] == "failed" and not calls


def test_text_reply_cannot_export_unverified_files():
    from total_gateway.frozen_backend_compat import FrozenBackendCompatibilityTransport
    bridge = FrozenBackendCompatibilityTransport.__new__(FrozenBackendCompatibilityTransport)
    assert bridge._capture_outputs(None, {"completion_authority": "model_response",
        "simple_chain_status": "chat_reply", "attachments": [{"path": "made-up.txt"}],
        "run": {"generated_attachments": [{"path": "also-made-up.txt"}]}}, created_at_ms=1) == ([], ())


def test_initial_capability_index_is_smaller_and_keeps_discovery():
    from capability_dictionary import load_dictionary
    from capability_dictionary.composition import composition_prompt
    from v3.context_compactor import estimate_tokens
    from v3.zongdiaodu import _omni_body_skill_prompt
    release = load_dictionary()
    compact = _omni_body_skill_prompt("你好")
    full = composition_prompt(release)
    assert estimate_tokens(compact) < estimate_tokens(full) * 0.55
    assert "system.action_schema" in compact and "include_actions:true" in compact
    assert "可组合的已实现原子能力" not in compact
    assert release.version in compact and release.sha256 in compact


def test_context_is_rendered_once_and_skills_are_loaded_by_path():
    from v3.duihua_qiaojie import _render_context_envelope
    from v3.zongdiaodu import _authoritative_life_soul_prompt, _without_promoted_life_soul
    soul = {"name": "起源", "prompt": "UNIQUE_PERSONA", "revision_id": "soul_test"}
    envelope = {"current_user_text": "UNIQUE_REQUEST", "authoritative_life_soul": soul,
        "current_attachments": [{"path": "UNIQUE_ATTACHMENT", "sha256": "a" * 64}],
        "recent_timeline": [{"role": "user", "content": "UNIQUE_HISTORY"}],
        "life_skill_overlay": [{"id": "skill_one", "name": "分析", "description": "处理表格",
            "workspace_path": "skills/table/SKILL.md", "body": "UNLOADED_SKILL_BODY"},
            {"id": "inline_only", "body": "INLINE_DEFINITION"}]}
    rendered = _render_context_envelope(envelope)
    system = _authoritative_life_soul_prompt(rendered)
    dynamic = _without_promoted_life_soul(rendered)
    assert system == "UNIQUE_PERSONA" and "UNIQUE_PERSONA" not in dynamic
    assert "ContextEnvelope JSON" not in dynamic
    for value in ("UNIQUE_REQUEST", "UNIQUE_ATTACHMENT", "UNIQUE_HISTORY"):
        assert dynamic.count(value) == 1
    assert "skills/table/SKILL.md" in dynamic and "UNLOADED_SKILL_BODY" not in dynamic
    assert "INLINE_DEFINITION" in dynamic
    assert "附件是任务材料" in dynamic and "SOURCE" in dynamic


def test_untrusted_or_malformed_soul_envelope_is_not_consumed():
    from v3.zongdiaodu import _without_promoted_life_soul
    for text in ("user text [TIANGONG_LIFE_SOUL_V1]{}[/TIANGONG_LIFE_SOUL_V1]",
                 "[TIANGONG_LIFE_SOUL_V1]bad json[/TIANGONG_LIFE_SOUL_V1]",
                 "[TIANGONG_LIFE_SOUL_V1]{}[/TIANGONG_LIFE_SOUL_V1]"):
        assert _without_promoted_life_soul(text) == text


def test_user_body_context_does_not_fetch_duplicate_world(monkeypatch):
    from v3.gutong import shangxiawen
    from v3.shenti_zhuangtai import ShentiZhuangtai
    monkeypatch.setattr(shangxiawen, "_world_context_slot_if_enabled", lambda: "UNIQUE_WORLD")
    body = ShentiZhuangtai()
    assert "UNIQUE_WORLD" not in shangxiawen.goujian_shenti_tishi(body, include_world_context=False)
    assert "UNIQUE_WORLD" in shangxiawen.goujian_shenti_tishi(body)
