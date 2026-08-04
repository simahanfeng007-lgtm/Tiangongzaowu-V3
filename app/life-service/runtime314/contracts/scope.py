"""Multi-tenant channel scope and deterministic idempotency-key derivation."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from .canonical import canonical_sha256
from .delivery import OutboundPlan
from .models import ContractModel, InboundEnvelope, OpaqueId, SCHEMA_BASE, LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Sha256


class InboundScope(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:InboundScope",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    channel: Literal["desktop", "wechat", "feishu", "system", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_ref: OpaqueId
    channel_message_ref: OpaqueId
    sender_ref: OpaqueId


class InboundScopeKeys(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:InboundScopeKeys",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    algorithm: Literal["sha256-jcs-domain-v1"] = "sha256-jcs-domain-v1"
    conversation_scope_hash: Sha256
    principal_scope_hash: Sha256
    message_scope_hash: Sha256
    idempotency_key: Sha256


class OutboundScope(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:OutboundScope",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    channel: Literal["desktop", "wechat", "feishu", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_ref: OpaqueId
    recipient_ref: OpaqueId
    reply_to_message_ref: OpaqueId | None = None


class OutboundScopeKeys(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:OutboundScopeKeys",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    algorithm: Literal["sha256-jcs-domain-v1"] = "sha256-jcs-domain-v1"
    conversation_scope_hash: Sha256
    recipient_scope_hash: Sha256


class ScopeBindingError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _conversation_hash(*, channel: str, tenant_id: str, link_account_id: str, conversation_ref: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.scope.conversation.v1",
            "channel": channel,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_ref": conversation_ref,
        }
    )


def derive_inbound_scope_keys(scope: InboundScope) -> InboundScopeKeys:
    conversation_hash = _conversation_hash(
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
    )
    principal_hash = canonical_sha256(
        {
            "domain": "tiangong.scope.inbound-principal.v1",
            "conversation_scope_hash": conversation_hash,
            "sender_ref": scope.sender_ref,
        }
    )
    message_hash = canonical_sha256(
        {
            "domain": "tiangong.scope.inbound-message.v1",
            "channel": scope.channel,
            "tenant_id": scope.tenant_id,
            "link_account_id": scope.link_account_id,
            "conversation_ref": scope.conversation_ref,
            "channel_message_ref": scope.channel_message_ref,
        }
    )
    idempotency_key = canonical_sha256(
        {
            "domain": "tiangong.idempotency.inbound.v1",
            "message_scope_hash": message_hash,
        }
    )
    return InboundScopeKeys(
        conversation_scope_hash=conversation_hash,
        principal_scope_hash=principal_hash,
        message_scope_hash=message_hash,
        idempotency_key=idempotency_key,
    )


def derive_outbound_scope_keys(scope: OutboundScope) -> OutboundScopeKeys:
    conversation_hash = _conversation_hash(
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
    )
    recipient_hash = canonical_sha256(
        {
            "domain": "tiangong.scope.outbound-recipient.v1",
            "conversation_scope_hash": conversation_hash,
            "recipient_ref": scope.recipient_ref,
        }
    )
    return OutboundScopeKeys(
        conversation_scope_hash=conversation_hash,
        recipient_scope_hash=recipient_hash,
    )


def bind_inbound_scope(envelope: InboundEnvelope, scope: InboundScope) -> InboundScopeKeys:
    exact_fields = (
        (envelope.channel, scope.channel, "channel"),
        (envelope.tenant_id, scope.tenant_id, "tenant_id"),
        (envelope.link_account_id, scope.link_account_id, "link_account_id"),
        (envelope.conversation_ref, scope.conversation_ref, "conversation_ref"),
        (envelope.channel_message_ref, scope.channel_message_ref, "channel_message_ref"),
        (envelope.sender_ref, scope.sender_ref, "sender_ref"),
    )
    for envelope_value, scope_value, name in exact_fields:
        if envelope_value != scope_value:
            raise ScopeBindingError(f"inbound_scope.{name}.mismatch")
    keys = derive_inbound_scope_keys(scope)
    if envelope.conversation_scope_hash != keys.conversation_scope_hash:
        raise ScopeBindingError("inbound_scope.conversation_hash.mismatch")
    if envelope.principal_scope_hash != keys.principal_scope_hash:
        raise ScopeBindingError("inbound_scope.principal_hash.mismatch")
    if envelope.message_scope_hash != keys.message_scope_hash:
        raise ScopeBindingError("inbound_scope.message_hash.mismatch")
    if envelope.idempotency_key != keys.idempotency_key:
        raise ScopeBindingError("inbound_scope.idempotency_key.mismatch")
    for attachment in envelope.attachments:
        if attachment.source_message_ref not in {None, scope.channel_message_ref}:
            raise ScopeBindingError("inbound_scope.attachment_message.mismatch")
    return keys


def bind_outbound_scope(plan: OutboundPlan, scope: OutboundScope) -> OutboundScopeKeys:
    exact_fields = (
        (plan.channel, scope.channel, "channel"),
        (plan.tenant_id, scope.tenant_id, "tenant_id"),
        (plan.link_account_id, scope.link_account_id, "link_account_id"),
        (plan.conversation_ref, scope.conversation_ref, "conversation_ref"),
        (plan.reply_to_message_ref, scope.reply_to_message_ref, "reply_to_message_ref"),
    )
    for plan_value, scope_value, name in exact_fields:
        if plan_value != scope_value:
            raise ScopeBindingError(f"outbound_scope.{name}.mismatch")
    keys = derive_outbound_scope_keys(scope)
    if plan.conversation_scope_hash != keys.conversation_scope_hash:
        raise ScopeBindingError("outbound_scope.conversation_hash.mismatch")
    if plan.recipient_scope_hash != keys.recipient_scope_hash:
        raise ScopeBindingError("outbound_scope.recipient_hash.mismatch")
    return keys


__all__ = [
    "InboundScope",
    "InboundScopeKeys",
    "OutboundScope",
    "OutboundScopeKeys",
    "ScopeBindingError",
    "bind_inbound_scope",
    "bind_outbound_scope",
    "derive_inbound_scope_keys",
    "derive_outbound_scope_keys",
]
