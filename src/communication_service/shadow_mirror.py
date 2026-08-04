"""7176 observe-only ingress mirror client for P8 shadow comparisons."""

from __future__ import annotations

import http.client
import json
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from contracts import (
    ChannelAckPermit,
    InboundEnvelope,
    ShadowComparison,
    ShadowDecisionObservation,
    ShadowObservationBatch,
    build_shadow_decision_observation,
    build_shadow_ingress_copy,
    canonical_json_bytes,
    canonical_sha256,
)

if TYPE_CHECKING:
    from .feishu_inbound import FeishuInboundOutcome
    from .wechat_inbound import WechatInboundOutcome


SHADOW_OBSERVE_PATH = "/api/v1/migration/shadow/observations"
MAX_SHADOW_RESPONSE_BYTES = 2 * 1024 * 1024


class ShadowMirrorError(RuntimeError):
    pass


class ShadowMirrorTransport(Protocol):
    def submit(self, batch: ShadowObservationBatch) -> ShadowComparison: ...


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


class LoopbackShadowMirrorTransport:
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
            raise ValueError("shadow mirror origin must be an exact loopback HTTP origin")
        if not 32 <= len(token) <= 512:
            raise ValueError("shadow mirror token length is invalid")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("shadow mirror timeout is invalid")
        self._host = "127.0.0.1"
        self._port = parsed.port
        self._token = token
        self._timeout_seconds = timeout_seconds

    def submit(self, batch: ShadowObservationBatch) -> ShadowComparison:
        if not batch.has_valid_sha256():
            raise ShadowMirrorError("shadow mirror batch digest is invalid")
        body = canonical_json_bytes(batch.model_dump(mode="json"))
        connection = http.client.HTTPConnection(
            self._host,
            self._port,
            timeout=self._timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                SHADOW_OBSERVE_PATH,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "X-Tiangong-Shadow-Token": self._token,
                },
            )
            response = connection.getresponse()
            payload_bytes = response.read(MAX_SHADOW_RESPONSE_BYTES + 1)
            if len(payload_bytes) > MAX_SHADOW_RESPONSE_BYTES:
                raise ShadowMirrorError("shadow mirror response is too large")
            if response.status != 200:
                raise ShadowMirrorError(f"shadow mirror rejected observation: HTTP {response.status}")
            content_type = str(response.getheader("Content-Type") or "").lower()
            if content_type != "application/json; charset=utf-8":
                raise ShadowMirrorError("shadow mirror response content type is invalid")
            try:
                payload = json.loads(
                    payload_bytes,
                    object_pairs_hook=_reject_pairs,
                    parse_constant=_reject_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ShadowMirrorError("shadow mirror response JSON is invalid") from exc
            if (
                not isinstance(payload, dict)
                or payload.get("mode") != "OBSERVE_ONLY"
                or payload.get("request_created") is not False
                or payload.get("effects_permitted") is not False
                or not isinstance(payload.get("comparison"), dict)
            ):
                raise ShadowMirrorError("shadow mirror response authority is invalid")
            try:
                comparison = ShadowComparison.model_validate_json(
                    canonical_json_bytes(payload["comparison"]),
                    strict=True,
                )
            except ValueError as exc:
                raise ShadowMirrorError("shadow mirror comparison is invalid") from exc
            if (
                not comparison.has_valid_sha256()
                or comparison.shadow_id != batch.ingress_copy.shadow_id
            ):
                raise ShadowMirrorError("shadow mirror comparison digest or identity is invalid")
            return comparison
        except (OSError, http.client.HTTPException) as exc:
            raise ShadowMirrorError("shadow mirror transport failed") from exc
        finally:
            connection.close()


class CommunicationShadowMirror:
    def __init__(
        self,
        transport: ShadowMirrorTransport,
        *,
        source_instance_id: str,
    ) -> None:
        if not source_instance_id or len(source_instance_id) > 160:
            raise ValueError("shadow mirror instance identity is invalid")
        self._transport = transport
        self._source_instance_id = source_instance_id

    def build_candidate_batch(
        self,
        envelope: InboundEnvelope,
        ack_permit: ChannelAckPermit,
        *,
        classification: str,
        should_forward: bool,
        source_decision_sha256: str,
        observed_at_ms: int,
        legacy_observation: ShadowDecisionObservation | None = None,
    ) -> ShadowObservationBatch:
        if (
            not ack_permit.has_valid_sha256()
            or ack_permit.ingress_id != envelope.inbound_id
            or ack_permit.idempotency_key != envelope.idempotency_key
            or ack_permit.channel != envelope.channel
            or ack_permit.tenant_id != envelope.tenant_id
            or ack_permit.link_account_id != envelope.link_account_id
            or ack_permit.channel_message_ref != envelope.channel_message_ref
            or observed_at_ms < ack_permit.issued_at_ms
        ):
            raise ValueError("shadow mirror ACK permit is not bound to the envelope")
        copy = build_shadow_ingress_copy(
            envelope,
            source_ingress_sha256=ack_permit.inbox_record_sha256,
            source_ack_permit_sha256=ack_permit.permit_sha256,
            copied_at_ms=observed_at_ms,
        )
        candidate = build_shadow_decision_observation(
            copy,
            side="candidate",
            source_component_id="tiangong-communication-service",
            source_instance_id=self._source_instance_id,
            source_decision_sha256=source_decision_sha256,
            classification=classification,
            should_forward=should_forward,
            observed_at_ms=observed_at_ms,
        )
        observations = [candidate]
        if legacy_observation is not None:
            if (
                legacy_observation.side != "legacy"
                or legacy_observation.shadow_id != copy.shadow_id
                or legacy_observation.envelope_sha256 != copy.envelope_sha256
                or not legacy_observation.has_valid_sha256()
            ):
                raise ValueError("legacy shadow observation is not bound to the copied ingress")
            observations.append(legacy_observation)
        return ShadowObservationBatch(
            ingress_copy=copy,
            observations=tuple(sorted(observations, key=lambda item: item.side)),
            batch_sha256="0" * 64,
        ).with_computed_sha256()

    def mirror_candidate(
        self,
        envelope: InboundEnvelope,
        ack_permit: ChannelAckPermit,
        *,
        classification: str,
        should_forward: bool,
        source_decision_sha256: str,
        observed_at_ms: int,
        legacy_observation: ShadowDecisionObservation | None = None,
    ) -> ShadowComparison:
        batch = self.build_candidate_batch(
            envelope,
            ack_permit,
            classification=classification,
            should_forward=should_forward,
            source_decision_sha256=source_decision_sha256,
            observed_at_ms=observed_at_ms,
            legacy_observation=legacy_observation,
        )
        return self._transport.submit(batch)

    def mirror_wechat_outcome(
        self,
        outcome: WechatInboundOutcome,
        *,
        observed_at_ms: int,
        legacy_observation: ShadowDecisionObservation | None = None,
    ) -> ShadowComparison:
        if not outcome.decision.has_valid_sha256():
            raise ValueError("WeChat shadow decision digest is invalid")
        return self.mirror_candidate(
            outcome.envelope,
            outcome.ack_permit,
            classification=outcome.decision.classification,
            should_forward=outcome.should_forward,
            source_decision_sha256=outcome.decision.decision_sha256,
            observed_at_ms=observed_at_ms,
            legacy_observation=legacy_observation,
        )

    def mirror_feishu_outcome(
        self,
        outcome: FeishuInboundOutcome,
        *,
        observed_at_ms: int,
        legacy_observation: ShadowDecisionObservation | None = None,
    ) -> ShadowComparison:
        source_decision_sha256 = canonical_sha256(
            {
                "domain": "tiangong.communication.feishu-shadow-decision.v1",
                "envelope_sha256": canonical_sha256(
                    outcome.envelope.model_dump(mode="json")
                ),
                "classification": outcome.classification,
                "should_forward": outcome.should_forward,
                "route_key": outcome.route_key,
                "resource_ids": outcome.resource_ids,
            }
        )
        return self.mirror_candidate(
            outcome.envelope,
            outcome.ack_permit,
            classification=outcome.classification,
            should_forward=outcome.should_forward,
            source_decision_sha256=source_decision_sha256,
            observed_at_ms=observed_at_ms,
            legacy_observation=legacy_observation,
        )


__all__ = [
    "CommunicationShadowMirror",
    "LoopbackShadowMirrorTransport",
    "MAX_SHADOW_RESPONSE_BYTES",
    "SHADOW_OBSERVE_PATH",
    "ShadowMirrorError",
    "ShadowMirrorTransport",
]
