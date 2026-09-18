"""Real immutable Tool Git/bundle and signed Method archive -> existing P4 path.

The keys, build observations and model text are fixtures, not live acceptance.
No candidate code is imported, Source published by planning, or Action dispatched.
"""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from contracts import canonical_json_bytes, canonical_sha256, derive_run_identity
from contracts.world_understanding.time import WorldTime
from total_gateway.action_registry import compile_action_authority
from total_gateway.composition_source_preparation import PlanningToolSource
from total_gateway.method_source_publication import (
    MethodPublicationResolver, PUBLICATION_DOMAIN, build_method_publication_body,
    stage_method_publication, method_publication_envelope,
)
from total_gateway.method_source_run_binding import MethodRunSourceResolver
from total_gateway.store import GatewayStateStore
from world_understanding.capability_composition import (
    compile_capability_composition_plan, validate_capability_composition_plan, parse_composition_proposal,
)
from world_understanding.context_output import ContextOutputPort, WorldContextProjector, WorldContextRequestHandler
from world_understanding.domain_contribution import compile_tool_capability_contribution
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.skill_method_world.compiler import compile_native_method_source
from world_understanding.software_world import SoftwareWorldFrame, SparseWorldGraph
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import WorldStateStore, WorldStateMaterializer, MaterializationInput, materialize_one_world_state
from tests.test_tool_source_bundle_p8 import source as original_tool_source
from tests.test_tool_source_publication_p8 import publication  # fixture uses the augmented source below
from tests.test_method_source_review_p9 import _inputs, _source, _sign_report, _bind_review
from tests.test_skill_method_source_lifecycle_p9 import _snapshot
from tests.test_gateway_worker_composition_resume_p7d2 import _envelope


@pytest.fixture
def source(tmp_path):
    root = original_tool_source.__wrapped__(tmp_path)
    policy = json.loads((root/'source-ownership.json').read_bytes())
    policy['mappings'].append({'id':'methods','source':'src/world_understanding','source_role':'authoritative','targets':[]})
    (root/'source-ownership.json').write_bytes(canonical_json_bytes(policy))
    for mid in ('native_0','native_1','acceptance_review','decompose_goal'):
        for version in ('v1','v2'):
            (_old_path, raw), _primitive = _source(mid,version)
            path=root/f'src/world_understanding/skill_method_world/sources/{mid}.{version}.json'
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return root


