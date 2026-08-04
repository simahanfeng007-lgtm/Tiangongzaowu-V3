"""Strict, side-effect-free gateway contract models."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256


SCHEMA_VERSION = "tiangong.gateway.contracts.v2"
LEGACY_SCHEMA_VERSION = "tiangong.gateway.contracts.v1"
SCHEMA_BASE = "urn:tiangong:gateway:contracts:v2"

# 读路径可接受的 schema 版本集合：v1 历史行（hash 按行自身字节验证）+ v2 新行。
# 新写入恒为 v2（字段默认值即 v2）；Literal 校验只为兼容存量读取放宽。
AcceptedSchemaVersion = Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"]

OpaqueId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    ),
]
RequestId = Annotated[str, StringConstraints(pattern=r"^req_[0-9a-f]{64}$")]
RunId = Annotated[str, StringConstraints(pattern=r"^run_[0-9a-f]{64}$")]
EffectId = Annotated[str, StringConstraints(pattern=r"^eff_[0-9a-f]{64}$")]
ArtifactId = Annotated[str, StringConstraints(pattern=r"^art_[0-9a-f]{64}$")]
ArtifactRevisionId = Annotated[str, StringConstraints(pattern=r"^arv_[0-9a-f]{64}$")]
DeliveryId = Annotated[str, StringConstraints(pattern=r"^del_[0-9a-f]{64}$")]
GenerationFenceId = Annotated[str, StringConstraints(pattern=r"^fnc_[0-9a-f]{64}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MimeType = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=255,
        to_lower=True,
        pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
    ),
]
ActionId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9._:-]*$"),
]
ReasonCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9._:-]*$"),
]


def _sorted_unique_refs(value: tuple[OpaqueId, ...]) -> tuple[OpaqueId, ...]:
    if any(value[index] >= value[index + 1] for index in range(len(value) - 1)):
        raise ValueError("refs must be sorted and unique")
    return value


def _sorted_unique_by_slot(value: tuple["ProviderSlot", ...]) -> tuple["ProviderSlot", ...]:
    slots = [item.slot_no for item in value]
    if any(slots[index] >= slots[index + 1] for index in range(len(slots) - 1)):
        raise ValueError("provider slots must be strictly ordered by slot_no")
    return value


class ContractModel(BaseModel):
    """Base policy: immutable, strict inputs, and no undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def validate_safe_filename(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("filename must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("filename must use NFC Unicode normalization")
    try:
        utf8_bytes = value.encode("utf-8", errors="strict")
        utf16_units = len(value.encode("utf-16-le", errors="strict")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("filename contains an invalid Unicode scalar") from exc
    # Linux commonly enforces NAME_MAX in bytes while Windows enforces a
    # component limit in UTF-16 code units.  Enforce both so a filename that
    # passed validation on one release host cannot fail on the other.
    if len(utf8_bytes) > 255 or utf16_units > 255:
        raise ValueError("filename exceeds the cross-platform component limit")
    if value != value.strip() or value.endswith((".", " ")):
        raise ValueError("filename may not have surrounding spaces or a trailing dot")
    if value in {".", ".."}:
        raise ValueError("relative path names are forbidden")
    forbidden_categories = {"Cc", "Cf", "Cs", "Zl", "Zp"}
    if any(unicodedata.category(char) in forbidden_categories for char in value):
        raise ValueError("filename contains an invisible, directional, or control character")
    if any(char in value for char in '\\/:*?"<>|'):
        raise ValueError("filename contains a path separator or reserved character")
    stem = value.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update({f"COM{index}" for index in range(1, 10)})
    reserved.update({f"LPT{index}" for index in range(1, 10)})
    if stem in reserved:
        raise ValueError("filename is reserved on Windows")
    return value


SourceType = Literal[
    "CURRENT_USER_INSTRUCTION",
    "PREAUTHORIZED_USER_FACT",
    "AUTHENTICATED_DIRECTORY",
    "EXTERNAL_DATA",
    "TOOL_DATA",
]


class SourceRef(ContractModel):
    """A content-addressed provenance reference with an optional span anchor."""

    source_type: SourceType
    object_id: OpaqueId
    object_revision: int = Field(ge=1)
    sha256: Sha256
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("source ref span must be all-or-none")
        if self.span_start is not None and self.span_start > self.span_end:
            raise ValueError("source ref span is inverted")
        return self

    def sort_key(self) -> tuple[str, str, int, str, int, int]:
        return (
            self.source_type,
            self.object_id,
            self.object_revision,
            self.sha256,
            self.span_start if self.span_start is not None else -1,
            self.span_end if self.span_end is not None else -1,
        )


class AttachmentRef(ContractModel):
    """An accepted, content-addressed attachment; never a host filesystem path."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AttachmentRef",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    object_id: OpaqueId
    revision: int = Field(ge=1)
    sha256: Sha256
    size_bytes: int = Field(ge=1, le=2_147_483_648)
    mime: MimeType
    filename: str = Field(min_length=1, max_length=255)
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_scope_hash: Sha256
    source_message_ref: OpaqueId | None = None
    created_at_ms: int = Field(ge=0)
    acceptance: Literal["accepted"] = "accepted"
    magic_verified: Literal[True] = True

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return validate_safe_filename(value)


class InboundEnvelope(ContractModel):
    """A durable, de-duplicated channel event presented to the total gateway."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:InboundEnvelope",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    inbound_id: OpaqueId
    channel: Literal["desktop", "wechat", "feishu", "system", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_ref: OpaqueId
    conversation_scope_hash: Sha256
    principal_scope_hash: Sha256
    message_scope_hash: Sha256
    channel_message_ref: OpaqueId
    sender_ref: OpaqueId
    received_at_ms: int = Field(ge=0)
    idempotency_key: Sha256
    channel_metadata_hash: Sha256
    text: str = Field(default="", max_length=100_000)
    attachments: tuple[AttachmentRef, ...] = Field(default=(), max_length=20)
    reply_to_message_ref: OpaqueId | None = None
    root_message_ref: OpaqueId | None = None
    sequence: int | None = Field(default=None, ge=0)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("text contains a NUL character")
        return value

    @model_validator(mode="after")
    def validate_content_and_scope(self) -> Self:
        if not self.text.strip() and not self.attachments:
            raise ValueError("an inbound envelope must contain text or an attachment")
        seen: set[tuple[str, int]] = set()
        for attachment in self.attachments:
            if attachment.tenant_id != self.tenant_id:
                raise ValueError("attachment tenant does not match envelope tenant")
            if attachment.link_account_id != self.link_account_id:
                raise ValueError("attachment account does not match envelope account")
            if attachment.conversation_scope_hash != self.conversation_scope_hash:
                raise ValueError("attachment conversation scope does not match envelope scope")
            key = (attachment.object_id, attachment.revision)
            if key in seen:
                raise ValueError("duplicate attachment revision")
            seen.add(key)
        return self


class LifeSnapshot(ContractModel):
    """A fixed life/persona projection bound to one gateway run."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:LifeSnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    source_contract: Literal["tiangong.life.api.v2"] = "tiangong.life.api.v2"
    snapshot_id: OpaqueId
    revision: int = Field(ge=1)
    sha256: Sha256
    created_at_ms: int = Field(ge=0)
    identity_ref: OpaqueId
    identity_revision: int = Field(ge=1)
    persona_name: str = Field(min_length=1, max_length=128)
    persona_avatar_ref: OpaqueId | None = None
    persona_voice_ref: OpaqueId | None = None
    user_callsign: str = Field(min_length=1, max_length=128)
    user_avatar_ref: OpaqueId | None = None
    user_occupation: str = Field(default="", max_length=512)
    compiled_context_object_id: OpaqueId
    compiled_context_sha256: Sha256
    context_authorization_id: OpaqueId | None = None
    context_authorization_sha256: Sha256 | None = None
    revision_vector_sha256: Sha256 | None = None
    soul_sha256: Sha256
    memory_revision: int = Field(ge=0)
    affect_revision: int = Field(ge=0)
    causal_revision: int = Field(default=0, ge=0)
    viability_revision: int = Field(default=0, ge=0)
    policy_revision: int = Field(default=0, ge=0)
    reflection_revision: int = Field(default=0, ge=0)
    capability_revision: int = Field(default=0, ge=0)
    capability_profile_hash: Sha256

    @field_validator("persona_name", "user_callsign", "user_occupation")
    @classmethod
    def validate_human_text(cls, value: str) -> str:
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("human-readable fields must use NFC normalization")
        if "\x00" in value or any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise ValueError("human-readable field contains a control character")
        return value

    @model_validator(mode="after")
    def validate_atomic_context_binding(self) -> Self:
        atomic = (
            self.context_authorization_id,
            self.context_authorization_sha256,
            self.revision_vector_sha256,
        )
        if any(value is not None for value in atomic) and not all(
            value is not None for value in atomic
        ):
            raise ValueError("atomic context snapshot binding must be all-or-none")
        return self


class SkillCandidate(ContractModel):
    """A Skill candidate checked against one fixed CapabilityManifest."""

    skill_id: OpaqueId
    version: OpaqueId
    sha256: Sha256
    source_ref: OpaqueId
    score_millis: int = Field(ge=0, le=1000)
    required_actions: tuple[ActionId, ...] = Field(default=(), max_length=256)
    missing_actions: tuple[ActionId, ...] = Field(default=(), max_length=256)
    incompatible_reasons: tuple[ReasonCode, ...] = Field(default=(), max_length=32)
    compatible: bool

    @field_validator("required_actions", "missing_actions", "incompatible_reasons")
    @classmethod
    def validate_sorted_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("set-like contract fields must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_compatibility(self) -> Self:
        if self.compatible and (self.missing_actions or self.incompatible_reasons):
            raise ValueError("a compatible Skill cannot have missing actions or incompatibility reasons")
        if not self.compatible and not (self.missing_actions or self.incompatible_reasons):
            raise ValueError("an incompatible Skill must provide a reason")
        if not set(self.missing_actions).issubset(self.required_actions):
            raise ValueError("missing actions must be a subset of required actions")
        return self


class SkillSelectionRecord(ContractModel):
    """Auditable system recommendation or model-initiated Skill selection."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:SkillSelectionRecord",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    selection_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    origin: Literal["system_recommendation", "model_request"]
    operation: Literal["system.recommend", "skill.route", "skill.list", "skill.get", "skill.read"]
    query_hash: Sha256 | None = None
    skill_catalog_hash: Sha256
    capability_manifest_hash: Sha256
    candidates: tuple[SkillCandidate, ...] = Field(default=(), max_length=32)
    decision: Literal["activate", "reject", "no_skill", "defer"]
    selected_skill_id: OpaqueId | None = None
    selected_skill_version: OpaqueId | None = None
    selected_skill_sha256: Sha256 | None = None
    activation_state: Literal["candidate", "resolved", "active", "rejected", "none"]
    resolved_via: Literal["skill.get", "skill.read"] | None = None
    reason_code: ReasonCode
    decided_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.origin == "system_recommendation" and self.operation != "system.recommend":
            raise ValueError("system recommendations must use system.recommend")
        if self.origin == "model_request" and self.operation == "system.recommend":
            raise ValueError("model requests cannot claim system.recommend")

        candidates_by_id = {candidate.skill_id: candidate for candidate in self.candidates}
        if len(candidates_by_id) != len(self.candidates):
            raise ValueError("candidate skill ids must be unique")

        selected_fields = (
            self.selected_skill_id,
            self.selected_skill_version,
            self.selected_skill_sha256,
        )
        selected_count = sum(value is not None for value in selected_fields)
        if selected_count not in {0, 3}:
            raise ValueError("selected Skill identity must bind id, version, and sha256 together")

        selected_candidate = None
        if selected_count == 3:
            selected_candidate = candidates_by_id.get(self.selected_skill_id or "")
            if selected_candidate is None:
                raise ValueError("selected Skill must be present in candidates")
            if (
                selected_candidate.version != self.selected_skill_version
                or selected_candidate.sha256 != self.selected_skill_sha256
            ):
                raise ValueError("selected Skill identity does not match the checked candidate")

        if self.activation_state in {"resolved", "active"}:
            if self.resolved_via is None or self.operation != self.resolved_via:
                raise ValueError("resolved Skill state must match the successful skill.get/read operation")
        elif self.resolved_via is not None:
            raise ValueError("unresolved Skill state cannot set resolved_via")

        if self.decision == "activate":
            if selected_count != 3:
                raise ValueError("activated Skill must bind id, version, and sha256")
            if selected_candidate is None or not selected_candidate.compatible:
                raise ValueError("an incompatible Skill cannot be activated")
            if self.activation_state != "active" or self.resolved_via not in {"skill.get", "skill.read"}:
                raise ValueError("Skill becomes active only after skill.get or skill.read")
        else:
            if self.activation_state == "active":
                raise ValueError("only an activated Skill may be active")
            if self.decision == "no_skill" and (selected_count or self.activation_state != "none"):
                raise ValueError("no_skill cannot bind a selected Skill or non-none state")
            if self.decision == "reject" and self.activation_state != "rejected":
                raise ValueError("rejected Skill decision must use rejected state")
            if self.decision == "defer" and self.activation_state not in {"candidate", "resolved"}:
                raise ValueError("deferred Skill must remain candidate or resolved")
        return self


ModelAttemptPlanId = Annotated[str, StringConstraints(pattern=r"^map_[0-9a-f]{64}$")]
ModelAttemptId = Annotated[str, StringConstraints(pattern=r"^mat_[0-9a-f]{64}$")]
ModelAttemptReceiptId = Annotated[str, StringConstraints(pattern=r"^mar_[0-9a-f]{64}$")]
ModelAttemptPlanOutcomeId = Annotated[str, StringConstraints(pattern=r"^mapo_[0-9a-f]{64}$")]
AssistantMessageId = Annotated[str, StringConstraints(pattern=r"^asm_[0-9a-f]{64}$")]
AssistantCommitId = Annotated[str, StringConstraints(pattern=r"^asc_[0-9a-f]{64}$")]
SystemStatusId = Annotated[str, StringConstraints(pattern=r"^sys_[0-9a-f]{64}$")]


def derive_model_inference_effect_id(
    *,
    origin_request_id: str,
    origin_run_id: str,
    root_experience_id: str,
    response_episode_id: str,
    request_sha256: str,
) -> str:
    return "eff_" + canonical_sha256(
        {
            "domain": "tiangong.v21.model-effect.v1",
            "origin_request_id": origin_request_id,
            "origin_run_id": origin_run_id,
            "root_experience_id": root_experience_id,
            "response_episode_id": response_episode_id,
            "request_sha256": request_sha256,
        }
    )


def derive_model_attempt_plan_id(
    *,
    model_effect_id: str,
    response_episode_id: str,
    request_sha256: str,
    plan_revision: int,
) -> str:
    return "map_" + canonical_sha256(
        {
            "domain": "tiangong.v21.model-plan.v1",
            "model_effect_id": model_effect_id,
            "response_episode_id": response_episode_id,
            "request_sha256": request_sha256,
            "plan_revision": plan_revision,
        }
    )


def derive_model_attempt_id(*, model_attempt_plan_id: str, slot_no: int) -> str:
    return "mat_" + canonical_sha256(
        {
            "domain": "tiangong.v21.model-attempt.v1",
            "model_attempt_plan_id": model_attempt_plan_id,
            "slot_no": slot_no,
        }
    )


def derive_model_attempt_receipt_id(*, model_attempt_id: str) -> str:
    return "mar_" + canonical_sha256(
        {
            "domain": "tiangong.v21.model-receipt.v1",
            "model_attempt_id": model_attempt_id,
        }
    )


def derive_model_attempt_plan_outcome_id(*, model_attempt_plan_id: str) -> str:
    return "mapo_" + canonical_sha256(
        {
            "domain": "tiangong.v21.model-plan-outcome.v1",
            "model_attempt_plan_id": model_attempt_plan_id,
        }
    )


def derive_assistant_message_id(
    *,
    life_id: str,
    root_experience_id: str,
    response_episode_id: str,
    model_attempt_receipt_id: str,
    committed_text_sha256: str,
) -> str:
    return "asm_" + canonical_sha256(
        {
            "domain": "tiangong.v21.assistant-message.v1",
            "life_id": life_id,
            "root_experience_id": root_experience_id,
            "response_episode_id": response_episode_id,
            "model_attempt_receipt_id": model_attempt_receipt_id,
            "committed_text_sha256": committed_text_sha256,
        }
    )


def derive_assistant_commit_id(*, response_episode_id: str, assistant_message_id: str) -> str:
    return "asc_" + canonical_sha256(
        {
            "domain": "tiangong.v21.assistant-commit.v1",
            "response_episode_id": response_episode_id,
            "assistant_message_id": assistant_message_id,
        }
    )


class ProviderSlot(ContractModel):
    schema_version: Literal["tiangong.model_attempt_plan.v1"] = "tiangong.model_attempt_plan.v1"
    slot_no: int = Field(ge=1)
    provider: OpaqueId
    model: OpaqueId
    transport_profile_sha256: Sha256


class ModelAttemptPlan(ContractModel):
    """Immutable frozen plan for one response episode; persisted before dispatch."""

    schema_id: Literal["ModelAttemptPlan"] = "ModelAttemptPlan"
    schema_version: Literal["tiangong.model_attempt_plan.v1"] = "tiangong.model_attempt_plan.v1"
    model_attempt_plan_id: ModelAttemptPlanId
    model_effect_id: EffectId
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    run_life_binding_sha256: Sha256
    root_experience_id: OpaqueId
    response_episode_id: OpaqueId
    response_episode_sha256: Sha256
    context_pack_ref: OpaqueId
    context_pack_sha256: Sha256
    response_basis_kind: Literal["commitment", "conversation"]
    response_basis_sha256: Sha256
    capability_profile_sha256: Sha256
    provider_slots: tuple[ProviderSlot, ...] = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    request_sha256: Sha256
    completion_delivery_mode: Literal["none", "response_delivery"] | None = None
    completion_decision_ref: OpaqueId | None = None
    completion_decision_sha256: Sha256 | None = None
    conversation_basis_ref: OpaqueId | None = None
    plan_sha256: Sha256

    _unique_slots = field_validator("provider_slots")(_sorted_unique_by_slot)

    @model_validator(mode="after")
    def validate_basis_shape(self) -> Self:
        if self.response_basis_kind == "commitment":
            if (
                self.completion_delivery_mode is None
                or self.completion_decision_ref is None
                or self.completion_decision_sha256 is None
            ):
                raise ValueError("commitment plan requires completion decision binding")
            if self.conversation_basis_ref is not None:
                raise ValueError("commitment plan cannot carry a conversation basis")
            if self.response_basis_sha256 != self.completion_decision_sha256:
                raise ValueError("commitment response basis must equal completion decision sha256")
        else:
            if self.conversation_basis_ref is None:
                raise ValueError("conversation plan requires conversation basis ref")
            if self.completion_delivery_mode is not None or self.completion_decision_ref is not None or self.completion_decision_sha256 is not None:
                raise ValueError("conversation plan cannot carry completion decision fields")
        return self

    def computed_plan_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"plan_sha256"}))

    def has_valid_plan_sha256(self) -> bool:
        return self.plan_sha256 == self.computed_plan_sha256()

    def with_computed_plan_sha256(self) -> Self:
        return self.model_copy(update={"plan_sha256": self.computed_plan_sha256()})


class ModelAttemptResult(ContractModel):
    """Immutable transport-adapter observation for one frozen plan slot."""

    schema_id: Literal["ModelAttemptResult"] = "ModelAttemptResult"
    schema_version: Literal["tiangong.model_attempt_result.v1"] = "tiangong.model_attempt_result.v1"
    model_attempt_receipt_id: ModelAttemptReceiptId
    model_attempt_plan_id: ModelAttemptPlanId
    model_attempt_plan_sha256: Sha256
    model_effect_id: EffectId
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    run_life_binding_sha256: Sha256
    root_experience_id: OpaqueId
    response_episode_id: OpaqueId
    attempt_id: ModelAttemptId
    slot_no: int = Field(ge=1)
    provider: OpaqueId
    model: OpaqueId
    status: Literal["SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED"]
    attempt_plan_revision: int = Field(ge=1)
    request_sha256: Sha256
    dispatched: bool
    started_at_ms: int = Field(ge=0)
    completed_at_ms: int = Field(ge=0)
    response_schema_valid: bool
    dispatch_marker_ref: OpaqueId | None = None
    transport_run_id: OpaqueId | None = None
    provider_response_id: OpaqueId | None = None
    text_object_id: OpaqueId | None = None
    output_text_sha256: Sha256 | None = None
    finish_reason: str | None = Field(default=None, max_length=160)
    usage: OpaqueId | None = None
    error_code: ReasonCode | None = None
    retryable: bool | None = None

    @model_validator(mode="after")
    def validate_dispatch_and_success_shape(self) -> Self:
        if self.dispatched:
            if self.dispatch_marker_ref is None:
                raise ValueError("dispatched attempt requires dispatch marker")
            if self.transport_run_id is None:
                raise ValueError("dispatched attempt requires transport run id")
        else:
            if self.dispatch_marker_ref is not None or self.transport_run_id is not None:
                raise ValueError("undispatched attempt cannot carry dispatch evidence")
            if self.status in {"SUCCEEDED", "AMBIGUOUS"}:
                raise ValueError("undispatched attempt cannot succeed or be ambiguous")
        if self.status == "SUCCEEDED":
            if (
                not self.response_schema_valid
                or self.text_object_id is None
                or self.output_text_sha256 is None
                or not self.finish_reason
            ):
                raise ValueError("succeeded attempt requires schema-valid text evidence")
        elif not self.response_schema_valid and self.status == "SUCCEEDED":
            raise ValueError("schema-invalid response cannot succeed")
        if self.completed_at_ms < self.started_at_ms:
            raise ValueError("attempt completion precedes start")
        return self

    def computed_receipt_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"model_attempt_receipt_id"}))


