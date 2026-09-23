"""P12: mechanical admission-input materialization for a compiled plan.

Closes the gap the R1D delivery recorded: the admission evidence provider.
Given one ``SourceCompositionResult`` this helper mechanically derives the
FULL system side of the P7 admission inputs — the authority-derived
workspace binding, per-intent verifier evidence, per-step invocation
bindings (candidate/permission/schema/source revisions straight from the
sealed preparation) and the activation window. The caller supplies validated
semantic values, output selectors and task acceptance predicates through the
system channel. These values do not carry action, permission or execution
authority. Model proposal JSON cannot name an admission or verifier provider.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from contracts import canonical_sha256
from contracts.composition_profile import composition_profile_valid
from contracts.verification import AcceptancePredicate, RegistrySnapshot

from .composition_executable_plan import (
    ArgumentSlotV1,
    FinalOutputAliasV1,
    LiteralValueBindingV1,
    OutputDeclarationV1,
    PlanInputV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
    WorkspaceBindingV1,
)
from .composition_registration_intake import VerificationIntentEvidenceV1
from .composition_activation_shadow import build_system_verification_binding
from .composition_source_preparation import SourceCompositionResult
from world_understanding.capability_composition import plan_has_valid_sha256
from .verification_registry import VerifierRegistry

ZERO = "0" * 64


class AdmissionMaterializationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class StepOutputReference:
    """System-bound dataflow reference, not a dictionary parsed from a model."""

    producer_step_id: str
    output_binding_id: str


@dataclass(frozen=True, slots=True)
class StepOutputSelector:
    """System-selected extraction point in a committed tool result."""

    source_kind: str = "RESULT_PAYLOAD"
    json_pointer: str | None = "/result"
    ordinal: int | None = None
    value_schema_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class StepInvocationInputs:
    """Validated caller values for one compiled step; contains no authority.

    Candidate, action, schema and permission bindings are always taken from
    the sealed preparation. ``args`` may contain ``StepOutputReference``
    instances at complete leaves. A similarly shaped JSON object is a literal
    value, never an executable reference. Targets remain statically known, as
    required by the existing executable-plan contract.
    """

    target: str = ""
    args: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, StepOutputSelector] | None = None


def _hashed(value):
    return value.with_computed_sha256()


def materialize_workspace(workspace_root: Path) -> WorkspaceBindingV1:
    resolved = str(Path(workspace_root).resolve(strict=True))
    normalized = os.path.normcase(unicodedata.normalize("NFC", resolved))
    return _hashed(WorkspaceBindingV1(
        workspace_id="workspace-" + canonical_sha256(resolved),
        workspace_root=resolved,
        workspace_scope_sha256=canonical_sha256(
            {"normalized_workspace": normalized}),
        sha256=ZERO))


def materialize_verifier_evidence(
    result: SourceCompositionResult,
    registry_snapshot: RegistrySnapshot,
    *,
    subject_identity: str,
    intent_evidence: tuple[VerificationIntentEvidenceV1, ...] | None = None,
) -> tuple[VerificationIntentEvidenceV1, ...]:
    """Validate exact system intent bindings against the installed registry.

    The ``artifact.nonempty`` default is retained for legacy callers. A
    production adapter must supply the task's actual ``intent_evidence``;
    neither model-selected intent names nor this compatibility default prove
    business acceptance.
    """
    if intent_evidence is None:
        intent_evidence = tuple(VerificationIntentEvidenceV1(
            intent_ref=intent,
            predicate=AcceptancePredicate.create(
                predicate_type="artifact.nonempty",
                subject_kind="artifact", params={}),
            subject_identity=subject_identity,
            evaluation_phase="POST_EXECUTION")
            for intent in sorted(result.plan.verification_intents))
    if (type(intent_evidence) is not tuple
            or any(type(item) is not VerificationIntentEvidenceV1
                   for item in intent_evidence)):
        raise AdmissionMaterializationError("admission.verifier_evidence_invalid")
    refs = tuple(item.intent_ref for item in intent_evidence)
    if (not refs or len(set(refs)) != len(refs)
            or set(refs) != set(result.plan.verification_intents)):
        raise AdmissionMaterializationError("admission.verifier_evidence_incomplete")
    for item in intent_evidence:
        try:
            build_system_verification_binding(
                intent_ref=item.intent_ref,
                predicate=item.predicate,
                subject_identity=item.subject_identity,
                evaluation_phase=item.evaluation_phase,
                registry_snapshot=registry_snapshot)
        except ValueError as exc:
            raise AdmissionMaterializationError(
                "admission.verifier_evidence_rejected", str(exc)) from exc
    return tuple(sorted(intent_evidence, key=lambda item: item.intent_ref))


def materialize_step_bindings(
    result: SourceCompositionResult,
    *,
    user_inputs: Mapping[str, Any],
    step_inputs: Mapping[str, StepInvocationInputs] | None = None,
    final_outputs: Mapping[str, StepOutputReference] | None = None,
    execution_profile_id: str | None = None,
    execution_profile_sha256: str | None = None,
) -> tuple[tuple[PlanInputV1, ...], tuple[StepExecutionBindingV1, ...],
           tuple[FinalOutputAliasV1, ...]]:
    """Derive invocation bindings without assigning authority to argument data.

    Existing ``user_inputs`` callers keep their shared argument values. New
    production callers use ``step_inputs`` with exact compiled step coverage
    so each action receives only its own target and arguments. The two input
    forms cannot be combined. Typed output references require an explicit
    preceding dependency and are sealed to the producer declaration hash.
    The original executable-plan and dispatch validators still validate
    permissions, workspace containment, complete dataflow and action schemas.
    """
    if not composition_profile_valid(execution_profile_id, execution_profile_sha256):
        raise AdmissionMaterializationError("admission.execution_profile_invalid")
    preparation = result.preparation
    proposal = result.parse_outcome.proposal
    if proposal is None or not result.plan.steps:
        raise AdmissionMaterializationError("admission.proposal_missing")
    if step_inputs is not None and user_inputs:
        raise AdmissionMaterializationError("admission.input_modes_conflict")
    plan_ids = tuple(item.step_id for item in result.plan.steps)
    if step_inputs is not None and (
            set(step_inputs) != set(plan_ids)
            or any(type(item) is not StepInvocationInputs
                   for item in step_inputs.values())):
        raise AdmissionMaterializationError("admission.step_inputs_incomplete")
    proposed_by_id = {item.step_id: item for item in proposal.steps}
    if set(proposed_by_id) != set(plan_ids):
        raise AdmissionMaterializationError("admission.step_identity_mismatch")
    candidates_by_id = preparation.candidates.action_by_candidate()
    permission_by_action = {
        item.action_id: item for item in preparation.registry.permissions}
    steps: list[StepExecutionBindingV1] = []
    all_inputs: list[PlanInputV1] = []
    output_index: dict[tuple[str, str], OutputDeclarationV1] = {}

    def output_reference(value, *, consumer=None):
        if type(value) is not StepOutputReference:
            raise AdmissionMaterializationError("admission.output_reference_invalid")
        declaration = output_index.get(
            (value.producer_step_id, value.output_binding_id))
        if declaration is None or (consumer is not None and
                value.producer_step_id not in consumer.depends_on):
            raise AdmissionMaterializationError(
                "admission.output_dependency_missing", value.producer_step_id)
        return _hashed(StepOutputValueBindingV1(
            producer_step_id=value.producer_step_id,
            output_binding_id=value.output_binding_id,
            output_declaration_sha256=declaration.sha256,
            sha256=ZERO))

    def pointer_token(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    for plan_step in result.plan.steps:
        proposed = proposed_by_id[plan_step.step_id]
        candidate = candidates_by_id.get(proposed.candidate_id)
        if candidate is None:
            raise AdmissionMaterializationError(
                "admission.candidate_missing", plan_step.step_id)
        primitive = candidate.primitive
        permission = permission_by_action.get(primitive.action_id)
        if permission is None:
            raise AdmissionMaterializationError(
                "admission.permission_missing", primitive.action_id)
        supplied = (StepInvocationInputs(args=user_inputs)
                    if step_inputs is None else step_inputs[plan_step.step_id])
        if not isinstance(supplied.target, str) or not isinstance(supplied.args, Mapping):
            raise AdmissionMaterializationError("admission.step_inputs_invalid")
        slots: list[ArgumentSlotV1] = []

        def literal_slot(value, pointer):
            # These values are static invocation literals, not separately
            # schema-addressed external inputs. Seal them with the existing
            # literal binding contract; the complete invocation still passes
            # the action's explicit schema and path policy before dispatch.
            slots.append(_hashed(ArgumentSlotV1(
                destination_json_pointer=pointer,
                value_binding=_hashed(LiteralValueBindingV1(
                    value=dict(value) if isinstance(value, Mapping) else value,
                    sha256=ZERO)), sha256=ZERO)))
            return None

        def walk(value, pointer):
            if type(value) is StepOutputReference:
                slots.append(_hashed(ArgumentSlotV1(
                    destination_json_pointer=pointer,
                    value_binding=output_reference(value, consumer=plan_step),
                    sha256=ZERO)))
                return None
            if isinstance(value, Mapping) and value:
                if any(not isinstance(key, str) for key in value):
                    raise AdmissionMaterializationError("admission.argument_key_invalid")
                skeleton = {}
                for key in sorted(value):
                    skeleton[key] = walk(
                        value[key], pointer + "/" + pointer_token(key))
                return skeleton
            if isinstance(value, list) and value:
                return [walk(item, pointer + "/" + str(index))
                        for index, item in enumerate(value)]
            return literal_slot(value, pointer)

        if step_inputs is None:
            # Keep legacy whole-value argument semantics, but bind supplied
            # constants as literals rather than inventing a zero-hash schema.
            skeleton = {}
            for name in sorted(user_inputs):
                skeleton[name] = literal_slot(
                    user_inputs[name], "/" + pointer_token(name))
        else:
            skeleton = {}
            if any(not isinstance(key, str) for key in supplied.args):
                raise AdmissionMaterializationError("admission.argument_key_invalid")
            for key in sorted(supplied.args):
                skeleton[key] = walk(
                    supplied.args[key], "/" + pointer_token(key))
        if supplied.outputs is None:
            if len(proposed.output_bindings) > 1:
                raise AdmissionMaterializationError(
                    "admission.output_selectors_required", plan_step.step_id)
            selectors = {key: StepOutputSelector()
                         for key in proposed.output_bindings}
        else:
            selectors = supplied.outputs
        if (set(selectors) != set(proposed.output_bindings)
                or any(type(item) is not StepOutputSelector
                       for item in selectors.values())):
            raise AdmissionMaterializationError("admission.output_selectors_incomplete")
        outputs = tuple(_hashed(OutputDeclarationV1(
                output_binding_id=binding_id,
                source_kind=selectors[binding_id].source_kind,
                json_pointer=selectors[binding_id].json_pointer,
                ordinal=selectors[binding_id].ordinal,
                value_schema_sha256=(primitive.result_schema_sha256
                    if selectors[binding_id].value_schema_sha256 is None
                    else selectors[binding_id].value_schema_sha256),
                sha256=ZERO))
            for binding_id in sorted(proposed.output_bindings))
        steps.append(_hashed(StepExecutionBindingV1(
            step_id=plan_step.step_id,
            candidate_id=proposed.candidate_id,
            candidate_binding_sha256=candidate.binding_sha256,
            action_id=plan_step.action_id,
            action_version=plan_step.action_version,
            source_revision=candidate.source_revision,
            argument_schema_sha256=primitive.argument_schema_sha256,
            result_schema_sha256=primitive.result_schema_sha256,
            permission=permission,
            permission_sha256=permission.permission_sha256,
            execution_profile_id=execution_profile_id,
            execution_profile_sha256=execution_profile_sha256,
            depends_on=plan_step.depends_on,
            target_skeleton=supplied.target,
            args_skeleton=skeleton,
            argument_slots=tuple(sorted(
                slots, key=lambda slot: slot.destination_json_pointer)),
            output_declarations=outputs,
            sha256=ZERO)))
        for declaration in outputs:
            output_index[(plan_step.step_id, declaration.output_binding_id)] = declaration
    if final_outputs is None:
        if len(proposal.output_bindings) != 1 or not steps[-1].output_declarations:
            raise AdmissionMaterializationError("admission.final_outputs_required")
        final_outputs = {proposal.output_bindings[0]: StepOutputReference(
            steps[-1].step_id, steps[-1].output_declarations[-1].output_binding_id)}
    if set(final_outputs) != set(proposal.output_bindings):
        raise AdmissionMaterializationError("admission.final_outputs_incomplete")
    aliases = tuple(_hashed(FinalOutputAliasV1(
        alias=alias, value_binding=output_reference(value), sha256=ZERO))
        for alias, value in sorted(final_outputs.items()))
    # Fail before registration on unused outputs, missing edges, or a malformed
    # typed reference. This is the same authority-free dataflow contract used
    # by executable-plan sealing, not an alternative validator.
    from .composition_executable_plan import _validate_dataflow
    ordered_inputs = tuple(sorted(all_inputs, key=lambda item: item.input_id))
    _validate_dataflow(plan_inputs=ordered_inputs, step_bindings=tuple(steps),
                       final_output_aliases=aliases)
    return ordered_inputs, tuple(steps), aliases


def materialize_admission_inputs(
    result: SourceCompositionResult,
    *,
    workspace_root: Path,
    user_inputs: Mapping[str, Any],
    step_inputs: Mapping[str, StepInvocationInputs] | None = None,
    final_outputs: Mapping[str, StepOutputReference] | None = None,
    intent_evidence: tuple[VerificationIntentEvidenceV1, ...] | None = None,
    subject_identity: str = "object:composition-turn",
    execution_profile_id: str | None = None,
    execution_profile_sha256: str | None = None,
    issued_at_ms: int,
    expires_at_ms: int,
) -> dict:
    """The full system-side admission inputs for one compiled result.

    All authority derives from the sealed preparation and the operator-pinned
    workspace. Semantic values, selectors and predicate evidence must already
    have been validated by the calling system adapter. This function neither
    parses model text nor infers task acceptance from model-selected names.
    """
    if not isinstance(result, SourceCompositionResult) \
            or not result.preparation.has_valid_sha256() \
            or not plan_has_valid_sha256(result.plan):
        raise AdmissionMaterializationError("admission.result_invalid")
    from .composition_admission_lifetime import composition_admission_lifetime_ms
    try:
        lifetime_ms = composition_admission_lifetime_ms(
            (step.action_id for step in result.plan.steps),
            execution_profile_id=execution_profile_id,
            execution_profile_sha256=execution_profile_sha256)
    except ValueError as exc:
        raise AdmissionMaterializationError("admission.window_invalid") from exc
    if (type(issued_at_ms) is not int or type(expires_at_ms) is not int
            or not 0 <= issued_at_ms < expires_at_ms <= issued_at_ms + lifetime_ms):
        raise AdmissionMaterializationError("admission.window_invalid")
    registry_snapshot = VerifierRegistry.with_defaults().snapshot(
        captured_at_ms=issued_at_ms)
    workspace = materialize_workspace(workspace_root)
    if workspace.workspace_id != result.preparation.workspace_id:
        raise AdmissionMaterializationError(
            "admission.workspace_root_mismatch",
            f"derived={workspace.workspace_id[:20]}… "
            f"scoped={result.preparation.workspace_id[:20]}…")
    evidence = materialize_verifier_evidence(
        result, registry_snapshot, subject_identity=subject_identity,
        intent_evidence=intent_evidence)
    plan_inputs, step_bindings, aliases = materialize_step_bindings(
        result, user_inputs=user_inputs, step_inputs=step_inputs,
        final_outputs=final_outputs, execution_profile_id=execution_profile_id,
        execution_profile_sha256=execution_profile_sha256)
    return dict(
        verification_registry=registry_snapshot,
        intent_evidence=evidence,
        workspace=workspace,
        plan_inputs=plan_inputs,
        step_bindings=step_bindings,
        final_output_aliases=aliases,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        recorded_at_ms=issued_at_ms + 1 if expires_at_ms > issued_at_ms + 1
        else issued_at_ms,
    )


__all__ = [
    "AdmissionMaterializationError",
    "StepInvocationInputs",
    "StepOutputReference",
    "StepOutputSelector",
    "materialize_admission_inputs",
    "materialize_step_bindings",
    "materialize_verifier_evidence",
    "materialize_workspace",
]
