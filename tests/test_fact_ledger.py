from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from total_gateway.backend_client import BackendClient, BackendExecutionResponse
from total_gateway.fact_ledger import (
    FACT_LEDGER_APPLICATION_ID,
    FACT_LEDGER_SCHEMA_VERSION,
    FactLedger,
    FactLedgerConflict,
    FactLedgerError,
    expected_fact_ledger_schema_sha256,
)
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore
from tests.test_backend_client import FakeBackendTransport, backend_response, signed_ticket


class FactLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.gateway_store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=1_000)
        self.object_store = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
        self.path = root / "facts.sqlite3"
        self.ledger = FactLedger.open(self.path, self.object_store, now_ms=1_000)

    def tearDown(self) -> None:
        self.ledger.close()
        self.object_store.close()
        self.gateway_store.close()
        self.temporary.cleanup()

    def verified_response(
        self,
        arguments: dict[str, object],
        result_payload: object,
        *,
        result_id: str = "execution_result_fact_001",
        fact_id: str = "fact_execution_001",
    ):
        ticket, manifest, trust = signed_ticket(arguments)
        transport = FakeBackendTransport()
        envelope = backend_response(ticket, result_payload)
        execution_result = dict(envelope["execution_result"])
        execution_result["result_id"] = result_id
        execution_result["fact_ids"] = [fact_id]
        envelope["execution_result"] = execution_result
        transport.response = envelope
        client = BackendClient(
            transport,
            self.gateway_store,
            ticket_consumer_instance_id="gateway_fact_test",
        )
        return client.execute(
            ticket,
            arguments,
            capability_manifest=manifest,
            trust_bundle=trust,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
        )

    def test_records_only_verified_machine_result_and_content_addressed_payload(self) -> None:
        response = self.verified_response(
            {"content": "hello"},
            {"created": True, "word_count": 1000},
        )
        registration = self.ledger.record_execution(response, observed_at_ms=20_200)
        self.assertTrue(registration.created_by_this_call)
        record = registration.record
        self.assertEqual(record.result, response.result)
        self.assertEqual(len(record.facts), 1)
        self.assertEqual(record.facts[0].fact_type, "execution.succeeded")
        self.assertFalse(record.facts[0].model_generated)
        self.assertTrue(record.facts[0].has_valid_sha256())
        self.assertEqual(
            self.object_store.read_bytes(record.result_payload_object_id),
            b'{"created":true,"word_count":1000}',
        )
        self.assertEqual(self.ledger.get_fact("fact_execution_001"), record.facts[0])
        self.assertEqual(
            self.ledger.list_request_facts(
                response.result.request_id,
                run_id=response.result.run_id,
                generation=response.result.generation,
            ),
            record.facts,
        )
        health = self.ledger.health_check(now_ms=21_000, full=True)
        self.assertTrue(health.healthy)
        self.assertEqual(health.schema_sha256, expected_fact_ledger_schema_sha256())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], FACT_LEDGER_APPLICATION_ID)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], FACT_LEDGER_SCHEMA_VERSION)
        finally:
            connection.close()

    def test_model_text_and_forged_response_have_no_fact_write_path(self) -> None:
        with self.assertRaisesRegex(FactLedgerError, "unverified"):
            self.ledger.record_execution("任务已完成", observed_at_ms=20_200)  # type: ignore[arg-type]
        valid = self.verified_response({"content": "hello"}, {"created": True})
        forged = BackendExecutionResponse(
            result=valid.result,
            result_payload=valid.result_payload,
            response_sha256=valid.response_sha256,
            ticket=valid.ticket,
            _verification_marker=object(),
        )
        with self.assertRaisesRegex(FactLedgerError, "unverified"):
            self.ledger.record_execution(forged, observed_at_ms=20_200)
        self.assertEqual(self.ledger.count_facts(), 0)

    def test_duplicate_returns_first_observation_but_changed_effect_evidence_conflicts(self) -> None:
        response = self.verified_response({"content": "hello"}, {"created": True})
        first = self.ledger.record_execution(response, observed_at_ms=20_200)
        duplicate = self.ledger.record_execution(response, observed_at_ms=20_500)
        self.assertFalse(duplicate.created_by_this_call)
        self.assertEqual(duplicate.record, first.record)
        self.assertEqual(duplicate.record.observed_at_ms, 20_200)

        other = self.verified_response(
            {"content": "other"},
            {"created": True, "changed": True},
            result_id="execution_result_fact_002",
            fact_id="fact_execution_002",
        )
        with self.assertRaises(FactLedgerConflict):
            self.ledger.record_execution(other, observed_at_ms=20_300)
        self.assertEqual(self.ledger.count_facts(), 1)

    def test_fact_insert_fault_rolls_back_batch_and_fact_together(self) -> None:
        response = self.verified_response({"content": "hello"}, {"created": True})
        self.ledger._connection.execute(  # noqa: SLF001 - deliberate crash-boundary injection
            """
            CREATE TRIGGER abort_fact_insert
            BEFORE INSERT ON fact_ledger
            BEGIN SELECT RAISE(ABORT, 'fault injection'); END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.ledger.record_execution(response, observed_at_ms=20_200)
        finally:
            self.ledger._connection.execute("DROP TRIGGER abort_fact_insert")  # noqa: SLF001
        self.assertEqual(self.ledger.count_facts(), 0)
        self.assertIsNone(self.ledger.get_batch(response.result.result_id))

    def test_restart_concurrency_and_semantic_tamper(self) -> None:
        response = self.verified_response({"content": "hello"}, {"created": True})
        other = FactLedger.open(self.path, self.object_store, now_ms=2_000)
        barrier = threading.Barrier(2)
        outcomes: list[bool] = []
        errors: list[Exception] = []

        def write(ledger: FactLedger) -> None:
            try:
                barrier.wait(timeout=5)
                outcomes.append(
                    ledger.record_execution(response, observed_at_ms=20_200).created_by_this_call
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(item,)) for item in (self.ledger, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other.close()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(outcomes), [False, True])

        self.ledger.close()
        self.ledger = FactLedger.open(self.path, self.object_store, now_ms=21_000)
        self.assertIsNotNone(self.ledger.get_batch(response.result.result_id))
        self.ledger._connection.execute(  # noqa: SLF001 - deliberate semantic tamper
            "UPDATE fact_ledger SET payload_sha256 = ? WHERE fact_id = ?",
            ("f" * 64, "fact_execution_001"),
        )
        self.assertFalse(self.ledger.health_check(now_ms=22_000, full=True).healthy)


if __name__ == "__main__":
    unittest.main()
