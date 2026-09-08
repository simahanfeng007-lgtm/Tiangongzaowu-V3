"""R2-B writeback faults on real temporary Memory/SQLite; observations are fixtures.

Production collection tests below use the existing registered Gateway chain.
None of these fixtures is independent production/model acceptance.
"""
from pathlib import Path
import json

import pytest

from contracts import canonical_json_bytes, canonical_sha256
from life_service.memory_coordinator import MemoryCoordinator
from life_service.memory_invalidation import invalidate_cascade
from life_service.store import LifeShadowStore, LifeShadowStoreError
from total_gateway.learning_experience_writeback import (
    ExperienceWritebackError, commit_observation_via_memory,
)
from tests.test_capability_experience_p5 import _observation


@pytest.fixture
def memory(tmp_path):
    store=LifeShadowStore.open(tmp_path/'memory.shadow.sqlite3',create=True,now_ms=0)
    try: yield MemoryCoordinator(store)
    finally: store.close()


def _write(memory, obs=None, *, event=None, now_ms=100):
    obs=obs or _observation(1)
    return commit_observation_via_memory(memory,obs,
        source_event_sha256=event or canonical_sha256({'event':obs.observation_id}),
        evidence_sha256=canonical_sha256({'machine-evidence':obs.observation_id}),
        current_sources=(*obs.plan.method_source_refs,*obs.plan.action_source_refs),now_ms=now_ms)


def _rows(store):
    return {r[0]:store._connection.execute('SELECT count(*) FROM "'+r[0]+'"').fetchone()[0]
        for r in store._connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}


def _state(memory,result):
    deriv=memory.store.get_memory_derivation(result['derivation_id'])
    assertion=memory.store.get_memory_assertion(deriv.memory_id,deriv.memory_revision)
    return json.loads(memory.store.read_protected_payload(assertion.protected_payload_id))['experience_state']


def test_one_real_memory_write_is_probation_data_and_retry_is_read_only(memory):
    first=_write(memory)
    assert first['status']=='CAPABILITY_EXPERIENCE_COMMITTED'
    assert first['success_count']==1 and first['lifecycle']=='PROBATION'
    assert first['memory_write_performed'] is True
    before=_rows(memory.store)
    second=_write(memory)
    assert second['memory_write_performed'] is False and second['duplicate'] is True
    assert second['state_sha256']==first['state_sha256']
    assert _rows(memory.store)==before
    assert second['may_execute'] is second['may_authorize'] is False
    assert _state(memory,second)['experience']['success_count']==1


def test_more_observations_advance_same_head_without_duplicating_old_observation(memory):
    first=_write(memory,_observation(1))
    second=_write(memory,_observation(2))
    assert second['success_count']==2
    before=_rows(memory.store)
    assert _write(memory,_observation(1))['success_count']==2
    assert before==_rows(memory.store)
    parent=memory.store.get_memory_derivation(second['derivation_id'])
    assert len(parent.lineage_root_event_ids)==2
    assert first['derivation_id'] in {p.parent_derivation_id for p in parent.parent_memory_refs}


def test_same_observation_id_with_other_event_is_not_counted(memory):
    _write(memory)
    with pytest.raises(ExperienceWritebackError,match='duplicate_observation_lineage'):
        _write(memory,event='f'*64)


def test_false_trace_cannot_keep_old_pass_even_when_outer_hash_recomputed(memory):
    obs=_observation(1)
    trace=obs.trace.model_copy(update={'unknown_side_effects':True}).with_computed_sha256()
    bad=obs.model_copy(update={'trace':trace}).with_computed_sha256()
    with pytest.raises(ExperienceWritebackError,match='attribution_not_reconstructed'):
        _write(memory,bad)
    assert memory.store._connection.execute('SELECT count(*) FROM memory_assertions').fetchone()[0]==0


def test_current_source_mismatch_does_not_write_parent(memory):
    obs=_observation(1)
    with pytest.raises(ExperienceWritebackError,match='source_revalidation'):
        commit_observation_via_memory(memory,obs,source_event_sha256='a'*64,evidence_sha256='b'*64,
            current_sources=obs.plan.action_source_refs,now_ms=100)
    assert memory.store._connection.execute('SELECT count(*) FROM memory_assertions').fetchone()[0]==0


def test_old_observation_does_not_regress_prior_time(memory):
    _write(memory,_observation(2))
    with pytest.raises(ExperienceWritebackError,match='time_regressed'):
        _write(memory,_observation(1))


