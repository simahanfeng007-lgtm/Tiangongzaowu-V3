"""Strict 7184 desktop control plane and bounded legacy compatibility surface.

Every forwarded method/path pair is reviewed and uses a service-specific
credential.  Generic legacy execution, run mutation, and conversation-event
business sinks are native fail-closed routes; they are never forwarded to 7174
without an ExecutionTicket boundary.
"""

from __future__ import annotations

from .diagnostics import diagnostic_log

import hmac
import hashlib
import http.client
import json
import re
import time
from dataclasses import dataclass, field
from typing import Literal, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit

from contracts import (
    AttachmentRef,
    InboundEnvelope,
    InboundScope,
    canonical_json_bytes,
    canonical_sha256,
    derive_inbound_scope_keys,
)

from .artifact_open import ArtifactOpenError
from .fact_ledger import FactLedgerCorruption, FactLedgerError
from .object_store import ObjectStoreCorruption, ObjectStoreError
from .runtime import GatewayRuntime
from .store import StoreConflictError, StoreCorruptionError, StoreError
from .ui_projection import build_gateway_ui_projection


MAX_DESKTOP_REQUEST_BYTES = 64 * 1024 * 1024
MAX_DESKTOP_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_QUERY_BYTES = 4_096
_JSON_MEDIA_TYPES = {"application/json", "application/json; charset=utf-8"}


@dataclass(frozen=True)
class DesktopRoute:
    method: Literal["GET", "POST"]
    path: str
    upstream: Literal["backend", "life", "communication", "gateway"]
    timeout_seconds: int = 30
    query_keys: frozenset[str] = frozenset()


def _route(
    method: Literal["GET", "POST"],
    path: str,
    upstream: Literal["backend", "life", "communication", "gateway"],
    *,
    timeout_seconds: int = 30,
    query_keys: tuple[str, ...] = (),
) -> DesktopRoute:
    return DesktopRoute(
        method,
        path,
        upstream,
        timeout_seconds,
        frozenset(query_keys),
    )


_ROUTES = (
    _route("GET", "/api/v1/llm/status", "backend"),
    _route("GET", "/api/v1/llm/optimization", "backend"),
    _route("GET", "/api/v1/llm/settings", "backend"),
    _route("POST", "/api/v1/llm/settings", "backend"),
    _route("GET", "/api/v1/character/state", "backend"),
    _route("GET", "/api/v1/body/settings", "backend"),
    _route("POST", "/api/v1/body/settings", "backend"),
    _route("GET", "/api/v1/body/voice/capabilities", "backend"),
    _route("POST", "/api/v1/body/voice/synthesize", "backend", timeout_seconds=60),
    _route("GET", "/api/v1/workspace/settings", "backend"),
    _route("GET", "/api/v1/policy/status", "backend"),
    _route("GET", "/api/v1/policy/settings", "backend"),
    _route("POST", "/api/v1/policy/settings", "backend"),
    # G3 退役：确认端点一律转发后端 410（POLICY_CONFIRMATION_RETIRED），archive 只读
    _route("GET", "/api/v1/policy/confirm", "backend"),
    _route("POST", "/api/v1/policy/confirm", "backend"),
    _route("GET", "/api/v1/policy/confirm/archive", "backend"),
    _route("GET", "/api/v1/v3/tools", "backend"),
    _route("GET", "/api/v1/v3/skills", "backend"),
    _route("GET", "/api/v1/v3/capabilities", "backend"),
    _route("POST", "/api/v1/v3/skills/delete", "backend"),
    _route(
        "GET",
        "/api/v1/run/status",
        "backend",
        query_keys=("request_id", "run_id", "task_id", "after_seq"),
    ),
    _route("POST", "/api/v1/knowledge/list", "backend", timeout_seconds=120),
    _route("GET", "/api/v1/knowledge/settings", "backend"),
    _route("POST", "/api/v1/knowledge/configure", "backend", timeout_seconds=120),
    _route("POST", "/api/v1/knowledge/import", "backend", timeout_seconds=300),
    _route("POST", "/api/v1/files/import", "backend", timeout_seconds=300),
    _route("POST", "/api/v1/knowledge/query", "backend", timeout_seconds=120),
    _route("POST", "/api/v1/knowledge/search", "backend", timeout_seconds=120),
    _route("POST", "/api/v1/knowledge/organize", "backend", timeout_seconds=300),
    _route("POST", "/api/v1/knowledge/export", "backend", timeout_seconds=300),
    _route("POST", "/api/v1/knowledge/remove", "backend", timeout_seconds=120),
    # Complete reviewed Life API v2 surface.  The renderer has one gateway
    # origin, so every non-execution-authority method/path declared by
    # life-api.mjs must terminate here and receive the service-internal
    # credential during forwarding.  context/compile,
    # context/compile-and-authorize and execution/prepare are
    # backend execution authority and remain renderer-closed.
    _route("GET", "/api/v1/v3/life/health", "life"),
    _route("GET", "/api/v1/v3/life/identities", "life"),
    _route("GET", "/api/v1/v3/life/identity/active", "life"),
    _route("GET", "/api/v1/v3/life/identity/audit", "life"),
    _route("GET", "/api/v1/v3/state", "life"),
    _route("GET", "/api/v1/v3/life/panel", "life"),
    _route("POST", "/api/v1/v3/life/identity/create", "life"),
    _route("POST", "/api/v1/v3/life/identity/bind", "life"),
    _route("POST", "/api/v1/v3/life/identity/activate", "life"),
    _route("POST", "/api/v1/v3/life/identity/unbind", "life"),
    _route("POST", "/api/v1/v3/life/identity/delete", "life"),
    _route("GET", "/api/v1/v3/life/soul", "life"),
    _route("GET", "/api/v1/v3/life/temperament", "life"),
    _route("GET", "/api/v1/v3/life/journal/verify", "life"),
    _route("POST", "/api/v1/v3/life/journal/migrate", "life"),
    _route("POST", "/api/v1/v3/life/projection/rebuild", "life"),
    _route("POST", "/api/v1/v3/life/projection/snapshot", "life"),
    _route("GET", "/api/v1/v3/life/memory/stats", "life"),
    _route("POST", "/api/v1/v3/life/memory/candidates", "life"),
    _route("POST", "/api/v1/v3/life/memory/assert", "life"),
    _route("POST", "/api/v1/v3/life/memory/correct", "life"),
    _route("POST", "/api/v1/v3/life/memory/status", "life"),
    _route("POST", "/api/v1/v3/life/memory/relation", "life"),
    _route("POST", "/api/v1/v3/life/memory/search", "life"),
    _route("POST", "/api/v1/v3/life/memory/turn", "life"),
    _route("POST", "/api/v1/v3/life/memory/delete", "life"),
    _route("POST", "/api/v1/v3/life/memory/rebuild-index", "life"),
    _route("GET", "/api/v1/v3/life/autonomy/tasks", "life"),
    _route("POST", "/api/v1/v3/life/autonomy/tick", "life"),
    _route("POST", "/api/v1/v3/life/autonomy/task/status", "life"),
    _route("GET", "/api/v1/v3/life/learning/activity-scope", "life"),
    _route("GET", "/api/v1/v3/life/capabilities/overlay", "life"),
    _route("POST", "/api/v1/v3/life/learning/draft", "life", timeout_seconds=120),
    _route("POST", "/api/v1/v3/life/learning/user-request", "life", timeout_seconds=300),
    _route("GET", "/api/v1/v3/life/affect", "life"),
    _route("POST", "/api/v1/v3/life/affect/appraise", "life"),
    _route("POST", "/api/v1/v3/life/affect/decay", "life"),
    _route("POST", "/api/v1/v3/life/affect/outcome", "life"),
    _route("GET", "/api/v1/v3/life/affect/expression", "life"),
    _route("GET", "/api/v1/v3/life/context/latest", "life"),
    _route("POST", "/api/v1/v3/life/context/replay", "life"),
    _route("POST", "/api/v1/v3/life/context/verify", "life"),
    _route("POST", "/api/v1/v3/life/execution/recover", "life"),
    _route("POST", "/api/v1/v3/life/execution/status", "life"),
    _route("POST", "/api/v1/v3/life/heartbeat", "life"),
    _route("POST", "/api/v1/v3/life/execution/commit", "life"),
    _route("POST", "/api/v1/v3/life/inbox/read", "life"),
    _route("POST", "/api/v1/v3/life/inbox/delete", "life"),
    _route("GET", "/api/v1/v3/life/proactive-chat/pending", "life"),
    _route("POST", "/api/v1/v3/life/proactive-chat/ack", "life"),
    _route("POST", "/api/v1/v3/life/settings", "life"),
    _route("POST", "/api/v1/v3/life/soul/update", "life"),
    _route("POST", "/api/v1/v3/life/upgrade/confirm", "life"),
    _route("POST", "/api/v1/v3/life/upgrade/cancel", "life"),
    _route("POST", "/api/v1/v3/life/upgrade/complete", "life"),
    _route("POST", "/api/v1/v3/life/capability/propose", "life"),
    _route("POST", "/api/v1/v3/life/capability/approve", "life"),
    _route("POST", "/api/v1/v3/life/capability/build", "life", timeout_seconds=300),
    _route("POST", "/api/v1/v3/life/capability/publish", "life"),
    _route("POST", "/api/v1/v3/life/capability/activate", "life"),
    _route("POST", "/api/v1/v3/life/capability/discard", "life"),
    _route("POST", "/api/v1/v3/life/capability/invoke", "life", timeout_seconds=300),
    _route("POST", "/api/v1/v3/life/capability/rollback", "life"),
    _route("POST", "/api/v1/v3/life/capability/usage", "life"),
    _route("POST", "/api/v1/v3/learning/confirm", "life"),
    _route("POST", "/api/v1/v3/learning/process-approved", "life", timeout_seconds=300),
    _route("POST", "/api/v1/v3/learning/request-activation", "life"),
    _route("POST", "/api/v1/v3/learning/activate", "life", timeout_seconds=300),
    _route("POST", "/api/v1/v3/learning/release", "life", timeout_seconds=300),
    _route("POST", "/api/v1/v3/learning/discard", "life"),
    _route("GET", "/api/v1/gateway/links/status", "communication"),
    _route("POST", "/api/v1/gateway/links/action", "communication", timeout_seconds=120),
)

