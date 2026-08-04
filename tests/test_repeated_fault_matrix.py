import hashlib
import tempfile
import unittest
from pathlib import Path

from communication_service.delivery_ledger import DeliveryLedger
from contracts import DeliveryPartReceipt, DeliveryReceipt, canonical_sha256
from tests.protocol_simulators import WechatProtocolSimulator
from tests.test_delivery_contracts import (
    accepted_receipt,
    consume_verified_delivery_for_test,
    delivery_ticket,
)


def _identity(prefix, domain, index):
    return prefix + hashlib.sha256(f"{domain}:{index}".encode()).hexdigest()


def _round_ticket(index):
    base = delivery_ticket()
    payload = base.payload.model_copy(
        update={
            "ticket_id": f"delivery_fault_round_{index:03d}",
            "request_id": _identity("req_", "request", index),
            "run_id": _identity("run_", "run", index),
            "delivery_id": _identity("del_", "delivery", index),
            "effect_id": _identity("eff_", "effect", index),
        }
    )
    return base.model_copy(update={"payload": payload})


def _retryable_receipt(ticket, *, observed_at_ms):
    payload = ticket.payload
    parts = tuple(
        DeliveryPartReceipt(
            part_id=grant.part_id,
            index=grant.index,
            kind=grant.kind,
            artifact_id=grant.artifact_id,
            artifact_revision_id=grant.artifact_revision_id,
            stage="FAILED_RETRYABLE",
            attempt=1,
            started_at_ms=observed_at_ms,
            finished_at_ms=observed_at_ms,
            evidence_sha256=canonical_sha256(
                {"round_effect": payload.effect_id, "part_id": grant.part_id}
            ),
            error_code="simulation.network.disconnected_before_effect",
        )
        for grant in payload.parts
    )
    return DeliveryReceipt(
        receipt_id="fault_receipt_" + payload.effect_id[4:36],
        ticket_id=payload.ticket_id,
        delivery_id=payload.delivery_id,
        effect_id=payload.effect_id,
        request_id=payload.request_id,
        run_id=payload.run_id,
        generation=payload.generation,
        channel=payload.channel,
        status="FAILED_RETRYABLE",
        parts=parts,
        observed_at_ms=observed_at_ms,
        error_code="simulation.network.disconnected_before_effect",
        receipt_sha256="0" * 64,
    ).with_computed_receipt_sha256()


class RepeatedFaultMatrixTests(unittest.TestCase):
    def test_120_duplicate_disconnect_and_ambiguity_rounds_remain_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "delivery.sqlite3"
            ledger = DeliveryLedger.open(path, now_ms=1_000)
            simulator = WechatProtocolSimulator()
            counts = {"duplicate": 0, "disconnect": 0, "ambiguous": 0}
            external_acceptances = 0
            try:
                for index in range(120):
                    ticket = _round_ticket(index)
                    claimed_at = 22_000 + index
                    first = consume_verified_delivery_for_test(
                        ledger,
                        ticket,
                        at_ms=claimed_at,
                    )
                    self.assertTrue(first.created)
                    scenario = index % 3
                    if scenario == 0:
                        counts["duplicate"] += 1
                        ledger.mark_side_effect_started(
                            ticket.payload.effect_id,
                            started_at_ms=23_000 + index,
                        )
                        external_acceptances += 1
                        receipt = accepted_receipt(ticket)
                        stored = ledger.record_receipt(receipt)
                        duplicate = consume_verified_delivery_for_test(
                            ledger,
                            ticket,
                            at_ms=22_500 + index,
                        )
                        self.assertFalse(duplicate.created)
                        self.assertEqual(duplicate.delivery.receipt, stored.receipt)
                    elif scenario == 1:
                        counts["disconnect"] += 1
                        simulator.script(
                            "message.send",
                            OSError("simulated disconnect before channel effect"),
                        )
                        with self.assertRaises(OSError):
                            simulator.send_message(
                                {"round": index},
                                bot_token="synthetic-secret",
                                timeout_seconds=1,
                            )
                        receipt = _retryable_receipt(
                            ticket,
                            observed_at_ms=23_500 + index,
                        )
                        stored = ledger.record_receipt(
                            receipt,
                            side_effect_absent_verified=True,
                        )
                        self.assertEqual(stored.state, "FAILED_RETRYABLE")
                    else:
                        counts["ambiguous"] += 1
                        ledger.mark_side_effect_started(
                            ticket.payload.effect_id,
                            started_at_ms=23_000 + index,
                        )
                        simulator.script(
                            "message.send",
                            OSError("simulated disconnect after channel effect"),
                        )
                        with self.assertRaises(OSError):
                            simulator.send_message(
                                {"round": index},
                                bot_token="synthetic-secret",
                                timeout_seconds=1,
                            )
                        recovered = ledger.recover_ambiguous(now_ms=24_000 + index)
                        self.assertEqual(
                            tuple(item.claim.effect_id for item in recovered),
                            (ticket.payload.effect_id,),
                        )
                        duplicate = consume_verified_delivery_for_test(
                            ledger,
                            ticket,
                            at_ms=22_500 + index,
                        )
                        self.assertFalse(duplicate.created)
                        self.assertEqual(
                            duplicate.delivery.state,
                            "RECONCILE_REQUIRED",
                        )

                self.assertEqual(counts, {"duplicate": 40, "disconnect": 40, "ambiguous": 40})
                self.assertEqual(external_acceptances, 40)
                self.assertEqual(simulator.call_count("message.send"), 80)
                self.assertEqual(len(ledger.list_reconcile_required()), 40)
                self.assertTrue(ledger.health_check(now_ms=30_000, full=True).healthy)
                ledger.close()
                ledger = DeliveryLedger.open(path, now_ms=31_000)
                self.assertEqual(len(ledger.list_reconcile_required()), 40)
                self.assertTrue(ledger.health_check(now_ms=31_000, full=True).healthy)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
