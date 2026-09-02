from __future__ import annotations

from dataclasses import replace
import json

import pytest

from contracts import (
    ActionPermission,
    ActionRegistrySnapshot,
    canonical_sha256,
)
from contracts.capability_composition import (
    SkillSourcePrimitiveV1,
    SourceRevisionRefV1,
    SourceSpanRefV1,
    ToolSourcePrimitiveV1,
)
from world_understanding.capability_composition import (
    CapabilityCompositionError,
    CompositionCompileContextV1,
    CompositionProposalParseError,
    P4EvaluationInputV1,
    build_candidate_snapshot,
    compile_capability_composition_plan,
    computed_plan_sha256,
    parse_composition_proposal,
    parse_with_single_repair,
    plan_has_valid_sha256,
    run_p4_early_evaluation,
    validate_capability_composition_plan,
    validation_has_valid_sha256,
)
from world_understanding.skill_method_world import (
    MethodMigrationBindingV1,
    SkillMethodWorldSnapshotV1,
    computed_skill_method_descriptor_sha256,
)
from world_understanding.tool_capability_world import (
    ToolCapabilityWorldSnapshotV1,
)


H = "a" * 64
H2 = "b" * 64
REQ = "req_" + "c" * 64
RUN = "run_" + "d" * 64


def _permission(
    action_id: str,
    *,
    manifest_sha256: str,
    risk: str,
    effect: str,
    side_effects: tuple[str, ...],
    allow_shell: bool = False,
    allow_python: bool = False,
) -> ActionPermission:
    return ActionPermission(
        action_id=action_id,
        action_version="omni-registry-v1",
        registry_risk=risk,
        effective_risk=risk,
        effect=effect,
        handler="_action_" + action_id.replace(".", "_"),
        allowed_side_effects=tuple(sorted(set(side_effects))),
        path_policy="object_grant_only",
        allow_absolute_paths=True,
        allow_shell=allow_shell,
        allow_python=allow_python,
        requires_confirmation=False,
        source_manifest_sha256=manifest_sha256,
        permission_sha256="0" * 64,
    ).with_computed_sha256()


def _registry(
    specs: tuple[dict, ...],
    *,
    manifest_sha256: str = H,
) -> ActionRegistrySnapshot:
    permissions = tuple(
        sorted(
            (
                _permission(
                    spec["action_id"],
                    manifest_sha256=manifest_sha256,
                    risk=spec["risk"],
                    effect=spec["effect"],
                    side_effects=spec["side_effects"],
                    allow_shell=spec.get("allow_shell", False),
                    allow_python=spec.get("allow_python", False),
                )
                for spec in specs
            ),
            key=lambda item: item.action_id,
        )
    )
    return ActionRegistrySnapshot(
        registry_id="omni-action-registry",
        revision=1,
        generated_at_ms=1,
        source_manifest_sha256=manifest_sha256,
        executable_count=len(permissions),
        permissions=permissions,
        registry_sha256="0" * 64,
    ).with_computed_sha256()


