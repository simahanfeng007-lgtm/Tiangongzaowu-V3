from __future__ import annotations

import ast
from pathlib import Path

import pytest

from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef, WorldValue
from contracts.world_understanding.relation import WorldRelation, derive_relation_id
from contracts.world_understanding.repository_query import RepositoryGraphQuery
from contracts.world_understanding.scope import (
    ScopeBinding,
    WorldScope,
    derive_world_id,
    derive_world_scope_hash,
)
from contracts.world_understanding.time import WorldTime
from world_understanding.software_world.entity import EntitySeed, build_entity, entity_ref
from world_understanding.software_world.frame import SoftwareWorldFrame
from world_understanding.software_world.graph import SparseWorldGraph
from world_understanding.software_world.query import execute_repository_graph_query

ROOT = Path(__file__).resolve().parents[1]


def _scope() -> WorldScope:
    life_id = "life.m3"
    bindings = (ScopeBinding(key="workspace_id", value="workspace.m3"),)
    world_id = derive_world_id(life_id=life_id, namespace_anchor="workspace.m3")
    domain_id = "software_runtime"
    return WorldScope(
        life_id=life_id,
        world_id=world_id,
        domain_id=domain_id,
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id=life_id,
            world_id=world_id,
            domain_id=domain_id,
            scope_bindings=bindings,
        ),
        principal_scope_hash="1" * 64,
        privacy_scope="system",
    )


def _frame() -> SoftwareWorldFrame:
    return SoftwareWorldFrame.build(
        scope=_scope(),
        workspace="workspace.m3",
        repository="repo.m3",
        worktree="worktree.m3",
        branch="main",
        commit="a" * 40,
        environment="test",
        time=WorldTime(valid_from_ms=1000, observed_at_ms=1000, recorded_at_ms=1000),
    )


def _basis(name: str) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="known",
        record_id="known." + name,
        revision=None,
        sha256=canonical_sha256({"name": name}),
    )


def _entity(frame: SoftwareWorldFrame, kind: str, anchor: str, name: str, *, retired: bool = False):
    return build_entity(
        frame,
        EntitySeed(
            entity_type=kind,
            stable_anchor=anchor,
            canonical_name=name,
            basis_ref=_basis(anchor),
            time=frame.time,
            truth_state="TRUE",
            epistemic_state="CURRENT",
        ),
        lifecycle="RETIRED" if retired else "ACTIVE",
    )


def _relation(frame: SoftwareWorldFrame, subject, predicate: str, target):
    subject_ref = entity_ref(subject)
    value = WorldValue(kind="entity_ref", entity_ref=target.entity_id)
    relation_id = derive_relation_id(
        world_scope_hash=frame.scope.world_scope_hash,
        subject_ref=subject_ref,
        predicate=predicate,
        value=value,
        condition_sha256=None,
    )
    return WorldRelation(
        relation_id=relation_id,
        scope=frame.scope,
        subject_ref=subject_ref,
        predicate=predicate,
        value=value,
        extraction_mode="deterministic",
        materialization_class="STRUCTURAL" if predicate in {"DEFINES", "CONTAINS"} else "MATERIALIZED",
        source_observation_refs=(),
        derivation_refs=(),
        truth_state="TRUE",
        epistemic_state="CURRENT",
        empirical_evidence_weight_milli=1000,
        revision=1,
        time=frame.time,
        relation_sha256="0" * 64,
    ).with_computed_hash()


def _graph():
    frame = _frame()
    graph = SparseWorldGraph(frame)
    module_a = _entity(frame, "Module", "module.a", "pkg.a")
    module_b = _entity(frame, "Module", "module.b", "pkg.b")
    function_b = _entity(frame, "Function", "function.b", "pkg.b.changed")
    test_a = _entity(frame, "Module", "test.a", "tests.test_a")
    unrelated = _entity(frame, "Module", "module.c", "pkg.c")
    for entity in (module_a, module_b, function_b, test_a, unrelated):
        graph.upsert_entity(entity)
    for relation in (
        _relation(frame, module_b, "DEFINES", function_b),
        _relation(frame, module_a, "IMPORTS", module_b),
        _relation(frame, test_a, "TESTS", module_a),
        _relation(frame, unrelated, "REFERENCES", test_a),
    ):
        graph.upsert_relation(relation)
    return frame, graph, module_a, module_b, function_b, test_a, unrelated


