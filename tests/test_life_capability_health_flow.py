"""能力健康链路集成测试：记账 -> 补丁验证门 -> 回滚/降级 -> 重新激活。"""

from __future__ import annotations

import time

from life_service.artifact_executor import compile_artifact, publish_artifact
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
    artifact = publish_artifact(compiled)
    scope["capabilities"][artifact["artifact_id"]] = {
        **artifact,
        "origin": "life_learning",
    }
    mapper = life_capability_workspace_mapper(workspace)
    mapper(artifact)
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


def test_patch_requires_trigger_condition(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    try:
        try:
            life._capability_patch_propose(
                {"life_id": life_id, "artifact_id": artifact["artifact_id"]},
                decision=_patch_decision(life_id),
            )
            raise AssertionError("patch before trigger must be rejected")
        except Exception as exc:
            assert "patch_not_triggered" in str(getattr(exc, "code", "") or exc)
    finally:
        life.close()


def test_good_patch_passes_verification_gate_and_replaces_pointer(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    artifact_id = artifact["artifact_id"]
    try:
        _fail_times(life, life_id, artifact_id, 3)
        proposed = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id, "actor": "life_health"},
            decision=_patch_decision(life_id),
        )
        assert proposed["ok"] is True
        patched_id = proposed["patch_artifact"]["artifact_id"]
        assert patched_id != artifact_id
        # 补丁版本保持稳定 skill_id（工作区映射路径不漂移）。
        patched = life._scope_state(life_id)["capabilities"][patched_id]
        assert (
            patched["skill_spec"]["skill_id"]
            == artifact["skill_spec"]["skill_id"]
        )
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["health"]["patch_pending"]["to_artifact_id"] == patched_id
        settled = life._capability_patch_settle(
            {"life_id": life_id, "artifact_id": artifact_id, "actor": "life_health"}
        )
        print("VERIFY DEBUG:", settled.get("verification"))
        assert settled["applied"] is True
        assert settled["reason"] == "applied"
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["current_artifact_id"] == patched_id
        assert pointer["health"]["consecutive_failures"] == 0
        assert pointer["status"] == "active"
    finally:
        life.close()


def test_bad_patch_build_failure_consumes_rounds_then_degrades_and_marks_mapping(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    artifact_id = artifact["artifact_id"]
    try:
        _fail_times(life, life_id, artifact_id, 3)
        # 第一轮坏补丁：编译失败（缺验收标准）-> 计一轮，指针保持旧版。
        result = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id},
            decision=_patch_decision(life_id, broken=True),
        )
        assert result["ok"] is False
        assert result["patch_rounds"] == 1
        assert result["degraded"] is False
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["current_artifact_id"] == artifact_id
        assert pointer["health"]["patch_rounds"] == 1
        # 第二轮坏补丁 -> 轮次用尽自动降级。
        result = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id},
            decision=_patch_decision(life_id, broken=True),
        )
        assert result["ok"] is False
        assert result["degraded"] is True
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["status"] == "degraded"
        # 工作区映射被标记为降级。
        workspace = life.paths.runtime_root.parent / "workspace"
        target = workspace / "skills" / "life" / f"{artifact['skill_spec']['skill_id']}.md"
        content = target.read_text(encoding="utf-8")
        assert "tiangong-life-status: degraded" in content
        assert "runtime_usable: false" in content
        assert "自动降级" in content
        # overlay 中不再 runtime_usable。
        overlay = life._capability_overlay_payload({"life_id": life_id})
        rows = [row for row in overlay["artifacts"] if row["artifact_id"] == artifact_id]
        assert rows and rows[0]["runtime_usable"] is False
        assert rows[0]["activation_status"] == "degraded"
    finally:
        life.close()


def test_verification_gate_rolls_back_on_tampered_digest_then_degrades(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    artifact_id = artifact["artifact_id"]
    try:
        _fail_times(life, life_id, artifact_id, 3)
        proposed = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id},
            decision=_patch_decision(life_id),
        )
        assert proposed["ok"] is True
        patched_id = proposed["patch_artifact"]["artifact_id"]
        # 篡改补丁摘要：验证门必须拒绝并回滚（指针保持旧版）。
        scope = life._scope_state(life_id)
        scope["capabilities"][patched_id]["artifact_sha256"] = "f" * 64
        life._persist(life_id)
        settled = life._capability_patch_settle(
            {"life_id": life_id, "artifact_id": artifact_id}
        )
        assert settled["applied"] is False
        assert settled["reason"] == "rolled_back"
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["current_artifact_id"] == artifact_id
        assert pointer["health"]["patch_rounds"] == 1
        # 第二轮补丁同样被验证门拒绝 -> 自动降级。
        proposed = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id},
            decision=_patch_decision(life_id),
        )
        assert proposed["ok"] is True
        scope = life._scope_state(life_id)
        scope["capabilities"][proposed["patch_artifact"]["artifact_id"]]["artifact_sha256"] = "f" * 64
        life._persist(life_id)
        settled = life._capability_patch_settle(
            {"life_id": life_id, "artifact_id": artifact_id}
        )
        assert settled["applied"] is False
        assert settled["reason"] == "degraded"
        assert _pointer_of(life, life_id, artifact)["status"] == "degraded"
    finally:
        life.close()


def test_reactivate_requires_user_and_unmarks_mapping(tmp_path):
    life, life_id, artifact = _setup_runtime(tmp_path)
    artifact_id = artifact["artifact_id"]
    try:
        _fail_times(life, life_id, artifact_id, 3)
        result = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id},
            decision=_patch_decision(life_id, broken=True),
        )
        assert result["ok"] is False and result["patch_rounds"] == 1
        result = life._capability_patch_propose(
            {"life_id": life_id, "artifact_id": artifact_id},
            decision=_patch_decision(life_id, broken=True),
        )
        assert result["ok"] is False and result["degraded"] is True
        pointer = _pointer_of(life, life_id, artifact)
        assert pointer["status"] == "degraded"
        # 非 user 不能重新激活。
        try:
            life._capability_reactivate(
                {"life_id": life_id, "artifact_id": artifact_id, "actor": "life_scheduler"}
            )
            raise AssertionError("scheduler must not reactivate")
        except Exception as exc:
            assert "reactivate_invalid" in str(getattr(exc, "code", "") or exc)
        # 用户重新激活：映射恢复 active 标记。
        result = life._capability_reactivate(
            {"life_id": life_id, "artifact_id": artifact_id, "actor": "user"}
        )
        assert result["pointer"]["status"] == "active"
        workspace = life.paths.runtime_root.parent / "workspace"
        target = workspace / "skills" / "life" / f"{artifact['skill_spec']['skill_id']}.md"
        content = target.read_text(encoding="utf-8")
        assert "tiangong-life-status: active" in content
        assert "runtime_usable: true" in content
    finally:
        life.close()
