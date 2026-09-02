"""Conservative tri-state validation for system-compiled composition plans."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Mapping

from contracts import ActionRegistrySnapshot, canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionProposalV1,
    CompositionValidationFindingV1,
    CompositionValidationResultV1,
    ToolSourcePrimitiveV1,
)

from .compiler import (
    compile_capability_composition_plan,
    plan_has_valid_sha256,
)
from .models import (
    CapabilityCompositionError,
    CompositionCandidateSnapshotV1,
    CompositionCompileContextV1,
)
from .parser import proposal_has_valid_sha256


_RISK_ORDER = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
_WRITE_EFFECTS = {"create", "write", "update", "execute"}
_DANGEROUS_SIDE_EFFECTS = {
    "credential_read",
    "destructive",
    "external_send",
    "external_write",
    "irreversible",
}
_DANGEROUS_RESOURCES = {
    "credential",
    "credentials",
    "python",
    "secret",
    "secrets",
    "shell",
    "user_private",
    "private_data",
}


def computed_validation_sha256(
    result: CompositionValidationResultV1,
) -> str:
    return canonical_sha256(
        result.model_dump(mode="json", exclude={"validation_sha256"})
    )


def validation_has_valid_sha256(
    result: CompositionValidationResultV1,
) -> bool:
    return result.validation_sha256 == computed_validation_sha256(result)


def _finding(
    code: str,
    state: str,
    subject_ref: str,
    detail: object | None = None,
) -> CompositionValidationFindingV1:
    return CompositionValidationFindingV1(
        code=code,
        state=state,  # type: ignore[arg-type]
        subject_ref=subject_ref,
        detail_hash=canonical_sha256(detail) if detail is not None else None,
    )


def _result(
    *,
    plan: CapabilityCompositionPlanV1,
    result: str,
    findings: Iterable[CompositionValidationFindingV1],
    validated_at_ms: int,
    unknown_disposition: str = "NOT_APPLICABLE",
    mandatory_verification: bool = False,
) -> CompositionValidationResultV1:
    ordered = tuple(
        sorted(
            findings,
            key=lambda item: (
                item.state,
                item.code,
                item.subject_ref,
                item.detail_hash or "",
            ),
        )
    )
    value = CompositionValidationResultV1(
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        result=result,  # type: ignore[arg-type]
        unknown_disposition=unknown_disposition,  # type: ignore[arg-type]
        findings=ordered,
        mandatory_verification=mandatory_verification,
        validated_at_ms=validated_at_ms,
        validation_sha256="0" * 64,
    )
    return value.model_copy(
        update={"validation_sha256": computed_validation_sha256(value)}
    )


def _primitive_by_action(
    candidates: CompositionCandidateSnapshotV1,
) -> dict[str, ToolSourcePrimitiveV1]:
    return {
        item.primitive.action_id: item.primitive
        for item in candidates.action_candidates
    }


def _reachability(
    plan: CapabilityCompositionPlanV1,
) -> Mapping[str, set[str]]:
    outgoing: dict[str, set[str]] = defaultdict(set)
    for step in plan.steps:
        for dependency in step.depends_on:
            outgoing[dependency].add(step.step_id)
    reachable: dict[str, set[str]] = {}
    for step in plan.steps:
        seen: set[str] = set()
        queue = deque(sorted(outgoing.get(step.step_id, ())))
        while queue:
            target = queue.popleft()
            if target in seen:
                continue
            seen.add(target)
            queue.extend(sorted(outgoing.get(target, ())))
        reachable[step.step_id] = seen
    return reachable


def _validate_dependency_compatibility(
    plan: CapabilityCompositionPlanV1,
    primitives: Mapping[str, ToolSourcePrimitiveV1],
) -> list[CompositionValidationFindingV1]:
    findings: list[CompositionValidationFindingV1] = []
    steps = {item.step_id: item for item in plan.steps}
    for step in plan.steps:
        consumer = primitives.get(step.action_id)
        if consumer is None or not consumer.consumes:
            continue
        predecessor_outputs = {
            output
            for dependency in step.depends_on
            for output in (
                primitives.get(steps[dependency].action_id).produces
                if dependency in steps
                and primitives.get(steps[dependency].action_id) is not None
                else ()
            )
        }
        if not predecessor_outputs.intersection(consumer.consumes):
            findings.append(
                _finding(
                    "validator.dependency.type_incompatible",
                    "PROVED_INVALID",
                    step.step_id,
                    {
                        "consumes": list(consumer.consumes),
                        "predecessor_outputs": sorted(predecessor_outputs),
                    },
                )
            )
    return findings


def _validate_parallel_write_conflicts(
    plan: CapabilityCompositionPlanV1,
    primitives: Mapping[str, ToolSourcePrimitiveV1],
) -> list[CompositionValidationFindingV1]:
    findings: list[CompositionValidationFindingV1] = []
    reachable = _reachability(plan)
    writes: list[tuple[str, set[str]]] = []
    for step in plan.steps:
        primitive = primitives.get(step.action_id)
        if primitive is None:
            continue
        write_set = set(primitive.write_set_descriptor)
        effect = primitive.effect_class.removeprefix("effect:").casefold()
        if write_set or effect in _WRITE_EFFECTS:
            writes.append((step.step_id, write_set or {"resource:write"}))
    for index, (left_id, left_set) in enumerate(writes):
        for right_id, right_set in writes[index + 1 :]:
            ordered = (
                right_id in reachable.get(left_id, set())
                or left_id in reachable.get(right_id, set())
            )
            overlap = left_set.intersection(right_set)
            if not ordered and overlap:
                findings.append(
                    _finding(
                        "validator.write_set.parallel_conflict",
                        "PROVED_INVALID",
                        left_id,
                        {
                            "other_step": right_id,
                            "overlap": sorted(overlap),
                        },
                    )
                )
    return findings


def _unknown_findings(
    plan: CapabilityCompositionPlanV1,
    primitives: Mapping[str, ToolSourcePrimitiveV1],
    *,
    available_verifiers: frozenset[str],
) -> list[CompositionValidationFindingV1]:
    findings: list[CompositionValidationFindingV1] = []
    for action_id in plan.permission_requirements:
        primitive = primitives.get(action_id)
        if primitive is None:
            continue
        if primitive.availability in {"DEGRADED", "UNKNOWN"}:
            findings.append(
                _finding(
                    "validator.action.availability_unknown",
                    "UNKNOWN",
                    action_id,
                    primitive.availability,
                )
            )
        if primitive.idempotency == "UNKNOWN":
            findings.append(
                _finding(
                    "validator.action.idempotency_unknown",
                    "UNKNOWN",
                    action_id,
                )
            )
        if primitive.determinism_class != "DETERMINISTIC":
            findings.append(
                _finding(
                    "validator.action.determinism_unknown",
                    "UNKNOWN",
                    action_id,
                    primitive.determinism_class,
                )
            )

    missing_verifiers = tuple(
        sorted(
            intent
            for intent in plan.verification_intents
            if intent not in available_verifiers
        )
    )
    if missing_verifiers:
        findings.append(
            _finding(
                "validator.verifier.unavailable",
                "UNKNOWN",
                plan.plan_id,
                list(missing_verifiers),
            )
        )
    return findings


def _safe_provisional_unknown(
    plan: CapabilityCompositionPlanV1,
    primitives: Mapping[str, ToolSourcePrimitiveV1],
    *,
    available_verifiers: frozenset[str],
) -> bool:
    if _RISK_ORDER[plan.composition_risk] > _RISK_ORDER["A1"]:
        return False
    if not plan.verification_intents or not set(plan.verification_intents).issubset(
        available_verifiers
    ):
        return False
    for action_id in plan.permission_requirements:
        primitive = primitives.get(action_id)
        if primitive is None:
            return False
        effect = primitive.effect_class.removeprefix("effect:").casefold()
        side_effects = {item.casefold() for item in primitive.side_effects}
        resources = {item.casefold() for item in primitive.resource_scope}
        if (
            effect not in {"read", "verify"}
            or side_effects.intersection(_DANGEROUS_SIDE_EFFECTS)
            or resources.intersection(_DANGEROUS_RESOURCES)
        ):
            return False
    return True


def validate_capability_composition_plan(
    plan: CapabilityCompositionPlanV1,
    proposal: CompositionProposalV1,
    candidates: CompositionCandidateSnapshotV1,
    context: CompositionCompileContextV1,
    registry: ActionRegistrySnapshot,
    *,
    available_verifiers: frozenset[str] = frozenset(),
    validated_at_ms: int,
) -> CompositionValidationResultV1:
    """Validate only mechanically decidable P4 properties.

    ``PROVED_VALID`` means the bounded mechanical contract is proven. It does
    not claim a natural-language theorem that the plan must satisfy the user's
    goal.
    """

    if validated_at_ms < 0:
        raise ValueError("validated_at_ms must be non-negative")

    invalid: list[CompositionValidationFindingV1] = []
    if not plan_has_valid_sha256(plan):
        invalid.append(
            _finding(
                "validator.plan.hash_invalid",
                "PROVED_INVALID",
                plan.plan_id,
            )
        )
    if not proposal_has_valid_sha256(proposal):
        invalid.append(
            _finding(
                "validator.proposal.hash_invalid",
                "PROVED_INVALID",
                plan.plan_id,
            )
        )
    if not candidates.has_valid_sha256():
        invalid.append(
            _finding(
                "validator.candidates.hash_invalid",
                "PROVED_INVALID",
                plan.plan_id,
            )
        )
    if not context.has_valid_sha256():
        invalid.append(
            _finding(
                "validator.context.hash_invalid",
                "PROVED_INVALID",
                plan.plan_id,
            )
        )
    if not registry.has_valid_sha256():
        invalid.append(
            _finding(
                "validator.registry.hash_invalid",
                "PROVED_INVALID",
                plan.plan_id,
            )
        )
    if invalid:
        return _result(
            plan=plan,
            result="PROVED_INVALID",
            findings=invalid,
            validated_at_ms=validated_at_ms,
        )

    try:
        expected = compile_capability_composition_plan(
            proposal, candidates, context, registry
        )
    except (CapabilityCompositionError, ValueError) as exc:
        code = getattr(exc, "code", "validator.compiler.unexpected_error")
        detail = getattr(exc, "detail", str(exc))
        return _result(
            plan=plan,
            result="PROVED_INVALID",
            findings=(
                _finding(
                    "validator.compiler.reconstruction_failed",
                    "PROVED_INVALID",
                    plan.plan_id,
                    {"code": code, "detail": detail},
                ),
            ),
            validated_at_ms=validated_at_ms,
        )

    if plan.model_dump(mode="json") != expected.model_dump(mode="json"):
        return _result(
            plan=plan,
            result="PROVED_INVALID",
            findings=(
                _finding(
                    "validator.plan.compiler_mismatch",
                    "PROVED_INVALID",
                    plan.plan_id,
                    {
                        "actual": plan.plan_sha256,
                        "expected": expected.plan_sha256,
                    },
                ),
            ),
            validated_at_ms=validated_at_ms,
        )

    primitives = _primitive_by_action(candidates)
    invalid.extend(
        _validate_dependency_compatibility(plan, primitives)
    )
    invalid.extend(
        _validate_parallel_write_conflicts(plan, primitives)
    )
    for action_id in plan.permission_requirements:
        primitive = primitives.get(action_id)
        if primitive is None:
            invalid.append(
                _finding(
                    "validator.action.primitive_missing",
                    "PROVED_INVALID",
                    action_id,
                )
            )
        elif primitive.availability == "UNAVAILABLE":
            invalid.append(
                _finding(
                    "validator.action.unavailable",
                    "PROVED_INVALID",
                    action_id,
                )
            )
    if plan.composition_risk == "A5":
        invalid.append(
            _finding(
                "validator.composition.a5_forbidden",
                "PROVED_INVALID",
                plan.plan_id,
                list(plan.information_flow_findings),
            )
        )
    if invalid:
        return _result(
            plan=plan,
            result="PROVED_INVALID",
            findings=invalid,
            validated_at_ms=validated_at_ms,
        )

    unknown = _unknown_findings(
        plan,
        primitives,
        available_verifiers=available_verifiers,
    )
    if unknown:
        provisional = _safe_provisional_unknown(
            plan,
            primitives,
            available_verifiers=available_verifiers,
        )
        return _result(
            plan=plan,
            result="UNKNOWN",
            findings=unknown,
            validated_at_ms=validated_at_ms,
            unknown_disposition=(
                "PROVISIONAL_ALLOW" if provisional else "REJECT"
            ),
            mandatory_verification=provisional,
        )

    return _result(
        plan=plan,
        result="PROVED_VALID",
        findings=(),
        validated_at_ms=validated_at_ms,
    )


__all__ = [
    "computed_validation_sha256",
    "validate_capability_composition_plan",
    "validation_has_valid_sha256",
]
