from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from life_service.complete_scheduler import EmbeddedLifeScheduler
from life_service.embedded_runtime import EmbeddedLifeError, EmbeddedLifeRuntime, LifeWriterLease


class EmbeddedLifeLifecycleTests(unittest.TestCase):
    def test_scheduler_preserves_only_machine_readable_error_code(self) -> None:
        class CodedFailure(RuntimeError):
            code = "life.scheduler.synthetic_failure"

        def fail_tick(_reason: str) -> None:
            raise CodedFailure("must not be surfaced")

        scheduler = EmbeddedLifeScheduler(fail_tick, interval_seconds=1.0)
        scheduler.start()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not scheduler.status()["last_error_type"]:
                time.sleep(0.01)
            status = scheduler.status()
            self.assertEqual(status["last_error_type"], "CodedFailure")
            self.assertEqual(status["last_error_code"], "life.scheduler.synthetic_failure")
            self.assertNotIn("must not be surfaced", str(status))
        finally:
            scheduler.stop(timeout_seconds=2)

    def test_startup_reconciles_stale_heartbeat_counter_from_signed_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            runtime_root = root / "life-runtime"
            life = EmbeddedLifeRuntime(data_root=data_root, runtime_root=runtime_root, mode="embedded")
            life_id = str(life._active()["life_id"])
            try:
                life.scheduler.stop(timeout_seconds=2)
                life.system.journal.append(
                    life_id,
                    "life.heartbeat",
                    {"reason": "crash-window", "heartbeat_count": 7},
                    actor="life_scheduler",
                    idempotency_key=f"heartbeat:{life_id}:7",
                )
                life._scope_state(life_id)["scheduler"]["heartbeat_count"] = 3
                life._persist(life_id)
            finally:
                life.close()

            recovered = EmbeddedLifeRuntime(data_root=data_root, runtime_root=runtime_root, mode="embedded")
            try:
                recovered.scheduler.stop(timeout_seconds=2)
                scheduler = recovered._scope_state(life_id)["scheduler"]
                self.assertEqual(scheduler["heartbeat_count"], 7)
                self.assertEqual(
                    scheduler["last_reason"],
                    "life.scheduler.reconciled_from_journal",
                )
                tick = recovered._scheduler_tick("recovery-test")
                self.assertEqual(tick["heartbeat_count"], 8)
            finally:
                recovered.close()

    def test_scheduler_uses_one_affect_decay_idempotency_key_per_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                life.scheduler.stop(timeout_seconds=2)
                life_id = str(life._active()["life_id"])
                affect = {
                    "updated_at_ms": 1_784_822_000_000,
                    "primary_emotion": "hope",
                    "intensity_milli": 500,
                }
                with (
                    mock.patch.object(
                        life,
                        "_decay_transient_affect",
                        return_value=(affect, 30_000, 2),
                    ),
                    mock.patch.object(
                        life,
                        "_advance_memory_lifecycles",
                        return_value={},
                    ),
                    mock.patch.object(
                        life,
                        "_generate_autonomy_tasks",
                        return_value=[],
                    ),
                    mock.patch.object(life, "_sync_daily_summary", return_value=False),
                    mock.patch.object(
                        life,
                        "_schedule_autonomous_activity_decision",
                    ),
                    mock.patch.object(
                        life,
                        "_schedule_autonomous_learning_decision",
                    ),
                ):
                    first = life._scheduler_tick("test")
                    second = life._scheduler_tick("test")
                decay_keys = [
                    str(event.get("idempotency_key") or "")
                    for event in life.system.journal.events(life_id)
                    if str(event.get("event_type") or "") == "affect.decayed"
                ]
                self.assertEqual(first["heartbeat_count"] + 1, second["heartbeat_count"])
                self.assertEqual(len(decay_keys), 2)
                self.assertEqual(len(set(decay_keys)), 2)
                self.assertTrue(decay_keys[0].endswith(f":{first['heartbeat_count']}"))
                self.assertTrue(decay_keys[1].endswith(f":{second['heartbeat_count']}"))
            finally:
                life.close()

    def test_scheduler_stop_fails_closed_while_tick_is_still_running(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocking_tick(_reason: str) -> None:
            entered.set()
            release.wait(timeout=3)

        scheduler = EmbeddedLifeScheduler(blocking_tick, interval_seconds=1.0)
        scheduler.start()
        # Trigger immediately rather than waiting for the production interval.
        worker = threading.Thread(target=lambda: blocking_tick("adversarial"), daemon=True)
        # Replace the tracked worker with a deliberately blocked thread to exercise
        # the stop proof directly without making tests wait one second.
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        scheduler._thread = worker
        try:
            with self.assertRaisesRegex(TimeoutError, "life.scheduler.stop_timeout"):
                scheduler.stop(timeout_seconds=0.01)
            self.assertIs(scheduler._thread, worker)
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(timeout=1)
            scheduler.stop(timeout_seconds=1)

    def test_writer_lease_rejects_symlink_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            data_root.mkdir(parents=True)
            target = root / "outside-authority.txt"
            target.write_text("must-not-change", encoding="utf-8")
            try:
                os.symlink(target, data_root / "life.writer.lock")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaises(EmbeddedLifeError) as caught:
                LifeWriterLease.acquire(data_root, mode="embedded")
            self.assertEqual(caught.exception.code, "life.writer.lock_unsafe")
            self.assertEqual(target.read_text(encoding="utf-8"), "must-not-change")

    def test_partial_initialization_releases_writer_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            runtime_root = root / "life-runtime"
            with mock.patch(
                "life_service.embedded_runtime.LifeShadowStore.open",
                side_effect=RuntimeError("forced-store-open-failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "forced-store-open-failure"):
                    EmbeddedLifeRuntime(
                        data_root=data_root,
                        runtime_root=runtime_root,
                        mode="embedded",
                    )
            lease = LifeWriterLease.acquire(data_root, mode="standalone")
            lease.release()

    def test_unreadable_active_life_is_preserved_and_replaced(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            runtime_root = root / "life-runtime"
            initial = EmbeddedLifeRuntime(data_root=data_root, runtime_root=runtime_root, mode="embedded")
            prior_life_id = str(initial._active()["life_id"])
            initial.close()
            journal = data_root / "lives" / prior_life_id / "journal" / "current" / "life_events.jsonl"
            original = b'{"schema":"unreadable"}\n'
            journal.write_bytes(original)

            # 草案不变量 3：journal schema 混杂/不可读属矛盾的安全事实，启动必须
            # fail-closed，绝不在损坏证据上静默替换身份；原始字节原地保留。
            with self.assertRaises(EmbeddedLifeError) as raised:
                EmbeddedLifeRuntime(data_root=data_root, runtime_root=runtime_root, mode="embedded")
            self.assertIn("journal_schema_mixed", str(raised.exception))
            self.assertEqual(journal.read_bytes(), original)

    def test_v1_unscoped_state_migrates_into_only_the_active_identity(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            runtime_root = root / "life-runtime"
            first = EmbeddedLifeRuntime(
                data_root=data_root,
                runtime_root=runtime_root,
                mode="embedded",
            )
            active_id = str(first._active()["life_id"])
            first.close()
            state_file = runtime_root / "embedded-life-state.json"
            legacy = {
                "schema": "tiangong.life.embedded-state.v1",
                "revision": 7,
                "memories": {
                    "mem_legacy": {
                        "memory_id": "mem_legacy",
                        "memory_type": "semantic",
                        "content": {"text": "legacy-scoped"},
                        "status": "active",
                        "revision": 1,
                    }
                },
                "memory_relations": [],
                "affect": {"valence": 0.4, "arousal": 0.0, "dominance": 0.0},
                "settings": {},
                "inbox": [],
                "proactive_chats": [],
                "capabilities": {},
                "learning": {},
                "upgrades": {},
                "executions": {},
                "scheduler": {},
                "updated_at": "2026-07-21T00:00:00Z",
            }
            state_file.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = EmbeddedLifeRuntime(
                data_root=data_root,
                runtime_root=runtime_root,
                mode="embedded",
            )
            try:
                self.assertEqual(str(migrated._active()["life_id"]), active_id)
                self.assertEqual(migrated._memory_stats()["total"], 1)
                persisted = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertEqual(persisted["schema"], "tiangong.life.embedded-state.v2")
                self.assertIn("mem_legacy", persisted["identity_states"][active_id]["memories"])
            finally:
                migrated.close()

    def test_panel_exposes_authoritative_projection_for_every_life_tab(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                status, panel, _ = life.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel)
                self.assertEqual(panel["projection_status"], "authoritative")
                self.assertEqual(
                    set(panel["sections"]),
                    {
                        "overview", "organism", "memory", "context", "schedule", "will",
                        "reflection", "capabilities", "iteration", "boundaries", "settings",
                    },
                )
                self.assertTrue(all(item["available"] for item in panel["sections"].values()))
                self.assertFalse(panel["sections"]["overview"]["partial"])
                self.assertTrue(panel["sections"]["context"]["partial"])
                self.assertFalse(panel["sections"]["reflection"]["partial"])
                self.assertEqual(panel["schedule"]["mode"], "embedded_autonomy")
                self.assertEqual(panel["body"]["schema"], "tiangong.life.body-state.v1")
                self.assertEqual(panel["relationship"]["source"], "life_state")
                self.assertTrue(panel["budget"]["available"])
                self.assertEqual(panel["budget"]["source"], "embedded_life_model_activity_ledger")
                self.assertEqual(panel["settings"]["source"], "embedded_life_runtime")
                self.assertTrue(panel["settings"]["editable"])
                self.assertIn("permission_mode", panel["boundaries"]["autonomy"])
                self.assertIn("hourly_limit", panel["boundaries"]["share"])
                self.assertTrue(
                    panel["boundaries"]["file_system"]["external_effects_require_gateway_grant"]
                )
                self.assertIn("declared_rules", panel["boundaries"])
                self.assertGreater(len(panel["goals"]), 0)
                self.assertGreater(len(panel["preferences"]["drive_weights"]), 0)
                self.assertEqual(panel["reflections"], [])
                self.assertEqual(panel["action_values"], [])
                self.assertIn("vector_sha256", panel["projection_authority"]["revisions"])
                status, state, _ = life.request("GET", "/api/v1/v3/state", None)
                self.assertEqual(status, 200, state)
                self.assertEqual(state["ui"]["schema"], "tiangong.desktop.ui-projection.v1")
                self.assertTrue(state["ui"]["memory"]["available"])
                self.assertTrue(state["ui"]["affect"]["available"])
                self.assertTrue(state["ui"]["free_will"]["heartbeat_running"])
                self.assertTrue(state["ui"]["operational"]["available"])
                self.assertEqual(state["ui"]["operational"]["memory_total"], 0)
                self.assertEqual(state["ui"]["operational"]["execution_total"], 0)
                self.assertTrue(state["ui"]["operational"]["scheduler"]["running"])
                self.assertIsInstance(panel["context"], dict)
            finally:
                life.close()

    def test_panel_projects_memory_records_status_and_safe_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                status, asserted, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                    {
                        "memory_id": "mem_panel_projection",
                        "memory_type": "episodic",
                        "content": {
                            "conversation": {
                                "user": "请记住 token=secret-value-123456",
                                "assistant": "已经记录这条测试记忆。",
                            }
                        },
                    },
                )
                self.assertEqual(status, 200, asserted)
                status, panel, _ = life.request(
                    "GET", "/api/v1/v3/life/panel", None
                )
                self.assertEqual(status, 200, panel)
                memory = panel["memory"]
                self.assertEqual(memory["total"], 1)
                self.assertEqual(memory["by_status"], {"active": 1})
                self.assertEqual(memory["by_type"], {"episodic": 1})
                projected = memory["records"]["mem_panel_projection"]
                self.assertEqual(projected["status"], "active")
                self.assertIn("[redacted]", projected["content_preview"])
                self.assertNotIn("secret-value-123456", projected["content_preview"])
                self.assertNotIn("content", projected)
            finally:
                life.close()

    def test_legacy_schedule_relationship_and_body_are_fused_into_the_authoritative_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(data_root=root / "life-data", runtime_root=root / "life-runtime", mode="embedded")
            try:
                status, updated, _ = life.request(
                    "POST", "/api/v1/v3/life/settings",
                    {
                        "settings": {"legacy_fusion_marker": True},
                        "schedule": {"date": time.strftime("%Y-%m-%d", time.gmtime()), "mode": "legacy_daily_plan", "summary": "迁移日计划", "tasks": {"daily": {"id": "daily", "title": "整理记忆", "window": "09:00-09:30"}}},
                        "body": {"profile": {"body_preset": "desktop"}, "signals": {"energy_milli": 720, "load_milli": 120, "availability": "active"}},
                    },
                )
                self.assertEqual(status, 200, updated)
                status, affect, _ = life.request("POST", "/api/v1/v3/life/affect/appraise", {"relationship_id": "user:primary", "valence": 0.6})
                self.assertEqual(status, 200, affect)
                status, panel, _ = life.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel)
                self.assertEqual(panel["schedule"]["mode"], "legacy_daily_plan")
                self.assertEqual(panel["schedule"]["tasks"][0]["title"], "整理记忆")
                self.assertEqual(panel["body"]["signals"]["energy_milli"], 720)
                self.assertEqual(panel["relationship"]["by_id"]["user:primary"]["source"], "affect_appraisal")
            finally:
                life.close()

    def test_completed_day_projects_one_persistent_daily_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                now_ms = int(time.time() * 1000)
                day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
                task = {
                    "task_id": "lat_projection_complete",
                    "source": "life_activity_catalog",
                    "activity_id": "goal_progress",
                    "task_kind": "goal_progress",
                    "title": "推进长期目标",
                    "objective": "验证长期目标投影",
                    "status": "completed",
                    "sequence": 1,
                    "risk_class": "A0",
                    "causal_basis": [f"day={day}"],
                    "created_at_ms": now_ms,
                    "updated_at_ms": now_ms,
                    "result": {
                        "summary": "已核对长期目标，并确认下一步应保持安全边界。",
                        "next_step": "下一轮继续验证目标一致性。",
                    },
                }
                old_task = {
                    **task,
                    "task_id": "lat_projection_yesterday",
                    "title": "昨天的行动",
                    "created_at_ms": now_ms - 86_400_000,
                    "updated_at_ms": now_ms - 86_400_000,
                    "causal_basis": ["day=2000-01-01"],
                }
                life._scope_state()["settings"].update(
                    {
                        "share_quiet_if_user_active": False,
                        "share_dnd_start": "00:00",
                        "share_dnd_end": "00:00",
                    }
                )
                life._scope_state()["autonomy"]["tasks"].update(
                    {
                        old_task["task_id"]: old_task,
                        task["task_id"]: task,
                    }
                )
                self.assertTrue(life._sync_daily_summary(str(life._active()["life_id"])))
                life._persist()

                status, panel, _ = life.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel)
                self.assertNotIn(
                    old_task["task_id"],
                    {row["task_id"] for row in panel["tasks"]},
                )
                self.assertTrue(
                    any(row["task_id"] == task["task_id"] and row["status"] == "completed"
                        for row in panel["schedule"]["tasks"])
                )
                self.assertEqual(panel["reflections"][0]["task_id"], task["task_id"])
                self.assertEqual(panel["action_values"][0]["action_id"], task["task_id"])
                self.assertGreater(panel["action_values"][0]["total_score"], 0)
                self.assertEqual(
                    panel["free_will"]["recent_autonomous_actions"][0]["task_id"],
                    task["task_id"],
                )
                summary_id = f"daily-summary:{day}"
                self.assertEqual(len(panel["inbox"]["items"]), 1)
                summary = panel["inbox"]["items"][0]
                self.assertEqual(summary["message_id"], summary_id)
                self.assertEqual(summary["kind"], "daily_life_summary")
                self.assertEqual(summary["title"], "今日生命总结")
                self.assertIn("推进长期目标", summary["message"])
                status, activity, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/activity/query",
                    {"date": day},
                )
                self.assertEqual(status, 200, activity)
                self.assertEqual(
                    [row["message_id"] for row in activity["daily_summaries"]],
                    [summary_id],
                )
                self.assertNotIn(
                    f"autonomy:{task['task_id']}",
                    {row["message_id"] for row in panel["inbox"]["items"]},
                )

                # Read state is persisted by the authoritative mailbox.
                status, read_result, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/inbox/read",
                    {"message_id": summary_id},
                )
                self.assertEqual(status, 200, read_result)
                self.assertTrue(read_result["found"])
                status, refreshed, _ = life.request(
                    "GET", "/api/v1/v3/life/panel", None
                )
                self.assertEqual(status, 200, refreshed)
                refreshed_message = next(
                    row for row in refreshed["inbox"]["items"]
                    if row["message_id"] == summary_id
                )
                self.assertTrue(refreshed_message["read"])

                # Deletion records a tombstone. Repeated projection refreshes
                # must never recreate the deterministic daily summary.
                status, delete_result, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/inbox/delete",
                    {"message_id": summary_id},
                )
                self.assertEqual(status, 200, delete_result)
                self.assertTrue(delete_result["found"])
                for _ in range(3):
                    status, refreshed, _ = life.request(
                        "GET", "/api/v1/v3/life/panel", None
                    )
                    self.assertEqual(status, 200, refreshed)
                    self.assertNotIn(
                        summary_id,
                        {row["message_id"] for row in refreshed["inbox"]["items"]},
                    )
                self.assertIn(
                    summary_id,
                    life._scope_state()["inbox_tombstones"],
                )
            finally:
                life.close()

    def test_legacy_identity_mailbox_is_not_migrated_to_daily_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                scope = life._scope_state()
                scope["inbox_contract_version"] = 1
                scope["inbox"] = [
                    {
                        "message_id": "legacy-message",
                        "title": "旧消息",
                        "message": "保持原样",
                        "read": True,
                        "created_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                    }
                ]
                now_ms = int(time.time() * 1000)
                day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
                scope["autonomy"]["tasks"]["lat_legacy_complete"] = {
                    "task_id": "lat_legacy_complete",
                    "source": "life_activity_catalog",
                    "activity_id": "goal_progress",
                    "task_kind": "goal_progress",
                    "title": "旧生命任务",
                    "status": "completed",
                    "causal_basis": [f"day={day}"],
                    "created_at_ms": now_ms,
                    "updated_at_ms": now_ms,
                    "result": {"summary": "旧生命结果"},
                }
                status, panel, _ = life.request(
                    "GET", "/api/v1/v3/life/panel", None
                )
                self.assertEqual(status, 200, panel)
                self.assertEqual(
                    [row["message_id"] for row in panel["inbox"]["items"]],
                    ["legacy-message"],
                )
            finally:
                life.close()

    def test_settings_accept_chinese_choices_and_project_typed_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                status, result, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/settings",
                    {
                        "settings": {
                            "permission_mode": "高风险操作需确认",
                            "autonomous_risk_max": "A3",
                            "llm_daily_budget": 8,
                            "llm_daily_attempt_budget": 12,
                            "share_hourly_limit": 2,
                            "share_daily_limit": 7,
                            "share_dnd_start": "22:30",
                            "share_dnd_end": "07:30",
                        }
                    },
                )
                self.assertEqual(status, 200, result)
                status, panel, _ = life.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel)
                self.assertEqual(panel["settings"]["permission_mode"], "confirm_high_risk")
                self.assertEqual(panel["settings"]["autonomous_risk_max"], "A3")
                for key in (
                    "autonomy_enabled",
                    "autonomy_task_generation_enabled",
                    "autonomy_activity_types",
                    "heartbeat_enabled",
                    "llm_daily_budget",
                    "llm_daily_attempt_budget",
                    "share_enabled",
                    "share_quiet_if_user_active",
                    "share_min_interval_seconds",
                    "share_hourly_limit",
                    "share_daily_limit",
                    "share_dnd_start",
                    "share_dnd_end",
                    "privacy",
                ):
                    self.assertIn(key, panel["settings"])
                self.assertIsInstance(panel["settings"]["llm_daily_budget"], int)
                self.assertIsInstance(panel["settings"]["llm_daily_attempt_budget"], int)
                self.assertIsInstance(panel["settings"]["privacy"]["redact_llm"], bool)
                self.assertIsInstance(panel["settings"]["privacy"]["redact_share"], bool)
                self.assertEqual(panel["budget"]["success_limit"], 8)
                self.assertEqual(panel["budget"]["attempt_limit"], 12)
                self.assertEqual(panel["boundaries"]["share"]["hourly_limit"], 2)
                self.assertEqual(panel["boundaries"]["share"]["dnd_start"], "22:30")
            finally:
                life.close()

    def test_settings_persist_across_runtime_restart_and_refresh_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "life-data"
            runtime_root = root / "life-runtime"
            life = EmbeddedLifeRuntime(
                data_root=data_root,
                runtime_root=runtime_root,
                mode="embedded",
            )
            try:
                status, result, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/settings",
                    {
                        "settings": {
                            "permission_mode": "confirm_all",
                            "autonomous_risk_max": "A2",
                            "autonomy_enabled": False,
                            "autonomy_task_generation_enabled": False,
                            "autonomy_activity_types": ["self_reflection", "knowledge_organization"],
                            "heartbeat_enabled": False,
                            "llm_daily_budget": 3,
                            "llm_daily_attempt_budget": 5,
                            "share_enabled": True,
                            "share_quiet_if_user_active": True,
                            "share_min_interval_seconds": 3600,
                            "share_hourly_limit": 2,
                            "share_daily_limit": 6,
                            "share_dnd_start": "21:15",
                            "share_dnd_end": "06:45",
                            "privacy": {"redact_llm": False, "redact_share": True},
                        }
                    },
                )
                self.assertEqual(status, 200, result)
            finally:
                life.close()

            recovered = EmbeddedLifeRuntime(
                data_root=data_root,
                runtime_root=runtime_root,
                mode="embedded",
            )
            try:
                status, panel, _ = recovered.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel)
                settings = panel["settings"]
                self.assertEqual(settings["permission_mode"], "confirm_all")
                self.assertEqual(settings["autonomous_risk_max"], "A2")
                self.assertFalse(settings["autonomy_enabled"])
                self.assertFalse(settings["autonomy_task_generation_enabled"])
                self.assertEqual(settings["autonomy_activity_types"], ["self_reflection", "knowledge_organization"])
                self.assertFalse(settings["heartbeat_enabled"])
                self.assertEqual(settings["llm_daily_budget"], 3)
                self.assertEqual(settings["llm_daily_attempt_budget"], 5)
                self.assertTrue(settings["share_enabled"])
                self.assertTrue(settings["share_quiet_if_user_active"])
                self.assertEqual(settings["share_min_interval_seconds"], 3600)
                self.assertEqual(settings["share_hourly_limit"], 2)
                self.assertEqual(settings["share_daily_limit"], 6)
                self.assertEqual(settings["share_dnd_start"], "21:15")
                self.assertEqual(settings["share_dnd_end"], "06:45")
                self.assertEqual(settings["privacy"], {"redact_llm": False, "redact_share": True})
                self.assertEqual(panel["budget"]["success_limit"], 3)
                self.assertEqual(panel["budget"]["attempt_limit"], 5)
                self.assertEqual(panel["boundaries"]["share"]["hourly_limit"], 2)
                self.assertEqual(panel["boundaries"]["share"]["dnd_start"], "21:15")
            finally:
                recovered.close()

    def test_runtime_settings_control_heartbeat_generation_and_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                life.scheduler.stop(timeout_seconds=2)
                life_id = str(life._active()["life_id"])
                scope = life._scope_state(life_id)
                scope["settings"]["heartbeat_enabled"] = False
                before = int(scope["scheduler"].get("heartbeat_count") or 0)
                tick = life._scheduler_tick("settings-test")
                self.assertEqual(tick["reason_code"], "life.scheduler.disabled")
                self.assertEqual(tick["heartbeat_count"], before)
                self.assertEqual(scope["scheduler"].get("heartbeat_count", 0), before)

                scope["settings"]["autonomy_task_generation_enabled"] = False
                self.assertEqual(
                    life._generate_autonomy_tasks(
                        life_id=life_id,
                        reason="settings-test",
                    ),
                    [],
                )
                self.assertEqual(scope["autonomy"]["tasks"], {})

                now_ms = int(time.time() * 1000)
                scope["autonomy"]["tasks"]["lat_risk_a1"] = {
                    "task_id": "lat_risk_a1",
                    "source": "life_activity_catalog",
                    "activity_id": "goal_progress",
                    "status": "pending",
                    "sequence": 1,
                    "priority": 100,
                    "risk_class": "A1",
                    "created_at_ms": now_ms,
                    "updated_at_ms": now_ms,
                    "task_sha256": "",
                }
                scope["settings"].update(
                    {
                        "autonomy_enabled": True,
                        "permission_mode": "confirm_all",
                        "autonomous_risk_max": "A4",
                    }
                )
                life.set_autonomy_decider(lambda _scope, _task: {"summary": "不应执行"})
                life._schedule_autonomous_activity_decision(life_id=life_id)
                self.assertEqual(
                    scope["autonomy"]["tasks"]["lat_risk_a1"]["status"],
                    "pending",
                )
                self.assertEqual(
                    scope["scheduler"]["last_autonomy_decision_error"],
                    "life.autonomy.user_confirmation_required",
                )
                scope["settings"].update(
                    {
                        "permission_mode": "confirm_high_risk",
                        "autonomous_risk_max": "A0",
                    }
                )
                life._schedule_autonomous_activity_decision(life_id=life_id)
                self.assertEqual(
                    scope["autonomy"]["tasks"]["lat_risk_a1"]["status"],
                    "pending",
                )
            finally:
                life.close()

    def test_share_and_privacy_settings_have_real_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                life_id = str(life._active()["life_id"])
                scope = life._scope_state(life_id)
                now_ms = int(time.time() * 1000)
                day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
                scope["autonomy"]["tasks"]["lat_share_complete"] = {
                    "task_id": "lat_share_complete",
                    "source": "life_activity_catalog",
                    "activity_id": "goal_progress",
                    "title": "隐私总结验证",
                    "status": "completed",
                    "sequence": 1,
                    "risk_class": "A0",
                    "causal_basis": [f"day={day}"],
                    "created_at_ms": now_ms,
                    "updated_at_ms": now_ms,
                    "result": {"summary": "已使用 token=private-value-123456 完成验证"},
                }
                scope["settings"].update(
                    {
                        "share_enabled": False,
                        "share_quiet_if_user_active": False,
                        "share_dnd_start": "00:00",
                        "share_dnd_end": "00:00",
                    }
                )
                self.assertFalse(life._sync_daily_summary(life_id, now_ms=now_ms))
                self.assertEqual(
                    scope["scheduler"]["last_share_decision_reason"],
                    "life.share.disabled",
                )
                self.assertEqual(scope["inbox"], [])

                status, updated, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/settings",
                    {
                        "settings": {
                            "share_enabled": True,
                            "privacy": {"redact_share": True},
                        }
                    },
                )
                self.assertEqual(status, 200, updated)
                self.assertTrue(updated["settings"]["privacy"]["redact_llm"])
                self.assertTrue(updated["settings"]["privacy"]["redact_share"])
                summary = next(
                    row for row in scope["inbox"]
                    if row["message_id"] == f"daily-summary:{day}"
                )
                self.assertIn("[已脱敏]", summary["message"])
                self.assertNotIn("private-value-123456", summary["message"])

                status, asserted, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                    {
                        "memory_id": "mem_redact_llm",
                        "content": {"text": "api_key=private-memory-123456"},
                    },
                )
                self.assertEqual(status, 200, asserted)
                memory_item = next(
                    item for item in life._external_memory_items()
                    if item.item_ref == "mem_redact_llm"
                )
                self.assertIn("[已脱敏]", memory_item.summary)
                self.assertNotIn("private-memory-123456", memory_item.summary)
                life_item = next(
                    item for item in life._external_memory_items()
                    if item.item_ref.startswith("life_activity_")
                )
                self.assertIn("隐私总结验证", life_item.summary)
                self.assertIn('"status":"completed"', life_item.summary)
                self.assertIn('"window_minutes":30', life_item.summary)
                self.assertIn('"history_included":false', life_item.summary)
                self.assertIn("[已脱敏]", life_item.summary)
                self.assertNotIn("private-value-123456", life_item.summary)

                status, activity, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/activity/query",
                    {"relative_day": "today", "limit": 10},
                )
                self.assertEqual(status, 200, activity)
                self.assertTrue(activity["read_only"])
                self.assertEqual(activity["date"], day)
                self.assertEqual(activity["relative_day"], "today")
                self.assertTrue(
                    any(row["title"] == "隐私总结验证" for row in activity["activities"])
                )
                encoded_activity = json.dumps(activity, ensure_ascii=False)
                self.assertIn("[已脱敏]", encoded_activity)
                self.assertNotIn("private-value-123456", encoded_activity)
            finally:
                life.close()

    def test_identity_scopes_isolate_memory_affect_settings_and_executions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                first_id = str(life._active()["life_id"])
                status, first_memory, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                    {"memory_id": "mem_identity_a", "content": {"text": "identity-a-only"}},
                )
                self.assertEqual(status, 200, first_memory)
                status, _affect, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/affect/appraise",
                    {"valence": 0.8},
                )
                self.assertEqual(status, 200)
                status, _settings, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/settings",
                    {"settings": {"identity_marker": "A"}},
                )
                self.assertEqual(status, 200)

                status, created, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/identity/create",
                    {"name": "隔离身份B"},
                )
                self.assertEqual(status, 200, created)
                second_id = str(created["identity"]["life_id"])
                if str(life._active()["life_id"]) != second_id:
                    status, activated, _ = life.request(
                        "POST",
                        "/api/v1/v3/life/identity/activate",
                        {"life_id": second_id},
                    )
                    self.assertEqual(status, 200, activated)
                status, stats_b, _ = life.request("GET", "/api/v1/v3/life/memory/stats", None)
                self.assertEqual(status, 200, stats_b)
                self.assertEqual(stats_b["total"], 0)
                status, affect_b, _ = life.request("GET", "/api/v1/v3/life/affect", None)
                self.assertEqual(status, 200, affect_b)
                self.assertEqual(affect_b["state"]["source"], "innate_temperament")
                self.assertEqual(
                    affect_b["state"]["valence"],
                    life._temperament_projection(second_id)[
                        "current_affective_disposition"
                    ]["valence_set_point"],
                )
                status, panel_b, _ = life.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel_b)
                self.assertNotIn("identity_marker", panel_b["settings"])
                self.assertEqual(panel_b["chat_gate"]["schema"], "tiangong.life.chat-gate.v1")
                self.assertTrue(panel_b["chat_gate"]["ready"])
                self.assertTrue(panel_b["chat_gate"]["available"])
                self.assertEqual(panel_b["identity"]["life_id"], second_id)
                identities_b = {row["life_id"]: row for row in panel_b["identities"]}
                self.assertEqual(set(identities_b), {first_id, second_id})
                self.assertEqual(identities_b[second_id]["status"], "active")
                self.assertEqual(identities_b[first_id]["status"], "dormant")
                self.assertTrue(identities_b[second_id]["active"])
                self.assertFalse(identities_b[first_id]["active"])
                self.assertEqual(identities_b[second_id]["integrity"], "valid")
                self.assertTrue(identities_b[second_id]["root"])
                self.assertTrue(identities_b[second_id]["soul_intro"])

                status, activated_a, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/identity/activate",
                    {"life_id": first_id},
                )
                self.assertEqual(status, 200, activated_a)
                status, search_a, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/memory/search",
                    {"query": "identity-a-only"},
                )
                self.assertEqual(status, 200, search_a)
                self.assertEqual([row["memory_id"] for row in search_a["results"]], ["mem_identity_a"])
                status, affect_a, _ = life.request("GET", "/api/v1/v3/life/affect", None)
                self.assertEqual(status, 200, affect_a)
                self.assertEqual(affect_a["state"]["valence"], 0.8)
                status, panel_a, _ = life.request("GET", "/api/v1/v3/life/panel", None)
                self.assertEqual(status, 200, panel_a)
                self.assertEqual(panel_a["settings"]["identity_marker"], "A")
                self.assertEqual(panel_a["identity"]["life_id"], first_id)
                identities_a = {row["life_id"]: row for row in panel_a["identities"]}
                self.assertEqual(set(identities_a), {first_id, second_id})
                self.assertEqual(identities_a[first_id]["status"], "active")
                self.assertEqual(identities_a[second_id]["status"], "dormant")
                self.assertTrue(identities_a[first_id]["active"])
                self.assertFalse(identities_a[second_id]["active"])
                self.assertEqual(identities_a[first_id]["integrity"], "valid")
                self.assertTrue(identities_a[first_id]["soul_intro"])
            finally:
                life.close()

    def test_execution_commit_rejects_missing_or_malformed_idempotency_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                valid = {
                    "schema": "tiangong.life.execution-terminal.v1",
                    "request_id": "req_lifecycle_validation",
                    "run_id": "run_lifecycle_validation",
                    "generation": 1,
                    "life_id": str(life._active()["life_id"]),
                    "session_scope_hash": "a" * 64,
                    "status": "completed",
                    "user_goal_sha256": "b" * 64,
                    "final_result_sha256": "c" * 64,
                    "fact_ids": [],
                    "completed_at_ms": 1,
                }
                for mutation, code in (
                    ({"request_id": ""}, "life.execution.request_id_invalid"),
                    ({"request_id": "../escape"}, "life.execution.request_id_invalid"),
                    ({"generation": True}, "life.execution.generation_invalid"),
                    ({"session_scope_hash": "A" * 64}, "life.execution.session_scope_hash_invalid"),
                    ({"fact_ids": ["../fact"]}, "life.execution.fact_id_invalid"),
                    ({"life_id": "life-other"}, "life.execution.life_id_mismatch"),
                ):
                    with self.subTest(code=code), self.assertRaises(EmbeddedLifeError) as caught:
                        life.commit_execution({**valid, **mutation})
                    self.assertEqual(caught.exception.code, code)
                committed = life.commit_execution(valid)
                self.assertTrue(committed["ok"])
                status, state, _ = life.request("GET", "/api/v1/v3/state", None)
                self.assertEqual(status, 200, state)
                operational = state["ui"]["operational"]
                self.assertEqual(operational["execution_total"], 1)
                self.assertEqual(operational["completed_execution_count"], 1)
                self.assertEqual(operational["failed_execution_count"], 0)
                self.assertEqual(
                    operational["latest_execution"]["request_id"],
                    valid["request_id"],
                )
            finally:
                life.close()

    def test_identity_delete_removes_dormant_tree_and_rejects_active_life(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                first = dict(life._active())
                first_root = Path(str(first["root"]))
                status, created, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/identity/create",
                    {"name": "second"},
                )
                self.assertEqual(status, 200, created)
                second_id = str(created["identity"]["life_id"])
                self.assertNotEqual(first["life_id"], second_id)

                status, deleted, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/identity/delete",
                    {"life_id": first["life_id"]},
                )
                self.assertEqual(status, 200, deleted)
                self.assertTrue(deleted["deleted"])
                self.assertTrue(deleted["files_removed"])
                self.assertEqual(deleted["audit"]["action"], "identity.deleted")
                self.assertEqual(deleted["audit"]["life_id"], first["life_id"])
                self.assertFalse(first_root.exists())

                status, audit, _ = life.request(
                    "GET",
                    "/api/v1/v3/life/identity/audit",
                    None,
                )
                self.assertEqual(status, 200, audit)
                self.assertTrue(
                    any(
                        entry.get("action") == "identity.deleted"
                        and entry.get("life_id") == first["life_id"]
                        for entry in audit["events"]
                    ),
                    audit,
                )

                status, panel, _ = life.request(
                    "GET",
                    "/api/v1/v3/life/panel",
                    None,
                )
                self.assertEqual(status, 200, panel)
                self.assertEqual(
                    [row["life_id"] for row in panel["identities"]],
                    [second_id],
                )

                status, rejected, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/identity/delete",
                    {"life_id": second_id},
                )
                self.assertEqual(status, 409, rejected)
                self.assertEqual(
                    rejected["reason_code"],
                    "active_life_delete_forbidden",
                )
            finally:
                life.close()


if __name__ == "__main__":
    unittest.main()
