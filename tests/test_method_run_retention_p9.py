"""Real local retention/Gateway tests. Not native Windows or model acceptance."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest
from contracts.world_understanding._base import WorldRecordRef
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.world_state import WorldStateStore
from world_understanding.world_state.retention import RetainedWorldState
from tests.test_world_understanding_p13_1_production_activation import _scope, _source, _frame
from tests.test_method_source_publication_p9 import context, _publication, _current  # noqa: F401


def _simple(tmp_path, **kwargs):
    store=WorldStateStore(root=tmp_path, max_history_per_frame=2, **kwargs)
    runtime=ProductionWorldUnderstandingRuntime(store=store, frame_factory=_frame)
    runtime.facade.accept(_source("initial", 1))
    state=store.current_candidates(life_id=_scope().life_id, principal_scope_hash=_scope().principal_scope_hash)[0]
    return runtime, store, state


def _advance(runtime, n=4):
    for i in range(n):
        assert runtime.facade.accept(_source("advance:"+str(i), 100+i)).processed


def test_retained_world_survives_pruning_restart_and_release(tmp_path):
    runtime, store, old=_simple(tmp_path)
    pin=RetainedWorldState("owner:1", old.state_ref, _scope())
    store.retain_state(pin)
    _advance(runtime, 70)
    assert len(store.history(life_id=_scope().life_id, world_scope_hash=_scope().world_scope_hash,
                             principal_scope_hash=_scope().principal_scope_hash, frame_id=old.frame_id))==2
    assert store.get(old.state.world_state_id)==old
    reopened=WorldStateStore(root=tmp_path, max_history_per_frame=2)
    assert reopened.retained_states()==(pin,)
    assert reopened.get(old.state.world_state_id)==old
    assert reopened.release_retained_state(pin)
    assert reopened.get(old.state.world_state_id) is None
    assert not reopened.release_retained_state(pin)
    assert json.loads((tmp_path/'index.json').read_bytes())["schema"].endswith('.v2')


def test_two_owners_one_state_need_both_releases(tmp_path):
    runtime, store, old=_simple(tmp_path)
    pins=[RetainedWorldState("owner:"+str(i), old.state_ref, _scope()) for i in range(2)]
    for pin in pins: store.retain_state(pin)
    _advance(runtime)
    store.release_retained_state(pins[0]); assert store.get(old.state.world_state_id)==old
    store.release_retained_state(pins[1]); assert store.get(old.state.world_state_id) is None


def test_duplicate_pin_is_idempotent_but_rebind_is_forbidden(tmp_path):
    runtime, store, old=_simple(tmp_path)
    pin=RetainedWorldState("owner:1", old.state_ref, _scope()); store.retain_state(pin)
    before=(tmp_path/'index.json').read_bytes(); store.retain_state(pin)
    assert (tmp_path/'index.json').read_bytes()==before
    _advance(runtime, 1)
    new=store.current_candidates(life_id=_scope().life_id, principal_scope_hash=_scope().principal_scope_hash)[0]
    with pytest.raises(ValueError, match="OWNER_REBOUND"):
        store.retain_state(replace(pin, state_ref=new.state_ref))
    with pytest.raises(ValueError, match="RELEASE_MISMATCH"):
        store.release_retained_state(replace(pin, state_ref=new.state_ref))


def test_retention_capacity_fails_closed_without_evicting_live_owner(tmp_path):
    _, store, old=_simple(tmp_path, max_retained_states=1)
    one=RetainedWorldState('owner:1', old.state_ref, _scope()); store.retain_state(one)
    with pytest.raises(ValueError, match="FULL"):
        store.retain_state(replace(one, owner_id='owner:2'))
    assert store.retained_states()==(one,)


@pytest.mark.parametrize('op', ['retain', 'release'])
def test_failed_index_write_rolls_back_retention_memory_and_disk(tmp_path, monkeypatch, op):
    _, store, old=_simple(tmp_path)
    pin=RetainedWorldState('owner:1', old.state_ref, _scope())
    if op=='release': store.retain_state(pin)
    before=(tmp_path/'index.json').read_bytes(); pins=store.retained_states()
    def fail(*a): raise OSError('injected retain index failure')
    with monkeypatch.context() as m:
        m.setattr(store,'_atomic_json',fail)
        with pytest.raises(OSError, match='injected'):
            (store.retain_state if op=='retain' else store.release_retained_state)(pin)
    assert store.retained_states()==pins and (tmp_path/'index.json').read_bytes()==before
    assert WorldStateStore(root=tmp_path).retained_states()==pins


@pytest.mark.parametrize('bad', ['missing', 'hash', 'scope', 'duplicate', 'budget', 'downgrade'])
def test_corrupt_pin_reopen_never_discards_it_as_unneeded(tmp_path, bad):
    _, store, old=_simple(tmp_path); pin=RetainedWorldState('owner:1',old.state_ref,_scope());store.retain_state(pin)
    file=tmp_path/'index.json'; value=json.loads(file.read_bytes())
    if bad=='missing': (tmp_path/'snapshots'/(old.state.world_state_id+'.json')).unlink()
    elif bad=='hash': value['retained_states'][0]['retention_sha256']='b'*64
    elif bad=='scope': value['retained_states'][0]['scope']['principal_scope_hash']='b'*64
    elif bad=='duplicate': value['retained_states']*=2
    elif bad=='budget': value.pop('retained_states')
    elif bad=='downgrade': value['schema']='tiangong.world-state-store.index.v1'
    file.write_text(json.dumps(value))
    with pytest.raises(ValueError): WorldStateStore(root=tmp_path)


@pytest.mark.parametrize('identity', ['../index', '', 'wst_../x', 'fake'])
def test_retention_rejects_unsafe_state_identity(tmp_path, identity):
    _, _, old=_simple(tmp_path)
    with pytest.raises(ValueError):
        RetainedWorldState('owner:1',old.state_ref.model_copy(update={'record_id':identity}),_scope())


def test_historical_method_pin_survives_more_than_default_history(context):
    c=context; event,args,_=_publication(c);assert c['runtime'].facade.accept(event).processed
    old=_current(c); sources=c['resolver'].load(old)
    retained=c['runtime'].method_world_for_state(old.state_ref,scope=_scope(),retention_owner='method-plan:test',
        expected_method_source_refs=(sources.primitives[0].source_ref,))
    event,_,_=_publication(c,('UPDATE',),at_ms=26);assert c['runtime'].facade.accept(event).processed
    _advance(c['runtime'],70)
    assert c['runtime'].method_world_for_state(old.state_ref,scope=_scope())==retained
    reopened=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=c['tmp_path']/'state'),
        frame_factory=c['frame_factory'],method_revision_resolver=c['resolver'])
    assert reopened.method_world_for_state(old.state_ref,scope=_scope())==retained
    assert _current(c).state_ref!=old.state_ref


def test_rejected_source_selection_does_not_pin_anything(context):
    c=context; event,_,_=_publication(c);assert c['runtime'].facade.accept(event).processed
    old=_current(c); sources=c['resolver'].load(old)
    wrong=sources.primitives[0].source_ref.model_copy(update={'version':'v999'})
    with pytest.raises(ValueError,match='PLAN_REVISION_MISMATCH'):
        c['runtime'].method_world_for_state(old.state_ref,scope=_scope(),retention_owner='method-plan:test',
            expected_method_source_refs=(wrong,))
    assert not c['runtime'].store.retained_states()


@pytest.fixture
def bound(tmp_path, monkeypatch):
    from total_gateway.store import GatewayStateStore
    from total_gateway.method_source_run_binding import MethodRunSourceResolver
    from tests import test_composition_executable_plan_p7c0 as ec
    from tests import test_world_understanding_p13_1_production_activation as wu
    root=tmp_path/'work';root.mkdir()
    gateway=GatewayStateStore.open(tmp_path/'gateway.sqlite',now_ms=1000)
    material=ec._compile_material(gateway,root)
    from v3.world_understanding_production import _scope as production_scope
    from tests import test_method_source_publication_p9 as pub
    from world_understanding.software_world import SoftwareWorldFrame
    import sys
    scope=production_scope(dict(life_id='life.main', principal_scope_hash=material['context'].principal_scope_hash,
                                workspace_id=material['workspace'].workspace_id))
    monkeypatch.setattr(wu,'_scope',lambda:scope)
    monkeypatch.setattr(pub,'_scope',lambda:scope)
    monkeypatch.setattr(sys.modules[__name__],'_scope',lambda:scope)
    scratch=tmp_path/'world';scratch.mkdir()
    c=context.__wrapped__(scratch)
    c['tmp_path']=scratch/'bound';c['tmp_path'].mkdir()
    def frame_factory(envelope, cut):
        return SoftwareWorldFrame.build(scope=envelope.scope_hint, workspace=material['workspace'].workspace_id,
            repository='repo.fixture',worktree='worktree.fixture',branch='fixture-main',commit=c['commit'],
            environment='test-env',time=envelope.source_time,world_cut=cut)
    c['frame_factory']=frame_factory
    c['runtime']=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=c['tmp_path']/'state'),
        frame_factory=frame_factory,method_revision_resolver=c['resolver'])
    assert c['runtime'].facade.accept(_source('bound-genesis',1)).processed
    event,_,_=_publication(c);assert c['runtime'].facade.accept(event).processed
    state=_current(c); sources=c['resolver'].load(state)
    old_worlds=ec._worlds;old_context=ec._context
    def worlds(specs):
        reg,tools,_=old_worlds(specs);return reg,tools,sources
    def ctx(**kwargs):
        return replace(old_context(**kwargs), world_state_ref=state.state.world_state_id,
                       world_state_sha256=state.state.state_sha256,context_sha256='0'*64).with_computed_sha256()
    monkeypatch.setattr(ec,'_worlds',worlds);monkeypatch.setattr(ec,'_context',ctx)
    material=ec._compile_material(gateway,root)
    bundle=ec._persist_executable(gateway,material)
    reader=MethodRunSourceResolver(gateway,c['runtime'])
    yield c,gateway,bundle.record.executable_plan,reader
    gateway.close()


def _read(reader,plan,**extra):
    return reader.read(request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation,
                       scope=_scope(),**extra)


def test_registered_gateway_plan_binds_reader_and_survives_pruning(bound):
    c,gateway,plan,reader=bound
    first=_read(reader,plan); assert first.primitives
    event,_,_=_publication(c,('UPDATE',),at_ms=26);assert c['runtime'].facade.accept(event).processed
    _advance(c['runtime'],70)
    assert _read(reader,plan)==first
    assert reader.reconcile()==()
    assert len(c['runtime'].store.retained_states())==1


def test_expiry_is_not_completion_for_retention(bound):
    c,gateway,plan,reader=bound;_read(reader,plan)
    gateway.expire_limited_activation_registrations(now_ms=3000)
    assert reader.reconcile()==()
    assert _read(reader,plan).snapshot_sha256==c['resolver'].load(c['runtime'].store.get(plan.legacy_plan.world_state_ref)).snapshot_sha256


@pytest.mark.parametrize('status',['cancel','release'])
def test_terminal_generation_releases_pin_without_granting_anything(bound,status):
    c,gateway,plan,reader=bound;_read(reader,plan);_advance(c['runtime'],70)
    if status=='cancel':gateway.cancel_generation(plan.request_id,reason_code='test.done',cancelled_at_ms=2000)
    else:gateway.release_generation(plan.request_id,released_at_ms=2000)
    with pytest.raises(ValueError,match='GENERATION_NOT_ACTIVE'): _read(reader,plan)
    assert len(reader.reconcile())==1
    assert c['runtime'].store.get(plan.legacy_plan.world_state_ref) is None
    assert reader.reconcile()==()


def test_registered_reader_rejects_state_substitution(bound):
    c,gateway,plan,reader=bound
    event,_,_=_publication(c,('UPDATE',),at_ms=26);assert c['runtime'].facade.accept(event).processed
    with pytest.raises(ValueError,match='CALLER_WORLD_MISMATCH'):_read(reader,plan,expected_state_ref=_current(c).state_ref)
    assert not c['runtime'].store.retained_states()


def test_registered_reader_rejects_other_generation_or_principal(bound):
    c,gateway,plan,reader=bound
    with pytest.raises(ValueError,match='REGISTERED_PLAN_REQUIRED'):
        _read(reader,plan.model_copy(update={'generation':plan.generation+1}))
    with pytest.raises(ValueError,match='PLAN_SCOPE_MISMATCH'):
        reader.read(request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation,
                    scope=_scope().model_copy(update={'principal_scope_hash':'b'*64}))
    assert not c['runtime'].store.retained_states()


def test_world_state_missing_before_first_pin_is_not_replaced_by_latest(bound):
    c,_,plan,reader=bound;_advance(c['runtime'],70)
    with pytest.raises(ValueError,match='WORLD_UNAVAILABLE'):_read(reader,plan)


def test_gateway_unavailable_cannot_release_all_pins(bound):
    c,gateway,plan,reader=bound;_read(reader,plan);before=c['runtime'].store.retained_states()
    gateway.close()
    with pytest.raises(Exception):reader.reconcile()
    assert c['runtime'].store.retained_states()==before


def test_existing_v3_context_reader_uses_registered_plan_when_configured(bound,monkeypatch):
    from v3 import world_understanding_production as module
    from v3.run_context import RunContext,bind_run_context
    c,gateway,plan,reader=bound
    monkeypatch.setattr(module,'_runtime',c['runtime']);monkeypatch.setattr(module,'_method_run_resolver',None)
    module.configure_production_method_run_sources(gateway)
    with bind_run_context(RunContext(request_id=plan.request_id,run_id=plan.run_id,generation=plan.generation,
        life_id='life.main',principal_scope_hash=plan.principal_scope_hash,workspace_id=plan.workspace.workspace_id)):
        methods=module.production_method_world_for_run()
        old=_current(c)
        assert module.production_method_world_for_state(old.state_ref)==methods
        event,_,_=_publication(c,('UPDATE',),at_ms=26);assert c['runtime'].facade.accept(event).processed
        with pytest.raises(ValueError,match='CALLER_WORLD_MISMATCH'):
            module.production_method_world_for_state(_current(c).state_ref)
        assert module.production_method_world_for_run()==methods


def test_real_child_recovers_both_gateway_plan_and_retained_old_world(bound):
    c,gateway,plan,reader=bound
    old=_read(reader,plan)
    event,_,_=_publication(c,('UPDATE',),at_ms=26);assert c['runtime'].facade.accept(event).processed
    _advance(c['runtime'],70)
    config=dict(gateway=str(gateway.path),state=str(c['tmp_path']/'state'),archive=str(c['archive']),
        repo=str(c['repo']),commit=c['commit'],bootstrap=c['resolver'].bootstrap_snapshot_sha256,
        reviewer=c['resolver'].trusted_reviewer_public_key.hex(),observer=c['resolver'].trusted_observer_public_key.hex(),
        request=plan.request_id,run=plan.run_id,generation=plan.generation,scope=_scope().model_dump(mode='json'))
    code='''
import json,sys
from pathlib import Path
from contracts.world_understanding.scope import WorldScope
from total_gateway.store import GatewayStateStore
from total_gateway.method_source_publication import MethodPublicationResolver
from total_gateway.method_source_run_binding import MethodRunSourceResolver
from world_understanding.world_state import WorldStateStore
from world_understanding.production import ProductionWorldUnderstandingRuntime
v=json.loads(sys.argv[1])
p=MethodPublicationResolver(Path(v['archive']),Path(v['repo']),'repo.fixture','worktree.fixture',v['commit'],
    v['bootstrap'],bytes.fromhex(v['reviewer']),bytes.fromhex(v['observer']),lambda:9999)
w=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=v['state']),frame_factory=lambda *a:None,
    method_revision_resolver=p)
g=GatewayStateStore.open(Path(v['gateway']),now_ms=2000)
r=MethodRunSourceResolver(g,w)
assert not r.reconcile()
a=r.read(request_id=v['request'],run_id=v['run'],generation=v['generation'],scope=WorldScope.model_validate_json(json.dumps(v['scope'])))
print(a.snapshot_sha256)
g.close()
'''
    import os
    result=subprocess.run([sys.executable,'-c',code,json.dumps(config)],text=True,capture_output=True,
        cwd=Path(__file__).parents[1],env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1]/'src')},timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==old.snapshot_sha256


def test_replanning_generation_releases_old_pin_only_after_gateway_supersedes(bound):
    c,gateway,plan,reader=bound;_read(reader,plan)
    lease=gateway.get_generation(plan.request_id)
    # Existing generation lease acquisition is the only generation authority.
    gateway.acquire_generation_lease(request_id=plan.request_id,run_id=plan.run_id,
        run_sequence=lease.run_sequence,generation=lease.generation+1,gateway_epoch=lease.gateway_epoch,
        lease_id='replacement.lease',owner_instance_id='replacement.worker',issued_at_ms=20000,lease_duration_ms=10000)
    assert gateway.get_generation(plan.request_id).generation>lease.generation
    with pytest.raises(ValueError,match='GENERATION_NOT_ACTIVE'):_read(reader,plan)
    assert len(reader.reconcile())==1


def test_run_metadata_is_not_a_replacement_for_a_durable_plan(bound):
    c,gateway,plan,reader=bound
    # A well-shaped but unregistered request cannot retain any state.
    with pytest.raises(ValueError,match='REGISTERED_PLAN_REQUIRED'):
        reader.read(request_id='req_'+'0'*64,run_id=plan.run_id,generation=plan.generation,scope=_scope())
    assert not c['runtime'].store.retained_states()


def test_pin_to_wrong_scope_cannot_be_inserted(tmp_path):
    _,store,old=_simple(tmp_path)
    with pytest.raises(ValueError,match='SNAPSHOT_UNAVAILABLE'):
        store.retain_state(RetainedWorldState('owner:1',old.state_ref,
            _scope().model_copy(update={'principal_scope_hash':'b'*64})))
    assert not store.retained_states()


def test_releasing_current_state_does_not_delete_current_snapshot(tmp_path):
    _,store,old=_simple(tmp_path);pin=RetainedWorldState('owner:1',old.state_ref,_scope())
    store.retain_state(pin);store.release_retained_state(pin)
    assert store.get(old.state.world_state_id)==old


def test_pin_cannot_be_created_for_an_already_evicted_snapshot(tmp_path):
    runtime,store,old=_simple(tmp_path);_advance(runtime)
    with pytest.raises(ValueError,match='SNAPSHOT_UNAVAILABLE'):
        store.retain_state(RetainedWorldState('owner:1',old.state_ref,_scope()))


def test_concurrent_pin_attempts_cannot_overwrite_owner_binding(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    runtime,store,old=_simple(tmp_path);_advance(runtime,1)
    current=store.current_candidates(life_id=_scope().life_id,principal_scope_hash=_scope().principal_scope_hash)[0]
    pins=[RetainedWorldState('owner:1',s.state_ref,_scope()) for s in (old,current)]
    def retain(pin):
        try:store.retain_state(pin);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(retain,pins))
    assert sum(results)==1
    assert len(store.retained_states())==1


def test_v1_index_remains_v1_until_first_successful_pin(tmp_path):
    _,store,old=_simple(tmp_path)
    assert json.loads((tmp_path/'index.json').read_bytes())['schema'].endswith('.v1')
    store.retain_state(RetainedWorldState('owner:1',old.state_ref,_scope()))
    assert json.loads((tmp_path/'index.json').read_bytes())['schema'].endswith('.v2')
