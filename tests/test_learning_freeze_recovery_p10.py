"""Freeze failure/restart and final callback boundaries use existing authorities."""
from copy import deepcopy
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.learning_workflow import (
    LEGACY_PUBLICATION_FROZEN as FROZEN, MIGRATION_REQUIRED, build_draft,
    legacy_publication_blocked,
)
from tests.test_learning_publication_freeze_p10 import life, decision


def approved(life):
    life_id = life._active()['life_id']
    card = build_draft(life_id=life_id, scope={}, decision=decision(), source='user_direct')
    card['learning_evidence'] = {'source_id': 'keep-exact-proof', 'error': 'original-attempt'}
    life._scope_state()['learning'][card['learning_id']] = deepcopy(card)
    life.system.journal.append(life_id, 'learning.draft_created', {'learning': card},
                              actor='fixture', idempotency_key='fixture:approved')
    life._persist(life_id, force=True)
    return card


def test_new_freeze_event_is_registered_and_replays_after_disk_failure(life, monkeypatch):
    card = approved(life); life_id = card['life_id']
    with monkeypatch.context() as m:
        m.setattr(life, '_persist', lambda *a, **k: (_ for _ in ()).throw(OSError('disk-failed-after-journal')))
        with pytest.raises(OSError, match='disk-failed-after-journal'):
            life._freeze_legacy_learning(life_id=life_id, learning_id=card['learning_id'])
    assert life._scope_state()['learning'][card['learning_id']] == card
    # Persist the pre-transaction projection. The new instance must recover from
    # the journal, not from an accidentally retained in-memory frozen record.
    life.close()
    reopened = EmbeddedLifeRuntime(data_root=life.paths.data_root, runtime_root=life.paths.runtime_root, mode='embedded')
    try:
        reopened.scheduler.stop(timeout_seconds=2)
        saved = reopened._scope_state()['learning'][card['learning_id']]
        assert saved['status'] == MIGRATION_REQUIRED and saved['retryable'] is False
        assert saved['learning_evidence'] == card['learning_evidence']
        assert saved['draft_artifact'] == card['draft_artifact']
        before = deepcopy(saved)
        reopened._recover_approved_learning_cards(life_id=life_id)
        assert reopened._scope_state()['learning'][card['learning_id']] == before
        assert reopened.system.journal.verify(life_id)['valid']
    finally:
        reopened.close()


def test_same_freeze_retry_after_projection_failure_is_idempotent(life, monkeypatch):
    card = approved(life)
    with monkeypatch.context() as m:
        m.setattr(life, '_persist', lambda *a, **k: (_ for _ in ()).throw(OSError('projection-failure')))
        with pytest.raises(OSError):
            life._freeze_legacy_learning(life_id=card['life_id'], learning_id=card['learning_id'])
    first = life._freeze_legacy_learning(life_id=card['life_id'], learning_id=card['learning_id'])
    second = life._freeze_legacy_learning(life_id=card['life_id'], learning_id=card['learning_id'])
    assert first == second and first['reason_code'] == FROZEN
    assert life.system.journal.verify(card['life_id'])['valid']


def test_journal_failure_does_not_change_projection(life, monkeypatch):
    card = approved(life)
    with monkeypatch.context() as m:
        m.setattr(life.system.journal, 'append', lambda *a, **k: (_ for _ in ()).throw(OSError('journal-failed')))
        with pytest.raises(OSError):
            life._freeze_legacy_learning(life_id=card['life_id'], learning_id=card['learning_id'])
    assert life._scope_state()['learning'][card['learning_id']] == card


def test_frozen_draft_remains_discardable_and_terminal_history_is_untouched(life):
    card = approved(life)
    frozen = life._freeze_legacy_learning(life_id=card['life_id'], learning_id=card['learning_id'])
    assert frozen['learning']['can_discard_learning']
    result = life._learning_discard({'learning_id': card['learning_id']})
    terminal = deepcopy(result['learning'])
    assert terminal['status'] == 'discarded'
    reply = life._learning_publish({'learning_id': card['learning_id']})
    assert reply['reason_code'] == FROZEN and reply['learning'] == terminal


def test_gateway_nested_callback_rejects_relabelled_or_complete_capabilities():
    # Execute the exact existing nested function body, without bootstrapping a
    # networked Gateway. This is callback-unit evidence, not a complete worker run.
    from total_gateway import runtime as module
    path = Path(module.__file__)
    definitions = [n for n in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
                   if isinstance(n, ast.FunctionDef) and n.name == 'publish_learning_artifact']
    assert len(definitions) == 1
    calls = []
    def backend(*args, **kwargs):
        calls.append(args)
        return 200, {'ok': True, 'imported': [{'document_id': 'knowledge-proof'}]}, None
    env = dict(module.__dict__)
    env['runtime'] = SimpleNamespace(backend_service=SimpleNamespace(request=backend))
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), env)
    publish = env['publish_learning_artifact']
    for bad in ({'kind': 'skill'}, {'kind': 'tool'}, {'kind': 'kb'},
                {'kind': 'knowledge', 'skill_spec': {'steps': []}},
                {'kind': 'knowledge', 'target': 'skill'}):
        with pytest.raises(ValueError, match=FROZEN):
            publish(bad)
    assert calls == []
    reply = publish({'kind': 'knowledge', 'document': {'content': '# source words tool SKILL.md'}})
    assert reply['knowledge_document_id'] == 'knowledge-proof'
    assert len(calls) == 1 and calls[0][1] == '/api/v1/knowledge/import'


def test_direct_registry_writer_only_removes_existing_rows(tmp_path, monkeypatch):
    from v3 import duihua_qiaojie as bridge, peizhi
    path = tmp_path/'registry.json'
    old = {'id': 'old', 'mingcheng': 'old', 'leixing': 'gongju', 'zhuangtai': 'jihuo'}
    path.write_text(json.dumps({'nengli_list': [old], 'marker': 'existing'}), encoding='utf-8')
    monkeypatch.setattr(peizhi, 'NENGLI_ZHUCE_LUJING', path)
    before = path.read_bytes()
    for rows in ([{**old, 'id': 'new'}], [{**old, 'version': 'new'}], [old, old]):
        with pytest.raises(ValueError, match=FROZEN):
            bridge._write_registry_rows({'nengli_list': rows}, rows)
        assert path.read_bytes() == before
    bridge._write_registry_rows({'nengli_liebiao': [{'id': 'injected'}], 'marker': 'injected'}, [])
    saved = json.loads(path.read_bytes())
    assert saved['marker'] == 'existing' and bridge.registry_rows(saved) == []
