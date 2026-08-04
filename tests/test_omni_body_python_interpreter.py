from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import sys
import unittest
from unittest import mock

from omni_body_skill.tools.portable_text import decode_portable_bytes


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
    / "omni_body_skill"
    / "tools"
    / "omni_body_tool.py"
)


def _load_interpreter_helpers() -> dict[str, object]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef))
        and (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_PYTHON_EXECUTABLE_NAME" for target in node.targets)
            or isinstance(node, ast.FunctionDef)
            and node.name
            in {
                "_is_python_interpreter",
                "_resolve_python_interpreter",
                "_bounded_subprocess_text",
                "_capability_prefix_counts",
            }
        )
    ]

    class OmniBodyError(RuntimeError):
        pass

    namespace: dict[str, object] = {
        "List": list,
        "Any": object,
        "Dict": dict,
        "Iterable": list,
        "OmniBodyError": OmniBodyError,
        "Path": Path,
        "__file__": str(SOURCE),
        "os": os,
        "re": re,
        "shutil": shutil,
        "sys": sys,
        "decode_portable_bytes": decode_portable_bytes,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


class OmniBodyPythonInterpreterTests(unittest.TestCase):
    def test_capability_discovery_can_use_compact_prefix_counts(self) -> None:
        summarize = _load_interpreter_helpers()["_capability_prefix_counts"]
        self.assertEqual(  # type: ignore[operator]
            summarize(["file.read", "file.write", "qc.novel.chapter_check", "skill.get"]),
            {"file": 2, "qc": 1, "skill": 1},
        )

    def test_subprocess_output_normalization_is_total_and_bounded(self) -> None:
        normalize = _load_interpreter_helpers()["_bounded_subprocess_text"]
        self.assertEqual(normalize(None), "")  # type: ignore[operator]
        self.assertEqual(normalize(b"\xe5\xa4\xa9\xe5\xb7\xa5"), "天工")  # type: ignore[operator]
        self.assertEqual(normalize("abcdef", 3), "def")  # type: ignore[operator]

    @unittest.skipUnless((ROOT / "app/backend/tiangong-backend/tiangong-backend.exe").is_file(), "frozen executable test is not applicable to source release")
    def test_frozen_backend_executable_resolves_to_bundled_python(self) -> None:
        helpers = _load_interpreter_helpers()
        backend_exe = ROOT / "app" / "backend" / "tiangong-backend" / "tiangong-backend.exe"
        bundled_python = ROOT / "app" / "life-service" / "runtime314" / "python.exe"
        self.assertTrue(backend_exe.is_file())
        self.assertTrue(bundled_python.is_file())

        with mock.patch.object(sys, "executable", str(backend_exe)), mock.patch.dict(
            os.environ, {"TIANGONG_PYTHON_EXECUTABLE": ""}, clear=False
        ):
            resolved = Path(helpers["_resolve_python_interpreter"]())  # type: ignore[operator]

        self.assertEqual(resolved, bundled_python.resolve())
        self.assertNotEqual(resolved, backend_exe.resolve())

    def test_configured_backend_binary_is_rejected(self) -> None:
        helpers = _load_interpreter_helpers()
        backend_exe = ROOT / "app" / "backend" / "tiangong-backend" / "tiangong-backend.exe"
        with mock.patch.dict(
            os.environ,
            {"TIANGONG_PYTHON_EXECUTABLE": str(backend_exe)},
            clear=False,
        ):
            self.assertFalse(helpers["_is_python_interpreter"](backend_exe))  # type: ignore[operator]


if __name__ == "__main__":
    unittest.main()
