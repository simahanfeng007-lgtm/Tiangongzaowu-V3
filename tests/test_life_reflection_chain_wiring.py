"""F3 步骤 3：任务源反思链接线（挂点 1-4）T1-T4。

任务启动 →（事前）预测快照 + OPEN episode → 任务完成/异常 →（事后）
结果证据 + 原子闭环反思；陈旧恢复把孤儿 OPEN episode 以 ABORTED 收尾。
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.episode_builder import build_prediction


def runtime(root: Path) -> EmbeddedLifeRuntime:
    life = EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "life-runtime",
        mode="embedded",
    )
    life.scheduler.stop(timeout_seconds=2)
    return life


def request(life: EmbeddedLifeRuntime, method: str, path: str, payload=None) -> dict:
    status, body, _ = life.request(method, path, payload)
    assert status == 200, (path, status, body)
    return body


def seed(life: EmbeddedLifeRuntime) -> None:
    request(
        life,
        "POST",
        "/api/v1/v3/life/settings",
        {"settings": {"autonomy_activity_types": ["daily_planning"]}},
    )
    request(
        life,
        "POST",
        "/api/v1/v3/life/memory/assert",
        {
            "memory_id": "mem_reflection_wiring",
            "content": {"text": "用户希望生命体做可验证且不过度打扰的事。"},
            "confidence_milli": 900,
        },
    )


def wait_task_status(life: EmbeddedLifeRuntime, life_id: str, task_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    result = {}
    while time.monotonic() < deadline:
        row = life._autonomy_state(life_id)["tasks"].get(task_id)
        if isinstance(row, dict) and str(row.get("status") or "") in statuses:
            result = row
            break
        time.sleep(0.02)
    assert result, f"task {task_id} never reached {statuses}"
    return result


def wait_reflection_closed(life: EmbeddedLifeRuntime, life_id: str, timeout: float = 3.0) -> None:
    """状态先行、反思闭环在后：轮询到 episode 闭合且注册表清空。"""
    store = life._contract_store()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        registry = life._scope_state(life_id)["scheduler"].get("open_episodes") or []
        if not registry and not store.open_causal_episodes(life_id):
            return
        time.sleep(0.02)
    raise AssertionError("reflection chain never closed the episode")


def test_t1_t2_task_success_closes_preregistered_episode(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        decider_entered = threading.Event()
        release_decider = threading.Event()
        try:
            seed(life)
            life_id = str(life._active()["life_id"])

            def decide(_scope: dict, _task: dict) -> dict:
                decider_entered.set()
                release_decider.wait(3)
                return {
                    "title": "今日规划",
                    "summary": "已完成内部规划，先推进一个可验证的小步骤。",
                    "findings": [],
                    "next_steps": [],
                    "uncertainties": [],
                }

            life.set_autonomy_decider(decide)
            first = request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "t1"})
            task_id = next(
                item["task_id"] for item in first["tasks"]
                if item.get("source") == "life_activity_catalog"
            )
            assert decider_entered.wait(3)
            store = life._contract_store()
            # T1：decider 执行前，OPEN episode 已带着事前预测落账。
            opens = store.open_causal_episodes(life_id)
            assert len(opens) == 1
            assert "predicted_success_milli" in opens[0].prior_prediction
            registry = life._scope_state(life_id)["scheduler"]["open_episodes"]
            assert len(registry) == 1
            assert registry[0]["ref"] == task_id
            episode_id = opens[0].episode_id
            release_decider.set()

            wait_task_status(life, life_id, task_id, {"completed"})
            # T2：完成后 episode 闭环、反思卡落库、注册表清空。
            wait_reflection_closed(life, life_id)
            cards = store.list_reflection_cards(life_id)
            assert len(cards) == 1
            card = cards[0]
            assert card.episode_id == episode_id
            assert "已完成内部规划" in card.observed_outcome
            assert store.open_causal_episodes(life_id) == ()
            assert life._scope_state(life_id)["scheduler"]["open_episodes"] == []
        finally:
            release_decider.set()
            life.close()


def test_t3_task_failure_maps_category_and_closes_episode(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            seed(life)
            life_id = str(life._active()["life_id"])

            def decide(_scope: dict, _task: dict) -> dict:
                raise TimeoutError("model bridge timed out")

            life.set_autonomy_decider(decide)
            first = request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "t3"})
            task_id = next(
                item["task_id"] for item in first["tasks"]
                if item.get("source") == "life_activity_catalog"
            )
            wait_task_status(life, life_id, task_id, {"blocked"})
            store = life._contract_store()
            wait_reflection_closed(life, life_id)
            cards = store.list_reflection_cards(life_id)
            assert len(cards) == 1
            # 异常类型 → 九类映射：TimeoutError → environment_error
            assert cards[0].failure_dimensions == ("environment_error",)
            assert cards[0].counterfactual_actions
            assert cards[0].next_minimal_experiment
            assert store.open_causal_episodes(life_id) == ()
        finally:
            life.close()


def test_t4_stale_recovery_aborts_orphan_open_episode(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            now_ms = time.time_ns() // 1_000_000
            # 伪造一个陈旧 running 任务 + 事前已落账的 OPEN episode。
            autonomy = life._autonomy_state(life_id)
            autonomy["tasks"]["task_orphan"] = {
                "task_id": "task_orphan",
                "source": "life_activity_catalog",
                "activity_id": "daily_planning",
                "title": "孤儿任务",
                "objective": "验证陈旧恢复",
                "status": "running",
                "risk_class": "A0",
                "priority": 500,
                "sequence": 1,
                "time_window": "空闲时",
                "created_at_ms": now_ms - 10_000_000,
                "updated_at_ms": now_ms - 10_000_000,
            }
            with life._lock:
                episode_id = life._open_runtime_episode_locked(
                    life_id=life_id,
                    source="autonomy",
                    ref_id="task_orphan",
                    event_kind="autonomy.task.attempt.started",
                    intention="验证陈旧恢复",
                    context_sha256="e" * 64,
                    prediction=build_prediction(
                        basis_inputs={"source": "autonomy", "task_id": "task_orphan"},
                    ),
                    action_risk="A0",
                )
            assert episode_id
            store = life._contract_store()
            assert len(store.open_causal_episodes(life_id)) == 1

            recovered = life._recover_stale_running_autonomy_tasks(
                life_id=life_id, now_ms=now_ms, stale_after_ms=600_000
            )
            assert recovered == 1
            # 孤儿 OPEN episode 以 ABORTED 收尾，不再悬挂。
            assert store.open_causal_episodes(life_id) == ()
            cards = store.list_reflection_cards(life_id)
            assert len(cards) == 1
            assert life._scope_state(life_id)["scheduler"]["open_episodes"] == []
        finally:
            life.close()


# ---------- F3 步骤 4：能力源接线（挂点 5-6）C1-C3 ----------


def _capability_runtime(tmp_path: Path, *, risk_level: str, invoker):
    from life_service.artifact_executor import compile_artifact, publish_artifact
    from life_service.capability_health import attach_health
    from total_gateway.runtime import (
        life_capability_workspace_mapper,
        life_capability_workspace_marker,
    )

    # 动作目录本身保持低风险：编译器按步骤动作抬 artifact 风险级，
    # A1 才能让失败证据的影响面进入自动回滚规则的适用范围。
    action_catalog = [
        {
            "action_id": "omni_body",
            "risk": "A1",
            "available": True,
            "effect": "bounded omni action",
            "argument_schema_sha256": "",
            "result_schema_sha256": "",
        }
    ]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    life = EmbeddedLifeRuntime(
        data_root=tmp_path / "life-data",
        runtime_root=tmp_path / "life-runtime",
        mode="embedded",
    )
    life.scheduler.stop(timeout_seconds=2)
    life.set_capability_workspace_mapper(life_capability_workspace_mapper(workspace))
    life.set_capability_workspace_marker(life_capability_workspace_marker(workspace))
    life.set_capability_workspace_remover(lambda artifact: {"removed": False})
    life.set_artifact_action_catalog_provider(lambda: list(action_catalog))
    life.set_artifact_invoker(invoker)
    life_id = str(life._active()["life_id"])
    learning = {
        "life_id": life_id,
        "learning_id": "learn_reflection_chain",
        "target": "skill",
        "title": "反思链测试技能",
        "summary": "测试能力源反思链接线",
        "risk_level": risk_level,
        "draft_artifact": {
            "content": "# 反思链测试技能\n\n完整正文\n",
            "required_actions": ["omni_body"],
            "task_intents": ["测试"],
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object"},
            "acceptance": [{"kind": "all_steps_succeeded"}],
            "steps": [
                {
                    "step_id": "s1",
                    "action_id": "omni_body",
                    "arguments_template": {"action": "file.write", "target": "t", "args": {}},
                    "on_failure": "stop",
                }
            ],
        },
    }
    compiled = compile_artifact(learning, action_catalog=list(action_catalog))
    artifact = publish_artifact(compiled)
    scope = life._scope_state(life_id)
    scope["capabilities"][artifact["artifact_id"]] = {**artifact, "origin": "life_learning"}
    pointer = {
        "schema": "tiangong.life.capability-pointer.v1",
        "life_id": life_id,
        "lineage_id": artifact["lineage_id"],
        "kind": "skill",
        "status": "active",
        "current_artifact_id": artifact["artifact_id"],
        "current_artifact_sha256": artifact["artifact_sha256"],
        "history": [],
        "pointer_sha256": "",
    }
    pointer = attach_health(pointer, artifact=artifact, now_ms=time.time_ns() // 1_000_000)
    scope["capability_pointers"][artifact["lineage_id"]] = pointer
    life._persist(life_id)
    return life, life_id, artifact


def test_c1_capability_success_preregisters_and_learns(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life, life_id, artifact = _capability_runtime(
            Path(temporary),
            risk_level="A1",
            invoker=lambda action_id, arguments, ctx: {"ok": True, "zhuangtai": "wancheng"},
        )
        try:
            result = life._capability_invoke(
                {"life_id": life_id, "artifact_id": artifact["artifact_id"]}
            )
            assert result["ok"] is True
            # 返回体追加只读观测键。
            assert result["reflection"]["source"] == "causal_reflection_chain"
            # 决策 C 诚实基线：correlation_only 的成功证据不进入能力学习。
            assert result["capability_learning"]["outcome"] == "not_committed"
            assert result["capability_learning"]["rollback_applied"] is False
            store = life._contract_store()
            assert store.open_causal_episodes(life_id) == ()
            cards = store.list_reflection_cards(life_id)
            assert len(cards) == 1
            assert "能力执行成功" in cards[0].observed_outcome
            assert store.list_capability_evidence(life_id) == ()
            assert store.latest_capability_profiles(life_id) == ()
        finally:
            life.close()


def test_c2_c3_three_verified_failures_auto_rollback_pointer_stays_active(tmp_path: Path) -> None:
    calls = {"count": 0}

    def failing_invoker(action_id, arguments, ctx):
        calls["count"] += 1
        return {"ok": False, "error_code": "permission.denied"}

    with tempfile.TemporaryDirectory() as temporary:
        life, life_id, artifact = _capability_runtime(
            Path(temporary), risk_level="A1", invoker=failing_invoker
        )
        try:
            for index in range(3):
                result = life._capability_invoke(
                    {
                        "life_id": life_id,
                        "artifact_id": artifact["artifact_id"],
                        "request_id": f"chain-failure-{index}",
                    }
                )
                assert result["ok"] is False
                # 失败证据是 eligible 的：进入能力学习（样本不足 → hold）。
                assert result["capability_learning"]["outcome"] == "hold"
            store = life._contract_store()
            evidence = store.list_capability_evidence(life_id)
            assert len(evidence) == 3
            assert all(item.eligible_failure for item in evidence)
            # 失败类别从步骤错误码映射：permission.denied → insufficient_permission
            cards = store.list_reflection_cards(life_id)
            assert all(
                card.failure_dimensions == ("insufficient_permission",) for card in cards
            )
            profiles = store.latest_capability_profiles(life_id)
            assert len(profiles) == 1
            # C2：A1 影响 + 3 条 verified 失败 → 自动回滚，熟练度归零。
            assert profiles[0].rollback_count == 1
            assert profiles[0].proficiency_lower_bound_milli == 0
            # C3：双体系互斥——认知层回滚不触碰运行时治理层：
            # pointer 仍 active，健康档案照常记账失败。
            pointer = life._scope_state(life_id)["capability_pointers"][artifact["lineage_id"]]
            assert pointer["status"] == "active"
            assert pointer["health"]["consecutive_failures"] == 3
            assert pointer["health"]["success_streak"] == 0
        finally:
            life.close()


# ---------- F3 步骤 5：消费面 P1/P2/S1/S2 ----------


def test_p1_panel_exposes_reflection_cards(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            seed(life)
            life_id = str(life._active()["life_id"])

            def decide(_scope: dict, _task: dict) -> dict:
                return {
                    "title": "今日规划",
                    "summary": "已完成内部规划，推进一个可验证的小步骤。",
                    "findings": [],
                    "next_steps": [],
                    "uncertainties": [],
                }

            life.set_autonomy_decider(decide)
            first = request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "p1"})
            task_id = next(
                item["task_id"] for item in first["tasks"]
                if item.get("source") == "life_activity_catalog"
            )
            wait_task_status(life, life_id, task_id, {"completed"})
            wait_reflection_closed(life, life_id)
            panel = request(life, "GET", "/api/v1/v3/life/panel")
            cards = panel["reflection_cards"]
            assert len(cards) == 1
            assert cards[0]["source"] == "causal_reflection_chain"
            assert cards[0]["revision"]
            assert "已完成内部规划" in cards[0]["observed_outcome"]
        finally:
            life.close()


def test_p2_overlay_carries_profile_and_composite_score(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        # 两次失败 + 一次成功：失败证据 eligible → profile 落库（hold）。
        calls = {"n": 0}

        def flaky_invoker(action_id, arguments, ctx):
            calls["n"] += 1
            if calls["n"] <= 2:
                return {"ok": False, "error_code": "tool_error"}
            return {"ok": True, "zhuangtai": "wancheng"}

        life, life_id, artifact = _capability_runtime(
            Path(temporary), risk_level="A1", invoker=flaky_invoker
        )
        try:
            for index in range(3):
                life._capability_invoke(
                    {
                        "life_id": life_id,
                        "artifact_id": artifact["artifact_id"],
                        "request_id": f"p2-{index}",
                    }
                )
            overlay = life._capability_overlay_payload({"life_id": life_id})
            rows = overlay["artifacts"]
            assert len(rows) == 1
            row = rows[0]
            # profile 字段挂载 + 双体系综合分。
            assert row["profile"]["rollback_count"] == 0
            assert row["proficiency_lower_bound_milli"] == 0
            assert row["composite_score_milli"] == max(
                row["health_score_milli"], row["proficiency_lower_bound_milli"]
            )
            context = overlay["model_context"][0]
            assert "proficiency_lower_bound_milli" in context
            assert "health_score_milli" in context
        finally:
            life.close()


def test_s1_s2_activity_scope_injects_recent_reflections_without_secret_keys(tmp_path: Path) -> None:
    from life_service.activity_scope import build_activity_scope

    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            rows = [
                {
                    "reflection_id": "rfc_test_1",
                    "observed_outcome": "任务失败：工具返回错误。" + "长" * 600,
                    "prediction_error_milli": 300,
                    "failure_dimensions": ["tool_error"],
                    "next_minimal_experiment": "先执行只读探针。",
                }
            ] * 7
            scope = build_activity_scope(
                life_id=life_id,
                soul=None,
                scope=life._scope_state(life_id),
                reflection_rows=rows,
            )
            # S1：注入最近 5 条、文本截断。
            assert len(scope["recent_reflections"]) == 5
            trimmed = scope["recent_reflections"][0]
            assert len(trimmed["observed_outcome"]) <= 400
            assert trimmed["prediction_error_milli"] == 300
            # S2：顶层键不含 credential 类词（防泄密守卫在构建时执行）。
            top_keys = {str(key).casefold() for key in scope}
            assert not top_keys & {
                "api_key", "apikey", "token", "password", "secret", "credential",
            }
            # 缺省参数零影响：不传 reflection_rows 时不注入内容。
            default_scope = build_activity_scope(
                life_id=life_id, soul=None, scope=life._scope_state(life_id)
            )
            assert default_scope["recent_reflections"] == []
        finally:
            life.close()
