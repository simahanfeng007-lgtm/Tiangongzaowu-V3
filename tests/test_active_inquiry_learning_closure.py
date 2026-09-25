from dataclasses import asdict, replace
from types import SimpleNamespace
import pytest

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.time import WorldTime
from world_understanding.active_cognition import ActiveWorldCognitionCoordinator
from world_understanding.inquiry.self_will_integration import ExistingSelfWillAdapter
from world_understanding.post_commit import install_native_post_commit_observer
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import MaterializationInput, WorldStateStore
from world_understanding.world_state.manifests import DependencyBinding
from tests.test_world_understanding_p13_1_production_activation import _scope, _frame
from tests.test_world_understanding_p13_2_active_closure import _snapshot, _source
from tests.test_world_dictionary_support import tool_source


def test_gateway_rebinds_only_an_authoritative_origin_in_the_same_scope(tmp_path):
    from contracts.canonical import canonical_sha256
    from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError
    from contracts.world_understanding.scope import ScopeBinding
    from contracts.scope import InboundScope, derive_inbound_scope_keys
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._workspace_root = tmp_path
    inbound = InboundScope(channel="desktop", tenant_id="desktop", link_account_id="desktop-local",
        conversation_ref="chat.original", channel_message_ref="message.original", sender_ref="desktop-user")
    keys = derive_inbound_scope_keys(inbound)
    principal = keys.principal_scope_hash
    origin = SimpleNamespace(**inbound.model_dump(exclude={"schema_version"}),
        conversation_scope_hash=keys.conversation_scope_hash, principal_scope_hash=principal)
    assert principal != canonical_sha256({"domain": "tiangong.gateway.life-principal-scope.v1", "tenant_id": "desktop",
        "link_account_id": "desktop-local", "conversation_scope_hash": keys.conversation_scope_hash})
    scope = SimpleNamespace(life_id="life.same", principal_scope_hash=principal,
        scope_bindings=(ScopeBinding(key="workspace_id", value="workspace-" + canonical_sha256(str(tmp_path))),))
    worker._store = SimpleNamespace(get_request_envelope=lambda _: origin,
        list_request_snapshots=lambda _: (SimpleNamespace(machine="request", run_id="run.one", generation=1),),
        get_execution_task_contract=lambda *a, **kw: {"life_id": "life.same"})
    assert worker._world_inquiry_origin(scope, "request.original", "life.same") is origin
    scope.principal_scope_hash = "b"*64
    with pytest.raises(OrchestrationError, match="origin_scope_mismatch"):
        worker._world_inquiry_origin(scope, "request.original", "life.same")


def test_autonomous_run_identity_can_enter_the_existing_omni_admission(monkeypatch):
    from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError
    from total_gateway.omni_grant_authority import OmniGrantAuthority
    captured = {}

    class CaptureLife:
        def __init__(self, *_args): pass
        def compile_and_authorize_snapshot(self, **bindings):
            captured.update(bindings)
            raise RuntimeError("stop before issuing authority")

    monkeypatch.setattr("total_gateway.orchestration.LifeClient", CaptureLife)
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = object()
    worker._life_transport_for_execution = lambda: None
    with pytest.raises(OrchestrationError, match="life_snapshot_unavailable"):
        with worker.authorize_life_capability_action(life_id="life.test", artifact_id="inquiry.test",
                artifact_sha256="a"*64, execution_id="autonomous.test", step_id="observe",
                action_id="omni_body", arguments={"action": "file.hash", "target": "a.txt", "args": {}}):
            raise AssertionError("must stop before authorization")
    assert OmniGrantAuthority._derive_run_sequence(captured["request_id"], captured["run_id"]) == 1


