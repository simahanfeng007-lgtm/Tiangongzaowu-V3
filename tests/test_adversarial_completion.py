"""Completion belongs to an isolated model verdict; no legacy success fallback."""
from contextlib import nullcontext
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import pytest

from v3 import adversarial_review as review
from test_adversarial_review import Client, observations, run_state


def verdict(decision="complete", **updates):
    value = {"decision": decision, "reason": "逐项核对原要求与结果。", "findings": [],
             "coverage_gaps": ["需要回读结果"] if decision == "continue" else [], "evidence_requests": []}
    value.update(updates)
    return json.dumps(value, ensure_ascii=False)


def session(client):
    return review.CompletionSession(client, endpoint_resolver=lambda: SimpleNamespace(
        provider_identity="fixture", model_name="fixture", protocol_family="test", config_fingerprint="test"))


@pytest.mark.parametrize("mode", [None, "typo", "judge"])
def test_default_and_invalid_configuration_require_agent(mode, monkeypatch):
    if mode is None:
        monkeypatch.delenv("TIANGONG_ADVERSARIAL_REVIEW", raising=False)
    else:
        monkeypatch.setenv("TIANGONG_ADVERSARIAL_REVIEW", mode)
    assert review.review_mode() == "judge"


def test_continue_repair_then_fresh_approval():
    client = Client(lambda p: verdict("complete" if '"sum":5' in p["observations"][0]["data_excerpt"].replace('\\"', '"').replace(' ', '') else "continue"))
    reviewer, state = session(client), run_state()
    first = reviewer.judge(state, observations(4), "sum=4", remaining_seconds=120)
    assert first["review"]["decision"] == "continue"
    assert not reviewer.approved(state, observations(4), "sum=4")
    second = reviewer.judge(state, observations(5), "sum=5", remaining_seconds=120)
    assert second["review"]["decision"] == "complete"
    assert reviewer.approved(state, observations(5), "sum=5")
    assert len(state["adversarial_completion"]["reports"]) == 2


@pytest.mark.parametrize("change", ["reply", "evidence", "goal", "guidance", "attachment", "run", "restart"])
def test_changed_delivery_or_recovered_approval_cannot_be_reused(change):
    reviewer, state, evidence = session(Client(lambda p: verdict())), run_state(), observations()
    reviewer.judge(state, evidence, "候选", remaining_seconds=120)
    assert reviewer.approved(state, evidence, "候选")
    reply = "候选"
    if change == "reply": reply += " 新结论"
    if change == "evidence": evidence = observations(6)
    if change == "goal": state["original_user_goal"] += " 还要运行测试"
    if change == "guidance": state["review_user_guidance"] = ["改成 6"]
    if change == "attachment": state["generated_attachments"] = [{"path": "other.txt"}]
    if change == "run": state["run_id"] = "another-run"
    if change == "restart": reviewer = session(Client())
    assert not reviewer.approved(state, evidence, reply)


@pytest.mark.parametrize("case", ["malformed", "no_client", "deadline", "cancelled", "budget", "oversize", "advice_only"])
def test_no_valid_verdict_never_means_complete(case):
    state, evidence = run_state(), observations()
    client = Client(lambda p: verdict())
    if case == "malformed": client.result = lambda p: "complete"
    if case == "advice_only": client.result = lambda p: '{"status":"no_counterexample","findings":[],"coverage_gaps":[]}'
    if case == "no_client": client = None
    if case == "budget": state["adversarial_completion"] = {"attempts": review.MAX_COMPLETION_ATTEMPTS, "reports": []}
    reviewer = session(client)
    result = reviewer.judge(state, evidence, "x" * 13000 if case == "oversize" else "候选",
                            remaining_seconds=10 if case == "deadline" else 120,
                            cancel_check=(lambda: True) if case == "cancelled" else None)
    assert result["review"]["decision"] == "unavailable"
    assert not reviewer.approved(state, evidence, "候选")


