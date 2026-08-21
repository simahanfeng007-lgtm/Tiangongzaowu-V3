from __future__ import annotations

import threading
import time
import types
from pathlib import Path

from life_service.autonomous_tasks import DEFAULT_ACTIVITY_TYPES
from life_service.embedded_runtime import EmbeddedLifeRuntime


def _runtime(root: Path) -> EmbeddedLifeRuntime:
    return EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "life-runtime",
        mode="embedded",
    )


def _request(life: EmbeddedLifeRuntime, method: str, path: str, payload=None) -> dict:
    status, body, _ = life.request(method, path, payload)
    assert status == 200, (path, status, body)
    return body


def _seed_context(life: EmbeddedLifeRuntime) -> None:
    _request(
        life,
        "POST",
        "/api/v1/v3/life/memory/assert",
        {
            "memory_id": "mem_activity_context",
            "content": {"text": "用户希望生命体做有意义、可验证且不过度打扰的事。"},
            "confidence_milli": 900,
        },
    )


def test_activity_catalog_is_selectable_daily_and_idempotent(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    try:
        assert set(life._scope_state()["settings"]["autonomy_activity_types"]) == set(
            DEFAULT_ACTIVITY_TYPES
        )
        _request(
            life,
            "POST",
            "/api/v1/v3/life/settings",
            {"settings": {"autonomy_activity_types": ["daily_planning", "system_health"]}},
        )
        _seed_context(life)
        first = _request(
            life,
            "POST",
            "/api/v1/v3/life/autonomy/tick",
            {"reason": "catalog-test"},
        )
        catalog_tasks = [
            item for item in first["tasks"]
            if item.get("source") == "life_activity_catalog"
        ]
        assert {item["activity_id"] for item in catalog_tasks} == {
            "daily_planning",
            "system_health",
        }
        task_ids = {item["task_id"] for item in catalog_tasks}

        second = _request(
            life,
            "POST",
            "/api/v1/v3/life/autonomy/tick",
            {"reason": "catalog-repeat"},
        )
        assert {
            item["task_id"] for item in second["tasks"]
            if item.get("source") == "life_activity_catalog"
        } == task_ids

        _request(
            life,
            "POST",
            "/api/v1/v3/life/settings",
            {"settings": {"autonomy_activity_types": ["system_health"]}},
        )
        _request(
            life,
            "POST",
            "/api/v1/v3/life/autonomy/tick",
            {"reason": "catalog-selection-change"},
        )
        panel = _request(life, "GET", "/api/v1/v3/life/panel")
        scheduled_catalog = [
            item for item in panel["schedule"]["tasks"]
            if item.get("source") == "life_activity_catalog"
        ]
        assert [item["activity_id"] for item in scheduled_catalog] == ["system_health"]
        daily = next(
            item for item in panel["tasks"]
            if item.get("activity_id") == "daily_planning"
        )
        assert daily["status"] == "cancelled"
    finally:
        life.close()


def test_model_internal_activity_creates_truthful_autonomous_record(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    called = threading.Event()
    try:
        _request(
            life,
            "POST",
            "/api/v1/v3/life/settings",
            {"settings": {"autonomy_activity_types": ["daily_planning"]}},
        )
        _seed_context(life)

        def decide(scope: dict, task: dict) -> dict:
            assert scope["life_id"] == task["life_id"]
            assert task["activity_id"] == "daily_planning"
            called.set()
            return {
                "title": "今日规划",
                "summary": "已完成内部规划，优先推进一个可验证的小步骤。",
                "findings": ["先核对当前状态"],
                "next_steps": ["在统一权限边界内推进"],
                "uncertainties": [],
            }

        life.set_autonomy_decider(decide)
        _request(
            life,
            "POST",
            "/api/v1/v3/life/autonomy/tick",
            {"reason": "model-internal-test"},
        )
        assert called.wait(2)
        deadline = time.monotonic() + 2
        latest = {}
        while time.monotonic() < deadline:
            panel = _request(life, "GET", "/api/v1/v3/life/panel")
            latest = panel["free_will"]["latest_autonomous_action"]
            if latest.get("status") == "completed":
                break
            time.sleep(0.02)
        assert latest["activity_id"] == "daily_planning"
        assert latest["result"]["external_side_effects"] is False
        assert latest["result"]["execution_scope"] == "internal_life_state"
        assert "内部规划" in latest["result"]["summary"]
    finally:
        life.close()


def _drift_task(
    task_id: str,
    activity_id: str,
    *,
    priority: int,
    sequence: int,
    status: str = "pending",
) -> dict:
    return {
        "task_id": task_id,
        "source": "life_activity_catalog",
        "activity_id": activity_id,
        "task_kind": activity_id,
        "title": activity_id,
        "objective": "漂移排序测试",
        "status": status,
        "risk_class": "A0",
        "priority": priority,
        "sequence": sequence,
        "time_window": "上午",
        "created_at_ms": 1_000 + sequence,
        "updated_at_ms": 1_000 + sequence,
    }


def _drift_history(count: int, activity_id: str, *, prefix: str = "done", start_ms: int = 10_000) -> dict:
    return {
        f"{prefix}-{index}": {
            **_drift_task(
                f"{prefix}-{index}",
                activity_id,
                priority=500,
                sequence=index,
                status="completed",
            ),
            "updated_at_ms": start_ms + index,
        }
        for index in range(count)
    }


def test_drift_affinity_boosts_undersampled_activity(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    try:
        scope = {
            "settings": {"autonomy_activity_types": ["learning_review", "system_health"]},
            "autonomy": {"enabled": True, "tasks": _drift_history(30, "system_health")},
        }
        affinity = life._drift_affinity(scope)
        # 偏好里排前面的 learning_review 被 30 次 system_health 欠采样，应获得加分；
        # 已被过度采样的 system_health 不加。
        assert affinity.get("learning_review", 0) > 0
        assert "system_health" not in affinity
    finally:
        life.close()


def test_drift_affinity_requires_drift_and_enough_samples(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    try:
        def scope_with(tasks: dict) -> dict:
            return {
                "settings": {"autonomy_activity_types": ["learning_review", "system_health"]},
                "autonomy": {"enabled": True, "tasks": tasks},
            }

        # 无历史（insufficient_evidence）与样本 <10（分布抖动大）都不加分
        assert life._drift_affinity(scope_with({})) == {}
        assert life._drift_affinity(scope_with(_drift_history(9, "system_health"))) == {}
        # 近 30 次分布与偏好一致（stable）不加分
        mixed = _drift_history(15, "learning_review", prefix="done-a")
        mixed.update(_drift_history(15, "system_health", prefix="done-b", start_ms=50_000))
        assert life._drift_affinity(scope_with(mixed)) == {}
    finally:
        life.close()


def test_drift_reorders_free_time_pool_but_not_due_tasks(tmp_path: Path, monkeypatch) -> None:
    life = _runtime(tmp_path)
    selected: list[dict] = []
    called = threading.Event()
    try:
        scope = life._scope_state()
        scope["settings"]["autonomy_activity_types"] = ["learning_review", "system_health"]
        scope["affect"] = {"emotions": {}}
        autonomy = life._autonomy_state()
        autonomy["enabled"] = True
        tasks = _drift_history(30, "system_health")
        tasks["pending-high"] = _drift_task("pending-high", "system_health", priority=760, sequence=101)
        tasks["pending-low"] = _drift_task("pending-low", "learning_review", priority=740, sequence=102)
        autonomy["tasks"] = tasks

        def decide(scope_view: dict, task: dict) -> dict:
            selected.append({
                "task_id": task["task_id"],
                "drift_status": (
                    scope_view.get("motivation_drift") or [{}])[0].get("status"),
            })
            called.set()
            return {
                "title": "漂移测试",
                "summary": "已完成内部复盘，无需外部副作用。",
                "findings": [],
                "next_steps": [],
                "uncertainties": [],
            }

        life.set_autonomy_decider(decide)
        life_id = str(life._active()["life_id"])

        def run_decision(hour: int) -> str:
            called.clear()
            monkeypatch.setattr(
                time,
                "localtime",
                lambda secs: types.SimpleNamespace(tm_hour=hour),
            )
            life._schedule_autonomous_activity_decision(life_id=life_id)
            assert called.wait(2), f"decider not called at hour {hour}"
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                scheduler = life._scope_state(life_id).get("scheduler") or {}
                states = life._autonomy_state(life_id)["tasks"]
                finished = (
                    not bool(scheduler.get("autonomy_decision_inflight"))
                    and str(states[selected[-1]["task_id"]].get("status") or "") == "completed"
                )
                if finished:
                    break
                time.sleep(0.02)
            return selected[-1]["task_id"]

        # 凌晨 3 点："上午"窗口（6-11）关闭 → 自由行动分支。偏好排名更高的
        # learning_review 被 30 次 system_health 欠采样，漂移加分把它顶过 760 优先级。
        assert run_decision(3) == "pending-low"
        assert selected[0]["drift_status"] == "drift"

        # 上午 8 点：窗口打开 → 到点必做分支，漂移不参与排序，高优先级先做。
        life._scope_state(life_id).setdefault("scheduler", {})["last_autonomy_decision_at_ms"] = 0
        life._autonomy_state(life_id)["tasks"]["pending-low-2"] = _drift_task(
            "pending-low-2", "learning_review", priority=740, sequence=103,
        )
        assert run_decision(8) == "pending-high"
    finally:
        life.close()
