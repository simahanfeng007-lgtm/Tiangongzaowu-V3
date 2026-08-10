from __future__ import annotations
import pytest
from contracts.canonical import canonical_sha256
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.time import WorldTime
from contracts.world_understanding.ingress import WorldIngressEnvelope, derive_ingress_dedup_key, derive_ingress_envelope_id
from contracts.world_understanding.entity import WorldEntity
from contracts.world_understanding.relation import WorldRelation
from world_understanding.source_compilers import SPECS
from world_understanding.source_compilers.base import make_direct_known
from world_understanding.software_world import SoftwareWorldFrame, SparseWorldGraph, SoftwareWorldUpdater, GitPathChange, GitCommitDelta, FrameMismatch
from world_understanding.software_world.perception import ENTITY_IDENTITY_TYPES, FORBIDDEN_SEMANTIC_RELATIONS

P='a'*64

def scope(life='life.A'):
    bindings=(ScopeBinding(key='repository',value='repo.main'),)
    wid=derive_world_id(life_id=life,namespace_anchor='primary')
    return WorldScope(life_id=life,world_id=wid,domain_id='software',scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(life_id=life,world_id=wid,domain_id='software',scope_bindings=bindings),
        principal_scope_hash=P,privacy_scope='system')

def env(*,native='n1',t=1,life='life.A'):
    sc=scope(life); payload={'text':native}; sha=canonical_sha256(payload)
    dd=derive_ingress_dedup_key(envelope_kind='SOURCE_RECORD',source_kind='GIT_CODE',source_native_id=native,payload_sha256=sha,world_scope_hash=sc.world_scope_hash)
    return WorldIngressEnvelope(envelope_id=derive_ingress_envelope_id(dedup_key=dd),envelope_kind='SOURCE_RECORD',source_kind='GIT_CODE',source_native_id=native,producer_ref='p6.test',payload_inline=payload,payload_sha256=sha,
        source_time=WorldTime(valid_from_ms=t,observed_at_ms=t,recorded_at_ms=t),life_id=life,principal_scope_hash=P,scope_hint=sc,correlation_id='c.'+native,dedup_key=dd)

def known(ptype,subject,obj,*,native=None,t=1,life='life.A'):
    e=env(native=native or f'{ptype}.{subject}.{t}',t=t,life=life)
    return make_direct_known(e,SPECS['GIT_CODE'],proposition_type=ptype,predicate='p6.test',subject_ref=subject,object_text=obj)

def identity(ptype,anchor,name,*,t=1): return known(ptype,anchor,name,native=f'id.{anchor}.{t}',t=t)
def rel(ptype,a,b,*,t=1): return known(ptype,a,b,native=f'rel.{ptype}.{canonical_sha256({"a":a,"b":b,"t":t})[:16]}',t=t)
def frame(*,branch='main',commit='c1',t=1):
    return SoftwareWorldFrame.build(scope=scope(),workspace='ws.main',repository='repo.main',worktree='wt.main',branch=branch,commit=commit,environment='env.sha',time=WorldTime(valid_from_ms=t,observed_at_ms=t,recorded_at_ms=t))
def delta(fr,parent,changes): return GitCommitDelta.build(frame=fr,parent_commit=parent,changes=changes)

@pytest.mark.parametrize('ptype,etype',sorted(ENTITY_IDENTITY_TYPES.items()))
def test_all_frozen_entity_types(ptype,etype):
    out=SoftwareWorldUpdater().update(frame=frame(),known_delta=(identity(ptype,'anchor.'+etype,'name.'+etype),))
    assert len(out.graph.entities())==1 and out.graph.entities()[0].entity_type==etype


def test_frame_branch_isolation_and_commit_revision():
    a=frame(branch='main',commit='c1',t=1); b=frame(branch='main',commit='c2',t=2); c=frame(branch='feature',commit='c2',t=2)
    assert a.frame_id==b.frame_id and a.frame_revision_hash!=b.frame_revision_hash and a.frame_id!=c.frame_id
    with pytest.raises(FrameMismatch): SoftwareWorldUpdater().update(frame=c,graph=SparseWorldGraph(a))


def test_rename_preserves_file_identity():
    u=SoftwareWorldUpdater(); f1=frame(commit='c1'); g=u.update(frame=f1,git_delta=delta(f1,None,(GitPathChange('ADD',new_path='a.py',new_blob_sha='1'*64,explicit_identity_anchor='file.a'),))).graph
    old=g.file_entities('a.py')[0]; f2=frame(commit='c2',t=2)
    u.update(frame=f2,graph=g,git_delta=delta(f2,'c1',(GitPathChange('RENAME',old_path='a.py',new_path='b.py',old_blob_sha='1'*64,new_blob_sha='1'*64),)))
    new=g.file_entities('b.py')[0]
    assert new.entity_id==old.entity_id and new.revision==old.revision+1 and 'a.py' in new.aliases


def test_delete_then_add_is_not_silent_rename():
    u=SoftwareWorldUpdater(); f1=frame(commit='c1'); g=u.update(frame=f1,git_delta=delta(f1,None,(GitPathChange('ADD',new_path='a.py',new_blob_sha='1'*64,explicit_identity_anchor='old.a'),))).graph
    old=g.file_entities('a.py')[0]; f2=frame(commit='c2',t=2)
    u.update(frame=f2,graph=g,git_delta=delta(f2,'c1',(GitPathChange('DELETE',old_path='a.py',old_blob_sha='1'*64),)))
    assert g.entity(old.entity_id).lifecycle=='RETIRED'
    f3=frame(commit='c3',t=3); u.update(frame=f3,graph=g,git_delta=delta(f3,'c2',(GitPathChange('ADD',new_path='a.py',new_blob_sha='2'*64),)))
    assert g.file_entities('a.py')[0].entity_id!=old.entity_id


