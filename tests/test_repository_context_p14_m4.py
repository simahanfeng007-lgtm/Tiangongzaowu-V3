from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.query import WorldQuery, derive_world_query_id
from world_understanding.context_output.enrichment import ContextProjectionCandidate
from world_understanding.context_output.handler import WorldContextRequestHandler
from world_understanding.context_output.projection import WorldContextProjector
from world_understanding.context_output.repository import build_repository_context_candidates
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.software_world.entity import EntitySeed, build_entity
from world_understanding.world_state import WorldStateStore

from tests.test_repository_query_p14_m3 import _basis, _entity, _graph, _relation


def _frame_ref(frame, *, sha: str | None = None) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="software_world_frame",
        record_id=frame.frame_id,
        revision=None,
        sha256=frame.frame_revision_hash if sha is None else sha,
    )


def _query(frame, focus: str, *, token_budget: int = 20_000) -> WorldQuery:
    task_sha = canonical_sha256({"task": focus})
    correlation_id = "corr.m4"
    task_ref = "task.m4"
    created_at_ms = 2000
    query_id = derive_world_query_id(
        world_scope_hash=frame.scope.world_scope_hash,
        correlation_id=correlation_id,
        task_ref=task_ref,
        task_sha256=task_sha,
        focus=focus,
        created_at_ms=created_at_ms,
    )
    return WorldQuery(
        query_id=query_id,
        correlation_id=correlation_id,
        scope=frame.scope,
        frame_ref=_frame_ref(frame),
        basis_world_state_ref=None,
        task_ref=task_ref,
        task_sha256=task_sha,
        focus=focus,
        token_budget=token_budget,
        requested_depth="L0",
        created_at_ms=created_at_ms,
        query_sha256="0" * 64,
    ).with_computed_hash()


def _snapshot(frame, graph):
    state_ref = WorldRecordRef(
        record_type="world_state",
        record_id="wst.m4",
        revision=1,
        sha256="7" * 64,
    )
    cut_ref = WorldRecordRef(
        record_type="world_cut",
        record_id="wcut.m4",
        revision=None,
        sha256="8" * 64,
    )
    entity_refs = tuple(
        WorldRecordRef(
            record_type="world_entity",
            record_id=entity.entity_id,
            revision=entity.revision,
            sha256=entity.entity_sha256,
        )
        for entity in graph.entities()
    )
    relation_refs = tuple(
        WorldRecordRef(
            record_type="world_relation",
            record_id=relation.relation_id,
            revision=relation.revision,
            sha256=relation.relation_sha256,
        )
        for relation in graph.relations()
    )
    state = SimpleNamespace(
        scope=frame.scope,
        frame_ref=_frame_ref(frame),
        world_sequence=0,
        stale_refs=(),
        unresolved_conflict_refs=(),
        world_cut_ref=cut_ref,
    )
    delta = SimpleNamespace(
        changed_source_keys=(),
        added_refs=(),
        removed_refs=(),
        changed_refs=(),
        ref=WorldRecordRef(
            record_type="world_delta",
            record_id="delta.m4",
            revision=None,
            sha256="9" * 64,
        ),
    )
    return SimpleNamespace(
        state=state,
        state_ref=state_ref,
        cut=SimpleNamespace(cut_id="wcut.m4"),
        entity_heads=SimpleNamespace(refs=entity_refs),
        relation_heads=SimpleNamespace(refs=relation_refs),
        cognition_heads=None,
        active_hypotheses=None,
        uncertainty=None,
        dependencies=SimpleNamespace(bindings=()),
        delta=delta,
    )


def _bound_query(frame, snapshot, focus: str) -> WorldQuery:
    return _query(frame, focus).model_copy(update={
        "basis_world_state_ref": snapshot.state_ref,
        "query_sha256": "0" * 64,
    }).with_computed_hash()


def test_m4_repository_focus_returns_committed_entity_and_neighbors() -> None:
    frame, graph, module_a, module_b, function_b, test_a, unrelated = _graph()
    query = _query(frame, "inspect pkg.b.changed impact")
    candidates = build_repository_context_candidates(graph, query)
    by_id = {candidate.ref.record_id: candidate for candidate in candidates}
    assert function_b.entity_id in by_id
    assert by_id[function_b.entity_id].item_kind == "repository_focus"
    assert module_b.entity_id in by_id
    assert unrelated.entity_id not in by_id
    assert any(candidate.item_kind == "repository_relation" for candidate in candidates)


