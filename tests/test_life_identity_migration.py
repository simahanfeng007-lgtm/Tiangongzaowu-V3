from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import base64
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "life-service" / "runtime314"))

from life_service.complete_core import CompleteLifeSystem, LifeCoreError, LifeIdentityManager
try:
    import life_core
except ModuleNotFoundError as exc:
    if exc.name != "life_core":
        raise
    # The redistributable source package intentionally omits the legacy frozen
    # life_core module.  Exercise the same migration contract against the
    # source-owned implementation used by the single-process product runtime.
    from life_service import complete_core as life_core
from life_service.embedded_runtime import EmbeddedLifeError, EmbeddedLifeRuntime
from life_service.identity_migration import (
    IDENTITY_SCHEMA,
    migrate_legacy_identities,
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_v1(root: Path, life_id: str, born_at: str, *, tamper: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    lineage_id = f"lineage_{life_id.removeprefix('org_')}"
    immutable = {"organism_id": life_id, "lineage_id": lineage_id, "born_at": born_at}
    identity = {
        "schema": "tiangong.organism.identity.v1",
        **immutable,
        "identity_hash": hashlib.sha256(canonical(immutable)).hexdigest(),
    }
    if tamper:
        identity["born_at"] = "2022-02-02T00:00:00.000Z"
    path = root / "identity.json"
    path.write_text(json.dumps(identity), encoding="utf-8")
    (root / "journal").mkdir(exist_ok=True)
    (root / "journal" / "life_events.jsonl").write_text('{"event":"preserve-me"}\n', encoding="utf-8")
    (root / "journal" / ".life-journal.lock").write_text("volatile", encoding="utf-8")
    return path


class LifeIdentityMigrationTests(unittest.TestCase):
    def environment(self, root: Path) -> dict[str, str]:
        return {
            "TIANGONG_LIFE_DATA_ROOT": str(root / "data"),
            "TIANGONG_EXECUTION_RUNTIME_ROOT": str(root / "life-kernel"),
            "TIANGONG_EXECUTION_LIFE_ROOT": str(root / "life-transaction"),
            "TIANGONG_LIFE_DEVICE_ID": "migration-test-device",
        }

    def test_create_identity_succeeds_in_a_fresh_windows_style_data_root(self) -> None:
        """A blank data root must bootstrap an identity without a preseeded file."""
        with tempfile.TemporaryDirectory() as temporary:
            # The final file remains below the classic Windows limit, while a
            # verbose temp filename would exceed it.
            root = Path(temporary) / ("d" * 120)
            system = CompleteLifeSystem(root, device_id="fresh-bootstrap-test")

            created = system.create_identity("fresh-life")

            identity_path = root / "lives" / created["life_id"] / "identity" / "life_identity.json"
            self.assertTrue(identity_path.is_file())
            self.assertEqual(
                json.loads((identity_path.parent / "soul.json").read_text(encoding="utf-8"))["name"],
                "fresh-life",
            )

    def test_identity_list_marks_one_corrupt_binding_without_hiding_valid_lives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            system = CompleteLifeSystem(root, device_id="identity-list-test")
            first = system.create_identity("first")
            second = system.create_identity("second")
            corrupt_signature = (
                root
                / "lives"
                / first["life_id"]
                / "identity"
                / "life_identity.sig"
            )
            corrupt_signature.write_text("not-a-signature\n", encoding="ascii")

            rows = {
                row["life_id"]: row
                for row in system.identities.list()
            }

            self.assertEqual(set(rows), {first["life_id"], second["life_id"]})
            self.assertEqual(rows[first["life_id"]]["integrity"], "invalid")
            self.assertTrue(rows[first["life_id"]]["integrity_error"])
            self.assertFalse(rows[first["life_id"]]["active"])
            self.assertEqual(rows[second["life_id"]]["integrity"], "valid")
            self.assertEqual(rows[second["life_id"]]["soul_integrity"], "valid")
            self.assertTrue(rows[second["life_id"]]["soul_intro"])
            self.assertLessEqual(len(rows[second["life_id"]]["soul_intro"]), 97)
            self.assertGreaterEqual(rows[second["life_id"]]["soul_tone"], 0)
            self.assertLessEqual(rows[second["life_id"]]["soul_tone"], 359)
            self.assertTrue(rows[second["life_id"]]["active"])

    def test_delete_removes_only_a_managed_dormant_life_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            system = CompleteLifeSystem(root, device_id="identity-delete-test")
            dormant = system.create_identity("dormant")
            active = system.create_identity("active")
            dormant_root = root / "lives" / dormant["life_id"]
            nested = dormant_root / "attachments" / "nested" / "payload.bin"
            nested.parent.mkdir(parents=True, exist_ok=True)
            nested.write_bytes(b"delete-me")

            deleted = system.identities.delete(dormant["life_id"])

            self.assertTrue(deleted["deleted"])
            self.assertTrue(deleted["files_removed"])
            self.assertFalse(dormant_root.exists())
            self.assertNotIn(
                dormant["life_id"],
                {row["life_id"] for row in system.identities.list()},
            )
            self.assertTrue((root / "lives" / active["life_id"]).is_dir())
            with self.assertRaises(LifeCoreError) as raised:
                system.identities.delete(active["life_id"])
            self.assertEqual(raised.exception.code, "active_life_delete_forbidden")

    def test_preserves_ids_backs_up_entire_sources_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oldest = "org_" + "a" * 32
            newest = "org_" + "b" * 32
            write_v1(root / "life-kernel", oldest, "2020-01-01T00:00:00.000Z")
            write_v1(root / "life-transaction", newest, "2021-01-01T00:00:00.000Z")

            first = migrate_legacy_identities(life_core, self.environment(root))
            self.assertEqual(first["status"], "completed")
            self.assertEqual(first["active_after"], oldest)
            self.assertEqual({item["life_id"] for item in first["actions"]}, {oldest, newest})
            backup = Path(first["backup_root"])
            self.assertTrue(backup.is_dir())
            manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(any(item["backup"].endswith("journal/life_events.jsonl") for item in manifest["files"]))
            self.assertTrue(any(item["path"].endswith("journal/.life-journal.lock") for item in manifest["excluded"]))
            self.assertFalse(any(item["backup"].endswith(".lock") for item in manifest["files"]))

            manager = LifeIdentityManager(root / "data", device_id="migration-test-device")
            for life_id in (oldest, newest):
                identity = manager.verify_root(root / "data" / "lives" / life_id, require_private=True)
                self.assertEqual(identity["schema"], IDENTITY_SCHEMA)
                self.assertEqual(identity["organism_id"], life_id)
            life_core.CompleteLifeSystem(root / "data", device_id="migration-test-device")

            second = migrate_legacy_identities(life_core, self.environment(root))
            self.assertEqual(second["status"], "completed")
            self.assertEqual(second["active_after"], oldest)
            self.assertEqual(
                [item["action"] for item in second["actions"]],
                ["already_migrated", "already_migrated"],
            )
            self.assertEqual(second["backup_root"], first["backup_root"])

    def test_embedded_7184_host_migrates_alias_bound_v1_identity_before_bootstrap(self) -> None:
        """The single-process host must not create a replacement before migration."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life_id = "org_" + "1" * 32
            legacy_root = root / "runtime" / "state" / "life_kernel"
            write_v1(legacy_root, life_id, "2020-01-02T03:04:05.000Z")
            documents = root / "profile" / "Documents"
            environment = {
                "TIANGONG_DOCUMENTS_PATH": str(documents),
                # app/main.js binds these current names for the embedded host.
                "TIANGONG_LIFE_KERNEL_ROOT": str(legacy_root),
                "TIANGONG_LIFE_ROOT": str(root / "runtime" / "state" / "life_transaction"),
                "TIANGONG_LIFE_RUNTIME_ROOT": str(root / "runtime" / "complete-life"),
            }

            runtime = EmbeddedLifeRuntime.from_environment(
                gateway_state_root=root / "runtime" / "gateway",
                gateway_environment="production",
                environ=environment,
            )
            try:
                active = runtime.system.identities.active(required=True)
                report = runtime.identity_migration_report

                self.assertEqual(active["life_id"], life_id)
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["active_after"], life_id)
                self.assertEqual(
                    json.loads(
                        (documents / "天工造物生命数据" / "identity_migration_report.json").read_text(
                            encoding="utf-8"
                        )
                    )["active_after"],
                    life_id,
                )
            finally:
                runtime.close()

    def test_embedded_7184_host_fails_closed_on_invalid_v1_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_root = root / "runtime" / "state" / "life_kernel"
            write_v1(
                legacy_root,
                "org_" + "2" * 32,
                "2020-01-02T03:04:05.000Z",
                tamper=True,
            )
            documents = root / "profile" / "Documents"
            environment = {
                "TIANGONG_DOCUMENTS_PATH": str(documents),
                "TIANGONG_LIFE_KERNEL_ROOT": str(legacy_root),
                "TIANGONG_LIFE_RUNTIME_ROOT": str(root / "runtime" / "complete-life"),
            }

            with self.assertRaises(EmbeddedLifeError) as raised:
                EmbeddedLifeRuntime.from_environment(
                    gateway_state_root=root / "runtime" / "gateway",
                    gateway_environment="production",
                    environ=environment,
                )

            self.assertEqual(raised.exception.code, "life.identity_migration_failed")
            report = json.loads(
                (documents / "天工造物生命数据" / "identity_migration_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(report["status"], "failed")
            self.assertIn(
                "legacy_identity_hash_mismatch",
                {item["code"] for item in report["failures"]},
            )
            self.assertFalse(
                (documents / "天工造物生命数据" / "life_registry.json").exists()
            )

    def test_existing_v2_active_identity_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = life_core.LifeIdentityManager(root / "data", device_id="migration-test-device")
            existing = manager.create("existing")["life_id"]
            legacy = "org_" + "c" * 32
            write_v1(root / "life-kernel", legacy, "2019-01-01T00:00:00.000Z")

            report = migrate_legacy_identities(life_core, self.environment(root))

            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["active_before"], existing)
            self.assertEqual(report["active_after"], existing)
            by_id = {item["life_id"]: item for item in manager.list()}
            self.assertEqual(by_id[existing]["status"], "active")
            self.assertEqual(by_id[legacy]["status"], "dormant")

    def test_signed_semantic_v2_journal_is_upgraded_without_losing_the_legacy_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = life_core.LifeIdentityManager(root / "data", device_id="migration-test-device")
            life_id = manager.create("legacy-journal")["life_id"]
            life_root = manager.root_for(life_id)
            private = serialization.load_pem_private_key(
                (life_root / "identity" / "private_key.pem").read_bytes(), password=None
            )
            events = []
            previous = ""
            for sequence in (1, 2):
                event = {
                    "schema": "tiangong.life.semantic-event.v2",
                    "life_id": life_id,
                    "sequence": sequence,
                    "event_id": f"evt_legacy_{sequence}",
                    "event_type": "legacy.recorded",
                    "cycle_id": "",
                    "occurred_at": f"2026-07-20T00:00:0{sequence}.000Z",
                    "actor": "legacy",
                    "epistemic_class": "observed",
                    "writer_epoch": 1,
                    "previous_hash": previous,
                    "idempotency_key": f"legacy-{sequence}",
                    "payload": {"sequence": sequence},
                }
                event_hash = hashlib.sha256(canonical(event)).hexdigest()
                events.append({
                    **event,
                    "event_hash": event_hash,
                    "signature": base64.b64encode(private.sign(event_hash.encode("ascii"))).decode("ascii"),
                })
                previous = event_hash
            journal = life_root / "journal" / "current" / "life_events.jsonl"
            journal.write_text("\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in events) + "\n", encoding="utf-8")
            (journal.with_name("life_head.json")).write_text(json.dumps({
                "schema": "tiangong.life.semantic-head.v2", "life_id": life_id,
                "last_sequence": 2, "last_hash": previous, "writer_epoch": 1,
            }), encoding="utf-8")

            system = CompleteLifeSystem(root / "data", device_id="migration-test-device")
            result = system.journal.ensure_hashed(life_id)
            self.assertTrue(result["valid"])
            migrated = system.journal.events(life_id)
            self.assertEqual([item["schema"] for item in migrated], ["tiangong.life.event.v3"] * 2)
            self.assertEqual([item["legacy_event_sha256"] for item in migrated], [item["event_hash"] for item in events])
            self.assertEqual(len(list((life_root / "journal" / "archive").glob("life_events.semantic-v2.*.jsonl.bak"))), 1)

    def test_tampered_v1_identity_fails_closed_without_creating_a_new_life(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = "org_" + "d" * 32
            source = write_v1(
                root / "life-kernel",
                legacy,
                "2020-01-01T00:00:00.000Z",
                tamper=True,
            )
            original = source.read_bytes()

            report = migrate_legacy_identities(life_core, self.environment(root))

            self.assertEqual(report["status"], "failed")
            self.assertIn("legacy_identity_hash_mismatch", {item["code"] for item in report["failures"]})
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((root / "data" / "lives" / legacy).exists())
            self.assertFalse((root / "data" / "life_registry.json").exists())

    def test_unsupported_registry_is_backed_up_before_v2_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = "org_" + "e" * 32
            write_v1(root / "life-kernel", legacy, "2020-01-01T00:00:00.000Z")
            data = root / "data"
            data.mkdir()
            old_registry = b'{"schema":"tiangong.life.registry.v1","active_id":"old"}\n'
            (data / "life_registry.json").write_bytes(old_registry)

            report = migrate_legacy_identities(life_core, self.environment(root))

            self.assertEqual(report["status"], "completed")
            backup = Path(report["backup_root"]) / "registry" / "life_registry.json"
            self.assertEqual(backup.read_bytes(), old_registry)
            registry = json.loads((data / "life_registry.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["schema"], "tiangong.life.registry.v2")
            self.assertEqual(registry["active_id"], legacy)

    def test_interrupted_publish_leaves_source_and_final_target_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = "org_" + "f" * 32
            source = write_v1(root / "life-kernel", legacy, "2020-01-01T00:00:00.000Z")
            original = source.read_bytes()
            from life_service import identity_migration

            real_replace = identity_migration.os.replace

            def interrupted_replace(source_path: object, target_path: object) -> None:
                source_text = str(source_path)
                target = Path(target_path)
                if Path(source_text).name.startswith(".m-") and target.name == legacy:
                    raise OSError("synthetic power loss before atomic publish")
                real_replace(source_path, target_path)

            with mock.patch.object(identity_migration.os, "replace", side_effect=interrupted_replace):
                report = migrate_legacy_identities(life_core, self.environment(root))

            self.assertEqual(report["status"], "failed")
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((root / "data" / "lives" / legacy).exists())
            backups = list((root / "data" / "migration-backups").glob("v1-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "manifest.json").is_file())

    @unittest.skipUnless(__import__("os").name == "nt", "Windows extended paths only")
    def test_long_unicode_profile_migrates_without_winerror_206(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ("长路径 用户 " + "x" * 120)
            legacy = "org_" + "9" * 32
            write_v1(root / "life-kernel", legacy, "2020-01-01T00:00:00.000Z")

            report = migrate_legacy_identities(life_core, self.environment(root))

            self.assertEqual(report["status"], "completed", report)
            self.assertEqual(report["active_after"], legacy)
            self.assertTrue(Path(report["backup_root"]).is_dir())


if __name__ == "__main__":
    unittest.main()
