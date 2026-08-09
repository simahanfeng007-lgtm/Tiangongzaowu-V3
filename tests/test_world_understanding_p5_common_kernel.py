from __future__ import annotations
import pytest
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldValue
from contracts.world_understanding.scope import ScopeBinding,WorldScope,derive_world_id,derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.ingress import WorldIngressEnvelope,derive_ingress_dedup_key,derive_ingress_envelope_id
from contracts.world_understanding.world_cut import WorldCut,SourceWatermark,derive_world_cut_id
from world_understanding.source_compilers import SPECS
from world_understanding.source_compilers.base import make_direct_known
from world_understanding.common import *
from world_understanding.known import KnownClosureEngine,RuleRegistry
from world_understanding.known.rule import RuleSpec,DerivedCandidate

P='a'*64

def scope(life='life.A', principal=P):
    bindings=(ScopeBinding(key='repository',value='repo.main'),)
    wid=derive_world_id(life_id=life,namespace_anchor='primary')
    return WorldScope(life_id=life,world_id=wid,domain_id='software',scope_bindings=bindings,world_scope_hash=derive_world_scope_hash(life_id=life,world_id=wid,domain_id='software',scope_bindings=bindings),principal_scope_hash=principal,privacy_scope='system')

def env(kind='FACT_EXECUTION',*,life='life.A',native='n1',payload=None,t=1):
    sc=scope(life); payload=payload or {'text':native}; sha=canonical_sha256(payload)
    dd=derive_ingress_dedup_key(envelope_kind='SOURCE_RECORD',source_kind=kind,source_native_id=native,payload_sha256=sha,world_scope_hash=sc.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=dd),envelope_kind='SOURCE_RECORD',source_kind=kind,source_native_id=native,producer_ref='p5.test',payload_inline=payload,payload_sha256=sha,source_time=WorldTime(valid_from_ms=t,observed_at_ms=t,recorded_at_ms=t),life_id=life,principal_scope_hash=P,scope_hint=sc,correlation_id=f'c.{native}',dedup_key=dd)

def known(native='n1',*,life='life.A',ptype='FACT_EXECUTION_RECORDED',obj='x',t=1,weight=1000,coverage=None,epistemic='CURRENT',truth='TRUE',source_kind='FACT_EXECUTION'):
    e=env(source_kind,life=life,native=native,payload={'text':obj},t=t)
    row=make_direct_known(e,SPECS[source_kind],proposition_type=ptype,predicate='p5.test',subject_ref=native,object_text=obj,empirical_evidence_weight_milli=weight,authority_domain=SPECS[source_kind].authority_domain)
    return row.model_copy(update={'coverage_milli':coverage,'epistemic_state':epistemic,'truth_state':truth}).with_computed_hash()

def watermark(kind='GIT_CODE',typ='commit',value='abc',seq=1):
    return SourceWatermark(source_kind=kind,watermark_type=typ,watermark_value=value,sequence=seq,watermark_sha256='0'*64).with_computed_hash()

def cut(sc=None,*marks,t=1):
    sc=sc or scope(); marks=tuple(sorted(marks,key=lambda x:x.sort_key()))
    cid=derive_world_cut_id(world_scope_hash=sc.world_scope_hash,watermarks=marks)
    return WorldCut(cut_id=cid,scope=sc,source_watermarks=marks,time=WorldTime(valid_from_ms=t,observed_at_ms=t,recorded_at_ms=t),cut_sha256='0'*64).with_computed_hash()

def boundary(life='life.A',q='BACKGROUND',cut_id=None):
    sc=scope(life)
    return HardBoundary(life_id=life,world_scope_hash=sc.world_scope_hash,principal_scope_hash=sc.principal_scope_hash,queue_class=q,world_cut_id=cut_id)

def event(i,*,life='life.A',q='BACKGROUND',key='same',at=0,cut_id=None,priority=50):
    return RhythmEvent(event_id=f'e{i}',coalesce_key=key,boundary=boundary(life,q,cut_id),arrived_at_ms=at,payload_sha256=canonical_sha256({'i':i}),priority=priority)

def budget():
    return BudgetLedger(BudgetConfig(token_budget=100,compute_budget_ms=100,io_budget_bytes=100,latency_budget_ms=100,interactive_token_reserve=20,interactive_compute_reserve_ms=20,interactive_io_reserve_bytes=20,interactive_latency_reserve_ms=20))

def test_stale_is_epistemic_not_truth_and_does_not_rewrite_true():
    d=EpistemicPlane().evaluate_known(known('s',epistemic='STALE',truth='TRUE'),expected_scope=scope())
    assert d.truth_state=='TRUE' and d.epistemic_state=='STALE' and not d.stable_promotion and 'EPISTEMIC_STALE' in d.reason_codes

