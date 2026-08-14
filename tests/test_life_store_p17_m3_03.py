from __future__ import annotations

import ast
import dataclasses
import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TARGET_START = "_protected_payload_record_from_row"
TARGET_END = "list_memory_relations"
STATIC_TARGETS = {"_protected_payload_record_from_row", "_term_digests"}


class LifeStoreM303Tests(unittest.TestCase):
    @staticmethod
    def _target_names() -> tuple[str, ...]:
        source = (SRC / "life_service" / "store_memory_repository.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        repo = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LifeMemoryRepository")
        return tuple(n.name for n in repo.body if isinstance(n, ast.FunctionDef) and n.name != "__init__")

    def test_memory_repository_is_single_connection_domain_boundary(self) -> None:
        from life_service.store_memory_repository import LifeMemoryRepository
        connection = sqlite3.connect(":memory:")
        try:
            repository = LifeMemoryRepository(connection)
            self.assertIs(repository._connection, connection)
        finally:
            connection.close()
        source = (SRC / "life_service" / "store_memory_repository.py").read_text(encoding="utf-8")
        self.assertNotIn("sqlite3.connect", source)
        self.assertNotIn("open_life_shadow_sqlite", source)
        self.assertNotIn("SHADOW_STORE_SCHEMA_VERSION", source)
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("class LifeShadowStore", source)

    def test_store_preserves_memory_method_signatures(self) -> None:
        from life_service.store import LifeShadowStore
        from life_service.store_memory_repository import LifeMemoryRepository
        for name in self._target_names():
            self.assertEqual(inspect.signature(getattr(LifeShadowStore, name)), inspect.signature(getattr(LifeMemoryRepository, name)), name)

    def test_store_memory_methods_are_thin_facades(self) -> None:
        source = (SRC / "life_service" / "store.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        store = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LifeShadowStore")
        methods = {n.name: n for n in store.body if isinstance(n, ast.FunctionDef)}
        for name in self._target_names():
            segment = ast.get_source_segment(source, methods[name]) or ""
            self.assertNotIn("BEGIN IMMEDIATE", segment, name)
            self.assertIn("LifeMemoryRepository" if name in STATIC_TARGETS else "_memory_repository", segment, name)

    def test_repository_method_set_is_memory_transaction_cluster(self) -> None:
        names = self._target_names()
        self.assertEqual(names[0], TARGET_START)
        self.assertEqual(names[-1], "delete_memory")
        self.assertIn(TARGET_END, names)
        self.assertIn("put_live_memory_assertion", names)
        self.assertIn("put_memory_derivation", names)
        self.assertIn("put_memory_invalidation", names)
        self.assertIn("put_world_candidate_outbox", names)
        self.assertIn("ack_memory_outbox", names)

    def test_repository_internal_private_calls_do_not_escape(self) -> None:
        source = (SRC / "life_service" / "store_memory_repository.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        repo = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LifeMemoryRepository")
        method_names = {n.name for n in repo.body if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(repo):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr.startswith("_"):
                self.assertTrue(node.attr == "_connection" or node.attr in method_names, node.attr)

    def test_store_and_repository_share_error_identity(self) -> None:
        from life_service.store import LifeShadowStoreError
        from life_service.store_contract_support import LifeShadowStoreError as SupportError
        self.assertIs(LifeShadowStoreError, SupportError)

    def test_memory_support_records_preserve_frozen_slotted_dataclasses(self) -> None:
        from life_service.store_contract_support import MemoryDeletionResult, ProtectedPayloadRecord
        self.assertTrue(dataclasses.is_dataclass(ProtectedPayloadRecord))
        self.assertTrue(dataclasses.is_dataclass(MemoryDeletionResult))
        self.assertIn("__slots__", ProtectedPayloadRecord.__dict__)
        self.assertIn("__slots__", MemoryDeletionResult.__dict__)
        record = ProtectedPayloadRecord(
            payload_id="payload",
            life_id="life",
            privacy_scope="private",
            ciphertext_sha256="0" * 64,
            created_at_ms=1,
            key_available=True,
            key_destroyed_at_ms=None,
        )
        self.assertEqual(record.payload_id, "payload")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            record.payload_id = "other"  # type: ignore[misc]
        source = (SRC / "life_service" / "store_contract_support.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("@dataclass(frozen=True, slots=True)"), 2)

    def test_open_wires_repository_to_same_connection(self) -> None:
        from life_service.store import LifeShadowStore
        with tempfile.TemporaryDirectory() as tmp:
            store = LifeShadowStore.open(Path(tmp) / "life.shadow.sqlite3", create=True, now_ms=1)
            try:
                self.assertIs(store._memory_repository._connection, store._connection)
                self.assertEqual(store.health()["schema_version"], 17)
            finally:
                store.close()

    def test_architecture_gate_covers_m3_03(self) -> None:
        gate = (ROOT / ".github" / "workflows" / "architecture-gate.yml").read_text(encoding="utf-8")
        self.assertEqual(gate.count("python tests/test_life_store_p17_m3_03.py -v"), 1)
        self.assertIn("src/life_service/store_memory_repository.py", gate)
        self.assertIn("src/life_service/store_contract_support.py", gate)
        self.assertIn("cryptography==48.0.1", gate)
        self.assertIn("pydantic==2.13.4", gate)


if __name__ == "__main__":
    unittest.main()