class ModelAttemptPlanOutcome(ContractModel):
    """Machine-computed terminal outcome for one frozen plan."""

    schema_id: Literal["ModelAttemptPlanOutcome"] = "ModelAttemptPlanOutcome"
    schema_version: Literal["tiangong.model_attempt_plan_outcome.v1"] = "tiangong.model_attempt_plan_outcome.v1"
    model_attempt_plan_outcome_id: ModelAttemptPlanOutcomeId
    model_attempt_plan_id: ModelAttemptPlanId
    model_attempt_plan_sha256: Sha256
    status: Literal["SUCCEEDED", "EXHAUSTED"]
    ordered_attempt_refs: tuple[ModelAttemptReceiptId, ...] = Field(min_length=1)
    winner_attempt_ref: ModelAttemptReceiptId | None = None
    completed_at_ms: int = Field(ge=0)
    outcome_sha256: Sha256

    @model_validator(mode="after")
    def validate_winner_shape(self) -> Self:
        if len(set(self.ordered_attempt_refs)) != len(self.ordered_attempt_refs):
            raise ValueError("attempt refs must be unique")
        if self.status == "SUCCEEDED":
            if self.winner_attempt_ref is None or self.winner_attempt_ref not in self.ordered_attempt_refs:
                raise ValueError("succeeded outcome requires a winner from the attempt refs")
        else:
            if self.winner_attempt_ref is not None:
                raise ValueError("exhausted outcome cannot carry a winner")
        return self

    def computed_outcome_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"outcome_sha256"}))

    def has_valid_outcome_sha256(self) -> bool:
        return self.outcome_sha256 == self.computed_outcome_sha256()

    def with_computed_outcome_sha256(self) -> Self:
        return self.model_copy(update={"outcome_sha256": self.computed_outcome_sha256()})


