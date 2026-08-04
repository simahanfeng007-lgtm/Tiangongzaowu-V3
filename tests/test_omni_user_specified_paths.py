"""D-21：用户指定路径即授权（omni_grant_authority 与后端直通语义对齐）。

用户本轮原文明确指定的路径（字面绝对路径 / 桌面·文档·下载别名）写入放行；
硬禁区（系统目录/盘符根/凭据目录/.env）即使用户点名也拒绝；
未指定且动作未携带路径自由权限时维持原围栏；A1-A4 的签名路径自由权限
可访问普通工作区外路径，但系统/凭据等 A5 硬禁区仍拒绝。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from total_gateway.omni_grant_authority import (
    OmniGrantAuthority,
    OmniGrantAuthorityError,
    _is_hard_deny_path,
    _user_specified_roots_from_text,
)


def _authority(workspace: Path) -> OmniGrantAuthority:
    authority = OmniGrantAuthority.__new__(OmniGrantAuthority)
    authority.workspace_root = workspace.resolve()
    return authority


class UserSpecifiedExtractionTests(unittest.TestCase):
    def test_alias_desktop(self) -> None:
        roots = _user_specified_roots_from_text("你帮我在桌面上写个关于母亲的作文吧我要word格式")
        desktop = (Path.home() / "Desktop").resolve(strict=False)
        self.assertIn(desktop, roots)

    def test_verbatim_absolute(self) -> None:
        roots = _user_specified_roots_from_text(r"把结果写到 D:\docs\报告.docx 就行")
        self.assertIn(Path(r"D:\docs\报告.docx").resolve(strict=False), roots)

    def test_no_mention_empty(self) -> None:
        self.assertEqual(_user_specified_roots_from_text("随便写点啥"), ())


class HardDenyTests(unittest.TestCase):
    def test_windows_dir(self) -> None:
        self.assertTrue(_is_hard_deny_path(Path(r"C:\Windows\System32\x.dll")))

    def test_drive_root(self) -> None:
        self.assertTrue(_is_hard_deny_path(Path(r"C:\\")))

    def test_credential_dir(self) -> None:
        self.assertTrue(_is_hard_deny_path(Path.home() / ".ssh" / "id_rsa"))

    def test_dotenv(self) -> None:
        self.assertTrue(_is_hard_deny_path(Path.home() / "Desktop" / ".env"))

    def test_normal_desktop_file_not_deny(self) -> None:
        self.assertFalse(_is_hard_deny_path(Path.home() / "Desktop" / "作文.docx"))


class UserSpecifiedValidationTests(unittest.TestCase):
    def test_user_named_desktop_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            authority._validate_path_value(
                str(desktop / "作文.docx"),
                allow_absolute=False,
                user_roots=(desktop,),
            )

    def test_unnamed_outside_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            with self.assertRaisesRegex(OmniGrantAuthorityError, "absolute_forbidden"):
                authority._validate_path_value(str(desktop / "作文.docx"), allow_absolute=False)

    def test_user_named_system_path_still_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            with self.assertRaisesRegex(OmniGrantAuthorityError, "workspace_escape"):
                authority._validate_path_value(
                    r"C:\Windows\notepad-fake.exe",
                    allow_absolute=True,
                    user_roots=(Path(r"C:\Windows"),),
                )

    def test_user_named_dotenv_still_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            with self.assertRaisesRegex(OmniGrantAuthorityError, "absolute_forbidden|workspace_escape"):
                authority._validate_path_value(
                    str(desktop / ".env"),
                    allow_absolute=False,
                    user_roots=(desktop,),
                )

    def test_traversal_outside_alias_root_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            sneaky = str(desktop / ".." / "secrets.txt")
            with self.assertRaisesRegex(OmniGrantAuthorityError, "absolute_forbidden|workspace_escape"):
                authority._validate_path_value(sneaky, allow_absolute=False, user_roots=(desktop,))

    def test_workspace_relative_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            authority._validate_path_value("canary/probe.txt", allow_absolute=False)
            with self.assertRaisesRegex(OmniGrantAuthorityError, "workspace_escape"):
                authority._validate_path_value("../escape.txt", allow_absolute=False)

    def test_signed_path_freedom_allows_normal_absolute_and_relative_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            outside = Path(temporary) / "outside" / "result.txt"
            authority._validate_path_value(str(outside), allow_absolute=True)
            authority._validate_path_value("../outside/result.txt", allow_absolute=True)


class UserSpecifiedWiringTests(unittest.TestCase):
    def test_roots_from_envelope_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = _authority(Path(temporary) / "ws")
            authority._effect_store = SimpleNamespace(
                get_request_envelope=lambda rid: SimpleNamespace(text="放桌面就行") if rid == "req_x" else None
            )
            roots = authority._user_specified_roots("req_x")
            self.assertIn((Path.home() / "Desktop").resolve(strict=False), roots)
            self.assertEqual(authority._user_specified_roots("req_other"), ())

    def test_missing_store_capability_fail_closed(self) -> None:
        authority = _authority(Path(tempfile.gettempdir()))
        authority._effect_store = SimpleNamespace()
        self.assertEqual(authority._user_specified_roots("req_any"), ())


class ToolContractsUserRootsTests(unittest.TestCase):

    def _validate(self, action, target, args, workspace, user_roots=()):
        from omni_body_skill.tool_contracts import validate_tool_request

        return validate_tool_request(action, target, args, workspace=workspace, user_roots=user_roots)

    def test_docx_desktop_allowed_with_user_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            out = self._validate(
                "docx.create", str(desktop / "作文.docx"), {"content": "正文"}, temporary,
                user_roots=[str(desktop)],
            )
            self.assertTrue(out.get("ok"), out)

    def test_docx_desktop_denied_without_user_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            out = self._validate("docx.create", str(desktop / "作文.docx"), {"content": "正文"}, temporary)
            self.assertFalse(out.get("ok"))
            self.assertTrue(any(i.get("code") == "outside_workspace" for i in out.get("issues", [])), out)

    def test_copy_destination_allowed_with_user_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            desktop = (Path.home() / "Desktop").resolve(strict=False)
            out = self._validate(
                "file.copy", "作文.docx", {"destination": str(desktop / "作文.docx")}, temporary,
                user_roots=[str(desktop)],
            )
            self.assertTrue(out.get("ok"), out)

    def test_system_path_denied_even_with_user_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = self._validate(
                "file.write", r"C:\Windows\temp-probe.txt", {"content": "x"}, temporary,
                user_roots=[r"C:\Windows"],
            )
            self.assertFalse(out.get("ok"))
            self.assertTrue(any(i.get("code") == "outside_workspace" for i in out.get("issues", [])), out)


class BodyRuntimeUserRootsE2ETests(unittest.TestCase):
    """D-21 第三层：BodyRuntime 端到端（contracts + _resolve + 落盘）。"""

    def _runtime(self, workspace: Path, user_roots):
        from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig

        return BodyRuntime(BodyRuntimeConfig(
            workspace=str(workspace),
            fact_kernel_enabled=False,
            user_path_roots=[str(r) for r in user_roots],
        ))

    def test_docx_create_to_desktop_user_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary) / "ws"
            ws.mkdir()
            desktop_root = Path(temporary) / "fake-desktop"
            desktop_root.mkdir()
            target = desktop_root / "作文.docx"
            rt = self._runtime(ws, [desktop_root])
            result = rt.run("docx.create", str(target), {"content": "# 我的母亲\n\n正文内容。"})
            self.assertTrue(result.get("ok") or result.get("success"), str(result)[:400])
            self.assertTrue(target.is_file())

    def test_docx_create_to_desktop_denied_without_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary) / "ws"
            ws.mkdir()
            desktop_root = Path(temporary) / "fake-desktop"
            desktop_root.mkdir()
            target = desktop_root / "作文.docx"
            rt = self._runtime(ws, [])
            result = rt.run("docx.create", str(target), {"content": "# 我的母亲\n\n正文内容。"})
            self.assertFalse(result.get("ok") or result.get("success"))
            self.assertFalse(target.is_file())

    def test_file_copy_to_user_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary) / "ws"
            ws.mkdir()
            (ws / "a.txt").write_text("内容", encoding="utf-8")
            desktop_root = Path(temporary) / "fake-desktop"
            desktop_root.mkdir()
            rt = self._runtime(ws, [desktop_root])
            result = rt.run("file.copy", "a.txt", {"destination": str(desktop_root / "a.txt")})
            self.assertTrue(result.get("ok") or result.get("success"), str(result)[:400])
            self.assertEqual((desktop_root / "a.txt").read_text(encoding="utf-8"), "内容")

    def test_user_root_hard_deny_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary) / "ws"
            ws.mkdir()
            rt = self._runtime(ws, [Path(r"C:\Windows")])
            result = rt.run("file.write", r"C:\Windows\d21-probe-should-not-exist.txt", {"content": "x"})
            self.assertFalse(result.get("ok") or result.get("success"))


if __name__ == "__main__":
    unittest.main()
