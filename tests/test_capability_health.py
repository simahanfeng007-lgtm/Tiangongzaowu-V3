"""能力健康状态机回归测试（源码版，从隔离实验移植）。"""

from __future__ import annotations

from life_service.capability_health import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_PATCH_ROUNDS,
    canonical_sha256,
    degrade_pointer,
    ingest_outcome,
    propose_patch,
    reactivate_pointer,
    runtime_usable,
    settle_patch,
)


def make_pointer(**overrides):
    pointer = {
        "schema": "tiangong.life.capability-pointer.v1",
        "life_id": "life_exp_1",
        "lineage_id": "lineage_v1",
        "kind": "skill",
        "status": "active",
        "current_artifact_id": "art_v1",
        "current_artifact_sha256": "a" * 64,
        "history": [],
        "pointer_sha256": "",
        "health": {
            "schema": "tiangong.life.capability-health.v1",
            "uses": 0,
            "successes": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "patch_rounds": 0,
            "patch_pending": None,
            "patch_history": [],
            "seen_outcome_ids": [],
            "last_outcome_at_ms": 0,
            "reactivated_at_ms": None,
            "created_at_ms": 0,
        },
    }
    pointer.update(overrides)
    return pointer


def make_outcome(outcome_id: str, artifact_id: str = "art_v1", outcome: str = "failure") -> dict:
    return {
        "outcome_id": outcome_id,
        "artifact_id": artifact_id,
        "outcome": outcome,
        "occurred_at_ms": 1000,
    }


def make_patched(artifact_id: str = "art_v2") -> dict:
    return {"artifact_id": artifact_id, "artifact_sha256": canonical_sha256({"id": artifact_id})}


def test_outcome_accounting_and_version_isolation():
    pointer = make_pointer()
    updated, action, reason = ingest_outcome(pointer, make_outcome("o1"), now_ms=2000)
    assert (action, reason) == ("none", "recorded")
    assert updated["health"]["uses"] == 1
    assert updated["health"]["failures"] == 1
    assert updated["health"]["consecutive_failures"] == 1
    updated2, action2, reason2 = ingest_outcome(
        updated, make_outcome("o2", artifact_id="art_old"), now_ms=2001
    )
    assert (action2, reason2) == ("none", "stale_version")
    assert updated2["health"]["uses"] == 1


def test_duplicate_outcome_is_idempotent():
    pointer = make_pointer()
    first, _, _ = ingest_outcome(pointer, make_outcome("dup1"), now_ms=2000)
    second, action, reason = ingest_outcome(first, make_outcome("dup1"), now_ms=2001)
    assert (action, reason) == ("none", "duplicate")
    assert second["health"]["uses"] == 1


def test_success_resets_consecutive_failures():
    pointer = make_pointer()
    for index in range(2):
        pointer, _, _ = ingest_outcome(pointer, make_outcome(f"f{index}"), now_ms=2000 + index)
    pointer, _, _ = ingest_outcome(pointer, make_outcome("s1", outcome="success"), now_ms=2002)
    assert pointer["health"]["consecutive_failures"] == 0
    assert pointer["health"]["successes"] == 1


def test_consecutive_failures_request_patch():
    pointer = make_pointer()
    action = "none"
    for index in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
        pointer, action, _ = ingest_outcome(pointer, make_outcome(f"f{index}"), now_ms=2000 + index)
    assert action == "request_patch"
    assert pointer["health"]["consecutive_failures"] == DEFAULT_MAX_CONSECUTIVE_FAILURES


def test_patch_propose_then_apply_switches_pointer():
    pointer = make_pointer()
    for index in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
        pointer, _, _ = ingest_outcome(pointer, make_outcome(f"f{index}"), now_ms=2000 + index)
    pending = propose_patch(pointer, make_patched(), now_ms=3000)
    assert pending["health"]["patch_pending"]["to_artifact_id"] == "art_v2"
    assert pending["health"]["patch_rounds"] == 1
    assert pending["current_artifact_id"] == "art_v1"
    settled, applied, reason = settle_patch(
        pending, {"passed": True, "evidence_sha256": "ev1"}, now_ms=4000
    )
    assert applied is True
    assert reason == "applied"
    assert settled["current_artifact_id"] == "art_v2"
    assert settled["health"]["consecutive_failures"] == 0
    assert settled["health"]["patch_pending"] is None
    assert settled["health"]["patch_history"][0]["result"] == "applied"


