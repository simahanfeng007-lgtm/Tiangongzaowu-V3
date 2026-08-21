"""F6：learning/self-iteration/capability-patch 三个调度器的子预算记账。

照 proactive 双池模式（全局池 + 子池）：预算耗尽时 decider 不被调用、
skipped 计数正确、跨 UTC 日重置、全局池与子池独立耗尽互不越界。
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime


def runtime(root: Path) -> EmbeddedLifeRuntime:
    life = EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "runtime",
        mode="embedded",
    )
    life.scheduler.stop(timeout_seconds=2)
    return life


def utc_day(offset_days: int = 0) -> str:
    moment = time.gmtime(time.time() + offset_days * 86_400)
    return time.strftime("%Y-%m-%d", moment)


def seed_pool(scheduler: dict, *, prefix: str, day: str, attempts: int, successes: int = 0) -> None:
    """把某个子池预置到指定状态；全局池默认留给调用方按需覆盖。"""
    scheduler.update({
        f"{prefix}budget_date": day,
        f"{prefix}attempts": attempts,
        f"{prefix}successes": successes,
        f"{prefix}failures": 0,
        f"{prefix}timeouts": 0,
        f"{prefix}skipped": 0,
    })


def seed_global(scheduler: dict, *, day: str, attempts: int = 0, successes: int = 0) -> None:
    scheduler.update({
        "model_budget_date": day,
        "model_attempts": attempts,
        "model_successes": successes,
        "model_failures": 0,
        "model_timeouts": 0,
        "model_skipped": 0,
    })


def wait_scheduler_idle(life: EmbeddedLifeRuntime, life_id: str, *, inflight_key: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scheduler = life._scope_state(life_id).get("scheduler") or {}
        if not bool(scheduler.get(inflight_key)):
            return
        time.sleep(0.01)
    raise AssertionError(f"scheduler still inflight: {inflight_key}")


def test_learning_sub_budget_exhausted_skips_decider_and_counts_skip():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            today = utc_day()
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            seed_global(scheduler, day=today)
            # 子池尝试数已到默认上限 8：耗尽。
            seed_pool(scheduler, prefix="learning_model_", day=today, attempts=8, successes=6)
            calls: list[dict] = []
            life.set_learning_decider(lambda _scope: calls.append({}) or {"target": "none"})
            life_id = str(life._active()["life_id"])
            life._schedule_autonomous_learning_decision(life_id=life_id)
            assert calls == []
            assert scheduler["model_skipped"] == 1
            assert scheduler["learning_model_skipped"] == 1
            assert scheduler["model_attempts"] == 0
            assert scheduler["last_learning_decision_error"] == "life.learning.model_budget_exhausted"
            assert scheduler.get("learning_decision_inflight") is not True
        finally:
            life.close()


def test_self_iteration_sub_budget_exhausted_skips_decider():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            today = utc_day()
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            seed_global(scheduler, day=today)
            seed_pool(scheduler, prefix="self_iteration_model_", day=today, attempts=6, successes=4)
            calls: list[dict] = []
            life.set_self_iteration_decider(lambda _scope: calls.append({}) or {"target": "none"})
            life_id = str(life._active()["life_id"])
            life._schedule_self_iteration_decision(life_id=life_id)
            assert calls == []
            assert scheduler["model_skipped"] == 1
            assert scheduler["self_iteration_model_skipped"] == 1
            assert scheduler["last_self_iteration_decision_error"] == "life.self_iteration.model_budget_exhausted"
        finally:
            life.close()


def test_capability_patch_entry_peek_blocks_without_side_effects():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            today = utc_day()
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            seed_global(scheduler, day=today)
            seed_pool(scheduler, prefix="capability_patch_model_", day=today, attempts=8, successes=6)
            calls: list[dict] = []
            life.set_capability_patch_decider(lambda _material: calls.append({}) or {})
            life_id = str(life._active()["life_id"])
            life._schedule_capability_health_decision(life_id=life_id)
            assert calls == []
            # 入口只窥探不记账：真正的按次记账在 worker 内每个补丁目标前完成。
            assert scheduler.get("model_skipped", 0) == 0
            assert scheduler.get("capability_patch_model_skipped", 0) == 0
            assert scheduler["last_capability_health_error"] == "life.capability_patch.model_budget_exhausted"
            assert scheduler.get("capability_health_inflight") is not True
        finally:
            life.close()


def test_cross_utc_day_resets_sub_pool_and_allows_call_again():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            yesterday = utc_day(-1)
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            seed_global(scheduler, day=yesterday, attempts=30, successes=20)
            seed_pool(scheduler, prefix="learning_model_", day=yesterday, attempts=8, successes=6)
            calls: list[dict] = []
            life.set_learning_decider(lambda _scope: calls.append({}) or {"target": "none"})
            life_id = str(life._active()["life_id"])
            life._schedule_autonomous_learning_decision(life_id=life_id)
            wait_scheduler_idle(life, life_id, inflight_key="learning_decision_inflight")
            assert calls, "跨日后预算应重置并允许调用"
            assert scheduler["model_attempts"] == 1
            assert scheduler["learning_model_attempts"] == 1
            assert scheduler["model_successes"] == 1
            assert scheduler["learning_model_successes"] == 1
        finally:
            life.close()


def test_global_pool_exhaustion_blocks_all_schedulers():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            today = utc_day()
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            # 全局尝试数到 30：即使各子池全新，也一律拒绝。
            seed_global(scheduler, day=today, attempts=30, successes=20)
            seed_pool(scheduler, prefix="learning_model_", day=today, attempts=0)
            seed_pool(scheduler, prefix="self_iteration_model_", day=today, attempts=0)
            learning_calls: list[dict] = []
            iteration_calls: list[dict] = []
            life.set_learning_decider(lambda _scope: learning_calls.append({}) or {"target": "none"})
            life.set_self_iteration_decider(lambda _scope: iteration_calls.append({}) or {"target": "none"})
            life_id = str(life._active()["life_id"])
            life._schedule_autonomous_learning_decision(life_id=life_id)
            life._schedule_self_iteration_decision(life_id=life_id)
            assert learning_calls == []
            assert iteration_calls == []
            assert scheduler["model_skipped"] == 2
            assert scheduler["learning_model_skipped"] == 1
            assert scheduler["self_iteration_model_skipped"] == 1
        finally:
            life.close()


def test_sub_pools_are_independent_learning_full_does_not_block_iteration():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            today = utc_day()
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            seed_global(scheduler, day=today)
            seed_pool(scheduler, prefix="learning_model_", day=today, attempts=8, successes=6)
            seed_pool(scheduler, prefix="self_iteration_model_", day=today, attempts=0)
            learning_calls: list[dict] = []
            iteration_calls: list[dict] = []
            life.set_learning_decider(lambda _scope: learning_calls.append({}) or {"target": "none"})
            life.set_self_iteration_decider(lambda _scope: iteration_calls.append({}) or {"target": "none"})
            life_id = str(life._active()["life_id"])
            life._schedule_autonomous_learning_decision(life_id=life_id)
            life._schedule_self_iteration_decision(life_id=life_id)
            wait_scheduler_idle(life, life_id, inflight_key="self_iteration_decision_inflight")
            # 学习子池耗尽 → 拒绝；自我迭代子池独立 → 正常调用并记账。
            assert learning_calls == []
            assert scheduler["learning_model_skipped"] == 1
            assert iteration_calls, "自我迭代子池应独立可用"
            assert scheduler["self_iteration_model_attempts"] == 1
            assert scheduler["self_iteration_model_successes"] == 1
            assert scheduler["model_attempts"] == 1
            assert scheduler["model_skipped"] == 1
        finally:
            life.close()
