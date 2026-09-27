"""Counterexamples for the retained novel helper and transaction receipt boundary."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_source(relative):
    spec = importlib.util.spec_from_file_location('novel_observer_test', ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_short_prose_missing_terms_and_old_failed_status_do_not_prevent_assembly(tmp_path, capsys):
    helper = load_source('src/bundled_skills/novel-creation/scripts/novel_tool.py')
    prose = tmp_path / '正文' / '第01章.md'
    prose.parent.mkdir()
    prose.write_text('星光。', encoding='utf-8')
    (tmp_path / 'project.json').write_text('{"title":"short"}')
    historical = prose.parent / '第01章.status.json'
    historical.write_text('{"status":"failed"}')
    original = historical.read_bytes()
    observed = helper.audit_text('星光。', 2500)
    assert observed['status'] == 'observed'
    assert not observed['meets_configured_count']
    assert observed['content_quality'] == 'unassessed'
    (tmp_path / 'story_contract.json').write_text('{"forbidden_drift":["星光"]}', encoding='utf-8')
    card = tmp_path / '章节卡' / '第01章.json'
    card.parent.mkdir()
    card.write_text('{"must_include":["终点"]}', encoding='utf-8')
    result = helper.contract_check_text(tmp_path, prose, 1)
    assert result['literal_matches'] == [
        {'field': 'must_include', 'term': '终点', 'present': False},
        {'field': 'forbidden_drift', 'term': '星光', 'present': True}]
    output = tmp_path / 'assembled.txt'
    assert helper.command_package(argparse.Namespace(project_dir=str(tmp_path), output=str(output))) == 0
    assert '星光。' in output.read_text(encoding='utf-8')
    assert json.loads(capsys.readouterr().out)['content_quality'] == 'unassessed'
    assert historical.read_bytes() == original
    with pytest.raises(FileExistsError):
        helper.command_package(argparse.Namespace(project_dir=str(tmp_path), output=str(output)))


def test_corrupt_contract_is_not_silently_read_as_missing(tmp_path):
    helper = load_source('src/bundled_skills/novel-creation/scripts/novel_tool.py')
    path = tmp_path / 'broken.json'
    path.write_text('{invalid')
    with pytest.raises(json.JSONDecodeError):
        helper.read_json(path)


def test_auxiliary_projection_error_does_not_turn_a_commit_into_failed_execution(tmp_path):
    from omni_body_skill.tools import novel_system as adapter
    engine = SimpleNamespace(submit_chapter=lambda args: {'success': True, 'committed': True, 'content_quality': 'unassessed'})
    with mock.patch.object(adapter, 'NovelSystemEngine', return_value=engine), mock.patch.object(adapter, '_sync_managed_workspace', side_effect=OSError('disk')):
        result = adapter.handle_novel_system_action(SimpleNamespace(_resolve=lambda *a, **kw: tmp_path), 'op1', 'novel.chapter.submit', str(tmp_path), {})
    assert result['success'] and result['committed']
    assert result['execution_state'] == 'completed'
    assert result['workspace_projection']['status'] == 'unavailable'


def test_storage_failure_receipt_requires_reconciliation(tmp_path):
    from omni_body_skill.tools import novel_system as adapter
    engine = SimpleNamespace(submit_chapter=mock.Mock(side_effect=OSError('disk')))
    with mock.patch.object(adapter, 'NovelSystemEngine', return_value=engine):
        result = adapter.handle_novel_system_action(SimpleNamespace(_resolve=lambda *a, **kw: tmp_path), 'op1', 'novel.chapter.submit', str(tmp_path), {})
    assert not result['success']
    assert result['ambiguous_effect'] and result['reconciliation_required']
    assert result['execution_state'] == 'unknown'
