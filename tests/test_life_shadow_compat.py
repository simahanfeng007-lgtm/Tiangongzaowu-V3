from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from life_service import (
    LegacySnapshotError,
    LegacySnapshotReader,
    ShadowLifeApi,
    build_shadow_http_server,
    compare_projection_anchor,
    snapshot_tree_sha256,
)
from life_service.legacy_adapter import (
    CONTEXT_STORE_SCHEMA,
    MEMORY_CONTENT_SCHEMA,
    SNAPSHOT_MANIFEST_SCHEMA,
)
from total_gateway.life_client import LifeClient, LifeProfileBindings
from total_gateway.object_store import ContentAddressedObjectStore


ROOT = Path(__file__).resolve().parents[1]
LIFE_ID = "org_shadow001"
CONTEXT_HASH = "a" * 64
CONVERSATION_SCOPE = "c" * 64


class DirectShadowTransport:
    def __init__(self, api: ShadowLifeApi) -> None:
        self.api = api

    def get_json(self, path: str) -> dict[str, object]:
        status, payload = self.api.handle("GET", path)
        if status != 200:
            raise AssertionError((status, payload))
        return payload


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def sign_document(path: Path, signature_path: Path, document: dict, key: Ed25519PrivateKey) -> None:
    write_json(path, document)
    signature_path.write_text(
        base64.b64encode(key.sign(canonical(document))).decode("ascii"),
        encoding="ascii",
    )


def refresh_manifest(snapshot: Path, **updates: object) -> None:
    manifest_path = snapshot / "life_snapshot_manifest.json"
    current = {}
    if manifest_path.is_file():
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    current.update(
        {
            "schema": SNAPSHOT_MANIFEST_SCHEMA,
            "source_kind": "snapshot_copy",
            "immutable": True,
            "capture_consistency": "atomic",
            "capture_method": "stopped_process_copy",
            "captured_at": "2026-07-16T00:00:00.000Z",
            "life_roots": {LIFE_ID: f"lives/{LIFE_ID}"},
            **updates,
        }
    )
    current["tree_sha256"] = snapshot_tree_sha256(snapshot)
    write_json(manifest_path, current)


