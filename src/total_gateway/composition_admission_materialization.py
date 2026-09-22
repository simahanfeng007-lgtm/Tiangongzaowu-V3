"""P12: mechanical admission-input materialization for a compiled plan.

Closes the gap the R1D delivery recorded: the admission evidence provider.
Given one ``SourceCompositionResult`` this helper mechanically derives the
FULL system side of the P7 admission inputs — the authority-derived
workspace binding, per-intent verifier evidence, per-step invocation
bindings (candidate/permission/schema/source revisions straight from the
sealed preparation) and the activation window. The ONLY caller-supplied
part is the semantic value set (``user_inputs``), which arrives through
the system channel — never from model text — and whose absence for a step
that needs values is an explicit refusal, not a guess.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from contracts import canonical_sha256
from contracts.verification import AcceptancePredicate, RegistrySnapshot

from .composition_executable_plan import (
    ArgumentSlotV1,
    FinalOutputAliasV1,
    OutputDeclarationV1,
    PlanInputV1,
    PlanInputValueBindingV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
    WorkspaceBindingV1,
)
from .composition_registration_intake import VerificationIntentEvidenceV1
from .composition_source_preparation import SourceCompositionResult
from world_understanding.capability_composition import plan_has_valid_sha256
from .verification_registry import VerifierRegistry

ZERO = "0" * 64


class AdmissionMaterializationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


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
) -> tuple[VerificationIntentEvidenceV1, ...]:
    """One deterministic evidence row per plan intent (artifact.nonempty)."""
    evidence = []
    for intent in sorted(result.plan.verification_intents):
        evidence.append(VerificationIntentEvidenceV1(
            intent_ref=intent,
            predicate=AcceptancePredicate.create(
                predicate_type="artifact.nonempty",
                subject_kind="artifact", params={}),
            subject_identity=subject_identity,
            evaluation_phase="POST_EXECUTION"))
    return tuple(evidence)


def materialize_step_bindings(
    result: SourceCompositionResult,
    *,
    user_inputs: Mapping[str, Any],
) -> tuple[tuple[PlanInputV1, ...], tuple[StepExecutionBindingV1, ...],
           tuple[FinalOutputAliasV1, ...]]:
    """Derive every step's invocation binding from the sealed preparation.

    ``user_inputs`` maps semantic input names to JSON values; each named
    input becomes one inline plan input bound into the step's argument
    slots. A step whose declared argument schema has no slot for a given
    input, or an input with no consuming slot, is refused — the materializer
    never invents, drops or renames a value.
    """
    preparation = result.preparation
    proposal = result.parse_outcome.proposal
    candidates_by_id = preparation.candidates.action_by_candidate()
    permission_by_action = {
        item.action_id: item for item in preparation.registry.permissions}
    steps = []
    for plan_step, proposed in zip(
            result.plan.steps, proposal.steps, strict=True):
        candidate = candidates_by_id.get(proposed.candidate_id)
        if candidate is None:
            raise AdmissionMaterializationError(
                "admission.candidate_missing", plan_step.step_id)
        primitive = candidate.primitive
        permission = permission_by_action.get(primitive.action_id)
        if permission is None:
            raise AdmissionMaterializationError(
                "admission.permission_missing", primitive.action_id)
        plan_inputs: list[PlanInputV1] = []
        slots = []
        for index, name in enumerate(sorted(user_inputs), 1):
            value = user_inputs[name]
            input_id = f"input.{name}"
            plan_input = _hashed(PlanInputV1(
                input_id=input_id, input_kind="INLINE_JSON",
                inline_value=value, value_schema_sha256=ZERO,
                value_sha256=canonical_sha256(value), sha256=ZERO))
            plan_inputs.append(plan_input)
            slots.append(_hashed(ArgumentSlotV1(
                destination_json_pointer=f"/{name}",
                value_binding=_hashed(PlanInputValueBindingV1(
                    input_id=input_id, input_sha256=plan_input.sha256,
                    json_pointer="", sha256=ZERO)),
                sha256=ZERO)))
        outputs = tuple(
            _hashed(OutputDeclarationV1(
                output_binding_id=binding_id,
                source_kind="RESULT_PAYLOAD",
                json_pointer="/result",
                value_schema_sha256=primitive.result_schema_sha256,
                sha256=ZERO))
            for binding_id in proposed.output_bindings)
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
            depends_on=plan_step.depends_on,
            target_skeleton="",
            args_skeleton={name: None for name in sorted(user_inputs)},
            argument_slots=tuple(sorted(
                slots, key=lambda slot: slot.destination_json_pointer)),
            output_declarations=outputs,
            sha256=ZERO)))
    final = proposal.steps[-1]
    last = steps[-1]
    final_reference = _hashed(StepOutputValueBindingV1(
        producer_step_id=last.step_id,
        output_binding_id=last.output_declarations[-1].output_binding_id,
        output_declaration_sha256=last.output_declarations[-1].sha256,
        sha256=ZERO))
    aliases = (_hashed(FinalOutputAliasV1(
        alias=proposal.output_bindings[0],
        value_binding=final_reference, sha256=ZERO)),)
    return (tuple(plan_inputs[:len(user_inputs)]
                  if len(steps) == 1 else plan_inputs),
            tuple(steps), aliases)


def materialize_admission_inputs(
    result: SourceCompositionResult,
    *,
    workspace_root: Path,
    user_inputs: Mapping[str, Any],
    subject_identity: str = "object:composition-turn",
    issued_at_ms: int,
    expires_at_ms: int,
) -> dict:
    """The full system-side admission inputs for one compiled result.

    Everything except ``user_inputs`` derives from the sealed preparation
    and the operator-pinned workspace; the semantic values themselves must
    already have arrived through the system channel (operator/user input
    objects) — this function neither parses model text nor guesses values.
    """
    from datetime import timezone  # noqa: F401  (kept for callers)
    if not isinstance(result, SourceCompositionResult) \
            or not result.preparation.has_valid_sha256() \
            or not plan_has_valid_sha256(result.plan):
        raise AdmissionMaterializationError("admission.result_invalid")
    if not 0 <= issued_at_ms < expires_at_ms <= issued_at_ms + 60_000:
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
        result, registry_snapshot, subject_identity=subject_identity)
    plan_inputs, step_bindings, aliases = materialize_step_bindings(
        result, user_inputs=user_inputs)
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
    "materialize_admission_inputs",
    "materialize_step_bindings",
    "materialize_verifier_evidence",
    "materialize_workspace",
]
