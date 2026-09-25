"""Production support for generated compositions; actual persisted World and prompt."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import time

import pytest

from contracts.world_understanding.time import WorldTime
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.semantic import SemanticPipeline
from world_understanding.semantic.model import SemanticModelDeferred
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import WorldStateStore
from world_understanding.context_output.runtime_facts import runtime_context_candidates
from test_world_understanding_p13_1_production_activation import _source, _frame, _current, _scope
from test_world_understanding_p8_semantic_pipeline import FakeModel, proposal


def tool_source(name="result.one", at=10, *, changed=(), deleted=(), ok=True, declared=False):
    payload = {"tool_name": "file.write", "ok": ok}
    if changed or deleted:
        payload.update(observed_write_effect=True, write_evidence={"authoritative": True,
            "changed_files": list(changed), "deleted_files": list(deleted)})
    if declared:
        payload.update(write_effect=True, paths=["D:/invented.txt"])
    return build_post_commit_source_envelope(source_kind="TOOL_RESULT", source_native_id=name,
        producer_ref="test.tool-result", payload=payload,
        source_time=WorldTime(valid_from_ms=at, observed_at_ms=at, recorded_at_ms=at),
        scope=_scope(), correlation_id="corr." + name, workspace_id="workspace.main")


def semantic_model():
    return FakeModel({"hypotheses": [proposal(subject=0, basis=[0],
        predicate="result.needs_check", value={"kind": "string", "string_value": "核对输出文件"})]})


def test_deferred_inference_retains_body_but_invalidates_changed_basis_and_survives_restart(tmp_path):
    model = semantic_model()
    rt = ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=tmp_path), frame_factory=_frame,
                                            semantic_pipeline=SemanticPipeline(model=model))
    assert rt.facade.accept(_source("one", 10)).processed
    first = _current(rt)
    assert first.hypotheses and not first.state.stale_refs
    def deferred(request):
        raise SemanticModelDeferred("SEMANTIC_RATE_LIMIT")
    model.generate = deferred
    assert rt.facade.accept(_source("two", 20)).processed
    second = _current(rt)
    assert second.hypotheses == first.hypotheses
    assert set(first.active_hypotheses.refs) <= set(second.state.stale_refs)
    reopened = WorldStateStore(root=tmp_path).get(second.state.world_state_id)
    assert reopened.hypotheses == first.hypotheses
    assert reopened.state.has_valid_hash()
    corrupt = first.hypotheses[0].model_copy(update={"uncertainty_milli": 1})
    with pytest.raises(ValueError, match="HYPOTHESIS_BODY"):
        WorldStateStore._validate_snapshot(replace(first, hypotheses=(corrupt,)))


def test_slow_semantic_provider_does_not_lock_other_ingress_and_stale_result_is_discarded():
    entered, release = threading.Event(), threading.Event()
    model = semantic_model()
    generate = model.generate
    calls = 0
    def slow(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(5)
            return generate(request)
        raise SemanticModelDeferred("SEMANTIC_PROVIDER_BUSY")
    model.generate = slow
    rt = ProductionWorldUnderstandingRuntime(store=WorldStateStore(), frame_factory=_frame,
                                            semantic_pipeline=SemanticPipeline(model=model))
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(rt.facade.accept, _source("slow", 10))
        assert entered.wait(2)
        second = workers.submit(rt.facade.accept, tool_source("fast", 20, changed=("D:/fresh.txt",)))
        try:
            assert second.result(timeout=2).processed
        finally:
            release.set()
        assert first.result(timeout=3).processed
    snapshot = _current(rt)
    assert any(e.canonical_name == "D:/fresh.txt" and e.lifecycle == "ACTIVE" for e in snapshot.entities)
    assert not snapshot.hypotheses
    assert rt._last_semantic_trace.status == "SUPERSEDED"


def test_only_observed_files_create_relations_and_deletion_retires_file():
    rt = ProductionWorldUnderstandingRuntime(store=WorldStateStore(), frame_factory=_frame)
    assert rt.facade.accept(tool_source("declared", declared=True)).processed
    assert not any(e.entity_type == "File" for e in _current(rt).entities)
    assert rt.facade.accept(tool_source("write", 20, changed=("D:/observed.txt",))).processed
    snapshot = _current(rt)
    assert any(e.entity_type == "File" and e.canonical_name == "D:/observed.txt" for e in snapshot.entities)
    assert any(r.predicate == "writes" or r.predicate == "WRITES" or r.predicate == "tool.writes" for r in snapshot.relations)
    assert rt.repository_evidence_snapshot(scope=_scope()) is None
    assert rt.facade.accept(tool_source("delete", 30, deleted=("D:/observed.txt",))).processed
    assert any(e.entity_type == "File" and e.lifecycle == "RETIRED" for e in _current(rt).entities)


@pytest.fixture
def production(monkeypatch, tmp_path):
    from v3 import world_understanding_production as wp, world_context_integration as wc
    monkeypatch.setenv("TIANGONG_WORLD_STATE_ROOT", str(tmp_path / "world"))
    monkeypatch.setenv("TIANGONG_WORLD_SEMANTIC_ENABLED", "0")
    monkeypatch.setenv("TIANGONG_WORLD_UNDERSTANDING_ENABLED", "1")
    for name in ("_runtime", "_context_output", "_active_coordinator", "_active_dispatcher", "_composition_memory_provider"):
        monkeypatch.setattr(wp, name, None)
    monkeypatch.setattr(wc, "_runtime", None)
    return wp, wc


def context(**changes):
    from v3.run_context import RunContext
    return RunContext(life_id="life.main", principal_scope_hash=changes.pop("principal_scope_hash", "a"*64),
        workspace_id="workspace.main", request_id=changes.pop("request_id", "request.one"), run_id="run.one",
        generation=1, current_user_text=changes.pop("current_user_text", "读取销售文件并生成 Excel 表格"), **changes)


def test_dynamic_task_has_bounded_same_frame_dictionary_and_fresh_readable_prompt(production, tmp_path):
    from v3.run_context import bind_run_context
    wp, wc = production
    ctx = context()
    with bind_run_context(ctx):
        first = wp.refresh_task_world_snapshot(ctx)
        assert 1 < sum(e.entity_type == "ToolCapability" for e in first.entities) <= 32
        assert not any(e.entity_type == "SkillMethod" for e in first.entities)
        runtime = wp.production_world_understanding_runtime()
        assert len(runtime.store.current_candidates(life_id=ctx.life_id, principal_scope_hash=ctx.principal_scope_hash)) == 1
        assert runtime.facade.accept(tool_source(changed=("D:/actual-output.xlsx",))).processed
        prompt = wc.refresh_world_context_in_prompt("base\n[WORLD_CONTEXT_SLOT]obsolete[/WORLD_CONTEXT_SLOT]", run_context=ctx, user_text=ctx.current_user_text)
        assert prompt.count("[WORLD_CONTEXT_SLOT]") == 1 and "obsolete" not in prompt
        assert "actual-output.xlsx" in prompt and "[ACTION_CANDIDATES]" in prompt
        assert "action_summary" in prompt
        current = wp.refresh_task_world_snapshot(ctx)
        assert current.frame_id == first.frame_id and current.state.world_sequence > first.state.world_sequence
        size = (tmp_path / "world" / "snapshots" / (current.state.world_state_id + ".json")).stat().st_size
        assert size < 1_000_000, "bounded catalog must not explode each persisted state"
    other = context(principal_scope_hash="b"*64, request_id="request.other")
    with bind_run_context(other):
        fresh = wp.refresh_task_world_snapshot(other)
        assert fresh.state.scope.principal_scope_hash == "b"*64
        assert not any(e.canonical_name == "D:/actual-output.xlsx" for e in fresh.entities)


def test_explicit_off_removes_stale_slot(production, monkeypatch):
    _, wc = production
    monkeypatch.setenv("TIANGONG_WORLD_UNDERSTANDING_ENABLED", "0")
    assert wc.refresh_world_context_in_prompt("base\n[WORLD_CONTEXT_SLOT]old[/WORLD_CONTEXT_SLOT]", run_context=context(), user_text="文件") == "base"


def test_host_schema_exposes_compiler_experience_field():
    from capability_dictionary import load_dictionary
    spec = load_dictionary().host_protocol["parameters"]["properties"]["composition"]["properties"]["experience_refs"]
    assert spec["maxItems"] == 3 and spec["uniqueItems"] is True


def test_wire_schema_retains_mutually_exclusive_composition_and_discovery():
    from capability_dictionary import load_dictionary
    from v3.jineng.http_kehuduan import _zhuanhuan_openai_geshi
    spec = load_dictionary().host_protocol
    wire = _zhuanhuan_openai_geshi([spec])[0]["function"]["parameters"]
    assert wire == spec["parameters"]
    assert wire["oneOf"][0]["required"] == ["composition"]
    assert wire["oneOf"][1]["required"] == ["action"]
    wire["oneOf"].clear()
    assert len(spec["parameters"]["oneOf"]) == 2


def test_field_error_identifies_missing_leaf_field_without_echoing_arguments():
    from capability_dictionary.composition import compile_task_composition
    from capability_dictionary import DictionaryError
    value = {"tools": [{"id": "read", "description": "Read input", "actions": [{"action": "file.read", "target": "private-path"}]}],
        "skill": {"id": "inspect", "description": "Inspect", "steps": [{"id": "one", "tool": "read", "depends_on": []}]}}
    with pytest.raises(DictionaryError) as caught:
        compile_task_composition(value)
    repair = caught.value.composition_repair
    assert repair["path"] == "composition.tools[0].actions[0]"
    assert repair["missing_fields"] == ["args"]
    assert "private-path" not in json.dumps(repair)


def test_postcommit_failure_is_observable_without_leaking_exception(caplog):
    from world_understanding.post_commit import NativePostCommitEvent, install_native_post_commit_observer, notify_native_post_commit
    def broken(event):
        raise RuntimeError("private-api-key-and-body")
    install_native_post_commit_observer(broken)
    try:
        assert notify_native_post_commit(NativePostCommitEvent("TOOL_RESULT", "one", "test", {}, 1)) is None
        assert "WORLD_POST_COMMIT_FAILED" in caplog.text and "private-api-key" not in caplog.text
    finally:
        install_native_post_commit_observer(None)


def test_invalid_optional_world_evidence_cannot_fail_life_terminal_commit():
    from total_gateway.orchestration import GatewayOrchestrationWorker
    worker = object.__new__(GatewayOrchestrationWorker)
    committed = []
    worker._life_execution_commit = lambda payload: committed.append(payload) or {"ok": True}
    worker._repository_evidence_provider = lambda identity: {"schema": "tiangong.life.repository-evidence.v1",
        "commit": "runtime-current", "repository_id": "workspace:main"}
    result = worker._commit_life_execution(request_id="req_"+"a"*64, run_id="run_"+"b"*64,
        generation=1, life_id="life.test", session_scope_hash="c"*64, principal_scope_hash="d"*64,
        workspace_id="workspace.test", user_goal="Write a file", final_result="Verified file",
        fact_ids=("fact.test",), completed_at_ms=10)
    assert result == {"ok": True}
    assert len(committed) == 1 and "repository_evidence" not in committed[0]
