"""Deterministic system compiler from model proposal to authoritative Plan IR.

The compiler derives all hashes, source/version bindings, permission
requirements, and composition risk. It does not issue a Grant, Ticket, execute
an Action, or decide Completion.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from contracts import ActionRegistrySnapshot, canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionPlanStepV1,
    CompositionProposalV1,
    SourceRevisionRefV1,
    ToolSourcePrimitiveV1,
)

from .models import (
    ActionCandidateBindingV1,
    CapabilityCompositionError,
    CompositionCandidateSnapshotV1,
    CompositionCompileContextV1,
    validate_registry_binding,
)
from .parser import proposal_has_valid_sha256


_RISK_ORDER = {
    "A0": 0,
    "A1": 1,
    "A2": 2,
    "A3": 3,
    "A4": 4,
    "A5": 5,
}
_RISK_BY_VALUE = {value: key for key, value in _RISK_ORDER.items()}
_WRITE_EFFECTS = {"create", "write", "update", "execute"}
_EXTERNAL_SIDE_EFFECTS = {"external_write", "external_send"}
_SENSITIVE_RESOURCE_CLASSES = {
    "credential",
    "credentials",
    "secret",
    "secrets",
    "user_private",
    "private_data",
    "sensitive_source",
}
_PRIVILEGED_RESOURCE_CLASSES = {"shell", "python"}
_DESTRUCTIVE_SIDE_EFFECTS = {"destructive", "irreversible"}


def computed_plan_sha256(plan: CapabilityCompositionPlanV1) -> str:
    return canonical_sha256(
        plan.model_dump(mode="json", exclude={"plan_sha256"})
    )


def plan_has_valid_sha256(plan: CapabilityCompositionPlanV1) -> bool:
    return plan.plan_sha256 == computed_plan_sha256(plan)


def _risk_max(values: Iterable[str]) -> str:
    materialized = tuple(values)
    if not materialized or any(value not in _RISK_ORDER for value in materialized):
        raise CapabilityCompositionError("compiler.risk.invalid")
    return _RISK_BY_VALUE[max(_RISK_ORDER[value] for value in materialized)]


def _risk_at_least(value: str, floor: str) -> str:
    return _RISK_BY_VALUE[max(_RISK_ORDER[value], _RISK_ORDER[floor])]


def analyze_composition_risk(
    primitives: tuple[ToolSourcePrimitiveV1, ...],
) -> tuple[str, str, tuple[str, ...]]:
    """Return leaf floor, composition risk, and deterministic flow findings."""

    if not primitives:
        raise CapabilityCompositionError("compiler.actions.empty")
    risk_floor = _risk_max(item.risk_floor for item in primitives)
    composition_risk = risk_floor
    findings: set[str] = set()

    resources = {
        value.casefold()
        for primitive in primitives
        for value in primitive.resource_scope
    }
    side_effects = {
        value.casefold()
        for primitive in primitives
        for value in primitive.side_effects
    }
    effects = {
        primitive.effect_class.removeprefix("effect:").casefold()
        for primitive in primitives
    }
    write_count = sum(
        1
        for primitive in primitives
        if primitive.effect_class.removeprefix("effect:").casefold()
        in _WRITE_EFFECTS
    )
    has_external_sink = bool(side_effects & _EXTERNAL_SIDE_EFFECTS)
    has_sensitive_source = bool(resources & _SENSITIVE_RESOURCE_CLASSES)
    has_privileged_source = bool(resources & _PRIVILEGED_RESOURCE_CLASSES)
    has_destructive = bool(side_effects & _DESTRUCTIVE_SIDE_EFFECTS)

    if write_count > 1:
        findings.add("composition:multiple-writes")
        composition_risk = _risk_at_least(composition_risk, "A3")
    if has_sensitive_source and has_external_sink:
        findings.add("flow:sensitive-source-to-external-sink")
        composition_risk = "A5"
    if has_privileged_source and has_external_sink:
        findings.add("flow:privileged-execution-to-external-sink")
        composition_risk = "A5"
    if has_destructive and has_external_sink:
        findings.add("flow:destructive-sequence-with-external-sink")
        composition_risk = "A5"
    if "execute" in effects and has_external_sink:
        findings.add("flow:execution-result-to-external-sink")
        composition_risk = "A5"

    return risk_floor, composition_risk, tuple(sorted(findings))


def _topological_step_ids(
    proposal: CompositionProposalV1,
) -> tuple[str, ...]:
    steps = {item.step_id: item for item in proposal.steps}
    indegree = {step_id: 0 for step_id in steps}
    outgoing: dict[str, set[str]] = defaultdict(set)
    for left, right in proposal.dependency_edges:
        if left not in steps or right not in steps or left == right:
            raise CapabilityCompositionError(
                "compiler.dependency.invalid", f"{left}>{right}"
            )
        if right not in outgoing[left]:
            outgoing[left].add(right)
            indegree[right] += 1

    ready = sorted(step_id for step_id, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        step_id = ready.pop(0)
        ordered.append(step_id)
        for target in sorted(outgoing.get(step_id, ())):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(steps):
        raise CapabilityCompositionError("compiler.dependency.cycle")
    return tuple(ordered)


def _source_sort_key(source: SourceRevisionRefV1) -> tuple[str, str, str, str]:
    return (
        source.source_kind,
        source.semantic_id,
        source.version,
        source.source_sha256,
    )


def _selected_action_bindings(
    proposal: CompositionProposalV1,
    candidates: CompositionCandidateSnapshotV1,
) -> tuple[ActionCandidateBindingV1, ...]:
    by_id = candidates.action_by_candidate()
    try:
        selected = tuple(
            by_id[candidate_id]
            for candidate_id in proposal.selected_action_candidate_ids
        )
    except KeyError as exc:
        raise CapabilityCompositionError(
            "compiler.action_candidate.unknown", str(exc)
        ) from exc
    return tuple(
        sorted(selected, key=lambda item: item.primitive.action_id)
    )


def _source_manifest_sha256(
    *,
    action_sources: tuple[SourceRevisionRefV1, ...],
    method_sources: tuple[SourceRevisionRefV1, ...],
) -> str:
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


def compile_capability_composition_plan(
    proposal: CompositionProposalV1,
    candidates: CompositionCandidateSnapshotV1,
    context: CompositionCompileContextV1,
    registry: ActionRegistrySnapshot,
) -> CapabilityCompositionPlanV1:
    """Compile the bounded model proposal into a fully system-derived Plan."""

    if not proposal_has_valid_sha256(proposal):
        raise CapabilityCompositionError("compiler.proposal.hash_invalid")
    if not candidates.has_valid_sha256():
        raise CapabilityCompositionError("compiler.candidates.hash_invalid")
    if not context.has_valid_sha256():
        raise CapabilityCompositionError("compiler.context.hash_invalid")
    if not registry.has_valid_sha256():
        raise CapabilityCompositionError("compiler.registry.hash_invalid")
    if proposal.goal_ref != context.goal_ref:
        raise CapabilityCompositionError("compiler.goal_ref.mismatch")
    if (
        context.capability_manifest_sha256
        != registry.source_manifest_sha256
    ):
        raise CapabilityCompositionError(
            "compiler.capability_manifest.mismatch"
        )
    validate_registry_binding(candidates, registry)

    methods_by_candidate = candidates.method_by_candidate()
    try:
        selected_methods = tuple(
            methods_by_candidate[candidate_id]
            for candidate_id in proposal.selected_method_candidate_ids
        )
        selected_actions = _selected_action_bindings(proposal, candidates)
    except KeyError as exc:
        raise CapabilityCompositionError(
            "compiler.candidate.unknown", str(exc)
        ) from exc
    if not selected_actions:
        raise CapabilityCompositionError("compiler.actions.empty")

    selected_action_ids = {
        item.candidate_id for item in selected_actions
    }
    used_action_ids = {item.candidate_id for item in proposal.steps}
    if selected_action_ids != used_action_ids:
        raise CapabilityCompositionError(
            "compiler.action_selection.step_mismatch"
        )

    permission_by_action = {
        permission.action_id: permission for permission in registry.permissions
    }
    for selected in selected_actions:
        primitive = selected.primitive
        permission = permission_by_action.get(primitive.action_id)
        if (
            permission is None
            or permission.action_version != primitive.action_version
            or permission.source_manifest_sha256
            != context.capability_manifest_sha256
            or selected.source_revision.manifest_sha256
            != context.capability_manifest_sha256
        ):
            raise CapabilityCompositionError(
                "compiler.action_authority.binding_mismatch",
                primitive.action_id,
            )

    action_sources = tuple(
        sorted(
            (item.source_revision for item in selected_actions),
            key=_source_sort_key,
        )
    )
    method_sources = tuple(
        sorted(
            (item.primitive.source_ref for item in selected_methods),
            key=_source_sort_key,
        )
    )

    selected_by_candidate = {
        item.candidate_id: item for item in selected_actions
    }
    proposal_step_by_id = {item.step_id: item for item in proposal.steps}
    ordered_step_ids = _topological_step_ids(proposal)
    plan_steps: list[CompositionPlanStepV1] = []
    for step_id in ordered_step_ids:
        proposed_step = proposal_step_by_id[step_id]
        selected = selected_by_candidate.get(proposed_step.candidate_id)
        if selected is None:
            raise CapabilityCompositionError(
                "compiler.step.candidate_not_selected", step_id
            )
        primitive = selected.primitive
        plan_steps.append(
            CompositionPlanStepV1(
                step_id=step_id,
                action_id=primitive.action_id,
                action_version=primitive.action_version,
                method_id=None,
                depends_on=proposed_step.depends_on,
                expected_effect_refs=tuple(sorted(set(primitive.produces))),
                verification_intent_refs=tuple(
                    sorted(set(primitive.verifier_refs))
                ),
            )
        )

    dependency_graph_sha256 = canonical_sha256(
        {
            "domain": "tiangong.capability-composition.dependency-graph.v1",
            "control_flow": proposal.control_flow,
            "steps": [
                {
                    "step_id": item.step_id,
                    "depends_on": list(item.depends_on),
                }
                for item in plan_steps
            ],
            "edges": [list(item) for item in proposal.dependency_edges],
        }
    )
    bindings_sha256 = canonical_sha256(
        {
            "domain": "tiangong.capability-composition.bindings.v1",
            "proposal_sha256": proposal.proposal_sha256,
            "candidate_snapshot_sha256": candidates.candidate_snapshot_sha256,
            "compile_context_sha256": context.context_sha256,
            "action_registry_sha256": registry.registry_sha256,
            "selected_method_bindings": [
                item.binding_sha256 for item in selected_methods
            ],
            "selected_action_bindings": [
                item.binding_sha256 for item in selected_actions
            ],
            "step_candidate_bindings": [
                [item.step_id, item.candidate_id] for item in proposal.steps
            ],
            "output_bindings": list(proposal.output_bindings),
        }
    )
    source_manifest_sha256 = _source_manifest_sha256(
        action_sources=action_sources,
        method_sources=method_sources,
    )

    primitives = tuple(item.primitive for item in selected_actions)
    risk_floor, composition_risk, flow_findings = analyze_composition_risk(
        primitives
    )
    permission_requirements = tuple(
        sorted({item.action_id for item in primitives})
    )
    expected_effects = tuple(
        sorted(
            {
                effect
                for primitive in primitives
                for effect in primitive.produces
            }
        )
    )
    required_resources = tuple(
        sorted(
            {
                resource
                for primitive in primitives
                for resource in primitive.resource_scope
            }
        )
    )
    verification_intents = tuple(
        sorted(
            {
                intent
                for method in selected_methods
                for intent in method.primitive.verification_intent
            }
            | {
                verifier
                for primitive in primitives
                for verifier in primitive.verifier_refs
            }
        )
    )

    plan_identity_sha256 = canonical_sha256(
        {
            "domain": "tiangong.capability-composition.plan-identity.v1",
            "request_id": context.request_id,
            "run_id": context.run_id,
            "generation": context.generation,
            "principal_scope_hash": context.principal_scope_hash,
            "world_state_sha256": context.world_state_sha256,
            "goal_fingerprint": context.goal_fingerprint,
            "proposal_sha256": proposal.proposal_sha256,
            "bindings_sha256": bindings_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "capability_manifest_sha256": context.capability_manifest_sha256,
        }
    )
    plan = CapabilityCompositionPlanV1(
        plan_id="plan_" + plan_identity_sha256,
        request_id=context.request_id,
        run_id=context.run_id,
        generation=context.generation,
        principal_scope_hash=context.principal_scope_hash,
        world_state_ref=context.world_state_ref,
        world_state_sha256=context.world_state_sha256,
        goal_fingerprint=context.goal_fingerprint,
        environment_class=context.environment_class,
        context_fingerprint_sha256=context.context_fingerprint_sha256,
        method_source_refs=method_sources,
        action_source_refs=action_sources,
        steps=tuple(plan_steps),
        dependency_graph_sha256=dependency_graph_sha256,
        bindings_sha256=bindings_sha256,
        control_flow=proposal.control_flow,
        expected_effects=expected_effects,
        required_resource_classes=required_resources,
        permission_requirements=permission_requirements,
        risk_floor=risk_floor,
        composition_risk=composition_risk,
        information_flow_findings=flow_findings,
        source_manifest_sha256=source_manifest_sha256,
        capability_manifest_sha256=context.capability_manifest_sha256,
        memory_experience_refs=(),
        verification_intents=verification_intents,
        created_at_ms=context.created_at_ms,
        plan_sha256="0" * 64,
    )
    return plan.model_copy(
        update={"plan_sha256": computed_plan_sha256(plan)}
    )


__all__ = [
    "analyze_composition_risk",
    "compile_capability_composition_plan",
    "computed_plan_sha256",
    "plan_has_valid_sha256",
]
