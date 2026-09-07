"""能力健康链路集成测试：记账 -> 补丁验证门 -> 回滚/降级 -> 重新激活。"""

from __future__ import annotations

import time

from life_service.artifact_executor import compile_artifact
from tests.legacy_learning_fixtures import historical_published_artifact
from copy import deepcopy
import pytest
from life_service.embedded_runtime import EmbeddedLifeError
from life_service.capability_health import attach_health
from life_service.embedded_runtime import EmbeddedLifeRuntime
from total_gateway.runtime import (
    life_capability_workspace_mapper,
    life_capability_workspace_marker,
)


_ACTION_CATALOG = [
    {
        "action_id": "omni_body",
        "risk": "A3",
        "available": True,
        "effect": "bounded omni action",
        "argument_schema_sha256": "",
        "result_schema_sha256": "",
    }
]


def _learning(life_id: str, *, draft: dict | None = None) -> dict:
    return {
        "life_id": life_id,
        "learning_id": "learn_health_test",
        "target": "skill",
        "title": "健康测试技能",
        "summary": "测试能力健康链路",
        "risk_level": "A3",
        "draft_artifact": draft
        or {
            "content": "# 健康测试技能\n\n完整正文\n",
            "required_actions": ["omni_body"],
            "task_intents": ["测试"],
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "acceptance": [{"kind": "all_steps_succeeded"}],
            "steps": [
                {
                    "step_id": "s1_write",
                    "action_id": "omni_body",
                    "arguments_template": {
                        "action": "file.write",
                        "target": "{{input.path}}",
                        "args": {"content": "# 完整正文\n"},
                    },
                    "on_failure": "stop",
                }
            ],
        },
    }


def _setup_runtime(tmp_path):
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
    life.set_artifact_action_catalog_provider(lambda: list(_ACTION_CATALOG))
    life.set_artifact_invoker(
        lambda action_id, arguments, ctx: {"ok": True, "zhuangtai": "wancheng"}
    )
    life_id = str(life._active()["life_id"])
    scope = life._scope_state(life_id)
    compiled = compile_artifact(
        _learning(life_id),
        action_catalog=list(_ACTION_CATALOG),
    )
    artifact = historical_published_artifact(compiled)
    scope["capabilities"][artifact["artifact_id"]] = {
        **artifact,
        "origin": "life_learning",
    }
    mapper = life_capability_workspace_mapper(workspace)
    # Pre-P10 workspace fixture, not a call to the frozen projection writer.
    target=workspace/'skills'/'life'/(artifact['skill_spec']['skill_id']+'.md')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(artifact['document']['content'],encoding='utf-8')
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
    pointer = attach_health(
        pointer,
        artifact=artifact,
        now_ms=time.time_ns() // 1_000_000,
    )
    scope["capability_pointers"][artifact["lineage_id"]] = pointer
    scope["executions"] = {}
    life._persist(life_id)
    return life, life_id, artifact


def _patch_decision(life_id: str, *, broken: bool = False) -> dict:
    steps = [
        {
            "step_id": "s1_write",
            "action_id": "omni_body",
            "arguments_template": {
                "action": "file.write",
                "target": "{{input.path}}",
                "args": {"content": "# 补丁正文\n"},
            },
            "on_failure": "stop",
        }
    ]
    if broken:
        steps = [
            {
                "step_id": "s1_broken",
                "action_id": "omni_body",
                "arguments_template": {"target": "{{input.missing_value}}"},
                "on_failure": "stop",
            }
        ]
    return {
        "title": "健康补丁",
        "summary": "修复失败链路",
        "risk_level": "A3",
        "draft_artifact": {
            "content": "# 补丁正文\n",
            "required_actions": ["omni_body"],
            "task_intents": ["测试"],
            "input_schema": (
                {"type": "object", "properties": {"path": {"type": "string"}}}
                if not broken
                else {"type": "object"}
            ),
            "output_schema": {"type": "object"},
            "acceptance": [{"kind": "all_steps_succeeded"}] if not broken else [],
            "steps": steps,
        },
    }


def _fail_times(life, life_id, artifact_id, count: int, start: int = 0):
    for index in range(count):
        life._capability_outcome_report(
            {
                "life_id": life_id,
                "artifact_id": artifact_id,
                "outcome": "failure",
                "outcome_id": f"flow_fail_{start + index}",
            }
        )


