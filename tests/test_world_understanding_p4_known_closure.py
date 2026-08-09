from __future__ import annotations
import time
import pytest
from contracts.canonical import canonical_sha256
from contracts.world_understanding.scope import ScopeBinding,WorldScope,derive_world_id,derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.ingress import WorldIngressEnvelope,derive_ingress_dedup_key,derive_ingress_envelope_id
from contracts.world_understanding._base import WorldValue
from world_understanding.source_compilers import SPECS,build_p3_compilers
from world_understanding.source_compilers.base import make_direct_known
from world_understanding.known import KnownClosureEngine,RuleRegistry,KnownSet,build_p4_rules,ActiveCutOverflow
from world_understanding.known.rule import RuleSpec,DerivedCandidate
from world_understanding.known.rules import FileStateTransitionRule
from world_understanding.known.rules import CallReachabilityRule
from world_understanding.scope_guard import ScopeMismatchError

P='a'*64

def scope(life='life.A'):
    bindings=(ScopeBinding(key='repository',value='repo.main'),)
    wid=derive_world_id(life_id=life,namespace_anchor='primary')
    return WorldScope(life_id=life,world_id=wid,domain_id='software',scope_bindings=bindings,world_scope_hash=derive_world_scope_hash(life_id=life,world_id=wid,domain_id='software',scope_bindings=bindings),principal_scope_hash=P,privacy_scope='system')

def env(kind='FACT_EXECUTION',*,life='life.A',native='n1',payload=None,t=1):
    sc=scope(life); payload=payload or {'text':native}; sha=canonical_sha256(payload)
    dd=derive_ingress_dedup_key(envelope_kind='SOURCE_RECORD',source_kind=kind,source_native_id=native,payload_sha256=sha,world_scope_hash=sc.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=dd),envelope_kind='SOURCE_RECORD',source_kind=kind,source_native_id=native,producer_ref='p4.test',payload_inline=payload,payload_sha256=sha,source_time=WorldTime(valid_from_ms=t,observed_at_ms=t,recorded_at_ms=t),life_id=life,principal_scope_hash=P,scope_hint=sc,correlation_id=f'c.{native}',dedup_key=dd)

def fact(native='n1',*,life='life.A',ptype='FACT_EXECUTION_RECORDED',subject=None,obj=None,ceiling=1000,weight=1000,t=1,domain=None):
    e=env('FACT_EXECUTION',life=life,native=native,payload={'text':obj or native},t=t)
    return make_direct_known(e,SPECS['FACT_EXECUTION'],proposition_type=ptype,predicate='p4.test',subject_ref=subject or native,object_text=obj or native,authority_ceiling_milli=ceiling,empirical_evidence_weight_milli=weight,authority_domain=domain)

def fs(native,path,exists=None,sha=None,t=1):
    payload={'path':path}
    if exists is not None: payload['exists']=exists
    if sha is not None: payload['sha256']=sha
    rows=build_p3_compilers()['FILESYSTEM'](env('FILESYSTEM',native=native,payload=payload,t=t))
    return rows

def default_engine(**kw): return KnownClosureEngine(RuleRegistry(build_p4_rules()),**kw)

def test_file_create_and_hash_change_reach_fixed_point():
    before=next(r for r in fs('fs1','a.txt',exists=False,t=1) if r.proposition_type=='FILE_EXISTS')
    after=next(r for r in fs('fs2','a.txt',exists=True,t=2) if r.proposition_type=='FILE_EXISTS')
    h1=next(r for r in fs('fh1','a.txt',sha='1'*64,t=3) if r.proposition_type=='FILE_HASH_AT')
    h2=next(r for r in fs('fh2','a.txt',sha='2'*64,t=4) if r.proposition_type=='FILE_HASH_AT')
    result=default_engine().close((before,after,h1,h2))
    props={r.proposition_type for r in result.known.records()}
    assert result.terminated and 'FILE_CREATED' in props and 'FILE_CONTENT_CHANGED' in props
    assert result.rounds < 10

def test_same_input_order_produces_stable_known_and_derivation_hashes():
    a=next(r for r in fs('fs1','a.txt',exists=False,t=1) if r.proposition_type=='FILE_EXISTS')
    b=next(r for r in fs('fs2','a.txt',exists=True,t=2) if r.proposition_type=='FILE_EXISTS')
    r1=default_engine().close((a,b)); r2=default_engine().close((b,a))
    assert {x.record_hash for x in r1.known.records()}=={x.record_hash for x in r2.known.records()}
    assert {x.derivation_sha256 for x in r1.derivations}=={x.derivation_sha256 for x in r2.derivations}

def test_cross_life_input_fails_before_any_derivation():
    a=fact('a',life='life.A'); b=fact('b',life='life.B')
    with pytest.raises(ScopeMismatchError): default_engine().close((a,b))

