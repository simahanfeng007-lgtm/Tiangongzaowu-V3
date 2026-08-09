"""Deterministic, bounded subgraph selection for P8 semantic interpretation."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding._base import WorldRecordRef, sorted_unique_refs
from contracts.world_understanding.scope import WorldScope
from world_understanding.software_world.graph import SparseWorldGraph
from world_understanding.common.scope import require_exact_scope


def entity_ref(entity: object) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_entity",
        record_id=getattr(entity, "entity_id"),
        revision=getattr(entity, "revision"),
        sha256=getattr(entity, "entity_sha256"),
    )


def relation_ref(relation: object) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_relation",
        record_id=getattr(relation, "relation_id"),
        revision=getattr(relation, "revision"),
        sha256=getattr(relation, "relation_sha256"),
    )

@dataclass(frozen=True, slots=True)
class SemanticSubgraph:
    scope: WorldScope
    entity_refs: tuple[WorldRecordRef, ...]
    relation_refs: tuple[WorldRecordRef, ...]
    seed_entity_ids: tuple[str, ...]
    relation_hops: int
    def __post_init__(self) -> None:
        sorted_unique_refs(self.entity_refs)
        sorted_unique_refs(self.relation_refs)
        if self.seed_entity_ids != tuple(sorted(set(self.seed_entity_ids))):
            raise ValueError("semantic subgraph seeds must be sorted unique")
        if self.relation_hops < 0:
            raise ValueError("relation_hops must be non-negative")
    @property
    def record_refs(self) -> tuple[WorldRecordRef, ...]:
        refs = (*self.entity_refs, *self.relation_refs)
        return tuple(sorted({ref.sort_key(): ref for ref in refs}.values(), key=lambda ref: ref.sort_key()))


def _relation_entity_ids(relation: object) -> tuple[str, ...]:
    ids = {getattr(getattr(relation, "subject_ref"), "record_id")}
    value = getattr(relation, "value")
    if getattr(value, "kind", None) == "entity_ref" and getattr(value, "entity_ref", None):
        ids.add(value.entity_ref)
    return tuple(sorted(ids))


def select_relevant_subgraph(
    graph: SparseWorldGraph,
    *,
    expected_scope: WorldScope,
    seed_entity_ids: tuple[str, ...],
    relation_hops: int = 1,
    max_entities: int = 64,
    max_relations: int = 128,
) -> SemanticSubgraph:
    require_exact_scope(expected_scope, graph.scope)
    if relation_hops < 0 or max_entities < 1 or max_relations < 0:
        raise ValueError("invalid semantic subgraph bounds")
    seeds = tuple(sorted(set(seed_entity_ids)))
    selected_entities: dict[str, object] = {}
    selected_relations: dict[str, object] = {}
    frontier = list(seeds)
    seen = set()
    depth = 0
    while frontier and depth <= relation_hops:
        next_frontier: list[str] = []
        for entity_id in sorted(frontier):
            if entity_id in seen:
                continue
            seen.add(entity_id)
            entity = graph.entity(entity_id)
            if entity is None or getattr(entity, "lifecycle", None) != "ACTIVE":
                continue
            if len(selected_entities) >= max_entities and entity_id not in selected_entities:
                continue
            selected_entities[entity_id] = entity
            if depth == relation_hops:
                continue
            for relation in graph.relations_touching(entity_id):
                if len(selected_relations) >= max_relations and relation.relation_id not in selected_relations:
                    continue
                selected_relations[relation.relation_id] = relation
                for related_id in _relation_entity_ids(relation):
                    if related_id not in seen and len(selected_entities) + len(next_frontier) < max_entities:
                        next_frontier.append(related_id)
        frontier = sorted(set(next_frontier))
        depth += 1
    entities = tuple(sorted((entity_ref(item) for item in selected_entities.values()), key=lambda ref: ref.sort_key()))
    relations = tuple(sorted((relation_ref(item) for item in selected_relations.values()), key=lambda ref: ref.sort_key()))
    return SemanticSubgraph(expected_scope, entities, relations, seeds, relation_hops)

__all__ = ["SemanticSubgraph", "entity_ref", "relation_ref", "select_relevant_subgraph"]
