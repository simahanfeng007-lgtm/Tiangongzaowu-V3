from __future__ import annotations

import json
import time
from pathlib import Path

from life_service.autonomous_tasks import (
    materialize_tasks,
    reap_stale_pending_tasks,
    update_task_status,
)
from life_service.embedded_runtime import EmbeddedLifeRuntime


def _candidate(kind: str, *, source: str | None = None, fingerprint: str | None = None) -> dict:
    fp = (fingerprint or f"{kind}-{'f' * 63}")[:64]
    item = {
        "task_kind": kind,
        "objective": "objective",
        "proposed_action": "proposed_action",
        "subject_refs": ["mem_x"],
        "causal_basis": ["basis"],
        "priority": 1,
        "risk_class": "A0",
        "requires_user": False,
        "fingerprint": fp,
    }
    if source is not None:
        item["source"] = source
    return item


def _task(task_id: str, *, created_ms: int, status: str = "pending", attempt_count: int = 0) -> dict:
    return {
        "schema": "tiangong.life.autonomy-task-engine.v1",
        "task_id": task_id,
        "life_id": "org_test",
        "task_kind": "identify_root_cause",
        "objective": "x",
        "proposed_action": "x",
        "subject_refs": ["mem_x"],
        "causal_basis": ["incoming_cause_missing"],
        "priority": 1900,
        "risk_class": "A0",
        "requires_user": False,
        "fingerprint": f"{task_id}-" + "f" * 40,
        "status": status,
        "generation_reason": "scheduled",
        "sequence": 1,
        "created_at_ms": created_ms,
        "updated_at_ms": created_ms,
        "attempt_count": attempt_count,
        "source": None,
    }


def test_reap_cancels_only_stale_never_attempted_pending() -> None:
    now_ms = 10_000_000_000
    old_ms = now_ms - 4 * 24 * 3600 * 1000
    state = {"tasks": {
        "t_stale": _task("t_stale", created_ms=old_ms),
        "t_recent": _task("t_recent", created_ms=now_ms - 3600_000),
        "t_attempted": _task("t_attempted", created_ms=old_ms, attempt_count=1),
        "t_completed": _task("t_completed", created_ms=old_ms, status="completed"),
        "t_running": _task("t_running", created_ms=old_ms, status="running"),
    }}
    reaped = reap_stale_pending_tasks(state, now_ms=now_ms)
    assert [item["task_id"] for item in reaped] == ["t_stale"]
    assert state["tasks"]["t_stale"]["status"] == "cancelled"
    assert state["tasks"]["t_stale"]["result"]["reason_code"] == "life.autonomy.stale_pending_reaped"
    assert state["tasks"]["t_recent"]["status"] == "pending"
    assert state["tasks"]["t_attempted"]["status"] == "pending"
    assert state["tasks"]["t_completed"]["status"] == "completed"
    assert state["tasks"]["t_running"]["status"] == "running"


def test_cognition_backlog_cannot_starve_catalog_generation() -> None:
    state = {"tasks": {}, "pending_limit": 64}
    # 填满认知类独立池（16 个活跃非目录任务）
    for index in range(16):
        task = materialize_tasks(
            state,
            [_candidate(f"cog_{index}", source=None, fingerprint=f"cogfp{index:02d}".ljust(64, "c"))],
            life_id="org_test",
            reason="fill",
            now_ms=1000,
        )[0]
        update_task_status(state, task_id=task["task_id"], status="running", now_ms=1000)
    # 目录类候选（每日计划）仍然可以生成
    created = materialize_tasks(
        state,
        [_candidate("life_activity.daily_planning", source="life_activity_catalog")],
        life_id="org_test",
        reason="catalog",
        now_ms=2000,
    )
    assert len(created) == 1
    assert created[0]["task_kind"] == "life_activity.daily_planning"


def test_cognition_cap_limits_non_catalog_tasks() -> None:
    state = {"tasks": {}, "pending_limit": 200}
    candidates = [
        _candidate(f"cog_{index}", source=None, fingerprint=f"cfp{index:02d}".ljust(64, "c"))
        for index in range(20)
    ]
    created = materialize_tasks(state, candidates, life_id="org_test", reason="many", now_ms=1000)
    assert len(created) == 16


def test_pending_limit_zero_blocks_all_generation() -> None:
    state = {"tasks": {}, "pending_limit": 0}
    created = materialize_tasks(
        state,
        [_candidate("life_activity.daily_planning", source="life_activity_catalog")],
        life_id="org_test",
        reason="zero",
        now_ms=1000,
    )
    assert created == []


def _runtime(root: Path) -> EmbeddedLifeRuntime:
    return EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "life-runtime",
        mode="embedded",
    )


def test_full_stale_pool_reaped_and_daily_plan_regenerated(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    life_id = str(life._active()["life_id"])
    scope = life._scope_state()
    scope["memories"]["mem_active_1"] = {
        "memory_id": "mem_active_1",
        "content": "用户上下文",
        "status": "active",
        "epistemic_status": "user_asserted",
        "confidence_milli": 900,
        "priority": 1000,
        "classification": {"causal_role": "context"},
    }
    scope["settings"]["autonomy_activity_types"] = [
        "daily_planning",
        "self_reflection",
        "goal_progress",
    ]
    now_ms = time.time_ns() // 1_000_000
    stale_ms = now_ms - 6 * 24 * 3600 * 1000
    autonomy = life._autonomy_state()
    autonomy["tasks"] = {
        f"lat_{index:064d}": _task(
            f"lat_{index:064d}",
            created_ms=stale_ms,
            status="pending",
            attempt_count=0,
        )
        for index in range(1, 65)
    }
    autonomy["pending_limit"] = 64
    life._persist()

    status, value, _ = life.request("POST", "/api/v1/v3/life/autonomy/tick", {"reason": "test"})
    assert status == 200, (status, value)

    state = life._autonomy_state()
    cancelled = [
        task for task in state["tasks"].values()
        if task.get("status") == "cancelled"
    ]
    active = [
        task for task in state["tasks"].values()
        if task.get("status") in {"pending", "running", "blocked", "awaiting_user"}
    ]
    assert len(cancelled) == 64
    kinds = {task.get("task_kind") for task in active}
    assert "life_activity.daily_planning" in kinds
    journal_text = life.system.journal._path(life_id).read_text(encoding="utf-8")
    assert "autonomy.task.stale-reap:" in journal_text
