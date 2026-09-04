"""Strict 7174 client that refuses all execution without a fixed ExecutionTicket."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from contracts import (
    CapabilityManifest,
    CompositionExecutionBindingV1,
    ExecutionResult,
    ExecutionTicket,
    TrustBundle,
    authorize_execution_contract,
    canonical_json_bytes,
    canonical_sha256,
)

from .store import GatewayStateStore, StoreConflictError
from .tickets import verify_execution_ticket


BACKEND_EXECUTION_PATH = "/api/v1/gateway/internal/execute-ticket"
BACKEND_API_CONTRACT = "tiangong.desktop.backend.v3"
_BACKEND_VERIFIED_RESPONSE = object()


class BackendClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status: int = 0,
        ambiguous: bool = False,
        pending_transport_future: object | None = None,
    ) -> None:
        super().__init__(code)
        if pending_transport_future is not None and not callable(
            getattr(pending_transport_future, "add_done_callback", None)
        ):
            raise TypeError("pending transport future is invalid")
        self.code = code
        self.status = status
        self.ambiguous = ambiguous
        # A running in-process transport cannot be killed when its caller's
        # timeout expires.  The composition owner uses this handle only to
        # retain its durable dispatch permit until the real call has stopped.
        self.pending_transport_future = pending_transport_future


class BackendExecutionTransport(Protocol):
    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, Any]: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, *args: object, **kwargs: object) -> None:
        raise BackendClientError("backend.http.redirect_forbidden")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BackendClientError("backend.http.duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise BackendClientError("backend.http.non_finite_json")


class LoopbackBackendExecutionTransport:
    """Exact endpoint transport; no legacy inbound/chat fallback exists by design."""

    def __init__(
        self,
        base_url: str,
        *,
        service_auth_assertion: str,
        max_response_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port is None
        ):
            raise ValueError("backend base URL must be an explicit loopback HTTP origin")
        if (
            not service_auth_assertion
            or len(service_auth_assertion) > 32_768
            or any(ord(char) < 33 for char in service_auth_assertion)
        ):
            raise ValueError("backend service-auth assertion is missing or malformed")
        if not 1024 <= max_response_bytes <= 128 * 1024 * 1024:
            raise ValueError("backend response limit is out of bounds")
        self._endpoint = base_url.rstrip("/") + BACKEND_EXECUTION_PATH
        self._service_auth_assertion = service_auth_assertion
        self._max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def _decode(self, body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_pairs,
                parse_constant=_reject_constant,
            )
        except BackendClientError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendClientError("backend.http.invalid_json", ambiguous=True) from exc
        if not isinstance(payload, dict):
            raise BackendClientError("backend.http.non_object_json", ambiguous=True)
        return payload

    def _read_bounded(self, response: Any) -> bytes:
        body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise BackendClientError("backend.http.response_too_large", ambiguous=True)
        return body

    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, Any]:
        if not body or len(body) > 16 * 1024 * 1024 or not 0.1 <= timeout_seconds <= 3_600:
            raise ValueError("backend execution request size or timeout is invalid")
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Content-Type": "application/json; charset=utf-8",
                "X-Tiangong-Service-Auth": self._service_auth_assertion,
            },
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
                if content_type not in {"application/json", "application/problem+json"}:
                    raise BackendClientError(
                        "backend.http.content_type_invalid",
                        status=int(response.status),
                        ambiguous=True,
                    )
                return self._decode(self._read_bounded(response))
        except BackendClientError:
            raise
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                payload = self._decode(self._read_bounded(exc))
                code = str(payload.get("error_code") or "backend.http.upstream_error")
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", code):
                    code = "backend.http.upstream_error"
            except BackendClientError:
                code = "backend.http.upstream_error"
            ambiguous = status >= 500
            raise BackendClientError(code, status=status, ambiguous=ambiguous) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise BackendClientError("backend.http.outcome_unknown", ambiguous=True) from exc


# Argument branches that carry natural-language message text rather than
# tool/file path parameters.  ``RequestProcessor.process`` places the raw
# recent conversation history under ``recent_messages`` and the current user
# utterance under ``text``; users legitimately paste host paths into chat,
# so these branches (and everything nested inside them, e.g. each message's
# ``content``) must not be scanned for host-path shapes.  Genuine tool or
# file path parameters keep the strict check below.
_NATURAL_TEXT_KEYS = frozenset({"messages", "recent_messages", "text"})


def _reject_host_paths(value: object, _in_natural_text: bool = False) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        raise BackendClientError("backend.arguments.float_forbidden")
    if isinstance(value, str):
        if _in_natural_text:
            return
        normalized = value.replace("\\", "/")
        if (
            re.match(r"^[A-Za-z]:/", normalized)
            or normalized.startswith("//")
            or normalized.startswith("/")
            or normalized.casefold().startswith("file:")
            or ".." in normalized.split("/")
        ):
            raise BackendClientError("backend.arguments.host_path_forbidden")
        return
    if isinstance(value, list):
        for item in value:
            _reject_host_paths(item, _in_natural_text)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BackendClientError("backend.arguments.non_string_key")
            _reject_host_paths(item, _in_natural_text or key in _NATURAL_TEXT_KEYS)
        return
    raise BackendClientError("backend.arguments.unsupported_type")


@dataclass(frozen=True)
class BackendExecutionResponse:
    result: ExecutionResult
    result_payload: object
    response_sha256: str
    ticket: ExecutionTicket = field(repr=False)
    _verification_marker: object = field(repr=False, compare=False)

    def assert_verified(self) -> None:
        if self._verification_marker is not _BACKEND_VERIFIED_RESPONSE:
            raise BackendClientError("backend.result.unverified")


class BackendClient:
    def __init__(
        self,
        transport: BackendExecutionTransport,
        nonce_store: GatewayStateStore,
        *,
        ticket_consumer_instance_id: str,
    ) -> None:
        if not ticket_consumer_instance_id or len(ticket_consumer_instance_id) > 160:
            raise ValueError("ticket consumer instance ID is invalid")
        self._transport = transport
        self._nonce_store = nonce_store
        self._ticket_consumer_instance_id = ticket_consumer_instance_id

    def execute(
        self,
        ticket: ExecutionTicket,
        arguments: dict[str, object],
        *,
        capability_manifest: CapabilityManifest,
        trust_bundle: TrustBundle,
        now_ms: int,
        expected_gateway_epoch: int,
        minimum_generation: int,
        grant: object | None = None,
        intent: object | None = None,
        decision: object | None = None,
        impact: object | None = None,
        claim: object | None = None,
        expected_fence_epoch: int | None = None,
        active_lease_epoch: int | None = None,
        expected_target_snapshot_sha256: str | None = None,
        expected_composition_binding: CompositionExecutionBindingV1 | None = None,
        actual_target_snapshot_sha256: str | None = None,
        before_dispatch: Callable[[int], None] | None = None,
        transport_runner: Callable[
            [Callable[[], dict[str, Any]], float], dict[str, Any]
        ]
        | None = None,
    ) -> BackendExecutionResponse:
        if now_ms < 0 or expected_gateway_epoch < 1 or minimum_generation < 0:
            raise ValueError("backend execution boundary time or generation is invalid")
        if transport_runner is not None and not callable(transport_runner):
            raise ValueError("backend transport runner is invalid")
        _reject_host_paths(arguments)
        argument_bytes = canonical_json_bytes(arguments)
        if len(argument_bytes) > 8 * 1024 * 1024:
            raise BackendClientError("backend.arguments.too_large")
        if canonical_sha256(arguments) != ticket.payload.arguments_hash:
            raise BackendClientError("backend.arguments.digest_mismatch")

        composition_arguments_sha256: str | None = None
        composition_target_sha256: str | None = None
        if expected_composition_binding is not None:
            if (
                set(arguments) != {"action", "target", "args"}
                or not isinstance(arguments.get("action"), str)
                or not isinstance(arguments.get("target"), str)
                or not isinstance(arguments.get("args"), dict)
                or arguments.get("action")
                != expected_composition_binding.action_id
            ):
                raise BackendClientError("backend.composition.invocation_invalid")
            composition_arguments_sha256 = canonical_sha256(arguments["args"])
            composition_target_sha256 = canonical_sha256(arguments["target"])

        verify_execution_ticket(ticket, trust_bundle, now_ms=now_ms)
        authorize_execution_contract(
            ticket,
            capability_manifest,
            signature_verified=True,
            now_ms=now_ms,
            expected_gateway_epoch=expected_gateway_epoch,
            minimum_generation=minimum_generation,
            grant=grant,
            intent=intent,
            decision=decision,
            impact=impact,
            claim=claim,
            expected_fence_epoch=expected_fence_epoch,
            active_lease_epoch=active_lease_epoch,
            expected_target_snapshot_sha256=expected_target_snapshot_sha256,
            actual_arguments_sha256=canonical_sha256(arguments),
            expected_composition_binding=expected_composition_binding,
            actual_materialized_arguments_sha256=(
                composition_arguments_sha256
            ),
            actual_target_sha256=composition_target_sha256,
            actual_target_snapshot_sha256=(
                actual_target_snapshot_sha256
            ),
        )
        dispatch_boundary_crossed = False
        if before_dispatch is not None:
            before_dispatch(now_ms)
            dispatch_boundary_crossed = True
        ticket_sha256 = canonical_sha256(ticket.model_dump(mode="json"))
        try:
            consumed = self._nonce_store.consume_security_nonce(
                issuer=ticket.payload.issuer,
                audience=ticket.payload.audience,
                purpose="execution_ticket",
                nonce=ticket.payload.nonce,
                payload_sha256=ticket_sha256,
                gateway_epoch=ticket.payload.gateway_epoch,
                consumer_instance_id=self._ticket_consumer_instance_id,
                consumed_at_ms=now_ms,
                expires_at_ms=ticket.payload.expires_at_ms,
            )
        except StoreConflictError as exc:
            raise BackendClientError(
                "backend.ticket.replay_conflict",
                ambiguous=dispatch_boundary_crossed,
            ) from exc
        if not consumed.consumed_by_this_call:
            raise BackendClientError(
                "backend.ticket.replay_forbidden",
                ambiguous=dispatch_boundary_crossed,
            )

        wire = {
            "schema": "tiangong.backend.execute-ticket.v1",
            "ticket": ticket.model_dump(mode="json"),
            "arguments": arguments,
        }
        timeout_seconds = ticket.payload.max_runtime_ms / 1000
        request_bytes = canonical_json_bytes(wire)
        try:
            if transport_runner is None:
                payload = self._transport.execute(
                    request_bytes,
                    timeout_seconds=timeout_seconds,
                )
            else:
                payload = transport_runner(
                    lambda: self._transport.execute(
                        request_bytes,
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout_seconds,
                )
        except BackendClientError as exc:
            if dispatch_boundary_crossed and not exc.ambiguous:
                raise BackendClientError(
                    exc.code,
                    status=exc.status,
                    ambiguous=True,
                    pending_transport_future=exc.pending_transport_future,
                ) from exc
            raise
        except Exception as exc:
            raise BackendClientError(
                "backend.transport.failed",
                ambiguous=dispatch_boundary_crossed,
            ) from exc
        if set(payload) != {"ok", "api_contract", "execution_result", "result_payload"}:
            raise BackendClientError("backend.result.envelope_invalid", ambiguous=True)
        if payload.get("ok") is not True or payload.get("api_contract") != BACKEND_API_CONTRACT:
            raise BackendClientError("backend.result.contract_mismatch", ambiguous=True)
        try:
            result = ExecutionResult.model_validate_json(
                canonical_json_bytes(payload["execution_result"]),
                strict=True,
            )
        except ValueError as exc:
            raise BackendClientError("backend.result.invalid", ambiguous=True) from exc
        result_payload = payload["result_payload"]
        try:
            result_payload_sha256 = canonical_sha256(result_payload)
        except (TypeError, ValueError) as exc:
            raise BackendClientError("backend.result.payload_invalid", ambiguous=True) from exc
        expected = ticket.payload
        if (
            result.ticket_id != expected.ticket_id
            or result.request_id != expected.request_id
            or result.run_id != expected.run_id
            or result.generation != expected.generation
            or result.effect_id != expected.effect_id
            or result.action_id != expected.action_id
            or result.action_version != expected.action_version
            or (claim is not None and result.attempt != claim.attempt)
            or result.result_payload_sha256 != result_payload_sha256
        ):
            raise BackendClientError("backend.result.binding_mismatch", ambiguous=True)
        return BackendExecutionResponse(
            result=result,
            result_payload=result_payload,
            response_sha256=canonical_sha256(payload),
            ticket=ticket,
            _verification_marker=_BACKEND_VERIFIED_RESPONSE,
        )


__all__ = [
    "BACKEND_API_CONTRACT",
    "BACKEND_EXECUTION_PATH",
    "BackendClient",
    "BackendClientError",
    "BackendExecutionResponse",
    "BackendExecutionTransport",
    "LoopbackBackendExecutionTransport",
]
