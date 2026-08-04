from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from total_gateway.life_client import (
    LifeClient,
    LifeClientError,
    LifeProfileBindings,
    LoopbackLifeJsonTransport,
    life_snapshot_sha256,
)
from total_gateway.object_store import ContentAddressedObjectStore


HASH_A = "a" * 64
HASH_B = "b" * 64
LIFE_ID = "org_life_test_001"
SOUL_REVISION = "soulrev_test_001"
CONVERSATION_SCOPE = "c" * 64


def response_fixtures() -> dict[str, dict[str, object]]:
    health = {
        "ok": True,
        "api_contract": "tiangong.life.api.v2",
        "life_ready": True,
        "setup_required": False,
    }
    active = {
        "life_id": LIFE_ID,
        "name": "起源",
        "root": r"C:\Users\someone\private-life-root",
        "status": "active",
        "writer_epoch": 3,
        "active": True,
        "integrity": "valid",
        "soul_revision_id": SOUL_REVISION,
        "soul_integrity": "valid",
    }
    soul = {
        "schema": "tiangong.life.soul.v1",
        "life_id": LIFE_ID,
        "name": "起源",
        "prompt": "保持真实，外部行动只以机器事实为准。",
        "revision": 5,
        "revision_id": SOUL_REVISION,
        "values": ["真实"],
        "boundaries": ["不臆想完成"],
        "source": "user",
        "created_at": "2026-07-14T10:00:00.000Z",
        "updated_at": "2026-07-14T10:01:00.000Z",
    }
    context_envelope = {
        "schema": "tiangong.context-envelope.v1",
        "life_id": LIFE_ID,
        "writer_epoch": 3,
        "cycle_id": "cyc_life_test_001",
        "soul_revision": SOUL_REVISION,
        "current_request": "请生成文档",
        "context_hash": HASH_A,
        "affective_state": {"expression_intensity": 0.25, "may_claim_execution": False},
        "active_skills": [],
    }
    context = {
        "ok": True,
        "api_contract": "tiangong.life.api.v2",
        "available": True,
        "meta": {
            "schema": "tiangong.life.context-store.v1",
            "life_id": LIFE_ID,
            "context_hash": HASH_A,
            "created_at": "2026-07-14T10:02:03.456Z",
            "plaintext_bytes": 512,
        },
        "envelope": context_envelope,
    }
    state = {
        "ok": True,
        "api_contract": "tiangong.life.api.v2",
        "setup_required": False,
        "life_id": LIFE_ID,
        "identity": active,
        "soul": {"available": True, **soul},
        "life": {"ready": True, "available": True, "phase": "alive", "status": "ALIVE"},
        "ui": {
            "lifecycle": {
                "available": True,
                "phase": "alive",
                "projection_status": "ready",
                "source_sequence": 11,
            },
            "context": {
                "available": True,
                "life_id": LIFE_ID,
                "writer_epoch": 3,
                "current_writer_epoch": 3,
                "current": True,
                "verified": True,
                "context_hash": HASH_A,
            },
            "capabilities": {
                "active_skills": ["skill.document"],
                "released_tools": ["docx.create"],
                "usage": {},
            },
        },
    }
    return {
        "/health": health,
        "/api/v1/v3/state": state,
        "/api/v1/v3/life/identity/active": {
            "ok": True,
            "api_contract": "tiangong.life.api.v2",
            "setup_required": False,
            "active": active,
        },
        "/api/v1/v3/life/soul": {
            "ok": True,
            "api_contract": "tiangong.life.api.v2",
            "life_id": LIFE_ID,
            "soul": soul,
        },
        "/api/v1/v3/life/context/latest": context,
    }


class FakeLifeTransport:
    def __init__(self, fixtures: dict[str, dict[str, object]]) -> None:
        self.fixtures = fixtures
        self.requests: list[str] = []
        self.state_reads = 0

    def get_json(self, path: str) -> dict[str, object]:
        self.requests.append(path)
        if path == "/api/v1/v3/state":
            self.state_reads += 1
        return copy.deepcopy(self.fixtures[path])