def _tool_primitive(
    action_id: str,
    *,
    manifest_sha256: str,
    risk: str,
    effect: str,
    side_effects: tuple[str, ...],
    resource_scope: tuple[str, ...] = ("object_grant_only",),
    consumes: tuple[str, ...] = (),
    produces: tuple[str, ...] = ("type:artifact",),
    read_set: tuple[str, ...] = (),
    write_set: tuple[str, ...] = (),
    verifier_refs: tuple[str, ...] = ("verifier:artifact",),
    idempotency: str = "IDEMPOTENT",
    determinism: str = "DETERMINISTIC",
    availability: str = "AVAILABLE",
) -> ToolSourcePrimitiveV1:
    primitive = ToolSourcePrimitiveV1(
        source_primitive_id=f"tool-source:{action_id}",
        action_id=action_id,
        action_version="omni-registry-v1",
        provider_component_id="omni-body",
        implementation_refs=(
            SourceSpanRefV1(
                path="src/omni_body_skill/tools/omni_body_tool.py"
            ),
        ),
        implementation_hashes=(H2,),
        action_manifest_sha256=manifest_sha256,
        argument_schema_sha256=H,
        result_schema_sha256=H,
        consumes=consumes,
        produces=produces,
        effect_class=f"effect:{effect}",
        side_effects=tuple(sorted(set(side_effects))),
        risk_floor=risk,
        idempotency=idempotency,
        determinism_class=determinism,
        resource_scope=tuple(sorted(set(resource_scope))),
        read_set_descriptor=tuple(sorted(set(read_set))),
        write_set_descriptor=tuple(sorted(set(write_set))),
        evidence_contract=("evidence:typed-result",),
        verifier_refs=tuple(sorted(set(verifier_refs))),
        failure_taxonomy=("failure:runtime",),
        availability=availability,
        descriptor_sha256="0" * 64,
    )
    return primitive.model_copy(
        update={
            "descriptor_sha256": canonical_sha256(
                primitive.model_dump(
                    mode="json", exclude={"descriptor_sha256"}
                )
            )
        }
    )


def _method_primitive() -> tuple[SkillSourcePrimitiveV1, MethodMigrationBindingV1]:
    binding = MethodMigrationBindingV1(
        method_id="generate_then_verify",
        legacy_skill_ids=(
            "skill_code_project_delivery_worldclass_v1",
            "skill_word_business_proposal_worldclass_v1",
        ),
        required_phases=("PRODUCTION", "VERIFICATION"),
        binding_sha256="0" * 64,
    ).with_computed_sha256()
    source_ref = SourceRevisionRefV1(
        source_kind="SKILL_METHOD",
        semantic_id="generate_then_verify",
        version="v1",
        source_files=(
            "src/omni_body_skill/deliverable_skills/29_skill_word_business_proposal_worldclass.md",
            "src/omni_body_skill/deliverable_skills/31_skill_code_project_delivery_worldclass.md",
        ),
        source_sha256=H2,
        descriptor_sha256="0" * 64,
        manifest_sha256=None,
    )
    primitive = SkillSourcePrimitiveV1(
        method_id="generate_then_verify",
        version="v1",
        source_ref=source_ref,
        source_sha256=H2,
        title="Generate then verify",
        semantic_summary="Produce an outcome and verify it before completion.",
        goal_classes=("goal-class:artifact-production",),
        preconditions=("condition:goal-defined",),
        expected_postconditions=("condition:outcome-verified",),
        required_capability_classes=(
            "capability-class:artifact-production",
        ),
        method_steps=(
            "method-step:01-produce",
            "method-step:02-verify",
        ),
        control_flow_hints=("control-flow:verify-before-complete",),
        failure_modes=("failure-mode:verification-failed",),
        fallback_patterns=("fallback-pattern:diagnose-and-retry",),
        verification_intent=("verifier:artifact",),
        composition_tags=("composition-tag:production-method",),
        descriptor_sha256="0" * 64,
    )
    descriptor = computed_skill_method_descriptor_sha256(primitive)
    return (
        primitive.model_copy(
            update={
                "source_ref": source_ref.model_copy(
                    update={"descriptor_sha256": descriptor}
                ),
                "descriptor_sha256": descriptor,
            }
        ),
        binding,
    )


