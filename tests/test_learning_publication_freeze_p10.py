"""P10 R1: deny new legacy capabilities, retain Knowledge/evidence/history."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import canonical_sha256
from life_service.learning_workflow import (
    LEGACY_PUBLICATION_FROZEN as FROZEN, MIGRATION_REQUIRED,
    build_draft, confirm_draft, publish_draft, legacy_publication_blocked,
)
from life_service.artifact_executor import (
    ArtifactExecutorError, compile_artifact, publish_artifact,
    persist_artifact_bundle, persist_current_pointer, rollback_pointer,
)
from life_service.embedded_runtime import EmbeddedLifeRuntime, EmbeddedLifeError
from total_gateway.runtime import life_capability_workspace_mapper, life_capability_workspace_marker
from tests.legacy_learning_fixtures import historical_published_artifact, seed_historical_bundle


def decision(kind='skill'):
    return {'target':kind, 'request':'retained learning source '+kind, 'title':'审计材料',
            'risk_level':'A0','summary':'带来源的学习预览',
            'draft_artifact':{'content':'# 原始预览\n不得当作已发布能力。', 'required_actions':['web.search'],
                'steps':[{'step_id':'lookup','action_id':'web.search','arguments_template':{'query':'proof'}}]}}


def compiled(kind='skill', life_id='life_freeze'):
    learning={**decision(kind), 'life_id':life_id,'learning_id':'learn_freeze'}
    if kind=='knowledge': learning['draft_artifact']={'content':'# 普通知识材料'}
    return compile_artifact(learning,action_catalog=[{'action_id':'web.search','risk':'A3','available':True}])


@pytest.fixture
def life(tmp_path):
    runtime=EmbeddedLifeRuntime(data_root=tmp_path/'data',runtime_root=tmp_path/'runtime',mode='embedded')
    runtime.scheduler.stop(timeout_seconds=2)
    runtime.set_artifact_action_catalog_provider(lambda:[{'action_id':'web.search','risk':'A3','available':True}])
    yield runtime
    runtime.close()


def seed_history(life,status='active'):
    life_id=life._active()['life_id']; c=compiled(life_id=life_id); a=historical_published_artifact(c)
    pointer={'schema':'tiangong.life.capability-pointer.v1','life_id':life_id,'lineage_id':a['lineage_id'],
             'kind':'skill','status':status,'current_artifact_id':a['artifact_id'],
             'current_artifact_sha256':a['artifact_sha256'],'history':[]}
    from life_service.capability_health import attach_health
    pointer=attach_health(pointer,artifact=a,now_ms=1)
    scope=life._scope_state(life_id)
    scope['capabilities'][a['artifact_id']]={**a,'origin':'life_learning'}
    scope['capability_pointers'][a['lineage_id']]=pointer
    seed_historical_bundle(life.paths.artifact_root,c,pointer)
    life._persist(life_id)
    return a,pointer


@pytest.mark.parametrize('kind',['skill','tool','capability','Skill','learning_tool'])
def test_classifier_cannot_be_opened_by_model_flags(kind):
    assert legacy_publication_blocked({'kind':kind,'may_publish':True,'user_confirmed':True,'registered':True})


@pytest.mark.parametrize('patch',[{'target':'skill'},{'skill_spec':{}},{'skill_spec':{'steps':[]}},{'required_actions':['file.write']},
                                  {'registers_tool':True},{'execution':{'artifact':{'kind':'tool'}}},
                                  {'artifact_kind':'unknown'},{'kind':'unknown'}])
def test_knowledge_label_cannot_hide_structural_capability_metadata(patch):
    assert legacy_publication_blocked({'kind':'knowledge',**patch})


def test_knowledge_text_can_contain_tool_words_without_becoming_a_capability():
    assert not legacy_publication_blocked({'kind':'kb','document':{'content':'SKILL.md tool skill_spec code example'}})


@pytest.mark.parametrize('kind',['skill','tool'])
@pytest.mark.parametrize('sink',['publish','persist','rollback'])
def test_lower_artifact_sinks_reject_before_any_disk_write(tmp_path,kind,sink):
    c=compiled(kind)
    with pytest.raises(ArtifactExecutorError,match=FROZEN):
        if sink=='publish': publish_artifact(c)
        elif sink=='persist': persist_artifact_bundle(tmp_path/'new',c)
        else: rollback_pointer(c,c)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('kind',['skill','tool'])
@pytest.mark.parametrize('source',['autonomous','user_direct'])
def test_confirmed_or_direct_draft_cannot_become_registered(kind,source):
    d=build_draft(life_id='life_freeze',scope={},decision=decision(kind),source=source)
    if d['status']=='awaiting_user': d=confirm_draft(d,draft_sha256=d['draft_sha256'])
    before=deepcopy(d)
    result,a=publish_draft(d,capabilities={})
    assert result['status']==MIGRATION_REQUIRED and not result['registered'] and a is None
    assert result['draft_artifact']==before['draft_artifact'] and d==before
    assert result['retryable'] is False


@pytest.mark.parametrize('kind',['skill','tool'])
@pytest.mark.parametrize('endpoint',['draft','user-request'])
def test_runtime_learning_retains_evidence_but_never_calls_publish_map_or_writes_bundle(life,kind,endpoint):
    calls=[]
    life.set_artifact_publisher(lambda a:calls.append('publish'))
    life.set_capability_workspace_mapper(lambda a:calls.append('map'))
    code,result,_=life.request('POST','/api/v1/v3/life/learning/'+endpoint,{'decision':decision(kind)})
    assert code==200 and result['ok'] is False and result['reason_code']==FROZEN
    d=result['learning']; assert d['status']==MIGRATION_REQUIRED and d['learning_evidence']
    assert d['draft_artifact'] and not d['registered']
    assert not life._scope_state()['capabilities'] and not life._scope_state()['capability_pointers']
    assert calls==[] and not list(life.paths.artifact_root.rglob('SKILL.md'))
    before=deepcopy(life._scope_state()['learning'])
    life._recover_approved_learning_cards(life_id=d['life_id'])
    assert life._scope_state()['learning']==before


@pytest.mark.parametrize('alias',['confirm','process-approved','request-activation','activate','release'])
def test_existing_approved_queue_and_every_compatibility_alias_fail_closed(life,alias):
    life_id=life._active()['life_id']
    d=build_draft(life_id=life_id,scope={},decision=decision(),source='user_direct')
    d['learning_evidence']={'source':'original-proof'}
    life._scope_state()['learning'][d['learning_id']]=deepcopy(d)
    code,result,_=life.request('POST','/api/v1/v3/learning/'+alias,{'learning_id':d['learning_id']})
    assert code==200 and result['reason_code']==FROZEN
    saved=life._scope_state()['learning'][d['learning_id']]
    assert saved['status']==MIGRATION_REQUIRED and saved['learning_evidence']==d['learning_evidence']
    life._recover_approved_learning_cards(life_id=life_id)
    assert 'publish_retry' not in saved


def test_knowledge_still_uses_original_import_callback(life):
    calls=[]
    life.set_artifact_publisher(lambda a:calls.append(a) or {'publisher':'knowledge_store','knowledge_document_id':'k1'})
    code,result,_=life.request('POST','/api/v1/v3/life/learning/user-request',{'decision':decision('knowledge')})
    assert code==200 and result['ok'] and result['learning']['status']=='published'
    assert len(calls)==1 and calls[0]['kind']=='knowledge'
    assert result['artifact']['artifact_id'] in life._scope_state()['knowledge']
    assert not life._scope_state()['capabilities']


@pytest.mark.parametrize('method',['_capability_patch_propose','_capability_patch_settle','_capability_reactivate','_capability_rollback'])
def test_patch_and_activation_reentry_cannot_change_retained_history(life,method):
    a,p=seed_history(life);before=deepcopy(life._scope_state()['capabilities']);ptr=deepcopy(p)
    with pytest.raises(EmbeddedLifeError,match=FROZEN):getattr(life,method)({'artifact_id':a['artifact_id']})
    assert life._scope_state()['capabilities']==before and life._scope_state()['capability_pointers'][a['lineage_id']]==ptr


def test_pending_cannot_activate_but_active_read_idempotence_survives(life):
    a,p=seed_history(life,'pending')
    with pytest.raises(EmbeddedLifeError,match=FROZEN):life._capability_activate({'artifact_id':a['artifact_id']})
    p['status']='active'; p['pointer_sha256']=canonical_sha256({k:v for k,v in p.items() if k!='pointer_sha256'})
    assert life._capability_activate({'artifact_id':a['artifact_id']})['already_active'] is True


def test_old_pinned_artifact_can_still_invoke_existing_authorized_invoker(life):
    a,p=seed_history(life);seen=[]
    life.set_artifact_invoker(lambda action,args,ctx:seen.append((action,ctx['artifact_sha256'])) or {'ok':True})
    result=life._capability_invoke({'artifact_id':a['artifact_id'],'inputs':{}})
    assert result['ok'] and seen==[('web.search',a['artifact_sha256'])]


def test_health_scheduler_and_workspace_rebuild_do_not_retry_frozen_route(life,tmp_path):
    a,p=seed_history(life);seen=[]
    life.set_capability_patch_decider(lambda x:seen.append('patch') or {})
    life.set_capability_workspace_mapper(lambda x:seen.append('map') or {})
    before=deepcopy(life._scope_state()['capability_pointers'])
    life._schedule_capability_health_decision(life_id=a['life_id'])
    life._sync_life_capability_workspace_zone(life_id=a['life_id'])
    assert seen==[] and before==life._scope_state()['capability_pointers']
    target=tmp_path/'skills'/'life'/'old.md';target.parent.mkdir(parents=True);target.write_text('old pinned file')
    assert life_capability_workspace_mapper(tmp_path)(a)['reason_code']==FROZEN
    assert target.read_text()=='old pinned file'
    assert life_capability_workspace_marker(tmp_path)(a,{'status':'active'})['reason_code']==FROZEN


def test_pointer_sink_disallows_creation_switch_and_reactivation_but_allows_same_identity_disable(life):
    a,p=seed_history(life)
    kw={'life_id':a['life_id'],'lineage_id':a['lineage_id']}
    for status in ('pending','active'):
        nextp={**p,'status':status};nextp['pointer_sha256']=canonical_sha256({k:v for k,v in nextp.items() if k!='pointer_sha256'})
        with pytest.raises(ArtifactExecutorError,match=FROZEN):persist_current_pointer(life.paths.artifact_root,pointer=nextp,**kw)
    nextp={**p,'status':'disabled'};nextp['pointer_sha256']=canonical_sha256({k:v for k,v in nextp.items() if k!='pointer_sha256'})
    path=persist_current_pointer(life.paths.artifact_root,pointer=nextp,**kw)
    assert json.loads(path.read_text())['status']=='disabled'


@pytest.mark.parametrize('endpoint',['publish','published','register','release','unknown'])
def test_compatibility_mutators_cannot_inject_published_rows(life,endpoint):
    a,p=seed_history(life);before=deepcopy(life._scope_state()['capabilities'])
    code,result,_=life.request('POST','/api/v1/v3/life/capability/'+endpoint,{**a,'registered':True})
    assert code==409 and result['reason_code']==FROZEN and life._scope_state()['capabilities']==before


def test_sqlite_old_current_promotion_is_frozen_even_without_runtime(tmp_path):
    from life_service.store import LifeShadowStore,LifeShadowStoreError
    with LifeShadowStore.open(tmp_path/'state.shadow.sqlite3',create=True,now_ms=1) as store:
        with pytest.raises(LifeShadowStoreError,match=FROZEN):
            store.put_capability_pointer(life_id='life',skill_id='skill',candidate_id='c',artifact_sha256='a'*64,
                pointer_sha256='b'*64,expected_pointer_sha256=None,now_ms=2)
        with pytest.raises(LifeShadowStoreError,match=FROZEN):
            store.advance_capability_candidate(candidate_id='c',to_phase='CURRENT',expected_phase='SHADOW',payload_sha256='b'*64)
        assert store.get_capability_pointer(life_id='life',skill_id='skill') is None


def test_legacy_bridge_cannot_dispatch_to_an_injected_old_engine():
    from v3.duihua_qiaojie import DuihuaQiaojie
    def bad(*a,**k):pytest.fail('legacy engine was called')
    bridge=object.__new__(DuihuaQiaojie);bridge._zd=SimpleNamespace(zizhu_xuexi_yq=SimpleNamespace(
        **{name:bad for name in ('create_learning_card_from_request','confirm_learning_card',
            'process_approved_learning_card','request_activation','activate_learning_card','release_learning_card')}))
    for name in ('create_learning_card_from_request','confirm_learning_card','process_approved_learning_card',
                 'request_learning_activation','activate_learning_card','release_learning_card'):
        assert getattr(bridge,name)({'actor':'user','desired_scope':'tool'})['reason_code']==FROZEN


def test_dormant_legacy_pipeline_is_blocked_before_import_or_network():
    from v3.jineng.jirou_ceng import JirouCeng
    assert JirouCeng._xuexi_liucheng(content='source',release_skill_tool=True)['reason_code']==FROZEN


def test_legacy_registry_cannot_add_or_activate_but_can_read_disable_remove(tmp_path):
    from v3.zhili.nengli_zhuche import NengliZhuche,NengliDingyi
    path=tmp_path/'registry.json'
    row=NengliDingyi('old','gongju',nengli_id='old',zhuangtai='daijihuo')
    path.write_text(json.dumps({'nengli_list':[row]}))
    registry=NengliZhuche(path)
    with pytest.raises(ValueError,match=FROZEN):registry.zhuce_nengli({'mingcheng':'new','leixing':'gongju'})
    with pytest.raises(ValueError,match=FROZEN):registry.jihuo_nengli('old')
    assert registry.huoqu_nengli('old') is not None
    assert registry.tingyong_nengli('old')
    assert registry.zhuxiao_nengli('old')