class SystemStatusRecord(ContractModel):
    """Typed system status card; never coerced into an assistant message."""

    schema_id: Literal["SystemStatusRecord"] = "SystemStatusRecord"
    schema_version: Literal["tiangong.system_status_record.v1"] = "tiangong.system_status_record.v1"
    system_status_id: SystemStatusId
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    response_episode_id: OpaqueId
    status_code: ReasonCode
    severity: Literal["info", "warning", "error", "fatal"]
    source_component: OpaqueId
    source_fact_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    display_object_ref: OpaqueId
    origin: Literal["system"] = "system"
    created_at_ms: int = Field(ge=0)
    system_status_sha256: Sha256

    _unique_facts = field_validator("source_fact_refs")(_sorted_unique_refs)

    def computed_status_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"system_status_sha256"}))

    def with_computed_status_sha256(self) -> Self:
        return self.model_copy(update={"system_status_sha256": self.computed_status_sha256()})


class AssistantMessage(ContractModel):
    schema_id: Literal["AssistantMessage"] = "AssistantMessage"
    schema_version: Literal["tiangong.assistant_message.v1"] = "tiangong.assistant_message.v1"
    assistant_message_id: AssistantMessageId
    assistant_commit_id: AssistantCommitId
    assistant_commit_sha256: Sha256
    text: str = Field(min_length=1, max_length=200_000)
    text_object_id: OpaqueId
    committed_text_sha256: Sha256
    life_id: OpaqueId
    root_experience_id: OpaqueId
    response_episode_id: OpaqueId
    model_attempt_receipt_id: ModelAttemptReceiptId
    provider: OpaqueId
    model: OpaqueId
    committed_at_ms: int = Field(ge=0)


