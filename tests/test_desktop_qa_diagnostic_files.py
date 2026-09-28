from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QA_SCRIPTS = (
    ROOT / "scripts" / "qa-installed-clean-first-run.ps1",
    ROOT / "scripts" / "qa-packaged-first-run.ps1",
)


def diagnostic_selector(script: Path) -> str:
    source = script.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^function Get-RendererDiagnosticFiles \{\n.*?^\}", source)
    if not match:
        raise AssertionError(f"diagnostic selector missing: {script.name}")
    return match.group(0)


class DesktopQaDiagnosticFilesTests(unittest.TestCase):
    def test_both_first_run_probes_use_the_product_rotated_diagnostics(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        self.assertIn("desktop_renderer.${stamp}.jsonl", main)
        installed, packaged = (script.read_text(encoding="utf-8") for script in QA_SCRIPTS)
        self.assertEqual(diagnostic_selector(QA_SCRIPTS[0]), diagnostic_selector(QA_SCRIPTS[1]))
        for source in (installed, packaged):
            self.assertIn('Get-RendererDiagnosticFiles -Root', source)
            self.assertIn('Get-Content -LiteralPath $_.FullName', source)
            self.assertNotIn('"desktop_renderer.jsonl"', source)

    def test_selector_collects_only_dated_logs_across_day_boundary(self) -> None:
        powershell = (
            os.environ.get("TIANGONG_TEST_PWSH")
            or shutil.which("pwsh")
            or shutil.which("powershell.exe")
        )
        if not powershell:
            self.skipTest("PowerShell runtime is unavailable")
        with tempfile.TemporaryDirectory(prefix="tg-qa-diagnostics-") as temporary:
            logs = Path(temporary) / "用户 配置" / "logs"
            logs.mkdir(parents=True)
            nested = logs / "alpha"
            nested.mkdir()
            (nested / "desktop_renderer.2026-09-29.jsonl").write_text("{}\n", encoding="utf-8")
            for name in (
                "desktop_renderer.2026-09-28.jsonl",
                "desktop_renderer.jsonl",
                "desktop_renderer.latest.jsonl",
                "desktop_renderer.2026-9-29.jsonl",
            ):
                (logs / name).write_text("{}\n", encoding="utf-8")
            script = (
                "$ErrorActionPreference = 'Stop'\n"
                + diagnostic_selector(QA_SCRIPTS[0])
                + "\n@((Get-RendererDiagnosticFiles -Root $env:TG_DIAGNOSTIC_TEST_ROOT) | "
                "ForEach-Object { $_.Name }) | ConvertTo-Json -Compress\n"
            )
            environment = {**os.environ, "TG_DIAGNOSTIC_TEST_ROOT": str(logs)}
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                timeout=30,
            )
            self.assertEqual(
                json.loads(completed.stdout),
                ["desktop_renderer.2026-09-28.jsonl", "desktop_renderer.2026-09-29.jsonl"],
            )


if __name__ == "__main__":
    unittest.main()
