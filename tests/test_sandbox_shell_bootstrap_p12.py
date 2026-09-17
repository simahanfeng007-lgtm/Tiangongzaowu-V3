"""PowerShell bootstrap regressions; native assertions never use compatibility fallback."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from omni_body_skill.tools import sandbox_runtime as sandbox


class ShellBootstrapContractTests(unittest.TestCase):
    def render(self, command="Set-Content -LiteralPath x.txt -Value ok"):
        with mock.patch.object(sandbox.shutil, "which", return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"):
            result = sandbox._prepare_windows_utf8_shell_command(
                [sandbox.WINDOWS_POWERSHELL_SHELL_MARKER, command], cwd=Path.cwd())
        self.assertEqual(result[-2], "-EncodedCommand")
        return base64.b64decode(result[-1]).decode("utf-16-le")

    def test_management_and_utility_are_explicit_before_drive_initialization(self):
        script = self.render()
        for name in ("Microsoft.PowerShell.Management.psd1", "Microsoft.PowerShell.Utility.psd1"):
            self.assertLess(script.index(name), script.index("New-PSDrive"))
        self.assertIn("$PSHOME", script)
        self.assertIn("$PSModuleAutoLoadingPreference='None'", script)

    def test_user_autoload_preference_is_restored_in_finally(self):
        script = self.render()
        self.assertIn("$tgSavedAutoload=$PSModuleAutoLoadingPreference", script)
        self.assertIn("finally{$PSModuleAutoLoadingPreference=$tgSavedAutoload}", script)

    def test_bootstrap_failure_is_not_success(self):
        script = self.render()
        self.assertEqual(script.count(" -ErrorAction Stop;"), 4)
        self.assertIn("[Console]::Error.WriteLine($_.Exception.Message);exit 125", script)

    def test_command_bytes_stay_encoded_and_exit_code_path_remains(self):
        command = "Write-Error '中文 failure'; exit 7"
        script = self.render(command)
        self.assertIn(base64.b64encode(command.encode('utf-16-le')).decode('ascii'), script)
        self.assertIn("$code=$LASTEXITCODE", script)
        self.assertIn("exit $code", script)

    def test_non_marker_argv_is_not_reinterpreted(self):
        command = ['custom-shell', '--flag', 'unchanged']
        self.assertEqual(sandbox._prepare_windows_utf8_shell_command(command), command)


@unittest.skipUnless(os.name == 'nt', 'requires actual Windows AppContainer')
class NativeShellBootstrapTests(unittest.TestCase):
    def test_native_readback_failure_code_secret_boundary_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / '工作 区'
            child = workspace / 'project'
            child.mkdir(parents=True)
            outside = root / 'parent-secret.txt'
            outside.write_text('must-not-be-readable', encoding='utf-8')
            runner = sandbox.SandboxRunner(workspace, root/'state', root/'trash', sandbox.SandboxLimits(timeout_seconds=20))
            with mock.patch.dict(os.environ, {'TIANGONG_SANDBOX_COMPAT':'0', 'OPENAI_API_KEY':'do-not-propagate'}, clear=False):
                written = runner.run([sandbox.WINDOWS_POWERSHELL_SHELL_MARKER,
                    "Set-Content -NoNewline -LiteralPath legacy.txt -Value sandboxed"], cwd=child, op_id='native-ps-write')
                self.assertEqual(written['containment'], 'windows-appcontainer')
                self.assertEqual(written['returncode'], 0, written)
                self.assertEqual((child/'legacy.txt').read_text(encoding='utf-8'), 'sandboxed')
                self.assertIn('project/legacy.txt', [p.replace('\\','/') for p in written['changed_files']])
                self.assertFalse(Path(written['sandbox_root']).exists())
                failed = runner.run([sandbox.WINDOWS_POWERSHELL_SHELL_MARKER,
                    "Write-Error 'sandbox failure'; exit 7"], cwd=child, op_id='native-ps-error')
                self.assertEqual(failed['containment'], 'windows-appcontainer')
                self.assertEqual(failed['returncode'], 7, failed)
                self.assertFalse(failed['ok'])
                self.assertTrue(failed['stderr'])
                self.assertFalse(Path(failed['sandbox_root']).exists())
                literal = str(outside).replace("'", "''")
                boundary = runner.run([sandbox.WINDOWS_POWERSHELL_SHELL_MARKER,
                    "if($env:OPENAI_API_KEY){exit 91};try{[IO.File]::ReadAllText('"+literal+"');exit 92}catch{exit 0}"],
                    cwd=child, op_id='native-ps-boundary')
                self.assertEqual(boundary['containment'], 'windows-appcontainer')
                self.assertEqual(boundary['returncode'], 0, boundary)
                self.assertEqual(outside.read_text(encoding='utf-8'), 'must-not-be-readable')
                self.assertFalse(Path(boundary['sandbox_root']).exists())


if __name__ == '__main__':
    unittest.main()
