from __future__ import annotations

from types import SimpleNamespace
from contextlib import contextmanager
import queue
import types

import pytest

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError
from total_gateway.embedded_backend import EmbeddedBackendRuntime
from world_understanding.active_cognition import ActiveWorldCognitionCoordinator
from world_understanding.source_adapters import build_autonomous_execution_feedback_envelope
from world_understanding.world_state import WorldStateStore


def _scope() -> WorldScope:
    bindings = (ScopeBinding(key="repository", value="repo.main"),)
    world_id = derive_world_id(life_id="life.A", namespace_anchor="primary")
    return WorldScope(
        life_id="life.A",
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id="life.A", world_id=world_id, domain_id="software", scope_bindings=bindings
        ),
        principal_scope_hash="a" * 64,
        privacy_scope="system",
    )


def _snapshot(*, stale: bool, sequence: int = 0):
    scope = _scope()
    subject = WorldRecordRef(record_type="world_entity", record_id="ent.1", revision=1, sha256="2" * 64)
    frame = WorldRecordRef(record_type="world_frame", record_id="frame.1", revision=1, sha256="4" * 64)
    state_ref = WorldRecordRef(
        record_type="world_state", record_id=f"wst.fixture.{sequence}", revision=sequence + 1, sha256=("5" if sequence == 0 else "6") * 64
    )
    state = SimpleNamespace(
        world_state_id=state_ref.record_id,
        world_sequence=sequence,
        state_sha256=state_ref.sha256,
        scope=scope,
        frame_ref=frame,
        unresolved_conflict_refs=(),
        stale_refs=(subject,) if stale else (),
        has_valid_hash=lambda: True,
    )
    return SimpleNamespace(state=state, state_ref=state_ref, uncertainty=None), subject


def _source(scope: WorldScope, *, at_ms: int = 1_000):
    from world_understanding.source_adapters import build_post_commit_source_envelope

    return build_post_commit_source_envelope(
        source_kind="RUNTIME_ENVIRONMENT",
        source_native_id=f"runtime.{at_ms}",
        producer_ref="runtime.fixture",
        payload={"status": "changed"},
        source_time=WorldTime(valid_from_ms=at_ms, observed_at_ms=at_ms, recorded_at_ms=at_ms),
        scope=scope,
        correlation_id="corr.p13.2",
    )


def test_gap_is_persisted_once_and_reality_closes_same_cycle_after_restart(tmp_path):
    dispatched = []

    def dispatch(inquiry, sink):
        dispatched.append((inquiry, sink))
        return True

    store = WorldStateStore(root=tmp_path / "world")
    coordinator = ActiveWorldCognitionCoordinator(store=store, dispatcher=dispatch)
    before, _ = _snapshot(stale=True)
    coordinator.observe(_source(before.state.scope), before)
    coordinator.observe(_source(before.state.scope, at_ms=1_001), before)
    assert len(dispatched) == 1
    inquiry, sink = dispatched[0]
    autonomous = {
        "autonomous_intent_id": "waut_" + "b" * 64,
        "origin": "SELF_WILL",
        "principal": "life:self",
        "life_id": inquiry.scope.life_id,
        "source_inquiry_id": inquiry.inquiry_id,
        "source_inquiry_sha256": inquiry.inquiry_sha256,
        "goal": "Revalidate the stale entity",
        "suggested_observation_modalities": inquiry.suggested_observation_modalities,
        "authority_refs": (),
        "authorization": "NONE",
        "may_execute_directly": False,
        "requires_gateway_evaluation": True,
        "empirical_evidence_weight_milli": 0,
        "created_at_ms": 2_000,
        "expires_at_ms": 62_000,
        "autonomous_intent_sha256": "",
    }
    from world_understanding.inquiry.self_will_integration import AutonomousIntent

    intent = AutonomousIntent(**autonomous).with_hash()
    sink({
        "phase": "DECIDED",
        "at_ms": 2_000,
        "decision": "ACCEPT",
        "autonomous_intent": __import__("dataclasses").asdict(intent),
    })
    sink({
        "phase": "STARTED",
        "at_ms": 2_001,
        "run_id": "run_" + "c" * 64,
        "execution_ticket_id": "ticket.fixture",
    })
    after, _ = _snapshot(stale=False, sequence=1)
    feedback = build_autonomous_execution_feedback_envelope(
        source_kind="TOOL_RESULT",
        source_native_id="tool.result.p13.2",
        producer_ref="gateway.runtime",
        payload={"ok": True},
        source_time=WorldTime(valid_from_ms=3_000, observed_at_ms=3_000, recorded_at_ms=3_000),
        scope=after.state.scope,
        correlation_id=inquiry.correlation_id,
        source_inquiry_id=inquiry.inquiry_id,
        autonomous_intent_id=intent.autonomous_intent_id,
        gateway_intent_id="ticket.fixture",
        terminal_status="success",
        run_id="run_" + "c" * 64,
    )
    coordinator.observe(feedback, after)
    row = store.active_cognition_record(inquiry.inquiry_id)
    assert row is not None and row["status"] == "CLOSED"
    assert row["outcome"]["resolved"] is True
    assert row["outcome"]["information_gain_milli"] == 1000
    assert len(dispatched) == 1  # hard anti-loop on the reality transaction

    reopened = WorldStateStore(root=tmp_path / "world")
    durable = reopened.active_cognition_record(inquiry.inquiry_id)
    assert durable is not None and durable["record_sha256"] == row["record_sha256"]


