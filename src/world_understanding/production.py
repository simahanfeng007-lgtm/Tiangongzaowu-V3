"""Production composition over the one World Understanding ingress.

This module owns no listener, worker, scheduler, Gateway, Runtime, or tool path.
It synchronously consumes already-committed source envelopes after the existing
compiler boundary and publishes one coherent P9 WorldState transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Protocol

from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.known import DirectKnownRecord
from contracts.world_understanding.query import WorldQuery
from contracts.world_understanding.repository_query import (
    RepositoryGraphQuery,
    RepositoryGraphQueryResult,
)
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.world_cut import SourceWatermark, WorldCut, derive_world_cut_id

from .context_output.enrichment import ContextProjectionCandidate
from .context_output.repository import build_repository_context_candidates
from .facade import WorldUnderstandingFacade
from .known import KnownClosureEngine, RuleRegistry, build_p4_rules
from .known.closure import ClosureResult
from .semantic import SemanticFactors, SemanticPipeline, build_semantic_input
from .software_world import SoftwareWorldFrame, SoftwareWorldUpdater, SparseWorldGraph
from .software_world.git_observation import repository_observation_to_git_delta
from .software_world.query import execute_repository_graph_query
from .world_state import MaterializationInput, WorldStateMaterializer, WorldStateStore
from .world_state.store import MaterializedWorldSnapshot


class FrameFactory(Protocol):
    def __call__(self, envelope: WorldIngressEnvelope, cut: WorldCut | None) -> SoftwareWorldFrame: ...


@dataclass(frozen=True, slots=True)
class SourceMaterializationDisposition:
    reason_code: str
    processed: bool
    world_state_id: str | None = None


@dataclass(slots=True)
class _StreamState:
    frame: SoftwareWorldFrame
    graph: SparseWorldGraph
    closure: ClosureResult | None


def _fork_graph(
    frame: SoftwareWorldFrame,
    previous: SparseWorldGraph | MaterializedWorldSnapshot | None,
) -> SparseWorldGraph:
    graph = SparseWorldGraph(frame)
    if previous is None:
        return graph
    for entity in previous.entities if isinstance(previous, MaterializedWorldSnapshot) else previous.entities():
        graph.upsert_entity(entity)
    for relation in previous.relations if isinstance(previous, MaterializedWorldSnapshot) else previous.relations():
        graph.upsert_relation(relation)
    if isinstance(previous, SparseWorldGraph):
        for delta_id in previous.applied_git_delta_ids():
            graph.mark_git_delta(delta_id)
    return graph


class ProductionWorldUnderstandingRuntime:
    """One synchronous compiler-to-WorldState composition.

    Publication is the commit point. Candidate closure/graph state is built on
    forks and becomes live only after ``WorldStateStore.publish`` succeeds.
    Repository graph queries and context enrichment are read-only projections
    over that committed live stream, never a second WorldState or Runtime.
    """

    def __init__(
        self,
        *,
        store: WorldStateStore,
        frame_factory: FrameFactory,
        context_request_handler: Callable[[WorldIngressEnvelope], object] | None = None,
        semantic_pipeline: SemanticPipeline | None = None,
        committed_state_observer: Callable[[WorldIngressEnvelope, MaterializedWorldSnapshot], object] | None = None,
    ) -> None:
        self.store = store
        self.frame_factory = frame_factory
        self._lock = RLock()
        self._streams: dict[str, _StreamState] = {}
        self._closure = KnownClosureEngine(RuleRegistry(build_p4_rules()))
        self._updater = SoftwareWorldUpdater()
        self._semantic = semantic_pipeline or SemanticPipeline(model=None)
        self._materializer = WorldStateMaterializer(store)
        self._committed_state_observer = committed_state_observer
        self.facade = WorldUnderstandingFacade(
            enabled=True,
            context_request_handler=context_request_handler,
            source_handler=self.consume_source,
        )

    @staticmethod
    def _next_cut(
        envelope: WorldIngressEnvelope,
        previous: MaterializedWorldSnapshot | None,
    ) -> WorldCut:
        by_key = {}
        if previous is not None:
            by_key = {
                (item.source_kind, item.watermark_type): item
                for item in previous.cut.source_watermarks
            }
        key = (envelope.source_kind, "ingress.envelope")
        old = by_key.get(key)
        if old is not None and old.watermark_value == envelope.envelope_id:
            return previous.cut
        sequence = 0 if old is None or old.sequence is None else old.sequence + 1
        by_key[key] = SourceWatermark(
            source_kind=envelope.source_kind,
            watermark_type="ingress.envelope",
            watermark_value=envelope.envelope_id,
            sequence=sequence,
            watermark_sha256="0" * 64,
        ).with_computed_hash()
        rows = tuple(sorted(by_key.values(), key=lambda item: item.sort_key()))
        cut_id = derive_world_cut_id(
            world_scope_hash=envelope.scope_hint.world_scope_hash,
            watermarks=rows,
        )
        return WorldCut(
            cut_id=cut_id,
            scope=envelope.scope_hint,
            source_watermarks=rows,
            time=envelope.source_time,
            cut_sha256="0" * 64,
        ).with_computed_hash()

    def _previous(self, frame: SoftwareWorldFrame) -> MaterializedWorldSnapshot | None:
        scope = frame.scope
        return self.store.current(
            life_id=scope.life_id,
            world_scope_hash=scope.world_scope_hash,
            principal_scope_hash=scope.principal_scope_hash,
            frame_id=frame.frame_id,
        )

    def _restore_live_stream(
        self,
        envelope: WorldIngressEnvelope,
        snapshot: MaterializedWorldSnapshot,
    ) -> _StreamState | None:
        """Rebuild the live query cache from canonical persisted WorldState.

        A duplicate source after process restart is already committed reality,
        but the in-process graph cache starts empty. Rehydration makes that
        persisted frame queryable again without creating a second authority or
        replaying the source effect.
        """

        historical_envelope = envelope.model_copy(
            update={"source_time": snapshot.cut.time}
        )
        frame = self.frame_factory(historical_envelope, snapshot.cut)
        if (
            frame.frame_id != snapshot.frame_id
            or frame.scope != snapshot.state.scope
            or frame.frame_revision_hash != snapshot.state.frame_ref.sha256
        ):
            return None
        graph = _fork_graph(frame, snapshot)
        restored = _StreamState(frame, graph, None)
        self._streams[frame.frame_id] = restored
        return restored

    def live_repository_frame(
        self,
        *,
        scope: WorldScope,
        repository: str,
        worktree: str,
        branch: str,
    ) -> SoftwareWorldFrame | None:
        """Return the exact committed live frame for one repository branch.

        This is a read-only view over the existing WU stream map.  It deliberately
        does not create a repository revision cache or resolve across branch frames.
        """
        with self._lock:
            for live in self._streams.values():
                frame = live.frame
                if (
                    frame.scope == scope
                    and frame.repository == repository
                    and frame.worktree == worktree
                    and frame.branch == branch
                ):
                    return frame
        return None

    def query_repository_graph(
        self, query: RepositoryGraphQuery
    ) -> RepositoryGraphQueryResult:
        """Run one bounded read-only query against the committed live graph."""
        with self._lock:
            live = self._streams.get(query.frame_id)
            if live is None:
                raise ValueError("REPOSITORY_QUERY_FRAME_NOT_LIVE")
            return execute_repository_graph_query(live.graph, query)

    def repository_evidence_snapshot(
        self,
        *,
        scope: WorldScope,
        max_entities: int = 32,
    ) -> dict[str, object] | None:
        """Return a bounded reference-only view of the newest exact-scope repo frame.

        This reads the already committed Software World graph.  It performs no
        filesystem/Git/parser work and exposes no source text or host paths.
        """
        if isinstance(max_entities, bool) or not isinstance(max_entities, int) or not 1 <= max_entities <= 128:
            raise ValueError("REPOSITORY_EVIDENCE_ENTITY_BUDGET_INVALID")
        with self._lock:
            candidates = [
                live
                for live in self._streams.values()
                if live.frame.scope == scope
                and any(entity.entity_type == "File" for entity in live.graph.entities())
            ]
            if not candidates:
                return None
            live = max(
                candidates,
                key=lambda item: (
                    item.frame.time.recorded_at_ms,
                    item.frame.frame_revision_hash,
                    item.frame.frame_id,
                ),
            )
            entities = sorted(
                (
                    entity
                    for entity in live.graph.entities()
                    if entity.lifecycle == "ACTIVE"
                ),
                key=lambda entity: (-entity.revision, entity.entity_id),
            )[:max_entities]
            return {
                "schema": "tiangong.life.repository-evidence.v1",
                "frame_id": live.frame.frame_id,
                "frame_revision_hash": live.frame.frame_revision_hash,
                "repository_id": live.frame.repository,
                "worktree_id": live.frame.worktree,
                "branch": live.frame.branch[:240],
                "commit": live.frame.commit,
                "observed_at_ms": live.frame.time.recorded_at_ms,
                "entity_refs": [
                    {
                        "record_id": entity.entity_id,
                        "revision": entity.revision,
                        "sha256": entity.entity_sha256,
                    }
                    for entity in entities
                ],
            }

    def repository_context_candidates(
        self,
        query: WorldQuery,
        snapshot: MaterializedWorldSnapshot,
    ) -> tuple[ContextProjectionCandidate, ...]:
        """Enrich only when the requested snapshot is the exact live frame revision.

        A historical WorldState, a process restart before the frame becomes live,
        or any frame revision mismatch returns no enrichment. The ordinary P10
        packet projection remains authoritative and available in every case.
        """
        with self._lock:
            frame_ref = snapshot.state.frame_ref
            if query.scope != snapshot.state.scope:
                return ()
            if query.frame_ref is not None and query.frame_ref != frame_ref:
                return ()
            live = self._streams.get(frame_ref.record_id)
            if live is None:
                return ()
            if live.graph.scope != snapshot.state.scope:
                return ()
            if (
                live.graph.frame_id != frame_ref.record_id
                or live.graph.frame_revision_hash != frame_ref.sha256
            ):
                return ()
            return build_repository_context_candidates(live.graph, query)

    def consume_source(
        self,
        envelope: WorldIngressEnvelope,
        rows: tuple[DirectKnownRecord, ...],
    ) -> SourceMaterializationDisposition:
        if not rows:
            return SourceMaterializationDisposition("SOURCE_EMPTY", True, None)
        with self._lock:
            probe = self.frame_factory(envelope, None)
            previous = self._previous(probe)
            if previous is not None and any(
                item.source_kind == envelope.source_kind
                and item.watermark_type == "ingress.envelope"
                and item.watermark_value == envelope.envelope_id
                for item in previous.cut.source_watermarks
            ):
                if probe.frame_id not in self._streams:
                    self._restore_live_stream(envelope, previous)
                return SourceMaterializationDisposition(
                    "SOURCE_ALREADY_MATERIALIZED", True, previous.state.world_state_id
                )
            cut = self._next_cut(envelope, previous)
            frame = self.frame_factory(envelope, cut)
            if frame.frame_id != probe.frame_id or frame.scope != envelope.scope_hint:
                raise ValueError("WORLD_PRODUCTION_FRAME_IDENTITY_MISMATCH")

            live = self._streams.get(frame.frame_id)
            prior_closure = None if live is None else live.closure
            closure = self._closure.close(rows, prior=prior_closure)
            graph = _fork_graph(frame, previous if live is None else live.graph)
            git_delta = repository_observation_to_git_delta(
                envelope=envelope,
                frame=frame,
                rows=rows,
            ) if envelope.source_kind == "GIT_CODE" else None
            added_hashes = set(closure.added_record_hashes)
            update = self._updater.update(
                frame=frame,
                graph=graph,
                known_delta=tuple(
                    item
                    for item in closure.known.records()
                    if item.record_hash in added_hashes
                ),
                git_delta=git_delta,
            )
            semantic_input = build_semantic_input(
                scope=frame.scope,
                known_records=tuple(rows),
                graph=update.graph,
                seed_entity_ids=update.touched_entity_ids,
            )
            semantic = self._semantic.run(
                semantic_input,
                factors=SemanticFactors(novelty_milli=1000, life_relevance_milli=1000),
                expected_gap_reduction_milli=1000,
                expected_cost_milli=1,
                created_at_ms=envelope.source_time.recorded_at_ms,
            )
            snapshot = self._materializer.materialize(
                MaterializationInput(
                    frame=frame,
                    cut=cut,
                    graph=update.graph,
                    active_hypotheses=semantic.hypotheses,
                    source_transaction_id=envelope.envelope_id,
                    materialized_at_ms=envelope.source_time.recorded_at_ms,
                )
            )
            self._streams[frame.frame_id] = _StreamState(frame, update.graph, closure)
            if self._committed_state_observer is not None:
                try:
                    self._committed_state_observer(envelope, snapshot)
                except Exception:
                    pass
            return SourceMaterializationDisposition(
                "SOURCE_MATERIALIZED", True, snapshot.state.world_state_id
            )


__all__ = [
    "FrameFactory",
    "ProductionWorldUnderstandingRuntime",
    "SourceMaterializationDisposition",
]
