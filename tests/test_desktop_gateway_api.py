from __future__ import annotations

import http.client
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from contracts import (
    InboundEnvelope,
    InboundScope,
    StateSnapshot,
    canonical_sha256,
    derive_inbound_scope_keys,
)
from total_gateway.artifact_open import DesktopArtifactCard
from total_gateway.bootstrap import GatewayConfig
from total_gateway.desktop_api import DESKTOP_ROUTES, DesktopApiConfig, DesktopApiRouter
from total_gateway.runtime import GatewayRuntime
from total_gateway.server import GatewayHttpServer


TOKEN = "desktop-test-token-" + "a" * 48
BACKEND_TOKEN = "backend-internal-test-token-" + "c" * 48
LIFE_TOKEN = "life-internal-test-token-" + "d" * 48
COMMUNICATION_TOKEN = "communication-internal-test-token-" + "e" * 48
OPEN_TOKEN = "artifact-open-test-token-" + "b" * 48


class _NoopOrchestration:
    def status_payload(self) -> dict[str, object]:
        return {"configured": True, "running": False}

    def close(self) -> None:
        return


class _UpstreamServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def upstream(self) -> _UpstreamServer:
        return self.server  # type: ignore[return-value]

    def dispatch(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        payload = json.loads(body) if body else {}
        self.upstream.calls.append(
            {
                "method": self.command,
                "path": self.path,
                "token": self.headers.get("X-Tiangong-Token"),
                "communication_token": self.headers.get("X-Tiangong-Communication-Token"),
                "gateway": self.headers.get("X-Tiangong-Gateway"),
                "payload": payload,
            }
        )
        response_payload: dict[str, object] = {
            "ok": True,
            "path": self.path,
            "request_id": payload.get("request_id", ""),
            "reply": "synthetic gateway reply" if self.path == "/api/v1/gateway/internal/inbound" else "",
        }
        if urlsplit(self.path).path == "/api/v1/run/status":
            requested = parse_qs(urlsplit(self.path).query).get("request_id", [""])[0]
            response_payload["run"] = {"request_id": requested, "status": "RUNNING"}
        encoded = json.dumps(response_payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    do_GET = dispatch
    do_POST = dispatch

    def log_message(self, _format: str, *args: object) -> None:
        return


class DesktopGatewayApiTests(unittest.TestCase):
    def test_failed_execution_exposes_original_error_code_and_recovery_action(self) -> None:
        snapshots = [SimpleNamespace(machine="request", run_id="run_test", generation=3)]
        cases = (
            (
                "life.identity_schema_mismatch",
                "life",
                "生命身份未能通过完整性校验或迁移。",
            ),
            (
                "backend.web_search.provider_unavailable",
                "backend",
                "后端执行链未能成功完成请求。",
            ),
        )
        for code, service, message in cases:
            with self.subTest(code=code):
                effect = SimpleNamespace(
                    claim=SimpleNamespace(effect_kind="execution"),
                    result=SimpleNamespace(error_code=code),
                )
                router = object.__new__(DesktopApiRouter)
                router._runtime = SimpleNamespace(
                    store=SimpleNamespace(list_effects_for_request=lambda *_args, **_kwargs: [effect])
                )
                detail = router._desktop_error_detail("req_" + "a" * 64, snapshots)
                self.assertEqual(detail["code"], code)
                self.assertEqual(detail["service"], service)
                self.assertEqual(detail["message"], message)
                self.assertTrue(detail["action"])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.upstream = _UpstreamServer()
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        config = GatewayConfig(
            environment="test",
            port=0,
            state_root=Path(self.temporary.name) / "gateway",
            min_free_bytes=1_048_576,
            disk_probe_interval_ms=100,
        )
        self.runtime = GatewayRuntime.start(config, now_ms=1_000)
        self.runtime.orchestration = _NoopOrchestration()  # type: ignore[assignment]
        upstream_port = self.upstream.server_address[1]
        router = DesktopApiRouter(
            self.runtime,
            DesktopApiConfig(
                desktop_token=TOKEN,
                backend_internal_token=BACKEND_TOKEN,
                life_internal_token=LIFE_TOKEN,
                communication_internal_token=COMMUNICATION_TOKEN,
                artifact_open_token=OPEN_TOKEN,
                backend_port=upstream_port,
                life_port=upstream_port,
                communication_port=upstream_port,
            ),
        )
        self.server = GatewayHttpServer(self.runtime, router)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=10,
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.runtime.close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=5)
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str = TOKEN,
        origin: str | None = "null",
        headers: dict[str, str] | None = None,
    ) -> tuple[http.client.HTTPResponse, dict[str, object] | None]:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request_headers = dict(headers or {})
        if token:
            request_headers["X-Tiangong-Token"] = token
        if origin is not None:
            request_headers["Origin"] = origin
        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")
            request_headers.setdefault("Content-Length", str(len(body)))
        self.connection.request(method, path, body=body, headers=request_headers)
        response = self.connection.getresponse()
        raw = response.read()
        return response, (json.loads(raw) if raw else None)

    def register_gateway_request(self, suffix: str) -> str:
        scope = InboundScope(
            channel="desktop",
            tenant_id="desktop",
            link_account_id="desktop-local",
            conversation_ref=f"desktop-conversation-{suffix}",
            channel_message_ref=f"desktop-message-{suffix}",
            sender_ref="desktop-user",
        )
        keys = derive_inbound_scope_keys(scope)
        envelope = InboundEnvelope(
            inbound_id=f"desktop-inbound-{suffix}",
            channel=scope.channel,
            tenant_id=scope.tenant_id,
            link_account_id=scope.link_account_id,
            conversation_ref=scope.conversation_ref,
            conversation_scope_hash=keys.conversation_scope_hash,
            principal_scope_hash=keys.principal_scope_hash,
            message_scope_hash=keys.message_scope_hash,
            channel_message_ref=scope.channel_message_ref,
            sender_ref=scope.sender_ref,
            received_at_ms=2_000,
            idempotency_key=keys.idempotency_key,
            channel_metadata_hash=canonical_sha256(
                {"domain": "tiangong.desktop-inbound.test.v1", "suffix": suffix}
            ),
            text="test",
        )
        return self.runtime.store.register_request(
            envelope,
            ingress_sha256=hashlib.sha256(suffix.encode()).hexdigest(),
            created_at_ms=2_000,
        ).entry.request_id

    def test_exact_allowlist_routes_three_services_and_rejects_unknown_or_bad_query(self) -> None:
        response, payload = self.request("GET", "/api/v1/llm/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["path"], "/api/v1/llm/status")
        response, payload = self.request("GET", "/api/v1/v3/life/panel")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["path"], "/api/v1/v3/life/panel")
        response, payload = self.request(
            "POST",
            "/api/v1/gateway/links/action",
            {"action": "wechat_direct_login_start"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["path"], "/api/v1/internal/control/wechat/login/start")
        response, payload = self.request(
            "GET",
            "/api/v1/run/status?request_id=req_1&after_seq=4",
        )
        self.assertEqual(response.status, 200)
        self.assertIn("after_seq=4", payload["path"])

        before = len(self.upstream.calls)
        response, payload = self.request("POST", "/chat", {"message": "forbidden"})
        self.assertEqual(response.status, 405)
        self.assertEqual(payload["reason_code"], "http.method.not_allowed")
        response, payload = self.request("GET", "/api/v1/run/status?unknown=1")
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["reason_code"], "desktop_api.query.invalid")
        self.assertEqual(len(self.upstream.calls), before)
        for call in self.upstream.calls:
            path = str(call["path"])
            if path.startswith("/api/v1/v3/life"):
                self.assertEqual(call["token"], LIFE_TOKEN)
            elif path.startswith("/api/v1/internal/control/wechat/"):
                self.assertEqual(call["communication_token"], COMMUNICATION_TOKEN)
                self.assertIsNone(call["token"])
            elif path.startswith("/api/v1/gateway/links"):
                self.assertIsNone(call["token"])
            else:
                self.assertEqual(call["token"], BACKEND_TOKEN)
            self.assertNotEqual(call["token"], TOKEN)
        self.assertTrue(all(call["gateway"] == "tiangong-total-gateway" for call in self.upstream.calls))

    def test_every_reviewed_frontend_route_forwards_with_the_service_credential(self) -> None:
        for (method, path), route in DESKTOP_ROUTES.items():
            with self.subTest(method=method, path=path, upstream=route.upstream):
                before = len(self.upstream.calls)
                probe = {"contract_probe": True} if method == "POST" else None
                if path == "/api/v1/gateway/links/action":
                    probe = {"action": "wechat_direct_login_start"}
                response, payload = self.request(
                    method,
                    path,
                    probe,
                )
                self.assertEqual(response.status, 200)
                expected_path = (
                    "/api/v1/internal/control/wechat/login/start"
                    if path == "/api/v1/gateway/links/action"
                    else path
                )
                self.assertEqual(payload["path"], expected_path)
                self.assertEqual(len(self.upstream.calls), before + 1)
                call = self.upstream.calls[-1]
                self.assertEqual(call["method"], method)
                self.assertEqual(call["path"], expected_path)
                self.assertEqual(call["gateway"], "tiangong-total-gateway")
                if route.upstream == "life":
                    self.assertEqual(call["token"], LIFE_TOKEN)
                elif route.upstream == "backend":
                    self.assertEqual(call["token"], BACKEND_TOKEN)
                else:
                    self.assertIsNone(call["token"])
                    self.assertEqual(
                        call["communication_token"],
                        COMMUNICATION_TOKEN if path == "/api/v1/gateway/links/action" else None,
                    )
                self.assertNotEqual(call["token"], TOKEN)

    def test_life_inbox_routes_accept_renderer_preflight_and_forward_to_life(self) -> None:
        for path in (
            "/api/v1/v3/life/inbox/read",
            "/api/v1/v3/life/inbox/delete",
        ):
            with self.subTest(path=path):
                before = len(self.upstream.calls)
                response, payload = self.request(
                    "OPTIONS",
                    path,
                    token="",
                    headers={
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type, x-tiangong-token",
                    },
                )
                self.assertEqual(response.status, 204)
                self.assertIsNone(payload)
                self.assertEqual(len(self.upstream.calls), before)

                response, payload = self.request(
                    "POST",
                    path,
                    {"message_id": "synthetic-message"},
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["path"], path)
                call = self.upstream.calls[-1]
                self.assertEqual(call["token"], LIFE_TOKEN)
                self.assertEqual(call["payload"], {"message_id": "synthetic-message"})

    def test_auth_cors_and_json_guards_fail_before_upstream(self) -> None:
        before = len(self.upstream.calls)
        response, payload = self.request("GET", "/api/v1/llm/status", token="wrong-token")
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["reason_code"], "desktop_api.unauthorized")
        response, payload = self.request(
            "POST",
            "/api/v1/body/settings",
            {"preset": "standard"},
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status, 415)
        self.assertEqual(payload["reason_code"], "desktop_api.content_type.invalid")
        self.assertEqual(len(self.upstream.calls), before)

        response, _ = self.request(
            "OPTIONS",
            "/api/v1/body/settings",
            token="",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-tiangong-token",
            },
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "null")
        self.assertIn("X-Tiangong-Token", response.getheader("Access-Control-Allow-Headers"))
        response, _ = self.request(
            "OPTIONS",
            "/health",
            token="",
            headers={
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-tiangong-token",
            },
        )
        self.assertEqual(response.status, 204)

    def test_service_credentials_are_required_and_cannot_reuse_renderer_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            DesktopApiConfig(
                desktop_token=TOKEN,
                backend_internal_token=TOKEN,
                life_internal_token=LIFE_TOKEN,
                communication_internal_token=COMMUNICATION_TOKEN,
            )
        with self.assertRaisesRegex(ValueError, "backend internal token length"):
            DesktopApiRouter.from_environment(
                self.runtime,
                {"TIANGONG_DESKTOP_TOKEN": TOKEN},
            )
        router = DesktopApiRouter.from_environment(
            self.runtime,
            {
                "TIANGONG_DESKTOP_TOKEN": TOKEN,
                "TIANGONG_BACKEND_INTERNAL_TOKEN": BACKEND_TOKEN,
                "TIANGONG_LIFE_INTERNAL_TOKEN": LIFE_TOKEN,
                "TIANGONG_GATEWAY_COMMUNICATION_TOKEN": COMMUNICATION_TOKEN,
            },
        )
        self.assertIsNotNone(router)

    def test_legacy_execution_and_mutation_routes_fail_closed_before_upstream(self) -> None:
        incoming = {
            "tenant_id": "desktop",
            "channel": "desktop_frontend",
            "conversation_id": "conversation-original",
            "session_id": "conversation-original",
            "request_id": "frontend-request-original",
            "message_id": "message-original",
            "text": "请生成一份文档",
            "metadata": {"gateway_frontend": "desktop_shell"},
        }
        before = len(self.upstream.calls)
        response, first = self.request(
            "POST",
            "/api/v1/gateway/internal/inbound",
            incoming,
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(first["status"], "LEGACY_BUSINESS_ROUTE_CLOSED")
        self.assertEqual(first["reason_code"], "desktop_api.execution.ticket_required")
        self.assertFalse(first["legacy_execution_permitted"])
        response, duplicate = self.request(
            "POST",
            "/api/v1/gateway/internal/inbound",
            incoming,
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(duplicate, first)
        response, control = self.request(
            "POST",
            "/api/v1/run/control",
            {"action": "cancel", "request_id": "frontend-request-original"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(
            control["reason_code"],
            "desktop_api.run_control.request_id_invalid",
        )
        response, event = self.request(
            "POST",
            "/api/v1/conversation/events",
            {"event": "legacy"},
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(event["reason_code"], "desktop_api.conversation.legacy_sink_closed")
        self.assertEqual(len(self.upstream.calls), before)
        self.assertEqual(self.runtime.store.count_journal_entries(), 0)

    def test_native_run_control_validates_gateway_request_before_forwarding(self) -> None:
        request_id = self.register_gateway_request("control")
        before = len(self.upstream.calls)
        response, control = self.request(
            "POST",
            "/api/v1/run/control",
            {"action": "cancel", "request_id": request_id},
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(control["ok"])
        self.assertEqual(control["gateway_request_id"], request_id)
        self.assertTrue(control["generation_fenced"])
        self.assertEqual(len(self.upstream.calls), before + 1)
        self.assertEqual(self.upstream.calls[-1]["path"], "/api/v1/run/control")
        self.assertEqual(self.upstream.calls[-1]["token"], BACKEND_TOKEN)

    def test_native_desktop_ingress_registers_once_and_status_reads_gateway_authority(self) -> None:
        incoming = {
            "presentation_request_id": "req_frontend_native_1",
            "session_id": "desktop-session-native-1",
            "message_id": "desktop-message-native-1",
            "submitted_at_ms": 2_500,
            "text": "直接从前端触发真实处理器",
            "attachments": [],
        }
        upstream_calls = len(self.upstream.calls)
        response, first = self.request(
            "POST",
            "/api/v1/gateway/desktop/inbound",
            incoming,
        )
        self.assertEqual(response.status, 202)
        self.assertEqual(first["schema"], "tiangong.gateway.desktop-inbound-acceptance.v1")
        self.assertTrue(first["ok"])
        self.assertFalse(first["duplicate"])
        gateway_request_id = str(first["gateway_request_id"])
        self.assertRegex(gateway_request_id, r"^req_[0-9a-f]{64}$")
        self.assertEqual(self.runtime.store.count_journal_entries(), 1)

        response, duplicate = self.request(
            "POST",
            "/api/v1/gateway/desktop/inbound",
            incoming,
        )
        self.assertEqual(response.status, 202)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["gateway_request_id"], gateway_request_id)
        self.assertEqual(self.runtime.store.count_journal_entries(), 1)

        delayed_retry = dict(incoming)
        delayed_retry["submitted_at_ms"] = incoming["submitted_at_ms"] + 250
        response, delayed = self.request(
            "POST",
            "/api/v1/gateway/desktop/inbound",
            delayed_retry,
        )
        self.assertEqual(response.status, 202)
        self.assertTrue(delayed["duplicate"])
        self.assertEqual(delayed["gateway_request_id"], gateway_request_id)
        self.assertEqual(self.runtime.store.count_journal_entries(), 1)

        # A workstation clock may be slightly ahead of the gateway.  The
        # accepted client timestamp is transport evidence only and must not
        # violate the server-side request journal time invariant.
        future_retry = dict(incoming)
        future_retry["submitted_at_ms"] = int(time.time() * 1_000) + 500
        response, future = self.request(
            "POST",
            "/api/v1/gateway/desktop/inbound",
            future_retry,
        )
        self.assertEqual(response.status, 202)
        self.assertTrue(future["duplicate"])
        self.assertEqual(future["gateway_request_id"], gateway_request_id)
        self.assertEqual(self.runtime.store.count_journal_entries(), 1)

        changed_text = dict(delayed_retry)
        changed_text["text"] = "相同消息标识不得改变业务内容"
        response, conflict = self.request(
            "POST",
            "/api/v1/gateway/desktop/inbound",
            changed_text,
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(
            conflict["reason_code"],
            "desktop_api.desktop_ingress.request.conflict",
        )

        response, status = self.request(
            "GET",
            f"/api/v1/gateway/desktop/status?request_id={gateway_request_id}",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(status["schema"], "tiangong.gateway.desktop-run-status.v1")
        self.assertEqual(status["gateway_request_id"], gateway_request_id)
        self.assertEqual(status["run"]["request_id"], gateway_request_id)
        self.assertEqual(status["gateway_projection"]["gateway_request_id"], gateway_request_id)
        self.assertEqual(len(self.upstream.calls), upstream_calls)

        rejected = dict(incoming)
        rejected["message_id"] = "desktop-message-native-file"
        rejected["attachments"] = [{"path": "C:/forbidden.txt"}]
        response, error = self.request(
            "POST",
            "/api/v1/gateway/desktop/inbound",
            rejected,
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(
            error["reason_code"],
            "desktop_api.desktop_ingress.attachments.object_ref_required",
        )
        self.assertEqual(self.runtime.store.count_journal_entries(), 1)

    def test_native_artifact_routes_do_not_proxy_and_open_requires_main_process_token(self) -> None:
        gateway_request_id = self.register_gateway_request("artifact-list")
        upstream_calls = len(self.upstream.calls)

        response, payload = self.request(
            "GET",
            f"/api/v1/artifacts?request_id={gateway_request_id}",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["schema"], "tiangong.gateway.artifact-cards.v1")
        self.assertEqual(payload["gateway_request_id"], gateway_request_id)
        self.assertEqual(payload["presentation_request_id"], gateway_request_id)
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(len(self.upstream.calls), upstream_calls)

        open_payload = {
            "gateway_request_id": gateway_request_id,
            "run_id": "run_" + "1" * 64,
            "generation": 0,
            "artifact_revision_id": "arv_" + "2" * 64,
            "manifest_sha256": "3" * 64,
            "card_sha256": "4" * 64,
        }
        response, payload = self.request(
            "POST",
            "/api/v1/artifacts/open",
            open_payload,
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["reason_code"], "desktop_api.artifact.open_unauthorized")
        response, payload = self.request(
            "POST",
            "/api/v1/artifacts/open",
            open_payload,
            headers={"X-Tiangong-Artifact-Open-Token": OPEN_TOKEN},
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["reason_code"], "desktop_api.artifact.open_scope_stale")
        self.assertEqual(len(self.upstream.calls), upstream_calls)

        response, _ = self.request(
            "OPTIONS",
            "/api/v1/artifacts/open",
            token="",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-tiangong-token, x-tiangong-artifact-open-token",
            },
        )
        self.assertEqual(response.status, 405)

    def test_native_artifact_list_and_open_bind_current_run_generation_and_card(self) -> None:
        gateway_request_id = self.register_gateway_request("artifact-success")
        run_id = "run_" + "1" * 64
        self.runtime.store.initialize_snapshot(
            StateSnapshot(
                machine="request",
                entity_id="request_artifact_success",
                request_id=gateway_request_id,
                run_id=run_id,
                generation=4,
                revision=0,
                state="RECEIVED",
                created_at_ms=2_000,
                updated_at_ms=2_000,
            )
        )
        card = DesktopArtifactCard(
            gateway_request_id=gateway_request_id,
            run_id=run_id,
            generation=4,
            artifact_id="art_" + "2" * 64,
            artifact_revision_id="arv_" + "3" * 64,
            revision=1,
            filename="verified.docx",
            size_bytes=1024,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            artifact_kind="document",
            format_id="docx",
            content_sha256="4" * 64,
            manifest_sha256="5" * 64,
            qc_checks=("docx-structure@1",),
            created_at_ms=2_500,
            card_sha256="0" * 64,
        ).with_computed_sha256()
        local_path = Path(self.temporary.name) / "materialized" / "verified.docx"
        local_path.parent.mkdir()
        local_path.write_bytes(b"x" * 1024)

        class _Artifacts:
            def list_cards(inner_self, request_id, *, run_id, generation):
                self.assertEqual((request_id, run_id, generation), (gateway_request_id, card.run_id, 4))
                return (card,)

            def materialize(inner_self, **values):
                self.assertEqual(values["gateway_request_id"], gateway_request_id)
                self.assertEqual(values["run_id"], card.run_id)
                self.assertEqual(values["generation"], card.generation)
                self.assertEqual(values["card_sha256"], card.card_sha256)
                return card, local_path

        self.runtime.artifacts = _Artifacts()
        upstream_calls = len(self.upstream.calls)
        response, payload = self.request(
            "GET",
            f"/api/v1/artifacts?request_id={gateway_request_id}",
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["artifacts"], [card.model_dump(mode="json")])
        self.assertNotIn("path", payload["artifacts"][0])

        response, payload = self.request(
            "POST",
            "/api/v1/artifacts/open",
            {
                "gateway_request_id": gateway_request_id,
                "run_id": card.run_id,
                "generation": card.generation,
                "artifact_revision_id": card.artifact_revision_id,
                "manifest_sha256": card.manifest_sha256,
                "card_sha256": card.card_sha256,
            },
            headers={"X-Tiangong-Artifact-Open-Token": OPEN_TOKEN},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["artifact"], card.model_dump(mode="json"))
        self.assertEqual(payload["path"], str(local_path))
        self.assertEqual(len(self.upstream.calls), upstream_calls)


if __name__ == "__main__":
    unittest.main()
