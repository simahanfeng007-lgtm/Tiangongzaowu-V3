"""Desktop projection must read sealed composition output, not parent prose."""
from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from contracts import canonical_json_bytes
from total_gateway.completion_gate import CompletionDecision
from total_gateway.continuity import build_task_continuity_capsule
from total_gateway.desktop_api import DesktopApiRouter
from total_gateway.composition_final_result import encode_composition_final_result
from total_gateway.store import StoreCorruptionError


def _case():
    request_id, run_id = "req_" + "1" * 64, "run_" + "2" * 64
    content = "第三个文件的真实测试内容\n第二行"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    aliases = {
        "final.s1": {"action": "file.list", "result": {"items": ["file1.txt", "file2.txt", "file3.txt"]}},
        "final.s2": {"action": "file.read", "result": {"path": "file3.txt", "content": content}},
        "final.s3": {"action": "file.hash", "result": {"path": "file3.txt", "sha256": digest}},
    }
    raw = canonical_json_bytes({
        "composition_final_output_aliases": aliases,
        "parent_reply": "组合计划已登记，执行结果由网关核验。",
    }).decode("utf-8")
    capsule = build_task_continuity_capsule(
        life_id="life_fixture", capsule_kind="TERMINAL_RESULT", request_id=request_id,
        run_id=run_id, generation=1, user_goal="读取并核验文件", created_at_ms=5000,
        final_result=raw, verified_fact_ids=("fact_child_read", "fact_parent"),
    )
    decision = CompletionDecision(
        request_id=request_id, run_id=run_id, generation=1, outcome="COMPLETED",
        reason_code="completion.requirements_satisfied", text_ready=True,
        execution_ready=True, artifacts_ready=True, delivery_ready=True,
        can_transition_request_completed=True, can_claim_platform_delivered=False,
        needs_reconciliation=False,
        execution_effect_states=(("eff_" + "3" * 64, "SUCCEEDED"),),
        artifact_revision_states=(), delivery_parts=(),
        supporting_fact_ids=("fact_child_read", "fact_parent"),
        candidate_text_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        verification_mode="PLAN_BOUND", verification_ready=True,
        verification_plan_sha256="4" * 64, verification_readiness_sha256="5" * 64,
        decision_sha256="0" * 64,
    ).with_computed_sha256()
    plan = SimpleNamespace(
        has_valid_identity=lambda: True, request_id=request_id, run_id=run_id,
        generation=1, executable_plan_id="ecp_" + "6" * 64,
        verification_plan_sha256="4" * 64,
        step_bindings=(SimpleNamespace(action_id="file.read", permission=SimpleNamespace(
            action_id="file.read", effect="read", has_valid_sha256=lambda: True,
            requires_confirmation=False, registry_risk="A0", allow_shell=False, allow_python=False,
            effective_risk="A0", allowed_side_effects=("read",),
        )),),
        final_output_aliases=tuple(SimpleNamespace(alias=alias) for alias in aliases),
    )
    snapshot = SimpleNamespace(machine="request", state="COMPLETED", run_id=run_id, generation=1)
    state = SimpleNamespace(capsule=capsule, terminal_status="TERMINAL", decisions=[decision], plan=plan)

    def check_scope(received_request_id, *, run_id, generation):
        assert (received_request_id, run_id, generation) == (request_id, snapshot.run_id, snapshot.generation)

    def terminal(*args, **kwargs):
        check_scope(*args, **kwargs)
        return SimpleNamespace(capsule=state.capsule, status=state.terminal_status)

    def decisions(*args, **kwargs):
        check_scope(*args, **kwargs)
        return tuple(SimpleNamespace(decision=item) for item in state.decisions)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("composition display read or mutated the parent Fact")

    store = SimpleNamespace(
        get_executable_composition_plan_for_request=lambda *_args, **_kwargs: SimpleNamespace(executable_plan=state.plan),
        get_terminal_request_capsule=terminal, list_completion_decisions=decisions,
        list_effects_for_request=forbidden,
    )
    router = object.__new__(DesktopApiRouter)
    router._runtime = SimpleNamespace(store=store, facts=SimpleNamespace(get_batch_for_fact=forbidden),
                                     objects=SimpleNamespace(read_bytes=forbidden))
    return router, request_id, snapshot, state, aliases, digest


