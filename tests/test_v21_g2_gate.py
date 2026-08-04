"""G2 gate evidence: stimulus inbox, bounded lanes, unified cognition shadow.

Wired into ``scripts/run_v21_gate.py`` for T03b_identity_runtime,
T17_no_IO_lock and T24_performance.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from contracts import canonical_sha256
from contracts.life import LifeAuthorityHead, RunLifeBinding
from life_service.cognition import (
    MAX_FOREGROUND_STREAK,
    CognitionTrigger,
    UnifiedCognitionShadow,
)
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.store import LifeShadowStore, LifeShadowStoreError


H = "a" * 64


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "life.shadow.sqlite3"
        with LifeShadowStore.open(path, create=True, now_ms=1) as instance:
            yield instance


def make_head(life: str, *, revision: int = 1) -> LifeAuthorityHead:
    return LifeAuthorityHead(
        life_id=life, writer_epoch=1, identity_revision=revision,
        identity_sha256=H, soul_revision=revision, soul_sha256=H,
        affect_revision=revision, affect_sha256=H, deletion_epoch=0,
        head_sha256="0" * 64,
    ).with_computed_head_sha256()


def make_binding(life: str, *, head_sha256: str, binding_id: str, event_id: str) -> RunLifeBinding:
    subject_sha256 = canonical_sha256({"subject": event_id})
    return RunLifeBinding(
        binding_id=binding_id, life_id=life, binding_subject_kind="internal_stimulus",
        binding_subject_id=event_id, binding_subject_sha256=subject_sha256,
        life_authority_head_sha256=head_sha256, writer_epoch=1,
        identity_revision=1, identity_sha256=H, soul_revision=1, soul_sha256=H,
        affect_revision=1, affect_sha256=H, deletion_epoch=0, bound_at_ms=1,
        binding_source="test", binding_sha256="0" * 64,
    ).with_computed_binding_sha256()


def trigger(event_id: str, lane: str, *, priority: int = 10, coalesce: bool = False) -> CognitionTrigger:
    return CognitionTrigger(
        event_id=event_id,
        lane=lane,
        base_priority=priority,
        payload_sha256=canonical_sha256({"event": event_id}),
        coalesce=coalesce,
    )


def test_stimulus_inbox_dedupe_coalesce_selection_and_claim(store) -> None:
    life = "life_inbox"
    assert store.enqueue_stimulus(life, "lev_" + "1" * 64, lane="foreground", base_priority=10, payload_sha256=H, enqueued_at_ms=1)
    assert not store.enqueue_stimulus(life, "lev_" + "1" * 64, lane="foreground", base_priority=10, payload_sha256=H, enqueued_at_ms=2)
    assert store.enqueue_stimulus(life, "lev_" + "2" * 64, lane="foreground", base_priority=10, payload_sha256=H, enqueued_at_ms=3)
    assert store.enqueue_stimulus(life, "lev_" + "3" * 64, lane="background", base_priority=10, payload_sha256=H, enqueued_at_ms=4)
    assert not store.enqueue_stimulus(life, "lev_" + "4" * 64, lane="background", base_priority=10, payload_sha256=H, enqueued_at_ms=5, coalesce=True)

    first = store.select_next_stimulus(life, claim_token="c1", now_ms=10, max_foreground_streak=8)
    assert first is not None and first["lane"] == "foreground" and first["event_id"] == "lev_" + "1" * 64
    with pytest.raises(LifeShadowStoreError, match="claim"):
        store.commit_stimulus(life, enqueue_seq=int(first["enqueue_seq"]), claim_token="wrong", now_ms=11)
    assert store.commit_stimulus(life, enqueue_seq=int(first["enqueue_seq"]), claim_token="c1", now_ms=11)
    assert not store.commit_stimulus(life, enqueue_seq=int(first["enqueue_seq"]), claim_token="c1", now_ms=12)
    second = store.select_next_stimulus(life, claim_token="c2", now_ms=20, max_foreground_streak=8)
    assert second is not None and second["event_id"] == "lev_" + "2" * 64
    assert store.release_stimulus(enqueue_seq=int(second["enqueue_seq"]), claim_token="c2")
    again = store.select_next_stimulus(life, claim_token="c3", now_ms=30, max_foreground_streak=8)
    assert again is not None and again["event_id"] == "lev_" + "2" * 64
    assert store.commit_stimulus(life, enqueue_seq=int(again["enqueue_seq"]), claim_token="c3", now_ms=31)
    third = store.select_next_stimulus(life, claim_token="c4", now_ms=40, max_foreground_streak=8)
    assert third is not None and third["lane"] == "background" and third["event_id"] == "lev_" + "3" * 64


def test_background_anti_starvation_after_max_foreground_streak(store) -> None:
    life = "life_streak"
    background_id = "lev_" + "b" * 64
    assert store.enqueue_stimulus(life, background_id, lane="background", base_priority=10, payload_sha256=H, enqueued_at_ms=1)
    foreground_ids = []
    for index in range(MAX_FOREGROUND_STREAK + 2):
        event_id = "lev_" + f"{index:064x}"
        foreground_ids.append(event_id)
        assert store.enqueue_stimulus(life, event_id, lane="foreground", base_priority=10, payload_sha256=H, enqueued_at_ms=2 + index)
    selected_lanes = []
    for step in range(MAX_FOREGROUND_STREAK + 1):
        item = store.select_next_stimulus(life, claim_token=f"c{step}", now_ms=100 + step, max_foreground_streak=MAX_FOREGROUND_STREAK)
        assert item is not None
        selected_lanes.append(item["lane"])
        store.commit_stimulus(life, enqueue_seq=int(item["enqueue_seq"]), claim_token=f"c{step}", now_ms=200 + step)
    assert selected_lanes == ["foreground"] * MAX_FOREGROUND_STREAK + ["background"]
    assert store.cognition_health(life)["foreground_streak"] == 0


def test_lane_leases_foreground_preempts_background(store) -> None:
    life = "life_lanes"
    foreground = store.acquire_lane(life, "foreground", owner_instance_id="owner_a", now_ms=1, duration_ms=1000)
    assert foreground is not None
    assert store.acquire_lane(life, "foreground", owner_instance_id="owner_b", now_ms=2, duration_ms=1000) is None
    background = store.acquire_lane(life, "background", owner_instance_id="owner_b", now_ms=4, duration_ms=1000)
    assert background is not None
    assert store.acquire_lane(life, "background", owner_instance_id="owner_b", now_ms=5, duration_ms=1000) is None
    assert store.release_lane(life, "foreground", lease_id=foreground)
    preempting = store.acquire_lane(life, "foreground", owner_instance_id="owner_c", now_ms=5, duration_ms=1000)
    assert preempting is not None and preempting != background
    assert not store.release_lane(life, "background", lease_id=background)
    assert store.renew_lane(life, "foreground", lease_id=preempting, now_ms=7, duration_ms=2000)
    assert store.release_lane(life, "foreground", lease_id=preempting)
    assert store.cognition_health(life)["active_lanes"] == 0


def test_model_attempt_shadow_is_idempotent_and_rejects_rebind(store) -> None:
    payload = H
    args = dict(
        attempt_shadow_id="mas_" + "1" * 64, life_id="life_shadow", root_experience_id="root_1",
        episode_id="lev_" + "2" * 64, lane="foreground", slot_no=1, provider="test", model="g2",
        request_sha256=H, status="succeeded", finish_reason="stop", output_text_sha256=H,
        started_at_ms=1, completed_at_ms=2, payload_sha256=payload,
    )
    assert store.put_model_attempt_shadow(**args)
    assert not store.put_model_attempt_shadow(**args)
    with pytest.raises(LifeShadowStoreError, match="rebound"):
        store.put_model_attempt_shadow(**{**args, "payload_sha256": "0" * 64})


def test_t03b_stale_subjective_commit_never_crosses_life_boundaries(store) -> None:
    head_a1 = make_head("life_a")
    head_b1 = make_head("life_b")
    assert store.put_life_authority_head(head_a1, expected_head_sha256=None)
    assert store.put_life_authority_head(head_b1, expected_head_sha256=None)
    assert store.put_run_life_binding(make_binding("life_a", head_sha256=head_a1.head_sha256, binding_id="bind_a", event_id="lev_" + "a" * 64))
    head_b2 = make_head("life_b", revision=2)
    assert store.put_life_authority_head(head_b2, expected_head_sha256=head_b1.head_sha256)
    head_a2 = make_head("life_a", revision=2)
    with pytest.raises(LifeShadowStoreError, match="CAS is stale"):
        store.put_life_authority_head(head_a2, expected_head_sha256=head_b1.head_sha256)
    assert store.put_life_authority_head(head_a2, expected_head_sha256=head_a1.head_sha256)
    head_b3 = make_head("life_b", revision=3)
    assert store.put_life_authority_head(head_b3, expected_head_sha256=head_b2.head_sha256)
    head_b4 = make_head("life_b", revision=4)
    with pytest.raises(LifeShadowStoreError, match="CAS is stale"):
        store.put_life_authority_head(head_b4, expected_head_sha256=head_b1.head_sha256)
    with pytest.raises(LifeShadowStoreError, match="authority head"):
        store.put_run_life_binding(make_binding("life_b", head_sha256=head_a2.head_sha256, binding_id="bind_b", event_id="lev_" + "b" * 64))


def test_t17_cognition_decider_runs_outside_life_writer_lock() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = EmbeddedLifeRuntime(data_root=root / "data", runtime_root=root / "runtime")
        try:
            probe: dict[str, bool] = {}

            def decider(context):
                probe["lock_free"] = not runtime._lock._is_owned()
                return {
                    "provider": "test", "model": "g2", "output_text": "ok",
                    "finish_reason": "stop",
                }

            runtime.set_cognition_decider(decider)
            life_id = runtime._active()["life_id"]
            event_id = "lev_" + canonical_sha256({"test": "t17"})
            assert runtime._cognition_shadow is not None
            assert runtime._cognition_shadow.enqueue(
                life_id,
                trigger(event_id, "foreground", priority=100),
            )
            result = runtime.run_cognition_shadow_pass(life_id)
            assert result["processed"] is True
            assert probe["lock_free"] is True
            health = runtime._contract_store().cognition_health(life_id)
            assert health["pending"] == 0
            assert health["selected"] == 0
        finally:
            runtime.close()


def test_t24_queue_drains_within_budget_and_stays_bounded() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "life.shadow.sqlite3"
        with LifeShadowStore.open(path, create=True, now_ms=1) as store:
            shadow = UnifiedCognitionShadow(
                store,
                cognition_decider=lambda context: {
                    "provider": "test", "model": "g2", "output_text": "ok",
                    "finish_reason": "stop",
                },
            )
            life = "life_perf"
            for index in range(120):
                event_id = "lev_" + f"{index:064x}"
                lane = "foreground" if index % 2 == 0 else "background"
                assert shadow.enqueue(life, trigger(event_id, lane, priority=10))
            started = time.perf_counter()
            drained = shadow.run_drain(life, owner_instance_id="perf", max_items=1000)
            elapsed_ms = (time.perf_counter() - started) * 1000
            assert drained["processed"] == 120
            assert drained["health"]["pending"] == 0
            assert drained["health"]["selected"] == 0
            per_pass_ms = elapsed_ms / 120
            assert per_pass_ms < 500, f"per-pass latency {per_pass_ms:.1f}ms exceeds 500ms budget"
            again = shadow.run_drain(life, owner_instance_id="perf", max_items=1000)
            assert again["processed"] == 0
            attempt_rows = int(store._connection.execute(
                "SELECT count(*) FROM model_attempt_shadow"
            ).fetchone()[0])
            inbox_committed = int(store._connection.execute(
                "SELECT count(*) FROM stimulus_inbox WHERE status='committed'"
            ).fetchone()[0])
            assert attempt_rows == 120
            assert inbox_committed == 120