def test_authority_and_empirical_weight_cannot_increase_and_provenance_union_preserved():
    a=next(r for r in fs('fs1','a.txt',exists=False,t=1) if r.proposition_type=='FILE_EXISTS')
    b=next(r for r in fs('fs2','a.txt',exists=True,t=2) if r.proposition_type=='FILE_EXISTS')
    a=a.model_copy(update={'authority_ceiling_milli':700,'empirical_evidence_weight_milli':650}).with_computed_hash()
    b=b.model_copy(update={'authority_ceiling_milli':400,'empirical_evidence_weight_milli':300}).with_computed_hash()
    result=KnownClosureEngine(RuleRegistry((FileStateTransitionRule(),))).close((a,b))
    child=next(r for r in result.known.records() if r.proposition_type=='FILE_CREATED')
    assert child.authority_ceiling_milli==400
    assert child.empirical_evidence_weight_milli==300
    roots={p.sha256 for p in a.provenance_refs}|{p.sha256 for p in b.provenance_refs}
    assert roots <= {p.sha256 for p in child.provenance_refs}
    assert next(d for d in result.derivations if d.target_refs[0].sha256==child.record_hash).lineage_root_hashes==tuple(sorted(roots))

def test_rule_error_aborts_transform_not_direct_known():
    class Boom:
        spec=RuleSpec('wu.rule.test.boom','v1')
        def apply(self,known,delta): raise RuntimeError('boom')
    direct=fact('x')
    result=KnownClosureEngine(RuleRegistry((Boom(),))).close((direct,))
    assert len(result.known.records())==1
    assert any(d.reason_code=='RULE_ERROR' for d in result.diagnostics)

def test_semantic_same_revision_cycle_is_rejected():
    class Toggle:
        spec=RuleSpec('wu.rule.test.toggle','v1','EXECUTION_ACTION',('EXECUTION_ACTION',))
        def apply(self,known,delta):
            out=[]
            for r in delta:
                if r.proposition_type=='P4_A': out.append(DerivedCandidate((r,),'P4_B',r.subject_ref,'p4.toggle',WorldValue(kind='string',string_value='x')))
                elif r.proposition_type=='P4_B': out.append(DerivedCandidate((r,),'P4_A',r.subject_ref,'p4.toggle',WorldValue(kind='string',string_value='x')))
            return tuple(out)
    start=fact('cyc',ptype='P4_A',subject='node',obj='x')
    result=KnownClosureEngine(RuleRegistry((Toggle(),)),max_rounds=8).close((start,))
    assert result.terminated
    assert {r.proposition_type for r in result.known.records()}=={'P4_A','P4_B'}
    assert any(d.reason_code=='SAME_REVISION_CYCLE' for d in result.diagnostics)

def test_direct_calls_never_transitively_become_direct_calls_but_reachability_is_derived():
    def edge(native,a,b):
        e=env('GIT_CODE',native=native,payload={'text':b},t=1)
        return make_direct_known(e,SPECS['GIT_CODE'],proposition_type='DIRECT_CALLS',predicate='code.direct_calls',subject_ref=a,object_text=b,authority_domain='GIT_CODE')
    ab=edge('ab','A','B'); bc=edge('bc','B','C')
    result=KnownClosureEngine(RuleRegistry((CallReachabilityRule(),))).close((ab,bc))
    direct={(r.subject_ref,r.object_value.string_value) for r in result.known.by_proposition('DIRECT_CALLS')}
    assert ('A','C') not in direct
    reachable=[r for r in result.known.by_proposition('CALL_REACHABLE') if r.subject_ref=='A' and r.object_value.string_value.startswith('C|path=')]
    assert reachable and reachable[0].object_value.string_value=='C|path=A>B>C'

def test_rule_version_changes_derived_hash_and_derivation_identity_not_stable_slot():
    class Unary:
        def __init__(self,v): self.spec=RuleSpec('wu.rule.test.versioned',v,'EXECUTION_ACTION',('EXECUTION_ACTION',))
        def apply(self,known,delta):
            return tuple(DerivedCandidate((r,),'P4_V',r.subject_ref,'p4.version',WorldValue(kind='string',string_value='out')) for r in delta if r.derivation_type=='DIRECT')
    direct=fact('v')
    a=KnownClosureEngine(RuleRegistry((Unary('v1'),))).close((direct,))
    b=KnownClosureEngine(RuleRegistry((Unary('v2'),))).close((direct,))
    da=next(r for r in a.known.records() if r.proposition_type=='P4_V'); db=next(r for r in b.known.records() if r.proposition_type=='P4_V')
    assert da.known_id==db.known_id
    assert da.record_hash!=db.record_hash
    assert a.derivations[0].derivation_id!=b.derivations[0].derivation_id

def test_finite_active_cut_is_enforced():
    records=tuple(fact(f'n{i}',obj=f'x{i}') for i in range(3))
    with pytest.raises(ActiveCutOverflow): KnownSet(scope(),records,max_records=2)

def test_incremental_closure_only_adds_new_delta_to_prior_cut():
    a=fact('a',obj='a'); b=fact('b',obj='b')
    engine=KnownClosureEngine(RuleRegistry(()))
    first=engine.close((a,)); second=engine.close((b,),prior=first)
    assert len(first.known)==1 and len(second.known)==2
    assert second.added_record_hashes==(b.record_hash,)

