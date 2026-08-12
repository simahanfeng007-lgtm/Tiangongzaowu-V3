"""工作区/全盘写入模式回归测试。

对应 2026-08-06 需求：设置面板下拉切换写入范围；全盘 = 除 Windows 核心系统
文件（硬禁区）外全可写；默认工作区；老配置按工作区；全盘不打断。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class WorkspaceSettingsModeTests(unittest.TestCase):
    def _isolated_env(self, tmp: str) -> dict:
        return {
            "USERPROFILE": str(Path(tmp) / "home"),
            "HOME": str(Path(tmp) / "home"),
            "TIANGONG_DESKTOP_WORKSPACE_ROOT": "",
            "TIANGONG_WORKSPACE_ROOT": "",
            "TIANGONG_WORKSPACE_MODE": "",
        }

    def test_default_mode_is_workspace(self) -> None:
        from v3.workspace_settings import duqu_workspace_settings

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._isolated_env(tmp), clear=False
        ):
            settings = duqu_workspace_settings()
            self.assertEqual(settings["workspace_mode"], "workspace")

    def test_save_and_read_full_mode(self) -> None:
        from v3.workspace_settings import baocun_workspace_settings, duqu_workspace_settings

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._isolated_env(tmp), clear=False
        ):
            workspace = str(Path(tmp) / "ws")
            saved = baocun_workspace_settings({"workspace": workspace, "workspace_mode": "full"})
            self.assertEqual(saved["workspace_mode"], "full")
            self.assertEqual(os.environ["TIANGONG_WORKSPACE_MODE"], "full")
            self.assertEqual(duqu_workspace_settings()["workspace_mode"], "full")
            # 非法值回退工作区。
            saved2 = baocun_workspace_settings({"workspace": workspace, "workspace_mode": "all"})
            self.assertEqual(saved2["workspace_mode"], "workspace")

    def test_legacy_config_without_mode_is_workspace(self) -> None:
        from v3.workspace_settings import WORKSPACE_SETTINGS_LUJING, duqu_workspace_settings

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._isolated_env(tmp), clear=False
        ):
            WORKSPACE_SETTINGS_LUJING.parent.mkdir(parents=True, exist_ok=True)
            WORKSPACE_SETTINGS_LUJING.write_text(
                '{"workspace": "C:/legacy-ws", "updated_at": 1}',
                encoding="utf-8",
            )
            self.assertEqual(duqu_workspace_settings()["workspace_mode"], "workspace")


class RuntimePromptWorkspaceModeTests(unittest.TestCase):
    def _status(self, mode: str) -> dict:
        return {
            "ok": True,
            "workspace": r"C:\TiangongWorkspace",
            "workspace_mode": mode,
            "permission_mode": "full_access",
            "mode_label": "完全访问权限",
            "home": r"C:\Users\someone",
            "desktop": r"C:\Users\someone\Desktop",
            "downloads": r"C:\Users\someone\Downloads",
            "documents": r"C:\Users\someone\Documents",
            "drive_roots": ["C:\\", "D:\\"],
            "runtime": {"user": {"username": "someone"}},
        }

    def test_full_mode_prompt_does_not_reinstate_workspace_gate(self) -> None:
        from v3 import permission_settings

        with mock.patch.object(permission_settings, "permission_status", return_value=self._status("full")):
            prompt = permission_settings.build_runtime_context_prompt("删除 D 盘文件", force=True)
        self.assertIn("写入范围: 全盘 (full)", prompt)
        self.assertIn("不是访问权限边界", prompt)
        self.assertIn("file.delete_to_trash", prompt)
        self.assertIn("A1-A4", prompt)
        self.assertNotIn("默认只在工作区内读写", prompt)
        self.assertNotIn("必须先向用户确认", prompt)

    def test_workspace_mode_prompt_uses_object_grant_not_retired_confirmation(self) -> None:
        from v3 import permission_settings

        with mock.patch.object(permission_settings, "permission_status", return_value=self._status("workspace")):
            prompt = permission_settings.build_runtime_context_prompt("操作文件", force=True)
        self.assertIn("写入范围: 工作区 (workspace)", prompt)
        self.assertIn("object grant", prompt)
        self.assertNotIn("必须先向用户确认", prompt)

    def test_permission_status_reports_runtime_workspace_mode(self) -> None:
        from v3 import permission_settings

        with mock.patch.dict(os.environ, {"TIANGONG_WORKSPACE_MODE": "full"}, clear=False), mock.patch.object(
            permission_settings,
            "runtime_environment_summary",
            return_value={"workspace": r"C:\ws", "drive_roots": []},
        ):
            status = permission_settings.permission_status()
        self.assertEqual(status["workspace_mode"], "full")


class ToolContractFullDiskTests(unittest.TestCase):
    def _validate(self, target: str, mode: str, action: str = "file.write"):
        from omni_body_skill.tool_contracts import validate_tool_request

        args = {"action": action}
        if action == "file.write":
            args["content"] = "hello"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"TIANGONG_WORKSPACE_MODE": mode},
            clear=False,
        ):
            return validate_tool_request(
                action,
                target,
                args,
                workspace=tmp,
            )

    def _has_outside_workspace(self, result: dict) -> bool:
        return any(
            str(issue.get("code") or "") == "outside_workspace"
            for issue in (result.get("issues") or [])
        )

    def test_workspace_mode_blocks_desktop(self) -> None:
        result = self._validate(r"C:\Users\someone\Desktop\a.md", "workspace")
        self.assertTrue(self._has_outside_workspace(result))

    def test_full_mode_allows_desktop(self) -> None:
        result = self._validate(r"C:\Users\someone\Desktop\a.md", "full")
        self.assertFalse(self._has_outside_workspace(result))
        self.assertTrue(bool(result.get("ok")))

    def test_workspace_mode_blocks_outside_delete(self) -> None:
        result = self._validate(r"D:\outside\delete-me.txt", "workspace", "file.delete_to_trash")
        self.assertTrue(self._has_outside_workspace(result))

    def test_full_mode_allows_outside_delete_to_trash(self) -> None:
        result = self._validate(r"D:\outside\delete-me.txt", "full", "file.delete_to_trash")
        self.assertFalse(self._has_outside_workspace(result))
        self.assertTrue(bool(result.get("ok")))

    def test_full_mode_still_blocks_windows_core(self) -> None:
        result = self._validate(r"C:\Windows\System32\drivers\etc\hosts", "full")
        self.assertTrue(self._has_outside_workspace(result))

    def test_full_mode_still_blocks_credential_dir(self) -> None:
        result = self._validate(r"C:\Users\someone\.ssh\id_rsa", "full")
        self.assertTrue(self._has_outside_workspace(result))

    def test_full_mode_still_blocks_delete_in_windows_core(self) -> None:
        result = self._validate(
            r"C:\Windows\System32\drivers\etc\hosts",
            "full",
            "file.delete_to_trash",
        )
        self.assertTrue(self._has_outside_workspace(result))

    def test_full_mode_blocks_hard_deny_in_shell_command(self) -> None:
        from omni_body_skill.tool_contracts import validate_tool_request

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"TIANGONG_WORKSPACE_MODE": "full"},
            clear=False,
        ):
            result = validate_tool_request(
                "shell.run",
                "",
                {
                    "action": "shell.run",
                    "command": 'cmd /c copy C:\\Windows\\System32\\drivers\\etc\\hosts D:\\x.txt',
                },
                workspace=tmp,
            )
            codes = [str(issue.get("code") or "") for issue in (result.get("issues") or [])]
            self.assertIn("hard_deny_path", codes)


class ImpactEvaluatorFullDiskTests(unittest.TestCase):
    def test_full_mode_skips_outside_workspace_blast(self) -> None:
        from total_gateway.impact_evaluator import _path_outside_workspace

        with mock.patch.dict(os.environ, {"TIANGONG_WORKSPACE_MODE": "full"}, clear=False):
            self.assertFalse(_path_outside_workspace(r"D:\anywhere\x.txt", r"C:\ws"))
        with mock.patch.dict(os.environ, {"TIANGONG_WORKSPACE_MODE": ""}, clear=False):
            self.assertTrue(_path_outside_workspace(r"D:\anywhere\x.txt", r"C:\ws"))


if __name__ == "__main__":
    unittest.main()
