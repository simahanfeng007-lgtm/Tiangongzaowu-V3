"""Explicit memory metadata survives storage and historical classifier upgrades."""
from copy import deepcopy
from types import SimpleNamespace
import time
import pytest
from contracts import canonical_sha256
from life_service import embedded_runtime as module
from life_service.embedded_runtime import EmbeddedLifeRuntime


@pytest.fixture
def life(tmp_path):
    runtime = EmbeddedLifeRuntime(data_root=tmp_path/'data', runtime_root=tmp_path/'runtime', mode='embedded')
    try:
        yield runtime
    finally:
        runtime.close()


def test_explicit_deadline_reaches_assertion_l4_and_recall(life, monkeypatch):
    deadline = time.time_ns() // 1_000_000 + 60_000
    payload = {'memory_id': 'mem_deadline', 'content': {'text': '今天只是一段文字'},
               'explicit_memory': True, 'expires_at_ms': deadline}
    result = life._memory_assert(payload)
    assert result['assertion']['expires_at_ms'] == deadline
    store = life._contract_store()
    assertion = store.get_latest_memory_assertion(result['contract_memory_id'])
    assert assertion.expires_at_ms == deadline
    l4 = [d for d in store.list_derivations_for_memory(assertion.memory_id) if d.layer == 'L4_EXPLICIT']
    assert len(l4) == 1 and l4[0].expires_at_ms == deadline
    assert life._memory_assert(payload)['duplicate'] is True
    assert any(row['memory_id'] == 'mem_deadline' for row in life._memory_search({'query': '今天'})['results'])
    clock = SimpleNamespace(**{name: getattr(time, name) for name in dir(time) if not name.startswith('__')})
    clock.time_ns = lambda: (deadline + 1) * 1_000_000
    monkeypatch.setattr(module, 'time', clock)
    assert life._memory_search({'query': '今天'})['results'] == []


def test_prose_does_not_create_l4_or_expiry(life):
    result = life._memory_assert({'memory_id': 'mem_prose', 'content': {'text': '今天请永久记住这个规则'}})
    assert 'expires_at_ms' not in result['assertion']
    assert result['assertion']['memory_type'] == 'semantic'
    assert not any(d.layer == 'L4_EXPLICIT' for d in life._contract_store().list_derivations_for_memory(result['contract_memory_id']))


def test_historical_classification_is_preserved_on_duplicate(life, monkeypatch):
    original = module.classify_memory
    def legacy(**kwargs):
        value = deepcopy(original(**kwargs))
        c = value['classification']
        c.update(schema='tiangong.life.memory-classifier.v1', memory_type='rule', assertion_kind='hard_constraint', causal_role='constraint')
        c['classification_sha256'] = canonical_sha256({k:v for k,v in c.items() if k != 'classification_sha256'})
        return value
    payload = {'memory_id': 'mem_historical', 'content': {'text': '必须保留历史记录'}}
    with monkeypatch.context() as context:
        context.setattr(module, 'classify_memory', legacy)
        before = life._memory_assert(payload)
    after = life._memory_assert(payload)
    assert after['duplicate'] is True
    assert after['assertion']['classification'] == before['assertion']['classification']
    assert after['memory_change_seq'] == before['memory_change_seq']
    from life_service.embedded_runtime import EmbeddedLifeError
    with pytest.raises(EmbeddedLifeError, match='id_conflict'):
        life._memory_assert({**payload, 'content': {'text': 'changed'}})
