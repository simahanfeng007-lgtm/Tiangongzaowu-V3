import ast
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from communication_service.adapters import AdapterHealth, AdapterRegistry
from communication_service.channel_authority import ChannelAuthorityGate
from communication_service.bootstrap import (
    CommunicationConfig,
    CommunicationConfigurationError,
    CommunicationInstanceError,
    CommunicationInstanceLease,
)
from communication_service.runtime import CommunicationRuntime
from communication_service.server import CommunicationHttpServer
from communication_service.wechat_login import WechatLoginOutcome
from contracts import canonical_json_bytes


HASH = "a" * 64
CONTROL_TOKEN = "communication-control-test-token-" + "z" * 48


class _FakeWechatLogin:
    def __init__(self) -> None:
        self.configured = False

    def start(self, payload, *, now_ms, local_tokens=()):
        return WechatLoginOutcome(
            {
                "ok": True,
                "session_key": "qr-session",
                "qrcode_url": "https://example.invalid/qr-content",
                "message": "二维码已生成，请用手机微信扫描。",
            }
        )

    def wait(self, payload, *, now_ms, existing_credentials=None):
        return WechatLoginOutcome(
            {
                "ok": True,
                "connected": True,
                "account_id": "wechat-account",
                "message": "微信已连接。",
            },
            {
                "account_id": "wechat-account",
                "bot_token": "wechat-secret-token",
                "cursor": "",
                "user_id": "wechat-user",
            },
        )

    def mark_configured(self, account_id, *, running):
        self.configured = True

    def snapshot(self, *, now_ms):
        return {"state": "available" if self.configured else "waiting_login"}


class _Adapter:
    def __init__(self, health: AdapterHealth) -> None:
        self.health = health
        self.closed = False

    def health_snapshot(self, *, now_ms: int) -> AdapterHealth:
        return self.health.model_copy(update={"observed_at_ms": now_ms}).with_computed_sha256()

    def close(self) -> None:
        self.closed = True


