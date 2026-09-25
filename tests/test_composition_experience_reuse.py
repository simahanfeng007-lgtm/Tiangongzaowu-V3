"""Real SQLite memory + Gateway ledger. Model acceptance is separately live-tested."""
from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from contracts import canonical_sha256
from capability_dictionary.composition import compile_task_composition
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from total_gateway.composition_experience import CompositionExperienceService
from tests.test_task_generated_composition import gateway, register, prepare, proposal


@pytest.fixture
def service(gateway, tmp_path):
    store = LifeShadowStore.open(tmp_path / "memory.shadow.sqlite3", create=True, now_ms=0)
    coordinator = MemoryCoordinator(store)
    contract = gateway.store.get_execution_task_contract(gateway.request_id, run_id=gateway.run_id, generation=gateway.generation)
    life_id = contract["life_id"]
    runtime = SimpleNamespace(store=gateway.store, config=SimpleNamespace(workspace_root=tmp_path),
        life_service=SimpleNamespace(_active=lambda: {"life_id": life_id}, _memory_coordinator=lambda: coordinator))
    service = CompositionExperienceService(runtime)
    # A real registered ledger; only terminal request projection/envelope is a fixture.
    source = proposal("monthly totals")
    registered = register(gateway, source)
    program = compile_task_composition(source)
    for ordinal, leaf in enumerate(program["leaves"], 1):
        bound = prepare(gateway, registered, leaf, ordinal)
        effect = {key: bound[key] for key in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
        gateway.provider(gateway.payload("start_effect", now_ms=3000 + ordinal, **effect))
        gateway.provider(gateway.payload("finish_effect", now_ms=4000 + ordinal, outcome="succeeded", result_summary={"ok": True}, **effect))
    snapshot = SimpleNamespace(machine="request", request_id=gateway.request_id, run_id=gateway.run_id,
        generation=gateway.generation, state="COMPLETED", last_event_id="evt_terminal")
    gateway.store.list_request_snapshots = lambda request_id: (snapshot,)
    gateway.store.get_request_envelope = lambda request_id: SimpleNamespace(text="汇总销售数据并生成销售表格 monthly sales totals")
    try:
        yield service, gateway, store, snapshot
    finally:
        store.close()


def approve(service, case, event="feedback_accept_1"):
    return service.feedback({"request_id": case.request_id, "mode": "accept", "event_id": event})


def test_approval_persists_full_program_and_recalls_paraphrase(service):
    svc, case, store, _ = service
    first = approve(svc, case)
    again = approve(svc, case)
    assert first["experience_id"] == again["experience_id"] and again["duplicate"]
    assert len(svc._rows()) == 1
    row, derivation = svc._rows()[0]
    assert derivation.layer == "L3_EXPERIENCE" and derivation.semantic_domain == "CAPABILITY_KNOWLEDGE"
    assert row["source"]["episodes"][0]["proposal"]["tools"][0]["actions"][0]["args"]["content"] == "monthly totals"
    assert first["experience_id"] in svc.recall("用另一份数据制作销售汇总表")
    assert svc.recall("写一首关于月亮的诗") == ""
    assert row["may_execute"] is row["may_authorize"] is False
    assert store.get_memory_assertion(derivation.memory_id, derivation.memory_revision).epistemic_status == "user_asserted"


def test_world_projection_reuses_same_memory_readonly_and_checks_scope(service):
    svc, case, _, running = service
    accepted = approve(svc, case)
    life_id = svc._scope()[0]
    scope = SimpleNamespace(life_id=life_id, principal_scope_hash="a"*64,
        scope_bindings=(SimpleNamespace(key="workspace_id", value="workspace.test"),))
    query = SimpleNamespace(scope=scope, basis_world_state_ref="state.test", focus="销售汇总")
    snapshot = SimpleNamespace(state_ref="state.test", state=SimpleNamespace(scope=scope))
    context = SimpleNamespace(life_id=life_id, principal_scope_hash="a"*64, workspace_id="workspace.test",
        request_id=case.request_id, run_id=running.run_id, generation=running.generation)
    before = svc._rows()
    entries = svc.world_context(query, snapshot, context)
    assert len(entries) == 1 and entries[0].experience_id == accepted["experience_id"]
    assert entries[0].status == "USER_APPROVED" and "file.write" in entries[0].summary
    assert svc._rows() == before
    context.principal_scope_hash = "b"*64
    assert svc.world_context(query, snapshot, context) == ()


def test_reopen_then_withdraw_does_not_reappear(service, tmp_path):
    svc, case, store, _ = service
    accepted = approve(svc, case)
    store.close()
    reopened = LifeShadowStore.open(tmp_path / "memory.shadow.sqlite3", create=False, now_ms=1)
    svc.runtime.life_service._memory_coordinator = lambda: MemoryCoordinator(reopened)
    try:
        assert accepted["experience_id"] in svc.recall("销售汇总")
        svc.feedback({"request_id": case.request_id, "mode": "withdraw", "event_id": "feedback_withdraw_1"})
        assert svc.recall("销售汇总") == ""
        # Retrying an old approval cannot undo a newer withdrawal.
        assert approve(svc, case)["status"] == "withdrawn"
    finally:
        reopened.close()


def test_incomplete_result_and_forged_acceptance_are_rejected(service):
    svc, case, _, snapshot = service
    snapshot.state = "FAILED"
    with pytest.raises(ValueError, match="not_completed"):
        approve(svc, case)
    with pytest.raises(ValueError, match="feedback_fields"):
        svc.feedback({"request_id": case.request_id, "mode": "accept", "approved": True})
    assert not svc._rows()


def test_partial_or_ambiguous_language_never_endorses_whole_program(service):
    svc, case, _, _ = service
    for decision, scope in (("none", "none"), ("accept", "partial")):
        svc.interpret = lambda text: {"decision": decision, "scope": scope, "feedback_only": False}
        result = svc.feedback({"request_id": case.request_id, "mode": "interpret", "user_text": "记住排版，计算还不对", "event_id": "feedback_partial"})
        assert not result["saved"]
    assert not svc._rows()


def test_source_drift_and_workspace_isolation(service, monkeypatch):
    svc, case, _, _ = service
    approve(svc, case)
    original_root = svc.runtime.config.workspace_root
    svc.runtime.config.workspace_root = original_root / "unrelated"
    assert svc.recall("销售汇总") == ""
    svc.runtime.config.workspace_root = original_root
    from capability_dictionary import load_dictionary
    release = load_dictionary()
    tools = deepcopy(release.tools)
    tools["file.write"] = {**tools["file.write"], "changed": True}
    monkeypatch.setattr("total_gateway.composition_experience.load_dictionary", lambda: SimpleNamespace(tools=tools))
    assert svc.recall("销售汇总") == ""


def test_new_program_use_and_failed_outcome_are_durable(service):
    svc, case, _, snapshot = service
    result = approve(svc, case)
    value = proposal("new input, new result")
    value["experience_refs"] = [result["experience_id"]]
    program = compile_task_composition(value)
    identity = SimpleNamespace(request_id=case.request_id, run_id=case.run_id, generation=case.generation)
    svc.bind_use(identity, program)
    svc.bind_use(identity, program)
    snapshot.state = "FAILED"
    svc.observe_terminal(case.request_id)
    svc.observe_terminal(case.request_id)
    row = svc._rows()[0][0]
    assert len(row["uses"]) == 1
    assert next(iter(row["uses"].values()))["status"] == "failed"
    assert svc.recall("销售汇总") == ""


def test_feedback_interpretation_is_bound_to_actual_user_words(service):
    svc, _, _, _ = service
    client = SimpleNamespace(scoped_tools=lambda **kw: nullcontext(), llm_diaoyong=lambda *args: json.dumps(
        {"decision": "accept", "scope": "whole", "quote": "forged words", "feedback_only": True}))
    svc.runtime.backend_service = SimpleNamespace(scheduler=SimpleNamespace(http_kehuduan=client))
    with pytest.raises(ValueError, match="feedback_unbound"):
        svc.interpret("这个做法可以，以后记住")


def test_later_generation_cannot_certify_prior_experience_use(service):
    svc, case, _, snapshot = service
    accepted = approve(svc, case)
    value = proposal("new result")
    value["experience_refs"] = [accepted["experience_id"]]
    svc.bind_use(SimpleNamespace(request_id=case.request_id, run_id=case.run_id, generation=case.generation),
                 compile_task_composition(value))
    snapshot.generation += 1
    svc.observe_terminal(case.request_id)
    assert next(iter(svc._rows()[0][0]["uses"].values()))["status"] == "pending"


@pytest.mark.parametrize("failure", [False, True])
def test_use_outcome_tracks_actual_composition_even_if_task_completes(service, failure):
    svc, case, _, _ = service
    accepted = approve(svc, case)
    case.provider._experience_service = svc
    value = proposal("adapted new input")
    for tool in value["tools"]:
        for action in tool["actions"]:
            action["target"] = "adapted-result.txt"
    value["experience_refs"] = [accepted["experience_id"]]
    registered = register(case, value)
    for ordinal, leaf in enumerate(compile_task_composition(value)["leaves"], 4):
        prepared = prepare(case, registered, leaf, ordinal)
        effect = {key: prepared[key] for key in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
        case.provider(case.payload("start_effect", now_ms=6000 + ordinal, **effect))
        failed = failure and ordinal == 5
        case.provider(case.payload("finish_effect", now_ms=7000 + ordinal, outcome="failed_final" if failed else "succeeded",
            result_summary={"ok": not failed, "error": "input changed" if failed else ""}, **effect))
        if failed:
            break
    svc.observe_terminal(case.request_id)
    use = next(iter(svc._rows()[0][0]["uses"].values()))
    assert use["status"] == ("failed" if failure else "succeeded")
    assert use["request_status"] == "COMPLETED" and len(use["outcomes"]) == 3
    assert bool(svc.candidates("销售汇总")) is not failure
    if failure:
        assert "FAILURE_OBSERVED" in svc.recall("销售汇总")
        assert "用户认可的组合经验 / DATA" not in svc.recall("销售汇总")


def test_declaring_a_reference_without_execution_is_not_a_success(service):
    svc, case, _, _ = service
    accepted = approve(svc, case)
    value = proposal("unexecuted program")
    value["experience_refs"] = [accepted["experience_id"]]
    svc.bind_use(SimpleNamespace(request_id=case.request_id, run_id=case.run_id, generation=case.generation),
                 compile_task_composition(value))
    svc.observe_terminal(case.request_id)
    use = next(iter(svc._rows()[0][0]["uses"].values()))
    assert use["status"] == "not_executed" and use["outcomes"] == []


def test_interrupted_promotion_retries_without_duplicating_feedback(service, monkeypatch):
    svc, case, _, _ = service
    coordinator = svc.runtime.life_service._memory_coordinator()
    original = coordinator._materialize_promotion
    monkeypatch.setattr(coordinator, "_materialize_promotion", lambda **kw: (_ for _ in ()).throw(RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        approve(svc, case)
    monkeypatch.setattr(coordinator, "_materialize_promotion", original)
    assert approve(svc, case)["status"] == "accepted"
    assert len(svc._rows()[0][0]["feedback"]) == 1


def test_withdraw_still_works_when_source_is_no_longer_valid(service, monkeypatch):
    svc, case, _, _ = service
    approve(svc, case)
    monkeypatch.setattr(svc, "capture", lambda request: (_ for _ in ()).throw(ValueError("source_changed")))
    assert svc.feedback({"request_id": case.request_id, "mode": "withdraw", "event_id": "feedback_source_changed"})["status"] == "withdrawn"


def test_managed_experience_cannot_collide_or_bypass_scoped_recall_in_generic_context(service):
    from contracts import CausalContextItem
    from life_service.context_authority import LifeContextAuthority
    from tests.test_continuity_capsule import capsule
    svc, case, store, _ = service
    accepted = approve(svc, case)
    row, derivation = svc._rows()[0]
    # The legacy projection used to expose the same native memory a second time.
    summary = json.dumps(row, ensure_ascii=False)
    external = CausalContextItem(item_ref=derivation.memory_id, item_kind="memory", source_revision=1,
        summary=summary, epistemic_status="user_asserted", confidence_milli=500, priority=900,
        privacy_scope="private", token_count=len(summary.encode()), supporting_event_ids=())
    continuity = capsule(life_id=svc._scope()[0], created_at_ms=1000).with_computed_capsule_sha256()
    store.put_context_capsule(continuity)
    result = LifeContextAuthority(store).compile_and_authorize(continuity, current_request="新的任务",
        principal_scope_hash="a" * 64, writer_epoch=1, identity_revision=1, soul_revision=1,
        current_context_tokens=0, issued_at_ms=2000, external_items=(external,))
    assert all("tiangong.composition-experience.v1" not in item.summary for item in result.context_pack.items)
    assert accepted["experience_id"] in svc.recall("销售汇总")
    assert store.get_context_authorization(continuity.request_id, run_id=continuity.run_id, generation=continuity.generation)


def test_restart_reconciliation_removes_only_legacy_experience_projection(service):
    from life_service.embedded_runtime import EmbeddedLifeRuntime
    svc, case, store, _ = service
    accepted = approve(svc, case)
    row, derivation = svc._rows()[0]
    scope = {"memories": {derivation.memory_id: {"memory_id": derivation.memory_id, "content": row, "status": "active"}}}
    runtime = object.__new__(EmbeddedLifeRuntime)
    runtime.authority_store = store
    runtime.system = SimpleNamespace(journal=SimpleNamespace(events=lambda _: ()))
    runtime._scope_state = lambda *_: scope
    runtime._memory_contract_synced = set()
    runtime._memory_contract_divergences = {}
    runtime._memory_contract_rebuilt = {}
    assert runtime._reconcile_memory_contract(svc._scope()[0])
    assert not scope["memories"]
    assert accepted["experience_id"] in svc.recall("销售汇总")
