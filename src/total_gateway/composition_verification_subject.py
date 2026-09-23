"""Read-only expansion of a system-bound read composition verification subject.

This adapter verifies terminal execution of every admitted A0 step.  It does
not assert business correctness, document quality, or any write outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contracts import canonical_sha256
from contracts.composition_profile import composition_completion_scope
from contracts.verification import VerificationRecord
from .composition_execution_projection import CompositionExecutionProjectionV1
from .outcome_oracles._common import assemble_record


COMPOSITION_SUBJECT_PREFIX = "composition-plan:"


class CompositionVerificationSubjectError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CompositionVerificationEvaluation:
    aggregate: VerificationRecord
    children: tuple[VerificationRecord, ...]


def evaluate_read_composition_subject(
    *, subject: str, entry, verification_plan, snapshot, store,
    effect_oracle, projector: Callable | None, evaluated_at_ms: int,
) -> CompositionVerificationEvaluation:
    """Resolve only a persisted plan, never model-reported effects or success."""
    if (
        not subject.startswith(COMPOSITION_SUBJECT_PREFIX)
        or entry.predicate.subject_kind != "effect"
        or entry.predicate.predicate_type != "effect.terminal_succeeded"
        or not entry.predicate.has_valid_identity()
        or not verification_plan.has_valid_identity()
        or entry not in verification_plan.entries
        or subject != entry.subject_identity
    ):
        raise CompositionVerificationSubjectError("composition.verification.subject_invalid")
    if projector is None:
        raise CompositionVerificationSubjectError("composition.verification.projector_unavailable")
    stored = store.get_executable_composition_plan_for_request(
        verification_plan.request_id, run_id=verification_plan.run_id,
        generation=verification_plan.generation,
    )
    if stored is None:
        raise CompositionVerificationSubjectError("composition.verification.plan_missing")
    plan = stored.executable_plan
    lineage = (verification_plan.request_id, verification_plan.run_id, verification_plan.generation)
    if (
        not plan.has_valid_identity()
        or subject != COMPOSITION_SUBJECT_PREFIX + plan.composition_plan_id
        or (plan.request_id, plan.run_id, plan.generation) != lineage
        or plan.verification_plan_id != verification_plan.verification_plan_id
        or plan.verification_plan_sha256 != verification_plan.plan_sha256
        or plan.verification_registry_sha256 != snapshot.snapshot_sha256
        or plan.sealed_at_ms > evaluated_at_ms
    ):
        raise CompositionVerificationSubjectError("composition.verification.plan_binding_invalid")
    try:
        scope = composition_completion_scope(plan.step_bindings)
    except ValueError as exc:
        raise CompositionVerificationSubjectError("composition.verification.profile_invalid") from exc
    if not plan.step_bindings:
        raise CompositionVerificationSubjectError("composition.verification.read_only_required")

    # The worker supplies its existing read-only coordinator projection.  It
    # revalidates authorization chains, immutable Effect/Fact identities,
    # payload bytes and the exact action result schema; no handler is called.
    projection = projector(plan)
    if (
        not isinstance(projection, CompositionExecutionProjectionV1)
        or projection.executable_plan_id != plan.executable_plan_id
        or len(projection.steps) != len(plan.step_bindings)
        or set(projection.by_step_id()) != {step.step_id for step in plan.step_bindings}
    ):
        raise CompositionVerificationSubjectError("composition.verification.projection_invalid")
    projected_steps = projection.by_step_id()
    children = []
    observation_steps = []
    for step in plan.step_bindings:
        projected = projected_steps[step.step_id]
        authorization = store.get_current_composition_step_authorization(
            plan.executable_plan_id, step.step_id,
        )
        if authorization is None:
            if projected.authorization_id is not None or projected.effect_id is not None:
                raise CompositionVerificationSubjectError("composition.verification.authorization_missing")
            observation_steps.append({"step_id": step.step_id, "state": projected.state})
            continue
        request = authorization.request
        if (
            authorization.authorization_id != projected.authorization_id
            or request.prebound_effect_id != projected.effect_id
            or (request.request_id, request.run_id, request.generation) != lineage
            or request.executable_plan_id != plan.executable_plan_id
            or request.executable_plan_sha256 != plan.executable_plan_sha256
            or request.registration_id != plan.registration_id
            or request.composition_plan_id != plan.composition_plan_id
            or request.step_id != step.step_id
            or request.step_binding_sha256 != step.sha256
            or request.action_id != step.action_id
            or request.action_version != step.action_version
        ):
            raise CompositionVerificationSubjectError("composition.verification.authorization_binding_invalid")
        child = effect_oracle.evaluate(
            request.prebound_effect_id, entry.predicate,
            evaluated_at_ms=evaluated_at_ms, evaluation_phase=entry.evaluation_phase,
        )
        if (child.request_id, child.run_id, child.generation) != lineage:
            raise CompositionVerificationSubjectError("composition.verification.effect_lineage_invalid")
        if child.status == "PASS" and (projected.state != "SUCCEEDED" or not projected.fact_ids):
            # A SUCCEEDED ledger head without the exact accepted Fact cannot
            # establish a composition step's successful execution.
            raise CompositionVerificationSubjectError("composition.verification.success_fact_missing")
        children.append(child)
        observation_steps.append({
            "step_id": step.step_id, "state": projected.state,
            "authorization_id": authorization.authorization_id,
            "effect_id": request.prebound_effect_id,
            "verification_record_id": child.verification_record_id,
            "verification_result_sha256": child.result_sha256,
            "fact_ids": list(projected.fact_ids),
        })
    if any(child.status == "ERROR" for child in children):
        status, reasons = "ERROR", ("composition.verification.child_authority_error",)
    elif projection.failed_step_ids or any(child.status == "FAIL" for child in children):
        status, reasons = "FAIL", ("composition.verification.step_not_succeeded",)
    elif (
        projection.all_steps_succeeded
        and not projection.next_step_id
        and not projection.reconcile_step_ids
        and not projection.recoverable_step_ids
        and len(children) == len(plan.step_bindings)
        and all(child.status == "PASS" for child in children)
    ):
        status, reasons = "PASS", ()
    else:
        status, reasons = "INCONCLUSIVE", ("composition.verification.execution_unproven",)
    child_ids = [child.verification_record_id for child in children]
    aggregate = assemble_record(
        descriptor=effect_oracle.descriptor, snapshot=snapshot,
        predicate=entry.predicate, subject_kind="effect", subject_identity=subject,
        request_id=plan.request_id, run_id=plan.run_id, generation=plan.generation,
        status=status, reason_codes=reasons,
        evidence_refs=(
            "composition_executable_plan:" + plan.executable_plan_id,
            "composition_plan_sha256:" + plan.executable_plan_sha256,
            "composition_child_records_sha256:" + canonical_sha256(child_ids),
        ),
        observation={"scope": scope, "steps": observation_steps},
        evaluated_at_ms=evaluated_at_ms, evaluation_phase=entry.evaluation_phase,
    )
    return CompositionVerificationEvaluation(aggregate, tuple(children))
