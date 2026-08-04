"""Authenticated 7176 to 7184 production channel-ingress boundary."""

from __future__ import annotations

import hmac
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping
from urllib.parse import urlsplit

from contracts import (
    ProductionInboundAcceptance,
    ProductionInboundSubmission,
    canonical_json_bytes,
)

from .store import StoreConflictError, StoreError

if TYPE_CHECKING:
    from .runtime import GatewayRuntime


PRODUCTION_CHANNEL_INGRESS_PATH = "/api/v1/gateway/internal/channel-inbound"
MAX_PRODUCTION_INGRESS_BYTES = 2 * 1024 * 1024


class ChannelIngressApiError(RuntimeError):
    def __init__(self, status: int, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _parse_submission(body: bytes) -> ProductionInboundSubmission:
    if not body or len(body) > MAX_PRODUCTION_INGRESS_BYTES:
        raise ChannelIngressApiError(
            413 if body else 400,
            "channel_ingress.request_size.invalid",
        )
    try:
        json.loads(
            body,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
        submission = ProductionInboundSubmission.model_validate_json(body, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ChannelIngressApiError(400, "channel_ingress.submission.invalid") from exc
    if not submission.has_valid_sha256():
        raise ChannelIngressApiError(400, "channel_ingress.submission.digest_invalid")
    if body != canonical_json_bytes(submission.model_dump(mode="json")):
        raise ChannelIngressApiError(400, "channel_ingress.submission.noncanonical")
    return submission


@dataclass(frozen=True)
class ChannelIngressApiResponse:
    status: int
    payload: dict[str, object]


class ChannelIngressApiRouter:
    def __init__(self, runtime: GatewayRuntime, token: str) -> None:
        if not 32 <= len(token) <= 512:
            raise ValueError("production channel-ingress token length is invalid")
        self._runtime = runtime
        self._token = token

    @staticmethod
    def handles_path(raw_target: str) -> bool:
        parsed = urlsplit(raw_target)
        return (
            not parsed.scheme
            and not parsed.netloc
            and not parsed.fragment
            and parsed.path == PRODUCTION_CHANNEL_INGRESS_PATH
        )

    def authorize(self, token: str) -> bool:
        return bool(token) and hmac.compare_digest(
            token.encode("utf-8"), self._token.encode("utf-8")
        )

    def dispatch(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now_ms: int | None = None,
    ) -> ChannelIngressApiResponse:
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        parsed = urlsplit(raw_target)
        if headers.get("Origin"):
            raise ChannelIngressApiError(403, "channel_ingress.browser_origin.forbidden")
        if method != "POST" or parsed.query:
            raise ChannelIngressApiError(405, "channel_ingress.method.invalid")
        content_type = str(headers.get("Content-Type") or "").lower()
        if content_type not in {"application/json", "application/json; charset=utf-8"}:
            raise ChannelIngressApiError(415, "channel_ingress.content_type.invalid")
        submission = _parse_submission(body)
        if submission.gateway_epoch != self._runtime.lease.gateway_epoch:
            raise ChannelIngressApiError(409, "channel_ingress.gateway_epoch.stale")
        lease = self._runtime.get_active_channel_lease(
            channel=submission.envelope.channel,
            tenant_id=submission.envelope.tenant_id,
            link_account_id=submission.envelope.link_account_id,
            now_ms=observed_ms,
        )
        if lease is None:
            raise ChannelIngressApiError(409, "channel_ingress.channel_ownership.inactive")
        if (
            not lease.has_valid_sha256()
            or lease.lease_sha256 != submission.channel_ownership_lease_sha256
            or lease.gateway_epoch != submission.gateway_epoch
            or lease.owner_instance_id != submission.source_instance_id
            or lease.owner_component_id != submission.source_component_id
            or lease.channel != submission.envelope.channel
            or lease.tenant_id != submission.envelope.tenant_id
            or lease.link_account_id != submission.envelope.link_account_id
            or "POLL" not in lease.allowed_operations
        ):
            raise ChannelIngressApiError(409, "channel_ingress.channel_ownership.mismatch")
        try:
            registration = self._runtime.store.register_request(
                submission.envelope,
                ingress_sha256=submission.inbox_record_sha256,
                created_at_ms=observed_ms,
            )
        except StoreConflictError as exc:
            raise ChannelIngressApiError(409, "channel_ingress.request.conflict") from exc
        except (StoreError, sqlite3.DatabaseError) as exc:
            raise ChannelIngressApiError(503, "channel_ingress.store.unavailable") from exc
        except ValueError as exc:
            raise ChannelIngressApiError(400, "channel_ingress.request.invalid") from exc
        acceptance = ProductionInboundAcceptance(
            submission_id=submission.submission_id,
            submission_sha256=submission.submission_sha256,
            gateway_epoch=submission.gateway_epoch,
            channel_ownership_lease_sha256=lease.lease_sha256,
            idempotency_key=submission.envelope.idempotency_key,
            request_id=registration.entry.request_id,
            request_entry_sha256=registration.entry.entry_sha256,
            session_scope_hash=registration.entry.session_scope_hash,
            queue_sequence=registration.queue_sequence,
            queue_state=registration.queue_state,
            request_created_at_ms=registration.entry.created_at_ms,
            accepted_at_ms=observed_ms,
            request_created=registration.created_by_this_call,
            duplicate=registration.duplicate,
            acceptance_sha256="0" * 64,
        ).with_computed_sha256()
        return ChannelIngressApiResponse(200, acceptance.model_dump(mode="json"))


__all__ = [
    "ChannelIngressApiError",
    "ChannelIngressApiResponse",
    "ChannelIngressApiRouter",
    "MAX_PRODUCTION_INGRESS_BYTES",
    "PRODUCTION_CHANNEL_INGRESS_PATH",
]