def _pointer_of(life, life_id, artifact):
    return life._scope_state(life_id)["capability_pointers"][artifact["lineage_id"]]


def test_invoke_records_outcome_and_success_resets(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    artifact_id = artifact["artifact_id"]
    try:
        _fail_times(life, life_id, artifact_id, 2)
        result = life._capability_invoke(
            {"life_id": life_id, "artifact_id": artifact_id, "inputs": {"path": "out/test.md"}}
        )
        assert result["ok"] is True
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["health"]["uses"] == 3
        assert pointer["health"]["consecutive_failures"] == 0
        assert pointer["health"]["successes"] == 1
    finally:
        life.close()


def test_outcome_report_is_idempotent_and_version_isolated(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    artifact_id = artifact["artifact_id"]
    try:
        life._capability_outcome_report(
            {"life_id": life_id, "artifact_id": artifact_id, "outcome": "failure", "outcome_id": "dup1"}
        )
        life._capability_outcome_report(
            {"life_id": life_id, "artifact_id": artifact_id, "outcome": "failure", "outcome_id": "dup1"}
        )
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["health"]["uses"] == 1
    finally:
        life.close()


def test_patch_freeze_precedes_trigger_evaluation(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    try:
        try:
            life._capability_patch_propose(
                {"life_id": life_id, "artifact_id": artifact["artifact_id"]},
                decision=_patch_decision(life_id),
            )
            raise AssertionError("patch before trigger must be rejected")
        except Exception as exc:
            assert "legacy_publication_frozen" in str(getattr(exc, "code", "") or exc)
    finally:
        life.close()


def test_good_legacy_patch_is_frozen_without_pointer_change(tmp_path):
    life,life_id,artifact=_setup_runtime(tmp_path)
    try:
        _fail_times(life,life_id,artifact['artifact_id'],3)
        before=deepcopy(_pointer_of(life,life_id,artifact))
        capabilities=deepcopy(life._scope_state(life_id)['capabilities'])
        for _ in range(2):
            with pytest.raises(EmbeddedLifeError,match='legacy_publication_frozen'):
                life._capability_patch_propose({'life_id':life_id,'artifact_id':artifact['artifact_id']},
                    decision=_patch_decision(life_id,broken=False))
        # Frozen routes neither consume repair rounds nor replace/degrade the old version.
        assert _pointer_of(life,life_id,artifact)==before
        assert life._scope_state(life_id)['capabilities']==capabilities
    finally:
        life.close()


def test_frozen_patch_does_not_consume_rounds_or_degrade_history(tmp_path):
    life,life_id,artifact=_setup_runtime(tmp_path)
    try:
        _fail_times(life,life_id,artifact['artifact_id'],3)
        before=deepcopy(_pointer_of(life,life_id,artifact))
        capabilities=deepcopy(life._scope_state(life_id)['capabilities'])
        for _ in range(2):
            with pytest.raises(EmbeddedLifeError,match='legacy_publication_frozen'):
                life._capability_patch_propose({'life_id':life_id,'artifact_id':artifact['artifact_id']},
                    decision=_patch_decision(life_id,broken=True))
        # Frozen routes neither consume repair rounds nor replace/degrade the old version.
        assert _pointer_of(life,life_id,artifact)==before
        assert life._scope_state(life_id)['capabilities']==capabilities
    finally:
        life.close()


def test_tampered_patch_still_fails_verification_and_cannot_settle(tmp_path):
    life,life_id,artifact=_setup_runtime(tmp_path)
    try:
        # Retained validator continues rejecting corrupted historical material.
        tampered={**artifact,'artifact_sha256':'f'*64}
        assert not life._capability_verify_patch(tampered)['passed']
        before=deepcopy(_pointer_of(life,life_id,artifact))
        with pytest.raises(EmbeddedLifeError,match='legacy_publication_frozen'):
            life._capability_patch_settle({'artifact_id':artifact['artifact_id']})
        assert _pointer_of(life,life_id,artifact)==before
    finally:
        life.close()


def test_legacy_reactivation_is_frozen_for_user_and_scheduler(tmp_path):
    life,life_id,artifact=_setup_runtime(tmp_path)
    try:
        from life_service.capability_health import degrade_pointer
        pointer=degrade_pointer(_pointer_of(life,life_id,artifact),reason='historical failure',now_ms=time.time_ns()//1_000_000)
        life._scope_state(life_id)['capability_pointers'][artifact['lineage_id']]=pointer
        life._mark_capability_workspace_status(artifact,pointer)
        workspace=life.paths.runtime_root.parent/'workspace'
        path=workspace/'skills'/'life'/(artifact['skill_spec']['skill_id']+'.md')
        original=path.read_bytes()
        assert b'runtime_usable: false' in original
        for actor in ('life_scheduler','user'):
            with pytest.raises(EmbeddedLifeError,match='legacy_publication_frozen'):
                life._capability_reactivate({'artifact_id':artifact['artifact_id'],'actor':actor})
        assert path.read_bytes()==original and _pointer_of(life,life_id,artifact)==pointer
    finally:
        life.close()


# ---------- F5：正向强化排序 + 闲置标记 ----------


def test_overlay_ranks_recent_successes_above_idle_and_marks_idle(tmp_path):
    from life_service.capability_health import ingest_outcome

    life, life_id, fresh = _setup_runtime(tmp_path)
    try:
        day = 86_400_000
        now_ms = time.time_ns() // 1_000_000
        scope = life._scope_state(life_id)
        # 第二个能力：标题字典序更靠前（旧排序会排第一），但最近成功在 8 天前。
        idle_learning = _learning(life_id)
        idle_learning["learning_id"] = "learn_idle_test"
        idle_learning["title"] = "AAA闲置技能"
        compiled = compile_artifact(idle_learning, action_catalog=list(_ACTION_CATALOG))
        idle_artifact = historical_published_artifact(compiled)
        scope["capabilities"][idle_artifact["artifact_id"]] = {
            **idle_artifact,
            "origin": "life_learning",
        }
        idle_pointer = {
            "schema": "tiangong.life.capability-pointer.v1",
            "life_id": life_id,
            "lineage_id": idle_artifact["lineage_id"],
            "kind": "skill",
            "status": "active",
            "current_artifact_id": idle_artifact["artifact_id"],
            "current_artifact_sha256": idle_artifact["artifact_sha256"],
            "history": [],
            "pointer_sha256": "",
        }
        from life_service.capability_health import attach_health as _attach

        idle_pointer = _attach(idle_pointer, artifact=idle_artifact, now_ms=now_ms)
        idle_pointer, _, _ = ingest_outcome(
            idle_pointer,
            {
                "outcome_id": "idle_ok_1",
                "artifact_id": idle_artifact["artifact_id"],
                "outcome": "success",
                "occurred_at_ms": now_ms - 8 * day,
            },
            now_ms=now_ms,
        )
        scope["capability_pointers"][idle_artifact["lineage_id"]] = idle_pointer
        # 第一个能力改名让字典序落后，并连续成功 10 次（最近）。
        fresh_pointer = scope["capability_pointers"][fresh["lineage_id"]]
        for index in range(10):
            fresh_pointer, _, _ = ingest_outcome(
                fresh_pointer,
                {
                    "outcome_id": f"fresh_ok_{index}",
                    "artifact_id": fresh["artifact_id"],
                    "outcome": "success",
                    "occurred_at_ms": now_ms - index * 1000,
                },
                now_ms=now_ms,
            )
        scope["capability_pointers"][fresh["lineage_id"]] = fresh_pointer
        life._persist(life_id)

        overlay = life._capability_overlay_payload({"life_id": life_id})
        rows = overlay["artifacts"]
        assert len(rows) == 2
        # 健康分排序：最近连续成功的能力必须排在闲置能力之前（与标题字典序相反）。
        assert rows[0]["artifact_id"] == fresh["artifact_id"]
        assert rows[1]["artifact_id"] == idle_artifact["artifact_id"]
        assert rows[1]["idle"] is True
        assert rows[0]["idle"] is False
        assert rows[0]["health_score_milli"] > rows[1]["health_score_milli"]
        # 模型上下文携带健康分（模型可见）。
        context = {
            row["artifact_id"]: row
            for row in overlay["model_context"]
        }
        assert context[fresh["artifact_id"]]["health_score_milli"] == rows[0]["health_score_milli"]
    finally:
        life.close()
