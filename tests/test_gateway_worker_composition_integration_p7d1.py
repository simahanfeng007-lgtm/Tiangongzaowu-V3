"""P7D.1 integration guards for the one Gateway orchestration worker.

These tests deliberately cover the worker seam rather than duplicating the
coordinator's Effect/Fact tests.  The structural guard fixes the only safe
place for child execution, while the state-machine test proves that a child
failure cannot leave a delivery path open.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from contracts import InboundEnvelope, InboundScope, derive_inbound_scope_keys
from total_gateway.active_requests import ActiveRequestActivator
from total_gateway.composition_activation_adapter import (
    CompositionActivationAdapter,
    CompositionActivationAdapterError,
)
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.store import GatewayStateStore


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION_SOURCE = ROOT / "src" / "total_gateway" / "orchestration.py"
HASH_A = "a" * 64


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def _attribute_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _attribute_name(node.value)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _calls(method: ast.FunctionDef, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and _attribute_name(node.func) == name
    ]


def _constant_argument(call: ast.Call, index: int, value: object) -> bool:
    return (
        len(call.args) > index
        and isinstance(call.args[index], ast.Constant)
        and call.args[index].value == value
    )


def _envelope(message_ref: str) -> InboundEnvelope:
    scope = InboundScope(
        channel="wechat",
        tenant_id="tenant_001",
        link_account_id="wechat_001",
        conversation_ref="conversation_001",
        channel_message_ref=message_ref,
        sender_ref="sender_001",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id=f"inbound_{message_ref}",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=1_000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A,
        text="run the authorized composition",
    )


def test_unavailable_executor_rejects_before_calling_composition_issuer() -> None:
    tree = ast.parse(
        ORCHESTRATION_SOURCE.read_text(encoding="utf-8"),
        filename=str(ORCHESTRATION_SOURCE),
    )
    constructor = _method(tree, "GatewayOrchestrationWorker", "__init__")
    adapter_calls = _calls(constructor, "CompositionActivationAdapter")
    assert len(adapter_calls) == 1
    availability = next(
        item.value
        for item in adapter_calls[0].keywords
        if item.arg == "execution_available"
    )
    assert ast.unparse(availability) == "composition_execution_available"
    binder_calls = _calls(
        constructor,
        "binder",
    )
    assert len(binder_calls) == 1
    assert ast.unparse(binder_calls[0].args[0]) == (
        "self._authorize_composition_handler_entry"
    )

    calls: list[dict[str, object]] = []

    class Issuer:
        def issue_composition_step(self, **kwargs):  # pragma: no cover - forbidden
            calls.append(kwargs)
            return {"status": "OK"}

    adapter = CompositionActivationAdapter(Issuer(), execution_available=False)

    with pytest.raises(CompositionActivationAdapterError) as caught:
        adapter.authorize_step(
            parent_ticket_id="ticket_parent",
            registration_id="registration_001",
            step_id="step.01",
            now_ms=1_500,
        )

    assert caught.value.code == "composition.authorization.execution_unavailable"
    assert calls == []


def test_process_dispatches_composition_only_at_parent_durable_success_boundary() -> None:
    tree = ast.parse(
        ORCHESTRATION_SOURCE.read_text(encoding="utf-8"),
        filename=str(ORCHESTRATION_SOURCE),
    )
    process = _method(tree, "GatewayOrchestrationWorker", "process")
    continuation = _method(
        tree,
        "GatewayOrchestrationWorker",
        "_continue_after_parent_success",
    )
    durable_adapter = _method(
        tree,
        "GatewayOrchestrationWorker",
        "_continue_durable_composition_parent",
    )
    run_loop = _method(tree, "GatewayOrchestrationWorker", "_run")

    # There is exactly one composition scheduling entry point, scoped to the
    # activation currently owned by process(); the idle loop is not a second
    # scheduler over globally recoverable receipts.
    dispatches = _calls(
        continuation,
        "self._dispatch_next_composition_step",
    )
    assert len(dispatches) == 1
    success_tail_calls = _calls(process, "self._continue_after_parent_success")
    assert len(success_tail_calls) == 1
    assert len(
        _calls(durable_adapter, "self._continue_after_parent_success")
    ) == 1
    assert _calls(process, "self._dispatch_next_composition_step") == []
    assert _calls(run_loop, "self._dispatch_next_composition_step") == []
    dispatch = dispatches[0]
    assert {item.arg for item in dispatch.keywords} >= {
        "now_ms",
        "request_id",
        "run_id",
        "generation",
    }

    parent_facts = _calls(process, "self._facts.record_execution")
    assert len(parent_facts) == 1
    parent_unregisters = _calls(process, "self._omni_grants.unregister")
    assert len(parent_unregisters) == 1
    parent_effect_commits = [
        call
        for call in _calls(process, "self._store.complete_effect")
        if call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "effect_result"
    ]
    assert len(parent_effect_commits) == 1

    aggregate_successes = [
        call
        for call in _calls(continuation, "advance_tail")
        if _constant_argument(call, 0, "execution")
        and len(call.args) > 1
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "execution_entity"
        and _constant_argument(call, 2, "SUCCEEDED")
    ]
    assert len(aggregate_successes) == 1

    result_payload_assignments = [
        node
        for node in ast.walk(continuation)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "result_payload"
            for target in node.targets
        )
    ]
    assert len(result_payload_assignments) == 1
    reply_assignments = [
        node
        for node in ast.walk(continuation)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "reply"
            for target in node.targets
        )
    ]
    assert reply_assignments
    first_reply_assignment = min(reply_assignments, key=lambda node: node.lineno)
    life_commits = _calls(continuation, "self._commit_life_execution")
    delivery_builds = _calls(continuation, "build_delivery_outbox_payload")
    assert len(life_commits) == 1
    assert len(delivery_builds) == 1

    assert (
        parent_unregisters[0].lineno
        < parent_facts[0].lineno
        < parent_effect_commits[0].lineno
        < success_tail_calls[0].lineno
    )
    assert (
        dispatch.lineno
        < aggregate_successes[0].lineno
        < result_payload_assignments[0].lineno
        < first_reply_assignment.lineno
        < life_commits[0].lineno
        < delivery_builds[0].lineno
    )


@pytest.mark.parametrize(
    ("ambiguous", "expected_execution_state"),
    ((False, "FAILED_FINAL"), (True, "RECONCILE_REQUIRED")),
)
def test_composition_failure_terminalizes_request_and_cancels_delivery_without_outbox(
    tmp_path: Path,
    ambiguous: bool,
    expected_execution_state: str,
) -> None:
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        registration = store.register_request(
            _envelope(f"message_{int(ambiguous)}"),
            ingress_sha256=HASH_A,
            created_at_ms=1_100,
        )
        activator = ActiveRequestActivator(
            store,
            gateway_epoch=7,
            owner_instance_id="gateway-instance-001",
            lease_duration_ms=10_000,
        )
        activation = activator.claim(
            registration.entry.request_id,
            registration.entry.session_scope_hash,
            now_ms=1_200,
        )

        worker = object.__new__(GatewayOrchestrationWorker)
        worker._store = store
        execution_entity = "execution-" + activation.generation.run_id
        delivery_entity = "delivery-" + activation.generation.run_id
        worker._initialize("execution", execution_entity, activation, 1_300)
        worker._initialize("delivery", delivery_entity, activation, 1_300)
        worker._advance(
            "request", activation.entry.request_id, "PLANNING", now_ms=1_301
        )
        worker._advance(
            "request", activation.entry.request_id, "EXECUTING", now_ms=1_302
        )
        worker._advance("execution", execution_entity, "PLANNED", now_ms=1_301)
        worker._advance(
            "execution", execution_entity, "TICKET_ISSUED", now_ms=1_302
        )
        worker._advance("execution", execution_entity, "CLAIMED", now_ms=1_303)
        worker._advance("execution", execution_entity, "RUNNING", now_ms=1_304)

        assert store.list_outbox_for_request(
            activation.entry.request_id,
            run_id=activation.generation.run_id,
            generation=activation.generation.generation,
        ) == ()

        worker._terminalize_composition_failure(
            activation,
            execution_entity=execution_entity,
            delivery_entity=delivery_entity,
            code=(
                "composition.execution.result_unknown"
                if ambiguous
                else "composition.execution.failed"
            ),
            ambiguous=ambiguous,
            observed_at_ms=2_000,
        )

        assert store.get_snapshot("execution", execution_entity).state == (
            expected_execution_state
        )
        assert store.get_snapshot("request", activation.entry.request_id).state == (
            "FAILED"
        )
        assert store.get_snapshot("delivery", delivery_entity).state == "CANCELLED"
        assert store.get_generation(activation.entry.request_id).status == "RELEASED"
        assert store.list_outbox_for_request(
            activation.entry.request_id,
            run_id=activation.generation.run_id,
            generation=activation.generation.generation,
        ) == ()
    finally:
        store.close()
