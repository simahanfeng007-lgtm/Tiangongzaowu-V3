"""R2-A preparation tests. Test-only signed observations are not production proofs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import canonical_json_bytes, canonical_sha256
from life_service.learning_workflow import build_draft, freeze_learning_publication, publish_draft
from total_gateway.learning_output_preparation import (
    LearningOutputBasisV1, LearningOutputPreparationError, PreparedLearningOutputV1,
    current_learning_sources_sha256, learning_record_sha256,
    prepare_knowledge_learning_output, prepare_tool_source_learning_output,
    prepare_method_source_learning_output, prepare_composition_experience_learning_output,
)
from tests.test_method_source_review_p9 import _inputs, _sign_report
from tests.test_capability_experience_p5 import _observation, _trace, _completion, _scoped_plan
from tests.test_tool_source_candidate_p8 import repository, write, commit  # noqa: F401

H = 'a' * 64
PRIVACY = canonical_sha256({'privacy_scope': 'private'})


def card(kind='knowledge', *, life_id='life_contract_test', principal='principal_test'):
    record = build_draft(life_id=life_id, scope={'scope_sha256': H}, source='user_direct', decision={
        'target': kind, 'request': 'prepare only', 'title': '保留来源的学习', 'summary': 'not approval',
        'draft_artifact': {'content': 'SKILL.md、Tool 可以作为普通知识正文。'},
    })
    if kind in {'skill', 'tool'}:
        record = freeze_learning_publication(record)
    basis = LearningOutputBasisV1(life_id, record['learning_id'], H, learning_record_sha256(record),
        principal, H, 'private', PRIVACY)
    return record, basis


def check_output(result, kind):
    value = result.payload()
    assert value['output_kind'] == kind and value['status'] == 'LEARNING_OUTPUT_PREPARED'
    assert all(value[f] is False for f in ('may_publish', 'may_authorize', 'may_execute', 'may_write_store'))
    assert value['context_section'] == 'DATA'
    assert result.preparation_sha256 == hashlib.sha256(result.canonical_bytes).hexdigest()
    value['body']['tamper'] = True
    assert 'tamper' not in result.payload()['body']
    with pytest.raises(Exception): result.canonical_bytes = b'{}'


def method_args():
    args, reviewer, observer = _inputs()
    record, basis = card('skill')
    prepare = dict(record=record, basis=basis,
        **{key: args[key] for key in ('base_snapshot', 'expected_base_snapshot_sha256', 'corpus',
            'candidates', 'source_documents', 'simulation_evidence', 'trusted_observer_public_key', 'now_ms')})
    return prepare, args, reviewer, observer


def experience_args(*, observation=None):
    observation = observation or _observation(1, life_id='life_contract_test')
    record, basis = card(life_id=observation.life_id, principal=observation.principal_ref)
    sources = (*observation.plan.method_source_refs, *observation.plan.action_source_refs)
    return dict(record=record, basis=basis, observation=observation,
        expected_observation_sha256=observation.observation_sha256, prior=None,
        expected_prior_state_sha256=None, current_sources=sources,
        expected_current_sources_sha256=current_learning_sources_sha256(sources),
        parent_derivation_ids=('mdr_parent',), now_ms=2000)


def test_knowledge_prepares_existing_built_artifact_without_publishing_or_mutating():
    record, basis = card(); before = deepcopy(record)
    result = prepare_knowledge_learning_output(record, basis=basis)
    check_output(result, 'KNOWLEDGE')
    artifact = result.payload()['body']['artifact']
    assert artifact['kind'] == 'knowledge' and artifact['status'] == 'built'
    assert artifact['skill_spec'] is None and not artifact['required_actions']
    assert 'SKILL.md' in artifact['document']['content']
    assert result == prepare_knowledge_learning_output(record, basis=basis)
    assert record == before
    published, _ = publish_draft(record, capabilities={})
    assert published['status'] == 'published'  # Original Knowledge path still owns consent/publication.


@pytest.mark.parametrize('kind', ['skill', 'tool'])
def test_new_adapter_does_not_unfreeze_legacy_full_publication(kind):
    record, basis = card(kind)
    with pytest.raises(LearningOutputPreparationError, match='target_mismatch'):
        prepare_knowledge_learning_output(record, basis=basis)
    before = deepcopy(record)
    approved = {**record, 'status': 'approved'}
    result, artifact = publish_draft(approved, capabilities={})
    assert artifact is None and result['status'] == 'migration_required' and not result['registered']
    assert record == before


@pytest.mark.parametrize('field,value', [('life_id','other'), ('learning_id','other'), ('scope_sha256','b'*64),
    ('summary','mutated'), ('draft_artifact',{'content':'substituted'})])
def test_input_record_drift_is_not_approved_by_old_draft_hash(field, value):
    record, basis = card(); record[field] = value
    with pytest.raises(LearningOutputPreparationError, match='record_pin_or_scope_mismatch'):
        prepare_knowledge_learning_output(record, basis=basis)


@pytest.mark.parametrize('status', ['discarded', 'published', 'active', 'unknown'])
def test_terminal_and_unknown_cards_cannot_be_reprepared(status):
    record, basis = card(); record['status'] = status
    basis = replace(basis, learning_record_sha256=learning_record_sha256(record))
    with pytest.raises(LearningOutputPreparationError, match='record_not_preparable'):
        prepare_knowledge_learning_output(record, basis=basis)


@pytest.mark.parametrize('patch', [{'skill_spec': {}}, {'required_actions':['file.write']},
    {'execution':{'artifact':{'kind':'tool'}}}, {'kind':'learning_tool'}])
def test_rehashed_knowledge_label_does_not_open_capability_routing(patch):
    record, basis = card(); record.update(patch)
    basis = replace(basis, learning_record_sha256=learning_record_sha256(record))
    with pytest.raises(LearningOutputPreparationError):
        prepare_knowledge_learning_output(record, basis=basis)


def test_forged_preparation_flags_and_noncanonical_bytes_are_rejected():
    record, basis = card(); payload = prepare_knowledge_learning_output(record, basis=basis).payload()
    for key in ('may_publish', 'may_execute', 'may_write_store', 'may_authorize'):
        with pytest.raises(LearningOutputPreparationError):
            PreparedLearningOutputV1(canonical_json_bytes({**payload, key: True}))
    with pytest.raises(LearningOutputPreparationError):
        PreparedLearningOutputV1(canonical_json_bytes(payload)+b'\n')


def test_tool_candidate_uses_existing_git_authority_and_never_imports_source(repository, tmp_path):
    root, base = repository
    sentinel = tmp_path/'must-not-run'
    text = f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\n'
    write(root, 'src/body/action.py', text); write(root, 'mirror/body/action.py', text)
    head = commit(root); record, basis = card('tool')
    result = prepare_tool_source_learning_output(record, basis=basis, repository=root,
        base_commit=base, candidate_commit=head, requested_action_ids=('file.read',))
    check_output(result, 'TOOL_SOURCE')
    value = result.payload()['body']
    assert value['candidate']['candidate_commit'] == head and value['candidate']['base_commit'] == base
    assert not value['build_verified'] and not value['review_verified'] and not sentinel.exists()
    write(root, 'src/body/action.py', 'modified checkout, not the pinned commit')
    assert result == prepare_tool_source_learning_output(record, basis=basis, repository=root,
        base_commit=base, candidate_commit=head, requested_action_ids=('file.read',))


def test_tool_mutable_ref_and_undeclared_change_cannot_be_candidate_authority(repository):
    root, base = repository; record, basis = card('tool')
    with pytest.raises(ValueError):
        prepare_tool_source_learning_output(record, basis=basis, repository=root,
            base_commit=base, candidate_commit='HEAD', requested_action_ids=('file.read',))


def test_method_output_reuses_p9_plan_and_can_feed_its_existing_review():
    from total_gateway.method_source_review import prepare_reviewed_method_world_revision
    prepared, review, _, _ = method_args()
    result = prepare_method_source_learning_output(**prepared)
    check_output(result, 'METHOD_SOURCE')
    body = result.payload()['body']
    assert body['simulation_verified'] is True
    assert body['review_verified'] is body['native_git_verified'] is body['publication_performed'] is False
    actual = prepare_reviewed_method_world_revision(**review)
    assert actual.lifecycle_plan_sha256 == body['plan']['plan_sha256']
    assert result == prepare_method_source_learning_output(**prepared)


@pytest.mark.parametrize('operation', ['ADD','UPDATE','REMOVE'])
def test_all_method_operations_stay_on_existing_lifecycle(operation):
    prepared, _, reviewer, observer = method_args()
    args, _, _ = _inputs((operation,), reviewer=reviewer, observer=observer)
    for key in tuple(prepared):
        if key in args: prepared[key] = args[key]
    result = prepare_method_source_learning_output(**prepared)
    assert result.payload()['body']['plan']['changes'][0]['operation'] == operation


@pytest.mark.parametrize('mutation',['base','bytes','signature','missing','extra','clock','forged_flag','graph'])
def test_method_adversarial_input_does_not_self_approve(mutation):
    args, _, _, _ = method_args()
    candidate = args['candidates'][0]; mid = candidate.method_id
    if mutation == 'base': args['expected_base_snapshot_sha256'] = 'f'*64
    if mutation == 'bytes':
        path, raw = args['source_documents'][mid]; args['source_documents'][mid] = (path, raw+b' ')
    if mutation == 'signature': args['trusted_observer_public_key'] = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    if mutation == 'missing': args['simulation_evidence'] = {}
    if mutation == 'extra': args['source_documents']['other'] = args['source_documents'][mid]
    if mutation == 'clock': args['now_ms'] = True
    if mutation == 'forged_flag':
        candidate = replace(candidate, may_authorize=0)
        args['candidates'] = (candidate,)
    if mutation == 'graph':
        changed = replace(args['base_snapshot'], relations=())
        changed = replace(changed, snapshot_sha256=canonical_sha256(changed.payload()))
        args.update(base_snapshot=changed, expected_base_snapshot_sha256=changed.snapshot_sha256)
    with pytest.raises((ValueError, TypeError)):
        prepare_method_source_learning_output(**args)


def test_experience_preparation_is_nonwriting_and_preserves_p5_data_policy():
    args = experience_args(); before = args['observation'].model_dump_json()
    result = prepare_composition_experience_learning_output(**args)
    check_output(result, 'COMPOSITION_EXPERIENCE')
    body = result.payload()['body']
    assert body['admission']['decision'] == 'POSITIVE_EXPERIENCE'
    assert body['aggregate']['experience']['lifecycle'] == 'PROBATION'
    assert body['memory_intent']['layer'] == 'L3_EXPERIENCE'
    assert not body['memory_intent']['may_write_store'] and body['memory_intent']['coordinator_required']
    assert body['memory_write_performed'] is False and body['requires_machine_origin_and_parent_lineage']
    assert args['observation'].model_dump_json() == before
    assert result == prepare_composition_experience_learning_output(**args)


def test_failed_task_enters_negative_not_positive_pool():
    plan = _scoped_plan(1); trace = _trace(plan, completion=_completion(plan, outcome='FAILED'), ordinal=1)
    obs = _observation(1, plan=plan, trace=trace, outcome='FAILURE', failure_category='RUNTIME_FAILURE', life_id='life_contract_test')
    result = prepare_composition_experience_learning_output(**experience_args(observation=obs))
    body = result.payload()['body']
    assert not body['admission']['positive_allowed'] and body['admission']['negative_allowed']
    assert body['aggregate']['experience']['success_count'] == 0
    assert body['aggregate']['experience']['failure_count'] == 1


@pytest.mark.parametrize('mutation',['observation_pin','principal','privacy','life','source_pin','source_stale','duplicate_source',
    'missing_prior','wrong_prior_pin','clock','parent','fake_attribution'])
def test_experience_requires_external_scope_sources_prior_and_attribution(mutation):
    args = experience_args(); obs = args['observation']
    if mutation == 'observation_pin': args['expected_observation_sha256'] = 'f'*64
    if mutation == 'principal': args['basis'] = replace(args['basis'], principal_scope_hash='b'*64)
    if mutation == 'privacy': args['basis'] = replace(args['basis'], privacy_scope_hash='b'*64)
    if mutation == 'life':
        args['record'], args['basis'] = card(life_id='other_life')
    if mutation == 'source_pin': args['expected_current_sources_sha256'] = 'f'*64
    if mutation == 'source_stale':
        values = list(args['current_sources']); values[0] = values[0].model_copy(update={'source_sha256':canonical_sha256({'changed':values[0].source_sha256})})
        args['current_sources'] = tuple(values)
        args['expected_current_sources_sha256'] = current_learning_sources_sha256(tuple(values))
    if mutation == 'duplicate_source': args['current_sources'] = args['current_sources']*2
    if mutation == 'missing_prior': args['expected_prior_state_sha256'] = H
    if mutation == 'wrong_prior_pin':
        from world_understanding.capability_composition.capability_experience_policy import CapabilityExperienceAggregateStateV1
        state = prepare_composition_experience_learning_output(**args).payload()['body']['aggregate']
        args['prior'] = CapabilityExperienceAggregateStateV1.model_validate_json(canonical_json_bytes(state))
        args['expected_prior_state_sha256'] = 'b'*64
    if mutation == 'clock': args['now_ms'] = True
    if mutation == 'parent': args['parent_derivation_ids'] = ()
    if mutation == 'fake_attribution':
        trace = obs.trace.model_copy(update={'human_takeover':True}).with_computed_sha256()
        # An internally rehashed observation retaining a previous PASS must fail.
        obs = obs.model_copy(update={'trace':trace}).with_computed_sha256()
        args.update(observation=obs, expected_observation_sha256=obs.observation_sha256)
    with pytest.raises((ValueError, TypeError)):
        prepare_composition_experience_learning_output(**args)


def test_p5_preparation_can_be_materialized_only_by_existing_memory_coordinator(tmp_path):
    from life_service.store import LifeShadowStore
    from life_service.memory_coordinator import MemoryCoordinator
    from world_understanding.capability_composition.capability_experience_policy import (
        CapabilityExperienceAggregateStateV1, CapabilityExperienceMemoryIntentV1)
    from world_understanding.capability_composition.capability_experience_memory import commit_capability_experience_via_memory_coordinator
    from tests.life_contract_support import event
    with LifeShadowStore.open(tmp_path/'life.shadow.sqlite3', create=True, now_ms=1) as store:
        coordinator = MemoryCoordinator(store); evt = event(1, None)
        _, parent, _created = coordinator.commit_life_event_l1(evt)
        args = experience_args(); args['parent_derivation_ids'] = (parent.derivation_id,)
        before = store._connection.total_changes
        body = prepare_composition_experience_learning_output(**args).payload()['body']
        assert store._connection.total_changes == before
        state = CapabilityExperienceAggregateStateV1.model_validate_json(canonical_json_bytes(body['aggregate']))
        intent = CapabilityExperienceMemoryIntentV1.model_validate_json(canonical_json_bytes(body['memory_intent']))
        assertion, derivation, created = commit_capability_experience_via_memory_coordinator(
            coordinator, state, intent, (parent,), body['memory_plaintext'].encode())
        assert created and derivation.layer == 'L3_EXPERIENCE' and not derivation.world_candidate_eligible
        assert assertion.life_id == evt.life_id


def test_preparation_never_opens_files_or_processes_except_tool_git_observation(monkeypatch):
    import subprocess
    knowledge, basis = card(); method, _, _, _ = method_args(); experience = experience_args()
    def denied(*a, **kw): raise AssertionError('preparation attempted file/process IO')
    with monkeypatch.context() as m:
        m.setattr(Path,'open',denied); m.setattr(subprocess,'Popen',denied)
        prepare_knowledge_learning_output(knowledge, basis=basis)
        prepare_method_source_learning_output(**method)
        prepare_composition_experience_learning_output(**experience)


def test_preparation_module_has_no_runtime_writer_or_publisher():
    import ast
    from total_gateway import learning_output_preparation as module
    tree = ast.parse(Path(module.__file__).read_text())
    forbidden = {'publish_artifact','publish_draft','persist_artifact_bundle','publish_tool_source_revision',
                 'stage_method_publication','commit_capability_experience_via_memory_coordinator',
                 'GatewayStateStore','LifeShadowStore','WorldStateStore','MemoryCoordinator','CompletionDecision'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func,ast.Name) else getattr(node.func,'attr','')
            assert name not in forbidden


def aggregate_from(args):
    from world_understanding.capability_composition.capability_experience_policy import CapabilityExperienceAggregateStateV1
    payload = prepare_composition_experience_learning_output(**args).payload()['body']['aggregate']
    return CapabilityExperienceAggregateStateV1.model_validate_json(canonical_json_bytes(payload))


def test_duplicate_experience_does_not_emit_a_second_memory_intent():
    args = experience_args(); prior = aggregate_from(args)
    args.update(prior=prior, expected_prior_state_sha256=prior.state_sha256,
        parent_derivation_ids=('mdr_new_parent',), now_ms=3000)
    body = prepare_composition_experience_learning_output(**args).payload()['body']
    assert body['already_accounted'] is True
    assert body['aggregate'] == prior.model_dump(mode='json')
    assert body['memory_intent'] is body['memory_plaintext'] is None
    assert body['memory_write_performed'] is False


def test_newer_experience_updates_original_p5_aggregate_once():
    prior = aggregate_from(experience_args())
    args = experience_args(observation=_observation(2, life_id='life_contract_test'))
    args.update(prior=prior, expected_prior_state_sha256=prior.state_sha256)
    body = prepare_composition_experience_learning_output(**args).payload()['body']
    assert not body['already_accounted']
    assert body['aggregate']['experience']['success_count'] == 2
    assert body['aggregate']['last_observed_at_ms'] == args['observation'].observed_at_ms
    assert body['memory_intent']['layer'] == 'L3_EXPERIENCE'


def test_out_of_order_new_experience_cannot_roll_back_aggregate_time():
    prior = aggregate_from(experience_args(observation=_observation(2, life_id='life_contract_test')))
    args = experience_args()
    args.update(prior=prior, expected_prior_state_sha256=prior.state_sha256)
    with pytest.raises(LearningOutputPreparationError, match='time_regression'):
        prepare_composition_experience_learning_output(**args)


def test_corrupted_prior_scope_cannot_hide_behind_duplicate_id():
    args = experience_args(); prior = aggregate_from(args)
    prior = prior.model_copy(update={'principal_ref':'other_principal'}).with_computed_sha256()
    args.update(prior=prior, expected_prior_state_sha256=prior.state_sha256)
    with pytest.raises(ValueError, match='crosses identity or scope'):
        prepare_composition_experience_learning_output(**args)


@pytest.mark.parametrize('mutation', ['list_kind', 'string_body', 'null_basis', 'integer_flag', 'unknown_field'])
def test_preparation_envelope_rejects_wrong_shapes(mutation):
    record, basis = card(); value = prepare_knowledge_learning_output(record, basis=basis).payload()
    if mutation == 'list_kind': value['output_kind'] = ['KNOWLEDGE']
    if mutation == 'string_body': value['body'] = 'claim'
    if mutation == 'null_basis': value['basis'] = None
    if mutation == 'integer_flag': value['may_authorize'] = 0
    if mutation == 'unknown_field': value['approved'] = True
    with pytest.raises(LearningOutputPreparationError):
        PreparedLearningOutputV1(canonical_json_bytes(value))
