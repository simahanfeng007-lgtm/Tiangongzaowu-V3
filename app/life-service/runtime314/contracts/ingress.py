"""Production channel-ingress contracts bound to durable Inbox and cutover authority."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .identities import derive_request_identity
from .models import ContractModel, InboundEnvelope, OpaqueId, SCHEMA_BASE, LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Sha256


class ChannelAckPermit(ContractModel):
    """Machine proof that 7176 durably committed an inbound event before ACK."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ChannelAckPermit",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    permit_id: OpaqueId
    ingress_id: OpaqueId
    idempotency_key: Sha256
    channel: Literal["desktop", "wechat", "feishu", "system", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    channel_message_ref: OpaqueId
    cursor_stream_key: Sha256
    cursor_revision: int = Field(ge=1)
    next_cursor_sha256: Sha256
    inbox_record_sha256: Sha256
    persisted_at_ms: int = Field(ge=0)
    issued_at_ms: int = Field(ge=0)
    permit_sha256: Sha256

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.issued_at_ms < self.persisted_at_ms:
            raise ValueError("ACK permit predates durable inbox persistence")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"permit_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.permit_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"permit_sha256": self.computed_sha256()})


def derive_production_submission_id(
    envelope: InboundEnvelope,
    *,
    source_instance_id: str,
    gateway_epoch: int,
    channel_ownership_lease_sha256: str,
    inbox_record_sha256: str,
    ack_permit_sha256: str,
) -> str:
    return "pin_" + canonical_sha256(
        {
            "domain": "tiangong.gateway.production-channel-ingress.v1",
            "source_instance_id": source_instance_id,
            "gateway_epoch": gateway_epoch,
            "channel_ownership_lease_sha256": channel_ownership_lease_sha256,
            "inbound_id": envelope.inbound_id,
            "idempotency_key": envelope.idempotency_key,
            "inbox_record_sha256": inbox_record_sha256,
            "ack_permit_sha256": ack_permit_sha256,
        }
    )


class ProductionInboundSubmission(ContractModel):
    """A real channel request submission; registration is allowed, effects are not."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ProductionInboundSubmission",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    ingress_schema: Literal["tiangong.gateway.production-channel-ingress.v1"] = (
        "tiangong.gateway.production-channel-ingress.v1"
    )
    submission_id: str = Field(pattern=r"^pin_[0-9a-f]{64}$")
    source_component_id: Literal["tiangong-communication-service"] = (
        "tiangong-communication-service"
    )
    source_instance_id: OpaqueId
    gateway_epoch: int = Field(ge=1)
    channel_ownership_lease_sha256: Sha256
    inbox_record_sha256: Sha256
    ack_permit: ChannelAckPermit
    envelope: InboundEnvelope
    envelope_sha256: Sha256
    submitted_at_ms: int = Field(ge=0)
    request_creation_permitted: Literal[True] = True
    effects_permitted: Literal[False] = False
    model_generated: Literal[False] = False
    submission_sha256: Sha256

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        permit = self.ack_permit
        envelope = self.envelope
        if envelope.channel not in {"wechat", "feishu"}:
            raise ValueError("production channel ingress only accepts WeChat or Feishu")
        if (
            not permit.has_valid_sha256()
            or permit.ingress_id != envelope.inbound_id
            or permit.idempotency_key != envelope.idempotency_key
            or permit.channel != envelope.channel
            or permit.tenant_id != envelope.tenant_id
            or permit.link_account_id != envelope.link_account_id
            or permit.channel_message_ref != envelope.channel_message_ref
            or permit.inbox_record_sha256 != self.inbox_record_sha256
        ):
            raise ValueError("production ingress ACK permit is not bound to the envelope")
        if self.envelope_sha256 != canonical_sha256(envelope.model_dump(mode="json")):
            raise ValueError("production ingress envelope digest is invalid")
        if self.submitted_at_ms < max(envelope.received_at_ms, permit.issued_at_ms):
            raise ValueError("production ingress submission predates durable persistence")
        expected = derive_production_submission_id(
            envelope,
            source_instance_id=self.source_instance_id,
            gateway_epoch=self.gateway_epoch,
            channel_ownership_lease_sha256=self.channel_ownership_lease_sha256,
            inbox_record_sha256=self.inbox_record_sha256,
            ack_permit_sha256=permit.permit_sha256,
        )
        if self.submission_id != expected:
            raise ValueError("production ingress submission identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"submission_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.submission_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"submission_sha256": self.computed_sha256()})


class ProductionInboundAcceptance(ContractModel):
    """7184 acknowledgement of request registration, never an execution result."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ProductionInboundAcceptance",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    acceptance_schema: Literal["tiangong.gateway.production-channel-acceptance.v1"] = (
        "tiangong.gateway.production-channel-acceptance.v1"
    )
    submission_id: str = Field(pattern=r"^pin_[0-9a-f]{64}$")
    submission_sha256: Sha256
    gateway_epoch: int = Field(ge=1)
    channel_ownership_lease_sha256: Sha256
    idempotency_key: Sha256
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    request_entry_sha256: Sha256
    session_scope_hash: Sha256
    queue_sequence: int = Field(ge=1)
    queue_state: Literal["ACTIVE", "QUEUED", "COMPLETED"]
    request_created_at_ms: int = Field(ge=0)
    accepted_at_ms: int = Field(ge=0)
    request_created: bool
    duplicate: bool
    effects_started: Literal[False] = False
    completion_claimed: Literal[False] = False
    model_generated: Literal[False] = False
    acceptance_sha256: Sha256

    @model_validator(mode="after")
    def validate_acceptance(self) -> Self:
        if self.request_id != derive_request_identity(self.idempotency_key).request_id:
            raise ValueError("production ingress acceptance request identity is invalid")
        if self.request_created == self.duplicate:
            raise ValueError("production ingress acceptance registration flags are invalid")
        if self.accepted_at_ms < self.request_created_at_ms:
            raise ValueError("production ingress acceptance predates request registration")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"acceptance_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.acceptance_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"acceptance_sha256": self.computed_sha256()})


def build_production_inbound_submission(
    envelope: InboundEnvelope,
    ack_permit: ChannelAckPermit,
    *,
    source_instance_id: str,
    gateway_epoch: int,
    channel_ownership_lease_sha256: str,
    submitted_at_ms: int,
) -> ProductionInboundSubmission:
    submission_id = derive_production_submission_id(
        envelope,
        source_instance_id=source_instance_id,
        gateway_epoch=gateway_epoch,
        channel_ownership_lease_sha256=channel_ownership_lease_sha256,
        inbox_record_sha256=ack_permit.inbox_record_sha256,
        ack_permit_sha256=ack_permit.permit_sha256,
    )
    return ProductionInboundSubmission(
        submission_id=submission_id,
        source_instance_id=source_instance_id,
        gateway_epoch=gateway_epoch,
        channel_ownership_lease_sha256=channel_ownership_lease_sha256,
        inbox_record_sha256=ack_permit.inbox_record_sha256,
        ack_permit=ack_permit,
        envelope=envelope,
        envelope_sha256=canonical_sha256(envelope.model_dump(mode="json")),
        submitted_at_ms=submitted_at_ms,
        submission_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "ChannelAckPermit",
    "ProductionInboundAcceptance",
    "ProductionInboundSubmission",
    "build_production_inbound_submission",
    "derive_production_submission_id",
]
