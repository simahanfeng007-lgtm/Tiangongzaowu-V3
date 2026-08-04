from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from life_service.cutover import (
    LifeCutoverAuthority,
    LifeCutoverError,
    activate_handoff,
    build_cutover_comparison,
    build_rollback_permit,
    capture_final_delta,
    capture_stopped_legacy_snapshot,
    create_drain_evidence,
    install_cutover_state_bundle,
    load_cow_manifest,
    prepare_cow_import,
    recover_cutover_state_bundle,
    renew_handoff_permit,
    rollback_cutover_state_bundle,
    verify_cutover_state_bundle,
    verify_handoff_permit,
    write_handoff_artifacts,
)
from life_service.legacy_adapter import LegacySnapshotReader, snapshot_tree_sha256
from life_service.production_api import (
    ProductionLifeApi,
    build_cutover_read_only_fallback_server,
    build_production_http_server,
)
from life_service.store import LifeShadowStore
from total_gateway.life_client import (
    LifeClient,
    LifeProfileBindings,
    LoopbackLifeJsonTransport,
)
from total_gateway.object_store import ContentAddressedObjectStore

from tests.test_life_shadow_compat import LIFE_ID, create_snapshot, refresh_manifest


NOW_MS = 10_000
TOKEN = "p11-desktop-token-" + "x" * 40


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".tiangong-generated-source.json"
    }