def _query(frame, seeds, **overrides):
    values = {
        "scope": frame.scope,
        "frame_id": frame.frame_id,
        "frame_revision_hash": frame.frame_revision_hash,
        "seed_tokens": tuple(sorted(seeds)),
        "mode": "NEIGHBORHOOD",
        "direction": "BOTH",
        "relation_predicates": (),
        "max_depth": 1,
        "max_entities": 64,
        "max_relations": 128,
        "max_operations": 2048,
        "include_retired": False,
    }
    values.update(overrides)
    return RepositoryGraphQuery.build(**values)


def _ids(refs):
    return {ref.record_id for ref in refs}


def test_m3_exact_seed_depth_zero_returns_only_seed() -> None:
    frame, graph, _, module_b, _, _, _ = _graph()
    result = execute_repository_graph_query(
        graph, _query(frame, (module_b.entity_id,), max_depth=0)
    )
    assert _ids(result.entity_refs) == {module_b.entity_id}
    assert not result.relation_refs
    assert not result.traversal_steps
    assert result.max_depth_reached == 0
    assert result.truncated is False


def test_m3_neighborhood_outbound_is_directional() -> None:
    frame, graph, module_a, module_b, _, _, _ = _graph()
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (module_a.entity_id,),
            direction="OUTBOUND",
            relation_predicates=("IMPORTS",),
        ),
    )
    assert _ids(result.entity_refs) == {module_a.entity_id, module_b.entity_id}
    assert {step.direction for step in result.traversal_steps} == {"OUTBOUND"}


def test_m3_reverse_impact_walks_defines_then_importers_then_tests() -> None:
    frame, graph, module_a, module_b, function_b, test_a, _ = _graph()
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (function_b.entity_id,),
            mode="IMPACT",
            direction="INBOUND",
            max_depth=3,
        ),
    )
    ids = _ids(result.entity_refs)
    assert function_b.entity_id in ids
    assert module_b.entity_id in ids
    assert module_a.entity_id in ids
    assert test_a.entity_id in ids
    assert result.max_depth_reached == 3
    assert all(step.direction == "INBOUND" for step in result.traversal_steps)


def test_m3_impact_default_does_not_cross_unrelated_fourth_hop() -> None:
    frame, graph, _, _, function_b, _, unrelated = _graph()
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (function_b.entity_id,),
            mode="IMPACT",
            direction="INBOUND",
            max_depth=3,
        ),
    )
    assert unrelated.entity_id not in _ids(result.entity_refs)


def test_m3_relation_filter_is_hard_not_advisory() -> None:
    frame, graph, module_a, _, _, _, _ = _graph()
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (module_a.entity_id,),
            direction="BOTH",
            relation_predicates=("TESTS",),
            max_depth=2,
        ),
    )
    assert {ref.record_id for ref in result.relation_refs} == {
        next(r.relation_id for r in graph.relations() if r.predicate == "TESTS")
    }


def test_m3_ambiguous_seed_never_guesses_identity() -> None:
    frame, graph, *_ = _graph()
    first = _entity(frame, "Function", "dup.1", "pkg.dup")
    second = _entity(frame, "Function", "dup.2", "pkg.dup")
    graph.upsert_entity(first)
    graph.upsert_entity(second)
    result = execute_repository_graph_query(graph, _query(frame, ("pkg.dup",)))
    assert result.ambiguous_seed_tokens == ("pkg.dup",)
    assert not result.matched_seed_refs
    assert not result.entity_refs


def test_m3_unknown_seed_is_explicitly_unresolved() -> None:
    frame, graph, *_ = _graph()
    result = execute_repository_graph_query(graph, _query(frame, ("missing.symbol",)))
    assert result.unresolved_seed_tokens == ("missing.symbol",)
    assert not result.entity_refs


def test_m3_entity_budget_truncates_deterministically() -> None:
    frame, graph, module_a, *_ = _graph()
    result = execute_repository_graph_query(
        graph,
        _query(frame, (module_a.entity_id,), max_depth=4, max_entities=2),
    )
    assert result.truncated is True
    assert "ENTITY_BUDGET" in result.truncation_reasons
    assert len(result.entity_refs) == 2


