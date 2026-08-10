from __future__ import annotations

from pathlib import Path

from contracts.canonical import canonical_sha256
from contracts.world_understanding.query import WorldQuery, derive_world_query_id
from contracts.world_understanding.scope import (
    ScopeBinding,
    WorldScope,
    derive_world_id,
    derive_world_scope_hash,
)
from contracts.world_understanding.time import WorldTime
from world_understanding.context_output import (
    ContextOutputPort,
    WorldContextProjector,
    WorldContextRequestHandler,
    build_context_request_envelope,
)
from world_understanding.post_commit import (
    NativePostCommitEvent,
    install_native_post_commit_observer,
    notify_native_post_commit,
)
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.software_world import SoftwareWorldFrame
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import WorldStateStore


PRINCIPAL = "a" * 64


def _scope() -> WorldScope:
    bindings = (
        ScopeBinding(key="frame_kind", value="v3_runtime_workspace"),
        ScopeBinding(key="workspace_id", value="workspace.main"),
    )
    world_id = derive_world_id(
        life_id="life.main", namespace_anchor="workspace:workspace.main"
    )
    return WorldScope(
        life_id="life.main",
        world_id=world_id,
        domain_id="software_runtime",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id="life.main",
            world_id=world_id,
            domain_id="software_runtime",
            scope_bindings=bindings,
        ),
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
    )


def _frame(envelope, cut) -> SoftwareWorldFrame:
    return SoftwareWorldFrame.build(
        scope=envelope.scope_hint,
        workspace="workspace.main",
        repository="workspace:workspace.main",
        worktree="workspace:workspace.main",
        branch="runtime-current",
        commit="runtime-current",
        environment="test",
        time=envelope.source_time,
        world_cut=cut,
    )


def _source(native_id: str, at_ms: int = 1):
    return build_post_commit_source_envelope(
        source_kind="FACT_EXECUTION",
        source_native_id=native_id,
        producer_ref="v3.fact_kernel",
        payload={
            "fact_transaction": {
                "operation_id": native_id,
                "action": "write_file",
                "state": "OBSERVED",
            }
        },
        source_time=WorldTime(
            valid_from_ms=at_ms, observed_at_ms=at_ms, recorded_at_ms=at_ms
        ),
        scope=_scope(),
        correlation_id="corr." + native_id,
        workspace_id="workspace.main",
    )


def _current(runtime: ProductionWorldUnderstandingRuntime):
    envelope = _source("probe")
    frame = _frame(envelope, None)
    scope = envelope.scope_hint
    return runtime.store.current(
        life_id=scope.life_id,
        world_scope_hash=scope.world_scope_hash,
        principal_scope_hash=scope.principal_scope_hash,
        frame_id=frame.frame_id,
    )


def test_source_envelope_materializes_once_and_advances_coherent_state(tmp_path: Path):
    runtime = ProductionWorldUnderstandingRuntime(
        store=WorldStateStore(root=tmp_path / "state"), frame_factory=_frame
    )
    first = runtime.facade.accept(_source("op.one", 10))
    duplicate = runtime.facade.accept(_source("op.one", 10))
    second = runtime.facade.accept(_source("op.two", 20))

    current = _current(runtime)
    assert first.disposition == "ACCEPTED"
    assert first.reason_code == "SOURCE_MATERIALIZED"
    assert duplicate is first
    assert second.reason_code == "SOURCE_MATERIALIZED"
    assert current is not None
    assert current.state.world_sequence == 1
    assert len(current.entity_heads.refs) == 1
    assert len(current.entities) == 1
    assert current.entity_heads.refs[0].sha256 == current.entities[0].entity_sha256


def test_persisted_world_graph_rehydrates_in_the_same_store_after_restart(tmp_path: Path):
    root = tmp_path / "state"
    first_runtime = ProductionWorldUnderstandingRuntime(
        store=WorldStateStore(root=root), frame_factory=_frame
    )
    assert first_runtime.facade.accept(_source("op.one", 10)).processed

    restarted = ProductionWorldUnderstandingRuntime(
        store=WorldStateStore(root=root), frame_factory=_frame
    )
    assert restarted.facade.accept(_source("op.two", 20)).processed
    current = _current(restarted)
    assert current is not None
    assert current.state.world_sequence == 1
    assert current.entity_heads.refs
    assert current.entities