def test_unknown_is_not_false_and_not_stable():
    d=EpistemicPlane().evaluate_known(known('u',truth='UNKNOWN',weight=0),expected_scope=scope())
    assert d.truth_state=='UNKNOWN' and d.truth_state!='FALSE' and not d.stable_promotion

def test_negative_evidence_requires_coverage():
    d=EpistemicPlane(min_negative_coverage_milli=1).evaluate_known(known('neg',ptype='FILE_EXISTS',obj='false',coverage=0),expected_scope=scope())
    assert not d.stable_promotion and 'NEGATIVE_EVIDENCE_REQUIRES_COVERAGE' in d.reason_codes

def test_negative_evidence_with_observed_coverage_can_promote():
    d=EpistemicPlane().evaluate_known(known('neg2',ptype='FILE_EXISTS',obj='false',coverage=1000),expected_scope=scope())
    assert d.stable_promotion and d.effective_coverage_milli==1000

def test_scope_mismatch_stops_stable_promotion():
    d=EpistemicPlane().evaluate_known(known('x',life='life.B'),expected_scope=scope('life.A'))
    assert not d.admissible and not d.stable_promotion and 'SCOPE_MISMATCH' in d.reason_codes

def test_provenance_broken_stops_empirical_stable_promotion():
    r=known('p').model_copy(update={'provenance_refs':()}).with_computed_hash(); d=EpistemicPlane().evaluate_known(r,expected_scope=scope())
    assert not d.stable_promotion and 'PROVENANCE_BROKEN' in d.reason_codes

def test_model_output_cannot_self_prove_empirical_reality():
    r=known('m',source_kind='MODEL_OUTPUT',ptype='MODEL_PROPOSED',weight=0)
    assert EpistemicPlane().evaluate_known(r,expected_scope=scope()).stable_promotion
    forged=r.model_copy(update={'empirical_evidence_weight_milli':1}).with_computed_hash(); d=EpistemicPlane().evaluate_known(forged,expected_scope=scope())
    assert not d.stable_promotion and 'SELF_PROOF_EMPIRICAL_FORBIDDEN' in d.reason_codes

def test_evidence_independence_counts_native_source_family_not_revision_hash():
    a=known('same',obj='a'); b=a.model_copy(update={'record_hash':'0'*64}).with_computed_hash(); c=known('different',obj='c')
    assert independent_evidence_count((a.provenance_refs,b.provenance_refs,c.provenance_refs))==2

def test_world_cut_same_and_monotonic_dominance_are_compatible():
    a=cut(scope(),watermark(value='a',seq=1)); b=cut(scope(),watermark(value='b',seq=2))
    assert compare_world_cuts(a,a)=='SAME' and compare_world_cuts(a,b)=='RIGHT_DOMINATES'; require_compatible_world_cuts((a,b))

def test_world_cut_equal_sequence_different_value_rejected():
    a=cut(scope(),watermark(value='a',seq=2)); b=cut(scope(),watermark(value='b',seq=2))
    assert compare_world_cuts(a,b)=='INCOMPATIBLE'
    with pytest.raises(IncompatibleWorldCut): require_compatible_world_cuts((a,b))

def test_world_cut_crossed_source_progress_rejected():
    a=cut(scope(),watermark('GIT_CODE','commit','g2',2),watermark('RUNTIME_ENVIRONMENT','seq','r1',1)); b=cut(scope(),watermark('GIT_CODE','commit','g1',1),watermark('RUNTIME_ENVIRONMENT','seq','r2',2))
    assert compare_world_cuts(a,b)=='INCOMPATIBLE'

def test_event_coalescing_same_boundary_within_debounce():
    c=EventCoalescer(debounce_ms=100); assert c.offer(event(1,at=0))[0]=='NEW'; status,row=c.offer(event(2,at=50)); assert status=='COALESCED' and row.coalesced_count==2

def test_event_coalescing_never_crosses_life_or_cut_boundary():
    c=EventCoalescer(debounce_ms=100); assert c.offer(event(1,life='life.A',at=0,cut_id='cutA'))[0]=='NEW'; assert c.offer(event(2,life='life.B',at=10,cut_id='cutA'))[0]=='NEW'; assert c.offer(event(3,life='life.A',at=20,cut_id='cutB'))[0]=='NEW'

def test_queue_overload_triggers_backpressure():
    r=RhythmPlane(config=RhythmConfig(queue_capacity=1,debounce_ms=0),budget=budget()); assert r.submit(WorkItem(event(1,key='a'),WorkCost(token_cost=1))).disposition=='ADMITTED'; assert r.submit(WorkItem(event(2,key='b',at=2),WorkCost(token_cost=1))).disposition=='BACKPRESSURE'

