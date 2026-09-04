"""Capability, execution-ticket, result, and fact contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .models import (
    ActionId,
    ContractModel,
    EffectId,
    MimeType,
    OpaqueId,
    ReasonCode,
    RequestId,
    RunId,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
)


RiskClass = Literal["A0", "A1", "A2", "A3", "A4", "A5"]
SideEffectClass = Literal[
    "none",
    "read",
    "local_write",
    "external_write",
    "external_send",
    "destructive",
]
Base64UrlEd25519Signature = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{86}$"),
]


class CapabilityAction(ContractModel):
    action_id: ActionId
    version: OpaqueId
    provider_component_id: OpaqueId
    argument_schema_sha256: Sha256
    result_schema_sha256: Sha256
    risk_class: RiskClass
    allowed_side_effects: tuple[SideEffectClass, ...] = Field(max_length=6)
    idempotency_mode: Literal["pure", "effect_id_required", "non_retriable"]
    max_runtime_ms: int = Field(ge=1, le=3_600_000)
    max_output_bytes: int = Field(ge=0, le=2_147_483_648)
    max_tool_calls: int = Field(ge=1, le=10_000)
    available: bool
    unavailable_reason: ReasonCode | None = None
    model_visible: bool = True

    @field_validator("allowed_side_effects")
    @classmethod
    def validate_side_effects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("allowed side effects must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available action cannot carry unavailable_reason")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("unavailable action must carry unavailable_reason")
        if self.idempotency_mode == "pure" and any(
            value not in {"none", "read"} for value in self.allowed_side_effects
        ):
            raise ValueError("pure action cannot authorize a write or send side effect")
        return self


class CapabilityManifest(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CapabilityManifest",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    manifest_id: OpaqueId
    revision: int = Field(ge=1)
    generated_at_ms: int = Field(ge=0)
    component_manifest_hash: Sha256
    actions: tuple[CapabilityAction, ...] = Field(min_length=1, max_length=10_000)
    sha256: Sha256

    @model_validator(mode="after")
    def validate_actions(self) -> Self:
        keys = tuple((action.action_id, action.version) for action in self.actions)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("CapabilityManifest actions must be sorted and unique")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"sha256": self.computed_sha256()})


class ObjectGrant(ContractModel):
    object_id: OpaqueId
    revision: int = Field(ge=1)
    sha256: Sha256
    size_bytes: int = Field(ge=0, le=2_147_483_648)
    mime: MimeType
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_scope_hash: Sha256
    permission: Literal["read"] = "read"


class CompositionExecutionBindingV1(ContractModel):
    """Immutable authorization coordinates for one materialized composition step.

    This object is evidence, not an authority by itself.  A trusted caller must
    independently supply the expected binding at policy/admission time; the
    signed copies carried by intent, decision, ticket, and grant then make any
    later plan, step, target, argument, or run substitution detectable.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CompositionExecutionBindingV1",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.composition-execution-binding.v1"] = (
        "tiangong.composition-execution-binding.v1"
    )
    binding_type: Literal["COMPOSITION_STEP"] = "COMPOSITION_STEP"
    executable_plan_id: OpaqueId
    executable_plan_sha256: Sha256
    step_id: OpaqueId
    step_binding_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    effect_id: EffectId
    action_id: ActionId
    action_version: OpaqueId
    materialized_arguments_sha256: Sha256
    canonical_invocation_sha256: Sha256
    target_sha256: Sha256
    target_snapshot_sha256: Sha256 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    workspace_id: OpaqueId
    workspace_scope_hash: Sha256
    binding_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"binding_sha256": self.computed_sha256()})


class ExecutionTicketHeader(ContractModel):
    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    alg: Literal["EdDSA"] = "EdDSA"
    typ: Literal["tiangong.execution-ticket+jws"] = "tiangong.execution-ticket+jws"
    kid: OpaqueId


