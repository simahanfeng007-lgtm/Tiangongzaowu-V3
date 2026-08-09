"""The one typed physical input contract for World Understanding."""
from __future__ import annotations
from typing import Any, Literal, Self
from pydantic import Field, field_validator, model_validator
from ._base import (
    IngressEnvelopeId, MAX_SAFE_INTEGER, WorldContractModel, WorldRecordRef,
    validate_inline_payload,
)
from .authority import AuthorityDomain
from .observability import ObservabilityState
from .scope import WorldScope
from .source import SourceKind, WorldSourceRef
from .time import WorldTime
from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256

IngressKind = Literal["SOURCE_RECORD", "CONTEXT_REQUEST"]


def derive_ingress_dedup_key(*, envelope_kind: str, source_kind: str, source_native_id: str, payload_sha256: str, world_scope_hash: str) -> str:
    return canonical_sha256({
        "domain": "tiangong.world.ingress-dedup.v1",
        "envelope_kind": envelope_kind,
        "source_kind": source_kind,
        "source_native_id": source_native_id,
        "payload_sha256": payload_sha256,
        "world_scope_hash": world_scope_hash,
    })


def derive_ingress_envelope_id(*, dedup_key: str) -> str:
    return "wing_" + canonical_sha256({"domain": "tiangong.world.ingress-id.v1", "dedup_key": dedup_key})


class WorldIngressEnvelope(WorldContractModel):
    schema_version: Literal["tiangong.world-understanding.contracts.v1"] = "tiangong.world-understanding.contracts.v1"
    envelope_id: IngressEnvelopeId
    envelope_kind: IngressKind
    source_kind: SourceKind
    source_native_id: OpaqueId
    producer_ref: OpaqueId
    payload_inline: dict[str, Any] | None = None
    payload_ref: WorldSourceRef | None = None
    payload_sha256: Sha256
    source_time: WorldTime
    life_id: OpaqueId | None = None
    run_id: OpaqueId | None = None
    request_id: OpaqueId | None = None
    session_id: OpaqueId | None = None
    conversation_id: OpaqueId | None = None
    workspace_id: OpaqueId | None = None
    principal_scope_hash: Sha256 | None = None
    scope_hint: WorldScope
    native_provenance_refs: tuple[WorldSourceRef, ...] = Field(default=(), max_length=4096)
    native_authority_domain: AuthorityDomain | None = None
    observability_hint: ObservabilityState | None = None
    integrity_ref: WorldRecordRef | None = None
    correlation_id: OpaqueId
    dedup_key: Sha256
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    empirical_evidence_weight_milli: Literal[0] = 0

    _validate_inline = field_validator("payload_inline")(validate_inline_payload)

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        if (self.payload_inline is None) == (self.payload_ref is None):
            raise ValueError("ingress payload must be exactly one of inline or ref")
        if self.payload_inline is not None:
            if canonical_sha256(self.payload_inline) != self.payload_sha256:
                raise ValueError("inline payload hash mismatch")
        elif self.payload_ref is not None and self.payload_ref.sha256 != self.payload_sha256:
            raise ValueError("payload_ref hash mismatch")

        if self.envelope_kind == "CONTEXT_REQUEST":
            if self.source_kind != "CONTEXT_REQUEST":
                raise ValueError("CONTEXT_REQUEST envelope requires CONTEXT_REQUEST source_kind")
            if self.native_authority_domain is not None:
                raise ValueError("context request cannot carry native reality authority")
        elif self.source_kind == "CONTEXT_REQUEST":
            raise ValueError("SOURCE_RECORD cannot use CONTEXT_REQUEST source_kind")

        expected_dedup = derive_ingress_dedup_key(
            envelope_kind=self.envelope_kind,
            source_kind=self.source_kind,
            source_native_id=self.source_native_id,
            payload_sha256=self.payload_sha256,
            world_scope_hash=self.scope_hint.world_scope_hash,
        )
        if self.dedup_key != expected_dedup:
            raise ValueError("ingress dedup key mismatch")
        if self.envelope_id != derive_ingress_envelope_id(dedup_key=self.dedup_key):
            raise ValueError("ingress envelope id mismatch")
        return self
