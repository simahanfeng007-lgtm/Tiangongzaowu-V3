"""Pure delivery authorization and receipt-correlation checks."""

from __future__ import annotations

from .delivery import (
    ComponentManifest,
    DeliveryReceipt,
    DeliveryTicket,
    OutboundPlan,
    grant_from_outbound_part,
)


class DeliveryAuthorizationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def authorize_delivery_contract(
    ticket: DeliveryTicket,
    plan: OutboundPlan,
    component_manifest: ComponentManifest,
    *,
    signature_verified: bool,
    now_ms: int,
    expected_gateway_epoch: int,
    minimum_generation: int = 0,
) -> OutboundPlan:
    if not signature_verified:
        raise DeliveryAuthorizationError("ticket.signature.unverified")
    if not component_manifest.has_valid_manifest_sha256():
        raise DeliveryAuthorizationError("component_manifest.digest.invalid")
    if not plan.has_valid_plan_sha256():
        raise DeliveryAuthorizationError("outbound_plan.digest.invalid")

    payload = ticket.payload
    if payload.component_manifest_hash != component_manifest.manifest_sha256:
        raise DeliveryAuthorizationError("ticket.component_manifest.mismatch")
    if payload.gateway_epoch != expected_gateway_epoch:
        raise DeliveryAuthorizationError("ticket.gateway_epoch.mismatch")
    if payload.generation < minimum_generation:
        raise DeliveryAuthorizationError("ticket.generation.fenced")
    if now_ms < payload.not_before_ms:
        raise DeliveryAuthorizationError("ticket.not_yet_valid")
    if now_ms > payload.expires_at_ms:
        raise DeliveryAuthorizationError("ticket.expired")

    exact_fields = (
        (payload.outbound_plan_id, plan.outbound_plan_id, "outbound_plan_id"),
        (payload.outbound_plan_sha256, plan.plan_sha256, "outbound_plan_sha256"),
        (payload.delivery_id, plan.delivery_id, "delivery_id"),
        (payload.effect_id, plan.effect_id, "effect_id"),
        (payload.request_id, plan.request_id, "request_id"),
        (payload.run_id, plan.run_id, "run_id"),
        (payload.generation, plan.generation, "generation"),
        (payload.channel, plan.channel, "channel"),
        (payload.tenant_id, plan.tenant_id, "tenant_id"),
        (payload.link_account_id, plan.link_account_id, "link_account_id"),
        (payload.conversation_ref, plan.conversation_ref, "conversation_ref"),
        (payload.conversation_scope_hash, plan.conversation_scope_hash, "conversation_scope_hash"),
        (payload.recipient_scope_hash, plan.recipient_scope_hash, "recipient_scope_hash"),
        (payload.reply_to_message_ref, plan.reply_to_message_ref, "reply_to_message_ref"),
        (payload.channel_policy_hash, plan.channel_policy_hash, "channel_policy_hash"),
    )
    for ticket_value, plan_value, name in exact_fields:
        if ticket_value != plan_value:
            raise DeliveryAuthorizationError(f"ticket.{name}.mismatch")

    expected_grants = tuple(grant_from_outbound_part(part) for part in plan.parts)
    if payload.parts != expected_grants:
        raise DeliveryAuthorizationError("ticket.parts.mismatch")
    return plan


def correlate_delivery_receipt(
    receipt: DeliveryReceipt,
    ticket: DeliveryTicket,
) -> DeliveryReceipt:
    if not receipt.has_valid_receipt_sha256():
        raise DeliveryAuthorizationError("delivery_receipt.digest.invalid")
    payload = ticket.payload
    exact_fields = (
        (receipt.ticket_id, payload.ticket_id, "ticket_id"),
        (receipt.delivery_id, payload.delivery_id, "delivery_id"),
        (receipt.effect_id, payload.effect_id, "effect_id"),
        (receipt.request_id, payload.request_id, "request_id"),
        (receipt.run_id, payload.run_id, "run_id"),
        (receipt.generation, payload.generation, "generation"),
        (receipt.channel, payload.channel, "channel"),
    )
    for receipt_value, ticket_value, name in exact_fields:
        if receipt_value != ticket_value:
            raise DeliveryAuthorizationError(f"delivery_receipt.{name}.mismatch")
    receipt_parts = tuple((part.part_id, part.index, part.kind) for part in receipt.parts)
    ticket_parts = tuple((part.part_id, part.index, part.kind) for part in payload.parts)
    if receipt_parts != ticket_parts:
        raise DeliveryAuthorizationError("delivery_receipt.parts.mismatch")
    for receipt_part, ticket_part in zip(receipt.parts, payload.parts, strict=True):
        if receipt_part.artifact_id != ticket_part.artifact_id:
            raise DeliveryAuthorizationError("delivery_receipt.artifact_id.mismatch")
        if receipt_part.artifact_revision_id != ticket_part.artifact_revision_id:
            raise DeliveryAuthorizationError("delivery_receipt.artifact_revision_id.mismatch")
    return receipt


__all__ = [
    "DeliveryAuthorizationError",
    "authorize_delivery_contract",
    "correlate_delivery_receipt",
]
