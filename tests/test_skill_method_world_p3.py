from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.capability_composition import (
    SkillSourcePrimitiveV1,
    SourceRevisionRefV1,
    SourceSpanRefV1,
)
from world_understanding.skill_method_world import (
    MethodMigrationBindingV1,
    SkillMethodRelationV1,
    SkillMethodWorldError,
    compile_skill_method_world,
    computed_skill_method_descriptor_sha256,
    method_source_revision_sha256,
    observe_legacy_skill_method_corpus,
)


H = "a" * 64
INDEX_H = "b" * 64


def legacy_index() -> dict:
    return {
        "schema": "tiangong.v3.omni_body.skill_router_index.v1",
        "version": "3.5.2",
        "principle": "legacy static Skill planner migration material",
        "skill_count": 3,
        "skills": [
            {
                "id": "skill_code_delivery_v1",
                "mingcheng": "code delivery",
                "file": "deliverable_skills/code.md",
                "category": "code",
                "starter_actions": ["writing.outline.create"],
                "production_actions": ["code.write"],
                "quality_gates": ["quality.run_tests"],
                "repair_actions": ["repair.plan"],
                "final_actions": ["deliverable.package"],
                "acceptance": {"must_pass": ["tests"]},
            },
            {
                "id": "skill_document_delivery_v1",
                "mingcheng": "document delivery",
                "file": "deliverable_skills/document.md",
                "category": "document",
                "starter_actions": ["writing.outline.create"],
                "production_actions": ["docx.create"],
                "quality_gates": ["qc.docx.delivery_check"],
                "repair_actions": ["repair.plan"],
                "final_actions": ["deliverable.package"],
                "acceptance": {"must_pass": ["qc"]},
            },
            {
                "id": "skill_research_review_v1",
                "mingcheng": "research review",
                "file": "deliverable_skills/research.md",
                "category": "research",
                "starter_actions": ["research.evidence_table.create"],
                "production_actions": ["file.write"],
                "quality_gates": ["qc.research.evidence_check"],
                "repair_actions": ["repair.plan"],
                "final_actions": ["deliverable.package"],
                "acceptance": {"must_pass": ["evidence"]},
            },
        ],
        "actions": [
            "skill.get",
            "skill.list",
            "skill.read",
            "skill.route",
            "skill.step.check",
        ],
        "tool_boundary": "skill router only returns skill cards",
    }


def source_hashes() -> dict[str, str]:
    return {
        "src/omni_body_skill/deliverable_skills/code.md": "c" * 64,
        "src/omni_body_skill/deliverable_skills/document.md": "d" * 64,
        "src/omni_body_skill/deliverable_skills/research.md": "e" * 64,
    }


def corpus():
    return observe_legacy_skill_method_corpus(
        legacy_index(),
        index_source_sha256=INDEX_H,
        skill_source_hashes=source_hashes(),
    )


def binding(
    method_id: str,
    skill_ids: tuple[str, ...],
    phases: tuple[str, ...],
) -> MethodMigrationBindingV1:
    return MethodMigrationBindingV1(
        method_id=method_id,
        legacy_skill_ids=skill_ids,
        required_phases=phases,
        binding_sha256="0" * 64,
    ).with_computed_sha256()


def primitive(
    method_binding: MethodMigrationBindingV1,
    *,
    steps: tuple[str, ...],
    capability_classes: tuple[str, ...],
    failure_modes: tuple[str, ...] = ("failure-mode:verification-failed",),
    fallback_patterns: tuple[str, ...] = ("fallback-pattern:diagnose-and-retry",),
) -> SkillSourcePrimitiveV1:
    observed = corpus()
    source_hash = method_source_revision_sha256(observed, method_binding)
    evidence = {item.legacy_skill_id: item for item in observed.evidence}
    files = tuple(
        sorted(
            evidence[skill_id].source_path
            for skill_id in method_binding.legacy_skill_ids
        )
    )
    source_ref = SourceRevisionRefV1(
        source_kind="SKILL_METHOD",
        semantic_id=method_binding.method_id,
        version="v1",
        source_files=files,
        source_sha256=source_hash,
        descriptor_sha256="0" * 64,
        manifest_sha256=None,
    )
    item = SkillSourcePrimitiveV1(
        method_id=method_binding.method_id,
        version="v1",
        source_ref=source_ref,
        source_sha256=source_hash,
        title=method_binding.method_id.replace("_", " "),
        semantic_summary="Reusable method distilled from multiple legacy Skill sources.",
        goal_classes=("goal-class:artifact-delivery",),
        preconditions=("condition:goal-defined",),
        expected_postconditions=("condition:outcome-verified",),
        required_capability_classes=capability_classes,
        method_steps=steps,
        control_flow_hints=("control-flow:sequential-with-repair",),
        failure_modes=failure_modes,
        fallback_patterns=fallback_patterns,
        verification_intent=("verification-intent:plan-bound-acceptance",),
        composition_tags=("composition-tag:reusable-method",),
        descriptor_sha256="0" * 64,
    )
    descriptor = computed_skill_method_descriptor_sha256(item)
    return item.model_copy(
        update={
            "source_ref": source_ref.model_copy(
                update={"descriptor_sha256": descriptor}
            ),
            "descriptor_sha256": descriptor,
        }
    )


