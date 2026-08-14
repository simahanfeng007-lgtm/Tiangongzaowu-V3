"""P17.1 production runtime integrity hardening regression.

对抗性回归：锁定 P17.1 修复的运行语义不变量——
两阶段停止、确认链退役、Run scope 绑定、PID 复用防护、
长任务完整恢复、Windows 保留文件名、设置权威原子写与损坏显式化。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from v3 import duihua_qiaojie as bridge
from v3 import workspace_settings


def _isolated_run_state(func):
    def wrapper(self, *args, **kwargs):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"TIANGONG_RUN_STATE_DIR": temporary}, clear=False
        ):
            return func(self, temporary, *args, **kwargs)

    return wrapper


class TwoPhaseStopTests(unittest.TestCase):
    @_isolated_run_state
    def test_stop_on_executing_run_requests_then_acknowledges(self, state_dir):
        manager = bridge.RunControlManager()
        handle, disposition, _ = manager.claim("agent-run:stop-a", "执行一个长任务")
        self.assertEqual(disposition, "started")
        result = manager.stop(handle.request_id)
        # 两阶段：指令已记录，但执行器尚未确认——不得宣称已停止。
        self.assertTrue(result["ok"])
        self.assertTrue(result["cancel_requested"])
        self.assertFalse(result["interrupted"])
        self.assertFalse(result["canceled"])
        persisted = bridge.load_run_snapshot(handle.request_id)
        self.assertEqual(persisted["phase"], "cancel_requested")
        self.assertTrue(persisted["stop_requested"])
        # 执行器在检查点确认退出后才落权威终态。
        manager.finish(handle.request_id, False, "用户停止")
        persisted = bridge.load_run_snapshot(handle.request_id)
        self.assertEqual(persisted["phase"], "interrupted")
        self.assertIn("已停止", persisted["final_response"])

    @_isolated_run_state
    def test_stop_without_executor_finalizes_immediately(self, state_dir):
        manager = bridge.RunControlManager()
        handle, disposition, _ = manager.claim("agent-run:stop-b", "任务")
        persisted = bridge.load_run_snapshot(handle.request_id)
        # 模拟崩溃后恢复：owner 已死（不可能存在的 PID）。
        persisted["owner_pid"] = 9999999
        persisted["executing"] = True
        bridge._save_run_snapshot_unlocked(persisted)
        manager._runs[handle.request_id] = persisted
        result = manager.stop(handle.request_id)
        self.assertTrue(result["ok"])
        self.assertTrue(result["interrupted"])
        self.assertTrue(result["acknowledged"])
        self.assertEqual(
            bridge.load_run_snapshot(handle.request_id)["phase"], "interrupted"
        )

    @_isolated_run_state
    def test_stop_is_idempotent_on_terminal_runs(self, state_dir):
        manager = bridge.RunControlManager()
        handle, _, _ = manager.claim("agent-run:stop-c", "任务")
        manager.stop(handle.request_id)
        manager.finish(handle.request_id, False, "用户停止")
        again = manager.stop(handle.request_id)
        self.assertTrue(again["ok"])
        self.assertTrue(again["terminal"])
        self.assertEqual(again["phase"], "interrupted")


class ConfirmationRetirementTests(unittest.TestCase):
    def test_pending_endpoint_is_permanently_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"TIANGONG_V3_STATE_DIR": tmp}, clear=False
        ):
            (Path(tmp) / "pending_confirmations.json").write_text(
                json.dumps({"version": 1, "records": [
                    {"confirm_id": "c-1", "action": "file.write", "status": "pending"},
                ]}), encoding="utf-8",
            )
            body = bridge._policy_pending_confirmations()
            self.assertTrue(body["ok"])
            self.assertIs(body["retired"], True)
            self.assertEqual(body["pending"], [])
            self.assertEqual(body["count"], 0)


class RunScopeBindingTests(unittest.TestCase):
    @_isolated_run_state
    def test_cross_scope_request_id_collision_is_rejected(self, state_dir):
        manager = bridge.RunControlManager()
        handle, disposition, _ = manager.claim("agent-run:scope", "任务", session_id="session-A")
        self.assertEqual(disposition, "started")
        _h2, disposition_b, _ = manager.claim("agent-run:scope", "任务", session_id="session-B")
        self.assertEqual(disposition_b, "scope_conflict")
        # 同一 scope 重放仍是 running（幂等保护），不串 Run。
        _h3, disposition_c, _ = manager.claim("agent-run:scope", "任务", session_id="session-A")
        self.assertEqual(disposition_c, "running")
        persisted = bridge.load_run_snapshot(handle.request_id)
        self.assertEqual(persisted["principal_scope"], "session-A")


class PidReuseLeaseTests(unittest.TestCase):
    def test_pid_alive_but_creation_token_mismatch_is_dead(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.5)
            pid = process.pid
            self.assertTrue(bridge._process_is_alive(pid))
            real_token = bridge._process_start_token(pid)
            self.assertIsNotNone(real_token)
            run = {"owner_pid": pid, "owner_start_token": "nt:0"}
            # PID 活着但创建 token 不匹配（模拟 PID 复用）→ 判定死。
            self.assertFalse(bridge._run_owner_is_alive(run))
            run["owner_start_token"] = real_token
            self.assertTrue(bridge._run_owner_is_alive(run))
            # 旧快照无 token：保持历史 pid-only 语义。
            self.assertTrue(bridge._run_owner_is_alive({"owner_pid": pid}))
        finally:
            process.kill()
            process.wait(timeout=10)


class LongTaskFullRecoveryTests(unittest.TestCase):
    @_isolated_run_state
    def test_resume_snapshot_keeps_full_user_task(self, state_dir):
        manager = bridge.RunControlManager()
        long_task = "步骤一：" + "详细约束。" * 2000
        handle, disposition, _ = manager.claim("agent-run:long", long_task)
        self.assertEqual(disposition, "started")
        persisted = bridge.load_run_snapshot(handle.request_id)
        self.assertLessEqual(len(persisted["message"]), 500)
        self.assertEqual(persisted["message_full"], long_task)
        recovered = manager._normalise_recovered_run(persisted)
        snapshot = recovered["resume_snapshot"]
        self.assertEqual(snapshot["last_user_message"], long_task)


class WindowsReservedFilenameTests(unittest.TestCase):
    def test_reserved_device_names_never_become_raw_filenames(self) -> None:
        for raw in ("CON", "con", "NUL", "COM1", "lpt3.json", "PRN", "AUX"):
            safe = bridge._safe_request_id(raw)
            self.assertNotEqual(safe, raw)
            # 落盘文件名（去掉扩展名）不得是裸保留设备名。
            stem = Path(safe).stem.upper()
            self.assertNotIn(stem, bridge._WINDOWS_RESERVED_BASENAMES, safe)
        # 普通合法 ID 保留历史文件名；含冒号的 ID 走 digest 形态。
        self.assertEqual(bridge._safe_request_id("agent-run-1"), "agent-run-1")
        self.assertNotEqual(bridge._safe_request_id("agent-run:1"), "agent-run:1")


class SettingsAuthorityAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "workspace_settings.json"
        self._original = workspace_settings.WORKSPACE_SETTINGS_LUJING
        workspace_settings.WORKSPACE_SETTINGS_LUJING = self.config_path
        self._env_snapshot = {
            key: os.environ.get(key)
            for key in ("TIANGONG_DESKTOP_WORKSPACE_ROOT", "TIANGONG_WORKSPACE_ROOT", "TIANGONG_WORKSPACE_MODE")
        }
        for key in self._env_snapshot:
            os.environ.pop(key, None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        workspace_settings.WORKSPACE_SETTINGS_LUJING = self._original
        for key, value in self._env_snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_atomic_write_leaves_no_temp_files_and_keeps_backup(self) -> None:
        workspace = Path(self._tmp.name) / "projects"
        workspace_settings.baocun_workspace_settings({"workspace": str(workspace), "workspace_mode": "full"})
        # 第二次写入把上一份完整内容滚入 .bak。
        workspace_settings.baocun_workspace_settings({"workspace_mode": "workspace"})
        leftovers = [p.name for p in self.config_path.parent.iterdir() if ".tmp" in p.name]
        self.assertEqual([], leftovers)
        self.assertTrue(self.config_path.with_name("workspace_settings.json.bak").is_file())

    def test_corrupt_config_is_flagged_not_silently_defaulted(self) -> None:
        self.config_path.write_text("{ 损坏的 JSON", encoding="utf-8")
        result = workspace_settings.duqu_workspace_settings()
        self.assertEqual(result["settings_integrity"], "corrupted")
        self.assertEqual(result["error_code"], "SETTINGS_AUTHORITY_CORRUPTED")

    def test_corrupt_config_recovers_from_backup(self) -> None:
        workspace = Path(self._tmp.name) / "projects"
        workspace_settings.baocun_workspace_settings({"workspace": str(workspace), "workspace_mode": "full"})
        # 再写一次使 .bak 成为上一份完整内容，然后破坏主文件。
        workspace_settings.baocun_workspace_settings({"workspace_mode": "workspace"})
        self.config_path.write_text("{ 损坏", encoding="utf-8")
        result = workspace_settings.duqu_workspace_settings()
        self.assertEqual(result["settings_integrity"], "recovered_from_backup")
        self.assertEqual(result["workspace"], str(Path(workspace).resolve()))


if __name__ == "__main__":
    unittest.main()