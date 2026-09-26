from copy import deepcopy
from dataclasses import replace
import json

import pytest

from v3 import adversarial_review as review
from v3 import model_roles
from v3 import review_evidence as evidence
from v3.simple_chain import kernel
from test_adversarial_completion import verdict, session
from test_adversarial_review import Client, run_state, observations
from test_dictionary_model_lifecycle import endpoint


def test_full_old_observation_survives_history_trimming_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT', str(tmp_path / 'runs'))
    state = {**run_state(), 'run_id': 'r', 'request_id': 'q', 'generation': 2}
    for n in range(40):
        payload = observations(n)[0]
        if n == 0:
            payload['tool_result'] = {'content': 'x' * 9000 + 'ORIGINAL_EVIDENCE'}
        kernel._simple_chain_record_observation(state, payload)
    index = state['review_evidence_index']
    assert len(index) == 40
    restored = json.loads(json.dumps(state))
    assert 'ORIGINAL_EVIDENCE' in evidence.read_observation(restored, index[0])
    def judge(packet):
        if not packet['evidence_pages']:
            return verdict('continue', evidence_requests=[{'ref': index[0]['ref'], 'start': 8800, 'length': 1000}])
        assert 'ORIGINAL_EVIDENCE' in packet['evidence_pages'][0]['data_excerpt']
        return verdict()
    client = Client(judge)
    reviewer = session(client)
    result = reviewer.judge(restored, observations(39), '候选', remaining_seconds=120)
    assert result['review']['decision'] == 'complete'
    assert len(client.calls) == 2 and restored['round'] == 40
    assert reviewer.approved(restored, observations(39), '候选')
    restored['generation'] = 3
    assert not reviewer.approved(restored, observations(39), '候选')
    with pytest.raises(ValueError, match='scope_changed'):
        evidence.read_observation(restored, index[0])


def test_long_candidate_can_be_read_in_pages_without_rerunning_tools():
    candidate = '候选正文' * 6200 + 'FINAL_BOUNDARY'
    def judge(packet):
        offset = 12000 + sum(len(p['data_excerpt']) for p in packet['evidence_pages'])
        if offset < len(candidate):
            return verdict('continue', evidence_requests=[{'ref': packet['candidate_ref'], 'start': offset, 'length': 12000}])
        assert 'FINAL_BOUNDARY' in packet['evidence_pages'][-1]['data_excerpt']
        return verdict()
    client, state = Client(judge), run_state()
    reviewer = session(client)
    result = reviewer.judge(state, [], candidate, remaining_seconds=180)
    assert result['review']['decision'] == 'complete', result
    assert reviewer.approved(state, [], candidate)
    assert len(client.calls) == 3


def test_v1_completion_is_history_only_and_cannot_approve():
    reviewer, state = session(Client(lambda p: '{"decision":"complete","reason":"ok","findings":[],"coverage_gaps":[]}')), run_state()
    assert reviewer.judge(state, [], 'text', remaining_seconds=120)['review']['decision'] == 'unavailable'


def test_repeated_retrieval_is_bounded_and_never_triggers_tool_execution():
    client = Client(lambda p: verdict('continue', evidence_requests=[{'ref': p['candidate_ref'], 'start': 12000, 'length': 100}]))
    result = session(client).judge(run_state(), [], 'a' * 13000, remaining_seconds=120)
    assert result['review']['decision'] == 'unavailable'
    assert 'judge_repeated_evidence_request' in result['review']['coverage_gaps']
    assert len(client.calls) == 2


def test_file_change_and_cancellation_revoke_current_approval(tmp_path):
    path = tmp_path / 'actual.txt'; path.write_text('correct')
    state = {**run_state(), 'generated_attachments': [{'path': str(path)}]}
    cancelled = False
    reviewer = session(Client(lambda p: verdict()))
    reviewer.judge(state, observations(), 'candidate', remaining_seconds=120, cancel_check=lambda: cancelled)
    assert reviewer.approved(state, observations(), 'candidate')
    path.write_text('different bytes')
    assert not reviewer.approved(state, observations(), 'candidate')
    reviewer.judge(state, observations(), 'candidate', remaining_seconds=120, cancel_check=lambda: cancelled)
    cancelled = True
    assert not reviewer.approved(state, observations(), 'candidate')


def test_model_roles_prefer_different_provider_and_keep_one_judge(endpoint):
    same = replace(endpoint, model_name='other')
    other = replace(endpoint, provider_identity='other', model_name='different')
    assert model_roles.select_roles([endpoint])['mode'] == 'single_model_isolated_contexts'
    roles = model_roles.select_roles([endpoint, same, other])
    assert roles['judge'] == other and roles['challenger'] == same
    assert roles['fallbacks'] == [endpoint]


def test_configured_pool_uses_existing_credential_authority(endpoint, monkeypatch):
    other = replace(endpoint, provider_identity='other', base_url='https://other.example', model_name='different')
    monkeypatch.setattr(model_roles, '_safe_settings', lambda: {'_endpoint_profiles': {'other': {}, 'missing': {}}})
    monkeypatch.setattr(model_roles.peizhi, 'L4_PROVIDER_IDS', ())
    monkeypatch.setattr(model_roles, 'duqu_model_endpoint_config', lambda name: other if name == 'other' else replace(endpoint, provider_identity=name))
    monkeypatch.setattr(model_roles.peizhi, 'provider_credential_state', lambda name, url: 'configured' if name == 'other' else 'not_configured')
    assert model_roles.configured_models(endpoint) == [endpoint, other]


def test_failed_judge_falls_back_once_with_explicit_record(endpoint):
    other = replace(endpoint, provider_identity='other', model_name='different')
    roles = model_roles.select_roles([endpoint, other])
    client = Client(lambda p: 'invalid' if len(client.calls) == 1 else verdict())
    reviewer = review.CompletionSession(client, endpoint_resolver=lambda: endpoint, roles_resolver=lambda: roles)
    result = reviewer.judge(run_state(), [], 'candidate', remaining_seconds=120)['review']
    assert result['decision'] == 'complete' and result['degraded_to_fallback']
    assert result['model']['provider'] == endpoint.provider_identity
    assert [c['status'] for c in result['model_calls']] == ['unavailable', 'completed']


def test_corrupt_stored_evidence_cannot_be_replaced_by_a_short_excerpt(tmp_path, monkeypatch):
    monkeypatch.setenv('TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT', str(tmp_path))
    state = {**run_state(), 'run_id': 'r'}
    kernel._simple_chain_record_observation(state, observations()[0])
    state['review_evidence_index'][0]['sha256'] = '0' * 64
    client = Client(lambda p: verdict())
    result = session(client).judge(state, observations(), 'candidate', remaining_seconds=120)
    assert result['review']['decision'] == 'unavailable' and not client.calls