class AssistantSystemEnvelope(ContractModel):
    """Desktop result envelope: assistant_message / system_status / completion_decision."""

    schema_id: Literal["AssistantSystemEnvelope"] = "AssistantSystemEnvelope"
    schema_version: Literal["tiangong.assistant_system_envelope.v1"] = "tiangong.assistant_system_envelope.v1"
    assistant_message: AssistantMessage | None = None
    system_status: SystemStatusRecord | None = None
    completion_decision: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_slot_shape(self) -> Self:
        if self.assistant_message is None and self.system_status is None and self.completion_decision is None:
            raise ValueError("terminal envelope requires at least one non-null slot")
        if (
            self.system_status is not None
            and self.system_status.status_code == "all_models_unavailable"
            and self.assistant_message is not None
        ):
            raise ValueError("all_models_unavailable forbids an assistant message")
        return self


class AssistantCommit(ContractModel):
    """Durable gateway authority record proving one committed assistant message."""

    schema_id: Literal["AssistantCommit"] = "AssistantCommit"
    schema_version: Literal["tiangong.assistant_commit.v1"] = "tiangong.assistant_commit.v1"
    assistant_commit_id: AssistantCommitId
    assistant_message_id: AssistantMessageId
    life_turn_commit_ref: OpaqueId
    life_turn_commit_sha256: Sha256
    response_episode_id: OpaqueId
    model_attempt_plan_outcome_ref: OpaqueId
    model_attempt_receipt_id: ModelAttemptReceiptId
    output_text_sha256: Sha256
    committed_text_sha256: Sha256
    text_object_id: OpaqueId
    committed_at_ms: int = Field(ge=0)
    commit_sha256: Sha256

    @model_validator(mode="after")
    def validate_text_binding(self) -> Self:
        if self.output_text_sha256 != self.committed_text_sha256:
            raise ValueError("assistant commit text hash is inconsistent")
        return self

    def computed_commit_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"commit_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.commit_sha256 == self.computed_commit_sha256()

    def with_computed_commit_sha256(self) -> Self:
        return self.model_copy(update={"commit_sha256": self.computed_commit_sha256()})