def test_interactive_reserve_cannot_be_spent_by_background():
    r=RhythmPlane(config=RhythmConfig(),budget=budget()); heavy=WorkCost(token_cost=85,compute_ms=1,io_bytes=1,latency_ms=1); assert r.submit(WorkItem(event(1,key='bg'),heavy)).reason_code=='BUDGET_RESERVE'; assert r.submit(WorkItem(event(2,q='INTERACTIVE',key='i'),heavy)).disposition=='ADMITTED'

def test_semantic_and_revalidation_admission_hooks_are_conservative():
    r=RhythmPlane(config=RhythmConfig(semantic_min_priority=60,revalidation_min_priority=40),budget=budget()); assert r.submit(WorkItem(event(1,q='SEMANTIC',priority=50),semantic=True)).reason_code=='SEMANTIC_ADMISSION_FLOOR'; assert r.submit(WorkItem(event(2,q='REVALIDATION',priority=30,key='r'),revalidation=True)).reason_code=='REVALIDATION_ADMISSION_FLOOR'

def test_telemetry_collects_required_lambda_mu_age_latency_token_io():
    r=RhythmPlane(config=RhythmConfig(debounce_ms=0),budget=budget(),start_ms=0); item=WorkItem(event(1,q='FAST',key='x',at=100),WorkCost(token_cost=3,compute_ms=2,io_bytes=7,latency_ms=4)); assert r.submit(item).disposition=='ADMITTED'; pre=r.telemetry('FAST',now_ms=200); assert pre.arrival_count==1 and pre.oldest_queue_age_ms==100 and pre.service_rate_milli_per_sec==0; r.service_one('FAST',now_ms=250,transform_latency_ms=11); post=r.telemetry('FAST',now_ms=1000); assert post.service_count==1 and post.transform_latency_total_ms==11 and post.token_cost_total==3 and post.io_cost_total==7 and post.rho_milli is not None

def test_adaptive_debounce_matches_frozen_formula_shape():
    assert adaptive_debounce_ms(arrival_rate_milli_per_sec=1000,service_rate_milli_per_sec=1000,semantic_ratio_milli=500,rho_target_milli=800)==0; assert adaptive_debounce_ms(arrival_rate_milli_per_sec=10_000,service_rate_milli_per_sec=1000,semantic_ratio_milli=1000,rho_target_milli=800)>0

def test_invalidation_marks_dirty_only_and_does_not_change_truth_or_staleness():
    result=propagate_invalidation(('a',),{'a':('b',),'b':('c',)}); assert result.dirty_record_hashes==('a','b','c') and not result.truth_mutated and not result.epistemic_state_mutated

def test_scoped_transaction_rejects_incompatible_cut_and_supports_rollback():
    sc=scope(); a=cut(sc,watermark(value='a',seq=1)); b=cut(sc,watermark(value='b',seq=1)); tx=ScopedTransaction(sc); tx.stage(TransactionItem('a',sc,a))
    with pytest.raises(IncompatibleWorldCut): tx.stage(TransactionItem('b',sc,b))
    tx.rollback()
    with pytest.raises(RuntimeError): tx.commit()

def test_p4_closure_now_uses_gamma_and_blocks_stale_parent_promotion():
    class Unary:
        spec=RuleSpec('wu.rule.p5.gamma','v1','EXECUTION_ACTION',('EXECUTION_ACTION',))
        def apply(self,known_set,delta): return tuple(DerivedCandidate((r,),'GAMMA_CHILD',r.subject_ref,'gamma.child',WorldValue(kind='string',string_value='x')) for r in delta)
    result=KnownClosureEngine(RuleRegistry((Unary(),))).close((known('stale',epistemic='STALE'),)); assert not result.known.by_proposition('GAMMA_CHILD') and any(d.reason_code=='EPISTEMIC_STALE' for d in result.diagnostics)

def test_queue_priority_services_higher_priority_first():
    r=RhythmPlane(config=RhythmConfig(debounce_ms=0),budget=budget()); low=WorkItem(event(10,q='FAST',key='low',priority=10),WorkCost(token_cost=1)); high=WorkItem(event(11,q='FAST',key='high',priority=90,at=1),WorkCost(token_cost=1)); assert r.submit(low).disposition=='ADMITTED' and r.submit(high).disposition=='ADMITTED'; assert r.service_one('FAST',now_ms=10).event.event_id=='e11'

def test_queued_work_cannot_overcommit_budget_before_service():
    r=RhythmPlane(config=RhythmConfig(debounce_ms=0),budget=budget()); assert r.submit(WorkItem(event(20,key='a'),WorkCost(token_cost=60))).disposition=='ADMITTED'; second=r.submit(WorkItem(event(21,key='b',at=1),WorkCost(token_cost=30))); assert second.disposition=='BACKPRESSURE' and second.reason_code=='BUDGET_RESERVE'
