from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from contracts import canonical_sha256
from world_understanding.capability_composition import (
    CapabilityCompositionError,
    CapabilityExperienceRecallItemV1,
    build_candidate_snapshot,
    parse_composition_proposal,
)
from world_understanding.context_output import (
    P6ContextEvaluationInputV1,
    P6ProposalProfileObservationV1,
    ProtectedContextIdentityV1,
    build_capability_context_packet,
    build_capability_world_context_slot,
    build_p6_context_evaluation_report,
)
from world_understanding.context_output.slot import conservative_token_estimate

from tests.test_capability_composition_p4 import (
    _proposal_document,
    _worlds,
)
from tests.test_one_world_context_p6 import (
    _materialized_one_world,
    _world_packet,
)
from tests.test_skill_method_world_p3_production import _production_inputs


_ACTION_SPECS = (
    {
        "action_id": "artifact.read",
        "risk": "A0",
        "effect": "read",
        "side_effects": ("read",),
        "produces": ("type:artifact",),
        "read_set": ("resource:artifact",),
    },
    {
        "action_id": "artifact.verify",
        "risk": "A0",
        "effect": "verify",
        "side_effects": ("read",),
        "consumes": ("type:artifact",),
        "produces": ("type:verification",),
        "read_set": ("resource:artifact",),
    },
)


def _legacy_static_context_tokens() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    skill_root = repository_root / "src" / "omni_body_skill"
    index, _index_sha256, _source_hashes = _production_inputs()
    parts = []
    for raw in index["skills"]:
        source_path = skill_root.joinpath(*Path(raw["file"]).parts)
        parts.append(source_path.read_text(encoding="utf-8"))
    return conservative_token_estimate("\n\n".join(parts))


def _proposal_signature(proposal) -> str:
    return canonical_sha256(
        {
            "selected_method_candidate_ids": list(
                proposal.selected_method_candidate_ids
            ),
            "selected_action_candidate_ids": list(
                proposal.selected_action_candidate_ids
            ),
            "steps": [
                {
                    "step_id": item.step_id,
                    "candidate_id": item.candidate_id,
                    "depends_on": list(item.depends_on),
                    "output_bindings": list(item.output_bindings),
                }
                for item in proposal.steps
            ],
            "dependency_edges": [
                list(item) for item in proposal.dependency_edges
            ],
            "control_flow": proposal.control_flow,
        }
    )


def _dsl(goal_ref: str, *, reverse: bool = False) -> str:
    first, second = ("A02", "A01") if reverse else ("A01", "A02")
    return "\n".join(
        (
            "PROPOSAL tiangong.composition-proposal.v1",
            f"GOAL {goal_ref}",
            "METHODS M01",
            "ACTIONS A01,A02",
            f"STEP step.01|{first}|-|out.step.01",
            f"STEP step.02|{second}|step.01|out.step.02",
            "EDGE step.01>step.02",
            "OUTPUTS out.final",
            "FLOW DAG",
            "TAGS rationale.recorded-p6-evaluation",
            "END",
        )
    )


def _recorded_profiles(task_id: str, candidates, *, divergent: bool):
    goal_ref = "goal." + task_id
    structured = parse_composition_proposal(
        _proposal_document(
            goal_ref=goal_ref,
            methods=("M01",),
            actions=("A01", "A02"),
            steps=(
                ("step.01", "A01", ()),
                ("step.02", "A02", ("step.01",)),
            ),
        ),
        candidates,
    )
    strict_dsl = parse_composition_proposal(_dsl(goal_ref), candidates)
    weak_recorded = parse_composition_proposal(
        _dsl(goal_ref, reverse=divergent), candidates
    )
    return tuple(
        sorted(
            (
                P6ProposalProfileObservationV1(
                    profile_id="recorded.strict-dsl",
                    proposal_signature_sha256=_proposal_signature(strict_dsl),
                ),
                P6ProposalProfileObservationV1(
                    profile_id="recorded.structured-json",
                    proposal_signature_sha256=_proposal_signature(structured),
                ),
                P6ProposalProfileObservationV1(
                    profile_id="recorded.weak-json-compatible",
                    proposal_signature_sha256=_proposal_signature(weak_recorded),
                ),
            ),
            key=lambda item: item.profile_id,
        )
    )


def _stale_descriptor_guard_passed(tool_world, method_world) -> bool:
    stale = tool_world.primitives[0].model_copy(
        update={"produces": ("type:stale-descriptor",)}
    )
    forged = replace(
        tool_world,
        primitives=(stale, *tool_world.primitives[1:]),
        snapshot_sha256="0" * 64,
    )
    forged = replace(
        forged,
        snapshot_sha256=canonical_sha256(forged.payload()),
    )
    try:
        build_candidate_snapshot(
            forged,
            method_world,
            method_ids=("generate_then_verify",),
            action_ids=("artifact.read", "artifact.verify"),
        )
    except CapabilityCompositionError as exc:
        return exc.code == "candidate.tool_world.primitive_invalid"
    return False


