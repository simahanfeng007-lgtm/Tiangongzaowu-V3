from types import SimpleNamespace

from contracts.world_understanding.time import WorldTime
from world_understanding.cognition.facade import WorldCognitionFacade
from world_understanding.cognition.runtime import RuntimeCognitionConsolidation, observation_binding
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import WorldStateStore
from tests.test_world_understanding_p13_1_production_activation import _scope, _frame


def source(index, *, request=None, target=None, ok=True, kind="TOOL_RESULT", scope=None, autonomous=False, file_sha=None):
    payload = {"schema": "tiangong.v3.tool_result.v1", "tool_name": "omni_body", "ok": ok,
        "observation_binding": observation_binding({"action": "file.read", "target": target or f"input-{index}.txt", "args": {}}, {"ok": ok},
            None if file_sha is None else {"path": target or f"input-{index}.txt", "sha256": file_sha})}
    if autonomous:
        payload["source_inquiry_id"] = "inquiry.test"
    at = index * 1000
    return build_post_commit_source_envelope(source_kind=kind, source_native_id=f"result.{index}",
        producer_ref="v3.tool_result_contract", payload=payload,
        source_time=WorldTime(valid_from_ms=at, observed_at_ms=at, recorded_at_ms=at),
        scope=scope or _scope(), correlation_id="test", run_id=f"run.{index}", request_id=request or f"request.{index}")


def cognition(tmp_path):
    return RuntimeCognitionConsolidation(WorldCognitionFacade(enabled=True, root=tmp_path / "cognition"))


def test_independent_tasks_promote_and_restart_preserves_stability(tmp_path):
    cog = cognition(tmp_path)
    first = cog.observe(source(1))
    assert first.head.stability_level == "C1" and not cog.eligible(_scope(), 1000)
    second = cog.observe(source(2))
    assert second.head.stability_level == "C2" and second.report.support_group_count == 2
    reopened = cognition(tmp_path)
    assert len(reopened.eligible(_scope(), 2000)) == 1
    counts = reopened.facade.counts()
    reopened.observe(source(2))
    assert reopened.facade.counts() == counts


def test_duplicate_task_or_input_does_not_create_independent_votes(tmp_path):
    cog = cognition(tmp_path)
    cog.observe(source(1, request="same.request", target="a.txt"))
    row = cog.observe(source(2, request="same.request", target="b.txt"))
    assert row.head.stability_level == "C1"
    row = cog.observe(source(3, request="different.request", target="a.txt"))
    assert row.head.stability_level == "C1" and row.report.support_group_count == 1


def test_counterevidence_stops_projection_and_expiry_and_host_drift_invalidate(tmp_path, monkeypatch):
    cog = cognition(tmp_path)
    cog.observe(source(1)); cog.observe(source(2))
    assert cog.eligible(_scope(), 2000)
    assert not cog.eligible(_scope(), 8 * 24 * 3600_000)
    row = cog.observe(source(3, ok=False))
    assert row.head.status == "CHALLENGED" and not cog.eligible(_scope(), 3000)
    monkeypatch.setattr("world_understanding.cognition.runtime.execution_condition", lambda: "f"*64)
    assert not cog.eligible(_scope(), 3000)


def test_copied_file_content_cannot_supply_independent_confirmation(tmp_path):
    cog = cognition(tmp_path)
    cog.observe(source(1, target="original.txt", file_sha="a"*64))
    copied = cog.observe(source(2, target="different-folder/copy.txt", file_sha="a"*64))
    assert copied.head.stability_level == "C1" and copied.report.support_group_count == 1
    independent = cog.observe(source(3, target="new-input.txt", file_sha="b"*64))
    assert independent.head.stability_level == "C2" and independent.report.support_group_count == 2


def test_model_memory_and_self_inquiry_cannot_supply_empirical_quorum(tmp_path):
    cog = cognition(tmp_path)
    for kind in ("MODEL_OUTPUT", "MEMORY", "USER_CONVERSATION"):
        assert cog.observe(source(1, kind=kind)) is None
    assert cog.observe(source(2, autonomous=True)) is None
    assert cog.facade.counts()["evidence"] == 0


def test_production_state_contains_only_live_cognition_and_context_is_scope_bound(tmp_path):
    cog = cognition(tmp_path)
    store = WorldStateStore(root=tmp_path / "world")
    rt = ProductionWorldUnderstandingRuntime(store=store, frame_factory=_frame, cognition_provider=cog.materialize)
    for index in (1, 2):
        result = rt.facade.accept(source(index))
        assert result.processed
    frame = _frame(source(2), None)
    snapshot = rt._previous(frame)
    assert snapshot.cognition_heads and len(snapshot.cognition_heads.refs) == 1
    query = SimpleNamespace(scope=_scope(), basis_world_state_ref=snapshot.state_ref, created_at_ms=2000)
    assert len(cog.context_candidates(query, snapshot)) == 1
    query.scope = _scope().model_copy(update={"principal_scope_hash": "b"*64})
    assert not cog.context_candidates(query, snapshot)
    assert rt.facade.accept(source(3, ok=False)).processed
    assert rt._previous(frame).cognition_heads is None


def test_first_failure_cannot_become_a_verified_capability(tmp_path):
    cog = cognition(tmp_path)
    row = cog.observe(source(1, ok=False))
    assert row.head.stability_level == "C0"
    assert not cog.eligible(_scope(), 1000)
    cog.observe(source(2, ok=False))
    assert not cog.eligible(_scope(), 2000)
