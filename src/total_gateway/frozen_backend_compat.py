"""Ticket-gated compatibility transport for the frozen 7174 execution kernel.

The frozen backend does not yet expose ``execute-ticket``.  This adapter is
therefore deliberately used *behind* :class:`BackendClient`: the gateway has
already verified and consumed the ExecutionTicket before this module can call
the historical inbound route.  No renderer or communication process can reach
this compatibility surface.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from contracts import (
    CausalContextPack,
    ExecutionResult,
    ExecutionTicket,
    LifeContextAuthorization,
    LifeSnapshot,
    canonical_json_bytes,
    canonical_sha256,
)
from contracts.models import validate_safe_filename

from .backend_client import BACKEND_API_CONTRACT, BackendClientError, BackendExecutionTransport
from .gateway_url import DEFAULT_GATEWAY_URL, normalize_gateway_url
from .context_projection import estimate_projected_context_tokens
from .object_store import ContentAddressedObjectStore


_BACKEND_PATH = "/api/v1/gateway/internal/inbound"
_LIFE_ATOMIC_PATH = "/api/v1/v3/life/context/compile-and-authorize"
_LIFE_RECOVER_PATH = "/api/v1/v3/life/execution/recover"
_SAFE_RUN_STATUS_PATH = re.compile(
    r"^/api/v1/run/status\?request_id=(req_[0-9a-f]{64})&after_seq=0$"
)
_INITIAL_BACKEND_RESPONSE_TIMEOUT_SECONDS = 60.0
_BACKEND_STATUS_POLL_SECONDS = 1.0


class FrozenBackendCompatibilityError(RuntimeError):
    def __init__(self, code: str, *, backend_started: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.backend_started = backend_started


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenBackendCompatibilityError("compat.http.duplicate_json_key")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except FrozenBackendCompatibilityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise FrozenBackendCompatibilityError(code) from exc
    if not isinstance(value, dict):
        raise FrozenBackendCompatibilityError(code)
    return value


def _legacy_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Encode a frozen-service payload without treating it as a signed contract."""

    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FrozenBackendCompatibilityError("compat.http.request_json_invalid") from exc




class _JsonRequestClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        timeout_seconds: float,
        backend_started: bool = False,
        before_request: Callable[[int], None] | None = None,
    ) -> tuple[int, dict[str, Any], str]: ...


