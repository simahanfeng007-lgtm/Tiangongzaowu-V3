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
from contracts.world_understanding.world_cut import SourceWatermark, WorldCut, derive_world_cut_id

from .facade import WorldUnderstandingFacade
from .known import KnownClosureEngine, RuleRegistry, build_p4_rules
from .known.closure import ClosureResult
from .semantic import SemanticFactors, SemanticPipeline, build_semantic_input
from .software_world import SoftwareWorldFrame, SoftwareWorldUpdater, SparseWorldGraph
from .software_world.git_observation import repository_observation_to_git_delta
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
            self._streams[frame.frame_id] = _StreamState(update.graph, closure)
            # P13.2 is strictly post-publication and fail-open. It may enqueue
            # one inquiry into the existing Gateway, but cannot roll back or
            # alter the passive perception transaction that just committed.
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
