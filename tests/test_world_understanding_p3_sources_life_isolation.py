from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import pytest
from contracts.canonical import canonical_sha256
from contracts.world_understanding import ScopeBinding,WorldScope,WorldTime,derive_world_id,derive_world_scope_hash,WorldIngressEnvelope,derive_ingress_dedup_key,derive_ingress_envelope_id,WorldContextPacket,WorldInquiry,WorldCuriosity,InquiryOutcome,PredictionOutcome,DerivationRef,DerivationEdge
from world_understanding import WorldUnderstandingFacade
from world_understanding.ingress.compiler_registry import CompilerRegistry
from world_understanding.scope_guard import ScopeMismatchError,require_same_scope_parents
from world_understanding.source_compilers import SPECS,build_p3_compilers
from world_understanding.source_compilers.base import make_direct_known
from world_understanding.source_adapters import build_post_commit_source_envelope

P='a'*64

def scope(life:str)->WorldScope:
    b=(ScopeBinding(key='repository',value='repo.main'),)
    wid=derive_world_id(life_id=life,namespace_anchor='primary')
    return WorldScope(life_id=life,world_id=wid,domain_id='software',scope_bindings=b,world_scope_hash=derive_world_scope_hash(life_id=life,world_id=wid,domain_id='software',scope_bindings=b),principal_scope_hash=P,privacy_scope='system')
def wt()->WorldTime: return WorldTime(valid_from_ms=1,observed_at_ms=1,recorded_at_ms=2)
def env(kind:str='FACT_EXECUTION',life:str='life.A',native:str='n1',payload=None,principal=P)->WorldIngressEnvelope:
    sc=scope(life)
    if payload is None and kind=='LIFE_LEARNING':
        from contracts.world_understanding.life_learning import LifeLearningObservation
        payload=LifeLearningObservation(
            life_id=life,artifact_id='art.test',artifact_kind='knowledge',lineage_id='lineage.test',
            status='published',confidence_milli=1000,epistemic_status='verified',
            prior_revision=0,new_revision=1,occurred_at_ms=1,observation_sha256='0'*64,
        ).with_computed_hash().model_dump(mode='json')
    payload=payload or {'text':'hello'}; sha=canonical_sha256(payload); dd=derive_ingress_dedup_key(envelope_kind='SOURCE_RECORD',source_kind=kind,source_native_id=native,payload_sha256=sha,world_scope_hash=sc.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=dd),envelope_kind='SOURCE_RECORD',source_kind=kind,source_native_id=native,producer_ref='p3.test',payload_inline=payload,payload_sha256=sha,source_time=wt(),life_id=life,principal_scope_hash=principal,scope_hint=sc,correlation_id='corr.1',dedup_key=dd)

@pytest.mark.parametrize('kind', sorted(SPECS))
def test_all_p3_compilers_are_deterministic_and_life_scoped(kind):
    e=env(kind,native=f'n.{kind.lower()}')
    compiler=build_p3_compilers()[kind]
    first=compiler(e); second=compiler(e)
    assert tuple(x.record_hash for x in first)==tuple(x.record_hash for x in second)
    assert all(x.world_scope.life_id=='life.A' for x in first)
    assert all(x.world_scope.world_scope_hash==e.scope_hint.world_scope_hash for x in first)

# LIFE-01/02

def test_shared_facade_has_no_life_identity_fields():
    facade=WorldUnderstandingFacade(enabled=True)
    assert not any(name in {'life_id','current_life','current_world','last_life_state'} for name in getattr(facade,'__slots__',()))

def test_registry_has_no_life_private_state():
    reg=CompilerRegistry(build_p3_compilers())
    assert set(reg.__slots__)=={'_lock','_compilers'}

# LIFE-03/04/05

def test_ingress_requires_life_id():
    data=env().model_dump(mode='python'); data.pop('life_id')
    with pytest.raises(Exception): WorldIngressEnvelope.model_validate(data)

def test_envelope_life_mismatch_rejected():
    e=env(); bad=e.model_copy(update={'life_id':'life.B'})
    r=WorldUnderstandingFacade(enabled=True).accept(bad)
    assert r.disposition=='REJECTED' and r.reason_code=='SCOPE_MISMATCH'

def test_principal_scope_mismatch_rejected():
    e=env(); bad=e.model_copy(update={'principal_scope_hash':'b'*64})
    r=WorldUnderstandingFacade(enabled=True).accept(bad)
    assert r.disposition=='REJECTED' and r.reason_code=='PRINCIPAL_SCOPE_MISMATCH'

# LIFE-06

def test_compiler_cannot_replace_scope():
    a=env(); b=env(life='life.B')
    malicious=lambda _: make_direct_known(b,SPECS['FACT_EXECUTION'])
    r=WorldUnderstandingFacade(enabled=True,compiler_registry=CompilerRegistry({'FACT_EXECUTION':malicious})).accept(a)
    assert r.disposition=='REJECTED' and r.reason_code=='SCOPE_MISMATCH'

# LIFE-08

def test_same_payload_native_different_life_have_different_scope_and_known_id():
    a=env(life='life.A'); b=env(life='life.B')
    ca=build_p3_compilers()['FACT_EXECUTION'](a)[0]; cb=build_p3_compilers()['FACT_EXECUTION'](b)[0]
    assert a.scope_hint.world_scope_hash!=b.scope_hint.world_scope_hash
    assert ca.known_id!=cb.known_id

# source semantics

def test_user_claim_is_user_said_not_reality_file_fact():
    row=build_p3_compilers()['USER_CONVERSATION'](env('USER_CONVERSATION',payload={'text':'A.txt不存在'}))[0]
    assert row.proposition_type=='USER_SAID'
    assert row.proposition_type!='FILE_EXISTS'