@pytest.mark.parametrize("bad", ["extra_authority", "contradiction", "duplicate", "forged_ref", "invented_requirement"])
def test_invalid_completion_protocol(bad):
    state = run_state()
    packet = review.completion_packet(state, observations(), "candidate")
    value = json.loads(verdict())
    if bad == "extra_authority": value["authorization"] = True
    if bad == "contradiction": value["coverage_gaps"] = ["未执行"]
    if bad in {"forged_ref", "invented_requirement"}:
        value["decision"] = "continue"
        value["findings"] = [{"requirement_quote": "invented" if bad == "invented_requirement" else "sum 等于 2 与 3 的和",
                              "claim": "missing", "evidence_refs": ["fake"] if bad == "forged_ref" else [],
                              "proposed_check": "read", "failure_condition": "sum != 5"}]
    text = json.dumps(value)
    if bad == "duplicate": text = text[:-1] + ',"decision":"complete"}'
    with pytest.raises(ValueError): review.parse_completion(text, packet)


def test_text_only_task_and_incidental_failed_tool_are_judged_by_model():
    reviewer, state = session(Client(lambda p: verdict())), run_state()
    for evidence in ([], [{"tool_action": "file.delete_to_trash", "ok": False, "failures": ["policy_rejected"]}]):
        assert reviewer.judge(state, evidence, "candidate", remaining_seconds=120)["review"]["decision"] == "complete"
        assert reviewer.approved(state, evidence, "candidate")


def run_orchestrator(monkeypatch, tmp_path, decisions, replies=("未修复候选", "修复后候选"), guidance=None):
    """Exercise the real orchestration loop with deterministic model transport."""
    from v3 import zongdiaodu as zd
    from v3.simple_chain import kernel
    from v3 import model_roles
    monkeypatch.setattr(model_roles, "configured_models", lambda endpoint: [endpoint])
    monkeypatch.setenv("TIANGONG_ADVERSARIAL_REVIEW", "judge")
    monkeypatch.setenv("TIANGONG_RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TIANGONG_V3_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(review, "duqu_model_endpoint_config", lambda: SimpleNamespace(
        provider_identity="fixture", model_name="fixture", protocol_family="test", config_fingerprint="test"))
    choices = iter(decisions)
    client = Client(lambda p: next(choices))
    client.scoped_native_history = lambda *a, **k: nullcontext()
    client.scoped_tools = lambda **k: nullcontext()
    client.scoped_native_audio = lambda *a, **k: nullcontext()
    candidates = iter(replies)
    feedbacks, callbacks, events, snapshots = [], [], [], []
    def model(*args, **kwargs):
        callbacks.append(kwargs.get("on_text_chunk"))
        if len(args) > 1 and isinstance(args[1], dict): feedbacks.append(deepcopy(args[1]))
        return SimpleNamespace(), next(candidates)
    host = zd.Zongdiaodu.__new__(zd.Zongdiaodu)
    host.http_kehuduan = client
    host.gutong = SimpleNamespace(huanxing=model, jixu=model, jiexi_duogongju=zd.GutongCeng.jiexi_duogongju)
    host._baocun_shenti = lambda *a: None
    monkeypatch.setattr(zd, "TONGBU", SimpleNamespace(tuibo=lambda *a: None))
    monkeypatch.setattr(zd, "QUANZHUIXIAN", SimpleNamespace(jilu_kuadu=lambda *a: None, jieshu=lambda *a: None))
    original_save = kernel._simple_chain_save_run_state
    def save(state):
        original_save(state)
        snapshots.append(deepcopy(state))
    monkeypatch.setattr(zd, "_simple_chain_save_run_state", save)
    def forbidden(*a, **k): raise AssertionError("legacy system completion must not run")
    monkeypatch.setattr(zd, "_simple_chain_life_completion_gate", forbidden)
    monkeypatch.setattr(zd, "_simple_chain_regenerative_verify_completion", forbidden)
    control = SimpleNamespace(request_id="judge-test", step=lambda *a, **k: None,
                              should_stop=lambda: False, check_stop=lambda *a: None, consume_guidance=guidance or (lambda: ""),
                              interim_reply=lambda *a, **k: events.append((a, k)))
    result = host._huanxing_simple_chain(xiaoxi="仅生成一句文本", shenti=SimpleNamespace(),
        yonghu_tishi="仅生成一句文本", system_tishi="test", dynamic_context="", zhuizong_id="judge-test",
        run_control=control, started_at=time.monotonic(), on_event=events.append)
    return result, snapshots, client.calls, feedbacks, callbacks, events


