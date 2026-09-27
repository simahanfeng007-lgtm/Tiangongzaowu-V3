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


def test_public_preflight_and_engine_share_empty_actual_revision_contract(tmp_path):
    from omni_body_skill.tool_contracts import validate_tool_request
    from v3.novel_system import NovelSystemEngine
    target = tmp_path / 'managed'
    engine = NovelSystemEngine(target)
    request = validate_tool_request('novel.project.create', str(target),
        {'title': '短篇', 'genre': '实验', 'planned_chapters': 1, 'target_words': 5}, workspace=tmp_path)
    assert request['ok'], request
    engine.create_project(request['args'])
    # No invented characters, events, outcomes, or emotional annotations.
    engine.update_blueprint({'section': 'chapters', 'data': [{'number': 1, 'title': '星光'}]})
    engine.compile_blueprint({})
    for content, revision_of in [('星光亮起。', None), ('星光熄灭。', 'current')]:
        args = {'chapter_number': 1}
        if revision_of:
            args['revision_of'] = engine._ledger()[-1]['sha256']
        checkout = validate_tool_request('novel.chapter.checkout', str(target), args, workspace=tmp_path)
        assert checkout['ok'], checkout
        lease = engine.checkout_chapter(checkout['args'])
        request = validate_tool_request('novel.chapter.submit', str(target),
            {'lease_id': lease['lease_id'], 'chapter_number': 1, 'title': '星光', 'content': content, 'actual': {}}, workspace=tmp_path)
        assert request['ok'], request
        assert request['args']['actual'] == {}
        result = engine.submit_chapter(request['args'])
        assert result['committed']
    assert Path(result['chapter_path']).read_text(encoding='utf-8') == '星光熄灭。\n'
    assert len(engine._ledger()) == 1


def test_public_preflight_single_scene_and_positive_scope_have_no_old_thresholds(tmp_path):
    from omni_body_skill.tool_contracts import validate_tool_request
    for action, args in (
        ('novel.project.create', {'title':'x', 'genre':'x', 'planned_chapters':1, 'target_words':100000}),
        ('novel.scene.design', {'candidates':[{'title':'x','target_chapter':1}], 'selected_index':0, 'expected_state_hash':'a'*64}),
        ('novel.blueprint.update', {'section':'chapters','data':[{'number':1, 'event_ids':[]}]}),
    ):
        result = validate_tool_request(action, str(tmp_path/'managed'), args, workspace=tmp_path)
        assert result['ok'], result
    invalid = validate_tool_request('novel.chapter.submit', str(tmp_path/'managed'),
        {'lease_id':'lease_'+'a'*32, 'chapter_number':1,'title':'x','content':'x','actual':{'events':[None]}}, workspace=tmp_path)
    assert not invalid['ok']