class CompletionObligation(ContractModel):
    schema_id: Literal["CompletionObligation"] = "CompletionObligation"
    schema_version: Literal["tiangong.completion_requirements.v2"] = "tiangong.completion_requirements.v2"
    obligation_id: OpaqueId
    kind: Literal["execution", "artifact", "delivery", "qc", "constraint"]
    source_kind: Literal["user", "skill", "derived_necessity", "model_optional"]
    source_requirement_stable_key: str = Field(min_length=1, max_length=256)
    source_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    mandatory: bool
    acceptance_ref: OpaqueId
    delivery_phase: Literal["execution", "response"] | None = None

    @model_validator(mode="after")
    def validate_delivery_phase(self) -> Self:
        if self.kind == "delivery" and self.delivery_phase is None:
            raise ValueError("delivery obligation requires delivery phase")
        if self.kind != "delivery" and self.delivery_phase is not None:
            raise ValueError("non-delivery obligation cannot carry delivery phase")
        return self


class CoverageProofRow(ContractModel):
    schema_id: Literal["CoverageProofRow"] = "CoverageProofRow"
    schema_version: Literal["tiangong.completion_requirements.v2"] = "tiangong.completion_requirements.v2"
    source_requirement_stable_key: str = Field(min_length=1, max_length=256)
    source_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    obligation_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=256)
    coverage_status: Literal["COVERED", "AMBIGUOUS"]

    def sort_key(self) -> tuple[str, ...]:
        return (self.source_requirement_stable_key, *self.source_refs)


