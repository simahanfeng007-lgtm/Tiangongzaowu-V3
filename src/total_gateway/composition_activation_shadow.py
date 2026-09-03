"""P7A shadow-only composition activation adapter.

The adapter validates a system-compiled composition plan against the current
principal, WorldState, Action Registry, and P19 verifier RegistrySnapshot. It
emits proposed-only activation/verification records and a differential trace.
It never writes GatewayStateStore, issues a real Grant or Ticket, invokes
Runtime, runs a verifier, records PASS, or changes Completion.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from contracts.canonical import canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionActivationContractV1,
    CompositionValidationResultV1,
    SourceRevisionRefV1,
)
from contracts.models import ContractModel, OpaqueId, RequestId, RunId, Sha256
from contracts.policy import ActionRegistrySnapshot
from contracts.verification import (
    AcceptancePredicate,
    RegistrySnapshot,
    VerificationPlan,
    VerificationPlanEntryV2,
)
from world_understanding.capability_composition import (
    plan_has_valid_sha256,
    validation_has_valid_sha256,
)


SHADOW_ACTIVATION_SCHEMA = "tiangong.composition-shadow-activation.v1"
SYSTEM_VERIFICATION_BINDING_SCHEMA = (
    "tiangong.composition-system-verification-binding.v1"
)

EvaluationPhase = Literal[
    "POST_EXECUTION",
    "PRE_DELIVERY",
    "DELIVERY_FINALIZATION",
    "ASYNC_OBSERVATION",
]
AcceptedValidationMode = Literal["PROVED_VALID", "PROVISIONAL_UNKNOWN"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HIGH_RISK_SIDE_EFFECTS = frozenset(
    {
        "credential_read",
        "destructive",
        "external_send",
        "external_write",
        "irreversible",
    }
)


class CompositionShadowActivationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(
            "set-like shadow activation fields must be sorted and unique"
        )
    return values


def _require_sha256(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise CompositionShadowActivationError(
            "shadow.identity.sha256_invalid", field
        )


def _source_sort_key(
    source: SourceRevisionRefV1,
) -> tuple[str, str, str, str, str, str]:
    return (
        source.source_kind,
        source.semantic_id,
        source.version,
        source.source_sha256,
        source.descriptor_sha256,
        source.manifest_sha256 or "",
    )


def computed_activation_sha256(
    activation: CompositionActivationContractV1,
) -> str:
    return canonical_sha256(
        activation.model_dump(mode="json", exclude={"activation_sha256"})
    )


def activation_has_valid_sha256(
    activation: CompositionActivationContractV1,
) -> bool:
    return activation.activation_sha256 == computed_activation_sha256(
        activation
    )


def computed_composition_source_manifest_sha256(
    plan: CapabilityCompositionPlanV1,
) -> str:
    action_sources = tuple(
        sorted(plan.action_source_refs, key=_source_sort_key)
    )
    method_sources = tuple(
        sorted(plan.method_source_refs, key=_source_sort_key)
    )
    return canonical_sha256(
        {
            "domain": "tiangong.capability-composition.source-manifest.v1",
            "action_sources": [
                item.model_dump(mode="json") for item in action_sources
            ],
            "method_sources": [
                item.model_dump(mode="json") for item in method_sources
            ],
        }
    )


class SystemVerificationBindingV1(ContractModel):
    """One system-resolved intent-to-P19 binding; never supplied by a model."""

    schema_version: Literal[SYSTEM_VERIFICATION_BINDING_SCHEMA] = (
        SYSTEM_VERIFICATION_BINDING_SCHEMA
    )
    intent_ref: OpaqueId
    predicate: AcceptancePredicate
    verifier_id: OpaqueId
    verifier_version: str = Field(min_length=1, max_length=64)
    subject_identity: str = Field(min_length=1, max_length=400)
    evaluation_phase: EvaluationPhase
    required: Literal[True] = True
    registry_snapshot_sha256: Sha256
    producer_component_id: Literal["tiangong-gateway"] = (
        "tiangong-gateway"
    )
    model_generated: Literal[False] = False
    binding_sha256: Sha256

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if not self.predicate.has_valid_identity():
            raise ValueError(
                "verification binding predicate identity is invalid"
            )
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "SystemVerificationBindingV1":
        return self.model_copy(
            update={"binding_sha256": self.computed_sha256()}
        )


class ShadowActivationDifferentialTraceV1(ContractModel):
    schema_version: Literal[
        "tiangong.composition-shadow-activation-trace.v1"
    ] = "tiangong.composition-shadow-activation-trace.v1"
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    composition_plan_id: OpaqueId
    composition_plan_sha256: Sha256
    validation_sha256: Sha256
    action_registry_sha256: Sha256
    verification_registry_sha256: Sha256
    verification_plan_sha256: Sha256
    planned_action_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    proposed_allowed_action_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    legacy_allowed_action_ids: tuple[OpaqueId, ...] = ()
    added_vs_legacy: tuple[OpaqueId, ...] = ()
    removed_vs_legacy: tuple[OpaqueId, ...] = ()
    exact_action_set: Literal[True] = True
    registry_subset: Literal[True] = True
    source_manifest_exact: Literal[True] = True
    action_versions_exact: Literal[True] = True
    verification_bindings_complete: Literal[True] = True
    limited_production_eligible: bool
    limited_rejection_codes: tuple[OpaqueId, ...] = ()
    proposed_only: Literal[True] = True
    persisted: Literal[False] = False
    authorizes: Literal[False] = False
    may_execute: Literal[False] = False
    trace_sha256: Sha256

    _sets = field_validator(
        "planned_action_ids",
        "proposed_allowed_action_ids",
        "legacy_allowed_action_ids",
        "added_vs_legacy",
        "removed_vs_legacy",
        "limited_rejection_codes",
    )(_sorted_unique)

    @model_validator(mode="after")
    def validate_differential(self) -> Self:
        planned = set(self.planned_action_ids)
        proposed = set(self.proposed_allowed_action_ids)
        legacy = set(self.legacy_allowed_action_ids)
        if planned != proposed:
            raise ValueError("shadow proposed action set differs from plan")
        if set(self.added_vs_legacy) != proposed - legacy:
            raise ValueError("shadow added Action differential is invalid")
        if set(self.removed_vs_legacy) != legacy - proposed:
            raise ValueError("shadow removed Action differential is invalid")
        if self.limited_production_eligible != (
            not self.limited_rejection_codes
        ):
            raise ValueError("limited eligibility disagrees with reasons")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"trace_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.trace_sha256 == self.computed_sha256()

    def with_computed_sha256(
        self,
    ) -> "ShadowActivationDifferentialTraceV1":
        return self.model_copy(
            update={"trace_sha256": self.computed_sha256()}
        )


class ShadowCompositionActivationProposalV1(ContractModel):
    """Proposed-only P7A output. It is deliberately not a gateway grant."""

    schema_version: Literal[SHADOW_ACTIVATION_SCHEMA] = (
        SHADOW_ACTIVATION_SCHEMA
    )
    activation_contract: CompositionActivationContractV1
    verification_plan: VerificationPlan
    validation_mode: AcceptedValidationMode
    validation_sha256: Sha256
    action_registry_sha256: Sha256
    verification_registry_sha256: Sha256
    differential_trace: ShadowActivationDifferentialTraceV1
    proposed_only: Literal[True] = True
    persistence_allowed: Literal[False] = False
    authorizes: Literal[False] = False
    confirms: Literal[False] = False
    changes_risk: Literal[False] = False
    may_execute: Literal[False] = False
    proposal_sha256: Sha256

    @model_validator(mode="after")
    def validate_proposal(self) -> Self:
        activation = self.activation_contract
        verification = self.verification_plan
        trace = self.differential_trace
        if not activation_has_valid_sha256(activation):
            raise ValueError("shadow activation contract hash is invalid")
        if not verification.has_valid_identity():
            raise ValueError("shadow VerificationPlan identity is invalid")
        if not trace.has_valid_sha256():
            raise ValueError("shadow differential trace hash is invalid")
        if (
            activation.request_id != verification.request_id
            or activation.run_id != verification.run_id
            or activation.generation != verification.generation
            or activation.request_id != trace.request_id
            or activation.run_id != trace.run_id
            or activation.generation != trace.generation
            or activation.composition_plan_id != trace.composition_plan_id
            or activation.composition_plan_sha256
            != trace.composition_plan_sha256
            or activation.verification_plan_ref
            != verification.verification_plan_id
            or verification.plan_sha256 != trace.verification_plan_sha256
            or verification.registry_snapshot_sha256
            != self.verification_registry_sha256
            or self.validation_sha256 != trace.validation_sha256
            or self.action_registry_sha256 != trace.action_registry_sha256
            or self.verification_registry_sha256
            != trace.verification_registry_sha256
            or activation.allowed_action_ids
            != trace.proposed_allowed_action_ids
        ):
            raise ValueError(
                "shadow activation output bindings are inconsistent"
            )
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"proposal_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.proposal_sha256 == self.computed_sha256()

    def with_computed_sha256(
        self,
    ) -> "ShadowCompositionActivationProposalV1":
        return self.model_copy(
            update={"proposal_sha256": self.computed_sha256()}
        )


def build_system_verification_binding(
    *,
    intent_ref: str,
    predicate: AcceptancePredicate,
    subject_identity: str,
    evaluation_phase: EvaluationPhase,
    registry_snapshot: RegistrySnapshot,
) -> SystemVerificationBindingV1:
    """Resolve a verifier mechanically; callers cannot name the verifier."""

    if not registry_snapshot.has_valid_identity():
        raise CompositionShadowActivationError(
            "shadow.verification_registry.invalid"
        )
    if not predicate.has_valid_identity():
        raise CompositionShadowActivationError("shadow.predicate.invalid")
    eligible = tuple(
        descriptor
        for descriptor in registry_snapshot.verifiers
        if descriptor.has_valid_descriptor_sha256()
        and descriptor.layer == "L0_DETERMINISTIC"
        and descriptor.deterministic
        and predicate.predicate_type
        in descriptor.supported_predicate_types
        and predicate.subject_kind in descriptor.supported_subject_kinds
    )
    if len(eligible) != 1:
        raise CompositionShadowActivationError(
            "shadow.verifier_resolution.not_unique",
            f"intent={intent_ref};eligible={len(eligible)}",
        )
    descriptor = eligible[0]
    value = SystemVerificationBindingV1(
        intent_ref=intent_ref,
        predicate=predicate,
        verifier_id=descriptor.verifier_id,
        verifier_version=descriptor.verifier_version,
        subject_identity=subject_identity,
        evaluation_phase=evaluation_phase,
        registry_snapshot_sha256=registry_snapshot.snapshot_sha256,
        binding_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _validate_plan_and_registry(
    plan: CapabilityCompositionPlanV1,
    action_registry: ActionRegistrySnapshot,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not plan_has_valid_sha256(plan):
        raise CompositionShadowActivationError("shadow.plan.hash_invalid")
    if not action_registry.has_valid_sha256():
        raise CompositionShadowActivationError(
            "shadow.action_registry.hash_invalid"
        )
    if (
        plan.capability_manifest_sha256
        != action_registry.source_manifest_sha256
    ):
        raise CompositionShadowActivationError(
            "shadow.capability_manifest.mismatch"
        )
    if plan.source_manifest_sha256 != (
        computed_composition_source_manifest_sha256(plan)
    ):
        raise CompositionShadowActivationError(
            "shadow.source_manifest.hash_invalid"
        )

    planned_actions = tuple(
        sorted(set(step.action_id for step in plan.steps))
    )
    if (
        plan.permission_requirements
        != tuple(sorted(set(plan.permission_requirements)))
        or planned_actions != plan.permission_requirements
    ):
        raise CompositionShadowActivationError(
            "shadow.plan.action_set_inconsistent"
        )
    source_by_action = {
        source.semantic_id: source for source in plan.action_source_refs
    }
    if (
        len(source_by_action) != len(plan.action_source_refs)
        or tuple(sorted(source_by_action)) != planned_actions
    ):
        raise CompositionShadowActivationError(
            "shadow.plan.action_sources_incomplete"
        )
    permission_by_action = {
        permission.action_id: permission
        for permission in action_registry.permissions
    }
    versions: list[str] = []
    for action_id in planned_actions:
        permission = permission_by_action.get(action_id)
        source = source_by_action[action_id]
        step_versions = {
            step.action_version
            for step in plan.steps
            if step.action_id == action_id
        }
        if permission is None:
            raise CompositionShadowActivationError(
                "shadow.action.not_registered", action_id
            )
        if (
            step_versions != {permission.action_version}
            or source.source_kind != "TOOL_ACTION"
            or source.semantic_id != action_id
            or source.version != permission.action_version
            or source.manifest_sha256
            != action_registry.source_manifest_sha256
        ):
            raise CompositionShadowActivationError(
                "shadow.action.version_or_source_mismatch", action_id
            )
        versions.append(permission.action_version)
    return planned_actions, tuple(versions)


def _validate_validation(
    plan: CapabilityCompositionPlanV1,
    validation: CompositionValidationResultV1,
) -> AcceptedValidationMode:
    if not validation_has_valid_sha256(validation):
        raise CompositionShadowActivationError(
            "shadow.validation.hash_invalid"
        )
    if (
        validation.plan_id != plan.plan_id
        or validation.plan_sha256 != plan.plan_sha256
        or validation.validated_at_ms < plan.created_at_ms
    ):
        raise CompositionShadowActivationError(
            "shadow.validation.plan_binding_mismatch"
        )
    if validation.result == "PROVED_VALID":
        if validation.mandatory_verification:
            raise CompositionShadowActivationError(
                "shadow.validation.valid_flag_inconsistent"
            )
        return "PROVED_VALID"
    if (
        validation.result == "UNKNOWN"
        and validation.unknown_disposition == "PROVISIONAL_ALLOW"
        and validation.mandatory_verification
    ):
        return "PROVISIONAL_UNKNOWN"
    raise CompositionShadowActivationError(
        "shadow.validation.not_activatable", validation.result
    )


def _compile_verification_plan(
    plan: CapabilityCompositionPlanV1,
    registry_snapshot: RegistrySnapshot,
    bindings: tuple[SystemVerificationBindingV1, ...],
) -> VerificationPlan:
    if not registry_snapshot.has_valid_identity():
        raise CompositionShadowActivationError(
            "shadow.verification_registry.invalid"
        )
    intents = tuple(plan.verification_intents)
    if not intents or intents != tuple(sorted(set(intents))):
        raise CompositionShadowActivationError(
            "shadow.verification_intents.invalid"
        )
    binding_intents = tuple(binding.intent_ref for binding in bindings)
    if binding_intents != tuple(sorted(set(binding_intents))):
        raise CompositionShadowActivationError(
            "shadow.verification_bindings.order_or_duplicate"
        )
    if binding_intents != intents:
        raise CompositionShadowActivationError(
            "shadow.verification_bindings.incomplete"
        )

    entries: list[VerificationPlanEntryV2] = []
    for binding in bindings:
        if (
            not binding.has_valid_sha256()
            or binding.registry_snapshot_sha256
            != registry_snapshot.snapshot_sha256
            or binding.model_generated
        ):
            raise CompositionShadowActivationError(
                "shadow.verification_binding.invalid", binding.intent_ref
            )
        descriptor = registry_snapshot.find(binding.verifier_id)
        if (
            descriptor is None
            or not descriptor.has_valid_descriptor_sha256()
            or descriptor.verifier_version != binding.verifier_version
            or descriptor.layer != "L0_DETERMINISTIC"
            or not descriptor.deterministic
            or binding.predicate.predicate_type
            not in descriptor.supported_predicate_types
            or binding.predicate.subject_kind
            not in descriptor.supported_subject_kinds
        ):
            raise CompositionShadowActivationError(
                "shadow.verification_binding.registry_mismatch",
                binding.intent_ref,
            )
        entry = VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id=binding.verifier_id,
            verifier_version=binding.verifier_version,
            predicate=binding.predicate,
            subject_identity=binding.subject_identity,
            evaluation_phase=binding.evaluation_phase,
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()
        entries.append(entry)
    entries_tuple = tuple(
        sorted(entries, key=lambda item: item.plan_entry_id)
    )
    if len({entry.plan_entry_id for entry in entries_tuple}) != len(
        entries_tuple
    ):
        raise CompositionShadowActivationError(
            "shadow.verification_entries.duplicate"
        )
    verification_plan = VerificationPlan(
        verification_plan_id="vpl_" + "0" * 64,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        registry_snapshot_sha256=registry_snapshot.snapshot_sha256,
        entries=entries_tuple,
        plan_sha256="0" * 64,
    ).with_computed_sha256()
    if not verification_plan.has_valid_identity():
        raise CompositionShadowActivationError(
            "shadow.verification_plan.identity_invalid"
        )
    return verification_plan


def _limited_eligibility(
    *,
    plan: CapabilityCompositionPlanV1,
    action_registry: ActionRegistrySnapshot,
    verification_plan: VerificationPlan,
) -> tuple[bool, tuple[str, ...]]:
    permission_by_action = {
        permission.action_id: permission
        for permission in action_registry.permissions
    }
    reasons: set[str] = set()
    if plan.composition_risk not in {"A0", "A1"}:
        reasons.add("limited.composition_risk_not_a0_a1")
    for action_id in plan.permission_requirements:
        permission = permission_by_action[action_id]
        if permission.effective_risk not in {"A0", "A1"}:
            reasons.add("limited.action_risk_not_a0_a1")
        if permission.effect not in {"read", "verify"}:
            reasons.add("limited.effect_not_read_verify")
        if permission.allow_shell:
            reasons.add("limited.shell_forbidden")
        if permission.allow_python:
            reasons.add("limited.python_forbidden")
        if set(permission.allowed_side_effects) & _HIGH_RISK_SIDE_EFFECTS:
            reasons.add("limited.high_risk_side_effect")
    if not verification_plan.entries or any(
        not entry.required or not entry.has_valid_identity()
        for entry in verification_plan.entries
    ):
        reasons.add("limited.verification_incomplete")
    return not reasons, tuple(sorted(reasons))


def propose_shadow_composition_activation(
    plan: CapabilityCompositionPlanV1,
    validation: CompositionValidationResultV1,
    action_registry: ActionRegistrySnapshot,
    verification_registry: RegistrySnapshot,
    verification_bindings: tuple[SystemVerificationBindingV1, ...],
    *,
    current_world_state_sha256: str,
    expected_principal_scope_hash: str,
    issued_at_ms: int,
    expires_at_ms: int,
    legacy_allowed_action_ids: tuple[str, ...] = (),
) -> ShadowCompositionActivationProposalV1:
    """Create a fully checked proposal without persistence or authority."""

    _require_sha256(current_world_state_sha256, "current WorldState")
    _require_sha256(expected_principal_scope_hash, "principal scope")
    if plan.world_state_sha256 != current_world_state_sha256:
        raise CompositionShadowActivationError(
            "shadow.world_state.mismatch"
        )
    if plan.principal_scope_hash != expected_principal_scope_hash:
        raise CompositionShadowActivationError(
            "shadow.principal_scope.mismatch"
        )
    if not 0 <= issued_at_ms < expires_at_ms <= issued_at_ms + 60_000:
        raise CompositionShadowActivationError(
            "shadow.activation.lifetime_invalid"
        )
    validation_mode = _validate_validation(plan, validation)
    if issued_at_ms < max(
        plan.created_at_ms,
        validation.validated_at_ms,
        verification_registry.captured_at_ms,
    ):
        raise CompositionShadowActivationError(
            "shadow.activation.time_inverted"
        )
    if legacy_allowed_action_ids != tuple(
        sorted(set(legacy_allowed_action_ids))
    ):
        raise CompositionShadowActivationError(
            "shadow.legacy_action_set.invalid"
        )
    planned_actions, versions = _validate_plan_and_registry(
        plan, action_registry
    )
    verification_plan = _compile_verification_plan(
        plan, verification_registry, verification_bindings
    )
    limited_eligible, limited_reasons = _limited_eligibility(
        plan=plan,
        action_registry=action_registry,
        verification_plan=verification_plan,
    )

    activation_identity = canonical_sha256(
        {
            "domain": SHADOW_ACTIVATION_SCHEMA,
            "composition_plan_sha256": plan.plan_sha256,
            "validation_sha256": validation.validation_sha256,
            "action_registry_sha256": action_registry.registry_sha256,
            "verification_registry_sha256": (
                verification_registry.snapshot_sha256
            ),
            "verification_plan_sha256": verification_plan.plan_sha256,
            "current_world_state_sha256": current_world_state_sha256,
            "expected_principal_scope_hash": expected_principal_scope_hash,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
        }
    )
    activation = CompositionActivationContractV1(
        composition_activation_id="activation_" + activation_identity,
        composition_plan_id=plan.plan_id,
        composition_plan_sha256=plan.plan_sha256,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash,
        world_state_sha256=plan.world_state_sha256,
        source_manifest_sha256=plan.source_manifest_sha256,
        capability_manifest_sha256=plan.capability_manifest_sha256,
        allowed_action_ids=planned_actions,
        allowed_action_versions=versions,
        verification_plan_ref=verification_plan.verification_plan_id,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        activation_sha256="0" * 64,
    )
    activation = activation.model_copy(
        update={
            "activation_sha256": computed_activation_sha256(activation)
        }
    )

    legacy = set(legacy_allowed_action_ids)
    proposed = set(planned_actions)
    trace = ShadowActivationDifferentialTraceV1(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        composition_plan_id=plan.plan_id,
        composition_plan_sha256=plan.plan_sha256,
        validation_sha256=validation.validation_sha256,
        action_registry_sha256=action_registry.registry_sha256,
        verification_registry_sha256=(
            verification_registry.snapshot_sha256
        ),
        verification_plan_sha256=verification_plan.plan_sha256,
        planned_action_ids=planned_actions,
        proposed_allowed_action_ids=planned_actions,
        legacy_allowed_action_ids=legacy_allowed_action_ids,
        added_vs_legacy=tuple(sorted(proposed - legacy)),
        removed_vs_legacy=tuple(sorted(legacy - proposed)),
        limited_production_eligible=limited_eligible,
        limited_rejection_codes=limited_reasons,
        trace_sha256="0" * 64,
    ).with_computed_sha256()
    proposal = ShadowCompositionActivationProposalV1(
        activation_contract=activation,
        verification_plan=verification_plan,
        validation_mode=validation_mode,
        validation_sha256=validation.validation_sha256,
        action_registry_sha256=action_registry.registry_sha256,
        verification_registry_sha256=(
            verification_registry.snapshot_sha256
        ),
        differential_trace=trace,
        proposal_sha256="0" * 64,
    )
    return proposal.with_computed_sha256()


__all__ = [
    "CompositionShadowActivationError",
    "SHADOW_ACTIVATION_SCHEMA",
    "ShadowActivationDifferentialTraceV1",
    "ShadowCompositionActivationProposalV1",
    "SystemVerificationBindingV1",
    "activation_has_valid_sha256",
    "build_system_verification_binding",
    "computed_activation_sha256",
    "computed_composition_source_manifest_sha256",
    "propose_shadow_composition_activation",
]
