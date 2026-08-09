from __future__ import annotations
from dataclasses import asdict
import pytest
from contracts.canonical import canonical_sha256
from contracts.cognition_statement import CognitionStatement, CognitionValue
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.world_cut import SourceWatermark, WorldCut, derive_world_cut_id
from world_understanding.cognition.l5 import to_l5_view
from world_understanding.software_world import SoftwareWorldFrame, SoftwareWorldUpdater
from world_understanding.source_compilers import SPECS
from world_understanding.source_compilers.base import make_direct_known
from contracts.world_understanding.ingress import WorldIngressEnvelope, derive_ingress_dedup_key, derive_ingress_envelope_id
from world_understanding.world_state import (
    CognitionSupportDecision, DependencyBinding, MaterializationInput,
    WorldStateMaterializer, WorldStateMaterializerConfig, WorldStateStore,
)

P='a'*64

def sc(life='life.A'):
    b=(ScopeBinding(key='repository',value='repo.main'),)
    wid=derive_world_id(life_id=life,namespace_anchor='primary')
    return WorldScope(life_id=life,world_id=wid,domain_id='software',scope_bindings=b,
        world_scope_hash=derive_world_scope_hash(life_id=life,world_id=wid,domain_id='software',scope_bindings=b),
        principal_scope_hash=P,privacy_scope='system')

def wt(t=1): return WorldTime(valid_from_ms=t,observed_at_ms=t,recorded_at_ms=t)

def wm(kind, typ, value, seq):
    return SourceWatermark(source_kind=kind,watermark_type=typ,watermark_value=value,sequence=seq,watermark_sha256='0'*64).with_computed_hash()

def cut(*,git='c1',gseq=1,runtime='r1',rseq=1,t=1,scope=None,extra=()):
    scope=scope or sc()
    rows=(wm('GIT_CODE','git.commit',git,gseq),wm('RUNTIME_ENVIRONMENT','runtime.seq',runtime,rseq),*extra)
    rows=tuple(sorted(rows,key=lambda x:x.sort_key()))
    cid=derive_world_cut_id(world_scope_hash=scope.world_scope_hash,watermarks=rows)
    return WorldCut(cut_id=cid,scope=scope,source_watermarks=rows,time=wt(t),cut_sha256='0'*64).with_computed_hash()

def env(native,t=1):
    scope=sc(); payload={'text':native}; sha=canonical_sha256(payload)
    d=derive_ingress_dedup_key(envelope_kind='SOURCE_RECORD',source_kind='GIT_CODE',source_native_id=native,payload_sha256=sha,world_scope_hash=scope.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=d),envelope_kind='SOURCE_RECORD',source_kind='GIT_CODE',source_native_id=native,producer_ref='p9.test',payload_inline=payload,payload_sha256=sha,source_time=wt(t),life_id=scope.life_id,principal_scope_hash=P,scope_hint=scope,correlation_id='c.'+native,dedup_key=d)

def known(subject,name,t=1): return make_direct_known(env('id.'+subject+str(t),t),SPECS['GIT_CODE'],proposition_type='FUNCTION_IDENTITY',predicate='p9.test',subject_ref=subject,object_text=name)

def frame(c,branch='main'):
    return SoftwareWorldFrame.build(scope=c.scope,workspace='ws.main',repository='repo.main',worktree='wt.main',branch=branch,commit=next(w.watermark_value for w in c.source_watermarks if w.source_kind=='GIT_CODE'),environment='env.sha',time=c.time,world_cut=c)

def graph_for(c, *, count=2, branch='main'):
    f=frame(c,branch)
    rows=tuple(known(f'fn.{i}',f'F{i}',i+1) for i in range(count))
    return f, SoftwareWorldUpdater().update(frame=f,known_delta=rows).graph

def eref(e): return WorldRecordRef(record_type='world_entity',record_id=e.entity_id,revision=e.revision,sha256=e.entity_sha256)

def cog(scope=None):
    scope=scope or sc(); cid='cog_'+canonical_sha256({'p9':'cog'})
    s=CognitionStatement(cognition_id=cid,life_id=scope.life_id,domain='software',world_scope_hash=scope.world_scope_hash,principal_scope_hash=scope.principal_scope_hash,privacy_scope=scope.privacy_scope,claim_kind='boundary',subject_ref='subject.1',predicate='is_boundary',value=CognitionValue(kind='boolean',boolean_value=True),proposal_origin='deterministic_extraction',status='CORE',stability_level='C3',confidence_milli=900,supporting_evidence_ids=('cev_'+'1'*64,'cev_'+'2'*64),valid_from_ms=1,last_verified_at_ms=1,revision=1,statement_sha256='0'*64).with_computed_statement_sha256()
    return to_l5_view(s,scope=scope)

class FakeSupport:
    def __init__(self, stable=True): self.stable=stable; self.calls=0
    def evaluate(self, view, *, invalidated_evidence_ids, now_ms):
        self.calls+=1
        return CognitionSupportDecision(self.stable,('cev_'+'2'*64,),(), 'ok' if self.stable else 'insufficient')

def materialize(store,c,graph,frame_obj,**kw):
    return WorldStateMaterializer(store,config=kw.pop('config',None),support_evaluator=kw.pop('support_evaluator',None)).materialize(MaterializationInput(frame=frame_obj,cut=c,graph=graph,materialized_at_ms=kw.pop('materialized_at_ms',c.time.recorded_at_ms),source_transaction_id=kw.pop('source_transaction_id','p9.tx'),**kw))

def advance(graph,c,branch='main'):
    f=frame(c,branch); graph.advance_frame(f); return f


