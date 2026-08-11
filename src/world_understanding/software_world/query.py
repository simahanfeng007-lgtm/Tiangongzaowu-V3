"""Deterministic bounded read-only queries over the Software World Graph.

The query engine does not read Git/filesystem/network state, does not invoke an
LLM, does not mutate graph records, and cannot authorize or execute work. Impact
queries are conservative reverse traversals over already-materialized graph
edges only.
"""
from __future__ import annotations

from collections import deque
import heapq

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.repository_query import (
    RepositoryGraphQuery,
    RepositoryGraphQueryResult,
    RepositoryRankedEvidence,
    RepositoryTraversalStep,
    derive_repository_graph_result_id,
)
from world_understanding.common.scope import require_exact_scope

from .entity import entity_ref
from .graph import SparseWorldGraph

# Reverse impact is intentionally structural and empirical. It cannot invent a
# semantic dependency that is absent from the graph.
DEFAULT_IMPACT_PREDICATES = frozenset({
    "CONTAINS",
    "DEFINES",
    "IMPORTS",
    "DIRECT_CALLS",
    "CALL_REACHABLE",
    "USES",
    "READS",
    "REFERENCES",
    "INHERITS",
    "IMPLEMENTS",
    "DEPENDS_ON",
    "BUILDS",
    "TESTS",
    "COVERS",
    "LOCATED_IN",
})

# Relation meaning, evidence confidence, hop decay and direction are kept
# separate so a result score can be reproduced without model inference.
ASSOCIATIVE_RELATION_WEIGHT_MILLI = {
    "DIRECT_CALLS": 1000,
    "REFERENCES": 940,
    "TESTS": 940,
    "COVERS": 940,
    "INHERITS": 900,
    "IMPLEMENTS": 900,
    "DEFINES": 860,
    "BUILDS": 850,
    "DEPENDS_ON": 820,
    "IMPORTS": 760,
    "CONTAINS": 700,
    "LOCATED_IN": 650,
}
_DEFAULT_RELATION_WEIGHT_MILLI = 600
_HOP_DECAY_MILLI = 780


def relation_ref(relation) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_relation",
        record_id=relation.relation_id,
        revision=relation.revision,
        sha256=relation.relation_sha256,
    )


def _active(entity, include_retired: bool) -> bool:
    return include_retired or entity.lifecycle == "ACTIVE"


def _target_id(relation) -> str | None:
    if relation.value.kind != "entity_ref":
        return None
    return relation.value.entity_ref


def _eligible_neighbors(
    graph: SparseWorldGraph,
    *,
    entity_id: str,
    direction: str,
    predicates: frozenset[str] | None,
    include_retired: bool,
):
    rows = []
    for relation in graph.relations_touching(entity_id):
        if predicates is not None and relation.predicate not in predicates:
            continue
        subject_id = relation.subject_ref.record_id
        target_id = _target_id(relation)
        if target_id is None:
            continue
        if direction in {"OUTBOUND", "BOTH"} and subject_id == entity_id:
            target = graph.entity(target_id)
            if target is not None and _active(target, include_retired):
                rows.append((relation, target, "OUTBOUND"))
        if direction in {"INBOUND", "BOTH"} and target_id == entity_id:
            subject = graph.entity(subject_id)
            if subject is not None and _active(subject, include_retired):
                rows.append((relation, subject, "INBOUND"))
    return tuple(sorted(
        rows,
        key=lambda item: (
            item[0].relation_id,
            item[1].entity_id,
            item[2],
        ),
    ))


