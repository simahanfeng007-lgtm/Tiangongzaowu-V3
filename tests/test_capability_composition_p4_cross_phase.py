from __future__ import annotations

from contracts import canonical_sha256
from total_gateway.action_registry import compile_action_registry
from world_understanding.capability_composition import (
    build_candidate_snapshot,
    compile_capability_composition_plan,
    parse_composition_proposal,
    validate_capability_composition_plan,
)
from world_understanding.skill_method_world import (
    compile_production_skill_method_world,
)
from world_understanding.tool_capability_world import (
    compile_tool_capability_world,
)

from tests.test_capability_composition_p4 import (
    H,
    _context,
    _proposal_document,
)
from tests.test_skill_method_world_p3_production import _production_inputs
from tests.test_tool_capability_world_p2 import manifest, source_ref


def test_p2_p3_outputs_form_a_p4_plan_without_new_authority() -> None:
    capability_manifest = manifest()
    registry = compile_action_registry(capability_manifest, generated_at_ms=1)
    manifest_sha256 = canonical_sha256(capability_manifest)
    action_ids = tuple(sorted(capability_manifest["capabilities"]))
    tool_world = compile_tool_capability_world(
        capability_manifest,
        registry,
        source_revisions={
            action_id: source_ref(action_id, manifest_sha256)
            for action_id in action_ids
        },
        argument_schema_hashes={action_id: H for action_id in action_ids},
        result_schema_hashes={action_id: H for action_id in action_ids},
    )

    index, index_sha256, source_hashes = _production_inputs()
    method_world = compile_production_skill_method_world(
        index,
        index_source_sha256=index_sha256,
        skill_source_hashes=source_hashes,
    )
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("file.read",),
    )
    assert candidates.may_authorize is False
    assert candidates.may_execute is False
    assert candidates.method_candidates[0].primitive.method_id == (
        "generate_then_verify"
    )
    assert candidates.action_candidates[0].primitive.action_id == "file.read"

    proposal = parse_composition_proposal(
        _proposal_document(
            goal_ref="goal.cross-phase-read",
            methods=("M01",),
            actions=("A01",),
            steps=(("step.01", "A01", ()),),
        ),
        candidates,
    )
    context = _context(
        goal_ref="goal.cross-phase-read",
        manifest_sha256=registry.source_manifest_sha256,
    )
    plan = compile_capability_composition_plan(
        proposal,
        candidates,
        context,
        registry,
    )
    assert plan.permission_requirements == ("file.read",)
    assert len(plan.method_source_refs) == 1
    assert len(plan.action_source_refs) == 1
    assert plan.capability_manifest_sha256 == registry.source_manifest_sha256
    assert plan.memory_experience_refs == ()

    # The current P2 source is intentionally conservative about idempotency
    # and determinism. P4 therefore returns UNKNOWN, but the A0 read can be
    # provisionally considered only with mandatory verification.
    result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset(plan.verification_intents),
        validated_at_ms=11,
    )
    assert result.result == "UNKNOWN"
    assert result.unknown_disposition == "PROVISIONAL_ALLOW"
    assert result.mandatory_verification is True