class DriftingStateTransport(FakeLifeTransport):
    def get_json(self, path: str) -> dict[str, object]:
        payload = super().get_json(path)
        if path == "/api/v1/v3/state" and self.state_reads == 2:
            payload["ui"]["lifecycle"]["source_sequence"] = 12  # type: ignore[index]
        return payload


class LifeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ContentAddressedObjectStore.open(Path(self.temporary.name) / "objects", now_ms=100)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def profile(self, callsign: str = "夏平") -> LifeProfileBindings:
        return LifeProfileBindings(
            user_callsign=callsign,
            user_occupation="产品设计",
            persona_avatar_ref="avatar_qiyuan",
            persona_voice_ref="voice_qiyuan",
            user_avatar_ref="avatar_user",
        )

    def acquire(self, transport: FakeLifeTransport, **kwargs: object):
        return LifeClient(transport, self.store).acquire_snapshot(
            tenant_id="desktop",
            link_account_id="local-user",
            conversation_scope_hash=CONVERSATION_SCOPE,
            profile=self.profile(),
            **kwargs,
        )

    def test_pins_stable_projection_and_stores_context_without_following_paths(self) -> None:
        transport = FakeLifeTransport(response_fixtures())
        pinned = self.acquire(transport)
        snapshot = pinned.snapshot
        self.assertEqual(
            transport.requests,
            [
                "/health",
                "/api/v1/v3/state",
                "/api/v1/v3/life/identity/active",
                "/api/v1/v3/life/soul",
                "/api/v1/v3/life/context/latest",
                "/api/v1/v3/state",
            ],
        )
        self.assertEqual(snapshot.identity_ref, LIFE_ID)
        self.assertEqual(snapshot.identity_revision, 3)
        self.assertEqual(snapshot.revision, 11)
        self.assertEqual(snapshot.persona_name, "起源")
        self.assertEqual(snapshot.user_callsign, "夏平")
        self.assertEqual(snapshot.user_occupation, "产品设计")
        self.assertEqual(snapshot.sha256, life_snapshot_sha256(snapshot))
        self.assertEqual(pinned.upstream_context_sha256, HASH_A)
        stored = self.store.read_bytes(snapshot.compiled_context_object_id)
        self.assertEqual(snapshot.compiled_context_sha256, __import__("hashlib").sha256(stored).hexdigest())
        self.assertEqual(json.loads(stored)["context_hash"], HASH_A)
        self.assertNotIn("private-life-root", snapshot.model_dump_json())

    def test_repeated_read_is_identical_and_profile_change_is_explicit(self) -> None:
        first = self.acquire(FakeLifeTransport(response_fixtures())).snapshot
        second = self.acquire(
            FakeLifeTransport(response_fixtures()),
            expected_revision=first.revision,
            expected_sha256=first.sha256,
        ).snapshot
        self.assertEqual(first, second)
        changed = LifeClient(FakeLifeTransport(response_fixtures()), self.store).acquire_snapshot(
            tenant_id="desktop",
            link_account_id="local-user",
            conversation_scope_hash=CONVERSATION_SCOPE,
            profile=self.profile("用户甲"),
        ).snapshot
        self.assertEqual(changed.identity_ref, first.identity_ref)
        self.assertEqual(changed.compiled_context_object_id, first.compiled_context_object_id)
        self.assertNotEqual(changed.snapshot_id, first.snapshot_id)
        self.assertNotEqual(changed.sha256, first.sha256)

    def test_setup_context_drift_and_cross_response_mismatch_fail_closed(self) -> None:
        fixtures = response_fixtures()
        fixtures["/health"]["life_ready"] = False
        fixtures["/health"]["setup_required"] = True
        with self.assertRaisesRegex(LifeClientError, "life.setup_required"):
            self.acquire(FakeLifeTransport(fixtures))

        fixtures = response_fixtures()
        fixtures["/api/v1/v3/life/context/latest"]["available"] = False
        with self.assertRaisesRegex(LifeClientError, "life.context_unavailable"):
            self.acquire(FakeLifeTransport(fixtures))

        with self.assertRaisesRegex(LifeClientError, "life.projection_changed_during_read"):
            self.acquire(DriftingStateTransport(response_fixtures()))

        fixtures = response_fixtures()
        fixtures["/api/v1/v3/life/context/latest"]["envelope"]["life_id"] = "org_swapped"  # type: ignore[index]
        with self.assertRaisesRegex(LifeClientError, "life.contract.cross_response_mismatch"):
            self.acquire(FakeLifeTransport(fixtures))

    def test_wrong_pinned_revision_or_hash_is_rejected_before_object_write(self) -> None:
        with self.assertRaisesRegex(LifeClientError, "life.pinned_revision_mismatch"):
            self.acquire(FakeLifeTransport(response_fixtures()), expected_revision=99)
        with self.assertRaisesRegex(LifeClientError, "life.pinned_sha256_mismatch"):
            self.acquire(FakeLifeTransport(response_fixtures()), expected_sha256=HASH_B)
        blobs = list((Path(self.temporary.name) / "objects" / "blobs").rglob("*"))
        self.assertEqual([item for item in blobs if item.is_file()], [])