class ExecutionTicketPayload(ContractModel):
    ticket_type: Literal["ExecutionTicket"] = "ExecutionTicket"
    contract_version: Literal[3] = 3
    ticket_id: OpaqueId
    nonce: OpaqueId
    issuer: Literal["tiangong-total-gateway"] = "tiangong-total-gateway"
    audience: Literal["tiangong-backend"] = "tiangong-backend"
    issued_at_ms: int = Field(ge=0)
    not_before_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    gateway_epoch: int = Field(ge=1)
    fence_epoch: int = Field(default=1, ge=1)
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    effect_id: EffectId
    channel: Literal["desktop", "wechat", "feishu", "system", "test"]
    tenant_id: OpaqueId
    link_account_id: OpaqueId
    conversation_scope_hash: Sha256
    principal_scope_hash: Sha256
    capability_manifest_hash: Sha256
    policy_snapshot_hash: Sha256
    policy_coverage_sha256: Sha256 = "0" * 64
    intent_id: OpaqueId = "unspecified"
    intent_sha256: Sha256 = "0" * 64
    canonical_invocation_sha256: Sha256 = "0" * 64
    decision_id: OpaqueId
    decision_sha256: Sha256
    impact_id: OpaqueId
    impact_sha256: Sha256
    action_permission_sha256: Sha256
    component_manifest_hash: Sha256
    life_snapshot_revision: int = Field(ge=1)
    life_snapshot_hash: Sha256
    claim_sha256: Sha256 = "0" * 64
    claim_revision: int = Field(default=1, ge=1)
    claim_lease_epoch: int = Field(default=1, ge=1)
    confirmation_id: OpaqueId | None = None
    confirmation_sha256: Sha256 | None = None
    risk_class: RiskClass
    action_id: ActionId
    action_version: OpaqueId
    argument_schema_sha256: Sha256
    arguments_hash: Sha256
    workspace_id: OpaqueId
    input_objects: tuple[ObjectGrant, ...] = Field(default=(), max_length=256)
    object_grants_sha256: Sha256
    output_root_id: OpaqueId
    artifact_intent_id: OpaqueId | None = None
    max_output_bytes: int = Field(ge=0, le=2_147_483_648)
    max_runtime_ms: int = Field(ge=1, le=3_600_000)
    max_tool_calls: int = Field(ge=1, le=10_000)
    resource_envelope_sha256: Sha256
    allowed_side_effects: tuple[SideEffectClass, ...] = Field(max_length=6)
    side_effect_envelope_sha256: Sha256
    skill_id: OpaqueId | None = None
    skill_version: OpaqueId | None = None
    skill_sha256: Sha256 | None = None
    skill_activation_id: OpaqueId | None = None
    skill_activation_sha256: Sha256 | None = None
    composition_execution_binding: CompositionExecutionBindingV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator("allowed_side_effects")
    @classmethod
    def validate_sorted_side_effects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("ticket side effects must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_execution_scope(self) -> Self:
        if not self.issued_at_ms <= self.not_before_ms <= self.expires_at_ms:
            raise ValueError("ticket time window is invalid")
        if self.expires_at_ms - self.issued_at_ms > 60_000:
            raise ValueError("ExecutionTicket issue-to-expiry window exceeds 60 seconds")
        if self.risk_class == "A5":
            raise ValueError("A5 execution tickets are forbidden")
        confirmation_fields = (self.confirmation_id, self.confirmation_sha256)
        if any(value is not None for value in confirmation_fields):
            raise ValueError("A0-A4 execution tickets must not carry confirmation grants")

        skill_fields = (self.skill_id, self.skill_version, self.skill_sha256)
        if sum(value is not None for value in skill_fields) not in {0, 3}:
            raise ValueError("Skill binding must include id, version, and sha256 together")
        activation_fields = (self.skill_activation_id, self.skill_activation_sha256)
        if sum(value is not None for value in activation_fields) not in {0, 2}:
            raise ValueError("Skill activation binding is incomplete")
        if (self.skill_id is None) != (self.skill_activation_id is None):
            raise ValueError("Skill identity and activation grant must be bound together")

        binding = self.composition_execution_binding
        if binding is not None:
            if not binding.has_valid_sha256():
                raise ValueError("ticket composition binding digest is invalid")
            if (
                binding.request_id != self.request_id
                or binding.run_id != self.run_id
                or binding.generation != self.generation
                or binding.effect_id != self.effect_id
                or binding.action_id != self.action_id
                or binding.action_version != self.action_version
                or binding.canonical_invocation_sha256
                != self.canonical_invocation_sha256
                or binding.workspace_id != self.workspace_id
            ):
                raise ValueError("ticket composition binding does not match ticket scope")

        object_keys: list[tuple[str, int]] = []
        for item in self.input_objects:
            if item.tenant_id != self.tenant_id:
                raise ValueError("input object tenant does not match ticket tenant")
            if item.link_account_id != self.link_account_id:
                raise ValueError("input object account does not match ticket account")
            if item.conversation_scope_hash != self.conversation_scope_hash:
                raise ValueError("input object conversation scope does not match ticket scope")
            object_keys.append((item.object_id, item.revision))
        if tuple(object_keys) != tuple(sorted(set(object_keys))):
            raise ValueError("input object grants must be sorted and unique")
        if self.object_grants_sha256 != canonical_sha256(
            [item.model_dump(mode="json") for item in self.input_objects]
        ):
            raise ValueError("ticket object grant digest is invalid")
        if self.resource_envelope_sha256 != canonical_sha256(
            {
                "max_output_bytes": self.max_output_bytes,
                "max_runtime_ms": self.max_runtime_ms,
                "max_tool_calls": self.max_tool_calls,
            }
        ):
            raise ValueError("ticket resource envelope digest is invalid")
        if self.side_effect_envelope_sha256 != canonical_sha256(
            {"allowed_side_effects": list(self.allowed_side_effects)}
        ):
            raise ValueError("ticket side-effect envelope digest is invalid")
        return self


class ExecutionTicket(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ExecutionTicket",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    header: ExecutionTicketHeader
    payload: ExecutionTicketPayload
    signature: Base64UrlEd25519Signature


class ExecutionResult(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ExecutionResult",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    result_id: OpaqueId
    ticket_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    effect_id: EffectId
    action_id: ActionId
    action_version: OpaqueId
    status: Literal[
        "SUCCEEDED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "AMBIGUOUS",
        "CANCELLED",
        "FENCED",
    ]
    attempt: int = Field(ge=1)
    started_at_ms: int = Field(ge=0)
    finished_at_ms: int = Field(ge=0)
    side_effect_started: bool
    result_payload_sha256: Sha256
    receipt_sha256: Sha256 | None = None
    output_object_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    fact_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=256)
    error_code: ReasonCode | None = None
    error_message: str | None = Field(default=None, max_length=512)

    @field_validator("output_object_refs", "fact_ids")
    @classmethod
    def validate_sorted_unique_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("result reference fields must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.finished_at_ms < self.started_at_ms:
            raise ValueError("execution result finished before it started")
        if self.status == "SUCCEEDED":
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful result cannot carry an error")
        elif self.error_code is None:
            raise ValueError("non-success result must carry an error code")
        if self.status == "FAILED_RETRYABLE" and self.side_effect_started:
            raise ValueError("a started external side effect cannot be blindly retryable")
        if self.status == "AMBIGUOUS" and not self.side_effect_started:
            raise ValueError("ambiguous result requires a started side effect")
        if self.status in {"CANCELLED", "FENCED"} and self.side_effect_started:
            raise ValueError("a cancelled or fenced effect must not have started its side effect")
        if self.side_effect_started and self.receipt_sha256 is None:
            raise ValueError("a started side effect must carry receipt evidence")
        if not self.side_effect_started and self.receipt_sha256 is not None:
            raise ValueError("a result cannot claim side-effect receipt evidence before the side effect")
        return self


class FactRecord(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:FactRecord",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    fact_id: OpaqueId
    fact_type: Literal[
        "execution.succeeded",
        "execution.failed",
        "execution.ambiguous",
        "execution.cancelled",
        "execution.fenced",
        "artifact.qc_passed",
        "artifact.qc_failed",
    ]
    source_component_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    ticket_id: OpaqueId
    effect_id: EffectId
    action_id: ActionId
    action_version: OpaqueId
    observed_at_ms: int = Field(ge=0)
    payload_sha256: Sha256
    evidence_sha256: Sha256
    verification_method: Literal["component_receipt", "gateway_observation", "qc_result"]
    supersedes_fact_id: OpaqueId | None = None
    model_generated: Literal[False] = False
    fact_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"fact_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.fact_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"fact_sha256": self.computed_sha256()})


class ExecutionResultVNext(ContractModel):
    """vNext leaf execution result: evidence-based, never partial at leaf level."""

    schema_id: Literal["ExecutionResultVNext"] = "ExecutionResultVNext"
    schema_version: Literal["tiangong.execution_result.v3"] = "tiangong.execution_result.v3"
    result_id: OpaqueId
    ticket_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    effect_id: EffectId
    action_id: ActionId
    action_version: OpaqueId
    status: Literal[
        "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED", "FENCED",
    ]
    attempt: int = Field(ge=1)
    started_at_ms: int = Field(ge=0)
    finished_at_ms: int = Field(ge=0)
    side_effect_started: bool
    result_payload_sha256: Sha256
    dispatch_evidence_sha256: Sha256 | None = None
    remote_receipt_sha256: Sha256 | None = None
    authoritative_observation_sha256: Sha256 | None = None
    conclusive_remote_rejection_sha256: Sha256 | None = None
    proven_not_applied_sha256: Sha256 | None = None
    output_object_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    fact_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=256)
    error_code: ReasonCode | None = None
    error_message: str | None = Field(default=None, max_length=512)

    @field_validator("output_object_refs", "fact_ids")
    @classmethod
    def validate_sorted_unique_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("result reference fields must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.finished_at_ms < self.started_at_ms:
            raise ValueError("execution result finished before it started")
        if self.side_effect_started and self.dispatch_evidence_sha256 is None:
            raise ValueError("started side effect requires dispatch evidence")
        if self.status == "SUCCEEDED" and self.side_effect_started:
            if self.remote_receipt_sha256 is None and self.authoritative_observation_sha256 is None:
                raise ValueError("succeeded started effect requires receipt or authoritative observation")
        if self.status == "AMBIGUOUS":
            if not self.side_effect_started or self.dispatch_evidence_sha256 is None:
                raise ValueError("ambiguous result requires started side effect with dispatch evidence")
        if self.status == "FAILED_FINAL" and self.side_effect_started:
            if self.conclusive_remote_rejection_sha256 is None and self.proven_not_applied_sha256 is None:
                raise ValueError("failed started effect requires conclusive rejection or proven not applied")
        if self.status in {"FAILED_RETRYABLE", "CANCELLED", "FENCED"} and self.side_effect_started:
            raise ValueError("retryable/cancelled/fenced results must not have started the side effect")
        if self.status == "SUCCEEDED":
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("successful result cannot carry an error")
        elif self.error_code is None:
            raise ValueError("non-success result must carry an error code")
        return self


class EffectReconciliationRecord(ContractModel):
    """Append-only reconciliation observation for one effect."""

    schema_id: Literal["EffectReconciliationRecord"] = "EffectReconciliationRecord"
    schema_version: Literal["tiangong.effect_reconciliation_record.v1"] = "tiangong.effect_reconciliation_record.v1"
    reconciliation_id: OpaqueId
    effect_id: EffectId
    previous_outcome_head_sha256: Sha256
    attempt_no: int = Field(ge=1)
    strategy_id: OpaqueId
    observation_status: Literal["APPLIED", "PROVEN_NOT_APPLIED", "INCONCLUSIVE"]
    observation_ref: OpaqueId
    observed_at_ms: int = Field(ge=0)
    reconciliation_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"reconciliation_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.reconciliation_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"reconciliation_sha256": self.computed_sha256()})


class EffectOutcomeHead(ContractModel):
    """Mutable per-effect outcome head; only reconciliation may advance it."""

    schema_id: Literal["EffectOutcomeHead"] = "EffectOutcomeHead"
    schema_version: Literal["tiangong.effect_outcome_head.v1"] = "tiangong.effect_outcome_head.v1"
    effect_id: EffectId
    original_execution_result_ref: OpaqueId
    effective_status: Literal[
        "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED", "FENCED",
    ]
    head_revision: int = Field(ge=1)
    latest_reconciliation_ref: OpaqueId | None = None
    head_sha256: Sha256

    @staticmethod
    def reconcile_mapping(observation_status: str) -> str:
        if observation_status == "APPLIED":
            return "SUCCEEDED"
        if observation_status == "PROVEN_NOT_APPLIED":
            return "FAILED_RETRYABLE"
        return "AMBIGUOUS"


class CompositeExecutionOutcome(ContractModel):
    """Parent aggregate: machine-computed from child outcome heads only."""

    schema_id: Literal["CompositeExecutionOutcome"] = "CompositeExecutionOutcome"
    schema_version: Literal["tiangong.composite_execution_outcome.v1"] = "tiangong.composite_execution_outcome.v1"
    composite_execution_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    parent_effect_id: EffectId
    child_result_refs: tuple[OpaqueId, ...] = Field(min_length=1, max_length=256)
    compensation_effect_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    warning_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    status: Literal[
        "SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL_WITH_FAILURES",
        "FAILED", "RETRY_REQUIRED", "CANCELLED", "RECONCILE_REQUIRED",
    ]
    retry_required: bool
    summary_sha256: Sha256
    created_at_ms: int = Field(ge=0)
    composite_outcome_sha256: Sha256

    @field_validator("child_result_refs", "compensation_effect_refs", "warning_refs")
    @classmethod
    def validate_sorted_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("composite reference fields must be sorted and unique")
        return value

    @staticmethod
    def derive_status(
        child_statuses: tuple[str, ...],
        *,
        warning_refs: tuple[str, ...] = (),
    ) -> str:
        """Machine precedence from the spec; never caller-claimed."""
        statuses = set(child_statuses)
        if not child_statuses:
            raise ValueError("composite outcome requires at least one child")
        if "AMBIGUOUS" in statuses:
            return "RECONCILE_REQUIRED"
        if statuses <= {"SUCCEEDED"}:
            return "SUCCEEDED_WITH_WARNINGS" if warning_refs else "SUCCEEDED"
        if statuses <= {"CANCELLED", "FENCED"}:
            return "CANCELLED"
        if "SUCCEEDED" in statuses and statuses & {"FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED", "FENCED"}:
            return "PARTIAL_WITH_FAILURES"
        if "SUCCEEDED" not in statuses and "FAILED_FINAL" in statuses:
            return "FAILED"
        if "SUCCEEDED" not in statuses and "FAILED_FINAL" not in statuses and "FAILED_RETRYABLE" in statuses:
            return "RETRY_REQUIRED"
        return "FAILED"

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"composite_outcome_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.composite_outcome_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"composite_outcome_sha256": self.computed_sha256()})


__all__ = [
    "CapabilityAction",
    "CapabilityManifest",
    "CompositeExecutionOutcome",
    "CompositionExecutionBindingV1",
    "EffectOutcomeHead",
    "EffectReconciliationRecord",
    "ExecutionResult",
    "ExecutionResultVNext",
    "ExecutionTicket",
    "ExecutionTicketHeader",
    "ExecutionTicketPayload",
    "FactRecord",
    "ObjectGrant",
    "RiskClass",
    "SideEffectClass",
]
