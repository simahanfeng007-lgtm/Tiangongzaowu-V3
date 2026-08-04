"""Read-only Life API v2 compatibility surface for P2 shadow comparison."""

from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit

from contracts import canonical_sha256

from .legacy_adapter import (
    LEGACY_API_CONTRACT,
    LegacySnapshotError,
    LegacySnapshotReader,
    compare_projection_anchor,
)


SHADOW_SERVICE_SCHEMA = "tiangong.life.shadow-service.v1"
DEFAULT_SHADOW_PORT = 17175
PRODUCTION_LIFE_PORT = 7175
_MAX_REQUEST_BYTES = 1024 * 1024

_READ_ONLY_GET_ROUTES = frozenset(
    {
        "/health",
        "/api/v1/v3/life/health",
        "/api/v1/v3/life/contract",
        "/api/v1/v3/life/identities",
        "/api/v1/v3/life/identity/active",
        "/api/v1/v3/life/soul",
        "/api/v1/v3/life/journal/verify",
        "/api/v1/v3/life/memory/stats",
        "/api/v1/v3/life/affect",
        "/api/v1/v3/life/affect/expression",
        "/api/v1/v3/life/context/latest",
        "/api/v1/v3/life/proactive-chat/pending",
        "/api/v1/v3/state",
        "/api/v1/v3/life/panel",
        "/api/v1/v3/life/shadow/anchor",
        "/api/v1/v3/life/shadow/compare",
    }
)

_READ_ONLY_POST_ROUTES = frozenset(
    {
        "/api/v1/v3/life/memory/search",
        "/api/v1/v3/life/context/replay",
        "/api/v1/v3/life/context/verify",
        "/api/v1/v3/life/execution/status",
        "/api/v1/v3/life/shadow/compare",
    }
)


