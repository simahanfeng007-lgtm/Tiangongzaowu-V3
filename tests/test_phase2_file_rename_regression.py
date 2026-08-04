from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
READABLE = ROOT / "readable-python-source"
if str(READABLE) not in sys.path:
    sys.path.insert(0, str(READABLE))

from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig


class FileRenameRegressionTests(unittest.TestCase):
    def make_runtime(self, workspace: Path) -> BodyRuntime:
        return BodyRuntime(BodyRuntimeConfig(workspace=str(workspace), fact_kernel_enabled=False))

    def test_relative_rename_stays_inside_signed_workspace_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            source = workspace / "jobs" / "original.txt"
            source.parent.mkdir(parents=True)
            source.write_text("rename-contract", encoding="utf-8")
            runtime = self.make_runtime(workspace)

            result = runtime.run("file.rename", "jobs/original.txt", {"new_name": "renamed.txt"})

            self.assertTrue(result.get("success"), result)
            self.assertFalse(source.exists())
            renamed = workspace / "jobs" / "renamed.txt"
            self.assertEqual(renamed.read_text(encoding="utf-8"), "rename-contract")

    def test_rename_then_move_remains_a_normal_relative_chain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            source = workspace / "jobs" / "original.txt"
            source.parent.mkdir(parents=True)
            source.write_text("rename-and-move", encoding="utf-8")
            runtime = self.make_runtime(workspace)

            renamed = runtime.run("file.rename", "jobs/original.txt", {"new_name": "renamed.txt"})
            moved = runtime.run(
                "file.move",
                "jobs/renamed.txt",
                {"destination": "jobs/archive/final.txt"},
            )

            self.assertTrue(renamed.get("success"), renamed)
            self.assertTrue(moved.get("success"), moved)
            self.assertEqual(
                (workspace / "jobs" / "archive" / "final.txt").read_text(encoding="utf-8"),
                "rename-and-move",
            )

    def test_rename_rejects_cross_platform_unsafe_filename_components(self) -> None:
        invalid_names = ("../escape.txt", "nested/name.txt", "CON.txt", "trailing. ", "bad\u202ename.txt")
        for invalid in invalid_names:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as td:
                workspace = Path(td)
                (workspace / "original.txt").write_text("x", encoding="utf-8")
                result = self.make_runtime(workspace).run(
                    "file.rename",
                    "original.txt",
                    {"new_name": invalid},
                )
                self.assertFalse(result.get("success"), result)
                self.assertTrue((workspace / "original.txt").exists())

    def test_workspace_root_cannot_be_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            result = self.make_runtime(workspace).run("file.rename", ".", {"new_name": "moved-root"})
            self.assertFalse(result.get("success"), result)
            self.assertIn("protected runtime root", str(result.get("message")))


if __name__ == "__main__":
    unittest.main()
