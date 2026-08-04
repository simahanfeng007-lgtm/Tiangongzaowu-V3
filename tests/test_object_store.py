import sqlite3
import tempfile
import unittest
from pathlib import Path

from total_gateway.object_store import (
    ContentAddressedObjectStore,
    ObjectStoreConflict,
    ObjectStoreCorruption,
    expected_object_store_schema_sha256,
)


SCOPE_A = "a" * 64
HASH_B = "b" * 64


class ContentAddressedObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "objects"
        self.store = ContentAddressedObjectStore.open(self.root, now_ms=1_000)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def put(self, data=b"immutable document", **overrides):
        values = {
            "kind": "artifact",
            "tenant_id": "tenant_001",
            "link_account_id": "wechat_001",
            "conversation_scope_hash": SCOPE_A,
            "created_at_ms": 1_100,
        }
        values.update(overrides)
        return self.store.put_bytes(data, **values)

    def test_put_read_and_duplicate_are_content_addressed_and_verified(self) -> None:
        first = self.put()
        self.assertTrue(first.created_by_this_call)
        self.assertTrue(first.content_created_by_this_call)
        self.assertEqual(self.store.read_bytes(first.reference.object_id), b"immutable document")
        duplicate = self.put(created_at_ms=1_200)
        self.assertFalse(duplicate.created_by_this_call)
        self.assertEqual(duplicate.reference, first.reference)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_same_content_different_scope_has_distinct_reference_but_one_blob(self) -> None:
        first = self.put()
        second = self.put(tenant_id="tenant_002")
        self.assertNotEqual(first.reference.object_id, second.reference.object_id)
        self.assertEqual(first.reference.content_object_id, second.reference.content_object_id)
        blobs = [path for path in (self.root / "blobs").rglob("*") if path.is_file()]
        self.assertEqual(len(blobs), 1)

    def test_revisions_are_contiguous_and_cannot_change_content(self) -> None:
        first = self.put(b"revision one")
        revision, created = self.store.register_revision(
            "artifact_001",
            1,
            first.reference.object_id,
            manifest_sha256=HASH_B,
            created_at_ms=1_200,
        )
        self.assertTrue(created)
        duplicate, created = self.store.register_revision(
            "artifact_001",
            1,
            first.reference.object_id,
            manifest_sha256=HASH_B,
            created_at_ms=1_200,
        )
        self.assertFalse(created)
        self.assertEqual(duplicate, revision)
        second = self.put(b"revision two", created_at_ms=1_300)
        with self.assertRaises(ObjectStoreConflict):
            self.store.register_revision(
                "artifact_001",
                3,
                second.reference.object_id,
                manifest_sha256=HASH_B,
                created_at_ms=1_400,
            )
        with self.assertRaises(ObjectStoreConflict):
            self.store.register_revision(
                "artifact_001",
                1,
                second.reference.object_id,
                manifest_sha256=HASH_B,
                created_at_ms=1_400,
            )

    def test_metadata_failure_never_creates_reference_and_retry_adopts_verified_blob(self) -> None:
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_object_reference
            BEFORE INSERT ON object_references
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.put(b"crash boundary")
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_object_reference")  # noqa: SLF001
        refs = self.store._connection.execute("SELECT count(*) FROM object_references").fetchone()[0]  # noqa: SLF001
        self.assertEqual(refs, 0)
        retried = self.put(b"crash boundary", created_at_ms=1_200)
        self.assertTrue(retried.created_by_this_call)
        self.assertFalse(retried.content_created_by_this_call)

    def test_stream_limit_failure_cleans_temporary_file(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put_stream(
                (b"1234", b"5678"),
                kind="attachment",
                tenant_id="tenant_001",
                link_account_id="wechat_001",
                conversation_scope_hash=SCOPE_A,
                created_at_ms=1_100,
                max_bytes=7,
            )
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_blob_tamper_is_detected_on_read_and_full_health(self) -> None:
        stored = self.put()
        blob = next(path for path in (self.root / "blobs").rglob("*") if path.is_file())
        blob.write_bytes(b"tampered")
        with self.assertRaises(ObjectStoreCorruption):
            self.store.read_bytes(stored.reference.object_id)
        self.assertFalse(self.store.health_check(now_ms=2_000, full=True).healthy)

    def test_schema_and_writable_health_are_reproducible(self) -> None:
        health = self.store.health_check(now_ms=1_100, full=True)
        self.assertTrue(health.healthy)
        self.assertEqual(health.schema_sha256, expected_object_store_schema_sha256())


if __name__ == "__main__":
    unittest.main()
