"""P1-5（H3）：Journal Reducer Registry 测试。

三层：
1. 注册表完备性——三分类覆盖、reducer 配对、未知类型 fail-closed
2. 新增家族 reducer 语义（能力治理/学习/升级/主动消息/信箱）
3. e2e 崩溃窗口（mock _persist）——capability.executed 的投影恢复
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from life_service.autonomous_tasks import normalize_autonomy_state
from life_service.journal_replay import (
    EVENT_REGISTRY,
    EventClass,
    JournalReplayError,
    replay_event,
)


def _event(event_type: str, payload: dict, *, created_at: str = "2026-08-22T12:00:00+00:00") -> dict:
    return {
        "event_type": event_type,
        "payload": payload,
        "created_at": created_at,
        "idempotency_key": f"test:{event_type}",
        "sequence": 1,
    }


def _replay(scope: dict, event_type: str, payload: dict) -> bool:
    return replay_event(
        scope,
        _event(event_type, payload),
        life_id="life_replay_test",
        normalize_autonomy_state=normalize_autonomy_state,
    )


def _pointer(artifact_id: str = "art_v1", **health) -> dict:
    return {
        "schema": "tiangong.life.capability-pointer.v1",
        "life_id": "life_replay_test",
        "lineage_id": "lineage_test",
        "kind": "skill",
        "status": "active",
        "current_artifact_id": artifact_id,
        "current_artifact_sha256": "a" * 64,
        "history": [],
        "pointer_sha256": "",
        "health": {
            "schema": "tiangong.life.capability-health.v1",
            "uses": 0,
            "successes": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "success_streak": 0,
            "last_success_at_ms": 0,
            "patch_rounds": 0,
            "patch_pending": None,
            "patch_history": [],
            "seen_outcome_ids": [],
            "last_outcome_at_ms": 0,
            "reactivated_at_ms": None,
            "created_at_ms": 0,
            **health,
        },
    }


# ---------- 1. 注册表完备性 ----------


def test_registry_covers_all_families_with_paired_reducers() -> None:
    from life_service.journal_replay import _REDUCERS

    assert len(EVENT_REGISTRY) >= 47
    replayable = {
        name for name, cls in EVENT_REGISTRY.items()
        if cls in {EventClass.REPLAYABLE_PROJECTION, EventClass.EXTERNAL_TERMINAL_EVIDENCE}
    }
    # 每个可重放类型必须有 reducer，反之每个 reducer 必须已分类。
    assert replayable == set(_REDUCERS)
    # 核心家族全部在场。
    for prefix in ("memory.", "autonomy.", "capability.", "learning.", "upgrade.", "life.proactive.", "life.episode."):
        assert any(name.startswith(prefix) for name in EVENT_REGISTRY)


def test_unknown_event_type_fails_closed() -> None:
    scope: dict = {"memories": {}, "memory_relations": [], "executions": {}, "autonomy": {}}
    with pytest.raises(JournalReplayError) as caught:
        _replay(scope, "future.unknown_event", {})
    assert caught.value.code == "life.projection.event_unclassified"


def test_audit_only_events_are_noop() -> None:
    scope: dict = {"memories": {}, "memory_relations": [], "executions": {}, "autonomy": {}}
    for event_type in ("life.heartbeat", "affect.decayed", "life.episode.opened", "memory.recalled"):
        assert _replay(scope, event_type, {}) is False


# ---------- 2. 新增家族 reducer 语义 ----------


def test_capability_pointer_family_assigns_full_state() -> None:
    scope = {"capability_pointers": {}, "capabilities": {"art_v1": {"artifact_id": "art_v1"}}, "executions": {}}
    pointer = _pointer()
    assert _replay(scope, "capability.activated", {"artifact_id": "art_v1", "pointer": pointer}) is True
    assert scope["capability_pointers"]["lineage_test"]["status"] == "active"
    # 幂等：同事件重放不再变化。
    assert _replay(scope, "capability.activated", {"artifact_id": "art_v1", "pointer": pointer}) is False
    # disabled：指针替换 + 工件删除。
    disabled_pointer = {**_pointer(), "status": "disabled"}
    assert _replay(scope, "capability.disabled", {"artifact_id": "art_v1", "pointer": disabled_pointer}) is True
    assert scope["capability_pointers"]["lineage_test"]["status"] == "disabled"
    assert "art_v1" not in scope["capabilities"]


def test_capability_executed_records_execution_and_recomputes_health() -> None:
    scope = {
        "capability_pointers": {"lineage_test": _pointer("art_v1")},
        "executions": {},
    }
    execution = {
        "execution_id": "caprun_replay1",
        "artifact_id": "art_v1",
        "status": "completed",
        "steps": [{"ok": True}],
    }
    changed = _replay(scope, "capability.executed", {"execution": execution})
    assert changed is True
    assert scope["executions"]["caprun_replay1"]["status"] == "completed"
    health = scope["capability_pointers"]["lineage_test"]["health"]
    assert health["uses"] == 1
    assert health["successes"] == 1
    assert health["success_streak"] == 1
    # 幂等：ingest_outcome 的 seen_outcome_ids 去重，重放不再计数。
    assert _replay(scope, "capability.executed", {"execution": execution}) is False


def test_capability_outcome_uses_idempotency_key_identity() -> None:
    scope = {"capability_pointers": {"lineage_test": _pointer("art_v1")}, "executions": {}}
    event = _event("capability.outcome", {"artifact_id": "art_v1", "outcome": "failure"})
    event["idempotency_key"] = "capability.outcome:outcome_r1"
    assert replay_event(
        scope, event, life_id="life_replay_test", normalize_autonomy_state=normalize_autonomy_state
    ) is True
    health = scope["capability_pointers"]["lineage_test"]["health"]
    assert health["failures"] == 1
    assert health["consecutive_failures"] == 1
    assert health["seen_outcome_ids"] == ["outcome_r1"]


def test_learning_published_rebuilds_card_and_artifact_entry() -> None:
    scope: dict = {"learning": {}, "capabilities": {}, "knowledge": {}}
    artifact = {"artifact_id": "art_learn_1", "kind": "skill", "title": "测试技能"}
    card = {
        "learning_id": "learn_r1",
        "status": "published",
        "target": "skill",
        "execution": {"publication": {"workspace_path": "w/1"}},
    }
    changed = _replay(
        scope, "learning.published", {"learning": card, "artifact": artifact, "report": {}}
    )
    assert changed is True
    assert scope["learning"]["learn_r1"]["status"] == "published"
    entry = scope["capabilities"]["art_learn_1"]
    assert entry["origin"] == "life_learning"
    assert entry["publication"] == {"workspace_path": "w/1"}
    # 幂等。
    assert _replay(scope, "learning.published", {"learning": card, "artifact": artifact, "report": {}}) is False


def test_upgrade_card_lifecycle_replay() -> None:
    scope: dict = {"upgrades": {}}
    card = {"card_id": "upg_r1", "status": "awaiting_user", "execution": {}}
    assert _replay(scope, "upgrade.card_created", {"upgrade": card}) is True
    assert _replay(scope, "upgrade.card_confirmed", {"card_id": "upg_r1"}) is True
    assert scope["upgrades"]["upg_r1"]["status"] == "confirmed"
    assert scope["upgrades"]["upg_r1"]["confirmed_at"]
    assert _replay(
        scope, "upgrade.card_completed", {"card_id": "upg_r1", "execution": {"result": "ok"}}
    ) is True
    assert scope["upgrades"]["upg_r1"]["status"] == "completed"
    assert scope["upgrades"]["upg_r1"]["execution"]["result"] == "ok"
    # confirmed 依赖卡已存在：缺失即 fail-closed。
    with pytest.raises(JournalReplayError) as caught:
        _replay(scope, "upgrade.card_confirmed", {"card_id": "missing_card"})
    assert caught.value.code == "life.projection.upgrade_card_missing"


def test_proactive_and_share_scheduler_keys_and_row_patches() -> None:
    scope: dict = {
        "scheduler": {},
        "proactive_chats": [{"message_id": "msg_r1", "acked": False, "replied": False}],
    }
    assert _replay(
        scope,
        "life.proactive.delivered",
        {"message_id": "msg_r0", "initiative_id": "ini_0", "decision": {}},
    ) is True
    assert int(scope["scheduler"]["last_proactive_delivery_at_ms"]) > 0
    assert _replay(scope, "life.proactive.acked", {"initiative_id": "ini_1", "message_id": "msg_r1"}) is True
    assert scope["proactive_chats"][0]["acked"] is True
    assert _replay(
        scope,
        "life.proactive.replied",
        {"initiative_id": "ini_1", "message_id": "msg_r1", "reply_run_id": "run_9", "reply_link_kind": "user_turn"},
    ) is True
    assert scope["proactive_chats"][0]["replied"] is True
    assert scope["proactive_chats"][0]["reply_run_id"] == "run_9"
    # 行缺失（delivered 行内容不可恢复的已知缺口）时补丁静默跳过。
    assert _replay(scope, "life.proactive.acked", {"initiative_id": "x", "message_id": "msg_nope"}) is False
    assert _replay(scope, "life.share.published", {"message_id": "sh_1", "kind": "daily", "task_count": 0}) is True
    assert scope["scheduler"]["last_share_decision_reason"] == "life.share.published"


# ---------- 3. e2e 崩溃窗口（capability.executed） ----------


def test_capability_executed_projection_recovers_after_persist_failure(tmp_path: Path) -> None:
    from tests.test_life_reflection_chain_wiring import _capability_runtime

    with tempfile.TemporaryDirectory() as temporary:
        life, life_id, artifact = _capability_runtime(
            Path(temporary),
            risk_level="A1",
            invoker=lambda action_id, arguments, ctx: {"ok": True, "zhuangtai": "wancheng"},
        )
        try:
            original_persist = life._persist
            life._persist = mock.Mock(side_effect=OSError("disk-full-after-journal"))
            with pytest.raises(OSError):
                life._capability_invoke({"life_id": life_id, "artifact_id": artifact["artifact_id"]})
            life._persist = original_persist
            life.close()
        finally:
            if not life._closed:
                life.close()
        reopened = EmbeddedLifeRuntime_reopen(Path(temporary))
        try:
            scope = reopened._scope_state(life_id)
            executions = scope.get("executions") or {}
            assert len(executions) == 1
            (execution,) = executions.values()
            assert execution["status"] == "completed"
            pointer = scope["capability_pointers"][artifact["lineage_id"]]
            health = pointer["health"]
            assert health["uses"] == 1
            assert health["successes"] == 1
            assert health["success_streak"] == 1
        finally:
            reopened.close()


def EmbeddedLifeRuntime_reopen(root: Path):
    from life_service.embedded_runtime import EmbeddedLifeRuntime

    return EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "life-runtime",
        mode="embedded",
    )