def create_snapshot(root: Path) -> tuple[Path, dict[str, object]]:
    snapshot = root / "snapshot"
    life = snapshot / "lives" / LIFE_ID
    identity_root = life / "identity"
    identity_root.mkdir(parents=True)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    registry = {
        "schema": "tiangong.life.registry.v2",
        "revision": 3,
        "active_id": LIFE_ID,
        "bindings": {
            LIFE_ID: {
                "root": "C:/captured-live-root",
                "name": "旧称",
                "created_at": "2026-07-15T00:00:00.000Z",
                "read_only": False,
            }
        },
        "updated_at": "2026-07-16T00:00:00.000Z",
    }
    write_json(snapshot / "life_registry.json", registry)
    identity = {
        "schema": "tiangong.life.identity.v2",
        "organism_id": LIFE_ID,
        "name": "旧称",
        "public_key": base64.b64encode(public).decode("ascii"),
        "created_at": "2026-07-15T00:00:00.000Z",
    }
    sign_document(
        identity_root / "life_identity.json",
        identity_root / "life_identity.sig",
        identity,
        private,
    )
    soul = {
        "schema": "tiangong.life.soul.v1",
        "life_id": LIFE_ID,
        "revision": 4,
        "revision_id": "soul_revision_0004",
        "name": "苏凌霜",
        "prompt": "保持真实、连续、审慎。",
        "values": ["真实", "连续"],
        "boundaries": ["不得伪造事实"],
        "source": "user",
        "created_at": "2026-07-15T00:00:00.000Z",
        "updated_at": "2026-07-16T00:00:00.000Z",
    }
    sign_document(identity_root / "soul.json", identity_root / "soul.sig", soul, private)
    write_json(
        identity_root / "writer_lease.json",
        {
            "schema": "tiangong.life.writer-lease.v1",
            "device_id": "dev_legacy",
            "epoch": 7,
            "acquired_at": "2026-07-16T00:00:00.000Z",
            "expires_at": "2099-01-01T00:00:00.000Z",
        },
    )

    journal_root = life / "journal" / "current"
    journal_root.mkdir(parents=True)
    previous = ""
    event_lines: list[str] = []
    for sequence, event_type in ((1, "LIFE_CREATED"), (2, "MEMORY_ASSERTED")):
        event = {
            "schema": "tiangong.life.semantic-event.v2",
            "life_id": LIFE_ID,
            "sequence": sequence,
            "event_id": f"evt_{sequence:04d}",
            "event_type": event_type,
            "cycle_id": f"cycle_{sequence:04d}",
            "occurred_at": f"2026-07-16T00:00:0{sequence}.000Z",
            "actor": "life_system",
            "epistemic_class": "verified_fact",
            "writer_epoch": 7,
            "previous_hash": previous,
            "idempotency_key": f"idem_{sequence}",
            "payload": {"sequence": sequence},
        }
        event_hash = hashlib.sha256(canonical(event)).hexdigest()
        stored = {
            **event,
            "event_hash": event_hash,
            "signature": base64.b64encode(private.sign(event_hash.encode("ascii"))).decode("ascii"),
        }
        event_lines.append(canonical(stored).decode("utf-8"))
        previous = event_hash
    (journal_root / "life_events.jsonl").write_text(
        "\n".join(event_lines) + "\n", encoding="utf-8", newline="\n"
    )
    write_json(
        journal_root / "life_head.json",
        {
            "schema": "tiangong.life.semantic-head.v2",
            "life_id": LIFE_ID,
            "last_sequence": 2,
            "last_hash": previous,
            "writer_epoch": 7,
            "updated_at": "2026-07-16T00:00:02.000Z",
        },
    )
    write_json(journal_root / "idempotency.json", {})

    projection = {
        "schema": "tiangong.life.projection.v3",
        "life_id": LIFE_ID,
        "projection_status": "ready",
        "source_sequence": 2,
        "source_hash": previous,
        "state": {"status": "ALIVE", "vitality": 0.8},
        "affect": {"valence": 0.2, "expression": {"tone": "warm"}},
        "memory": {"total": 1},
        "relationship": {"trust": 0.7},
        "capabilities": {"available": True, "items": []},
        "free_will": {"enabled": True},
        "scheduler": {"status": "active"},
        "execution_bridge": {"status": "idle"},
        "execution_runs": [{"request_id": "req_1", "cycle_id": "cycle_run", "status": "done"}],
        "inference_runs": [],
        "tasks": [],
        "settings": {"voice": "concise"},
        "reflection": {},
        "iteration": {},
        "boundaries": {},
        "proactive_chats": [],
    }
    write_json(life / "projections" / "life.json", projection)

    memory_root = life / "memory"
    (memory_root / "blobs").mkdir(parents=True)
    (memory_root / "keys").mkdir(parents=True)
    memory_id = "mem_0001"
    memory_content = {"fact": "用户喜欢雨天", "token": "secret-must-not-leak"}
    plaintext = canonical(memory_content)
    memory_key = AESGCM.generate_key(bit_length=256)
    memory_nonce = os.urandom(12)
    memory_aad = f"{LIFE_ID}:{memory_id}:{MEMORY_CONTENT_SCHEMA}".encode("utf-8")
    memory_cipher = memory_nonce + AESGCM(memory_key).encrypt(memory_nonce, plaintext, memory_aad)
    (memory_root / "blobs" / f"{memory_id}.blob").write_bytes(memory_cipher)
    (memory_root / "keys" / f"{memory_id}.key").write_bytes(memory_key)
    descriptor = {
        "schema": MEMORY_CONTENT_SCHEMA,
        "storage": "encrypted_blob",
        "blob_id": memory_id,
        "cipher_sha256": hashlib.sha256(memory_cipher).hexdigest(),
        "content_sha256": hashlib.sha256(plaintext).hexdigest(),
    }
    database = sqlite3.connect(memory_root / "memory_index.sqlite3")
    try:
        database.execute(
            "CREATE TABLE memories (memory_id TEXT PRIMARY KEY, memory_type TEXT, status TEXT, search_text TEXT, content_json TEXT)"
        )
        database.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        database.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?)",
            (memory_id, "preference", "active", "用户 喜欢 雨天", canonical(descriptor).decode("utf-8")),
        )
        database.execute("INSERT INTO meta VALUES ('schema', 'tiangong.life.memory-index.v1')")
        database.commit()
    finally:
        database.close()

    context_root = life / "context"
    (context_root / "envelopes").mkdir(parents=True)
    context_key = AESGCM.generate_key(bit_length=256)
    (context_root / "context.key").write_bytes(context_key)
    envelope = {
        "schema": "tiangong.life.context-envelope.v2",
        "life_id": LIFE_ID,
        "writer_epoch": 7,
        "cycle_id": "cycle_context",
        "context_hash": CONTEXT_HASH,
        "soul_revision": soul["revision_id"],
        "estimated_tokens": 120,
        "token_budget": 8000,
        "compile_reasons": ["恢复连续性"],
        "omitted_blocks": [],
        "memory_cards": [{"memory_id": memory_id}],
        "messages": [],
    }
    context_plaintext = canonical(envelope)
    context_nonce = os.urandom(12)
    context_aad = f"{LIFE_ID}:{CONTEXT_HASH}:{CONTEXT_STORE_SCHEMA}".encode("utf-8")
    context_cipher = context_nonce + AESGCM(context_key).encrypt(
        context_nonce, context_plaintext, context_aad
    )
    (context_root / "envelopes" / f"{CONTEXT_HASH}.ctx").write_bytes(context_cipher)
    context_meta = {
        "schema": CONTEXT_STORE_SCHEMA,
        "life_id": LIFE_ID,
        "cycle_id": "cycle_context",
        "context_hash": CONTEXT_HASH,
        "algorithm": "AES-256-GCM",
        "cipher_sha256": hashlib.sha256(context_cipher).hexdigest(),
        "plaintext_bytes": len(context_plaintext),
        "estimated_tokens": 120,
        "token_budget": 8000,
        "created_at": "2026-07-16T00:00:03.000Z",
    }
    write_json(context_root / "envelopes" / f"{CONTEXT_HASH}.meta.json", context_meta)
    write_json(context_root / "latest.json", context_meta)
    refresh_manifest(snapshot)
    return snapshot, {
        "identity": identity,
        "soul": soul,
        "event_hash": previous,
        "memory_id": memory_id,
        "memory_content": memory_content,
        "envelope": envelope,
    }


class LifeShadowCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.snapshot, self.expected = create_snapshot(Path(self.temporary.name))

    def reader(self) -> LegacySnapshotReader:
        return LegacySnapshotReader(self.snapshot)

    def test_snapshot_verifies_identity_soul_journal_and_authority_anchor(self) -> None:
        reader = self.reader()
        self.assertEqual(reader.identity(), self.expected["identity"])
        self.assertEqual(reader.soul(), self.expected["soul"])
        self.assertEqual(reader.verify_journal()["sequence"], 2)
        anchor = reader.anchor()
        self.assertEqual(anchor.life_id, LIFE_ID)
        self.assertEqual(anchor.writer_epoch, 7)
        self.assertEqual(anchor.event_sequence, 2)
        self.assertEqual(anchor.event_hash, self.expected["event_hash"])
        self.assertEqual(anchor.memory_total, 1)
        self.assertEqual(anchor.context_hash, CONTEXT_HASH)

    def test_memory_and_context_are_decrypted_with_legacy_aad_and_hashes(self) -> None:
        reader = self.reader()
        self.assertEqual(
            reader.decrypt_memory_content(str(self.expected["memory_id"])),
            self.expected["memory_content"],
        )
        latest = reader.latest_context()
        self.assertTrue(latest["available"])
        self.assertEqual(latest["envelope"], self.expected["envelope"])

    def test_manifest_rejects_tree_tampering_and_live_source_kinds(self) -> None:
        projection = self.snapshot / "lives" / LIFE_ID / "projections" / "life.json"
        projection.write_bytes(projection.read_bytes() + b" ")
        with self.assertRaisesRegex(LegacySnapshotError, "tree"):
            self.reader()
        refresh_manifest(self.snapshot, source_kind="live_root")
        with self.assertRaisesRegex(LegacySnapshotError, "offline snapshot_copy"):
            self.reader()

    def test_snapshot_requires_atomic_checkpointed_sqlite_capture(self) -> None:
        refresh_manifest(self.snapshot, capture_consistency="best_effort")
        with self.assertRaisesRegex(LegacySnapshotError, "atomically consistent"):
            self.reader()
        refresh_manifest(self.snapshot, capture_consistency="atomic")
        sidecar = (
            self.snapshot
            / "lives"
            / LIFE_ID
            / "memory"
            / "memory_index.sqlite3-wal"
        )
        sidecar.write_bytes(b"not-checkpointed")
        refresh_manifest(self.snapshot)
        with self.assertRaisesRegex(LegacySnapshotError, "WAL/SHM"):
            self.reader()

    def test_snapshot_duplicate_json_keys_fail_closed(self) -> None:
        projection = self.snapshot / "lives" / LIFE_ID / "projections" / "life.json"
        projection.write_text(
            '{"life_id":"org_shadow001","life_id":"org_shadow001"}',
            encoding="utf-8",
        )
        refresh_manifest(self.snapshot)
        with self.assertRaisesRegex(LegacySnapshotError, "duplicate JSON key"):
            self.reader().projection()

    def test_inner_signatures_and_journal_chain_are_not_replaced_by_manifest_trust(self) -> None:
        signature = self.snapshot / "lives" / LIFE_ID / "identity" / "soul.sig"
        signature.write_text(base64.b64encode(b"x" * 64).decode("ascii"), encoding="ascii")
        refresh_manifest(self.snapshot)
        with self.assertRaisesRegex(LegacySnapshotError, "signature"):
            self.reader().soul()

        self.snapshot, _ = create_snapshot(Path(self.temporary.name) / "second")
        journal = self.snapshot / "lives" / LIFE_ID / "journal" / "current" / "life_events.jsonl"
        lines = journal.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[1])
        event["previous_hash"] = "f" * 64
        lines[1] = canonical(event).decode("utf-8")
        journal.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        refresh_manifest(self.snapshot)
        with self.assertRaisesRegex(LegacySnapshotError, "chain"):
            self.reader().verify_journal()

    def test_projection_comparison_names_mismatch_and_information_loss(self) -> None:
        anchor = self.reader().anchor()
        exact = compare_projection_anchor(anchor, anchor.to_dict())
        self.assertTrue(exact["compatible"])
        candidate = anchor.to_dict()
        candidate.pop("context_hash")
        candidate["memory_total"] = 99
        candidate["new_dimension"] = 1
        report = compare_projection_anchor(anchor, candidate)
        self.assertFalse(report["compatible"])
        self.assertIn("context_hash", report["unrecoverable_information"])
        self.assertEqual(report["candidate_only_fields"], ["new_dimension"])
        self.assertEqual(
            {item["classification"] for item in report["differences"]},
            {"missing_in_candidate", "value_mismatch"},
        )
        status, response = ShadowLifeApi(self.reader()).handle(
            "POST",
            "/api/v1/v3/life/shadow/compare",
            {"candidate_anchor": candidate},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["comparison"], report)

    def test_core_v2_read_routes_preserve_cross_response_authority(self) -> None:
        api = ShadowLifeApi(self.reader())
        status, health = api.handle("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["api_contract"], "tiangong.life.api.v2")
        self.assertTrue(health["life_ready"])
        self.assertFalse(health["production_writer_enabled"])
        status, state = api.handle("GET", "/api/v1/v3/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["life_id"], LIFE_ID)
        self.assertEqual(state["identity"]["writer_epoch"], 7)
        self.assertEqual(state["soul"]["revision_id"], "soul_revision_0004")
        self.assertEqual(state["ui"]["lifecycle"]["source_sequence"], 2)
        self.assertEqual(state["ui"]["context"]["context_hash"], CONTEXT_HASH)
        self.assertTrue(state["ui"]["context"]["verified"])
        self.assertEqual(state["ui"]["context"]["selected_context_tokens"], 120)
        self.assertEqual(state["ui"]["context"]["current_context_tokens"], 120)
        self.assertEqual(state["ui"]["context"]["context_utilization_milli"], 15)
        status, latest = api.handle("GET", "/api/v1/v3/life/context/latest")
        self.assertEqual(status, 200)
        self.assertEqual(latest["envelope"]["context_hash"], CONTEXT_HASH)

    def test_existing_gateway_life_client_pins_the_shadow_without_adapter_exceptions(self) -> None:
        object_root = Path(self.temporary.name) / "objects"
        store = ContentAddressedObjectStore.open(object_root, now_ms=100)
        self.addCleanup(store.close)
        pinned = LifeClient(
            DirectShadowTransport(ShadowLifeApi(self.reader())), store
        ).acquire_snapshot(
            tenant_id="desktop",
            link_account_id="local-user",
            conversation_scope_hash=CONVERSATION_SCOPE,
            profile=LifeProfileBindings(
                user_callsign="夏平",
                user_occupation="产品设计",
                persona_avatar_ref="avatar_suling",
                persona_voice_ref="voice_suling",
                user_avatar_ref="avatar_user",
            ),
        )
        self.assertEqual(pinned.snapshot.identity_ref, LIFE_ID)
        self.assertEqual(pinned.snapshot.identity_revision, 7)
        self.assertEqual(pinned.snapshot.revision, 2)
        self.assertEqual(pinned.upstream_context_sha256, CONTEXT_HASH)

    def test_read_only_post_queries_work_but_every_mutation_is_rejected(self) -> None:
        api = ShadowLifeApi(self.reader())
        before = snapshot_tree_sha256(self.snapshot)
        status, search = api.handle(
            "POST",
            "/api/v1/v3/life/memory/search",
            {"query": "雨天", "include_content": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(search["results"]), 1)
        self.assertEqual(search["results"][0]["content"]["token"], "[REDACTED]")
        status, replay = api.handle(
            "POST", "/api/v1/v3/life/context/replay", {"context_hash": CONTEXT_HASH}
        )
        self.assertEqual(status, 200)
        self.assertEqual(replay["envelope"], self.expected["envelope"])
        status, execution = api.handle(
            "POST", "/api/v1/v3/life/execution/status", {"request_id": "req_1"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(execution["available"])
        for path in (
            "/api/v1/v3/life/heartbeat",
            "/api/v1/v3/life/memory/assert",
            "/api/v1/v3/life/soul/update",
            "/api/v1/v3/life/execution/prepare",
            "/api/v1/v3/life/projection/rebuild",
        ):
            status, rejected = api.handle("POST", path, {})
            self.assertEqual(status, 405, path)
            self.assertEqual(rejected["error"]["code"], "shadow.mutation_forbidden")
        self.assertEqual(snapshot_tree_sha256(self.snapshot), before)

    def test_shadow_listener_is_loopback_authenticated_and_never_7175(self) -> None:
        with self.assertRaisesRegex(ValueError, "7175"):
            build_shadow_http_server(self.reader(), token="t" * 32, port=7175)
        with self.assertRaisesRegex(ValueError, "32"):
            build_shadow_http_server(self.reader(), token="short", port=0)
        server, config = build_shadow_http_server(self.reader(), token="t" * 32, port=0)
        self.addCleanup(server.server_close)
        self.assertEqual(config.host, "127.0.0.1")
        self.assertFalse(config.production_writer_enabled)
        self.assertFalse(config.writer_lease_acquisition_enabled)
        self.assertFalse(config.scheduler_enabled)
        self.assertFalse(config.side_effects_enabled)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", config.port, timeout=2)
        connection.request("GET", "/health")
        unauthorized = connection.getresponse()
        self.assertEqual(unauthorized.status, 401)
        unauthorized.read()
        connection.request("GET", "/health", headers={"Authorization": "Bearer " + "t" * 32})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["life_ready"])
        connection.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            body='{"query":"雨天","query":"冲突"}'.encode("utf-8"),
            headers={
                "Authorization": "Bearer " + "t" * 32,
                "Content-Type": "application/json",
            },
        )
        duplicate = connection.getresponse()
        duplicate_payload = json.loads(duplicate.read().decode("utf-8"))
        self.assertEqual(duplicate.status, 400)
        self.assertEqual(
            duplicate_payload["error"]["code"], "shadow.request_duplicate_key"
        )
        connection.close()

    def test_shadow_failure_is_isolated_and_snapshot_remains_unchanged(self) -> None:
        api = ShadowLifeApi(self.reader())
        context_blob = self.snapshot / "lives" / LIFE_ID / "context" / "envelopes" / f"{CONTEXT_HASH}.ctx"
        context_blob.write_bytes(context_blob.read_bytes()[:-1] + b"x")
        refresh_manifest(self.snapshot)
        before_request = snapshot_tree_sha256(self.snapshot)
        status, payload = ShadowLifeApi(self.reader()).handle(
            "GET", "/api/v1/v3/life/context/latest"
        )
        self.assertEqual(status, 409)
        self.assertFalse(payload["ok"])
        self.assertEqual(snapshot_tree_sha256(self.snapshot), before_request)
        status, rejected = api.handle("POST", "/api/v1/v3/life/heartbeat", {})
        self.assertEqual(status, 405)
        self.assertEqual(rejected["error"]["code"], "shadow.mutation_forbidden")

    def test_cli_inspection_has_no_secret_or_effect_capability(self) -> None:
        completed = subprocess.run(
            ["python", "-m", "life_service", "--inspect-snapshot", str(self.snapshot)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "read_only_snapshot")
        self.assertFalse(payload["production_writer_enabled"])
        self.assertFalse(payload["writer_lease_acquisition_enabled"])
        self.assertFalse(payload["scheduler_enabled"])
        self.assertFalse(payload["side_effects_enabled"])
        self.assertNotIn("secret-must-not-leak", completed.stdout)


if __name__ == "__main__":
    unittest.main()