class _TransportProbeHandler(BaseHTTPRequestHandler):
    response_body = b"{}"
    response_status = 200
    content_type = "application/json"
    location = ""
    received_token = ""

    def do_GET(self) -> None:  # noqa: N802
        type(self).received_token = self.headers.get("X-Tiangong-Token", "")
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", type(self).content_type)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format: str, *args: object) -> None:
        return


class LoopbackLifeTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        _TransportProbeHandler.response_body = b'{"ok":true,"api_contract":"tiangong.life.api.v2"}'
        _TransportProbeHandler.response_status = 200
        _TransportProbeHandler.content_type = "application/json"
        _TransportProbeHandler.location = ""
        _TransportProbeHandler.received_token = ""
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _TransportProbeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def transport(self, *, limit: int = 4096) -> LoopbackLifeJsonTransport:
        return LoopbackLifeJsonTransport(
            self.base,
            desktop_token="test-secret-token",
            max_response_bytes=limit,
        )

    def test_accepts_only_loopback_origin_and_allowed_get_paths(self) -> None:
        with self.assertRaises(ValueError):
            LoopbackLifeJsonTransport("https://127.0.0.1:7175", desktop_token="token")
        with self.assertRaises(ValueError):
            LoopbackLifeJsonTransport("http://example.com:7175", desktop_token="token")
        with self.assertRaisesRegex(LifeClientError, "life.http.path_not_allowed"):
            self.transport().get_json("/api/v1/v3/life/soul/update")
        payload = self.transport().get_json("/health")
        self.assertTrue(payload["ok"])
        self.assertEqual(_TransportProbeHandler.received_token, "test-secret-token")

    def test_redirect_duplicate_key_wrong_type_and_oversize_fail_closed(self) -> None:
        _TransportProbeHandler.response_status = 302
        _TransportProbeHandler.location = "http://example.com/steal-token"
        with self.assertRaisesRegex(LifeClientError, "life.http.redirect_forbidden"):
            self.transport().get_json("/health")

        _TransportProbeHandler.response_status = 200
        _TransportProbeHandler.location = ""
        _TransportProbeHandler.response_body = b'{"ok":true,"ok":false}'
        with self.assertRaisesRegex(LifeClientError, "life.http.duplicate_json_key"):
            self.transport().get_json("/health")

        _TransportProbeHandler.response_body = b"{}"
        _TransportProbeHandler.content_type = "text/html"
        with self.assertRaisesRegex(LifeClientError, "life.http.content_type_invalid"):
            self.transport().get_json("/health")

        _TransportProbeHandler.content_type = "application/json"
        _TransportProbeHandler.response_body = b'{"value":"' + b"x" * 5000 + b'"}'
        with self.assertRaisesRegex(LifeClientError, "life.http.response_too_large"):
            self.transport(limit=1024).get_json("/health")


if __name__ == "__main__":
    unittest.main()
