"""P7B.1 limited-production composition activation registration seam.

This module converts one *authoritatively rebuilt* P7A shadow proposal into a
Gateway-owned, content-addressed registration and delegates the write to the
existing Gateway state-store authority through a narrow port.  A registration
is eligibility state only: it is not a PolicyDecision, permission, Grant,
Ticket, Runtime invocation, verification result, or Completion decision.
"""

from __future__ import annotations

from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, field_validator, model_validator

from contracts.canonical import canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionValidationResultV1,
)
from contracts.models import ContractModel, OpaqueId, RequestId, RunId, Sha256
from contracts.policy import ActionRegistrySnapshot
from contracts.verification import RegistrySnapshot
from total_gateway.composition_activation_shadow import (
    CompositionShadowActivationError,
    ShadowCompositionActivationProposalV1,
    SystemVerificationBindingV1,
    activation_has_valid_sha256,
    propose_shadow_composition_activation,
)


LIMITED_ACTIVATION_REGISTRATION_SCHEMA = (
    "tiangong.composition-limited-activation-registration.v1"
)
LIMITED_ACTIVATION_RECEIPT_SCHEMA = (
    "tiangong.composition-limited-activation-registration-receipt.v1"
)
EXISTING_GATEWAY_STATE_STORE_AUTHORITY = "EXISTING_GATEWAY_STATE_STORE"


class LimitedActivationRegistrationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError("set-like registration fields must be sorted and unique")
    return values


class LimitedCompositionActivationRegistrationV1(ContractModel):
    """Durable eligibility registration for the first A0/A1 production batch."""

    schema_version: Literal[LIMITED_ACTIVATION_REGISTRATION_SCHEMA] = (
        LIMITED_ACTIVATION_REGISTRATION_SCHEMA
    )
    registration_id: OpaqueId
    activation_mode: Literal["LIMITED_PRODUCTION"] = "LIMITED_PRODUCTION"
    shadow_proposal_sha256: Sha256
    differential_trace_sha256: Sha256
    composition_activation_id: OpaqueId
    composition_activation_sha256: Sha256
    composition_plan_id: OpaqueId
    composition_plan_sha256: Sha256
    verification_plan_id: OpaqueId
    verification_plan_sha256: Sha256
    validation_mode: Literal["PROVED_VALID", "PROVISIONAL_UNKNOWN"]
    validation_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    principal_scope_hash: Sha256
    world_state_sha256: Sha256
    source_manifest_sha256: Sha256
    capability_manifest_sha256: Sha256
    action_registry_sha256: Sha256
    verification_registry_sha256: Sha256
    allowed_action_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=30)
    allowed_action_versions: tuple[str, ...] = Field(min_length=1, max_length=30)
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    registered_at_ms: int = Field(ge=0)
    provisional_verification_required: bool
    writer_authority: Literal[
        "EXISTING_GATEWAY_STATE_STORE"
    ] = EXISTING_GATEWAY_STATE_STORE_AUTHORITY
    eligibility_only: Literal[True] = True
    authorizes: Literal[False] = False
    confirms: Literal[False] = False
    changes_risk: Literal[False] = False
    may_execute: Literal[False] = False
    registration_sha256: Sha256

    _action_ids = field_validator("allowed_action_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        if len(self.allowed_action_ids) != len(self.allowed_action_versions):
            raise ValueError("registered action ids and versions differ in cardinality")
        if not self.issued_at_ms <= self.registered_at_ms < self.expires_at_ms:
            raise ValueError("limited activation registration lifetime is invalid")
        if self.provisional_verification_required != (
            self.validation_mode == "PROVISIONAL_UNKNOWN"
        ):
            raise ValueError("provisional verification flag is inconsistent")
        return self

    def payload(self) -> dict[str, object]:
        """The complete immutable row payload, including first-write time."""

        return self.model_dump(
            mode="json",
            exclude={"registration_id", "registration_sha256"},
        )

    def authority_payload(self) -> dict[str, object]:
        """Stable authority content used to reconcile retries and write races.

        ``registered_at_ms`` is observation metadata chosen by the first
        successful Gateway Store write.  It must be covered by the row hash, but
        it must not create a second logical registration when a retry arrives at
        a later wall-clock time.
        """

        return self.model_dump(
            mode="json",
            exclude={
                "registration_id",
                "registration_sha256",
                "registered_at_ms",
            },
        )

    def computed_registration_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def computed_registration_id(self) -> str:
        # One logical registration per system-derived activation identity.  A
        # reused activation id with different authority content therefore maps
        # to the same key and is rejected as a collision instead of creating a
        # second record under a timestamp-dependent hash.
        return "car_" + canonical_sha256(
            {
                "domain": self.schema_version,
                "composition_activation_id": self.composition_activation_id,
                "request_id": self.request_id,
                "run_id": self.run_id,
                "generation": self.generation,
            }
        )

    def has_valid_identity(self) -> bool:
        return (
            self.registration_sha256 == self.computed_registration_sha256()
            and self.registration_id == self.computed_registration_id()
        )

    def has_same_authority(
        self, other: "LimitedCompositionActivationRegistrationV1"
    ) -> bool:
        return (
            self.registration_id == other.registration_id
            and self.authority_payload() == other.authority_payload()
        )

    def with_computed_identity(self) -> "LimitedCompositionActivationRegistrationV1":
        return self.model_copy(
            update={
                "registration_id": self.computed_registration_id(),
                "registration_sha256": self.computed_registration_sha256(),
            }
        )


class LimitedActivationRegistrationReceiptV1(ContractModel):
    schema_version: Literal[LIMITED_ACTIVATION_RECEIPT_SCHEMA] = (
        LIMITED_ACTIVATION_RECEIPT_SCHEMA
    )
    receipt_id: OpaqueId
    registration_id: OpaqueId
    registration_sha256: Sha256
    composition_activation_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    accepted: Literal[True] = True
    persisted: Literal[True] = True
    idempotent_replay: bool
    writer_authority: Literal[
        "EXISTING_GATEWAY_STATE_STORE"
    ] = EXISTING_GATEWAY_STATE_STORE_AUTHORITY
    authorizes: Literal[False] = False
    may_execute: Literal[False] = False
    recorded_at_ms: int = Field(ge=0)
    receipt_sha256: Sha256

    def payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"receipt_id", "receipt_sha256"}
        )

    def computed_receipt_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_identity(self) -> bool:
        if self.receipt_sha256 != self.computed_receipt_sha256():
            return False
        return self.receipt_id == "carr_" + canonical_sha256(
            {
                "domain": self.schema_version,
                "receipt_sha256": self.receipt_sha256,
            }
        )

    def with_computed_identity(self) -> "LimitedActivationRegistrationReceiptV1":
        digest = self.computed_receipt_sha256()
        return self.model_copy(
            update={
                "receipt_sha256": digest,
                "receipt_id": "carr_"
                + canonical_sha256(
                    {
                        "domain": self.schema_version,
                        "receipt_sha256": digest,
                    }
                ),
            }
        )


