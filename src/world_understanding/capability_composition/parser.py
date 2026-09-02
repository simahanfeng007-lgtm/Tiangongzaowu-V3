"""Strict JSON/DSL parser for the small model-authored CompositionProposal ABI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from pydantic import ValidationError

from contracts import canonical_sha256
from contracts.capability_composition import (
    CompositionProposalV1,
    ProposalStepV1,
)

from .models import (
    CapabilityCompositionError,
    CompositionCandidateSnapshotV1,
)


_RAW_SCHEMA = "tiangong.composition-proposal.v1"
_RAW_ROOT_FIELDS = {
    "proposal_schema",
    "goal_ref",
    "selected_method_candidate_ids",
    "selected_action_candidate_ids",
    "steps",
    "dependency_edges",
    "output_bindings",
    "control_flow",
    "rationale_tags",
}
_RAW_STEP_FIELDS = {
    "step_id",
    "candidate_id",
    "depends_on",
    "output_bindings",
}


class CompositionProposalParseError(CapabilityCompositionError):
    pass


@dataclass(frozen=True, slots=True)
class ProposalParseOutcomeV1:
    proposal: CompositionProposalV1
    repaired: bool
    primary_error_code: str | None


def computed_proposal_sha256(proposal: CompositionProposalV1) -> str:
    return canonical_sha256(
        proposal.model_dump(mode="json", exclude={"proposal_sha256"})
    )


def proposal_has_valid_sha256(proposal: CompositionProposalV1) -> bool:
    return proposal.proposal_sha256 == computed_proposal_sha256(proposal)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CompositionProposalParseError(
                "proposal.json.duplicate_key", key
            )
        result[key] = value
    return result


def _reject_number(value: str) -> None:
    raise CompositionProposalParseError("proposal.json.number_forbidden", value)


def _parse_json(text: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_number,
            parse_int=_reject_number,
            parse_constant=_reject_number,
        )
    except CompositionProposalParseError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CompositionProposalParseError(
            "proposal.json.invalid", str(exc)
        ) from exc
    if not isinstance(value, Mapping):
        raise CompositionProposalParseError("proposal.json.root_not_object")
    return value


def _csv(value: str) -> list[str]:
    stripped = value.strip()
    if stripped in {"", "-"}:
        return []
    parts = [item.strip() for item in stripped.split(",")]
    if any(not item for item in parts):
        raise CompositionProposalParseError("proposal.dsl.empty_list_item")
    return parts


def _parse_dsl(text: str) -> Mapping[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[-1] != "END":
        raise CompositionProposalParseError("proposal.dsl.missing_end")
    if len(lines) > 256:
        raise CompositionProposalParseError("proposal.dsl.too_many_lines")

    singleton: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    edges: list[list[str]] = []
    for line in lines[:-1]:
        key, separator, remainder = line.partition(" ")
        if not separator:
            raise CompositionProposalParseError(
                "proposal.dsl.line_invalid", line[:120]
            )
        key = key.upper()
        value = remainder.strip()
        if key == "STEP":
            parts = [item.strip() for item in value.split("|")]
            if len(parts) != 4 or not parts[0] or not parts[1]:
                raise CompositionProposalParseError(
                    "proposal.dsl.step_invalid", value[:120]
                )
            steps.append(
                {
                    "step_id": parts[0],
                    "candidate_id": parts[1],
                    "depends_on": _csv(parts[2]),
                    "output_bindings": _csv(parts[3]),
                }
            )
        elif key == "EDGE":
            left, marker, right = value.partition(">")
            left = left.strip()
            right = right.strip()
            if marker != ">" or not left or not right:
                raise CompositionProposalParseError(
                    "proposal.dsl.edge_invalid", value[:120]
                )
            edges.append([left, right])
        elif key in {
            "PROPOSAL",
            "GOAL",
            "METHODS",
            "ACTIONS",
            "OUTPUTS",
            "FLOW",
            "TAGS",
        }:
            if key in singleton:
                raise CompositionProposalParseError(
                    "proposal.dsl.duplicate_field", key
                )
            singleton[key] = value
        else:
            raise CompositionProposalParseError(
                "proposal.dsl.unknown_line", key
            )

    required = {"PROPOSAL", "GOAL", "METHODS", "ACTIONS", "OUTPUTS", "FLOW", "TAGS"}
    if set(singleton) != required:
        missing = ",".join(sorted(required - set(singleton)))
        extra = ",".join(sorted(set(singleton) - required))
        raise CompositionProposalParseError(
            "proposal.dsl.fields_incomplete", f"missing={missing};extra={extra}"
        )
    return {
        "proposal_schema": singleton["PROPOSAL"],
        "goal_ref": singleton["GOAL"],
        "selected_method_candidate_ids": _csv(singleton["METHODS"]),
        "selected_action_candidate_ids": _csv(singleton["ACTIONS"]),
        "steps": steps,
        "dependency_edges": edges,
        "output_bindings": _csv(singleton["OUTPUTS"]),
        "control_flow": singleton["FLOW"],
        "rationale_tags": _csv(singleton["TAGS"]),
    }


def _string_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CompositionProposalParseError(
            "proposal.field.string_list_invalid", field
        )
    if len(value) != len(set(value)):
        raise CompositionProposalParseError(
            "proposal.field.duplicate_value", field
        )
    return tuple(sorted(value))


def _steps(value: object) -> tuple[ProposalStepV1, ...]:
    if not isinstance(value, list) or not value or len(value) > 128:
        raise CompositionProposalParseError("proposal.steps.invalid")
    result: list[ProposalStepV1] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _RAW_STEP_FIELDS:
            raise CompositionProposalParseError(
                "proposal.step.fields_invalid"
            )
        step_id = raw.get("step_id")
        candidate_id = raw.get("candidate_id")
        if (
            not isinstance(step_id, str)
            or not step_id
            or not isinstance(candidate_id, str)
            or not candidate_id
            or step_id in seen
        ):
            raise CompositionProposalParseError(
                "proposal.step.identity_invalid"
            )
        seen.add(step_id)
        result.append(
            ProposalStepV1(
                step_id=step_id,
                candidate_id=candidate_id,
                depends_on=_string_list(
                    raw.get("depends_on"), field=f"{step_id}.depends_on"
                ),
                output_bindings=_string_list(
                    raw.get("output_bindings"),
                    field=f"{step_id}.output_bindings",
                ),
            )
        )
    return tuple(result)


def _edges(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or len(value) > 512:
        raise CompositionProposalParseError("proposal.edges.invalid")
    result: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(not isinstance(part, str) or not part for part in item)
        ):
            raise CompositionProposalParseError(
                "proposal.edge.invalid"
            )
        result.append((item[0], item[1]))
    if len(result) != len(set(result)):
        raise CompositionProposalParseError(
            "proposal.edge.duplicate"
        )
    return tuple(sorted(result))


def _validate_candidate_and_graph_bindings(
    *,
    proposal: CompositionProposalV1,
    candidates: CompositionCandidateSnapshotV1,
) -> None:
    if not candidates.has_valid_sha256():
        raise CompositionProposalParseError(
            "proposal.candidate_snapshot.hash_invalid"
        )
    methods = candidates.method_by_candidate()
    actions = candidates.action_by_candidate()
    selected_methods = set(proposal.selected_method_candidate_ids)
    selected_actions = set(proposal.selected_action_candidate_ids)
    if not selected_methods.issubset(methods):
        unknown = sorted(selected_methods - set(methods))
        raise CompositionProposalParseError(
            "proposal.method_candidate.unknown", ",".join(unknown)
        )
    if not selected_actions or not selected_actions.issubset(actions):
        unknown = sorted(selected_actions - set(actions))
        raise CompositionProposalParseError(
            "proposal.action_candidate.unknown",
            ",".join(unknown) if unknown else "empty",
        )

    step_ids = {step.step_id for step in proposal.steps}
    used_actions = {step.candidate_id for step in proposal.steps}
    if used_actions != selected_actions:
        raise CompositionProposalParseError(
            "proposal.action_selection.step_mismatch"
        )
    if not used_actions.issubset(actions):
        raise CompositionProposalParseError(
            "proposal.step.candidate_not_action"
        )
    for step in proposal.steps:
        dependencies = set(step.depends_on)
        if step.step_id in dependencies or not dependencies.issubset(step_ids):
            raise CompositionProposalParseError(
                "proposal.step.dependency_invalid", step.step_id
            )

    derived_edges = tuple(
        sorted(
            (dependency, step.step_id)
            for step in proposal.steps
            for dependency in step.depends_on
        )
    )
    if proposal.control_flow == "SEQUENTIAL":
        expected_edges: list[tuple[str, str]] = []
        for index, step in enumerate(proposal.steps):
            expected = () if index == 0 else (proposal.steps[index - 1].step_id,)
            if step.depends_on != expected:
                raise CompositionProposalParseError(
                    "proposal.sequential.dependency_invalid", step.step_id
                )
            if index:
                expected_edges.append((proposal.steps[index - 1].step_id, step.step_id))
        if proposal.dependency_edges != tuple(sorted(expected_edges)):
            raise CompositionProposalParseError(
                "proposal.sequential.edges_invalid"
            )
    elif proposal.control_flow == "DAG":
        if proposal.dependency_edges != derived_edges:
            raise CompositionProposalParseError(
                "proposal.dag.edges_mismatch"
            )
    else:
        raise CompositionProposalParseError(
            "proposal.control_flow.invalid"
        )


def _build_proposal(
    raw: Mapping[str, Any],
    candidates: CompositionCandidateSnapshotV1,
) -> CompositionProposalV1:
    if set(raw) != _RAW_ROOT_FIELDS:
        missing = ",".join(sorted(_RAW_ROOT_FIELDS - set(raw)))
        extra = ",".join(sorted(set(raw) - _RAW_ROOT_FIELDS))
        raise CompositionProposalParseError(
            "proposal.fields.invalid", f"missing={missing};extra={extra}"
        )
    if raw.get("proposal_schema") != _RAW_SCHEMA:
        raise CompositionProposalParseError("proposal.schema.invalid")
    goal_ref = raw.get("goal_ref")
    control_flow = raw.get("control_flow")
    if not isinstance(goal_ref, str) or not goal_ref:
        raise CompositionProposalParseError("proposal.goal_ref.invalid")
    if control_flow not in {"SEQUENTIAL", "DAG"}:
        raise CompositionProposalParseError("proposal.control_flow.invalid")

    try:
        proposal = CompositionProposalV1(
            goal_ref=goal_ref,
            selected_method_candidate_ids=_string_list(
                raw.get("selected_method_candidate_ids"),
                field="selected_method_candidate_ids",
            ),
            selected_action_candidate_ids=_string_list(
                raw.get("selected_action_candidate_ids"),
                field="selected_action_candidate_ids",
            ),
            steps=_steps(raw.get("steps")),
            dependency_edges=_edges(raw.get("dependency_edges")),
            output_bindings=_string_list(
                raw.get("output_bindings"), field="output_bindings"
            ),
            control_flow=control_flow,
            rationale_tags=_string_list(
                raw.get("rationale_tags"), field="rationale_tags"
            ),
            proposal_sha256="0" * 64,
        )
    except ValidationError as exc:
        raise CompositionProposalParseError(
            "proposal.contract.invalid", str(exc)
        ) from exc
    proposal = proposal.model_copy(
        update={"proposal_sha256": computed_proposal_sha256(proposal)}
    )
    _validate_candidate_and_graph_bindings(
        proposal=proposal, candidates=candidates
    )
    return proposal


def parse_composition_proposal(
    text: str,
    candidates: CompositionCandidateSnapshotV1,
) -> CompositionProposalV1:
    """Parse one strict model proposal without any implicit retry."""

    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 65_536:
        raise CompositionProposalParseError("proposal.text.invalid")
    stripped = text.strip()
    raw = _parse_json(stripped) if stripped.startswith("{") else _parse_dsl(stripped)
    return _build_proposal(raw, candidates)


def parse_with_single_repair(
    primary_text: str,
    candidates: CompositionCandidateSnapshotV1,
    *,
    repair_text: str | None = None,
) -> ProposalParseOutcomeV1:
    """Allow at most one externally-produced repair proposal."""

    try:
        proposal = parse_composition_proposal(primary_text, candidates)
        return ProposalParseOutcomeV1(
            proposal=proposal,
            repaired=False,
            primary_error_code=None,
        )
    except CompositionProposalParseError as primary_error:
        if repair_text is None:
            raise
        try:
            proposal = parse_composition_proposal(repair_text, candidates)
        except CompositionProposalParseError as repair_error:
            raise CompositionProposalParseError(
                "proposal.repair.failed",
                f"primary={primary_error.code};repair={repair_error.code}",
            ) from repair_error
        return ProposalParseOutcomeV1(
            proposal=proposal,
            repaired=True,
            primary_error_code=primary_error.code,
        )


__all__ = [
    "CompositionProposalParseError",
    "ProposalParseOutcomeV1",
    "computed_proposal_sha256",
    "parse_composition_proposal",
    "parse_with_single_repair",
    "proposal_has_valid_sha256",
]
