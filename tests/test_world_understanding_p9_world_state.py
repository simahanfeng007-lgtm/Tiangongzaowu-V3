from __future__ import annotations
from dataclasses import asdict
import pytest
from contracts.canonical import canonical_sha256
from contracts.cognition_statement import CognitionStatement, CognitionValue, derive_cognition_id
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
    scope=scope or sc(); cid=derive_cognition_id(life_id=scope.life_id,domain='software',world_scope_hash=scope.world_scope_hash,principal_scope_hash=scope.principal_scope_hash,claim_kind='boundary',subject_ref='subject.1',predicate='is_boundary',condition_sha256=None)
    s=CognitionStatement(cognition_id=cid,life_id=scope.life_id,domain='software',world_scope_hash=scope.world_scope_hash,principal_scope_hash=scope.principal_scope_hash,privacy_scope=scope.privacy_scope,claim_kind='boundary',subject_ref='subject.1',predicate='is_boundary',value=CognitionValue(kind='boolean',boolean_value=True),proposal_origin='deterministic_extraction',status='CORE',stability_level='C3',confidence_milli=900,supporting_evidence_ids=('cev_'+'1'*64,'cev_'+'2'*64,'cev_'+'3'*64),valid_from_ms=1,last_verified_at_ms=1,revision=1,statement_sha256='0'*64).with_computed_statement_sha256()
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


def test_genesis_state_is_coherent_and_non_authorizing():
    c=cut(); f,g=graph_for(c); s=materialize(WorldStateStore(),c,g,f)
    assert s.state.has_valid_hash() and s.state.world_sequence==0
    assert s.state.world_cut_ref.record_id==c.cut_id
    assert s.state.empirical_evidence_weight_milli==0 and not s.state.may_authorize and not s.state.may_execute
    assert len(s.entity_heads.refs)==2 and s.relation_heads.refs==()

def test_incompatible_cut_rejected():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); materialize(store,c1,g,f)
    bad=cut(git='DIFFERENT',gseq=1,t=2); f2=advance(g,bad)
    with pytest.raises(ValueError,match='WORLD_CUT_INCOMPATIBLE'): materialize(store,bad,g,f2)

def test_regression_cut_rejected():
    store=WorldStateStore(); c2=cut(git='c2',gseq=2,t=2); f,g=graph_for(c2); materialize(store,c2,g,f)
    old=cut(git='c1',gseq=1,t=3); f2=advance(g,old)
    with pytest.raises(ValueError,match='WORLD_STATE_CURRENT_REGRESSION'): materialize(store,old,g,f2)

def test_disjoint_cut_continuity_rejected():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); materialize(store,c1,g,f)
    rows=(wm('MEMORY','memory.rev','m2',2),); rows=tuple(sorted(rows,key=lambda x:x.sort_key())); c2=WorldCut(cut_id=derive_world_cut_id(world_scope_hash=sc().world_scope_hash,watermarks=rows),scope=sc(),source_watermarks=rows,time=wt(2),cut_sha256='0'*64).with_computed_hash()
    f2=SoftwareWorldFrame.build(scope=c2.scope,workspace='ws.main',repository='repo.main',worktree='wt.main',branch='main',commit='memory-only',environment='env.sha',time=c2.time,world_cut=c2); g.advance_frame(f2)
    with pytest.raises(ValueError,match='WORLD_CUT_CONTINUITY_UNKNOWN'): materialize(store,c2,g,f2)

def test_snapshot_entity_limit_enforced():
    c=cut(); f,g=graph_for(c,count=3)
    with pytest.raises(ValueError,match='WORLD_STATE_SNAPSHOT_LIMIT'): materialize(WorldStateStore(),c,g,f,config=WorldStateMaterializerConfig(max_entities=2))

def test_snapshot_manifests_do_not_copy_lower_layer_raw_data():
    c=cut(); f,g=graph_for(c); snap=materialize(WorldStateStore(),c,g,f)
    d=asdict(snap.entity_heads)
    text=str(d)
    assert 'F0' not in text and 'F1' not in text and 'payload_inline' not in text
    assert 'record_id' in text and 'sha256' in text

def test_precise_invalidation_only_hits_bound_ref():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); ents=g.entities(); deps=(DependencyBinding(eref(ents[0]),('GIT_CODE:git.commit',)),DependencyBinding(eref(ents[1]),('RUNTIME_ENVIRONMENT:runtime.seq',)))
    materialize(store,c1,g,f,dependency_bindings=deps)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); snap=materialize(store,c2,g,f2,dependency_bindings=deps)
    assert tuple(r.record_id for r in snap.state.stale_refs)==(ents[0].entity_id,)

def test_refreshed_head_is_not_marked_stale_after_source_change():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); old=g.entities()[0]; deps=(DependencyBinding(eref(old),('GIT_CODE:git.commit',)),); materialize(store,c1,g,f,dependency_bindings=deps)
    newer=old.model_copy(update={'revision':old.revision+1,'entity_sha256':'0'*64}).with_computed_hash(); g.upsert_entity(newer)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); deps2=(DependencyBinding(eref(newer),('GIT_CODE:git.commit',)),); snap=materialize(store,c2,g,f2,dependency_bindings=deps2)
    assert not snap.state.stale_refs and snap.delta.changed_refs[0].record_id==old.entity_id

