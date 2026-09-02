from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contracts import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.context_packet import (
    WorldContextPacket,
    derive_world_packet_id,
)
from total_gateway.action_registry import compile_action_registry
from world_understanding.capability_composition import (
    ProtectedContextIdentityV1 if False else CompositionCandidateSnapshotV1,
)
from world_understanding.context_output import (
    ProtectedContextIdentityV1,
    build_capability_context_packet,
    build_capability_world_context_slot,
)
from world_understanding.domain_contribution import (
    FrameBindingV1,
    compile_skill_method_contribution,
    compile_software_domain_contribution,
    compile_tool_capability_contribution,
)
from world_understanding.skill_method_world import (
    compile_production_skill_method_world,
)
from world_understanding.software_world import SoftwareWorldFrame
from world_understanding.tool_capability_world import (
    compile_tool_capability_world,
)
from world_understanding.world_state import (
    MaterializationInput,
    WorldStateMaterializer,
    WorldStateStore,
    bind_domain_contributions,
    materialize_one_world_state,
)

from tests.test_capability_composition_p4 import _single_read_fixture
from tests.test_skill_method_world_p3_production import _production_inputs
from tests.test_tool_capability_world_p2 import H, manifest, source_ref
from tests.test_world_understanding_p9_world_state import cut, graph_for


