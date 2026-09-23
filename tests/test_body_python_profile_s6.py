"""Production Body handlers + real Windows child processes; no model or UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256
from runtime_security.composition_path import probe_composition_write_target
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig, OmniBodyError
from omni_body_skill.tools.sandbox_runtime import SandboxRunner, SandboxLimits, SandboxError


def body(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    rt = BodyRuntime.__new__(BodyRuntime)
    rt.workspace = workspace
    rt.config = BodyRuntimeConfig(workspace=str(workspace), allow_absolute_paths=True, allow_python=True,
        execution_profile_id=WORKSPACE_PYTHON_PROFILE_ID, execution_profile_sha256=WORKSPACE_PYTHON_PROFILE_SHA256,
        sandbox_enabled=True, sandbox_require_os_containment=True, sandbox_allow_deletions=False,
        sandbox_max_changed_mb=4)
    rt.backup_dir = workspace / '.omni_backups'
    rt.backup_dir.mkdir()
    rt._execution_state = threading.local()
    rt.sandbox = SandboxRunner(workspace, tmp_path / 'state', tmp_path / 'trash',
        SandboxLimits(max_changed_bytes=4*1024*1024))
    return rt


def bind(rt, path):
    rt.config.target_snapshot_sha256 = canonical_sha256(probe_composition_write_target(str(path), rt.workspace))


@pytest.mark.skipif(os.name != 'nt', reason='requires actual AppContainer execution')
def test_write_failed_run_fix_success_and_identical_output(tmp_path):
    rt = body(tmp_path)
    script = rt.workspace / 'main.py'
    bind(rt, script)
    rt._action_file_write('bad-script', str(script), {'content':
        "from pathlib import Path\nPath('summary.json').write_text('incorrect')\nraise SystemExit(5)\n"})
    bind(rt, script)
    failed = rt._action_python_run('failed-run', str(script), {'argv': [], 'timeout': 15})
    assert failed['success'] is False
    assert failed['execution']['returncode'] == 5
    assert failed['execution']['commit_state'] == 'discarded'
    assert not (rt.workspace / 'summary.json').exists()
    fixed = '''from pathlib import Path
import unittest,json
class Tests(unittest.TestCase):
    def test_one(self): self.assertEqual(2+3,5)
    def test_two(self): self.assertEqual(sum([1,2,3]),6)
    def test_three(self): self.assertEqual('paid'.upper(),'PAID')
suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
r=unittest.TextTestRunner().run(suite)
if not r.wasSuccessful(): raise SystemExit(1)
Path('summary.json').write_text(json.dumps({'total':6,'count':3}),encoding='utf-8')
'''
    bind(rt, script)
    rt._action_file_write('fixed-script', str(script), {'content': fixed})
    bind(rt, script)
    for index in range(2):
        result = rt._action_python_run('success-' + str(index), str(script), {'argv': [], 'timeout': 15})
        execution = result['execution']
        assert result['success'] and execution['returncode'] == 0, result
        assert 'Ran 3 tests' in execution['stderr'] and 'OK' in execution['stderr']
        assert execution['containment'] == 'windows-appcontainer'
        assert execution['network'] == 'denied' and execution['commit_state'] == 'committed'
        assert execution['deleted_files'] == []
        if index:
            assert execution['changed_files'] == []
        else:
            assert execution['changed_files'] == ['summary.json']
        assert json.loads((rt.workspace / 'summary.json').read_text()) == {'total':6,'count':3}


@pytest.mark.skipif(os.name != 'nt', reason='requires actual AppContainer execution')
def test_python_profile_delete_refuses_entire_commit(tmp_path):
    rt = body(tmp_path)
    (rt.workspace / 'retained.txt').write_text('keep')
    script = rt.workspace / 'main.py'
    script.write_text("from pathlib import Path\nPath('retained.txt').unlink()\nPath('new.txt').write_text('no commit')")
    bind(rt, script)
    with pytest.raises(SandboxError, match='sandbox_deletion_forbidden'):
        rt._action_python_run('delete', str(script), {'argv': [], 'timeout': 15})
    assert (rt.workspace / 'retained.txt').read_text() == 'keep'
    assert not (rt.workspace / 'new.txt').exists()


@pytest.mark.parametrize('arguments', [{'code':'print(1)'}, {'argv':'bad'}, {'timeout':0}, {'timeout':61}, {'env':{}}])
def test_python_profile_rejects_unbound_execution_inputs(tmp_path, arguments):
    rt = body(tmp_path)
    script = rt.workspace / 'main.py'
    script.write_text('print(1)')
    bind(rt, script)
    with pytest.raises((ValueError, OmniBodyError)):
        rt._action_python_run('invalid', str(script), arguments)


def test_python_profile_requires_fresh_script_snapshot_and_strict_runner(tmp_path):
    rt = body(tmp_path)
    script = rt.workspace / 'main.py'
    script.write_text('print(1)')
    bind(rt, script)
    script.write_text('print(2)')
    with pytest.raises(OmniBodyError, match='signed target snapshot changed'):
        rt._action_python_run('stale', str(script), {'timeout': 15})
    bind(rt, script)
    rt.config.sandbox_require_os_containment = False
    with pytest.raises(OmniBodyError, match='containment'):
        rt._action_python_run('uncontained', str(script), {'timeout': 15})


@pytest.mark.skipif(os.name != 'nt', reason='requires native private storage preparation')
def test_bound_script_changed_during_workspace_copy_never_launches(tmp_path, monkeypatch):
    from omni_body_skill.tools import sandbox_runtime as sandbox
    rt = body(tmp_path)
    script = rt.workspace / 'main.py'
    script.write_text('print(1)')
    bind(rt, script)
    copy = sandbox._copy_workspace
    def changed(source, target, limit):
        copy(source, target, limit)
        (target / 'main.py').write_text('print(2)')
    monkeypatch.setattr(sandbox, '_copy_workspace', changed)
    monkeypatch.setattr(sandbox, '_run_windows_appcontainer', lambda *a, **k: pytest.fail('changed bound script launched'))
    with pytest.raises(SandboxError, match='sandbox_bound_input_changed'):
        rt._action_python_run('changed-copy', str(script), {'timeout': 15})