def test_real_loop_retries_before_delivery_and_never_uses_old_gates(monkeypatch, tmp_path):
    result, snapshots, calls, feedbacks, callbacks, events = run_orchestrator(
        monkeypatch, tmp_path, [verdict("continue"), verdict()])
    assert result == "修复后候选"
    assert len(calls) == 2 and len(feedbacks) == 1
    assert feedbacks[0]["schema"] == review.COMPLETION_SCHEMA
    assert snapshots[-1]["status"] == "complete"
    assert snapshots[-1]["last_transition"]["source"] == "adversarial_agent"
    assert all(cb is None for cb in callbacks) and not events
    assert all(s["task_contract"]["goal_state"]["completion_percentage"] is None
               for s in snapshots if s.get("status") != "complete")


@pytest.mark.parametrize("answer", [verdict("blocked", reason="缺少运行 Python 的环境。"), "invalid-json"])
def test_real_loop_blocked_or_unavailable_never_delivers_candidate(monkeypatch, tmp_path, answer):
    result, snapshots, calls, feedbacks, callbacks, events = run_orchestrator(monkeypatch, tmp_path, [answer])
    assert "最终结果未提交" in result and "未修复候选" not in result
    assert snapshots[-1]["status"] == "incomplete"
    assert snapshots[-1]["task_contract"]["acceptance_status"] == "blocked"
    assert not feedbacks and not events


def test_timed_out_judge_cannot_approve_late_or_overlap(monkeypatch):
    import threading
    started, finish, exited = threading.Event(), threading.Event(), threading.Event()
    original = review.run_model_call
    monkeypatch.setattr(review, "run_model_call", lambda call, **kw: original(
        call, seconds=0.02, child=True, cancel_check=kw.get("cancel_check")))
    def slow(packet):
        started.set()
        finish.wait(2)
        exited.set()
        return verdict()
    reviewer, state = session(Client(slow)), run_state()
    try:
        result = reviewer.judge(state, observations(), "candidate", remaining_seconds=120)
        assert result["review"]["decision"] == "unavailable" and started.is_set()
        again = reviewer.judge(state, observations(4), "changed", remaining_seconds=120)
        assert again["review"]["coverage_gaps"] == ["judge_still_running"]
        before = deepcopy(state)
    finally:
        finish.set()
    assert exited.wait(1)
    assert state == before and not reviewer.approved(state, observations(), "candidate")


def test_cancel_after_inference_drops_successful_verdict():
    stopped = False
    def respond(packet):
        nonlocal stopped
        stopped = True
        return verdict()
    reviewer, state = session(Client(respond)), run_state()
    result = reviewer.judge(state, observations(), "candidate", remaining_seconds=120, cancel_check=lambda: stopped)
    assert result["review"]["decision"] == "unavailable"
    assert not reviewer.approved(state, observations(), "candidate")


def test_unapproved_attachments_cannot_escape_gateway_capture():
    from total_gateway.frozen_backend_compat import FrozenBackendCompatibilityTransport
    bridge = FrozenBackendCompatibilityTransport.__new__(FrozenBackendCompatibilityTransport)
    # No object store is installed: exporting any candidate would raise.
    for status in ("incomplete", "failed", "force_stopped", "reviewing"):
        assert bridge._capture_outputs(None, {
            "completion_authority": "adversarial_agent", "simple_chain_status": status,
            "attachments": [{"path": "candidate.docx"}],
            "run": {"generated_attachments": [{"path": "fallback.docx"}]},
        }, created_at_ms=1) == ([], ())


def test_completion_record_is_valid_gateway_signed_contract_data():
    from contracts import canonical_json_bytes
    state = run_state()
    session(Client(lambda p: verdict())).judge(state, observations(), "candidate", remaining_seconds=120)
    encoded = canonical_json_bytes(state["adversarial_completion"])
    assert b'"elapsed_ms":' in encoded


def test_user_guidance_arriving_during_judgment_requires_fresh_review(monkeypatch, tmp_path):
    messages = iter(["", "改成另一句文本", "", ""])
    result, snapshots, calls, feedbacks, _, _ = run_orchestrator(
        monkeypatch, tmp_path, [verdict(), verdict()], guidance=lambda: next(messages, ""))
    assert result == "修复后候选" and len(calls) == 2
    assert calls[-1]["user_guidance"] == ["改成另一句文本"]
    assert feedbacks[0]["schema"] == "tiangong.v3.user_guidance.v1"
