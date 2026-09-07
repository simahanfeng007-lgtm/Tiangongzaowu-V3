"""Pure Method revision seam inside the existing World materialization path.

Only an operator-configured Gateway resolver supplies the verified input. No
signature, filesystem, Git, execution, or current-head authority lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.world_cut import SourceWatermark, WorldCut, derive_world_cut_id
from world_understanding.domain_contribution import compile_skill_method_contribution
from world_understanding.software_world import SoftwareWorldFrame, SparseWorldGraph
from world_understanding.world_state import MaterializationInput, WorldStateMaterializer
from world_understanding.world_state.domain_contributions import bind_domain_contributions
from world_understanding.world_state.store import MaterializedWorldSnapshot
from .models import SkillMethodWorldSnapshotV1

PUBLICATION_SCHEMA = "tiangong.method-world-publication.v1"
ARCHIVE_WATERMARK = "method-source.archive"
SNAPSHOT_WATERMARK = "method-world.snapshot"


def method_marker(state: MaterializedWorldSnapshot, name: str) -> str | None:
    return next((w.watermark_value for w in state.cut.source_watermarks
                 if w.source_kind == "SYSTEM_GOVERNANCE" and w.watermark_type == name), None)


@dataclass(frozen=True, slots=True)
class VerifiedMethodUpdate:
    """Transaction-local data, never accepted from an envelope as authority."""
    frame: SoftwareWorldFrame
    expected_state_ref: WorldRecordRef
    archive_sha256: str
    method_world: SkillMethodWorldSnapshotV1
    changed_source_keys: tuple[str, ...]


class MethodRevisionResolver(Protocol):
    def __call__(self, envelope: WorldIngressEnvelope,
                 previous: MaterializedWorldSnapshot) -> VerifiedMethodUpdate: ...
    def load(self, state: MaterializedWorldSnapshot) -> SkillMethodWorldSnapshotV1: ...


def materialize_method_update(
    materializer: WorldStateMaterializer, envelope: WorldIngressEnvelope,
    previous: MaterializedWorldSnapshot, update: VerifiedMethodUpdate, cut: WorldCut,
) -> tuple[MaterializedWorldSnapshot, SoftwareWorldFrame, SparseWorldGraph]:
    """Replace just the Method domain, retaining unrelated records/dependencies."""
    if (type(update) is not VerifiedMethodUpdate or update.expected_state_ref != previous.state_ref
            or update.frame.scope != envelope.scope_hint
            or update.frame.frame_id != previous.frame_id
            or update.frame.frame_revision_hash != previous.state.frame_ref.sha256
            or update.archive_sha256 != envelope.payload_inline["archive_sha256"]
            or not update.method_world.has_valid_sha256()):
        raise ValueError("METHOD_PUBLICATION_VERIFIED_INPUT_MISMATCH")
    by_key = {(w.source_kind, w.watermark_type): w for w in cut.source_watermarks}
    for name, digest in ((ARCHIVE_WATERMARK, update.archive_sha256),
                         (SNAPSHOT_WATERMARK, update.method_world.snapshot_sha256)):
        key = ("SYSTEM_GOVERNANCE", name)
        old = by_key.get(key)
        by_key[key] = SourceWatermark(
            source_kind=key[0], watermark_type=name, watermark_value=digest,
            sequence=0 if old is None else old.sequence + 1,
            watermark_sha256="0" * 64,
        ).with_computed_hash()
    rows = tuple(sorted(by_key.values(), key=lambda w: w.sort_key()))
    cut = WorldCut(
        cut_id=derive_world_cut_id(world_scope_hash=cut.scope.world_scope_hash, watermarks=rows),
        scope=cut.scope, source_watermarks=rows, time=envelope.source_time, cut_sha256="0" * 64,
    ).with_computed_hash()
    old_frame = update.frame
    frame = SoftwareWorldFrame.build(
        scope=old_frame.scope, workspace=old_frame.workspace, repository=old_frame.repository,
        worktree=old_frame.worktree, branch=old_frame.branch, commit=old_frame.commit,
        environment=old_frame.environment, time=envelope.source_time, world_cut=cut,
    )
    graph = SparseWorldGraph(frame)
    method_entities = {e.entity_id for e in previous.entities if e.entity_type == "SkillMethod"}
    old_method_relations = {r.relation_id for r in previous.relations
                            if r.subject_ref.record_id in method_entities and r.predicate.startswith("method.")}
    for entity in previous.entities:
        if entity.entity_id not in method_entities:
            graph.upsert_entity(entity)
    for relation in previous.relations:
        if relation.relation_id not in old_method_relations:
            graph.upsert_relation(relation)
    contribution = compile_skill_method_contribution(
        frame, cut, update.method_world,
        previous_entities={e.entity_id: e for e in previous.entities},
        previous_relations={r.relation_id: r for r in previous.relations},
    )
    # Preserve non-Method dependency edges so source changes invalidate their
    # derived records rather than silently losing dependency information.
    dependencies = tuple(b for b in previous.dependencies.bindings
                         if not (b.ref.record_type == "world_entity" and b.ref.record_id in method_entities)
                         and not (b.ref.record_type == "world_relation" and b.ref.record_id in old_method_relations))
    data = MaterializationInput(
        frame=frame, cut=cut, graph=graph, dependency_bindings=dependencies,
        changed_source_keys=update.changed_source_keys, preserve_previous_domains=True,
        source_transaction_id=envelope.envelope_id, materialized_at_ms=envelope.source_time.recorded_at_ms,
    )
    unified = bind_domain_contributions(data, (contribution,))
    state = materializer.materialize(unified)
    return state, frame, unified.graph
