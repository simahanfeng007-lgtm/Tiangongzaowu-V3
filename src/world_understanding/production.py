"""Production composition over the one World Understanding ingress.

This module owns no listener, worker, scheduler, Gateway, Runtime, or tool path.
It synchronously consumes already-committed source envelopes after the existing
compiler boundary and publishes one coherent P9 WorldState transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import re
from threading import RLock
from typing import Callable, Protocol

from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding._base import WorldRecordRef
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
from .world_state.retention import RetainedWorldState
from .world_state.manifests import DependencyBinding
from .skill_method_world.publication import (
    PUBLICATION_SCHEMA, ARCHIVE_WATERMARK, MethodRevisionResolver,
    materialize_method_update, method_marker,
)
from .tool_capability_world.publication import (
    TOOL_BUNDLE_WATERMARK, TOOL_PUBLICATION_SCHEMA, ToolRevisionResolver,
    materialize_tool_update,
)


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
        method_revision_resolver: MethodRevisionResolver | None = None,
        domain_provider: Callable | None = None,
        semantic_source_kinds: frozenset[str] | None = None,
        semantic_trace_observer: Callable | None = None,
        cognition_provider: Callable | None = None,
    ) -> None:
        self.store = store
        self.frame_factory = frame_factory
        self._lock = RLock()
        self._streams: dict[str, _StreamState] = {}
        self._closure = KnownClosureEngine(RuleRegistry(build_p4_rules()))
        self._updater = SoftwareWorldUpdater()
        self._semantic = semantic_pipeline or SemanticPipeline(model=None)
        self._last_semantic_trace = None
        self._materializer = WorldStateMaterializer(store)
        self._committed_state_observer = committed_state_observer
        self._method_revision_resolver = method_revision_resolver
        self._tool_revision_resolver = None
        self._domain_provider = domain_provider
        self._semantic_source_kinds = semantic_source_kinds
        self._semantic_trace_observer = semantic_trace_observer
        self._cognition_provider = cognition_provider
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
                and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", live.frame.commit)
                and any(entity.entity_type == "Repository" for entity in live.graph.entities())
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

    def install_method_revision_resolver(self, resolver: MethodRevisionResolver) -> None:
        """Operator-only composition seam; never an envelope or model field."""
        if not callable(resolver) or not callable(getattr(resolver, "load", None)):
            raise TypeError("METHOD_PUBLICATION_RESOLVER_INVALID")
        with self._lock:
            if self._method_revision_resolver is not None and self._method_revision_resolver is not resolver:
                raise ValueError("METHOD_PUBLICATION_RESOLVER_ALREADY_CONFIGURED")
            self._method_revision_resolver = resolver

    def install_tool_revision_resolver(self, resolver: ToolRevisionResolver) -> None:
        """Operator-only composition seam; never an envelope or model field."""
        if not callable(resolver):
            raise TypeError("TOOL_PUBLICATION_RESOLVER_INVALID")
        with self._lock:
            if getattr(self, "_tool_revision_resolver", None) is not None and self._tool_revision_resolver is not resolver:
                raise ValueError("TOOL_PUBLICATION_RESOLVER_ALREADY_CONFIGURED")
            self._tool_revision_resolver = resolver

    def method_world_for_state(
        self, state_ref: WorldRecordRef, *, scope: WorldScope,
        retention_owner: str | None = None, expected_method_source_refs: tuple | None = None,
    ):
        """Read an exact current/historical source binding; no latest fallback.

        Callers use the WorldState ref already bound to their running plan.
        Evicted/unavailable states fail closed instead of switching that plan.
        """
        # No live runtime graph is read here. Store-owned admission can hold
        # Gateway -> WorldStore, but must never acquire the runtime lock:
        # World publication observers may already hold Runtime -> Gateway.
        # The resolver is install-once and cannot be replaced after publication.
        with self.store.retention_transaction():
            if type(state_ref) is not WorldRecordRef:
                raise ValueError("METHOD_SOURCE_PINNED_WORLD_UNAVAILABLE")
            import re
            if re.fullmatch(r"wst_[0-9a-f]{64}", state_ref.record_id) is None:
                raise ValueError("METHOD_SOURCE_PINNED_WORLD_UNAVAILABLE")
            snapshot = self.store.get(state_ref.record_id)
            if (state_ref.record_type != "world_state" or snapshot is None
                    or snapshot.state_ref != state_ref or snapshot.state.scope != scope
                    or not snapshot.state.has_valid_hash()):
                raise ValueError("METHOD_SOURCE_PINNED_WORLD_UNAVAILABLE")
            if self._method_revision_resolver is None:
                raise ValueError("METHOD_PUBLICATION_NOT_CONFIGURED")
            methods = self._method_revision_resolver.load(snapshot)
            if expected_method_source_refs is not None:
                refs=expected_method_source_refs
                by_id={p.method_id:p.source_ref for p in methods.primitives}
                if (type(refs) is not tuple or not refs
                        or len({r.semantic_id for r in refs})!=len(refs)
                        or any(by_id.get(r.semantic_id)!=r for r in refs)):
                    raise ValueError("METHOD_SOURCE_PLAN_REVISION_MISMATCH")
            if retention_owner is not None:
                if expected_method_source_refs is None:
                    raise ValueError("METHOD_SOURCE_RETENTION_REQUIRES_PLAN_REFS")
                self.store.retain_state(RetainedWorldState(retention_owner, state_ref, scope))
            return methods

    def _consume_tool_publication(self, envelope: WorldIngressEnvelope) -> SourceMaterializationDisposition:
        payload = envelope.payload_inline
        if (envelope.source_kind != "SYSTEM_GOVERNANCE" or type(payload) is not dict
                or set(payload) != {"schema", "bundle_sha256", "frame_id"}
                or payload["schema"] != TOOL_PUBLICATION_SCHEMA
                or type(payload["frame_id"]) is not str):
            raise ValueError("TOOL_PUBLICATION_ENVELOPE_INVALID")
        with self._lock:
            resolver = self._tool_revision_resolver
            if resolver is None:
                raise ValueError("TOOL_PUBLICATION_NOT_CONFIGURED")
            scope = envelope.scope_hint
            previous = self.store.current(life_id=scope.life_id, world_scope_hash=scope.world_scope_hash,
                                          principal_scope_hash=scope.principal_scope_hash, frame_id=payload["frame_id"])
            if previous is None:
                raise ValueError("TOOL_PUBLICATION_REQUIRES_EXISTING_FRAME")
            with self.store.publication_transaction(previous):
                from .skill_method_world.publication import method_marker
                if method_marker(previous, TOOL_BUNDLE_WATERMARK) == payload["bundle_sha256"]:
                    return SourceMaterializationDisposition("TOOL_REVISION_ALREADY_MATERIALIZED", True, previous.state.world_state_id)
                update = resolver(envelope, previous)
                snapshot, frame, graph = materialize_tool_update(
                    self._materializer, envelope, previous, update, self._next_cut(envelope, previous))
                live = self._streams.get(frame.frame_id)
                self._streams[frame.frame_id] = _StreamState(frame, graph, None if live is None else live.closure)
            if self._committed_state_observer is not None:
                try:
                    self._committed_state_observer(envelope, snapshot)
                except Exception:
                    pass
            return SourceMaterializationDisposition("TOOL_REVISION_MATERIALIZED", True, snapshot.state.world_state_id)

    def _consume_method_publication(self, envelope: WorldIngressEnvelope) -> SourceMaterializationDisposition:
        payload = envelope.payload_inline
        if (envelope.source_kind != "SYSTEM_GOVERNANCE" or type(payload) is not dict
                or set(payload) != {"schema", "archive_sha256", "frame_id"}
                or type(payload["frame_id"]) is not str):
            raise ValueError("METHOD_PUBLICATION_ENVELOPE_INVALID")
        with self._lock:
            resolver = self._method_revision_resolver
            if resolver is None:
                raise ValueError("METHOD_PUBLICATION_NOT_CONFIGURED")
            scope = envelope.scope_hint
            previous = self.store.current(life_id=scope.life_id, world_scope_hash=scope.world_scope_hash,
                                          principal_scope_hash=scope.principal_scope_hash, frame_id=payload["frame_id"])
            if previous is None:
                raise ValueError("METHOD_PUBLICATION_REQUIRES_EXISTING_FRAME")
            with self.store.publication_transaction(previous):
                if method_marker(previous, ARCHIVE_WATERMARK) == payload["archive_sha256"]:
                    resolver.load(previous)  # Recheck retained bytes/signatures after restart.
                    return SourceMaterializationDisposition("METHOD_REVISION_ALREADY_MATERIALIZED", True, previous.state.world_state_id)
                update = resolver(envelope, previous)
                snapshot, frame, graph = materialize_method_update(
                    self._materializer, envelope, previous, update, self._next_cut(envelope, previous))
                live = self._streams.get(frame.frame_id)
                self._streams[frame.frame_id] = _StreamState(frame, graph, None if live is None else live.closure)
            if self._committed_state_observer is not None:
                try:
                    self._committed_state_observer(envelope, snapshot)
                except Exception:
                    pass
            return SourceMaterializationDisposition("METHOD_REVISION_MATERIALIZED", True, snapshot.state.world_state_id)

    def _prepare_update(self, envelope, rows):
        """Caller holds the short runtime lock; no model/network work here."""
        previous = self._previous(self.frame_factory(envelope, None))
        cut = self._next_cut(envelope, previous)
        frame = self.frame_factory(envelope, cut)
        if frame.scope != envelope.scope_hint:
            raise ValueError("WORLD_PRODUCTION_FRAME_IDENTITY_MISMATCH")
        live = self._streams.get(frame.frame_id)
        closure = self._closure.close(rows, prior=None if live is None else live.closure)
        graph = _fork_graph(frame, previous if live is None else live.graph)
        git_delta = repository_observation_to_git_delta(envelope=envelope, frame=frame, rows=rows) if envelope.source_kind == "GIT_CODE" else None
        added_hashes = set(closure.added_record_hashes)
        update = self._updater.update(frame=frame, graph=graph,
            known_delta=tuple(r for r in closure.known.records() if r.record_hash in added_hashes), git_delta=git_delta)
        return previous, cut, frame, closure, update

    def consume_source(
        self,
        envelope: WorldIngressEnvelope,
        rows: tuple[DirectKnownRecord, ...],
    ) -> SourceMaterializationDisposition:
        if isinstance(envelope.payload_inline, dict) and envelope.payload_inline.get("schema") == PUBLICATION_SCHEMA:
            return self._consume_method_publication(envelope)
        if isinstance(envelope.payload_inline, dict) and envelope.payload_inline.get("schema") == TOOL_PUBLICATION_SCHEMA:
            return self._consume_tool_publication(envelope)
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
            previous, cut, frame, closure, update = self._prepare_update(envelope, rows)
            if frame.frame_id != probe.frame_id:
                raise ValueError("WORLD_PRODUCTION_FRAME_IDENTITY_MISMATCH")
            data = MaterializationInput(frame=frame, cut=cut, graph=update.graph,
                dependency_bindings=() if previous is None else previous.dependencies.bindings,
                preserve_previous_hypotheses=True, preserve_previous_cognition=True,
                preserve_previous_domains=previous is not None and method_marker(previous, ARCHIVE_WATERMARK) is not None,
                source_transaction_id=envelope.envelope_id, materialized_at_ms=envelope.source_time.recorded_at_ms)
            if self._domain_provider is not None:
                try:
                    data = self._domain_provider(data, previous)
                except Exception as exc:
                    logging.getLogger("tiangong.world").warning("WORLD_DICTIONARY_PROJECTION_FAILED type=%s", type(exc).__name__)
            if self._cognition_provider is not None:
                try:
                    data = self._cognition_provider(data, previous, envelope)
                except Exception as exc:
                    logging.getLogger("tiangong.world").warning("WORLD_COGNITION_PROJECTION_FAILED type=%s", type(exc).__name__)
                    data = replace(data, stable_cognition=(), replace_previous_cognition=True)
            current_keys = {WorldRecordRef(record_type="world_entity", record_id=e.entity_id, revision=e.revision, sha256=e.entity_sha256).sort_key() for e in data.graph.entities()}
            current_keys.update(WorldRecordRef(record_type="world_relation", record_id=r.relation_id, revision=r.revision, sha256=r.relation_sha256).sort_key() for r in data.graph.relations())
            data = replace(data, dependency_bindings=tuple(b for b in data.dependency_bindings
                if b.ref.record_type not in {"world_entity", "world_relation"} or b.ref.sort_key() in current_keys))
            # Native facts become durable BEFORE optional model inference. Late
            # model replies cannot replay an old fact over a newer tool outcome.
            snapshot = self._materializer.materialize(data)
            self._streams[frame.frame_id] = _StreamState(frame, data.graph, closure)
        self._notify_commit(envelope, snapshot)
        # A slow provider must not hold either the Runtime or WorldState lock.
        # Recheck the exact basis after inference; concurrent facts take precedence.
        semantic = None
        if self._semantic_source_kinds is None or envelope.source_kind in self._semantic_source_kinds:
            try:
                semantic_input = build_semantic_input(
                    scope=frame.scope, known_records=tuple(rows), graph=update.graph,
                    seed_entity_ids=update.touched_entity_ids)
                semantic = self._semantic.run(semantic_input,
                    factors=SemanticFactors(novelty_milli=1000, life_relevance_milli=1000),
                    expected_gap_reduction_milli=1000, expected_cost_milli=1,
                    created_at_ms=envelope.source_time.recorded_at_ms)
            except Exception as exc:
                logging.getLogger("tiangong.world_semantic").warning("WORLD_SEMANTIC_INPUT_FAILED type=%s", type(exc).__name__)
        with self._lock:
            current = self._previous(frame)
            if current is None or current.state_ref != snapshot.state_ref:
                if semantic is not None:
                    semantic = replace(semantic, status="SUPERSEDED", hypotheses=(),
                        trace=replace(semantic.trace, status="SUPERSEDED", admission_reason_code="SEMANTIC_BASIS_CHANGED", hypothesis_refs=()))
            if semantic is not None:
                self._last_semantic_trace = semantic.trace
            hypotheses = () if semantic is None else semantic.hypotheses
            dependencies = [b for b in data.dependency_bindings if b.ref.record_type != "world_hypothesis"]
            # Conservatively invalidate interpretations when any observed input
            # source advances. An inference is never promoted to an empirical fact.
            keys = tuple(sorted(w.source_kind + ":" + w.watermark_type for w in cut.source_watermarks))
            for hyp in hypotheses:
                ref = WorldRecordRef(record_type="world_hypothesis", record_id=hyp.hypothesis_id, sha256=hyp.hypothesis_sha256)
                dependencies.append(DependencyBinding(ref=ref, source_keys=keys))
            logging.getLogger("tiangong.world_semantic").info(
                "world_semantic status=%s reason=%s model=%s hypotheses=%d latency_ms=%d",
                "SOURCE_NOT_SEMANTIC" if semantic is None else semantic.status,
                None if semantic is None else semantic.trace.admission_reason_code,
                None if semantic is None else semantic.trace.model_ref,
                len(hypotheses), 0 if semantic is None else semantic.trace.latency_ms,
            )
            refined = semantic is not None and semantic.status == "COMPLETED"
            if refined:
                refinement = replace(data, active_hypotheses=hypotheses, dependency_bindings=tuple(dependencies),
                    preserve_previous_hypotheses=False, preserve_previous_domains=False,
                    source_transaction_id="semantic." + semantic.trace.trace_sha256)
                snapshot = self._materializer.materialize(refinement)
        if semantic is not None and self._semantic_trace_observer is not None:
            try:
                self._semantic_trace_observer(envelope, semantic.trace)
            except Exception as exc:
                logging.getLogger("tiangong.world_semantic").warning("WORLD_SEMANTIC_DIAGNOSTIC_FAILED type=%s", type(exc).__name__)
        if refined:
            self._notify_commit(envelope, snapshot)
        return SourceMaterializationDisposition("SOURCE_MATERIALIZED", True, snapshot.state.world_state_id)

    def _notify_commit(self, envelope, snapshot):
        if self._committed_state_observer is not None:
            try:
                self._committed_state_observer(envelope, snapshot)
            except Exception as exc:
                logging.getLogger("tiangong.world").warning("WORLD_COMMIT_OBSERVER_FAILED type=%s", type(exc).__name__)


__all__ = [
    "FrameFactory",
    "ProductionWorldUnderstandingRuntime",
    "SourceMaterializationDisposition",
]