def test_invalidated_ancestor_is_never_silently_reused(memory):
    first=_write(memory)
    head=memory.store.get_memory_derivation(first['derivation_id'])
    parent=head.parent_memory_refs[0].parent_derivation_id
    invalidate_cascade(memory.store,derivation_id=parent,reason='invalidated',invalidated_at_ms=101,
                       source_trigger_ref='test.retraction')
    with pytest.raises(Exception): _write(memory,now_ms=102)
    # A retired head must not be resurrected by replaying the same successful observation.
    assert not memory.store.is_derivation_active(parent)


def test_l3_failure_preserves_l1_evidence_and_retry_has_one_success(memory,monkeypatch):
    original=memory._materialize_promotion
    def fail(**kwargs): raise OSError('injected before L3')
    with monkeypatch.context() as m:
        m.setattr(memory,'_materialize_promotion',fail)
        with pytest.raises(OSError): _write(memory)
    assert memory.store._connection.execute("SELECT count(*) FROM memory_derivations WHERE layer='L1_STREAM'").fetchone()[0]==1
    assert memory.store._connection.execute("SELECT count(*) FROM memory_derivations WHERE layer='L3_EXPERIENCE'").fetchone()[0]==0
    assert _write(memory)['success_count']==1


def test_atomic_head_guard_retries_after_another_connection_wins(memory,tmp_path,monkeypatch):
    original=memory._materialize_promotion
    rival=LifeShadowStore.open(tmp_path/'memory.shadow.sqlite3',create=False,now_ms=0)
    seen=[]
    def compete(**kwargs):
        if not seen:
            seen.append(True)
            _write(MemoryCoordinator(rival),_observation(1))
        return original(**kwargs)
    try:
        with monkeypatch.context() as m:
            m.setattr(memory,'_materialize_promotion',compete)
            result=_write(memory,_observation(2))
        assert result['success_count']==2
        state=_state(memory,result)
        assert len(state['observation_ids'])==2
    finally: rival.close()


def test_cas_contention_is_bounded_and_no_l3_written(memory,monkeypatch):
    attempts=[]
    def fail(**kwargs): attempts.append(kwargs['head_guard']); raise LifeShadowStoreError('memory promotion head changed')
    with monkeypatch.context() as m:
        m.setattr(memory,'_materialize_promotion',fail)
        with pytest.raises(ExperienceWritebackError,match='contention_retry_required'): _write(memory)
    assert len(attempts)==3
    assert _write(memory)['success_count']==1


def test_parent_invalidated_after_preflight_is_rechecked_in_original_write_transaction(memory,monkeypatch):
    original=memory._materialize_promotion
    def revoke(**kwargs):
        invalidate_cascade(memory.store,derivation_id=kwargs['parents'][0].derivation_id,reason='invalidated',
                           invalidated_at_ms=101,source_trigger_ref='test.retraction')
        return original(**kwargs)
    with monkeypatch.context() as m:
        m.setattr(memory,'_materialize_promotion',revoke)
        with pytest.raises(LifeShadowStoreError,match='parent is inactive'):_write(memory)
    assert memory.store._connection.execute("SELECT count(*) FROM memory_derivations WHERE layer='L3_EXPERIENCE'").fetchone()[0]==0


def test_reopen_uses_persisted_head_and_does_not_count_again(tmp_path):
    path=tmp_path/'memory.shadow.sqlite3'
    with LifeShadowStore.open(path,create=True,now_ms=0) as store:
        first=_write(MemoryCoordinator(store))
    with LifeShadowStore.open(path,create=False,now_ms=100) as store:
        before=_rows(store)
        result=_write(MemoryCoordinator(store))
        assert result['state_sha256']==first['state_sha256'] and result['duplicate']
        assert before==_rows(store)


from tests.test_learning_publication_freeze_p10 import life  # noqa: F401