@pytest.fixture
def planning(publication, tmp_path, monkeypatch):
    from v3 import world_understanding_production as installed
    from v3.world_context_integration import WorldContextIntegration
    repo, base, head, package, marker = publication
    bundle, digest = package()
    import zipfile
    with zipfile.ZipFile(bundle) as z:
        artifact=json.loads(z.read('build-report.json'))['build_artifact']
    registry=compile_action_authority(artifact['gateway_manifest'],generated_at_ms=0).registry
    tool_source=PlanningToolSource(repo,bundle,digest,base,head,('skill.list',),
        'src/omni_body_skill/tools/handler.py','repo.fixture','worktree.fixture')
    tools,_=tool_source.load(registry)
    gateway=GatewayStateStore.open(tmp_path/'planning-gateway.sqlite3',now_ms=1000)
    inbound=_envelope('planning').model_copy(update={'text':'请用 native_0，查看 skill.list。'})
    registered=gateway.register_request(inbound,ingress_sha256='b'*64,created_at_ms=1100)
    request=registered.entry.request_id;run=derive_run_identity(request,1).run_id
    gateway.acquire_generation_lease(request_id=request,run_id=run,run_sequence=1,generation=1,gateway_epoch=1,
        lease_id='lease.planning',owner_instance_id='gateway.planning',issued_at_ms=1200,lease_duration_ms=100000)
    rc=SimpleNamespace(request_id=request,run_id=run,generation=1,life_id='life.main',
        principal_scope_hash=inbound.principal_scope_hash,workspace_id='workspace.main',
        session_id=inbound.conversation_ref,conversation_id=inbound.conversation_ref)
    scope=installed._scope(installed._run_identity(rc))
    args,reviewer,observer=_inputs()
    archive=tmp_path/'method-archives';archive.mkdir()
    method_resolver=MethodPublicationResolver(archive,repo,'repo.fixture','worktree.fixture',base,
        _snapshot().snapshot_sha256,reviewer.public_key().public_bytes_raw(),observer.public_key().public_bytes_raw(),lambda:30)
    def frame_factory(envelope,cut):
        return SoftwareWorldFrame.build(scope=envelope.scope_hint,workspace=rc.workspace_id,
            repository='repo.fixture',worktree='worktree.fixture',branch='main',commit=head,
            environment='test-env',time=envelope.source_time,world_cut=cut)
    store=WorldStateStore(root=tmp_path/'world')
    port=ContextOutputPort()
    def resolve(q):
        s=store.get(q.basis_world_state_ref.record_id)
        return s if s is not None and s.state_ref==q.basis_world_state_ref and s.state.scope==q.scope else None
    handler=WorldContextRequestHandler(state_resolver=resolve,projector=WorldContextProjector(),output_port=port)
    world=ProductionWorldUnderstandingRuntime(store=store,frame_factory=frame_factory,
        method_revision_resolver=method_resolver,context_request_handler=handler)
    initial=build_post_commit_source_envelope(source_kind='FACT_EXECUTION',source_native_id='planning.genesis',
        producer_ref='v3.fact_kernel',payload={'fact_transaction':{'operation_id':'planning.genesis','action':'write_file','state':'OBSERVED'}},
        source_time=WorldTime(valid_from_ms=1,observed_at_ms=1,recorded_at_ms=1),scope=scope,
        correlation_id='planning.genesis',workspace_id=rc.workspace_id)
    assert world.facade.accept(initial).processed
    previous=store.current_candidates(life_id=rc.life_id,principal_scope_hash=scope.principal_scope_hash)[0]
    candidates=[]
    for candidate in args['candidates']:
        path=f'src/world_understanding/skill_method_world/sources/{candidate.method_id}.v1.json'
        raw=(repo/path).read_bytes()
        primitive=compile_native_method_source(path,raw,expected_source_sha256=hashlib.sha256(raw).hexdigest())
        candidate=replace(candidate,primitive=primitive).with_computed_sha256()
        evidence=_sign_report(candidate,observer)
        candidate=replace(candidate,simulation_evidence_sha256=hashlib.sha256(evidence.report_bytes).hexdigest()).with_computed_sha256()
        candidates.append(candidate);args['source_documents'][candidate.method_id]=(path,raw)
        args['simulation_evidence'][candidate.method_id]=evidence
    args['candidates']=tuple(candidates);_bind_review(args,reviewer)
    frame=frame_factory(initial,previous.cut)
    body=build_method_publication_body(frame=frame,previous=previous,review_inputs=args,publication_at_ms=25)
    archive_sha=stage_method_publication(archive_root=archive,body=body,
        publication_signature=reviewer.sign(PUBLICATION_DOMAIN+body))
    event=method_publication_envelope(archive_sha256=archive_sha,frame=frame,at_ms=25)
    receipt=world.facade.accept(event);assert receipt.processed,receipt
    current=store.current_candidates(life_id=rc.life_id,principal_scope_hash=scope.principal_scope_hash)[0]
    frame=frame_factory(event,current.cut);graph=SparseWorldGraph(frame)
    for e in current.entities:graph.upsert_entity(e)
    for r in current.relations:graph.upsert_relation(r)
    # The existing P6 materializer combines the measured Tool domain with this
    # signed Method world once; no second state or planner-owned store is created.
    current=materialize_one_world_state(WorldStateMaterializer(store),
        MaterializationInput(frame=frame,cut=current.cut,graph=graph,dependency_bindings=current.dependencies.bindings,
            preserve_previous_domains=True,source_transaction_id='planning.tool.observation',materialized_at_ms=25),
        (compile_tool_capability_contribution(frame,current.cut,tools),))
    resolver=MethodRunSourceResolver(gateway,world)
    monkeypatch.setattr(installed,'_method_run_resolver',resolver)
    bridge=WorldContextIntegration(store=store,facade=world.facade,output_port=port,token_budget=8000)
    data=dict(gateway=gateway,world=world,bridge=bridge,rc=rc,query_scope=scope,registry=registry,
        tool_source=tool_source,resolver=resolver,current=current,user=inbound.text,marker=marker,
        method_resolver=method_resolver,port=port)
    yield data
    gateway.close()


def prepare(c,**updates):
    return c['bridge'].prepare_composition_for_turn(run_context=c['rc'],user_text=c['user'],
        tool_source=c['tool_source'],registry=c['registry'],now_ms=5000,**updates)