def test_cognition_root_loss_reevaluates_and_can_remain_current():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); view=cog(); cref=view.statement_ref.record_ref; deps=(DependencyBinding(cref,('GIT_CODE:git.commit',),('cev_'+'1'*64,)),); ev=FakeSupport(True)
    materialize(store,c1,g,f,stable_cognition=(view,),dependency_bindings=deps,support_evaluator=ev)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); snap=materialize(store,c2,g,f2,stable_cognition=(view,),dependency_bindings=deps,support_evaluator=ev)
    assert ev.calls==1 and snap.cognition_heads and cref in snap.cognition_heads.refs and cref in snap.delta.revalidated_cognition_refs and cref not in snap.state.stale_refs

def test_cognition_root_loss_insufficient_support_drops_only_that_head():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); view=cog(); cref=view.statement_ref.record_ref; deps=(DependencyBinding(cref,('GIT_CODE:git.commit',),('cev_'+'1'*64,)),); ev=FakeSupport(False)
    materialize(store,c1,g,f,stable_cognition=(view,),dependency_bindings=deps,support_evaluator=ev)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); snap=materialize(store,c2,g,f2,stable_cognition=(view,),dependency_bindings=deps,support_evaluator=ev)
    assert snap.cognition_heads is None and cref in snap.state.stale_refs and len(snap.state.stale_refs)==1

def test_cognition_touched_without_evaluator_fails_closed_to_stale():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); view=cog(); cref=view.statement_ref.record_ref; deps=(DependencyBinding(cref,('GIT_CODE:git.commit',),('cev_'+'1'*64,)),)
    materialize(store,c1,g,f,stable_cognition=(view,),dependency_bindings=deps)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); snap=materialize(store,c2,g,f2,stable_cognition=(view,),dependency_bindings=deps)
    assert snap.cognition_heads is None and cref in snap.state.stale_refs

def test_unrelated_source_change_does_not_reevaluate_cognition():
    store=WorldStateStore(); c1=cut(); f,g=graph_for(c1); view=cog(); cref=view.statement_ref.record_ref; deps=(DependencyBinding(cref,('RUNTIME_ENVIRONMENT:runtime.seq',),('cev_'+'1'*64,)),); ev=FakeSupport(False)
    materialize(store,c1,g,f,stable_cognition=(view,),dependency_bindings=deps,support_evaluator=ev)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); snap=materialize(store,c2,g,f2,stable_cognition=(view,),dependency_bindings=deps,support_evaluator=ev)
    assert ev.calls==0 and snap.cognition_heads and cref in snap.cognition_heads.refs

def test_conflict_and_uncertainty_retained_as_refs_only():
    c=cut(); f,g=graph_for(c); u=WorldRecordRef(record_type='world_uncertainty',record_id='unc.1',revision=None,sha256='b'*64); x=WorldRecordRef(record_type='world_conflict',record_id='conf.1',revision=None,sha256='c'*64)
    snap=materialize(WorldStateStore(),c,g,f,uncertainty_refs=(u,),conflict_refs=(x,))
    assert snap.uncertainty and snap.uncertainty.refs==(u,) and snap.delta.uncertainty_manifest_ref==snap.uncertainty.ref
    assert snap.state.unresolved_conflict_refs==(x,)

def test_cross_scope_cut_rejected():
    c=cut(); f,g=graph_for(c); other=cut(scope=sc('life.B'))
    with pytest.raises(Exception): materialize(WorldStateStore(),other,g,f)

def test_frame_cut_mismatch_rejected():
    c1=cut(); f,g=graph_for(c1); c2=cut(git='c2',gseq=2,t=2)
    g.frame_revision_hash=f.frame_revision_hash
    with pytest.raises(ValueError,match='WORLD_FRAME_CUT_MISMATCH'): WorldStateMaterializer(WorldStateStore()).materialize(MaterializationInput(frame=f,cut=c2,graph=g,materialized_at_ms=2))

def test_graph_frame_revision_must_match_materialization_frame():
    c1=cut(); f,g=graph_for(c1); c2=cut(git='c2',gseq=2,t=2); f2=frame(c2)
    with pytest.raises(ValueError,match='WORLD_STATE_GRAPH_FRAME_REVISION_MISMATCH'): materialize(WorldStateStore(),c2,g,f2)

def test_removed_watermark_is_a_changed_source_key():
    store=WorldStateStore(); c1=cut(extra=(wm('MEMORY','memory.rev','m1',1),)); f,g=graph_for(c1); a=materialize(store,c1,g,f)
    c2=cut(git='c2',gseq=2,t=2); f2=advance(g,c2); b=materialize(store,c2,g,f2)
    assert 'MEMORY:memory.rev' in b.delta.changed_source_keys and b.delta.previous_state_ref.record_id==a.state.world_state_id

def test_dependency_manifest_contains_only_refs_and_source_keys():
    c=cut(); f,g=graph_for(c); r=eref(g.entities()[0]); snap=materialize(WorldStateStore(),c,g,f,dependency_bindings=(DependencyBinding(r,('GIT_CODE:git.commit',)),))
    text=str(asdict(snap.dependencies))
    assert 'GIT_CODE:git.commit' in text and r.record_id in text and 'F0' not in text and 'payload' not in text

def test_p9_package_has_no_runtime_gateway_tool_or_llm_imports():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'src'/'world_understanding'/'world_state'
    text='\n'.join(p.read_text(encoding='utf-8') for p in root.glob('*.py'))
    for forbidden in ('zongdiaodu','gateway_links','omni_body','HttpKehuduan','SemanticPipeline','llm_diaoyong'):
        assert forbidden not in text
