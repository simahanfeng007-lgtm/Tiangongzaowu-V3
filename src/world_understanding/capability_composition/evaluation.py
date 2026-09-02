"""P4 early-evaluation harness for proposal/compiler/validator outputs.

The harness accepts either recorded fixtures or outputs captured from live
providers. It never performs network calls and never represents recorded
protocol fixtures as live model performance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from contracts import ActionRegistrySnapshot, canonical_sha256

from .compiler import compile_capability_composition_plan
from .models import (
    CapabilityCompositionError,
    CompositionCandidateSnapshotV1,
    CompositionCompileContextV1,
)
from .parser import (
    CompositionProposalParseError,
    parse_with_single_repair,
)
from .validator import validate_capability_composition_plan


@dataclass(frozen=True, slots=True)
class P4EvaluationInputV1:
    task_id: str
    model_id: str
    primary_text: str
    repair_text: str | None = None


@dataclass(frozen=True, slots=True)
class P4EvaluationCaseResultV1:
    task_id: str
    model_id: str
    evidence_mode: str
    parse_succeeded: bool
    repaired: bool
    primary_error_code: str | None
    plan_succeeded: bool
    validation_result: str | None
    validation_disposition: str | None
    proposal_sha256: str | None
    plan_sha256: str | None
    validation_sha256: str | None
    error_code: str | None

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_id": self.model_id,
            "evidence_mode": self.evidence_mode,
            "parse_succeeded": self.parse_succeeded,
            "repaired": self.repaired,
            "primary_error_code": self.primary_error_code,
            "plan_succeeded": self.plan_succeeded,
            "validation_result": self.validation_result,
            "validation_disposition": self.validation_disposition,
            "proposal_sha256": self.proposal_sha256,
            "plan_sha256": self.plan_sha256,
            "validation_sha256": self.validation_sha256,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class P4ModelMetricsV1:
    model_id: str
    case_count: int
    primary_parse_failure_count: int
    repair_attempt_count: int
    repair_success_count: int
    final_parse_failure_count: int
    plan_compile_failure_count: int
    proved_valid_count: int
    proved_invalid_count: int
    unknown_count: int

    def payload(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "case_count": self.case_count,
            "primary_parse_failure_count": self.primary_parse_failure_count,
            "repair_attempt_count": self.repair_attempt_count,
            "repair_success_count": self.repair_success_count,
            "final_parse_failure_count": self.final_parse_failure_count,
            "plan_compile_failure_count": self.plan_compile_failure_count,
            "proved_valid_count": self.proved_valid_count,
            "proved_invalid_count": self.proved_invalid_count,
            "unknown_count": self.unknown_count,
        }


@dataclass(frozen=True, slots=True)
class P4EarlyEvaluationReportV1:
    schema: str
    evidence_mode: str
    cases: tuple[P4EvaluationCaseResultV1, ...]
    model_metrics: tuple[P4ModelMetricsV1, ...]
    report_sha256: str

    def __post_init__(self) -> None:
        if self.schema != "tiangong.p4-early-evaluation.v1":
            raise CapabilityCompositionError("evaluation.schema.invalid")
        if self.evidence_mode not in {"RECORDED_FIXTURE", "LIVE_PROVIDER"}:
            raise CapabilityCompositionError("evaluation.evidence_mode.invalid")
        case_keys = tuple((item.model_id, item.task_id) for item in self.cases)
        if not case_keys or case_keys != tuple(sorted(set(case_keys))):
            raise CapabilityCompositionError("evaluation.cases.invalid")
        model_ids = tuple(item.model_id for item in self.model_metrics)
        if model_ids != tuple(sorted(set(model_ids))):
            raise CapabilityCompositionError("evaluation.models.invalid")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "evidence_mode": self.evidence_mode,
            "cases": [item.payload() for item in self.cases],
            "model_metrics": [item.payload() for item in self.model_metrics],
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.report_sha256 == self.computed_sha256()


def _metrics(
    model_id: str,
    results: tuple[P4EvaluationCaseResultV1, ...],
) -> P4ModelMetricsV1:
    selected = tuple(item for item in results if item.model_id == model_id)
    return P4ModelMetricsV1(
        model_id=model_id,
        case_count=len(selected),
        primary_parse_failure_count=sum(
            item.primary_error_code is not None for item in selected
        ),
        repair_attempt_count=sum(
            item.primary_error_code is not None for item in selected
        ),
        repair_success_count=sum(item.repaired for item in selected),
        final_parse_failure_count=sum(
            not item.parse_succeeded for item in selected
        ),
        plan_compile_failure_count=sum(
            item.parse_succeeded and not item.plan_succeeded
            for item in selected
        ),
        proved_valid_count=sum(
            item.validation_result == "PROVED_VALID" for item in selected
        ),
        proved_invalid_count=sum(
            item.validation_result == "PROVED_INVALID" for item in selected
        ),
        unknown_count=sum(
            item.validation_result == "UNKNOWN" for item in selected
        ),
    )


def run_p4_early_evaluation(
    inputs: tuple[P4EvaluationInputV1, ...],
    *,
    candidates_by_task: Mapping[str, CompositionCandidateSnapshotV1],
    contexts_by_task: Mapping[str, CompositionCompileContextV1],
    registry: ActionRegistrySnapshot,
    available_verifiers: frozenset[str],
    validated_at_ms: int,
    evidence_mode: str,
) -> P4EarlyEvaluationReportV1:
    """Evaluate already-produced proposal texts without executing Actions."""

    if evidence_mode not in {"RECORDED_FIXTURE", "LIVE_PROVIDER"}:
        raise CapabilityCompositionError("evaluation.evidence_mode.invalid")
    keys = tuple((item.model_id, item.task_id) for item in inputs)
    if not keys or len(keys) != len(set(keys)):
        raise CapabilityCompositionError("evaluation.input.identity_invalid")

    results: list[P4EvaluationCaseResultV1] = []
    for item in sorted(inputs, key=lambda value: (value.model_id, value.task_id)):
        candidates = candidates_by_task.get(item.task_id)
        context = contexts_by_task.get(item.task_id)
        if candidates is None or context is None:
            raise CapabilityCompositionError(
                "evaluation.task_binding.missing", item.task_id
            )
        try:
            parsed = parse_with_single_repair(
                item.primary_text,
                candidates,
                repair_text=item.repair_text,
            )
        except CompositionProposalParseError as exc:
            primary_error_code = exc.code
            if exc.code == "proposal.repair.failed":
                for part in exc.detail.split(";"):
                    if part.startswith("primary="):
                        primary_error_code = part.partition("=")[2] or exc.code
                        break
            results.append(
                P4EvaluationCaseResultV1(
                    task_id=item.task_id,
                    model_id=item.model_id,
                    evidence_mode=evidence_mode,
                    parse_succeeded=False,
                    repaired=False,
                    primary_error_code=primary_error_code,
                    plan_succeeded=False,
                    validation_result=None,
                    validation_disposition=None,
                    proposal_sha256=None,
                    plan_sha256=None,
                    validation_sha256=None,
                    error_code=exc.code,
                )
            )
            continue

        proposal = parsed.proposal
        try:
            plan = compile_capability_composition_plan(
                proposal, candidates, context, registry
            )
        except CapabilityCompositionError as exc:
            results.append(
                P4EvaluationCaseResultV1(
                    task_id=item.task_id,
                    model_id=item.model_id,
                    evidence_mode=evidence_mode,
                    parse_succeeded=True,
                    repaired=parsed.repaired,
                    primary_error_code=parsed.primary_error_code,
                    plan_succeeded=False,
                    validation_result=None,
                    validation_disposition=None,
                    proposal_sha256=proposal.proposal_sha256,
                    plan_sha256=None,
                    validation_sha256=None,
                    error_code=exc.code,
                )
            )
            continue

        validation = validate_capability_composition_plan(
            plan,
            proposal,
            candidates,
            context,
            registry,
            available_verifiers=available_verifiers,
            validated_at_ms=validated_at_ms,
        )
        results.append(
            P4EvaluationCaseResultV1(
                task_id=item.task_id,
                model_id=item.model_id,
                evidence_mode=evidence_mode,
                parse_succeeded=True,
                repaired=parsed.repaired,
                primary_error_code=parsed.primary_error_code,
                plan_succeeded=True,
                validation_result=validation.result,
                validation_disposition=validation.unknown_disposition,
                proposal_sha256=proposal.proposal_sha256,
                plan_sha256=plan.plan_sha256,
                validation_sha256=validation.validation_sha256,
                error_code=None,
            )
        )

    ordered_results = tuple(
        sorted(results, key=lambda item: (item.model_id, item.task_id))
    )
    model_ids = tuple(sorted({item.model_id for item in ordered_results}))
    report = P4EarlyEvaluationReportV1(
        schema="tiangong.p4-early-evaluation.v1",
        evidence_mode=evidence_mode,
        cases=ordered_results,
        model_metrics=tuple(
            _metrics(model_id, ordered_results) for model_id in model_ids
        ),
        report_sha256="0" * 64,
    )
    return replace(report, report_sha256=report.computed_sha256())


__all__ = [
    "P4EarlyEvaluationReportV1",
    "P4EvaluationCaseResultV1",
    "P4EvaluationInputV1",
    "P4ModelMetricsV1",
    "run_p4_early_evaluation",
]
