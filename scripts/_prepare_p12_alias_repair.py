"""Temporary, exact-source development preparation; never part of product runtime."""
from __future__ import annotations
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

BASE = '9e941b22c18e9645ea5c47f94d7b52b439f1653e'
REPO = 'simahanfeng007-lgtm/Tiangongzaowu-V3'
ROOT = Path.cwd()
OUT = Path(os.environ['RUNNER_TEMP']) / 'p12-repair-evidence'
PATCH = Path('docs/capability-composition/p12-alias-repair.patch')
TEST_SOURCE = Path('docs/capability-composition/p12-alias-tests.py')
SHELL_TEST_SOURCE = Path('docs/capability-composition/p12-shell-tests.py')
PRODUCT = Path('src/omni_body_skill/tools/sandbox_runtime.py')
TEST = Path('tests/test_sandbox_workspace_alias_p12.py')
SHELL_TEST = Path('tests/test_sandbox_shell_bootstrap_p12.py')
VERSION = Path('src/total_gateway/verification_plane.py')
GUARD = Path('tests/golden/p19_r2/test_freeze_and_guards.py')
FREEZE = Path('docs/p19-r2/m6/VERIFICATION_PLANE_FREEZE.json')
CALIBRATION = Path('tests/golden/p19_r2/test_calibration_and_stability.py')


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def run(args, *, name, extra_env=None):
    env = os.environ.copy()
    for key in ('UPDATE_FREEZE', 'UPDATE_FINGERPRINT', 'UPDATE_GOLDEN', 'UPDATE_PERF', 'GH_TOKEN'):
        env.pop(key, None)
    env.update(extra_env or {})
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    (OUT / (name + '.log')).write_bytes(result.stdout)
    print(result.stdout.decode('utf-8', errors='replace'), flush=True)
    if result.returncode:
        raise RuntimeError(f'{name} failed with exit {result.returncode}')


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
    if actual != os.environ['GITHUB_SHA'] or os.environ['GITHUB_REPOSITORY'] != REPO:
        raise RuntimeError('Unexpected candidate/repository identity')
    subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, actual], check=True)
    expected = {
        PRODUCT.as_posix(): '6e6fe73c81744e1a3870f48da3fbb711b1cf5a1e',
        'src/omni_body_skill/tools/portable_text.py': '33e7fd07fc2138df682a2da43e6f41895efe9265',
        VERSION.as_posix(): 'b0ddd80a83fbc390617a9aae58c838a52e75aa3c',
        GUARD.as_posix(): '7bc0922e422fbb312738f3b0ee3a9eb6f277c816',
        'scripts/sync-generated-sources.py': '6bcac7178e65b25181ff33dfbdefb73a6adf2924',
        'source-ownership.json': '2fd5708e7ced67f964478e0cb6f4a33e17ec77cc',
    }
    for path, sha in expected.items():
        if blob(Path(path).read_bytes()) != sha:
            raise RuntimeError('Preparation input changed: ' + path)
    before_freeze = json.loads(FREEZE.read_bytes())
    run(['git', 'apply', '--check', str(PATCH)], name='patch-check')
    run(['git', 'apply', str(PATCH)], name='patch-apply')
    if hashlib.sha256(PRODUCT.read_bytes()).hexdigest() != 'dde9ad5f1807bba733b0d3a8ae4cc893608e9c0b2580fbe199884e1ac5311292':
        raise RuntimeError('Initial alias candidate changed')

    # Native observations bind this extension to the original alias candidate.
    # No ACL, timeout, environment allowlist or execution authority is changed.
    text = PRODUCT.read_text(encoding='utf-8')
    old_target = 'sandbox_text = str(sandbox.resolve(strict=False))'
    if text.count(old_target) != 1:
        raise RuntimeError('Expected one destination spelling boundary')
    text = text.replace(old_target, 'sandbox_text = str(sandbox.expanduser().absolute())', 1)
    bootstrap = (
        "$tgSavedAutoload=$PSModuleAutoLoadingPreference;"
        "$PSModuleAutoLoadingPreference='None';"
        "try{"
        "Import-Module ($PSHOME+'\\Modules\\Microsoft.PowerShell.Management\\Microsoft.PowerShell.Management.psd1') -ErrorAction Stop;"
        "Import-Module ($PSHOME+'\\Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop;"
        "}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 125}"
        "finally{$PSModuleAutoLoadingPreference=$tgSavedAutoload};"
    )
    anchor = '        script = (\n            "$utf8='
    if text.count(anchor) != 1:
        raise RuntimeError('Expected one marked PowerShell bootstrap boundary')
    replacement = '        script = (\n            # Load the two system modules before hermetic auto-discovery can stall.\n            ' + repr(bootstrap) + '\n            "$utf8='
    text = text.replace(anchor, replacement, 1)
    ast.parse(text, filename=PRODUCT.as_posix())
    PRODUCT.write_bytes(text.encode('utf-8'))

    data = TEST_SOURCE.read_bytes()
    if hashlib.sha256(data).hexdigest() != '3835972e8844f6adeb55af2f7a058e96abe0db6b5aa2a08630b476ecbbf6f994':
        raise RuntimeError('Original alias regression bytes changed')
    # The target spelling is now retained; keep the non-recursive replacement
    # assertion while aligning only this new fixture with that explicit contract.
    if data.count(b'self.real / "private"') != 2:
        raise RuntimeError('Unexpected destination-spelling fixture')
    data = data.replace(b'self.real / "private"', b'self.alias / "private"')
    TEST.write_bytes(data)
    shell_data = SHELL_TEST_SOURCE.read_bytes()
    if hashlib.sha256(shell_data).hexdigest() != '402bd9b830bd16c0b824a046280eef15b5a454bd5e1963c0ed4d31e411e2cdb2':
        raise RuntimeError('Bootstrap regression bytes changed')
    SHELL_TEST.write_bytes(shell_data)
    for path in (TEST, SHELL_TEST):
        ast.parse(path.read_text(encoding='utf-8'), filename=path.as_posix())
    VERSION.write_bytes(VERSION.read_bytes().replace(b'"1.14"', b'"1.15"'))
    GUARD.write_bytes(GUARD.read_bytes().replace(b'1.14', b'1.15'))
    run([sys.executable, 'scripts/sync-generated-sources.py', '--write'], name='official-mirrors')
    run([sys.executable, '-m', 'pytest', '-q', GUARD.as_posix() + '::VerificationPlaneFreezeGuardTests::test_freeze_manifest_unchanged'],
        name='official-freeze-generation', extra_env={'UPDATE_FREEZE': '1'})
    after_freeze = json.loads(FREEZE.read_bytes())
    changed = {key for key in before_freeze if before_freeze[key] != after_freeze.get(key)}
    if changed != {'verification_plane_version', 'authority_surface_sha256'}:
        raise RuntimeError('Unexpected freeze semantics changed: ' + repr(changed))
    old_surface, new_surface = before_freeze['authority_surface_sha256'], after_freeze['authority_surface_sha256']
    if old_surface.keys() != new_surface.keys() or {p for p in old_surface if old_surface[p] != new_surface[p]} != {PRODUCT.as_posix(), VERSION.as_posix()}:
        raise RuntimeError('Unexpected authority surface mutation')
    run([sys.executable, 'scripts/sync-generated-sources.py', '--check-committed'], name='normal-mirrors')
    run([sys.executable, 'scripts/check-source-authority.py'], name='normal-source-authority')
    run([sys.executable, '-m', 'pytest', '-q', GUARD.as_posix()], name='normal-freeze-and-guards')
    calibration_text = CALIBRATION.read_text(encoding='utf-8')
    fingerprint_nodes = []
    for node in ast.parse(calibration_text).body:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith('test_'):
                    if 'UPDATE_FINGERPRINT' in ast.get_source_segment(calibration_text, method):
                        fingerprint_nodes.append(CALIBRATION.as_posix() + '::' + node.name + '::' + method.name)
    if len(fingerprint_nodes) != 1:
        raise RuntimeError('Expected one existing fingerprint guard')
    run([sys.executable, '-m', 'pytest', '-q', *fingerprint_nodes], name='normal-independent-fingerprint')
    run([sys.executable, '-m', 'pytest', '-q', '-ra', TEST.as_posix(), SHELL_TEST.as_posix()], name='alias-and-bootstrap-regressions')
    dirs = ['readable-python-source/omni_body_skill', 'app/backend/tiangong-backend/omni_body_skill',
            'app/backend/tiangong-backend/_internal/omni_body_skill',
            'app/backend/tiangong-backend/v3/bundled_skills/omni_body_skill',
            'app/backend/tiangong-backend/_internal/v3/bundled_skills/omni_body_skill']
    allowed = {PRODUCT.as_posix(), VERSION.as_posix(), GUARD.as_posix(), FREEZE.as_posix(), TEST.as_posix(), SHELL_TEST.as_posix()}
    allowed.update(d + '/tools/sandbox_runtime.py' for d in dirs)
    allowed.update(d + '/.tiangong-generated-source.json' for d in dirs)
    modified = subprocess.check_output(['git', 'diff', '--name-only', '-z']).decode().split('\0')
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z']).decode().split('\0')
    paths = sorted((set(modified) | set(untracked)) - {''})
    if set(paths) != allowed:
        raise RuntimeError('Unexpected prepared delta: ' + repr(set(paths) ^ allowed))
    entries = [{'path': p, 'sha': blob(Path(p).read_bytes()), 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest(), 'size': Path(p).stat().st_size} for p in paths]
    receipt = {'base': BASE, 'observer_commit': actual, 'evidence_mode': 'PREPARED_WORKTREE_NOT_COMMITTED_SOURCE',
               'entries': entries, 'retirement_authorized': False, 'p12_authorized': False}
    (OUT / 'prepared-entries.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'prepared-tracked.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary']))
    print('P12_PREPARED_ENTRIES=' + json.dumps(receipt, sort_keys=True), flush=True)


def upload():
    receipt = json.loads((OUT / 'prepared-entries.json').read_bytes())
    for entry in receipt['entries']:
        data = Path(entry['path']).read_bytes()
        if blob(data) != entry['sha']:
            raise RuntimeError('Prepared bytes changed before object transfer')
        req = urllib.request.Request(
            'https://api.github.com/repos/' + REPO + '/git/blobs',
            data=json.dumps({'content': base64.b64encode(data).decode(), 'encoding': 'base64'}).encode(),
            headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'}, method='POST')
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
        if result.get('sha') != entry['sha']:
            raise RuntimeError('Remote blob identity differs')
    print('P12_REMOTE_BLOBS=' + json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['upload']:
        upload()
    elif not sys.argv[1:]:
        prepare()
    else:
        raise SystemExit('Unsupported operation')
