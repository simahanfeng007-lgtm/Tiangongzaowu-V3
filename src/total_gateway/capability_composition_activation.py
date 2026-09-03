"""P7 activation gate for system-compiled capability composition plans.

This module extends the existing Total Gateway authority boundary. It does not
create a second gateway, policy engine, ticket issuer, grant issuer, runtime, or
completion authority. A model-produced proposal can reach this gate only after
system compilation and Tri-State validation. The gate then freezes an exact
activation and emits step requests that still require the existing
Policy/Ticket/Grant execution chain.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping, Protocol, runtime_checkable

from contracts import ActionRegistrySnapshot, canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionActivationContractV1,
    CompositionValidationResultV1,
)
from pydantic import Field

from contracts.models import ContractModel, OpaqueId, RequestId, RunId, Sha256
from world_understanding.capability_composition import (
    computed_validation_sha256,
    plan_has_valid_sha256,
    validation_has_valid_sha256,
)


ACTIVATION_ENVELOPE_SCHEMA = "tiangong.composition-activation-envelope.v1"
STEP_AUTHORIZATION_SCHEMA = "tiangong.composition-step-authorization.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CapabilityCompositionActivationError(RuntimeError):
    """Fail-closed activation error with a stable machine code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _activation_hash_field() -> str | None:
    for name in (
        "activation_sha256",
        "activation_contract_sha256",
        "contract_sha256",
    ):
        if name in CompositionActivationContractV1.model_fields:
            return name
    return None


def computed_activation_contract_sha256(
    contract: CompositionActivationContractV1,
) -> str:
    hash_field = _activation_hash_field()
    excluded = {hash_field} if hash_field is not None else set()
    return canonical_sha256(contract.model_dump(mode="json", exclude=excluded))


def activation_contract_has_valid_sha256(
    contract: CompositionActivationContractV1,
) -> bool:
    hash_field = _activation_hash_field()
    if hash_field is None:
        return False
    return getattr(contract, hash_field) == computed_activation_contract_sha256(
        contract
    )


def _required_field_names(model: type[ContractModel]) -> tuple[str, ...]:
    return tuple(
        name
        for name, field in model.model_fields.items()
        if field.is_required()
    )


def _build_frozen_activation_contract(
    *,
    plan: CapabilityCompositionPlanV1,
    validation: CompositionValidationResultV1,
    registry: ActionRegistrySnapshot,
    activation_id: str,
    issued_at_ms: int,
    expires_at_ms: int,
    mandatory_verification: bool,
) -> CompositionActivationContractV1:
    """Populate the frozen P1 activation ABI without model-supplied fields.

    The contract was frozen before P7. This constructor intentionally derives
    values by field name from system authorities, rejects any unknown required
    ABI field, and recomputes the contract digest after validation. This avoids
    introducing a second activation contract merely to fit P7.
    """

    action_ids = tuple(sorted(set(plan.permission_requirements)))
    source_refs = tuple(
        sorted(
            (*plan.method_source_refs, *plan.action_source_refs),
            key=lambda item: (
                item.source_kind,
                item.semantic_id,
                item.version,
                item.source_sha256,
                item.descriptor_sha256,
                item.manifest_sha256 or "",
            ),
        )
    )
    hash_field = _activation_hash_field()
    values: dict[str, Any] = {
        "activation_id": activation_id,
        "request_id": plan.request_id,
        "run_id": plan.run_id,
        "generation": plan.generation,
        "principal_scope_hash": plan.principal_scope_hash,
        "composition_plan_id": plan.plan_id,
        "composition_plan_sha256": plan.plan_sha256,
        "plan_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "validation_sha256": validation.validation_sha256,
        "composition_validation_sha256": validation.validation_sha256,
        "validation_result": validation.result,
        "unknown_disposition": validation.unknown_disposition,
        "world_state_ref": plan.world_state_ref,
        "world_state_sha256": plan.world_state_sha256,
        "context_fingerprint_sha256": plan.context_fingerprint_sha256,
        "goal_fingerprint": plan.goal_fingerprint,
        "environment_class": plan.environment_class,
        "source_manifest_sha256": plan.source_manifest_sha256,
        "capability_manifest_sha256": plan.capability_manifest_sha256,
        "action_registry_sha256": registry.registry_sha256,
        "allowed_action_ids": action_ids,
        "permission_requirements": action_ids,
        "source_revision_refs": source_refs,
        "method_source_refs": plan.method_source_refs,
        "action_source_refs": plan.action_source_refs,
        "verification_intents": plan.verification_intents,
        "verification_required": mandatory_verification,
        "mandatory_verification": mandatory_verification,
        "issued_at_ms": issued_at_ms,
        "activated_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
        "may_execute": False,
        "model_generated": False,
    }
    if hash_field is not None:
        values[hash_field] = "0" * 64

    payload: dict[str, Any] = {}
    missing: list[str] = []
    for name, field in CompositionActivationContractV1.model_fields.items():
        if name in values:
            payload[name] = values[name]
        elif field.is_required():
            missing.append(name)
    if missing:
        raise CapabilityCompositionActivationError(
            "composition.activation.abi_unmapped_required_fields",
            ",".join(sorted(missing)),
        )
    try:
        contract = CompositionActivationContractV1.model_validate(payload)
    except Exception as exc:  # Pydantic emits multiple concrete error classes.
        raise CapabilityCompositionActivationError(
            "composition.activation.contract_invalid", str(exc)
        ) from exc
    if hash_field is None:
        raise CapabilityCompositionActivationError(
            "composition.activation.hash_field_missing"
        )
    contract = contract.model_copy(
        update={hash_field: computed_activation_contract_sha256(contract)}
    )
    if not activation_contract_has_valid_sha256(contract):
        raise CapabilityCompositionActivationError(
            "composition.activation.hash_invalid"
        )
    return contract


