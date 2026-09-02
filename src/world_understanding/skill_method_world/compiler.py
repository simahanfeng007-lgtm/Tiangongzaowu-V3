"""Compile Skill Method World from deterministic migration evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from contracts import canonical_sha256
from contracts.capability_composition import SkillSourcePrimitiveV1

from .models import (
    _ALLOWED_PHASES,
    _LEGACY_SKILL_SCHEMA,
    _SHA256,
    _SKILL_METHOD_WORLD_SCHEMA,
    _PHASE_FIELDS,
    _PREFIX_RULES,
    LegacySkillMethodCorpusV1,
    LegacySkillMethodEvidenceV1,
    MethodMigrationBindingV1,
    SkillMethodRelationV1,
    SkillMethodWorldError,
    SkillMethodWorldSnapshotV1,
)


def _skill_version(skill_id: str) -> str:
    match = re.search(r"_v([1-9][0-9]*)$", skill_id)
    if match is None:
        raise SkillMethodWorldError("legacy Skill ID does not bind a version")
    return "v" + match.group(1)


def _string_list(raw: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = raw.get(field)
    if value is None:
        return ()
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise SkillMethodWorldError(f"legacy Skill {field} must be a string list")
    return tuple(value)


def observe_legacy_skill_method_corpus(
    index: Mapping[str, Any],
    *,
    index_source_sha256: str,
    skill_source_hashes: Mapping[str, str],
) -> LegacySkillMethodCorpusV1:
    """Reduce the static Skill catalog to zero-authority method evidence.

    Action IDs and full Skill instructions are deliberately not copied into the
    corpus. Only source identity and broad lifecycle-phase presence are kept.
    """

    if _SHA256.fullmatch(index_source_sha256) is None:
        raise SkillMethodWorldError("legacy Skill index source hash is invalid")
    expected_root = {
        "schema",
        "version",
        "principle",
        "skill_count",
        "skills",
        "actions",
        "tool_boundary",
    }
    if set(index) != expected_root or index.get("schema") != _LEGACY_SKILL_SCHEMA:
        raise SkillMethodWorldError("legacy Skill index schema is incompatible")
    actions = _string_list(index, "actions")
    if not {"skill.route", "skill.list", "skill.get", "skill.read"}.issubset(actions):
        raise SkillMethodWorldError("legacy Skill index query surface is incomplete")
    tool_boundary = index.get("tool_boundary")
    if not isinstance(tool_boundary, str) or not tool_boundary.strip():
        raise SkillMethodWorldError("legacy Skill tool boundary is malformed")
    skills = index.get("skills")
    if (
        not isinstance(skills, list)
        or isinstance(index.get("skill_count"), bool)
        or index.get("skill_count") != len(skills)
        or not 1 <= len(skills) <= 10_000
    ):
        raise SkillMethodWorldError("legacy Skill index count is invalid")

    evidence: list[LegacySkillMethodEvidenceV1] = []
    seen_paths: set[str] = set()
    for raw in skills:
        if not isinstance(raw, Mapping):
            raise SkillMethodWorldError("legacy Skill index item is malformed")
        skill_id = raw.get("id")
        source_path = raw.get("file")
        if not isinstance(skill_id, str) or not skill_id:
            raise SkillMethodWorldError("legacy Skill ID is malformed")
        if not isinstance(source_path, str):
            raise SkillMethodWorldError("legacy Skill source path is malformed")
        posix = PurePosixPath(source_path)
        if (
            posix.is_absolute()
            or source_path != source_path.strip()
            or str(posix) != source_path
            or "\\" in source_path
            or ".." in posix.parts
            or len(posix.parts) != 2
            or posix.parts[0] != "deliverable_skills"
            or posix.suffix.casefold() != ".md"
            or source_path in seen_paths
        ):
            raise SkillMethodWorldError("legacy Skill source path is unsafe or duplicated")
        seen_paths.add(source_path)
        authoritative_source_path = f"src/omni_body_skill/{source_path}"
        source_sha256 = skill_source_hashes.get(authoritative_source_path)
        if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
            raise SkillMethodWorldError(
                f"legacy Skill source hash is missing or invalid: {skill_id}"
            )

        observed: set[str] = set()
        for phase, fields in _PHASE_FIELDS.items():
            field = fields[0]
            if field == "acceptance":
                acceptance = raw.get(field)
                if acceptance is not None and not isinstance(acceptance, Mapping):
                    raise SkillMethodWorldError("legacy Skill acceptance is malformed")
                if isinstance(acceptance, Mapping) and acceptance:
                    observed.add(phase)
                continue
            values = _string_list(raw, field)
            if values:
                observed.add(phase)
        if not observed:
            raise SkillMethodWorldError(
                f"legacy Skill has no method-phase evidence: {skill_id}"
            )

        item = LegacySkillMethodEvidenceV1(
            legacy_skill_id=skill_id,
            legacy_skill_version=_skill_version(skill_id),
            source_path=authoritative_source_path,
            source_sha256=source_sha256,
            observed_phases=tuple(sorted(observed)),
            evidence_sha256="0" * 64,
        ).with_computed_sha256()
        evidence.append(item)

    ordered = tuple(sorted(evidence, key=lambda item: item.legacy_skill_id))
    corpus = LegacySkillMethodCorpusV1(
        index_source_sha256=index_source_sha256,
        index_semantic_sha256=canonical_sha256(dict(index)),
        evidence=ordered,
        corpus_sha256="0" * 64,
    )
    return replace(corpus, corpus_sha256=corpus.computed_sha256())


def method_source_revision_sha256(
    corpus: LegacySkillMethodCorpusV1,
    binding: MethodMigrationBindingV1,
) -> str:
    if not corpus.has_valid_sha256():
        raise SkillMethodWorldError("legacy Skill corpus hash is invalid")
    if not binding.has_valid_sha256():
        raise SkillMethodWorldError("method migration binding hash is invalid")
    by_id = {item.legacy_skill_id: item for item in corpus.evidence}
    selected = []
    for skill_id in binding.legacy_skill_ids:
        item = by_id.get(skill_id)
        if item is None:
            raise SkillMethodWorldError("method source references an unknown legacy Skill")
        selected.append(
            {
                "legacy_skill_id": item.legacy_skill_id,
                "legacy_skill_version": item.legacy_skill_version,
                "source_path": item.source_path,
                "source_sha256": item.source_sha256,
                "observed_phases": list(item.observed_phases),
                "evidence_sha256": item.evidence_sha256,
            }
        )
    return canonical_sha256(
        {
            "domain": "tiangong.skill-method-source-revision.v1",
            "migration_binding": {
                **binding.payload(),
                "binding_sha256": binding.binding_sha256,
            },
            "legacy_sources": selected,
        }
    )


def computed_skill_method_descriptor_sha256(
    primitive: SkillSourcePrimitiveV1,
) -> str:
    payload = primitive.model_dump(mode="json")
    payload["descriptor_sha256"] = "0" * 64
    source_ref = dict(payload["source_ref"])
    source_ref["descriptor_sha256"] = "0" * 64
    payload["source_ref"] = source_ref
    return canonical_sha256(payload)


def _validate_prefixed_values(primitive: SkillSourcePrimitiveV1) -> None:
    for field, prefix in _PREFIX_RULES:
        values = tuple(getattr(primitive, field))
        if len(values) != len(set(values)):
            raise SkillMethodWorldError(
                f"method {field} must be unique: {primitive.method_id}"
            )
        if field != "method_steps" and values != tuple(sorted(values)):
            raise SkillMethodWorldError(
                f"method {field} must be sorted: {primitive.method_id}"
            )
        if any(
            not value.startswith(prefix) or len(value) == len(prefix)
            for value in values
        ):
            raise SkillMethodWorldError(
                f"method {field} contains a non-semantic identifier: {primitive.method_id}"
            )


def _validate_primitive(
    primitive: SkillSourcePrimitiveV1,
    *,
    binding: MethodMigrationBindingV1,
    corpus: LegacySkillMethodCorpusV1,
    evidence_by_id: Mapping[str, LegacySkillMethodEvidenceV1],
) -> None:
    if (
        primitive.source_ref.source_kind != "SKILL_METHOD"
        or primitive.source_ref.semantic_id != primitive.method_id
        or primitive.source_ref.version != primitive.version
        or primitive.source_ref.manifest_sha256 is not None
        or primitive.source_sha256 != primitive.source_ref.source_sha256
    ):
        raise SkillMethodWorldError(
            f"method source identity is invalid: {primitive.method_id}"
        )
    expected_source_hash = method_source_revision_sha256(corpus, binding)
    if primitive.source_sha256 != expected_source_hash:
        raise SkillMethodWorldError(
            f"method source revision does not match migration evidence: {primitive.method_id}"
        )
    expected_files = tuple(
        sorted(evidence_by_id[skill_id].source_path for skill_id in binding.legacy_skill_ids)
    )
    if primitive.source_ref.source_files != expected_files:
        raise SkillMethodWorldError(
            f"method source files do not match migration evidence: {primitive.method_id}"
        )
    span_keys = tuple(
        (item.path, item.start_line or 0, item.end_line or 0)
        for item in primitive.source_ref.source_spans
    )
    if (
        len(span_keys) != len(set(span_keys))
        or span_keys != tuple(sorted(span_keys))
        or any(item.path not in expected_files for item in primitive.source_ref.source_spans)
    ):
        raise SkillMethodWorldError(
            f"method source spans do not match migration evidence: {primitive.method_id}"
        )
    expected_descriptor = computed_skill_method_descriptor_sha256(primitive)
    if (
        primitive.descriptor_sha256 != expected_descriptor
        or primitive.source_ref.descriptor_sha256 != expected_descriptor
    ):
        raise SkillMethodWorldError(
            f"method descriptor hash is invalid: {primitive.method_id}"
        )
    _validate_prefixed_values(primitive)


def compile_skill_method_world(
    primitives: tuple[SkillSourcePrimitiveV1, ...],
    *,
    corpus: LegacySkillMethodCorpusV1,
    migration_bindings: tuple[MethodMigrationBindingV1, ...],
) -> SkillMethodWorldSnapshotV1:
    """Compile reusable method semantics without creating execution authority."""

    if not corpus.has_valid_sha256():
        raise SkillMethodWorldError("legacy Skill corpus hash is invalid")
    ordered_primitives = tuple(sorted(primitives, key=lambda item: item.method_id))
    method_ids = tuple(item.method_id for item in ordered_primitives)
    if not method_ids or method_ids != tuple(sorted(set(method_ids))):
        raise SkillMethodWorldError("method primitives are empty or duplicated")

    ordered_bindings = tuple(sorted(migration_bindings, key=lambda item: item.method_id))
    if tuple(item.method_id for item in ordered_bindings) != method_ids:
        raise SkillMethodWorldError(
            "method primitives and migration bindings are not one-to-one"
        )
    if any(not item.has_valid_sha256() for item in ordered_bindings):
        raise SkillMethodWorldError("method migration binding hash is invalid")

    evidence_by_id = {item.legacy_skill_id: item for item in corpus.evidence}
    binding_by_method = {item.method_id: item for item in ordered_bindings}

    relations: set[tuple[str, str, str]] = set()
    for primitive in ordered_primitives:
        binding = binding_by_method[primitive.method_id]
        for skill_id in binding.legacy_skill_ids:
            evidence = evidence_by_id.get(skill_id)
            if evidence is None:
                raise SkillMethodWorldError(
                    f"method references unknown legacy Skill: {primitive.method_id}"
                )
            if not set(binding.required_phases).issubset(evidence.observed_phases):
                raise SkillMethodWorldError(
                    f"legacy Skill lacks required method phases: {primitive.method_id}"
                )
        _validate_primitive(
            primitive,
            binding=binding,
            corpus=corpus,
            evidence_by_id=evidence_by_id,
        )

        method_ref = f"method:{primitive.method_id}"
        for value in primitive.goal_classes:
            relations.add(("DECLARES_GOAL_CLASS", method_ref, value))
        for value in primitive.preconditions:
            relations.add(("REQUIRES_PRECONDITION", method_ref, value))
        for value in primitive.expected_postconditions:
            relations.add(("EXPECTS_POSTCONDITION", method_ref, value))
        for value in primitive.required_capability_classes:
            relations.add(("REQUIRES_CAPABILITY_CLASS", method_ref, value))
        for value in primitive.method_steps:
            relations.add(("HAS_METHOD_STEP", method_ref, value))
        for left, right in zip(primitive.method_steps, primitive.method_steps[1:]):
            relations.add(("PRECEDES", left, right))
        for value in primitive.control_flow_hints:
            relations.add(("HAS_CONTROL_FLOW_HINT", method_ref, value))
        for value in primitive.failure_modes:
            relations.add(("HAS_FAILURE_MODE", method_ref, value))
        for value in primitive.fallback_patterns:
            relations.add(("FALLS_BACK_TO_PATTERN", method_ref, value))
        for value in primitive.verification_intent:
            relations.add(("DECLARES_VERIFICATION_INTENT", method_ref, value))
        for value in primitive.composition_tags:
            relations.add(("HAS_COMPOSITION_TAG", method_ref, value))
        relations.add(
            (
                "SOURCE_REVISION_OF",
                f"source-revision:{primitive.source_sha256}",
                method_ref,
            )
        )
        for skill_id in binding.legacy_skill_ids:
            relations.add(
                (
                    "DERIVED_FROM_LEGACY_SKILL",
                    method_ref,
                    f"legacy-skill:{skill_id}",
                )
            )

    method_sources_sha256 = canonical_sha256(
        {
            "domain": "tiangong.skill-method-sources.v1",
            "primitives": [
                item.model_dump(mode="json") for item in ordered_primitives
            ],
        }
    )
    ordered_relations = tuple(
        SkillMethodRelationV1(*item) for item in sorted(relations)
    )
    snapshot = SkillMethodWorldSnapshotV1(
        schema=_SKILL_METHOD_WORLD_SCHEMA,
        legacy_corpus_sha256=corpus.corpus_sha256,
        method_sources_sha256=method_sources_sha256,
        primitives=ordered_primitives,
        migration_bindings=ordered_bindings,
        relations=ordered_relations,
        snapshot_sha256="0" * 64,
    )
    return replace(snapshot, snapshot_sha256=canonical_sha256(snapshot.payload()))
