from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    target.write_text(
        text.replace(old, new, 1),
        encoding="utf-8",
        newline="\n",
    )


replace_once(
    "tests/gateway_store_migration_support.py",
    '''    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == 29:
''',
    '''    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == 30:
        # v30 additive: P7B.2 limited composition activation registration.
        # Remove its indexes and row table before removing the P19 plan and
        # RegistrySnapshot authorities referenced by its foreign keys.
        connection.execute(
            "DROP INDEX IF EXISTS composition_activation_registration_expiry_idx"
        )
        connection.execute(
            "DROP INDEX IF EXISTS composition_activation_registration_lineage_idx"
        )
        connection.execute(
            "DROP TABLE IF EXISTS composition_activation_registration"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 30")
        connection.execute("PRAGMA user_version = 29")
        version = 29
    if version == 29:
''',
    "downgrade v30 to v29",
)

replace_once(
    "tests/test_p19_m1_store.py",
    '''        connection = sqlite3.connect(self.path)
        connection.execute(
            "DROP INDEX IF EXISTS repair_execution_binding_attempt_idx"
        )
''',
    '''        connection = sqlite3.connect(self.path)
        connection.execute(
            "DROP INDEX IF EXISTS composition_activation_registration_expiry_idx"
        )
        connection.execute(
            "DROP INDEX IF EXISTS composition_activation_registration_lineage_idx"
        )
        connection.execute(
            "DROP TABLE IF EXISTS composition_activation_registration"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 30")
        connection.execute(
            "DROP INDEX IF EXISTS repair_execution_binding_attempt_idx"
        )
''',
    "strip v30 before v22 fixture",
)

replace_once(
    "tests/test_p19_m1_store.py",
    "self.assertEqual(store.health_check(full=True, now_ms=950).schema_version, 29)",
    "self.assertEqual(store.health_check(full=True, now_ms=950).schema_version, 30)",
    "fresh schema expectation",
)
replace_once(
    "tests/test_p19_m1_store.py",
    "self.assertEqual(health.schema_version, 29)",
    "self.assertEqual(health.schema_version, 30)",
    "upgraded schema expectation",
)
replace_once(
    "tests/test_p19_m1_store.py",
    "# Upgrade path: opening with the current binary migrates 22 -> 23.",
    "# Upgrade path: opening with the current binary migrates 22 -> 30.",
    "migration comment",
)
replace_once(
    "tests/test_p19_m3_write_evidence_v2.py",
    "self.assertEqual(self.store.health_check(now_ms=2_100).schema_version, 29)",
    "self.assertEqual(self.store.health_check(now_ms=2_100).schema_version, 30)",
    "write evidence schema expectation",
)

print("P7B.2 schema-v30 compatibility fixtures updated")