def _worlds(
    specs: tuple[dict, ...],
    *,
    manifest_sha256: str = H,
) -> tuple[
    ActionRegistrySnapshot,
    ToolCapabilityWorldSnapshotV1,
    SkillMethodWorldSnapshotV1,
]:
    registry = _registry(specs, manifest_sha256=manifest_sha256)
    primitives = tuple(
        sorted(
            (
                _tool_primitive(
                    spec["action_id"],
                    manifest_sha256=manifest_sha256,
                    risk=spec["risk"],
                    effect=spec["effect"],
                    side_effects=spec["side_effects"],
                    resource_scope=spec.get(
                        "resource_scope", ("object_grant_only",)
                    ),
                    consumes=spec.get("consumes", ()),
                    produces=spec.get("produces", ("type:artifact",)),
                    read_set=spec.get("read_set", ()),
                    write_set=spec.get("write_set", ()),
                    verifier_refs=spec.get(
                        "verifier_refs", ("verifier:artifact",)
                    ),
                    idempotency=spec.get("idempotency", "IDEMPOTENT"),
                    determinism=spec.get(
                        "determinism", "DETERMINISTIC"
                    ),
                    availability=spec.get("availability", "AVAILABLE"),
                )
                for spec in specs
            ),
            key=lambda item: item.action_id,
        )
    )
    tool_world = ToolCapabilityWorldSnapshotV1(
        schema="tiangong.tool-capability-world.v1",
        source_manifest_sha256=manifest_sha256,
        action_registry_sha256=registry.registry_sha256,
        primitives=primitives,
        relations=(),
        snapshot_sha256="0" * 64,
    )
    tool_world = replace(
        tool_world,
        snapshot_sha256=canonical_sha256(tool_world.payload()),
    )

    method, binding = _method_primitive()
    method_world = SkillMethodWorldSnapshotV1(
        schema="tiangong.skill-method-world.v1",
        legacy_corpus_sha256=H,
        method_sources_sha256=H2,
        primitives=(method,),
        migration_bindings=(binding,),
        relations=(),
        snapshot_sha256="0" * 64,
    )
    method_world = replace(
        method_world,
        snapshot_sha256=canonical_sha256(method_world.payload()),
    )
    return registry, tool_world, method_world


def _context(
    *,
    goal_ref: str,
    manifest_sha256: str = H,
    generation: int = 0,
) -> CompositionCompileContextV1:
    return CompositionCompileContextV1(
        schema="tiangong.composition-compile-context.v1",
        request_id=REQ,
        run_id=RUN,
        generation=generation,
        principal_scope_hash=H,
        world_state_ref="world.current",
        world_state_sha256=H2,
        goal_ref=goal_ref,
        goal_fingerprint=canonical_sha256({"goal_ref": goal_ref}),
        environment_class="environment.test",
        context_fingerprint_sha256=H,
        capability_manifest_sha256=manifest_sha256,
        created_at_ms=10,
        context_sha256="0" * 64,
    ).with_computed_sha256()


def _proposal_document(
    *,
    goal_ref: str,
    methods: tuple[str, ...],
    actions: tuple[str, ...],
    steps: tuple[tuple[str, str, tuple[str, ...]], ...],
    control_flow: str = "DAG",
    extra: dict | None = None,
) -> str:
    document = {
        "proposal_schema": "tiangong.composition-proposal.v1",
        "goal_ref": goal_ref,
        "selected_method_candidate_ids": list(methods),
        "selected_action_candidate_ids": list(actions),
        "steps": [
            {
                "step_id": step_id,
                "candidate_id": candidate_id,
                "depends_on": list(depends_on),
                "output_bindings": [f"out.{step_id}"],
            }
            for step_id, candidate_id, depends_on in steps
        ],
        "dependency_edges": [
            [dependency, step_id]
            for step_id, _candidate_id, dependencies in steps
            for dependency in dependencies
        ],
        "output_bindings": ["out.final"],
        "control_flow": control_flow,
        "rationale_tags": ["rationale.bounded-candidates"],
    }
    if extra:
        document.update(extra)
    return json.dumps(document, ensure_ascii=False, sort_keys=True)


