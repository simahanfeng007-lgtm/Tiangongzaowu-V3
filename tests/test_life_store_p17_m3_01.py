from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from life_service.store_connection import open_life_shadow_sqlite

CONNECTION = ROOT / "src" / "life_service" / "store_connection.py"
STORE = ROOT / "src" / "life_service" / "store.py"
GATE = ROOT / ".github" / "workflows" / "architecture-gate.yml"


class MarkerStoreError(RuntimeError):
    pass


class LifeStoreM301Tests(unittest.TestCase):
    def test_connection_boundary_is_sqlite_lifecycle_only(self):
        text = CONNECTION.read_text(encoding="utf-8")
        tree = ast.parse(text)
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("LifeStoreSchemaLifecycle", classes)
        self.assertIn("OpenedLifeShadowSqlite", classes)
        self.assertIn("open_life_shadow_sqlite", functions)
        self.assertNotIn("LifeShadowStore", classes)
        for forbidden in ("_SCHEMA_SQL", "CREATE TABLE", "BEGIN IMMEDIATE", "COMMIT", "contracts", "LifeShadowStoreError"):
            self.assertNotIn(forbidden, text)

    def test_store_open_delegates_but_schema_and_health_stay_owned(self):
        text = STORE.read_text(encoding="utf-8")
        self.assertIn("from .store_connection import open_life_shadow_sqlite", text)
        start = text.index("    @classmethod\n    def open(")
        end = text.index("    @staticmethod\n    def _initialize", start)
        body = text[start:end]
        for expected in (
            "open_life_shadow_sqlite(",
            "error_factory=LifeShadowStoreError",
            "initialize=cls._initialize",
            "migrate=cls._migrate",
            "store.health()",
            "opened.connection.close()",
        ):
            self.assertIn(expected, body)
        self.assertNotIn("sqlite3.connect(", body)
        self.assertIn("def _initialize(connection: sqlite3.Connection", text)
        self.assertIn("def _migrate(connection: sqlite3.Connection", text)
        self.assertIn('connection.execute("BEGIN IMMEDIATE")', text)

    def test_create_and_reopen_callback_order_and_pragmas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "life.shadow.sqlite3"
            calls: list[tuple[str, int]] = []

            def initialize(connection, *, now_ms: int) -> None:
                calls.append(("initialize", now_ms))
                connection.execute("CREATE TABLE probe(value INTEGER) STRICT")

            def migrate(connection, *, now_ms: int) -> None:
                calls.append(("migrate", now_ms))
                connection.execute("SELECT value FROM probe").fetchall()

            opened = open_life_shadow_sqlite(
                path, create=True, now_ms=11, error_factory=MarkerStoreError,
                initialize=initialize, migrate=migrate,
            )
            try:
                self.assertFalse(opened.existed)
                self.assertEqual(int(opened.connection.execute("PRAGMA foreign_keys").fetchone()[0]), 1)
                self.assertEqual(int(opened.connection.execute("PRAGMA trusted_schema").fetchone()[0]), 0)
                self.assertEqual(str(opened.connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal")
                self.assertEqual(int(opened.connection.execute("PRAGMA synchronous").fetchone()[0]), 2)
            finally:
                opened.connection.close()

            reopened = open_life_shadow_sqlite(
                path, create=False, now_ms=12, error_factory=MarkerStoreError,
                initialize=initialize, migrate=migrate,
            )
            try:
                self.assertTrue(reopened.existed)
            finally:
                reopened.connection.close()
            self.assertEqual(calls, [("initialize", 11), ("migrate", 12)])

    def test_error_factory_preserves_store_owned_exception_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            callbacks = dict(
                error_factory=MarkerStoreError,
                initialize=lambda connection, *, now_ms: None,
                migrate=lambda connection, *, now_ms: None,
            )
            with self.assertRaisesRegex(MarkerStoreError, "timestamp is invalid"):
                open_life_shadow_sqlite(root / "life.shadow.sqlite3", create=True, now_ms=True, **callbacks)
            with self.assertRaisesRegex(MarkerStoreError, "does not exist"):
                open_life_shadow_sqlite(root / "life.shadow.sqlite3", create=False, now_ms=1, **callbacks)
            with self.assertRaisesRegex(MarkerStoreError, "must end with"):
                open_life_shadow_sqlite(root / "life.sqlite3", create=True, now_ms=1, **callbacks)

    def test_architecture_gate_covers_m3_01(self):
        gate = GATE.read_text(encoding="utf-8")
        self.assertIn("Run P17 M3-01 life store connection regression", gate)
        self.assertIn("python tests/test_life_store_p17_m3_01.py -v", gate)
        self.assertIn("src/life_service/store_connection.py", gate)
        self.assertIn("src/life_service/store.py", gate)


if __name__ == "__main__":
    unittest.main()
