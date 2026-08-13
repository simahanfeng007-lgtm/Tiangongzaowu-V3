from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest import mock

from life_service.embedded_runtime import EmbeddedLifeRuntime


NOW = int(time.time() * 1000)


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



def test_relationship_projection_is_bounded_and_does_not_leak_raw_text():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            scope = life._scope_state(life_id)
            scope["relationships"] = {
                f"person-{index}": {
                    "target_life_id": f"target-{index}",
                    "direction": "outbound",
                    "trust_milli": 700,
                    "familiarity_milli": 500,
                    "obligations": [f"SECRET-OBLIGATION-{index}"],
                    "promises": [f"SECRET-PROMISE-{index}"],
                    "relationship_tags": [f"SECRET-TAG-{index}"],
                }
                for index in range(24)
            }
            rows = life._project_proactive_relationships(life_id=life_id)
            assert len(rows) == 16
            serialized = repr(rows)
            assert "SECRET-" not in serialized
            assert all("relationship_ref" in row for row in rows)
            assert all(row["obligation_count"] == 1 for row in rows)
        finally:
            life.close()


def test_world_projection_accepts_only_committed_wu_snapshot_shape():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            life.set_world_identity_provider(lambda life_id: {"life_id": life_id, "principal_scope_hash": "p" * 64, "workspace_id": "workspace-test"})
            life.set_proactive_world_provider(lambda _identity: {"schema": "untrusted", "observed_at_ms": NOW})
            assert life._proactive_world_observations(life_id=life_id, now_ms=NOW) == []

            life.set_proactive_world_provider(lambda _identity: {
                "schema": "tiangong.life.repository-evidence.v1",
                "frame_id": "frame-1",
                "frame_revision_hash": "a" * 64,
                "observed_at_ms": NOW - 1_000,
                "branch": "main",
                "commit": "b" * 40,
                "entity_refs": [{"record_id": "file:1", "sha256": "c" * 64}],
            })
            rows = life._proactive_world_observations(life_id=life_id, now_ms=NOW)
            assert len(rows) == 1
            assert rows[0]["authority"] == "world_understanding_committed"
            assert rows[0]["kind"] == "world:repository_evidence"
        finally:
            life.close()


def test_live_turn_counts_decision_and_expression_as_two_model_calls():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            scope = life._scope_state()
            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 30, "proactive_llm_daily_budget": 6, "proactive_llm_daily_attempt_budget": 8})
            scheduler = scope["scheduler"]
            scheduler.update({
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_failures": 0,
                "model_skipped": 0,
            })
            life.set_proactive_decider(lambda _context: proposal())
            life.set_proactive_expression_writer(lambda _material: {"text": "继续处理方案吗？"})
            life_id = str(life._active()["life_id"])
            # Simulate the scheduler's pre-call reservation for decision LLM #1.
            assert life._reserve_proactive_model_call_locked(
                scheduler=scheduler, settings=scope["settings"]
            ) is True
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            assert scheduler["model_attempts"] == 2
            assert scheduler["model_successes"] == 2
            assert scheduler["model_failures"] == 0
            assert scheduler["proactive_model_attempts"] == 2
            assert scheduler["proactive_model_successes"] == 2
        finally:
            life.close()


def test_expression_call_is_not_made_when_second_model_budget_is_exhausted():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            scope = life._scope_state()
            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 1, "proactive_llm_daily_budget": 6, "proactive_llm_daily_attempt_budget": 8})
            scheduler = scope["scheduler"]
            scheduler.update({
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_failures": 0,
                "model_skipped": 0,
            })
            life.set_proactive_decider(lambda _context: proposal())
            writer_calls: list[dict] = []
            life.set_proactive_expression_writer(lambda material: writer_calls.append(dict(material)) or {"text": "x"})
            life_id = str(life._active()["life_id"])
            assert life._reserve_proactive_model_call_locked(
                scheduler=scheduler, settings=scope["settings"]
            ) is True
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            assert writer_calls == []
            assert scheduler["model_attempts"] == 1
            assert scheduler["model_successes"] == 1
            assert scheduler["model_skipped"] == 1
            assert scheduler["last_proactive_reason"] == "life.proactive.expression_budget_exhausted"
            assert scope["proactive_chats"] == []
        finally:
            life.close()