class CompositionStepAuthorizationV1(ContractModel):
    """Non-executing request bound to one plan step and existing permission."""

    schema_version: str = STEP_AUTHORIZATION_SCHEMA
    step_authorization_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    principal_scope_hash: Sha256
    activation_id: OpaqueId
    activation_sha256: Sha256
    plan_id: OpaqueId
    plan_sha256: Sha256
    validation_sha256: Sha256
    step_id: OpaqueId
    action_id: str = Field(min_length=1, max_length=160)
    action_version: str = Field(min_length=1, max_length=160)
    action_permission_sha256: Sha256
    action_registry_sha256: Sha256
    capability_manifest_sha256: Sha256
    world_state_sha256: Sha256
    dependency_step_ids: tuple[OpaqueId, ...] = ()
    completed_dependency_step_ids: tuple[OpaqueId, ...] = ()
    arguments_sha256: Sha256
    object_grant_refs: tuple[OpaqueId, ...] = ()
    mandatory_verification: bool
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    authorization_sha256: Sha256
    requires_existing_policy_ticket_grant: bool = True
    may_execute: bool = False
    model_generated: bool = False

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"authorization_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.authorization_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CompositionStepAuthorizationV1":
        return self.model_copy(
            update={"authorization_sha256": self.computed_sha256()}
        )


class CompositionActivationEnvelopeV1(ContractModel):
    """System-owned activation plus exact current authority snapshots."""

    schema_version: str = ACTIVATION_ENVELOPE_SCHEMA
    activation_contract: CompositionActivationContractV1
    activation_id: OpaqueId
    activation_sha256: Sha256
    plan_id: OpaqueId
    plan_sha256: Sha256
    validation_sha256: Sha256
    action_registry_sha256: Sha256
    capability_manifest_sha256: Sha256
    world_state_sha256: Sha256
    allowed_action_ids: tuple[str, ...]
    mandatory_verification: bool
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    envelope_sha256: Sha256
    may_execute: bool = False
    model_generated: bool = False

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"envelope_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.envelope_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CompositionActivationEnvelopeV1":
        return self.model_copy(
            update={"envelope_sha256": self.computed_sha256()}
        )


