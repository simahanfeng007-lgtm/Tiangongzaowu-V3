"""Sparse in-memory World Graph for L0-L3 software world.

Only materialized WorldEntity/WorldRelation records live here. Derivation DAG records never enter this graph.
"""
from __future__ import annotations
from collections import defaultdict
from contracts.world_understanding.entity import WorldEntity
from contracts.world_understanding.relation import WorldRelation
from world_understanding.common.scope import require_exact_scope
from .frame import SoftwareWorldFrame

class FrameMismatch(ValueError): pass
class InvalidGraphRecord(ValueError): pass

class SparseWorldGraph:
    __slots__ = (
        "scope", "frame_id", "frame_revision_hash", "_entities", "_relations",
        "_token_index", "_path_index", "_relations_by_entity", "_applied_git_delta_ids",
    )
    def __init__(self, frame: SoftwareWorldFrame) -> None:
        self.scope = frame.scope
        self.frame_id = frame.frame_id
        self.frame_revision_hash = frame.frame_revision_hash
        self._entities: dict[str, WorldEntity] = {}
        self._relations: dict[str, WorldRelation] = {}
        self._token_index: dict[str, set[str]] = defaultdict(set)
        self._path_index: dict[str, set[str]] = defaultdict(set)
        self._relations_by_entity: dict[str, set[str]] = defaultdict(set)
        self._applied_git_delta_ids: set[str] = set()

    def require_frame(self, frame: SoftwareWorldFrame) -> None:
        require_exact_scope(self.scope, frame.scope)
        if self.frame_id != frame.frame_id:
            raise FrameMismatch("SOFTWARE_WORLD_FRAME_MISMATCH")

    def entity(self, entity_id: str) -> WorldEntity | None:
        return self._entities.get(entity_id)

    def relation(self, relation_id: str) -> WorldRelation | None:
        return self._relations.get(relation_id)

    def entities(self) -> tuple[WorldEntity, ...]:
        return tuple(self._entities[key] for key in sorted(self._entities))

    def relations(self) -> tuple[WorldRelation, ...]:
        return tuple(self._relations[key] for key in sorted(self._relations))

    def resolve_token(self, token: str) -> tuple[WorldEntity, ...]:
        if token in self._entities:
            return (self._entities[token],)
        ids = self._token_index.get(token, ())
        return tuple(self._entities[entity_id] for entity_id in sorted(ids) if entity_id in self._entities and self._entities[entity_id].lifecycle == "ACTIVE")

    def file_entities(self, path: str) -> tuple[WorldEntity, ...]:
        ids = self._path_index.get(path, ())
        return tuple(self._entities[entity_id] for entity_id in sorted(ids) if entity_id in self._entities and self._entities[entity_id].lifecycle == "ACTIVE")

    def relations_touching(self, entity_id: str) -> tuple[WorldRelation, ...]:
        ids = self._relations_by_entity.get(entity_id, ())
        return tuple(self._relations[relation_id] for relation_id in sorted(ids) if relation_id in self._relations)

    def _unindex_entity(self, entity: WorldEntity) -> None:
        for token in (entity.canonical_name, *entity.aliases):
            bucket = self._token_index.get(token)
            if bucket is not None:
                bucket.discard(entity.entity_id)
                if not bucket: self._token_index.pop(token, None)
        if entity.entity_type == "File":
            for token in (entity.canonical_name, *entity.aliases):
                bucket = self._path_index.get(token)
                if bucket is not None:
                    bucket.discard(entity.entity_id)
                    if not bucket: self._path_index.pop(token, None)

    def _index_entity(self, entity: WorldEntity) -> None:
        if entity.lifecycle != "ACTIVE":
            return
        for token in (entity.canonical_name, *entity.aliases):
            self._token_index[token].add(entity.entity_id)
        if entity.entity_type == "File":
            self._path_index[entity.canonical_name].add(entity.entity_id)

    def upsert_entity(self, entity: WorldEntity) -> WorldEntity | None:
        require_exact_scope(self.scope, entity.scope)
        if not entity.has_valid_hash(): raise InvalidGraphRecord("entity hash invalid")
        old = self._entities.get(entity.entity_id)
        if old is not None: self._unindex_entity(old)
        self._entities[entity.entity_id] = entity
        self._index_entity(entity)
        return old

    def delete_entity(self, entity_id: str) -> WorldEntity | None:
        old = self._entities.pop(entity_id, None)
        if old is not None: self._unindex_entity(old)
        return old

    @staticmethod
    def _relation_entity_ids(relation: WorldRelation) -> tuple[str, ...]:
        ids = {relation.subject_ref.record_id}
        if relation.value.kind == "entity_ref" and relation.value.entity_ref is not None:
            ids.add(relation.value.entity_ref)
        return tuple(sorted(ids))

    def _unindex_relation(self, relation: WorldRelation) -> None:
        for entity_id in self._relation_entity_ids(relation):
            bucket = self._relations_by_entity.get(entity_id)
            if bucket is not None:
                bucket.discard(relation.relation_id)
                if not bucket: self._relations_by_entity.pop(entity_id, None)

    def _index_relation(self, relation: WorldRelation) -> None:
        for entity_id in self._relation_entity_ids(relation):
            self._relations_by_entity[entity_id].add(relation.relation_id)

    def upsert_relation(self, relation: WorldRelation) -> WorldRelation | None:
        require_exact_scope(self.scope, relation.scope)
        if not relation.has_valid_hash(): raise InvalidGraphRecord("relation hash invalid")
        old = self._relations.get(relation.relation_id)
        if old is not None: self._unindex_relation(old)
        self._relations[relation.relation_id] = relation
        self._index_relation(relation)
        return old

    def delete_relation(self, relation_id: str) -> WorldRelation | None:
        old = self._relations.pop(relation_id, None)
        if old is not None: self._unindex_relation(old)
        return old

    def has_git_delta(self, delta_id: str) -> bool:
        return delta_id in self._applied_git_delta_ids

    def applied_git_delta_ids(self) -> tuple[str, ...]:
        """Expose immutable dedup state so transaction graph forks retain it."""
        return tuple(sorted(self._applied_git_delta_ids))

    def mark_git_delta(self, delta_id: str) -> None:
        self._applied_git_delta_ids.add(delta_id)

    def advance_frame(self, frame: SoftwareWorldFrame) -> None:
        self.require_frame(frame)
        self.frame_revision_hash = frame.frame_revision_hash

__all__ = ["FrameMismatch", "InvalidGraphRecord", "SparseWorldGraph"]