class CompletionRequirementsVNext(ContractModel):
    """Frozen commitment requirements with machine coverage proof."""

    schema_id: Literal["CompletionRequirementsVNext"] = "CompletionRequirementsVNext"
    schema_version: Literal["tiangong.completion_requirements.v2"] = "tiangong.completion_requirements.v2"
    commitment_id: OpaqueId
    commitment_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    root_experience_id: OpaqueId
    raw_goal_sha256: Sha256
    source_input_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    source_input_set_sha256: Sha256
    commitment_revision: int = Field(ge=1)
    obligations: tuple[CompletionObligation, ...] = Field(min_length=1, max_length=1024)
    obligation_set_sha256: Sha256
    coverage_proof: tuple[CoverageProofRow, ...] = Field(min_length=1, max_length=1024)
    requirements_sha256: Sha256
    selected_skill_activation_sha256: Sha256 | None = None
    supersedes_sha256: Sha256 | None = None
    amendment_source_binding_sha256: Sha256 | None = None
    amendment_source_event_ref: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_requirements(self) -> Self:
        if (self.commitment_revision == 1) != (self.supersedes_sha256 is None):
            raise ValueError("commitment revision chain is invalid")
        keys = [row.sort_key() for row in self.coverage_proof]
        if keys != sorted(set(keys)):
            raise ValueError("coverage proof rows must be sorted and unique by source requirement")
        if self.obligation_set_sha256 != canonical_sha256(
            [item.model_dump(mode="json") for item in self.obligations]
        ):
            raise ValueError("obligation set digest is invalid")
        if self.source_input_set_sha256 != canonical_sha256(list(self.source_input_refs)):
            raise ValueError("source input set digest is invalid")
        covered_ids = {
            obligation_id
            for row in self.coverage_proof
            if row.coverage_status == "COVERED"
            for obligation_id in row.obligation_ids
        }
        mandatory_ids = {
            item.obligation_id
            for item in self.obligations
            if item.mandatory and item.source_kind in {"user", "skill", "derived_necessity"}
        }
        if mandatory_ids and not mandatory_ids <= covered_ids:
            raise ValueError("every mandatory source obligation must be covered")
        return self

    def computed_requirements_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"commitment_sha256", "requirements_sha256"})
        )

    def has_valid_requirements_sha256(self) -> bool:
        return (
            self.requirements_sha256 == self.computed_requirements_sha256()
            and self.commitment_sha256 == self.requirements_sha256
        )

    def with_computed_sha256(self) -> Self:
        requirements_sha256 = self.computed_requirements_sha256()
        return self.model_copy(
            update={
                "requirements_sha256": requirements_sha256,
                "commitment_sha256": requirements_sha256,
            }
        )