def test_patch_failure_rolls_back_and_keeps_old_version():
    pointer = make_pointer()
    for index in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
        pointer, _, _ = ingest_outcome(pointer, make_outcome(f"f{index}"), now_ms=2000 + index)
    pending = propose_patch(pointer, make_patched(), now_ms=3000)
    settled, applied, reason = settle_patch(
        pending, {"passed": False, "evidence_sha256": "ev_bad"}, now_ms=4000
    )
    assert applied is False
    assert reason == "rolled_back"
    assert settled["current_artifact_id"] == "art_v1"
    assert settled["health"]["patch_history"][0]["result"] == "rolled_back"
    again = propose_patch(settled, make_patched("art_v3"), now_ms=5000)
    assert again["health"]["patch_rounds"] == 2


def test_patch_rounds_exhausted_degrades():
    pointer = make_pointer()
    for index in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
        pointer, _, _ = ingest_outcome(pointer, make_outcome(f"f{index}"), now_ms=2000 + index)
    for round_index in range(DEFAULT_MAX_PATCH_ROUNDS):
        pending = propose_patch(pointer, make_patched(f"art_v{round_index + 2}"), now_ms=3000 + round_index)
        pointer, applied, reason = settle_patch(
            pending, {"passed": False, "evidence_sha256": f"ev_bad{round_index}"}, now_ms=4000 + round_index
        )
        assert applied is False
    assert reason == "degraded"
    assert pointer["status"] == "degraded"
    assert runtime_usable(pointer) is False
    assert "patch_rounds_exhausted" in pointer["degraded_reason"]


def test_degrade_from_active_keeps_history():
    degraded = degrade_pointer(make_pointer(), reason="manual_test", now_ms=5000)
    assert degraded["status"] == "degraded"
    assert degraded["current_artifact_id"] == "art_v1"
    assert degraded["health"]["patch_history"] == []


def test_reactivate_requires_user_and_resets_loop():
    pointer = make_pointer(status="degraded")
    try:
        reactivate_pointer(pointer, actor="life_scheduler", now_ms=6000)
        raise AssertionError("scheduler must not reactivate")
    except ValueError:
        pass
    reactivated = reactivate_pointer(pointer, actor="user", now_ms=6000)
    assert reactivated["status"] == "active"
    assert runtime_usable(reactivated) is True
    assert reactivated["health"]["consecutive_failures"] == 0
    assert reactivated["health"]["patch_rounds"] == 0
    assert reactivated["health"]["reactivated_at_ms"] == 6000


def test_invalid_state_transitions_rejected():
    pending_pointer = make_pointer(status="pending")
    _, action, reason = ingest_outcome(pending_pointer, make_outcome("x"), now_ms=1000)
    assert (action, reason) == ("none", "not_active")
    for call in (
        lambda: propose_patch(pending_pointer, make_patched(), now_ms=1000),
        lambda: settle_patch(make_pointer(), {"passed": True}, now_ms=1000),
        lambda: reactivate_pointer(make_pointer(status="active"), actor="user", now_ms=1000),
        lambda: degrade_pointer(make_pointer(status="disabled"), reason="x", now_ms=1000),
    ):
        try:
            call()
            raise AssertionError("invalid transition must raise ValueError")
        except ValueError:
            pass


