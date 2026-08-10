"""L3 deterministic relation materialization for sparse Software World Graph."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding._base import WorldValue
from contracts.world_understanding.relation import WorldRelation, derive_relation_id
from .perception import SoftwarePerception, RELATION_TYPES, FORBIDDEN_SEMANTIC_RELATIONS
from .entity import entity_ref
from .graph import SparseWorldGraph

MATERIALIZATION_CLASS = {
    "CONTAINS": "STRUCTURAL",
    "DEFINES": "STRUCTURAL",
    "IMPORTS": "MATERIALIZED",
    "DIRECT_CALLS": "MATERIALIZED",
    "CALL_REACHABLE": "DERIVED_CACHE",
    "USES": "MATERIALIZED",
    "READS": "MATERIALIZED",
    "WRITES": "MATERIALIZED",
    "REGISTERED_AS": "STRUCTURAL",
    "BELONGS_TO": "STRUCTURAL",
    "LOCATED_IN": "STRUCTURAL",
    "REFERENCES": "MATERIALIZED",
    "INHERITS": "STRUCTURAL",
    "IMPLEMENTS": "STRUCTURAL",
    "DEPENDS_ON": "MATERIALIZED",
    "BUILDS": "MATERIALIZED",
    "TESTS": "MATERIALIZED",
    "COVERS": "MATERIALIZED",
    "INSTRUCTS_SCOPE": "STRUCTURAL",
}

@dataclass(frozen=True, slots=True)
class RelationMaterializationResult:
    relation: WorldRelation | None
    reason_code: str


def _target_token(perception: SoftwarePerception) -> str | None:
    text = perception.object_text
    if text is None:
        return None
    if perception.proposition_type == "CALL_REACHABLE" and "|path=" in text:
        return text.split("|path=", 1)[0]
    return text


def materialize_relation(
    graph: SparseWorldGraph, perception: SoftwarePerception
) -> RelationMaterializationResult:
    ptype = perception.proposition_type
    if ptype in FORBIDDEN_SEMANTIC_RELATIONS:
        return RelationMaterializationResult(
            None, "SEMANTIC_RELATION_DEFERRED_TO_L4_L5"
        )
    if ptype not in RELATION_TYPES:
        return RelationMaterializationResult(None, "NOT_A_P6_RELATION")
    subjects = graph.resolve_token(perception.subject_ref)
    target_token = _target_token(perception)
    targets = () if target_token is None else graph.resolve_token(target_token)
    if len(subjects) != 1:
        return RelationMaterializationResult(
            None,
            "SUBJECT_IDENTITY_AMBIGUOUS" if subjects else "SUBJECT_ENTITY_NOT_FOUND",
        )
    if len(targets) != 1:
        return RelationMaterializationResult(
            None,
            "TARGET_IDENTITY_AMBIGUOUS" if targets else "TARGET_ENTITY_NOT_FOUND",
        )
    subject, target = subjects[0], targets[0]
    subject_ref = entity_ref(subject)
    value = WorldValue(kind="entity_ref", entity_ref=target.entity_id)
    relation_id = derive_relation_id(
        world_scope_hash=graph.scope.world_scope_hash,
        subject_ref=subject_ref,
        predicate=ptype,
        value=value,
        condition_sha256=None,
    )
    existing = graph.relation(relation_id)
    source_refs = {perception.known_ref.sort_key(): perception.known_ref}
    revision = 1
    supersedes = None
    if existing is not None:
        revision = existing.revision + 1
        supersedes = existing.relation_sha256
        for ref in existing.source_observation_refs:
            source_refs[ref.sort_key()] = ref
    relation = WorldRelation(
        relation_id=relation_id,
        scope=graph.scope,
        subject_ref=subject_ref,
        predicate=ptype,
        value=value,
        extraction_mode=(
            "deterministic"
            if perception.record.derivation_type == "DETERMINISTIC_DERIVED"
            else "observed"
        ),
        materialization_class=MATERIALIZATION_CLASS[ptype],
        source_observation_refs=tuple(
            source_refs[key] for key in sorted(source_refs)
        ),
        derivation_refs=(),
        truth_state=perception.record.truth_state,
        epistemic_state=perception.record.epistemic_state,
        empirical_evidence_weight_milli=(
            perception.record.empirical_evidence_weight_milli
        ),
        revision=revision,
        supersedes_relation_sha256=supersedes,
        time=perception.record.time,
        relation_sha256="0" * 64,
    ).with_computed_hash()
    return RelationMaterializationResult(relation, "OK")


__all__ = [
    "MATERIALIZATION_CLASS",
    "RelationMaterializationResult",
    "materialize_relation",
]