def test_10k_known_incremental_recompute_benchmark():
    records=tuple(fact(f'n{i}',obj=f'x{i}') for i in range(10_000))
    engine=KnownClosureEngine(RuleRegistry(()),max_records=20_000)
    t0=time.perf_counter(); baseline=engine.close(records); baseline_s=time.perf_counter()-t0
    extra=fact('extra',obj='extra')
    t1=time.perf_counter(); updated=engine.close((extra,),prior=baseline); incremental_s=time.perf_counter()-t1
    assert len(baseline.known)==10_000 and len(updated.known)==10_001
    assert incremental_s < baseline_s
    print(f'P4_BENCH baseline_10k={baseline_s:.6f}s incremental_1={incremental_s:.6f}s')

def test_hash_equality_rule_emits_deterministic_relation():
    digest='d'*64
    a=next(r for r in fs('ha','a.txt',sha=digest,t=1) if r.proposition_type=='FILE_HASH_AT')
    b=next(r for r in fs('hb','b.txt',sha=digest,t=2) if r.proposition_type=='FILE_HASH_AT')
    result=default_engine().close((a,b))
    assert any(r.proposition_type=='HASH_EQUAL' for r in result.known.records())

def test_event_order_rule_orders_same_subject_events():
    def event(native,name,t):
        e=env('CHAIN_EVENT',native=native,payload={'event_kind':name},t=t)
        return make_direct_known(e,SPECS['CHAIN_EVENT'],proposition_type='CHAIN_EVENT_RECORDED',predicate='chain.event',subject_ref='run.1',object_text=name,authority_domain='TASK_RUN_LIFECYCLE')
    a=event('e1','started',1); b=event('e2','completed',2)
    result=default_engine().close((a,b))
    assert any(r.proposition_type=='EVENT_PRECEDES' for r in result.known.records())

def test_same_source_root_grouping_is_pairwise_and_provenance_preserving():
    a=fact('r1',obj='same-payload'); b=fact('r2',obj='same-payload')
    result=default_engine().close((a,b))
    grouped=[r for r in result.known.records() if r.proposition_type=='SHARES_SOURCE_ROOT']
    assert grouped
    root=a.provenance_refs[0].sha256
    assert root in {ref.sha256 for ref in grouped[0].provenance_refs}

def test_git_structural_normalization_does_not_invent_semantic_role():
    e=env('GIT_CODE',native='git.import',payload={'text':'pkg.b'},t=1)
    row=make_direct_known(e,SPECS['GIT_CODE'],proposition_type='GIT_IMPORTS',predicate='git.imports',subject_ref='pkg.a',object_text='pkg.b',authority_domain='GIT_CODE')
    result=default_engine().close((row,))
    assert any(r.proposition_type=='IMPORTS' and r.subject_ref=='pkg.a' for r in result.known.records())
    assert not any(r.proposition_type in {'AUTHORITATIVE_FOR','GUARDED_BY'} for r in result.known.records())

def test_scope_containment_and_worldframe_identity_require_explicit_input_propositions():
    scope_binding=fact('s1',ptype='SCOPE_BINDING_OBSERVED',subject='scope',obj='workspace:w1')
    life=fact('s2',ptype='RUN_BELONGS_TO_LIFE',subject='run.1',obj='life.A')
    result=default_engine().close((scope_binding,life))
    props={r.proposition_type for r in result.known.records()}
    assert 'SCOPE_CONTAINS' in props and 'WORLD_FRAME_LIFE' in props

def test_each_accepted_derivation_emits_dag_edges_in_same_scope():
    a=next(r for r in fs('fs1','a.txt',exists=False,t=1) if r.proposition_type=='FILE_EXISTS')
    b=next(r for r in fs('fs2','a.txt',exists=True,t=2) if r.proposition_type=='FILE_EXISTS')
    result=default_engine().close((a,b))
    assert result.derivations and result.edges
    assert all(edge.scope.life_id=='life.A' and edge.scope.world_scope_hash==scope().world_scope_hash for edge in result.edges)
    target_hashes={target.sha256 for d in result.derivations for target in d.target_refs}
    assert target_hashes <= {r.record_hash for r in result.known.records()}

def test_authority_domain_widening_is_rejected_by_central_matrix():
    class BadDomain:
        spec=RuleSpec('wu.rule.test.bad-domain','v1','GIT_CODE',('EXECUTION_ACTION',))
        def apply(self,known,delta):
            return tuple(DerivedCandidate((r,),'BAD_DOMAIN_CHILD',r.subject_ref,'bad.domain',WorldValue(kind='string',string_value='x')) for r in delta if r.derivation_type=='DIRECT')
    direct=fact('d1')
    result=KnownClosureEngine(RuleRegistry((BadDomain(),))).close((direct,))
    assert not any(r.proposition_type=='BAD_DOMAIN_CHILD' for r in result.known.records())
    assert any(d.reason_code=='AUTHORITY_DOMAIN_WIDENING_FORBIDDEN' for d in result.diagnostics)
