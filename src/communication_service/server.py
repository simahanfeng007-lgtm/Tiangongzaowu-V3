"""Loopback-only 7176 HTTP health and compatibility status surface."""

from __future__ import annotations

import json
import hmac
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from contracts import (
    ChannelOwnershipLease,
    ComponentManifest,
    DeliveryTicket,
    OutboundPlan,
    TrustBundle,
    canonical_json_bytes,
)

from .bootstrap import CommunicationConfig
from .runtime import CommunicationRuntime


def _strict_model_from_json_value(model, value):
    """Rebuild nested HTTP contracts with JSON rather than Python coercions."""

    return model.model_validate_json(canonical_json_bytes(value), strict=True)


class CommunicationHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, runtime: CommunicationRuntime) -> None:
        self.runtime = runtime
        super().__init__((runtime.config.bind_host, runtime.config.port), CommunicationRequestHandler)


class CommunicationRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TiangongCommunication/3"
    sys_version = ""

    @property
    def communication(self) -> CommunicationHttpServer:
        return self.server  # type: ignore[return-value]

    def _send_json(self, status: int, payload: dict[str, Any], *, close: bool = False) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._not_found()
            return
        if parsed.path == "/health":
            self._send_json(200, self.communication.runtime.health_payload())
            return
        if parsed.path == "/ready":
            status, payload = self.communication.runtime.ready_payload()
            self._send_json(status, payload)
            return
        if parsed.path == "/api/v1/internal/control/readiness":
            if not self._authorize_control():
                return
            status, payload = self.communication.runtime.ready_payload()
            self._send_json(status, payload)
            return
        if parsed.path == "/api/v1/gateway/links/status":
            self._send_json(200, self.communication.runtime.links_status_payload())
            return
        if parsed.path == "/api/v1/internal/control/credentials/status":
            if not self._authorize_control():
                return
            self._send_json(200, self.communication.runtime.credential_status_payload())
            return
        self._not_found()

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if not parsed.query and parsed.path == "/api/v1/internal/delivery":
            self._handle_delivery()
            return
        if not parsed.query and parsed.path.startswith("/api/v1/internal/control/"):
            self._handle_control(parsed.path)
            return
        if parsed.path in {
            "/api/v1/gateway/links/settings",
            "/api/v1/gateway/links/action",
        } and not parsed.query:
            # Consume a bounded request body before closing the connection.  On
            # Windows, closing a socket with unread client data can turn the
            # intended HTTP 403 into WSAECONNABORTED at the caller.
            self._discard_body()
            self._send_json(
                403,
                {
                    "ok": False,
                    "status": "FORBIDDEN",
                    "reason_code": "communication.control_plane.total_gateway_only",
                },
                close=True,
            )
            return
        self._method_not_allowed()

    def _authorize_control(self) -> bool:
        if self.headers.get("Origin") is not None:
            self._send_json(403, {"ok": False, "reason_code": "communication.control.origin_forbidden"}, close=True)
            return False
        provided = self.headers.get("X-Tiangong-Communication-Token", "")
        expected = self.communication.runtime.config.gateway_api_token
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            self._discard_body()
            self._send_json(401, {"ok": False, "reason_code": "communication.control.unauthorized"}, close=True)
            return False
        return True

    def _discard_body(self) -> None:
        if str(self.headers.get("Transfer-Encoding") or "").strip().casefold() == "chunked":
            self._discard_chunked_body()
            return
        raw = self.headers.get("Content-Length")
        if raw and raw.isdecimal():
            self.rfile.read(min(int(raw), self.communication.runtime.config.max_body_bytes + 1))

    def _discard_chunked_body(self) -> None:
        """Drain one bounded chunked body without interpreting its JSON payload.

        Windows can reset a TCP connection when the server closes it with unread
        client bytes, hiding the intended HTTP error response from the caller.
        The control plane still rejects transfer encoding before JSON parsing;
        this only consumes framing already sent on the socket, with strict size,
        line and timeout bounds.
        """

        connection = self.connection
        previous_timeout = connection.gettimeout()
        maximum = self.communication.runtime.config.max_body_bytes + 1
        consumed = 0
        try:
            connection.settimeout(0.25)
            for _ in range(128):
                size_line = self.rfile.readline(128)
                if not size_line.endswith(b"\r\n"):
                    return
                size_text = size_line[:-2].split(b";", 1)[0].strip()
                if not size_text:
                    return
                size = int(size_text, 16)
                if size < 0 or consumed + size > maximum:
                    return
                if size == 0:
                    for _ in range(16):
                        trailer = self.rfile.readline(1024)
                        if trailer in {b"", b"\r\n"}:
                            return
                    return
                chunk = self.rfile.read(size + 2)
                if len(chunk) != size + 2 or not chunk.endswith(b"\r\n"):
                    return
                consumed += size
        except (OSError, ValueError):
            return
        finally:
            connection.settimeout(previous_timeout)

    def _control_json(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("communication.control.transfer_encoding_forbidden")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) != 1:
            raise ValueError("communication.control.content_length_ambiguous")
        raw_length = content_lengths[0]
        if media_type != "application/json" or not raw_length.isdecimal():
            raise ValueError("communication.control.request_invalid")
        length = int(raw_length)
        if not 1 <= length <= self.communication.runtime.config.max_body_bytes:
            raise ValueError("communication.control.request_size_invalid")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("communication.control.request_truncated")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("communication.control.duplicate_json_key")
                result[key] = value
            return result

        value = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict) or canonical_json_bytes(value) != body:
            raise ValueError("communication.control.noncanonical_json")
        return value

    def _handle_control(self, path: str) -> None:
        if not self._authorize_control():
            return
        try:
            payload = self._control_json()
            now_ms = time.time_ns() // 1_000_000
            runtime = self.communication.runtime
            if path == "/api/v1/internal/control/credentials/install":
                if set(payload) != {"channel", "tenant_id", "link_account_id", "credentials"}:
                    raise ValueError("communication.control.fields_invalid")
                credentials = payload["credentials"]
                if not isinstance(credentials, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in credentials.items()
                ):
                    raise ValueError("communication.control.credentials_invalid")
                status = runtime.install_credentials(
                    channel=str(payload["channel"]),
                    tenant_id=str(payload["tenant_id"]),
                    link_account_id=str(payload["link_account_id"]),
                    values=credentials,
                    now_ms=now_ms,
                )
                result = {"configured": status.configured, "revision": status.revision, "evidence_sha256": status.evidence_sha256}
            elif path == "/api/v1/internal/control/wechat/login/start":
                result = runtime.wechat_login_start(payload, now_ms=now_ms)
            elif path == "/api/v1/internal/control/wechat/login/wait":
                result = runtime.wechat_login_wait(payload, now_ms=now_ms)
            elif path == "/api/v1/internal/control/wechat/adapter/start":
                result = runtime.wechat_adapter_start(payload, now_ms=now_ms)
            elif path == "/api/v1/internal/control/wechat/adapter/stop":
                result = runtime.wechat_adapter_stop(payload, now_ms=now_ms)
            elif path == "/api/v1/internal/control/credentials/migrate-legacy":
                if payload:
                    raise ValueError("communication.control.fields_invalid")
                statuses = runtime.migrate_legacy_credentials(now_ms=now_ms)
                result = {
                    "migrated": [
                        {
                            "channel": item.channel,
                            "tenant_id": item.tenant_id,
                            "link_account_id": item.link_account_id,
                            "revision": item.revision,
                            "evidence_sha256": item.evidence_sha256,
                        }
                        for item in statuses
                    ]
                }
            elif path == "/api/v1/internal/control/lease/install":
                if set(payload) != {"lease"}:
                    raise ValueError("communication.control.fields_invalid")
                lease = _strict_model_from_json_value(ChannelOwnershipLease, payload["lease"])
                installed = runtime.install_channel_lease(lease, now_ms=now_ms)
                started = False
                if lease.channel == "wechat":
                    started = runtime.start_wechat_adapter(
                        tenant_id=lease.tenant_id,
                        link_account_id=lease.link_account_id,
                        now_ms=now_ms,
                    )
                elif lease.channel == "feishu":
                    started = runtime.start_feishu_adapter(
                        tenant_id=lease.tenant_id,
                        link_account_id=lease.link_account_id,
                        now_ms=now_ms,
                    )
                result = {"installed": installed, "adapter_started": started, "lease_sha256": lease.lease_sha256}
            elif path == "/api/v1/internal/control/delivery/authority/install":
                if set(payload) != {"trust_bundle", "component_manifest"}:
                    raise ValueError("communication.control.fields_invalid")
                trust_bundle = _strict_model_from_json_value(
                    TrustBundle, payload["trust_bundle"]
                )
                component_manifest = _strict_model_from_json_value(
                    ComponentManifest, payload["component_manifest"]
                )
                runtime.install_delivery_authority(trust_bundle, component_manifest)
                result = {
                    "installed": True,
                    "trust_bundle_sha256": trust_bundle.bundle_sha256,
                    "component_manifest_sha256": component_manifest.manifest_sha256,
                }
            elif path == "/api/v1/internal/control/drain":
                if set(payload) != {"channel", "tenant_id", "link_account_id"}:
                    raise ValueError("communication.control.fields_invalid")
                poll_count, send_count = runtime.adapters.begin_drain(
                    channel=str(payload["channel"]),
                    tenant_id=str(payload["tenant_id"]),
                    link_account_id=str(payload["link_account_id"]),
                )
                result = {"draining": True, "poll_inflight": poll_count, "send_inflight": send_count}
            elif path == "/api/v1/internal/control/drain/facts":
                if set(payload) != {"channel", "tenant_id", "link_account_id"}:
                    raise ValueError("communication.control.fields_invalid")
                result = runtime.channel_drain_facts_payload(
                    channel=str(payload["channel"]),
                    tenant_id=str(payload["tenant_id"]),
                    link_account_id=str(payload["link_account_id"]),
                )
            else:
                self._not_found()
                return
            self._send_json(200, {"ok": True, **result})
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if self.headers.get("Transfer-Encoding"):
                self._discard_body()
            self._send_json(400, {"ok": False, "reason_code": str(exc)[:160]}, close=True)
        except Exception as exc:
            code = getattr(exc, "code", None) or "communication.control.failed"
            self._send_json(409, {"ok": False, "reason_code": str(code)[:160]}, close=True)

    def _handle_delivery(self) -> None:
        if not self._authorize_control():
            return
        ticket = None
        try:
            payload = self._control_json()
            if set(payload) != {"ticket", "plan"}:
                raise ValueError("communication.delivery.fields_invalid")
            ticket = _strict_model_from_json_value(DeliveryTicket, payload["ticket"])
            plan = _strict_model_from_json_value(OutboundPlan, payload["plan"])
            receipt = self.communication.runtime.dispatch_delivery(ticket, plan)
            self._send_json(
                200,
                {
                    "ok": True,
                    "api_contract": "tiangong.communication.api.v1",
                    "delivery_receipt": receipt.model_dump(mode="json"),
                },
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_json(
                400,
                {
                    "ok": False,
                    "reason_code": str(exc)[:160],
                    "outcome_unknown": False,
                },
                close=True,
            )
        except Exception as exc:
            code = getattr(exc, "code", None) or "communication.delivery.failed"
            record = (
                None
                if ticket is None
                else self.communication.runtime.deliveries.get(ticket.payload.effect_id)
            )
            outcome_unknown = bool(
                record is not None
                and (
                    record.state in {"SIDE_EFFECT_STARTED", "RECONCILE_REQUIRED"}
                    or (
                        record.receipt is not None
                        and record.receipt.status in {"AMBIGUOUS", "RECONCILE_REQUIRED"}
                    )
                )
            )
            self._send_json(
                409,
                {
                    "ok": False,
                    "reason_code": str(code)[:160],
                    "outcome_unknown": outcome_unknown,
                },
                close=True,
            )

    def _not_found(self) -> None:
        self._send_json(
            404,
            {"status": "NOT_FOUND", "reason_code": "communication.route.not_found"},
        )

    def _method_not_allowed(self) -> None:
        self._send_json(
            405,
            {"status": "METHOD_NOT_ALLOWED", "reason_code": "communication.method.not_allowed"},
            close=True,
        )

    do_DELETE = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_PUT = _method_not_allowed

    def log_message(self, _format: str, *args: object) -> None:
        return


def run_communication_service(config: CommunicationConfig | None = None) -> None:
    resolved = CommunicationConfig.from_environment() if config is None else config
    runtime = CommunicationRuntime.start(resolved)
    server: CommunicationHttpServer | None = None
    try:
        server = CommunicationHttpServer(runtime)
        server.serve_forever(poll_interval=0.25)
    finally:
        if server is not None:
            server.server_close()
        runtime.close()


__all__ = [
    "CommunicationHttpServer",
    "CommunicationRequestHandler",
    "run_communication_service",
]
