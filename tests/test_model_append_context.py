"""The entire previous successful wire must remain a prefix of the next turn."""
import dataclasses
import json
from copy import deepcopy

import pytest

from test_dictionary_model_lifecycle import endpoint, Client, event
from test_model_cache_layout import pair
from v3.jineng.model_context_cache import AppendOnlyContext
from v3.jineng.model_transport_registry import get_model_transport
from v3.jineng import model_transport_executor as executor
from v3.model_protocol_contract import ProviderTurnEnvelope

PROTOCOLS = ['openai_chat_completions', 'openai_responses', 'anthropic_messages']


def build(ep, session, history, number, **extra):
    transaction = session.begin()
    wire = get_model_transport(ep.protocol_family).build_request(ep, 'test', {
        'messages': [{'role': 'system', 'content': 'stable instructions'},
                     {'role': 'user', 'content': 'stable task'},
                     {'role': 'assistant', 'content': 'EXACT_HOST_WARNING'},
                     {'role': 'user', 'content': f'REPAIR_{number}'}],
        '__provider_history': history, '__native_observations_compacted': True,
        '__cache_ordered_history': True, '__runtime_context': f'CURRENT_WORLD_{number}',
        '__append_context': transaction, **extra,
    }).payload
    return transaction, wire


def commit(transaction, text='', tools=True):
    transaction.commit(ProviderTurnEnvelope(text, visible_text=text,
        tool_calls=[{'id': 'pending'}] if tools else []))


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_entire_successful_wire_prefix_and_latest_feedback(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    session = AppendOnlyContext(token_budget=100000)
    transport = get_model_transport(protocol)
    history = []
    previous = None
    for n in range(5):
        if n:
            history.append(pair(transport, ep, n, {'ok': n != 2, 'content': f'RESULT_{n}'}))
        tx, wire = build(ep, session, history, n)
        key = 'input' if protocol == 'openai_responses' else 'messages'
        if previous:
            assert wire[key][:len(previous[key])] == previous[key]
            assert tx.metrics['mode'] == 'append'
        serialized = json.dumps(wire)
        assert serialized.count('EXACT_HOST_WARNING') == 1
        assert f'CURRENT_WORLD_{n}' in serialized and f'REPAIR_{n}' in serialized
        for i in range(1, n + 1):
            assert serialized.count(f'"call_{i}"') == 2
            assert serialized.count(f'RESULT_{i}') == 1
        assert '__append_context' not in serialized
        commit(tx)
        previous = deepcopy(wire)


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_failure_retry_repair_and_out_of_order_commit(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    session = AppendOnlyContext(token_budget=100000)
    tx, first = build(ep, session, [], 0)
    commit(tx, 'MODEL_CANDIDATE', tools=False)
    failed, _ = build(ep, session, [], 1)
    fresh, wire = build(ep, session, [], 2)
    field = 'input' if protocol == 'openai_responses' else 'messages'
    assert 'CURRENT_WORLD_1' not in json.dumps(wire)
    assert 'MODEL_CANDIDATE' in json.dumps(wire)
    wire[field].append({'role': 'user', 'content': 'FORMAT_REPAIR'})
    fresh.observe_wire(wire)
    commit(fresh)
    commit(failed)
    _, next_wire = build(ep, session, [], 3)
    assert next_wire[field][:len(wire[field])] == wire[field]
    assert 'CURRENT_WORLD_1' not in json.dumps(next_wire)


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_identity_and_history_changes_reset_private_prefix(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    transport = get_model_transport(protocol)
    history = [pair(transport, ep, i, {'content': f'PRIVATE_{i}'}) for i in (1, 2)]
    for change in ('model', 'tools', 'history', 'new_run'):
        session = AppendOnlyContext(token_budget=100000)
        tx, _ = build(ep, session, history, 0); commit(tx)
        new_ep = dataclasses.replace(ep, model_name='other') if change == 'model' else ep
        new_session = AppendOnlyContext(token_budget=100000) if change == 'new_run' else session
        new_history = history[1:] if change == 'history' else history
        tx, wire = build(new_ep, new_session, new_history, 1,
                         **({'tools': [{'type': 'function', 'function': {'name': 'new_tool'}}]} if change == 'tools' else {}))
        assert tx.metrics['mode'] != 'append'
        assert 'CURRENT_WORLD_0' not in json.dumps(wire)
        if change in ('model', 'history'):
            assert 'PRIVATE_1' not in json.dumps(wire)


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_budget_reset_retains_complete_pairs_current_feedback_and_world(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    session = AppendOnlyContext(token_budget=100000)
    history = [pair(get_model_transport(protocol), ep, 1, {'content': 'UNIQUE_RECEIPT'})]
    tx, _ = build(ep, session, history, 0); commit(tx, 'old presentation ' * 10000, tools=False)
    session.token_budget = 4000
    tx, wire = build(ep, session, history, 1)
    serialized = json.dumps(wire, ensure_ascii=False)
    assert tx.metrics['mode'] == 'budget_reset'
    assert 'CURRENT_WORLD_0' not in serialized and 'CURRENT_WORLD_1' in serialized
    assert 'REPAIR_1' in serialized and '上下文整理' in serialized
    assert serialized.count('"call_1"') == 2 and serialized.count('UNIQUE_RECEIPT') == 1


def test_auxiliary_review_does_not_inherit_main_timeline(endpoint):
    from v3.jineng.http_kehuduan import HttpKehuduan
    client = HttpKehuduan()
    session = AppendOnlyContext(token_budget=100000)
    try:
        with client.scoped_native_history([], append_context=session):
            assert client._append_context.get() is session
            with client.scoped_semantic_inference(endpoint=endpoint):
                assert client._append_context.get() is None
            assert client._append_context.get() is session
        assert client._append_context.get() is None
    finally:
        client._kehuduan.close()


def test_executor_records_actual_repair_wire_and_failed_stream_is_not_committed(endpoint):
    session = AppendOnlyContext(token_budget=100000)
    tx = session.begin()
    client = Client(lambda: iter([event({'content': 'ok'}, 'stop'), 'data: [DONE]']))
    result = executor.execute_streaming_turn(client=client, endpoint=endpoint, api_key='test',
        canonical_payload={'messages': [{'role': 'user', 'content': 'task'}],
            '__append_context': tx, '__turn_repair_instruction': 'REPAIR_FORMAT'})
    assert session.committed is None
    tx.commit(result.turn)
    assert session.committed['messages'][-1]['content'] == 'REPAIR_FORMAT'


def test_world_delta_exact_reconstruction_with_removed_changed_and_added_blocks():
    from v3.jineng.model_context_cache import encode_world_view
    marker = '[当前运行上下文：非授权数据] '
    stable = 'unchanged ABI permissions are not evidence; ' * 500
    first = marker + 'packet_id=OLD\n\n' + stable + '\n\nSTALE=old\n\nobsolete block'
    _, old = encode_world_view([{'role': 'user', 'content': first}])
    for text in (marker + 'packet_id=NEW\n\n' + stable + '\n\nSTALE=new',
                 marker + 'packet_id=NEXT\n\n' + stable + '\n\nSTALE=new\n\nnew block'):
        tail, view = encode_world_view([{'role': 'user', 'content': text}], old)
        assert '[WORLD_VIEW_DELTA]' in tail[0]['content']
        data = json.loads(tail[0]['content'].split('\n', 1)[1])
        rebuilt = '\n\n'.join(data['replace'].get(str(i), old['blocks'][i] if i < len(old['blocks']) else '')
                              for i in range(data['block_count']))
        assert rebuilt == text and data['base_sha256'] == old['sha256']
        assert len(tail[0]['content']) < len(text) * 0.2
        old = view


def test_budget_reset_uses_full_world_instead_of_orphan_delta(endpoint):
    session = AppendOnlyContext(token_budget=100000)
    stable = 'immutable context ' * 500
    tx, _ = build(endpoint, session, [], 0, __runtime_context='OLD\n\n'+stable)
    commit(tx)
    tx, wire = build(endpoint, session, [], 1, __runtime_context='NEW\n\n'+stable)
    assert '[WORLD_VIEW_DELTA]' in wire['messages'][-1]['content']
    commit(tx, 'old presentation ' * 10000, tools=False)
    session.token_budget = 6000
    tx, wire = build(endpoint, session, [], 2, __runtime_context='CURRENT\n\n'+stable)
    assert 'WORLD_VIEW_FULL' in json.dumps(wire) and 'WORLD_VIEW_DELTA' not in json.dumps(wire)
    assert 'CURRENT' in json.dumps(wire) and 'OLD' not in json.dumps(wire)


def test_wire_budget_rejects_oversized_fixed_context_and_never_splits_latest_pair(endpoint):
    from v3.context_compactor import ContextAssemblyError, estimate_tokens
    from v3.jineng.model_context_cache import ContextTransaction
    session = AppendOnlyContext(token_budget=2500)
    groups = [[{'role': 'assistant', 'content': 'call_'+str(n)}, {'role':'user','content': str(n)*3000}] for n in range(4)]
    payload = {}
    tx = session.begin()
    tx.render(endpoint, payload, 'messages', [{'role': 'system', 'content':'fixed'}], groups, [{'role':'user','content':'current'}])
    assert tx.metrics['dropped_history_groups'] > 0
    assert payload['messages'][-3:-1] == groups[-1]
    assert estimate_tokens(json.dumps(payload, ensure_ascii=False)) <= 2500
    with pytest.raises(ContextAssemblyError, match='current_context_exceeds'):
        session.begin().render(endpoint, {}, 'messages', [{'role':'system','content':'x'*20000}], groups, [])