def test_p6_recorded_context_evaluation_60_tasks_passes_gate() -> None:
    snapshot, store, frame, _cut, tools, _methods = _materialized_one_world()
    _registry, tool_world, method_world = _worlds(_ACTION_SPECS)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.read", "artifact.verify"),
    )
    stale_guard = _stale_descriptor_guard_passed(tool_world, method_world)
    assert stale_guard is True

    experience = CapabilityExperienceRecallItemV1(
        experience_id="capexp_recorded_p6",
        experience_sha256="a" * 64,
        lifecycle="STABLE",
        posterior_success_milli=900,
        lower_confidence_milli=800,
        success_count=9,
        failure_count=1,
        independent_context_count=6,
        last_success_ms=100,
    )
    base_packet = build_capability_context_packet(
        world_state_ref=snapshot.state_ref,
        frame_binding_sha256=tools.frame_binding.binding_sha256,
        candidates=candidates,
        experiences=(experience,),
        protected_identities=(
            ProtectedContextIdentityV1(
                "plan_ref", "plan_" + "1" * 64 + "@" + "2" * 64
            ),
            ProtectedContextIdentityV1(
                "activation_ref", "activation_" + "3" * 64
            ),
            ProtectedContextIdentityV1(
                "verification_plan_ref", "verification_" + "4" * 64
            ),
        ),
    )
    long_method = replace(
        base_packet.method_candidates[0],
        summary="recorded optional summary " + ("x" * 18_000),
    )
    packet = replace(
        base_packet,
        method_candidates=(long_method,),
        packet_sha256="0" * 64,
    )
    packet = replace(packet, packet_sha256=packet.computed_sha256())

    current_count = len(
        store.current_candidates(
            life_id=frame.scope.life_id,
            principal_scope_hash=frame.scope.principal_scope_hash,
            world_scope_hash=frame.scope.world_scope_hash,
        )
    )
    legacy_tokens = _legacy_static_context_tokens()
    assert legacy_tokens > 2_000

    inputs = []
    for ordinal in range(1, 61):
        task_id = f"p6-task-{ordinal:02d}"
        world_packet = _world_packet(snapshot, token_budget=2_000)
        result = build_capability_world_context_slot(
            world_packet,
            packet,
            mode="SHADOW",
        )
        assert result.status == "AVAILABLE"
        assert "recorded optional summary" not in result.slot.rendered_text
        expected_actions = (
            ("action:artifact.read", "action:artifact.verify")
            if ordinal % 2 == 0
            else ("action:artifact.read",)
        )
        inputs.append(
            P6ContextEvaluationInputV1(
                task_id=task_id,
                packet=packet,
                build_result=result,
                token_budget=world_packet.token_budget,
                legacy_static_context_tokens=legacy_tokens,
                expected_frame_binding_sha256=(
                    tools.frame_binding.binding_sha256
                ),
                current_world_state_count=current_count,
                expected_method_refs=("method:generate_then_verify",),
                expected_action_refs=expected_actions,
                expected_experience_refs=(experience.experience_id,),
                proposal_profiles=_recorded_profiles(
                    task_id,
                    candidates,
                    divergent=ordinal % 10 == 0,
                ),
                stale_descriptor_guard_passed=stale_guard,
            )
        )

    report = build_p6_context_evaluation_report(
        tuple(inputs), evidence_mode="RECORDED_FIXTURE"
    )
    assert report.has_valid_sha256()
    assert report.evidence_mode == "RECORDED_FIXTURE"
    assert report.task_count == 60
    assert report.gate_passed is True
    assert report.median_method_precision_milli == 1000
    assert report.median_method_recall_milli == 1000
    assert report.median_action_precision_milli == 750
    assert report.median_action_recall_milli == 1000
    assert report.median_experience_recall_milli == 1000
    assert report.frame_exact_count == 60
    assert report.one_world_state_count == 60
    assert report.authority_safe_count == 60
    assert report.protected_identity_preserved_count == 60
    assert report.stale_descriptor_guard_count == 60
    assert report.divergent_proposal_task_count == 6
    assert report.median_context_tokens < (
        report.median_legacy_static_context_tokens
    )
    assert report.p95_context_tokens <= 2_000
    assert report.median_token_ratio_milli < 1000

    # The report is replayable evidence, not a live-model benchmark.
    serialized = json.dumps(report.payload(), sort_keys=True)
    assert '"evidence_mode": "RECORDED_FIXTURE"' in serialized
    assert "DeepSeek" not in serialized
    assert "GPT" not in serialized
    assert "GLM" not in serialized
