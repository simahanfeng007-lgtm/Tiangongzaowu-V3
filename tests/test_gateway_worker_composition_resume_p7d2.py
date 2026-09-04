"""Durable parent restart cutpoints for the P7D.2 Gateway worker."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import (
    ExecutionResult,
    InboundEnvelope,
    InboundScope,
    canonical_sha256,
    derive_effect_identity,
    derive_inbound_scope_keys,
)
import total_gateway.backend_client as backend_client_module
from total_gateway.active_requests import ActiveRequestActivator
from total_gateway.backend_client import BACKEND_API_CONTRACT, BackendExecutionResponse
from total_gateway.effects import EffectClaim
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError
from total_gateway.store import GatewayStateStore
from tests.test_backend_client import signed_ticket


HASH_A = "a" * 64


class _StoreHarness:
    """Keep all writes/Effect reads real; only inject the plan continuation."""

    def __init__(self, store: GatewayStateStore, plan_record, continuation) -> None:
        self._store = store
        self.plan_record = plan_record
        self.continuation = continuation

    def __getattr__(self, name):
        return getattr(self._store, name)

    def get_composition_continuation_for_plan(self, executable_plan_id, **kwargs):
        assert executable_plan_id == self.plan_record.executable_plan.executable_plan_id
        del kwargs
        return self.continuation

    def get_active_executable_composition_plan(self, registration_id, *, now_ms):
        assert registration_id == self.plan_record.executable_plan.registration_id
        assert now_ms >= 0
        return self.plan_record

    def get_executable_composition_plan_for_request(
        self,
        request_id,
        *,
        run_id,
        generation,
    ):
        plan = self.plan_record.executable_plan
        assert (request_id, run_id, generation) == (
            plan.request_id,
            plan.run_id,
            plan.generation,
        )
        return self.plan_record


class _ForbiddenHandlerPool:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, *_args, **_kwargs):  # pragma: no cover - forbidden
        self.submit_calls += 1
        raise AssertionError("restart replayed the parent handler")


def _envelope(message_ref: str) -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_resume",
        link_account_id="desktop_resume",
        conversation_ref="session_resume",
        channel_message_ref=message_ref,
        sender_ref="sender_resume",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_" + message_ref,
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
        text="resume the persisted composition",
    )


def _runtime(tmp_path: Path, *, message_ref: str):
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    objects = ContentAddressedObjectStore.open(tmp_path / "objects", now_ms=900)
    facts = FactLedger.open(tmp_path / "facts.sqlite3", objects, now_ms=900)
    registration = store.register_request(
        _envelope(message_ref),
        ingress_sha256=HASH_A,
        created_at_ms=1_100,
    )
    activation = ActiveRequestActivator(
        store,
        gateway_epoch=7,
        owner_instance_id="gateway-resume-test",
        lease_duration_ms=100_000,
    ).claim(
        registration.entry.request_id,
        registration.entry.session_scope_hash,
        now_ms=1_200,
    )
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._store = store
    worker._objects = objects
    worker._facts = facts
    execution_entity = "execution-" + activation.generation.run_id
    delivery_entity = "delivery-" + activation.generation.run_id
    worker._initialize("execution", execution_entity, activation, 1_250)
    worker._initialize("delivery", delivery_entity, activation, 1_250)
    worker._advance("request", activation.entry.request_id, "PLANNING", now_ms=1_251)
    worker._advance("execution", execution_entity, "PLANNED", now_ms=1_251)
    worker._advance("execution", execution_entity, "TICKET_ISSUED", now_ms=1_252)
    worker._advance("execution", execution_entity, "CLAIMED", now_ms=1_253)
    worker._advance("request", activation.entry.request_id, "EXECUTING", now_ms=1_253)
    plan = SimpleNamespace(
        registration_id="registration_resume",
        executable_plan_id="executable_plan_resume",
        executable_plan_sha256="b" * 64,
        request_id=activation.entry.request_id,
        run_id=activation.generation.run_id,
        generation=activation.generation.generation,
        principal_scope_hash=activation.envelope.principal_scope_hash,
        workspace=SimpleNamespace(
            workspace_id="workspace_resume",
            workspace_scope_sha256="c" * 64,
        ),
    )
    plan_record = SimpleNamespace(executable_plan=plan)
    return SimpleNamespace(
        store=store,
        objects=objects,
        facts=facts,
        worker=worker,
        activation=activation,
        execution_entity=execution_entity,
        delivery_entity=delivery_entity,
        plan_record=plan_record,
    )


def _claim_parent(runtime):
    generation = runtime.activation.generation
    intent = canonical_sha256({"cutpoint": "parent", "run": generation.run_id})
    identity = derive_effect_identity(
        request_id=runtime.activation.entry.request_id,
        run_id=generation.run_id,
        run_sequence=generation.run_sequence,
        generation=generation.generation,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=intent,
    )
    claim = EffectClaim(
        effect_id=identity.effect_id,
        request_id=runtime.activation.entry.request_id,
        run_id=generation.run_id,
        run_sequence=generation.run_sequence,
        generation=generation.generation,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=intent,
        owner_component_id="tiangong-backend",
        claimed_at_ms=1_260,
        claim_sha256="0" * 64,
    ).with_computed_sha256()
    runtime.store.claim_effect(claim)
    return claim


def _record_parent_fact(runtime, claim):
    envelope = runtime.activation.envelope
    payload = {"reply_text": "parent succeeded"}
    ticket, _manifest, _trust = signed_ticket(
        {},
        ticket_id="ticket_parent_resume",
        request_id=claim.request_id,
        run_id=claim.run_id,
        generation=claim.generation,
        effect_id=claim.effect_id,
        claim_sha256=claim.claim_sha256,
        channel="desktop",
        tenant_id=envelope.tenant_id,
        link_account_id=envelope.link_account_id,
        conversation_scope_hash=envelope.conversation_scope_hash,
        principal_scope_hash=envelope.principal_scope_hash,
        action_id="gateway.model.run",
        action_version="1.0.0",
        workspace_id="workspace_resume",
        input_objects=(),
        output_root_id="output_resume",
        artifact_intent_id="artifact_resume",
        max_output_bytes=1_000_000,
        allowed_side_effects=("none",),
        risk_class="A0",
    )
    result = ExecutionResult(
        result_id="execution_result_parent_resume",
        ticket_id=ticket.payload.ticket_id,
        request_id=claim.request_id,
        run_id=claim.run_id,
        generation=claim.generation,
        effect_id=claim.effect_id,
        action_id="gateway.model.run",
        action_version="1.0.0",
        status="SUCCEEDED",
        attempt=1,
        started_at_ms=1_300,
        finished_at_ms=1_310,
        side_effect_started=True,
        result_payload_sha256=canonical_sha256(payload),
        receipt_sha256="d" * 64,
        output_object_refs=(),
        fact_ids=("fact_parent_resume",),
    )
    response_sha256 = canonical_sha256(
        {
            "ok": True,
            "api_contract": BACKEND_API_CONTRACT,
            "execution_result": result.model_dump(mode="json"),
            "result_payload": payload,
        }
    )
    response = BackendExecutionResponse(
        result=result,
        result_payload=payload,
        response_sha256=response_sha256,
        ticket=ticket,
        _verification_marker=backend_client_module._BACKEND_VERIFIED_RESPONSE,
    )
    batch = runtime.facts.record_execution(response, observed_at_ms=1_310).record
    return response, batch


def _continuation(runtime, claim, ticket_id: str):
    envelope = runtime.activation.envelope
    plan = runtime.plan_record.executable_plan
    return SimpleNamespace(
        registration_id=plan.registration_id,
        executable_plan_id=plan.executable_plan_id,
        executable_plan_sha256=plan.executable_plan_sha256,
        request_id=claim.request_id,
        run_id=claim.run_id,
        generation=claim.generation,
        principal_scope_hash=envelope.principal_scope_hash,
        workspace_id=plan.workspace.workspace_id,
        workspace_scope_sha256=plan.workspace.workspace_scope_sha256,
        parent_effect_id=claim.effect_id,
        parent_effect_claim_sha256=claim.claim_sha256,
        parent_ticket_id=ticket_id,
        issuance_context={
            "channel": envelope.channel,
            "tenant_id": envelope.tenant_id,
            "link_account_id": envelope.link_account_id,
            "conversation_scope_hash": envelope.conversation_scope_hash,
            "session_id": envelope.conversation_ref,
            "artifact_intent_id": "artifact_resume",
            "life_id": "life_resume",
            "life_evidence_ref": "lev_" + "e" * 64,
            "output_root_id": "output_resume",
            "max_output_bytes": 1_000_000,
        },
    )


def _close(runtime) -> None:
    runtime.facts.close()
    runtime.objects.close()
    runtime.store.close()


def test_preseal_claimed_parent_without_fact_is_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, message_ref="preseal")
    try:
        claim = _claim_parent(runtime)
        pool = _ForbiddenHandlerPool()
        monkeypatch.setattr(
            "total_gateway.orchestration._EXECUTION_WATCHDOG_POOL",
            pool,
        )
        runtime.worker._store = _StoreHarness(
            runtime.store,
            runtime.plan_record,
            None,
        )

        with pytest.raises(OrchestrationError) as caught:
            runtime.worker._durable_composition_parent_resume(
                runtime.activation,
                runtime.plan_record,
                now_ms=1_400,
            )

        assert caught.value.code == (
            "composition.execution.parent_proven_not_applied_after_restart"
        )
        assert caught.value.ambiguous is False
        assert runtime.store.get_effect(claim.effect_id).state == "FAILED_FINAL"
        assert runtime.store.get_snapshot(
            "execution", runtime.execution_entity
        ).state == "FAILED_FINAL"
        assert runtime.store.get_snapshot(
            "delivery", runtime.delivery_entity
        ).state == "CANCELLED"
        assert runtime.store.get_snapshot(
            "request", runtime.activation.entry.request_id
        ).state == "FAILED"
        assert pool.submit_calls == 0
    finally:
        _close(runtime)


def test_started_composition_parent_is_protected_from_generic_startup_recovery(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, message_ref="startup-protection")
    try:
        claim = _claim_parent(runtime)
        runtime.store.mark_effect_started(claim.effect_id, started_at_ms=1_305)
        runtime.worker._store = _StoreHarness(
            runtime.store,
            runtime.plan_record,
            None,
        )

        protected = runtime.worker._composition_parent_started_effect_ids()
        assert protected == (claim.effect_id,)
        assert runtime.store.recover_started_effects(
            now_ms=1_400,
            exclude_effect_ids=protected,
        ) == ()
        head = runtime.store.get_effect(claim.effect_id)
        assert head is not None
        assert head.state == "SIDE_EFFECT_STARTED"
        assert head.result is None
    finally:
        _close(runtime)


def test_sealed_claimed_parent_without_fact_is_proven_not_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, message_ref="sealed-before-dispatch")
    try:
        claim = _claim_parent(runtime)
        continuation = _continuation(
            runtime,
            claim,
            "ticket_parent_resume",
        )
        pool = _ForbiddenHandlerPool()
        monkeypatch.setattr(
            "total_gateway.orchestration._EXECUTION_WATCHDOG_POOL",
            pool,
        )
        runtime.worker._store = _StoreHarness(
            runtime.store,
            runtime.plan_record,
            continuation,
        )

        with pytest.raises(OrchestrationError) as caught:
            runtime.worker._durable_composition_parent_resume(
                runtime.activation,
                runtime.plan_record,
                now_ms=1_400,
            )

        assert caught.value.code == (
            "composition.execution.parent_proven_not_applied_after_restart"
        )
        assert caught.value.ambiguous is False
        assert runtime.store.get_effect(claim.effect_id).state == "FAILED_FINAL"
        assert runtime.store.get_snapshot(
            "execution", runtime.execution_entity
        ).state == "FAILED_FINAL"
        assert runtime.store.get_snapshot(
            "delivery", runtime.delivery_entity
        ).state == "CANCELLED"
        assert runtime.store.get_snapshot(
            "request", runtime.activation.entry.request_id
        ).state == "FAILED"
        assert pool.submit_calls == 0
    finally:
        _close(runtime)


def test_claimed_parent_fact_is_promoted_and_reconstructed_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, message_ref="fact-cutpoint")
    try:
        claim = _claim_parent(runtime)
        response, batch = _record_parent_fact(runtime, claim)
        continuation = _continuation(
            runtime,
            claim,
            response.result.ticket_id,
        )
        pool = _ForbiddenHandlerPool()
        monkeypatch.setattr(
            "total_gateway.orchestration._EXECUTION_WATCHDOG_POOL",
            pool,
        )
        runtime.worker._store = _StoreHarness(
            runtime.store,
            runtime.plan_record,
            continuation,
        )

        recovered = runtime.worker._durable_composition_parent_resume(
            runtime.activation,
            runtime.plan_record,
            now_ms=1_400,
        )

        assert recovered is not None
        assert recovered.parent_effect_id == claim.effect_id
        assert recovered.result == response.result
        assert recovered.result_payload == response.result_payload
        assert recovered.response_sha256 == response.response_sha256
        head = runtime.store.get_effect(claim.effect_id)
        assert head is not None and head.state == "SUCCEEDED"
        assert head.result is not None
        assert head.result.result_object_id == batch.result_payload_object_id
        assert runtime.objects.read_bytes(batch.result_payload_object_id) == (
            b'{"reply_text":"parent succeeded"}'
        )
        assert pool.submit_calls == 0
    finally:
        _close(runtime)


def test_succeeded_parent_uses_the_single_shared_success_tail(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, message_ref="success-tail")
    try:
        claim = _claim_parent(runtime)
        response, _batch = _record_parent_fact(runtime, claim)
        continuation = _continuation(
            runtime,
            claim,
            response.result.ticket_id,
        )
        runtime.worker._store = _StoreHarness(
            runtime.store,
            runtime.plan_record,
            continuation,
        )
        parent = runtime.worker._durable_composition_parent_resume(
            runtime.activation,
            runtime.plan_record,
            now_ms=1_400,
        )
        assert parent is not None
        captured = []
        runtime.worker._continue_after_parent_success = (
            lambda **kwargs: captured.append(kwargs)
        )

        runtime.worker._continue_durable_composition_parent(
            runtime.activation,
            runtime.plan_record,
            parent,
        )

        assert len(captured) == 1
        assert captured[0]["parent_effect_id"] == claim.effect_id
        assert captured[0]["response"] is parent
        assert captured[0]["composition_plan_record"] is runtime.plan_record
    finally:
        _close(runtime)