class _LoopbackJsonClient:
    def __init__(self, port: int, token: str, *, max_response_bytes: int) -> None:
        if not 1 <= port <= 65_535 or not 32 <= len(token) <= 512:
            raise ValueError("compatibility upstream configuration is invalid")
        self._port = port
        self._token = token
        self._max_response_bytes = max_response_bytes

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        timeout_seconds: float,
        backend_started: bool = False,
        before_request: Callable[[int], None] | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        query_path_allowed = method == "GET" and _SAFE_RUN_STATUS_PATH.fullmatch(path) is not None
        if (
            method not in {"GET", "POST"}
            or not path.startswith("/api/")
            or ("?" in path and not query_path_allowed)
        ):
            raise FrozenBackendCompatibilityError("compat.http.route_forbidden")
        body = b"" if payload is None else _legacy_json_bytes(payload)
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=timeout_seconds)
        response = None
        try:
            headers = {
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "X-Tiangong-Token": self._token,
            }
            if body:
                headers.update(
                    {
                        "Content-Type": "application/json; charset=utf-8",
                        "Content-Length": str(len(body)),
                    }
                )
            if before_request is not None:
                before_request(time.time_ns() // 1_000_000)
            connection.request(method, path, body=body or None, headers=headers)
            response = connection.getresponse()
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise FrozenBackendCompatibilityError(
                    "compat.http.response_too_large", backend_started=backend_started
                )
            if content_type not in {"application/json", "application/problem+json"}:
                raise FrozenBackendCompatibilityError(
                    "compat.http.content_type_invalid", backend_started=backend_started
                )
            value = _decode_json(raw, code="compat.http.response_json_invalid")
            return int(response.status), value, hashlib.sha256(raw).hexdigest()
        except FrozenBackendCompatibilityError:
            raise
        except Exception as exc:
            raise FrozenBackendCompatibilityError(
                "compat.http.outcome_unknown", backend_started=backend_started
            ) from exc
        finally:
            if response is not None:
                response.close()
            connection.close()


def _first_text(data: Mapping[str, Any]) -> str:
    nested = data.get("data") if isinstance(data.get("data"), Mapping) else {}
    run = data.get("run") if isinstance(data.get("run"), Mapping) else {}
    for value in (
        run.get("final_response"),
        run.get("reply_text"),
        run.get("reply"),
        data.get("huifu"),
        nested.get("huifu"),
        nested.get("text"),
        nested.get("message"),
        data.get("text"),
        data.get("message"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    outbound = data.get("outbound") if isinstance(data.get("outbound"), Mapping) else {}
    parts = outbound.get("parts") if isinstance(outbound.get("parts"), list) else []
    for item in parts:
        if isinstance(item, Mapping) and item.get("type") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                return text
    return ""


def _backend_terminal_projection(payload: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    """Project frozen run state into one fail-closed gateway terminal fact."""
    run = payload.get("run") if isinstance(payload.get("run"), Mapping) else {}
    actual_request_id = str(
        run.get("gateway_request_id")
        or run.get("request_id")
        or payload.get("gateway_request_id")
        or payload.get("request_id")
        or ""
    )
    status = str(run.get("status") or run.get("phase") or "").strip().upper()
    stage = str(run.get("stage") or "").strip().upper()
    backend_ok = run.get("ok")
    if actual_request_id != request_id or not status:
        classification = "AMBIGUOUS"
        reason_code = "compat.backend.terminal_unverified"
    elif stage in {"EFFECT_UNKNOWN", "UNKNOWN_EFFECT", "RECONCILE_REQUIRED"} or status in {
        "BLOCKED",
        "EFFECT_UNKNOWN",
        "UNKNOWN_EFFECT",
        "RECONCILE_REQUIRED",
    }:
        classification = "AMBIGUOUS"
        reason_code = "compat.backend.effect_unknown"
    elif status in {"COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE", "WANCHENG"} and backend_ok is False:
        # The frozen backend uses ``phase=finished`` for both successful and
        # unsuccessful terminal runs.  ``finished`` is a lifecycle fact; the
        # explicit boolean is the business outcome.  Ignoring it lets an
        # incomplete tool chain become a forged gateway SUCCEEDED fact.
        classification = "FAILED_FINAL"
        reason_code = "compat.backend.reported_failure"
    elif status in {"COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE", "WANCHENG"}:
        classification = "SUCCEEDED"
        reason_code = "compat.backend.completed"
    elif status == "WAITING_FOR_USER":
        # This is a durable authorization checkpoint, not a backend crash.
        # The execution contract is terminal per request, so it is projected
        # as a dedicated classification and later carried as a bounded
        # confirmation checkpoint into the next request.
        classification = "WAITING_FOR_USER"
        reason_code = "compat.backend.waiting_for_user"
    elif status in {
        "FAILED_SAFE",
        "FAILED",
        "FAILURE",
        "ERROR",
        "CANCELLED",
        "CANCELED",
        "ABORTED",
        "INTERRUPTED",
    }:
        classification = "FAILED_FINAL"
        reason_code = "compat.backend.terminal_failure"
    else:
        # A synchronous inbound request returning while the backend still says
        # RUNNING is an outcome-unknown boundary, never a successful reply.
        classification = "AMBIGUOUS"
        reason_code = "compat.backend.nonterminal_return"
    return {
        "request_id": actual_request_id,
        "status": status,
        "stage": stage,
        "backend_ok": backend_ok if isinstance(backend_ok, bool) else None,
        "classification": classification,
        "reason_code": reason_code,
        "event_seq": int(run.get("event_seq") or 0),
    }


def _mime_and_format(path: Path) -> tuple[str, str]:
    suffix = path.suffix.casefold()
    explicit = {
        ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
        ".pdf": ("application/pdf", "pdf"),
        ".zip": ("application/zip", "zip"),
        ".png": ("image/png", "png"),
        ".jpg": ("image/jpeg", "jpeg"),
        ".jpeg": ("image/jpeg", "jpeg"),
        ".gif": ("image/gif", "gif"),
        ".webp": ("image/webp", "webp"),
        ".mp4": ("video/mp4", "mp4"),
        ".mp3": ("audio/mpeg", "mp3"),
        ".wav": ("audio/wav", "wav"),
        ".json": ("application/json", "json"),
        ".csv": ("text/csv", "csv"),
        ".bin": ("application/octet-stream", "binary"),
        ".dat": ("application/octet-stream", "binary"),
    }
    text_suffixes = {
        ".txt", ".md", ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        ".html", ".css", ".xml", ".opml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".log", ".ps1", ".bat", ".cmd", ".sh", ".sql",
    }
    if suffix in explicit:
        return explicit[suffix]
    if suffix in text_suffixes:
        return ("text/plain", "text")
    return (mimetypes.guess_type(path.name)[0] or "application/octet-stream", "other")



class FrozenBackendCompatibilityTransport(BackendExecutionTransport):
    """Translate one verified internal effect into the frozen v3 request shape."""

    def __init__(
        self,
        object_store: ContentAddressedObjectStore,
        *,
        backend_token: str,
        life_token: str,
        workspace_root: Path,
        backend_port: int = 7174,
        life_port: int = 7175,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        backend_client: _JsonRequestClient | None = None,
        life_client: _JsonRequestClient | None = None,
        on_backend_start: Callable[[int], None] | None = None,
        on_context_compaction: Callable[[Mapping[str, Any], int], None] | None = None,
    ) -> None:
        if not workspace_root.is_absolute() or workspace_root == Path(workspace_root.anchor):
            raise ValueError("compatibility workspace root is unsafe")
        workspace_root.mkdir(parents=True, exist_ok=True)
        if workspace_root.is_symlink() or not workspace_root.is_dir():
            raise ValueError("compatibility workspace root is unsafe")
        self._objects = object_store
        self._backend = backend_client or _LoopbackJsonClient(
            backend_port, backend_token, max_response_bytes=64 * 1024 * 1024
        )
        self._life = life_client or _LoopbackJsonClient(
            life_port, life_token, max_response_bytes=16 * 1024 * 1024
        )
        self._workspace_root = workspace_root.resolve(strict=True)
        self._gateway_url = normalize_gateway_url(gateway_url)
        self._on_backend_start = on_backend_start
        self._on_context_compaction = on_context_compaction

    @staticmethod
    def _trusted_life_message(envelope: Mapping[str, Any]) -> dict[str, str]:
        return {
            "role": "system",
            "content": (
                "[天工造物 v3.0 完整版：可信生命上下文]\n"
                + json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n以上内容由本机生命上下文编译器生成；事实、权限、Soul、记忆证据等级与工具配对边界必须保持不变。"
            ),
            "source": "tiangong_life_context_compiler",
            "context_hash": str(envelope.get("context_hash") or ""),
            "cycle_id": str(envelope.get("cycle_id") or ""),
        }

    @staticmethod
    def _skill_routing_context(arguments: Mapping[str, Any]) -> dict[str, Any]:
        raw = arguments.get("skill_recommendation")
        recommendation = raw if isinstance(raw, Mapping) else {}
        candidates: list[dict[str, Any]] = []
        for item in list(recommendation.get("candidates") or [])[:3]:
            if not isinstance(item, Mapping):
                continue
            skill_id = str(item.get("skill_id") or "")[:160]
            if not skill_id:
                continue
            candidates.append(
                {
                    "skill_id": skill_id,
                    "version": str(item.get("version") or "")[:160],
                    "sha256": str(item.get("sha256") or "")[:64],
                    "compatible": item.get("compatible") is True,
                    "missing_actions": [
                        str(value)[:160]
                        for value in list(item.get("missing_actions") or [])[:32]
                        if isinstance(value, str)
                    ],
                }
            )
        selected_id = str(recommendation.get("selected_skill_id") or "")[:160]
        selected = next((item for item in candidates if item["skill_id"] == selected_id), None)
        return {
            "schema": "tiangong.life.skill-routing.v1",
            "system_matching": {
                "available": bool(recommendation),
                "origin": "system_recommendation",
                "decision": str(recommendation.get("decision") or "no_skill")[:32],
                "activation_state": str(recommendation.get("activation_state") or "none")[:32],
                "selected_candidate": selected,
                "candidates": candidates,
                "candidate_is_not_activated": True,
            },
            "model_request": {
                "available": True,
                "tool": "omni_body",
                "operations": ["skill.route", "skill.list", "skill.get", "skill.read"],
                "activation_operations": ["skill.get", "skill.read"],
                "procedure_loaded": False,
                "instruction": (
                    "系统匹配只提供候选。模型可接受候选、重新 route/list 或不使用 Skill；"
                    "只有显式 skill.get/skill.read 返回完整过程后才视为激活。"
                ),
            },
        }

    def _prepare_life(
        self,
        ticket: ExecutionTicket,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        snapshot = LifeSnapshot.model_validate(arguments.get("life_snapshot"), strict=True)
        if snapshot.sha256 != ticket.payload.life_snapshot_hash or snapshot.revision != ticket.payload.life_snapshot_revision:
            raise FrozenBackendCompatibilityError("compat.life_snapshot.binding_mismatch")
        messages = tuple(
            dict(item)
            for item in list(arguments.get("recent_messages") or [])[-80:]
            if isinstance(item, Mapping) and str(item.get("role") or "") in {"user", "assistant"}
        )
        skill_routing = self._skill_routing_context(arguments)
        observed_at_ms = time.time_ns() // 1_000_000
        expected_principal_scope_hash: str | None = None
        if snapshot.context_authorization_id is not None:
            try:
                raw_projection = self._objects.read_bytes(
                    snapshot.compiled_context_object_id
                )
            except Exception as exc:
                raise FrozenBackendCompatibilityError(
                    "compat.life.context_object_unavailable"
                ) from exc
            if hashlib.sha256(raw_projection).hexdigest() != snapshot.compiled_context_sha256:
                raise FrozenBackendCompatibilityError(
                    "compat.life.context_object_digest_mismatch"
                )
            projection = _decode_json(
                raw_projection, code="compat.life.context_object_invalid"
            )
        else:
            expected_principal_scope_hash = canonical_sha256(
                {
                    "channel": ticket.payload.channel,
                    "life_snapshot_hash": snapshot.sha256,
                    "request_id": ticket.payload.request_id,
                }
            )
            status, compiled, _ = self._life.request(
                "POST",
                _LIFE_ATOMIC_PATH,
                {
                    "request_id": ticket.payload.request_id,
                    "run_id": ticket.payload.run_id,
                    "generation": ticket.payload.generation,
                    "current_request": str(arguments.get("text") or ""),
                    "current_context_tokens": estimate_projected_context_tokens(
                        list(messages),
                        str(arguments.get("text") or ""),
                    ),
                    "principal_scope_hash": expected_principal_scope_hash,
                    "issued_at_ms": observed_at_ms,
                },
                timeout_seconds=30.0,
            )
            if status >= 400:
                raise FrozenBackendCompatibilityError("compat.life.context_authorization_failed")
            projection = compiled.get("projection") if isinstance(compiled.get("projection"), Mapping) else {}
        try:
            context_pack = CausalContextPack.model_validate_json(
                canonical_json_bytes(projection.get("context_pack")), strict=True
            )
            authorization = LifeContextAuthorization.model_validate_json(
                canonical_json_bytes(projection.get("authorization")), strict=True
            )
            soul = dict(projection.get("soul"))
        except Exception as exc:
            raise FrozenBackendCompatibilityError("compat.life.context_authorization_invalid") from exc
        revisions = authorization.revisions
        soul_sha256 = hashlib.sha256(canonical_json_bytes(soul)).hexdigest()
        if (
            not context_pack.has_valid_pack_sha256()
            or not authorization.has_valid_authorization_sha256()
            or authorization.life_id != snapshot.identity_ref
            or authorization.request_id != ticket.payload.request_id
            or authorization.run_id != ticket.payload.run_id
            or authorization.generation != ticket.payload.generation
            or (
                expected_principal_scope_hash is not None
                and authorization.principal_scope_hash
                != expected_principal_scope_hash
            )
            or hashlib.sha256(str(arguments.get("text") or "").encode("utf-8")).hexdigest()
            != authorization.current_request_sha256
            or authorization.context_pack_id != context_pack.pack_id
            or authorization.context_pack_sha256 != context_pack.pack_sha256
            or revisions.identity_revision != snapshot.identity_revision
            or revisions.memory_revision != snapshot.memory_revision
            or revisions.affect_revision != snapshot.affect_revision
            or revisions.causal_revision != snapshot.causal_revision
            or revisions.viability_revision != snapshot.viability_revision
            or revisions.policy_revision != snapshot.policy_revision
            or revisions.reflection_revision != snapshot.reflection_revision
            or revisions.capability_revision != snapshot.capability_revision
            or soul.get("life_id") != snapshot.identity_ref
            or soul.get("revision") != revisions.soul_revision
            or not isinstance(soul.get("name"), str)
            or not isinstance(soul.get("prompt"), str)
            or soul_sha256 != snapshot.soul_sha256
            or (
                snapshot.context_authorization_id is not None
                and (
                    snapshot.context_authorization_id
                    != authorization.authorization_id
                    or snapshot.context_authorization_sha256
                    != authorization.authorization_sha256
                    or snapshot.revision_vector_sha256
                    != revisions.vector_sha256
                )
            )
            or authorization.expires_at_ms <= observed_at_ms
        ):
            raise FrozenBackendCompatibilityError("compat.life.context_authorization_binding_mismatch")
        envelope = context_pack.model_dump(mode="json")
        # The CausalContextPack binds Soul's revision, while the immutable
        # atomic projection carries its actual text.  Keep both together and
        # verify the text against the LifeSnapshot hash before it reaches the
        # legacy model adapter.
        envelope["soul"] = soul
        envelope["skill_routing"] = skill_routing
        envelope["recent_messages"] = list(messages)
        omitted = context_pack.omitted_item_count
        if (
            self._on_context_compaction is not None
            and omitted > 0
        ):
            self._on_context_compaction(dict(envelope), time.time_ns() // 1_000_000)
        return {
            "life_id": snapshot.identity_ref,
            "writer_epoch": snapshot.identity_revision,
            "binding_status": "authorized",
            "cycle_id": authorization.authorization_id,
            "context_hash": context_pack.pack_sha256,
            "context_envelope": dict(envelope),
            "lifecycle_state": "authorized",
        }

    def _recover(self, request_id: str, cycle_id: str) -> dict[str, Any]:
        last_error = ""
        for _ in range(3):
            try:
                status, payload, response_sha256 = self._life.request(
                    "POST",
                    _LIFE_RECOVER_PATH,
                    {"request_id": request_id, "cycle_id": cycle_id},
                    timeout_seconds=10.0,
                )
                if status < 400:
                    # Frozen life payloads may contain non-contract floats and
                    # other presentation fields.  Preserve the raw response as
                    # a digest and project only typed terminal machine facts.
                    return {
                        "http_status": status,
                        "ok": payload.get("ok") is True,
                        "recovered": payload.get("recovered") is True,
                        "state": str(
                            payload.get("state")
                            or payload.get("status")
                            or payload.get("lifecycle_state")
                            or ""
                        )[:160],
                        "response_sha256": response_sha256,
                    }
                last_error = "compat.life.recover_rejected"
            except FrozenBackendCompatibilityError as exc:
                last_error = exc.code
            time.sleep(0.25)
        return {
            "ok": False,
            "recovered": False,
            "error": last_error or "execution_terminal_not_verified",
        }

    def _backend_status(
        self,
        request_id: str,
    ) -> tuple[int, dict[str, Any], str, dict[str, Any]]:
        status, payload, response_sha256 = self._backend.request(
            "GET",
            f"/api/v1/run/status?request_id={request_id}&after_seq=0",
            None,
            timeout_seconds=10.0,
            backend_started=True,
        )
        if status >= 400:
            projected = {
                "request_id": request_id,
                "status": "",
                "stage": "",
                "classification": "AMBIGUOUS",
                "reason_code": "compat.backend.terminal_http_rejected",
                "event_seq": 0,
                "http_status": status,
                "response_sha256": response_sha256,
            }
        else:
            projected = _backend_terminal_projection(payload, request_id)
            projected.update({"http_status": status, "response_sha256": response_sha256})
        return status, payload, response_sha256, projected

    def _backend_terminal(self, request_id: str) -> dict[str, Any]:
        try:
            return self._backend_status(request_id)[3]
        except FrozenBackendCompatibilityError as exc:
            return {
                "request_id": request_id,
                "status": "",
                "stage": "",
                "classification": "AMBIGUOUS",
                "reason_code": exc.code,
                "event_seq": 0,
            }

    def _wait_backend_terminal(
        self,
        request_id: str,
        *,
        deadline_monotonic: float,
    ) -> tuple[int, dict[str, Any], str, dict[str, Any]]:
        """Reconcile a timed-out synchronous call with the durable run ledger.

        Closing the historical inbound HTTP connection does not cancel its run.
        A transport timeout is therefore not a terminal fact.  Poll the
        authoritative run record until it reaches a verified terminal state or
        the signed execution lease expires.
        """

        while True:
            try:
                status, payload, response_sha256, projected = self._backend_status(request_id)
                classification = str(projected.get("classification") or "")
                reason_code = str(projected.get("reason_code") or "")
                if classification in {"SUCCEEDED", "FAILED_FINAL", "WAITING_FOR_USER"} or reason_code == "compat.backend.effect_unknown":
                    return status, payload, response_sha256, projected
            except FrozenBackendCompatibilityError:
                # The status route can be momentarily unavailable while the
                # old handler is registering its run.  The lease remains the
                # sole deadline; a transient read must not create a false fact.
                pass
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise FrozenBackendCompatibilityError(
                    "compat.backend.execution_lease_expired",
                    backend_started=True,
                )
            time.sleep(min(_BACKEND_STATUS_POLL_SECONDS, remaining))

    def _materialize_inputs(
        self,
        ticket: ExecutionTicket,
        arguments: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        declared = arguments.get("attachments")
        items = declared if isinstance(declared, list) else []
        by_identity = {
            (str(item.get("object_id") or ""), int(item.get("revision") or 0)): item
            for item in items
            if isinstance(item, Mapping)
        }
        # Keep the physical path compact.  The former layout embedded both the
        # 68-character request id and the 69-character object id before the
        # original filename.  A normal 32-character hash-named upload therefore
        # crossed the legacy Win32 MAX_PATH boundary and failed before backend
        # execution.  The authoritative ids remain in the ticket/object store;
        # the workspace copy only needs a collision-free content name.
        request_token = str(ticket.payload.request_id).removeprefix("req_")
        if re.fullmatch(r"[0-9a-f]{64}", request_token) is None:
            raise FrozenBackendCompatibilityError("compat.attachment.request_id_invalid")
        root: Path | None = None
        for token_width in (16, 24, 32, 64):
            candidate_root = self._workspace_root / ".in" / request_token[:token_width]
            candidate_root.mkdir(parents=True, exist_ok=True)
            if (
                candidate_root.is_symlink()
                or self._workspace_root not in candidate_root.resolve(strict=True).parents
            ):
                raise FrozenBackendCompatibilityError(
                    "compat.attachment.materialization_root_unsafe"
                )
            marker = candidate_root / ".request-id"
            try:
                with marker.open("x", encoding="ascii", newline="\n") as stream:
                    stream.write(str(ticket.payload.request_id))
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
            try:
                marker_request_id = marker.read_text(encoding="ascii")
            except (OSError, UnicodeError):
                marker_request_id = ""
            if marker_request_id == str(ticket.payload.request_id):
                root = candidate_root
                break
        if root is None:
            raise FrozenBackendCompatibilityError(
                "compat.attachment.materialization_request_collision"
            )
        result: list[dict[str, Any]] = []
        for grant in ticket.payload.input_objects:
            item = by_identity.get((grant.object_id, grant.revision))
            if item is None:
                raise FrozenBackendCompatibilityError("compat.attachment.grant_missing")
            try:
                filename = validate_safe_filename(str(item.get("filename") or ""))
            except ValueError as exc:
                raise FrozenBackendCompatibilityError(
                    "compat.attachment.filename_unsafe"
                ) from exc
            reference = self._objects.get_reference(grant.object_id)
            if (
                reference is None
                or reference.sha256 != grant.sha256
                or reference.size_bytes != grant.size_bytes
                or reference.tenant_id != grant.tenant_id
                or reference.link_account_id != grant.link_account_id
                or reference.conversation_scope_hash != grant.conversation_scope_hash
            ):
                raise FrozenBackendCompatibilityError("compat.attachment.object_binding_invalid")
            data = self._objects.read_bytes(grant.object_id)
            suffix = Path(filename).suffix
            physical_name = f"{grant.sha256}{suffix}"
            target = root / physical_name
            # Leave headroom for the atomic temporary suffix and for legacy
            # Windows APIs used by the frozen backend/tool adapters.  If an
            # unusually long custom extension consumes the budget, the content
            # hash alone remains exact; the original filename is still exposed
            # separately in the attachment metadata below.
            if len(str(target)) + len(".tmp") > 240:
                target = root / grant.sha256
            if len(str(target)) + len(".tmp") > 240:
                raise FrozenBackendCompatibilityError(
                    "compat.attachment.materialization_path_too_long"
                )
            temporary = target.with_suffix(target.suffix + ".tmp")
            if not target.exists():
                try:
                    with temporary.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            elif target.read_bytes() != data:
                raise FrozenBackendCompatibilityError("compat.attachment.materialized_content_changed")
            result.append(
                {
                    "name": filename,
                    "filename": filename,
                    "path": str(target),
                    "mime": grant.mime,
                    "size": grant.size_bytes,
                    "sha256": grant.sha256,
                }
            )
        return result

    def _capture_outputs(
        self,
        ticket: ExecutionTicket,
        backend_payload: Mapping[str, Any],
        *,
        created_at_ms: int,
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        nested = backend_payload.get("data") if isinstance(backend_payload.get("data"), Mapping) else {}
        run = backend_payload.get("run") if isinstance(backend_payload.get("run"), Mapping) else {}
        raw_items = backend_payload.get("attachments")
        if not isinstance(raw_items, list):
            raw_items = nested.get("attachments") if isinstance(nested.get("attachments"), list) else []
        if not raw_items:
            raw_items = run.get("attachments") if isinstance(run.get("attachments"), list) else []
        if not raw_items:
            raw_items = (
                run.get("generated_attachments")
                if isinstance(run.get("generated_attachments"), list)
                else []
            )
        sanitized: list[dict[str, Any]] = []
        object_ids: list[str] = []
        for index, item in enumerate(raw_items[:256]):
            if not isinstance(item, Mapping):
                continue
            path_text = str(item.get("path") or item.get("source_path") or "").strip()
            if not path_text:
                continue
            source = Path(path_text).expanduser()
            try:
                resolved = source.resolve(strict=True)
                resolved.relative_to(self._workspace_root)
            except (OSError, ValueError):
                continue
            if resolved.is_symlink() or not resolved.is_file():
                continue
            data = resolved.read_bytes()
            if not data or len(data) > ticket.payload.max_output_bytes:
                continue
            reference = self._objects.put_bytes(
                data,
                kind="artifact",
                tenant_id=ticket.payload.tenant_id,
                link_account_id=ticket.payload.link_account_id,
                conversation_scope_hash=ticket.payload.conversation_scope_hash,
                created_at_ms=created_at_ms,
            ).reference
            mime, format_id = _mime_and_format(resolved)
            # D-22：文件名验收与 artifact gate 同一标准（validate_safe_filename，
            # CJK/Unicode 安全名通过）；不通过时回退名保留真实扩展名，
            # 保证 docx/xlsx/pptx/pdf/zip/png/jpeg/json/csv/text 等所有格式
            # 仍满足门的扩展策略，不因非 ASCII 名被误拒。
            try:
                filename = validate_safe_filename(resolved.name)
            except ValueError:
                suffix = resolved.suffix.casefold()
                safe_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix) else ".bin"
                filename = f"artifact-{index + 1}{safe_suffix}"
            sanitized.append(
                {
                    "object_id": reference.object_id,
                    "revision": 1,
                    "sha256": reference.sha256,
                    "size_bytes": reference.size_bytes,
                    "mime": mime,
                    "format_id": format_id,
                    "filename": filename,
                }
            )
            object_ids.append(reference.object_id)
        return sanitized, tuple(sorted(set(object_ids)))

    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, Any]:
        started_at_ms = time.time_ns() // 1_000_000
        execution_deadline = time.monotonic() + timeout_seconds
        backend_started = False
        ticket: ExecutionTicket | None = None
        try:
            wire = _decode_json(body, code="compat.execute_ticket.invalid_json")
            if set(wire) != {"schema", "ticket", "arguments"} or wire.get("schema") != "tiangong.backend.execute-ticket.v1":
                raise FrozenBackendCompatibilityError("compat.execute_ticket.envelope_invalid")
            # The ticket crossed a canonical JSON boundary, so tuple fields
            # are represented as JSON arrays.  Strict Python-mode validation
            # rejects those arrays even though strict JSON-mode validation is
            # the contractually correct decoder.
            ticket = ExecutionTicket.model_validate_json(
                canonical_json_bytes(wire["ticket"]),
                strict=True,
            )
            arguments = wire["arguments"]
            if not isinstance(arguments, dict) or canonical_sha256(arguments) != ticket.payload.arguments_hash:
                raise FrozenBackendCompatibilityError("compat.execute_ticket.arguments_invalid")
            life_context = self._prepare_life(ticket, arguments)
            skill_routing = self._skill_routing_context(arguments)
            attachments = self._materialize_inputs(ticket, arguments)
            recent = [
                dict(item)
                for item in list(arguments.get("recent_messages") or [])[-80:]
                if isinstance(item, Mapping) and str(item.get("role") or "") in {"user", "assistant"}
            ]
            text = str(arguments.get("text") or "")
            user_callsign = str(arguments.get("user_callsign") or ticket.payload.channel)
            session_id = str(arguments.get("conversation_ref") or ticket.payload.conversation_scope_hash)
            message_id = str(arguments.get("channel_message_ref") or ticket.payload.request_id)
            trusted_message = self._trusted_life_message(life_context["context_envelope"])
            backend_request = {
                "tenant_id": "desktop",
                "channel": ticket.payload.channel,
                "user_name": user_callsign,
                "display_name": user_callsign,
                "character_id": "qiyuan",
                "conversation_id": session_id,
                "session_id": session_id,
                "request_id": ticket.payload.request_id,
                "message_id": message_id,
                "text": text,
                "xiaoxi": text,
                "yonghu_ming": user_callsign,
                "persona_name": "起源",
                "execute": True,
                "runtime_mode": "chat",
                "attachments": attachments,
                "knowledge_references": list(arguments.get("knowledge_references") or []),
                "conversation_context": {
                    "request_id": ticket.payload.request_id,
                    "active_id": ticket.payload.request_id,
                    "run_id": ticket.payload.run_id,
                    "generation": ticket.payload.generation,
                    "principal_scope_hash": ticket.payload.principal_scope_hash,
                    "execution_ticket_id": ticket.payload.ticket_id,
                    "workspace_id": ticket.payload.workspace_id,
                    "gateway_url": self._gateway_url,
                    "life_id": life_context["life_id"],
                    "agent_id": "qiyuan",
                    "session_id": session_id,
                    "conversation_id": session_id,
                    "attachments": attachments,
                    "recent_messages": [trusted_message, *recent],
                    "life_context": life_context,
                    "life_context_envelope": life_context["context_envelope"],
                    "life_context_hash": life_context["context_hash"],
                    "cycle_id": life_context["cycle_id"],
                    "skill_routing": skill_routing,
                },
                "life_context": life_context,
                "life_context_envelope": life_context["context_envelope"],
                "life_context_hash": life_context["context_hash"],
                "cycle_id": life_context["cycle_id"],
                "skill_routing": skill_routing,
                "metadata": {
                    "gateway_frontend": "tiangong_total_gateway",
                    "backend": "v3_complete_execution_chain",
                    "request_id": ticket.payload.request_id,
                    "session_id": session_id,
                    "life_id": life_context["life_id"],
                    "writer_epoch": life_context["writer_epoch"],
                    "cycle_id": life_context["cycle_id"],
                    "context_hash": life_context["context_hash"],
                    "execution_ticket_id": ticket.payload.ticket_id,
                    "run_id": ticket.payload.run_id,
                    "generation": ticket.payload.generation,
                    "principal_scope_hash": ticket.payload.principal_scope_hash,
                    "workspace_id": ticket.payload.workspace_id,
                    "gateway_url": self._gateway_url,
                    "agent_id": "qiyuan",
                    "effect_id": ticket.payload.effect_id,
                },
            }
            def mark_backend_started(marked_at_ms: int) -> None:
                nonlocal backend_started
                if self._on_backend_start is not None:
                    self._on_backend_start(marked_at_ms)
                backend_started = True

            try:
                remaining = max(0.1, execution_deadline - time.monotonic())
                status, backend_payload, backend_body_sha = self._backend.request(
                    "POST",
                    _BACKEND_PATH,
                    backend_request,
                    timeout_seconds=min(_INITIAL_BACKEND_RESPONSE_TIMEOUT_SECONDS, remaining),
                    backend_started=True,
                    before_request=mark_backend_started,
                )
                backend_terminal = self._backend_terminal(ticket.payload.request_id)
            except FrozenBackendCompatibilityError as exc:
                if exc.code != "compat.http.outcome_unknown" or not exc.backend_started:
                    raise
                status, backend_payload, backend_body_sha, backend_terminal = self._wait_backend_terminal(
                    ticket.payload.request_id,
                    deadline_monotonic=execution_deadline,
                )
            finished_at_ms = time.time_ns() // 1_000_000
            terminal = self._recover(ticket.payload.request_id, life_context["cycle_id"])
            reply = _first_text(backend_payload) if status < 400 else ""
            outputs, output_refs = self._capture_outputs(
                ticket,
                backend_payload,
                created_at_ms=finished_at_ms,
            )
            backend_classification = str(backend_terminal["classification"])
            if status < 400 and reply and backend_classification == "SUCCEEDED":
                result_status = "SUCCEEDED"
                error_code = None
                error_message = None
            elif status >= 500 or backend_classification == "AMBIGUOUS":
                result_status = "AMBIGUOUS"
                error_code = str(backend_terminal.get("reason_code") or "compat.backend.outcome_ambiguous")
                error_message = "The frozen backend did not provide a reliable terminal result."
            else:
                result_status = "FAILED_FINAL"
                error_code = str(backend_terminal.get("reason_code") or "compat.backend.reply_unavailable")
                error_message = "The frozen backend did not return a sendable reply."
            result_payload = {
                "reply_text": reply,
                "artifacts": outputs,
                "backend_http_status": status,
                "backend_response_sha256": backend_body_sha,
                "backend_terminal": backend_terminal,
                "life_terminal": terminal,
                "compatibility_boundary": "7184-ticket-gated-frozen-7174",
            }
            if isinstance(backend_payload, dict):
                for _structured_key in ("simple_chain_status", "terminal_reason", "last_transition", "origin"):
                    _structured_value = backend_payload.get(_structured_key)
                    if _structured_value not in (None, ""):
                        result_payload[_structured_key] = _structured_value
            fact_id = "fact_" + canonical_sha256(
                {
                    "domain": "tiangong.gateway.compat-execution-fact.v1",
                    "ticket_id": ticket.payload.ticket_id,
                    "effect_id": ticket.payload.effect_id,
                    "result_payload_sha256": canonical_sha256(result_payload),
                }
            )
            result = ExecutionResult(
                result_id="result_" + canonical_sha256(
                    {
                        "domain": "tiangong.gateway.compat-execution-result.v1",
                        "ticket_id": ticket.payload.ticket_id,
                        "effect_id": ticket.payload.effect_id,
                    }
                ),
                ticket_id=ticket.payload.ticket_id,
                request_id=ticket.payload.request_id,
                run_id=ticket.payload.run_id,
                generation=ticket.payload.generation,
                effect_id=ticket.payload.effect_id,
                action_id=ticket.payload.action_id,
                action_version=ticket.payload.action_version,
                status=result_status,
                attempt=1,
                started_at_ms=started_at_ms,
                finished_at_ms=finished_at_ms,
                side_effect_started=True,
                result_payload_sha256=canonical_sha256(result_payload),
                receipt_sha256=canonical_sha256(
                    {
                        "backend_response_sha256": backend_body_sha,
                        "backend_terminal": backend_terminal,
                        "life_terminal": terminal,
                    }
                ),
                output_object_refs=output_refs,
                fact_ids=(fact_id,),
                error_code=error_code,
                error_message=error_message,
            )
        except FrozenBackendCompatibilityError as exc:
            if ticket is None:
                raise BackendClientError(exc.code, ambiguous=exc.backend_started) from exc
            finished_at_ms = time.time_ns() // 1_000_000
            result_status = "AMBIGUOUS" if (backend_started or exc.backend_started) else "FAILED_RETRYABLE"
            result_payload = {
                "reply_text": "",
                "artifacts": [],
                "compatibility_boundary": "7184-ticket-gated-frozen-7174",
                "error_code": exc.code,
            }
            fact_id = "fact_" + canonical_sha256(
                {
                    "domain": "tiangong.gateway.compat-execution-fact.v1",
                    "ticket_id": ticket.payload.ticket_id,
                    "effect_id": ticket.payload.effect_id,
                    "result_payload_sha256": canonical_sha256(result_payload),
                }
            )
            result = ExecutionResult(
                result_id="result_" + canonical_sha256(
                    {
                        "domain": "tiangong.gateway.compat-execution-result.v1",
                        "ticket_id": ticket.payload.ticket_id,
                        "effect_id": ticket.payload.effect_id,
                    }
                ),
                ticket_id=ticket.payload.ticket_id,
                request_id=ticket.payload.request_id,
                run_id=ticket.payload.run_id,
                generation=ticket.payload.generation,
                effect_id=ticket.payload.effect_id,
                action_id=ticket.payload.action_id,
                action_version=ticket.payload.action_version,
                status=result_status,
                attempt=1,
                started_at_ms=started_at_ms,
                finished_at_ms=finished_at_ms,
                side_effect_started=result_status == "AMBIGUOUS",
                result_payload_sha256=canonical_sha256(result_payload),
                receipt_sha256=(
                    canonical_sha256({"outcome": "unknown", "error_code": exc.code})
                    if result_status == "AMBIGUOUS"
                    else None
                ),
                output_object_refs=(),
                fact_ids=(fact_id,),
                error_code=exc.code,
                error_message="Compatibility execution failed before a verified reply was available.",
            )
        assert ticket is not None
        return {
            "ok": True,
            "api_contract": BACKEND_API_CONTRACT,
            "execution_result": result.model_dump(mode="json"),
            "result_payload": result_payload,
        }


__all__ = [
    "FrozenBackendCompatibilityError",
    "FrozenBackendCompatibilityTransport",
    "_backend_terminal_projection",
]