def test_failed_or_nonaccepted_cycle_closes_without_reality(tmp_path):
    events = []

    def dispatch(inquiry, sink):
        events.append(sink)
        return True

    store = WorldStateStore(root=tmp_path / "world")
    snapshot, _ = _snapshot(stale=True)
    ActiveWorldCognitionCoordinator(store=store, dispatcher=dispatch).observe(
        _source(snapshot.state.scope), snapshot
    )
    inquiry_id = store.active_cognition_records()[0]["record_id"]
    events[0]({"phase": "DECIDED", "at_ms": 2_000, "decision": "DEFER"})
    events[0]({"phase": "DEFERRED", "at_ms": 2_001, "decision": "DEFER"})
    row = store.active_cognition_record(inquiry_id)
    assert row is not None and row["status"] == "CLOSED"
    assert row["outcome"]["self_will_decision"] == "DEFER"
    assert row["outcome"]["information_gain_milli"] == 0

    # A new state can produce a new stable inquiry id, but the same zero-gain
    # family remains backed off for 60 seconds across coordinator instances.
    later, _ = _snapshot(stale=True, sequence=1)
    ActiveWorldCognitionCoordinator(store=store, dispatcher=dispatch).observe(
        _source(later.state.scope, at_ms=3_000), later
    )
    assert len(events) == 1


def test_gateway_world_observation_is_hard_read_only():
    assert GatewayOrchestrationWorker._world_observation(
        {"action": "file.read", "target": "README.md", "args": {}}
    )["action"] == "file.read"
    with pytest.raises(OrchestrationError, match="not_read_only"):
        GatewayOrchestrationWorker._world_observation(
            {"action": "file.write", "target": "x.txt", "args": {"content": "x"}}
        )
    with pytest.raises(OrchestrationError, match="not_read_only"):
        GatewayOrchestrationWorker._world_observation(
            {"action": "shell.run", "target": "", "args": {"command": "whoami"}}
        )


def test_existing_self_will_model_bridge_only_proposes_and_run_context_keeps_lineage():
    fake = SimpleNamespace(
        scheduler=SimpleNamespace(
            _zhiming_llm=lambda _system, _user: '{"decision":"ACCEPT","goal":"observe",'
            '"reason_codes":["information_gain"],"observation":{"action":"system.health","target":"","args":{}}}'
        )
    )
    response = EmbeddedBackendRuntime._world_inquiry_decision(
        fake, {"inquiry": {"authorization": "NONE", "question": "health?"}}
    )
    assert response["ok"] is True
    assert response["decision"]["observation"]["action"] == "system.health"

    from v3.run_context import from_conversation_context

    context = from_conversation_context({
        "source_inquiry_id": "winq_" + "1" * 64,
        "autonomous_intent_id": "waut_" + "2" * 64,
    })
    assert context.source_inquiry_id.startswith("winq_")
    assert context.autonomous_intent_id.startswith("waut_")
    assert context.audit_metadata()["source_inquiry_id"] == context.source_inquiry_id


def test_existing_gateway_worker_lane_carries_inquiry_to_authorized_runtime(tmp_path):
    captured = []

    def dispatch(inquiry, _sink):
        captured.append(inquiry)
        return True

    snapshot, _ = _snapshot(stale=True)
    ActiveWorldCognitionCoordinator(
        store=WorldStateStore(root=tmp_path / "world"), dispatcher=dispatch
    ).observe(_source(snapshot.state.scope), snapshot)
    inquiry = captured[0]

    class Backend:
        def __init__(self):
            self.invocations = []

        def request(self, _method, path, payload, *, timeout_seconds):
            del timeout_seconds
            if path.endswith("/decision"):
                return 200, {
                    "ok": True,
                    "decision": {
                        "decision": "ACCEPT",
                        "goal": "Read the bounded health observation",
                        "reason_codes": ["information_gain"],
                        "observation": {"action": "system.health", "target": "", "args": {}},
                    },
                }, "fixture"
            self.invocations.append((path, payload))
            return 200, {"ok": True, "status": "success"}, "fixture"

    worker = object.__new__(GatewayOrchestrationWorker)
    worker._world_inquiries = queue.Queue(maxsize=4)
    worker._backend_compat_client = Backend()
    authorized = []

    @contextmanager
    def authorize(_self, **kwargs):
        authorized.append(kwargs)
        yield {
            "execution_ticket_id": "ticket.fixture",
            "request_id": "req_" + "1" * 64,
            "run_id": "run_" + "2" * 64,
            "generation": 0,
            "principal_scope_hash": inquiry.scope.principal_scope_hash,
            "workspace_id": "workspace.fixture",
            "life_id": inquiry.scope.life_id,
            "source_inquiry_id": inquiry.inquiry_id,
            "autonomous_intent_id": kwargs["autonomous_intent_id"],
        }

    worker.authorize_life_capability_action = types.MethodType(authorize, worker)
    events = []
    assert worker.submit_world_inquiry(inquiry, events.append)
    assert worker._dispatch_next_world_inquiry()
    assert [event["phase"] for event in events] == ["DECIDED", "STARTED"]
    assert authorized[0]["source_inquiry_id"] == inquiry.inquiry_id
    assert authorized[0]["action_id"] == "omni_body"
    assert worker._backend_compat_client.invocations[0][0].endswith("/life-action/invoke")


def test_production_gateway_path_uses_existing_life_intent_emitter():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/total_gateway/orchestration.py").read_text(encoding="utf-8")
    assert "LifeActionIntentEmitter(transport).submit_self_will(" in source
    assert 'source_type="EXTERNAL_DATA"' in source
    assert "authorization_source_refs=authorization_source_refs" in source
