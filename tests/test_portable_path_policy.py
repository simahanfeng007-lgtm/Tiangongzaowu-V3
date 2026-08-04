from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDITOR_PATH = ROOT / "scripts" / "audit-portable-paths.py"
SPEC = importlib.util.spec_from_file_location("tiangong_portable_path_audit", AUDITOR_PATH)
assert SPEC and SPEC.loader
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


class PortablePathPolicyTests(unittest.TestCase):
    def test_current_release_inputs_have_no_host_specific_paths(self) -> None:
        self.assertEqual(AUDITOR.audit(ROOT), [])

    def test_named_profile_and_external_install_literal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tiangong-portable-path-") as temporary:
            root = Path(temporary)
            source = root / "app" / "main.js"
            source.parent.mkdir(parents=True)
            source.write_text(
                'const a = "C:\\\\Users\\\\publisher\\\\Desktop";\n'
                'const b = "C:\\\\Program Files\\\\ExternalTool";\n',
                encoding="utf-8",
            )
            reasons = {item["reason"] for item in AUDITOR.audit(root)}
            self.assertEqual(reasons, {"named_windows_profile", "external_install_literal"})

    def test_placeholders_and_runtime_resolution_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tiangong-portable-path-") as temporary:
            root = Path(temporary)
            source = root / "app" / "main.js"
            source.parent.mkdir(parents=True)
            source.write_text(
                'const example = "C:\\\\Users\\\\...\\\\Desktop";\n'
                'const dynamic = process.env.ProgramFiles;\n',
                encoding="utf-8",
            )
            self.assertEqual(AUDITOR.audit(root), [])

    def test_unshipped_python_launchers_are_excluded_but_runtime_files_are_checked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tiangong-portable-path-") as temporary:
            root = Path(temporary)
            scripts = root / "app" / "runtime" / "python312" / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "generated.py").write_text(
                "#!C:\\Users\\publisher\\runtime\\python.exe\n",
                encoding="utf-8",
            )
            self.assertEqual(AUDITOR.audit(root), [])

            runtime_manifest = scripts.parent / "runtime-manifest.json"
            runtime_manifest.write_text(
                '{"publisher": "C:\\\\Users\\\\publisher\\\\runtime"}\n',
                encoding="utf-8",
            )
            findings = AUDITOR.audit(root)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["path"], "app/runtime/python312/runtime-manifest.json")


if __name__ == "__main__":
    unittest.main()
