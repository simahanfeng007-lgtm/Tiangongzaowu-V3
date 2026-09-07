"""R3-C: actual Gateway transactions and World retention, not model acceptance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from contracts import canonical_sha256, derive_effect_identity
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.method_source_run_binding import MethodRunSourceResolver, requires_method_retention
from total_gateway.store import GatewayStateStore, StoreConflictError
from world_understanding.world_state import WorldStateStore
from tests.test_method_run_retention_p9 import (
    unregistered_bound, bound, _read, _advance,  # noqa: F401
)
from tests.test_composition_executable_plan_p7c0 import _persist_executable


def _rows(gateway):
    tables=('composition_executable_plan','composition_activation_registration','verification_plan','verification_plan_activation')
    actual={r[0] for r in gateway._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(tables) <= actual
    return {t:gateway._connection.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}


def _install(item):
    c,g,material,reader=item
    g.configure_method_source_lifecycle(reader)
    return c,g,material,reader


def _claim(plan):
    intent=canonical_sha256({'purpose':'p9-source-retention','plan':plan.executable_plan_id})
    values=dict(request_id=plan.request_id,run_id=plan.run_id,run_sequence=1,generation=plan.generation,
                effect_kind='execution',ordinal=0,intent_sha256=intent)
    return EffectClaim(effect_id=derive_effect_identity(**values).effect_id,**values,
        pipeline_version='p9.source-retention-test',owner_component_id='tiangong-total-gateway',
        claimed_at_ms=1700,claim_sha256='0'*64).with_computed_sha256()


def _terminal(claim, *, status='SUCCEEDED'):
    return EffectResult(result_id='result.p9',effect_id=claim.effect_id,status=status,
        fact_id='fact.p9',evidence_sha256='7'*64,error_code='test.ambiguous' if status=='AMBIGUOUS' else None,
        observed_at_ms=2100,result_sha256='0'*64).with_computed_sha256()


class _CommitFault:
    def __init__(self, connection): self.connection=connection;self.failed=False
    def __getattr__(self, name): return getattr(self.connection,name)
    def execute(self, sql, parameters=()):
        if sql=='COMMIT' and not self.failed:
            self.failed=True
            raise sqlite3.OperationalError('injected before COMMIT')
        return self.connection.execute(sql,parameters)


def test_missing_configuration_rolls_back_all_registration_rows(unregistered_bound):
    c,g,material,_=unregistered_bound;before=_rows(g)
    with pytest.raises(StoreConflictError,match='NOT_CONFIGURED'):
        _persist_executable(g,material)
    assert _rows(g)==before
    assert not c['runtime'].store.retained_states()


def test_pin_is_durable_before_other_connection_can_see_plan(unregistered_bound,monkeypatch):
    c,g,material,reader=_install(unregistered_bound)
    real=c['runtime'].store.retain_state;observed=[]
    def retain(pin):
        real(pin)
        with sqlite3.connect(g.path) as outsider:
            observed.append(outsider.execute('SELECT COUNT(*) FROM composition_executable_plan').fetchone()[0])
        reopened=WorldStateStore(root=c['tmp_path']/'state')
        assert pin in reopened.retained_states()
    monkeypatch.setattr(c['runtime'].store,'retain_state',retain)
    bundle=_persist_executable(g,material)
    assert observed==[0]
    assert _rows(g)['composition_executable_plan']==1
    expected=reader.read(request_id=bundle.record.executable_plan.request_id,
                         run_id=bundle.record.executable_plan.run_id,generation=bundle.record.executable_plan.generation,
                         scope=c['runtime'].store.retained_states()[0].scope)
    _advance(c['runtime'],70)
    assert _read(reader,bundle.record.executable_plan)==expected


def test_pin_failure_rolls_back_parent_and_companion(unregistered_bound,monkeypatch):
    c,g,material,_=_install(unregistered_bound);before=_rows(g)
    def failed(): raise OSError('injected retention index failure')
    with monkeypatch.context() as m:
        m.setattr(c['runtime'].store,'_persist_index',failed)
        with pytest.raises(OSError,match='retention index'):_persist_executable(g,material)
    assert _rows(g)==before
    assert not c['runtime'].store.retained_states()
    assert not g._connection.in_transaction
    assert _persist_executable(g,material).created_by_this_call


def test_sql_commit_failure_releases_only_new_admission_pin(unregistered_bound):
    c,g,material,_=_install(unregistered_bound);before=_rows(g)
    connection=g._connection;g._connection=_CommitFault(connection)
    try:
        with pytest.raises(sqlite3.OperationalError,match='before COMMIT'):_persist_executable(g,material)
    finally:g._connection=connection
    assert _rows(g)==before and not connection.in_transaction
    assert not c['runtime'].store.retained_states()
    assert g.method_source_retention_status()['cleanup_error'] is None
    assert _persist_executable(g,material).created_by_this_call


def test_outer_registration_rollback_does_not_leave_dispatchable_plan(unregistered_bound):
    c,g,material,_=_install(unregistered_bound);before=_rows(g)
    with pytest.raises(RuntimeError,match='outer failure'):
        with g._lock,g._write_transaction():
            _persist_executable(g,material)
            assert c['runtime'].store.retained_states()
            raise RuntimeError('outer failure')
    assert _rows(g)==before and not c['runtime'].store.retained_states()


def test_duplicate_registration_does_not_duplicate_or_rebind_pin(unregistered_bound):
    c,g,material,_=_install(unregistered_bound)
    first=_persist_executable(g,material);pins=c['runtime'].store.retained_states()
    second=_persist_executable(g,material)
    assert second.duplicate and first.record.executable_plan==second.record.executable_plan
    assert c['runtime'].store.retained_states()==pins


def test_failed_duplicate_outer_transaction_does_not_release_existing_pin(unregistered_bound):
    c,g,material,_=_install(unregistered_bound)
    first=_persist_executable(g,material);pins=c['runtime'].store.retained_states()
    with pytest.raises(RuntimeError):
        with g._lock,g._write_transaction():
            _persist_executable(g,material)
            raise RuntimeError('after duplicate')
    assert c['runtime'].store.retained_states()==pins
    assert g.get_executable_composition_plan_record(first.record.executable_plan.executable_plan_id)


@pytest.mark.parametrize('boundary',['claim','mark_started','permit'])
def test_missing_pin_blocks_every_store_dispatch_boundary(bound,boundary):
    c,g,plan,_=bound;claim=_claim(plan)
    if boundary!='claim':g.claim_effect(claim)
    for pin in c['runtime'].store.retained_states():c['runtime'].store.release_retained_state(pin)
    with pytest.raises(ValueError,match='REQUIRED_BEFORE_DISPATCH'):
        if boundary=='claim':g.claim_effect(claim)
        elif boundary=='mark_started':g.mark_effect_started(claim.effect_id,started_at_ms=1701)
        else:g.acquire_dispatch_permit(effect_id=claim.effect_id,attempt=1,expected_fence_epoch=0,
            nonce_sha256='a'*64,now_ms=1701)
    record=g.get_effect(claim.effect_id)
    assert record is None if boundary=='claim' else record.state=='CLAIMED'
    assert not c['runtime'].store.retained_states()  # Dispatch may not auto-repair admission.
    assert g.action_fence_status()['inflight_count']==0


def test_second_gateway_connection_cannot_bypass_required_source_configuration(bound):
    c,g,plan,_=bound
    other=GatewayStateStore.open(g.path,now_ms=1700)
    try:
        with pytest.raises(StoreConflictError,match='NOT_CONFIGURED'):other.claim_effect(_claim(plan))
        other.configure_method_source_lifecycle(MethodRunSourceResolver(other,c['runtime']))
        assert other.claim_effect(_claim(plan))[1]
    finally:other.close()


def test_archive_drift_after_admission_blocks_start(bound):
    c,g,plan,_=bound;claim=_claim(plan);g.claim_effect(claim)
    pin=c['runtime'].store.retained_states()[0]
    from world_understanding.skill_method_world.publication import method_marker, ARCHIVE_WATERMARK
    state=c['runtime'].store.get(pin.state_ref.record_id)
    path=c['archive']/(method_marker(state,ARCHIVE_WATERMARK)+'.json')
    raw=path.read_bytes();path.chmod(0o644);path.write_bytes(raw+b' ');path.chmod(0o444)
    with pytest.raises(ValueError):g.mark_effect_started(claim.effect_id,started_at_ms=1701)
    assert g.get_effect(claim.effect_id).state=='CLAIMED'


@pytest.mark.parametrize('terminal',['cancel','release'])
def test_terminal_nested_rollback_does_not_release_live_pin(bound,terminal):
    c,g,plan,_=bound;pins=c['runtime'].store.retained_states()
    with pytest.raises(RuntimeError):
        with g._lock,g._write_transaction():
            if terminal=='cancel':g.cancel_generation(plan.request_id,reason_code='test.stop',cancelled_at_ms=2000)
            else:g.release_generation(plan.request_id,released_at_ms=2000)
            assert c['runtime'].store.retained_states()==pins
            raise RuntimeError('terminal rollback')
    assert g.get_generation(plan.request_id).status=='ACTIVE'
    assert c['runtime'].store.retained_states()==pins


def test_post_commit_cleanup_error_keeps_terminal_result_and_reports_debt(bound,monkeypatch):
    c,g,plan,_=bound;pins=c['runtime'].store.retained_states()
    def failed():raise OSError('cannot persist release')
    with monkeypatch.context() as m:
        m.setattr(c['runtime'].store,'_persist_index',failed)
        result=g.cancel_generation(plan.request_id,reason_code='test.stop',cancelled_at_ms=2000)
    assert result.status=='CANCELLED' and g.get_generation(plan.request_id).status=='CANCELLED'
    assert c['runtime'].store.retained_states()==pins
    assert g.method_source_retention_status()['cleanup_error']=='METHOD_RETENTION_CLEANUP:OSError'
    g.release_generation(plan.request_id,released_at_ms=2001)  # Existing idempotent terminal retry.
    assert not c['runtime'].store.retained_states()
    assert g.method_source_retention_status()['cleanup_error'] is None


def test_cancellation_retains_inflight_pin_until_effect_fact_commits(bound):
    c,g,plan,_=bound;claim=_claim(plan);g.claim_effect(claim)
    g.mark_effect_started(claim.effect_id,started_at_ms=1701)
    g.cancel_generation(plan.request_id,reason_code='test.stop',cancelled_at_ms=2000)
    assert c['runtime'].store.retained_states()
    g.complete_effect(_terminal(claim))
    assert not c['runtime'].store.retained_states()


def test_ambiguous_effect_does_not_become_safe_to_collect_on_cancel(bound):
    c,g,plan,_=bound;claim=_claim(plan);g.claim_effect(claim)
    g.mark_effect_started(claim.effect_id,started_at_ms=1701)
    g.cancel_generation(plan.request_id,reason_code='test.stop',cancelled_at_ms=2000)
    g.complete_effect(_terminal(claim,status='AMBIGUOUS'))
    assert c['runtime'].store.retained_states()


def test_registration_does_not_acquire_runtime_lock_under_gateway_lock(unregistered_bound):
    c,g,material,_=_install(unregistered_bound)
    # The publication observer can hold Runtime while requesting Gateway. The
    # registration path must therefore need only WorldStore, never Runtime.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with c['runtime']._lock:
            task=pool.submit(_persist_executable,g,material)
            assert task.result(timeout=10).created_by_this_call


def test_native_sources_require_retention_even_with_nonconcrete_world_reference(bound):
    _,_,plan,_=bound
    from types import SimpleNamespace
    from tests.test_method_source_review_p9 import _source
    _,method=_source()
    assert requires_method_retention(SimpleNamespace(legacy_plan=SimpleNamespace(
        method_source_refs=(method.source_ref,),world_state_ref='world.current')),configured=False)
    assert requires_method_retention(plan,configured=False)


def test_configuration_change_or_install_during_transaction_is_rejected(bound):
    c,g,_,reader=bound
    with g._lock,g._write_transaction():
        with pytest.raises(StoreConflictError,match='NOT_QUIESCENT'):g.configure_method_source_lifecycle(reader)
    other=GatewayStateStore.open(c['tmp_path']/'other.sqlite',now_ms=1000)
    try:
        with pytest.raises(TypeError):g.configure_method_source_lifecycle(MethodRunSourceResolver(other,c['runtime']))
    finally:other.close()


def test_commit_ack_loss_does_not_remove_an_actually_committed_plan_pin(unregistered_bound):
    c,g,material,_=_install(unregistered_bound)
    class LostAck(_CommitFault):
        def execute(self,sql,parameters=()):
            result=self.connection.execute(sql,parameters)
            if sql=='COMMIT':raise sqlite3.OperationalError('lost COMMIT acknowledgement')
            return result
    connection=g._connection;g._connection=LostAck(connection)
    try:
        with pytest.raises(sqlite3.OperationalError):_persist_executable(g,material)
    finally:g._connection=connection
    assert _rows(g)['composition_executable_plan']==1
    assert len(c['runtime'].store.retained_states())==1
    result=_persist_executable(g,material)
    assert result.duplicate
    g.claim_effect(_claim(result.record.executable_plan))


@pytest.mark.parametrize('boundary',['before_pin','after_pin_before_commit','after_commit'])
def test_real_process_exit_at_two_store_boundaries_is_fail_closed(tmp_path,boundary):
    import subprocess,sys,os
    work=tmp_path/'crash';work.mkdir()
    # The child uses real test Git, SQLite and the real World index. Only the
    # abrupt process-exit location is injected; no completion flag is forged.
    code=r'''
import json,os,sys
from pathlib import Path
import pytest
from tests.test_method_run_retention_p9 import unregistered_bound
from tests.test_composition_executable_plan_p7c0 import _persist_executable
root=Path(sys.argv[1]);stage=sys.argv[2]
mp=pytest.MonkeyPatch()
fixture=unregistered_bound.__wrapped__(root,mp)
c,g,m,r=next(fixture)
g.configure_method_source_lifecycle(r)
state=c['runtime'].store.current_candidates(life_id='life.main',principal_scope_hash=m['context'].principal_scope_hash)[0]
meta=dict(gateway=str(g.path),state=str(c['tmp_path']/'state'),archive=str(c['archive']),repo=str(c['repo']),commit=c['commit'],
    request=m['context'].request_id,run=m['context'].run_id,generation=m['context'].generation,
    scope=state.state.scope.model_dump(mode='json'),bootstrap=c['resolver'].bootstrap_snapshot_sha256,
    reviewer=c['resolver'].trusted_reviewer_public_key.hex(),observer=c['resolver'].trusted_observer_public_key.hex(),
    source_snapshot=c['resolver'].load(state).snapshot_sha256)
(root/'restart.json').write_text(json.dumps(meta))
real=c['runtime'].store.retain_state
def retain(pin):
    if stage=='before_pin':os._exit(71)
    real(pin)
    if stage=='after_pin_before_commit':os._exit(72)
c['runtime'].store.retain_state=retain
connection=g._connection
class AfterCommit:
    def __getattr__(self,n):return getattr(connection,n)
    def execute(self,sql,parameters=()):
        result=connection.execute(sql,parameters)
        if sql=='COMMIT':os._exit(73)
        return result
if stage=='after_commit':g._connection=AfterCommit()
_persist_executable(g,m)
raise AssertionError('crash boundary was not crossed')
'''
    root=Path(__file__).parents[1]
    result=subprocess.run([sys.executable,'-c',code,str(work),boundary],cwd=root,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(root),str(root/'src'),str(root/'app/backend/tiangong-backend')])},
        text=True,capture_output=True,timeout=40)
    assert result.returncode=={'before_pin':71,'after_pin_before_commit':72,'after_commit':73}[boundary],result.stderr
    meta=json.loads((work/'restart.json').read_text())
    from total_gateway.method_source_publication import MethodPublicationResolver
    from world_understanding.production import ProductionWorldUnderstandingRuntime
    from contracts.world_understanding.scope import WorldScope
    publication=MethodPublicationResolver(Path(meta['archive']),Path(meta['repo']),'repo.fixture','worktree.fixture',meta['commit'],
        meta['bootstrap'],bytes.fromhex(meta['reviewer']),bytes.fromhex(meta['observer']),lambda:9999)
    world=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=meta['state']),frame_factory=lambda *a:None,
                                              method_revision_resolver=publication)
    gateway=GatewayStateStore.open(Path(meta['gateway']),now_ms=1700)
    try:
        reader=MethodRunSourceResolver(gateway,world)
        gateway.configure_method_source_lifecycle(reader)
        plan=gateway.get_executable_composition_plan_for_request(meta['request'],run_id=meta['run'],generation=meta['generation'])
        pins=world.store.retained_states()
        assert bool(pins)==(boundary!='before_pin')
        if boundary=='after_commit':
            assert plan is not None
            methods=reader.read(request_id=meta['request'],run_id=meta['run'],generation=meta['generation'],
                                scope=WorldScope.model_validate_json(json.dumps(meta['scope'])))
            assert methods.snapshot_sha256==meta['source_snapshot']
            gateway.claim_effect(_claim(plan.executable_plan))
        else:
            assert plan is None
            with pytest.raises(ValueError,match='REGISTERED_PLAN_REQUIRED'):
                reader.read(request_id=meta['request'],run_id=meta['run'],generation=meta['generation'],
                            scope=WorldScope.model_validate_json(json.dumps(meta['scope'])))
            if boundary=='after_pin_before_commit':
                assert gateway.method_source_retention_status()['cleanup_error'] is not None
                assert world.store.retained_states()==pins  # Unknown orphan != completion.
    finally:gateway.close()


def test_new_generation_requires_its_own_retention_before_dispatch(bound):
    c,g,plan,reader=bound;old_pins=c['runtime'].store.retained_states()
    lease=g.get_generation(plan.request_id)
    g.acquire_generation_lease(request_id=plan.request_id,run_id=plan.run_id,
        run_sequence=lease.run_sequence,generation=lease.generation+1,gateway_epoch=lease.gateway_epoch,
        lease_id='p9.next.lease',owner_instance_id='p9.next.worker',issued_at_ms=20000,lease_duration_ms=10000)
    assert not c['runtime'].store.retained_states()
    with pytest.raises(ValueError,match='GENERATION_NOT_ACTIVE'):
        reader.require_retained_plan(plan)
    assert not c['runtime'].store.retained_states()


def test_capacity_does_not_evict_another_live_reference(unregistered_bound):
    c,g,material,_=_install(unregistered_bound)
    from world_understanding.world_state.retention import RetainedWorldState
    state=c['runtime'].store.current_candidates(life_id='life.main',principal_scope_hash=material['context'].principal_scope_hash)[0]
    old=RetainedWorldState('unrelated:owner',state.state_ref,state.state.scope)
    c['runtime'].store.retain_state(old);c['runtime'].store.max_retained_states=1
    before=_rows(g)
    with pytest.raises(ValueError,match='WORLD_RETENTION_FULL'):_persist_executable(g,material)
    assert _rows(g)==before
    assert c['runtime'].store.retained_states()==(old,)


def test_volatile_world_store_cannot_be_used_for_durable_admission(tmp_path):
    from world_understanding.production import ProductionWorldUnderstandingRuntime
    world=ProductionWorldUnderstandingRuntime(store=WorldStateStore(),frame_factory=lambda *a:None)
    gateway=GatewayStateStore.open(tmp_path/'gateway.sqlite',now_ms=1000)
    try:
        with pytest.raises(ValueError,match='PERSISTENT_WORLD_REQUIRED'):
            gateway.configure_method_source_lifecycle(MethodRunSourceResolver(gateway,world))
        assert not gateway.method_source_retention_status()['configured']
    finally:gateway.close()


def test_task_read_does_not_recreate_missing_admission_pin(bound):
    c,g,plan,reader=bound
    for pin in c['runtime'].store.retained_states():c['runtime'].store.release_retained_state(pin)
    with pytest.raises(ValueError,match='REQUIRED_BEFORE_READ'):_read(reader,plan)
    assert not c['runtime'].store.retained_states()


def test_two_connection_cancel_cannot_commit_between_task_verification_and_read(bound,monkeypatch):
    c,g,plan,reader=bound
    other=GatewayStateStore.open(g.path,now_ms=1700)
    other.configure_method_source_lifecycle(MethodRunSourceResolver(other,c['runtime']))
    entered=threading.Event();allow=threading.Event();cancel_done=threading.Event()
    original=c['runtime'].method_world_for_state
    def slow(*a,**kw):
        entered.set()
        assert allow.wait(10)
        return original(*a,**kw)
    monkeypatch.setattr(c['runtime'],'method_world_for_state',slow)
    def cancel():
        result=other.cancel_generation(plan.request_id,reason_code='test.stop',cancelled_at_ms=2000)
        cancel_done.set();return result
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reading=pool.submit(_read,reader,plan)
            assert entered.wait(10)
            cancelling=pool.submit(cancel)
            try:
                assert not cancel_done.wait(0.1)
            finally:allow.set()
            assert reading.result(timeout=10).primitives
            assert cancelling.result(timeout=10).status=='CANCELLED'
        assert not c['runtime'].store.retained_states()
    finally:other.close()


def test_known_rollback_cleanup_serializes_against_second_connection_registration(unregistered_bound,monkeypatch):
    c,g,material,reader=_install(unregistered_bound)
    # Retain one known transaction's pin, but inject interruption of its GC.
    with monkeypatch.context() as m:
        m.setattr(MethodRunSourceResolver,'release_rolled_back_admissions',lambda *a:None)
        with pytest.raises(RuntimeError):
            with g._lock,g._write_transaction():
                _persist_executable(g,material)
                raise RuntimeError('known registration rollback')
    pins=c['runtime'].store.retained_states()
    assert len(pins)==1 and _rows(g)['composition_executable_plan']==0
    other=GatewayStateStore.open(g.path,now_ms=1700)
    other.configure_method_source_lifecycle(MethodRunSourceResolver(other,c['runtime']))
    checked=threading.Event();allow=threading.Event();registered=threading.Event()
    real=g.get_executable_composition_plan_record
    def pause_after_absence(plan_id):
        result=real(plan_id)
        assert result is None
        checked.set()
        assert allow.wait(10)
        return result
    monkeypatch.setattr(g,'get_executable_composition_plan_record',pause_after_absence)
    def register():
        result=_persist_executable(other,material)
        registered.set();return result
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            cleanup=pool.submit(reader.release_rolled_back_admissions,pins)
            assert checked.wait(10)
            admission=pool.submit(register)
            try:
                assert not registered.wait(0.15)
            finally:allow.set()
            cleanup.result(timeout=10)
            plan=admission.result(timeout=10).record.executable_plan
        assert len(c['runtime'].store.retained_states())==1
        other.claim_effect(_claim(plan))
    finally:other.close()


@pytest.mark.parametrize('mutation',['effect','dynamic','wrong_plan','missing_pin'])
def test_extended_registration_guard_keeps_method_seam_non_authorizing(mutation):
    import ast
    from tests.test_composition_executable_plan_p7c0 import (
        _p7c0_store_tree, _p7c0_store_method, _assert_p7c0_register_bundle_is_persistence_only,
    )
    tree=_p7c0_store_tree()
    seam=_p7c0_store_method(tree,'_retain_method_sources_before_admission')
    if mutation=='effect':
        seam.body.append(ast.parse('self.claim_effect(plan)').body[0])
    elif mutation=='dynamic':
        seam.body.append(ast.parse('getattr(self, "claim_effect")(plan)').body[0])
    elif mutation=='wrong_plan':
        call=next(n for n in ast.walk(seam) if isinstance(n,ast.Call)
                  and isinstance(n.func,ast.Attribute) and n.func.attr=='retain_before_admission')
        call.args=[ast.Name(id='unreviewed_plan',ctx=ast.Load())]
    else:
        seam.body=[ast.Return(value=None)]
    with pytest.raises(AssertionError):
        _assert_p7c0_register_bundle_is_persistence_only(tree)