def valid_world_inputs():
    skill_ids = (
        "skill_code_delivery_v1",
        "skill_document_delivery_v1",
        "skill_research_review_v1",
    )
    acceptance_binding = binding(
        "acceptance_review", skill_ids, ("ACCEPTANCE", "VERIFICATION")
    )
    generate_binding = binding(
        "generate_then_verify", skill_ids, ("PRODUCTION", "REPAIR", "VERIFICATION")
    )
    acceptance = primitive(
        acceptance_binding,
        steps=(
            "method-step:01-collect-evidence",
            "method-step:02-evaluate-acceptance",
        ),
        capability_classes=(
            "capability-class:evidence-read",
            "capability-class:verification-evaluate",
        ),
    )
    generate = primitive(
        generate_binding,
        steps=(
            "method-step:01-produce",
            "method-step:02-verify",
            "method-step:03-repair-if-needed",
        ),
        capability_classes=(
            "capability-class:artifact-produce",
            "capability-class:artifact-verify",
        ),
    )
    return (acceptance, generate), (acceptance_binding, generate_binding)


def test_production_static_skill_catalog_is_valid_migration_material() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    skill_root = repository_root / "src" / "omni_body_skill"
    index_path = skill_root / "registry" / "skill_router_index.json"
    index_bytes = index_path.read_bytes()
    index = json.loads(index_bytes.decode("utf-8", errors="strict"))
    source_bindings: dict[str, str] = {}
    for raw in index["skills"]:
        source_path = skill_root.joinpath(*Path(raw["file"]).parts)
        relative = source_path.relative_to(repository_root).as_posix()
        source_bindings[relative] = hashlib.sha256(source_path.read_bytes()).hexdigest()

    observed = observe_legacy_skill_method_corpus(
        index,
        index_source_sha256=hashlib.sha256(index_bytes).hexdigest(),
        skill_source_hashes=source_bindings,
    )
    assert observed.has_valid_sha256()
    assert len(observed.evidence) == index["skill_count"]
    assert observed.may_authorize is False
    assert observed.may_execute is False
    assert all(
        item.source_path.startswith("src/omni_body_skill/deliverable_skills/")
        for item in observed.evidence
    )


def test_legacy_skill_corpus_discards_actions_and_authority() -> None:
    observed = corpus()
    assert observed.has_valid_sha256()
    assert observed.may_authorize is False
    assert observed.may_execute is False
    assert tuple(item.legacy_skill_id for item in observed.evidence) == (
        "skill_code_delivery_v1",
        "skill_document_delivery_v1",
        "skill_research_review_v1",
    )
    serialized = json.dumps(observed.payload(), ensure_ascii=False, sort_keys=True)
    assert "code.write" not in serialized
    assert "docx.create" not in serialized
    assert "quality.run_tests" not in serialized
    assert all(item.may_authorize is False for item in observed.evidence)
    assert all(item.may_execute is False for item in observed.evidence)


def test_skill_method_world_is_reusable_non_authorizing_and_deterministic() -> None:
    primitives, bindings = valid_world_inputs()
    first = compile_skill_method_world(
        tuple(reversed(primitives)),
        corpus=corpus(),
        migration_bindings=tuple(reversed(bindings)),
    )
    second = compile_skill_method_world(
        primitives,
        corpus=corpus(),
        migration_bindings=bindings,
    )
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.has_valid_sha256()
    assert first.may_authorize is False
    assert first.may_execute is False
    assert tuple(item.method_id for item in first.primitives) == (
        "acceptance_review",
        "generate_then_verify",
    )
    assert all(len(item.legacy_skill_ids) >= 2 for item in first.migration_bindings)
    assert len(first.primitives) < len(corpus().evidence)


