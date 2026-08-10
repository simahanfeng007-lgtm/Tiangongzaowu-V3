"""P8 semantic input assembly from P4/P6/P7 records, with scope/hash validation and source dedup."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_json_bytes
from contracts.world_understanding._base import WorldRecordRef, WorldValue
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.known import DirectKnownRecord, DerivedKnownRecord
from world_understanding.known.set import known_ref
from world_understanding.common.epistemic import EpistemicPlane
from world_understanding.common.scope import require_exact_scope
from world_understanding.software_world.graph import SparseWorldGraph
from world_understanding.cognition.l5 import CognitionL5View
from .selection import SemanticSubgraph, select_relevant_subgraph

_ALLOWED_CATEGORIES = frozenset({"KNOWN", "ENTITY", "RELATION", "COGNITION", "PRIOR", "UNCERTAINTY", "CONFLICT"})

@dataclass(frozen=True, slots=True)
class SemanticInputItem:
    ref: WorldRecordRef
    category: str
    summary: str
    empirical_evidence_weight_milli: int = 0
    truth_state: str | None = None
    epistemic_state: str | None = None
    def __post_init__(self) -> None:
        if self.category not in _ALLOWED_CATEGORIES:
            raise ValueError("unknown semantic input category")
        if not self.summary or len(self.summary) > 20_000 or "\x00" in self.summary:
            raise ValueError("invalid semantic input summary")
        if not 0 <= self.empirical_evidence_weight_milli <= 1000:
            raise ValueError("semantic input evidence weight out of range")

@dataclass(frozen=True, slots=True)
class SemanticInputBundle:
    scope: WorldScope
    items: tuple[SemanticInputItem, ...]
    subgraph: SemanticSubgraph | None
    def __post_init__(self) -> None:
        keys = tuple(item.ref.sort_key() for item in self.items)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("semantic input refs must be sorted unique")
        if self.subgraph is not None:
            require_exact_scope(self.scope, self.subgraph.scope)
    @property
    def refs(self) -> tuple[WorldRecordRef, ...]:
        return tuple(item.ref for item in self.items)
    @property
    def prior_indices(self) -> frozenset[int]:
        return frozenset(i for i, item in enumerate(self.items) if item.category == "PRIOR")
    def model_payload(self) -> dict:
        return {
            "scope": {
                "life_id": self.scope.life_id,
                "world_id": self.scope.world_id,
                "domain_id": self.scope.domain_id,
                "world_scope_hash": self.scope.world_scope_hash,
                "principal_scope_hash": self.scope.principal_scope_hash,
                "privacy_scope": self.scope.privacy_scope,
            },
            "records": [
                {
                    "index": index,
                    "category": item.category,
                    "ref": item.ref.model_dump(mode="json"),
                    "summary": item.summary,
                    "empirical_evidence_weight_milli": item.empirical_evidence_weight_milli,
                    "truth_state": item.truth_state,
                    "epistemic_state": item.epistemic_state,
                }
                for index, item in enumerate(self.items)
            ],
            "selected_subgraph": None if self.subgraph is None else {
                "seed_entity_ids": self.subgraph.seed_entity_ids,
                "relation_hops": self.subgraph.relation_hops,
                "entity_refs": [ref.model_dump(mode="json") for ref in self.subgraph.entity_refs],
                "relation_refs": [ref.model_dump(mode="json") for ref in self.subgraph.relation_refs],
            },
        }
    def model_payload_json(self) -> str:
        return canonical_json_bytes(self.model_payload()).decode("utf-8")


def _world_value_text(value: WorldValue | None, object_ref: WorldRecordRef | None) -> str:
    if object_ref is not None:
        return f"record:{object_ref.record_type}:{object_ref.record_id}"
    if value is None:
        return ""
    for name in ("entity_ref", "string_value", "integer_value", "boolean_value", "number_milli", "string_set"):
        current = getattr(value, name, None)
        if current is not None:
            return str(current)
    return value.kind


def _known_item(record: DirectKnownRecord | DerivedKnownRecord, *, scope: WorldScope, gamma: EpistemicPlane) -> SemanticInputItem:
    decision = gamma.evaluate_known(record, expected_scope=scope)
    if not decision.admissible:
        raise ValueError("SEMANTIC_KNOWN_SCOPE_OR_CUT_REJECTED")
    if not record.has_valid_hash():
        raise ValueError("SEMANTIC_KNOWN_HASH_INVALID")
    summary = f"{record.proposition_type}|{record.subject_ref}|{record.predicate}|{_world_value_text(record.object_value, record.object_ref)}"
    return SemanticInputItem(known_ref(record), "KNOWN", summary, record.empirical_evidence_weight_milli, record.truth_state, record.epistemic_state)


def _entity_item(entity: object) -> SemanticInputItem:
    if not bool(getattr(entity, "has_valid_hash")()):
        raise ValueError("SEMANTIC_ENTITY_HASH_INVALID")
    ref = WorldRecordRef(record_type="world_entity", record_id=entity.entity_id, revision=entity.revision, sha256=entity.entity_sha256)
    summary = f"{entity.entity_type}|{entity.canonical_name}|lifecycle={entity.lifecycle}"
    return SemanticInputItem(ref, "ENTITY", summary, 0, entity.truth_state, entity.epistemic_state)


def _relation_item(relation: object) -> SemanticInputItem:
    if not bool(getattr(relation, "has_valid_hash")()):
        raise ValueError("SEMANTIC_RELATION_HASH_INVALID")
    ref = WorldRecordRef(record_type="world_relation", record_id=relation.relation_id, revision=relation.revision, sha256=relation.relation_sha256)
    summary = f"{relation.predicate}|subject={relation.subject_ref.record_id}|value={_world_value_text(relation.value, None)}|class={relation.materialization_class}"
    return SemanticInputItem(ref, "RELATION", summary, relation.empirical_evidence_weight_milli, relation.truth_state, relation.epistemic_state)


def _cognition_item(view: CognitionL5View, *, scope: WorldScope) -> SemanticInputItem:
    require_exact_scope(scope, view.scope)
    statement = view.statement
    if statement.status not in {"STABLE", "CORE"} or statement.stability_level not in {"C2", "C3", "C4"}:
        raise ValueError("SEMANTIC_COGNITION_NOT_STABLE")
    if not statement.has_valid_statement_sha256():
        raise ValueError("SEMANTIC_COGNITION_HASH_INVALID")
    value = statement.value
    rendered = next((str(getattr(value, name)) for name in ("entity_ref", "string_value", "integer_value", "boolean_value") if getattr(value, name, None) is not None), value.kind)
    summary = f"{statement.subject_ref}|{statement.predicate}|{rendered}|{statement.status}/{statement.stability_level}|confidence={statement.confidence_milli}"
    return SemanticInputItem(view.statement_ref.record_ref, "COGNITION", summary, 0, None, None)


def build_semantic_input(
    *,
    scope: WorldScope,
    known_records: tuple[DirectKnownRecord | DerivedKnownRecord, ...] = (),
    graph: SparseWorldGraph | None = None,
    seed_entity_ids: tuple[str, ...] = (),
    relation_hops: int = 1,
    stable_cognition: tuple[CognitionL5View, ...] = (),
    auxiliary_items: tuple[SemanticInputItem, ...] = (),
    epistemic_plane: EpistemicPlane | None = None,
) -> SemanticInputBundle:
    gamma = epistemic_plane or EpistemicPlane()
    items: list[SemanticInputItem] = []
    for record in known_records:
        items.append(_known_item(record, scope=scope, gamma=gamma))
    subgraph = None
    if graph is not None:
        require_exact_scope(scope, graph.scope)
        subgraph = select_relevant_subgraph(graph, expected_scope=scope, seed_entity_ids=seed_entity_ids, relation_hops=relation_hops)
        for ref in subgraph.entity_refs:
            entity = graph.entity(ref.record_id)
            if entity is None:
                raise ValueError("SEMANTIC_SUBGRAPH_ENTITY_MISSING")
            items.append(_entity_item(entity))
        for ref in subgraph.relation_refs:
            relation = graph.relation(ref.record_id)
            if relation is None:
                raise ValueError("SEMANTIC_SUBGRAPH_RELATION_MISSING")
            items.append(_relation_item(relation))
    for view in stable_cognition:
        items.append(_cognition_item(view, scope=scope))
    items.extend(auxiliary_items)
    by_ref: dict[tuple[str, str, int, str], SemanticInputItem] = {}
    for item in items:
        key = item.ref.sort_key()
        old = by_ref.get(key)
        if old is not None and old != item:
            raise ValueError("SEMANTIC_DUPLICATE_REF_CONFLICT")
        by_ref[key] = item
    normalized = tuple(by_ref[key] for key in sorted(by_ref))
    if not normalized:
        raise ValueError("semantic input requires at least one source record")
    return SemanticInputBundle(scope, normalized, subgraph)

__all__ = ["SemanticInputItem", "SemanticInputBundle", "build_semantic_input"]
