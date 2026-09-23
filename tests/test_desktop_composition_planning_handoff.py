"""Production planning seam and typed parent-stage handoff regressions.

These tests exercise wiring boundaries, not live model or task acceptance.
The existing coordinator suites own signed child execution and finalization.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import canonical_json_bytes, canonical_sha256
from tests.test_backend_client import signed_ticket
from total_gateway.frozen_backend_compat import FrozenBackendCompatibilityTransport
from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError


def _activation(channel="desktop"):
    return SimpleNamespace(
        entry=SimpleNamespace(request_id="req_" + "1" * 64),
        generation=SimpleNamespace(run_id="run_" + "2" * 64, generation=1),
        envelope=SimpleNamespace(channel=channel),
    )


class _PlanStore:
    def __init__(self, record=None):
        self.record = record
        self.reads = []

    def get_executable_composition_plan_for_request(self, request_id, **scope):
        self.reads.append((request_id, scope))
        return self.record


def _worker(planner, record=None):
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._store = _PlanStore(record)
    worker._composition_planner = planner
    return worker


def test_planning_uses_authoritative_activation_and_life_then_reloads_plan():
    activation = _activation()
    life = SimpleNamespace(identity_ref="life_fixture")
    record = SimpleNamespace(executable_plan=SimpleNamespace(registration_id="registration_fixture"))
    observed = []

    def planner(received, received_life):
        observed.append((received, received_life))
        worker._store.record = record
        return True

    worker = _worker(planner)
    assert worker._prepare_composition_plan(activation, life) is record
    assert observed == [(activation, life)]
    assert worker._store.reads == [
        (activation.entry.request_id, {"run_id": activation.generation.run_id, "generation": 1})
    ] * 2
    assert worker._prepare_composition_plan(activation, life) is record
    assert len(observed) == 1, "restart must use the admitted plan, not re-plan"


@pytest.mark.parametrize("channel,registered", [("wechat", False), ("desktop", True)])
def test_external_channels_and_existing_plan_skip_planner(channel, registered):
    def forbidden(*_args):
        raise AssertionError("unexpected model planning")

    record = object() if registered else None
    worker = _worker(forbidden, record)
    assert worker._prepare_composition_plan(_activation(channel), object()) is record


def test_disabled_mode_keeps_legacy_path_and_has_no_registration():
    worker = _worker(lambda _activation, _life: False)
    assert worker._prepare_composition_plan(_activation(), object()) is None


@pytest.mark.parametrize("result", [None, {}, "registered", 1])
def test_planner_cannot_supply_an_executable_claim_in_place_of_store_registration(result):
    worker = _worker(lambda _activation, _life: result)
    with pytest.raises(OrchestrationError, match="planner_result_invalid"):
        worker._prepare_composition_plan(_activation(), object())


def test_selected_planning_without_registration_fails_closed():
    worker = _worker(lambda _activation, _life: True)
    with pytest.raises(OrchestrationError, match="planner_registration_missing"):
        worker._prepare_composition_plan(_activation(), object())


def test_selected_planning_exception_is_not_legacy_fallback():
    def fail(*_args):
        raise RuntimeError("source changed during planning")

    worker = _worker(fail)
    with pytest.raises(RuntimeError, match="source changed"):
        worker._prepare_composition_plan(_activation(), object())
    assert len(worker._store.reads) == 1


def test_bypass_cannot_secretly_install_a_plan():
    def planner(*_args):
        worker._store.record = object()
        return False

    worker = _worker(planner)
    with pytest.raises(OrchestrationError, match="planner_bypass_registered"):
        worker._prepare_composition_plan(_activation(), object())


def test_process_prepares_before_parent_claim_ticket_and_backend_dispatch():
    source = Path(__file__).resolve().parents[1] / "src/total_gateway/orchestration.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GatewayOrchestrationWorker")
    process = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "process")

    def call_line(name):
        return min(n.lineno for n in ast.walk(process) if isinstance(n, ast.Call) and ast.unparse(n.func) == name)

    planning = call_line("self._prepare_composition_plan")
    assert call_line("self._acquire_life_snapshot") < planning
    assert planning < call_line("self._store.claim_effect")
    assert planning < call_line("self._authority.execution_signer.sign_execution")
    assert planning < call_line("self._omni_grants.seal_composition_continuation")
    assert planning < call_line("_EXECUTION_WATCHDOG_POOL.submit")


def _transport_case(tmp_path, *, include_registration=True, ack_updates=None, http_status=200):
    arguments = {"text": "fixture read task", "attachments": []}
    if include_registration:
        arguments["composition_registration_id"] = "registration_fixture"
    ticket, _manifest, _trust = signed_ticket(arguments)
    ack = {
        "schema": "tiangong.composition-parent-handoff.v1",
        "ok": True,
        "registration_id": "registration_fixture",
        "request_id": ticket.payload.request_id,
        "run_id": ticket.payload.run_id,
        "generation": ticket.payload.generation,
        "parent_ticket_id": ticket.payload.ticket_id,
        "stage_success": True,
        "task_completed": False,
        "reply_text": "Plan registered; Gateway must verify child results.",
        # A parent acknowledgment cannot export artifacts even if the backend
        # accidentally attaches a path.  Only verified child outputs may win.
        "attachments": [{"path": str(tmp_path / "unverified.txt")}],
    }
    ack.update(ack_updates or {})
    seen = []

    def request(_method, _path, payload, **kwargs):
        kwargs["before_request"](100)
        seen.append(payload)
        return http_status, ack, "a" * 64

    def forbidden(*_args, **_kwargs):
        raise AssertionError("typed handoff entered legacy run polling/recovery/output capture")

    transport = object.__new__(FrozenBackendCompatibilityTransport)
    transport._backend = SimpleNamespace(request=request)
    transport._gateway_url = "http://127.0.0.1:7184"
    transport._on_backend_start = None
    transport._prepare_life = lambda *_: {
        "life_id": "life_fixture", "context_envelope": {}, "context_hash": "a" * 64,
        "cycle_id": "cycle_fixture", "writer_epoch": 1,
    }
    transport._materialize_inputs = lambda *_: []
    transport._backend_terminal = forbidden
    transport._recover = forbidden
    transport._capture_outputs = forbidden
    wire = {"schema": "tiangong.backend.execute-ticket.v1", "ticket": ticket.model_dump(mode="json"), "arguments": arguments}
    return transport, wire, seen


def test_typed_handoff_is_only_parent_stage_success_and_forwards_signed_identity(tmp_path):
    transport, wire, seen = _transport_case(tmp_path)
    response = transport.execute(canonical_json_bytes(wire), timeout_seconds=2)
    assert response["execution_result"]["status"] == "SUCCEEDED"
    result = response["result_payload"]
    assert result["composition_parent_handoff"]["task_completed"] is False
    assert result["backend_terminal"]["reason_code"] == "composition.parent_handoff_acknowledged"
    assert result["life_terminal"]["task_completed"] is False
    assert result["artifacts"] == []
    assert len(seen) == 1
    for section in (seen[0], seen[0]["metadata"], seen[0]["conversation_context"]):
        assert section["composition_registration_id"] == "registration_fixture"


@pytest.mark.parametrize("updates", [
    {"request_id": "other"}, {"run_id": "other"}, {"generation": True},
    {"generation": 999}, {"parent_ticket_id": "other"},
    {"registration_id": "other"}, {"stage_success": False},
    {"task_completed": True}, {"schema": "unknown"}, {"reply_text": ""},
])
def test_mismatched_handoff_never_reports_parent_success_or_polls_legacy(tmp_path, updates):
    transport, wire, seen = _transport_case(tmp_path, ack_updates=updates)
    response = transport.execute(canonical_json_bytes(wire), timeout_seconds=2)
    assert response["execution_result"]["status"] != "SUCCEEDED"
    assert response["result_payload"]["error_code"] == "compat.composition.handoff_invalid"
    assert len(seen) == 1


def test_unrequested_handoff_cannot_switch_old_chain_to_composition(tmp_path):
    transport, wire, _seen = _transport_case(tmp_path, include_registration=False)
    response = transport.execute(canonical_json_bytes(wire), timeout_seconds=2)
    assert response["execution_result"]["status"] != "SUCCEEDED"
    assert response["result_payload"]["error_code"] == "compat.composition.handoff_unexpected"


def test_handoff_registration_cannot_be_added_after_ticket_signing(tmp_path):
    transport, wire, seen = _transport_case(tmp_path)
    wire["arguments"]["composition_registration_id"] = "attacker_registration"
    response = transport.execute(canonical_json_bytes(wire), timeout_seconds=2)
    assert response["execution_result"]["status"] != "SUCCEEDED"
    assert response["result_payload"]["error_code"] == "compat.execute_ticket.arguments_invalid"
    assert seen == []


def test_handoff_http_error_is_not_stage_success(tmp_path):
    transport, wire, _seen = _transport_case(tmp_path, http_status=500)
    response = transport.execute(canonical_json_bytes(wire), timeout_seconds=2)
    assert response["execution_result"]["status"] != "SUCCEEDED"


def _handoff_validator_case():
    ticket, _manifest, _trust = signed_ticket(
        {"composition_registration_id": "registration_fixture"}, channel="desktop",
    )
    payload = ticket.payload
    plan = SimpleNamespace(
        request_id=payload.request_id, run_id=payload.run_id,
        generation=payload.generation, registration_id="registration_fixture",
        executable_plan_id="executable_fixture",
    )
    continuation = SimpleNamespace(
        parent_ticket_id=payload.ticket_id,
        parent_ticket_sha256=canonical_sha256(ticket.model_dump(mode="json")),
        parent_effect_id=payload.effect_id,
    )
    effect = SimpleNamespace(state="SIDE_EFFECT_STARTED", claim=SimpleNamespace(claim_sha256=payload.claim_sha256))
    observed = []

    def validate_parent(**kwargs):
        observed.append(kwargs)
        return SimpleNamespace(ticket=ticket)

    worker = object.__new__(GatewayOrchestrationWorker)
    worker._store = SimpleNamespace(
        get_active_executable_composition_plan=lambda *_args, **_kwargs: SimpleNamespace(executable_plan=plan),
        get_composition_continuation_for_plan=lambda *_args, **_kwargs: continuation,
        get_effect=lambda _effect_id: effect,
    )
    worker._omni_grants = SimpleNamespace(_composition_parent=validate_parent)
    inputs = dict(
        request_id=plan.request_id, run_id=plan.run_id, generation=plan.generation,
        registration_id=plan.registration_id, parent_ticket_id=payload.ticket_id,
        now_ms=20_000,
    )
    return worker, inputs, continuation, effect, observed


def test_handoff_validator_requires_live_parent_authority_and_exact_continuation():
    worker, inputs, _continuation, _effect, observed = _handoff_validator_case()
    assert worker.validate_composition_parent_handoff(**inputs) is None
    assert len(observed) == 1
    assert observed[0]["parent_ticket_id"] == inputs["parent_ticket_id"]


@pytest.mark.parametrize("field,value", [
    ("request_id", "wrong"), ("run_id", "wrong"), ("generation", True),
    ("registration_id", "wrong"),
])
def test_handoff_validator_rejects_cross_request_scope(field, value):
    worker, inputs, _continuation, _effect, observed = _handoff_validator_case()
    inputs[field] = value
    with pytest.raises(OrchestrationError, match="handoff_scope_invalid"):
        worker.validate_composition_parent_handoff(**inputs)
    assert observed == []


@pytest.mark.parametrize("change", ["ticket", "digest", "effect", "unstarted", "claim"])
def test_handoff_validator_cannot_ack_before_dispatch_or_with_other_authority(change):
    worker, inputs, continuation, effect, _observed = _handoff_validator_case()
    if change == "ticket":
        continuation.parent_ticket_id = "other"
    elif change == "digest":
        continuation.parent_ticket_sha256 = "0" * 64
    elif change == "effect":
        continuation.parent_effect_id = "other"
    elif change == "unstarted":
        effect.state = "CLAIMED"
    else:
        effect.claim.claim_sha256 = "other"
    with pytest.raises(OrchestrationError, match="handoff_authority_invalid"):
        worker.validate_composition_parent_handoff(**inputs)


def test_handoff_validator_never_treats_missing_live_parent_as_acknowledgment():
    worker, inputs, _continuation, _effect, _observed = _handoff_validator_case()

    def expired(**_kwargs):
        raise RuntimeError("parent ticket authority expired")

    worker._omni_grants._composition_parent = expired
    with pytest.raises(RuntimeError, match="authority expired"):
        worker.validate_composition_parent_handoff(**inputs)