def test_m3_relation_budget_truncates_deterministically() -> None:
    frame, graph, _, module_b, *_ = _graph()
    extra1 = _entity(frame, "Module", "extra.1", "pkg.extra1")
    extra2 = _entity(frame, "Module", "extra.2", "pkg.extra2")
    graph.upsert_entity(extra1)
    graph.upsert_entity(extra2)
    graph.upsert_relation(_relation(frame, extra1, "IMPORTS", module_b))
    graph.upsert_relation(_relation(frame, extra2, "IMPORTS", module_b))
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (module_b.entity_id,),
            direction="INBOUND",
            max_relations=1,
            max_depth=1,
        ),
    )
    assert result.truncated is True
    assert "RELATION_BUDGET" in result.truncation_reasons
    assert len(result.relation_refs) == 1


def test_m3_operation_budget_is_hard() -> None:
    frame, graph, _, module_b, *_ = _graph()
    extra1 = _entity(frame, "Module", "op.1", "pkg.op1")
    extra2 = _entity(frame, "Module", "op.2", "pkg.op2")
    graph.upsert_entity(extra1)
    graph.upsert_entity(extra2)
    graph.upsert_relation(_relation(frame, extra1, "IMPORTS", module_b))
    graph.upsert_relation(_relation(frame, extra2, "IMPORTS", module_b))
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (module_b.entity_id,),
            direction="INBOUND",
            max_operations=1,
            max_depth=1,
        ),
    )
    assert result.operation_count == 1
    assert "OPERATION_BUDGET" in result.truncation_reasons


def test_m3_query_result_is_hash_deterministic() -> None:
    frame, graph, _, module_b, *_ = _graph()
    query = _query(frame, (module_b.entity_id,), direction="INBOUND", max_depth=3)
    first = execute_repository_graph_query(graph, query)
    second = execute_repository_graph_query(graph, query)
    assert first == second
    assert first.has_valid_hash()


def test_m3_query_does_not_mutate_graph() -> None:
    frame, graph, module_a, *_ = _graph()
    before_entities = graph.entities()
    before_relations = graph.relations()
    execute_repository_graph_query(graph, _query(frame, (module_a.entity_id,), max_depth=4))
    assert graph.entities() == before_entities
    assert graph.relations() == before_relations


def test_m3_stale_frame_revision_is_rejected() -> None:
    frame, graph, module_a, *_ = _graph()
    query = RepositoryGraphQuery.build(
        scope=frame.scope,
        frame_id=frame.frame_id,
        frame_revision_hash="f" * 64,
        seed_tokens=(module_a.entity_id,),
    )
    with pytest.raises(ValueError, match="FRAME_REVISION_MISMATCH"):
        execute_repository_graph_query(graph, query)


def test_m3_impact_contract_requires_inbound_direction() -> None:
    frame, *_ = _graph()
    with pytest.raises(ValueError, match="impact query must use inbound"):
        RepositoryGraphQuery.build(
            scope=frame.scope,
            frame_id=frame.frame_id,
            frame_revision_hash=frame.frame_revision_hash,
            seed_tokens=("pkg.a",),
            mode="IMPACT",
            direction="BOTH",
        )


def test_m3_retired_entity_is_not_queryable_by_default() -> None:
    frame, graph, *_ = _graph()
    retired = _entity(frame, "Module", "retired", "pkg.retired", retired=True)
    graph.upsert_entity(retired)
    result = execute_repository_graph_query(graph, _query(frame, (retired.entity_id,)))
    assert result.unresolved_seed_tokens == (retired.entity_id,)
    assert not result.entity_refs


def test_m3_query_engine_has_no_io_or_runtime_ownership() -> None:
    path = ROOT / "src" / "world_understanding" / "software_world" / "query.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_roots = {
        "os", "pathlib", "subprocess", "socket", "requests", "httpx", "urllib",
        "git", "total_gateway", "life_service", "runtime_security",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert not any(
        imported == root or imported.startswith(root + ".")
        for imported in imports
        for root in forbidden_roots
    )
