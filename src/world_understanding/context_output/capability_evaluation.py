"""Deterministic P6 context evaluation over recorded or live observations.

The evaluator never calls a model and never labels recorded protocol fixtures as
live provider performance. It measures the exact properties required by the P6
gate: candidate precision/recall, token budget, frame exactness, one current
WorldState, identity-preserving compaction, experience recall, stale-descriptor
guards, and cross-profile proposal variance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Literal

from contracts.canonical import canonical_sha256

from .capability_context import (
    CapabilityContextBuildResultV1,
    CapabilityContextPacketV1,
)


EvidenceMode = Literal["RECORDED_FIXTURE", "LIVE_PROVIDER"]


def _sorted_unique(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must be sorted and unique")
    return values


def _ratio_milli(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 1000 if numerator == 0 else 0
    return min(1000, (max(0, numerator) * 1000) // denominator)


def _precision_milli(retrieved: set[str], expected: set[str]) -> int:
    return _ratio_milli(len(retrieved & expected), len(retrieved))


def _recall_milli(retrieved: set[str], expected: set[str]) -> int:
    return _ratio_milli(len(retrieved & expected), len(expected))


def _percentile_nearest_rank(values: tuple[int, ...], percentile: int) -> int:
    if not values or not 1 <= percentile <= 100:
        raise ValueError("percentile input is invalid")
    ordered = tuple(sorted(values))
    rank = max(1, (len(ordered) * percentile + 99) // 100)
    return ordered[min(len(ordered), rank) - 1]


@dataclass(frozen=True, slots=True)
class P6ProposalProfileObservationV1:
    profile_id: str
    proposal_signature_sha256: str

    def __post_init__(self) -> None:
        if not self.profile_id or len(self.profile_id) > 160:
            raise ValueError("P6 proposal profile identity is invalid")
        if len(self.proposal_signature_sha256) != 64:
            raise ValueError("P6 proposal signature is invalid")


@dataclass(frozen=True, slots=True)
class P6ContextEvaluationInputV1:
    task_id: str
    packet: CapabilityContextPacketV1
    build_result: CapabilityContextBuildResultV1
    token_budget: int
    legacy_static_context_tokens: int
    expected_frame_binding_sha256: str
    current_world_state_count: int
    expected_method_refs: tuple[str, ...]
    expected_action_refs: tuple[str, ...]
    expected_experience_refs: tuple[str, ...]
    proposal_profiles: tuple[P6ProposalProfileObservationV1, ...]
    stale_descriptor_guard_passed: bool

    def __post_init__(self) -> None:
        if not self.task_id or len(self.task_id) > 160:
            raise ValueError("P6 evaluation task identity is invalid")
        if self.token_budget <= 0 or self.legacy_static_context_tokens <= 0:
            raise ValueError("P6 evaluation token budgets are invalid")
        if len(self.expected_frame_binding_sha256) != 64:
            raise ValueError("P6 expected frame binding is invalid")
        _sorted_unique(self.expected_method_refs, field="expected_method_refs")
        _sorted_unique(self.expected_action_refs, field="expected_action_refs")
        _sorted_unique(
            self.expected_experience_refs,
            field="expected_experience_refs",
        )
        profile_ids = tuple(item.profile_id for item in self.proposal_profiles)
        if len(profile_ids) < 2 or profile_ids != tuple(sorted(set(profile_ids))):
            raise ValueError("P6 proposal profiles must be sorted and unique")
        if not self.packet.has_valid_sha256() or not self.build_result.has_valid_sha256():
            raise ValueError("P6 evaluation input contains an invalid hash")


@dataclass(frozen=True, slots=True)
class P6ContextEvaluationCaseV1:
    task_id: str
    status: str
    method_precision_milli: int
    method_recall_milli: int
    action_precision_milli: int
    action_recall_milli: int
    experience_precision_milli: int
    experience_recall_milli: int
    context_tokens: int
    token_budget: int
    legacy_static_context_tokens: int
    frame_exact: bool
    one_world_state: bool
    authority_safe: bool
    protected_identity_preserved: bool
    stale_descriptor_guard_passed: bool
    proposal_profile_count: int
    proposal_signature_count: int
    proposal_variance_milli: int
    case_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "method_precision_milli": self.method_precision_milli,
            "method_recall_milli": self.method_recall_milli,
            "action_precision_milli": self.action_precision_milli,
            "action_recall_milli": self.action_recall_milli,
            "experience_precision_milli": self.experience_precision_milli,
            "experience_recall_milli": self.experience_recall_milli,
            "context_tokens": self.context_tokens,
            "token_budget": self.token_budget,
            "legacy_static_context_tokens": self.legacy_static_context_tokens,
            "frame_exact": self.frame_exact,
            "one_world_state": self.one_world_state,
            "authority_safe": self.authority_safe,
            "protected_identity_preserved": self.protected_identity_preserved,
            "stale_descriptor_guard_passed": self.stale_descriptor_guard_passed,
            "proposal_profile_count": self.proposal_profile_count,
            "proposal_signature_count": self.proposal_signature_count,
            "proposal_variance_milli": self.proposal_variance_milli,
        }

    def has_valid_sha256(self) -> bool:
        return self.case_sha256 == canonical_sha256(self.payload())


@dataclass(frozen=True, slots=True)
class P6ContextEvaluationReportV1:
    schema: str
    evidence_mode: EvidenceMode
    task_count: int
    cases: tuple[P6ContextEvaluationCaseV1, ...]
    median_method_precision_milli: int
    median_method_recall_milli: int
    median_action_precision_milli: int
    median_action_recall_milli: int
    median_experience_recall_milli: int
    median_context_tokens: int
    p95_context_tokens: int
    median_legacy_static_context_tokens: int
    median_token_ratio_milli: int
    divergent_proposal_task_count: int
    frame_exact_count: int
    one_world_state_count: int
    authority_safe_count: int
    protected_identity_preserved_count: int
    stale_descriptor_guard_count: int
    gate_passed: bool
    report_sha256: str

    def __post_init__(self) -> None:
        if self.schema != "tiangong.p6-context-evaluation.v1":
            raise ValueError("P6 evaluation schema is invalid")
        if self.evidence_mode not in {"RECORDED_FIXTURE", "LIVE_PROVIDER"}:
            raise ValueError("P6 evaluation evidence mode is invalid")
        if not 50 <= self.task_count <= 60 or self.task_count != len(self.cases):
            raise ValueError("P6 evaluation requires 50-60 task cases")
        case_ids = tuple(item.task_id for item in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("P6 evaluation cases are not canonical")
        if any(not item.has_valid_sha256() for item in self.cases):
            raise ValueError("P6 evaluation case hash is invalid")

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "evidence_mode": self.evidence_mode,
            "task_count": self.task_count,
            "cases": [
                {**item.payload(), "case_sha256": item.case_sha256}
                for item in self.cases
            ],
            "median_method_precision_milli": self.median_method_precision_milli,
            "median_method_recall_milli": self.median_method_recall_milli,
            "median_action_precision_milli": self.median_action_precision_milli,
            "median_action_recall_milli": self.median_action_recall_milli,
            "median_experience_recall_milli": self.median_experience_recall_milli,
            "median_context_tokens": self.median_context_tokens,
            "p95_context_tokens": self.p95_context_tokens,
            "median_legacy_static_context_tokens": self.median_legacy_static_context_tokens,
            "median_token_ratio_milli": self.median_token_ratio_milli,
            "divergent_proposal_task_count": self.divergent_proposal_task_count,
            "frame_exact_count": self.frame_exact_count,
            "one_world_state_count": self.one_world_state_count,
            "authority_safe_count": self.authority_safe_count,
            "protected_identity_preserved_count": self.protected_identity_preserved_count,
            "stale_descriptor_guard_count": self.stale_descriptor_guard_count,
            "gate_passed": self.gate_passed,
        }

    def has_valid_sha256(self) -> bool:
        return self.report_sha256 == canonical_sha256(self.payload())


def evaluate_p6_context_case(
    data: P6ContextEvaluationInputV1,
) -> P6ContextEvaluationCaseV1:
    packet = data.packet
    result = data.build_result
    methods = {item.method_ref for item in packet.method_candidates}
    actions = {item.action_ref for item in packet.action_candidates}
    experiences = {
        item.experience_id for item in packet.procedural_experience
    }
    expected_methods = set(data.expected_method_refs)
    expected_actions = set(data.expected_action_refs)
    expected_experiences = set(data.expected_experience_refs)

    rendered = result.slot.rendered_text
    protected_lines = {
        f"{item.key}={item.value}" for item in packet.protected_identities
    }
    candidate_lines = {
        f"candidate_id={item.candidate_id}" for item in packet.method_candidates
    } | {
        f"candidate_id={item.candidate_id}" for item in packet.action_candidates
    }
    semantic_refs = {
        f"method_ref={item.method_ref}" for item in packet.method_candidates
    } | {
        f"action_ref={item.action_ref}" for item in packet.action_candidates
    }
    source_revisions = {
        f"source_revision={item.source_revision}"
        for item in packet.method_candidates
    } | {
        f"source_revision={item.source_revision}"
        for item in packet.action_candidates
    }
    identity_preserved = result.status == "AVAILABLE" and all(
        line in rendered
        for line in (
            protected_lines | candidate_lines | semantic_refs | source_revisions
        )
    )
    authority_safe = (
        packet.context_only
        and not packet.authorization_source
        and not packet.authorizes
        and not packet.confirms
        and not packet.changes_risk
        and not packet.may_execute
        and "authorization_source=false" in rendered
        and "authorizes=false" in rendered
        and "confirms=false" in rendered
        and "changes_risk=false" in rendered
        and "may_execute=false" in rendered
    )
    signatures = {
        item.proposal_signature_sha256 for item in data.proposal_profiles
    }
    profile_count = len(data.proposal_profiles)
    variance = _ratio_milli(len(signatures) - 1, profile_count - 1)
    value = P6ContextEvaluationCaseV1(
        task_id=data.task_id,
        status=result.status,
        method_precision_milli=_precision_milli(methods, expected_methods),
        method_recall_milli=_recall_milli(methods, expected_methods),
        action_precision_milli=_precision_milli(actions, expected_actions),
        action_recall_milli=_recall_milli(actions, expected_actions),
        experience_precision_milli=_precision_milli(
            experiences, expected_experiences
        ),
        experience_recall_milli=_recall_milli(
            experiences, expected_experiences
        ),
        context_tokens=result.slot.estimated_tokens,
        token_budget=data.token_budget,
        legacy_static_context_tokens=data.legacy_static_context_tokens,
        frame_exact=(
            packet.frame_binding_sha256
            == data.expected_frame_binding_sha256
        ),
        one_world_state=data.current_world_state_count == 1,
        authority_safe=authority_safe,
        protected_identity_preserved=identity_preserved,
        stale_descriptor_guard_passed=data.stale_descriptor_guard_passed,
        proposal_profile_count=profile_count,
        proposal_signature_count=len(signatures),
        proposal_variance_milli=variance,
        case_sha256="0" * 64,
    )
    return replace(value, case_sha256=canonical_sha256(value.payload()))


def build_p6_context_evaluation_report(
    inputs: tuple[P6ContextEvaluationInputV1, ...],
    *,
    evidence_mode: EvidenceMode,
) -> P6ContextEvaluationReportV1:
    if not 50 <= len(inputs) <= 60:
        raise ValueError("P6 evaluation requires 50-60 task inputs")
    cases = tuple(
        sorted(
            (evaluate_p6_context_case(item) for item in inputs),
            key=lambda item: item.task_id,
        )
    )
    context_tokens = tuple(item.context_tokens for item in cases)
    legacy_tokens = tuple(item.legacy_static_context_tokens for item in cases)
    median_context = int(median(context_tokens))
    median_legacy = int(median(legacy_tokens))
    task_count = len(cases)
    frame_count = sum(item.frame_exact for item in cases)
    one_world_count = sum(item.one_world_state for item in cases)
    authority_count = sum(item.authority_safe for item in cases)
    identity_count = sum(item.protected_identity_preserved for item in cases)
    stale_count = sum(item.stale_descriptor_guard_passed for item in cases)
    med_method_precision = int(
        median(item.method_precision_milli for item in cases)
    )
    med_method_recall = int(
        median(item.method_recall_milli for item in cases)
    )
    med_action_precision = int(
        median(item.action_precision_milli for item in cases)
    )
    med_action_recall = int(
        median(item.action_recall_milli for item in cases)
    )
    med_experience_recall = int(
        median(item.experience_recall_milli for item in cases)
    )
    gate = (
        all(item.status == "AVAILABLE" for item in cases)
        and all(item.context_tokens <= item.token_budget for item in cases)
        and med_method_precision >= 500
        and med_method_recall == 1000
        and med_action_precision >= 500
        and med_action_recall == 1000
        and med_experience_recall == 1000
        and frame_count == task_count
        and one_world_count == task_count
        and authority_count == task_count
        and identity_count == task_count
        and stale_count == task_count
        and median_context < median_legacy
    )
    value = P6ContextEvaluationReportV1(
        schema="tiangong.p6-context-evaluation.v1",
        evidence_mode=evidence_mode,
        task_count=task_count,
        cases=cases,
        median_method_precision_milli=med_method_precision,
        median_method_recall_milli=med_method_recall,
        median_action_precision_milli=med_action_precision,
        median_action_recall_milli=med_action_recall,
        median_experience_recall_milli=med_experience_recall,
        median_context_tokens=median_context,
        p95_context_tokens=_percentile_nearest_rank(context_tokens, 95),
        median_legacy_static_context_tokens=median_legacy,
        median_token_ratio_milli=_ratio_milli(median_context, median_legacy),
        divergent_proposal_task_count=sum(
            item.proposal_signature_count > 1 for item in cases
        ),
        frame_exact_count=frame_count,
        one_world_state_count=one_world_count,
        authority_safe_count=authority_count,
        protected_identity_preserved_count=identity_count,
        stale_descriptor_guard_count=stale_count,
        gate_passed=gate,
        report_sha256="0" * 64,
    )
    return replace(value, report_sha256=canonical_sha256(value.payload()))


__all__ = [
    "P6ContextEvaluationCaseV1",
    "P6ContextEvaluationInputV1",
    "P6ContextEvaluationReportV1",
    "P6ProposalProfileObservationV1",
    "build_p6_context_evaluation_report",
    "evaluate_p6_context_case",
]
