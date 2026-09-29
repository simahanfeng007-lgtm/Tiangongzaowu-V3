from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


class SourceRuntimeRecoveryTests(unittest.TestCase):
    def test_existing_python_is_rechecked_and_missing_pip_is_bootstrapped(self) -> None:
        setup = script("setup-source.ps1")
        provision = script("provision-embedded-python.ps1")

        self.assertIn('if (-not (& $ProvisionEmbeddedPython -Check))', setup)
        self.assertIn('& $ProvisionEmbeddedPython\n', setup)
        self.assertIn('if (-not (& $ProvisionEmbeddedPython -Check)) {\n        throw', setup)
        self.assertIn('if (-not $PipAvailable) {', provision)
        self.assertIn('& $Python -m pip --version', provision)
        self.assertIn('& $Python $Bootstrap', provision)
        self.assertLess(provision.index('if ($NeedsExtract) {'), provision.index('if (-not $PipAvailable) {'))
        self.assertNotIn('Embedded runtime target is non-empty', provision)
        self.assertIn('Expand-Archive -LiteralPath $Archive -DestinationPath $RuntimeRoot -Force', provision)
        self.assertIn('$ExistingPthLines', provision)

    def test_manifest_marks_only_completed_core_install_and_tracks_both_locks(self) -> None:
        provision = script("provision-embedded-python.ps1")

        self.assertIn('source_requirements_sha256 = Get-FileDigest', provision)
        self.assertIn('$Manifest.source_requirements_sha256 -ne', provision)
        self.assertIn('& $Python -m pip check', provision)
        self.assertIn('Remove-Item -LiteralPath $ManifestPath -Force', provision)
        self.assertLess(
            provision.index('$TkinterAvailable = [bool](Copy-TkinterRuntime)'),
            provision.index('$Manifest = [ordered]@{'),
        )
        self.assertIn('tkinter_available = [bool]$TkinterAvailable', provision)
        self.assertNotIn('if (-not $Manifest.tkinter_available)', provision)
        self.assertIn('shutil.copytree(sys.argv[1], sys.argv[2], dirs_exist_ok=True)', provision)
        self.assertIn('$ManifestTempPath = "$ManifestPath.tmp"', provision)
        self.assertIn('Move-Item -LiteralPath $ManifestTempPath -Destination $ManifestPath -Force', provision)

    def test_start_reports_incomplete_install_before_port_probe(self) -> None:
        start = script("start-source.ps1")

        self.assertLess(start.index('provision-embedded-python.ps1'), start.index('Get-NetTCPConnection -State Listen -LocalPort 7184'))
        self.assertIn('Run scripts\\setup-source.ps1 to repair it.', start)
        self.assertIn('Source mode will not adopt or stop it', start)
        self.assertIn('$Python = Join-Path $AppRoot ".venv\\Scripts\\python.exe"', start)

    def test_native_windows_recovery_smoke_is_scoped_to_source_runtime_changes(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "source-runtime-recovery.yml").read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("timeout-minutes: 30", workflow)
        self.assertIn("shell: powershell", workflow)
        self.assertIn('TIANGONG_SKIP_PLAYWRIGHT_BROWSERS: "1"', workflow)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $RuntimeRoot "runtime-manifest.json")', workflow)
        self.assertIn('Remove-Item -LiteralPath (Join-Path $RuntimeRoot "python312._pth")', workflow)
        self.assertNotIn("Start-Transcript", workflow)
        self.assertNotIn("actions/upload-artifact", workflow)
        self.assertNotIn("release-desktop.yml", workflow)


if __name__ == "__main__":
    unittest.main()
