from __future__ import annotations

import tempfile
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime


NOW = 1_800_000_000_000


def runtime(root: Path) -> EmbeddedLifeRuntime:
    life = EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "runtime",
        mode="embedded",
    )
    life.scheduler.stop(timeout_seconds=2)
    return life


def initiative_context(life_id: str) -> dict:
    return {
        "schema": "tiangong.life.initiative-context.v1",
        "life_id": life_id,
        "observed_at_ms": NOW,
        "last_user_activity_at_ms": 0,
        "recent_delivery_times_ms": [],
        "observations": [{
            "source_ref": "memory:goal-1",
            "observed_at_ms": NOW - 10_000,
            "confidence_milli": 950,
            "kind": "memory",
            "summary": "用户明确表示今天要完成方案提交。",
        }],
        "affect": {},
        "relationships": {},
        "recent_tasks": [],
    }


def proposal() -> dict:
    return {
        "candidate_kind": "respond",
        "topic": "方案提交",
        "expression_intent": "自然提醒方案提交这个未闭环事项，并询问是否需要继续协助。",
        "evidence_refs": ["memory:goal-1"],
        "score": {
            "goal_gain_milli": 500,
            "viability_gain_milli": 0,
            "information_gain_milli": 120,
            "relationship_value_milli": 180,
            "resource_cost_milli": 20,
            "expected_harm_milli": 10,
            "uncertainty_penalty_milli": 20,
            "irreversibility_penalty_milli": 0,
        },
    }


def configure(life: EmbeddedLifeRuntime, *, mode: str) -> None:
    scope = life._scope_state()
    scope["settings"].update({
        "proactive_enabled": True,
        "proactive_mode": mode,
        "proactive_min_interval_seconds": 0,
        "proactive_max_messages_per_hour": 10,
        "proactive_max_messages_per_day": 10,
        "proactive_respect_user_activity": False,
        "proactive_dnd_enabled": False,
    })


def test_shadow_decision_never_queues_message():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="shadow")
            life.set_proactive_decider(lambda _context: proposal())
            writer_calls: list[dict] = []
            life.set_proactive_expression_writer(
                lambda material: writer_calls.append(dict(material)) or {"text": "不应发送"}
            )
            life_id = str(life._active()["life_id"])
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            assert life._scope_state()["proactive_chats"] == []
            assert writer_calls == []
            assert life._scope_state()["scheduler"]["last_proactive_reason"] == "life.proactive.shadow_eligible"
        finally:
            life.close()


def test_live_native_decision_queues_exactly_one_message_with_lineage():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            life.set_proactive_decider(lambda _context: proposal())
            life.set_proactive_expression_writer(
                lambda _material: {
                    "text": "你今天提过方案要提交，这件事还需要我继续帮你收尾吗？",
                    "conversation_id": "conv-1",
                }
            )
            life_id = str(life._active()["life_id"])
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            rows = life._scope_state()["proactive_chats"]
            assert len(rows) == 1
            row = rows[0]
            assert row["reason"] == "life.proactive.native"
            assert row["candidate_kind"] == "respond"
            assert row["initiative_id"]
            assert row["trigger_event_refs"] == ["memory:goal-1"]
            assert row["conversation_id"] == "conv-1"
            assert row["acked"] is False
            assert row["replied"] is False
        finally:
            life.close()


def test_ack_is_delivery_not_reply_and_next_user_turn_links_reply():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            life.set_proactive_decider(lambda _context: proposal())
            life.set_proactive_expression_writer(lambda _material: {"text": "还要继续处理方案吗？"})
            life_id = str(life._active()["life_id"])
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            row = life._scope_state()["proactive_chats"][0]
            status, payload, _ = life.request(
                "POST",
                "/api/v1/v3/life/proactive-chat/ack",
                {"message_id": row["message_id"]},
            )
            assert status == 200 and payload["found"] is True
            assert row["acked"] is True
            assert row["replied"] is False

            linked = life._mark_latest_proactive_replied(
                life_id=life_id,
                user_activity_at_ms=NOW + 30_000,
                run_id="run-user-after-proactive",
            )
            assert linked is True
            assert row["replied"] is True
            assert row["reply_run_id"] == "run-user-after-proactive"
        finally:
            life.close()


def test_native_proactive_is_only_queue_producer_after_legacy_freeze():
    source = Path(__file__).resolve().parents[1] / "src" / "life_service" / "embedded_runtime.py"
    text = source.read_text(encoding="utf-8")
    assert "def _schedule_greeting" in text
    greeting = text.split("def _schedule_greeting", 1)[1].split("\n    def ", 1)[0]
    assert "proactive_chats" not in greeting
    assert "def _learning_report" in text
    learning_report = text.split("def _learning_report", 1)[1].split("\n    def ", 1)[0]
    assert 'proactive_chats"].append' not in learning_report
    assert text.count('proactive_chats"].append') == 1
