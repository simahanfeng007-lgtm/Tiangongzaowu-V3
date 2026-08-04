from __future__ import annotations

import threading
import time
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
