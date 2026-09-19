"""P12 R1C3: request-bound plan results into the existing P7 authorities.

This seam connects one request-bound ``SourceCompositionResult`` (R1C2) to the
ORIGINAL P7A shadow compiler and the existing ``GatewayStateStore``
registration + executable-plan sealing entry. It owns no Store, no second
registry, no Runtime, Policy, Ticket, Grant or Completion authority. A
registration is eligibility state only: admitting one plan never dispatches a
tool, never issues a Grant, and never claims task Completion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from contracts.verification import AcceptancePredicate, RegistrySnapshot

from world_understanding.capability_composition import (
    plan_has_valid_sha256,
    validation_has_valid_sha256,
)

from .composition_activation_shadow import (
    CompositionShadowActivationError,
    SystemVerificationBindingV1,
    build_system_verification_binding,
    propose_shadow_composition_activation,
)
from .composition_executable_plan import (
    FinalOutputAliasV1,
    PlanInputV1,
    StepExecutionBindingV1,
    WorkspaceBindingV1,
)
from .composition_source_preparation import (
    SourceCompositionResult,
    _planning_snapshot,
)


class CompositionRegistrationIntakeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class VerificationIntentEvidenceV1:
    """One system-resolved verification intent; never parsed from a model."""

    intent_ref: str
    predicate: AcceptancePredicate
    subject_identity: str
    evaluation_phase: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.intent_ref, str)
            or not self.intent_ref
            or not isinstance(self.subject_identity, str)
            or not self.subject_identity
            or not isinstance(self.evaluation_phase, str)
            or self.evaluation_phase not in {
                "POST_EXECUTION",
                "PRE_DELIVERY",
                "DELIVERY_FINALIZATION",
                "ASYNC_OBSERVATION",
            }
            or not isinstance(self.predicate, AcceptancePredicate)
            or not self.predicate.has_valid_identity()
        ):
            raise ValueError("COMPOSITION_INTENT_EVIDENCE_INVALID")


@dataclass(frozen=True, slots=True)
class RegisteredCompositionPlan:
    """Outcome of one intake registration; eligibility state only.

    The stages are deliberately distinct: ``compiled_and_validated`` reports
    that the R1C2 plan result was revalidated, ``registered`` that the original
    P7B registration and executable-plan sealing committed. ``granted``,
    ``executed`` and ``completed`` remain False forever on this type; the
    original Policy/Ticket/Grant/A5 chain owns those decisions.
    """

    registration_id: str
    executable_plan_id: str
    composition_activation_id: str
    verification_plan_id: str
    validation_mode: str
    created_by_this_call: bool
    idempotent_replay: bool
    compiled_and_validated: bool = True
    registered: bool = True
    granted: bool = False
    executed: bool = False
    completed: bool = False

    def __post_init__(self) -> None:
        if self.granted or self.executed or self.completed:
            raise ValueError("COMPOSITION_REGISTRATION_CANNOT_GRANT_OR_EXECUTE")
        if not (self.compiled_and_validated and self.registered):
            raise ValueError("COMPOSITION_REGISTRATION_STAGES_INVALID")
        if self.validation_mode not in {"PROVED_VALID", "PROVISIONAL_UNKNOWN"}:
            raise ValueError("COMPOSITION_REGISTRATION_MODE_INVALID")
        if self.created_by_this_call == self.idempotent_replay:
            raise ValueError("COMPOSITION_REGISTRATION_FLAGS_DISAGREE")


def register_source_composition(
    owner,
    result: SourceCompositionResult,
    *,
    verification_registry: RegistrySnapshot,
    intent_evidence: tuple[VerificationIntentEvidenceV1, ...],
    workspace: WorkspaceBindingV1,
    plan_inputs: tuple[PlanInputV1, ...],
    step_bindings: tuple[StepExecutionBindingV1, ...],
    final_output_aliases: tuple[FinalOutputAliasV1, ...],
    issued_at_ms: int,
    expires_at_ms: int,
    recorded_at_ms: int,
) -> RegisteredCompositionPlan:
    """Admit one request-bound plan result through the original P7 chain.

    No model field reaches this function: the plan result was compiled by the
    R1C2 request-bound path, and every admission input here (verifier
    registry, intent evidence, workspace, invocation bindings, clock) is
    supplied by the system or operator.
    """

    from .method_source_run_binding import MethodRunSourceResolver

    if (
        type(owner) is not MethodRunSourceResolver
        or type(result) is not SourceCompositionResult
    ):
        raise TypeError("COMPOSITION_REGISTRATION_SYSTEM_INPUTS_REQUIRED")

    preparation = result.preparation
    plan = result.plan
    validation = result.validation
    context = preparation.context
    registry = preparation.registry

    # Immutable identity of the admitted artifacts. A tampered or rehashed
    # result cannot present a consistent preparation/plan/validation binding.
    if not preparation.has_valid_sha256():
        raise CompositionRegistrationIntakeError(
            "intake.preparation.hash_invalid"
        )
    if not plan_has_valid_sha256(plan) or not validation_has_valid_sha256(
        validation
    ):
        raise CompositionRegistrationIntakeError("intake.p4.identity_invalid")
    if (
        plan.request_id != context.request_id
        or plan.run_id != context.run_id
        or plan.generation != context.generation
        or plan.principal_scope_hash != context.principal_scope_hash
        or plan.world_state_sha256 != context.world_state_sha256
        or plan.capability_manifest_sha256 != registry.source_manifest_sha256
        or validation.plan_id != plan.plan_id
        or validation.plan_sha256 != plan.plan_sha256
    ):
        raise CompositionRegistrationIntakeError("intake.plan.binding_mismatch")

    # Trivalent admission with the original P4 semantics preserved. The P7A
    # shadow compiler re-checks the same contract; this seam rejects early so
    # callers get the intake's own refusal code and the original findings.
    if validation.result == "PROVED_INVALID":
        raise CompositionRegistrationIntakeError(
            "intake.validation.proved_invalid",
            ",".join(
                sorted({finding.code for finding in validation.findings})
            ),
        )
    if validation.result == "UNKNOWN" and not (
        validation.unknown_disposition == "PROVISIONAL_ALLOW"
        and validation.mandatory_verification
    ):
        raise CompositionRegistrationIntakeError(
            "intake.validation.unknown_not_provisional"
        )

    # Mechanical verifier resolution. Every plan intent needs exactly one
    # system evidence row; the verifier itself is resolved by the existing
    # shadow builder and cannot be named by the caller.
    if type(intent_evidence) is not tuple:
        raise CompositionRegistrationIntakeError(
            "intake.verification_evidence.required"
        )
    by_intent: dict[str, VerificationIntentEvidenceV1] = {}
    for evidence in intent_evidence:
        if type(evidence) is not VerificationIntentEvidenceV1:
            raise CompositionRegistrationIntakeError(
                "intake.verification_evidence.invalid"
            )
        if evidence.intent_ref in by_intent:
            raise CompositionRegistrationIntakeError(
                "intake.verification_evidence.duplicate", evidence.intent_ref
            )
        by_intent[evidence.intent_ref] = evidence
    intents = tuple(plan.verification_intents)
    if not intents or set(by_intent) != set(intents):
        raise CompositionRegistrationIntakeError(
            "intake.verification_evidence.incomplete"
        )
    bindings: list[SystemVerificationBindingV1] = []
    for intent_ref in sorted(by_intent):
        evidence = by_intent[intent_ref]
        try:
            bindings.append(
                build_system_verification_binding(
                    intent_ref=intent_ref,
                    predicate=evidence.predicate,
                    subject_identity=evidence.subject_identity,
                    evaluation_phase=evidence.evaluation_phase,  # type: ignore[arg-type]
                    registry_snapshot=verification_registry,
                )
            )
        except (CompositionShadowActivationError, ValueError) as exc:
            raise CompositionRegistrationIntakeError(
                "intake.verification_binding.rejected", str(exc)
            ) from exc
    verification_bindings = tuple(bindings)

    # The sealed plan keeps the workspace identity the request was scoped to;
    # the executable contract separately re-derives the workspace authority.
    if (
        type(workspace) is not WorkspaceBindingV1
        or not workspace.has_valid_sha256()
        or workspace.workspace_id != preparation.workspace_id
    ):
        raise CompositionRegistrationIntakeError(
            "intake.workspace.binding_mismatch"
        )
    if (
        type(plan_inputs) is not tuple
        or type(step_bindings) is not tuple
        or type(final_output_aliases) is not tuple
    ):
        raise CompositionRegistrationIntakeError("intake.bindings.required")

    # Activation lifetime: the original P7A/P7B 60-second window, sealed at
    # the Store's own recorded time inside the single registration UoW.
    if (
        type(issued_at_ms) is not int
        or type(expires_at_ms) is not int
        or type(recorded_at_ms) is not int
        or not 0
        <= issued_at_ms
        < recorded_at_ms
        < expires_at_ms
        <= issued_at_ms + 60_000
    ):
        raise CompositionRegistrationIntakeError(
            "intake.activation.lifetime_invalid"
        )

    # Re-run the SAME request/world binding check the pre-Plan path used, so
    # a released generation, replaced request or moved world head refuses
    # admission instead of silently binding "latest".
    _planning_snapshot(
        owner,
        preparation.query,
        request_id=context.request_id,
        run_id=context.run_id,
        generation=context.generation,
        workspace_id=preparation.workspace_id,
    )

    # Original P7A shadow compiler: pure recompute, no persistence, no grant.
    try:
        shadow = propose_shadow_composition_activation(
            plan,
            validation,
            registry,
            verification_registry,
            verification_bindings,
            current_world_state_sha256=context.world_state_sha256,
            expected_principal_scope_hash=context.principal_scope_hash,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
        )
    except CompositionShadowActivationError as exc:
        raise CompositionRegistrationIntakeError(
            "intake.shadow.rejected", exc.code
        ) from exc

    # The one existing atomic registration + sealing + retention entry. Its
    # own authoritative rebuild, idempotency keys, request-binding recheck,
    # execution cutoff and method-source retention all stay in force.
    bundle = owner.gateway.register_executable_composition_plan_bundle(
        shadow,
        plan=plan,
        validation=validation,
        action_registry=registry,
        verification_registry=verification_registry,
        verification_bindings=verification_bindings,
        current_world_state_sha256=context.world_state_sha256,
        expected_principal_scope_hash=context.principal_scope_hash,
        composition_proposal=result.parse_outcome.proposal,
        candidates=preparation.candidates,
        compile_context=context,
        plan_inputs=plan_inputs,
        step_bindings=step_bindings,
        final_output_aliases=final_output_aliases,
        workspace=workspace,
        recorded_at_ms=recorded_at_ms,
    )

    registration = bundle.activation_bundle.record.registration
    executable = bundle.record.executable_plan
    return RegisteredCompositionPlan(
        registration_id=registration.registration_id,
        executable_plan_id=executable.executable_plan_id,
        composition_activation_id=registration.composition_activation_id,
        verification_plan_id=registration.verification_plan_id,
        validation_mode=registration.validation_mode,
        created_by_this_call=bundle.created_by_this_call,
        idempotent_replay=bundle.duplicate,
    )


__all__ = [
    "CompositionRegistrationIntakeError",
    "RegisteredCompositionPlan",
    "VerificationIntentEvidenceV1",
    "register_source_composition",
]