class PhaseNode(ContractModel):
    schema_id: Literal["PhaseNode"] = "PhaseNode"
    schema_version: Literal["tiangong.skill_definition_core.v1"] = "tiangong.skill_definition_core.v1"
    phase_id: str = Field(min_length=1, max_length=160)
    kind: Literal["starter", "production", "inspection", "qc", "repair", "final"]
    optional: bool = False
    one_of_group: str | None = None
    depends_on: tuple[str, ...] = Field(default=(), max_length=64)
    invalidates: tuple[str, ...] = Field(default=(), max_length=64)
    retry_to: str | None = None
    allowed_action_bindings: tuple[str, ...] = Field(default=(), max_length=256)


class SkillDefinitionCore(ContractModel):
    """G5 core SkillDefinition: schemas, phase graph, acceptance and pointer binding."""

    schema_id: Literal["SkillDefinitionCore"] = "SkillDefinitionCore"
    schema_version: Literal["tiangong.skill_definition_core.v1"] = "tiangong.skill_definition_core.v1"
    skill_id: OpaqueId
    skill_version: OpaqueId
    skill_sha256: Sha256
    input_schema_sha256: Sha256
    output_schema_sha256: Sha256
    source_ref: OpaqueId
    source_revision: int = Field(ge=1)
    commitment_template_sha256: Sha256
    phase_graph: tuple[PhaseNode, ...] = Field(min_length=1, max_length=256)
    acceptance_profile_id: OpaqueId
    acceptance_profile_version: OpaqueId
    acceptance_profile_sha256: Sha256
    artifact_sha256: Sha256
    active_pointer_sha256: Sha256
    life_overlay_sha256: Sha256 | None = None
    definition_sha256: Sha256

    @model_validator(mode="after")
    def validate_phase_graph(self) -> Self:
        phase_ids = [node.phase_id for node in self.phase_graph]
        if len(set(phase_ids)) != len(phase_ids):
            raise ValueError("phase ids must be unique")
        for node in self.phase_graph:
            for dependency in (*node.depends_on, *node.invalidates):
                if dependency not in phase_ids:
                    raise ValueError("phase graph references an unknown phase")
            if node.retry_to is not None and node.retry_to not in phase_ids:
                raise ValueError("phase retry target is unknown")
        if not any(node.kind == "final" for node in self.phase_graph):
            raise ValueError("phase graph requires a final phase")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"definition_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.definition_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"definition_sha256": self.computed_sha256()})


