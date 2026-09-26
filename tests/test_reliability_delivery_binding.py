"""Delivery invalidation and crash replay at real authority boundaries."""
from dataclasses import replace
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from test_dictionary_model_lifecycle import endpoint
from test_adversarial_completion import verdict, session
from test_adversarial_review import Client, run_state
from v3.review_evidence import artifact_versions
from v3.model_roles import input_budget
from v3.fact_kernel import FactExecutionKernel, _sha256
from total_gateway.frozen_backend_compat import FrozenBackendCompatibilityTransport, FrozenBackendCompatibilityError


def test_context_budget_resolves_existing_alias_and_honors_explicit_limit(endpoint):
    alias = replace(endpoint, model_name='deepseek-chat', optimization_family='deepseek_v4')
    assert input_budget(alias) == 1048576 - 8192
    assert input_budget(replace(alias, endpoint_overrides={'context_window_tokens': 16384}), output_reserve=4096) == 12288
    assert input_budget(replace(alias, endpoint_overrides={'context_window_tokens': 4096}), output_reserve=8192) == 1


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO type')
def test_artifact_hashing_does_not_open_fifo_as_a_regular_file(tmp_path):
    path = tmp_path / 'pipe'
    os.mkfifo(path)
    assert artifact_versions({'generated_attachments': [{'path': str(path)}]})[0]['state'] == 'unavailable'


def approved_case(tmp_path):
    path = tmp_path / 'answer.txt'; path.write_text('correct bytes')
    identity = {'request_id': 'req', 'run_id': 'run', 'generation': 3}
    state = {**run_state(), 'review_authority_identity': identity,
             'generated_attachments': [{'path': str(path)}]}
    session(Client(lambda p: verdict())).judge(state, [], 'candidate', remaining_seconds=120)
    payload = {'completion_authority': 'adversarial_agent', 'simple_chain_status': 'complete',
               'reply_text': 'candidate', 'adversarial_completion': state['adversarial_completion'],
               'attachments': state['generated_attachments']}
    captured = []
    def put(data, **kw):
        captured.append(data)
        return SimpleNamespace(reference=SimpleNamespace(object_id='oref', sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)))
    bridge = object.__new__(FrozenBackendCompatibilityTransport)
    bridge._workspace_root = tmp_path
    bridge._objects = SimpleNamespace(put_bytes=put)
    ticket = SimpleNamespace(payload=SimpleNamespace(**identity, max_output_bytes=10000,
        tenant_id='t', link_account_id='a', conversation_scope_hash='c'))
    return path, payload, bridge, ticket, captured


def test_capture_only_exact_approved_bytes_and_bound_reply(tmp_path):
    path, payload, bridge, ticket, captured = approved_case(tmp_path)
    rows, ids = bridge._capture_outputs(ticket, payload, created_at_ms=1)
    assert rows[0]['filename'] == 'answer.txt' and captured == [path.read_bytes()]
    assert ids == ('oref',)


def test_approved_empty_local_file_keeps_version_check_without_empty_attachment(tmp_path):
    from total_gateway.object_store import ContentAddressedObjectStore
    path, payload, bridge, ticket, captured = approved_case(tmp_path)
    path.write_bytes(b'')
    payload['adversarial_completion']['reports'][-1]['artifact_versions'] = artifact_versions({'generated_attachments':[{'path':str(path)}]})
    objects = ContentAddressedObjectStore.open(tmp_path / 'objects', now_ms=1)
    bridge._objects = objects
    try:
        assert bridge._capture_outputs(ticket, payload, created_at_ms=1) == ([], ())
        path.write_text('unapproved')
        with pytest.raises(FrozenBackendCompatibilityError, match='artifact_changed'):
            bridge._capture_outputs(ticket, payload, created_at_ms=1)
    finally:
        objects.close()


@pytest.mark.parametrize('change', ['bytes', 'missing', 'oversize', 'reply', 'request', 'generation', 'old_protocol'])
def test_changed_review_basis_never_exports_an_artifact(tmp_path, change):
    path, payload, bridge, ticket, captured = approved_case(tmp_path)
    if change == 'bytes': path.write_text('unapproved replacement')
    if change == 'missing': path.unlink()
    if change == 'oversize': ticket.payload.max_output_bytes = 1
    if change == 'reply': payload['reply_text'] = 'forged candidate'
    if change == 'request': ticket.payload.request_id = 'different'
    if change == 'generation': ticket.payload.generation += 1
    if change == 'old_protocol': payload['adversarial_completion']['schema'] = 'tiangong.adversarial-completion.v1'
    with pytest.raises(FrozenBackendCompatibilityError, match='compat.review.'):
        bridge._capture_outputs(ticket, payload, created_at_ms=1)
    assert not captured


def test_legacy_fact_receipt_replays_but_version_change_and_corruption_do_not(tmp_path):
    kernel = FactExecutionKernel(tmp_path, tmp_path / 'facts', 'run')
    kernel.execute('test', 'target', {'v': 1}, lambda op: {'ok': True, 'content': 'original'}, expected_version='1', idempotency_key='key')
    path = kernel._idempotency_path('key')
    record = json.loads(path.read_text())
    record['fact_transaction']['input_sha256'] = _sha256(record['input'])
    path.write_text(json.dumps(record))
    def no_execution(op): raise AssertionError('must never execute a completed key')
    assert kernel.execute('test', 'target', {'v': 1}, no_execution, expected_version='1', idempotency_key='key')['fact_transaction']['idempotent_replay']
    with pytest.raises(ValueError, match='binding_changed'):
        kernel.execute('test', 'target', {'v': 1}, no_execution, expected_version='2', idempotency_key='key')
    record['result']['content'] = 'forged'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='corrupted'):
        kernel.execute('test', 'target', {'v': 1}, no_execution, expected_version='1', idempotency_key='key')