@runtime_checkable
class ExistingGatewayActivationRegistrationPort(Protocol):
    """Narrow single-writer port implemented only by the existing Gateway Store."""

    @property
    def authority_kind(self) -> str: ...

    def get_limited_activation_registration(
        self, registration_id: str
    ) -> LimitedCompositionActivationRegistrationV1 | None: ...

    def put_limited_activation_registration(
        self,
        registration: LimitedCompositionActivationRegistrationV1,
        *,
        expected_absent: bool,
        recorded_at_ms: int,
    ) -> bool: ...


def _rebuild_authoritative_shadow(
    proposal: ShadowCompositionActivationProposalV1,
    *,
    plan: CapabilityCompositionPlanV1,
    validation: CompositionValidationResultV1,
    action_registry: ActionRegistrySnapshot,
    verification_registry: RegistrySnapshot,
    verification_bindings: tuple[SystemVerificationBindingV1, ...],
    current_world_state_sha256: str,
    expected_principal_scope_hash: str,
) -> ShadowCompositionActivationProposalV1:
    """Re-run P7A from its authorities and require byte-equivalent output."""

    try:
        rebuilt = propose_shadow_composition_activation(
            plan,
            validation,
            action_registry,
            verification_registry,
            verification_bindings,
            current_world_state_sha256=current_world_state_sha256,
            expected_principal_scope_hash=expected_principal_scope_hash,
            issued_at_ms=proposal.activation_contract.issued_at_ms,
            expires_at_ms=proposal.activation_contract.expires_at_ms,
            legacy_allowed_action_ids=(
                proposal.differential_trace.legacy_allowed_action_ids
            ),
        )
    except CompositionShadowActivationError as exc:
        raise LimitedActivationRegistrationError(
            "limited_registration.authoritative_rebuild_failed", exc.code
        ) from exc
    if rebuilt != proposal:
        raise LimitedActivationRegistrationError(
            "limited_registration.shadow_rebuild_mismatch"
        )
    return rebuilt