class LifeCutoverP11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot, self.expected = create_snapshot(self.root / "legacy")
        self.stage = self.root / "stage"

    def prepare_final_handoff(self):
        initial = prepare_cow_import(self.snapshot, self.stage, now_ms=NOW_MS)
        final = capture_final_delta(self.snapshot, self.stage, now_ms=NOW_MS + 1)
        drain = create_drain_evidence(
            scheduler_pending=0,
            inflight_requests=0,
            old_writer_stopped=True,
            final_manifest_sha256=final.manifest_sha256,
            observed_at_ms=NOW_MS + 2,
        )
        authority = LifeCutoverAuthority.generate()
        permit = activate_handoff(
            final,
            drain,
            authority,
            issued_at_ms=NOW_MS + 3,
            expires_at_ms=NOW_MS + 300_000,
        )
        write_handoff_artifacts(self.stage, permit, authority)
        return initial, final, drain, authority, permit

    def rollback_artifact(self, authority, permit) -> Path:
        rollback = build_rollback_permit(
            permit,
            authority,
            new_writer_stopped=True,
            post_cutover_event_hashes=(),
            compatible_replay_event_hashes=(),
            issued_at_ms=NOW_MS + 20,
            expires_at_ms=NOW_MS + 300_000,
        )
        root = self.root / "rollback-artifact"
        root.mkdir()
        path, _ = write_handoff_artifacts(root, rollback, authority)
        return path

    def test_cow_import_verifies_every_domain_without_rewriting_legacy(self) -> None:
        before_tree = snapshot_tree_sha256(self.snapshot)
        before_bytes = tree_bytes(self.snapshot)
        manifest = prepare_cow_import(self.snapshot, self.stage, now_ms=NOW_MS)
        self.assertEqual(snapshot_tree_sha256(self.snapshot), before_tree)
        self.assertEqual(tree_bytes(self.snapshot), before_bytes)
        self.assertEqual(manifest.life_id, LIFE_ID)
        self.assertEqual(manifest.event_sequence, 2)
        self.assertEqual(manifest.memory_total, 1)
        self.assertEqual(manifest.context_hash, "a" * 64)
        self.assertEqual(manifest.writer_epoch, 7)
        self.assertTrue((self.stage / "life-overlay.shadow.sqlite3").is_file())
        comparison = build_cutover_comparison(
            LegacySnapshotReader(self.snapshot),
            manifest,
            self.stage / manifest.overlay_file,
        )
        self.assertTrue(comparison["compatible"])
        self.assertEqual(
            set(comparison["domains"]),
            {
                "anchor",
                "projection",
                "affect",
                "recall",
                "context",
                "decision",
                "overlay",
                "overlay_memory",
                "overlay_memory_count",
            },
        )
        self.assertEqual(comparison["performance"]["network_calls"], 0)

    def test_stopped_writer_capture_is_atomic_verified_and_source_immutable(self) -> None:
        source_before = tree_bytes(self.snapshot)
        captured = self.root / "captured-real-shape"
        with self.assertRaisesRegex(
            LifeCutoverError, "cutover.snapshot_writer_not_stopped"
        ):
            capture_stopped_legacy_snapshot(
                self.snapshot,
                captured,
                writer_stopped=False,
                now_ms=NOW_MS,
            )
        manifest = capture_stopped_legacy_snapshot(
            self.snapshot,
            captured,
            writer_stopped=True,
            now_ms=NOW_MS,
        )
        self.assertEqual(manifest["capture_method"], "stopped_process_copy")
        self.assertEqual(manifest["tree_sha256"], snapshot_tree_sha256(captured))
        self.assertEqual(LegacySnapshotReader(captured).anchor().life_id, LIFE_ID)
        self.assertEqual(source_before, tree_bytes(self.snapshot))

    def test_tampered_signature_fails_before_any_stage_survives(self) -> None:
        signature = self.snapshot / "lives" / LIFE_ID / "identity" / "soul.sig"
        signature.write_text("AAAA", encoding="ascii")
        refresh_manifest(self.snapshot)
        with self.assertRaisesRegex(Exception, "signature"):
            prepare_cow_import(self.snapshot, self.stage, now_ms=NOW_MS)
        self.assertFalse(self.stage.exists())

    def test_final_delta_is_prefix_bound_and_identity_substitution_fails(self) -> None:
        initial = prepare_cow_import(self.snapshot, self.stage, now_ms=NOW_MS)
        final = capture_final_delta(self.snapshot, self.stage, now_ms=NOW_MS + 1)
        self.assertEqual(final.previous_manifest_sha256, initial.manifest_sha256)
        self.assertEqual(final.delta_from_sequence, 2)
        self.assertEqual(final.event_hash, initial.event_hash)
        other, _ = create_snapshot(self.root / "substitution")
        with self.assertRaisesRegex(LifeCutoverError, "prefix"):
            capture_final_delta(other, self.stage, now_ms=NOW_MS + 2)

    def test_handoff_requires_drain_and_increments_epoch_exactly_once(self) -> None:
        _, final, drain, authority, permit = self.prepare_final_handoff()
        self.assertEqual(permit.writer_epoch, 8)
        self.assertEqual(permit.owner, "source_life_service")
        verify_handoff_permit(permit, authority.public_bytes(), now_ms=NOW_MS + 4)
        renewed = renew_handoff_permit(
            permit,
            authority,
            issued_at_ms=NOW_MS + 5,
            expires_at_ms=NOW_MS + 600_000,
        )
        self.assertEqual(renewed.writer_epoch, permit.writer_epoch)
        self.assertEqual(renewed.previous_permit_sha256, permit.permit_sha256)
        verify_handoff_permit(renewed, authority.public_bytes(), now_ms=NOW_MS + 6)
        with self.assertRaisesRegex(LifeCutoverError, "drain"):
            create_drain_evidence(
                scheduler_pending=1,
                inflight_requests=0,
                old_writer_stopped=True,
                final_manifest_sha256=final.manifest_sha256,
                observed_at_ms=NOW_MS + 2,
            )
        wrong = drain.__class__(
            **{**drain.to_dict(), "final_manifest_sha256": "f" * 64}
        )
        with self.assertRaisesRegex(LifeCutoverError, "evidence"):
            activate_handoff(
                final,
                wrong,
                authority,
                issued_at_ms=NOW_MS + 3,
                expires_at_ms=NOW_MS + 10_000,
            )

    def test_source_http_is_single_writer_and_first_message_is_atomic(self) -> None:
        before = tree_bytes(self.snapshot)
        _, final, _, authority, permit = self.prepare_final_handoff()
        reader = LegacySnapshotReader(self.snapshot)
        server, config = build_production_http_server(
            reader,
            self.stage / final.overlay_file,
            final,
            permit,
            trusted_public_key=authority.public_bytes(),
            token=TOKEN,
            port=0,
            now_ms=NOW_MS + 4,
            allow_ephemeral_test_port=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{config.port}"
        transport = LoopbackLifeJsonTransport(base_url, desktop_token=TOKEN)
        health = transport.get_json("/health")
        self.assertTrue(health["source_owned"])
        self.assertTrue(health["production_writer_enabled"])
        self.assertFalse(health["scheduler_enabled"])
        self.assertFalse(health["side_effects_enabled"])
        self.assertEqual(health["writer_epoch"], 8)
        self.assertEqual(
            health["projection_authority"]["revisions"]["source_sequence"], 2
        )

        objects = ContentAddressedObjectStore.open(self.root / "objects", now_ms=NOW_MS)
        try:
            pinned = LifeClient(transport, objects).compile_and_authorize_snapshot(
                request_id="req_" + "7" * 64,
                run_id="run_" + "8" * 64,
                generation=0,
                current_request="第一次消息必须直接得到原子上下文。",
                tenant_id="desktop",
                link_account_id="local-user",
                conversation_scope_hash="c" * 64,
                profile=LifeProfileBindings(user_callsign="用户"),
                observed_at_ms=NOW_MS + 5,
            )
            compiled = json.loads(
                objects.read_bytes(pinned.snapshot.compiled_context_object_id).decode("utf-8")
            )
            summaries = "\n".join(
                item["summary"] for item in compiled["context_pack"]["items"]
            )
            self.assertTrue(
                any(
                    item["item_ref"].startswith("legacy_context_")
                    for item in compiled["context_pack"]["items"]
                )
            )
            self.assertIn("tiangong.life.legacy-context-checkpoint.v1", summaries)
            self.assertIn("用户喜欢雨天", summaries)
            self.assertIn("[REDACTED]", summaries)
            self.assertNotIn("secret-must-not-leak", summaries)
        finally:
            objects.close()
        self.assertEqual(pinned.snapshot.identity_ref, LIFE_ID)
        self.assertEqual(pinned.snapshot.revision, 2)
        self.assertEqual(pinned.snapshot.memory_revision, 1)
        self.assertEqual(tree_bytes(self.snapshot), before)

        connection = http.client.HTTPConnection("127.0.0.1", config.port, timeout=3)
        connection.request("GET", "/health")
        unauthorized = connection.getresponse()
        self.assertEqual(unauthorized.status, 401)
        unauthorized.read()
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", config.port, timeout=3)
        connection.request(
            "POST",
            "/api/v1/v3/life/identity/reset",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-Tiangong-Token": TOKEN,
            },
        )
        forbidden = connection.getresponse()
        body = json.loads(forbidden.read().decode("utf-8"))
        self.assertEqual(forbidden.status, 405)
        self.assertEqual(body["error_code"], "life.source.mutation_forbidden")
        connection.close()

    def test_tampered_expired_or_wrong_port_handoff_cannot_listen(self) -> None:
        _, final, _, authority, permit = self.prepare_final_handoff()
        reader = LegacySnapshotReader(self.snapshot)
        tampered = permit.__class__(
            **{**permit.to_dict(), "writer_epoch": permit.writer_epoch + 1}
        )
        with self.assertRaisesRegex(LifeCutoverError, "handoff"):
            build_production_http_server(
                reader,
                self.stage / final.overlay_file,
                final,
                tampered,
                trusted_public_key=authority.public_bytes(),
                token=TOKEN,
                now_ms=NOW_MS + 4,
            )
        with self.assertRaisesRegex(LifeCutoverError, "expired"):
            build_production_http_server(
                reader,
                self.stage / final.overlay_file,
                final,
                permit,
                trusted_public_key=authority.public_bytes(),
                token=TOKEN,
                now_ms=permit.expires_at_ms,
            )
        with self.assertRaisesRegex(ValueError, "7175"):
            build_production_http_server(
                reader,
                self.stage / final.overlay_file,
                final,
                permit,
                trusted_public_key=authority.public_bytes(),
                token=TOKEN,
                port=7176,
                now_ms=NOW_MS + 4,
            )
        with self.assertRaisesRegex(ValueError, "7175"):
            build_production_http_server(
                reader,
                self.stage / final.overlay_file,
                final,
                permit,
                trusted_public_key=authority.public_bytes(),
                token=TOKEN,
                port=0,
                now_ms=NOW_MS + 4,
            )

    def test_post_cutover_failure_uses_read_only_legacy_fallback(self) -> None:
        before = tree_bytes(self.snapshot)
        server, config = build_cutover_read_only_fallback_server(
            LegacySnapshotReader(self.snapshot),
            token=TOKEN,
            port=0,
            allow_ephemeral_test_port=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = LoopbackLifeJsonTransport(
            f"http://127.0.0.1:{config.port}", desktop_token=TOKEN
        )
        health = transport.get_json("/health")
        self.assertEqual(health["source_mode"], "cutover_read_only_fallback")
        self.assertFalse(health["production_writer_enabled"])
        self.assertTrue(health["cutover_recovery_required"])
        connection = http.client.HTTPConnection("127.0.0.1", config.port, timeout=3)
        connection.request(
            "POST",
            "/api/v1/v3/life/context/compile-and-authorize",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-Tiangong-Token": TOKEN,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 405)
        self.assertEqual(payload["error_code"], "life.source.fallback_read_only")
        connection.close()
        self.assertEqual(tree_bytes(self.snapshot), before)

    def test_rollback_requires_exact_compatibility_replay_and_new_epoch(self) -> None:
        _, _, _, authority, permit = self.prepare_final_handoff()
        events = (hashlib.sha256(b"post-cutover-event").hexdigest(),)
        with self.assertRaisesRegex(LifeCutoverError, "incomplete"):
            build_rollback_permit(
                permit,
                authority,
                new_writer_stopped=True,
                post_cutover_event_hashes=events,
                compatible_replay_event_hashes=(),
                issued_at_ms=NOW_MS + 20,
                expires_at_ms=NOW_MS + 30_000,
            )
        rollback = build_rollback_permit(
            permit,
            authority,
            new_writer_stopped=True,
            post_cutover_event_hashes=events,
            compatible_replay_event_hashes=events,
            issued_at_ms=NOW_MS + 20,
            expires_at_ms=NOW_MS + 30_000,
        )
        self.assertEqual(rollback.writer_epoch, 9)
        self.assertEqual(rollback.compatibility_event_count, 1)
        self.assertEqual(rollback.previous_permit_sha256, permit.permit_sha256)
        verify_handoff_permit(rollback, authority.public_bytes(), now_ms=NOW_MS + 21)

    def test_fresh_upgrade_rollback_and_recovery_keep_every_release(self) -> None:
        _, _, _, authority, permit = self.prepare_final_handoff()
        install = self.root / "installed"
        fresh = install_cutover_state_bundle(
            self.stage, install, release_id="p11-r1", mode="fresh"
        )
        self.assertEqual(fresh["release_id"], "p11-r1")
        installed_bases = [path for path in (install / "base").iterdir() if path.is_dir()]
        self.assertEqual(len(installed_bases), 1)
        installed_reader = LegacySnapshotReader(installed_bases[0])
        self.assertEqual(installed_reader.anchor().life_id, LIFE_ID)
        r1_manifest = load_cow_manifest(
            install / "releases" / "p11-r1" / "cow_final.json"
        )
        pre_upgrade_request = "req_" + "e" * 64
        pre_upgrade_run = "run_" + "f" * 64
        pre_upgrade_api = ProductionLifeApi(
            installed_reader,
            install / "releases" / "p11-r1" / r1_manifest.overlay_file,
            r1_manifest,
            permit,
            clock_ms=lambda: NOW_MS + 25,
        )
        status, response = pre_upgrade_api.handle(
            "POST",
            "/api/v1/v3/life/context/compile-and-authorize",
            {
                "request_id": pre_upgrade_request,
                "run_id": pre_upgrade_run,
                "generation": 1,
                "current_request": "升级前状态不得丢失。",
                "principal_scope_hash": "1" * 64,
                "issued_at_ms": NOW_MS + 25,
            },
        )
        self.assertEqual(status, 200, response)
        with self.assertRaisesRegex(LifeCutoverError, "writer_not_stopped"):
            install_cutover_state_bundle(
                self.stage,
                install,
                release_id="p11-rejected",
                mode="upgrade",
            )
        upgraded = install_cutover_state_bundle(
            self.stage,
            install,
            release_id="p11-r2",
            mode="upgrade",
            writer_stopped=True,
        )
        self.assertEqual(upgraded["previous_release_id"], "p11-r1")
        self.assertTrue((install / "releases" / "p11-r1").is_dir())
        self.assertTrue((install / "releases" / "p11-r2").is_dir())
        with LifeShadowStore.open(
            install / "releases" / "p11-r2" / r1_manifest.overlay_file,
            create=False,
            now_ms=NOW_MS + 26,
        ) as store:
            self.assertIsNotNone(
                store.get_context_authorization(
                    pre_upgrade_request,
                    run_id=pre_upgrade_run,
                    generation=1,
                )
            )
        r2_overlay_sha256 = hashlib.sha256(
            (
                install
                / "releases"
                / "p11-r2"
                / "life-overlay.shadow.sqlite3"
            ).read_bytes()
        ).hexdigest()
        (install / "active.json").write_text("{}", encoding="utf-8")
        recovered = recover_cutover_state_bundle(
            install,
            release_id="p11-r2",
            previous_release_id="p11-r1",
            expected_overlay_sha256=r2_overlay_sha256,
        )
        self.assertEqual(recovered["release_id"], "p11-r2")
        verify_cutover_state_bundle(install)
        rollback_path = self.rollback_artifact(authority, permit)
        with self.assertRaisesRegex(LifeCutoverError, "writer_not_stopped"):
            rollback_cutover_state_bundle(
                install,
                writer_stopped=False,
                rollback_permit_path=rollback_path,
            )
        installed_manifest = load_cow_manifest(
            install / "releases" / "p11-r2" / "cow_final.json"
        )
        installed_api = ProductionLifeApi(
            installed_reader,
            install
            / "releases"
            / "p11-r2"
            / installed_manifest.overlay_file,
            installed_manifest,
            permit,
            clock_ms=lambda: NOW_MS + 30,
        )
        request_id = "req_" + "a" * 64
        run_id = "run_" + "b" * 64
        status, written = installed_api.handle(
            "POST",
            "/api/v1/v3/life/context/compile-and-authorize",
            {
                "request_id": request_id,
                "run_id": run_id,
                "generation": 1,
                "current_request": "回滚前写入且回滚后必须仍然存在。",
                "principal_scope_hash": "d" * 64,
                "issued_at_ms": NOW_MS + 30,
            },
        )
        self.assertEqual(status, 200, written)
        rolled_back = rollback_cutover_state_bundle(
            install,
            writer_stopped=True,
            rollback_permit_path=rollback_path,
        )
        self.assertEqual(rolled_back["release_id"], "p11-r1")
        verify_cutover_state_bundle(install)
        with LifeShadowStore.open(
            install
            / "releases"
            / "p11-r1"
            / installed_manifest.overlay_file,
            create=False,
            now_ms=NOW_MS + 31,
        ) as store:
            self.assertIsNotNone(
                store.get_context_authorization(
                    request_id, run_id=run_id, generation=1
                )
            )

    def test_overwrite_retains_hash_verified_previous_release(self) -> None:
        _, _, _, authority, permit = self.prepare_final_handoff()
        install = self.root / "overwrite-install"
        install_cutover_state_bundle(
            self.stage, install, release_id="p11-r1", mode="fresh"
        )
        overwritten = install_cutover_state_bundle(
            self.stage,
            install,
            release_id="p11-r1",
            mode="overwrite",
            writer_stopped=True,
        )
        previous = overwritten["previous_release_id"]
        self.assertRegex(previous, r"^p11-r1\.previous-[0-9a-f]{12}$")
        self.assertTrue((install / "releases" / previous).is_dir())
        verify_cutover_state_bundle(install)
        rolled_back = rollback_cutover_state_bundle(
            install,
            writer_stopped=True,
            rollback_permit_path=self.rollback_artifact(authority, permit),
        )
        self.assertEqual(rolled_back["release_id"], previous)
        verify_cutover_state_bundle(install)

    def test_bootstrap_and_desktop_only_activate_a_complete_artifact_set(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readable = (
            root
            / "readable-python-source"
            / "life-bootstrap"
            / "tiangong_life_bootstrap.py"
        ).read_text(encoding="utf-8")
        packaged = (
            root
            / "app"
            / "life-service"
            / "runtime314"
            / "tiangong_life_bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(readable, packaged)
        self.assertLess(
            readable.index("if _p11_cutover_configured():"),
            readable.index("import life_core"),
        )
        self.assertIn("serve_production_from_environment", readable)
        self.assertIn("serve_cutover_read_only_fallback_from_environment", readable)
        main = (root / "app" / "main.js").read_text(encoding="utf-8")
        self.assertIn("const p11Ready = isDirectory(p11Snapshot)", main)
        self.assertIn("TIANGONG_LIFE_P11_FINAL_MANIFEST", main)
        self.assertIn("delete env[key]", main)
        self.assertIn("TIANGONG_LIFE_P11_CUTOVER_REQUIRED", main)
        self.assertIn("base_snapshot_relative_path", main)
        self.assertIn("const usePatchedRuntime", main)

    def test_embedded_runtime_contains_exact_source_and_contract_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        runtime = root / "app" / "life-service" / "runtime314"
        for package in ("life_service", "contracts"):
            source = tree_bytes(root / "src" / package)
            packaged = tree_bytes(runtime / package)
            source = {
                name: payload
                for name, payload in source.items()
                if "__pycache__" not in name and not name.endswith(".pyc")
            }
            packaged = {
                name: payload
                for name, payload in packaged.items()
                if "__pycache__" not in name and not name.endswith(".pyc")
            }
            self.assertEqual(source, packaged, package)
        completed = subprocess.run(
            [
                str(runtime / "python.exe") if (runtime / "python.exe").is_file() else sys.executable,
                "-c",
                (
                    "import life_service.production_api as p;"
                    "print(p.PRODUCTION_SERVICE_SCHEMA)"
                ),
            ],
            cwd=runtime,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "tiangong.life.source-service.v1")


if __name__ == "__main__":
    unittest.main()
