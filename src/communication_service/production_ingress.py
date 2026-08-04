"""7176 client for forwarding only durable, lease-authorized channel ingress to 7184."""

from __future__ import annotations

import http.client
import json
from typing import Protocol
from urllib.parse import urlsplit

from contracts import (
    ChannelAckPermit,
    ChannelOwnershipLease,
    InboundEnvelope,
    ProductionInboundAcceptance,
    ProductionInboundSubmission,
    build_production_inbound_submission,
    canonical_json_bytes,
)


PRODUCTION_CHANNEL_INGRESS_PATH = "/api/v1/gateway/internal/channel-inbound"
MAX_PRODUCTION_INGRESS_RESPONSE_BYTES = 64 * 1024


class ProductionIngressError(RuntimeError):
    pass


class ProductionIngressTransport(Protocol):
    def submit(self, submission: ProductionInboundSubmission) -> ProductionInboundAcceptance: ...


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


class LoopbackProductionIngressTransport:
    def __init__(self, origin: str, token: str, *, timeout_seconds: int = 10) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65_535
        ):
            raise ValueError("production ingress origin must be an exact loopback HTTP origin")
        if not 32 <= len(token) <= 512:
            raise ValueError("production ingress token length is invalid")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("production ingress timeout is invalid")
        self._host = "127.0.0.1"
        self._port = parsed.port
        self._token = token
        self._timeout_seconds = timeout_seconds

    def submit(self, submission: ProductionInboundSubmission) -> ProductionInboundAcceptance:
        if not submission.has_valid_sha256():
            raise ProductionIngressError("production ingress submission digest is invalid")
        body = canonical_json_bytes(submission.model_dump(mode="json"))
        connection = http.client.HTTPConnection(
            self._host,
            self._port,
            timeout=self._timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                PRODUCTION_CHANNEL_INGRESS_PATH,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "X-Tiangong-Communication-Token": self._token,
                },
            )
            response = connection.getresponse()
            payload_bytes = response.read(MAX_PRODUCTION_INGRESS_RESPONSE_BYTES + 1)
            if len(payload_bytes) > MAX_PRODUCTION_INGRESS_RESPONSE_BYTES:
                raise ProductionIngressError("production ingress response is too large")
            if response.status != 200:
                raise ProductionIngressError(
                    f"production ingress was rejected: HTTP {response.status}"
                )
            if str(response.getheader("Content-Type") or "").lower() != (
                "application/json; charset=utf-8"
            ):
                raise ProductionIngressError("production ingress response content type is invalid")
            try:
                json.loads(
                    payload_bytes,
                    object_pairs_hook=_reject_pairs,
                    parse_constant=_reject_constant,
                )
                acceptance = ProductionInboundAcceptance.model_validate_json(
                    payload_bytes,
                    strict=True,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ProductionIngressError("production ingress response JSON is invalid") from exc
            if (
                payload_bytes != canonical_json_bytes(acceptance.model_dump(mode="json"))
                or not acceptance.has_valid_sha256()
                or acceptance.submission_id != submission.submission_id
                or acceptance.submission_sha256 != submission.submission_sha256
                or acceptance.gateway_epoch != submission.gateway_epoch
                or acceptance.channel_ownership_lease_sha256
                != submission.channel_ownership_lease_sha256
                or acceptance.idempotency_key != submission.envelope.idempotency_key
                or acceptance.session_scope_hash
                != submission.envelope.conversation_scope_hash
                or acceptance.effects_started
                or acceptance.completion_claimed
            ):
                raise ProductionIngressError("production ingress acceptance binding is invalid")
            return acceptance
        except (OSError, http.client.HTTPException) as exc:
            raise ProductionIngressError("production ingress transport failed") from exc
        finally:
            connection.close()


class CommunicationProductionIngress:
    def __init__(self, transport: ProductionIngressTransport, *, source_instance_id: str) -> None:
        if not source_instance_id or len(source_instance_id) > 160:
            raise ValueError("production ingress source instance is invalid")
        self._transport = transport
        self._source_instance_id = source_instance_id

    def forward(
        self,
        envelope: InboundEnvelope,
        ack_permit: ChannelAckPermit,
        lease: ChannelOwnershipLease,
        *,
        submitted_at_ms: int,
    ) -> ProductionInboundAcceptance:
        if (
            not lease.has_valid_sha256()
            or lease.owner_component_id != "tiangong-communication-service"
            or lease.owner_instance_id != self._source_instance_id
            or lease.channel != envelope.channel
            or lease.tenant_id != envelope.tenant_id
            or lease.link_account_id != envelope.link_account_id
            or "POLL" not in lease.allowed_operations
            or not lease.not_before_ms <= submitted_at_ms < lease.expires_at_ms
        ):
            raise ProductionIngressError("production ingress channel ownership is invalid")
        submission = build_production_inbound_submission(
            envelope,
            ack_permit,
            source_instance_id=self._source_instance_id,
            gateway_epoch=lease.gateway_epoch,
            channel_ownership_lease_sha256=lease.lease_sha256,
            submitted_at_ms=submitted_at_ms,
        )
        return self._transport.submit(submission)


__all__ = [
    "CommunicationProductionIngress",
    "LoopbackProductionIngressTransport",
    "MAX_PRODUCTION_INGRESS_RESPONSE_BYTES",
    "PRODUCTION_CHANNEL_INGRESS_PATH",
    "ProductionIngressError",
    "ProductionIngressTransport",
]
