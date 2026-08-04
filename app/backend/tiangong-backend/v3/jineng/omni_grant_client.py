"""Fail-closed client for gateway-issued one-time Omni capability grants."""
from __future__ import annotations

import http.client
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

from ..run_context import current_run_context


_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_PATH = "/api/v1/internal/omni/grant"


class OmniGrantClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OmniGrantClientError("omni_grant_client.duplicate_json_key")
        result[key] = value
    return result


def _backend_token() -> str:
    # The frozen backend receives the gateway's backend-internal token through
    # TIANGONG_DESKTOP_TOKEN.  Prefer the explicit name for development runs.
    token = str(
        os.environ.get("TIANGONG_BACKEND_INTERNAL_TOKEN")
        or os.environ.get("TIANGONG_DESKTOP_TOKEN")
        or ""
    ).strip()
    if len(token) < 32:
        raise OmniGrantClientError("omni_grant_client.internal_token_missing")
    return token


def _gateway_endpoint() -> tuple[str, int]:
    context = current_run_context()
    try:
        parsed = urllib.parse.urlsplit(context.gateway_url or "http://127.0.0.1:7184")
        port = parsed.port
    except ValueError as exc:
        raise OmniGrantClientError("omni_grant_client.gateway_endpoint_invalid") from exc
    host = str(parsed.hostname or "")
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise OmniGrantClientError("omni_grant_client.gateway_endpoint_invalid")
    return "127.0.0.1", port


def _validate_context() -> None:
    context = current_run_context()
    if (
        not context.request_id
        or not context.run_id
        or type(context.generation) is not int
        or context.generation < 0
        or len(context.principal_scope_hash) != 64
        or not context.outer_execution_ticket_id
        or not context.workspace_id
    ):
        raise OmniGrantClientError("omni_grant_client.run_authority_missing")


def issue_omni_grant(
    invocation: Mapping[str, Any],
    *,
    workspace: Path,
    call_id: str = "",
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Request one signed grant for the exact model-proposed invocation."""

    _validate_context()
    context = current_run_context()
    action = str(invocation.get("action") or "").strip()
    target = str(invocation.get("target") or "").strip()
    args = invocation.get("args")
    if not action or not isinstance(args, Mapping):
        raise OmniGrantClientError("omni_grant_client.invocation_invalid")
    try:
        resolved_workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise OmniGrantClientError("omni_grant_client.workspace_invalid") from exc

    # A call id identifies one execution occurrence, not merely one set of
    # arguments.  Reusing an argument-derived id returns the already-consumed
    # one-time grant and makes every legitimate retry look like a nonce replay.
    occurrence_id = str(call_id or "").strip() or ("attempt_" + secrets.token_hex(32))
    payload = {
        "ticket_id": context.outer_execution_ticket_id,
        "call_id": "toolcall_" + hashlib.sha256(
            json.dumps(
                {
                    "request_id": context.request_id,
                    "run_id": context.run_id,
                    "generation": context.generation,
                    "occurrence_id": occurrence_id,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "request_id": context.request_id,
        "run_id": context.run_id,
        "generation": context.generation,
        "principal_scope_hash": context.principal_scope_hash,
        "action": action,
        "target": target,
        "args": dict(args),
        "workspace": str(resolved_workspace),
    }
    body = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    host, port = _gateway_endpoint()
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-store",
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "X-Tiangong-Token": _backend_token(),
    }
    timeout = max(1.0, min(timeout_seconds, 30.0))
    transport_errors = (OSError, TimeoutError, http.client.HTTPException)
    for attempt in range(3):
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        response = None
        try:
            # Body, call_id and authentication header are deliberately built
            # once. A lost response may be retried, but it remains the same
            # idempotent authority request.
            connection.request("POST", _PATH, body=body, headers=headers)
            response = connection.getresponse()
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise OmniGrantClientError("omni_grant_client.response_too_large")
            if content_type not in {"application/json", "application/problem+json"}:
                raise OmniGrantClientError("omni_grant_client.response_content_type_invalid")
            try:
                decoded = json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except OmniGrantClientError:
                raise
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise OmniGrantClientError("omni_grant_client.response_json_invalid") from exc
            if not isinstance(decoded, dict):
                raise OmniGrantClientError("omni_grant_client.response_object_required")
            if response.status != 200 or decoded.get("status") != "OK":
                reason = str(decoded.get("reason_code") or "omni_grant_client.rejected")
                raise OmniGrantClientError(reason)
            grant = decoded.get("grant")
            runtime = decoded.get("runtime")
            if not isinstance(grant, dict) or not isinstance(runtime, dict):
                raise OmniGrantClientError("omni_grant_client.authority_payload_invalid")
            return {"grant": dict(grant), "runtime": dict(runtime)}
        except OmniGrantClientError:
            # A received protocol or policy decision is authoritative and must
            # never be replayed as though it were a transport failure.
            raise
        except transport_errors as exc:
            if attempt == 2:
                raise OmniGrantClientError("omni_grant_client.gateway_unavailable") from exc
        except Exception as exc:
            raise OmniGrantClientError("omni_grant_client.gateway_unavailable") from exc
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                connection.close()
        time.sleep(0.05 * (attempt + 1))
    raise OmniGrantClientError("omni_grant_client.gateway_unavailable")


__all__ = ["OmniGrantClientError", "issue_omni_grant"]