class ShadowApiError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _response(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = dict(value or {})
    result.setdefault("ok", True)
    result.setdefault("api_contract", LEGACY_API_CONTRACT)
    return result


def _finite_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ShadowApiError("shadow.invalid_response", "shadow response is not finite JSON", 500) from exc


def _strict_request_json(data: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ShadowApiError(
                    "shadow.request_duplicate_key", f"duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ShadowApiError(
            "shadow.request_non_finite", f"non-finite JSON constant: {value}"
        )

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except ShadowApiError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowApiError("shadow.request_json_invalid", "request body is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ShadowApiError("shadow.request_not_object", "request body must be an object")
    return value


def _context_summary(reader: LegacySnapshotReader, life_id: str, writer_epoch: int) -> dict[str, Any]:
    latest = reader.latest_context(life_id)
    if latest.get("available") is not True:
        return {"available": False, "reason_code": "NO_CONTEXT_COMPILED", "life_id": life_id}
    meta = latest["meta"]
    envelope = latest["envelope"]
    token_budget = max(0, int(meta.get("token_budget") or 0))
    selected_tokens = max(0, int(meta.get("selected_context_tokens", meta.get("estimated_tokens")) or 0))
    current_tokens = max(0, int(meta.get("current_context_tokens", meta.get("estimated_tokens")) or 0))
    raw_utilization = meta.get("context_utilization_milli")
    utilization_milli = (
        max(0, min(1000, int(raw_utilization)))
        if raw_utilization is not None
        else (min(1000, (current_tokens * 1000) // token_budget) if token_budget else 0)
    )
    return {
        "available": True,
        "life_id": life_id,
        "writer_epoch": writer_epoch,
        "current_writer_epoch": writer_epoch,
        "current": True,
        "verified": True,
        "context_hash": meta.get("context_hash"),
        "created_at": meta.get("created_at"),
        "cycle_id": meta.get("cycle_id"),
        "token_budget": token_budget,
        "estimated_tokens": meta.get("estimated_tokens"),
        "selected_context_tokens": selected_tokens,
        "current_context_tokens": current_tokens,
        "context_utilization_milli": utilization_milli,
        "compile_reasons": deepcopy(envelope.get("compile_reasons", [])),
        "omitted_blocks": deepcopy(envelope.get("omitted_blocks", [])),
        "storage": {
            "algorithm": meta.get("algorithm"),
            "cipher_sha256": meta.get("cipher_sha256"),
            "plaintext_bytes": meta.get("plaintext_bytes"),
        },
    }


class ShadowLifeApi:
    """Compatibility router that has no mutation-capable dependency."""

    def __init__(self, reader: LegacySnapshotReader) -> None:
        self.reader = reader

    def _health(self) -> dict[str, Any]:
        life_id = self.reader.active_life_id()
        anchor = self.reader.anchor()
        return _response(
            {
                "schema": "tiangong.life.service.v2",
                "service": "tiangong-life-service-shadow",
                "shadow_schema": SHADOW_SERVICE_SCHEMA,
                "shadow_mode": "read_only_snapshot",
                "life_ready": True,
                "setup_required": False,
                "life_id": life_id,
                "source_sequence": anchor.event_sequence,
                "production_writer_enabled": False,
                "writer_lease_acquisition_enabled": False,
                "scheduler_enabled": False,
                "side_effects_enabled": False,
            }
        )

    def _soul_projection(self, soul: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "available": True,
            "schema": soul.get("schema"),
            "life_id": soul.get("life_id"),
            "revision": soul.get("revision"),
            "revision_id": soul.get("revision_id"),
            "name": soul.get("name") or "起源",
            "prompt": soul.get("prompt"),
            "values": deepcopy(soul.get("values", [])),
            "boundaries": deepcopy(soul.get("boundaries", [])),
            "source": soul.get("source"),
            "life_ip": soul.get("life_ip"),
            "created_at": soul.get("created_at"),
            "updated_at": soul.get("updated_at"),
        }

    def _state(self) -> dict[str, Any]:
        life_id = self.reader.active_life_id()
        binding = self.reader.active_binding()
        soul = self.reader.soul(life_id)
        projection = self.reader.projection(life_id)
        stats = self.reader.memory_stats(life_id)
        head = self.reader.head(life_id)
        writer_epoch = int(head["writer_epoch"])
        context = _context_summary(self.reader, life_id, writer_epoch)
        affect = deepcopy(projection.get("affect", {}))
        expression = deepcopy(
            affect.get("expression", projection.get("affect_expression", {}))
            if isinstance(affect, Mapping)
            else {}
        )
        return {
            "setup_required": False,
            "life_id": life_id,
            "identity": binding,
            "soul": self._soul_projection(soul),
            "life": {
                "ready": True,
                "available": True,
                "phase": "alive",
                "status": projection.get("state", {}).get("status", "ALIVE")
                if isinstance(projection.get("state"), Mapping)
                else "ALIVE",
                "state": deepcopy(projection.get("state", {})),
                "free_will": deepcopy(projection.get("free_will", {})),
                "scheduler": deepcopy(projection.get("scheduler", {})),
                "execution_runs": deepcopy(projection.get("execution_runs", [])),
                "inference_runs": deepcopy(projection.get("inference_runs", [])),
            },
            "ui": {
                "schema": "tiangong.desktop.ui-projection.v1",
                "lifecycle": {
                    "available": True,
                    "phase": "alive",
                    "projection_status": projection.get("projection_status", "ready"),
                    "source_sequence": projection.get("source_sequence", head["last_sequence"]),
                },
                "soul": {
                    "available": True,
                    "revision_id": soul.get("revision_id"),
                    "life_id": life_id,
                },
                "memory": {
                    "available": True,
                    "total": stats["total"],
                    "by_type": deepcopy(stats["by_type"]),
                    "by_status": deepcopy(stats["by_status"]),
                },
                "affect": {"available": True, "state": affect, "expression": expression},
                "affective": deepcopy(affect),
                "relationship": deepcopy(projection.get("relationship", {})),
                "context": context,
                "capabilities": deepcopy(projection.get("capabilities", {})),
                "free_will": deepcopy(projection.get("free_will", {})),
                "execution_bridge": deepcopy(projection.get("execution_bridge", {})),
                "execution_runs": deepcopy(projection.get("execution_runs", [])),
                "inference_runs": deepcopy(projection.get("inference_runs", [])),
            },
        }

    def _projection_authority(self) -> dict[str, Any]:
        """Build a minimal projection authority from the frozen reader snapshot.

        The shadow API has no live writer, so revisions are pinned to the
        last-known snapshot.  The vector_sha256 is a content hash of the
        revision tuple, giving the frontend a stable identity to compare
        across refreshes without trusting raw integer fields.
        """
        life_id = self.reader.active_life_id()
        head = self.reader.head(life_id)
        soul = self.reader.soul(life_id)
        projection = self.reader.projection(life_id)
        binding = self.reader.active_binding()
        writer_epoch = int(head.get("writer_epoch", 1))
        identity_revision = int((binding or {}).get("revision", 1))
        soul_revision = int(soul.get("revision", 1))
        memory_revision = int((projection.get("memory") or {}).get("revision", 0))
        affect_revision = int((projection.get("affect") or {}).get("revision", 0))
        causal_revision = int((projection.get("causal") or {}).get("revision", 0))
        viability_revision = int((projection.get("viability") or {}).get("revision", 0))
        policy_revision = int((projection.get("policy") or {}).get("revision", 0))
        reflection_revision = int((projection.get("reflection") or {}).get("revision", 0))
        capability_revision = int((projection.get("capabilities") or {}).get("revision", 0))
        if isinstance(projection.get("task_capabilities"), Mapping):
            capability_revision = max(
                capability_revision,
                int(projection["task_capabilities"].get("revision", 0)),
            )
        revisions = {
            "writer_epoch": writer_epoch,
            "identity_revision": identity_revision,
            "soul_revision": soul_revision,
            "memory_revision": memory_revision,
            "affect_revision": affect_revision,
            "causal_revision": causal_revision,
            "viability_revision": viability_revision,
            "policy_revision": policy_revision,
            "reflection_revision": reflection_revision,
            "capability_revision": capability_revision,
        }
        vector_sha256 = canonical_sha256(
            {
                "domain": "tiangong.gateway.life-view-authority.v1",
                "revisions": revisions,
            }
        )
        revisions["vector_sha256"] = vector_sha256
        return {
            "schema": "tiangong.gateway.life-view-authority.v1",
            "revisions": revisions,
            "source_refs": {
                "projection_source": "tiangong-life-service-shadow-snapshot",
                "life_id": life_id,
                "projection_sha256": projection.get("source_hash", "")
                or hashlib.sha256(
                    json.dumps(projection, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
            },
            "vector_sha256": vector_sha256,
        }

    def _panel(self) -> dict[str, Any]:
        state = self._state()
        projection = self.reader.projection(state["life_id"])
        authority = self._projection_authority()
        return {
            "generated_at": self.reader.head(state["life_id"]).get("updated_at"),
            "setup_required": False,
            "identity": state["identity"],
            "identities": self.reader.identities(),
            "soul": state["soul"],
            "projection_status": "authoritative",
            "projection_authority": authority,
            "source_sequence": state["ui"]["lifecycle"]["source_sequence"],
            "source_hash": projection.get("source_hash", ""),
            "memory": state["ui"]["memory"],
            "context": state["ui"]["context"],
            "affect": state["ui"]["affect"],
            "relationship": state["ui"]["relationship"],
            "capabilities": state["ui"]["capabilities"],
            "free_will": state["ui"]["free_will"],
            "scheduler": deepcopy(projection.get("scheduler", {})),
            "tasks": deepcopy(projection.get("tasks", [])),
            "settings": deepcopy(projection.get("settings", {})),
            "reflection": deepcopy(projection.get("reflection", {})),
            "iteration": deepcopy(projection.get("iteration", {})),
            "boundaries": deepcopy(projection.get("boundaries", {})),
            "shadow_read_only": True,
        }

    def _contract(self) -> dict[str, Any]:
        routes = [
            {"method": "GET", "path": path, "shadow_access": "read_only"}
            for path in sorted(_READ_ONLY_GET_ROUTES)
        ] + [
            {"method": "POST", "path": path, "shadow_access": "read_only_query"}
            for path in sorted(_READ_ONLY_POST_ROUTES)
        ]
        return _response(
            {
                "schema": LEGACY_API_CONTRACT,
                "shadow_schema": SHADOW_SERVICE_SCHEMA,
                "routes": routes,
                "mutation_policy": "reject_all",
            }
        )

    def _execution_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        projection = self.reader.projection()
        request_id = str(payload.get("request_id") or "")
        cycle_id = str(payload.get("cycle_id") or "")
        if not request_id and not cycle_id:
            raise ShadowApiError(
                "shadow.empty_execution_reference",
                "request_id or cycle_id is required",
            )
        candidates: list[Mapping[str, Any]] = []
        for key in ("execution_runs", "inference_runs"):
            value = projection.get(key, [])
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, Mapping))
            elif isinstance(value, Mapping):
                candidates.extend(item for item in value.values() if isinstance(item, Mapping))
        for item in candidates:
            if (request_id and item.get("request_id") == request_id) or (
                cycle_id and item.get("cycle_id") == cycle_id
            ):
                return _response({"available": True, "execution": deepcopy(dict(item))})
        return _response({"available": False, "reason_code": "EXECUTION_NOT_FOUND"})

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        normalized_method = str(method).upper()
        normalized_path = urlsplit(path).path
        body = dict(payload or {})
        try:
            if normalized_method == "GET" and normalized_path in _READ_ONLY_GET_ROUTES:
                if normalized_path in {"/health", "/api/v1/v3/life/health"}:
                    return 200, self._health()
                if normalized_path == "/api/v1/v3/life/contract":
                    return 200, self._contract()
                if normalized_path == "/api/v1/v3/life/identities":
                    return 200, _response({"identities": self.reader.identities()})
                if normalized_path == "/api/v1/v3/life/identity/active":
                    return 200, _response(
                        {"active": self.reader.active_binding(), "setup_required": False}
                    )
                if normalized_path == "/api/v1/v3/life/soul":
                    soul = self.reader.soul()
                    return 200, _response({"life_id": soul["life_id"], "soul": soul})
                if normalized_path == "/api/v1/v3/life/journal/verify":
                    return 200, _response(self.reader.verify_journal())
                if normalized_path == "/api/v1/v3/life/memory/stats":
                    return 200, _response(self.reader.memory_stats())
                if normalized_path == "/api/v1/v3/life/affect":
                    projection = self.reader.projection()
                    return 200, _response(
                        {"life_id": self.reader.active_life_id(), "state": deepcopy(projection.get("affect", {}))}
                    )
                if normalized_path == "/api/v1/v3/life/affect/expression":
                    projection = self.reader.projection()
                    affect = projection.get("affect", {})
                    expression = (
                        affect.get("expression", projection.get("affect_expression", {}))
                        if isinstance(affect, Mapping)
                        else {}
                    )
                    return 200, _response({"expression": deepcopy(expression)})
                if normalized_path == "/api/v1/v3/life/context/latest":
                    return 200, _response(self.reader.latest_context())
                if normalized_path == "/api/v1/v3/state":
                    return 200, _response(self._state())
                if normalized_path == "/api/v1/v3/life/panel":
                    return 200, _response(self._panel())
                if normalized_path == "/api/v1/v3/life/proactive-chat/pending":
                    projection = self.reader.projection()
                    pending = projection.get("proactive_chats", projection.get("inbox", []))
                    return 200, _response({"messages": deepcopy(pending)})
                if normalized_path == "/api/v1/v3/life/shadow/anchor":
                    anchor = self.reader.anchor()
                    return 200, _response({"anchor": anchor.to_dict(), "anchor_sha256": anchor.sha256})
                if normalized_path == "/api/v1/v3/life/shadow/compare":
                    anchor = self.reader.anchor()
                    return 200, _response({"comparison": compare_projection_anchor(anchor, anchor.to_dict())})

            if normalized_method == "POST" and normalized_path in _READ_ONLY_POST_ROUTES:
                if normalized_path == "/api/v1/v3/life/memory/search":
                    memory_types = body.get("memory_types")
                    if memory_types is not None and not isinstance(memory_types, list):
                        raise ShadowApiError("shadow.invalid_memory_types", "memory_types must be an array")
                    return 200, _response(
                        self.reader.search_memory(
                            str(body.get("query") or ""),
                            limit=body.get("limit", 20),
                            memory_types=memory_types,
                            include_content=body.get("include_content") is True,
                        )
                    )
                if normalized_path == "/api/v1/v3/life/context/replay":
                    return 200, _response(
                        self.reader.context(str(body.get("context_hash") or ""))
                    )
                if normalized_path == "/api/v1/v3/life/context/verify":
                    envelope = body.get("envelope")
                    if not isinstance(envelope, Mapping):
                        raise ShadowApiError("shadow.invalid_context_envelope", "envelope must be an object")
                    context_hash = str(envelope.get("context_hash") or "")
                    stored = self.reader.context(context_hash)["envelope"]
                    compatible = _finite_json_bytes(dict(envelope)) == _finite_json_bytes(stored)
                    return 200, _response(
                        {
                            "verified": compatible,
                            "context_hash": context_hash,
                            "reason_code": None if compatible else "CONTEXT_MISMATCH",
                        }
                    )
                if normalized_path == "/api/v1/v3/life/execution/status":
                    return 200, self._execution_status(body)
                if normalized_path == "/api/v1/v3/life/shadow/compare":
                    candidate = body.get("candidate_anchor")
                    if not isinstance(candidate, Mapping):
                        raise ShadowApiError(
                            "shadow.invalid_candidate_anchor",
                            "candidate_anchor must be an object",
                        )
                    return 200, _response(
                        {
                            "comparison": compare_projection_anchor(
                                self.reader.anchor(), dict(candidate)
                            )
                        }
                    )

            if normalized_method in {"POST", "PUT", "PATCH", "DELETE"}:
                raise ShadowApiError(
                    "shadow.mutation_forbidden",
                    "P2 shadow service rejects every mutation and side effect",
                    HTTPStatus.METHOD_NOT_ALLOWED,
                )
            raise ShadowApiError("shadow.route_not_found", "shadow route was not found", HTTPStatus.NOT_FOUND)
        except LegacySnapshotError as exc:
            return 409, {
                "ok": False,
                "api_contract": LEGACY_API_CONTRACT,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except ShadowApiError as exc:
            return int(exc.status), {
                "ok": False,
                "api_contract": LEGACY_API_CONTRACT,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception:
            return 500, {
                "ok": False,
                "api_contract": LEGACY_API_CONTRACT,
                "error": {
                    "code": "shadow.internal_error",
                    "message": "shadow request failed closed",
                },
            }


@dataclass(frozen=True, slots=True)
class ShadowServerConfig:
    host: str
    port: int
    token_sha256: str
    production_writer_enabled: bool = False
    writer_lease_acquisition_enabled: bool = False
    scheduler_enabled: bool = False
    side_effects_enabled: bool = False


class _ShadowHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], api: ShadowLifeApi, token: str) -> None:
        self.shadow_api = api
        self.shadow_token = token
        super().__init__(address, _ShadowRequestHandler)


class _ShadowRequestHandler(BaseHTTPRequestHandler):
    server: _ShadowHttpServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.shadow_token
        try:
            return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        except UnicodeError:
            return False

    def _write(self, status: int, payload: Mapping[str, Any]) -> None:
        data = _finite_json_bytes(dict(payload))
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _payload(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ShadowApiError("shadow.content_length_invalid", "Content-Length is invalid") from exc
        if not 0 <= length <= _MAX_REQUEST_BYTES:
            raise ShadowApiError("shadow.request_too_large", "request body exceeds the shadow limit", 413)
        if not length:
            return {}
        return _strict_request_json(self.rfile.read(length))

    def _dispatch(self) -> None:
        if not self._authorized():
            self._write(
                401,
                {
                    "ok": False,
                    "api_contract": LEGACY_API_CONTRACT,
                    "error": {"code": "shadow.unauthorized", "message": "shadow token is required"},
                },
            )
            return
        try:
            payload = self._payload() if self.command == "POST" else {}
            status, response = self.server.shadow_api.handle(self.command, self.path, payload)
        except ShadowApiError as exc:
            status = exc.status
            response = {
                "ok": False,
                "api_contract": LEGACY_API_CONTRACT,
                "error": {"code": exc.code, "message": str(exc)},
            }
        self._write(status, response)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch


def build_shadow_http_server(
    reader: LegacySnapshotReader,
    *,
    token: str,
    port: int = DEFAULT_SHADOW_PORT,
) -> tuple[_ShadowHttpServer, ShadowServerConfig]:
    if not isinstance(token, str) or len(token.encode("utf-8")) < 32:
        raise ValueError("shadow token must contain at least 32 UTF-8 bytes")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("shadow port is invalid")
    if port == PRODUCTION_LIFE_PORT:
        raise ValueError("shadow service can never bind the production 7175 port")
    server = _ShadowHttpServer(("127.0.0.1", port), ShadowLifeApi(reader), token)
    actual_port = int(server.server_address[1])
    return server, ShadowServerConfig(
        host="127.0.0.1",
        port=actual_port,
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "DEFAULT_SHADOW_PORT",
    "PRODUCTION_LIFE_PORT",
    "SHADOW_SERVICE_SCHEMA",
    "ShadowApiError",
    "ShadowLifeApi",
    "ShadowServerConfig",
    "build_shadow_http_server",
]
