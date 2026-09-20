"""Tool-source publication into the ONE World invalidation chain.

Mirrors the Method publication path: one SYSTEM_GOVERNANCE envelope whose
payload names the published bundle replaces ONLY the Tool Capability domain,
advances the tool watermarks, and passes precise ``changed_source_keys``
(``tool-world:<old/new snapshot>``) into the ORIGINAL materializer so the
existing dependency manifest invalidates exactly the affected records. No
second invalidation store, no planner authority, no execution grant; the
tool world itself stays non-authorizing data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.world_cut import (
    SourceWatermark,
    WorldCut,
    derive_world_cut_id,
)
from world_understanding.domain_contribution import (
    compile_tool_capability_contribution,
)
from world_understanding.software_world import (
    SoftwareWorldFrame,
    SparseWorldGraph,
)
from world_understanding.world_state.manifests import DependencyManifest
from world_understanding.world_state.materializer import (
    MaterializationInput,
    WorldStateMaterializer,
)
from world_understanding.world_state.store import MaterializedWorldSnapshot
from contracts.world_understanding.ingress import WorldIngressEnvelope
from world_understanding.world_state.domain_contributions import bind_domain_contributions

from .compiler import ToolCapabilityWorldSnapshotV1

TOOL_BUNDLE_WATERMARK = "tool-source.bundle"
TOOL_WORLD_WATERMARK = "tool-world.snapshot"
TOOL_PUBLICATION_SCHEMA = "tiangong.tool-source-publication.v1"
_TOOL_WORLD_KEY_PREFIX = "tool-world:"


@dataclass(frozen=True, slots=True)
class VerifiedToolUpdate:
    """Transaction-local data, never accepted from an envelope as authority."""

    frame: SoftwareWorldFrame
    expected_state_ref: WorldRecordRef
    bundle_sha256: str
    tool_world: ToolCapabilityWorldSnapshotV1
    changed_source_keys: tuple[str, ...]


class ToolRevisionResolver(Protocol):
    def __call__(self, envelope: WorldIngressEnvelope,
                 previous: MaterializedWorldSnapshot) -> VerifiedToolUpdate: ...


def previous_tool_world_keys(previous: MaterializedWorldSnapshot) -> tuple[str, ...]:
    """Every ``tool-world:`` dependency key the current state still binds."""

    return tuple(sorted({
        key
        for binding in previous.dependencies.bindings
        for key in binding.source_keys
        if key.startswith(_TOOL_WORLD_KEY_PREFIX)
    }))


def compute_tool_changed_source_keys(
    previous: MaterializedWorldSnapshot,
    tool_world: ToolCapabilityWorldSnapshotV1,
) -> tuple[str, ...]:
    """Old bindings plus the new snapshot key: byte changes never pass silently.

    A same-version bundle with different bytes produces a different snapshot
    digest, so both keys land in the changed set and the materializer
    invalidates every record bound to either. No display name or bare version
    number participates.
    """

    return tuple(sorted(set(previous_tool_world_keys(previous))
                        | {_TOOL_WORLD_KEY_PREFIX + tool_world.snapshot_sha256}))


def _bump(cut: WorldCut, watermark_type: str, digest: str) -> WorldCut:
    by_key = {(w.source_kind, w.watermark_type): w for w in cut.source_watermarks}
    key = ("SYSTEM_GOVERNANCE", watermark_type)
    old = by_key.get(key)
    by_key[key] = SourceWatermark(
        source_kind=key[0], watermark_type=watermark_type,
        watermark_value=digest,
        sequence=0 if old is None else old.sequence + 1,
        watermark_sha256="0" * 64,
    ).with_computed_hash()
    rows = tuple(sorted(by_key.values(), key=lambda w: w.sort_key()))
    return WorldCut(
        cut_id=derive_world_cut_id(
            world_scope_hash=cut.scope.world_scope_hash, watermarks=rows),
        scope=cut.scope, source_watermarks=rows, time=cut.time,
        cut_sha256="0" * 64,
    ).with_computed_hash()


def materialize_tool_update(
    materializer: WorldStateMaterializer, envelope: WorldIngressEnvelope,
    previous: MaterializedWorldSnapshot, update: VerifiedToolUpdate, cut: WorldCut,
) -> tuple[MaterializedWorldSnapshot, SoftwareWorldFrame, SparseWorldGraph]:
    """Replace just the Tool domain, retaining unrelated records/dependencies."""

    if (type(update) is not VerifiedToolUpdate
            or update.expected_state_ref != previous.state_ref
            or update.frame.scope != envelope.scope_hint
            or update.frame.frame_id != previous.frame_id
            or update.bundle_sha256 != envelope.payload_inline["bundle_sha256"]
            or not update.tool_world.has_valid_sha256()):
        # Frame identity is frame_id + scope, not the revision hash: the
        # revision embeds time, so a new publication on the SAME workspace
        # stream keeps its frame_id (commit changes do not fork the stream)
        # while the revision legitimately advances with the envelope time.
        raise ValueError("TOOL_PUBLICATION_VERIFIED_INPUT_MISMATCH")
    cut = _bump(cut, TOOL_BUNDLE_WATERMARK, update.bundle_sha256)
    cut = _bump(cut, TOOL_WORLD_WATERMARK, update.tool_world.snapshot_sha256)
    frame = SoftwareWorldFrame.build(
        scope=update.frame.scope, workspace=update.frame.workspace,
        repository=update.frame.repository, worktree=update.frame.worktree,
        branch=update.frame.branch, commit=update.frame.commit,
        environment=update.frame.environment, time=envelope.source_time,
        world_cut=cut,
    )
    graph = SparseWorldGraph(frame)
    tool_entities = {e.entity_id for e in previous.entities
                     if e.entity_type == "ToolCapability"}
    old_tool_relations = {r.relation_id for r in previous.relations
                          if r.subject_ref.record_id in tool_entities
                          and r.predicate.startswith("tool.")}
    for entity in previous.entities:
        if entity.entity_id not in tool_entities:
            graph.upsert_entity(entity)
    for relation in previous.relations:
        if relation.relation_id not in old_tool_relations:
            graph.upsert_relation(relation)
    contribution = compile_tool_capability_contribution(
        frame, cut, update.tool_world,
        previous_entities={e.entity_id: e for e in previous.entities},
        previous_relations={r.relation_id: r for r in previous.relations},
    )
    dependencies = tuple(
        b for b in previous.dependencies.bindings
        if not (b.ref.record_type == "world_entity" and b.ref.record_id in tool_entities)
        and not (b.ref.record_type == "world_relation" and b.ref.record_id in old_tool_relations)
    )
    data = MaterializationInput(
        frame=frame, cut=cut, graph=graph, dependency_bindings=dependencies,
        changed_source_keys=update.changed_source_keys,
        preserve_previous_domains=True,
        source_transaction_id=envelope.envelope_id,
        materialized_at_ms=envelope.source_time.recorded_at_ms,
    )
    unified = bind_domain_contributions(data, (contribution,))
    state = materializer.materialize(unified)
    return state, frame, unified.graph


__all__ = [
    "TOOL_BUNDLE_WATERMARK",
    "TOOL_PUBLICATION_SCHEMA",
    "TOOL_WORLD_WATERMARK",
    "ToolRevisionResolver",
    "VerifiedToolUpdate",
    "compute_tool_changed_source_keys",
    "materialize_tool_update",
    "previous_tool_world_keys",
]
