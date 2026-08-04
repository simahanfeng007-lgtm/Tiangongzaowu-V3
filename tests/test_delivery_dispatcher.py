import base64
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from communication_service.delivery_dispatcher import (
    DeliveryDispatchError,
    VerifiedDeliveryDispatcher,
)
from communication_service.channel_authority import ChannelAuthorityError, ChannelAuthorityGate
from communication_service.delivery_ledger import DeliveryLedger
from contracts import (
    DeliveryAuthorizationError,
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
    activate_candidate_owner,
    apply_channel_drain,
    begin_channel_cutover,
    build_channel_drain_evidence,
)
from runtime_security import TicketVerificationError
from total_gateway.tickets import TicketSigner
from tests.test_delivery_contracts import (
    accepted_receipt,
    component_manifest,
    delivery_ticket,
    outbound_plan,
)


class _NeverHandler:
    def __init__(self):
        self.calls = 0

    def send(self, payload, plan):
        self.calls += 1
        raise AssertionError("unexpected channel dispatch")


class _LedgerHandler:
    def __init__(self, ledger, ticket, *, crash=False):
        self.ledger = ledger
        self.ticket = ticket
        self.crash = crash
        self.calls = 0
        self.external_effects = 0
        self.arguments = []
        self.lock = threading.Lock()

    def send(self, payload, plan):
        with self.lock:
            self.calls += 1
            self.arguments.append((payload, plan))
            record = self.ledger.get(payload.effect_id)
            if record is not None and record.receipt is not None:
                return record.receipt
            if self.crash:
                raise RuntimeError("injected crash before channel side effect")
            self.external_effects += 1
            self.ledger.mark_side_effect_started(
                payload.effect_id,
                started_at_ms=23_100,
            )
            return self.ledger.record_receipt(
                accepted_receipt(self.ticket)
            ).receipt


def _signed_fixture():
    plan = outbound_plan()
    components = component_manifest()
    unsigned = delivery_ticket(plan=plan, components=components).payload
    private = Ed25519PrivateKey.generate()
    raw_public = private.public_key().public_bytes_raw()
    descriptor = PublicKeyDescriptor(
        kid="delivery_dispatch_key_001",
        issuer="tiangong-total-gateway",
        audience="tiangong-communication-service",
        purpose="delivery_ticket",
        public_key_base64url=base64.urlsafe_b64encode(raw_public)
        .rstrip(b"=")
        .decode("ascii"),
        public_key_sha256=hashlib.sha256(raw_public).hexdigest(),
        state="ACTIVE",
        not_before_ms=0,
        not_after_ms=100_000,
        component_manifest_hash=components.manifest_sha256,
    )
    trust = TrustBundle(
        bundle_id="delivery_dispatch_trust_001",
        revision=1,
        gateway_epoch=unsigned.gateway_epoch,
        generated_at_ms=20_000,
        required_scopes=(
            TrustScope(
                issuer=descriptor.issuer,
                audience=descriptor.audience,
                purpose=descriptor.purpose,
            ),
        ),
        keys=(descriptor,),
        production_ready=True,
        bundle_sha256="0" * 64,
    ).with_computed_sha256()
    ticket = TicketSigner(descriptor.kid, private).sign_delivery(unsigned)
    return plan, components, trust, ticket


class VerifiedDeliveryDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = DeliveryLedger.open(
            Path(self.temporary.name) / "delivery.sqlite3",
            now_ms=1_000,
        )
        self.plan, self.components, self.trust, self.ticket = _signed_fixture()
        self.never = _NeverHandler()

    def tearDown(self):
        self.ledger.close()
        self.temporary.cleanup()

    def authority(self, components=None):
        components = components or self.components
        payload = self.ticket.payload
        snapshot = begin_channel_cutover(
            channel=payload.channel,
            tenant_id=payload.tenant_id,
            link_account_id=payload.link_account_id,
            gateway_epoch=payload.gateway_epoch,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            candidate_owner_instance_id="candidate-instance",
            started_at_ms=21_000,
        )
        evidence = build_channel_drain_evidence(
            channel=payload.channel,
            tenant_id=payload.tenant_id,
            link_account_id=payload.link_account_id,
            gateway_epoch=payload.gateway_epoch,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            inbox_ledger_sha256="a" * 64,
            delivery_ledger_sha256="b" * 64,
            last_cursor_sha256="c" * 64,
            observed_at_ms=21_100,
        )
        drained = apply_channel_drain(snapshot, evidence)
        _, lease = activate_candidate_owner(
            drained,
            evidence,
            component_manifest_sha256=components.manifest_sha256,
            issued_at_ms=22_000,
            lease_ttl_ms=30_000,
        )
        authority = ChannelAuthorityGate(
            owner_instance_id="candidate-instance",
            expected_gateway_epoch=payload.gateway_epoch,
            expected_component_manifest_sha256=components.manifest_sha256,
        )
        authority.install_lease(lease, now_ms=23_000)
        return authority

    def dispatcher(self, handler, *, components=None, generation_floor=0):
        components = components or self.components
        return VerifiedDeliveryDispatcher(
            self.ledger,
            self.trust,
            components,
            {"wechat": handler, "feishu": self.never},
            clock_ms=lambda: 23_000,
            generation_floor=lambda _request, _run: generation_floor,
            channel_authority=self.authority(components),
        )

    def test_full_signed_ticket_is_persisted_before_handler_and_duplicate_is_idempotent(self):
        handler = _LedgerHandler(self.ledger, self.ticket)
        dispatcher = self.dispatcher(handler)
        first = dispatcher.dispatch(self.ticket, self.plan)
        duplicate = dispatcher.dispatch(self.ticket, self.plan)

        self.assertEqual(first, duplicate)
        self.assertEqual(handler.external_effects, 1)
        self.assertEqual(handler.calls, 1)
        verification = self.ledger.get_verified_ticket(self.ticket.payload.ticket_id)
        self.assertIsNotNone(verification)
        self.assertEqual(verification.effect_id, self.ticket.payload.effect_id)
        self.assertEqual(verification.kid, self.ticket.header.kid)
        self.assertEqual(handler.arguments, [(self.ticket.payload, self.plan)])

    def test_payload_only_dispatch_is_rejected_before_handler(self):
        with self.assertRaises(TypeError):
            self.dispatcher(self.never).dispatch(self.ticket.payload, self.plan)
        self.assertEqual(self.never.calls, 0)
        self.assertIsNone(self.ledger.get_verified_ticket(self.ticket.payload.ticket_id))

    def test_missing_channel_lease_rejects_before_ticket_claim_and_handler(self):
        authority = ChannelAuthorityGate(
            owner_instance_id="candidate-instance",
            expected_gateway_epoch=self.ticket.payload.gateway_epoch,
            expected_component_manifest_sha256=self.components.manifest_sha256,
        )
        dispatcher = VerifiedDeliveryDispatcher(
            self.ledger,
            self.trust,
            self.components,
            {"wechat": self.never, "feishu": _NeverHandler()},
            clock_ms=lambda: 23_000,
            generation_floor=lambda _request, _run: 0,
            channel_authority=authority,
        )
        with self.assertRaises(ChannelAuthorityError):
            dispatcher.dispatch(self.ticket, self.plan)
        self.assertEqual(self.never.calls, 0)
        self.assertIsNone(self.ledger.get_verified_ticket(self.ticket.payload.ticket_id))
        self.assertIsNone(self.ledger.get(self.ticket.payload.effect_id))

    def test_signature_kid_plan_manifest_and_generation_fail_before_handler(self):
        cases = []
        bad_signature = self.ticket.model_copy(update={"signature": "C" * 86})
        cases.append((bad_signature, self.plan, self.components, 0, TicketVerificationError))
        bad_kid = self.ticket.model_copy(
            update={
                "header": self.ticket.header.model_copy(
                    update={"kid": "unknown_delivery_key"}
                )
            }
        )
        cases.append((bad_kid, self.plan, self.components, 0, TicketVerificationError))
        wrong_plan = self.plan.model_copy(
            update={"recipient_scope_hash": "e" * 64, "plan_sha256": "0" * 64}
        ).with_computed_plan_sha256()
        cases.append((self.ticket, wrong_plan, self.components, 0, DeliveryAuthorizationError))
        other_components = self.components.model_copy(
            update={"product_version": "3.0.1", "manifest_sha256": "0" * 64}
        ).with_computed_manifest_sha256()
        cases.append((self.ticket, self.plan, other_components, 0, DeliveryAuthorizationError))
        cases.append((self.ticket, self.plan, self.components, 3, DeliveryAuthorizationError))

        for ticket, plan, components, floor, error in cases:
            with self.subTest(error=error.__name__, floor=floor):
                with self.assertRaises(error):
                    self.dispatcher(
                        self.never,
                        components=components,
                        generation_floor=floor,
                    ).dispatch(ticket, plan)
                self.assertIsNone(
                    self.ledger.get_verified_ticket(self.ticket.payload.ticket_id)
                )
        self.assertEqual(self.never.calls, 0)

        wrong_epoch_trust = self.trust.model_copy(
            update={"gateway_epoch": 4, "bundle_sha256": "0" * 64}
        ).with_computed_sha256()
        wrong_epoch_dispatcher = VerifiedDeliveryDispatcher(
            self.ledger,
            wrong_epoch_trust,
            self.components,
            {"wechat": self.never, "feishu": _NeverHandler()},
            clock_ms=lambda: 23_000,
            generation_floor=lambda _request, _run: 0,
            channel_authority=self.authority(),
        )
        with self.assertRaises(TicketVerificationError):
            wrong_epoch_dispatcher.dispatch(self.ticket, self.plan)
        self.assertIsNone(
            self.ledger.get_verified_ticket(self.ticket.payload.ticket_id)
        )

    def test_crash_after_atomic_consumption_can_resume_without_reverification_gap(self):
        crashing = _LedgerHandler(self.ledger, self.ticket, crash=True)
        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            self.dispatcher(crashing).dispatch(self.ticket, self.plan)
        self.assertIsNotNone(
            self.ledger.get_verified_ticket(self.ticket.payload.ticket_id)
        )
        self.assertEqual(self.ledger.get(self.ticket.payload.effect_id).state, "CLAIMED")

        resumed = _LedgerHandler(self.ledger, self.ticket)
        receipt = self.dispatcher(resumed).dispatch(self.ticket, self.plan)
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual(resumed.external_effects, 1)

    def test_effect_claim_failure_rolls_back_ticket_consumption_atomically(self):
        self.ledger._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_verified_effect
            BEFORE INSERT ON delivery_effects
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.dispatcher(self.never).dispatch(self.ticket, self.plan)
        finally:
            self.ledger._connection.execute(  # noqa: SLF001
                "DROP TRIGGER test_abort_verified_effect"
            )
        self.assertIsNone(
            self.ledger.get_verified_ticket(self.ticket.payload.ticket_id)
        )
        self.assertIsNone(self.ledger.get(self.ticket.payload.effect_id))
        self.assertEqual(self.never.calls, 0)

    def test_verification_semantic_tamper_makes_delivery_ledger_not_ready(self):
        crashing = _LedgerHandler(self.ledger, self.ticket, crash=True)
        with self.assertRaises(RuntimeError):
            self.dispatcher(crashing).dispatch(self.ticket, self.plan)
        self.ledger._connection.execute(  # noqa: SLF001 - corruption injection
            "UPDATE verified_delivery_tickets "
            "SET component_manifest_sha256=? WHERE ticket_id=?",
            ("f" * 64, self.ticket.payload.ticket_id),
        )
        self.assertFalse(self.ledger.health_check(now_ms=31_000, full=True).healthy)

    def test_concurrent_replay_has_one_verification_and_one_external_effect(self):
        handler = _LedgerHandler(self.ledger, self.ticket)
        dispatcher = self.dispatcher(handler)
        barrier = threading.Barrier(8)
        receipts = []
        errors = []

        def run():
            try:
                barrier.wait(timeout=5)
                receipts.append(dispatcher.dispatch(self.ticket, self.plan))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(receipts), 8)
        self.assertEqual(handler.external_effects, 1)
        self.assertGreaterEqual(handler.calls, 1)
        self.assertTrue(self.ledger.health_check(now_ms=31_000, full=True).healthy)

    def test_non_channel_ticket_is_rejected_without_persistence(self):
        desktop_payload = self.ticket.payload.model_copy(update={"channel": "desktop"})
        desktop_ticket = self.ticket.model_copy(update={"payload": desktop_payload})
        with self.assertRaises(DeliveryDispatchError):
            self.dispatcher(self.never).dispatch(desktop_ticket, self.plan)
        self.assertEqual(self.never.calls, 0)


if __name__ == "__main__":
    unittest.main()
