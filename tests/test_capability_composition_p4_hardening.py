from __future__ import annotations

from dataclasses import replace

import pytest

from world_understanding.capability_composition import (
    CapabilityCompositionError,
    CompositionCandidateSnapshotV1,
    P4EvaluationInputV1,
    build_candidate_snapshot,
    compile_capability_composition_plan,
    parse_composition_proposal,
    run_p4_early_evaluation,
    validate_capability_composition_plan,
)

from tests.test_capability_composition_p4 import (
    H,
    _context,
    _proposal_document,
    _single_read_fixture,
    _worlds,
)


def test_candidate_snapshot_rejects_noncanonical_numbering_gaps() -> None:
    _registry, candidates, _context_value, _document = _single_read_fixture()
    original = candidates.action_candidates[0]
    gapped = replace(
        original,
        candidate_id="A02",
        binding_sha256="0" * 64,
    )
    gapped = replace(gapped, binding_sha256=gapped.computed_sha256())

    with pytest.raises(
        CapabilityCompositionError,
        match="candidate.action_set.invalid",
    ):
        CompositionCandidateSnapshotV1(
            schema="tiangong.composition-candidates.v1",
            tool_world_sha256=candidates.tool_world_sha256,
            method_world_sha256=candidates.method_world_sha256,
            method_candidates=candidates.method_candidates,
            action_candidates=(gapped,),
            candidate_snapshot_sha256=H,
        )


def test_candidate_builder_recomputes_tool_descriptor_integrity() -> None:
    _registry, tool_world, method_world = _worlds(
        (
            {
                "action_id": "artifact.read",
                "risk": "A0",
                "effect": "read",
                "side_effects": ("read",),
            },
        )
    )
    original = tool_world.primitives[0]
    tampered = original.model_copy(
        update={"produces": ("type:tampered",)}
    )
    forged_world = replace(
        tool_world,
        primitives=(tampered,),
        snapshot_sha256="0" * 64,
    )
    forged_world = replace(
        forged_world,
        snapshot_sha256=forged_world.payload()
        and __import__("contracts").canonical_sha256(forged_world.payload()),
    )

    with pytest.raises(
        CapabilityCompositionError,
        match="candidate.tool_world.primitive_invalid",
    ):
        build_candidate_snapshot(
            forged_world,
            method_world,
            method_ids=("generate_then_verify",),
            action_ids=("artifact.read",),
        )


def test_candidate_builder_recomputes_method_descriptor_integrity() -> None:
    _registry, tool_world, method_world = _worlds(
        (
            {
                "action_id": "artifact.read",
                "risk": "A0",
                "effect": "read",
                "side_effects": ("read",),
            },
        )
    )
    original = method_world.primitives[0]
    tampered = original.model_copy(
        update={"semantic_summary": "tampered method semantics"}
    )
    forged_world = replace(
        method_world,
        primitives=(tampered,),
        snapshot_sha256="0" * 64,
    )
    forged_world = replace(
        forged_world,
        snapshot_sha256=__import__("contracts").canonical_sha256(
            forged_world.payload()
        ),
    )

    with pytest.raises(
        CapabilityCompositionError,
        match="candidate.method_world.primitive_invalid",
    ):
        build_candidate_snapshot(
            tool_world,
            forged_world,
            method_ids=("generate_then_verify",),
            action_ids=("artifact.read",),
        )


def test_missing_verification_binding_is_unknown_and_rejected() -> None:
    specs = (
        {
            "action_id": "artifact.read",
            "risk": "A0",
            "effect": "read",
            "side_effects": ("read",),
            "verifier_refs": (),
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=(),
        action_ids=("artifact.read",),
    )
    proposal = parse_composition_proposal(
        _proposal_document(
            goal_ref="goal.no-verifier",
            methods=(),
            actions=("A01",),
            steps=(("step.01", "A01", ()),),
        ),
        candidates,
    )
    context = _context(goal_ref="goal.no-verifier")
    plan = compile_capability_composition_plan(
        proposal,
        candidates,
        context,
        registry,
    )
    assert plan.verification_intents == ()

    result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset(),
        validated_at_ms=11,
    )
    assert result.result == "UNKNOWN"
    assert result.unknown_disposition == "REJECT"
    assert result.mandatory_verification is False
    assert any(
        item.code == "validator.verifier.binding_missing"
        for item in result.findings
    )


def test_early_evaluation_reports_plan_and_semantic_metrics_explicitly() -> None:
    registry, candidates, context, document = _single_read_fixture()
    report = run_p4_early_evaluation(
        (
            P4EvaluationInputV1(
                task_id="task-01",
                model_id="abi.test",
                primary_text=document,
            ),
        ),
        candidates_by_task={"task-01": candidates},
        contexts_by_task={"task-01": context},
        registry=registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=12,
        evidence_mode="RECORDED_FIXTURE",
    )
    metrics = report.model_metrics[0]
    assert metrics.plan_success_count == 1
    assert metrics.plan_compile_failure_count == 0
    assert metrics.semantic_validation_failure_count == 0
    assert metrics.proved_valid_count == 1