def test_ambiguous_identity_does_not_strong_merge():
    u=SoftwareWorldUpdater(); fr=frame(); g=u.update(frame=fr,known_delta=(identity('FILE_IDENTITY','f.1','same.py'),identity('FILE_IDENTITY','f.2','same.py',t=2))).graph
    basis=known('GIT_OBSERVED','rename.event','same.py',native='rename.basis')
    from world_understanding.known.set import known_ref
    f2=frame(commit='c2',t=2); out=u.update(frame=f2,graph=g,git_delta=delta(f2,'c1',(GitPathChange('RENAME',old_path='same.py',new_path='new.py',source_ref=known_ref(basis)),)))
    assert not g.file_entities('new.py') and out.identity_candidates and out.identity_candidates[0].state=='AMBIGUOUS'


def test_one_file_delta_does_not_rebuild_graph():
    u=SoftwareWorldUpdater(); f1=frame(); rows=tuple(identity('FILE_IDENTITY',f'f.{i}',f'src/f{i}.py') for i in range(1000)); first=u.update(frame=f1,known_delta=rows)
    f2=frame(commit='c2',t=2); out=u.update(frame=f2,graph=first.graph,git_delta=delta(f2,'c1',(GitPathChange('MODIFY',old_path='src/f500.py',new_path='src/f500.py',old_blob_sha='1'*64,new_blob_sha='2'*64),)))
    assert len(out.graph.entities())==1000 and out.stats.entities_examined==1 and out.stats.full_rescan is False and len(out.touched_entity_ids)==1


def test_world_graph_and_derivation_dag_are_separate():
    out=SoftwareWorldUpdater().update(frame=frame(),known_delta=(identity('FUNCTION_IDENTITY','fn.A','A'),identity('FUNCTION_IDENTITY','fn.B','B'),rel('DIRECT_CALLS','A','B')))
    assert all(isinstance(x,WorldEntity) for x in out.graph.entities()) and all(isinstance(x,WorldRelation) for x in out.graph.relations())

@pytest.mark.parametrize('ptype,klass',[('CONTAINS','STRUCTURAL'),('DEFINES','STRUCTURAL'),('IMPORTS','MATERIALIZED'),('DIRECT_CALLS','MATERIALIZED'),('CALL_REACHABLE','DERIVED_CACHE'),('USES','MATERIALIZED'),('READS','MATERIALIZED'),('WRITES','MATERIALIZED'),('REGISTERED_AS','STRUCTURAL'),('BELONGS_TO','STRUCTURAL'),('LOCATED_IN','STRUCTURAL')])
def test_l3_relation_classes(ptype,klass):
    obj='B|path=A>B' if ptype=='CALL_REACHABLE' else 'B'
    out=SoftwareWorldUpdater().update(frame=frame(),known_delta=(identity('FUNCTION_IDENTITY','fn.A','A'),identity('FUNCTION_IDENTITY','fn.B','B'),rel(ptype,'A',obj)))
    assert len(out.graph.relations())==1 and out.graph.relations()[0].materialization_class==klass


def test_semantic_relations_are_deferred():
    rows=[identity('FUNCTION_IDENTITY','fn.A','A'),identity('FUNCTION_IDENTITY','fn.B','B')]+[rel(p,'A','B') for p in sorted(FORBIDDEN_SEMANTIC_RELATIONS)]
    out=SoftwareWorldUpdater().update(frame=frame(),known_delta=tuple(rows))
    assert not out.graph.relations() and 'SEMANTIC_RELATION_DEFERRED_TO_L4_L5' in out.diagnostics


def test_false_and_stale_records_do_not_materialize():
    a=identity('FUNCTION_IDENTITY','fn.A','A').model_copy(update={'epistemic_state':'STALE'}).with_computed_hash()
    b=identity('FUNCTION_IDENTITY','fn.B','B').model_copy(update={'truth_state':'FALSE'}).with_computed_hash()
    out=SoftwareWorldUpdater().update(frame=frame(),known_delta=(a,b))
    assert not out.graph.entities()


def test_cross_life_known_fails_closed():
    row=known('FUNCTION_IDENTITY','fn.B','B',life='life.B')
    with pytest.raises(Exception): SoftwareWorldUpdater().update(frame=frame(),known_delta=(row,))


def test_git_delta_replay_is_idempotent():
    u=SoftwareWorldUpdater(); fr=frame(); d=delta(fr,None,(GitPathChange('ADD',new_path='same.py',new_blob_sha='1'*64,explicit_identity_anchor='same'),)); first=u.update(frame=fr,git_delta=d); before=first.graph.file_entities('same.py')[0]
    second=u.update(frame=fr,graph=first.graph,git_delta=d); after=second.graph.file_entities('same.py')[0]
    assert before.entity_sha256==after.entity_sha256 and second.stats.git_change_count==0 and 'GIT_DELTA_ALREADY_APPLIED' in second.diagnostics