def proposal(prepared):
    from tests.test_capability_composition_p4 import _proposal_document
    action=prepared.candidates.action_candidates[0].candidate_id
    method=next(m.candidate_id for m in prepared.candidates.method_candidates if m.primitive.method_id=='native_0')
    return _proposal_document(goal_ref=prepared.context.goal_ref,methods=(method,),actions=(action,),
                              steps=(("s1",action,()),))


def compile_reply(c,p,text=None,**kw):
    return c['bridge'].compile_composition_for_turn(p,proposal(p) if text is None else text,
        run_context=c['rc'],tool_source=c['tool_source'],validated_at_ms=5100,**kw)


def test_actual_sources_reach_real_candidates_and_original_p4_plan(planning):
    c=planning;p,prompt=prepare(c)
    assert p.has_valid_sha256()
    assert p.candidates.candidate_snapshot_sha256 != p.reference_context.candidate_snapshot_sha256
    assert 'DISPLAY_ONLY' not in prompt and 'system_compiler_required=true' in prompt
    assert prompt.count('[WORLD_CONTEXT_SLOT]')==1
    assert p.candidates.candidate_snapshot_sha256 in prompt
    before=c['gateway']._connection.total_changes
    result=compile_reply(c,p)
    expected=compile_capability_composition_plan(result.parse_outcome.proposal,p.candidates,p.context,p.registry)
    assert result.plan==expected
    assert result.plan.world_state_ref==c['current'].state_ref.record_id
    assert result.plan.action_source_refs[0]==p.candidates.action_candidates[0].source_revision
    assert result.validation==validate_capability_composition_plan(expected,result.parse_outcome.proposal,
        p.candidates,p.context,p.registry,validated_at_ms=5100)
    assert result.validation.result in {'PROVED_VALID','UNKNOWN','PROVED_INVALID'}
    assert not result.may_authorize and not result.may_execute
    assert c['gateway']._connection.total_changes==before
    assert c['gateway'].get_executable_composition_plan_for_request(c['rc'].request_id,run_id=c['rc'].run_id,generation=1) is None
    assert not c['marker'].exists()


def test_real_method_ref_and_reassigned_ids_are_not_guessed_from_display_labels(planning):
    c=planning;p,_prompt=prepare(c)
    rows={semantic:(display,real) for display,semantic,real in p.display_to_candidate}
    display,actual=rows['method:native_0']
    assert display!=actual  # task-priority display order differs from P4's semantic order
    result=compile_reply(c,p)
    method=next(m for m in p.candidates.method_candidates if m.primitive.method_id=='native_0')
    assert result.plan.method_source_refs==(method.primitive.source_ref,)
    assert result.plan.method_source_refs[0].source_files[0].endswith('/native_0.v1.json')
    assert result.validation.result=='UNKNOWN'  # P8 observed source lacks some mechanical capability facts.
    assert not result.may_execute


def test_preplan_reader_does_not_require_or_fabricate_registered_plan(planning):
    c=planning
    with pytest.raises(ValueError,match='REGISTERED_PLAN_REQUIRED'):
        c['resolver'].read(request_id=c['rc'].request_id,run_id=c['rc'].run_id,generation=1,scope=c['query_scope'])
    p,_=prepare(c)
    assert compile_reply(c,p).plan.plan_id
    assert c['world'].store.retained_states()==()


@pytest.mark.parametrize('field', ['risk','source_hash','permission','action_version'])
def test_model_authority_fields_are_rejected_by_original_parser(planning,field):
    c=planning;p,_=prepare(c);raw=json.loads(proposal(p));raw[field]='not-authoritative'
    with pytest.raises(ValueError,match='proposal.fields.invalid'):
        compile_reply(c,p,json.dumps(raw))


def test_original_parser_keeps_one_repair_and_original_error(planning):
    c=planning;p,_=prepare(c)
    result=compile_reply(c,p,'{not JSON',repair_text=proposal(p))
    assert result.parse_outcome.repaired and result.parse_outcome.primary_error_code
    with pytest.raises(ValueError,match='proposal.repair.failed'):
        compile_reply(c,p,'{not JSON',repair_text='still invalid')


def test_display_packet_is_not_a_prepared_p4_input(planning):
    c=planning;p,_=prepare(c)
    with pytest.raises(ValueError,match='REPLY_SCOPE_MISMATCH'):
        compile_reply(c,p.reference_context,proposal(p))