def _activation_score(current_score: int, relation, direction: str) -> int:
    predicate_weight = ASSOCIATIVE_RELATION_WEIGHT_MILLI.get(
        relation.predicate, _DEFAULT_RELATION_WEIGHT_MILLI
    )
    evidence_weight = max(1, relation.empirical_evidence_weight_milli)
    direction_weight = 1000 if direction == "OUTBOUND" else 920
    return max(1, (
        current_score
        * predicate_weight
        * evidence_weight
        * direction_weight
        * _HOP_DECAY_MILLI
    ) // (1000 ** 4))


def execute_repository_graph_query(
    graph: SparseWorldGraph,
    query: RepositoryGraphQuery,
) -> RepositoryGraphQueryResult:
    if not query.has_valid_hash():
        raise ValueError("REPOSITORY_QUERY_HASH_INVALID")
    require_exact_scope(query.scope, graph.scope)
    if query.frame_id != graph.frame_id:
        raise ValueError("REPOSITORY_QUERY_FRAME_MISMATCH")
    if query.frame_revision_hash != graph.frame_revision_hash:
        raise ValueError("REPOSITORY_QUERY_FRAME_REVISION_MISMATCH")

    predicates: frozenset[str] | None
    if query.relation_predicates:
        predicates = frozenset(query.relation_predicates)
    elif query.mode == "IMPACT":
        predicates = DEFAULT_IMPACT_PREDICATES
    else:
        predicates = None

    matched_seed_ids: set[str] = set()
    seed_match_counts: dict[str, int] = {}
    ambiguous: set[str] = set()
    unresolved: set[str] = set()
    truncation_reasons: set[str] = set()

    for token in query.seed_tokens:
        matches = tuple(
            entity
            for entity in graph.resolve_token(token)
            if _active(entity, query.include_retired)
        )
        # Never guess among identities. Callers can retry with an exact entity id.
        if len(matches) > 1:
            ambiguous.add(token)
            continue
        if not matches:
            unresolved.add(token)
            continue
        matched_seed_ids.add(matches[0].entity_id)
        seed_match_counts[matches[0].entity_id] = (
            seed_match_counts.get(matches[0].entity_id, 0) + 1
        )

    selected_entities: dict[str, object] = {}
    queue: deque[tuple[str, int]] = deque()
    priority_queue: list[tuple[int, int, str]] = []
    activation_scores: dict[str, int] = {}
    activation_paths: dict[str, tuple[str, ...]] = {}
    activation_predicates: dict[str, str | None] = {}
    for entity_id in sorted(matched_seed_ids):
        if len(selected_entities) >= query.max_entities:
            truncation_reasons.add("ENTITY_BUDGET")
            break
        entity = graph.entity(entity_id)
        if entity is None or not _active(entity, query.include_retired):
            continue
        selected_entities[entity_id] = entity
        activation_scores[entity_id] = 1000
        activation_paths[entity_id] = ()
        activation_predicates[entity_id] = None
        if query.mode == "ASSOCIATIVE":
            heapq.heappush(priority_queue, (-1000, 0, entity_id))
        else:
            queue.append((entity_id, 0))

    selected_relations: dict[str, object] = {}
    steps: dict[tuple, RepositoryTraversalStep] = {}
    visited_depth: dict[str, int] = {
        entity_id: 0 for entity_id in selected_entities
    }
    operation_count = 0
    max_depth_reached = 0
    hard_stop = False

    while (priority_queue if query.mode == "ASSOCIATIVE" else queue) and not hard_stop:
        if query.mode == "ASSOCIATIVE":
            negative_score, current_depth, current_id = heapq.heappop(priority_queue)
            if -negative_score != activation_scores.get(current_id):
                continue
        else:
            current_id, current_depth = queue.popleft()
        max_depth_reached = max(max_depth_reached, current_depth)
        if current_depth >= query.max_depth:
            continue
        eligible = list(_eligible_neighbors(
            graph,
            entity_id=current_id,
            direction=query.direction,
            predicates=predicates,
            include_retired=query.include_retired,
        ))
        if query.mode == "ASSOCIATIVE":
            current_score = activation_scores.get(current_id, 1)
            eligible.sort(key=lambda item: (
                -_activation_score(current_score, item[0], item[2]),
                item[0].relation_id,
                item[1].entity_id,
                item[2],
            ))
        for relation, neighbor, traversal_direction in eligible:
            operation_count += 1
            if operation_count > query.max_operations:
                operation_count = query.max_operations
                truncation_reasons.add("OPERATION_BUDGET")
                hard_stop = True
                break

            # A bounded result is a coherent subgraph: never publish a relation
            # whose opposite endpoint could not fit inside the entity budget.
            neighbor_is_new = neighbor.entity_id not in selected_entities
            if neighbor_is_new and len(selected_entities) >= query.max_entities:
                truncation_reasons.add("ENTITY_BUDGET")
                hard_stop = True
                break

            if relation.relation_id not in selected_relations:
                if len(selected_relations) >= query.max_relations:
                    truncation_reasons.add("RELATION_BUDGET")
                    hard_stop = True
                    break
                selected_relations[relation.relation_id] = relation

            next_depth = current_depth + 1
            candidate_score = _activation_score(
                activation_scores.get(current_id, 1), relation, traversal_direction
            )
            candidate_path = (
                activation_paths.get(current_id, ()) + (relation.relation_id,)
            )[:query.max_depth]
            if neighbor_is_new:
                selected_entities[neighbor.entity_id] = neighbor
                visited_depth[neighbor.entity_id] = next_depth
                activation_scores[neighbor.entity_id] = candidate_score
                activation_paths[neighbor.entity_id] = candidate_path
                activation_predicates[neighbor.entity_id] = relation.predicate
                if query.mode == "ASSOCIATIVE":
                    heapq.heappush(
                        priority_queue,
                        (-candidate_score, next_depth, neighbor.entity_id),
                    )
                else:
                    queue.append((neighbor.entity_id, next_depth))
            elif next_depth < visited_depth.get(neighbor.entity_id, next_depth):
                visited_depth[neighbor.entity_id] = next_depth
                if query.mode == "ASSOCIATIVE":
                    if candidate_score > activation_scores.get(neighbor.entity_id, 0):
                        activation_scores[neighbor.entity_id] = candidate_score
                        activation_paths[neighbor.entity_id] = candidate_path
                        activation_predicates[neighbor.entity_id] = relation.predicate
                        heapq.heappush(
                            priority_queue,
                            (-candidate_score, next_depth, neighbor.entity_id),
                        )
                else:
                    queue.append((neighbor.entity_id, next_depth))
            elif (
                query.mode == "ASSOCIATIVE"
                and candidate_score > activation_scores.get(neighbor.entity_id, 0)
            ):
                activation_scores[neighbor.entity_id] = candidate_score
                activation_paths[neighbor.entity_id] = candidate_path
                activation_predicates[neighbor.entity_id] = relation.predicate
                heapq.heappush(
                    priority_queue,
                    (-candidate_score, next_depth, neighbor.entity_id),
                )

            current = graph.entity(current_id)
            if current is None:
                continue
            step = RepositoryTraversalStep(
                depth=next_depth,
                direction=traversal_direction,
                from_entity_ref=entity_ref(current),
                relation_ref=relation_ref(relation),
                to_entity_ref=entity_ref(neighbor),
                step_sha256="0" * 64,
            ).with_computed_hash()
            steps[step.sort_key()] = step
            max_depth_reached = max(max_depth_reached, next_depth)

    entity_refs = tuple(sorted(
        (entity_ref(entity) for entity in selected_entities.values()),
        key=lambda ref: ref.sort_key(),
    ))
    relation_refs = tuple(sorted(
        (relation_ref(relation) for relation in selected_relations.values()),
        key=lambda ref: ref.sort_key(),
    ))
    matched_refs = tuple(sorted(
        (
            entity_ref(selected_entities[entity_id])
            for entity_id in matched_seed_ids
            if entity_id in selected_entities
        ),
        key=lambda ref: ref.sort_key(),
    ))
    reasons = tuple(sorted(truncation_reasons))
    ranked_evidence = ()
    if query.mode == "ASSOCIATIVE":
        ranked_rows = []
        for entity_id, entity in selected_entities.items():
            path_refs = tuple(
                relation_ref(selected_relations[relation_id])
                for relation_id in activation_paths.get(entity_id, ())
                if relation_id in selected_relations
            )
            ranked_rows.append(RepositoryRankedEvidence(
                entity_ref=entity_ref(entity),
                score_milli=activation_scores.get(entity_id, 0),
                seed_distance=visited_depth.get(entity_id, 0),
                matched_seed_count=max(1, seed_match_counts.get(entity_id, 1)),
                strongest_predicate=activation_predicates.get(entity_id),
                path_relation_refs=path_refs,
                evidence_sha256="0" * 64,
            ).with_computed_hash())
        ranked_evidence = tuple(sorted(
            ranked_rows, key=lambda item: item.sort_key()
        ))
    result = RepositoryGraphQueryResult(
        result_id=derive_repository_graph_result_id(
            query_id=query.query_id, query_sha256=query.query_sha256
        ),
        query_id=query.query_id,
        query_sha256=query.query_sha256,
        scope=query.scope,
        frame_id=query.frame_id,
        frame_revision_hash=query.frame_revision_hash,
        matched_seed_refs=matched_refs,
        ambiguous_seed_tokens=tuple(sorted(ambiguous)),
        unresolved_seed_tokens=tuple(sorted(unresolved)),
        entity_refs=entity_refs,
        relation_refs=relation_refs,
        traversal_steps=tuple(steps[key] for key in sorted(steps)),
        ranked_evidence=ranked_evidence,
        max_depth_reached=max_depth_reached,
        operation_count=operation_count,
        truncated=bool(reasons),
        truncation_reasons=reasons,
        result_sha256="0" * 64,
    ).with_computed_hash()
    return result


__all__ = [
    "DEFAULT_IMPACT_PREDICATES",
    "ASSOCIATIVE_RELATION_WEIGHT_MILLI",
    "execute_repository_graph_query",
    "relation_ref",
]
