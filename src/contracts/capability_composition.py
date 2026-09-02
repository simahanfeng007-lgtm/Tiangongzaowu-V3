"""Capability composition contracts for world-understanding-driven execution.

P1 contract freeze only.  This module is intentionally side-effect free: it does
not select capabilities, authorize actions, execute tools, mutate WorldState, or
write Memory.  Models propose candidate IDs; system authorities derive hashes,
versions, risk, permission, verification and execution bindings.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .models import ActionId, ContractModel, OpaqueId, RequestId, RunId, Sha256


CAPABILITY_COMPOSITION_SCHEMA = "tiangong.capability-composition.contracts.v1"

CapabilityDescriptorKind = Literal["TOOL_ACTION", "SKILL_METHOD"]
SourceKind = Literal["TOOL_ACTION", "SKILL_METHOD"]
ValidationState = Literal["PROVED_VALID", "PROVED_INVALID", "UNKNOWN"]
UnknownDisposition = Literal["NOT_APPLICABLE", "PROVISIONAL_ALLOW", "REJECT", "REVIEW"]
ExperienceOutcome = Literal["SUCCESS", "FAILURE", "PARTIAL", "RECONCILE"]
ExperienceLifecycle = Literal[
    "PROBATION",
    "STABLE",
    "STALE",
    "REVALIDATION_REQUIRED",
    "RETIRED",
]
AttributionState = Literal["PASS", "FAIL"]
RiskClass = Literal["A0", "A1", "A2", "A3", "A4", "A5"]


class SourceSpanRefV1(ContractModel):
    path: str = Field(min_length=1, max_length=1024)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("source span must be all-or-none")
        if self.start_line is not None and self.start_line > self.end_line:
            raise ValueError("source span is inverted")
        return self


class SourceRevisionRefV1(ContractModel):
    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    source_kind: SourceKind
    semantic_id: OpaqueId
    version: str = Field(min_length=1, max_length=80)
    source_files: tuple[str, ...] = Field(min_length=1)
    source_spans: tuple[SourceSpanRefV1, ...] = ()
    source_sha256: Sha256
    descriptor_sha256: Sha256
    manifest_sha256: Sha256 | None = None


class CapabilityDescriptorObservationV1(ContractModel):
    """Zero-execution observation consumed by World Understanding."""

    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    observation_id: OpaqueId
    descriptor_kind: CapabilityDescriptorKind
    semantic_id: OpaqueId
    source_revision: SourceRevisionRefV1
    observed_at_ms: int = Field(ge=0)
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    descriptor_sha256: Sha256


class ToolSourcePrimitiveV1(ContractModel):
    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    source_primitive_id: OpaqueId
    action_id: ActionId
    action_version: str = Field(min_length=1, max_length=80)
    provider_component_id: OpaqueId
    implementation_refs: tuple[SourceSpanRefV1, ...] = Field(min_length=1)
    implementation_hashes: tuple[Sha256, ...] = Field(min_length=1)
    action_manifest_sha256: Sha256
    argument_schema_sha256: Sha256
    result_schema_sha256: Sha256
    consumes: tuple[OpaqueId, ...] = ()
    produces: tuple[OpaqueId, ...] = ()
    effect_class: OpaqueId
    side_effects: tuple[OpaqueId, ...] = ()
    risk_floor: RiskClass
    idempotency: Literal["IDEMPOTENT", "NON_IDEMPOTENT", "CONDITIONAL", "UNKNOWN"]
    determinism_class: Literal["DETERMINISTIC", "BOUNDED_NONDETERMINISTIC", "NONDETERMINISTIC"]
    resource_scope: tuple[OpaqueId, ...] = ()
    read_set_descriptor: tuple[OpaqueId, ...] = ()
    write_set_descriptor: tuple[OpaqueId, ...] = ()
    evidence_contract: tuple[OpaqueId, ...] = ()
    verifier_refs: tuple[OpaqueId, ...] = ()
    failure_taxonomy: tuple[OpaqueId, ...] = ()
    availability: Literal["AVAILABLE", "UNAVAILABLE", "DEGRADED", "UNKNOWN"]
    descriptor_sha256: Sha256


class SkillSourcePrimitiveV1(ContractModel):
    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    method_id: OpaqueId
    version: str = Field(min_length=1, max_length=80)
    source_ref: SourceRevisionRefV1
    source_sha256: Sha256
    title: str = Field(min_length=1, max_length=240)
    semantic_summary: str = Field(min_length=1, max_length=4000)
    goal_classes: tuple[OpaqueId, ...] = Field(min_length=1)
    preconditions: tuple[OpaqueId, ...] = ()
    expected_postconditions: tuple[OpaqueId, ...] = ()
    required_capability_classes: tuple[OpaqueId, ...] = ()
    method_steps: tuple[OpaqueId, ...] = Field(min_length=1)
    control_flow_hints: tuple[OpaqueId, ...] = ()
    failure_modes: tuple[OpaqueId, ...] = ()
    fallback_patterns: tuple[OpaqueId, ...] = ()
    verification_intent: tuple[OpaqueId, ...] = ()
    composition_tags: tuple[OpaqueId, ...] = ()
    descriptor_sha256: Sha256


class ProposalStepV1(ContractModel):
    step_id: OpaqueId
    candidate_id: OpaqueId
    depends_on: tuple[OpaqueId, ...] = ()
    output_bindings: tuple[OpaqueId, ...] = ()


class CompositionProposalV1(ContractModel):
    """Small model ABI.  No authority-bearing fields are accepted here."""

    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    goal_ref: OpaqueId
    selected_method_candidate_ids: tuple[OpaqueId, ...] = ()
    selected_action_candidate_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    steps: tuple[ProposalStepV1, ...] = Field(min_length=1)
    dependency_edges: tuple[tuple[OpaqueId, OpaqueId], ...] = ()
    output_bindings: tuple[OpaqueId, ...] = ()
    control_flow: Literal["SEQUENTIAL", "DAG"] = "DAG"
    rationale_tags: tuple[OpaqueId, ...] = ()
    proposal_sha256: Sha256


class CompositionPlanStepV1(ContractModel):
    step_id: OpaqueId
    action_id: ActionId
    action_version: str = Field(min_length=1, max_length=80)
    method_id: OpaqueId | None = None
    depends_on: tuple[OpaqueId, ...] = ()
    expected_effect_refs: tuple[OpaqueId, ...] = ()
    verification_intent_refs: tuple[OpaqueId, ...] = ()


class CapabilityCompositionPlanV1(ContractModel):
    """System-compiled authoritative plan IR.  Never directly minted by a model."""

    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    plan_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=1)
    principal_scope_hash: Sha256
    world_state_ref: OpaqueId
    world_state_sha256: Sha256
    goal_fingerprint: Sha256
    environment_class: OpaqueId
    context_fingerprint_sha256: Sha256
    method_source_refs: tuple[SourceRevisionRefV1, ...] = ()
    action_source_refs: tuple[SourceRevisionRefV1, ...] = Field(min_length=1)
    steps: tuple[CompositionPlanStepV1, ...] = Field(min_length=1)
    dependency_graph_sha256: Sha256
    bindings_sha256: Sha256
    control_flow: Literal["SEQUENTIAL", "DAG"]
    expected_effects: tuple[OpaqueId, ...] = ()
    required_resource_classes: tuple[OpaqueId, ...] = ()
    permission_requirements: tuple[ActionId, ...] = Field(min_length=1)
    risk_floor: RiskClass
    composition_risk: RiskClass
    information_flow_findings: tuple[OpaqueId, ...] = ()
    source_manifest_sha256: Sha256
    capability_manifest_sha256: Sha256
    memory_experience_refs: tuple[OpaqueId, ...] = ()
    verification_intents: tuple[OpaqueId, ...] = ()
    created_at_ms: int = Field(ge=0)
    plan_sha256: Sha256


class CompositionValidationFindingV1(ContractModel):
    code: OpaqueId
    state: ValidationState
    subject_ref: OpaqueId
    detail_hash: Sha256 | None = None


class CompositionValidationResultV1(ContractModel):
    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    plan_id: OpaqueId
    plan_sha256: Sha256
    result: ValidationState
    unknown_disposition: UnknownDisposition = "NOT_APPLICABLE"
    findings: tuple[CompositionValidationFindingV1, ...] = ()
    mandatory_verification: bool = False
    validated_at_ms: int = Field(ge=0)
    validation_sha256: Sha256

    @model_validator(mode="after")
    def validate_unknown_policy(self) -> Self:
        if self.result != "UNKNOWN" and self.unknown_disposition != "NOT_APPLICABLE":
            raise ValueError("unknown disposition applies only to UNKNOWN results")
        if self.result == "UNKNOWN" and self.unknown_disposition == "NOT_APPLICABLE":
            raise ValueError("UNKNOWN result requires an explicit disposition")
        if self.unknown_disposition == "PROVISIONAL_ALLOW" and not self.mandatory_verification:
            raise ValueError("provisional allow requires mandatory verification")
        return self


class AttributionIntegrityV1(ContractModel):
    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=1)
    composition_plan_sha256: Sha256
    state: AttributionState
    reason_codes: tuple[OpaqueId, ...] = ()
    checked_lineage_sha256: Sha256
    checked_at_ms: int = Field(ge=0)
    attribution_sha256: Sha256


class CapabilityCombinationExperienceV1(ContractModel):
    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    experience_id: OpaqueId
    goal_class: OpaqueId
    environment_class: OpaqueId
    scene_fingerprint: Sha256
    context_fingerprint_sha256: Sha256
    method_source_refs: tuple[SourceRevisionRefV1, ...] = ()
    action_source_refs: tuple[SourceRevisionRefV1, ...] = Field(min_length=1)
    topology_sha256: Sha256
    composition_plan_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=1)
    completion_decision_sha256: Sha256
    verification_readiness_sha256: Sha256
    verification_record_refs: tuple[OpaqueId, ...] = ()
    terminal_fact_hashes: tuple[Sha256, ...] = ()
    outcome: ExperienceOutcome
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    independent_context_count: int = Field(ge=0)
    last_success_ms: int | None = Field(default=None, ge=0)
    last_failure_ms: int | None = Field(default=None, ge=0)
    posterior_success_milli: int = Field(ge=0, le=1000)
    lower_confidence_milli: int = Field(ge=0, le=1000)
    lifecycle: ExperienceLifecycle
    source_revision_family: OpaqueId
    exact_source_hashes: tuple[Sha256, ...] = Field(min_length=1)
    experience_sha256: Sha256


class CompositionActivationContractV1(ContractModel):
    """Request/run/generation-scoped binding consumed through the existing gateway."""

    schema_version: Literal[CAPABILITY_COMPOSITION_SCHEMA] = CAPABILITY_COMPOSITION_SCHEMA
    composition_activation_id: OpaqueId
    composition_plan_id: OpaqueId
    composition_plan_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=1)
    principal_scope_hash: Sha256
    world_state_sha256: Sha256
    source_manifest_sha256: Sha256
    capability_manifest_sha256: Sha256
    allowed_action_ids: tuple[ActionId, ...] = Field(min_length=1)
    allowed_action_versions: tuple[str, ...] = Field(min_length=1)
    verification_plan_ref: OpaqueId | None = None
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    activation_sha256: Sha256

    @model_validator(mode="after")
    def validate_activation(self) -> Self:
        if self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("activation must expire after issuance")
        if len(self.allowed_action_ids) != len(self.allowed_action_versions):
            raise ValueError("action ids and versions must have equal cardinality")
        return self


__all__ = [
    "CAPABILITY_COMPOSITION_SCHEMA",
    "AttributionIntegrityV1",
    "CapabilityCombinationExperienceV1",
    "CapabilityCompositionPlanV1",
    "CapabilityDescriptorObservationV1",
    "CompositionActivationContractV1",
    "CompositionProposalV1",
    "CompositionValidationResultV1",
    "SkillSourcePrimitiveV1",
    "SourceRevisionRefV1",
    "ToolSourcePrimitiveV1",
]
