"""Deterministic, non-authorizing Skill Method World projection.

Legacy static Skills are consumed only as migration evidence. This package does
not route Skills, execute methods, mint permissions, or own a WorldState.
"""

from .compiler import (
    compile_skill_method_world,
    computed_skill_method_descriptor_sha256,
    method_source_revision_sha256,
    observe_legacy_skill_method_corpus,
)
from .models import (
    LegacySkillMethodCorpusV1,
    LegacySkillMethodEvidenceV1,
    MethodMigrationBindingV1,
    SkillMethodRelationV1,
    SkillMethodWorldError,
    SkillMethodWorldSnapshotV1,
)

__all__ = [
    "LegacySkillMethodCorpusV1",
    "LegacySkillMethodEvidenceV1",
    "MethodMigrationBindingV1",
    "SkillMethodRelationV1",
    "SkillMethodWorldError",
    "SkillMethodWorldSnapshotV1",
    "compile_skill_method_world",
    "computed_skill_method_descriptor_sha256",
    "method_source_revision_sha256",
    "observe_legacy_skill_method_corpus",
]
