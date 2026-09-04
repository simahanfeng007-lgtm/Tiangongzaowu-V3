"""Pure identity derivation shared by composition issuance and execution.

The helpers in this module read no Store, sign no contract, consume no nonce,
and dispatch no handler.  They keep the P7C issuer and P7D consumer on one
deterministic derivation for the pre-bound Effect and
``CompositionExecutionBindingV1``.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    CompositionExecutionBindingV1,
    canonical_sha256,
    derive_effect_identity,
    derive_run_identity,
)

from .composition_activation_adapter import MaterializedCompositionStepV1
from .composition_executable_plan import ExecutableCompositionPlanV1
from .composition_step_authorization import CompositionStepAuthorizationRequest
from .effects import EffectClaim


COMPOSITION_STEP_PIPELINE_VERSION = (
    "tiangong.composition-step-authorization.v1"
)
_RUN_SEQUENCE_PROBE_LIMIT = 256


class CompositionExecutionBindingError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DerivedCompositionExecutionBinding:
    run_sequence: int
    ordinal: int
    effect_intent_sha256: str
    effect_id: str
    binding: CompositionExecutionBindingV1


def derive_run_sequence(request_id: str, run_id: str) -> int:
    for candidate in range(1, _RUN_SEQUENCE_PROBE_LIMIT + 1):
        if derive_run_identity(request_id, candidate).run_id == run_id:
            return candidate
    raise CompositionExecutionBindingError("composition.run_identity.unbound")


def derive_composition_execution_binding(
    plan: ExecutableCompositionPlanV1,
    materialized: MaterializedCompositionStepV1,
    *,
    parent_ticket_id: str,
    workspace_id: str,
    workspace_scope_hash: str,
    target_snapshot_sha256: str | None,
    attempt: int = 1,
    continuation_delegation_id: str | None = None,
    continuation_delegation_sha256: str | None = None,
    dependency_evidence_sha256: str | None = None,
    supersedes_authorization_id: str | None = None,
    supersedes_effect_id: str | None = None,
    supersedes_claim_sha256: str | None = None,
) -> DerivedCompositionExecutionBinding:
    """Derive the exact signed binding from a live plan and actual invocation."""

    if (
        not isinstance(plan, ExecutableCompositionPlanV1)
        or not plan.has_valid_identity()
        or materialized.executable_plan_id != plan.executable_plan_id
        or materialized.executable_plan_sha256
        != plan.executable_plan_sha256
        or materialized.registration_id != plan.registration_id
        or materialized.request_id != plan.request_id
        or materialized.run_id != plan.run_id
        or materialized.generation != plan.generation
        or materialized.principal_scope_hash != plan.principal_scope_hash
        or workspace_id != plan.workspace.workspace_id
        or workspace_scope_hash != plan.workspace.workspace_scope_sha256
    ):
        raise CompositionExecutionBindingError(
            "composition.execution.plan_projection_mismatch"
        )
    steps = tuple(
        item for item in plan.step_bindings if item.step_id == materialized.step.step_id
    )
    if len(steps) != 1 or steps[0] != materialized.step:
        raise CompositionExecutionBindingError(
            "composition.execution.step_projection_mismatch"
        )
    ordinal = next(
        index + 1
        for index, item in enumerate(plan.step_bindings)
        if item.step_id == materialized.step.step_id
    )
    run_sequence = derive_run_sequence(plan.request_id, plan.run_id)
    materialized_arguments_sha256 = canonical_sha256(materialized.arguments)
    arguments_sha256 = canonical_sha256(
        {
            "action": materialized.step.action_id,
            "args": materialized.arguments,
            "target": materialized.target,
        }
    )
    target_sha256 = canonical_sha256(materialized.target)
    target_ref = (
        "target-"
        + canonical_sha256(
            {
                "action": materialized.step.action_id,
                "target": materialized.target,
            }
        )
        if materialized.target
        else None
    )
    canonical_invocation_sha256 = canonical_sha256(
        {
            "action_id": materialized.step.action_id,
            "action_version": materialized.step.action_version,
            "payload_sha256": materialized_arguments_sha256,
            "target_ref": target_ref,
            "workspace_id": workspace_id,
        }
    )
    continuation_values = (
        continuation_delegation_id,
        continuation_delegation_sha256,
        dependency_evidence_sha256,
    )
    predecessor_values = (
        supersedes_authorization_id,
        supersedes_effect_id,
        supersedes_claim_sha256,
    )
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise CompositionExecutionBindingError(
            "composition.execution.attempt_invalid"
        )
    continuation_bound = any(value is not None for value in continuation_values)
    if continuation_bound and any(value is None for value in continuation_values):
        raise CompositionExecutionBindingError(
            "composition.execution.continuation_incomplete"
        )
    if attempt == 1:
        if any(value is not None for value in predecessor_values):
            raise CompositionExecutionBindingError(
                "composition.execution.predecessor_unexpected"
            )
    elif (
        not continuation_bound
        or any(value is None for value in predecessor_values)
    ):
        raise CompositionExecutionBindingError(
            "composition.execution.predecessor_incomplete"
        )
    effect_intent = {
            "domain": (
                "tiangong.composition-step-effect-intent.v2"
                if continuation_bound
                else "tiangong.composition-step-effect-intent.v1"
            ),
            "parent_ticket_id": parent_ticket_id,
            "registration_id": plan.registration_id,
            "executable_plan_id": plan.executable_plan_id,
            "executable_plan_sha256": plan.executable_plan_sha256,
            "step_id": materialized.step.step_id,
            "step_binding_sha256": materialized.step.sha256,
            "action_id": materialized.step.action_id,
            "action_version": materialized.step.action_version,
            "arguments_sha256": arguments_sha256,
            "materialized_arguments_sha256": materialized_arguments_sha256,
            "target_sha256": target_sha256,
            "target_snapshot_sha256": target_snapshot_sha256,
            "workspace_id": workspace_id,
            "workspace_scope_hash": workspace_scope_hash,
            "request_id": plan.request_id,
            "run_id": plan.run_id,
            "generation": plan.generation,
        }
    if continuation_bound:
        effect_intent.update(
            {
                "attempt": attempt,
                "continuation_delegation_id": continuation_delegation_id,
                "continuation_delegation_sha256": continuation_delegation_sha256,
                "dependency_evidence_sha256": dependency_evidence_sha256,
                "supersedes_authorization_id": supersedes_authorization_id,
                "supersedes_effect_id": supersedes_effect_id,
                "supersedes_claim_sha256": supersedes_claim_sha256,
            }
        )
    effect_intent_sha256 = canonical_sha256(effect_intent)
    effect_id = derive_effect_identity(
        request_id=plan.request_id,
        run_id=plan.run_id,
        run_sequence=run_sequence,
        generation=plan.generation,
        effect_kind="execution",
        ordinal=ordinal,
        intent_sha256=effect_intent_sha256,
    ).effect_id
    binding = CompositionExecutionBindingV1(
        schema_version="tiangong.composition-execution-binding.v1",
        executable_plan_id=plan.executable_plan_id,
        executable_plan_sha256=plan.executable_plan_sha256,
        step_id=materialized.step.step_id,
        step_binding_sha256=materialized.step.sha256,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        effect_id=effect_id,
        action_id=materialized.step.action_id,
        action_version=materialized.step.action_version,
        materialized_arguments_sha256=materialized_arguments_sha256,
        canonical_invocation_sha256=canonical_invocation_sha256,
        target_sha256=target_sha256,
        target_snapshot_sha256=target_snapshot_sha256,
        workspace_id=workspace_id,
        workspace_scope_hash=workspace_scope_hash,
        attempt=(attempt if continuation_bound else None),
        continuation_delegation_id=continuation_delegation_id,
        continuation_delegation_sha256=continuation_delegation_sha256,
        dependency_evidence_sha256=dependency_evidence_sha256,
        supersedes_authorization_id=supersedes_authorization_id,
        supersedes_effect_id=supersedes_effect_id,
        supersedes_claim_sha256=supersedes_claim_sha256,
        binding_sha256="0" * 64,
    ).with_computed_sha256()
    return DerivedCompositionExecutionBinding(
        run_sequence=run_sequence,
        ordinal=ordinal,
        effect_intent_sha256=effect_intent_sha256,
        effect_id=effect_id,
        binding=binding,
    )


def rebuild_composition_effect_claim(
    request: CompositionStepAuthorizationRequest,
    *,
    run_sequence: int,
    ordinal: int,
    lease_epoch: int,
) -> EffectClaim:
    """Rebuild the claim signed into a P7C child ticket after restart."""

    if lease_epoch < 1:
        raise CompositionExecutionBindingError(
            "composition.execution.lease_epoch_invalid"
        )
    claim = EffectClaim(
        effect_id=request.prebound_effect_id,
        request_id=request.request_id,
        run_id=request.run_id,
        run_sequence=run_sequence,
        generation=request.generation,
        effect_kind="execution",
        ordinal=ordinal,
        intent_sha256=request.prebound_effect_intent_sha256,
        pipeline_version=COMPOSITION_STEP_PIPELINE_VERSION,
        attempt=request.attempt,
        claim_revision=request.attempt,
        lease_epoch=lease_epoch,
        supersedes_claim_sha256=request.supersedes_claim_sha256,
        owner_component_id="tiangong-backend",
        claimed_at_ms=request.issued_at_ms,
        claim_sha256="0" * 64,
    ).with_computed_sha256()
    return claim


__all__ = [
    "COMPOSITION_STEP_PIPELINE_VERSION",
    "CompositionExecutionBindingError",
    "DerivedCompositionExecutionBinding",
    "derive_composition_execution_binding",
    "derive_run_sequence",
    "rebuild_composition_effect_claim",
]