def compile_limited_activation_registration(
    proposal: ShadowCompositionActivationProposalV1,
    *,
    plan: CapabilityCompositionPlanV1,
    validation: CompositionValidationResultV1,
    action_registry: ActionRegistrySnapshot,
    verification_registry: RegistrySnapshot,
    verification_bindings: tuple[SystemVerificationBindingV1, ...],
    current_world_state_sha256: str,
    expected_principal_scope_hash: str,
    registered_at_ms: int,
) -> LimitedCompositionActivationRegistrationV1:
    """Seal an authoritatively rebuilt P7A proposal into eligibility state."""

    if not proposal.has_valid_sha256():
        raise LimitedActivationRegistrationError(
            "limited_registration.proposal.hash_invalid"
        )
    proposal = _rebuild_authoritative_shadow(
        proposal,
        plan=plan,
        validation=validation,
        action_registry=action_registry,
        verification_registry=verification_registry,
        verification_bindings=verification_bindings,
        current_world_state_sha256=current_world_state_sha256,
        expected_principal_scope_hash=expected_principal_scope_hash,
    )
    if (
        not proposal.proposed_only
        or proposal.persistence_allowed
        or proposal.authorizes
        or proposal.confirms
        or proposal.changes_risk
        or proposal.may_execute
    ):
        raise LimitedActivationRegistrationError(
            "limited_registration.proposal.authority_invalid"
        )

    activation = proposal.activation_contract
    verification = proposal.verification_plan
    trace = proposal.differential_trace
    if not activation_has_valid_sha256(activation):
        raise LimitedActivationRegistrationError(
            "limited_registration.activation.hash_invalid"
        )
    if not verification.has_valid_identity():
        raise LimitedActivationRegistrationError(
            "limited_registration.verification_plan.invalid"
        )
    if not trace.has_valid_sha256():
        raise LimitedActivationRegistrationError(
            "limited_registration.trace.hash_invalid"
        )
    if not trace.limited_production_eligible or trace.limited_rejection_codes:
        raise LimitedActivationRegistrationError(
            "limited_registration.not_eligible"
        )
    if not all(
        (
            trace.exact_action_set,
            trace.registry_subset,
            trace.source_manifest_exact,
            trace.action_versions_exact,
            trace.verification_bindings_complete,
        )
    ):
        raise LimitedActivationRegistrationError(
            "limited_registration.trace.proof_incomplete"
        )
    if not activation.issued_at_ms <= registered_at_ms < activation.expires_at_ms:
        raise LimitedActivationRegistrationError(
            "limited_registration.activation.expired_or_not_yet_valid"
        )
    if (
        activation.verification_plan_ref != verification.verification_plan_id
        or activation.request_id != verification.request_id
        or activation.run_id != verification.run_id
        or activation.generation != verification.generation
        or verification.plan_sha256 != trace.verification_plan_sha256
        or activation.allowed_action_ids != trace.proposed_allowed_action_ids
        or proposal.validation_sha256 != trace.validation_sha256
        or proposal.action_registry_sha256 != trace.action_registry_sha256
        or proposal.verification_registry_sha256
        != trace.verification_registry_sha256
    ):
        raise LimitedActivationRegistrationError(
            "limited_registration.binding_mismatch"
        )

    value = LimitedCompositionActivationRegistrationV1(
        registration_id="car_" + "0" * 64,
        shadow_proposal_sha256=proposal.proposal_sha256,
        differential_trace_sha256=trace.trace_sha256,
        composition_activation_id=activation.composition_activation_id,
        composition_activation_sha256=activation.activation_sha256,
        composition_plan_id=activation.composition_plan_id,
        composition_plan_sha256=activation.composition_plan_sha256,
        verification_plan_id=verification.verification_plan_id,
        verification_plan_sha256=verification.plan_sha256,
        validation_mode=proposal.validation_mode,
        validation_sha256=proposal.validation_sha256,
        request_id=activation.request_id,
        run_id=activation.run_id,
        generation=activation.generation,
        principal_scope_hash=activation.principal_scope_hash,
        world_state_sha256=activation.world_state_sha256,
        source_manifest_sha256=activation.source_manifest_sha256,
        capability_manifest_sha256=activation.capability_manifest_sha256,
        action_registry_sha256=proposal.action_registry_sha256,
        verification_registry_sha256=proposal.verification_registry_sha256,
        allowed_action_ids=activation.allowed_action_ids,
        allowed_action_versions=activation.allowed_action_versions,
        issued_at_ms=activation.issued_at_ms,
        expires_at_ms=activation.expires_at_ms,
        registered_at_ms=registered_at_ms,
        provisional_verification_required=(
            proposal.validation_mode == "PROVISIONAL_UNKNOWN"
        ),
        registration_sha256="0" * 64,
    )
    return value.with_computed_identity()


