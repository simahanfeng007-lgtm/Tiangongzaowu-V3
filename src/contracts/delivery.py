"""Artifact, outbound delivery, receipt, and component contracts."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .execution import Base64UrlEd25519Signature
from .models import (
    ArtifactId,
    ArtifactRevisionId,
    ContractModel,
    DeliveryId,
    EffectId,
    MimeType,
    OpaqueId,
    ReasonCode,
    RequestId,
    RunId,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
    validate_safe_filename,
)


SafeRelativePath = Annotated[str, StringConstraints(min_length=1, max_length=512)]


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class QcEvidence(ContractModel):
    check_id: OpaqueId
    check_version: OpaqueId
    status: Literal["PASSED", "FAILED"]
    checked_at_ms: int = Field(ge=0)
    evidence_sha256: Sha256
    tool_fact_id: OpaqueId


class ArtifactManifest(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ArtifactManifest",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    artifact_id: ArtifactId
    artifact_revision_id: ArtifactRevisionId
    revision: int = Field(ge=1)
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    source_effect_id: EffectId
    producer_fact_id: OpaqueId
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_scope_hash: Sha256
    workspace_id: OpaqueId
    content_object_id: OpaqueId
    sha256: Sha256
    size_bytes: int = Field(ge=1, le=2_147_483_648)
    mime: MimeType
    filename: str = Field(min_length=1, max_length=255)
    artifact_kind: Literal["document", "image", "audio", "video", "archive", "data", "other"]
    format_id: OpaqueId
    created_at_ms: int = Field(ge=0)
    qc_state: Literal["PENDING", "PASSED", "FAILED"]
    qc_evidence: tuple[QcEvidence, ...] = Field(default=(), max_length=64)
    manifest_sha256: Sha256

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return validate_safe_filename(value)

    @model_validator(mode="after")
    def validate_qc(self) -> Self:
        keys = tuple((item.check_id, item.check_version) for item in self.qc_evidence)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("QC evidence must be sorted and unique")
        states = {item.status for item in self.qc_evidence}
        if self.qc_state == "PENDING" and self.qc_evidence:
            raise ValueError("pending artifact cannot claim completed QC evidence")
        if self.qc_state == "PASSED" and (not self.qc_evidence or states != {"PASSED"}):
            raise ValueError("passed artifact requires at least one exclusively passed QC check")
        if self.qc_state == "FAILED" and "FAILED" not in states:
            raise ValueError("failed artifact requires failed QC evidence")
        return self

    def computed_manifest_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"manifest_sha256"}))

    def has_valid_manifest_sha256(self) -> bool:
        return self.manifest_sha256 == self.computed_manifest_sha256()

    def with_computed_manifest_sha256(self) -> Self:
        return self.model_copy(update={"manifest_sha256": self.computed_manifest_sha256()})


class OutboundPart(ContractModel):
    part_id: OpaqueId
    index: int = Field(ge=0, le=999)
    kind: Literal["text", "artifact"]
    text: str | None = Field(default=None, min_length=1, max_length=100_000)
    text_sha256: Sha256 | None = None
    artifact: ArtifactManifest | None = None

    @model_validator(mode="after")
    def validate_part(self) -> Self:
        if self.kind == "text":
            if self.text is None or self.text_sha256 is None or self.artifact is not None:
                raise ValueError("text part must contain only text and text_sha256")
            if "\x00" in self.text or self.text_sha256 != text_sha256(self.text):
                raise ValueError("text part hash is invalid")
        else:
            if self.artifact is None or self.text is not None or self.text_sha256 is not None:
                raise ValueError("artifact part must contain only an ArtifactManifest")
            if self.artifact.qc_state != "PASSED" or not self.artifact.has_valid_manifest_sha256():
                raise ValueError("outbound artifact must have valid passed QC manifest")
        return self


class OutboundPlan(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:OutboundPlan",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    outbound_plan_id: OpaqueId
    delivery_id: DeliveryId
    effect_id: EffectId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    channel: Literal["desktop", "wechat", "feishu", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_ref: OpaqueId
    conversation_scope_hash: Sha256
    recipient_scope_hash: Sha256
    reply_to_message_ref: OpaqueId | None = None
    channel_policy_hash: Sha256
    created_at_ms: int = Field(ge=0)
    parts: tuple[OutboundPart, ...] = Field(min_length=1, max_length=50)
    plan_sha256: Sha256

    @model_validator(mode="after")
    def validate_parts(self) -> Self:
        if tuple(part.index for part in self.parts) != tuple(range(len(self.parts))):
            raise ValueError("outbound part indexes must be contiguous and ordered")
        part_ids = tuple(part.part_id for part in self.parts)
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("outbound part ids must be unique")
        artifact_keys: set[tuple[str, int]] = set()
        for part in self.parts:
            if part.artifact is None:
                continue
            artifact = part.artifact
            if artifact.request_id != self.request_id or artifact.run_id != self.run_id:
                raise ValueError("artifact run does not match outbound plan")
            if artifact.generation != self.generation:
                raise ValueError("artifact generation does not match outbound plan")
            if artifact.tenant_id != self.tenant_id or artifact.link_account_id != self.link_account_id:
                raise ValueError("artifact tenant/account does not match outbound plan")
            if artifact.conversation_scope_hash != self.conversation_scope_hash:
                raise ValueError("artifact conversation scope does not match outbound plan")
            key = (artifact.artifact_id, artifact.revision)
            if key in artifact_keys:
                raise ValueError("duplicate artifact revision in outbound plan")
            artifact_keys.add(key)
        return self

    def computed_plan_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"plan_sha256"}))

    def has_valid_plan_sha256(self) -> bool:
        return self.plan_sha256 == self.computed_plan_sha256()

    def with_computed_plan_sha256(self) -> Self:
        return self.model_copy(update={"plan_sha256": self.computed_plan_sha256()})


class DeliveryPartGrant(ContractModel):
    part_id: OpaqueId
    index: int = Field(ge=0, le=999)
    kind: Literal["text", "artifact"]
    text_sha256: Sha256 | None = None
    text_bytes: int | None = Field(default=None, ge=1, le=1_000_000)
    artifact_id: ArtifactId | None = None
    artifact_revision_id: ArtifactRevisionId | None = None
    artifact_revision: int | None = Field(default=None, ge=1)
    artifact_manifest_sha256: Sha256 | None = None
    content_object_id: OpaqueId | None = None
    content_sha256: Sha256 | None = None
    size_bytes: int | None = Field(default=None, ge=1, le=2_147_483_648)
    mime: MimeType | None = None
    filename: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        return None if value is None else validate_safe_filename(value)

    @model_validator(mode="after")
    def validate_grant(self) -> Self:
        artifact_fields = (
            self.artifact_id,
            self.artifact_revision_id,
            self.artifact_revision,
            self.artifact_manifest_sha256,
            self.content_object_id,
            self.content_sha256,
            self.size_bytes,
            self.mime,
            self.filename,
        )
        if self.kind == "text":
            if self.text_sha256 is None or self.text_bytes is None or any(
                value is not None for value in artifact_fields
            ):
                raise ValueError("text grant must bind only text hash and byte length")
        else:
            if self.text_sha256 is not None or self.text_bytes is not None or any(
                value is None for value in artifact_fields
            ):
                raise ValueError("artifact grant must bind the complete immutable artifact identity")
        return self


def grant_from_outbound_part(part: OutboundPart) -> DeliveryPartGrant:
    if part.kind == "text":
        assert part.text is not None and part.text_sha256 is not None
        return DeliveryPartGrant(
            part_id=part.part_id,
            index=part.index,
            kind="text",
            text_sha256=part.text_sha256,
            text_bytes=len(part.text.encode("utf-8")),
        )
    assert part.artifact is not None
    artifact = part.artifact
    return DeliveryPartGrant(
        part_id=part.part_id,
        index=part.index,
        kind="artifact",
        artifact_id=artifact.artifact_id,
        artifact_revision_id=artifact.artifact_revision_id,
        artifact_revision=artifact.revision,
        artifact_manifest_sha256=artifact.manifest_sha256,
        content_object_id=artifact.content_object_id,
        content_sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        mime=artifact.mime,
        filename=artifact.filename,
    )


class DeliveryTicketHeader(ContractModel):
    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    alg: Literal["EdDSA"] = "EdDSA"
    typ: Literal["tiangong.delivery-ticket+jws"] = "tiangong.delivery-ticket+jws"
    kid: OpaqueId


class DeliveryTicketPayload(ContractModel):
    ticket_type: Literal["DeliveryTicket"] = "DeliveryTicket"
    ticket_id: OpaqueId
    issuer: Literal["tiangong-total-gateway"] = "tiangong-total-gateway"
    audience: Literal["tiangong-communication-service"] = "tiangong-communication-service"
    issued_at_ms: int = Field(ge=0)
    not_before_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    gateway_epoch: int = Field(ge=1)
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    delivery_id: DeliveryId
    effect_id: EffectId
    channel: Literal["desktop", "wechat", "feishu", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_ref: OpaqueId
    conversation_scope_hash: Sha256
    recipient_scope_hash: Sha256
    reply_to_message_ref: OpaqueId | None = None
    outbound_plan_id: OpaqueId
    outbound_plan_sha256: Sha256
    channel_policy_hash: Sha256
    component_manifest_hash: Sha256
    allow_text: bool
    allow_files: bool
    max_text_parts: int = Field(ge=0, le=50)
    max_file_parts: int = Field(ge=0, le=50)
    upload_timeout_ms: int = Field(ge=1_000, le=3_600_000)
    send_timeout_ms: int = Field(ge=1_000, le=3_600_000)
    parts: tuple[DeliveryPartGrant, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_delivery_scope(self) -> Self:
        if not self.issued_at_ms <= self.not_before_ms <= self.expires_at_ms:
            raise ValueError("delivery ticket time window is invalid")
        if self.expires_at_ms - self.issued_at_ms > 60_000:
            raise ValueError("DeliveryTicket issue-to-expiry window exceeds 60 seconds")
        if tuple(part.index for part in self.parts) != tuple(range(len(self.parts))):
            raise ValueError("delivery part grants must be contiguous and ordered")
        part_ids = tuple(part.part_id for part in self.parts)
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("delivery part grant ids must be unique")
        text_count = sum(part.kind == "text" for part in self.parts)
        file_count = sum(part.kind == "artifact" for part in self.parts)
        if self.allow_text != bool(text_count) or self.allow_files != bool(file_count):
            raise ValueError("allow_text/allow_files must match actual granted parts")
        if text_count > self.max_text_parts or file_count > self.max_file_parts:
            raise ValueError("delivery part count exceeds ticket limit")
        return self


class DeliveryTicket(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:DeliveryTicket",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    header: DeliveryTicketHeader
    payload: DeliveryTicketPayload
    signature: Base64UrlEd25519Signature


class DeliveryPartReceipt(ContractModel):
    part_id: OpaqueId
    index: int = Field(ge=0, le=999)
    kind: Literal["text", "artifact"]
    artifact_id: ArtifactId | None = None
    artifact_revision_id: ArtifactRevisionId | None = None
    stage: Literal[
        "PLANNED",
        "FETCHED",
        "UPLOADED",
        "CHANNEL_ACCEPTED",
        "DELIVERED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "AMBIGUOUS",
    ]
    attempt: int = Field(ge=1)
    started_at_ms: int = Field(ge=0)
    finished_at_ms: int = Field(ge=0)
    channel_message_ref: OpaqueId | None = None
    evidence_sha256: Sha256
    platform_receipt_sha256: Sha256 | None = None
    error_code: ReasonCode | None = None

    @model_validator(mode="after")
    def validate_part_receipt(self) -> Self:
        if self.finished_at_ms < self.started_at_ms:
            raise ValueError("part receipt finished before it started")
        if self.kind == "text" and (
            self.artifact_id is not None or self.artifact_revision_id is not None
        ):
            raise ValueError("text part receipt cannot bind an artifact")
        if self.kind == "artifact" and (
            self.artifact_id is None or self.artifact_revision_id is None
        ):
            raise ValueError("artifact part receipt must bind an artifact revision")
        if self.stage in {"CHANNEL_ACCEPTED", "DELIVERED"}:
            if self.channel_message_ref is None or self.platform_receipt_sha256 is None:
                raise ValueError("accepted/delivered part requires platform evidence")
            if self.error_code is not None:
                raise ValueError("accepted/delivered part cannot carry an error")
        elif self.stage in {"FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS"}:
            if self.error_code is None:
                raise ValueError("failed or ambiguous part requires an error code")
        elif self.error_code is not None:
            raise ValueError("non-failure part cannot carry an error code")
        return self


class DeliveryReceipt(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:DeliveryReceipt",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    receipt_id: OpaqueId
    ticket_id: OpaqueId
    delivery_id: DeliveryId
    effect_id: EffectId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    channel: Literal["desktop", "wechat", "feishu", "test"]
    status: Literal[
        "CHANNEL_ACCEPTED",
        "DELIVERED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "AMBIGUOUS",
        "RECONCILE_REQUIRED",
    ]
    parts: tuple[DeliveryPartReceipt, ...] = Field(min_length=1, max_length=50)
    observed_at_ms: int = Field(ge=0)
    error_code: ReasonCode | None = None
    model_generated: Literal[False] = False
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_aggregate_state(self) -> Self:
        if tuple(part.index for part in self.parts) != tuple(range(len(self.parts))):
            raise ValueError("receipt part indexes must be contiguous and ordered")
        ids = tuple(part.part_id for part in self.parts)
        if len(set(ids)) != len(ids):
            raise ValueError("receipt part ids must be unique")
        stages = {part.stage for part in self.parts}
        if self.status == "DELIVERED" and stages != {"DELIVERED"}:
            raise ValueError("DELIVERED requires every part to have delivery evidence")
        if self.status == "CHANNEL_ACCEPTED" and not stages.issubset({"CHANNEL_ACCEPTED", "DELIVERED"}):
            raise ValueError("CHANNEL_ACCEPTED cannot hide an incomplete or failed part")
        if self.status in {"AMBIGUOUS", "RECONCILE_REQUIRED"} and "AMBIGUOUS" not in stages:
            raise ValueError("ambiguous/reconcile status requires an ambiguous part")
        if "AMBIGUOUS" in stages and self.status not in {"AMBIGUOUS", "RECONCILE_REQUIRED"}:
            raise ValueError("ambiguous part requires ambiguous/reconcile aggregate status")
        if self.status == "FAILED_RETRYABLE" and "FAILED_RETRYABLE" not in stages:
            raise ValueError("FAILED_RETRYABLE requires a retryable failed part")
        if self.status == "FAILED_FINAL" and "FAILED_FINAL" not in stages:
            raise ValueError("FAILED_FINAL requires a final failed part")
        if self.status in {"AMBIGUOUS", "RECONCILE_REQUIRED", "FAILED_FINAL", "FAILED_RETRYABLE"}:
            if self.error_code is None:
                raise ValueError("non-success aggregate status requires an error code")
        elif self.error_code is not None:
            raise ValueError("accepted/delivered aggregate status cannot carry an error")
        return self

    def computed_receipt_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"receipt_sha256"}))

    def has_valid_receipt_sha256(self) -> bool:
        return self.receipt_sha256 == self.computed_receipt_sha256()

    def with_computed_receipt_sha256(self) -> Self:
        return self.model_copy(update={"receipt_sha256": self.computed_receipt_sha256()})


class ComponentDescriptor(ContractModel):
    component_id: OpaqueId
    version: OpaqueId
    build_id: OpaqueId
    role: Literal["desktop", "orchestrator", "execution", "life", "communication"]
    executable_relative_path: SafeRelativePath
    sha256: Sha256
    size_bytes: int = Field(ge=1)
    ports: tuple[int, ...] = Field(default=(), max_length=16)
    api_contract_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=32)
    schema_bundle_hash: Sha256

    @field_validator("executable_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value or value.startswith("/") or value.endswith("/") or ":" in value:
            raise ValueError("component path must be a portable relative path")
        segments = value.split("/")
        if any(part in {"", ".", ".."} for part in segments):
            raise ValueError("component path contains an unsafe segment")
        return value

    @field_validator("ports", "api_contract_ids")
    @classmethod
    def validate_sorted_unique_values(cls, value: tuple) -> tuple:
        if tuple(sorted(set(value))) != value:
            raise ValueError("component set-like fields must be sorted and unique")
        return value

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(port < 1 or port > 65_535 for port in value):
            raise ValueError("component port is outside the TCP range")
        return value


class ComponentManifest(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ComponentManifest",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    manifest_id: OpaqueId
    product_version: OpaqueId
    generated_at_ms: int = Field(ge=0)
    contract_schema_bundle_hash: Sha256
    capability_manifest_hash: Sha256
    skill_index_hash: Sha256
    release_policy_hash: Sha256
    components: tuple[ComponentDescriptor, ...] = Field(min_length=1, max_length=64)
    production_claim: bool = False
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        ids = tuple(component.component_id for component in self.components)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("components must be sorted and unique")
        if self.production_claim:
            required = {
                "tiangong-backend",
                "tiangong-communication-service",
                "tiangong-desktop",
                "tiangong-life-service",
                "tiangong-total-gateway",
            }
            if not required.issubset(ids):
                raise ValueError("production manifest is missing a required component")
            if any("dev" in component.version.lower() for component in self.components):
                raise ValueError("production manifest cannot contain a development component version")
        return self

    def computed_manifest_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"manifest_sha256"}))

    def has_valid_manifest_sha256(self) -> bool:
        return self.manifest_sha256 == self.computed_manifest_sha256()

    def with_computed_manifest_sha256(self) -> Self:
        return self.model_copy(update={"manifest_sha256": self.computed_manifest_sha256()})


__all__ = [
    "ArtifactManifest",
    "ComponentDescriptor",
    "ComponentManifest",
    "DeliveryPartGrant",
    "DeliveryPartReceipt",
    "DeliveryReceipt",
    "DeliveryTicket",
    "DeliveryTicketHeader",
    "DeliveryTicketPayload",
    "OutboundPart",
    "OutboundPlan",
    "QcEvidence",
    "grant_from_outbound_part",
    "text_sha256",
]