_NATIVE_ROUTES = (
    _route("POST", "/api/v1/gateway/internal/inbound", "gateway"),
    _route("POST", "/api/v1/gateway/desktop/inbound", "gateway"),
    _route(
        "GET",
        "/api/v1/gateway/desktop/status",
        "gateway",
        query_keys=("request_id",),
    ),
    _route("POST", "/api/v1/run/control", "gateway"),
    _route("POST", "/api/v1/v3/life/learning/decide", "gateway", timeout_seconds=300),
    _route("POST", "/api/v1/conversation/events", "gateway"),
    _route(
        "GET",
        "/api/v1/artifacts",
        "gateway",
        query_keys=("request_id",),
    ),
    _route("POST", "/api/v1/artifacts/open", "gateway", timeout_seconds=120),
    _route("GET", "/api/v1/gateway/life-log/verify", "gateway"),
    _route("POST", "/api/v1/gateway/soul-backup/create", "gateway", timeout_seconds=300),
    _route("POST", "/api/v1/gateway/soul-backup/verify", "gateway", timeout_seconds=300),
)

_CLOSED_LEGACY_BUSINESS_ROUTES = {
    "/api/v1/gateway/internal/inbound": "desktop_api.execution.ticket_required",
    "/api/v1/conversation/events": "desktop_api.conversation.legacy_sink_closed",
}

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_GATEWAY_REQUEST_ID = re.compile(r"^req_[0-9a-f]{64}$")
_DESKTOP_INGRESS_KEYS = frozenset(
    {
        "attachments",
        "message_id",
        "presentation_request_id",
        "session_id",
        "submitted_at_ms",
        "text",
    }
)

DESKTOP_ROUTES: Mapping[tuple[str, str], DesktopRoute] = {
    (item.method, item.path): item for item in _ROUTES
}
if len(DESKTOP_ROUTES) != len(_ROUTES):  # pragma: no cover - import-time invariant
    raise RuntimeError("desktop route table contains a duplicate method/path")
NATIVE_DESKTOP_ROUTES: Mapping[tuple[str, str], DesktopRoute] = {
    (item.method, item.path): item for item in _NATIVE_ROUTES
}
if len(NATIVE_DESKTOP_ROUTES) != len(_NATIVE_ROUTES):  # pragma: no cover
    raise RuntimeError("native desktop route table contains a duplicate method/path")
if set(DESKTOP_ROUTES).intersection(NATIVE_DESKTOP_ROUTES):  # pragma: no cover
    raise RuntimeError("desktop compatibility and native routes overlap")


