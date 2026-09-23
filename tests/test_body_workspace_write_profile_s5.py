from __future__ import annotations

import hashlib
from pathlib import Path
import threading

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256
from runtime_security.composition_path import probe_composition_write_target
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig, OmniBodyError


def body(tmp_path):
    # Exercise the real file handlers/broker without unrelated manifest startup.
    rt = BodyRuntime.__new__(BodyRuntime)
    rt.workspace = tmp_path
    rt.config = BodyRuntimeConfig(workspace=str(tmp_path), allow_absolute_paths=True,
        execution_profile_id=WORKSPACE_WRITE_PROFILE_ID,
        execution_profile_sha256=WORKSPACE_WRITE_PROFILE_SHA256)
    rt.backup_dir = tmp_path / '.omni_backups'
    rt.backup_dir.mkdir()
    rt._execution_state = threading.local()
    return rt


def bind(rt, path):
    rt.config.target_snapshot_sha256 = canonical_sha256(probe_composition_write_target(str(path), rt.workspace))


def test_new_and_unchanged_file_have_actual_content_evidence(tmp_path):
    rt = body(tmp_path)
    path = tmp_path / 'output.txt'
    bind(rt, path)
    first = rt._action_file_write('first', str(path), {'content': '中文 result'})
    receipt = first['write_evidence']
    assert receipt['pre']['exists'] is False
    assert receipt['post']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt['post']['bytes'] == len(path.read_bytes())
    assert receipt['changed'] is True
    bind(rt, path)
    second = rt._action_file_write('second', str(path), {'content': '中文 result'})
    assert second['write_evidence']['changed'] is False
    assert second['write_evidence']['rollback']['available'] is True
    assert Path(second['write_evidence']['rollback']['backup_path']).read_bytes() == path.read_bytes()


def test_mkdir_and_code_write_evidence_and_no_hidden_execution(tmp_path):
    rt = body(tmp_path)
    folder = tmp_path / 'project'
    bind(rt, folder)
    result = rt._action_file_mkdir('folder', str(folder), {'exist_ok': True})
    assert result['write_evidence']['post']['is_dir'] is True
    script = folder / 'main.py'
    bind(rt, script)
    with pytest.raises(OmniBodyError, match='syntax_check=false'):
        rt._action_code_write('bad', str(script), {'content': 'print(1)'})
    assert not script.exists()
    result = rt._action_code_write('write', str(script), {'content': 'print(1)', 'syntax_check': False})
    assert result['write_evidence']['post']['sha256']
    assert result['quality_checks'] == []


@pytest.mark.parametrize('extra', [{'count': 0}, {'count': 1, 'regex': True}, {'count': 1, 'allow_noop': True},
                                   {'count': 2}, {'count': 1, 'replace': 'original'}])
def test_invalid_patch_cannot_write(tmp_path, extra):
    rt = body(tmp_path)
    path = tmp_path / 'main.py'
    path.write_text('original', encoding='utf-8')
    bind(rt, path)
    with pytest.raises(OmniBodyError):
        rt._action_code_patch_replace('bad', str(path), {'find': 'original', 'replace': 'updated', **extra})
    assert path.read_text() == 'original'


def test_patch_receipt_and_stale_signed_snapshot(tmp_path):
    rt = body(tmp_path)
    path = tmp_path / 'main.py'
    path.write_text('original', encoding='utf-8')
    bind(rt, path)
    result = rt._action_code_patch_replace('patch', str(path), {'find': 'original', 'replace': 'updated', 'count': 1})
    assert result['write_evidence']['changed'] is True
    assert result['replacements'] == 1
    with pytest.raises(OmniBodyError, match='signed target snapshot changed'):
        rt._action_file_write('stale', str(path), {'content': 'incorrect'})
    assert path.read_text() == 'updated'


def test_concurrent_change_during_preparation_is_not_overwritten(tmp_path, monkeypatch):
    rt = body(tmp_path)
    path = tmp_path / 'main.py'
    path.write_text('original')
    bind(rt, path)
    original = rt._snapshot
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text('host change')
        return result
    monkeypatch.setattr(rt, '_snapshot', changed)
    with pytest.raises(OmniBodyError, match='signed target snapshot changed|changed concurrently'):
        rt._action_file_write('race', str(path), {'content': 'incorrect'})
    assert path.read_text() == 'host change'


def test_existing_directory_does_not_backup_its_contents(tmp_path, monkeypatch):
    rt = body(tmp_path)
    folder = tmp_path / 'existing'
    folder.mkdir()
    (folder / 'retained.txt').write_text('keep')
    bind(rt, folder)
    monkeypatch.setattr(rt, '_snapshot', lambda *args: pytest.fail('no recursive backup for unchanged mkdir'))
    result = rt._action_file_mkdir('noop', str(folder), {'exist_ok': True})
    assert result['write_evidence']['changed'] is False
    assert result['write_evidence']['rollback']['available'] is True
