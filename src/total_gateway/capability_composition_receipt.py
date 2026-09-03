"""P7 evidence receipt for the existing Policy/Ticket/Grant execution route.

The receipt is not a CompletionDecision. It proves that one activated step was
processed by the existing authority route and that its Effect/Fact evidence is
ready to return through P19. Only CompletionGate may make a terminal completion
claim.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from contracts import canonical_sha256
from contracts.models import ContractModel, OpaqueId, RequestId, RunId, Sha256

from .capability_composition_activation import (
    CapabilityCompositionActivationAuthority,
    CapabilityCompositionActivationError,
    CompositionStepAuthorizationV1,
)


STEP_EXECUTION_RECEIPT_SCHEMA = (
    "tiangong.composition-step-execution-receipt.v1"
)


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("receipt values must be sorted and unique")
    return value


class CompositionStepExecutionReceiptV1(ContractModel):
    schema_version: Literal[STEP_EXECUTION_RECEIPT_SCHEMA] = (
        STEP_EXECUTION_RECEIPT_SCHEMA
    )
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
    step_authorization_sha256: Sha256
    action_id: str = Field(min_length=1, max_length=160)
    action_version: str = Field(min_length=1, max_length=160)
    action_registry_sha256: Sha256
    capability_manifest_sha256: Sha256
    world_state_sha256: Sha256
    execution_route: Literal[
        "TOTAL_GATEWAY_POLICY_TICKET_GRANT_OMNI_RUNTIME"
    ] = "TOTAL_GATEWAY_POLICY_TICKET_GRANT_OMNI_RUNTIME"
    policy_decision_sha256: Sha256
    execution_ticket_sha256: Sha256
    capability_grant_sha256: Sha256
    effect_id: OpaqueId
    effect_sha256: Sha256
    fact_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    fact_hashes: tuple[Sha256, ...] = Field(min_length=1)
    verification_plan_sha256: Sha256
    p19_ingress_required: Literal[True] = True
    completion_claimed: Literal[False] = False
    executed_at_ms: int = Field(ge=0)
    receipt_sha256: Sha256
    model_generated: Literal[False] = False
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False

    _fact_ids = field_validator("fact_ids")(_sorted_unique)
    _fact_hashes = field_validator("fact_hashes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_fact_alignment(self):
        if len(self.fact_ids) != len(self.fact_hashes):
            raise ValueError("receipt Fact identities and hashes must align")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.receipt_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CompositionStepExecutionReceiptV1":
        return self.model_copy(
            update={"receipt_sha256": self.computed_sha256()}
        )


@runtime_checkable
class ExistingPolicyTicketGrantExecutionPort(Protocol):
    """Implemented only by the repository's existing Total Gateway seam."""

    def authorize_and_execute_composition_step(
        self,
        *,
        step_authorization: CompositionStepAuthorizationV1,
        arguments: Mapping[str, Any],
    ) -> CompositionStepExecutionReceiptV1:
        ...


def validate_step_execution_receipt(
    authorization: CompositionStepAuthorizationV1,
    receipt: CompositionStepExecutionReceiptV1,
    *,
    checked_at_ms: int,
) -> None:
    if checked_at_ms < authorization.issued_at_ms:
        raise CapabilityCompositionActivationError(
            "composition.receipt.check_time_invalid"
        )
    if not authorization.has_valid_sha256():
        raise CapabilityCompositionActivationError(
            "composition.receipt.authorization_hash_invalid"
        )
    if not isinstance(receipt, CompositionStepExecutionReceiptV1):
        raise CapabilityCompositionActivationError(
            "composition.receipt.contract_required"
        )
    if not receipt.has_valid_sha256():
        raise CapabilityCompositionActivationError(
            "composition.receipt.hash_invalid"
        )
    expected = (
        authorization.request_id,
        authorization.run_id,
        authorization.generation,
        authorization.principal_scope_hash,
        authorization.activation_id,
        authorization.activation_sha256,
        authorization.plan_id,
        authorization.plan_sha256,
        authorization.validation_sha256,
        authorization.step_id,
        authorization.authorization_sha256,
        authorization.action_id,
        authorization.action_version,
        authorization.action_registry_sha256,
        authorization.capability_manifest_sha256,
        authorization.world_state_sha256,
    )
    observed = (
        receipt.request_id,
        receipt.run_id,
        receipt.generation,
        receipt.principal_scope_hash,
        receipt.activation_id,
        receipt.activation_sha256,
        receipt.plan_id,
        receipt.plan_sha256,
        receipt.validation_sha256,
        receipt.step_id,
        receipt.step_authorization_sha256,
        receipt.action_id,
        receipt.action_version,
        receipt.action_registry_sha256,
        receipt.capability_manifest_sha256,
        receipt.world_state_sha256,
    )
    if expected != observed:
        raise CapabilityCompositionActivationError(
            "composition.receipt.binding_mismatch"
        )
    if not authorization.issued_at_ms <= receipt.executed_at_ms <= checked_at_ms:
        raise CapabilityCompositionActivationError(
            "composition.receipt.execution_time_invalid"
        )
    if receipt.completion_claimed:
        raise CapabilityCompositionActivationError(
            "composition.receipt.completion_claim_forbidden"
        )
    if not receipt.p19_ingress_required:
        raise CapabilityCompositionActivationError(
            "composition.receipt.p19_bypass_forbidden"
        )


def dispatch_and_verify_existing_gateway_step(
    authority: CapabilityCompositionActivationAuthority,
    port: ExistingPolicyTicketGrantExecutionPort,
    authorization: CompositionStepAuthorizationV1,
    *,
    arguments: Mapping[str, Any],
    checked_at_ms: int,
) -> CompositionStepExecutionReceiptV1:
    """Delegate once, then require Ticket/Grant/Effect/Fact/P19 evidence."""

    if not isinstance(port, ExistingPolicyTicketGrantExecutionPort):
        raise CapabilityCompositionActivationError(
            "composition.receipt.existing_gateway_port_required"
        )
    result = authority.dispatch_via_existing_gateway(
        port, authorization, arguments=arguments
    )
    if not isinstance(result, CompositionStepExecutionReceiptV1):
        raise CapabilityCompositionActivationError(
            "composition.receipt.contract_required"
        )
    validate_step_execution_receipt(
        authorization, result, checked_at_ms=checked_at_ms
    )
    return result


__all__ = [
    "STEP_EXECUTION_RECEIPT_SCHEMA",
    "CompositionStepExecutionReceiptV1",
    "ExistingPolicyTicketGrantExecutionPort",
    "dispatch_and_verify_existing_gateway_step",
    "validate_step_execution_receipt",
]