@dataclass(frozen=True)
class DesktopApiConfig:
    desktop_token: str = field(repr=False)
    backend_internal_token: str = field(repr=False)
    life_internal_token: str = field(repr=False)
    communication_internal_token: str = field(repr=False)
    artifact_open_token: str = ""
    backend_host: str = "127.0.0.1"
    backend_port: int = 7174
    life_host: str = "127.0.0.1"
    life_port: int = 7175
    communication_host: str = "127.0.0.1"
    communication_port: int = 7176

    def __post_init__(self) -> None:
        for name, token in (
            ("desktop API", self.desktop_token),
            ("backend internal", self.backend_internal_token),
            ("life internal", self.life_internal_token),
            ("communication internal", self.communication_internal_token),
        ):
            if len(token) < 32 or len(token) > 512:
                raise ValueError(f"{name} token length is invalid")
        if len({
            self.desktop_token,
            self.backend_internal_token,
            self.life_internal_token,
            self.communication_internal_token,
        }) != 4:
            raise ValueError("desktop and service-internal tokens must be distinct")
        if self.artifact_open_token and not 32 <= len(self.artifact_open_token) <= 512:
            raise ValueError("artifact open token length is invalid")
        for host in (self.backend_host, self.life_host, self.communication_host):
            if host != "127.0.0.1":
                raise ValueError("desktop compatibility upstream must be exact loopback")
        for port in (self.backend_port, self.life_port, self.communication_port):
            if not 1 <= port <= 65_535:
                raise ValueError("desktop compatibility upstream port is invalid")


@dataclass(frozen=True)
class DesktopProxyResponse:
    status: int
    content_type: str
    body: bytes
    gateway_request_id: str = ""


