"""G4 CommitmentCompiler: frozen requirements with coverage proof and monotonic revisions.

T06: a missing explicit second artifact obligation blocks completion.
T07: route outputs can add obligations but never reduce them; only a user
Amendment may reduce or downgrade a mandatory obligation.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from contracts import (
    CompletionObligation,
    CompletionRequirementsVNext,
    CoverageProofRow,
    canonical_sha256,
)


SourceRequirement = dict[str, Any]


class CommitmentCompilerError(RuntimeError):
    pass


def _obligation_id(commitment_id: str, source_key: str, kind: str) -> str:
    return "obl_" + canonical_sha256(
        {
            "domain": "tiangong.v21.obligation.v1",
            "commitment_id": commitment_id,
            "source_requirement_stable_key": source_key,
            "kind": kind,
        }
    )


def _canonical_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(refs)))


class CommitmentCompiler:
    """Gateway-owned authoritative requirements compiler (canary authority)."""

    def __init__(
        self,
        *,
        obligation_extractor: Callable[[Mapping[str, Any]], tuple[SourceRequirement, ...]] | None = None,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._obligation_extractor = obligation_extractor or self._default_extractor
        self._now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)

    @staticmethod
    def _default_extractor(inputs: Mapping[str, Any]) -> tuple[SourceRequirement, ...]:
        """Deterministic explicit-requirement extraction from raw user input."""
        raw = str(inputs.get("raw_user_message") or "").strip()
        requirements: list[SourceRequirement] = []
        if raw:
            requirements.append(
                {
                    "source_kind": "user",
                    "stable_key": f"user#{canonical_sha256({'text': raw})[:24]}",
                    "source_refs": tuple(inputs.get("source_input_refs") or ()),
                    "kind": "execution",
                    "mandatory": True,
                    "acceptance_ref": "acceptance:user:goal",
                }
            )
        selected_skill = inputs.get("selected_skill")
        if isinstance(selected_skill, Mapping):
            acceptance = selected_skill.get("acceptance")
            if isinstance(acceptance, Mapping) and acceptance.get("id"):
                requirements.append(
                    {
                        "source_kind": "skill",
                        "stable_key": f"skill:{selected_skill.get('id')}:{acceptance.get('id')}",
                        "source_refs": (
                            str(selected_skill.get("activation_sha256") or ""),
                        ),
                        "kind": "artifact",
                        "mandatory": True,
                        "acceptance_ref": str(acceptance.get("id")),
                    }
                )
        explicit_outputs = inputs.get("explicit_output_constraints")
        if isinstance(explicit_outputs, (list, tuple)):
            for index, output in enumerate(explicit_outputs):
                requirements.append(
                    {
                        "source_kind": "user",
                        "stable_key": f"user:output#{index}",
                        "source_refs": (str(output.get("ref") or ""),),
                        "kind": "artifact",
                        "mandatory": True,
                        "acceptance_ref": str(output.get("acceptance_ref") or "acceptance:user:output"),
                    }
                )
        delivery_constraints = inputs.get("delivery_constraints")
        if isinstance(delivery_constraints, (list, tuple)):
            for index, delivery in enumerate(delivery_constraints):
                requirements.append(
                    {
                        "source_kind": "user",
                        "stable_key": f"user:delivery#{index}",
                        "source_refs": (str(delivery.get("ref") or ""),),
                        "kind": "delivery",
                        "mandatory": bool(delivery.get("mandatory", True)),
                        "acceptance_ref": str(
                            delivery.get("acceptance_ref") or "acceptance:user:delivery"
                        ),
                        "delivery_phase": "response",
                    }
                )
        return tuple(requirements)

    def compile(
        self,
        *,
        commitment_id: str,
        request_id: str,
        run_id: str,
        run_sequence: int,
        generation: int,
        root_experience_id: str,
        raw_user_message: str,
        source_input_refs: tuple[str, ...],
        selected_skill: Mapping[str, Any] | None = None,
        explicit_output_constraints: tuple[Mapping[str, Any], ...] = (),
        delivery_constraints: tuple[Mapping[str, Any], ...] = (),
        commitment_revision: int = 1,
        supersedes_sha256: str | None = None,
    ) -> CompletionRequirementsVNext:
        """Compile and freeze one requirements revision with coverage proof."""
        inputs: Mapping[str, Any] = {
            "raw_user_message": raw_user_message,
            "source_input_refs": _canonical_refs(source_input_refs),
            "selected_skill": selected_skill or {},
            "explicit_output_constraints": explicit_output_constraints,
            "delivery_constraints": delivery_constraints,
        }
        extracted = self._obligation_extractor(inputs)
        if not extracted:
            raise CommitmentCompilerError("work commitment requires at least one explicit obligation")
        obligations: list[CompletionObligation] = []
        coverage_by_key: dict[str, list[str]] = {}
        for requirement in extracted:
            stable_key = str(requirement["stable_key"])
            kind = str(requirement.get("kind") or "execution")
            delivery_phase = requirement.get("delivery_phase")
            obligation = CompletionObligation(
                obligation_id=_obligation_id(commitment_id, stable_key, kind),
                kind=kind,
                source_kind=str(requirement["source_kind"]),
                source_requirement_stable_key=stable_key,
                source_refs=_canonical_refs(tuple(requirement.get("source_refs") or ())),
                mandatory=bool(requirement.get("mandatory", True)),
                acceptance_ref=str(requirement.get("acceptance_ref") or ""),
                delivery_phase=delivery_phase,
            )
            obligations.append(obligation)
            coverage_by_key.setdefault(stable_key, []).append(obligation.obligation_id)
        coverage = tuple(
            CoverageProofRow(
                source_requirement_stable_key=key,
                source_refs=tuple(
                    sorted(
                        {
                            ref
                            for item in obligations
                            if item.source_requirement_stable_key == key
                            for ref in item.source_refs
                        }
                    )
                ),
                obligation_ids=tuple(sorted(ids)),
                coverage_status="COVERED",
            )
            for key, ids in sorted(coverage_by_key.items())
        )
        requirements = CompletionRequirementsVNext(
            commitment_id=commitment_id,
            commitment_sha256="0" * 64,
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation,
            root_experience_id=root_experience_id,
            raw_goal_sha256=canonical_sha256({"text": raw_user_message}),
            source_input_refs=_canonical_refs(source_input_refs),
            source_input_set_sha256=canonical_sha256(list(_canonical_refs(source_input_refs))),
            commitment_revision=commitment_revision,
            obligations=tuple(obligations),
            obligation_set_sha256=canonical_sha256(
                [item.model_dump(mode="json") for item in obligations]
            ),
            coverage_proof=coverage,
            requirements_sha256="0" * 64,
            selected_skill_activation_sha256=(
                str(selected_skill.get("activation_sha256")) if selected_skill else None
            ),
            supersedes_sha256=supersedes_sha256,
        ).with_computed_sha256()
        if not requirements.has_valid_requirements_sha256():
            raise CommitmentCompilerError("compiled requirements digest is invalid")
        return requirements

    def amend(
        self,
        current: CompletionRequirementsVNext,
        *,
        user_amendment: bool,
        raw_user_message: str,
        explicit_output_constraints: tuple[Mapping[str, Any], ...] = (),
    ) -> CompletionRequirementsVNext:
        """Monotonic update: add-only unless a user Amendment authorizes reduction."""
        next_revision = current.commitment_revision + 1
        updated = self.compile(
            commitment_id=current.commitment_id,
            request_id=current.request_id,
            run_id=current.run_id,
            run_sequence=current.run_sequence,
            generation=current.generation,
            root_experience_id=current.root_experience_id,
            raw_user_message=raw_user_message,
            source_input_refs=current.source_input_refs,
            selected_skill=(
                {"activation_sha256": current.selected_skill_activation_sha256}
                if current.selected_skill_activation_sha256
                else None
            ),
            explicit_output_constraints=explicit_output_constraints,
            commitment_revision=next_revision,
            supersedes_sha256=current.requirements_sha256,
        )
        if not user_amendment:
            current_mandatory = {
                item.source_requirement_stable_key
                for item in current.obligations
                if item.mandatory
            }
            updated_keys = {
                item.source_requirement_stable_key for item in updated.obligations
            }
            missing = current_mandatory - updated_keys
            if missing:
                raise CommitmentCompilerError(
                    f"route output cannot reduce obligations: {sorted(missing)}"
                )
            downgraded = [
                key
                for key in current_mandatory
                if any(
                    item.source_requirement_stable_key == key and not item.mandatory
                    for item in updated.obligations
                )
            ]
            if downgraded:
                raise CommitmentCompilerError(
                    f"route output cannot downgrade mandatory obligations: {sorted(downgraded)}"
                )
        return updated

    @staticmethod
    def completion_ready(
        requirements: CompletionRequirementsVNext,
        satisfied_obligation_ids: set[str],
    ) -> bool:
        """T06: every mandatory obligation must be terminal before completion."""
        mandatory = {
            item.obligation_id
            for item in requirements.obligations
            if item.mandatory and item.source_kind in {"user", "skill", "derived_necessity"}
        }
        return bool(mandatory) and mandatory <= set(satisfied_obligation_ids)

    @staticmethod
    def classify_delivery_mode(
        requirements: CompletionRequirementsVNext,
        terminal_obligation_ids: set[str],
    ) -> str:
        """T05c: response delivery stays classified after all other obligations terminal."""
        unsatisfied = [
            item
            for item in requirements.obligations
            if item.obligation_id not in terminal_obligation_ids
        ]
        if not unsatisfied:
            return "none"
        if all(
            item.kind == "delivery" and item.delivery_phase == "response"
            for item in unsatisfied
        ):
            return "response_delivery"
        return "invalid"
