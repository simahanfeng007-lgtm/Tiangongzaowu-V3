from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts import (
    DeliveryTicket,
    DeliveryTicketHeader,
    InboundEnvelope,
    InboundScope,
    OutboundPart,
    OutboundPlan,
    OutboundScope,
    TransitionEvent,
    canonical_json_bytes,
    canonical_sha256,
    derive_delivery_identity,
    derive_effect_identity,
    derive_inbound_scope_keys,
    derive_outbound_scope_keys,
    new_state_snapshot,
    text_sha256,
)
from total_gateway.active_requests import ActiveRequestActivator
from total_gateway.delivery_outbox import (
    GatewayDeliveryOutboxWorker,
    build_delivery_outbox_payload,
)
from total_gateway.completion_gate import CompletionDecision
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.outbox import OutboxIntent, derive_outbox_id
from total_gateway.store import GatewayStateStore
from tests.test_delivery_contracts import accepted_receipt, component_manifest


HASH_A = "a" * 64
HASH_B = "b" * 64


def completed_decision(plan: OutboundPlan) -> CompletionDecision:
    return CompletionDecision(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        outcome="COMPLETED",
        reason_code="completion.requirements_satisfied",
        text_ready=True,
        execution_ready=True,
        artifacts_ready=True,
        delivery_ready=True,
        can_transition_request_completed=True,
        can_claim_platform_delivered=False,
        needs_reconciliation=False,
        execution_effect_states=(),
        artifact_revision_states=(),
        delivery_parts=(),
        supporting_fact_ids=(),
        decision_sha256=HASH_B,
    ).with_computed_sha256()


def reconciliation_decision(plan: OutboundPlan) -> CompletionDecision:
    return CompletionDecision(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        outcome="RECONCILE_REQUIRED",
        reason_code="completion.reconciliation_required",
        text_ready=True,
        execution_ready=True,
        artifacts_ready=True,
        delivery_ready=False,
        can_transition_request_completed=False,
        can_claim_platform_delivered=False,
        needs_reconciliation=True,
        execution_effect_states=(),
        artifact_revision_states=(),
        delivery_parts=(),
        supporting_fact_ids=(),
        decision_sha256=HASH_B,
    ).with_computed_sha256()


class _Signer:
    def sign_delivery(self, payload):
        return DeliveryTicket(
            header=DeliveryTicketHeader(kid="delivery-test-key"),
            payload=payload,
            signature="B" * 86,
        )


class _Authority:
    delivery_signer = _Signer()

    @staticmethod
    def delivery_trust_bundle(*, gateway_epoch: int, now_ms: int):
        return {"gateway_epoch": gateway_epoch, "now_ms": now_ms}


class _Communication:
    def __init__(self) -> None:
        self.install_calls = 0
        self.dispatch_calls = 0
        self.last_ticket = None

    def install_delivery_authority(self, _trust, _components) -> None:
        self.install_calls += 1

    def dispatch_delivery(self, ticket, _plan):
        self.dispatch_calls += 1
        self.last_ticket = ticket
        return accepted_receipt(ticket=ticket)


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="wechat",
        tenant_id="tenant_001",
        link_account_id="wechat_001",
        conversation_ref="conversation_001",
        channel_message_ref="message_001",
        sender_ref="sender_001",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_001",
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
        text="hello",
    )


class DeliveryOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=900)
        self.objects = ContentAddressedObjectStore.open(root / "objects", now_ms=900)
        registration = self.store.register_request(
            inbound(),
            ingress_sha256=HASH_A,
            created_at_ms=1_100,
        )
        self.activation = ActiveRequestActivator(
            self.store,
            gateway_epoch=7,
            owner_instance_id="gateway-instance-001",
            lease_duration_ms=30_000,
        ).claim(
            registration.entry.request_id,
            registration.entry.session_scope_hash,
            now_ms=1_200,
        )
        self.components = component_manifest()
        self.communication = _Communication()
        self.execution_effect_id, self.plan, self.outbox = self._seed_delivery()
        self.worker = GatewayDeliveryOutboxWorker(
            store=self.store,
            objects=self.objects,
            facts=object(),
            authority=_Authority(),
            component_manifest=self.components,
            communication=self.communication,
            gateway_epoch=7,
            worker_id="gateway-instance-001",
            advance=self.advance,
        )

    def tearDown(self) -> None:
        self.objects.close()
        self.store.close()
        self.temporary.cleanup()

    def advance(
        self,
        machine: str,
        entity_id: str,
        to_state: str,
        *,
        now_ms: int,
        fact_id: str | None = None,
        evidence_sha256: str | None = None,
        outbox: tuple[OutboxIntent, ...] = (),
    ):
        snapshot = self.store.get_snapshot(machine, entity_id)
        self.assertIsNotNone(snapshot)
        if snapshot.state == to_state:
            return snapshot
        event = TransitionEvent(
            event_id="event-" + canonical_sha256(
                {
                    "entity": entity_id,
                    "revision": snapshot.revision,
                    "state": to_state,
                }
            ),
            event_type=f"test.{machine}.{to_state.lower()}",
            source_component_id="tiangong-total-gateway",
            machine=machine,
            entity_id=entity_id,
            request_id=snapshot.request_id,
            run_id=snapshot.run_id,
            generation=snapshot.generation,
            expected_revision=snapshot.revision,
            to_state=to_state,
            occurred_at_ms=max(now_ms, snapshot.updated_at_ms),
            fact_id=fact_id,
            evidence_sha256=evidence_sha256,
            side_effect_started=to_state in {"RUNNING", "SENDING", "AMBIGUOUS"},
            event_sha256="0" * 64,
        ).with_computed_event_sha256()
        result = self.store.apply_event_with_outbox(event, outbox, recorded_at_ms=event.occurred_at_ms)
        self.assertTrue(result.decision.accepted, result.decision.reason_code)
        return result.decision.current

    def _seed_delivery(self):
        request_id = self.activation.entry.request_id
        run_id = self.activation.generation.run_id
        generation = self.activation.generation.generation
        execution_entity = "execution-" + run_id
        delivery_entity = "delivery-" + run_id
        for machine, entity in (("execution", execution_entity), ("delivery", delivery_entity)):
            self.store.initialize_snapshot(
                new_state_snapshot(
                    machine,
                    entity_id=entity,
                    request_id=request_id,
                    run_id=run_id,
                    generation=generation,
                    created_at_ms=1_300,
                )
            )
        self.advance("request", request_id, "PLANNING", now_ms=1_400)
        self.advance("request", request_id, "EXECUTING", now_ms=1_500)
        self.advance("execution", execution_entity, "PLANNED", now_ms=1_500)
        self.advance("execution", execution_entity, "TICKET_ISSUED", now_ms=1_600)
        self.advance("execution", execution_entity, "CLAIMED", now_ms=1_700)
        execution = derive_effect_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=1,
            generation=generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=HASH_B,
        )
        claim = EffectClaim(
            effect_id=execution.effect_id,
            request_id=request_id,
            run_id=run_id,
            run_sequence=1,
            generation=generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=HASH_B,
            owner_component_id="tiangong-backend",
            claimed_at_ms=1_700,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(execution.effect_id, started_at_ms=1_800)
        self.advance("execution", execution_entity, "RUNNING", now_ms=1_800)
        result = EffectResult(
            result_id="execution-result-001",
            effect_id=execution.effect_id,
            status="SUCCEEDED",
            fact_id="execution-fact-001",
            evidence_sha256=HASH_A,
            observed_at_ms=1_900,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.complete_effect(result)
        self.advance(
            "execution",
            execution_entity,
            "SUCCEEDED",
            now_ms=1_900,
            fact_id=result.fact_id,
            evidence_sha256=result.evidence_sha256,
        )
        scope = OutboundScope(
            channel="wechat",
            tenant_id="tenant_001",
            link_account_id="wechat_001",
            conversation_ref="conversation_001",
            recipient_ref="sender_001",
            reply_to_message_ref="message_001",
        )
        scope_keys = derive_outbound_scope_keys(scope)
        payload_manifest = canonical_sha256({"text": text_sha256("reply")})
        delivery = derive_delivery_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=1,
            generation=generation,
            recipient_scope_hash=scope_keys.recipient_scope_hash,
            reply_to_message_ref=scope.reply_to_message_ref,
            payload_manifest_sha256=payload_manifest,
        )
        delivery_effect = derive_effect_identity(
            request_id=request_id,
            run_id=run_id,
            run_sequence=1,
            generation=generation,
            effect_kind="delivery",
            ordinal=0,
            intent_sha256=payload_manifest,
        )
        plan = OutboundPlan(
            outbound_plan_id="outbound-plan-001",
            delivery_id=delivery.delivery_id,
            effect_id=delivery_effect.effect_id,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            channel="wechat",
            tenant_id=scope.tenant_id,
            link_account_id=scope.link_account_id,
            conversation_ref=scope.conversation_ref,
            conversation_scope_hash=scope_keys.conversation_scope_hash,
            recipient_scope_hash=scope_keys.recipient_scope_hash,
            reply_to_message_ref=scope.reply_to_message_ref,
            channel_policy_hash=HASH_A,
            created_at_ms=2_000,
            parts=(
                OutboundPart(
                    part_id="part-text-001",
                    index=0,
                    kind="text",
                    text="reply",
                    text_sha256=text_sha256("reply"),
                ),
            ),
            plan_sha256="0" * 64,
        ).with_computed_plan_sha256()
        assembly = build_delivery_outbox_payload(
            plan,
            life_id="life_delivery_test",
            session_scope_hash=self.activation.entry.session_scope_hash,
            execution_effect_id=execution.effect_id,
        )
        reference = self.objects.put_bytes(
            canonical_json_bytes(assembly.model_dump(mode="json")),
            kind="payload",
            tenant_id=plan.tenant_id,
            link_account_id=plan.link_account_id,
            conversation_scope_hash=plan.conversation_scope_hash,
            created_at_ms=2_000,
        ).reference
        outgoing = OutboxIntent(
            outbox_id=derive_outbox_id(
                delivery_effect.effect_id,
                "tiangong-communication-service",
                reference.sha256,
            ),
            effect_id=delivery_effect.effect_id,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            destination_component_id="tiangong-communication-service",
            intent_kind="DELIVERY",
            payload_object_id=reference.object_id,
            payload_sha256=reference.sha256,
            created_at_ms=2_000,
            intent_sha256="0" * 64,
        ).with_computed_sha256()
        self.advance("delivery", delivery_entity, "PLANNED", now_ms=2_000, outbox=(outgoing,))
        self.advance("request", request_id, "DELIVERING", now_ms=2_000)
        return execution.effect_id, plan, outgoing

    def test_success_receipt_is_persisted_before_request_finalization(self) -> None:
        decision = completed_decision(self.plan)
        with patch("total_gateway.delivery_outbox.CompletionGate") as gate:
            gate.return_value.evaluate.return_value = decision
            self.assertTrue(self.worker.dispatch_next(now_ms=2_100))
        record = self.store.get_outbox(self.outbox.outbox_id)
        boundary = self.store.get_outbox_dispatch_boundary(self.outbox.outbox_id)
        self.assertEqual(record.state, "ACKED")
        self.assertIsNotNone(boundary.result_object_id)
        self.assertIsNotNone(boundary.finalized_at_ms)
        self.assertEqual(self.communication.dispatch_calls, 1)
        self.assertEqual(
            self.store.get_snapshot("request", self.activation.entry.request_id).state,
            "COMPLETED",
        )
        self.assertEqual(
            self.store.get_generation(self.activation.entry.request_id).status,
            "RELEASED",
        )
        decisions = self.store.list_completion_decisions(
            self.plan.request_id,
            run_id=self.plan.run_id,
            generation=self.plan.generation,
        )
        self.assertEqual(len(decisions), 1)
        terminal = self.store.get_terminal_request_capsule(
            self.plan.request_id,
            run_id=self.plan.run_id,
            generation=self.plan.generation,
        )
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal.capsule.final_result, "reply")
        self.assertTrue(self.store.health_check(now_ms=24_000, full=True).healthy)

    def test_pending_outbox_resumes_under_new_epoch_without_duplicate_send(self) -> None:
        recovered = ActiveRequestActivator(
            self.store,
            gateway_epoch=8,
            owner_instance_id="gateway-instance-002",
            lease_duration_ms=30_000,
        ).recover_next(now_ms=31_200)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.generation.run_id, self.plan.run_id)
        self.assertEqual(recovered.generation.generation, self.plan.generation)
        communication = _Communication()
        worker = GatewayDeliveryOutboxWorker(
            store=self.store,
            objects=self.objects,
            facts=object(),
            authority=_Authority(),
            component_manifest=self.components,
            communication=communication,
            gateway_epoch=8,
            worker_id="gateway-instance-002",
            advance=self.advance,
        )
        decision = completed_decision(self.plan)
        with patch("total_gateway.delivery_outbox.CompletionGate") as gate:
            gate.return_value.evaluate.return_value = decision
            self.assertTrue(worker.dispatch_next(now_ms=31_300))
        self.assertEqual(communication.dispatch_calls, 1)
        self.assertEqual(communication.last_ticket.payload.gateway_epoch, 8)
        self.assertEqual(self.store.get_outbox(self.outbox.outbox_id).state, "ACKED")
        self.assertEqual(
            self.store.get_generation(self.activation.entry.request_id).status,
            "RELEASED",
        )
        self.assertTrue(self.store.health_check(now_ms=31_400, full=True).healthy)

    def test_unhandled_error_cannot_cancel_a_committed_delivery_outbox(self) -> None:
        orchestrator = object.__new__(GatewayOrchestrationWorker)
        orchestrator._store = self.store
        orchestrator._advance = self.advance
        orchestrator._finalize_unhandled(self.activation, RuntimeError("late assembly failure"))
        self.assertEqual(
            self.store.get_snapshot("request", self.activation.entry.request_id).state,
            "DELIVERING",
        )
        self.assertEqual(self.store.get_outbox(self.outbox.outbox_id).state, "PENDING")
        self.assertEqual(
            self.store.get_generation(self.activation.entry.request_id).status,
            "ACTIVE",
        )

    def test_orphaned_crossed_boundary_never_resends_and_enters_reconciliation(self) -> None:
        claimed = self.store.claim_outbox(
            self.outbox.outbox_id,
            worker_id="gateway-instance-001",
            now_ms=2_100,
            lease_ms=1_000,
        )
        self.advance("delivery", "delivery-" + self.plan.run_id, "TICKET_ISSUED", now_ms=2_100)
        self.advance("delivery", "delivery-" + self.plan.run_id, "SENDING", now_ms=2_100)
        ticket_reference = self.objects.put_bytes(
            b"synthetic-ticket",
            kind="payload",
            tenant_id=self.plan.tenant_id,
            link_account_id=self.plan.link_account_id,
            conversation_scope_hash=self.plan.conversation_scope_hash,
            created_at_ms=2_100,
        ).reference
        self.store.mark_outbox_dispatch_started(
            claimed.intent.outbox_id,
            worker_id="gateway-instance-001",
            gateway_epoch=7,
            ticket_object_id=ticket_reference.object_id,
            ticket_sha256=ticket_reference.sha256,
            started_at_ms=2_100,
        )
        with patch("total_gateway.delivery_outbox.CompletionGate") as gate:
            gate.return_value.evaluate.return_value = reconciliation_decision(self.plan)
            self.assertTrue(self.worker.dispatch_next(now_ms=3_100))
        self.assertEqual(self.communication.dispatch_calls, 0)
        self.assertEqual(self.store.get_outbox(self.outbox.outbox_id).state, "AMBIGUOUS")
        self.assertEqual(
            self.store.get_snapshot("delivery", "delivery-" + self.plan.run_id).state,
            "RECONCILE_REQUIRED",
        )
        self.assertEqual(
            self.store.get_snapshot("request", self.activation.entry.request_id).state,
            "DELIVERING",
        )
        self.assertEqual(
            self.store.get_generation(self.activation.entry.request_id).status,
            "ACTIVE",
        )
        self.assertIsNone(
            self.store.get_terminal_request_capsule(
                self.plan.request_id,
                run_id=self.plan.run_id,
                generation=self.plan.generation,
            )
        )
        checkpoint = self.store.get_active_request_capsule(
            self.plan.request_id,
            run_id=self.plan.run_id,
            generation=self.plan.generation,
        )
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.capsule.capsule_kind, "WORKING_CHECKPOINT")
        self.assertEqual(
            len(self.store.list_completion_decisions(
                self.plan.request_id,
                run_id=self.plan.run_id,
                generation=self.plan.generation,
            )),
            1,
        )
        self.assertTrue(self.store.health_check(now_ms=3_200, full=True).healthy)


if __name__ == "__main__":
    unittest.main()
