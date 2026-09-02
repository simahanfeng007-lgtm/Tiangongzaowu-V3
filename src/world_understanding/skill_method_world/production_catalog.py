"""Reviewed production seed catalog for the P3 Skill Method World.

The catalog contains reusable method semantics only.  It intentionally carries
no Action IDs, handlers, permissions, grants, tickets, or Runtime bindings.
Static Skills remain migration evidence and the current production planner is
unchanged until the later cutover phases.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from contracts import canonical_sha256
from contracts.capability_composition import (
    SkillSourcePrimitiveV1,
    SourceRevisionRefV1,
)

from .compiler import (
    compile_skill_method_world,
    computed_skill_method_descriptor_sha256,
    method_source_revision_sha256,
    observe_legacy_skill_method_corpus,
)
from .models import (
    LegacySkillMethodCorpusV1,
    MethodMigrationBindingV1,
    SkillMethodWorldError,
    SkillMethodWorldSnapshotV1,
)


@dataclass(frozen=True, slots=True)
class ReviewedMethodSeedV1:
    """One human-reviewed, zero-authority method source candidate."""

    method_id: str
    version: str
    title: str
    semantic_summary: str
    goal_classes: tuple[str, ...]
    preconditions: tuple[str, ...]
    expected_postconditions: tuple[str, ...]
    required_capability_classes: tuple[str, ...]
    method_steps: tuple[str, ...]
    control_flow_hints: tuple[str, ...]
    failure_modes: tuple[str, ...]
    fallback_patterns: tuple[str, ...]
    verification_intent: tuple[str, ...]
    composition_tags: tuple[str, ...]
    legacy_skill_ids: tuple[str, ...]
    required_phases: tuple[str, ...]
    seed_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.may_authorize or self.may_execute:
            raise SkillMethodWorldError("reviewed method seed is non-authorizing")
        if (
            len(self.legacy_skill_ids) < 2
            or self.legacy_skill_ids != tuple(sorted(set(self.legacy_skill_ids)))
        ):
            raise SkillMethodWorldError(
                "reviewed method seed requires at least two sorted legacy Skills"
            )
        if (
            not self.required_phases
            or self.required_phases != tuple(sorted(set(self.required_phases)))
        ):
            raise SkillMethodWorldError(
                "reviewed method seed phases must be sorted and unique"
            )
        if (
            not self.method_steps
            or len(self.method_steps) != len(set(self.method_steps))
        ):
            raise SkillMethodWorldError(
                "reviewed method seed steps must be non-empty and unique"
            )

    def payload(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.reviewed-skill-method-seed.v1",
            "method_id": self.method_id,
            "version": self.version,
            "title": self.title,
            "semantic_summary": self.semantic_summary,
            "goal_classes": list(self.goal_classes),
            "preconditions": list(self.preconditions),
            "expected_postconditions": list(self.expected_postconditions),
            "required_capability_classes": list(self.required_capability_classes),
            "method_steps": list(self.method_steps),
            "control_flow_hints": list(self.control_flow_hints),
            "failure_modes": list(self.failure_modes),
            "fallback_patterns": list(self.fallback_patterns),
            "verification_intent": list(self.verification_intent),
            "composition_tags": list(self.composition_tags),
            "legacy_skill_ids": list(self.legacy_skill_ids),
            "required_phases": list(self.required_phases),
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.seed_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "ReviewedMethodSeedV1":
        return replace(self, seed_sha256=self.computed_sha256())


_COMMON_DELIVERY_SKILLS = (
    "skill_code_project_delivery_worldclass_v1",
    "skill_ppt_executive_report_worldclass_v1",
    "skill_research_literature_review_worldclass_v1",
    "skill_short_video_delivery_worldclass_v1",
    "skill_word_business_proposal_worldclass_v1",
)


def _seed(**values: Any) -> ReviewedMethodSeedV1:
    return ReviewedMethodSeedV1(seed_sha256="0" * 64, **values).with_computed_sha256()


PRODUCTION_METHOD_SEEDS: tuple[ReviewedMethodSeedV1, ...] = tuple(
    sorted(
        (
            _seed(
                method_id="acceptance_review",
                version="v1",
                title="Acceptance review",
                semantic_summary=(
                    "Evaluate a produced outcome against explicit acceptance "
                    "obligations and preserve unresolved findings."
                ),
                goal_classes=("goal-class:verified-deliverable",),
                preconditions=("condition:acceptance-obligations-defined",),
                expected_postconditions=("condition:acceptance-status-recorded",),
                required_capability_classes=(
                    "capability-class:evidence-inspection",
                    "capability-class:verification-evaluation",
                ),
                method_steps=(
                    "method-step:01-collect-terminal-evidence",
                    "method-step:02-evaluate-each-obligation",
                    "method-step:03-record-unresolved-findings",
                ),
                control_flow_hints=("control-flow:fail-closed-on-unresolved-obligation",),
                failure_modes=("failure-mode:acceptance-evidence-incomplete",),
                fallback_patterns=("fallback-pattern:obtain-missing-verification-evidence",),
                verification_intent=("verification-intent:plan-bound-acceptance",),
                composition_tags=("composition-tag:terminal-review",),
                legacy_skill_ids=_COMMON_DELIVERY_SKILLS,
                required_phases=("ACCEPTANCE", "VERIFICATION"),
            ),
            _seed(
                method_id="decompose_goal",
                version="v1",
                title="Goal decomposition",
                semantic_summary=(
                    "Convert a complex deliverable goal into explicit outcomes, "
                    "constraints, and a reviewable work sequence."
                ),
                goal_classes=("goal-class:complex-deliverable",),
                preconditions=("condition:user-goal-present",),
                expected_postconditions=("condition:goal-decomposition-explicit",),
                required_capability_classes=("capability-class:goal-analysis",),
                method_steps=(
                    "method-step:01-identify-required-outcome",
                    "method-step:02-identify-constraints-and-unknowns",
                    "method-step:03-form-reviewable-work-sequence",
                ),
                control_flow_hints=("control-flow:sequential",),
                failure_modes=("failure-mode:goal-remains-ambiguous",),
                fallback_patterns=("fallback-pattern:surface-minimum-missing-context",),
                verification_intent=("verification-intent:goal-constraint-consistency",),
                composition_tags=("composition-tag:planning-method",),
                legacy_skill_ids=_COMMON_DELIVERY_SKILLS,
                required_phases=("PREPARATION",),
            ),
            _seed(
                method_id="finalize_verified_artifact",
                version="v1",
                title="Verified artifact finalization",
                semantic_summary=(
                    "Finalize an artifact only after verification evidence is "
                    "available, while keeping finalization distinct from completion."
                ),
                goal_classes=("goal-class:deliverable-finalization",),
                preconditions=("condition:verification-evidence-available",),
                expected_postconditions=("condition:artifact-ready-for-completion-review",),
                required_capability_classes=(
                    "capability-class:artifact-finalization",
                    "capability-class:evidence-inspection",
                ),
                method_steps=(
                    "method-step:01-confirm-verification-evidence",
                    "method-step:02-finalize-deliverable-form",
                    "method-step:03-preserve-finalization-evidence",
                ),
                control_flow_hints=("control-flow:verification-before-finalization",),
                failure_modes=("failure-mode:finalization-precedes-verification",),
                fallback_patterns=("fallback-pattern:return-to-verification",),
                verification_intent=("verification-intent:final-artifact-integrity",),
                composition_tags=("composition-tag:pre-completion-method",),
                legacy_skill_ids=_COMMON_DELIVERY_SKILLS,
                required_phases=("FINALIZATION", "VERIFICATION"),
            ),
            _seed(
                method_id="generate_then_verify",
                version="v1",
                title="Generate then verify",
                semantic_summary=(
                    "Produce an outcome, collect evidence, and verify the result "
                    "before it can be treated as complete."
                ),
                goal_classes=("goal-class:artifact-production",),
                preconditions=("condition:production-target-defined",),
                expected_postconditions=("condition:production-outcome-verified",),
                required_capability_classes=(
                    "capability-class:artifact-production",
                    "capability-class:artifact-verification",
                ),
                method_steps=(
                    "method-step:01-produce-outcome",
                    "method-step:02-collect-outcome-evidence",
                    "method-step:03-verify-against-obligations",
                ),
                control_flow_hints=("control-flow:verify-before-complete",),
                failure_modes=("failure-mode:unverified-production-outcome",),
                fallback_patterns=("fallback-pattern:route-failure-to-diagnosis",),
                verification_intent=("verification-intent:produced-outcome",),
                composition_tags=("composition-tag:production-method",),
                legacy_skill_ids=_COMMON_DELIVERY_SKILLS,
                required_phases=("PRODUCTION", "VERIFICATION"),
            ),
            _seed(
                method_id="retry_after_diagnosis",
                version="v1",
                title="Retry after diagnosis",
                semantic_summary=(
                    "Diagnose a verified failure, apply a bounded repair, and "
                    "re-run the original verification obligation."
                ),
                goal_classes=("goal-class:failed-verification-recovery",),
                preconditions=("condition:verification-failure-recorded",),
                expected_postconditions=("condition:original-obligation-reverified",),
                required_capability_classes=(
                    "capability-class:failure-diagnosis",
                    "capability-class:targeted-repair",
                    "capability-class:verification-evaluation",
                ),
                method_steps=(
                    "method-step:01-diagnose-failure-evidence",
                    "method-step:02-apply-bounded-repair",
                    "method-step:03-rerun-original-verification",
                ),
                control_flow_hints=("control-flow:bounded-repair-loop",),
                failure_modes=("failure-mode:blind-retry-without-diagnosis",),
                fallback_patterns=("fallback-pattern:stop-and-preserve-failure-evidence",),
                verification_intent=("verification-intent:original-predicate-retained",),
                composition_tags=("composition-tag:repair-method",),
                legacy_skill_ids=_COMMON_DELIVERY_SKILLS,
                required_phases=("REPAIR", "VERIFICATION"),
            ),
        ),
        key=lambda item: item.method_id,
    )
)

PRODUCTION_METHOD_SEEDS_SHA256 = canonical_sha256(
    {
        "domain": "tiangong.production-skill-method-seeds.v1",
        "seeds": [
            {**seed.payload(), "seed_sha256": seed.seed_sha256}
            for seed in PRODUCTION_METHOD_SEEDS
        ],
    }
)


def _build_method_primitive(
    seed: ReviewedMethodSeedV1,
    *,
    corpus: LegacySkillMethodCorpusV1,
) -> tuple[SkillSourcePrimitiveV1, MethodMigrationBindingV1]:
    if not seed.has_valid_sha256():
        raise SkillMethodWorldError(
            f"reviewed method seed hash is invalid: {seed.method_id}"
        )
    binding = MethodMigrationBindingV1(
        method_id=seed.method_id,
        legacy_skill_ids=seed.legacy_skill_ids,
        required_phases=seed.required_phases,
        binding_sha256="0" * 64,
    ).with_computed_sha256()
    source_hash = method_source_revision_sha256(corpus, binding)
    evidence_by_id = {item.legacy_skill_id: item for item in corpus.evidence}
    try:
        source_files = tuple(
            sorted(
                evidence_by_id[skill_id].source_path
                for skill_id in seed.legacy_skill_ids
            )
        )
    except KeyError as exc:
        raise SkillMethodWorldError(
            f"reviewed method seed references an unknown legacy Skill: {seed.method_id}"
        ) from exc

    source_ref = SourceRevisionRefV1(
        source_kind="SKILL_METHOD",
        semantic_id=seed.method_id,
        version=seed.version,
        source_files=source_files,
        source_sha256=source_hash,
        descriptor_sha256="0" * 64,
        manifest_sha256=None,
    )
    primitive = SkillSourcePrimitiveV1(
        method_id=seed.method_id,
        version=seed.version,
        source_ref=source_ref,
        source_sha256=source_hash,
        title=seed.title,
        semantic_summary=seed.semantic_summary,
        goal_classes=seed.goal_classes,
        preconditions=seed.preconditions,
        expected_postconditions=seed.expected_postconditions,
        required_capability_classes=seed.required_capability_classes,
        method_steps=seed.method_steps,
        control_flow_hints=seed.control_flow_hints,
        failure_modes=seed.failure_modes,
        fallback_patterns=seed.fallback_patterns,
        verification_intent=seed.verification_intent,
        composition_tags=seed.composition_tags,
        descriptor_sha256="0" * 64,
    )
    descriptor_sha256 = computed_skill_method_descriptor_sha256(primitive)
    primitive = primitive.model_copy(
        update={
            "source_ref": source_ref.model_copy(
                update={"descriptor_sha256": descriptor_sha256}
            ),
            "descriptor_sha256": descriptor_sha256,
        }
    )
    return primitive, binding


def compile_production_skill_method_world(
    index: Mapping[str, Any],
    *,
    index_source_sha256: str,
    skill_source_hashes: Mapping[str, str],
) -> SkillMethodWorldSnapshotV1:
    """Compile the reviewed production P3 method set from the current Skill catalog."""

    corpus = observe_legacy_skill_method_corpus(
        index,
        index_source_sha256=index_source_sha256,
        skill_source_hashes=skill_source_hashes,
    )
    primitives: list[SkillSourcePrimitiveV1] = []
    bindings: list[MethodMigrationBindingV1] = []
    for seed in PRODUCTION_METHOD_SEEDS:
        primitive, binding = _build_method_primitive(seed, corpus=corpus)
        primitives.append(primitive)
        bindings.append(binding)
    return compile_skill_method_world(
        tuple(primitives),
        corpus=corpus,
        migration_bindings=tuple(bindings),
    )


__all__ = [
    "PRODUCTION_METHOD_SEEDS",
    "PRODUCTION_METHOD_SEEDS_SHA256",
    "ReviewedMethodSeedV1",
    "compile_production_skill_method_world",
]