def test_m4_total_part_ancestors_precede_horizontal_expansion() -> None:
    frame, graph, *_ = _graph()
    repository = _entity(frame, "Repository", "tree.repo", "local-repository:repo.m3")
    src = _entity(frame, "RepositoryBranch", "tree.src", "repository-branch:repo.m3:src")
    core = _entity(frame, "RepositoryBranch", "tree.core", "repository-branch:repo.m3:src/core")
    file = build_entity(
        frame,
        EntitySeed(
            entity_type="File",
            stable_anchor="tree.file",
            canonical_name="src/core/unique_tree_file.py",
            basis_ref=_basis("tree.file"),
            time=frame.time,
            truth_state="TRUE",
            epistemic_state="CURRENT",
            aliases=("unique_tree_file.py",),
        ),
    )
    for entity in (repository, src, core, file):
        graph.upsert_entity(entity)
    for relation in (
        _relation(frame, repository, "CONTAINS", src),
        _relation(frame, src, "CONTAINS", core),
        _relation(frame, core, "CONTAINS", file),
    ):
        graph.upsert_relation(relation)

    candidates = build_repository_context_candidates(
        graph, _query(frame, "inspect unique_tree_file.py")
    )
    by_id = {item.ref.record_id: item for item in candidates}
    assert by_id[file.entity_id].item_kind == "repository_focus"
    assert by_id[core.entity_id].item_kind == "repository_tree_ancestor"
    assert by_id[src.entity_id].item_kind == "repository_tree_ancestor"
    assert by_id[repository.entity_id].item_kind == "repository_tree_ancestor"


def test_m4_seed_discovery_never_scans_all_graph_entities() -> None:
    frame, graph, _, _, function_b, *_ = _graph()
    query = _query(frame, "inspect pkg.b.changed")
    with patch.object(type(graph), "entities", side_effect=AssertionError("whole graph scan forbidden")):
        candidates = build_repository_context_candidates(graph, query)
    assert any(candidate.ref.record_id == function_b.entity_id for candidate in candidates)


def test_m4_ambiguous_focus_token_never_guesses_identity() -> None:
    frame, graph, *_ = _graph()
    first = _entity(frame, "Function", "m4.dup.1", "pkg.duplicate")
    second = _entity(frame, "Function", "m4.dup.2", "pkg.duplicate")
    graph.upsert_entity(first)
    graph.upsert_entity(second)
    assert build_repository_context_candidates(graph, _query(frame, "pkg.duplicate")) == ()


def test_m4_token_index_fanout_is_hard_bounded() -> None:
    frame, graph, *_ = _graph()
    first = _entity(frame, "Function", "m4.fanout.1", "pkg.fanout")
    second = _entity(frame, "Function", "m4.fanout.2", "pkg.fanout")
    graph.upsert_entity(first)
    graph.upsert_entity(second)
    with pytest.raises(ValueError, match="TOKEN_MATCH_LIMIT_EXCEEDED"):
        graph.resolve_token_bounded("pkg.fanout", max_matches=1)


