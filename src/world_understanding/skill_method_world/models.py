"""Immutable, non-authorizing Skill Method World records."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any

from contracts import canonical_sha256
from contracts.capability_composition import SkillSourcePrimitiveV1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_SKILL_SCHEMA = "tiangong.v3.omni_body.skill_router_index.v1"
_SKILL_METHOD_WORLD_SCHEMA = "tiangong.skill-method-world.v1"

_PHASE_FIELDS: dict[str, tuple[str, ...]] = {
    "PREPARATION": ("starter_actions",),
    "INSPECTION": ("inspection_actions",),
    "PRODUCTION": ("production_actions",),
    "VERIFICATION": ("quality_gates",),
    "REPAIR": ("repair_actions",),
    "FINALIZATION": ("final_actions",),
    "ACCEPTANCE": ("acceptance",),
}
_ALLOWED_PHASES = frozenset(_PHASE_FIELDS)

_ALLOWED_RELATIONS = frozenset(
    {
        "DECLARES_GOAL_CLASS",
        "REQUIRES_PRECONDITION",
        "EXPECTS_POSTCONDITION",
        "REQUIRES_CAPABILITY_CLASS",
        "HAS_METHOD_STEP",
        "PRECEDES",
        "HAS_CONTROL_FLOW_HINT",
        "HAS_FAILURE_MODE",
        "FALLS_BACK_TO_PATTERN",
        "DECLARES_VERIFICATION_INTENT",
        "HAS_COMPOSITION_TAG",
        "SOURCE_REVISION_OF",
        "DERIVED_FROM_LEGACY_SKILL",
    }
)

_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("goal_classes", "goal-class:"),
    ("preconditions", "condition:"),
    ("expected_postconditions", "condition:"),
    ("required_capability_classes", "capability-class:"),
    ("method_steps", "method-step:"),
    ("control_flow_hints", "control-flow:"),
    ("failure_modes", "failure-mode:"),
    ("fallback_patterns", "fallback-pattern:"),
    ("verification_intent", "verification-intent:"),
    ("composition_tags", "composition-tag:"),
)


class SkillMethodWorldError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LegacySkillMethodEvidenceV1:
    legacy_skill_id: str
    legacy_skill_version: str
    source_path: str
    source_sha256: str
    observed_phases: tuple[str, ...]
    evidence_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.may_authorize or self.may_execute:
            raise SkillMethodWorldError("legacy Skill migration evidence is non-authorizing")
        if not self.legacy_skill_id or not self.source_path:
            raise SkillMethodWorldError("legacy Skill migration identity is incomplete")
        if _SHA256.fullmatch(self.source_sha256) is None:
            raise SkillMethodWorldError("legacy Skill source hash is invalid")
        if self.observed_phases != tuple(sorted(set(self.observed_phases))):
            raise SkillMethodWorldError("legacy Skill phases must be sorted and unique")
        if not self.observed_phases or not set(self.observed_phases).issubset(_ALLOWED_PHASES):
            raise SkillMethodWorldError("legacy Skill phases are invalid")

    def payload(self) -> dict[str, Any]:
        return {
            "legacy_skill_id": self.legacy_skill_id,
            "legacy_skill_version": self.legacy_skill_version,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "observed_phases": list(self.observed_phases),
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "LegacySkillMethodEvidenceV1":
        return replace(self, evidence_sha256=self.computed_sha256())


@dataclass(frozen=True, slots=True)
class LegacySkillMethodCorpusV1:
    index_source_sha256: str
    index_semantic_sha256: str
    evidence: tuple[LegacySkillMethodEvidenceV1, ...]
    corpus_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.may_authorize or self.may_execute:
            raise SkillMethodWorldError("legacy Skill corpus is non-authorizing")
        if _SHA256.fullmatch(self.index_source_sha256) is None:
            raise SkillMethodWorldError("legacy Skill index source hash is invalid")
        if _SHA256.fullmatch(self.index_semantic_sha256) is None:
            raise SkillMethodWorldError("legacy Skill index semantic hash is invalid")
        ids = tuple(item.legacy_skill_id for item in self.evidence)
        if not ids or ids != tuple(sorted(set(ids))):
            raise SkillMethodWorldError("legacy Skill evidence must be sorted and unique")
        if any(not item.has_valid_sha256() for item in self.evidence):
            raise SkillMethodWorldError("legacy Skill evidence hash is invalid")

    def payload(self) -> dict[str, Any]:
        return {
            "index_source_sha256": self.index_source_sha256,
            "index_semantic_sha256": self.index_semantic_sha256,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
            "evidence": [
                {**item.payload(), "evidence_sha256": item.evidence_sha256}
                for item in self.evidence
            ],
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.corpus_sha256 == self.computed_sha256()


@dataclass(frozen=True, slots=True)
class MethodMigrationBindingV1:
    method_id: str
    legacy_skill_ids: tuple[str, ...]
    required_phases: tuple[str, ...]
    binding_sha256: str

    def __post_init__(self) -> None:
        if not self.method_id:
            raise SkillMethodWorldError("method migration identity is empty")
        if self.method_id in self.legacy_skill_ids:
            raise SkillMethodWorldError(
                "method identity cannot be a copied legacy Skill identity"
            )
        if (
            len(self.legacy_skill_ids) < 2
            or self.legacy_skill_ids != tuple(sorted(set(self.legacy_skill_ids)))
        ):
            raise SkillMethodWorldError(
                "legacy Skill decomposition must support a reusable method from at least two Skills"
            )
        if (
            not self.required_phases
            or self.required_phases != tuple(sorted(set(self.required_phases)))
            or not set(self.required_phases).issubset(_ALLOWED_PHASES)
        ):
            raise SkillMethodWorldError("method migration phases are invalid")

    def payload(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "legacy_skill_ids": list(self.legacy_skill_ids),
            "required_phases": list(self.required_phases),
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "MethodMigrationBindingV1":
        return replace(self, binding_sha256=self.computed_sha256())


@dataclass(frozen=True, slots=True)
class SkillMethodRelationV1:
    relation_type: str
    source_ref: str
    target_ref: str

    def __post_init__(self) -> None:
        if self.relation_type not in _ALLOWED_RELATIONS:
            raise SkillMethodWorldError("P3 relation type is not method-semantic")
        if not self.source_ref or not self.target_ref:
            raise SkillMethodWorldError("P3 relation endpoints must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "relation_type": self.relation_type,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
        }


@dataclass(frozen=True, slots=True)
class SkillMethodWorldSnapshotV1:
    schema: str
    legacy_corpus_sha256: str
    method_sources_sha256: str
    primitives: tuple[SkillSourcePrimitiveV1, ...]
    migration_bindings: tuple[MethodMigrationBindingV1, ...]
    relations: tuple[SkillMethodRelationV1, ...]
    snapshot_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.schema != _SKILL_METHOD_WORLD_SCHEMA:
            raise SkillMethodWorldError("unsupported Skill Method World snapshot schema")
        if self.may_authorize or self.may_execute:
            raise SkillMethodWorldError("Skill Method World is non-authorizing and non-executing")
        method_ids = tuple(item.method_id for item in self.primitives)
        if not method_ids or method_ids != tuple(sorted(set(method_ids))):
            raise SkillMethodWorldError("method primitives must be sorted and unique")
        binding_ids = tuple(item.method_id for item in self.migration_bindings)
        if binding_ids != method_ids:
            raise SkillMethodWorldError(
                "every P3 method must have exactly one reusable migration binding"
            )
        relation_keys = tuple(
            (item.relation_type, item.source_ref, item.target_ref)
            for item in self.relations
        )
        if relation_keys != tuple(sorted(set(relation_keys))):
            raise SkillMethodWorldError("method relations must be sorted and unique")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "legacy_corpus_sha256": self.legacy_corpus_sha256,
            "method_sources_sha256": self.method_sources_sha256,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
            "primitives": [item.model_dump(mode="json") for item in self.primitives],
            "migration_bindings": [
                {**item.payload(), "binding_sha256": item.binding_sha256}
                for item in self.migration_bindings
            ],
            "relations": [item.to_dict() for item in self.relations],
        }

    def has_valid_sha256(self) -> bool:
        return self.snapshot_sha256 == canonical_sha256(self.payload())