@pytest.mark.parametrize('field,value', [('generation',2),('run_id','run_'+'e'*64),('request_id','req_'+'f'*64),('workspace_id','workspace.other')])
def test_reply_cannot_change_request_run_generation_or_workspace(planning,field,value):
    c=planning;p,_=prepare(c);setattr(c['rc'],field,value)
    with pytest.raises(ValueError,match='REPLY_SCOPE_MISMATCH'):
        compile_reply(c,p)


def test_gateway_generation_release_rejects_late_response(planning):
    c=planning;p,_=prepare(c)
    c['gateway'].release_generation(c['rc'].request_id,released_at_ms=5050)
    with pytest.raises(ValueError,match='GENERATION_NOT_ACTIVE'):
        compile_reply(c,p)


def test_query_must_belong_to_the_persisted_inbound_task(planning):
    c=planning
    with pytest.raises(ValueError,match='REQUEST_TASK_MISMATCH'):
        c['bridge'].prepare_composition_for_turn(run_context=c['rc'],user_text='another task',
            tool_source=c['tool_source'],registry=c['registry'],now_ms=5000)


def test_mutable_tool_checkout_cannot_change_pinned_source(planning):
    c=planning;p,_=prepare(c)
    path=c['tool_source'].repository/c['tool_source'].action_entry_path
    path.write_bytes(b'raise AssertionError("do not execute changed checkout")\n')
    result=compile_reply(c,p)
    assert result.plan.action_source_refs[0]==p.candidates.action_candidates[0].source_revision
    assert not c['marker'].exists()


def test_tool_bundle_byte_drift_is_rejected_before_model_proposal_parse(planning):
    c=planning;p,_=prepare(c);path=c['tool_source'].bundle_path
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='digest'):
        compile_reply(c,p,'not JSON')


def test_method_archive_byte_drift_is_rejected(planning):
    c=planning;p,_=prepare(c)
    path=next(c['method_resolver'].archive_root.glob('*.json'))
    path.chmod(0o644);path.write_bytes(path.read_bytes()+b' ');path.chmod(0o444)
    with pytest.raises(ValueError):compile_reply(c,p)


def test_system_registry_drift_does_not_become_source_authority(planning):
    c=planning
    wrong=c['registry'].model_copy(update={'source_manifest_sha256':'f'*64}).with_computed_sha256()
    with pytest.raises(ValueError,match='SYSTEM_REGISTRY_MISMATCH'):
        c['bridge'].prepare_composition_for_turn(run_context=c['rc'],user_text=c['user'],
            tool_source=c['tool_source'],registry=wrong,now_ms=5000)


@pytest.mark.parametrize('field,value',[('repository_id','repo.other'),('worktree_id','worktree.other'),('candidate_commit','f'*40)])
def test_tool_source_configuration_must_match_exact_world_frame(planning,field,value):
    c=planning
    with pytest.raises(ValueError,match='TOOL_FRAME_MISMATCH'):
        c['bridge'].prepare_composition_for_turn(run_context=c['rc'],user_text=c['user'],
            tool_source=replace(c['tool_source'],**{field:value}),registry=c['registry'],now_ms=5000)


def test_preparation_tamper_even_after_rehash_cannot_change_plan_sources(planning):
    c=planning;p,_=prepare(c)
    candidate=p.candidates.action_candidates[0]
    # Alter a nested descriptor without changing the source evidence; P4 validates
    # it or exact preparation reconstruction rejects it, never forwards a plan.
    bad=replace(p,context=replace(p.context,environment_class='foreign-env',context_sha256='0'*64).with_computed_sha256())
    bad=replace(bad,preparation_sha256=canonical_sha256(bad.payload()))
    assert bad.has_valid_sha256()
    with pytest.raises(ValueError,match='PREPARATION_DRIFT'):
        compile_reply(c,bad)


def test_reference_only_digest_is_not_accepted_as_actual_candidate_digest(planning):
    c=planning;p,_=prepare(c)
    candidates=replace(p.candidates,candidate_snapshot_sha256=p.reference_context.candidate_snapshot_sha256)
    bad=replace(p,candidates=candidates)
    bad=replace(bad,preparation_sha256=canonical_sha256(bad.payload()))
    with pytest.raises(ValueError,match='PREPARATION_HASH_INVALID'):compile_reply(c,bad)


def test_unconfigured_preplan_reader_does_not_fallback_to_legacy_sources(planning,monkeypatch):
    c=planning
    from v3 import world_understanding_production as installed
    monkeypatch.setattr(installed,'_method_run_resolver',None)
    with pytest.raises(ValueError,match='RUNTIME_SCOPE_UNAVAILABLE'):prepare(c)