@runtime_checkable
class ExistingGatewayStepPort(Protocol):
    """Port implemented by the already-authoritative Gateway execution seam."""

    def authorize_and_execute_composition_step(
        self,
        *,
        step_authorization: CompositionStepAuthorizationV1,
        arguments: Mapping[str, Any],
    ) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class CapabilityCompositionActivationAuthority:
    """P7 extension of Total Gateway; it owns no independent authority store."""

    registry: ActionRegistrySnapshot

    def __post_init__(self) -> None:
        if not self.registry.has_valid_sha256():
            raise CapabilityCompositionActivationError(
                "composition.activation.registry_hash_invalid"
            )

    def activate(
        self,
        plan: CapabilityCompositionPlanV1,
        validation: CompositionValidationResultV1,
        *,
        current_request_id: str,
        current_run_id: str,
        current_generation: int,
        current_principal_scope_hash: str,
        current_world_state_ref: str,
        current_world_state_sha256: str,
        issued_at_ms: int,
        expires_at_ms: int,
    ) -> CompositionActivationEnvelopeV1:
        if issued_at_ms < 0 or expires_at_ms <= issued_at_ms:
            raise CapabilityCompositionActivationError(
                "composition.activation.time_invalid"
            )
        if not plan_has_valid_sha256(plan):
            raise CapabilityCompositionActivationError(
                "composition.activation.plan_hash_invalid"
            )
        if not validation_has_valid_sha256(validation):
            raise CapabilityCompositionActivationError(
                "composition.activation.validation_hash_invalid"
            )
        if validation.validation_sha256 != computed_validation_sha256(validation):
            raise CapabilityCompositionActivationError(
                "composition.activation.validation_recompute_failed"
            )
        if (
            validation.plan_id != plan.plan_id
            or validation.plan_sha256 != plan.plan_sha256
        ):
            raise CapabilityCompositionActivationError(
                "composition.activation.validation_plan_mismatch"
            )
        if issued_at_ms < max(plan.created_at_ms, validation.validated_at_ms):
            raise CapabilityCompositionActivationError(
                "composition.activation.time_precedes_evidence"
            )
        expected_scope = (
            plan.request_id,
            plan.run_id,
            plan.generation,
            plan.principal_scope_hash,
            plan.world_state_ref,
            plan.world_state_sha256,
        )
        current_scope = (
            current_request_id,
            current_run_id,
            current_generation,
            current_principal_scope_hash,
            current_world_state_ref,
            current_world_state_sha256,
        )
        if expected_scope != current_scope:
            raise CapabilityCompositionActivationError(
                "composition.activation.scope_or_world_drift"
            )
        if plan.capability_manifest_sha256 != self.registry.source_manifest_sha256:
            raise CapabilityCompositionActivationError(
                "composition.activation.capability_manifest_drift"
            )
        if plan.composition_risk == "A5":
            raise CapabilityCompositionActivationError(
                "composition.activation.a5_forbidden"
            )

        provisional = (
            validation.result == "UNKNOWN"
            and validation.unknown_disposition == "PROVISIONAL_ALLOW"
            and validation.mandatory_verification
        )
        proved = validation.result == "PROVED_VALID"
        if not (proved or provisional):
            raise CapabilityCompositionActivationError(
                "composition.activation.validation_not_activatable"
            )
        mandatory_verification = bool(
            validation.mandatory_verification or provisional
        )

        permission_by_action = {
            item.action_id: item for item in self.registry.permissions
        }
        action_source_by_id = {
            item.semantic_id: item for item in plan.action_source_refs
        }
        step_actions = tuple(sorted({item.action_id for item in plan.steps}))
        if step_actions != tuple(sorted(set(plan.permission_requirements))):
            raise CapabilityCompositionActivationError(
                "composition.activation.permission_union_mismatch"
            )
        for step in plan.steps:
            permission = permission_by_action.get(step.action_id)
            source = action_source_by_id.get(step.action_id)
            if (
                permission is None
                or source is None
                or permission.action_version != step.action_version
                or source.version != step.action_version
                or permission.source_manifest_sha256
                != plan.capability_manifest_sha256
                or source.manifest_sha256
                != plan.capability_manifest_sha256
            ):
                raise CapabilityCompositionActivationError(
                    "composition.activation.action_binding_invalid",
                    step.action_id,
                )

        activation_id = "composition_activation_" + canonical_sha256(
            {
                "domain": "tiangong.composition-activation-id.v1",
                "request_id": plan.request_id,
                "run_id": plan.run_id,
                "generation": plan.generation,
                "principal_scope_hash": plan.principal_scope_hash,
                "plan_sha256": plan.plan_sha256,
                "validation_sha256": validation.validation_sha256,
                "registry_sha256": self.registry.registry_sha256,
                "world_state_sha256": plan.world_state_sha256,
                "issued_at_ms": issued_at_ms,
                "expires_at_ms": expires_at_ms,
            }
        )
        contract = _build_frozen_activation_contract(
            plan=plan,
            validation=validation,
            registry=self.registry,
            activation_id=activation_id,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            mandatory_verification=mandatory_verification,
        )
        activation_sha256 = computed_activation_contract_sha256(contract)
        envelope = CompositionActivationEnvelopeV1(
            activation_contract=contract,
            activation_id=activation_id,
            activation_sha256=activation_sha256,
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            validation_sha256=validation.validation_sha256,
            action_registry_sha256=self.registry.registry_sha256,
            capability_manifest_sha256=plan.capability_manifest_sha256,
            world_state_sha256=plan.world_state_sha256,
            allowed_action_ids=step_actions,
            mandatory_verification=mandatory_verification,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            envelope_sha256="0" * 64,
        )
        return envelope.with_computed_sha256()

    def authorize_step(
        self,
        activation: CompositionActivationEnvelopeV1,
        plan: CapabilityCompositionPlanV1,
        validation: CompositionValidationResultV1,
        *,
        step_id: str,
        completed_step_ids: tuple[str, ...],
        arguments_sha256: str,
        object_grant_refs: tuple[str, ...] = (),
        issued_at_ms: int,
        expires_at_ms: int,
    ) -> CompositionStepAuthorizationV1:
        if not activation.has_valid_sha256():
            raise CapabilityCompositionActivationError(
                "composition.step.activation_envelope_invalid"
            )
        if not activation_contract_has_valid_sha256(
            activation.activation_contract
        ):
            raise CapabilityCompositionActivationError(
                "composition.step.activation_contract_invalid"
            )
        if not plan_has_valid_sha256(plan) or not validation_has_valid_sha256(
            validation
        ):
            raise CapabilityCompositionActivationError(
                "composition.step.plan_or_validation_invalid"
            )
        if (
            activation.plan_id != plan.plan_id
            or activation.plan_sha256 != plan.plan_sha256
            or activation.validation_sha256 != validation.validation_sha256
            or activation.action_registry_sha256 != self.registry.registry_sha256
            or activation.capability_manifest_sha256
            != self.registry.source_manifest_sha256
        ):
            raise CapabilityCompositionActivationError(
                "composition.step.activation_binding_mismatch"
            )
        if not activation.issued_at_ms <= issued_at_ms <= activation.expires_at_ms:
            raise CapabilityCompositionActivationError(
                "composition.step.activation_expired_or_not_yet_valid"
            )
        if expires_at_ms <= issued_at_ms or expires_at_ms > activation.expires_at_ms:
            raise CapabilityCompositionActivationError(
                "composition.step.time_invalid"
            )
        if _SHA256.fullmatch(arguments_sha256) is None:
            raise CapabilityCompositionActivationError(
                "composition.step.arguments_hash_invalid"
            )
        if completed_step_ids != tuple(sorted(set(completed_step_ids))):
            raise CapabilityCompositionActivationError(
                "composition.step.completed_dependencies_not_canonical"
            )
        if object_grant_refs != tuple(sorted(set(object_grant_refs))):
            raise CapabilityCompositionActivationError(
                "composition.step.object_grants_not_canonical"
            )

        step_by_id = {item.step_id: item for item in plan.steps}
        step = step_by_id.get(step_id)
        if step is None:
            raise CapabilityCompositionActivationError(
                "composition.step.unknown_step", step_id
            )
        if step.action_id not in activation.allowed_action_ids:
            raise CapabilityCompositionActivationError(
                "composition.step.action_not_activated", step.action_id
            )
        if not set(step.depends_on).issubset(set(completed_step_ids)):
            raise CapabilityCompositionActivationError(
                "composition.step.dependencies_incomplete", step_id
            )
        permission = next(
            (
                item
                for item in self.registry.permissions
                if item.action_id == step.action_id
            ),
            None,
        )
        if (
            permission is None
            or permission.action_version != step.action_version
            or permission.source_manifest_sha256
            != activation.capability_manifest_sha256
            or not permission.has_valid_sha256()
        ):
            raise CapabilityCompositionActivationError(
                "composition.step.permission_binding_invalid", step.action_id
            )

        step_authorization_id = "composition_step_" + canonical_sha256(
            {
                "domain": "tiangong.composition-step-authorization-id.v1",
                "activation_sha256": activation.activation_sha256,
                "plan_sha256": plan.plan_sha256,
                "step_id": step.step_id,
                "action_id": step.action_id,
                "action_version": step.action_version,
                "arguments_sha256": arguments_sha256,
                "object_grant_refs": list(object_grant_refs),
                "issued_at_ms": issued_at_ms,
                "expires_at_ms": expires_at_ms,
            }
        )
        authorization = CompositionStepAuthorizationV1(
            step_authorization_id=step_authorization_id,
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
            principal_scope_hash=plan.principal_scope_hash,
            activation_id=activation.activation_id,
            activation_sha256=activation.activation_sha256,
            plan_id=plan.plan_id,
            plan_sha256=plan.plan_sha256,
            validation_sha256=validation.validation_sha256,
            step_id=step.step_id,
            action_id=step.action_id,
            action_version=step.action_version,
            action_permission_sha256=permission.permission_sha256,
            action_registry_sha256=self.registry.registry_sha256,
            capability_manifest_sha256=activation.capability_manifest_sha256,
            world_state_sha256=activation.world_state_sha256,
            dependency_step_ids=step.depends_on,
            completed_dependency_step_ids=completed_step_ids,
            arguments_sha256=arguments_sha256,
            object_grant_refs=object_grant_refs,
            mandatory_verification=activation.mandatory_verification,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            authorization_sha256="0" * 64,
        )
        return authorization.with_computed_sha256()

    def dispatch_via_existing_gateway(
        self,
        port: ExistingGatewayStepPort,
        authorization: CompositionStepAuthorizationV1,
        *,
        arguments: Mapping[str, Any],
    ) -> Any:
        """Delegate to the existing Gateway port; P7 never executes directly."""

        if not isinstance(port, ExistingGatewayStepPort):
            raise CapabilityCompositionActivationError(
                "composition.step.existing_gateway_port_required"
            )
        if not authorization.has_valid_sha256():
            raise CapabilityCompositionActivationError(
                "composition.step.authorization_hash_invalid"
            )
        if authorization.action_registry_sha256 != self.registry.registry_sha256:
            raise CapabilityCompositionActivationError(
                "composition.step.registry_drift"
            )
        if canonical_sha256(dict(arguments)) != authorization.arguments_sha256:
            raise CapabilityCompositionActivationError(
                "composition.step.arguments_mismatch"
            )
        return port.authorize_and_execute_composition_step(
            step_authorization=authorization,
            arguments=arguments,
        )


__all__ = [
    "ACTIVATION_ENVELOPE_SCHEMA",
    "STEP_AUTHORIZATION_SCHEMA",
    "CapabilityCompositionActivationAuthority",
    "CapabilityCompositionActivationError",
    "CompositionActivationEnvelopeV1",
    "CompositionStepAuthorizationV1",
    "ExistingGatewayStepPort",
    "activation_contract_has_valid_sha256",
    "computed_activation_contract_sha256",
]
