"""认知任务执行接线测试。

覆盖三处手术：
1. 认知候选携带 life_cognition 来源与 model_internal 通道标记
2. 被 reap 的僵尸任务（从未尝试）指纹不再封锁重新提案
3. 认知任务经活动回路执行，proposed_relations 落回记忆系统
4. noop 决策携带原因；停机恢复叙事事件可发出且幂等
"""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from pathlib import Path

from life_service.autonomous_tasks import (
    COGNITION_SOURCE,
    default_autonomy_state,
    derive_task_candidates,
    materialize_tasks,
    update_task_status,
)
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


def _hypothesis_scope() -> dict:
    return {
        "memories": {
            "mem_low_conf": {
                "memory_id": "mem_low_conf",
                "status": "active",
                "content": {"text": "周报显示项目的构建时长在上周变长了。"},
                "epistemic_status": "hypothesis",
                "confidence_milli": 400,
                "priority": 900,
                "classification": {"causal_role": "context"},
            },
        },
        "memory_relations": [],
        "learning": {},
        "capabilities": {},
        "settings": {},
    }


def test_cognition_candidates_carry_source_and_channel() -> None:
    candidates = derive_task_candidates(_hypothesis_scope(), life_id="life_test")
    cognition = [c for c in candidates if c["task_kind"] == "verify_memory_hypothesis"]
    assert cognition, "低置信记忆应推导出 verify_memory_hypothesis 候选"
    item = cognition[0]
    assert item["source"] == COGNITION_SOURCE
    assert item["execution_mode"] == "model_internal"
    assert item["subject_refs"] == ["mem_low_conf"]


def test_reaped_zombie_fingerprint_allows_reproposal() -> None:
    scope = _hypothesis_scope()
    candidates = derive_task_candidates(scope, life_id="life_test")
    cognition = [c for c in candidates if c.get("source") == COGNITION_SOURCE]
    assert cognition

    state = default_autonomy_state()
    created = materialize_tasks(
        state, cognition, life_id="life_test", reason="first", now_ms=1_000
    )
    assert len(created) == 1
    task_id = created[0]["task_id"]
    fingerprint = created[0]["fingerprint"]

    # 模拟 reap：从未尝试、按陈旧回收取消。
    update_task_status(
        state,
        task_id=task_id,
        status="cancelled",
        now_ms=2_000,
        result={"reason_code": "life.autonomy.stale_pending_reaped", "age_hours": 80},
    )

    recreated = materialize_tasks(
        deepcopy(state), cognition, life_id="life_test", reason="second", now_ms=3_000
    )
    assert [t["fingerprint"] for t in recreated] == [fingerprint], (
        "reap 僵尸任务的指纹必须允许重新提案"
    )

    # 对照：正常完成的任务指纹仍应封锁，防止重复执行。
    state_done = default_autonomy_state()
    created2 = materialize_tasks(
        state_done, cognition, life_id="life_test", reason="a", now_ms=1_000
    )
    update_task_status(
        state_done,
        task_id=created2[0]["task_id"],
        status="completed",
        now_ms=2_000,
        result={"summary": "done"},
    )
    recreated2 = materialize_tasks(
        deepcopy(state_done), cognition, life_id="life_test", reason="b", now_ms=3_000
    )
    assert not recreated2, "已完成任务的指纹仍应封锁"


def test_cognition_task_executes_and_writes_memory_relation(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    seen: dict = {}
    called = threading.Event()
    try:
        _request(
            life,
            "POST",
            "/api/v1/v3/life/memory/assert",
            {
                "memory_id": "mem_low_conf",
                "content": {"text": "周报显示项目的构建时长在上周变长了。"},
                "confidence_milli": 300,
            },
        )
        _request(
            life,
            "POST",
            "/api/v1/v3/life/memory/assert",
            {
                "memory_id": "mem_io_peak",
                "content": {"text": "系统监控记录了周四晚间的磁盘IO高峰。"},
                "confidence_milli": 900,
            },
        )

        def decide(scope: dict, task: dict) -> dict:
            seen["task"] = deepcopy(task)
            called.set()
            return {
                "title": "假设验证",
                "summary": "已核验构建时长增长与磁盘IO高峰的关联证据。",
                "findings": ["IO高峰与构建时长增长在同周出现"],
                "proposed_relations": [
                    {
                        "source_memory_id": "mem_io_peak",
                        "kind": "causes",
                        "target_memory_id": "mem_low_conf",
                        "evidence": "时间重合且构建过程依赖磁盘IO",
                    }
                ],
                "next_steps": [],
                "uncertainties": [],
            }

        life.set_autonomy_decider(decide)
        _request(
            life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "cognition-test"}
        )
        assert called.wait(2)
        task_payload = seen["task"]
        assert task_payload["source"] == COGNITION_SOURCE
        assert task_payload["subject_memories"], "认知任务必须携带目标记忆材料"
        assert task_payload["subject_memories"][0]["memory_id"] == "mem_low_conf"

        deadline = time.monotonic() + 4
        completed = None
        while time.monotonic() < deadline:
            tasks = life._scope_state()["autonomy"]["tasks"]
            rows = [
                t for t in tasks.values()
                if isinstance(t, dict) and t.get("source") == COGNITION_SOURCE
            ]
            done = [t for t in rows if t.get("status") == "completed"]
            if done:
                completed = done[0]
                break
            time.sleep(0.02)
        assert completed is not None, "认知任务应经活动回路完成"
        assert completed["result"].get("applied_relation_ids"), "关系写回应记录ID"

        relations = life._scope_state()["memory_relations"]
        assert any(
            r.get("kind") == "causes"
            and r.get("source_memory_id") == "mem_io_peak"
            and r.get("target_ref") == "mem_low_conf"
            for r in relations
        ), "补出的因果边应写入 memory_relations"
    finally:
        life.close()


def test_learning_noop_records_reason(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    try:
        life.set_learning_decider(
            lambda scope: {"target": "none", "reason": "近期材料无新学习方向"}
        )
        _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "noop-test"})
        life_id = str(life._active()["life_id"])
        deadline = time.monotonic() + 4
        payload = None
        while time.monotonic() < deadline:
            for event in reversed(life.system.journal.events(life_id)):
                if event.get("event_type") == "learning.decision_noop":
                    payload = event.get("payload")
                    break
            if payload:
                break
            time.sleep(0.02)
        assert payload is not None
        assert payload.get("reason") == "近期材料无新学习方向"
    finally:
        life.close()


def test_scheduler_resume_narrative_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    life = _runtime(tmp_path)
    try:
        _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "beat"})
        life_id = str(life._active()["life_id"])
        # 把最后心跳时间伪造成远古 → gap 超阈值。
        monkeypatch.setattr(
            EmbeddedLifeRuntime, "_iso_ms", staticmethod(lambda value: 1)
        )
        assert life._emit_scheduler_resume_narrative(life_id) is True
        assert life._emit_scheduler_resume_narrative(life_id) is False, "同一缺口幂等"
        events = [
            e for e in life.system.journal.events(life_id)
            if e.get("event_type") == "life.scheduler.resumed"
        ]
        assert len(events) == 1
        assert events[0]["payload"]["last_heartbeat_count"] >= 1
    finally:
        life.close()
