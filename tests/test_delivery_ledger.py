import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import DeliveryPartReceipt, DeliveryReceipt
from communication_service.delivery_ledger import (
    DeliveryLedger,
    DeliveryLedgerConflict,
    derive_channel_client_message_id,
    expected_delivery_ledger_schema_sha256,
)
from tests.test_delivery_contracts import (
    ARTIFACT_ID,
    ARTIFACT_REVISION_ID,
    HASH_A,
    HASH_B,
    accepted_receipt,
    consume_verified_delivery_for_test,
    delivery_ticket,
)


def ambiguous_receipt(ticket=None):
    ticket = ticket or delivery_ticket()
    return DeliveryReceipt(
        receipt_id="delivery_receipt_ambiguous_001",
        ticket_id=ticket.payload.ticket_id,
        delivery_id=ticket.payload.delivery_id,
        effect_id=ticket.payload.effect_id,
        request_id=ticket.payload.request_id,
        run_id=ticket.payload.run_id,
        generation=ticket.payload.generation,
        channel=ticket.payload.channel,
        status="RECONCILE_REQUIRED",
        parts=(
            DeliveryPartReceipt(
                part_id="part_text_001",
                index=0,
                kind="text",
                stage="AMBIGUOUS",
                attempt=1,
                started_at_ms=23_000,
                finished_at_ms=23_100,
                evidence_sha256=HASH_A,
                error_code="delivery.receipt_missing",
            ),
            DeliveryPartReceipt(
                part_id="part_artifact_001",
                index=1,
                kind="artifact",
                artifact_id=ARTIFACT_ID,
                artifact_revision_id=ARTIFACT_REVISION_ID,
                stage="AMBIGUOUS",
                attempt=1,
                started_at_ms=23_100,
                finished_at_ms=23_500,
                evidence_sha256=HASH_B,
                error_code="delivery.receipt_missing",
            ),
        ),
        observed_at_ms=23_500,
        error_code="delivery.receipt_missing",
        receipt_sha256=HASH_A,
    ).with_computed_receipt_sha256()


class DeliveryLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "delivery-ledger.sqlite3"
        self.ledger = DeliveryLedger.open(self.path, now_ms=20_000)
        self.ticket = delivery_ticket()
        self.claim = self.ledger.claim_from_payload(self.ticket.payload, claimed_at_ms=22_000)

    def consume(self, *, at_ms=22_000):
        return consume_verified_delivery_for_test(
            self.ledger,
            self.ticket,
            at_ms=at_ms,
        )

    def tearDown(self) -> None:
        self.ledger.close()
        self.temporary.cleanup()

    def test_claim_is_durable_idempotent_and_client_id_is_effect_bound(self) -> None:
        consumed = self.consume()
        record, created = consumed.delivery, consumed.created
        self.assertTrue(created)
        self.assertEqual(record.state, "CLAIMED")
        self.assertEqual(
            record.claim.channel_client_message_id,
            derive_channel_client_message_id(self.claim.effect_id),
        )
        later = self.ledger.claim_from_payload(self.ticket.payload, claimed_at_ms=22_500)
        duplicate_consumption = self.consume(at_ms=22_500)
        duplicate, created = (
            duplicate_consumption.delivery,
            duplicate_consumption.created,
        )
        self.assertFalse(created)
        self.assertEqual(duplicate, record)

    def test_changed_effect_context_is_rejected(self) -> None:
        self.consume()
        changed = self.claim.model_copy(
            update={"recipient_scope_hash": "f" * 64, "claim_sha256": HASH_A}
        ).with_computed_sha256()
        with self.assertRaises(DeliveryLedgerConflict):
            self.ledger.consume_verified_ticket(
                self.ledger.get_verified_ticket(self.ticket.payload.ticket_id),
                changed,
            )

    def test_crash_after_side_effect_start_requires_reconciliation_not_resend(self) -> None:
        self.consume()
        self.ledger.mark_side_effect_started(self.claim.effect_id, started_at_ms=23_000)
        self.ledger.close()
        self.ledger = DeliveryLedger.open(self.path, now_ms=24_000)
        recovered = self.ledger.recover_ambiguous(now_ms=24_000)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, "RECONCILE_REQUIRED")
        self.assertEqual(len(self.ledger.list_reconcile_required()), 1)
        with self.assertRaises(DeliveryLedgerConflict):
            self.ledger.mark_side_effect_started(self.claim.effect_id, started_at_ms=24_100)

    def test_explicit_platform_receipt_commits_terminal_fact(self) -> None:
        self.consume()
        self.ledger.mark_side_effect_started(self.claim.effect_id, started_at_ms=23_000)
        result = self.ledger.record_receipt(accepted_receipt(self.ticket))
        self.assertEqual(result.state, "CHANNEL_ACCEPTED")
        duplicate = self.ledger.record_receipt(accepted_receipt(self.ticket))
        self.assertEqual(duplicate, result)

    def test_ambiguous_receipt_and_recovery_require_explicit_reconciliation(self) -> None:
        self.consume()
        self.ledger.mark_side_effect_started(self.claim.effect_id, started_at_ms=23_000)
        ambiguous = self.ledger.record_receipt(ambiguous_receipt(self.ticket))
        self.assertEqual(ambiguous.state, "RECONCILE_REQUIRED")
        with self.assertRaises(DeliveryLedgerConflict):
            self.ledger.record_receipt(accepted_receipt(self.ticket))
        reconciled = self.ledger.record_receipt(
            accepted_receipt(self.ticket),
            reconciliation=True,
        )
        self.assertEqual(reconciled.state, "RECONCILED")

    def test_fault_during_claim_event_rolls_back_effect(self) -> None:
        self.ledger._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_delivery_event
            BEFORE INSERT ON delivery_stage_events
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.consume()
        finally:
            self.ledger._connection.execute("DROP TRIGGER test_abort_delivery_event")  # noqa: SLF001
        self.assertIsNone(self.ledger.get(self.claim.effect_id))
        self.assertIsNone(
            self.ledger.get_verified_ticket(self.ticket.payload.ticket_id)
        )

    def test_health_detects_semantic_column_tamper(self) -> None:
        self.consume()
        self.ledger._connection.execute(  # noqa: SLF001 - deliberate corruption injection
            "UPDATE delivery_effects SET tenant_id = 'tampered'"
        )
        self.assertFalse(self.ledger.health_check(now_ms=24_000).healthy)

    def test_schema_health_is_reproducible(self) -> None:
        health = self.ledger.health_check(now_ms=22_000, full=True)
        self.assertTrue(health.healthy)
        self.assertEqual(health.schema_sha256, expected_delivery_ledger_schema_sha256())


if __name__ == "__main__":
    unittest.main()
