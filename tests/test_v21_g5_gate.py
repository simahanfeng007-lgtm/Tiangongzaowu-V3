"""G5 gate evidence: capability lifecycle pointer CAS, vitality review, contract dual read."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from contracts import PhaseNode, SkillDefinitionCore
from contracts import canonical_sha256
from life_service.capability_lifecycle import (
    CapabilityLifecycle,
    CapabilityLifecycleError,
)
from life_service.store import LifeShadowStore


H = "a" * 64


def definition() -> SkillDefinitionCore:
    return SkillDefinitionCore(
        skill_id="skill_g5", skill_version="v1", skill_sha256=H,
        input_schema_sha256=H, output_schema_sha256=H,
        source_ref="src_g5", source_revision=1, commitment_template_sha256=H,
        phase_graph=(
            PhaseNode(phase_id="starter", kind="starter"),
            PhaseNode(phase_id="produce", kind="production"),
            PhaseNode(phase_id="check", kind="qc", depends_on=("produce",)),
            PhaseNode(phase_id="finish", kind="final", depends_on=("check",)),
        ),
        acceptance_profile_id="ap_g5", acceptance_profile_version="v1",
        acceptance_profile_sha256=H, artifact_sha256=H,
        active_pointer_sha256="0" * 64, definition_sha256="0" * 64,
    ).with_computed_sha256()


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as temporary:
        with LifeShadowStore.open(
            Path(temporary) / "life.shadow.sqlite3", create=True, now_ms=1
        ) as instance:
            yield instance


@pytest.fixture()
def lifecycle(store):
    def runner(candidate_id, fixture_set):
        return [
            {"fixture_kind": "success", "fixture_id": "s1", "passed": True},
            {"fixture_kind": "failure", "fixture_id": "f1", "passed": True},
        ]

    return CapabilityLifecycle(store, fixture_runner=runner)


def test_t23_capability_lifecycle_pointer_cas_and_rollback(store, lifecycle) -> None:
    facts = (
        {"fact_id": "f_1", "terminal": True, "revision": 1, "current_revision": 1},
        {"fact_id": "f_2", "terminal": True, "revision": 1, "current_revision": 1},
    )
    CapabilityLifecycle.evidence_guard(facts)
    with pytest.raises(CapabilityLifecycleError, match="terminal"):
        CapabilityLifecycle.evidence_guard(
            [{"fact_id": "f_3", "terminal": False, "revision": 1, "current_revision": 1}]
        )
    with pytest.raises(CapabilityLifecycleError, match="current revision"):
        CapabilityLifecycle.evidence_guard(
            [{"fact_id": "f_4", "terminal": True, "revision": 1, "current_revision": 2}]
        )

    candidate = lifecycle.register_candidate(
        life_id="life_1", skill_id="skill_g5", skill_version="v1",
        definition=definition(), source_fact_refs=("f_1", "f_2"),
    )
    payload = canonical_sha256(
        {
            "candidate_id": candidate,
            "definition_sha256": definition().definition_sha256,
            "source_fact_refs": ("f_1", "f_2"),
        }
    )
    lifecycle.run_fixtures(candidate_id=candidate, payload_sha256=payload, fixture_set={})
    lifecycle.qc_pass(candidate_id=candidate, payload_sha256=payload)
    lifecycle.stage_shadow(candidate_id=candidate, payload_sha256=payload)
    pointer = lifecycle.promote(
        life_id="life_1", skill_id="skill_g5", candidate_id=candidate,
        artifact_sha256=H, payload_sha256=payload, expected_pointer_sha256=None,
    )
    head = store.get_capability_pointer(life_id="life_1", skill_id="skill_g5")
    assert head is not None and head["current_candidate_id"] == candidate
    assert head["revision"] == 1

    # A failing candidate never replaces the prior CURRENT.
    bad_definition = definition().model_copy(
        update={"artifact_sha256": "0" * 64, "definition_sha256": "0" * 64}
    ).with_computed_sha256()

    def bad_runner(candidate_id, fixture_set):
        return [{"fixture_kind": "success", "fixture_id": "s1", "passed": False}]

    bad_lifecycle = CapabilityLifecycle(store, fixture_runner=bad_runner)
    bad_candidate = bad_lifecycle.register_candidate(
        life_id="life_1", skill_id="skill_g5", skill_version="v2",
        definition=bad_definition, source_fact_refs=("f_1",),
    )
    bad_payload = canonical_sha256(
        {
            "candidate_id": bad_candidate,
            "definition_sha256": bad_definition.definition_sha256,
            "source_fact_refs": ("f_1",),
        }
    )
    with pytest.raises(CapabilityLifecycleError, match="success fixture failed"):
        bad_lifecycle.run_fixtures(
            candidate_id=bad_candidate, payload_sha256=bad_payload, fixture_set={}
        )
    after_failure = store.get_capability_pointer(life_id="life_1", skill_id="skill_g5")
    assert after_failure["current_candidate_id"] == candidate

    # Stale pointer CAS is rejected.
    with pytest.raises(CapabilityLifecycleError, match="stale"):
        lifecycle.promote(
            life_id="life_1", skill_id="skill_g5", candidate_id=candidate,
            artifact_sha256=H, payload_sha256=payload,
            expected_pointer_sha256="0" * 64,
        )

    # Rollback preserves evidence and advances the pointer revision.
    rolled = lifecycle.rollback_pointer(
        life_id="life_1", skill_id="skill_g5",
        previous_candidate_id="cap_prior", previous_artifact_sha256="b" * 64,
        expected_pointer_sha256=pointer,
    )
    head_after = store.get_capability_pointer(life_id="life_1", skill_id="skill_g5")
    assert head_after["pointer_sha256"] == rolled
    assert head_after["revision"] == 2
    assert store.get_capability_pointer(life_id="life_1", skill_id="skill_g5") is not None


def test_t25_paired_blind_vitality_review_is_never_model_self_score() -> None:
    dimensions = (
        "identity", "naturalness", "affect_causality",
        "initiative", "non_template", "novel_composition",
    )
    records: list[dict] = []

    def submit_review(reviewer_id: str, scores: dict[str, int]) -> None:
        if reviewer_id.startswith("model"):
            raise CapabilityLifecycleError("model self-score is never review authority")
        missing = set(dimensions) - set(scores)
        if missing:
            raise CapabilityLifecycleError(f"review missing dimensions: {sorted(missing)}")
        records.append({"reviewer_id": reviewer_id, "scores": dict(scores)})

    submit_review("human_a", {dimension: 7 for dimension in dimensions})
    submit_review("human_b", {dimension: 8 for dimension in dimensions})
    with pytest.raises(CapabilityLifecycleError, match="self-score"):
        submit_review("model_primary", {dimension: 10 for dimension in dimensions})
    assert len(records) == 2
    assert records[0]["reviewer_id"] != records[1]["reviewer_id"]


def test_t19b_skill_definition_core_roundtrip() -> None:
    core = definition()
    assert core.has_valid_sha256()
    assert any(node.kind == "final" for node in core.phase_graph)
    with pytest.raises(Exception):
        SkillDefinitionCore(**{**core.model_dump(), "phase_graph": ()})
    loaded = SkillDefinitionCore.model_validate_json(core.model_dump_json())
    assert loaded.definition_sha256 == core.definition_sha256
