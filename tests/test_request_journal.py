import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import InboundEnvelope, InboundScope, derive_inbound_scope_keys
from total_gateway import store as store_module
from total_gateway.store import (
    APPLICATION_ID,
    STORE_SCHEMA_VERSION,
    GatewayStateStore,
    StoreConflictError,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def envelope(
    message_ref: str = "message_001",
    *,
    conversation_ref: str = "conversation_001",
    text: str = "hello",
) -> InboundEnvelope:
    scope = InboundScope(
        channel="wechat",
        tenant_id="tenant_001",
        link_account_id="wechat_001",
        conversation_ref=conversation_ref,
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
        text=text,
    )


class RequestJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_same_session_is_serialized_and_completion_activates_fifo(self) -> None:
        first = self.store.register_request(envelope("message_001"), ingress_sha256=HASH_A, created_at_ms=1_100)
        second = self.store.register_request(envelope("message_002"), ingress_sha256=HASH_B, created_at_ms=1_200)
        third = self.store.register_request(envelope("message_003"), ingress_sha256="c" * 64, created_at_ms=1_300)
        self.assertEqual((first.queue_state, second.queue_state, third.queue_state), ("ACTIVE", "QUEUED", "QUEUED"))
        self.assertEqual((first.queue_sequence, second.queue_sequence, third.queue_sequence), (1, 2, 3))

        activated = self.store.complete_session_request(
            first.entry.session_scope_hash,
            first.entry.request_id,
            completed_at_ms=1_400,
        )
        self.assertEqual(activated.request_id, second.entry.request_id)
        self.assertEqual(activated.state, "ACTIVE")
        duplicate = self.store.complete_session_request(
            first.entry.session_scope_hash,
            first.entry.request_id,
            completed_at_ms=1_500,
        )
        self.assertEqual(duplicate.request_id, second.entry.request_id)
        queue = self.store.get_session_queue(first.entry.session_scope_hash)
        self.assertEqual([item.state for item in queue], ["COMPLETED", "ACTIVE", "QUEUED"])

    def test_duplicate_reuses_first_request_and_content_swap_is_rejected(self) -> None:
        incoming = envelope()
        first = self.store.register_request(incoming, ingress_sha256=HASH_A, created_at_ms=1_100)
        duplicate = self.store.register_request(incoming, ingress_sha256=HASH_A, created_at_ms=1_900)
        self.assertTrue(duplicate.duplicate)
        self.assertFalse(duplicate.created_by_this_call)
        self.assertEqual(duplicate.entry, first.entry)
        self.assertEqual(duplicate.entry.created_at_ms, 1_100)
        self.assertEqual(self.store.count_journal_entries(), 1)
        with self.assertRaises(StoreConflictError):
            self.store.register_request(incoming, ingress_sha256=HASH_B, created_at_ms=2_000)

    def test_different_sessions_have_independent_active_requests(self) -> None:
        first = self.store.register_request(envelope("message_001"), ingress_sha256=HASH_A, created_at_ms=1_100)
        other = self.store.register_request(
            envelope("message_101", conversation_ref="conversation_002"),
            ingress_sha256=HASH_B,
            created_at_ms=1_200,
        )
        self.assertEqual(first.queue_state, "ACTIVE")
        self.assertEqual(other.queue_state, "ACTIVE")
        self.assertNotEqual(first.entry.session_scope_hash, other.entry.session_scope_hash)

    def test_two_connections_concurrently_enqueue_one_active_and_one_queued(self) -> None:
        other = GatewayStateStore.open(self.path, now_ms=950)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def register(store: GatewayStateStore, incoming: InboundEnvelope, digest: str) -> None:
            try:
                barrier.wait(timeout=5)
                results.append(store.register_request(incoming, ingress_sha256=digest, created_at_ms=1_100))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = (
            threading.Thread(target=register, args=(self.store, envelope("message_001"), HASH_A)),
            threading.Thread(target=register, args=(other, envelope("message_002"), HASH_B)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other.close()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(item.queue_state for item in results), ["ACTIVE", "QUEUED"])
        self.assertEqual(sorted(item.queue_sequence for item in results), [1, 2])

    def test_fault_during_queue_insert_rolls_back_journal_and_actor(self) -> None:
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_session_queue
            BEFORE INSERT ON session_queue
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.register_request(envelope(), ingress_sha256=HASH_A, created_at_ms=1_100)
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_session_queue")  # noqa: SLF001
        self.assertEqual(self.store.count_journal_entries(), 0)
        actors = self.store._connection.execute("SELECT count(*) FROM session_actor").fetchone()[0]  # noqa: SLF001
        self.assertEqual(actors, 0)

    def test_inbound_payload_insert_fault_rolls_back_request_registration(self) -> None:
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_request_inbound
            BEFORE INSERT ON request_inbound_payload
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.register_request(envelope(), ingress_sha256=HASH_A, created_at_ms=1_100)
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_request_inbound")  # noqa: SLF001
        self.assertEqual(self.store.count_journal_entries(), 0)
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM request_inbound_payload").fetchone()[0],  # noqa: SLF001
            0,
        )
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM session_actor").fetchone()[0],  # noqa: SLF001
            0,
        )

    def test_journal_semantic_tamper_fails_health(self) -> None:
        self.store.register_request(envelope(), ingress_sha256=HASH_A, created_at_ms=1_100)
        self.store._connection.execute(  # noqa: SLF001 - deliberate corruption injection
            "UPDATE request_journal SET ingress_sha256 = ?",
            (HASH_B,),
        )
        self.assertFalse(self.store.health_check(now_ms=2_000).healthy)

    def test_inbound_payload_tamper_fails_health(self) -> None:
        self.store.register_request(envelope(), ingress_sha256=HASH_A, created_at_ms=1_100)
        self.store._connection.execute(  # noqa: SLF001 - deliberate semantic tamper
            "UPDATE request_inbound_payload SET envelope_sha256 = ?",
            (HASH_B,),
        )
        self.assertFalse(self.store.health_check(now_ms=2_000, full=True).healthy)


class RequestJournalMigrationTests(unittest.TestCase):
    def test_version_one_database_migrates_in_place_to_request_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gateway.sqlite3"
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.execute("BEGIN EXCLUSIVE")
                for statement in store_module._MIGRATION_V1_STATEMENTS:  # noqa: SLF001
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                    (
                        1,
                        store_module._MIGRATION_V1_ID,  # noqa: SLF001
                        store_module._MIGRATION_DIGESTS[1],  # noqa: SLF001
                        500,
                    ),
                )
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 1")
                connection.execute("COMMIT")
            finally:
                connection.close()
            store = GatewayStateStore.open(path, now_ms=1_000)
            try:
                self.assertEqual(store.health_check(now_ms=1_100, full=True).schema_version, STORE_SCHEMA_VERSION)
                self.assertEqual(store.count_journal_entries(), 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