def test_current_world_advancement_rejects_preplan_response_without_latest_fallback(planning):
    c=planning;p,_=prepare(c);snapshot=c['current']
    frame=c['world'].frame_factory(SimpleNamespace(scope_hint=snapshot.state.scope,source_time=snapshot.cut.time),snapshot.cut)
    graph=SparseWorldGraph(frame)
    for entity in snapshot.entities:graph.upsert_entity(entity)
    for relation in snapshot.relations:graph.upsert_relation(relation)
    newer=WorldStateMaterializer(c['world'].store).materialize(MaterializationInput(
        frame=frame,cut=snapshot.cut,graph=graph,dependency_bindings=snapshot.dependencies.bindings,
        source_transaction_id='new-observation',materialized_at_ms=5050,preserve_previous_domains=True))
    assert newer.state_ref!=snapshot.state_ref
    with pytest.raises(ValueError,match='WORLD_NO_LONGER_CURRENT'):compile_reply(c,p)
    assert p.context.world_state_sha256==snapshot.state_ref.sha256


def test_restart_can_revalidate_same_preplan_without_new_state_store(planning,monkeypatch):
    c=planning;p,_=prepare(c)
    from v3 import world_understanding_production as installed
    restarted=ProductionWorldUnderstandingRuntime(store=c['world'].store,frame_factory=c['world'].frame_factory,
                                                   method_revision_resolver=c['method_resolver'])
    resolver=MethodRunSourceResolver(c['gateway'],restarted)
    monkeypatch.setattr(installed,'_method_run_resolver',resolver)
    result=compile_reply(c,p)
    assert result.plan.world_state_sha256==p.context.world_state_sha256
    assert resolver.gateway is c['gateway'] and restarted.store is c['world'].store


def test_baseline_parent_gateway_fixture_restores_surrounding_providers(tmp_path,monkeypatch):
    from unittest.mock import Mock
    from v3.simple_chain import kernel
    from tests.test_gateway_parent_claim_ticket_p8 import test_parent_ticket_binds_the_persisted_effect_claim
    names=('_SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER','_SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER')
    previous={name:Mock(name=name) for name in names}
    for name,value in previous.items():monkeypatch.setattr(kernel,name,value)
    with monkeypatch.context() as inner:
        test_parent_ticket_binds_the_persisted_effect_claim(tmp_path,inner,False)
    for name,value in previous.items():
        assert getattr(kernel,name) is value
        value.assert_not_called()


def test_query_cannot_be_replayed_for_same_text_in_a_second_active_request(planning):
    c=planning;p,_=prepare(c)
    inbound=_envelope('second-request').model_copy(update={'text':c['user']})
    assert inbound.principal_scope_hash==c['query_scope'].principal_scope_hash
    registration=c['gateway'].register_request(inbound,ingress_sha256='c'*64,created_at_ms=1200)
    request=registration.entry.request_id;run=derive_run_identity(request,1).run_id
    c['gateway'].acquire_generation_lease(request_id=request,run_id=run,run_sequence=1,generation=1,gateway_epoch=1,
        lease_id='lease.second',owner_instance_id='gateway.planning',issued_at_ms=1250,lease_duration_ms=100000)
    with pytest.raises(ValueError,match='REQUEST_QUERY_MISMATCH'):
        c['resolver'].prepare_composition(query=p.query,reference_context=p.reference_context,
            tool_source=c['tool_source'],registry=p.registry,request_id=request,run_id=run,generation=1,
            workspace_id=p.workspace_id,prepared_at_ms=5000)


def test_model_prompt_has_exact_goal_and_real_candidate_abi(planning):
    p,prompt=prepare(planning)
    assert 'goal_ref='+p.context.goal_ref in prompt
    assert 'compile_context='+p.context.context_sha256 in prompt
    assert 'source_approval_proven=false' in prompt
    assert 'proposal_fields=proposal_schema,goal_ref' in prompt
    assert p.capability_context().has_valid_sha256()


def test_rehashed_display_risk_cannot_lie_about_actual_candidate(planning):
    c=planning;p,_=prepare(c)
    first=p.reference_context.action_candidates[0]
    wrong=replace(first,risk_floor='A5' if first.risk_floor!='A5' else 'A0')
    reference=replace(p.reference_context,action_candidates=(wrong,*p.reference_context.action_candidates[1:]))
    reference=replace(reference,packet_sha256=reference.computed_sha256())
    with pytest.raises(ValueError,match='DISPLAY_SEMANTICS_MISMATCH'):
        c['resolver'].prepare_composition(query=p.query,reference_context=reference,
            tool_source=c['tool_source'],registry=p.registry,request_id=p.context.request_id,run_id=p.context.run_id,
            generation=p.context.generation,workspace_id=p.workspace_id,prepared_at_ms=5000)


