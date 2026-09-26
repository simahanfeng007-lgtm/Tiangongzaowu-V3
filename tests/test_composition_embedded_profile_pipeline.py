"""Real private Embedded route -> wrapper -> signed Body -> Facts/final proof.

No HTTP server or model is started: production's in-process request port is the
real private boundary. Only clocks/request identities and the host QA workspace
are supplied by the fixture. Tool/method metadata comes from actual source.
"""
from copy import deepcopy
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from tests.test_composition_workspace_pipeline import material_for_calls, PROFILE, assert_unavailable_python_execution
from tests.test_installed_composition_sources import source_copy  # noqa: F401
from tests import test_composition_grant_authority_p7c1 as p7c1
from tests import test_composition_grant_authority_p7d2 as p7d2
from total_gateway.composition_step_execution import CompositionStepExecutionCoordinator
from total_gateway.embedded_backend import EmbeddedBackendRuntime
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.service_ports import CompatibilityJsonClient


def _actual_worlds(loaded, *, store, envelope, request, run, workspace_root, source_root):
    from total_gateway.installed_composition_sources import InstalledCompositionSources
    from world_understanding.production import ProductionWorldUnderstandingRuntime
    from world_understanding.world_state import WorldStateStore
    from v3.world_understanding_production import _frame_factory
    from tests import test_composition_executable_plan_p7c0 as p7c0
    runtime=ProductionWorldUnderstandingRuntime(store=WorldStateStore(root=workspace_root/'world'),
        frame_factory=_frame_factory)
    installed=InstalledCompositionSources.install(runtime=runtime, gateway=store,
        source_root=source_root, archive_root=workspace_root/'installed-sources',
        registry=loaded.registry, manifest=loaded.manifest)
    context=SimpleNamespace(request_id=request.request_id, run_id=run.run_id, generation=1,
        life_id='life_p7c1', principal_scope_hash=envelope.principal_scope_hash,
        workspace_id=p7c0._workspace(workspace_root).workspace_id)
    state=installed.ensure_world(context, now_ms=1240)
    tools,_=installed.tool_source.load(loaded.registry)
    methods=runtime.method_world_for_state(state.state_ref, scope=state.state.scope)
    assert all(item.idempotency=='UNKNOWN' and item.determinism_class=='NONDETERMINISTIC'
               for item in tools.primitives if item.action_id in {'code.write','python.run'})
    return tools,methods,state


