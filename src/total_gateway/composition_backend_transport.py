"""Ticket-gated in-process transport for one authorized composition step.

This is a narrow ``BackendExecutionTransport`` implementation, not a Runtime
or scheduler.  ``BackendClient`` has already verified the ExecutionTicket and
calls this transport only after the canonical Effect dispatch permit exists.
The transport binds that ticket to the persisted Omni grant/runtime metadata,
uses the embedded backend's private composition route, and translates the raw
Omni result into the existing backend execution envelope.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping, Protocol

from contracts import (
    ExecutionResult,
    ExecutionTicket,
    OmniCapabilityGrant,
    canonical_json_bytes,
    canonical_sha256,
)

from .action_registry import ActionRegistryError, ActionSchemaCatalog
from .backend_client import BACKEND_API_CONTRACT, BackendClientError


COMPOSITION_BACKEND_PATH = "/api/v1/internal/composition/execute-ticket"
COMPOSITION_BACKEND_REQUEST_SCHEMA = (
    "tiangong.backend.composition-execute-ticket.v1"
)
COMPOSITION_RESULT_PAYLOAD_SCHEMA = (
    "tiangong.backend.composition-result-payload.v1"
)


class _CompatibilityJsonClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        timeout_seconds: float,
        backend_started: bool = False,
        before_request: Any = None,
    ) -> tuple[int, dict[str, Any], str]: ...


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BackendClientError("backend.composition.duplicate_json_key")
        value[key] = item
    return value


def _decode_wire(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                BackendClientError("backend.composition.non_finite_json")
            ),
        )
    except BackendClientError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendClientError("backend.composition.invalid_json") from exc
    if not isinstance(value, dict):
        raise BackendClientError("backend.composition.envelope_invalid")
    return value


def _safe_error_message(payload: Mapping[str, Any]) -> str:
    value = str(
        payload.get("cuowu")
        or payload.get("error")
        or payload.get("message")
        or "composition action returned failure"
    )
    return value[:512]


class CompositionBackendExecutionTransport:
    """Call only the embedded Omni Body composition route.

    The signed grant and runtime metadata are constructor-bound so no caller
    can substitute them through the normal BackendClient argument map.
    """

    def __init__(
        self,
        client: _CompatibilityJsonClient,
        *,
        signed_grant: Mapping[str, Any],
        runtime_meta: Mapping[str, Any],
        schema_catalog: ActionSchemaCatalog,
        expected_result_schema_sha256: str,
    ) -> None:
        if not callable(getattr(client, "request", None)):
            raise ValueError("composition backend client is unavailable")
        if (
            not isinstance(schema_catalog, ActionSchemaCatalog)
            or not schema_catalog.has_valid_sha256()
        ):
            raise ValueError("composition result schema catalog is invalid")
        if (
            not isinstance(expected_result_schema_sha256, str)
            or len(expected_result_schema_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_result_schema_sha256
            )
        ):
            raise ValueError("composition expected result schema hash is invalid")
        try:
            self._grant = json.loads(canonical_json_bytes(dict(signed_grant)))
            self._runtime = json.loads(canonical_json_bytes(dict(runtime_meta)))
        except (TypeError, ValueError) as exc:
            raise ValueError("composition backend authority payload is invalid") from exc
        if not isinstance(self._grant, dict) or not isinstance(self._runtime, dict):
            raise ValueError("composition backend authority payload is invalid")
        self._client = client
        self._schema_catalog = schema_catalog
        self._expected_result_schema_sha256 = expected_result_schema_sha256

    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, Any]:
        if not body or len(body) > 16 * 1024 * 1024 or not 0.1 <= timeout_seconds <= 3_600:
            raise ValueError("composition backend request size or timeout is invalid")
        wire = _decode_wire(body)
        if (
            set(wire) != {"schema", "ticket", "arguments"}
            or wire.get("schema") != "tiangong.backend.execute-ticket.v1"
        ):
            raise BackendClientError("backend.composition.envelope_invalid")
        try:
            ticket = ExecutionTicket.model_validate_json(
                canonical_json_bytes(wire["ticket"]), strict=True
            )
            grant = OmniCapabilityGrant.model_validate_json(
                canonical_json_bytes(self._grant), strict=True
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendClientError("backend.composition.ticket_invalid") from exc
        invocation = wire.get("arguments")
        if (
            not isinstance(invocation, dict)
            or set(invocation) != {"action", "target", "args"}
            or not isinstance(invocation.get("action"), str)
            or not isinstance(invocation.get("target"), str)
            or not isinstance(invocation.get("args"), dict)
            or canonical_sha256(invocation) != ticket.payload.arguments_hash
        ):
            raise BackendClientError("backend.composition.arguments_invalid")

        grant_payload = grant.payload
        ticket_binding = ticket.payload.composition_execution_binding
        if (
            ticket_binding is None
            or grant_payload.composition_execution_binding != ticket_binding
            or grant_payload.ticket_id != ticket.payload.ticket_id
            or grant_payload.ticket_sha256
            != canonical_sha256(ticket.payload.model_dump(mode="json"))
            or grant_payload.effect_id != ticket.payload.effect_id
            or grant_payload.arguments_sha256 != ticket.payload.arguments_hash
            or grant_payload.action_id != invocation["action"]
            or grant_payload.action_version != ticket.payload.action_version
            or self._runtime.get("execution_ticket_id") != ticket.payload.ticket_id
            or self._runtime.get("effect_id") != ticket.payload.effect_id
            or self._runtime.get("action_id") != invocation["action"]
            or self._runtime.get("composition_execution_binding")
            != ticket_binding.model_dump(mode="json")
            or self._runtime.get("composition_binding_sha256")
            != ticket_binding.binding_sha256
            or self._runtime.get("fact_kernel_enabled") is not False
        ):
            raise BackendClientError("backend.composition.authority_mismatch")

        # Resolve the signed request's result authority before crossing the
        # handler boundary.  The expected digest is supplied by the persisted
        # composition authorization, not by the raw backend response.
        try:
            self._schema_catalog.resolve(
                ticket.payload.action_id,
                ticket.payload.action_version,
                expected_result_sha256=self._expected_result_schema_sha256,
                require_result_explicit=True,
            )
        except ActionRegistryError as exc:
            raise BackendClientError(
                "backend.composition.result_schema_authority_invalid"
            ) from exc

        request_payload = {
            "schema": COMPOSITION_BACKEND_REQUEST_SCHEMA,
            "execute_ticket": wire,
            "capability_grant": self._grant,
            "runtime": self._runtime,
        }
        started_at_ms = time.time_ns() // 1_000_000
        try:
            status, backend_payload, backend_sha256 = self._client.request(
                "POST",
                COMPOSITION_BACKEND_PATH,
                request_payload,
                timeout_seconds=timeout_seconds,
                backend_started=True,
            )
        except BackendClientError:
            raise
        except Exception as exc:
            raise BackendClientError(
                "backend.composition.outcome_unknown", ambiguous=True
            ) from exc
        finished_at_ms = time.time_ns() // 1_000_000
        if not isinstance(backend_payload, dict):
            raise BackendClientError(
                "backend.composition.response_invalid", ambiguous=True
            )
        if status >= 500:
            raise BackendClientError(
                "backend.composition.outcome_unknown",
                status=status,
                ambiguous=True,
            )

        try:
            raw_result_bytes = json.dumps(
                backend_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            raise BackendClientError(
                "backend.composition.response_invalid", ambiguous=True
            ) from exc
        raw_result_sha256 = hashlib.sha256(raw_result_bytes).hexdigest()
        if raw_result_sha256 != backend_sha256:
            raise BackendClientError(
                "backend.composition.response_digest_mismatch", ambiguous=True
            )

        # This transport has no ObjectStore authority.  A backend may omit the
        # field or explicitly report an empty list, but it cannot manufacture
        # object identities that the Gateway did not verify and persist.
        claimed_output_object_refs = backend_payload.get("output_object_refs")
        if claimed_output_object_refs not in (None, []):
            raise BackendClientError(
                "backend.composition.output_object_refs_untrusted",
                ambiguous=True,
            )

        succeeded = status < 400 and backend_payload.get("ok") is True
        output_too_large = len(raw_result_bytes) > ticket.payload.max_output_bytes
        if succeeded and not output_too_large:
            try:
                self._schema_catalog.validate_result_exact(
                    ticket.payload.action_id,
                    ticket.payload.action_version,
                    backend_payload,
                )
            except ActionRegistryError as exc:
                raise BackendClientError(
                    "backend.composition.result_schema_rejected",
                    ambiguous=True,
                ) from exc
        result_status = (
            "SUCCEEDED"
            if succeeded and not output_too_large
            else "FAILED_FINAL"
        )
        result_payload: object = {
            "schema": COMPOSITION_RESULT_PAYLOAD_SCHEMA,
            "backend_http_status": status,
            "backend_response_sha256": backend_sha256,
            "execution_boundary": "embedded-omni-body-composition-v1",
            "omni_ok": backend_payload.get("ok") is True,
            # The legacy Omni result may legitimately contain finite floats
            # (for example elapsed_seconds).  Store its strict JSON bytes as a
            # string so Gateway canonical JSON remains integer-only while the
            # exact result is still content-addressed and inspectable.
            "omni_result_json": raw_result_bytes.decode("utf-8"),
            "omni_result_sha256": raw_result_sha256,
            "omni_result_size_bytes": len(raw_result_bytes),
        }
        if (
            output_too_large
            or len(canonical_json_bytes(result_payload))
            > ticket.payload.max_output_bytes
        ):
            output_too_large = True
            result_status = "FAILED_FINAL"
            result_payload = {
                "error_code": "composition.runtime.output_too_large",
                "omni_result_sha256": raw_result_sha256,
                "omni_result_size_bytes": len(raw_result_bytes),
            }
            # The manifest compiler should never admit an Action whose output
            # ceiling cannot hold this bounded failure receipt.  If authority
            # is nevertheless inconsistent, the outcome after STARTED is
            # unknown rather than silently violating the signed envelope.
            if len(canonical_json_bytes(result_payload)) > ticket.payload.max_output_bytes:
                raise BackendClientError(
                    "backend.composition.output_envelope_impossible",
                    ambiguous=True,
                )
        result_payload_sha256 = canonical_sha256(result_payload)
        fact_id = "fact_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.composition-execution-fact.v1",
                "ticket_id": ticket.payload.ticket_id,
                "effect_id": ticket.payload.effect_id,
                "result_payload_sha256": result_payload_sha256,
            }
        )
        error_code = (
            None
            if result_status == "SUCCEEDED"
            else "composition.runtime.output_too_large"
            if output_too_large
            else "composition.runtime.action_failed"
        )
        result = ExecutionResult(
            result_id="result_" + canonical_sha256(
                {
                    "domain": "tiangong.gateway.composition-execution-result.v1",
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
            attempt=(1 if ticket_binding.attempt is None else ticket_binding.attempt),
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
            side_effect_started=True,
            result_payload_sha256=result_payload_sha256,
            receipt_sha256=canonical_sha256(
                {
                    "backend_http_status": status,
                    "backend_response_sha256": backend_sha256,
                }
            ),
            output_object_refs=(),
            fact_ids=(fact_id,),
            error_code=error_code,
            error_message=(
                None
                if result_status == "SUCCEEDED"
                else "composition result exceeded the signed output envelope"
                if output_too_large
                else _safe_error_message(backend_payload)
            ),
        )
        return {
            "ok": True,
            "api_contract": BACKEND_API_CONTRACT,
            "execution_result": result.model_dump(mode="json"),
            "result_payload": result_payload,
        }


__all__ = [
    "COMPOSITION_BACKEND_PATH",
    "COMPOSITION_BACKEND_REQUEST_SCHEMA",
    "COMPOSITION_RESULT_PAYLOAD_SCHEMA",
    "CompositionBackendExecutionTransport",
]