def test_state_reconstructable_and_current_history_separate():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); a=materialize(store,c1,g,f)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); b=materialize(store,c2,g,f2)
    assert store.get(a.state.world_state_id)==a and store.get(b.state.world_state_id)==b
    assert store.current(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id)==b
    assert tuple(x.state.world_state_id for x in store.history(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id))==(a.state.world_state_id,b.state.world_state_id)

def test_branch_frames_have_separate_current_streams():
    store=WorldStateStore(); c1=cut(); f1,g1=graph_for(c1,branch='main'); a=materialize(store,c1,g1,f1)
    c2=cut(git='x1',gseq=1,t=2); f2,g2=graph_for(c2,branch='feature'); b=materialize(store,c2,g2,f2)
    assert a.state.world_sequence==0 and b.state.world_sequence==0 and f1.frame_id!=f2.frame_id
    assert len(store.history(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f1.frame_id))==1
    assert len(store.history(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f2.frame_id))==1

def test_bounded_history_evicts_old_non_current_snapshots():
    store=WorldStateStore(max_history_per_frame=2); c1=cut(); f,g=graph_for(c1); a=materialize(store,c1,g,f)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); b=materialize(store,c2,g,f2)
    c3=cut(git='c3',gseq=3,t=3); f3=advance(g,c3); d=materialize(store,c3,g,f3)
    hist=store.history(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id)
    assert tuple(x.state.world_state_id for x in hist)==(b.state.world_state_id,d.state.world_state_id)
    assert store.get(a.state.world_state_id) is None and store.get(d.state.world_state_id)==d

def test_same_cut_can_advance_semantic_state_without_cut_regression():
    store=WorldStateStore(); c=cut(); f,g=graph_for(c); a=materialize(store,c,g,f,source_transaction_id='p9.tx1')
    b=materialize(store,c,g,f,source_transaction_id='p9.tx2',materialized_at_ms=2)
    assert b.state.world_sequence==1 and b.cut.cut_id==a.cut.cut_id and b.delta.previous_state_ref.record_id==a.state.world_state_id

def test_store_rejects_snapshot_manifest_mismatch():
    from dataclasses import replace
    c=cut(); f,g=graph_for(c); snap=materialize(WorldStateStore(),c,g,f)
    bad_manifest=snap.entity_heads.__class__.build('entity_heads',(),max_items=2)
    bad=replace(snap,entity_heads=bad_manifest)
    with pytest.raises(ValueError,match='WORLD_STATE_ENTITY_MANIFEST_MISMATCH'): WorldStateStore().publish(bad)

def test_persistent_store_constructor_is_io_quiet_until_publish(tmp_path):
    root=tmp_path/'world-state'
    store=WorldStateStore(root=root)
    assert not root.exists()
    c=cut(); f,g=graph_for(c); snap=materialize(store,c,g,f)
    assert root.is_dir() and (root/'index.json').is_file()
    assert (root/'snapshots'/f'{snap.state.world_state_id}.json').is_file()

def test_persistent_store_reopens_current_and_history_exactly(tmp_path):
    root=tmp_path/'world-state'; store=WorldStateStore(root=root,max_history_per_frame=4)
    c1=cut(); f,g=graph_for(c1); a=materialize(store,c1,g,f)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); b=materialize(store,c2,g,f2)
    reopened=WorldStateStore(root=root,max_history_per_frame=4)
    current=reopened.current(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id)
    hist=reopened.history(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id)
    assert current==b
    assert tuple(x.state.world_state_id for x in hist)==(a.state.world_state_id,b.state.world_state_id)
    assert reopened.get(a.state.world_state_id)==a

def test_persistent_store_tampered_snapshot_fails_closed(tmp_path):
    import json
    root=tmp_path/'world-state'; store=WorldStateStore(root=root)
    c=cut(); f,g=graph_for(c); snap=materialize(store,c,g,f)
    path=root/'snapshots'/f'{snap.state.world_state_id}.json'
    payload=json.loads(path.read_text(encoding='utf-8'))
    payload['entity_heads']['manifest_sha256']='f'*64
    path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    reopened=WorldStateStore(root=root)
    with pytest.raises(ValueError,match='WORLD_STATE_PERSISTED_MANIFEST_HASH_MISMATCH'):
        reopened.get(snap.state.world_state_id)

def test_memory_only_store_never_creates_world_state_directory(tmp_path):
    root=tmp_path/'must-not-exist'
    store=WorldStateStore(root=None)
    c=cut(); f,g=graph_for(c); materialize(store,c,g,f)
    assert not root.exists()

def test_persistent_index_failure_does_not_advance_live_head(tmp_path, monkeypatch):
    root=tmp_path/'world-state'; store=WorldStateStore(root=root,max_history_per_frame=4)
    c1=cut(); f,g=graph_for(c1); a=materialize(store,c1,g,f)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2)
    original=store._atomic_json; calls={'n':0}
    def fail_index(path,payload):
        calls['n']+=1
        if calls['n']==2: raise OSError('index-write-fault')
        return original(path,payload)
    monkeypatch.setattr(store,'_atomic_json',fail_index)
    with pytest.raises(OSError,match='index-write-fault'):
        materialize(store,c2,g,f2,source_transaction_id='p9.tx.fail')
    current=store.current(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id)
    assert current==a
    assert tuple(x.state.world_state_id for x in store.history(life_id=sc().life_id,world_scope_hash=sc().world_scope_hash,principal_scope_hash=P,frame_id=f.frame_id))==(a.state.world_state_id,)
    assert len(tuple((root/'snapshots').glob('*.json')))==1
