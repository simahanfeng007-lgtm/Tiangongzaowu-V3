"""Installed QA clears absent variables and restores its process environment."""

from pathlib import Path
import json
import os
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qa-installed-clean-first-run.ps1"


def test_environment_clear_and_restore_preserve_absence(tmp_path):
    source = SCRIPT.read_text(encoding="utf-8")
    functions = []
    for name in ("Capture-QAEnvironment", "Restore-QAEnvironment", "Set-IsolatedEnvironment"):
        match = re.search(r"(?ms)^function " + re.escape(name) + r" \{\n.*?^\}", source)
        assert match, name
        functions.append(match.group(0))
    command = "$ErrorActionPreference='Stop'\n" + "\n".join(functions) + r'''
$env:TIANGONG_QA_ENV_LEGACY='before'
$env:TIANGONG_QA_ENV_CREATED=$null
$env:NODE_OPTIONS='--qa-marker'
$snapshot=Capture-QAEnvironment
Set-IsolatedEnvironment -Bindings @{TIANGONG_QA_ENV_CREATED='during'; HOME=$env:TG_QA_TEST_HOME}
if ((Test-Path -LiteralPath Env:\TIANGONG_QA_ENV_LEGACY) -or (Test-Path -LiteralPath Env:\NODE_OPTIONS)) { throw 'Clear left present empty variables' }
Restore-QAEnvironment -Snapshot $snapshot
if (Test-Path -LiteralPath Env:\TIANGONG_QA_ENV_CREATED) { throw 'Restore left a newly introduced variable' }
if ($env:TIANGONG_QA_ENV_LEGACY -ne 'before' -or $env:NODE_OPTIONS -ne '--qa-marker') { throw 'Restore changed prior values' }
@{cleared=$true; restored=$true; introducedVariableAbsent=$true} | ConvertTo-Json -Compress
'''
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert powershell, "Release QA requires PowerShell"
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                            env={**os.environ, "TG_QA_TEST_HOME": str(tmp_path)},
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cleared": True, "restored": True, "introducedVariableAbsent": True}


def test_isolated_installed_qa_uses_packaged_node_with_windows_only_launch_path():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '$Json = & $CdpNode $CdpHelper' in source
    assert 'resources\\python\\Lib\\site-packages\\playwright\\driver\\node.exe' in source
    assert '$EnvironmentBindings.PATH = @(' in source
    assert '(Join-Path $WindowsRoot "System32\\WindowsPowerShell\\v1.0")' in source