def test_composition_displays_real_sealed_aliases_and_explicit_read_scope():
    router, request_id, snapshot, state, aliases, digest = _case()
    payload = router._desktop_result_payload(request_id, (snapshot,))
    assert payload["reply_text"].startswith("只读步骤已核验完成。")
    assert "第三个文件的真实测试内容" in payload["reply_text"]
    assert "file1.txt" in payload["reply_text"] and digest in payload["reply_text"]
    assert "组合计划已登记" not in payload["reply_text"]
    assert payload["composition_final_output_aliases"] == aliases
    assert payload["completion_scope"] == "read_only_terminal_execution"
    assert payload["read_only_execution_completed"] is True
    assert payload["task_completed"] is False
    assert payload["business_outcome_verified"] is False
    assert payload["user_goal_status"] == "not_verified"
    assert payload["execution_steps_verified"] is True
    assert payload["execution_requirements_verified"] is False
    assert payload["completion_decision_sha256"] == state.decisions[-1].decision_sha256
    assert payload["final_result_sha256"] == hashlib.sha256(state.capsule.final_result.encode("utf-8")).hexdigest()


def _attested_case():
    from contracts import canonical_sha256
    from total_gateway.composition_task_floor import seal_execution_requirements_attestation
    from v3.execution_integrity import build_action_obligations
    router, request_id, snapshot, state, aliases, _digest = _case()
    text = "请读取 file3.txt。"
    state.plan.executable_plan_sha256 = "6" * 64
    router._runtime.store.get_request_envelope = lambda _request_id: SimpleNamespace(text=text)
    obligations = build_action_obligations(text)
    assert obligations
    proof = seal_execution_requirements_attestation(
        {"obligations_count": len(obligations), "obligations_sha256": canonical_sha256(obligations),
         "required_outputs": [], "output_witnesses": [], "execution_requirements_verified": True,
         "business_outcome_verified": False}, request_id=request_id, user_text=text,
        executable_plan_id=state.plan.executable_plan_id, executable_plan_sha256=state.plan.executable_plan_sha256,
        supporting_fact_ids=("fact_child_read",), execution_completed_at_ms=5000,
    )
    raw = encode_composition_final_result(aliases, parent_reply="registered", execution_requirements_attestation=proof)
    state.capsule = state.capsule.model_copy(update={"final_result": raw}).with_computed_capsule_sha256()
    state.decisions = [state.decisions[-1].model_copy(update={
        "candidate_text_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }).with_computed_sha256()]
    return router, request_id, snapshot, state, proof


def test_sealed_execution_requirements_project_separately_from_business_claims():
    router, request_id, snapshot, state, proof = _attested_case()
    payload = router._desktop_result_payload(request_id, (snapshot,))
    assert payload["execution_requirements_verified"] is True
    assert payload["execution_requirements_attestation_sha256"] == proof["sha256"]
    assert payload["user_goal_status"] == "execution_requirements_verified"
    assert payload["task_completed"] is False
    assert payload["business_outcome_verified"] is False
    assert payload["reply_text"].startswith("执行和文件交付要求已核验。")


@pytest.mark.parametrize("changed", ("request", "plan", "facts"))
def test_execution_requirements_cannot_be_borrowed_from_other_context(changed):
    router, request_id, snapshot, state, _proof = _attested_case()
    if changed == "request":
        router._runtime.store.get_request_envelope = lambda _rid: SimpleNamespace(text="请创建 missing.py，并实际运行。")
    elif changed == "plan":
        state.plan.executable_plan_sha256 = "9" * 64
    else:
        state.capsule = state.capsule.model_copy(update={"verified_fact_ids": ("fact_parent",)}).with_computed_capsule_sha256()
        state.decisions = [state.decisions[-1].model_copy(update={"supporting_fact_ids": ("fact_parent",)}).with_computed_sha256()]
    with pytest.raises(StoreCorruptionError):
        router._desktop_result_payload(request_id, (snapshot,))


@pytest.mark.parametrize("phase", ["PLANNING", "EXECUTING", "DELIVERING", "FAILED"])
def test_parent_ack_is_never_exposed_as_final_result_before_completed(phase):
    router, request_id, snapshot, _state, _aliases, _digest = _case()
    snapshot.state = phase
    assert router._desktop_result_payload(request_id, (snapshot,)) == {}


@pytest.mark.parametrize("tamper", [
    "capsule_digest", "capsule_request", "capsule_run", "capsule_generation", "terminal_status",
    "final_text", "decision_digest", "decision_request", "verification_plan", "verification_ready",
    "facts", "readonly", "latest_decision", "aliases", "ack_only",
])
def test_composition_result_fails_closed_on_authority_or_content_mismatch(tamper):
    router, request_id, snapshot, state, _aliases, _digest = _case()
    decision = state.decisions[-1]
    if tamper == "capsule_digest":
        state.capsule = state.capsule.model_copy(update={"final_result": "changed without checksum"})
    elif tamper in {"capsule_request", "capsule_run", "capsule_generation"}:
        field, value = {"capsule_request": ("request_id", "req_" + "9" * 64),
                        "capsule_run": ("run_id", "run_" + "9" * 64),
                        "capsule_generation": ("generation", 99)}[tamper]
        state.capsule = state.capsule.model_copy(update={field: value}).with_computed_capsule_sha256()
    elif tamper == "terminal_status":
        state.terminal_status = "ACTIVE"
    elif tamper == "final_text":
        state.capsule = state.capsule.model_copy(update={"final_result": "valid capsule, other text"}).with_computed_capsule_sha256()
    elif tamper == "decision_digest":
        state.decisions = [decision.model_copy(update={"candidate_text_sha256": "0" * 64})]
    elif tamper == "decision_request":
        state.decisions = [decision.model_copy(update={"request_id": "req_" + "9" * 64}).with_computed_sha256()]
    elif tamper == "verification_plan":
        state.decisions = [decision.model_copy(update={"verification_plan_sha256": "9" * 64}).with_computed_sha256()]
    elif tamper == "verification_ready":
        state.decisions = [decision.model_copy(update={"verification_ready": False}).with_computed_sha256()]
    elif tamper == "facts":
        state.capsule = state.capsule.model_copy(update={"verified_fact_ids": ()}).with_computed_capsule_sha256()
    elif tamper == "readonly":
        state.plan.step_bindings[0].permission.effective_risk = "A1"
    elif tamper == "latest_decision":
        state.decisions.append(decision.model_copy(update={"outcome": "FAILED", "can_transition_request_completed": False}).with_computed_sha256())
    else:
        raw = canonical_json_bytes(
            {"composition_final_output_aliases": {"final.s1": "missing other outputs"}}
            if tamper == "aliases" else {"parent_reply": "registered"}
        ).decode("utf-8")
        state.capsule = state.capsule.model_copy(update={"final_result": raw}).with_computed_capsule_sha256()
        state.decisions = [decision.model_copy(update={"candidate_text_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}).with_computed_sha256()]
    with pytest.raises(StoreCorruptionError):
        router._desktop_result_payload(request_id, (snapshot,))


def test_composition_completed_without_terminal_capsule_or_decision_is_rejected():
    for missing in ("capsule", "decision"):
        router, request_id, snapshot, state, _aliases, _digest = _case()
        if missing == "capsule":
            router._runtime.store.get_terminal_request_capsule = lambda *_args, **_kwargs: None
        else:
            state.decisions = []
        with pytest.raises(StoreCorruptionError):
            router._desktop_result_payload(request_id, (snapshot,))


def test_ordinary_legacy_results_keep_existing_fact_projection():
    router, request_id, snapshot, _state, _aliases, _digest = _case()
    router._runtime.store.get_executable_composition_plan_for_request = lambda *_args, **_kwargs: None
    effect = SimpleNamespace(claim=SimpleNamespace(effect_kind="execution"), result=SimpleNamespace(fact_id="legacy_fact"))
    router._runtime.store.list_effects_for_request = lambda *_args, **_kwargs: (effect,)
    router._runtime.facts.get_batch_for_fact = lambda _fact_id: SimpleNamespace(
        result_payload_object_id="legacy_payload", result=SimpleNamespace(request_id=request_id),
    )
    router._runtime.objects.read_bytes = lambda _object_id: canonical_json_bytes({"reply_text": "ordinary result"})
    assert router._desktop_result_payload(request_id, (snapshot,)) == {"reply_text": "ordinary result"}


def _seal_test_reply(state, raw):
    state.capsule = state.capsule.model_copy(update={"final_result": raw}).with_computed_capsule_sha256()
    state.decisions = [state.decisions[-1].model_copy(update={
        "candidate_text_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }).with_computed_sha256()]


def test_lossless_float_outputs_reach_the_desktop_http_response():
    router, request_id, snapshot, state, aliases, _digest = _case()
    aliases["final.s1"]["result"]["modified"] = 1790131312.733
    for value in aliases.values():
        value["elapsed_seconds"] = 0.125
    raw = encode_composition_final_result(aliases, parent_reply="registered")
    _seal_test_reply(state, raw)
    payload = router._desktop_result_payload(request_id, (snapshot,))
    assert payload["composition_final_output_aliases"] == aliases
    response = router._artifact_response(200, {"run": payload})
    assert json.loads(response.body)["run"]["composition_final_output_aliases"] == aliases
    # The signed outer envelope still contains no float JSON values.
    assert canonical_json_bytes(json.loads(raw)).decode("utf-8") == raw


def test_readonly_json_display_preserves_underscores_and_embedded_backtick_fences():
    router, request_id, snapshot, state, aliases, _digest = _case()
    content = 'order_count=6\npaid_total=800.00\n```json\n{"keep_me":true}\n```\n```````'
    aliases["final.s2"]["result"]["content"] = content
    _seal_test_reply(state, encode_composition_final_result(aliases, parent_reply="registered"))
    payload = router._desktop_result_payload(request_id, (snapshot,))
    blocks = re.findall(r"^(`{3,})json\n([\s\S]*?)\n\1$", payload["reply_text"], re.MULTILINE)
    assert len(blocks) == len(aliases)
    assert [json.loads(body) for _fence, body in blocks] == list(aliases.values())
    assert json.loads(blocks[1][1])["result"]["content"] == content
    assert len(blocks[1][0]) == 3
    for fence, body in blocks:
        assert fence == "```"
        assert not re.search(r"^\s*`{3,}\s*$", body, re.MULTILINE)
    assert payload["composition_final_output_aliases"] == aliases


@pytest.mark.parametrize("tamper", ["hash", "size", "duplicate", "nan", "noncanonical", "mixed"])
def test_float_output_envelope_rejects_inner_tampering_even_with_rebound_outer_hash(tamper):
    router, request_id, snapshot, state, aliases, _digest = _case()
    envelope = json.loads(encode_composition_final_result(aliases, parent_reply="registered"))
    if tamper == "hash":
        envelope["composition_final_output_aliases_sha256"] = "0" * 64
    elif tamper == "size":
        envelope["composition_final_output_aliases_size_bytes"] += 1
    elif tamper == "mixed":
        envelope["composition_final_output_aliases"] = aliases
    else:
        inner = {"duplicate": '{"final.s1":1,"final.s1":2}',
                 "nan": '{"final.s1":NaN}',
                 "noncanonical": json.dumps(aliases, ensure_ascii=False, indent=2)}[tamper]
        raw_inner = inner.encode("utf-8")
        envelope.update(composition_final_output_aliases_json=inner,
                        composition_final_output_aliases_sha256=hashlib.sha256(raw_inner).hexdigest(),
                        composition_final_output_aliases_size_bytes=len(raw_inner))
    _seal_test_reply(state, canonical_json_bytes(envelope).decode("utf-8"))
    with pytest.raises(StoreCorruptionError):
        router._desktop_result_payload(request_id, (snapshot,))
