"""Loopback-only total-gateway health, readiness, and reviewed desktop API."""

from __future__ import annotations

from .diagnostics import diagnostic_log

import json
import hmac
import os
import signal
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from contracts import canonical_json_bytes

from .bootstrap import GatewayConfig
from .attachment_ingress import AttachmentIngressError, GatewayAttachmentIngress
from .artifact_egress import ArtifactEgressError, GatewayArtifactEgress
from .channel_ingress import (
    MAX_PRODUCTION_INGRESS_BYTES,
    ChannelIngressApiError,
    ChannelIngressApiRouter,
)
from .desktop_attachment_ingress import (
    DesktopAttachmentIngress,
    DesktopAttachmentIngressError,
)
from .desktop_api import (
    MAX_DESKTOP_REQUEST_BYTES,
    DesktopApiError,
    DesktopApiRouter,
    DesktopProxyResponse,
)
from .life_action_intake import (
    LifeActionIntentApi,
    LifeActionIntentApiError,
    MAX_LIFE_ACTION_INTENT_BYTES,
)
from .omni_grant_api import (
    MAX_OMNI_GRANT_REQUEST_BYTES,
    OmniGrantApiError,
    OmniGrantInternalApiRouter,
)
from .runtime import GatewayRuntime
from .shadow_api import MAX_SHADOW_REQUEST_BYTES, ShadowApiError, ShadowApiRouter
from .skill_api import (
    MAX_SKILL_API_REQUEST_BYTES,
    SkillApiError,
    SkillInternalApiRouter,
)


class GatewayHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    # POSIX requires SO_REUSEADDR for deterministic close-and-restart cycles.
    # On Windows, SO_REUSEADDR can permit unsafe multiple binds, so keep it
    # disabled and request exclusive ownership instead.
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(
        self,
        runtime: GatewayRuntime,
        desktop_api: DesktopApiRouter | None = None,
    ) -> None:
        self.runtime = runtime
        self.desktop_api = desktop_api or DesktopApiRouter.from_environment(runtime, os.environ)
        self.shadow_api = (
            None
            if not runtime.config.shadow_api_token
            else ShadowApiRouter(runtime.store, runtime.config.shadow_api_token)
        )
        self.channel_ingress_api = (
            None
            if not runtime.config.communication_api_token
            else ChannelIngressApiRouter(runtime, runtime.config.communication_api_token)
        )
        life_action_intent_token = str(
            getattr(runtime.config, "life_action_intent_token", "") or ""
        )
        self.life_action_intent_api = (
            None
            if not life_action_intent_token
            else LifeActionIntentApi(life_action_intent_token)
        )
        orchestration = getattr(runtime, "orchestration", None)
        skill_authority = getattr(orchestration, "skill_authority", None)
        self.skill_api = (
            None
            if skill_authority is None or not runtime.config.backend_internal_token
            else SkillInternalApiRouter(
                skill_authority,
                runtime.config.backend_internal_token,
            )
        )
        omni_authority = getattr(orchestration, "omni_grant_authority", None)
        self.omni_grant_api = (
            None
            if omni_authority is None or not runtime.config.backend_internal_token
            else OmniGrantInternalApiRouter(
                omni_authority,
                runtime.config.backend_internal_token,
            )
        )
        self.attachment_ingress = GatewayAttachmentIngress(runtime.objects)
        state_root = getattr(runtime.config, "state_root", None)
        if state_root is None:
            # Compatibility/recovery adapters may intentionally expose only
            # the immutable object store and fact ledger.  Derive the staging
            # sibling from the authoritative object-store root instead of
            # requiring a full GatewayConfig at server construction time.
            object_root = getattr(runtime.objects, "root", None)
            if object_root is None:
                raise RuntimeError("desktop attachment staging authority is unavailable")
            state_root = Path(object_root).resolve(strict=True).parent
        else:
            state_root = Path(state_root).resolve(strict=False)
        self.desktop_attachment_ingress = DesktopAttachmentIngress(
            runtime.objects,
            state_root / "staging" / "desktop-attachments",
        )
        self.artifact_egress = GatewayArtifactEgress(runtime.objects, runtime.facts)
        super().__init__((runtime.config.bind_host, runtime.config.port), GatewayRequestHandler)


class GatewayRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TiangongGateway/3"
    sys_version = ""

    @property
    def gateway(self) -> GatewayHttpServer:
        return self.server  # type: ignore[return-value]

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        allow_null_origin: bool = True,
    ) -> None:
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
        if self.close_connection:
            self.send_header("Connection", "close")
        if allow_null_origin and self.headers.get("Origin") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _send_proxy(self, response: DesktopProxyResponse) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Tiangong-Gateway", "tiangong-total-gateway")
        if response.gateway_request_id:
            self.send_header("X-Tiangong-Request-Id", response.gateway_request_id)
        if self.headers.get("Origin") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(response.body)

    def _desktop_error(self, error: DesktopApiError) -> None:
        self._send_json(
            error.status,
            {
                "status": "REJECTED" if error.status < 500 else "UNAVAILABLE",
                "reason_code": error.reason_code,
            },
        )

    def _read_desktop_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise DesktopApiError(400, "desktop_api.transfer_encoding.forbidden")
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) > 1:
            raise DesktopApiError(400, "desktop_api.content_length.invalid")
        raw_length = str(content_lengths[0] if content_lengths else "0")
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise DesktopApiError(400, "desktop_api.content_length.invalid")
        length = int(raw_length)
        if length > MAX_DESKTOP_REQUEST_BYTES:
            raise DesktopApiError(413, "desktop_api.request_too_large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise DesktopApiError(400, "desktop_api.request_truncated")
        return body

    def _read_shadow_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ShadowApiError(400, "shadow_api.transfer_encoding.forbidden")
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) > 1:
            raise ShadowApiError(400, "shadow_api.content_length.invalid")
        raw_length = str(content_lengths[0] if content_lengths else "0")
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise ShadowApiError(400, "shadow_api.content_length.invalid")
        length = int(raw_length)
        if length > MAX_SHADOW_REQUEST_BYTES:
            raise ShadowApiError(413, "shadow_api.request_too_large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ShadowApiError(400, "shadow_api.request_truncated")
        return body

    def _read_channel_ingress_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ChannelIngressApiError(400, "channel_ingress.transfer_encoding.forbidden")
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) > 1:
            raise ChannelIngressApiError(400, "channel_ingress.content_length.invalid")
        raw_length = str(content_lengths[0] if content_lengths else "0")
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise ChannelIngressApiError(400, "channel_ingress.content_length.invalid")
        length = int(raw_length)
        if length > MAX_PRODUCTION_INGRESS_BYTES:
            raise ChannelIngressApiError(413, "channel_ingress.request_too_large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ChannelIngressApiError(400, "channel_ingress.request_truncated")
        return body

    def _read_omni_grant_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise OmniGrantApiError(400, "omni_grant_api.transfer_encoding_forbidden")
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) != 1:
            raise OmniGrantApiError(400, "omni_grant_api.content_length_invalid")
        raw_length = str(content_lengths[0])
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise OmniGrantApiError(400, "omni_grant_api.content_length_invalid")
        length = int(raw_length)
        if length > MAX_OMNI_GRANT_REQUEST_BYTES:
            raise OmniGrantApiError(413, "omni_grant_api.request_size_invalid")
        body = self.rfile.read(length)
        if len(body) != length:
            raise OmniGrantApiError(400, "omni_grant_api.request_truncated")
        return body

    def _dispatch_omni_grant(self) -> bool:
        if not OmniGrantInternalApiRouter.handles_path(self.path):
            return False
        router = self.gateway.omni_grant_api
        if router is None:
            self._send_json(
                503,
                {"status": "UNAVAILABLE", "reason_code": "omni_grant_api.not_configured"},
                allow_null_origin=False,
            )
            return True
        if (
            self.headers.get("Origin") is not None
            or not router.authorize(str(self.headers.get("X-Tiangong-Token") or ""))
        ):
            self._send_json(
                401,
                {"status": "REJECTED", "reason_code": "omni_grant_api.unauthorized"},
                allow_null_origin=False,
            )
            return True
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise OmniGrantApiError(415, "omni_grant_api.content_type_invalid")
            body = self._read_omni_grant_body()
            status, payload = router.dispatch(self.command, self.path, body)
            self._send_json(status, payload, allow_null_origin=False)
        except OmniGrantApiError as exc:
            self._send_json(
                exc.status,
                {
                    "status": "REJECTED" if exc.status < 500 else "UNAVAILABLE",
                    "reason_code": exc.reason_code,
                },
                allow_null_origin=False,
            )
        return True

    def _read_skill_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise SkillApiError(400, "skill_api.transfer_encoding_forbidden")
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) != 1:
            raise SkillApiError(400, "skill_api.content_length_invalid")
        raw_length = str(content_lengths[0])
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise SkillApiError(400, "skill_api.content_length_invalid")
        length = int(raw_length)
        if length > MAX_SKILL_API_REQUEST_BYTES:
            raise SkillApiError(413, "skill_api.request.size_invalid")
        body = self.rfile.read(length)
        if len(body) != length:
            raise SkillApiError(400, "skill_api.request_truncated")
        return body

    def _dispatch_skill(self) -> bool:
        if not SkillInternalApiRouter.handles_path(self.path):
            return False
        router = self.gateway.skill_api
        if router is None:
            self._send_json(
                503,
                {"status": "UNAVAILABLE", "reason_code": "skill_api.not_configured"},
                allow_null_origin=False,
            )
            return True
        if (
            self.headers.get("Origin") is not None
            or not router.authorize(str(self.headers.get("X-Tiangong-Token") or ""))
        ):
            self._send_json(
                401,
                {"status": "REJECTED", "reason_code": "skill_api.unauthorized"},
                allow_null_origin=False,
            )
            return True
        try:
            body = b"" if self.command != "POST" else self._read_skill_body()
            response = router.dispatch(
                self.command,
                self.path,
                str(self.headers.get("Content-Type") or ""),
                body,
                now_ms=__import__("time").time_ns() // 1_000_000,
            )
            self._send_json(response.status, response.payload, allow_null_origin=False)
        except SkillApiError as exc:
            self._send_json(
                exc.status,
                {
                    "status": "REJECTED" if exc.status < 500 else "UNAVAILABLE",
                    "reason_code": exc.reason_code,
                },
                allow_null_origin=False,
            )
        return True

    def _dispatch_life_action_intent(self) -> bool:
        if self.path != "/api/v1/gateway/life/action-intents":
            return False
        if self.command != "POST":
            self._send_json(405, {"status": "REJECTED", "reason_code": "life_action_intent.method_not_allowed"}, allow_null_origin=False)
            return True
        api = self.gateway.life_action_intent_api
        if api is None:
            self._send_json(503, {"status": "UNAVAILABLE", "reason_code": "life_action_intent.not_configured"}, allow_null_origin=False)
            return True
        if not api.authorize(str(self.headers.get("X-Tiangong-Token") or "")):
            self._send_json(401, {"status": "REJECTED", "reason_code": "life_action_intent.unauthorized"}, allow_null_origin=False)
            return True
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise LifeActionIntentApiError(415, "life_action_intent.content_type_invalid")
            if self.headers.get("Transfer-Encoding"):
                raise LifeActionIntentApiError(400, "life_action_intent.transfer_encoding_forbidden")
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) != 1 or not str(lengths[0]).isascii() or not str(lengths[0]).isdecimal():
                raise LifeActionIntentApiError(400, "life_action_intent.content_length_invalid")
            length = int(lengths[0])
            if length > MAX_LIFE_ACTION_INTENT_BYTES:
                raise LifeActionIntentApiError(413, "life_action_intent.size_invalid")
            body = self.rfile.read(length)
            if len(body) != length:
                raise LifeActionIntentApiError(400, "life_action_intent.request_truncated")
            response = api.submit(body, now_ms=__import__("time").time_ns() // 1_000_000)
            self._send_json(response.status_code, response.payload, allow_null_origin=False)
        except LifeActionIntentApiError as exc:
            self._send_json(exc.status, {"status": "REJECTED", "effects_started": False, "reason_code": exc.reason_code}, allow_null_origin=False)
        return True

    def _channel_ingress_error(self, error: ChannelIngressApiError) -> None:
        self._send_json(
            error.status,
            {
                "status": "REJECTED" if error.status < 500 else "UNAVAILABLE",
                "request_created": False,
                "effects_started": False,
                "completion_claimed": False,
                "reason_code": error.reason_code,
            },
            allow_null_origin=False,
        )

    def _dispatch_channel_ingress(self) -> bool:
        if not ChannelIngressApiRouter.handles_path(self.path):
            return False
        router = self.gateway.channel_ingress_api
        if router is None:
            self._channel_ingress_error(
                ChannelIngressApiError(503, "channel_ingress.not_configured")
            )
            return True
        if not router.authorize(
            str(self.headers.get("X-Tiangong-Communication-Token") or "")
        ):
            self._channel_ingress_error(
                ChannelIngressApiError(401, "channel_ingress.unauthorized")
            )
            return True
        try:
            body = self._read_channel_ingress_body()
            response = router.dispatch(self.command, self.path, self.headers, body)
            self._send_json(response.status, response.payload, allow_null_origin=False)
        except ChannelIngressApiError as error:
            self._channel_ingress_error(error)
        return True

    def _dispatch_desktop_attachment_ingress(self) -> bool:
        if self.path != "/api/v1/gateway/desktop/attachments" or self.command != "POST":
            return False
        self.close_connection = True
        router = self.gateway.desktop_api
        provided = str(self.headers.get("X-Tiangong-Token") or "")
        if (
            router is None
            or self.headers.get("Origin") is not None
            or not router.authorize(provided)
        ):
            self._send_json(
                401,
                {"ok": False, "reason_code": "desktop_attachment.unauthorized"},
                allow_null_origin=False,
            )
            return True
        if self.headers.get("Transfer-Encoding"):
            self._send_json(
                400,
                {"ok": False, "reason_code": "desktop_attachment.transfer_encoding.forbidden"},
                allow_null_origin=False,
            )
            return True
        content_lengths = self.headers.get_all("Content-Length") or []
        raw_length = str(content_lengths[0] if len(content_lengths) == 1 else "")
        if not raw_length.isascii() or not raw_length.isdecimal():
            self._send_json(
                400,
                {"ok": False, "reason_code": "desktop_attachment.content_length.invalid"},
                allow_null_origin=False,
            )
            return True
        length = int(raw_length)
        if not 1 <= length <= 536_870_912:
            self._send_json(
                413,
                {"ok": False, "reason_code": "desktop_attachment.body.size_invalid"},
                allow_null_origin=False,
            )
            return True
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            self._send_json(
                415,
                {"ok": False, "reason_code": "desktop_attachment.content_type.invalid"},
                allow_null_origin=False,
            )
            return True
        try:
            attachment = self.gateway.desktop_attachment_ingress.accept(
                str(self.headers.get("X-Tiangong-Attachment-Metadata") or ""),
                self.rfile,
                content_length=length,
            )
            payload = attachment.model_dump(mode="json")
            self._send_json(
                200,
                {
                    "ok": True,
                    "status": "uploaded",
                    "name": attachment.filename,
                    "size": attachment.size_bytes,
                    "attachment": payload,
                    **payload,
                },
                allow_null_origin=False,
            )
        except DesktopAttachmentIngressError as error:
            self._send_json(
                400,
                {"ok": False, "reason_code": error.code},
                allow_null_origin=False,
            )
        except Exception:
            self._send_json(
                503,
                {"ok": False, "reason_code": "desktop_attachment.failed"},
                allow_null_origin=False,
            )
        return True

    def _dispatch_attachment_ingress(self) -> bool:
        if self.path != "/api/v1/internal/channel/attachments":
            return False
        if self.command != "POST":
            return False
        self.close_connection = True
        expected = self.gateway.runtime.config.communication_api_token
        provided = str(self.headers.get("X-Tiangong-Communication-Token") or "")
        if (
            self.headers.get("Origin") is not None
            or not expected
            or not provided
            or not hmac.compare_digest(expected, provided)
        ):
            self._send_json(
                401,
                {"ok": False, "reason_code": "attachment_ingress.unauthorized"},
                allow_null_origin=False,
            )
            return True
        if self.headers.get("Transfer-Encoding"):
            self._send_json(
                400,
                {"ok": False, "reason_code": "attachment_ingress.transfer_encoding.forbidden"},
                allow_null_origin=False,
            )
            return True
        content_lengths = self.headers.get_all("Content-Length") or []
        raw_length = str(content_lengths[0] if len(content_lengths) == 1 else "")
        if not raw_length.isascii() or not raw_length.isdecimal():
            self._send_json(
                400,
                {"ok": False, "reason_code": "attachment_ingress.content_length.invalid"},
                allow_null_origin=False,
            )
            return True
        length = int(raw_length)
        if not 1 <= length <= 536_870_912:
            self._send_json(
                413,
                {"ok": False, "reason_code": "attachment_ingress.body.size_invalid"},
                allow_null_origin=False,
            )
            return True
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            self._send_json(
                415,
                {"ok": False, "reason_code": "attachment_ingress.content_type.invalid"},
                allow_null_origin=False,
            )
            return True
        try:
            result = self.gateway.attachment_ingress.accept(
                str(self.headers.get("X-Tiangong-Attachment-Metadata") or ""),
                self.rfile,
                content_length=length,
            )
            self._send_json(200, {"ok": True, **result}, allow_null_origin=False)
        except AttachmentIngressError as error:
            self._send_json(
                400,
                {"ok": False, "reason_code": error.code},
                allow_null_origin=False,
            )
        except Exception:
            self._send_json(
                503,
                {"ok": False, "reason_code": "attachment_ingress.failed"},
                allow_null_origin=False,
            )
        return True

    def _dispatch_artifact_egress(self) -> bool:
        if self.path != "/api/v1/internal/channel/artifacts/fetch" or self.command != "POST":
            return False
        self.close_connection = True
        expected = self.gateway.runtime.config.communication_api_token
        provided = str(self.headers.get("X-Tiangong-Communication-Token") or "")
        if (
            self.headers.get("Origin") is not None
            or not expected
            or not provided
            or not hmac.compare_digest(expected, provided)
        ):
            self._send_json(
                401,
                {"ok": False, "reason_code": "artifact_egress.unauthorized"},
                allow_null_origin=False,
            )
            return True
        if self.headers.get("Transfer-Encoding"):
            self._send_json(
                400,
                {"ok": False, "reason_code": "artifact_egress.transfer_encoding.forbidden"},
                allow_null_origin=False,
            )
            return True
        content_lengths = self.headers.get_all("Content-Length") or []
        raw_length = str(content_lengths[0] if len(content_lengths) == 1 else "")
        if not raw_length.isascii() or not raw_length.isdecimal() or not 1 <= int(raw_length) <= 65_536:
            self._send_json(
                400,
                {"ok": False, "reason_code": "artifact_egress.content_length.invalid"},
                allow_null_origin=False,
            )
            return True
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._send_json(
                415,
                {"ok": False, "reason_code": "artifact_egress.content_type.invalid"},
                allow_null_origin=False,
            )
            return True
        expected_length = int(raw_length)
        raw = self.rfile.read(expected_length)
        try:
            if len(raw) != expected_length:
                raise ValueError("artifact egress request is truncated")
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=lambda pairs: self._strict_json_pairs(pairs, "artifact_egress.json.duplicate_key"),
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if (
                not isinstance(value, dict)
                or set(value) != {"grant", "timeout_seconds"}
                or canonical_json_bytes(value) != raw
                or type(value["timeout_seconds"]) is not int
            ):
                raise ValueError("artifact egress body is invalid")
            result = self.gateway.artifact_egress.fetch(
                value["grant"],
                timeout_seconds=value["timeout_seconds"],
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(result.data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Tiangong-Content-SHA256", result.content_sha256)
            self.send_header(
                "X-Tiangong-Artifact-Manifest-SHA256",
                result.artifact_manifest_sha256,
            )
            self.send_header("Connection", "close")
            self.end_headers()
            view = memoryview(result.data)
            for offset in range(0, len(view), 1024 * 1024):
                self.wfile.write(view[offset : offset + 1024 * 1024])
        except ArtifactEgressError as error:
            self._send_json(
                409,
                {"ok": False, "reason_code": error.code},
                allow_null_origin=False,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            self._send_json(
                400,
                {"ok": False, "reason_code": "artifact_egress.request.invalid"},
                allow_null_origin=False,
            )
        except Exception:
            self._send_json(
                503,
                {"ok": False, "reason_code": "artifact_egress.failed"},
                allow_null_origin=False,
            )
        return True

    @staticmethod
    def _strict_json_pairs(pairs: list[tuple[str, Any]], code: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(code)
            result[key] = value
        return result

    def _shadow_error(self, error: ShadowApiError) -> None:
        self._send_json(
            error.status,
            {
                "status": "REJECTED" if error.status < 500 else "UNAVAILABLE",
                "mode": "OBSERVE_ONLY",
                "request_created": False,
                "effects_permitted": False,
                "reason_code": error.reason_code,
            },
            allow_null_origin=False,
        )

    def _dispatch_shadow(self) -> bool:
        if not ShadowApiRouter.handles_path(self.path):
            return False
        router = self.gateway.shadow_api
        if router is None:
            self._shadow_error(ShadowApiError(503, "shadow_api.not_configured"))
            return True
        if not router.authorize(str(self.headers.get("X-Tiangong-Shadow-Token") or "")):
            self._shadow_error(ShadowApiError(401, "shadow_api.unauthorized"))
            return True
        try:
            body = self._read_shadow_body()
            response = router.dispatch(self.command, self.path, self.headers, body)
            self._send_json(response.status, response.payload, allow_null_origin=False)
        except ShadowApiError as error:
            self._shadow_error(error)
        return True

    def _dispatch_action_fence(self) -> bool:
        """全局 action fence（草案 §12）：状态查询 + 单调递增。

        GET  /api/v1/internal/action-fence/status   → 当前 fence 状态（含 inflight/draining）
        POST /api/v1/internal/action-fence/increment → 全局递增 epoch（新 admission/dispatch 归零）
        """
        if not (self.path.startswith("/api/v1/internal/action-fence/") or self.path in ("/api/v1/internal/confirmation-retirement", "/api/v1/internal/execution-contract-epoch")):
            return False
        router = self.gateway.desktop_api
        if router is None:
            self._send_json(503, {"status": "UNAVAILABLE", "reason_code": "desktop_api.not_configured"})
            return True
        if not router.authorize(str(self.headers.get("X-Tiangong-Token") or "")):
            self._send_json(401, {"status": "REJECTED", "reason_code": "action_fence.unauthorized"})
            return True
        store = self.gateway.runtime.store
        if self.path == "/api/v1/internal/action-fence/status" and self.command == "GET":
            self._send_json(200, {"ok": True, **store.action_fence_status()})
            return True
        if self.path == "/api/v1/internal/action-fence/increment" and self.command == "POST":
            body = self._read_desktop_body()
            reason = "manual"
            try:
                import json as _json
                if body:
                    reason = str((_json.loads(body.decode("utf-8")) or {}).get("reason") or "manual")
            except Exception:
                reason = "manual"
            epoch = store.increment_action_fence(reason=reason, now_ms=__import__("time").time_ns() // 1_000_000)
            self._send_json(200, {"ok": True, "action_fence_epoch": epoch, **store.action_fence_status()})
            return True
        if self.path == "/api/v1/internal/confirmation-retirement" and self.command == "GET":
            self._send_json(200, {"ok": True, **store.confirmation_retirement_status()})
            return True
        if self.path == "/api/v1/internal/confirmation-retirement" and self.command == "POST":
            body = self._read_desktop_body()
            import json as _json
            try:
                payload = _json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._send_json(400, {"ok": False, "reason_code": "confirmation_retirement.bad_json"})
                return True
            now_ms = __import__("time").time_ns() // 1_000_000
            try:
                if payload.get("receipt") is not None:
                    digest = store.commit_confirmation_retirement_receipt(
                        receipt=payload["receipt"],
                        expected_epoch=int(payload.get("expected_epoch") or 0),
                        now_ms=now_ms,
                    )
                    self._send_json(200, {"ok": True, "receipt_sha256": digest, **store.confirmation_retirement_status()})
                else:
                    epoch = store.commit_confirmation_retirement(
                        reason=str(payload.get("reason") or "G3 retirement"), now_ms=now_ms,
                    )
                    self._send_json(200, {"ok": True, "confirmation_retirement_epoch": epoch, **store.confirmation_retirement_status()})
            except Exception as exc:  # noqa: BLE001 - CAS 冲突一律 409
                self._send_json(409, {"ok": False, "reason_code": f"confirmation_retirement.{type(exc).__name__}"})
            return True
        if self.path == "/api/v1/internal/execution-contract-epoch" and self.command == "GET":
            self._send_json(200, {"ok": True, **store.execution_contract_epoch_status()})
            return True
        if self.path == "/api/v1/internal/execution-contract-epoch" and self.command == "POST":
            body = self._read_desktop_body()
            import json as _json
            try:
                payload = _json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._send_json(400, {"ok": False, "reason_code": "execution_contract_epoch.bad_json"})
                return True
            now_ms = __import__("time").time_ns() // 1_000_000
            try:
                digest = store.activate_execution_contract_epoch(
                    contract_epoch=str(payload.get("contract_epoch") or "vNext"),
                    dispositions=list(payload.get("dispositions") or []),
                    now_ms=now_ms,
                )
                self._send_json(200, {"ok": True, "receipt_sha256": digest, **store.execution_contract_epoch_status()})
            except Exception as exc:  # noqa: BLE001 - 前置不满足/CAS 冲突一律 409
                self._send_json(409, {"ok": False, "reason_code": f"execution_contract_epoch.{type(exc).__name__}"})
            return True
        self._send_json(404, {"status": "NOT_FOUND", "reason_code": "http.route.not_found"})
        return True


    def _dispatch_desktop(self) -> bool:
        router = self.gateway.desktop_api
        route = DesktopApiRouter.route_for(self.command, self.path)
        if route is None:
            return False
        if router is None:
            self._send_json(
                503,
                {"status": "UNAVAILABLE", "reason_code": "desktop_api.not_configured"},
            )
            return True
        if not router.authorize(str(self.headers.get("X-Tiangong-Token") or "")):
            self._desktop_error(DesktopApiError(401, "desktop_api.unauthorized"))
            return True
        try:
            body = self._read_desktop_body()
            self._send_proxy(router.dispatch(self.command, self.path, self.headers, body))
        except DesktopApiError as error:
            self._desktop_error(error)
        return True

    def do_GET(self) -> None:
        import sys, datetime
        if self.path not in ("/health", "/ready"):
            print(f"[REQ] {datetime.datetime.now().isoformat()} GET {self.path}", file=sys.stderr, flush=True)
        if self.path == "/health":
            payload = self.gateway.runtime.health_payload()
            self._send_json(200, payload)
            return
        if self.path == "/ready":
            status, payload = self.gateway.runtime.ready_payload()
            self._send_json(status, payload)
            return
        if self._dispatch_life_action_intent():
            return
        if self._dispatch_omni_grant():
            return
        if self._dispatch_skill():
            return
        if self._dispatch_channel_ingress():
            return
        if self._dispatch_shadow():
            return
        if self._dispatch_action_fence():
            return
        if self._dispatch_desktop():
            return
        self._send_json(
            404,
            {
                "status": "NOT_FOUND",
                "reason_code": "http.route.not_found",
            },
        )

    def _method_not_allowed(self) -> None:
        if self.path == "/api/v1/policy/confirm" and self.command in {"PUT", "DELETE", "PATCH"}:
            # G3 退役：确认端点全方法 410（草案 §4.2 第 5 步），不经桌面代理
            self._send_json(410, {
                "ok": False,
                "error": "POLICY_CONFIRMATION_RETIRED",
                "error_code": "POLICY_CONFIRMATION_RETIRED",
                "retired": True,
            })
            return
        if self._dispatch_omni_grant():
            return
        if self._dispatch_skill():
            return
        if self._dispatch_channel_ingress():
            return
        if self._dispatch_shadow():
            return
        if self._dispatch_desktop():
            return
        # Drain a small, length-delimited rejected body before closing.  On
        # Windows, closing a socket with unread request bytes can turn the
        # intended JSON 405 into WSAECONNABORTED at the client.
        if not self.headers.get("Transfer-Encoding"):
            raw_length = str(self.headers.get("Content-Length") or "0")
            if raw_length.isascii() and raw_length.isdecimal():
                length = int(raw_length)
                if 0 < length <= MAX_DESKTOP_REQUEST_BYTES:
                    self.rfile.read(length)
        self.close_connection = True
        self._send_json(
            405,
            {
                "status": "METHOD_NOT_ALLOWED",
                "reason_code": "http.method.not_allowed",
            },
        )

    do_DELETE = _method_not_allowed
    do_HEAD = _method_not_allowed
    def do_OPTIONS(self) -> None:
        router = self.gateway.desktop_api
        requested_method = str(self.headers.get("Access-Control-Request-Method") or "").upper()
        requested_headers = {
            item.strip().lower()
            for item in str(self.headers.get("Access-Control-Request-Headers") or "").split(",")
            if item.strip()
        }
        route = DesktopApiRouter.route_for(requested_method, self.path)
        public_probe = requested_method == "GET" and self.path in {"/health", "/ready"}
        if (
            router is None
            or self.headers.get("Origin") != "null"
            or (route is None and not public_probe)
            or not requested_headers.issubset({"accept", "content-type", "x-tiangong-token"})
        ):
            self._method_not_allowed()
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Methods", requested_method)
        self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type, X-Tiangong-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin, Access-Control-Request-Method, Access-Control-Request-Headers")
        self.end_headers()

    do_PATCH = _method_not_allowed
    def do_POST(self) -> None:
        diagnostic_log(f"[REQ] POST {self.path}")
        if self._dispatch_life_action_intent():
            return
        if self._dispatch_omni_grant():
            return
        if self._dispatch_skill():
            return
        if self._dispatch_artifact_egress():
            return
        if self._dispatch_desktop_attachment_ingress():
            return
        if self._dispatch_attachment_ingress():
            return
        if self._dispatch_channel_ingress():
            return
        if self._dispatch_shadow():
            return
        if self._dispatch_action_fence():
            return
        if self._dispatch_desktop():
            return
        self._method_not_allowed()

    do_PUT = _method_not_allowed

    def log_message(self, _format: str, *args: object) -> None:
        return


def run_gateway(config: GatewayConfig | None = None) -> None:
    resolved = GatewayConfig.from_environment() if config is None else config
    runtime = GatewayRuntime.start(resolved)
    server: GatewayHttpServer | None = None
    previous_handlers: dict[signal.Signals, object] = {}
    try:
        server = GatewayHttpServer(runtime)

        def request_shutdown(_signum: int, _frame: object) -> None:
            assert server is not None
            threading.Thread(
                target=server.shutdown,
                name="tiangong-gateway-drain",
                daemon=True,
            ).start()

        for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            shutdown_signal = getattr(signal, signal_name, None)
            if shutdown_signal is None or shutdown_signal in previous_handlers:
                continue
            try:
                previous_handlers[shutdown_signal] = signal.getsignal(shutdown_signal)
                signal.signal(shutdown_signal, request_shutdown)
            except (OSError, ValueError):
                previous_handlers.pop(shutdown_signal, None)
        server.serve_forever(poll_interval=0.25)
    finally:
        for shutdown_signal, previous in previous_handlers.items():
            try:
                signal.signal(shutdown_signal, previous)
            except (OSError, ValueError):
                pass
        if server is not None:
            server.server_close()
        runtime.close()


__all__ = ["GatewayHttpServer", "GatewayRequestHandler", "run_gateway"]