class LimitedCompositionActivationRegistrar:
    """Idempotent registration service; never grants or executes capability."""

    def __init__(self, writer: ExistingGatewayActivationRegistrationPort) -> None:
        if not isinstance(writer, ExistingGatewayActivationRegistrationPort):
            raise TypeError("existing Gateway activation registration port required")
        if writer.authority_kind != EXISTING_GATEWAY_STATE_STORE_AUTHORITY:
            raise TypeError("writer is not the existing Gateway State Store authority")
        self._writer = writer

    def register(
        self,
        proposal: ShadowCompositionActivationProposalV1,
        *,
        plan: CapabilityCompositionPlanV1,
        validation: CompositionValidationResultV1,
        action_registry: ActionRegistrySnapshot,
        verification_registry: RegistrySnapshot,
        verification_bindings: tuple[SystemVerificationBindingV1, ...],
        current_world_state_sha256: str,
        expected_principal_scope_hash: str,
        recorded_at_ms: int,
    ) -> LimitedActivationRegistrationReceiptV1:
        candidate = compile_limited_activation_registration(
            proposal,
            plan=plan,
            validation=validation,
            action_registry=action_registry,
            verification_registry=verification_registry,
            verification_bindings=verification_bindings,
            current_world_state_sha256=current_world_state_sha256,
            expected_principal_scope_hash=expected_principal_scope_hash,
            registered_at_ms=recorded_at_ms,
        )
        persisted = candidate
        existing = self._writer.get_limited_activation_registration(
            candidate.registration_id
        )
        idempotent = False
        if existing is not None:
            if not existing.has_valid_identity() or not existing.has_same_authority(
                candidate
            ):
                raise LimitedActivationRegistrationError(
                    "limited_registration.identity_collision"
                )
            persisted = existing
            idempotent = True
        else:
            inserted = self._writer.put_limited_activation_registration(
                candidate,
                expected_absent=True,
                recorded_at_ms=recorded_at_ms,
            )
            if not inserted:
                raced = self._writer.get_limited_activation_registration(
                    candidate.registration_id
                )
                if (
                    raced is None
                    or not raced.has_valid_identity()
                    or not raced.has_same_authority(candidate)
                ):
                    raise LimitedActivationRegistrationError(
                        "limited_registration.write_conflict"
                    )
                persisted = raced
                idempotent = True

        receipt = LimitedActivationRegistrationReceiptV1(
            receipt_id="carr_" + "0" * 64,
            registration_id=persisted.registration_id,
            registration_sha256=persisted.registration_sha256,
            composition_activation_id=persisted.composition_activation_id,
            request_id=persisted.request_id,
            run_id=persisted.run_id,
            generation=persisted.generation,
            idempotent_replay=idempotent,
            recorded_at_ms=recorded_at_ms,
            receipt_sha256="0" * 64,
        )
        return receipt.with_computed_identity()


__all__ = [
    "EXISTING_GATEWAY_STATE_STORE_AUTHORITY",
    "ExistingGatewayActivationRegistrationPort",
    "LIMITED_ACTIVATION_RECEIPT_SCHEMA",
    "LIMITED_ACTIVATION_REGISTRATION_SCHEMA",
    "LimitedActivationRegistrationError",
    "LimitedActivationRegistrationReceiptV1",
    "LimitedCompositionActivationRegistrar",
    "LimitedCompositionActivationRegistrationV1",
    "compile_limited_activation_registration",
]