def test_gateway_wires_proactive_world_to_existing_committed_wu_reader():
    source = Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "runtime.py"
    gateway = source.read_text(encoding="utf-8")
    assert "set_proactive_world_provider" in gateway
    assert "runtime.backend_service.repository_evidence_snapshot" in gateway
    backend = (Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "embedded_backend.py").read_text(encoding="utf-8")
    reader = backend.split("def repository_evidence_snapshot", 1)[1].split("\n    def ", 1)[0]
    assert "production_repository_evidence_snapshot" in reader
    assert "world_understanding_production" in reader



def test_proactive_context_uses_p15_layered_memory_authority_only():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            # A legacy row must never be a fallback source for P16 cognition.
            life._scope_state(life_id)["memories"] = {
                "legacy-secret": {
                    "status": "active",
                    "memory_type": "goal",
                    "created_at_ms": NOW - 1_000,
                    "confidence_milli": 1000,
                    "content": "LEGACY-MEMORY-MUST-NOT-LEAK",
                }
            }
            with mock.patch(
                "life_service.embedded_runtime.select_layered_memories",
                return_value=((), (), (), 0),
            ) as selector:
                context = life._build_proactive_context(life_id=life_id, now_ms=NOW)
            selector.assert_called_once_with(
                life._contract_store(),
                life_id=life_id,
                principal_ref=life_id,
                privacy_scope="private",
                now_ms=NOW,
                limit=24,
            )
            assert "LEGACY-MEMORY-MUST-NOT-LEAK" not in repr(context)
            source = (Path(__file__).resolve().parents[1] / "src" / "life_service" / "embedded_runtime.py").read_text(encoding="utf-8")
            block = source.split("def _build_proactive_context", 1)[1].split("\n    def ", 1)[0]
            assert 'scope.get("memories")' not in block
            assert "select_layered_memories(" in block
        finally:
            life.close()


def test_context_failure_does_not_spend_model_budget_or_break_scheduler():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="shadow")
            life.set_proactive_decider(lambda _context: proposal())
            life_id = str(life._active()["life_id"])
            scheduler = life._scope_state(life_id)["scheduler"]
            before = int(scheduler.get("model_attempts") or 0)
            with mock.patch.object(life, "_build_proactive_context", side_effect=RuntimeError("context failed")):
                life._schedule_native_proactive(life_id=life_id)
            assert int(scheduler.get("model_attempts") or 0) == before
            assert int(scheduler.get("proactive_model_attempts") or 0) == 0
            assert scheduler["proactive_decision_inflight"] is False
            assert scheduler["last_proactive_reason"] == "life.proactive.context_unavailable"
        finally:
            life.close()


def test_proactive_subbudget_prevents_shadow_from_starving_global_pool():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            scope = life._scope_state()
            scope["settings"].update({
                "llm_daily_budget": 20,
                "llm_daily_attempt_budget": 30,
                "proactive_llm_daily_budget": 6,
                "proactive_llm_daily_attempt_budget": 1,
            })
            scheduler = scope["scheduler"]
            scheduler.update({
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_skipped": 0,
                "proactive_model_budget_date": "",
                "proactive_model_attempts": 0,
                "proactive_model_successes": 0,
                "proactive_model_skipped": 0,
            })
            assert life._reserve_proactive_model_call_locked(scheduler=scheduler, settings=scope["settings"]) is True
            assert life._reserve_proactive_model_call_locked(scheduler=scheduler, settings=scope["settings"]) is False
            assert scheduler["model_attempts"] == 1
            assert scheduler["proactive_model_attempts"] == 1
            assert scheduler["proactive_model_skipped"] == 1
        finally:
            life.close()


def test_reply_lineage_expires_after_bounded_temporal_window():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            scope = life._scope_state()
            scope["settings"]["proactive_reply_link_window_seconds"] = 3600
            life.set_proactive_decider(lambda _context: proposal())
            life.set_proactive_expression_writer(lambda _material: {"text": "还需要继续吗？"})
            life_id = str(life._active()["life_id"])
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            row = scope["proactive_chats"][0]
            row["created_at_ms"] = NOW - 3_600_001
            linked = life._mark_latest_proactive_replied(
                life_id=life_id,
                user_activity_at_ms=NOW,
                run_id="run-too-late",
            )
            assert linked is False
            assert row["replied"] is False
        finally:
            life.close()