def test_web_claim_has_zero_reality_weight():
    row=build_p3_compilers()['WEB_EXTERNAL'](env('WEB_EXTERNAL',payload={'claim':'X'}))[0]
    assert row.proposition_type=='WEB_SOURCE_CLAIMS' and row.empirical_evidence_weight_milli==0

def test_model_output_has_zero_reality_weight():
    row=build_p3_compilers()['MODEL_OUTPUT'](env('MODEL_OUTPUT',payload={'text':'X'}))[0]
    assert row.proposition_type=='MODEL_PROPOSED' and row.empirical_evidence_weight_milli==0

def test_memory_does_not_upgrade_truth_authority():
    row=build_p3_compilers()['MEMORY'](env('MEMORY',payload={'text':'remembered X'}))[0]
    assert row.proposition_type=='MEMORY_RECORDED' and row.authority_ceiling_milli==0

def test_autonomy_decision_has_zero_reality_weight():
    row=build_p3_compilers()['AUTONOMY'](env('AUTONOMY',payload={'decision':'ACCEPT'}))[0]
    assert row.empirical_evidence_weight_milli==0

def test_chain_completed_does_not_become_goal_completed():
    row=build_p3_compilers()['CHAIN_EVENT'](env('CHAIN_EVENT',payload={'event_kind':'chain_completed'}))[0]
    assert row.proposition_type=='CHAIN_EVENT_RECORDED'
    assert 'GOAL_COMPLETED' not in row.proposition_type

def test_authorization_is_not_execution_result():
    row=build_p3_compilers()['AUTHORIZATION'](env('AUTHORIZATION',payload={'decision':'AUTHORIZED'}))[0]
    assert row.proposition_type=='AUTHORIZATION_DECISION_RECORDED'
    assert row.proposition_type!='FACT_EXECUTION_RECORDED'

def test_tool_observed_write_and_declared_write_are_separate():
    comp=build_p3_compilers()['TOOL_RESULT']
    observed=comp(env('TOOL_RESULT',payload={'write_effect':True,'observed_write_effect':True,'write_evidence':{'authoritative':True,'changed_files':['a.txt'],'deleted_files':[]}}))
    declared=comp(env('TOOL_RESULT',native='n2',payload={'write_effect':True,'observed_write_effect':False,'summary':'planned'}))
    assert any(x.proposition_type=='FILE_WRITE_OBSERVED' and x.empirical_evidence_weight_milli==1000 for x in observed)
    assert any(x.proposition_type=='TOOL_WRITE_DECLARED' and x.empirical_evidence_weight_milli==0 for x in declared)

def test_filesystem_exists_false_is_only_from_observed_filesystem_source():
    rows=build_p3_compilers()['FILESYSTEM'](env('FILESYSTEM',payload={'path':'a.txt','exists':False}))
    assert any(x.proposition_type=='FILE_EXISTS' and x.object_value.string_value=='false' for x in rows)

def test_filesystem_hash_requires_observed_sha_field():
    sha='c'*64
    rows=build_p3_compilers()['FILESYSTEM'](env('FILESYSTEM',payload={'path':'a.txt','exists':True,'sha256':sha}))
    assert any(x.proposition_type=='FILE_HASH_AT' and x.object_value.string_value==sha for x in rows)

# adapter scope inheritance

def test_post_commit_adapter_inherits_life_and_principal_from_scope():
    sc=scope('life.A')
    e=build_post_commit_source_envelope(source_kind='FACT_EXECUTION',source_native_id='fact.1',producer_ref='fact.kernel',payload={'status':'completed'},source_time=wt(),scope=sc,correlation_id='corr.a')
    assert e.life_id==sc.life_id and e.principal_scope_hash==sc.principal_scope_hash and e.scope_hint is sc

# P4 precondition only

def test_p4_guard_rejects_cross_life_parent_set_without_derivation():
    a=build_p3_compilers()['FACT_EXECUTION'](env(life='life.A',native='a'))[0]
    b=build_p3_compilers()['FACT_EXECUTION'](env(life='life.B',native='b'))[0]
    with pytest.raises(ScopeMismatchError): require_same_scope_parents(scope('life.A'),(a,b))

# LIFE-07/08 concurrency

def test_shared_registry_2000_events_no_cross_life_contamination():
    lock=Lock(); seen=[]
    base=build_p3_compilers()['FACT_EXECUTION']
    def recording(e):
        rows=base(e)
        with lock: seen.append((e.life_id,tuple(r.world_scope.life_id for r in rows)))
        return rows
    facade=WorldUnderstandingFacade(enabled=True,compiler_registry=CompilerRegistry({'FACT_EXECUTION':recording}))
    items=[env(life='life.A',native=f'a{i}') for i in range(1000)]+[env(life='life.B',native=f'b{i}') for i in range(1000)]
    with ThreadPoolExecutor(max_workers=32) as ex: receipts=list(ex.map(facade.accept,items))
    assert all(r.disposition=='ACCEPTED' for r in receipts)
    assert len(seen)==2000
    assert all(all(out_life==in_life for out_life in out_lives) for in_life,out_lives in seen)


def test_context_packet_and_inquiry_keep_scope_as_single_life_source():
    assert "scope" in WorldContextPacket.model_fields and "life_id" not in WorldContextPacket.model_fields
    assert "scope" in WorldInquiry.model_fields and "life_id" not in WorldInquiry.model_fields

def test_curiosity_outcome_and_derivation_are_world_scope_bound():
    assert "scope" in WorldCuriosity.model_fields and "life_id" not in WorldCuriosity.model_fields
    assert "scope" in InquiryOutcome.model_fields
    assert "scope" in PredictionOutcome.model_fields
    assert "scope" in DerivationRef.model_fields
    assert "scope" in DerivationEdge.model_fields
