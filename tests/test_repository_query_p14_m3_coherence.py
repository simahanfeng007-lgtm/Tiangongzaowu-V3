from __future__ import annotations

from tests.test_repository_query_p14_m3 import _graph, _query
from world_understanding.software_world.query import execute_repository_graph_query


def test_m3_entity_budget_never_returns_dangling_relation_endpoint() -> None:
    frame, graph, module_a, *_ = _graph()
    result = execute_repository_graph_query(
        graph,
        _query(
            frame,
            (module_a.entity_id,),
            direction="BOTH",
            max_depth=4,
            max_entities=2,
            max_relations=128,
        ),
    )
    entity_ids = {ref.record_id for ref in result.entity_refs}
    assert result.truncated is True
    assert "ENTITY_BUDGET" in result.truncation_reasons
    for ref in result.relation_refs:
        relation = graph.relation(ref.record_id)
        assert relation is not None
        assert relation.subject_ref.record_id in entity_ids
        assert relation.value.kind == "entity_ref"
        assert relation.value.entity_ref in entity_ids


def test_m3_every_traversal_step_is_closed_over_returned_entity_and_relation_refs() -> None:
    frame, graph, _, _, function_b, *_ = _graph()
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
    entity_keys = {ref.sort_key() for ref in result.entity_refs}
    relation_keys = {ref.sort_key() for ref in result.relation_refs}
    for step in result.traversal_steps:
        assert step.from_entity_ref.sort_key() in entity_keys
        assert step.to_entity_ref.sort_key() in entity_keys
        assert step.relation_ref.sort_key() in relation_keys