@pytest.fixture
def production(life,tmp_path,monkeypatch):
    """Real registered/ticketed P7 execution + P6 World + P19 oracle.

    Only the backend reply and the legacy Method snapshot provider are test
    fixtures; no current-source verifier, readiness, Memory writer or gate is mocked.
    """
    from dataclasses import replace
    from types import SimpleNamespace
    from contracts import derive_effect_identity
    from contracts.verification import AcceptancePredicate
    from contracts.world_understanding.time import WorldTime
    from contracts.world_understanding.world_cut import WorldCut,SourceWatermark,derive_world_cut_id
    from v3.world_understanding_production import _scope
    from world_understanding.production import ProductionWorldUnderstandingRuntime
    from world_understanding.software_world import SoftwareWorldFrame,SparseWorldGraph
    from world_understanding.domain_contribution import compile_tool_capability_contribution,compile_skill_method_contribution
    from world_understanding.world_state import WorldStateStore,WorldStateMaterializer,MaterializationInput,materialize_one_world_state
    from total_gateway.method_source_run_binding import MethodRunSourceResolver
    from total_gateway.learning_output_binding import LearningOutputProductionBinding
    from tests.test_composition_step_execution_p7d1 import _runtime_fixture,p7c1
    from total_gateway.verification_plan_executor import VerificationPlanExecutor
    from total_gateway.completion_gate import CompletionGate,CompletionRequirements
    from tests.test_learning_output_binding_p10 import execution_payload
    root=(tmp_path/'machine').resolve()
    details={}
    orig_lineage=p7c1.p7c0._register_request_lineage
    orig_candidates=p7c1.p7c0.build_candidate_snapshot
    orig_context=p7c1.p4._context
    orig_vbind=p7c1.p7c0.build_system_verification_binding
    def lineage(store):
        env,req,run=orig_lineage(store)
        details.update(store=store,env=env,request=req,run=run)
        return env,req,run
    def candidate_world(tools,methods,**kw):
        identity={'life_id':life._active()['life_id'],'principal_scope_hash':details['env'].principal_scope_hash,
                  'workspace_id':'workspace-'+canonical_sha256(str(root))}
        scope=_scope(identity)
        tm=WorldTime(valid_from_ms=1100,observed_at_ms=1100,recorded_at_ms=1100)
        marks=(SourceWatermark(source_kind='GIT_CODE',watermark_type='git.commit',watermark_value='a'*40,sequence=1,watermark_sha256='0'*64).with_computed_hash(),)
        cut=WorldCut(cut_id=derive_world_cut_id(world_scope_hash=scope.world_scope_hash,watermarks=marks),
                    scope=scope,source_watermarks=marks,time=tm,cut_sha256='0'*64).with_computed_hash()
        frame=SoftwareWorldFrame.build(scope=scope,workspace=identity['workspace_id'],repository='repo.fixture',
            worktree='worktree.fixture',branch='fixture',commit='a'*40,environment='test',time=tm,world_cut=cut)
        graph=SparseWorldGraph(frame)
        store=WorldStateStore(root=tmp_path/'world')
        contributions=(compile_tool_capability_contribution(frame,cut,tools),compile_skill_method_contribution(frame,cut,methods))
        state=materialize_one_world_state(WorldStateMaterializer(store),MaterializationInput(frame=frame,cut=cut,graph=graph,
            materialized_at_ms=1100,source_transaction_id='test.production-method-input'),contributions)
        class LegacyFixtureSource:
            def load(self,stored):
                assert stored.state.scope==scope
                return methods
            def __call__(self,*a): raise AssertionError('this test never publishes a Method revision')
        world=ProductionWorldUnderstandingRuntime(store=store,frame_factory=lambda *a: frame,
                                                 method_revision_resolver=LegacyFixtureSource())
        details['store'].configure_method_source_lifecycle(MethodRunSourceResolver(details['store'],world))
        life.set_world_identity_provider(lambda _id:dict(identity))
        details.update(world=world,state=state,frame=frame,contributions=contributions,identity=identity,tools=tools,methods=methods)
        return orig_candidates(tools,methods,**kw)
    def context(**kw):
        ctx=orig_context(**kw);state=details['state']
        return replace(ctx,world_state_ref=state.state.world_state_id,world_state_sha256=state.state.state_sha256,
                       context_sha256='0'*64).with_computed_sha256()
    def vbind(**kw):
        req,run=details['request'],details['run']
        intent=canonical_sha256({'domain':'tiangong.test.composition-parent.v1','request_id':req.request_id,
                                'run_id':run.run_id,'generation':1})
        parent=derive_effect_identity(request_id=req.request_id,run_id=run.run_id,run_sequence=1,generation=1,
                                      effect_kind='execution',ordinal=0,intent_sha256=intent)
        kw.update(predicate=AcceptancePredicate.create(predicate_type='effect.terminal_succeeded',subject_kind='effect',params={}),
                  subject_identity=parent.effect_id)
        return orig_vbind(**kw)
    with monkeypatch.context() as m:
        m.setattr(p7c1.p7c0,'_register_request_lineage',lineage)
        m.setattr(p7c1.p7c0,'build_candidate_snapshot',candidate_world)
        m.setattr(p7c1.p4,'_context',context)
        m.setattr(p7c1.p7c0,'build_system_verification_binding',vbind)
        with _runtime_fixture(root) as fixture:
            fixture.coordinator.dispatch_record(fixture.record,now_ms=1700)
            plan=fixture.p7c.plan;store=fixture.p7c.store
            final=fixture.coordinator.finalize_plan(plan)
            verification=store.get_active_verification_plan(request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation)
            registry=store.get_verification_registry_snapshot_by_sha256(verification.registry_snapshot_sha256)
            ready=VerificationPlanExecutor(snapshot=registry,store=store,object_store=fixture.p7c.objects,
                fact_ledger=fixture.facts,plan=verification).execute(evaluated_at_ms=final.completed_at_ms+1)
            assert ready.verification_ready, ready
            requirements=CompletionRequirements(request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation,
                verification_mode='PLAN_BOUND',required_execution_effect_ids=tuple(sorted(final.leaf_effect_ids)),
                execution_lineage_effect_ids=tuple(sorted((final.parent_effect_id,*final.lineage_effect_ids))))
            decision=CompletionGate(fixture.p7c.objects,fixture.facts,lambda e:store.get_effect(e).state).evaluate(
                requirements,verification_readiness=ready,active_plan=verification,
                verification_dispositions=(),verification_failure_evidences=(),
                readiness_authority_reader=store.get_latest_verification_readiness)
            assert decision.outcome=='COMPLETED',decision
            store.record_completion_decision(decision,recorded_at_ms=ready.evaluated_at_ms+1)
            runtime=SimpleNamespace(life_service=life,store=store,config=SimpleNamespace(workspace_root=root),
                                    orchestration=SimpleNamespace(_composition_steps=fixture.coordinator))
            hook=LearningOutputProductionBinding(runtime)
            payload=execution_payload(life,plan,final.fact_ids,store.get_request_entry(plan.request_id).session_scope_hash,
                                      ready.evaluated_at_ms+2)
            details.update(runtime=runtime,hook=hook,payload=payload,fixture=fixture,life=life)
            yield SimpleNamespace(**details)