def test_patch_round_limit_is_parameterized_not_hardcoded():
    """回归：补丁轮次上限必须参数化，否则 max_patch_rounds=3 时永不降级。"""
    pointer = make_pointer()
    for index in range(DEFAULT_MAX_CONSECUTIVE_FAILURES):
        pointer, _, _ = ingest_outcome(pointer, make_outcome(f"f{index}"), now_ms=2000 + index)
    # 3 轮上限：ingest 在 rounds<3 时仍应允许继续触发补丁。
    for round_index in range(2):
        pending = propose_patch(pointer, make_patched(f"art_v{round_index + 2}"), now_ms=3000 + round_index, max_patch_rounds=3)
        pointer, applied, _ = settle_patch(
            pending, {"passed": False, "evidence_sha256": f"ev{round_index}"},
            now_ms=4000 + round_index, max_patch_rounds=3,
        )
        assert applied is False
    # 第三次失败后仍允许发起第 3 轮补丁（轮次未用尽）。
    pointer, action, _ = ingest_outcome(
        pointer, make_outcome("f_last"), now_ms=5000, max_patch_rounds=3
    )
    assert action == "request_patch"
    pending = propose_patch(pointer, make_patched("art_v4"), now_ms=6000, max_patch_rounds=3)
    pointer, applied, reason = settle_patch(
        pending, {"passed": False, "evidence_sha256": "ev_last"},
        now_ms=7000, max_patch_rounds=3,
    )
    assert applied is False
    assert reason == "degraded"


# ---------- F5：正向强化（streak / 最近成功）与综合健康分 ----------


def test_success_streak_accumulates_resets_and_records_last_success():
    pointer = make_pointer()
    pointer, _, _ = ingest_outcome(
        pointer, {**make_outcome("s1", outcome="success"), "occurred_at_ms": 1000}, now_ms=1000
    )
    assert pointer["health"]["success_streak"] == 1
    assert pointer["health"]["last_success_at_ms"] == 1000
    pointer, _, _ = ingest_outcome(
        pointer, {**make_outcome("s2", outcome="success"), "occurred_at_ms": 2000}, now_ms=2000
    )
    assert pointer["health"]["success_streak"] == 2
    assert pointer["health"]["last_success_at_ms"] == 2000
    pointer, _, _ = ingest_outcome(
        pointer, {**make_outcome("f1"), "occurred_at_ms": 3000}, now_ms=3000
    )
    assert pointer["health"]["success_streak"] == 0
    # 失败不清除最近成功时间：那是历史事实，供健康分新鲜度使用。
    assert pointer["health"]["last_success_at_ms"] == 2000


def test_reactivate_resets_success_streak():
    pointer = make_pointer()
    for index in range(3):
        pointer, _, _ = ingest_outcome(
            pointer,
            {**make_outcome(f"s{index}", outcome="success"), "occurred_at_ms": 1000 + index},
            now_ms=1000 + index,
        )
    pointer = degrade_pointer(pointer, reason="test", now_ms=5000)
    pointer = reactivate_pointer(pointer, actor="user", now_ms=6000)
    assert pointer["health"]["success_streak"] == 0


def test_health_score_milli_neutral_without_evidence_and_rises_with_successes():
    from life_service.capability_health import health_score_milli

    # 无任何执行结果：中性 500。
    assert health_score_milli({"uses": 0, "successes": 0}, now_ms=10_000) == 500
    fresh = {
        "uses": 20,
        "successes": 20,
        "success_streak": 20,
        "last_outcome_at_ms": 9_000,
    }
    high = health_score_milli(fresh, now_ms=10_000)
    assert high > 600
    # 失败为主且近期：低于中性。
    bad = {
        "uses": 5,
        "successes": 0,
        "success_streak": 0,
        "last_outcome_at_ms": 9_000,
    }
    assert health_score_milli(bad, now_ms=10_000) < 500


def test_health_score_milli_decays_toward_neutral_when_idle():
    from life_service.capability_health import health_score_milli

    health = {
        "uses": 20,
        "successes": 20,
        "success_streak": 20,
        "last_outcome_at_ms": 0,
    }
    day = 86_400_000
    now_ms = 100 * day
    fresh = dict(health, last_outcome_at_ms=now_ms - 1 * day)
    week_idle = dict(health, last_outcome_at_ms=now_ms - 8 * day)
    month_idle = dict(health, last_outcome_at_ms=now_ms - 30 * day)
    score_fresh = health_score_milli(fresh, now_ms)
    score_week = health_score_milli(week_idle, now_ms)
    score_month = health_score_milli(month_idle, now_ms)
    # 7 天半衰：闲置越久越向中性收缩，但历史成功者仍略高于完全未知。
    assert score_fresh > score_week > score_month
    assert 500 < score_month < score_week < score_fresh
