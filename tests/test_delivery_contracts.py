import hashlib
import unittest

from pydantic import ValidationError

from contracts import (
    ArtifactManifest,
    ComponentDescriptor,
    ComponentManifest,
    DeliveryAuthorizationError,
    DeliveryPartGrant,
    DeliveryPartReceipt,
    DeliveryReceipt,
    DeliveryTicket,
    DeliveryTicketHeader,
    DeliveryTicketPayload,
    OutboundPart,
    OutboundPlan,
    QcEvidence,
    authorize_delivery_contract,
    canonical_sha256,
    correlate_delivery_receipt,
    grant_from_outbound_part,
    text_sha256,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
EXECUTION_EFFECT_ID = "eff_" + "3" * 64
DELIVERY_EFFECT_ID = "eff_" + "4" * 64
ARTIFACT_ID = "art_" + "5" * 64
DELIVERY_ID = "del_" + "6" * 64
ARTIFACT_REVISION_ID = "arv_" + "7" * 64


def qc_evidence(**overrides):
    values = {
        "check_id": "qc.docx.delivery_check",
        "check_version": "1.0.0",
        "status": "PASSED",
        "checked_at_ms": 20_500,
        "evidence_sha256": HASH_A,
        "tool_fact_id": "fact_qc_001",
    }
    values.update(overrides)
    return QcEvidence(**values)


def artifact_manifest(**overrides):
    values = {
        "artifact_id": ARTIFACT_ID,
        "artifact_revision_id": ARTIFACT_REVISION_ID,
        "revision": 1,
        "request_id": REQUEST_ID,
        "run_id": RUN_ID,
        "generation": 2,
        "source_effect_id": EXECUTION_EFFECT_ID,
        "producer_fact_id": "fact_execution_001",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_scope_hash": HASH_B,
        "workspace_id": "workspace_001",
        "content_object_id": "content_object_001",
        "sha256": HASH_A,
        "size_bytes": 37_544,
        "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "filename": "ai_essay.docx",
        "artifact_kind": "document",
        "format_id": "docx",
        "created_at_ms": 20_000,
        "qc_state": "PASSED",
        "qc_evidence": (qc_evidence(),),
        "manifest_sha256": HASH_D,
    }
    values.update(overrides)
    return ArtifactManifest(**values).with_computed_manifest_sha256()


def outbound_plan(**overrides):
    text = "文档已经通过校验。"
    artifact = artifact_manifest()
    values = {
        "outbound_plan_id": "outbound_plan_001",
        "delivery_id": DELIVERY_ID,
        "effect_id": DELIVERY_EFFECT_ID,
        "request_id": REQUEST_ID,
        "run_id": RUN_ID,
        "generation": 2,
        "channel": "wechat",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_ref": "conversation_001",
        "conversation_scope_hash": HASH_B,
        "recipient_scope_hash": HASH_D,
        "reply_to_message_ref": "message_001",
        "channel_policy_hash": HASH_C,
        "created_at_ms": 21_000,
        "parts": (
            OutboundPart(
                part_id="part_text_001",
                index=0,
                kind="text",
                text=text,
                text_sha256=text_sha256(text),
            ),
            OutboundPart(
                part_id="part_artifact_001",
                index=1,
                kind="artifact",
                artifact=artifact,
            ),
        ),
        "plan_sha256": HASH_D,
    }
    values.update(overrides)
    return OutboundPlan(**values).with_computed_plan_sha256()


def component_descriptor(**overrides):
    values = {
        "component_id": "tiangong-communication-service",
        "version": "3.0.0",
        "build_id": "communication_build_001",
        "role": "communication",
        "executable_relative_path": "communication-service/tiangong-communication-service.exe",
        "sha256": HASH_A,
        "size_bytes": 1_000_000,
        "ports": (7176,),
        "api_contract_ids": ("tiangong.communication.api.v1",),
        "schema_bundle_hash": HASH_B,
    }
    values.update(overrides)
    return ComponentDescriptor(**values)


def component_manifest(**overrides):
    values = {
        "manifest_id": "component_manifest_001",
        "product_version": "3.0.0",
        "generated_at_ms": 10_000,
        "contract_schema_bundle_hash": HASH_A,
        "capability_manifest_hash": HASH_B,
        "skill_index_hash": HASH_C,
        "release_policy_hash": HASH_D,
        "components": (component_descriptor(),),
        "production_claim": False,
        "manifest_sha256": HASH_A,
    }
    values.update(overrides)
    return ComponentManifest(**values).with_computed_manifest_sha256()


def delivery_ticket(plan=None, components=None, **overrides):
    plan = plan or outbound_plan()
    components = components or component_manifest()
    grants = tuple(grant_from_outbound_part(part) for part in plan.parts)
    values = {
        "ticket_id": "delivery_ticket_001",
        "issued_at_ms": 22_000,
        "not_before_ms": 22_000,
        "expires_at_ms": 82_000,
        "gateway_epoch": 3,
        "request_id": plan.request_id,
        "run_id": plan.run_id,
        "generation": plan.generation,
        "delivery_id": plan.delivery_id,
        "effect_id": plan.effect_id,
        "channel": plan.channel,
        "tenant_id": plan.tenant_id,
        "link_account_id": plan.link_account_id,
        "conversation_ref": plan.conversation_ref,
        "conversation_scope_hash": plan.conversation_scope_hash,
        "recipient_scope_hash": plan.recipient_scope_hash,
        "reply_to_message_ref": plan.reply_to_message_ref,
        "outbound_plan_id": plan.outbound_plan_id,
        "outbound_plan_sha256": plan.plan_sha256,
        "channel_policy_hash": plan.channel_policy_hash,
        "component_manifest_hash": components.manifest_sha256,
        "allow_text": True,
        "allow_files": True,
        "max_text_parts": 1,
        "max_file_parts": 1,
        "upload_timeout_ms": 300_000,
        "send_timeout_ms": 60_000,
        "parts": grants,
    }
    values.update(overrides)
    return DeliveryTicket(
        header=DeliveryTicketHeader(kid="delivery_key_001"),
        payload=DeliveryTicketPayload(**values),
        signature="B" * 86,
    )


def consume_verified_delivery_for_test(ledger, ticket_or_payload, *, at_ms=22_000):
    """Seed the transport ledger after the dispatcher boundary in isolated unit tests."""

    from communication_service.delivery_ledger import VerifiedDeliveryTicketFact

    if isinstance(ticket_or_payload, DeliveryTicket):
        payload = ticket_or_payload.payload
        kid = ticket_or_payload.header.kid
        signature = ticket_or_payload.signature.encode("ascii")
    else:
        payload = ticket_or_payload
        kid = "transport_unit_test_key"
        signature = b"transport-unit-test-signature"
    verification = VerifiedDeliveryTicketFact(
        ticket_id=payload.ticket_id,
        kid=kid,
        issuer=payload.issuer,
        audience=payload.audience,
        gateway_epoch=payload.gateway_epoch,
        request_id=payload.request_id,
        run_id=payload.run_id,
        generation=payload.generation,
        delivery_id=payload.delivery_id,
        effect_id=payload.effect_id,
        outbound_plan_sha256=payload.outbound_plan_sha256,
        payload_sha256=canonical_sha256(payload.model_dump(mode="json")),
        signature_sha256=hashlib.sha256(signature).hexdigest(),
        trust_bundle_sha256="e" * 64,
        component_manifest_sha256=payload.component_manifest_hash,
        verified_at_ms=at_ms,
        expires_at_ms=payload.expires_at_ms,
        verification_sha256="0" * 64,
    ).with_computed_sha256()
    claim = ledger.claim_from_payload(payload, claimed_at_ms=at_ms)
    return ledger.consume_verified_ticket(verification, claim)


def accepted_receipt(ticket=None, **overrides):
    ticket = ticket or delivery_ticket()
    values = {
        "receipt_id": "delivery_receipt_001",
        "ticket_id": ticket.payload.ticket_id,
        "delivery_id": ticket.payload.delivery_id,
        "effect_id": ticket.payload.effect_id,
        "request_id": ticket.payload.request_id,
        "run_id": ticket.payload.run_id,
        "generation": ticket.payload.generation,
        "channel": ticket.payload.channel,
        "status": "CHANNEL_ACCEPTED",
        "parts": (
            DeliveryPartReceipt(
                part_id="part_text_001",
                index=0,
                kind="text",
                stage="CHANNEL_ACCEPTED",
                attempt=1,
                started_at_ms=23_000,
                finished_at_ms=23_100,
                channel_message_ref="wechat_message_text_001",
                evidence_sha256=HASH_A,
                platform_receipt_sha256=HASH_B,
            ),
            DeliveryPartReceipt(
                part_id="part_artifact_001",
                index=1,
                kind="artifact",
                artifact_id=ARTIFACT_ID,
                artifact_revision_id=ARTIFACT_REVISION_ID,
                stage="DELIVERED",
                attempt=1,
                started_at_ms=23_100,
                finished_at_ms=23_500,
                channel_message_ref="wechat_message_file_001",
                evidence_sha256=HASH_C,
                platform_receipt_sha256=HASH_D,
            ),
        ),
        "observed_at_ms": 23_500,
        "receipt_sha256": HASH_A,
    }
    values.update(overrides)
    return DeliveryReceipt(**values).with_computed_receipt_sha256()


class ArtifactManifestTests(unittest.TestCase):
    def test_passed_artifact_is_content_addressed_and_self_hashes(self) -> None:
        artifact = artifact_manifest()
        self.assertTrue(artifact.has_valid_manifest_sha256())
        self.assertEqual(artifact.size_bytes, 37_544)

    def test_rejects_false_qc_pass_or_path_filename(self) -> None:
        with self.assertRaises(ValidationError):
            artifact_manifest(
                qc_evidence=(qc_evidence(status="FAILED"),),
                qc_state="PASSED",
            )
        with self.assertRaises(ValidationError):
            artifact_manifest(filename="..\\ai_essay.docx")


class OutboundPlanTests(unittest.TestCase):
    def test_plan_keeps_text_and_file_as_independent_parts(self) -> None:
        plan = outbound_plan()
        self.assertTrue(plan.has_valid_plan_sha256())
        self.assertEqual([part.kind for part in plan.parts], ["text", "artifact"])

    def test_rejects_cross_tenant_artifact(self) -> None:
        with self.assertRaises(ValidationError):
            OutboundPlan(
                **{
                    **outbound_plan().model_dump(exclude={"plan_sha256"}),
                    "parts": (
                        OutboundPart(
                            part_id="part_artifact_001",
                            index=0,
                            kind="artifact",
                            artifact=artifact_manifest(tenant_id="tenant_002"),
                        ),
                    ),
                    "plan_sha256": HASH_A,
                }
            )


class DeliveryAuthorizationTests(unittest.TestCase):
    def test_authorizes_exact_plan_and_component_manifest(self) -> None:
        plan = outbound_plan()
        components = component_manifest()
        ticket = delivery_ticket(plan, components)
        authorized = authorize_delivery_contract(
            ticket,
            plan,
            components,
            signature_verified=True,
            now_ms=30_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
        )
        self.assertEqual(authorized.delivery_id, DELIVERY_ID)

    def test_rejects_swapped_file_or_unverified_signature(self) -> None:
        plan = outbound_plan()
        components = component_manifest()
        ticket = delivery_ticket(plan, components)
        with self.assertRaises(DeliveryAuthorizationError) as caught:
            authorize_delivery_contract(
                ticket,
                plan,
                components,
                signature_verified=False,
                now_ms=30_000,
                expected_gateway_epoch=3,
            )
        self.assertEqual(caught.exception.code, "ticket.signature.unverified")

        wrong_recipient = delivery_ticket(plan, components, recipient_scope_hash=HASH_A)
        with self.assertRaises(DeliveryAuthorizationError) as caught:
            authorize_delivery_contract(
                wrong_recipient,
                plan,
                components,
                signature_verified=True,
                now_ms=30_000,
                expected_gateway_epoch=3,
            )
        self.assertEqual(caught.exception.code, "ticket.recipient_scope_hash.mismatch")

        original = ticket.payload.parts[1]
        swapped = DeliveryPartGrant(**{**original.model_dump(), "content_sha256": HASH_D})
        tampered_payload = ticket.payload.model_copy(
            update={"parts": (ticket.payload.parts[0], swapped)}
        )
        tampered_ticket = ticket.model_copy(update={"payload": tampered_payload})
        with self.assertRaises(DeliveryAuthorizationError) as caught:
            authorize_delivery_contract(
                tampered_ticket,
                plan,
                components,
                signature_verified=True,
                now_ms=30_000,
                expected_gateway_epoch=3,
            )
        self.assertEqual(caught.exception.code, "ticket.parts.mismatch")


class DeliveryReceiptTests(unittest.TestCase):
    def test_channel_accepted_does_not_claim_every_part_delivered(self) -> None:
        ticket = delivery_ticket()
        receipt = accepted_receipt(ticket)
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual(receipt.parts[0].stage, "CHANNEL_ACCEPTED")
        self.assertEqual(receipt.parts[1].stage, "DELIVERED")
        self.assertIs(correlate_delivery_receipt(receipt, ticket), receipt)

    def test_receipt_cannot_swap_artifact_revision(self) -> None:
        ticket = delivery_ticket()
        receipt = accepted_receipt(ticket)
        swapped_part = receipt.parts[1].model_copy(
            update={"artifact_revision_id": "arv_" + "f" * 64}
        )
        swapped = receipt.model_copy(update={"parts": (receipt.parts[0], swapped_part)})
        swapped = swapped.with_computed_receipt_sha256()
        with self.assertRaises(DeliveryAuthorizationError) as caught:
            correlate_delivery_receipt(swapped, ticket)
        self.assertEqual(
            caught.exception.code,
            "delivery_receipt.artifact_revision_id.mismatch",
        )

    def test_delivered_requires_delivery_evidence_for_every_part(self) -> None:
        with self.assertRaises(ValidationError):
            accepted_receipt(status="DELIVERED")
        with self.assertRaises(ValidationError):
            DeliveryPartReceipt(
                part_id="part_text_001",
                index=0,
                kind="text",
                stage="DELIVERED",
                attempt=1,
                started_at_ms=23_000,
                finished_at_ms=23_100,
                evidence_sha256=HASH_A,
            )

    def test_receipt_cannot_be_model_generated(self) -> None:
        receipt = accepted_receipt()
        with self.assertRaises(ValidationError):
            DeliveryReceipt(**{**receipt.model_dump(), "model_generated": True})


class ComponentManifestTests(unittest.TestCase):
    def test_component_manifest_self_hashes(self) -> None:
        manifest = component_manifest()
        self.assertTrue(manifest.has_valid_manifest_sha256())

    def test_rejects_absolute_path_or_false_production_claim(self) -> None:
        with self.assertRaises(ValidationError):
            component_descriptor(executable_relative_path="C:/service.exe")
        with self.assertRaises(ValidationError):
            component_manifest(production_claim=True)


if __name__ == "__main__":
    unittest.main()