def test_original_terminal_callback_collects_machine_evidence_and_writes_l3_once(production):
    p=production
    calls=p.fixture.backend.calls
    result=p.hook.commit_execution(p.payload)
    assert result['ok'],result
    assert result['learning_evidence']['status']=='MACHINE_EVIDENCE_AUDITED',result
    assert result['learning_experience']['status']=='CAPABILITY_EXPERIENCE_COMMITTED',result
    assert result['learning_experience']['success_count']==1
    assert result['learning_experience']['audit_pending'] is False
    before=_rows(p.life._memory_coordinator().store)
    duplicate=p.hook.commit_execution(p.payload)
    assert duplicate['learning_experience']['duplicate'] is True,duplicate
    assert duplicate['learning_experience']['audit_pending'] is False
    assert _rows(p.life._memory_coordinator().store)==before
    assert p.fixture.backend.calls==calls


def test_negative_admission_remains_negative_not_an_extra_success(memory):
    first=_write(memory,_observation(1))
    negative=_observation(2,outcome='FAILURE',failure_category='RUNTIME_FAILURE',failure_reason_codes=('fixture.failure',))
    result=_write(memory,negative)
    assert result['admission']=='NEGATIVE_EVIDENCE'
    assert result['success_count']==1 and result['failure_count']==1
    assert len(_state(memory,result)['negative_evidence_ids'])==1


def test_low_quality_never_counts_as_positive_even_with_completion(memory):
    result=_write(memory,_observation(1,quality_milli=0))
    assert result['success_count']==0 and result['failure_count']==1
    assert result['admission']=='NEGATIVE_EVIDENCE'


def test_injection_flag_cannot_be_omitted_by_rehashing_trace(memory):
    from tests.test_capability_experience_p5 import _trace,_scoped_plan
    plan=_scoped_plan(1)
    obs=_observation(1,plan=plan,trace=_trace(plan,prompt_injection_present=True))
    result=_write(memory,obs)
    assert result['success_count']==0 and result['failure_count']==1


