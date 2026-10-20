"""Compiled terminal descriptions with .js names are never JavaScript."""

import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_only_valid_git_terminfo_binary_is_excluded_from_javascript(tmp_path):
    source = (ROOT / "scripts/check.ps1").read_text(encoding="utf-8")
    function = re.search(r"(?ms)^function Test-GitCompiledTerminfo \{\n.*?^\}", source)
    assert function
    assert '-TerminfoRoot $GitTerminfoRoot)' in source
    assert '-TerminfoRoot $GitSharedTerminfoRoot)' in source
    assert 'runtime\\python312\\git\\usr\\share\\terminfo' in source
    terminfo = tmp_path / "git/usr/lib/terminfo"
    (terminfo / "78").mkdir(parents=True)
    binary = struct.pack("<6H", 0x021E, 2, 0, 0, 0, 0) + b"x\0"
    (terminfo / "78/xterm.js").write_bytes(binary)
    (terminfo / "78/xterm-old.js").write_bytes(struct.pack("<6H", 0x011A, 2, 0, 0, 0, 0) + b"x\0")
    (terminfo / "78/actual.js").write_text("console.log('still syntax checked');", encoding="utf-8")
    (terminfo / "78/fake.js").write_bytes(b"\x1e\x02console.log('not valid terminfo')")
    (tmp_path / "product.js").write_bytes(binary)
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert powershell
    command = "$ErrorActionPreference='Stop'\n" + function.group(0) + r'''
$root=$env:TG_TERMINFO_ROOT
@{
  current=Test-GitCompiledTerminfo -Path (Join-Path $root '78/xterm.js') -TerminfoRoot $root
  old=Test-GitCompiledTerminfo -Path (Join-Path $root '78/xterm-old.js') -TerminfoRoot $root
  realJs=Test-GitCompiledTerminfo -Path (Join-Path $root '78/actual.js') -TerminfoRoot $root
  invalidHeader=Test-GitCompiledTerminfo -Path (Join-Path $root '78/fake.js') -TerminfoRoot $root
  outside=Test-GitCompiledTerminfo -Path $env:TG_PRODUCT_JS -TerminfoRoot $root
} | ConvertTo-Json -Compress
'''
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                            env={**os.environ, "TG_TERMINFO_ROOT": str(terminfo), "TG_PRODUCT_JS": str(tmp_path / "product.js")},
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(current=True, old=True, realJs=False, invalidHeader=False, outside=False)