__all__ = [
    "ArtifactId",
    "ArtifactRevisionId",
    "AssistantCommit",
    "AssistantCommitId",
    "AssistantMessage",
    "AssistantMessageId",
    "AssistantSystemEnvelope",
    "AttachmentRef",
    "CompletionObligation",
    "CompletionRequirementsVNext",
    "CoverageProofRow",
    "DeliveryId",
    "EffectId",
    "GenerationFenceId",
    "InboundEnvelope",
    "LifeSnapshot",
    "ModelAttemptId",
    "ModelAttemptPlan",
    "ModelAttemptPlanId",
    "ModelAttemptPlanOutcome",
    "ModelAttemptPlanOutcomeId",
    "ModelAttemptReceiptId",
    "ModelAttemptResult",
    "ProviderSlot",
    "PhaseNode",
    "SkillDefinitionCore",
    "RequestId",
    "RunId",
    "SCHEMA_VERSION",
    "SkillCandidate",
    "SkillSelectionRecord",
    "SourceRef",
    "SourceType",
    "SystemStatusId",
    "SystemStatusRecord",
    "derive_assistant_commit_id",
    "derive_assistant_message_id",
    "derive_model_attempt_id",
    "derive_model_attempt_plan_id",
    "derive_model_attempt_plan_outcome_id",
    "derive_model_attempt_receipt_id",
    "derive_model_inference_effect_id",
    "validate_safe_filename",
]