def _capability_worlds():
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    manifest_sha256 = canonical_sha256(document)
    action_ids = tuple(sorted(document["capabilities"]))
    tool_world = compile_tool_capability_world(
        document,
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
    return tool_world, method_world


def _materialized_one_world():
    world_cut = cut()
    frame, graph = graph_for(world_cut)
    tool_world, method_world = _capability_worlds()
    software = compile_software_domain_contribution(frame, world_cut, graph)
    tools = compile_tool_capability_contribution(
        frame, world_cut, tool_world
    )
    methods = compile_skill_method_contribution(
        frame, world_cut, method_world
    )
    store = WorldStateStore()
    materializer = WorldStateMaterializer(store)
    data = MaterializationInput(
        frame=frame,
        cut=world_cut,
        graph=graph,
        materialized_at_ms=world_cut.time.recorded_at_ms,
        source_transaction_id="p6.one-world",
    )
    snapshot = materialize_one_world_state(
        materializer,
        data,
        (software, tools, methods),
    )
    return snapshot, store, frame, world_cut, tools, methods


def _world_packet(snapshot, *, token_budget: int = 8_000):
    frame_ref = snapshot.state.frame_ref
    basis_ref = snapshot.state_ref
    task_sha256 = canonical_sha256({"task": "p6-context"})
    projection_sha256 = canonical_sha256({"policy": "p6-context"})
    packet_id = derive_world_packet_id(
        world_scope_hash=snapshot.state.scope.world_scope_hash,
        frame_ref=frame_ref,
        basis_world_state_ref=basis_ref,
        task_ref="task.p6-context",
        task_sha256=task_sha256,
        generated_at_ms=100,
        projection_policy_sha256=projection_sha256,
    )
    return WorldContextPacket(
        packet_id=packet_id,
        scope=snapshot.state.scope,
        frame_ref=frame_ref,
        basis_world_state_ref=basis_ref,
        task_ref="task.p6-context",
        task_sha256=task_sha256,
        generated_at_ms=100,
        token_budget=token_budget,
        mandatory_items=(),
        ranked_items=(),
        uncertainty_items=(),
        prediction_items=(),
        evidence_digest=(),
        expansion_handles=(),
        overflow_state="NONE",
        projection_policy_ref="policy.p6-context",
        projection_policy_sha256=projection_sha256,
        packet_sha256="0" * 64,
    ).with_computed_hash()


def test_exact_frame_contributions_share_one_binding_and_are_non_authorizing() -> None:
    snapshot, _store, frame, world_cut, tools, methods = (
        _materialized_one_world()
    )
    expected = FrameBindingV1.from_frame(frame, world_cut)
    assert tools.frame_binding == expected
    assert methods.frame_binding == expected
    assert tools.may_authorize is False and tools.may_execute is False
    assert methods.may_authorize is False and methods.may_execute is False
    assert tools.has_valid_sha256() and methods.has_valid_sha256()
    assert snapshot.state.may_authorize is False
    assert snapshot.state.may_execute is False


def test_one_current_world_state_contains_software_tool_and_method_entities() -> None:
    snapshot, store, frame, _cut, _tools, _methods = _materialized_one_world()
    types = {item.entity_type for item in snapshot.entities}
    assert "ToolCapability" in types
    assert "SkillMethod" in types
    assert len(types - {"ToolCapability", "SkillMethod"}) >= 1
    current = store.current(
        life_id=frame.scope.life_id,
        world_scope_hash=frame.scope.world_scope_hash,
        principal_scope_hash=frame.scope.principal_scope_hash,
        frame_id=frame.frame_id,
    )
    assert current is not None
    assert current.state.world_state_id == snapshot.state.world_state_id
    assert len(
        store.current_candidates(
            life_id=frame.scope.life_id,
            principal_scope_hash=frame.scope.principal_scope_hash,
            world_scope_hash=frame.scope.world_scope_hash,
        )
    ) == 1


def test_domain_binding_rejects_other_frame_and_generic_runtime_frame() -> None:
    world_cut = cut()
    frame, graph = graph_for(world_cut)
    tool_world, _method_world = _capability_worlds()
    contribution = compile_tool_capability_contribution(
        frame, world_cut, tool_world
    )
    other = SoftwareWorldFrame.build(
        scope=frame.scope,
        workspace=frame.workspace,
        repository=frame.repository,
        worktree=frame.worktree,
        branch="other",
        commit=frame.commit,
        environment=frame.environment,
        time=frame.time,
        world_cut=world_cut,
    )
    with pytest.raises(ValueError, match="FRAME_BINDING_MISMATCH"):
        contribution.require_exact_frame(other, world_cut)

    generic = SoftwareWorldFrame.build(
        scope=frame.scope,
        workspace=frame.workspace,
        repository="generic",
        worktree="generic",
        branch="current",
        commit="latest",
        environment="runtime",
        time=frame.time,
        world_cut=world_cut,
    )
    with pytest.raises(
        ValueError, match="REPOSITORY_BOUND_DESCRIPTOR_GENERIC_FRAME"
    ):
        compile_tool_capability_contribution(
            generic, world_cut, tool_world
        )


def test_binding_is_order_independent_and_materializes_once() -> None:
    world_cut = cut()
    frame, graph = graph_for(world_cut)
    tool_world, method_world = _capability_worlds()
    tools = compile_tool_capability_contribution(
        frame, world_cut, tool_world
    )
    methods = compile_skill_method_contribution(
        frame, world_cut, method_world
    )
    data = MaterializationInput(
        frame=frame,
        cut=world_cut,
        graph=graph,
        materialized_at_ms=1,
        source_transaction_id="p6.order",
    )
    first = bind_domain_contributions(data, (tools, methods))
    second = bind_domain_contributions(data, (methods, tools))
    assert first.source_transaction_id == second.source_transaction_id
    assert first.graph.refs() == second.graph.refs()

    materializer = WorldStateMaterializer(WorldStateStore())
    calls: list[str] = []
    original = materializer.materialize

    def counted(value):
        calls.append(value.source_transaction_id)
        return original(value)

    materializer.materialize = counted
    snapshot = materialize_one_world_state(
        materializer, data, (tools, methods)
    )
    assert snapshot.state.has_valid_hash()
    assert len(calls) == 1


def test_capability_context_uses_existing_slot_and_preserves_all_identities() -> None:
    snapshot, _store, _frame, _cut, tools, _methods = (
        _materialized_one_world()
    )
    _registry, candidates, _context, _document = _single_read_fixture()
    identities = (
        ProtectedContextIdentityV1(
            "plan_ref", "plan_" + "1" * 64 + "@" + "2" * 64
        ),
        ProtectedContextIdentityV1(
            "activation_ref", "activation_" + "3" * 64
        ),
        ProtectedContextIdentityV1(
            "verification_plan_ref", "verification_" + "4" * 64
        ),
    )
    capability = build_capability_context_packet(
        world_state_ref=snapshot.state_ref,
        frame_binding_sha256=tools.frame_binding.binding_sha256,
        candidates=candidates,
        protected_identities=identities,
    )
    packet = _world_packet(snapshot)
    result = build_capability_world_context_slot(
        packet, capability, mode="SHADOW"
    )
    assert result.status == "AVAILABLE"
    assert result.fallback_used is False
    assert result.has_valid_sha256()
    text = result.slot.rendered_text
    for section in (
        "[CURRENT_WORLD]",
        "[METHOD_CANDIDATES]",
        "[ACTION_CANDIDATES]",
        "[PROCEDURAL_EXPERIENCE]",
        "[NEGATIVE_EVIDENCE]",
        "[COMPOSITION_ABI]",
    ):
        assert section in text
    assert f"world_state_ref={snapshot.state_ref.record_id}@{snapshot.state_ref.sha256}" in text
    for identity in identities:
        assert f"{identity.key}={identity.value}" in text
    assert "candidate_id=M01" in text
    assert "candidate_id=A01" in text
    assert "context_only=true" in text
    assert "authorization_source=false" in text
    assert "authorizes=false" in text
    assert "confirms=false" in text
    assert "changes_risk=false" in text
    assert "may_execute=false" in text
    assert result.slot.packet_ref.record_id == packet.packet_id
    assert result.slot.estimated_tokens <= packet.token_budget


def test_context_failure_policy_has_no_implicit_default_fallback() -> None:
    snapshot, _store, _frame, _cut, tools, _methods = (
        _materialized_one_world()
    )
    _registry, candidates, _context, _document = _single_read_fixture()
    capability = build_capability_context_packet(
        world_state_ref=snapshot.state_ref,
        frame_binding_sha256=tools.frame_binding.binding_sha256,
        candidates=candidates,
    )
    tight = _world_packet(snapshot, token_budget=128)
    shadow = build_capability_world_context_slot(
        tight, capability, mode="SHADOW"
    )
    assert shadow.status == "UNAVAILABLE"
    assert shadow.fallback_used is True
    assert shadow.reason_code == "CAPABILITY_CONTEXT_IDENTITY_BUDGET_EXCEEDED"

    default = build_capability_world_context_slot(
        tight, capability, mode="DEFAULT"
    )
    assert default.status == "UNAVAILABLE"
    assert default.fallback_used is False
    limited = build_capability_world_context_slot(
        tight,
        capability,
        mode="LIMITED",
        audited_migration_fallback=True,
    )
    assert limited.fallback_used is True


def test_p6_code_has_no_second_world_state_store_or_context_authority() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "world_understanding"
    paths = (
        root / "domain_contribution.py",
        root / "world_state" / "domain_contributions.py",
        root / "context_output" / "capability_context.py",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "ToolWorldStateStore",
        "SkillWorldStateStore",
        "CapabilityWorldStateStore",
        "sqlite3.connect",
        "authorizes: Literal[True]",
        "may_execute: Literal[True]",
        "from total_gateway",
        "import total_gateway",
    ):
        assert forbidden not in text
    adapter = (
        root / "world_state" / "domain_contributions.py"
    ).read_text(encoding="utf-8")
    tree = __import__("ast").parse(adapter)
    calls = [
        node
        for node in __import__("ast").walk(tree)
        if isinstance(node, __import__("ast").Call)
        and isinstance(node.func, __import__("ast").Attribute)
        and node.func.attr == "materialize"
    ]
    assert len(calls) == 1
