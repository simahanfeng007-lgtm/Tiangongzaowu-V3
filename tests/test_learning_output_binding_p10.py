"""P10 real temporary Life/Gateway inputs and existing terminal callback wiring.

The backend and signed simulation fixtures are test evidence, not production
model acceptance or permission to write positive P5 memories.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from contracts import canonical_sha256
from life_service.embedded_runtime import EmbeddedLifeError
from life_service.embedded_runtime_wiring import EmbeddedLifeGatewayBinding, bind_embedded_life_gateway_callback
from total_gateway.learning_output_binding import LearningOutputProductionBinding, collect_composition_learning_evidence
from total_gateway.store import GatewayStateStore
from tests.test_learning_publication_freeze_p10 import life, decision
from tests.test_composition_step_execution_p7d1 import _runtime_fixture


@contextmanager
def binding(life, tmp_path, store=None):
    own = store is None
    store = store or GatewayStateStore.open((tmp_path/'gateway.sqlite3').resolve(), now_ms=1)
    runtime = SimpleNamespace(life_service=life, store=store,
        config=SimpleNamespace(workspace_root=(tmp_path/'workspace').resolve()), orchestration=None)
    hook = LearningOutputProductionBinding(runtime)
    life.set_world_identity_provider(lambda life_id: {'life_id':life_id,
        'principal_scope_hash':canonical_sha256({'domain':'production-test-self','life_id':life_id}),
        'workspace_id':'workspace-'+canonical_sha256(str(runtime.config.workspace_root))})
    bind_embedded_life_gateway_callback(life,EmbeddedLifeGatewayBinding.LEARNING_OUTPUT_PREPARER,hook.prepare_from_life)
    try: yield runtime,hook
    finally:
        life.set_learning_output_preparer(None)
        if own: store.close()


def draft(life, kind='knowledge', endpoint='draft', extra=None):
    data=decision(kind)
    if kind=='knowledge' and endpoint=='draft': data['risk_level']='A4'
    if extra: data.update(extra)
    return life.request('POST','/api/v1/v3/life/learning/'+endpoint,{'decision':data})


def events(life, name):
    return [e for e in life.system.journal.events(life._active()['life_id']) if e['event_type']==name]


def test_actual_life_draft_prepares_from_signed_current_card(life,tmp_path):
    with binding(life,tmp_path) as (runtime,hook):
        status,result,_=draft(life)
        assert status==200, result
        output=result['learning_output']; card=result['learning']
        assert output['status']=='LEARNING_OUTPUT_PREPARED' and output['output_kind']=='KNOWLEDGE'
        assert output['basis']['life_id']==card['life_id']
        assert output['basis']['learning_record_sha256']!=card['draft_sha256']
        assert output['body']['artifact']['document']['content']
        assert not life._scope_state()['knowledge']
        before=len(events(life,'learning.output_prepared'))
        assert life._prepare_current_learning_output(card['life_id'],card['learning_id'])==output
        assert len(events(life,'learning.output_prepared'))==before==1
        assert life.system.journal.verify(card['life_id'])['valid']


def test_original_knowledge_publish_remains_actual_single_callback(life,tmp_path):
    calls=[]
    life.set_artifact_publisher(lambda a: calls.append(a) or {'knowledge_document_id':'content-bound-doc'})
    with binding(life,tmp_path):
        status,result,_=draft(life,endpoint='user-request')
        assert status==200 and result['ok'],result
        assert result['learning']['status']=='published'
        assert len(calls)==1 and calls[0]['kind']=='knowledge'
        assert len(events(life,'learning.output_prepared'))==1
        assert len(life._scope_state()['knowledge'])==1 and not life._scope_state()['capabilities']


@pytest.mark.parametrize('kind',['skill','tool'])
def test_source_without_operator_data_stays_frozen_without_publisher(life,tmp_path,kind):
    calls=[];life.set_artifact_publisher(lambda a:calls.append(a))
    with binding(life,tmp_path):
        status,result,_=draft(life,kind,endpoint='user-request')
        assert status==200,result
        assert result['learning']['status']=='migration_required' and result['registered'] is False
        assert result['learning_output']['status']=='SOURCE_INPUT_REQUIRED'
        assert calls==[] and not life._scope_state()['capabilities']


def test_model_expected_hashes_never_choose_identity_or_write_authority(life,tmp_path):
    with binding(life,tmp_path) as (runtime,hook):
        status,result,_=draft(life,extra={'principal_scope_hash':'f'*64,'learning_record_sha256':'e'*64,
            'may_publish':True,'expected_prior_state_sha256':'d'*64})
        assert status==200,result
        basis=result['learning_output']['basis']
        assert basis['principal_scope_hash']!='f'*64
        assert result['learning_output']['may_write_store'] is False
        with pytest.raises(TypeError):hook.prepare_from_life(result['learning']['life_id'],result['learning']['learning_id'],record={})


@pytest.mark.parametrize('field,value',[('title','tampered'),('status','published'),('draft_artifact',{}),
    ('risk_level','A5'),('registered',True),('scope_sha256','c'*64),('unexpected_source_hint',{'trust':True})])
def test_cache_material_cannot_override_signed_journal(life,tmp_path,field,value):
    with binding(life,tmp_path) as (runtime,hook):
        _,r,_=draft(life);c=r['learning'];cache=life._scope_state()['learning'][c['learning_id']]
        cache[field]=value
        with pytest.raises(EmbeddedLifeError,match='journal_mismatch'):
            hook.prepare_from_life(c['life_id'],c['learning_id'])


def test_journal_invalid_report_is_not_ignored(life,tmp_path,monkeypatch):
    with binding(life,tmp_path) as (runtime,hook):
        _,r,_=draft(life);c=r['learning']
        monkeypatch.setattr(life.system.journal,'verify',lambda _: {'valid':False})
        with pytest.raises(EmbeddedLifeError,match='journal_invalid'):
            hook.prepare_from_life(c['life_id'],c['learning_id'])


def test_scope_from_config_cannot_drift(life,tmp_path):
    with binding(life,tmp_path) as (runtime,hook):
        _,r,_=draft(life);c=r['learning'];life.set_world_identity_provider(lambda _: {
            'life_id':c['life_id'],'workspace_id':'workspace-other','principal_scope_hash':'a'*64})
        with pytest.raises(ValueError,match='workspace_mismatch'):
            hook.prepare_from_life(c['life_id'],c['learning_id'])


def test_high_risk_knowledge_does_not_gain_consent(life,tmp_path):
    seen=[];life.set_artifact_publisher(lambda a:seen.append(a))
    with binding(life,tmp_path):
        status,result,_=draft(life,extra={'risk_level':'A4'})
        assert status==200,result
        assert result['learning']['status']=='awaiting_user' and not seen
        assert result['learning_output']['body']['publication_performed'] is False


def test_preparation_failure_does_not_fall_back_to_publish(life,tmp_path):
    seen=[];life.set_artifact_publisher(lambda a:seen.append(a))
    with binding(life,tmp_path):
        def fail(*args):raise RuntimeError('injected preparation failure')
        life.set_learning_output_preparer(fail)
        status,result,_=draft(life,endpoint='user-request')
        assert status!=200,result
        assert not seen and not life._scope_state()['knowledge']
        assert len(events(life,'learning.draft_created'))==1


def test_production_source_selector_without_config_is_explicitly_deferred(life,tmp_path):
    with binding(life,tmp_path):
        status,r,_=draft(life,'tool',extra={'draft_artifact':{'content':'tool source proposal',
            'source_evolution':{'kind':'TOOL_SOURCE','world_state_id':'wst_'+'f'*64,
                'candidate_commit':'f'*40,'requested_action_ids':['file.read']}}})
        assert status==200,r
        assert r['learning_output']['reason_code']=='learning_output.world_source_operator_required'
        assert r['learning']['registered'] is False


def test_machine_reader_uses_real_committed_effect_fact_and_objects(tmp_path):
    with _runtime_fixture(tmp_path/'machine') as f:
        f.coordinator.dispatch_record(f.record,now_ms=1700)
        plan=f.p7c.plan
        before_calls=f.backend.calls
        data=collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation)
        assert data['status']=='MACHINE_EVIDENCE_COLLECTED'
        assert len(data['fact_refs'])>=2 and len(data['effect_refs'])>=2
        assert 'SEALED_COMPLETION_REQUIRED' in data['blockers']
        assert data['may_write_memory'] is False
        assert f.backend.calls==before_calls
        assert data==collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation)


def test_machine_reader_rejects_before_actual_step_completion(tmp_path):
    with _runtime_fixture(tmp_path/'machine') as f:
        p=f.p7c.plan
        with pytest.raises(Exception):collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=p.request_id,run_id=p.run_id,generation=p.generation)
        assert f.backend.calls==0


def execution_payload(life,plan,fact_ids,session_scope_hash,completed_at_ms):
    return {'schema':'tiangong.life.execution-terminal.v1','request_id':plan.request_id,
        'run_id':plan.run_id,'generation':plan.generation,'life_id':life._active()['life_id'],
        'session_scope_hash':session_scope_hash,'status':'completed','user_goal_sha256':'b'*64,
        'final_result_sha256':'c'*64,'fact_ids':list(fact_ids),'completed_at_ms':completed_at_ms}


def test_terminal_callback_audits_after_real_life_commit_without_memory_write(life,tmp_path):
    with _runtime_fixture(tmp_path/'machine') as f, binding(life,tmp_path,f.p7c.store) as (runtime,hook):
        f.coordinator.dispatch_record(f.record,now_ms=1700)
        runtime.orchestration=SimpleNamespace(_composition_steps=f.coordinator)
        runtime.config.workspace_root=Path(f.p7c.plan.workspace.workspace_root)
        payload=execution_payload(life,f.p7c.plan,f.coordinator.finalize_plan(f.p7c.plan).fact_ids,
            f.p7c.store.get_request_entry(f.p7c.plan.request_id).session_scope_hash,
            f.coordinator.finalize_plan(f.p7c.plan).completed_at_ms+1)
        result=hook.commit_execution(payload)
        assert result['ok'],result
        assert result['learning_evidence']['status']=='MACHINE_EVIDENCE_AUDITED',result
        assert result['learning_evidence']['may_write_memory'] is False
        assert len(events(life,'execution.committed'))==1
        assert len(events(life,'learning.execution_evidence'))==1
        repeat=hook.commit_execution(payload)
        assert repeat['execution']==result['execution'] and repeat['duplicate'] is True
        assert len(events(life,'learning.execution_evidence'))==1


def test_terminal_audit_failure_preserves_durable_execution_and_can_retry(life,tmp_path,monkeypatch):
    with _runtime_fixture(tmp_path/'machine') as f, binding(life,tmp_path,f.p7c.store) as (runtime,hook):
        f.coordinator.dispatch_record(f.record,now_ms=1700)
        runtime.orchestration=SimpleNamespace(_composition_steps=f.coordinator)
        runtime.config.workspace_root=Path(f.p7c.plan.workspace.workspace_root)
        payload=execution_payload(life,f.p7c.plan,f.coordinator.finalize_plan(f.p7c.plan).fact_ids,
            f.p7c.store.get_request_entry(f.p7c.plan.request_id).session_scope_hash,
            f.coordinator.finalize_plan(f.p7c.plan).completed_at_ms+1)
        real=life.record_learning_execution_evidence
        def fail(*a):raise OSError('test disk failure')
        monkeypatch.setattr(life,'record_learning_execution_evidence',fail)
        result=hook.commit_execution(payload)
        assert result['ok'] and result['learning_evidence']['status']=='LEARNING_EVIDENCE_DEFERRED'
        assert len(events(life,'execution.committed'))==1 and not events(life,'learning.execution_evidence')
        monkeypatch.setattr(life,'record_learning_execution_evidence',real)
        again=hook.commit_execution(payload)
        assert again['learning_evidence']['status']=='MACHINE_EVIDENCE_AUDITED',again
        assert again['execution']==result['execution']


def test_new_events_are_in_original_replay_registry():
    from life_service.journal_replay import EVENT_REGISTRY,EventClass
    for key in ('learning.output_prepared','learning.execution_evidence'):
        assert EVENT_REGISTRY[key] is EventClass.AUDIT_ONLY


@contextmanager
def source_context(life,tmp_path):
    """Real P9 archive/Git fixture, retargeted to the actual test Life scope."""
    from tests.test_method_source_publication_p9 import context as original_context
    from tests.test_world_understanding_p13_1_production_activation import _source as source_event
    from world_understanding.production import ProductionWorldUnderstandingRuntime
    from world_understanding.world_state import WorldStateStore
    from world_understanding.software_world import SoftwareWorldFrame
    from total_gateway.method_source_run_binding import MethodRunSourceResolver
    from v3.world_understanding_production import _scope
    with binding(life,tmp_path) as (runtime,hook):
        root=tmp_path/'p9';root.mkdir()
        c=original_context.__wrapped__(root)
        identity=life._world_identity_provider(life._active()['life_id'])
        scope=_scope(identity)
        def frame_factory(envelope,cut):
            return SoftwareWorldFrame.build(scope=scope,workspace=identity['workspace_id'],
                repository='repo.fixture',worktree='worktree.fixture',branch='fixture-main',commit=c['commit'],
                environment='test-env',time=envelope.source_time,world_cut=cut)
        world=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=tmp_path/'bound-world'),
            frame_factory=frame_factory,method_revision_resolver=c['resolver'])
        event=source_event('scoped-genesis',1).model_copy(update={'scope_hint':scope,'workspace_id':identity['workspace_id']})
        # Envelope identity/hash comes from the original adapter, not model_copy.
        from world_understanding.source_adapters import build_post_commit_source_envelope
        event=build_post_commit_source_envelope(source_kind=event.source_kind,source_native_id='scoped-genesis',
            producer_ref=event.producer_ref,payload=event.payload_inline,source_time=event.source_time,
            scope=scope,correlation_id='corr.scoped-genesis',workspace_id=identity['workspace_id'])
        assert world.facade.accept(event).processed
        runtime.store._method_source_resolver=MethodRunSourceResolver(runtime.store,world)
        state=world.store.current_candidates(life_id=scope.life_id,principal_scope_hash=scope.principal_scope_hash)[0]
        c.update(runtime=world,frame_factory=frame_factory)
        yield runtime,hook,c,state,event


def test_actual_life_source_selector_reuses_p9_archived_git_proof_without_publication(life,tmp_path):
    from tests.test_method_source_publication_p9 import _publication
    with source_context(life,tmp_path) as (runtime,hook,c,state,_):
        envelope,_,_= _publication(c,base=state)
        selector={'kind':'METHOD_SOURCE','world_state_id':state.state.world_state_id,
            'archive_sha256':envelope.payload_inline['archive_sha256']}
        status,r,_=draft(life,'skill',extra={'draft_artifact':{'content':'reviewed method proposal','source_evolution':selector}})
        assert status==200,r
        assert r['learning_output']['output_kind']=='METHOD_SOURCE',r
        assert r['learning_output']['body']['simulation_verified'] is True
        assert r['registered'] is False and r['learning']['status']=='migration_required'
        assert c['runtime'].store.current_candidates(life_id=state.state.scope.life_id,
            principal_scope_hash=state.state.scope.principal_scope_hash)[0].state_ref==state.state_ref
        assert len(events(life,'learning.output_prepared'))==1


@pytest.mark.parametrize('mutation',['stale','archive','extra_key'])
def test_source_selector_rejection_never_advances_world(life,tmp_path,mutation):
    from tests.test_method_source_publication_p9 import _publication
    with source_context(life,tmp_path) as (runtime,hook,c,state,event):
        envelope,_,_=_publication(c,base=state)
        selector={'kind':'METHOD_SOURCE','world_state_id':state.state.world_state_id,
            'archive_sha256':envelope.payload_inline['archive_sha256']}
        if mutation=='archive':selector['archive_sha256']='f'*64
        if mutation=='extra_key':selector['trusted_observer_public_key']='self-issued'
        if mutation=='stale':
            from world_understanding.source_adapters import build_post_commit_source_envelope
            later=build_post_commit_source_envelope(source_kind=event.source_kind,source_native_id='later',
                producer_ref=event.producer_ref,payload=event.payload_inline,source_time=event.source_time,
                scope=event.scope_hint,correlation_id='corr.later',workspace_id=event.workspace_id)
            assert c['runtime'].facade.accept(later).processed
        before=c['runtime'].store.current_candidates(life_id=state.state.scope.life_id,
            principal_scope_hash=state.state.scope.principal_scope_hash)[0]
        status,r,_=draft(life,'skill',extra={'draft_artifact':{'content':'proposal','source_evolution':selector}})
        assert status!=200,r
        assert not events(life,'learning.output_prepared')
        assert c['runtime'].store.current_candidates(life_id=state.state.scope.life_id,
            principal_scope_hash=state.state.scope.principal_scope_hash)[0]==before


def test_machine_fact_payload_corruption_does_not_become_experience(tmp_path):
    with _runtime_fixture(tmp_path/'machine') as f:
        f.coordinator.dispatch_record(f.record,now_ms=1700);p=f.p7c.plan
        batch=f.facts.get_batch_for_effect(f.record.request.prebound_effect_id)
        # Corrupt the authoritative batch binding instead of supplying a fake observation.
        f.facts._connection.execute('UPDATE execution_fact_batches SET result_payload_sha256=? WHERE result_id=?',
            ('f'*64,batch.result.result_id))
        with pytest.raises(Exception):collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=p.request_id,run_id=p.run_id,generation=p.generation)


def test_machine_hash_and_identity_cannot_be_supplied_by_caller(tmp_path):
    with _runtime_fixture(tmp_path/'machine') as f:
        p=f.p7c.plan
        with pytest.raises(TypeError):collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=p.request_id,run_id=p.run_id,generation=p.generation,expected_observation_sha256='f'*64)
        with pytest.raises(ValueError):collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=p.request_id,run_id='run_'+'f'*64,generation=p.generation)


def test_learning_event_write_failure_preserves_draft_for_explicit_retry(life,tmp_path,monkeypatch):
    with binding(life,tmp_path) as (runtime,hook):
        real=life.system.journal.append
        def fail(life_id,kind,payload,**kwargs):
            if kind=='learning.output_prepared':raise OSError('injected append failure')
            return real(life_id,kind,payload,**kwargs)
        with monkeypatch.context() as m:
            m.setattr(life.system.journal,'append',fail)
            code,result,_=draft(life)
            assert code!=200,result
        card=next(iter(life._scope_state()['learning'].values()))
        result=life._prepare_current_learning_output(card['life_id'],card['learning_id'])
        assert result['output_kind']=='KNOWLEDGE'
        assert len(events(life,'learning.draft_created'))==len(events(life,'learning.output_prepared'))==1


def test_real_journal_tamper_is_detected_before_preparation(life,tmp_path):
    with binding(life,tmp_path) as (runtime,hook):
        _,r,_=draft(life);card=r['learning'];path=life.system.journal._path(card['life_id'])
        raw=path.read_bytes()
        # Alter an existing payload without updating the signed head/chain.
        rows=[json.loads(x) for x in raw.splitlines() if x]
        next(e for e in rows if e['event_type']=='learning.draft_created')['payload']['learning']['title']='tampered'
        path.write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in rows)+'\n',encoding='utf-8')
        try:
            with pytest.raises(EmbeddedLifeError,match='journal_invalid'):
                hook.prepare_from_life(card['life_id'],card['learning_id'])
        finally:path.write_bytes(raw)


def test_tool_selector_reads_current_git_base_without_importing_candidate(life,tmp_path):
    from tests.test_tool_source_candidate_p8 import git,commit,write
    with source_context(life,tmp_path) as (runtime,hook,c,state,_):
        marker=tmp_path/'candidate-was-executed'
        write(c['repo'],'src/world_understanding/p10_candidate.py',f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        candidate=commit(c['repo'])
        selector={'kind':'TOOL_SOURCE','world_state_id':state.state.world_state_id,
            'candidate_commit':candidate,'requested_action_ids':['file.read']}
        status,r,_=draft(life,'tool',extra={'draft_artifact':{'content':'source proposal','source_evolution':selector}})
        assert status==200,r
        output=r['learning_output']
        assert output['output_kind']=='TOOL_SOURCE' and output['body']['candidate']['base_commit']==c['commit']
        assert output['body']['candidate']['candidate_commit']==candidate
        assert not marker.exists() and output['body']['publication_performed'] is False
        assert not life._scope_state()['capabilities']


def test_original_completion_gate_is_read_not_replaced_and_missing_p19_still_blocks(tmp_path):
    from total_gateway.completion_gate import CompletionGate,CompletionRequirements
    with _runtime_fixture(tmp_path/'machine') as f:
        f.coordinator.dispatch_record(f.record,now_ms=1700);p=f.p7c.plan
        final=f.coordinator.finalize_plan(p)
        requirements=CompletionRequirements(request_id=p.request_id,run_id=p.run_id,generation=p.generation,
            required_execution_effect_ids=tuple(sorted(final.leaf_effect_ids)),
            execution_lineage_effect_ids=tuple(sorted((final.parent_effect_id,*final.lineage_effect_ids))))
        gate=CompletionGate(f.p7c.objects,f.facts,lambda e:f.p7c.store.get_effect(e).state)
        decision=gate.evaluate(requirements)
        assert decision.outcome=='COMPLETED'
        f.p7c.store.record_completion_decision(decision,recorded_at_ms=2000)
        collected=collect_composition_learning_evidence(f.p7c.store,f.coordinator,
            request_id=p.request_id,run_id=p.run_id,generation=p.generation)
        assert collected['completion_sha256']==decision.decision_sha256
        assert 'SEALED_COMPLETION_REQUIRED' not in collected['blockers']
        assert 'AUTHORITATIVE_P19_READINESS_REQUIRED' in collected['blockers']
        assert collected['may_write_memory'] is False


def test_known_knowledge_confirm_preserves_signed_consent_and_publishes_once(life,tmp_path):
    seen=[];life.set_artifact_publisher(lambda a:seen.append(a) or {'knowledge_document_id':'k1'})
    with binding(life,tmp_path):
        _,r,_=draft(life);c=r['learning'];assert c['status']=='awaiting_user'
        code,result,_=life.request('POST','/api/v1/v3/learning/confirm',
            {'learning_id':c['learning_id'],'draft_sha256':c['draft_sha256']})
        assert code==200,result
        assert result['learning']['status']=='published' and len(seen)==1
        assert len(events(life,'learning.confirmed'))==1
        assert len(events(life,'learning.output_prepared'))==2


def test_actual_gateway_start_installs_both_existing_call_sites(tmp_path,monkeypatch):
    from total_gateway.bootstrap import GatewayConfig
    from total_gateway.runtime import GatewayRuntime
    root=Path(__file__).parents[1]
    for key,sub in [('APPDATA','appdata'),('TIANGONG_DOCUMENTS_PATH','documents'),
                    ('TIANGONG_LIFE_DATA_ROOT','life-data'),('TIANGONG_LIFE_RUNTIME_ROOT','life-runtime'),
                    ('TIANGONG_WORLD_STATE_ROOT','world-state')]:
        monkeypatch.setenv(key,str(tmp_path/sub))
    workspace=tmp_path/'workspace';workspace.mkdir()
    config=GatewayConfig(environment='test',deployment_mode='embedded',port=0,
        state_root=tmp_path/'gateway-root',min_free_bytes=1_048_576,
        backend_internal_token='test-binding-token-'+('x'*48),release_source_root=root,
        workspace_root=workspace,skill_root=root/'app/backend/tiangong-backend/_internal/omni_body_skill')
    runtime=GatewayRuntime.start(config)
    try:
        hook=runtime.learning_output_binding
        assert type(hook) is LearningOutputProductionBinding
        assert runtime.life_service._learning_output_preparer.__self__ is hook
        assert runtime.orchestration._life_execution_commit.__self__ is hook
        assert hook._runtime.store is runtime.store and hook._runtime.life_service is runtime.life_service
    finally:runtime.close()


def test_source_preparation_does_not_acquire_world_runtime_lock_under_life(life,tmp_path):
    import concurrent.futures
    from tests.test_method_source_publication_p9 import _publication
    with source_context(life,tmp_path) as (runtime,hook,c,state,_):
        envelope,_,_=_publication(c,base=state)
        data={'draft_artifact':{'content':'proposal','source_evolution':{
            'kind':'METHOD_SOURCE','world_state_id':state.state.world_state_id,
            'archive_sha256':envelope.payload_inline['archive_sha256']}}}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with c['runtime']._lock:
                future=pool.submit(draft,life,'skill','draft',data)
                code,result,_=future.result(timeout=15)
                assert code==200,result
        assert result['learning_output']['output_kind']=='METHOD_SOURCE'