def test_context_request_reuses_the_production_facade_and_current_store(tmp_path: Path):
    store = WorldStateStore(root=tmp_path / "state")
    output = ContextOutputPort()

    def resolve(query):
        basis = query.basis_world_state_ref
        return None if basis is None else store.get(basis.record_id)

    handler = WorldContextRequestHandler(
        state_resolver=resolve,
        projector=WorldContextProjector(token_estimator=lambda text: max(1, len(text) // 4)),
        output_port=output,
    )
    runtime = ProductionWorldUnderstandingRuntime(
        store=store, frame_factory=_frame, context_request_handler=handler
    )
    assert runtime.facade.accept(_source("op.one", 10)).processed
    snapshot = _current(runtime)
    assert snapshot is not None
    focus = "understand the current workspace"
    task_sha = canonical_sha256({"focus": focus})
    correlation = "corr.context.one"
    query_id = derive_world_query_id(
        world_scope_hash=snapshot.state.scope.world_scope_hash,
        correlation_id=correlation,
        task_ref="task.context",
        task_sha256=task_sha,
        focus=focus,
        created_at_ms=30,
    )
    query = WorldQuery(
        query_id=query_id,
        correlation_id=correlation,
        scope=snapshot.state.scope,
        frame_ref=snapshot.state.frame_ref,
        basis_world_state_ref=snapshot.state_ref,
        task_ref="task.context",
        task_sha256=task_sha,
        focus=focus,
        token_budget=2400,
        created_at_ms=30,
        query_sha256="0" * 64,
    ).with_computed_hash()
    receipt = runtime.facade.accept(build_context_request_envelope(query))
    emission = output.take(correlation)
    assert receipt.reason_code == "CONTEXT_PACKET_EMITTED"
    assert emission is not None
    assert emission.packet.context_only is True
    assert emission.packet.authorizes is False
    assert emission.packet.may_execute is False


def test_materialization_failure_does_not_publish_partial_state(tmp_path: Path):
    store = WorldStateStore(root=tmp_path / "state")

    def fail_frame(_envelope, _cut):
        raise RuntimeError("frame unavailable")

    runtime = ProductionWorldUnderstandingRuntime(store=store, frame_factory=fail_frame)
    receipt = runtime.facade.accept(_source("op.fail", 10))
    assert receipt.disposition == "REJECTED"
    assert receipt.processed is False
    assert store.current_candidates(life_id="life.main", principal_scope_hash=PRINCIPAL) == ()
    assert not (tmp_path / "state").exists()


def test_native_post_commit_observer_is_fail_open_for_native_owner():
    event = NativePostCommitEvent(
        source_kind="FACT_EXECUTION",
        source_native_id="op.fail-open",
        producer_ref="test",
        payload={},
        occurred_at_ms=1,
    )

    def fail(_event):
        raise RuntimeError("world understanding unavailable")

    install_native_post_commit_observer(fail)
    try:
        assert notify_native_post_commit(event) is None
    finally:
        install_native_post_commit_observer(None)


def test_v3_composition_materializes_native_event_and_renders_context(monkeypatch, tmp_path: Path):
    import v3.world_context_integration as context_integration
    import v3.world_understanding_production as composition
    from v3.run_context import RunContext, bind_run_context

    monkeypatch.setenv("TIANGONG_WORLD_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("TIANGONG_WORLD_UNDERSTANDING_ENABLED", raising=False)
    monkeypatch.setattr(composition, "_runtime", None)
    monkeypatch.setattr(composition, "_context_output", None)
    monkeypatch.setattr(context_integration, "_runtime", None)
    composition.install_world_understanding_observer()
    run = RunContext(
        life_id="life.main",
        principal_scope_hash=PRINCIPAL,
        workspace_id="workspace.main",
        run_id="run.one",
        request_id="request.one",
        session_id="session.one",
    )
    event = NativePostCommitEvent(
        source_kind="TOOL_RESULT",
        source_native_id="call.one",
        producer_ref="v3.tool_result_contract",
        payload={"tool_name": "read_file", "ok": True, "status": "success"},
        occurred_at_ms=10,
    )
    try:
        with bind_run_context(run):
            receipt = notify_native_post_commit(event)
            rendered = context_integration.render_world_context_slot_for_turn(
                run_context=run, user_text="What is the current workspace state?"
            )
        assert receipt is not None
        assert receipt.reason_code == "SOURCE_MATERIALIZED"
        assert "[WORLD_CONTEXT_SLOT]" in rendered
        assert "[/WORLD_CONTEXT_SLOT]" in rendered
        assert (tmp_path / "state" / "index.json").is_file()
    finally:
        install_native_post_commit_observer(None)


def test_v3_composition_does_not_guess_missing_scope_identity(monkeypatch, tmp_path: Path):
    import v3.world_understanding_production as composition

    monkeypatch.setenv("TIANGONG_WORLD_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(composition, "_runtime", None)
    monkeypatch.setattr(composition, "_context_output", None)
    event = NativePostCommitEvent(
        source_kind="RUNTIME_ENVIRONMENT",
        source_native_id="runtime.one",
        producer_ref="v3.runtime_environment",
        payload={"machine": "test"},
        occurred_at_ms=10,
    )
    assert composition.observe_native_post_commit(event) is None
    assert not (tmp_path / "state").exists()