class CommunicationConfigTests(unittest.TestCase):
    def test_only_total_gateway_loopback_dependency_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = CommunicationConfig(
                environment="test",
                port=0,
                state_root=Path(temporary) / "state",
                total_gateway_origin="http://127.0.0.1:7184/",
            )
        self.assertEqual(config.total_gateway_origin, "http://127.0.0.1:7184")
        for origin in (
            "https://127.0.0.1:7184",
            "http://localhost:7184",
            "http://127.0.0.1:7174",
            "http://user@127.0.0.1:7184",
            "http://127.0.0.1:7184/api",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                CommunicationConfig(
                    environment="production",
                    state_root=Path("C:/safe/communication"),
                    total_gateway_origin=origin,
                )

    def test_legacy_backend_life_and_unknown_service_variables_fail_closed(self) -> None:
        base = {"APPDATA": "C:/Users/test/AppData/Roaming"}
        for name in ("TIANGONG_BACKEND_URL", "TIANGONG_LIFE_URL"):
            with self.subTest(name=name), self.assertRaises(CommunicationConfigurationError):
                CommunicationConfig.from_environment({**base, name: "http://127.0.0.1:7174"})
        with self.assertRaises(CommunicationConfigurationError):
            CommunicationConfig.from_environment(
                {**base, "TIANGONG_COMMUNICATION_MODEL_URL": "http://127.0.0.1:9999"}
            )

        token = "shadow-token-" + "a" * 40
        gateway_token = "gateway-token-" + "b" * 40
        config = CommunicationConfig.from_environment(
            {
                **base,
                "TIANGONG_COMMUNICATION_ENVIRONMENT": "test",
                "TIANGONG_COMMUNICATION_PORT": "0",
                "TIANGONG_COMMUNICATION_SHADOW_TOKEN": token,
                "TIANGONG_COMMUNICATION_GATEWAY_TOKEN": gateway_token,
            }
        )
        self.assertEqual(config.shadow_api_token, token)
        self.assertEqual(config.gateway_api_token, gateway_token)
        self.assertNotIn(token, repr(config))
        self.assertNotIn(gateway_token, repr(config))
        with self.assertRaises(CommunicationConfigurationError):
            CommunicationConfig.from_environment(
                {
                    **base,
                    "TIANGONG_COMMUNICATION_GATEWAY_TOKEN": "too-short",
                }
            )

    def test_single_instance_lease_blocks_a_second_poller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            first = CommunicationInstanceLease.acquire(root)
            try:
                with self.assertRaises(CommunicationInstanceError):
                    CommunicationInstanceLease.acquire(root)
            finally:
                first.release()
            second = CommunicationInstanceLease.acquire(root)
            second.release()


class AdapterRegistryTests(unittest.TestCase):
    def test_registry_is_account_scoped_deterministic_and_transport_only(self) -> None:
        registry = AdapterRegistry()
        health = AdapterHealth(
            channel="wechat",
            tenant_id="tenant-a",
            link_account_id="account-a",
            state="ready",
            observed_at_ms=1,
            health_sha256=HASH,
        ).with_computed_sha256()
        adapter = _Adapter(health)
        key = registry.register(adapter, now_ms=2)
        self.assertEqual(key, "wechat:tenant-a:account-a")
        self.assertEqual(tuple(registry.snapshots(now_ms=3)), (key,))
        with self.assertRaises(ValueError):
            registry.register(_Adapter(health), now_ms=4)
        registry.close()
        self.assertTrue(adapter.closed)


class CommunicationRuntimeTests(unittest.TestCase):
    def test_pending_wechat_login_outranks_an_existing_adapter_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = CommunicationConfig(
                environment="test",
                port=0,
                state_root=Path(temporary) / "state",
            )
            runtime = CommunicationRuntime.start(config, now_ms=1_000)
            try:
                class PendingLogin:
                    @staticmethod
                    def snapshot(*, now_ms):
                        return {
                            "state": "waiting_login",
                            "session_key": "fresh-session",
                            "qrcode_url": "https://example.invalid/fresh-qr",
                        }

                runtime.wechat_login = PendingLogin()
                health = AdapterHealth(
                    channel="wechat",
                    tenant_id="wechat",
                    link_account_id="old-account",
                    state="ready",
                    observed_at_ms=1_000,
                    health_sha256=HASH,
                ).with_computed_sha256()
                runtime.adapters.register(_Adapter(health), now_ms=1_001)
                payload = runtime.links_status_payload(now_ms=1_002)
                self.assertEqual(payload["links"]["wechat_direct"]["state"], "waiting_login")
                self.assertEqual(
                    payload["links"]["wechat_direct"]["qrcode_url"],
                    "https://example.invalid/fresh-qr",
                )
            finally:
                runtime.close()

    def test_runtime_owns_only_transport_ledgers_credentials_and_channel_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "state"
            config = CommunicationConfig(environment="test", port=0, state_root=root)
            runtime = CommunicationRuntime.start(config, now_ms=1_000)
            try:
                status, payload = runtime.ready_payload(now_ms=1_001)
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["adapter_count"], 0)
                self.assertTrue(payload["delivery_ticket_required"])
                self.assertFalse(payload["legacy_business_dependencies_permitted"])
                self.assertFalse(payload["channel_authority_bound"])
                self.assertFalse(payload["production_ingress_configured"])
                self.assertFalse(payload["production_ingress_effects_permitted"])
                with self.assertRaises(ValueError):
                    runtime.bind_channel_authority(
                        ChannelAuthorityGate(
                            owner_instance_id="another-instance",
                            expected_gateway_epoch=1,
                            expected_component_manifest_sha256=HASH,
                        )
                    )
                runtime.bind_channel_authority(
                    ChannelAuthorityGate(
                        owner_instance_id=runtime.instance_id,
                        expected_gateway_epoch=1,
                        expected_component_manifest_sha256=HASH,
                    )
                )
                self.assertTrue(runtime.health_payload()["channel_authority_bound"])
                names = {item.name for item in root.iterdir()}
                self.assertEqual(
                    names,
                    {
                        "communication-delivery.sqlite3",
                        "communication-delivery.sqlite3-shm",
                        "communication-delivery.sqlite3-wal",
                        "communication-attachments.sqlite3",
                        "communication-attachments.sqlite3-shm",
                        "communication-attachments.sqlite3-wal",
                        "communication-inbox.sqlite3",
                        "communication-inbox.sqlite3-shm",
                        "communication-inbox.sqlite3-wal",
                        "communication-wechat-session.sqlite3",
                        "communication-wechat-session.sqlite3-shm",
                        "communication-wechat-session.sqlite3-wal",
                        "communication-feishu-route.sqlite3",
                        "communication-feishu-route.sqlite3-shm",
                        "communication-feishu-route.sqlite3-wal",
                        "communication-credentials.sqlite3",
                        "communication-credentials.sqlite3-shm",
                        "communication-credentials.sqlite3-wal",
                        "raw-inbound",
                        "communication.instance.lock",
                    },
                )
                self.assertFalse(any("life" in name or "model" in name for name in names))
            finally:
                runtime.close()

    def test_package_imports_no_total_gateway_backend_or_life_module(self) -> None:
        root = Path(__file__).parents[1] / "src" / "communication_service"
        forbidden_roots = {"total_gateway", "backend", "life_service", "life_core"}
        imports: set[str] = set()
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])
        self.assertTrue(imports.isdisjoint(forbidden_roots), imports & forbidden_roots)

    def test_shadow_client_is_observe_only_and_token_is_not_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token = "shadow-token-" + "a" * 40
            config = CommunicationConfig(
                environment="test",
                port=0,
                state_root=Path(temporary) / "state",
                shadow_api_token=token,
            )
            runtime = CommunicationRuntime.start(config, now_ms=1_000)
            try:
                self.assertIsNotNone(runtime.shadow_mirror)
                health = runtime.health_payload()
                self.assertEqual(health["shadow_mode"], "OBSERVE_ONLY")
                self.assertFalse(health["shadow_effects_permitted"])
                self.assertNotIn(token, json.dumps(health))
            finally:
                runtime.close()


class CommunicationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        config = CommunicationConfig(
            environment="test",
            port=0,
            state_root=Path(self.temporary.name) / "state",
        )
        self.runtime = CommunicationRuntime.start(config, now_ms=1_000)
        self.runtime.config = config.model_copy(update={"gateway_api_token": CONTROL_TOKEN})
        self.server = CommunicationHttpServer(self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.runtime.close()
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        headers: dict[str, str] | None = None,
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = json.loads(response.read())
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def test_health_ready_and_read_only_link_status_keep_compatibility_contract(self) -> None:
        status, headers, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_contract"], "tiangong.communication.api.v1")
        self.assertEqual(payload["authority"], "transport_only")
        self.assertTrue(payload["delivery_ticket_required"])
        self.assertFalse(payload["legacy_business_dependencies_permitted"])
        self.assertFalse(payload["production_ingress_configured"])
        self.assertFalse(payload["production_ingress_effects_permitted"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, _, payload = self.request("GET", "/ready")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "READY")
        self.assertTrue(payload["delivery_ticket_required"])
        self.assertFalse(payload["legacy_business_dependencies_permitted"])
        status, _, payload = self.request(
            "GET",
            "/api/v1/internal/control/readiness",
            headers={"X-Tiangong-Communication-Token": CONTROL_TOKEN},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "READY")
        status, _, payload = self.request(
            "GET",
            "/api/v1/internal/control/readiness",
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["reason_code"], "communication.control.unauthorized")
        status, _, payload = self.request("GET", "/api/v1/gateway/links/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["authority"], "tiangong-total-gateway")
        self.assertEqual(payload["links"]["wechat_direct"]["state"], "missing_credentials")

    def test_direct_control_and_legacy_business_routes_are_closed(self) -> None:
        for path in (
            "/api/v1/gateway/links/settings",
            "/api/v1/gateway/links/action",
        ):
            status, _, payload = self.request("POST", path, b"{}")
            self.assertEqual(status, 403)
            self.assertEqual(
                payload["reason_code"],
                "communication.control_plane.total_gateway_only",
            )
        for path in (
            "/api/v1/gateway/internal/inbound",
            "/api/v1/chat",
            "/api/v1/life/state",
        ):
            status, _, payload = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertEqual(payload["reason_code"], "communication.route.not_found")

    def test_total_gateway_can_start_qr_login_and_confirm_without_exposing_credentials(self) -> None:
        self.runtime.wechat_login = _FakeWechatLogin()
        control_headers = {
            "Content-Type": "application/json",
            "X-Tiangong-Communication-Token": CONTROL_TOKEN,
        }
        status, _, payload = self.request(
            "POST",
            "/api/v1/internal/control/wechat/login/start",
            canonical_json_bytes({}),
            headers=control_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session_key"], "qr-session")

        status, _, payload = self.request(
            "POST",
            "/api/v1/internal/control/wechat/login/wait",
            canonical_json_bytes({"session_key": "qr-session"}),
            headers=control_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["credentials_saved"])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("wechat-secret-token", serialized)
        self.assertEqual(
            self.runtime.credentials.get("wechat", "wechat", "wechat-account")["bot_token"],
            "wechat-secret-token",
        )

        status, _, payload = self.request(
            "POST",
            "/api/v1/internal/control/wechat/login/start",
            canonical_json_bytes({}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["reason_code"], "communication.control.unauthorized")

    def test_control_plane_rejects_chunked_transfer_before_parsing_json(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        try:
            connection.putrequest(
                "POST",
                "/api/v1/internal/control/wechat/login/start",
            )
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Transfer-Encoding", "chunked")
            connection.putheader("X-Tiangong-Communication-Token", CONTROL_TOKEN)
            connection.endheaders()
            connection.send(b"2\r\n{}\r\n0\r\n\r\n")
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(
            payload["reason_code"],
            "communication.control.transfer_encoding_forbidden",
        )

    def test_control_plane_rejects_duplicate_content_length_headers(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        try:
            connection.putrequest(
                "POST",
                "/api/v1/internal/control/wechat/login/start",
            )
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "0")
            connection.putheader("Content-Length", "0")
            connection.putheader("X-Tiangong-Communication-Token", CONTROL_TOKEN)
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(
            payload["reason_code"],
            "communication.control.content_length_ambiguous",
        )


if __name__ == "__main__":
    unittest.main()
