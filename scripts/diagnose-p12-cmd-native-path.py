"""Development-only Win32 path observation inside the existing AppContainer.

Runs harmless controlled commands in a temporary workspace. It never patches
product modules, grants parent ACLs, changes limits or turns observations into
product acceptance. The launcher/source hashes and each error are preserved.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]

WORKER = r'''
import ctypes, json, os, pathlib, subprocess
from ctypes import wintypes
root = pathlib.Path.cwd()
project = root / 'project'
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
k.GetLongPathNameW.restype = wintypes.DWORD
k.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
k.GetFileAttributesW.restype = wintypes.DWORD
k.SetCurrentDirectoryW.argtypes = [wintypes.LPCWSTR]
k.SetCurrentDirectoryW.restype = wintypes.BOOL
k.FindFirstFileW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.WIN32_FIND_DATAW)]
k.FindFirstFileW.restype = wintypes.HANDLE
k.FindClose.argtypes = [wintypes.HANDLE]

def emit(obj):
    print(json.dumps(obj, ensure_ascii=True), flush=True)

for value in (str(root), str(project), 'project', str(project / 'input.txt')):
    for name in ('GetFileAttributesW', 'GetLongPathNameW', 'FindFirstFileW'):
        ctypes.set_last_error(0)
        extra = {}
        if name == 'GetFileAttributesW':
            result = k.GetFileAttributesW(value)
            ok = result != 0xffffffff
        elif name == 'GetLongPathNameW':
            buf = ctypes.create_unicode_buffer(32768)
            result = k.GetLongPathNameW(value, buf, len(buf))
            ok = 0 < result < len(buf)
            if ok: extra['resolved'] = buf.value
        else:
            data = wintypes.WIN32_FIND_DATAW()
            result = k.FindFirstFileW(value, ctypes.byref(data))
            ok = result != wintypes.HANDLE(-1).value
        error = ctypes.get_last_error()
        if name == 'FindFirstFileW' and ok:
            extra['name'] = data.cFileName
            k.FindClose(result)
        emit({'kind': 'winapi', 'path': value, 'api': name, 'ok': ok, 'error': error, **extra})
ctypes.set_last_error(0)
ok = bool(k.SetCurrentDirectoryW(str(project)))
emit({'kind': 'winapi', 'api': 'SetCurrentDirectoryW', 'ok': ok, 'error': ctypes.get_last_error()})
os.chdir(root)
cmd = subprocess.list2cmdline([os.environ.get('COMSPEC') or 'cmd.exe'])
# These are comparisons, not replacement commands used by the product.
cases = {
    'absolute-cd': f'cd /d "{project}" && echo native-ok>result.txt',
    'relative-cd': 'cd /d project && echo native-ok>result.txt',
    'absolute-read': f'type "{project / "input.txt"}"',
    'absolute-dir': f'dir /b "{project}"',
    'relative-dir': 'dir /b project',
}
for name, body in cases.items():
    target = project / 'result.txt'
    target.unlink(missing_ok=True)
    command = cmd + ' /d /s /c "' + body + '"'
    try:
        cp = subprocess.run(command, cwd=str(root), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        emit({'kind':'cmd','case':name,'returncode':cp.returncode,
              'stdout':cp.stdout.decode('utf-8',errors='replace'),
              'stderr':cp.stderr.decode('utf-8',errors='replace'),
              'readback':target.read_text().strip() if target.exists() else None})
    except subprocess.TimeoutExpired:
        emit({'kind':'cmd','case':name,'error':'child_timeout'})
'''


def main() -> int:
    if os.name != 'nt':
        raise SystemExit('Native Windows is required; no portable substitute.')
    compile(WORKER, '<p12-native-worker>', 'exec')
    out = Path(os.environ['RUNNER_TEMP']) / 'p12-path-observation'
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    if head != os.environ['GITHUB_SHA']:
        raise SystemExit('Unexpected observer commit')
    paths = ['scripts/diagnose-p12-cmd-native-path.py',
             'src/omni_body_skill/tools/sandbox_runtime.py',
             'src/omni_body_skill/tools/windows_appcontainer.py',
             'src/omni_body_skill/tools/portable_text.py']
    identities = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    pkg = types.ModuleType('p12_readonly_probe')
    pkg.__path__ = [str(ROOT / 'src/omni_body_skill/tools')]
    sys.modules[pkg.__name__] = pkg
    sandbox = importlib.import_module(pkg.__name__ + '.sandbox_runtime')
    identity = {'head':head, 'inputs':identities, 'evidence_mode':'NATIVE_API_DIAGNOSTIC',
                'retirement_authorized':False, 'product_acceptance':False,
                'acl_changes_beyond_existing_launcher':False, 'timeout_seconds':20}
    (out / 'identity.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
    os.environ['TIANGONG_SANDBOX_COMPAT'] = '0'
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        workspace = root / '工作 区'
        (workspace / 'project').mkdir(parents=True)
        (workspace / 'project/input.txt').write_text('fixture-input', encoding='utf-8')
        runner = sandbox.SandboxRunner(workspace, root/'state', root/'trash', sandbox.SandboxLimits(timeout_seconds=20))
        result = runner.run([sys.executable, '-c', WORKER], cwd=workspace, op_id='p12-native-path-api')
        (out/'observation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('P12_NATIVE_API=' + json.dumps(result, ensure_ascii=True), flush=True)
        if result['containment'] != 'windows-appcontainer' or result['returncode'] != 0:
            return 1
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--'], cwd=ROOT, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