def test_sqlite_rolls_back_payload_assertion_derivation_head_and_outbox_together(memory,monkeypatch):
    repository=memory.store._memory_repository
    original=repository._put_memory_derivation_locked
    counts=[]
    def fail(derivation,*,activate_head):
        if derivation.layer=='L3_EXPERIENCE':
            # Assertion/payload/change/outbox have already been inserted in
            # the open ORIGINAL transaction. Throw at its next real boundary.
            counts.append(repository._connection.in_transaction)
            raise OSError('injected after assertion and outbox insertion')
        return original(derivation,activate_head=activate_head)
    with monkeypatch.context() as m:
        m.setattr(repository,'_put_memory_derivation_locked',fail)
        with pytest.raises(OSError):_write(memory)
    assert counts==[True]
    assert memory.store.memory_change_head()==1  # only the retained L1
    assert memory.store.count_pending_memory_outbox()==1
    assert memory.store._connection.execute('SELECT count(*) FROM memory_assertions').fetchone()[0]==1
    assert memory.store._connection.execute('SELECT count(*) FROM memory_active_heads').fetchone()[0]==0
    result=_write(memory)
    assert result['success_count']==1 and memory.store.memory_change_head()==2


def test_cas_winner_from_other_connection_duplicate_is_not_double_counted(memory,tmp_path,monkeypatch):
    original=memory._materialize_promotion
    rival=LifeShadowStore.open(tmp_path/'memory.shadow.sqlite3',create=False,now_ms=0)
    calls=[]
    def compete(**kwargs):
        if not calls:
            calls.append(True);_write(MemoryCoordinator(rival))
        return original(**kwargs)
    try:
        with monkeypatch.context() as m:
            m.setattr(memory,'_materialize_promotion',compete)
            result=_write(memory)
        assert result['duplicate'] and result['success_count']==1
        assert memory.store.memory_change_head()==2
    finally:rival.close()


def test_source_change_with_same_family_cannot_mix_statistics(memory):
    from tests.test_capability_experience_p5 import _scoped_plan,_trace
    from world_understanding.capability_composition.compiler import computed_plan_sha256
    _write(memory,_observation(1))
    plan=_scoped_plan(2)
    newref=plan.action_source_refs[0].model_copy(update={'source_sha256':'e'*64})
    plan=plan.model_copy(update={'action_source_refs':(newref,)})
    # Recompute only via the original plan hash helper; P5 still requires all
    # its internal source/plan consistency and must not reuse the old aggregate.
    plan=plan.model_copy(update={'plan_sha256':computed_plan_sha256(plan)})
    obs=_observation(2,plan=plan,trace=_trace(plan,ordinal=2))
    with pytest.raises(ValueError):_write(memory,obs)


def test_retracted_ancestor_blocks_even_if_newer_head_has_another_live_parent(memory):
    first=_write(memory,_observation(1));second=_write(memory,_observation(2))
    head=memory.store.get_memory_derivation(first['derivation_id'])
    parent=head.parent_memory_refs[0].parent_derivation_id
    invalidate_cascade(memory.store,derivation_id=parent,reason='invalidated',invalidated_at_ms=101,source_trigger_ref='test.retract')
    # Generic invalidation can retain a descendant supported by its other
    # parent. Capability counters cannot keep a retracted observation hidden.
    with pytest.raises(ExperienceWritebackError,match='parent_inactive'):_write(memory,_observation(3),now_ms=102)


def test_corrupt_protected_prior_cannot_become_a_new_aggregate(memory):
    first=_write(memory)
    head=memory.store.get_memory_derivation(first['derivation_id'])
    assertion=memory.store.get_memory_assertion(head.memory_id,head.memory_revision)
    memory.store._connection.execute("UPDATE protected_payloads SET ciphertext=? WHERE payload_id=?",(b'corrupted'+b'x'*20,assertion.protected_payload_id))
    with pytest.raises(LifeShadowStoreError):_write(memory,_observation(2))
    assert memory.store.get_active_memory_head(life_id=head.life_id,principal_ref=head.principal_ref,
        claim_key=head.claim_key,layer='L3_EXPERIENCE').derivation_id==head.derivation_id


