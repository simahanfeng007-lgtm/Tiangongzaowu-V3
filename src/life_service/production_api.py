"""P11 source-owned 7175 service gated by a signed writer handoff."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from contracts import CausalContextItem, LifeRevisionVector

from .context_api import (
    LifeContextApiError,
    LifeContextCompileAuthorizeApi,
    LifeProjectionInputs,
)
from .context_authority import merge_revision_floor
from .cutover import (
    PRODUCTION_PORT,
    LifeCowImportManifest,
    LifeCutoverError,
    LifeHandoffPermit,
    build_cutover_comparison,
    load_and_verify_handoff,
    load_cow_manifest,
)
from .legacy_adapter import LegacySnapshotReader, snapshot_tree_sha256
from .shadow_api import ShadowLifeApi
from .store import LifeShadowStore, LifeShadowStoreError


PRODUCTION_SERVICE_SCHEMA = "tiangong.life.source-service.v1"
PROJECTION_AUTHORITY_SCHEMA = "tiangong.gateway.life-view-authority.v1"
_MAX_REQUEST_BYTES = 2 * 1024 * 1024


class ProductionLifeApiError(RuntimeError):
    def __init__(self, code: str, message: str | None = None, status: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProductionLifeApiError("life.source.response_invalid", status=500) from exc


def _strict_payload(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ProductionLifeApiError("life.source.request_duplicate_key")
            result[key] = value
        return result

    def constant(_: str) -> Any:
        raise ProductionLifeApiError("life.source.request_non_finite")

    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except ProductionLifeApiError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionLifeApiError("life.source.request_json_invalid") from exc
    if not isinstance(value, dict):
        raise ProductionLifeApiError("life.source.request_not_object")
    return value


def _positive_revision(value: Any, fallback: int = 1) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else fallback


def build_legacy_revision_floor(
    reader: LegacySnapshotReader,
    manifest: LifeCowImportManifest,
    *,
    writer_epoch: int,
) -> LifeRevisionVector:
    projection = reader.projection(manifest.life_id)
    registry = reader.registry()
    soul = reader.soul(manifest.life_id)
    result = LifeRevisionVector(
        life_id=manifest.life_id,
        writer_epoch=writer_epoch,
        source_sequence=manifest.event_sequence,
        identity_revision=_positive_revision(registry.get("revision")),
        soul_revision=_positive_revision(soul.get("revision")),
        # Legacy memory is copied into the protected overlay during COW import,
        # so its row ledger already contributes to the mutable revision vector.
        memory_revision=0,
        affect_revision=1 if projection.get("affect") else 0,
        causal_revision=manifest.event_sequence,
        viability_revision=1 if projection.get("state") else 0,
        policy_revision=1 if projection.get("free_will") else 0,
        reflection_revision=1 if projection.get("reflection") else 0,
        capability_revision=1 if projection.get("capabilities") else 0,
        vector_sha256="0" * 64,
    )
    return result.with_computed_vector_sha256()


def _source_refs(revisions: LifeRevisionVector) -> dict[str, list[str]]:
    life_id = revisions.life_id
    return {
        "identity": [f"life:{life_id}:identity:{revisions.identity_revision}"],
        "soul": [f"life:{life_id}:soul:{revisions.soul_revision}"],
        "memory": [f"life:{life_id}:memory:{revisions.memory_revision}"],
        "affect": [f"life:{life_id}:affect:{revisions.affect_revision}"],
        "causal": [f"life:{life_id}:causal:{revisions.causal_revision}"],
        "viability": [f"life:{life_id}:viability:{revisions.viability_revision}"],
        "policy": [f"life:{life_id}:policy:{revisions.policy_revision}"],
        "reflection": [f"life:{life_id}:reflection:{revisions.reflection_revision}"],
        "capability": [f"life:{life_id}:capability:{revisions.capability_revision}"],
    }


def _checkpoint_text(value: Any, *, limit: int = 6_000) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).replace("\x00", "")
    text = re.sub(
        r"(?i)\b(?:authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[REDACTED]", text)
    return text[:limit]


def build_legacy_context_items(
    reader: LegacySnapshotReader,
    manifest: LifeCowImportManifest,
) -> tuple[CausalContextItem, ...]:
    """Project only a bounded final-result/breakpoint view of legacy context."""

    latest = reader.latest_context(manifest.life_id)
    if latest.get("available") is not True:
        return ()
    envelope = latest["envelope"]
    messages = envelope.get("messages", [])
    retained_messages: list[dict[str, str]] = []
    pending_tool = False
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "")
            if role == "assistant" and message.get("tool_calls"):
                pending_tool = True
                continue
            if role == "tool":
                pending_tool = True
                continue
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "assistant":
                pending_tool = False
            retained_messages.append(
                {"role": role, "content": _checkpoint_text(content)}
            )
    retained_messages = retained_messages[-2:]
    active = envelope.get("active_run")
    active_checkpoint: dict[str, str] | None = None
    if isinstance(active, Mapping):
        allowed = (
            "status",
            "request_id",
            "run_id",
            "summary",
            "latest_safe_step",
            "next_step",
        )
        active_checkpoint = {
            key: _checkpoint_text(active.get(key), limit=2_000)
            for key in allowed
            if active.get(key) not in (None, "")
        }
    memory_ids = tuple(
        sorted(
            {
                str(item.get("memory_id"))
                for item in envelope.get("memory_cards", [])
                if isinstance(item, Mapping) and item.get("memory_id")
            }
        )
    ) if isinstance(envelope.get("memory_cards", []), list) else ()
    checkpoint = {
        "schema": "tiangong.life.legacy-context-checkpoint.v1",
        "context_hash": manifest.context_hash,
        "cycle_id": _checkpoint_text(envelope.get("cycle_id"), limit=256),
        "compile_reasons": [
            _checkpoint_text(item, limit=1_000)
            for item in envelope.get("compile_reasons", [])[:32]
        ] if isinstance(envelope.get("compile_reasons"), list) else [],
        "memory_ids": memory_ids,
        "retained_messages": retained_messages,
        "active_run": active_checkpoint,
        "breakpoint": bool(pending_tool or active_checkpoint),
    }
    summary = _canonical_bytes(checkpoint).decode("utf-8")
    item = CausalContextItem(
        item_ref="legacy_context_" + hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        item_kind="artifact",
        source_revision=max(1, manifest.event_sequence),
        summary=summary,
        epistemic_status="verified",
        confidence_milli=900,
        priority=4_000,
        privacy_scope="private",
        token_count=max(1, len(summary.encode("utf-8"))),
        supporting_event_ids=(),
    )
    return (item,)


class ProductionLifeApi:
    """Dual-read legacy base + source overlay, with one mutation-capable route."""

    def __init__(
        self,
        reader: LegacySnapshotReader,
        overlay_path: Path,
        manifest: LifeCowImportManifest,
        permit: LifeHandoffPermit,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.reader = reader
        self.overlay_path = overlay_path.resolve(strict=True)
        self.manifest = manifest
        self.permit = permit
        self.compatibility = ShadowLifeApi(reader)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        if (
            permit.owner != "source_life_service"
            or permit.life_id != manifest.life_id
            or permit.writer_epoch != manifest.writer_epoch + 1
            or permit.final_manifest_sha256 != manifest.manifest_sha256
            or permit.overlay_identity_sha256 != manifest.overlay_identity_sha256
            or snapshot_tree_sha256(reader.root) != manifest.source_tree_sha256
        ):
            raise LifeCutoverError("cutover.production_binding_invalid")
        comparison = build_cutover_comparison(reader, manifest, self.overlay_path)
        if comparison["compatible"] is not True:
            raise LifeCutoverError("cutover.production_projection_mismatch")
        self.revision_floor = build_legacy_revision_floor(
            reader, manifest, writer_epoch=permit.writer_epoch
        )

    def _projection_inputs(self) -> LifeProjectionInputs:
        projection = self.reader.projection(self.manifest.life_id)
        capabilities = projection.get("capabilities", {})
        if not isinstance(capabilities, Mapping):
            capabilities = {}
        return LifeProjectionInputs(
            life_id=self.manifest.life_id,
            writer_epoch=self.permit.writer_epoch,
            identity_revision=self.revision_floor.identity_revision,
            soul=self.reader.soul(self.manifest.life_id),
            capabilities=deepcopy(dict(capabilities)),
            revision_floor=self.revision_floor,
            external_items=build_legacy_context_items(
                self.reader, self.manifest
            ),
        )

    def _current_revisions(self) -> LifeRevisionVector:
        store = LifeShadowStore.open(
            self.overlay_path, create=False, now_ms=self.manifest.imported_at_ms
        )
        try:
            raw = store.build_revision_vector(
                self.manifest.life_id,
                writer_epoch=self.permit.writer_epoch,
                identity_revision=self.revision_floor.identity_revision,
                soul_revision=self.revision_floor.soul_revision,
            )
            return merge_revision_floor(raw, self.revision_floor)
        finally:
            store.close()

    def _projection_authority(self) -> dict[str, Any]:
        revisions = self._current_revisions()
        return {
            "schema": PROJECTION_AUTHORITY_SCHEMA,
            "revisions": revisions.model_dump(mode="json"),
            "source_refs": _source_refs(revisions),
            "vector_sha256": revisions.vector_sha256,
        }

    def _decorate_read(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(payload)
        authority = self._projection_authority()
        if path in {"/health", "/api/v1/v3/life/health"}:
            result.update(
                {
                    "schema": PRODUCTION_SERVICE_SCHEMA,
                    "service": "tiangong-life-service-source",
                    "source_owned": True,
                    "source_mode": "production_single_writer",
                    "writer_epoch": self.permit.writer_epoch,
                    "writer_lease_expires_at_ms": self.permit.expires_at_ms,
                    "writer_lease_active": self._clock_ms() < self.permit.expires_at_ms,
                    "production_writer_enabled": True,
                    "writer_lease_acquisition_enabled": True,
                    "scheduler_enabled": False,
                    "side_effects_enabled": False,
                    "legacy_base_read_only": True,
                    "projection_authority": authority,
                }
            )
        elif path == "/api/v1/v3/life/panel":
            result.update(
                {
                    "shadow_read_only": False,
                    "legacy_base_read_only": True,
                    "source_overlay_writer": True,
                    "projection_authority": authority,
                }
            )
        elif path == "/api/v1/v3/state":
            result["projection_authority"] = authority
            if isinstance(result.get("ui"), dict):
                result["ui"]["projection_authority"] = deepcopy(authority)
        return result

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        verb = str(method).upper()
        route = urlsplit(path).path
        if verb == "POST" and route == "/api/v1/v3/life/context/compile-and-authorize":
            issued_at_ms = (payload or {}).get("issued_at_ms")
            request_now_ms = self._clock_ms()
            if request_now_ms >= self.permit.expires_at_ms:
                return 503, {
                    "ok": False,
                    "error_code": "life.source.writer_lease_expired",
                    "error": {
                        "code": "life.source.writer_lease_expired",
                        "message": "source writer lease expired; a signed renewal is required",
                    },
                }
            if (
                isinstance(issued_at_ms, bool)
                or not isinstance(issued_at_ms, int)
                or abs(issued_at_ms - request_now_ms) > 300_000
            ):
                return 409, {
                    "ok": False,
                    "error_code": "life.source.request_clock_invalid",
                    "error": {
                        "code": "life.source.request_clock_invalid",
                        "message": "atomic life context timestamp is outside the trusted clock window",
                    },
                }
            store = LifeShadowStore.open(
                self.overlay_path, create=False, now_ms=self.manifest.imported_at_ms
            )
            try:
                response = LifeContextCompileAuthorizeApi(store).compile_and_authorize(
                    dict(payload or {}), self._projection_inputs()
                )
                response["source_owned"] = True
                response["writer_epoch"] = self.permit.writer_epoch
                return 200, response
            except LifeContextApiError as exc:
                return 409, {
                    "ok": False,
                    "error_code": str(exc),
                    "error": {"code": str(exc), "message": "atomic life context was rejected"},
                }
            except (LifeShadowStoreError, ValueError):
                return 500, {
                    "ok": False,
                    "error_code": "life.source.atomic_failed",
                    "error": {"code": "life.source.atomic_failed", "message": "atomic life context failed closed"},
                }
            finally:
                store.close()
        status, response = self.compatibility.handle(verb, route, payload)
        if status == 200 and verb == "GET":
            return status, self._decorate_read(route, response)
        if status >= 400 and verb in {"POST", "PUT", "PATCH", "DELETE"}:
            return HTTPStatus.METHOD_NOT_ALLOWED, {
                "ok": False,
                "error_code": "life.source.mutation_forbidden",
                "error": {
                    "code": "life.source.mutation_forbidden",
                    "message": "only the atomic context authority route can mutate the source overlay",
                },
            }
        return status, response


class CutoverReadOnlyFallbackApi:
    """Serve the immutable pre-cutover snapshot after a failed active cutover."""

    def __init__(self, reader: LegacySnapshotReader) -> None:
        self.compatibility = ShadowLifeApi(reader)

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        verb = str(method).upper()
        route = urlsplit(path).path
        status, response = self.compatibility.handle(verb, route, payload)
        if status == 200 and verb == "GET" and route in {
            "/health",
            "/api/v1/v3/life/health",
        }:
            result = deepcopy(response)
            result.update(
                {
                    "schema": PRODUCTION_SERVICE_SCHEMA,
                    "service": "tiangong-life-service-cutover-fallback",
                    "source_owned": True,
                    "source_mode": "cutover_read_only_fallback",
                    "production_writer_enabled": False,
                    "writer_lease_acquisition_enabled": False,
                    "scheduler_enabled": False,
                    "side_effects_enabled": False,
                    "legacy_base_read_only": True,
                    "cutover_recovery_required": True,
                }
            )
            return 200, result
        if status >= 400 and verb in {"POST", "PUT", "PATCH", "DELETE"}:
            return HTTPStatus.METHOD_NOT_ALLOWED, {
                "ok": False,
                "error_code": "life.source.fallback_read_only",
                "error": {
                    "code": "life.source.fallback_read_only",
                    "message": "active cutover is unavailable; the legacy snapshot is read-only",
                },
            }
        return status, response


@dataclass(frozen=True, slots=True)
class ProductionServerConfig:
    host: str
    port: int
    token_sha256: str
    writer_epoch: int
    production_writer_enabled: bool = True
    writer_lease_acquisition_enabled: bool = True
    scheduler_enabled: bool = False
    side_effects_enabled: bool = False
    legacy_base_read_only: bool = True


@dataclass(frozen=True, slots=True)
class ReadOnlyFallbackConfig:
    host: str
    port: int
    token_sha256: str
    production_writer_enabled: bool = False
    writer_lease_acquisition_enabled: bool = False
    scheduler_enabled: bool = False
    side_effects_enabled: bool = False


class _ProductionHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], api: Any, token: str) -> None:
        self.production_api = api
        self.production_token = token
        super().__init__(address, _ProductionRequestHandler)


class _ProductionRequestHandler(BaseHTTPRequestHandler):
    server: _ProductionHttpServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _write(self, status: int, payload: Mapping[str, Any]) -> None:
        data = _canonical_bytes(dict(payload))
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Tiangong-Token", "")
        try:
            return hmac.compare_digest(
                supplied.encode("utf-8"), self.server.production_token.encode("utf-8")
            )
        except UnicodeError:
            return False

    def _payload(self) -> dict[str, Any]:
        raw = self.headers.get("Content-Length", "0")
        try:
            length = int(raw)
        except ValueError as exc:
            raise ProductionLifeApiError("life.source.content_length_invalid") from exc
        if not 0 <= length <= _MAX_REQUEST_BYTES:
            raise ProductionLifeApiError("life.source.request_too_large", status=413)
        return _strict_payload(self.rfile.read(length)) if length else {}

    def _dispatch(self) -> None:
        if not self._authorized():
            self._write(
                401,
                {
                    "ok": False,
                    "error_code": "life.source.unauthorized",
                    "error": {"code": "life.source.unauthorized", "message": "desktop token is required"},
                },
            )
            return
        try:
            payload = self._payload() if self.command == "POST" else {}
            status, response = self.server.production_api.handle(
                self.command, self.path, payload
            )
        except ProductionLifeApiError as exc:
            status = exc.status
            response = {
                "ok": False,
                "error_code": exc.code,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception:
            status = 500
            response = {
                "ok": False,
                "error_code": "life.source.internal_error",
                "error": {"code": "life.source.internal_error", "message": "source service failed closed"},
            }
        self._write(status, response)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch


def build_production_http_server(
    reader: LegacySnapshotReader,
    overlay_path: Path,
    manifest: LifeCowImportManifest,
    permit: LifeHandoffPermit,
    *,
    trusted_public_key: bytes,
    token: str,
    port: int = PRODUCTION_PORT,
    now_ms: int | None = None,
    allow_ephemeral_test_port: bool = False,
) -> tuple[_ProductionHttpServer, ProductionServerConfig]:
    current_ms = int(time.time() * 1000) if now_ms is None else now_ms
    from .cutover import verify_handoff_permit

    verify_handoff_permit(permit, trusted_public_key, now_ms=current_ms)
    if not isinstance(token, str) or len(token.encode("utf-8")) < 32:
        raise ValueError("production life token must contain at least 32 UTF-8 bytes")
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError("production life port is invalid")
    if port != PRODUCTION_PORT and not (port == 0 and allow_ephemeral_test_port):
        raise ValueError("source-owned production service must bind 7175")
    clock = (lambda: int(time.time() * 1000)) if now_ms is None else (lambda: current_ms)
    api = ProductionLifeApi(
        reader,
        overlay_path,
        manifest,
        permit,
        clock_ms=clock,
    )
    server = _ProductionHttpServer(("127.0.0.1", port), api, token)
    actual_port = int(server.server_address[1])
    return server, ProductionServerConfig(
        host="127.0.0.1",
        port=actual_port,
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        writer_epoch=permit.writer_epoch,
    )


def build_cutover_read_only_fallback_server(
    reader: LegacySnapshotReader,
    *,
    token: str,
    port: int = PRODUCTION_PORT,
    allow_ephemeral_test_port: bool = False,
) -> tuple[_ProductionHttpServer, ReadOnlyFallbackConfig]:
    if not isinstance(token, str) or len(token.encode("utf-8")) < 32:
        raise ValueError("fallback life token must contain at least 32 UTF-8 bytes")
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError("fallback life port is invalid")
    if port != PRODUCTION_PORT and not (port == 0 and allow_ephemeral_test_port):
        raise ValueError("cutover fallback must bind 7175")
    server = _ProductionHttpServer(
        ("127.0.0.1", port), CutoverReadOnlyFallbackApi(reader), token
    )
    return server, ReadOnlyFallbackConfig(
        host="127.0.0.1",
        port=int(server.server_address[1]),
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )


def serve_production_from_environment() -> None:
    """Start only when every explicit P11 authority artifact is present."""

    required = {
        name: os.environ.get(name, "").strip()
        for name in (
            "TIANGONG_LIFE_P11_SNAPSHOT",
            "TIANGONG_LIFE_P11_FINAL_MANIFEST",
            "TIANGONG_LIFE_P11_OVERLAY",
            "TIANGONG_LIFE_P11_HANDOFF",
            "TIANGONG_LIFE_P11_PUBLIC_KEY",
            "TIANGONG_LIFE_P11_TRUSTED_PUBLIC_KEY_SHA256",
            "TIANGONG_DESKTOP_TOKEN",
        )
    }
    if not all(required.values()):
        raise LifeCutoverError("cutover.environment_incomplete")
    public_key_bytes = Path(required["TIANGONG_LIFE_P11_PUBLIC_KEY"]).read_bytes()
    if (
        hashlib.sha256(public_key_bytes).hexdigest()
        != required["TIANGONG_LIFE_P11_TRUSTED_PUBLIC_KEY_SHA256"]
    ):
        raise LifeCutoverError("cutover.root_trust_mismatch")
    now_ms = int(time.time() * 1000)
    manifest = load_cow_manifest(Path(required["TIANGONG_LIFE_P11_FINAL_MANIFEST"]))
    permit = load_and_verify_handoff(
        Path(required["TIANGONG_LIFE_P11_HANDOFF"]),
        Path(required["TIANGONG_LIFE_P11_PUBLIC_KEY"]),
        now_ms=now_ms,
    )
    if permit.owner != "source_life_service":
        serve_cutover_read_only_fallback_from_environment()
        return
    public_key = public_key_bytes
    port = int(os.environ.get("TIANGONG_LIFE_PORT", str(PRODUCTION_PORT)))
    server, config = build_production_http_server(
        LegacySnapshotReader(Path(required["TIANGONG_LIFE_P11_SNAPSHOT"])),
        Path(required["TIANGONG_LIFE_P11_OVERLAY"]),
        manifest,
        permit,
        trusted_public_key=public_key,
        token=required["TIANGONG_DESKTOP_TOKEN"],
        port=port,
        now_ms=now_ms,
    )
    print(
        _canonical_bytes(
            {
                "schema": PRODUCTION_SERVICE_SCHEMA,
                "host": config.host,
                "port": config.port,
                "writer_epoch": config.writer_epoch,
                "production_writer_enabled": True,
                "scheduler_enabled": False,
                "side_effects_enabled": False,
            }
        ).decode("utf-8"),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


def serve_cutover_read_only_fallback_from_environment() -> None:
    snapshot = os.environ.get("TIANGONG_LIFE_P11_SNAPSHOT", "").strip()
    token = os.environ.get("TIANGONG_DESKTOP_TOKEN", "")
    if not snapshot:
        raise LifeCutoverError("cutover.fallback_snapshot_missing")
    port = int(os.environ.get("TIANGONG_LIFE_PORT", str(PRODUCTION_PORT)))
    server, config = build_cutover_read_only_fallback_server(
        LegacySnapshotReader(Path(snapshot)), token=token, port=port
    )
    print(
        _canonical_bytes(
            {
                "schema": PRODUCTION_SERVICE_SCHEMA,
                "host": config.host,
                "port": config.port,
                "source_mode": "cutover_read_only_fallback",
                "production_writer_enabled": False,
                "scheduler_enabled": False,
                "side_effects_enabled": False,
                "cutover_recovery_required": True,
            }
        ).decode("utf-8"),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


__all__ = [
    "PRODUCTION_SERVICE_SCHEMA",
    "PROJECTION_AUTHORITY_SCHEMA",
    "ProductionLifeApi",
    "ProductionLifeApiError",
    "ProductionServerConfig",
    "CutoverReadOnlyFallbackApi",
    "ReadOnlyFallbackConfig",
    "build_cutover_read_only_fallback_server",
    "build_legacy_revision_floor",
    "build_legacy_context_items",
    "build_production_http_server",
    "serve_production_from_environment",
    "serve_cutover_read_only_fallback_from_environment",
]