def test_original_invalid_validation_is_returned_without_upgrade(planning):
    c=planning;p,_=prepare(c)
    result=c['bridge'].compile_composition_for_turn(p,proposal(p),run_context=c['rc'],
        tool_source=c['tool_source'],validated_at_ms=4999)
    assert result.validation.result=='PROVED_INVALID'
    assert any(f.code=='validator.time.before_plan' for f in result.validation.findings)
    assert not result.may_execute and not result.may_authorize


def test_real_existing_http_payload_and_model_text_roundtrip_into_original_p4(planning,tmp_path,monkeypatch):
    """Only endpoint configuration/DNS/socket I/O is mocked, not the P4 pipeline."""
    import httpx
    import sys
    from v3 import endpoint_security
    from v3.model_endpoint import ModelEndpointConfig
    from v3.jineng import http_kehuduan as client
    from v3.run_context import bind_run_context
    from v3.shenti_zhuangtai import ShentiZhuangtai
    c=planning;p,prompt=prepare(c);answer=proposal(p)
    endpoint=ModelEndpointConfig(service_preset='deepseek',provider_identity='deepseek_v4',
        protocol_family='openai_chat_completions',base_url='https://model.example.test/v1',
        model_name='deepseek-v4-pro',credential_scope='fixture',reasoning_mode='off',endpoint_overrides={},
        optimization_family='deepseek_v4',config_fingerprint='a'*64)
    monkeypatch.setattr(client,'duqu_model_endpoint_config',lambda _identity:endpoint)
    monkeypatch.setattr(client,'duqu_endpoint_api_miyao',lambda *_args:'test-only-noncredential')
    monkeypatch.setattr(endpoint_security,'_resolve',lambda _host,_port:('93.184.216.34',))
    registry=tmp_path/'legacy-registry.json';registry.write_text('{}','utf-8')
    monkeypatch.setattr(client,'NENGLI_ZHUCE_LUJING',registry)
    monkeypatch.setattr(client,'L4_OPTIMIZATION_TRACE_PATH',tmp_path/'trace.jsonl')
    monkeypatch.setattr(client,'duqu_model_reasoning_config',lambda *_a,**_kw:{'supported':False})
    monkeypatch.setattr(client,'_MODEL_ADAPTER_CORE',None)
    modules=set(sys.modules);captured=[]
    def respond(request):
        assert request.url.host=='93.184.216.34'
        captured.append(json.loads(request.content))
        data={'id':'fixture','model':endpoint.model_name,'choices':[{'index':0,'delta':{'content':answer},'finish_reason':None}]}
        final={'id':'fixture','model':endpoint.model_name,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=(
            'data: '+json.dumps(data)+'\n\ndata: '+json.dumps(final)+'\n\ndata: [DONE]\n\n').encode())
    http=client.HttpKehuduan(moren_provider='deepseek_v4');http._kehuduan.close()
    http._kehuduan=httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with bind_run_context({**vars(c['rc']),'current_user_message':c['user']}),http.scoped_tools(disable_tools=True):
            reply=http.llm_diaoyong(prompt,c['user'],provider_id='deepseek_v4',shenti=ShentiZhuangtai())
        assert len(captured)==1
        sent=captured[0]['messages']
        system=next(m['content'] for m in sent if m['role']=='system')
        assert system.count('[WORLD_CONTEXT_SLOT]')==1 and 'DISPLAY_ONLY' not in system
        assert 'candidate_snapshot='+p.candidates.candidate_snapshot_sha256 in system
        assert next(m['content'] for m in sent if m['role']=='user')==c['user']
        result=compile_reply(c,p,str(reply))
        assert result.parse_outcome.proposal==parse_composition_proposal(answer,p.candidates)
        assert result.plan.request_id==c['rc'].request_id and result.validation.result=='UNKNOWN'
        assert not result.may_execute and not c['marker'].exists()
    finally:
        http._kehuduan.close()
        for name in set(sys.modules)-modules:
            if name.startswith('_tiangong_omni_model_adapter_'):sys.modules.pop(name,None)