def _single_read_fixture(
    *,
    idempotency: str = "IDEMPOTENT",
    determinism: str = "DETERMINISTIC",
    risk: str = "A0",
    effect: str = "read",
) -> tuple[
    ActionRegistrySnapshot,
    object,
    CompositionCompileContextV1,
    str,
]:
    specs = (
        {
            "action_id": "artifact.read",
            "risk": risk,
            "effect": effect,
            "side_effects": ("read",)
            if effect == "read"
            else ("local_write", "read"),
            "read_set": ("resource:artifact",)
            if effect == "read"
            else (),
            "write_set": ("resource:artifact",)
            if effect != "read"
            else (),
            "idempotency": idempotency,
            "determinism": determinism,
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.read",),
    )
    goal_ref = "goal.single-read"
    document = _proposal_document(
        goal_ref=goal_ref,
        methods=("M01",),
        actions=("A01",),
        steps=(("step.01", "A01", ()),),
    )
    return registry, candidates, _context(goal_ref=goal_ref), document


def test_json_and_dsl_proposals_compile_to_the_same_identity() -> None:
    registry, candidates, context, document = _single_read_fixture()
    json_proposal = parse_composition_proposal(document, candidates)
    dsl = """\
PROPOSAL tiangong.composition-proposal.v1
GOAL goal.single-read
METHODS M01
ACTIONS A01
STEP step.01|A01|-|out.step.01
OUTPUTS out.final
FLOW DAG
TAGS rationale.bounded-candidates
END
"""
    dsl_proposal = parse_composition_proposal(dsl, candidates)
    assert json_proposal.proposal_sha256 == dsl_proposal.proposal_sha256

    first = compile_capability_composition_plan(
        json_proposal, candidates, context, registry
    )
    second = compile_capability_composition_plan(
        dsl_proposal, candidates, context, registry
    )
    assert first.plan_sha256 == second.plan_sha256
    assert plan_has_valid_sha256(first)
    assert first.permission_requirements == ("artifact.read",)
    assert first.capability_manifest_sha256 == registry.source_manifest_sha256
    assert first.memory_experience_refs == ()


def test_model_cannot_submit_authority_or_invent_candidate() -> None:
    _registry_value, candidates, _context_value, document = _single_read_fixture()
    with pytest.raises(
        CompositionProposalParseError, match="proposal.fields.invalid"
    ):
        parse_composition_proposal(
            _proposal_document(
                goal_ref="goal.single-read",
                methods=("M01",),
                actions=("A01",),
                steps=(("step.01", "A01", ()),),
                extra={"permission_requirements": ["artifact.read"]},
            ),
            candidates,
        )

    invented = json.loads(document)
    invented["selected_action_candidate_ids"] = ["A99"]
    invented["steps"][0]["candidate_id"] = "A99"
    with pytest.raises(
        CompositionProposalParseError, match="action_candidate.unknown"
    ):
        parse_composition_proposal(
            json.dumps(invented, ensure_ascii=False), candidates
        )


def test_parser_allows_exactly_one_repair() -> None:
    _registry_value, candidates, _context_value, valid = _single_read_fixture()
    outcome = parse_with_single_repair(
        '{"bad": true}',
        candidates,
        repair_text=valid,
    )
    assert outcome.repaired is True
    assert outcome.primary_error_code == "proposal.fields.invalid"

    with pytest.raises(
        CompositionProposalParseError, match="proposal.repair.failed"
    ):
        parse_with_single_repair(
            '{"bad": true}',
            candidates,
            repair_text='{"still_bad": true}',
        )


def test_cycle_is_rejected_by_system_compiler() -> None:
    specs = (
        {
            "action_id": "artifact.read",
            "risk": "A0",
            "effect": "read",
            "side_effects": ("read",),
        },
        {
            "action_id": "artifact.verify",
            "risk": "A0",
            "effect": "verify",
            "side_effects": ("read",),
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.read", "artifact.verify"),
    )
    text = _proposal_document(
        goal_ref="goal.cycle",
        methods=("M01",),
        actions=("A01", "A02"),
        steps=(
            ("step.01", "A01", ("step.02",)),
            ("step.02", "A02", ("step.01",)),
        ),
    )
    proposal = parse_composition_proposal(text, candidates)
    with pytest.raises(CapabilityCompositionError, match="dependency.cycle"):
        compile_capability_composition_plan(
            proposal, candidates, _context(goal_ref="goal.cycle"), registry
        )


def test_composition_risk_escalates_sensitive_external_flow_to_a5() -> None:
    specs = (
        {
            "action_id": "credential.read",
            "risk": "A1",
            "effect": "read",
            "side_effects": ("read",),
            "resource_scope": ("credential",),
            "produces": ("type:credential",),
            "read_set": ("resource:credential",),
        },
        {
            "action_id": "http.send",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("external_write", "read"),
            "resource_scope": ("network",),
            "consumes": ("type:credential",),
            "produces": ("type:delivery-result",),
            "write_set": ("resource:external-endpoint",),
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("credential.read", "http.send"),
    )
    proposal = parse_composition_proposal(
        _proposal_document(
            goal_ref="goal.sensitive-flow",
            methods=("M01",),
            actions=("A01", "A02"),
            steps=(
                ("step.01", "A01", ()),
                ("step.02", "A02", ("step.01",)),
            ),
        ),
        candidates,
    )
    context = _context(goal_ref="goal.sensitive-flow")
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    assert plan.risk_floor == "A2"
    assert plan.composition_risk == "A5"
    assert "flow:sensitive-source-to-external-sink" in (
        plan.information_flow_findings
    )

    result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert result.result == "PROVED_INVALID"
    assert validation_has_valid_sha256(result)


def test_validator_rejects_permission_expansion_even_with_recomputed_plan_hash() -> None:
    registry, candidates, context, document = _single_read_fixture()
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    tampered = plan.model_copy(
        update={
            "permission_requirements": (
                "artifact.read",
                "invented.write",
            )
        }
    )
    tampered = tampered.model_copy(
        update={"plan_sha256": computed_plan_sha256(tampered)}
    )
    assert plan_has_valid_sha256(tampered)

    result = validate_capability_composition_plan(
        tampered,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert result.result == "PROVED_INVALID"
    assert any(
        finding.code == "validator.plan.compiler_mismatch"
        for finding in result.findings
    )


def test_validator_unknown_policy_is_risk_sensitive() -> None:
    registry, candidates, context, document = _single_read_fixture(
        idempotency="UNKNOWN",
        determinism="NONDETERMINISTIC",
    )
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    read_result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert read_result.result == "UNKNOWN"
    assert read_result.unknown_disposition == "PROVISIONAL_ALLOW"
    assert read_result.mandatory_verification is True

    write_specs = (
        {
            "action_id": "artifact.write",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("local_write", "read"),
            "write_set": ("resource:artifact",),
            "idempotency": "UNKNOWN",
            "determinism": "NONDETERMINISTIC",
        },
    )
    write_registry, tool_world, method_world = _worlds(write_specs)
    write_candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.write",),
    )
    write_text = _proposal_document(
        goal_ref="goal.write",
        methods=("M01",),
        actions=("A01",),
        steps=(("step.01", "A01", ()),),
    )
    write_proposal = parse_composition_proposal(
        write_text, write_candidates
    )
    write_context = _context(goal_ref="goal.write")
    write_plan = compile_capability_composition_plan(
        write_proposal, write_candidates, write_context, write_registry
    )
    write_result = validate_capability_composition_plan(
        write_plan,
        write_proposal,
        write_candidates,
        write_context,
        write_registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert write_result.result == "UNKNOWN"
    assert write_result.unknown_disposition == "REJECT"
    assert write_result.mandatory_verification is False


def test_validator_proves_mechanical_validity_for_deterministic_plan() -> None:
    registry, candidates, context, document = _single_read_fixture()
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert result.result == "PROVED_VALID"
    assert result.unknown_disposition == "NOT_APPLICABLE"
    assert result.mandatory_verification is False
    assert validation_has_valid_sha256(result)


def test_dependency_type_mismatch_is_proved_invalid() -> None:
    specs = (
        {
            "action_id": "artifact.read",
            "risk": "A0",
            "effect": "read",
            "side_effects": ("read",),
            "produces": ("type:text",),
        },
        {
            "action_id": "artifact.write",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("local_write", "read"),
            "consumes": ("type:image",),
            "produces": ("type:artifact",),
            "write_set": ("resource:artifact",),
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.read", "artifact.write"),
    )
    proposal = parse_composition_proposal(
        _proposal_document(
            goal_ref="goal.type-mismatch",
            methods=("M01",),
            actions=("A01", "A02"),
            steps=(
                ("step.01", "A01", ()),
                ("step.02", "A02", ("step.01",)),
            ),
        ),
        candidates,
    )
    context = _context(goal_ref="goal.type-mismatch")
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert result.result == "PROVED_INVALID"
    assert any(
        finding.code == "validator.dependency.type_incompatible"
        for finding in result.findings
    )


def test_parallel_write_conflict_is_proved_invalid() -> None:
    specs = (
        {
            "action_id": "artifact.write-a",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("local_write", "read"),
            "write_set": ("resource:artifact",),
        },
        {
            "action_id": "artifact.write-b",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("local_write", "read"),
            "write_set": ("resource:artifact",),
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.write-a", "artifact.write-b"),
    )
    proposal = parse_composition_proposal(
        _proposal_document(
            goal_ref="goal.parallel-write",
            methods=("M01",),
            actions=("A01", "A02"),
            steps=(
                ("step.01", "A01", ()),
                ("step.02", "A02", ()),
            ),
        ),
        candidates,
    )
    context = _context(goal_ref="goal.parallel-write")
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    result = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert result.result == "PROVED_INVALID"
    assert any(
        finding.code == "validator.write_set.parallel_conflict"
        for finding in result.findings
    )


def _dsl_for_task(task_id: str) -> str:
    return f"""\
PROPOSAL tiangong.composition-proposal.v1
GOAL goal.{task_id}
METHODS M01
ACTIONS A01
STEP step.01|A01|-|out.step.01
OUTPUTS out.final
FLOW DAG
TAGS rationale.bounded-candidates
END
"""


def test_p4_recorded_protocol_preflight_runs_24_by_3_without_execution() -> None:
    registry, tool_world, method_world = _worlds(
        (
            {
                "action_id": "artifact.read",
                "risk": "A0",
                "effect": "read",
                "side_effects": ("read",),
            },
        )
    )
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.read",),
    )
    inputs: list[P4EvaluationInputV1] = []
    contexts = {}
    candidate_bindings = {}
    for ordinal in range(1, 25):
        task_id = f"task-{ordinal:02d}"
        goal_ref = f"goal.{task_id}"
        valid_json = _proposal_document(
            goal_ref=goal_ref,
            methods=("M01",),
            actions=("A01",),
            steps=(("step.01", "A01", ()),),
        )
        inputs.extend(
            (
                P4EvaluationInputV1(
                    task_id=task_id,
                    model_id="abi.json-structured",
                    primary_text=valid_json,
                ),
                P4EvaluationInputV1(
                    task_id=task_id,
                    model_id="abi.json-one-repair",
                    primary_text='{"bad": true}',
                    repair_text=valid_json,
                ),
                P4EvaluationInputV1(
                    task_id=task_id,
                    model_id="abi.strict-dsl",
                    primary_text=_dsl_for_task(task_id),
                ),
            )
        )
        contexts[task_id] = _context(goal_ref=goal_ref)
        candidate_bindings[task_id] = candidates

    report = run_p4_early_evaluation(
        tuple(inputs),
        candidates_by_task=candidate_bindings,
        contexts_by_task=contexts,
        registry=registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=12,
        evidence_mode="RECORDED_FIXTURE",
    )
    assert report.has_valid_sha256()
    assert report.evidence_mode == "RECORDED_FIXTURE"
    assert len(report.cases) == 72
    assert len(report.model_metrics) == 3
    assert all(item.case_count == 24 for item in report.model_metrics)
    assert all(item.proved_valid_count == 24 for item in report.model_metrics)
    assert all(item.final_parse_failure_count == 0 for item in report.model_metrics)
    repaired = next(
        item
        for item in report.model_metrics
        if item.model_id == "abi.json-one-repair"
    )
    assert repaired.primary_parse_failure_count == 24
    assert repaired.repair_attempt_count == 24
    assert repaired.repair_success_count == 24