def test_method_relations_cover_semantics_and_never_action_authority() -> None:
    primitives, bindings = valid_world_inputs()
    snapshot = compile_skill_method_world(
        primitives, corpus=corpus(), migration_bindings=bindings
    )
    relation_types = {item.relation_type for item in snapshot.relations}
    expected = {
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
    assert expected.issubset(relation_types)
    assert {
        "EXECUTES", "ALLOWS_ACTION", "COMPILES_TO_ACTION", "GRANTS", "HANDLED_BY"
    }.isdisjoint(relation_types)
    serialized = json.dumps(snapshot.payload(), ensure_ascii=False, sort_keys=True)
    assert "code.write" not in serialized
    assert "docx.create" not in serialized

    with pytest.raises(SkillMethodWorldError, match="relation type"):
        SkillMethodRelationV1(
            "EXECUTES", "method:generate_then_verify", "action:code.write"
        )


def test_one_to_one_skill_copy_is_rejected() -> None:
    with pytest.raises(SkillMethodWorldError, match="at least two Skills"):
        MethodMigrationBindingV1(
            method_id="copied_skill",
            legacy_skill_ids=("skill_code_delivery_v1",),
            required_phases=("PRODUCTION",),
            binding_sha256=H,
        )


def test_method_identity_cannot_copy_a_legacy_skill_identity() -> None:
    with pytest.raises(SkillMethodWorldError, match="copied legacy Skill identity"):
        MethodMigrationBindingV1(
            method_id="skill_code_delivery_v1",
            legacy_skill_ids=(
                "skill_code_delivery_v1",
                "skill_document_delivery_v1",
            ),
            required_phases=("PRODUCTION",),
            binding_sha256=H,
        )


def test_method_source_revision_ignores_unrelated_skill_drift() -> None:
    selected = ("skill_code_delivery_v1", "skill_document_delivery_v1")
    method_binding = binding(
        "generate_then_verify", selected, ("PRODUCTION", "VERIFICATION")
    )
    first = corpus()
    changed_hashes = source_hashes()
    changed_hashes["src/omni_body_skill/deliverable_skills/research.md"] = "f" * 64
    second = observe_legacy_skill_method_corpus(
        legacy_index(),
        index_source_sha256="9" * 64,
        skill_source_hashes=changed_hashes,
    )
    assert first.corpus_sha256 != second.corpus_sha256
    assert method_source_revision_sha256(first, method_binding) == (
        method_source_revision_sha256(second, method_binding)
    )


def test_method_source_revision_binds_the_decomposition_binding() -> None:
    selected = ("skill_code_delivery_v1", "skill_document_delivery_v1")
    production = binding("generate_then_verify", selected, ("PRODUCTION",))
    verified = binding(
        "generate_then_verify", selected, ("PRODUCTION", "VERIFICATION")
    )
    assert method_source_revision_sha256(corpus(), production) != (
        method_source_revision_sha256(corpus(), verified)
    )


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../outside.md",
        "deliverable_skills//double.md",
        "deliverable_skills/./dot.md",
        " deliverable_skills/leading.md",
        "deliverable_skills\\windows.md",
    ),
)
def test_legacy_skill_source_paths_must_be_canonical(unsafe_path: str) -> None:
    index = legacy_index()
    index["skills"][0]["file"] = unsafe_path
    hashes = source_hashes()
    hashes[f"src/omni_body_skill/{unsafe_path}"] = "9" * 64
    with pytest.raises(SkillMethodWorldError, match="unsafe or duplicated"):
        observe_legacy_skill_method_corpus(
            index,
            index_source_sha256=INDEX_H,
            skill_source_hashes=hashes,
        )


def test_legacy_catalog_query_surface_must_be_complete() -> None:
    index = legacy_index()
    index["actions"] = ["skill.route"]
    with pytest.raises(SkillMethodWorldError, match="query surface"):
        observe_legacy_skill_method_corpus(
            index,
            index_source_sha256=INDEX_H,
            skill_source_hashes=source_hashes(),
        )