def test_actual_signed_private_entry_writes_runs_and_finalizes(tmp_path,monkeypatch,source_copy):
    from v3 import execution_integrity
    from tests import test_composition_activation_store_p7b2 as lineage_fixture
    root=tmp_path/'workspace'
    root.mkdir()
    source=Path(__file__).resolve().parents[1]
    monkeypatch.setenv('TIANGONG_OMNI_BODY_ROOT',str(source/'src/omni_body_skill'))
    monkeypatch.setenv('TIANGONG_DICTIONARY_ROOT',str(source/'dictionaries'))
    monkeypatch.setenv('TIANGONG_FORCE_WORKSPACE_ROOT',str(root))
    monkeypatch.setenv('TIANGONG_OMNI_BODY_STATE_ROOT',str(tmp_path/'body-state'))
    envelope_type=lineage_fixture.InboundEnvelope
    text='请写入 worker.py 和 test_worker.py，实际运行三项单元测试并运行脚本，保留产物 answer.txt。'
    monkeypatch.setattr(lineage_fixture,'InboundEnvelope',lambda **values:envelope_type(**(values|{'text':text})))
    code="from pathlib import Path\nPath('answer.txt').write_text('42',encoding='utf-8')\nprint('computed 42')\n"
    tests="import unittest\nclass RealChecks(unittest.TestCase):\n def test_a(self): self.assertEqual(2+2,4)\n def test_b(self): self.assertEqual(6*7,42)\n def test_c(self): self.assertTrue(True)\nif __name__=='__main__': unittest.main()\n"
    driver="import subprocess,sys\nfrom pathlib import Path\nresult=subprocess.run([sys.executable,'worker.py'],capture_output=True,text=True)\nprint(result.stdout,end='')\nprint(result.stderr,end='',file=sys.stderr)\nif result.returncode: raise SystemExit(result.returncode)\nPath('README.md').write_text('worker.py exit: '+str(result.returncode),encoding='utf-8')\n"
    calls=[('code.write',root/'worker.py',{'content':code,'syntax_check':False}),
           ('code.write',root/'test_worker.py',{'content':tests,'syntax_check':False}),
           ('code.write',root/'driver.py',{'content':driver,'syntax_check':False}),
           ('python.run',root/'test_worker.py',{'argv':[],'timeout':30}),
           ('python.run',root/'driver.py',{'argv':[],'timeout':30}),
           ('file.read',root/'README.md',{'encoding':'utf-8','max_chars':20000}),
           ('file.read',root/'answer.txt',{})]
    from total_gateway.composition_admission_lifetime import composition_admission_lifetime_ms
    lifetime=composition_admission_lifetime_ms((action for action,_,_ in calls),**PROFILE)
    assert lifetime==390_000  # Actual 712c shape: three writes, two runs, two reads.
    monkeypatch.setattr(p7c1,'_production_material',material_for_calls(
        calls,source_worlds=partial(_actual_worlds, source_root=source_copy),admission_lifetime_ms=lifetime))
    clock=SimpleNamespace(value=1800)
    monkeypatch.setattr(time,'time_ns',lambda:clock.value*1_000_000)
    from v3.jineng import jirou_ceng as muscle
    real_runner=muscle._run_omni_body_tool
    receipts=[]
    def observe(payload):
        value=real_runner(payload)
        receipts.append(value)
        return value
    monkeypatch.setattr(muscle,'_run_omni_body_tool',observe)
    with p7c1._harness(root,multi_step=True,complete_parent_effect=False,outer_expires_at_ms=61000,
                      authority_expires_at_ms=61400,plan_expires_at_ms=1500+lifetime) as harness:
        manifest=p7d2._execution_manifest(harness)
        harness.authority.composition_capability_manifest_hash=manifest.sha256
        delegation=p7d2._seal(harness)
        original_delegation=harness.store.get_composition_continuation_delegation(delegation)
        assert original_delegation.expires_at_ms==1500+lifetime
        p7d2._finish_parent_with_fact(harness)
        backend=EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
        backend._lock=threading.RLock(); backend._closed=False; backend._closing=False
        backend.qiaojie=SimpleNamespace(_core_execution_lock=threading.RLock())
        backend.scheduler=SimpleNamespace()
        class ObservedClient(CompatibilityJsonClient):
            sent=[]
            def request(self,method,path,payload,**kwargs):
                self.sent.append((method,path,deepcopy(payload)))
                return super().request(method,path,payload,**kwargs)
        client=ObservedClient(backend)
        generation=harness.store.get_generation(harness.plan.request_id)
        worker=object.__new__(GatewayOrchestrationWorker)
        worker._store=harness.store; worker._epoch=1; worker._instance_id=generation.owner_instance_id
        worker._authority=SimpleNamespace(execution_trust_bundle=lambda **_:harness.trust)
        client.set_composition_dispatch_authorizer(worker._authorize_composition_handler_entry)
        coordinator=CompositionStepExecutionCoordinator(store=harness.store,objects=harness.objects,facts=harness.facts,
            registry=harness.loaded.registry,schema_catalog=harness.loaded.schema_catalog,capability_manifest=manifest,
            trust_bundle_provider=lambda _:harness.trust,backend_compat_client=client,workspace_root=root,
            gateway_epoch=1,gateway_instance_id=generation.owner_instance_id,append_effect_event=lambda *_a,**_k:True)
        for i,step in enumerate(harness.plan.step_bindings):
            # Native operations still run. Only the authority clock advances:
            # reads occur after 60 seconds and after the admission parent expires.
            clock.value=1800+i*15000
            # The live worker supplies a generation heartbeat independently of
            # the sealed plan deadline; do the same through the real Store API.
            harness.store.heartbeat_generation_lease(harness.plan.request_id,
                lease_id=generation.lease_id,owner_instance_id=generation.owner_instance_id,
                now_ms=clock.value,lease_duration_ms=30_000)
            harness.authority.issue_composition_continuation_step(continuation_delegation_id=delegation,
                registration_id=harness.plan.registration_id,step_id=step.step_id,now_ms=clock.value)
            record=harness.store.get_current_composition_step_authorization(harness.plan.executable_plan_id,step.step_id)
            assert 0 < record.request.expires_at_ms-record.request.issued_at_ms <= 60_000
            assert record.request.expires_at_ms <= original_delegation.expires_at_ms
            if step.action_id=='file.read':
                assert clock.value > harness.outer.payload.expires_at_ms
            outcome=coordinator.dispatch_record(record,now_ms=clock.value)
            # The same signed request cannot cross the real private entry twice.
            count=len(receipts)
            method,path,wire=client.sent[-1]
            status,_,_=backend.request(method,path,wire)
            assert status>=400 and len(receipts)==count
            if os.name!='nt' and step.action_id=='python.run':
                assert len(receipts)==i+1==4
                assert_unavailable_python_execution(harness,coordinator,outcome,step,receipts[-1])
                assert (root/'worker.py').read_text('utf-8')==code
                assert (root/'test_worker.py').read_text('utf-8')==tests
                assert (root/'driver.py').read_text('utf-8')==driver
                assert not (root/'answer.txt').exists()
                assert not (root/'README.md').exists()
                assert harness.store.get_composition_continuation_delegation(delegation)==original_delegation
                return
            assert outcome.status=='SUCCEEDED',json.dumps({'step':step.step_id,'receipts':receipts[-1:]},ensure_ascii=False)
            fact=harness.facts.get_batch_for_effect(outcome.effect_id,verify_payload=True)
            assert fact is not None
        assert len(receipts)==7 and (root/'answer.txt').read_text('utf-8')=='42'
        runs=[item for item in receipts if item.get('action')=='python.run']
        assert len(runs)==2
        for receipt in runs:
            execution=receipt['result']['execution']
            assert execution['returncode']==0 and execution['execution_state']=='completed'
            assert execution['commit_state']=='committed' and execution['containment']=='windows-appcontainer'
            assert execution['network']=='denied'
        assert 'Ran 3 tests' in runs[0]['result']['execution']['stderr']
        assert 'OK' in runs[0]['result']['execution']['stderr']
        final=coordinator.finalize_plan(harness.plan)
        proof=final.execution_requirements_attestation
        # The retired prose floor cannot issue semantic approval. Actual signed
        # steps, process results, files and Fact lineage remain required above.
        assert proof['execution_requirements_verified'] is False
        assert proof['business_outcome_verified'] is False
        assert proof['obligations_count']==0
        assert proof['required_outputs']==proof['output_witnesses']==[]
        assert proof['supporting_fact_ids']==list(final.fact_ids)
        assert proof['request_id']==harness.plan.request_id
        assert proof['executable_plan_id']==harness.plan.executable_plan_id
        assert len(final.fact_ids)==7 and set(final.final_output_aliases)=={f'final.{i:02}' for i in range(1,8)}
        assert harness.store.get_composition_continuation_delegation(delegation)==original_delegation
        from total_gateway.store import StoreConflictError
        with pytest.raises(StoreConflictError,match='not live'):
            harness.store.get_composition_continuation_for_plan(harness.plan.executable_plan_id,
                now_ms=original_delegation.expires_at_ms,require_parent_success=True)
        assert len(receipts)==7  # Deadline rejection has no handler side effects.
