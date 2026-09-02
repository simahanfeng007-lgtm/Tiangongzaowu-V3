"""Merge exact-frame P6 domain contributions before one existing materialization.

The adapter creates a transaction-local SparseWorldGraph, adds Software/Tool/
Method records, and calls the existing WorldStateMaterializer exactly once. It
does not own a Store, current-head index, publication path, or WorldState type.
"""

from __future__ import annotations

from dataclasses import replace

from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.entity import WorldEntity
from contracts.world_understanding.relation import WorldRelation
from world_understanding.domain_contribution import WorldDomainContributionV1
from world_understanding.software_world.graph import SparseWorldGraph

from .manifests import DependencyBinding
from .materializer import MaterializationInput, WorldStateMaterializer
from .store import MaterializedWorldSnapshot


def _entity_ref(value: WorldEntity) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_entity",
        record_id=value.entity_id,
        revision=value.revision,
        sha256=value.entity_sha256,
    )


def _relation_ref(value: WorldRelation) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_relation",
        record_id=value.relation_id,
        revision=value.revision,
        sha256=value.relation_sha256,
    )


def _merge_entity(graph: SparseWorldGraph, incoming: WorldEntity) -> None:
    previous = graph.entity(incoming.entity_id)
    if previous is not None:
        if previous.entity_sha256 == incoming.entity_sha256:
            return
        if (
            incoming.revision != previous.revision + 1
            or incoming.supersedes_entity_sha256 != previous.entity_sha256
        ):
            raise ValueError("WORLD_DOMAIN_ENTITY_REVISION_DISCONTINUITY")
    graph.upsert_entity(incoming)


def _merge_relation(graph: SparseWorldGraph, incoming: WorldRelation) -> None:
    previous = graph.relation(incoming.relation_id)
    if previous is not None:
        if previous.relation_sha256 == incoming.relation_sha256:
            return
        if (
            incoming.revision != previous.revision + 1
            or incoming.supersedes_relation_sha256 != previous.relation_sha256
        ):
            raise ValueError("WORLD_DOMAIN_RELATION_REVISION_DISCONTINUITY")
    graph.upsert_relation(incoming)


def _merge_dependency(
    by_ref: dict[tuple[str, str, int, str], DependencyBinding],
    binding: DependencyBinding,
) -> None:
    key = binding.ref.sort_key()
    previous = by_ref.get(key)
    if previous is None:
        by_ref[key] = binding
        return
    by_ref[key] = DependencyBinding(
        ref=binding.ref,
        source_keys=tuple(
            sorted(set(previous.source_keys) | set(binding.source_keys))
        ),
        evidence_ids=tuple(
            sorted(set(previous.evidence_ids) | set(binding.evidence_ids))
        ),
    )


def bind_domain_contributions(
    data: MaterializationInput,
    contributions: tuple[WorldDomainContributionV1, ...],
) -> MaterializationInput:
    """Return one transaction input containing all exact-frame contributions."""

    ordered = tuple(
        sorted(
            contributions,
            key=lambda item: (item.contribution_kind, item.contribution_id),
        )
    )
    ids = tuple(item.contribution_id for item in ordered)
    kinds = tuple(item.contribution_kind for item in ordered)
    if len(ids) != len(set(ids)):
        raise ValueError("WORLD_DOMAIN_CONTRIBUTION_ID_DUPLICATE")
    if len(kinds) != len(set(kinds)):
        raise ValueError("WORLD_DOMAIN_CONTRIBUTION_KIND_DUPLICATE")

    merged = SparseWorldGraph(data.frame)
    for entity in data.graph.entities():
        merged.upsert_entity(entity)
    for relation in data.graph.relations():
        merged.upsert_relation(relation)
    for delta_id in data.graph.applied_git_delta_ids():
        merged.mark_git_delta(delta_id)

    dependencies = {
        item.ref.sort_key(): item for item in data.dependency_bindings
    }
    for contribution in ordered:
        contribution.require_exact_frame(data.frame, data.cut)
        source_keys = tuple(
            sorted(
                set(contribution.dependency_source_keys)
                | {"contribution:" + contribution.contribution_sha256}
            )
        )
        for entity in contribution.entities:
            _merge_entity(merged, entity)
            _merge_dependency(
                dependencies,
                DependencyBinding(
                    ref=_entity_ref(entity),
                    source_keys=source_keys,
                ),
            )
        for relation in contribution.relations:
            _merge_relation(merged, relation)
            _merge_dependency(
                dependencies,
                DependencyBinding(
                    ref=_relation_ref(relation),
                    source_keys=source_keys,
                ),
            )

    contribution_digest = canonical_sha256(
        {
            "domain": "tiangong.one-world-domain-materialization.v1",
            "base_transaction_id": data.source_transaction_id,
            "frame_revision_hash": data.frame.frame_revision_hash,
            "world_cut_sha256": data.cut.cut_sha256,
            "contributions": [
                {
                    "contribution_id": item.contribution_id,
                    "contribution_sha256": item.contribution_sha256,
                    "kind": item.contribution_kind,
                }
                for item in ordered
            ],
        }
    )
    return replace(
        data,
        graph=merged,
        dependency_bindings=tuple(
            dependencies[key] for key in sorted(dependencies)
        ),
        source_transaction_id="worldstate.tx." + contribution_digest,
    )


def materialize_one_world_state(
    materializer: WorldStateMaterializer,
    data: MaterializationInput,
    contributions: tuple[WorldDomainContributionV1, ...],
) -> MaterializedWorldSnapshot:
    """Use the existing materializer once; no alternate publication path exists."""

    if type(materializer) is not WorldStateMaterializer:
        raise TypeError("existing WorldStateMaterializer authority is required")
    unified = bind_domain_contributions(data, contributions)
    return materializer.materialize(unified)


__all__ = [
    "bind_domain_contributions",
    "materialize_one_world_state",
]