def test_missing_required_phase_is_rejected() -> None:
    skill_ids = ("skill_code_delivery_v1", "skill_document_delivery_v1")
    invalid_binding = binding(
        "inspect_then_patch", skill_ids, ("INSPECTION", "PRODUCTION")
    )
    item = primitive(
        invalid_binding,
        steps=("method-step:01-inspect", "method-step:02-patch"),
        capability_classes=(
            "capability-class:artifact-read",
            "capability-class:artifact-update",
        ),
    )
    with pytest.raises(SkillMethodWorldError, match="lacks required method phases"):
        compile_skill_method_world(
            (item,), corpus=corpus(), migration_bindings=(invalid_binding,)
        )


def test_method_source_revision_must_match_exact_legacy_evidence() -> None:
    primitives, bindings = valid_world_inputs()
    item = primitives[0]
    tampered = item.model_copy(
        update={
            "source_sha256": H,
            "source_ref": item.source_ref.model_copy(update={"source_sha256": H}),
        }
    )
    with pytest.raises(SkillMethodWorldError, match="source revision"):
        compile_skill_method_world(
            (tampered, primitives[1]), corpus=corpus(), migration_bindings=bindings
        )


def test_method_descriptor_hash_must_bind_source_and_semantics() -> None:
    primitives, bindings = valid_world_inputs()
    item = primitives[0].model_copy(update={"semantic_summary": "tampered summary"})
    with pytest.raises(SkillMethodWorldError, match="descriptor hash"):
        compile_skill_method_world(
            (item, primitives[1]), corpus=corpus(), migration_bindings=bindings
        )


def test_action_like_identifiers_cannot_enter_method_semantic_fields() -> None:
    skill_ids = ("skill_code_delivery_v1", "skill_document_delivery_v1")
    method_binding = binding("bad_method", skill_ids, ("PRODUCTION",))
    item = primitive(
        method_binding,
        steps=("method-step:01-produce",),
        capability_classes=("code.write",),
    )
    with pytest.raises(SkillMethodWorldError, match="non-semantic identifier"):
        compile_skill_method_world(
            (item,), corpus=corpus(), migration_bindings=(method_binding,)
        )


def test_empty_semantic_identifier_suffix_is_rejected() -> None:
    skill_ids = ("skill_code_delivery_v1", "skill_document_delivery_v1")
    method_binding = binding("bad_empty_semantic", skill_ids, ("PRODUCTION",))
    item = primitive(
        method_binding,
        steps=("method-step:",),
        capability_classes=("capability-class:artifact-update",),
    )
    with pytest.raises(SkillMethodWorldError, match="non-semantic identifier"):
        compile_skill_method_world(
            (item,), corpus=corpus(), migration_bindings=(method_binding,)
        )


def test_method_source_spans_cannot_escape_selected_legacy_sources() -> None:
    primitives, bindings = valid_world_inputs()
    item = primitives[0]
    source_ref = item.source_ref.model_copy(
        update={
            "source_spans": (
                SourceSpanRefV1(path="src/unrelated.py", start_line=1, end_line=2),
            ),
        }
    )
    tampered = item.model_copy(update={"source_ref": source_ref})
    with pytest.raises(SkillMethodWorldError, match="source spans"):
        compile_skill_method_world(
            (tampered, primitives[1]), corpus=corpus(), migration_bindings=bindings
        )


def test_skill_method_contract_rejects_handler_or_execution_fields() -> None:
    observed = corpus()
    skill_ids = ("skill_code_delivery_v1", "skill_document_delivery_v1")
    method_binding = binding("method_with_handler", skill_ids, ("PRODUCTION",))
    source_hash = method_source_revision_sha256(observed, method_binding)
    with pytest.raises(ValidationError):
        SkillSourcePrimitiveV1(
            method_id="method_with_handler",
            version="v1",
            source_ref=SourceRevisionRefV1(
                source_kind="SKILL_METHOD",
                semantic_id="method_with_handler",
                version="v1",
                source_files=(
                    "src/omni_body_skill/deliverable_skills/code.md",
                    "src/omni_body_skill/deliverable_skills/document.md",
                ),
                source_sha256=source_hash,
                descriptor_sha256=H,
                manifest_sha256=None,
            ),
            source_sha256=source_hash,
            title="method with handler",
            semantic_summary="must fail",
            goal_classes=("goal-class:test",),
            method_steps=("method-step:01-test",),
            descriptor_sha256=H,
            handler="_action_write",
        )
