"""Verified 7176 delivery entrypoint; channel handlers never receive raw authority."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

from contracts import (
    ComponentManifest,
    DeliveryReceipt,
    DeliveryTicket,
    DeliveryTicketPayload,
    OutboundPlan,
    TrustBundle,
    authorize_delivery_contract,
    canonical_sha256,
    correlate_delivery_receipt,
)
from runtime_security import b64url_decode, verify_delivery_ticket

from .delivery_ledger import (
    DeliveryLedger,
    VerifiedDeliveryTicketFact,
)
from .channel_authority import ChannelAuthorityGate


class DeliveryDispatchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DeliveryChannelHandler(Protocol):
    """A control-plane-bound transport handler with no authority parameters."""

    def send(
        self,
        payload: DeliveryTicketPayload,
        plan: OutboundPlan,
    ) -> DeliveryReceipt: ...


class VerifiedDeliveryDispatcher:
    """Verify, authorize and durably consume a DeliveryTicket before dispatch."""

    _CHANNELS = frozenset({"wechat", "feishu"})

    def __init__(
        self,
        ledger: DeliveryLedger,
        trust_bundle: TrustBundle,
        component_manifest: ComponentManifest,
        handlers: Mapping[str, DeliveryChannelHandler],
        *,
        clock_ms: Callable[[], int],
        generation_floor: Callable[[str, str], int],
        channel_authority: ChannelAuthorityGate,
    ) -> None:
        if not trust_bundle.has_valid_sha256() or not trust_bundle.production_ready:
            raise ValueError("delivery dispatcher trust bundle is not ready")
        if not component_manifest.has_valid_manifest_sha256():
            raise ValueError("delivery dispatcher component manifest is invalid")
        if set(handlers) != self._CHANNELS:
            raise ValueError("delivery dispatcher requires exactly wechat and feishu handlers")
        if any(not callable(getattr(handler, "send", None)) for handler in handlers.values()):
            raise TypeError("delivery dispatcher handler does not implement send")
        self._ledger = ledger
        self._trust_bundle = trust_bundle
        self._component_manifest = component_manifest
        self._handlers = MappingProxyType(dict(handlers))
        self._clock_ms = clock_ms
        self._generation_floor = generation_floor
        if not isinstance(channel_authority, ChannelAuthorityGate):
            raise TypeError("delivery dispatcher channel authority is required")
        self._channel_authority = channel_authority

    def dispatch(
        self,
        ticket: DeliveryTicket,
        plan: OutboundPlan,
    ) -> DeliveryReceipt:
        """The only public dispatch form: a complete signed ticket plus its plan."""

        if not isinstance(ticket, DeliveryTicket) or not isinstance(plan, OutboundPlan):
            raise TypeError("delivery dispatch requires DeliveryTicket and OutboundPlan")
        payload = ticket.payload
        if payload.channel not in self._CHANNELS:
            raise DeliveryDispatchError("delivery.channel.not_dispatchable")
        now_ms = self._clock_ms()
        trusted_key = verify_delivery_ticket(
            ticket,
            self._trust_bundle,
            now_ms=now_ms,
        )
        minimum_generation = self._generation_floor(
            payload.request_id,
            payload.run_id,
        )
        if not isinstance(minimum_generation, int) or isinstance(
            minimum_generation, bool
        ) or minimum_generation < 0:
            raise DeliveryDispatchError("delivery.generation_floor.invalid")
        authorized_plan = authorize_delivery_contract(
            ticket,
            plan,
            self._component_manifest,
            signature_verified=True,
            now_ms=now_ms,
            expected_gateway_epoch=self._trust_bundle.gateway_epoch,
            minimum_generation=minimum_generation,
        )
        with self._channel_authority.operation(
            channel=payload.channel,
            tenant_id=payload.tenant_id,
            link_account_id=payload.link_account_id,
            operation="SEND",
            now_ms=now_ms,
        ):
            verification = VerifiedDeliveryTicketFact(
                ticket_id=payload.ticket_id,
                kid=trusted_key.kid,
                issuer=payload.issuer,
                audience=payload.audience,
                gateway_epoch=payload.gateway_epoch,
                request_id=payload.request_id,
                run_id=payload.run_id,
                generation=payload.generation,
                delivery_id=payload.delivery_id,
                effect_id=payload.effect_id,
                outbound_plan_sha256=payload.outbound_plan_sha256,
                payload_sha256=canonical_sha256(payload.model_dump(mode="json")),
                signature_sha256=hashlib.sha256(
                    b64url_decode(ticket.signature)
                ).hexdigest(),
                trust_bundle_sha256=self._trust_bundle.bundle_sha256,
                component_manifest_sha256=self._component_manifest.manifest_sha256,
                verified_at_ms=now_ms,
                expires_at_ms=payload.expires_at_ms,
                verification_sha256="0" * 64,
            ).with_computed_sha256()
            claim = self._ledger.claim_from_payload(payload, claimed_at_ms=now_ms)
            consumed = self._ledger.consume_verified_ticket(verification, claim)
            if consumed.delivery.receipt is not None:
                return correlate_delivery_receipt(consumed.delivery.receipt, ticket)
            receipt = self._handlers[payload.channel].send(payload, authorized_plan)
            return correlate_delivery_receipt(receipt, ticket)


__all__ = [
    "DeliveryChannelHandler",
    "DeliveryDispatchError",
    "VerifiedDeliveryDispatcher",
]