def test_m4_projector_reuses_existing_world_context_packet_and_summary_override() -> None:
    frame, graph, _, _, function_b, *_ = _graph()
    snapshot = _snapshot(frame, graph)
    query = _bound_query(frame, snapshot, "pkg.b.changed")
    candidates = build_repository_context_candidates(graph, query)
    result = WorldContextProjector(token_estimator=lambda text: max(1, len(text) // 8)).project(
        query,
        snapshot,
        enrichment_candidates=candidates,
    )
    packet = result.packet
    assert packet.__class__.__name__ == "WorldContextPacket"
    assert packet.context_only is True
    assert packet.authorizes is False
    assert packet.may_execute is False
    focused = [
        item for item in packet.ranked_items
        if any(ref.record_id == function_b.entity_id for ref in item.referenced_world_records)
    ]
    assert focused
    assert focused[0].item_kind == "repository_focus"
    assert "pkg.b.changed" in focused[0].summary
    assert result.estimated_tokens <= query.token_budget


def test_m4_enriched_and_plain_packet_identities_cannot_collide() -> None:
    frame, graph, *_ = _graph()
    snapshot = _snapshot(frame, graph)
    query = _bound_query(frame, snapshot, "pkg.b.changed")
    candidates = build_repository_context_candidates(graph, query)
    projector = WorldContextProjector(token_estimator=lambda text: max(1, len(text) // 8))
    enriched = projector.project(query, snapshot, enrichment_candidates=candidates).packet
    plain = projector.project(query, snapshot, enrichment_candidates=()).packet
    assert enriched.packet_id != plain.packet_id
    assert enriched.projection_policy_sha256 != plain.projection_policy_sha256
    assert enriched.projection_policy_ref.endswith("repository-p14-m4")
    assert plain.projection_policy_ref == "policy.world-context.p10.v1"


def test_m4_projector_rejects_enrichment_ref_outside_snapshot() -> None:
    frame, graph, *_ = _graph()
    snapshot = _snapshot(frame, graph)
    query = _bound_query(frame, snapshot, "pkg.a")
    alien = WorldRecordRef(
        record_type="world_entity",
        record_id="went_" + "a" * 64,
        revision=1,
        sha256="b" * 64,
    )
    candidate = ContextProjectionCandidate(
        ref=alien,
        item_kind="repository_focus",
        summary="must not enter packet",
    )
    with pytest.raises(ValueError, match="ENRICHMENT_REF_OUTSIDE_SNAPSHOT"):
        WorldContextProjector().project(query, snapshot, enrichment_candidates=(candidate,))


def test_m4_runtime_rejects_stale_frame_revision_enrichment() -> None:
    frame, graph, *_ = _graph()
    snapshot = _snapshot(frame, graph)
    query = _query(frame, "pkg.a")
    runtime = ProductionWorldUnderstandingRuntime(
        store=WorldStateStore(root=None),
        frame_factory=lambda envelope, cut: frame,
    )
    runtime._streams[frame.frame_id] = SimpleNamespace(graph=graph, closure=None)
    assert runtime.repository_context_candidates(query, snapshot)

    stale_snapshot = _snapshot(frame, graph)
    stale_snapshot.state.frame_ref = _frame_ref(frame, sha="f" * 64)
    assert runtime.repository_context_candidates(query, stale_snapshot) == ()


def test_m4_runtime_without_live_stream_falls_back_to_plain_context() -> None:
    frame, graph, *_ = _graph()
    snapshot = _snapshot(frame, graph)
    query = _query(frame, "pkg.a")
    runtime = ProductionWorldUnderstandingRuntime(
        store=WorldStateStore(root=None),
        frame_factory=lambda envelope, cut: frame,
    )
    assert runtime.repository_context_candidates(query, snapshot) == ()


def test_m4_handler_enricher_failure_is_fail_open() -> None:
    frame, graph, *_ = _graph()
    snapshot = _snapshot(frame, graph)
    query = _bound_query(frame, snapshot, "plain world context")

    emitted = []
    output = SimpleNamespace(emit=lambda q, packet: emitted.append((q, packet)))

    def broken_enricher(_query, _snapshot):
        raise RuntimeError("optional enrichment unavailable")

    handler = WorldContextRequestHandler(
        state_resolver=lambda _query: snapshot,
        projector=WorldContextProjector(token_estimator=lambda text: max(1, len(text) // 8)),
        output_port=output,
        projection_enricher=broken_enricher,
    )
    with patch("world_understanding.context_output.handler.compile_world_query", return_value=query):
        disposition = handler(object())
    assert disposition.processed is True
    assert disposition.reason_code == "CONTEXT_PACKET_EMITTED"
    assert len(emitted) == 1
    assert emitted[0][1].__class__.__name__ == "WorldContextPacket"


def test_m4_no_symbol_focus_produces_no_repository_enrichment() -> None:
    frame, graph, *_ = _graph()
    query = _query(frame, "请继续处理当前工作")
    assert build_repository_context_candidates(graph, query) == ()
