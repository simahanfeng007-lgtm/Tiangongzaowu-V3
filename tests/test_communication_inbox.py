import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import InboundEnvelope, InboundScope, canonical_sha256, derive_inbound_scope_keys
from communication_service.inbox import (
    AckConflictError,
    CommunicationInbox,
    CursorConflictError,
    INBOX_APPLICATION_ID,
    INBOX_SCHEMA_VERSION,
    InboxConflictError,
    InboxCorruptionError,
    InboxIngress,
    cursor_token_sha256,
    derive_cursor_stream_key,
    expected_inbox_schema_sha256,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def envelope(message_ref: str = "message_001", *, text: str = "hello") -> InboundEnvelope:
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
        text=text,
    )


def ingress(
    message_ref: str = "message_001",
    *,
    text: str = "hello",
    previous_cursor_sha256: str | None = None,
    next_cursor_token: str = "cursor_001",
) -> InboxIngress:
    incoming = envelope(message_ref, text=text)
    candidate = InboxIngress(
        ingress_id=incoming.inbound_id,
        envelope=incoming,
        raw_payload_object_id=f"raw_{message_ref}",
        raw_payload_sha256=canonical_sha256({"message_ref": message_ref, "text": text}),
        raw_payload_size_bytes=max(1, len(text.encode("utf-8"))),
        cursor_stream_key=derive_cursor_stream_key(
            incoming.channel,
            incoming.tenant_id,
            incoming.link_account_id,
        ),
        previous_cursor_sha256=previous_cursor_sha256,
        next_cursor_token=next_cursor_token,
        next_cursor_sha256=cursor_token_sha256(next_cursor_token),
        captured_at_ms=1_100,
        ingress_sha256=HASH_B,
    )
    return candidate.with_computed_sha256()


class CommunicationInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "communication-inbox.sqlite3"
        self.inbox = CommunicationInbox.open(self.path, now_ms=1_000)

    def tearDown(self) -> None:
        self.inbox.close()
        self.temporary.cleanup()

    def test_migration_pragmas_schema_and_health_are_verified(self) -> None:
        health = self.inbox.health_check(now_ms=2_000, full=True)
        self.assertTrue(health.healthy)
        self.assertEqual(health.schema_sha256, expected_inbox_schema_sha256())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], INBOX_APPLICATION_ID)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], INBOX_SCHEMA_VERSION)
        finally:
            connection.close()

    def test_persist_precedes_ack_permit_and_cursor_then_ack_is_idempotent(self) -> None:
        incoming = ingress()
        result = self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=1_200)
        self.assertTrue(result.persisted_by_this_call)
        self.assertFalse(result.duplicate)
        self.assertTrue(result.permit.has_valid_sha256())
        self.assertNotIn(incoming.next_cursor_token, result.permit.model_dump_json())
        cursor = self.inbox.get_cursor(incoming.cursor_stream_key)
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.cursor_token, incoming.next_cursor_token)
        self.assertEqual(cursor.revision, 1)
        self.assertEqual(self.inbox.count_records(), 1)

        self.assertTrue(
            self.inbox.mark_acknowledged(
                result.permit.permit_id,
                platform_receipt_sha256=HASH_A,
                acknowledged_at_ms=1_300,
            )
        )
        self.assertFalse(
            self.inbox.mark_acknowledged(
                result.permit.permit_id,
                platform_receipt_sha256=HASH_A,
                acknowledged_at_ms=1_400,
            )
        )
        with self.assertRaises(AckConflictError):
            self.inbox.mark_acknowledged(
                result.permit.permit_id,
                platform_receipt_sha256=HASH_B,
                acknowledged_at_ms=1_500,
            )

    def test_duplicate_returns_first_permit_but_identity_content_swap_is_rejected(self) -> None:
        incoming = ingress()
        first = self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=1_200)
        duplicate = self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=1_300)
        self.assertFalse(duplicate.persisted_by_this_call)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.permit, first.permit)
        self.assertEqual(self.inbox.count_records(), 1)
        with self.assertRaises(InboxConflictError):
            self.inbox.persist_and_advance_cursor(
                ingress(text="changed"),
                persisted_at_ms=1_400,
            )

    def test_cursor_compare_and_set_rejects_stale_followup_without_partial_record(self) -> None:
        first = ingress(next_cursor_token="cursor_001")
        self.inbox.persist_and_advance_cursor(first, persisted_at_ms=1_200)
        second = ingress(
            "message_002",
            previous_cursor_sha256=first.next_cursor_sha256,
            next_cursor_token="cursor_002",
        )
        self.inbox.persist_and_advance_cursor(second, persisted_at_ms=1_300)
        stale = ingress(
            "message_003",
            previous_cursor_sha256=first.next_cursor_sha256,
            next_cursor_token="cursor_003",
        )
        with self.assertRaises(CursorConflictError):
            self.inbox.persist_and_advance_cursor(stale, persisted_at_ms=1_400)
        self.assertEqual(self.inbox.count_records(), 2)
        cursor = self.inbox.get_cursor(first.cursor_stream_key)
        self.assertEqual(cursor.revision, 2)
        self.assertEqual(cursor.cursor_sha256, second.next_cursor_sha256)

    def test_fault_before_cursor_commit_rolls_back_record_cursor_and_ack_permit(self) -> None:
        self.inbox._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_cursor_insert
            BEFORE INSERT ON cursor_state
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        incoming = ingress()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=1_200)
        finally:
            self.inbox._connection.execute("DROP TRIGGER test_abort_cursor_insert")  # noqa: SLF001
        self.assertEqual(self.inbox.count_records(), 0)
        self.assertIsNone(self.inbox.get_cursor(incoming.cursor_stream_key))
        permits = self.inbox._connection.execute("SELECT count(*) FROM ack_permits").fetchone()[0]  # noqa: SLF001
        self.assertEqual(permits, 0)

    def test_reopen_preserves_cursor_dedup_and_ack_permit(self) -> None:
        incoming = ingress()
        first = self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=1_200)
        self.inbox.close()
        self.inbox = CommunicationInbox.open(self.path, now_ms=2_000)
        duplicate = self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=2_100)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.permit, first.permit)
        self.assertEqual(self.inbox.get_cursor(incoming.cursor_stream_key).revision, 1)

    def test_two_connections_allow_only_one_first_cursor_commit(self) -> None:
        other = CommunicationInbox.open(self.path, now_ms=1_100)
        barrier = threading.Barrier(2)
        successes = []
        errors = []

        def write(store: CommunicationInbox, candidate: InboxIngress) -> None:
            try:
                barrier.wait(timeout=5)
                successes.append(store.persist_and_advance_cursor(candidate, persisted_at_ms=1_200))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = (
            threading.Thread(target=write, args=(self.inbox, ingress("message_001", next_cursor_token="one"))),
            threading.Thread(target=write, args=(other, ingress("message_002", next_cursor_token="two"))),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other.close()
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CursorConflictError)
        self.assertEqual(self.inbox.count_records(), 1)

    def test_semantic_tamper_fails_health_even_when_sqlite_is_valid(self) -> None:
        self.inbox.persist_and_advance_cursor(ingress(), persisted_at_ms=1_200)
        self.inbox._connection.execute(  # noqa: SLF001 - deliberate corruption injection
            "UPDATE inbox_records SET raw_payload_object_id = 'tampered'"
        )
        health = self.inbox.health_check(now_ms=2_000)
        self.assertFalse(health.healthy)
        self.assertEqual(health.reason_code, "inbox.check.failed")


class CommunicationInboxCorruptionTests(unittest.TestCase):
    def test_random_database_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "communication-inbox.sqlite3"
            original = b"not-a-sqlite-database"
            path.write_bytes(original)
            with self.assertRaises(InboxCorruptionError):
                CommunicationInbox.open(path, now_ms=1_000)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