def test_process_reopen_after_l3_commit_before_receipt_is_idempotent(memory,tmp_path):
    import os,subprocess,sys
    obs=_observation(1);first=_write(memory,obs)
    config={'path':str(tmp_path/'memory.shadow.sqlite3'),'observation':obs.model_dump(mode='json'),
            'event':canonical_sha256({'event':obs.observation_id}),'evidence':canonical_sha256({'machine-evidence':obs.observation_id})}
    code='''
import json,sys
from life_service.store import LifeShadowStore
from life_service.memory_coordinator import MemoryCoordinator
from total_gateway.learning_experience_writeback import commit_observation_via_memory
from world_understanding.capability_composition.capability_experience_policy import CapabilityExperienceObservationV1
v=json.loads(sys.argv[1]);o=CapabilityExperienceObservationV1.model_validate_json(json.dumps(v['observation']))
from pathlib import Path
with LifeShadowStore.open(Path(v['path']),create=False,now_ms=100) as s:
 r=commit_observation_via_memory(MemoryCoordinator(s),o,source_event_sha256=v['event'],evidence_sha256=v['evidence'],current_sources=(*o.plan.method_source_refs,*o.plan.action_source_refs),now_ms=100)
 print(json.dumps(r))
'''
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1]/'src')}
    result=subprocess.run([sys.executable,'-c',code,json.dumps(config)],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    receipt=json.loads(result.stdout)
    assert receipt['duplicate'] and receipt['state_sha256']==first['state_sha256']
    assert memory.store.memory_change_head()==2


def test_deferred_write_does_not_erase_completed_execution_and_callback_retries(production,monkeypatch):
    from total_gateway import learning_experience_writeback as module
    p=production
    original=module.commit_observation_via_memory
    with monkeypatch.context() as m:
        m.setattr(module,'commit_observation_via_memory',lambda *a,**kw:(_ for _ in ()).throw(OSError('injected commit error')))
        result=p.hook.commit_execution(p.payload)
    assert result['ok'] and result['execution']['status']=='completed'
    assert result['learning_experience']['status']=='EXPERIENCE_DEFERRED'
    assert result['learning_experience']['memory_write_performed'] is None
    assert result['learning_experience']['retryable']
    retried=p.hook.commit_execution(p.payload)
    assert retried['duplicate'] and retried['learning_experience']['success_count']==1
    assert p.fixture.backend.calls==1


def test_receipt_failure_after_memory_commit_retries_without_second_promotion(production,monkeypatch):
    p=production;journal=p.life.system.journal;append=journal.append
    def fail(life_id,event_type,*args,**kwargs):
        if event_type=='learning.experience_committed':raise OSError('receipt unavailable')
        return append(life_id,event_type,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(journal,'append',fail)
        first=p.hook.commit_execution(p.payload)
    assert first['learning_experience']['memory_write_performed'] and first['learning_experience']['audit_pending']
    before=_rows(p.life._memory_coordinator().store)
    second=p.hook.commit_execution(p.payload)
    assert second['learning_experience']['duplicate'] and not second['learning_experience']['audit_pending']
    assert before==_rows(p.life._memory_coordinator().store)
    assert len([e for e in journal.events(p.identity['life_id']) if e['event_type']=='learning.experience_committed'])==1


@pytest.mark.parametrize('mutation',['descriptor','removed','stale','principal','unavailable'])
def test_current_world_change_keeps_old_task_but_cannot_count_current_experience(production,mutation):
    from dataclasses import replace
    from world_understanding.software_world import SparseWorldGraph
    from world_understanding.world_state import MaterializationInput,WorldStateMaterializer
    p=production
    if mutation=='principal':
        p.life.set_world_identity_provider(lambda _: {**p.identity,'principal_scope_hash':'f'*64})
    else:
        graph=SparseWorldGraph(p.frame)
        changed=[]
        for e in p.state.entities:
            if e.entity_type=='ToolCapability':
                if mutation=='removed':continue
                if mutation in {'descriptor','unavailable'}:
                    changed_key='descriptor_sha256' if mutation=='descriptor' else 'availability'
                    changed_value='f'*64 if mutation=='descriptor' else 'UNAVAILABLE'
                    attrs=tuple(a.model_copy(update={'value':a.value.model_copy(update={'string_value':changed_value})})
                        if a.key==changed_key else a for a in e.attributes)
                    e=e.model_copy(update={'attributes':attrs,'revision':e.revision+1,'supersedes_entity_sha256':e.entity_sha256}).with_computed_hash()
            graph.upsert_entity(e)
        for r in p.state.relations:graph.upsert_relation(r)
        sources=tuple(sorted(k for b in p.state.dependencies.bindings for k in b.source_keys if k.startswith('source:')))
        data=MaterializationInput(frame=p.frame,cut=p.state.cut,graph=graph,
            dependency_bindings=p.state.dependencies.bindings,source_transaction_id='test.source-changed',materialized_at_ms=2000,
            changed_source_keys=tuple(sorted(set(sources))) if mutation=='stale' else ())
        WorldStateMaterializer(p.world.store).materialize(data)
    result=p.hook.commit_execution(p.payload)
    assert result['ok'] and result['execution']['status']=='completed'
    assert result['learning_experience']['status']=='EXPERIENCE_DEFERRED',result
    assert p.life._memory_coordinator().store._connection.execute("SELECT count(*) FROM memory_derivations WHERE layer='L3_EXPERIENCE'").fetchone()[0]==0
    assert p.world.store.get(p.state.state.world_state_id)==p.state


def test_p19_missing_after_execution_is_not_fabricated_as_pass(production):
    p=production
    p.store._connection.execute('DELETE FROM verification_readiness')
    result=p.hook.commit_execution(p.payload)
    assert result['ok'] and result['learning_experience']['status']=='EXPERIENCE_DEFERRED'
    assert p.life._memory_coordinator().store._connection.execute("SELECT count(*) FROM memory_derivations WHERE layer='L3_EXPERIENCE'").fetchone()[0]==0


def test_model_fields_cannot_replace_machine_observation_prior_or_parents(production):
    p=production
    forged={**p.payload,'observation':{'outcome':'FAILURE','quality_milli':0},
            'expected_prior_state_sha256':'f'*64,'parent_derivation_ids':['invented']}
    result=p.hook.commit_execution(forged)
    # The existing terminal API ignores unrelated fields. Its machine collector
    # must use the actual registered/verified run, not these invented fields.
    assert result['ok'] and result['learning_experience']['success_count']==1
    assert result['learning_experience']['failure_count']==0
    repeat=p.hook.commit_execution(p.payload)
    assert repeat['learning_experience']['duplicate']
    assert repeat['learning_experience']['state_sha256']==result['learning_experience']['state_sha256']


def test_post_commit_transport_error_reports_unknown_then_retries_existing_head(production,monkeypatch):
    from life_service.memory_coordinator import MemoryCoordinator
    p=production;original=MemoryCoordinator._materialize_promotion
    def after_commit(self,**kwargs):
        original(self,**kwargs)
        raise OSError('injected after real L3 COMMIT')
    with monkeypatch.context() as m:
        m.setattr(MemoryCoordinator,'_materialize_promotion',after_commit)
        first=p.hook.commit_execution(p.payload)
    assert first['ok'] and first['learning_experience']['memory_write_performed'] is None
    before=_rows(p.life._memory_coordinator().store)
    second=p.hook.commit_execution(p.payload)
    assert second['learning_experience']['duplicate'] and second['learning_experience']['success_count']==1
    assert before==_rows(p.life._memory_coordinator().store)
    assert p.fixture.backend.calls==1


def test_unchanged_sources_in_new_world_cut_allow_original_run_attribution(production):
    from world_understanding.world_state import MaterializationInput,WorldStateMaterializer
    from world_understanding.software_world import SparseWorldGraph
    p=production;graph=SparseWorldGraph(p.frame)
    for e in p.state.entities:graph.upsert_entity(e)
    for r in p.state.relations:graph.upsert_relation(r)
    newer=WorldStateMaterializer(p.world.store).materialize(MaterializationInput(frame=p.frame,cut=p.state.cut,graph=graph,
        dependency_bindings=p.state.dependencies.bindings,materialized_at_ms=2000,source_transaction_id='test.unrelated'))
    assert newer.state_ref!=p.state.state_ref
    result=p.hook.commit_execution(p.payload)
    assert result['learning_experience']['success_count']==1,result
    assert p.fixture.p7c.plan.world_state_sha256==p.state.state.state_sha256


def test_memory_write_does_not_advance_world_or_reauthorize_tool(production):
    p=production;prior=p.world.store.current_candidates(life_id=p.identity['life_id'],principal_scope_hash=p.identity['principal_scope_hash'])
    status=p.store.method_source_retention_status()
    result=p.hook.commit_execution(p.payload)
    assert result['learning_experience']['context_section']=='DATA'
    assert result['learning_experience']['may_execute'] is result['learning_experience']['may_authorize'] is False
    assert p.world.store.current_candidates(life_id=p.identity['life_id'],principal_scope_hash=p.identity['principal_scope_hash'])==prior
    assert p.store.method_source_retention_status()==status
    assert p.life._memory_coordinator().store.list_world_candidate_outbox()==()


def test_new_receipt_event_uses_original_replay_registry():
    from life_service.journal_replay import EVENT_REGISTRY,EventClass
    assert EVENT_REGISTRY['learning.experience_committed']==EventClass.AUDIT_ONLY


def _recovery_worker(production):
    from total_gateway.orchestration import GatewayOrchestrationWorker
    p=production
    worker=object.__new__(GatewayOrchestrationWorker)
    def forbidden(_): raise AssertionError('recovered tail must not recommit execution')
    worker._life_execution_commit=forbidden
    worker._life_execution_learning_recovery=p.hook.resume_execution_learning
    worker._life_compat_client=p.life
    worker._repository_evidence_provider=None
    payload={**p.payload,'user_goal_sha256':canonical_sha256('goal'),'final_result_sha256':canonical_sha256('result')}
    p.life.commit_execution(payload)
    args={k:payload[k] for k in ('request_id','run_id','generation','life_id','session_scope_hash','fact_ids','completed_at_ms')}
    args.update(principal_scope_hash=p.identity['principal_scope_hash'],workspace_id=p.identity['workspace_id'],
                user_goal='goal',final_result='result',require_authority=True,recover_existing=True)
    return worker,args


def test_actual_strict_worker_recovery_completes_missing_memory_without_execution_recommit(production):
    p=production;worker,args=_recovery_worker(p)
    before=len([e for e in p.life.system.journal.events(p.identity['life_id']) if e['event_type']=='execution.commit'])
    result=worker._commit_life_execution(**args)
    assert result['ok'] and result['duplicate']
    assert result['learning_experience']['success_count']==1,result
    assert not result['learning_experience']['audit_pending']
    tables=_rows(p.life._memory_coordinator().store)
    repeat=worker._commit_life_execution(**args)
    assert repeat['learning_experience']['duplicate'] and repeat['learning_experience']['success_count']==1
    assert tables==_rows(p.life._memory_coordinator().store)
    assert len([e for e in p.life.system.journal.events(p.identity['life_id']) if e['event_type']=='execution.commit'])==before
    assert p.fixture.backend.calls==1


def test_worker_recovery_preserves_terminal_on_unknown_memory_outcome_and_can_retry(production):
    p=production;worker,args=_recovery_worker(p)
    real=worker._life_execution_learning_recovery
    def lost_response(execution):
        real(execution)
        raise OSError('after recovery L3 commit')
    worker._life_execution_learning_recovery=lost_response
    first=worker._commit_life_execution(**args)
    assert first['ok'] and first['learning_experience']['write_status']=='UNCONFIRMED'
    assert first['learning_experience']['memory_write_performed'] is None
    worker._life_execution_learning_recovery=real
    second=worker._commit_life_execution(**args)
    assert second['learning_experience']['duplicate'] and second['learning_experience']['success_count']==1


def test_recovery_callback_cannot_replace_the_authoritative_terminal_record(production):
    p=production;worker,args=_recovery_worker(p)
    original=worker._commit_life_execution(**args)
    worker._life_execution_learning_recovery=lambda ex: {'ok':True,'duplicate':True,'execution':{**ex,'generation':99}}
    second=worker._commit_life_execution(**args)
    assert second['ok'] and second['execution']==original['execution']
    assert second['learning_experience']['status']=='EXPERIENCE_DEFERRED'
    with pytest.raises(Exception,match='recovered_execution_mismatch'):
        p.hook.resume_execution_learning({**original['execution'],'generation':99})


def test_runtime_root_passes_recovery_callback_to_existing_worker(monkeypatch,tmp_path):
    from total_gateway.orchestration import GatewayOrchestrationWorker
    from tests.test_learning_output_binding_p10 import test_actual_gateway_start_installs_both_existing_call_sites
    original=GatewayOrchestrationWorker.from_runtime_config
    seen=[]
    def checked(**kwargs):
        assert kwargs['life_execution_learning_recovery'].__self__ is kwargs['life_execution_commit'].__self__
        worker=original(**kwargs)
        assert worker._life_execution_learning_recovery is kwargs['life_execution_learning_recovery']
        seen.append(worker)
        return worker
    monkeypatch.setattr(GatewayOrchestrationWorker,'from_runtime_config',staticmethod(checked))
    test_actual_gateway_start_installs_both_existing_call_sites(tmp_path,monkeypatch)
    assert len(seen)==1
