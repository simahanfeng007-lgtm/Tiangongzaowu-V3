from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "src" / "life_service" / "store.py"
SCHEMA = ROOT / "src" / "life_service" / "store_schema.py"
GATE = ROOT / ".github" / "workflows" / "architecture-gate.yml"

spec = importlib.util.spec_from_file_location("p17_m3_02_store_schema", SCHEMA)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load store_schema.py")
schema = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = schema
spec.loader.exec_module(schema)


class MarkerStoreError(RuntimeError):
    pass


class LifeStoreM302Tests(unittest.TestCase):
    def test_schema_module_is_single_definition_authority(self):
        schema_text = SCHEMA.read_text(encoding="utf-8")
        store_text = STORE.read_text(encoding="utf-8")
        store_tree = ast.parse(store_text)

        self.assertIn("CREATE TABLE schema_migrations", schema_text)
        self.assertIn("_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID", schema_text)
        self.assertIn("def initialize_life_shadow_schema", schema_text)
        self.assertIn("def migrate_life_shadow_schema", schema_text)
        self.assertNotIn("LifeShadowStoreError", schema_text)
        self.assertNotIn("CREATE TABLE schema_migrations", store_text)

        assigned = set()
        for node in store_tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
        self.assertNotIn("SHADOW_STORE_SCHEMA_VERSION", assigned)
        self.assertNotIn("SHADOW_STORE_APPLICATION_ID", assigned)
        self.assertNotIn("_SCHEMA_SQL", assigned)
        self.assertNotIn("_EXPECTED_TABLES", assigned)
        self.assertFalse(any(name.startswith("_P17_") for name in assigned))

        schema_imports = [
            node
            for node in store_tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "store_schema" and node.level == 1
        ]
        self.assertEqual(len(schema_imports), 1)
        imported = {alias.name for alias in schema_imports[0].names}
        self.assertIn("SHADOW_STORE_SCHEMA_VERSION", imported)
        self.assertIn("SHADOW_STORE_APPLICATION_ID", imported)
        self.assertIn("_P1_SCHEMA_SQL", imported)
        self.assertIn("_P17_MEMORY_WORLD_CANDIDATE_SHA256", imported)
        self.assertIn("_EXPECTED_TABLES", imported)

    def test_store_keeps_only_thin_schema_facades_and_error_authority(self):
        text = STORE.read_text(encoding="utf-8")
        tree = ast.parse(text)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LifeShadowStore")
        methods = {
            node.name: node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name in {"_initialize", "_migrate"}
        }
        self.assertEqual(set(methods), {"_initialize", "_migrate"})
        init_source = ast.get_source_segment(text, methods["_initialize"]) or ""
        migrate_source = ast.get_source_segment(text, methods["_migrate"]) or ""
        self.assertIn("initialize_life_shadow_schema(connection, now_ms=now_ms)", init_source)
        self.assertNotIn("BEGIN IMMEDIATE", init_source)
        self.assertIn("migrate_life_shadow_schema(", migrate_source)
        self.assertIn("error_factory=LifeShadowStoreError", migrate_source)
        self.assertNotIn("PRAGMA table_list", migrate_source)

    def test_initialize_preserves_version_identity_and_migration_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sqlite3"
            connection = sqlite3.connect(path, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                schema.initialize_life_shadow_schema(connection, now_ms=101)
                self.assertEqual(
                    int(connection.execute("PRAGMA application_id").fetchone()[0]),
                    schema.SHADOW_STORE_APPLICATION_ID,
                )
                self.assertEqual(
                    int(connection.execute("PRAGMA user_version").fetchone()[0]),
                    schema.SHADOW_STORE_SCHEMA_VERSION,
                )
                versions = [
                    int(row[0])
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                self.assertEqual(versions, list(range(1, 18)))
                metadata = dict(
                    connection.execute("SELECT key, value FROM schema_metadata").fetchall()
                )
                self.assertEqual(metadata["purpose"], "life-shadow-only")
                self.assertEqual(metadata["schema_sha256"], schema._SCHEMA_SHA256)
                tables = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_list").fetchall()
                    if str(row["type"]) == "table" and not str(row["name"]).startswith("sqlite_")
                }
                self.assertEqual(tables, set(schema._EXPECTED_TABLES))
            finally:
                connection.close()

    def test_migrate_preserves_full_v1_to_v17_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "migrate.sqlite3"
            connection = sqlite3.connect(path, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.executescript(schema._P1_SCHEMA_SQL)
                connection.execute(
                    "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (1, ?, ?, ?)",
                    ("p1-initial-shadow-schema", schema._P1_SCHEMA_SHA256, 1),
                )
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES ('purpose', 'life-shadow-only')"
                )
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES ('schema_sha256', ?)",
                    (schema._P1_SCHEMA_SHA256,),
                )
                connection.execute(f"PRAGMA application_id={schema.SHADOW_STORE_APPLICATION_ID}")
                connection.execute("PRAGMA user_version=1")

                schema.migrate_life_shadow_schema(
                    connection,
                    now_ms=202,
                    error_factory=MarkerStoreError,
                )

                self.assertEqual(int(connection.execute("PRAGMA user_version").fetchone()[0]), 17)
                rows = connection.execute(
                    "SELECT version, migration_id FROM schema_migrations ORDER BY version"
                ).fetchall()
                self.assertEqual([int(row[0]) for row in rows], list(range(1, 18)))
                self.assertEqual(str(rows[-1][1]), schema._P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID)
                metadata = dict(
                    connection.execute("SELECT key, value FROM schema_metadata").fetchall()
                )
                self.assertEqual(metadata["schema_sha256"], schema._SCHEMA_SHA256)
            finally:
                connection.close()

    def test_migration_error_type_remains_injected_by_store_facade(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = sqlite3.connect(Path(tmp) / "invalid.sqlite3", isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA application_id=1")
                connection.execute("PRAGMA user_version=1")
                with self.assertRaisesRegex(MarkerStoreError, "application identity is invalid"):
                    schema.migrate_life_shadow_schema(
                        connection,
                        now_ms=1,
                        error_factory=MarkerStoreError,
                    )
            finally:
                connection.close()

    def test_architecture_gate_covers_m3_02(self):
        gate = GATE.read_text(encoding="utf-8")
        self.assertIn("Run P17 M3-02 life store schema regression", gate)
        self.assertIn("python tests/test_life_store_p17_m3_02.py -v", gate)
        self.assertIn("src/life_service/store_schema.py", gate)


if __name__ == "__main__":
    unittest.main()