def test_real_file_observation_closes_seeded_stale_gap_and_persists(tmp_path):
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    from v3.runtime_tool_result_boundary import attach_tool_result_contract
    from v3.run_context import bind_run_context, current_run_context

    target = tmp_path / "observed.txt"
    target.write_text("actual independent readback", encoding="utf-8")
    store = WorldStateStore(root=tmp_path / "world")
    dispatched = []
    coordinator = ActiveWorldCognitionCoordinator(store=store,
        dispatcher=lambda inquiry, sink: dispatched.append((inquiry, sink)) or True)
    rt = ProductionWorldUnderstandingRuntime(store=store, frame_factory=_frame, committed_state_observer=coordinator.observe)
    initial = tool_source("write", 1000, changed=(str(target),))
    assert rt.facade.accept(initial).processed
    frame = _frame(initial, None)
    snapshot = rt._previous(frame)
    entity = next(e for e in snapshot.entities if e.entity_type == "File")
    ref = WorldRecordRef(record_type="world_entity", record_id=entity.entity_id, revision=entity.revision, sha256=entity.entity_sha256)
    stream = rt._streams[frame.frame_id]
    data = MaterializationInput(frame=stream.frame, cut=snapshot.cut, graph=stream.graph,
        dependency_bindings=(DependencyBinding(ref, ("fixture.file-version",)),),
        source_transaction_id="test.bind", materialized_at_ms=1100)
    rt._materializer.materialize(data)
    stale = rt._materializer.materialize(replace(data, source_transaction_id="test.changed", materialized_at_ms=1200,
        changed_source_keys=("fixture.file-version",)))
    assert ref in stale.state.stale_refs
    coordinator.observe(initial, stale)
    assert len(dispatched) == 1
    inquiry, sink = dispatched[0]
    decision, intent = ExistingSelfWillAdapter(lambda _inquiry: {"decision": "ACCEPT", "goal": "verify file digest"}).decide(inquiry, decided_at_ms=1300)
    sink({"phase": "DECIDED", "at_ms": 1300, "decision": "ACCEPT", "autonomous_intent": asdict(intent)})
    sink({"phase": "STARTED", "at_ms": 1301, "run_id": "run.observation", "execution_ticket_id": "ticket.observation"})

    def native(event):
        context = current_run_context()
        envelope = build_post_commit_source_envelope(source_kind=event.source_kind, source_native_id=event.source_native_id,
            producer_ref=event.producer_ref, payload=event.payload,
            source_time=WorldTime(valid_from_ms=2000, observed_at_ms=2000, recorded_at_ms=2000),
            scope=initial.scope_hint, correlation_id=inquiry.correlation_id, run_id=context.run_id,
            request_id=context.request_id)
        return rt.facade.accept(envelope)

    install_native_post_commit_observer(native)
    try:
        with bind_run_context({"request_id": "request.observation", "run_id": "run.observation", "life_id": initial.scope_hint.life_id,
                "principal_scope_hash": initial.scope_hint.principal_scope_hash, "workspace_id": "workspace.main",
                "execution_ticket_id": "ticket.observation", "source_inquiry_id": inquiry.inquiry_id,
                "autonomous_intent_id": intent.autonomous_intent_id}):
            call = {"action": "file.hash", "target": str(target), "args": {}}
            result = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="inquiry-test")).run(**call)
            assert result["success"]
            attach_tool_result_contract("omni_body", result, invocation=call, source_native_id="real.file.hash")
    finally:
        install_native_post_commit_observer(None)
    record = store.active_cognition_record(inquiry.inquiry_id)
    assert record["status"] == "CLOSED", record
    assert record["outcome"]["resolved"] and record["information_gain_milli"] == 1000, record
    assert len(record["outcome"]["observation_refs"]) == 1
    assert len(dispatched) == 1
    assert WorldStateStore(root=tmp_path / "world").active_cognition_record(inquiry.inquiry_id) == record


def test_revision_noise_cannot_duplicate_open_work_and_expiry_frees_capacity(tmp_path):
    dispatched = []
    store = WorldStateStore(root=tmp_path / "world")
    coordinator = ActiveWorldCognitionCoordinator(store=store, max_open_per_scope=1,
        dispatcher=lambda inquiry, sink: dispatched.append((inquiry, sink)) or True)
    snapshot, _ = _snapshot(stale=True)
    coordinator.observe(_source(snapshot.state.scope), snapshot)
    newer, _ = _snapshot(stale=True, sequence=1)
    coordinator.observe(_source(snapshot.state.scope, at_ms=2000), newer)
    assert len(dispatched) == 1
    reopened = ActiveWorldCognitionCoordinator(store=WorldStateStore(root=tmp_path / "world"),
        dispatcher=lambda *_args: True, max_open_per_scope=1)
    reopened.observe(_source(snapshot.state.scope, at_ms=100_000), newer)
    first = reopened._store.active_cognition_record(dispatched[0][0].inquiry_id)
    assert first["status"] == "CLOSED" and first["information_gain_milli"] == 0


def test_unresolvable_first_gap_does_not_starve_observable_file_gap(tmp_path):
    snapshot, file_ref = _snapshot(stale=True)
    unresolvable = WorldRecordRef(record_type="world_hypothesis", record_id="hyp.test", sha256="f"*64)
    snapshot.state.stale_refs = (unresolvable, file_ref)
    dispatched = []
    ActiveWorldCognitionCoordinator(store=WorldStateStore(root=tmp_path),
        dispatcher=lambda inquiry, _sink: dispatched.append(inquiry) or True).observe(_source(snapshot.state.scope), snapshot)
    assert len(dispatched) == 1 and dispatched[0].subject_refs == (file_ref,)


def test_missing_reality_feedback_closes_without_invented_gain(tmp_path):
    dispatched = []
    store = WorldStateStore(root=tmp_path)
    coordinator = ActiveWorldCognitionCoordinator(store=store,
        dispatcher=lambda inquiry, sink: dispatched.append((inquiry, sink)) or True)
    snapshot, _ = _snapshot(stale=True)
    coordinator.observe(_source(snapshot.state.scope), snapshot)
    inquiry, sink = dispatched[0]
    _, intent = ExistingSelfWillAdapter(lambda _: {"decision": "ACCEPT", "goal": "observe"}).decide(inquiry, decided_at_ms=2000)
    sink({"phase": "DECIDED", "at_ms": 2000, "decision": "ACCEPT", "autonomous_intent": asdict(intent)})
    sink({"phase": "STARTED", "at_ms": 2001, "run_id": "run.test", "execution_ticket_id": "ticket.test"})
    sink({"phase": "FINISHED", "at_ms": 3000})
    row = store.active_cognition_record(inquiry.inquiry_id)
    assert row["status"] == "CLOSED" and row["reason_code"] == "OBSERVATION_FEEDBACK_MISSING"
    assert not row["outcome"]["resolved"] and row["information_gain_milli"] == 0