class DesktopApiError(RuntimeError):
    def __init__(self, status: int, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _strict_json_object(body: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "duplicate JSON key":
            raise DesktopApiError(400, "desktop_api.json.duplicate_key") from exc
        if detail == "non-finite JSON number":
            raise DesktopApiError(400, "desktop_api.json.non_finite") from exc
        raise DesktopApiError(400, "desktop_api.json.invalid") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopApiError(400, "desktop_api.json.invalid") from exc
    if not isinstance(decoded, dict):
        raise DesktopApiError(400, "desktop_api.json.object_required")
    return decoded


def _json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DesktopApiError(400, "desktop_api.json.unsupported_value") from exc


class DesktopApiRouter:
    def __init__(self, runtime: GatewayRuntime, config: DesktopApiConfig) -> None:
        self._runtime = runtime
        self._config = config

    @classmethod
    def from_environment(cls, runtime: GatewayRuntime, environ: Mapping[str, str]) -> "DesktopApiRouter | None":
        token = str(environ.get("TIANGONG_DESKTOP_TOKEN", ""))
        if not token:
            return None
        return cls(
            runtime,
            DesktopApiConfig(
                desktop_token=token,
                backend_internal_token=str(
                    environ.get("TIANGONG_BACKEND_INTERNAL_TOKEN", "")
                ),
                life_internal_token=str(environ.get("TIANGONG_LIFE_INTERNAL_TOKEN", "")),
                communication_internal_token=str(
                    environ.get("TIANGONG_GATEWAY_COMMUNICATION_TOKEN", "")
                ),
                artifact_open_token=str(environ.get("TIANGONG_ARTIFACT_OPEN_TOKEN", "")),
            ),
        )

    @staticmethod
    def route_for(method: str, raw_target: str) -> DesktopRoute | None:
        parsed = urlsplit(raw_target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return None
        key = (method.upper(), parsed.path)
        return DESKTOP_ROUTES.get(key) or NATIVE_DESKTOP_ROUTES.get(key)

    def authorize(self, provided_token: str) -> bool:
        return bool(provided_token) and hmac.compare_digest(
            provided_token.encode("utf-8"),
            self._config.desktop_token.encode("utf-8"),
        )

    def _validated_target(self, route: DesktopRoute, raw_target: str) -> str:
        parsed = urlsplit(raw_target)
        if parsed.path != route.path or len(parsed.query.encode("utf-8")) > MAX_QUERY_BYTES:
            raise DesktopApiError(400, "desktop_api.target.invalid")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        seen: set[str] = set()
        for key, value in pairs:
            if key in seen or key not in route.query_keys or len(value) > 512:
                raise DesktopApiError(400, "desktop_api.query.invalid")
            seen.add(key)
        if pairs and not route.query_keys:
            raise DesktopApiError(400, "desktop_api.query.forbidden")
        query = urlencode(pairs)
        return route.path if not query else f"{route.path}?{query}"

    def _upstream(self, route: DesktopRoute) -> tuple[str, int]:
        if route.upstream == "backend":
            return self._config.backend_host, self._config.backend_port
        if route.upstream == "life":
            return self._config.life_host, self._config.life_port
        if route.upstream == "communication":
            return self._config.communication_host, self._config.communication_port
        raise DesktopApiError(500, "desktop_api.native_route.forward_forbidden")

    def _artifact_request_scope(self, value: str) -> tuple[str, str | None, int | None]:
        presentation_request_id = str(value or "").strip()
        if not presentation_request_id or len(presentation_request_id) > 160:
            raise DesktopApiError(400, "desktop_api.artifact.request_id_invalid")
        gateway_request_id = presentation_request_id
        entry = self._runtime.store.get_request_entry(gateway_request_id)
        if entry is None:
            raise DesktopApiError(404, "desktop_api.artifact.request_not_found")
        snapshots = self._runtime.store.list_request_snapshots(gateway_request_id)
        request_snapshots = tuple(item for item in snapshots if item.machine == "request")
        if len(request_snapshots) > 1:
            raise StoreError("request has multiple request-state authorities")
        generation = self._runtime.store.get_generation(gateway_request_id)
        scopes = {
            (item.run_id, item.generation)
            for item in request_snapshots
        }
        if generation is not None:
            scopes.add((generation.run_id, generation.generation))
        if len(scopes) > 1:
            raise StoreError("request state and generation authority disagree")
        if not scopes:
            return gateway_request_id, None, None
        run_id, generation_number = next(iter(scopes))
        return gateway_request_id, run_id, generation_number

    @staticmethod
    def _artifact_response(status: int, payload: object) -> DesktopProxyResponse:
        return DesktopProxyResponse(
            status,
            "application/json; charset=utf-8",
            _json_bytes(payload),
        )

    @staticmethod
    def _required_opaque(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
            raise DesktopApiError(400, f"desktop_api.desktop_ingress.{key}.invalid")
        return value

    def _register_desktop_inbound(self, body: bytes, *, now_ms: int) -> DesktopProxyResponse:
        diagnostic_log(f"[REG] _register_desktop_inbound called at {now_ms}")
        if self._runtime.orchestration is None:
            raise DesktopApiError(503, "desktop_api.orchestration.not_configured")
        payload = _strict_json_object(body)
        if set(payload) != _DESKTOP_INGRESS_KEYS:
            raise DesktopApiError(400, "desktop_api.desktop_ingress.fields.invalid")
        presentation_request_id = self._required_opaque(payload, "presentation_request_id")
        session_id = self._required_opaque(payload, "session_id")
        message_id = self._required_opaque(payload, "message_id")
        text = payload.get("text")
        attachments = payload.get("attachments")
        submitted_at_ms = payload.get("submitted_at_ms")
        if not isinstance(text, str) or not text.strip() or len(text) > 100_000 or "\x00" in text:
            raise DesktopApiError(400, "desktop_api.desktop_ingress.text.invalid")
        if not isinstance(attachments, list) or len(attachments) > 20:
            raise DesktopApiError(400, "desktop_api.desktop_ingress.attachments.invalid")
        if (
            type(submitted_at_ms) is not int
            or submitted_at_ms < 0
            or submitted_at_ms > now_ms + 5_000
        ):
            raise DesktopApiError(400, "desktop_api.desktop_ingress.submitted_at_ms.invalid")
        scope = InboundScope(
            channel="desktop",
            tenant_id="desktop",
            link_account_id="desktop-local",
            conversation_ref=session_id,
            channel_message_ref=message_id,
            sender_ref="desktop-user",
        )
        keys = derive_inbound_scope_keys(scope)
        accepted_attachments: list[AttachmentRef] = []
        seen_object_ids: set[str] = set()
        total_attachment_bytes = 0
        for raw_attachment in attachments:
            if not isinstance(raw_attachment, dict):
                raise DesktopApiError(400, "desktop_api.desktop_ingress.attachments.invalid")
            if any(key in raw_attachment for key in ("path", "dataUrl", "data_url", "url")):
                raise DesktopApiError(409, "desktop_api.desktop_ingress.attachments.object_ref_required")
            try:
                attachment = AttachmentRef.model_validate(raw_attachment, strict=True)
            except ValueError as exc:
                raise DesktopApiError(400, "desktop_api.desktop_ingress.attachments.object_ref_invalid") from exc
            if (
                attachment.object_id in seen_object_ids
                or attachment.tenant_id != "desktop"
                or attachment.link_account_id != "desktop-local"
                or attachment.conversation_scope_hash != keys.conversation_scope_hash
                or attachment.source_message_ref not in {None, message_id}
            ):
                raise DesktopApiError(409, "desktop_api.desktop_ingress.attachments.scope_mismatch")
            reference = self._runtime.objects.get_reference(attachment.object_id)
            if (
                reference is None
                or reference.kind != "attachment"
                or reference.sha256 != attachment.sha256
                or reference.size_bytes != attachment.size_bytes
                or reference.tenant_id != attachment.tenant_id
                or reference.link_account_id != attachment.link_account_id
                or reference.conversation_scope_hash != attachment.conversation_scope_hash
            ):
                raise DesktopApiError(409, "desktop_api.desktop_ingress.attachments.object_binding_mismatch")
            try:
                self._runtime.objects.read_bytes(attachment.object_id)
            except (ObjectStoreError, ObjectStoreCorruption) as exc:
                raise DesktopApiError(503, "desktop_api.desktop_ingress.attachments.object_unavailable") from exc
            seen_object_ids.add(attachment.object_id)
            total_attachment_bytes += attachment.size_bytes
            if total_attachment_bytes > 536_870_912:
                raise DesktopApiError(413, "desktop_api.desktop_ingress.attachments.total_size_exceeded")
            accepted_attachments.append(attachment)
        envelope = InboundEnvelope(
            inbound_id="desktop-inbound-" + canonical_sha256(
                {
                    "channel_message_ref": message_id,
                    "conversation_scope_hash": keys.conversation_scope_hash,
                    "domain": "tiangong.gateway.desktop-inbound.v1",
                }
            ),
            channel=scope.channel,
            tenant_id=scope.tenant_id,
            link_account_id=scope.link_account_id,
            conversation_ref=scope.conversation_ref,
            conversation_scope_hash=keys.conversation_scope_hash,
            principal_scope_hash=keys.principal_scope_hash,
            message_scope_hash=keys.message_scope_hash,
            channel_message_ref=scope.channel_message_ref,
            sender_ref=scope.sender_ref,
            # ``submitted_at_ms`` is client transport evidence and may be a few
            # seconds ahead because of workstation clock skew.  The gateway
            # journal authority must record the actual server receive time;
            # otherwise an accepted future-skew timestamp can later fail the
            # store invariant ``created_at_ms >= received_at_ms`` and turn an
            # otherwise equivalent retry into a false 503.
            received_at_ms=now_ms,
            idempotency_key=keys.idempotency_key,
            channel_metadata_hash=canonical_sha256(
                {
                    "domain": "tiangong.gateway.desktop-channel-metadata.v1",
                    "presentation_request_id": presentation_request_id,
                }
            ),
            text=text,
            attachments=tuple(accepted_attachments),
        )
        try:
            registration = self._runtime.store.register_request(
                envelope,
                ingress_sha256=canonical_sha256(
                    {
                        "domain": "tiangong.gateway.desktop-ingress-record.v1",
                        "payload": payload,
                    }
                ),
                created_at_ms=now_ms,
            )
            diagnostic_log(f"[REG-OK] request_id={registration.entry.request_id}")
        except StoreConflictError as exc:
            diagnostic_log(f"[REG-ERR] StoreConflictError: {exc}")
            raise DesktopApiError(409, "desktop_api.desktop_ingress.request.conflict") from exc
        except (StoreError, ValueError) as exc:
            diagnostic_log(f"[REG-ERR] StoreError: {exc}")
            self._runtime.readiness.clear()
            raise DesktopApiError(503, "desktop_api.desktop_ingress.store.unavailable") from exc
        return self._artifact_response(
            202,
            {
                "schema": "tiangong.gateway.desktop-inbound-acceptance.v1",
                "ok": True,
                "status": "ACCEPTED",
                "presentation_request_id": presentation_request_id,
                "gateway_request_id": registration.entry.request_id,
                "queue_state": registration.queue_state,
                "duplicate": registration.duplicate,
                "effects_started": False,
                "completion_claimed": False,
            },
        )

    def _desktop_result_payload(self, request_id: str, snapshots) -> dict[str, object]:
        request_snapshot = next((item for item in snapshots if item.machine == "request"), None)
        if request_snapshot is None:
            return {}
        effects = self._runtime.store.list_effects_for_request(
            request_id,
            run_id=request_snapshot.run_id,
            generation=request_snapshot.generation,
        )
        execution = next(
            (
                item
                for item in effects
                if item.claim.effect_kind == "execution" and item.result is not None
            ),
            None,
        )
        if execution is None or execution.result is None or execution.result.fact_id is None:
            return {}
        batch = self._runtime.facts.get_batch_for_fact(execution.result.fact_id)
        if batch is None:
            return {}
        raw = self._runtime.objects.read_bytes(batch.result_payload_object_id)
        try:
            payload = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise StoreCorruptionError("desktop execution payload is invalid") from exc
        if not isinstance(payload, dict) or canonical_json_bytes(payload) != raw:
            raise StoreCorruptionError("desktop execution payload is not canonical")
        if batch.result.request_id != request_id:
            raise StoreCorruptionError("desktop execution payload belongs to another request")
        return payload

    def _desktop_error_detail(self, request_id: str, snapshots) -> dict[str, str]:
        request_snapshot = next((item for item in snapshots if item.machine == "request"), None)
        if request_snapshot is None:
            return {
                "code": "gateway.request.authority_missing",
                "service": "total-gateway",
                "message": "网关请求缺少终态权威记录。",
                "action": "请重启应用后重试；若仍失败，请导出运行日志。",
            }
        effects = self._runtime.store.list_effects_for_request(
            request_id,
            run_id=request_snapshot.run_id,
            generation=request_snapshot.generation,
        )
        execution = next(
            (
                item
                for item in effects
                if item.claim.effect_kind == "execution" and item.result is not None
            ),
            None,
        )
        code = str(
            execution.result.error_code
            if execution is not None and execution.result is not None and execution.result.error_code
            else "gateway_request_failed"
        )
        lowered = code.lower()
        if "identity" in lowered or lowered.startswith(("life.", "legacy.", "compat.life")):
            return {
                "code": code,
                "service": "life",
                "message": "生命身份未能通过完整性校验或迁移。",
                "action": "请打开“设置 → 生命系统”查看迁移报告；不要删除旧数据目录或反复新建身份。",
            }
        if code == "compat.backend.waiting_for_user":
            return {
                "code": code,
                "service": "authorization",
                "message": "该动作需要用户明确授权。",
                "action": "确认风险和目标后，在当前会话明确回复同意或授权。",
            }
        if "credential" in lowered or "api_key" in lowered or lowered.startswith("model."):
            return {
                "code": code,
                "service": "model",
                "message": "模型服务凭据缺失、无效或不可用。",
                "action": "请在“设置 → 模型服务”保存 API Key，并用连接检查确认后端已读取。",
            }
        if lowered.startswith(("backend.", "compat.backend")):
            return {
                "code": code,
                "service": "backend",
                "message": "后端执行链未能成功完成请求。",
                "action": "请按错误码检查模型、联网工具或工作区权限；原始错误已保留。",
            }
        return {
            "code": code,
            "service": "execution",
            "message": "执行链未能成功完成请求。",
            "action": "请按错误码检查运行日志；再次尝试不会覆盖本次断点。",
        }

    def _desktop_status(self, target: str, *, now_ms: int) -> DesktopProxyResponse:
        query = dict(parse_qsl(urlsplit(target).query, keep_blank_values=True, strict_parsing=True))
        request_id = str(query.get("request_id") or "")
        if _GATEWAY_REQUEST_ID.fullmatch(request_id) is None:
            raise DesktopApiError(400, "desktop_api.desktop_status.request_id.invalid")
        try:
            entry = self._runtime.store.get_request_entry(request_id)
            if entry is None:
                raise DesktopApiError(404, "desktop_api.desktop_status.request_not_found")
            queue = self._runtime.store.get_session_queue(entry.session_scope_hash)
            queue_item = next((item for item in queue if item.request_id == request_id), None)
            if queue_item is None:
                raise StoreCorruptionError("desktop request is missing its session queue item")
            snapshots = self._runtime.store.list_request_snapshots(request_id)
            request_snapshots = tuple(item for item in snapshots if item.machine == "request")
            if len(request_snapshots) > 1:
                raise StoreCorruptionError("desktop request has multiple authorities")
            request_snapshot = request_snapshots[0] if request_snapshots else None
            result_payload = self._desktop_result_payload(request_id, snapshots)
            projection = build_gateway_ui_projection(
                gateway_request_id=request_id,
                presentation_request_id=request_id,
                journal_state=queue_item.state,
                snapshots=snapshots,
                legacy_status={},
                observed_at_ms=now_ms,
            )
        except DesktopApiError:
            raise
        except (
            FactLedgerCorruption,
            FactLedgerError,
            ObjectStoreError,
            StoreError,
            ValueError,
        ) as exc:
            self._runtime.readiness.clear()
            raise DesktopApiError(503, "desktop_api.desktop_status.unavailable") from exc
        state = request_snapshot.state if request_snapshot is not None else queue_item.state
        run_id = request_snapshot.run_id if request_snapshot is not None else ""
        generation = request_snapshot.generation if request_snapshot is not None else 0
        updated_at_ms = request_snapshot.updated_at_ms if request_snapshot is not None else entry.created_at_ms
        reply = str(result_payload.get("reply_text") or "")
        run: dict[str, object] = {
            "request_id": request_id,
            "gateway_request_id": request_id,
            "run_id": run_id,
            "generation": generation,
            "status": state,
            "updated_at": str(updated_at_ms),
            "steps": [],
        }
        if reply:
            run["reply"] = reply
            run["final_response"] = reply
        if state == "FAILED":
            error_detail = self._desktop_error_detail(request_id, snapshots)
            run["error"] = error_detail["code"]
            run["error_detail"] = error_detail
        return self._artifact_response(
            200,
            {
                "schema": "tiangong.gateway.desktop-run-status.v1",
                "ok": True,
                "gateway_request_id": request_id,
                "run": run,
                "events": [],
                "event_cursor": {"next_seq": 0},
                "gateway_projection": projection.model_dump(mode="json"),
            },
        )

    def _dispatch_native(
        self,
        route: DesktopRoute,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> DesktopProxyResponse:
        if route.path == "/api/v1/v3/life/learning/decide":
            media_type = str(headers.get("Content-Type", "")).strip().lower()
            if media_type not in _JSON_MEDIA_TYPES:
                raise DesktopApiError(415, "desktop_api.content_type.invalid")
            if not body or len(body) > 256 * 1024:
                raise DesktopApiError(413 if body else 400, "desktop_api.learning_decide.size_invalid")
            payload = _strict_json_object(body)
            if not set(payload).issubset({"request", "source", "life_id"}):
                raise DesktopApiError(400, "desktop_api.learning_decide.fields_invalid")
            request = str(payload.get("request") or "").strip()
            source = str(payload.get("source") or "user_direct").strip()
            if source not in {"user_direct", "autonomous"} or (source == "user_direct" and not request):
                raise DesktopApiError(400, "desktop_api.learning_decide.request_invalid")
            life_service = self._runtime.life_service
            backend_service = self._runtime.backend_service
            if life_service is None or backend_service is None:
                raise DesktopApiError(503, "desktop_api.learning_decide.services_unavailable")
            scope_status, scope_payload, _ = life_service.request(
                "GET", "/api/v1/v3/life/learning/activity-scope", {"life_id": payload.get("life_id") or ""}, timeout_seconds=30,
            )
            if scope_status >= 400 or scope_payload.get("ok") is not True:
                raise DesktopApiError(503, "desktop_api.learning_decide.scope_unavailable")
            decision_status, decision_payload, _ = backend_service.request(
                "POST", "/api/v1/internal/learning/decision",
                {"request": request, "source": source, "activity_scope": scope_payload.get("activity_scope")},
                timeout_seconds=240,
            )
            if decision_status >= 400 or decision_payload.get("ok") is not True:
                return self._artifact_response(decision_status, {"ok": False, "reason_code": "life.learning.model_decision_failed", "model": decision_payload})
            life_path = "/api/v1/v3/life/learning/user-request" if source == "user_direct" else "/api/v1/v3/life/learning/draft"
            publish_status, publish_payload, _ = life_service.request(
                "POST", life_path,
                {"life_id": payload.get("life_id") or "", "decision": decision_payload.get("decision"), "actor": "llm_learning_router"},
                timeout_seconds=240,
            )
            return self._artifact_response(publish_status, {
                **publish_payload,
                "model_output_sha256": decision_payload.get("model_output_sha256"),
                "gateway_authority": "tiangong-total-gateway",
            })

        if route.path == "/api/v1/gateway/life-log/verify":
            if body:
                raise DesktopApiError(400, "desktop_api.get.body_forbidden")
            return self._artifact_response(200, self._runtime.life_log.verify())

        if route.path in {"/api/v1/gateway/soul-backup/create", "/api/v1/gateway/soul-backup/verify"}:
            media_type = str(headers.get("Content-Type", "")).strip().lower()
            if media_type not in _JSON_MEDIA_TYPES:
                raise DesktopApiError(415, "desktop_api.content_type.invalid")
            if not body or len(body) > 32 * 1024:
                raise DesktopApiError(413 if body else 400, "desktop_api.soul_backup.size_invalid")
            payload = _strict_json_object(body)
            passphrase = str(payload.get("passphrase") or "")
            try:
                if route.path.endswith("/create"):
                    allowed = {"passphrase", "destination"}
                    if not set(payload).issubset(allowed):
                        raise DesktopApiError(400, "desktop_api.soul_backup.fields_invalid")
                    destination = str(payload.get("destination") or "").strip()
                    result = self._runtime.soul_backup.create(
                        None if not destination else __import__("pathlib").Path(destination),
                        passphrase=passphrase,
                    )
                else:
                    if set(payload) != {"passphrase", "path"}:
                        raise DesktopApiError(400, "desktop_api.soul_backup.fields_invalid")
                    result = self._runtime.soul_backup.verify(
                        __import__("pathlib").Path(str(payload.get("path") or "")),
                        passphrase=passphrase,
                    )
            except DesktopApiError:
                raise
            except Exception as exc:
                raise DesktopApiError(400, f"desktop_api.soul_backup.failed:{type(exc).__name__}") from exc
            return self._artifact_response(200, result)

        if route.path == "/api/v1/run/control":
            media_type = str(headers.get("Content-Type", "")).strip().lower()
            if media_type not in _JSON_MEDIA_TYPES:
                raise DesktopApiError(415, "desktop_api.content_type.invalid")
            if not body or len(body) > 32 * 1024:
                raise DesktopApiError(413 if body else 400, "desktop_api.run_control.size_invalid")
            payload = _strict_json_object(body)
            action = str(payload.get("action") or "").strip().lower()
            request_id = str(payload.get("request_id") or "").strip()
            allowed_keys = {"action", "request_id"} if action == "cancel" else {"action", "request_id", "message"}
            if action not in {"cancel", "guide"} or set(payload) != allowed_keys:
                raise DesktopApiError(400, "desktop_api.run_control.fields_invalid")
            if _GATEWAY_REQUEST_ID.fullmatch(request_id) is None:
                raise DesktopApiError(400, "desktop_api.run_control.request_id_invalid")
            entry = self._runtime.store.get_request_entry(request_id)
            if entry is None:
                raise DesktopApiError(404, "desktop_api.run_control.request_not_found")
            if action == "guide":
                message = payload.get("message")
                if not isinstance(message, str) or not message.strip() or len(message) > 20_000:
                    raise DesktopApiError(400, "desktop_api.run_control.message_invalid")
            if action == "cancel":
                generation = self._runtime.store.get_generation(request_id)
                if generation is not None and generation.status == "ACTIVE":
                    try:
                        cancelled_at_ms = int(time.time() * 1_000)
                        self._runtime.store.cancel_generation(
                            request_id,
                            reason_code="desktop.user_cancelled",
                            cancelled_at_ms=cancelled_at_ms,
                        )
                        # Retire the durable session head in the same control
                        # turn. Otherwise a process exit after fencing leaves
                        # this request ACTIVE forever and every later message
                        # remains QUEUED.
                        self._runtime.store.complete_session_request(
                            entry.session_scope_hash,
                            request_id,
                            completed_at_ms=cancelled_at_ms,
                            release_generation=False,
                        )
                    except StoreConflictError as exc:
                        raise DesktopApiError(409, "desktop_api.run_control.generation_conflict") from exc
            # The renderer cannot call the historical control route directly.
            # After the gateway validates the canonical request and fences its
            # generation, forward only the bounded control command with the
            # service-internal credential.
            backend_route = _route("POST", "/api/v1/run/control", "backend", timeout_seconds=10)
            response = self._forward(backend_route, backend_route.path, _json_bytes(payload))
            try:
                decoded = _strict_json_object(response.body)
            except DesktopApiError:
                return response
            decoded["gateway_authority"] = "tiangong-total-gateway"
            decoded["gateway_request_id"] = request_id
            decoded["generation_fenced"] = action == "cancel"
            return self._artifact_response(response.status, decoded)

        closed_reason = _CLOSED_LEGACY_BUSINESS_ROUTES.get(route.path)
        if closed_reason is not None:
            media_type = str(headers.get("Content-Type", "")).strip().lower()
            if media_type not in _JSON_MEDIA_TYPES:
                raise DesktopApiError(415, "desktop_api.content_type.invalid")
            if not body or len(body) > MAX_DESKTOP_REQUEST_BYTES:
                raise DesktopApiError(
                    413 if body else 400,
                    "desktop_api.request_size.invalid",
                )
            _strict_json_object(body)
            return self._artifact_response(
                503,
                {
                    "ok": False,
                    "status": "LEGACY_BUSINESS_ROUTE_CLOSED",
                    "reason_code": closed_reason,
                    "gateway_authority": "tiangong-total-gateway",
                    "legacy_execution_permitted": False,
                },
            )

        if route.path == "/api/v1/gateway/desktop/inbound":
            media_type = str(headers.get("Content-Type", "")).strip().lower()
            if media_type not in _JSON_MEDIA_TYPES:
                raise DesktopApiError(415, "desktop_api.content_type.invalid")
            if not body or len(body) > MAX_DESKTOP_REQUEST_BYTES:
                raise DesktopApiError(413 if body else 400, "desktop_api.request_size.invalid")
            return self._register_desktop_inbound(body, now_ms=int(time.time() * 1_000))

        if route.path == "/api/v1/gateway/desktop/status":
            if body:
                raise DesktopApiError(400, "desktop_api.get.body_forbidden")
            return self._desktop_status(target, now_ms=int(time.time() * 1_000))

        if route.path == "/api/v1/artifacts":
            if body:
                raise DesktopApiError(400, "desktop_api.get.body_forbidden")
            query = dict(parse_qsl(urlsplit(target).query, keep_blank_values=True, strict_parsing=True))
            presentation_request_id = str(query.get("request_id") or "")
            try:
                gateway_request_id, run_id, generation = self._artifact_request_scope(
                    presentation_request_id
                )
                cards = () if run_id is None or generation is None else self._runtime.artifacts.list_cards(
                    gateway_request_id,
                    run_id=run_id,
                    generation=generation,
                )
            except (
                StoreError,
                FactLedgerError,
                ObjectStoreError,
                ValueError,
                ArtifactOpenError,
            ) as exc:
                self._runtime.readiness.clear()
                raise DesktopApiError(503, "desktop_api.artifact.list_unavailable") from exc
            return self._artifact_response(
                200,
                {
                    "schema": "tiangong.gateway.artifact-cards.v1",
                    "gateway_request_id": gateway_request_id,
                    "presentation_request_id": presentation_request_id,
                    "artifacts": [item.model_dump(mode="json") for item in cards],
                },
            )

        if route.path != "/api/v1/artifacts/open":  # pragma: no cover - route invariant
            raise DesktopApiError(404, "desktop_api.route.not_found")
        provided_open_token = str(headers.get("X-Tiangong-Artifact-Open-Token", ""))
        if (
            not self._config.artifact_open_token
            or not provided_open_token
            or not hmac.compare_digest(
                provided_open_token.encode("utf-8"),
                self._config.artifact_open_token.encode("utf-8"),
            )
        ):
            raise DesktopApiError(401, "desktop_api.artifact.open_unauthorized")
        media_type = str(headers.get("Content-Type", "")).strip().lower()
        if media_type not in _JSON_MEDIA_TYPES:
            raise DesktopApiError(415, "desktop_api.content_type.invalid")
        if not body or len(body) > 32 * 1024:
            raise DesktopApiError(413 if body else 400, "desktop_api.artifact.open_size_invalid")
        payload = _strict_json_object(body)
        required = {
            "gateway_request_id",
            "run_id",
            "generation",
            "artifact_revision_id",
            "manifest_sha256",
            "card_sha256",
        }
        if set(payload) != required:
            raise DesktopApiError(400, "desktop_api.artifact.open_payload_invalid")
        gateway_request_id = str(payload["gateway_request_id"] or "")
        run_id = str(payload["run_id"] or "")
        generation = payload["generation"]
        artifact_revision_id = str(payload["artifact_revision_id"] or "")
        manifest_sha256 = str(payload["manifest_sha256"] or "")
        card_sha256 = str(payload["card_sha256"] or "")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise DesktopApiError(400, "desktop_api.artifact.open_payload_invalid")
        try:
            resolved_request_id, current_run_id, current_generation = self._artifact_request_scope(
                gateway_request_id
            )
            if (
                resolved_request_id != gateway_request_id
                or current_run_id != run_id
                or current_generation != generation
            ):
                raise DesktopApiError(409, "desktop_api.artifact.open_scope_stale")
            card, local_path = self._runtime.artifacts.materialize(
                gateway_request_id=gateway_request_id,
                run_id=run_id,
                generation=generation,
                artifact_revision_id=artifact_revision_id,
                manifest_sha256=manifest_sha256,
                card_sha256=card_sha256,
            )
        except DesktopApiError:
            raise
        except ArtifactOpenError as exc:
            if exc.reason_code == "artifact.open.revision_not_found":
                raise DesktopApiError(404, exc.reason_code) from exc
            if exc.reason_code in {
                "artifact.open.manifest_binding_invalid",
                "artifact.open.card_binding_invalid",
            }:
                raise DesktopApiError(409, exc.reason_code) from exc
            if exc.reason_code in {
                "artifact.open.authority_invalid",
                "artifact.open.readback_invalid",
            }:
                self._runtime.readiness.clear()
            raise DesktopApiError(503, exc.reason_code) from exc
        except (StoreError, FactLedgerError, ObjectStoreError, OSError, ValueError) as exc:
            self._runtime.readiness.clear()
            raise DesktopApiError(503, "desktop_api.artifact.open_unavailable") from exc
        return self._artifact_response(
            200,
            {
                "schema": "tiangong.gateway.artifact-open-result.v1",
                "artifact": card.model_dump(mode="json"),
                "path": str(local_path),
            },
        )

    def _present_status_response(
        self,
        route: DesktopRoute,
        original_target: str,
        forwarded_target: str,
        response: DesktopProxyResponse,
    ) -> DesktopProxyResponse:
        if route.path != "/api/v1/run/status" or response.status != 200:
            return response
        original_query = dict(parse_qsl(urlsplit(original_target).query, keep_blank_values=True))
        forwarded_query = dict(parse_qsl(urlsplit(forwarded_target).query, keep_blank_values=True))
        try:
            decoded = _strict_json_object(response.body)
        except DesktopApiError:
            return response
        run = decoded.get("run")
        if not isinstance(run, dict):
            return response
        observed_request_id = str(run.get("request_id") or run.get("requestId") or "")
        gateway_request_id = str(forwarded_query.get("request_id") or observed_request_id)
        presentation_id = str(original_query.get("request_id") or observed_request_id)
        if not gateway_request_id or not presentation_id or observed_request_id != gateway_request_id:
            return response
        try:
            entry = self._runtime.store.get_request_entry(gateway_request_id)
            if entry is None:
                return response
            queue = self._runtime.store.get_session_queue(entry.session_scope_hash)
            queue_item = next(
                (item for item in queue if item.request_id == gateway_request_id),
                None,
            )
            if queue_item is None:
                raise StoreError("request journal queue projection is missing")
            projection = build_gateway_ui_projection(
                gateway_request_id=gateway_request_id,
                presentation_request_id=presentation_id,
                journal_state=queue_item.state,
                snapshots=self._runtime.store.list_request_snapshots(gateway_request_id),
                legacy_status=decoded,
                observed_at_ms=int(time.time() * 1_000),
            )
        except (StoreError, ValueError) as exc:
            self._runtime.readiness.clear()
            raise DesktopApiError(503, "desktop_api.projection.unavailable") from exc
        presented_run = dict(run)
        presented_run["gateway_request_id"] = gateway_request_id
        presented_run["request_id"] = presentation_id
        decoded["run"] = presented_run
        decoded["gateway_request_id"] = gateway_request_id
        decoded["presentation_request_id"] = presentation_id
        decoded["gateway_projection"] = projection.model_dump(mode="json")
        return DesktopProxyResponse(
            response.status,
            response.content_type,
            _json_bytes(decoded),
            gateway_request_id,
        )

    def _forward(self, route: DesktopRoute, target: str, body: bytes) -> DesktopProxyResponse:
        embedded_service = None
        if self._runtime.config.deployment_mode == "embedded":
            embedded_service = {
                "backend": self._runtime.backend_service,
                "life": self._runtime.life_service,
                "communication": self._runtime.communication_service,
            }.get(route.upstream)
        if embedded_service is not None:
            payload = None
            if route.method == "POST":
                payload = {} if not body else _strict_json_object(body)
            try:
                status, value, content_type = embedded_service.request(
                    route.method,
                    target,
                    payload,
                    timeout_seconds=route.timeout_seconds,
                )
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                raise DesktopApiError(503, "desktop_api.embedded_service.unavailable") from exc
            if not isinstance(value, dict):
                raise DesktopApiError(502, "desktop_api.embedded_service.non_object_json")
            raw = _json_bytes(value)
            if len(raw) > MAX_DESKTOP_RESPONSE_BYTES:
                raise DesktopApiError(502, "desktop_api.upstream.response_too_large")
            return DesktopProxyResponse(int(status), str(content_type).lower(), raw)
        host, port = self._upstream(route)
        connection = http.client.HTTPConnection(host, port, timeout=route.timeout_seconds)
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "X-Tiangong-Gateway": "tiangong-total-gateway",
            }
            if route.upstream == "backend":
                headers["X-Tiangong-Token"] = self._config.backend_internal_token
            elif route.upstream == "life":
                headers["X-Tiangong-Token"] = self._config.life_internal_token
            elif route.upstream == "communication" and target.startswith(
                "/api/v1/internal/control/"
            ):
                headers["X-Tiangong-Communication-Token"] = (
                    self._config.communication_internal_token
                )
            import sys
            print(f"[GW_FORWARD] {route.method} {target} → upstream={route.upstream} host={host}:{port}", file=sys.stderr, flush=True)
            connection.request(
                route.method,
                target,
                body=body if route.method == "POST" else None,
                headers=headers,
            )
            response = connection.getresponse()
            content_type = str(response.getheader("Content-Type") or "").lower()
            if not (
                content_type.startswith("application/json")
                or content_type.startswith("application/problem+json")
            ):
                response.read(MAX_DESKTOP_RESPONSE_BYTES + 1)
                raise DesktopApiError(502, "desktop_api.upstream.non_json")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DESKTOP_RESPONSE_BYTES:
                    raise DesktopApiError(502, "desktop_api.upstream.response_too_large")
                chunks.append(chunk)
            resp_body = b"".join(chunks)
            print(f"[GW_FORWARD] response status={response.status} body={resp_body[:500]!r}", file=sys.stderr, flush=True)
            return DesktopProxyResponse(int(response.status), content_type, resp_body)
        except DesktopApiError:
            raise
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise DesktopApiError(503, "desktop_api.upstream.unavailable") from exc
        finally:
            connection.close()

    def _dispatch_unlogged(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> DesktopProxyResponse:
        route = self.route_for(method, raw_target)
        if route is None:
            raise DesktopApiError(404, "desktop_api.route.not_found")
        if not self.authorize(str(headers.get("X-Tiangong-Token", ""))):
            raise DesktopApiError(401, "desktop_api.unauthorized")
        target = self._validated_target(route, raw_target)
        if route.upstream == "gateway":
            return self._dispatch_native(route, target, headers, body)
        if route.method == "GET":
            if body:
                raise DesktopApiError(400, "desktop_api.get.body_forbidden")
            response = self._forward(route, target, b"")
            return self._present_status_response(route, target, target, response)
        media_type = str(headers.get("Content-Type", "")).strip().lower()
        if media_type not in _JSON_MEDIA_TYPES:
            raise DesktopApiError(415, "desktop_api.content_type.invalid")
        if not body or len(body) > MAX_DESKTOP_REQUEST_BYTES:
            raise DesktopApiError(413 if body else 400, "desktop_api.request_size.invalid")
        payload = _strict_json_object(body)
        if route.path == "/api/v1/gateway/links/action":
            action = payload.pop("action", None)
            targets = {
                "wechat_direct_login_start": "/api/v1/internal/control/wechat/login/start",
                "wechat_direct_login_wait": "/api/v1/internal/control/wechat/login/wait",
                "wechat_direct_start": "/api/v1/internal/control/wechat/adapter/start",
                "wechat_direct_stop": "/api/v1/internal/control/wechat/adapter/stop",
            }
            if not isinstance(action, str) or action not in targets:
                raise DesktopApiError(400, "desktop_api.communication.action_invalid")
            target = targets[action]
        return self._forward(route, target, _json_bytes(payload))

    def dispatch(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> DesktopProxyResponse:
        normalized_method = str(method or "").upper()
        route = self.route_for(normalized_method, raw_target)
        event_fields = {
            "method": normalized_method,
            "path": urlsplit(raw_target).path,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_bytes": len(body),
            "upstream": None if route is None else route.upstream,
        }
        preflight_record = None
        if normalized_method == "POST":
            if route is not None and self.authorize(str(headers.get("X-Tiangong-Token", ""))):
                try:
                    preflight_record = self._runtime.life_log.append("gateway.action.intent", event_fields)
                except Exception as exc:
                    self._runtime.readiness.clear()
                    raise DesktopApiError(503, "desktop_api.life_log.unavailable") from exc
        try:
            response = self._dispatch_unlogged(method, raw_target, headers, body)
        except DesktopApiError as exc:
            try:
                self._runtime.life_log.append(
                    "gateway.action.rejected",
                    {**event_fields, "status": exc.status, "reason_code": exc.reason_code, "intent_record_hash": str((preflight_record or {}).get("record_hash") or "")},
                )
            except Exception:
                self._runtime.readiness.clear()
            raise
        if normalized_method == "POST":
            try:
                self._runtime.life_log.append(
                    "gateway.action.completed",
                    {
                        **event_fields,
                        "status": response.status,
                        "response_sha256": hashlib.sha256(response.body).hexdigest(),
                        "gateway_request_id": response.gateway_request_id,
                        "intent_record_hash": str((preflight_record or {}).get("record_hash") or ""),
                    },
                )
            except Exception:
                # The intent record is already durable, so the action remains
                # attributable even if the completion checkpoint cannot be
                # appended. Readiness is cleared to force operator recovery.
                self._runtime.readiness.clear()
        return response


__all__ = [
    "DESKTOP_ROUTES",
    "NATIVE_DESKTOP_ROUTES",
    "MAX_DESKTOP_REQUEST_BYTES",
    "DesktopApiConfig",
    "DesktopApiError",
    "DesktopApiRouter",
    "DesktopProxyResponse",
    "DesktopRoute",
]
