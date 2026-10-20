from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from omni_body_skill.tools.portable_text import decode_portable_bytes
from omni_body_skill.tools.sandbox_runtime import _is_link_or_reparse


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "src"
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
        "_is_link_or_reparse": _is_link_or_reparse,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def _select_ffmpeg(configured: str | None) -> str | None:
    """Run the production constructor's selection statements without starting a runtime."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    runtime = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BodyRuntime")
    constructor = next(node for node in runtime.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    selected = [node for node in constructor.body if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "bundled_ffmpeg"
        or isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
        and target.value.id == "self" and target.attr == "ffmpeg"
        for target in node.targets
    )]
    state = SimpleNamespace(config=SimpleNamespace(ffmpeg_path=configured))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"),
         {"self": state, "os": os, "Path": Path, "shutil": shutil})
    return state.ffmpeg


class OmniBodyPythonInterpreterTests(unittest.TestCase):
    def installed_fixture(self, root: Path) -> tuple[Path, Path]:
        resources = root / "resources"
        gateway = resources / "total-gateway" / "tiangong-total-gateway.exe"
        gateway.parent.mkdir(parents=True)
        gateway.write_bytes(b"MZ fixture gateway")
        python_root = resources / "python"
        (python_root / "Lib" / "site-packages").mkdir(parents=True)
        for name in ("python.exe", "python3.dll", "python312.dll", "python312.zip", "runtime-manifest.json"):
            (python_root / name).write_bytes(b"fixture runtime file")
        # The legacy backend path is intentionally absent in the installed layout.
        (python_root / "python312._pth").write_text(
            "python312.zip\n.\nLib\\site-packages\n..\\..\\backend\nimport site\n", encoding="utf-8",
        )
        return gateway, python_root / "python.exe"

    def test_installed_gateway_uses_private_python_in_both_execution_modes(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-resolver-") as temporary:
            root = Path(temporary)
            gateway, bundled = self.installed_fixture(root)
            host = root / "host" / "python.exe"
            host.parent.mkdir()
            host.write_bytes(b"caller-provided interpreter")
            for controlled in (False, True):
                with self.subTest(controlled=controlled), mock.patch.object(sys, "platform", "win32"), \
                        mock.patch.object(sys, "frozen", True, create=True), \
                        mock.patch.object(sys, "executable", str(gateway)), \
                        mock.patch.dict(os.environ, {"TIANGONG_PYTHON_EXECUTABLE": str(host),
                                                    "PYTHONHOME": str(host.parent), "PYTHONPATH": str(host.parent)}), \
                        mock.patch.object(shutil, "which", side_effect=AssertionError("must not look up host Python")):
                    result = helpers["_resolve_python_interpreter"](controlled=controlled)
                    self.assertEqual(Path(result), bundled.resolve())

    def test_missing_installed_runtime_never_falls_back_to_caller_or_host(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-missing-") as temporary:
            gateway, bundled = self.installed_fixture(Path(temporary))
            host = Path(temporary) / "python.exe"
            host.write_bytes(b"caller-provided interpreter")
            for name in ("python.exe", "python312._pth", "runtime-manifest.json"):
                item = bundled.parent / name
                saved = item.read_bytes()
                item.unlink()
                try:
                    for controlled in (False, True):
                        with self.subTest(missing=name, controlled=controlled), \
                                mock.patch.object(sys, "platform", "win32"), \
                                mock.patch.object(sys, "frozen", True, create=True), \
                                mock.patch.object(sys, "executable", str(gateway)), \
                                mock.patch.dict(os.environ, {"TIANGONG_PYTHON_EXECUTABLE": str(host)}), \
                                mock.patch.object(shutil, "which", side_effect=AssertionError("must fail closed")):
                            with self.assertRaisesRegex(helpers["OmniBodyError"], "trusted bundled Python"):
                                helpers["_resolve_python_interpreter"](controlled=controlled)
                finally:
                    item.write_bytes(saved)

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 aliases")
    def test_installed_gateway_accepts_short_directory_alias(self) -> None:
        import ctypes
        from ctypes import wintypes

        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-short-alias-") as temporary:
            root = Path(temporary).resolve()
            gateway, bundled = self.installed_fixture(root)
            get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
            get_short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            get_short_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_short_path(str(root), buffer, len(buffer))
            self.assertTrue(0 < length < len(buffer), ctypes.get_last_error())
            short_root = Path(buffer.value)
            if os.path.normcase(str(short_root)) == os.path.normcase(str(root)):
                self.skipTest("The test volume does not provide an 8.3 alias")
            alias_gateway = short_root / gateway.relative_to(root)
            for controlled in (False, True):
                with self.subTest(controlled=controlled), \
                        mock.patch.object(sys, "frozen", True, create=True), \
                        mock.patch.object(sys, "executable", str(alias_gateway)), \
                        mock.patch.object(shutil, "which", side_effect=AssertionError("must use private Python")):
                    self.assertEqual(
                        Path(helpers["_resolve_python_interpreter"](controlled=controlled)), bundled.resolve(),
                    )

    def test_installed_runtime_redirected_ancestor_is_rejected(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-ancestor-") as temporary:
            root = Path(temporary).resolve()
            gateway, bundled = self.installed_fixture(root / "actual-install")
            alias = root / "redirected-install"
            if os.name == "nt":
                command = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
                created = subprocess.run(
                    [str(command), "/d", "/c", "mklink", "/J", str(alias), str(gateway.parents[2])],
                    capture_output=True, timeout=10,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
            else:
                alias.symlink_to(gateway.parents[2], target_is_directory=True)
            try:
                redirected = alias / gateway.relative_to(gateway.parents[2])
                for controlled in (False, True):
                    with self.subTest(controlled=controlled), mock.patch.object(sys, "platform", "win32"), \
                            mock.patch.object(sys, "frozen", True, create=True), \
                            mock.patch.object(sys, "executable", str(redirected)), \
                            mock.patch.object(shutil, "which", side_effect=AssertionError("must fail closed")):
                        with self.assertRaisesRegex(helpers["OmniBodyError"], "trusted bundled Python"):
                            helpers["_resolve_python_interpreter"](controlled=controlled)
            finally:
                if os.name == "nt":
                    alias.rmdir()
                else:
                    alias.unlink()
            self.assertTrue(bundled.is_file())

    def test_installed_runtime_directory_link_is_rejected(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-linked-") as temporary:
            root = Path(temporary)
            gateway, bundled = self.installed_fixture(root)
            python_root = bundled.parent
            outside = root / "outside-runtime"
            python_root.rename(outside)
            if os.name == "nt":
                command = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
                created = subprocess.run([str(command), "/d", "/c", "mklink", "/J", str(python_root), str(outside)],
                                         capture_output=True, timeout=10)
                self.assertEqual(created.returncode, 0, created.stderr)
            else:
                python_root.symlink_to(outside, target_is_directory=True)
            try:
                for controlled in (False, True):
                    with self.subTest(controlled=controlled), mock.patch.object(sys, "platform", "win32"), \
                            mock.patch.object(sys, "frozen", True, create=True), \
                            mock.patch.object(sys, "executable", str(gateway)), \
                            mock.patch.object(shutil, "which", side_effect=AssertionError("must fail closed")):
                        with self.assertRaisesRegex(helpers["OmniBodyError"], "trusted bundled Python"):
                            helpers["_resolve_python_interpreter"](controlled=controlled)
            finally:
                if os.name == "nt":
                    python_root.rmdir()
                else:
                    python_root.unlink()
            self.assertTrue((outside / "python.exe").is_file())

    def test_same_gateway_filename_without_frozen_state_is_not_trusted(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-not-frozen-") as temporary:
            gateway, _bundled = self.installed_fixture(Path(temporary))
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "frozen", False, create=True), \
                    mock.patch.object(sys, "executable", str(gateway)), \
                    mock.patch.dict(os.environ, {"TIANGONG_PYTHON_EXECUTABLE": ""}):
                with self.assertRaisesRegex(helpers["OmniBodyError"], "No trusted Python"):
                    helpers["_resolve_python_interpreter"](controlled=True)

    def test_frozen_gateway_outside_resources_layout_is_rejected(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-layout-") as temporary:
            gateway, _bundled = self.installed_fixture(Path(temporary))
            renamed = gateway.parent.parent.with_name("host-runtime")
            gateway.parent.parent.rename(renamed)
            gateway = renamed / gateway.parent.name / gateway.name
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(gateway)), \
                    mock.patch.object(shutil, "which", side_effect=AssertionError("must fail closed")):
                with self.assertRaisesRegex(helpers["OmniBodyError"], "unsupported installed runtime layout"):
                    helpers["_resolve_python_interpreter"]()

    def test_legacy_app_layout_keeps_bundled_interpreter_behavior(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-legacy-") as temporary:
            app = Path(temporary) / "app"
            backend = app / "backend" / "tiangong-backend" / "tiangong-backend.exe"
            bundled = app / "life-service" / "runtime314" / "python.exe"
            for item in (backend, bundled):
                item.parent.mkdir(parents=True)
                item.write_bytes(b"legacy fixture")
            for controlled in (False, True):
                with self.subTest(controlled=controlled), mock.patch.object(sys, "frozen", True, create=True), \
                        mock.patch.object(sys, "executable", str(backend)), \
                        mock.patch.dict(os.environ, {"TIANGONG_PYTHON_EXECUTABLE": ""}), \
                        mock.patch.object(shutil, "which", return_value=None):
                    self.assertEqual(Path(helpers["_resolve_python_interpreter"](controlled=controlled)), bundled.resolve())

    def test_source_interpreter_and_caller_override_remain_available(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-source-") as temporary:
            source = Path(temporary) / "python.exe"
            configured = Path(temporary) / "python3.exe"
            for item in (source, configured):
                item.write_bytes(b"source fixture")
            with mock.patch.object(sys, "frozen", False, create=True), \
                    mock.patch.object(sys, "executable", str(source)), \
                    mock.patch.dict(os.environ, {"TIANGONG_PYTHON_EXECUTABLE": str(configured)}), \
                    mock.patch.object(shutil, "which", return_value=None):
                self.assertEqual(Path(helpers["_resolve_python_interpreter"]()), configured.resolve())
                self.assertEqual(Path(helpers["_resolve_python_interpreter"](controlled=True)), source.resolve())

    def test_ffmpeg_prefers_explicit_config_then_valid_private_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tiangong-private-ffmpeg-") as temporary:
            bundled = Path(temporary) / "ffmpeg.exe"
            bundled.write_bytes(b"bundled ffmpeg fixture")
            with mock.patch.dict(os.environ, {"TIANGONG_BUNDLED_FFMPEG_EXECUTABLE": str(bundled)}), \
                    mock.patch.object(shutil, "which", side_effect=AssertionError("bundle/config must precede host lookup")):
                self.assertEqual(_select_ffmpeg(None), str(bundled))
                self.assertEqual(_select_ffmpeg("explicit-ffmpeg"), "explicit-ffmpeg")
            for candidate in ("", "relative-ffmpeg.exe", str(bundled.with_name("missing.exe"))):
                with self.subTest(candidate=candidate), \
                        mock.patch.dict(os.environ, {"TIANGONG_BUNDLED_FFMPEG_EXECUTABLE": candidate}), \
                        mock.patch.object(shutil, "which", return_value="host-ffmpeg"):
                    self.assertEqual(_select_ffmpeg(None), "host-ffmpeg")

    def test_linux_source_keeps_system_interpreter_preference(self) -> None:
        helpers = _load_interpreter_helpers()
        with tempfile.TemporaryDirectory(prefix="tiangong-python-linux-") as temporary:
            source = Path(temporary) / "python.exe"
            source.write_bytes(b"source fixture")
            system = Path("/usr/bin/python3")
            expected = system if helpers["_is_python_interpreter"](system) else source
            with mock.patch.object(sys, "platform", "linux"), mock.patch.object(sys, "frozen", False, create=True), \
                    mock.patch.object(sys, "executable", str(source)), \
                    mock.patch.object(shutil, "which", side_effect=AssertionError("controlled mode must not look up host Python")):
                self.assertEqual(Path(helpers["_resolve_python_interpreter"](controlled=True)), expected.resolve())

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
