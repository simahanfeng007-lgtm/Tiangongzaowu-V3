"""Actual installed source metadata -> UNKNOWN admission -> sealed registration.

No action metadata is made deterministic, no model or Body execution occurs.
"""
from dataclasses import replace
import json

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import (
    WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256,
    WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256,
)
from contracts.verification import AcceptancePredicate
from total_gateway.composition_admission_materialization import materialize_admission_inputs, materialize_workspace
from total_gateway.composition_profile_admission import (
    PROFILE_ADMISSION_CODE, require_profile_validation_binding, resolve_profile_validation,
)
from total_gateway.composition_registration_intake import CompositionRegistrationIntakeError, VerificationIntentEvidenceV1
from total_gateway.desktop_composition import bind_model_invocations
from world_understanding.capability_composition.validator import computed_validation_sha256
from tests.test_installed_composition_sources import installed, source_copy  # noqa: F401
from tests.test_desktop_composition_filesystem_bindings import _prepared
from tests.test_capability_composition_p4 import _proposal_document

PROFILE=dict(execution_profile_id=WORKSPACE_PYTHON_PROFILE_ID,execution_profile_sha256=WORKSPACE_PYTHON_PROFILE_SHA256)
WRITE=dict(execution_profile_id=WORKSPACE_WRITE_PROFILE_ID,execution_profile_sha256=WORKSPACE_WRITE_PROFILE_SHA256)
VERIFIERS=frozenset({'verification-intent:plan-bound-acceptance'})


def test_real_installed_uncertainty_is_preserved_and_registration_recomputes(installed,tmp_path):
    c=installed
    workspace=tmp_path/'controlled-workspace'
    workspace.mkdir()
    c.rc.workspace_id=materialize_workspace(workspace).workspace_id
    bridge,prepared=_prepared(c)
    actions={item.primitive.action_id:item for item in prepared.candidates.action_candidates}
    method=next(item for item in prepared.candidates.method_candidates if item.primitive.method_id=='acceptance_review')
    selected=('file.write','python.run')
    actual_primitives=tuple(actions[action].primitive for action in selected)
    before=tuple(item.model_dump(mode='json') for item in actual_primitives)
    assert all(item.idempotency=='UNKNOWN' and item.determinism_class=='NONDETERMINISTIC' for item in actual_primitives)
    calls=[('file.write','worker.py',{'content':"print('worker')\n"}),
           ('file.write','test_worker.py',{'content':"print('test fixture; not executed')\n"}),
           ('file.write','driver.py',{'content':"print('driver')\n"}),
           ('python.run','test_worker.py',{'argv':[],'timeout':30}),
           ('python.run','driver.py',{'argv':[],'timeout':30})]
    document=_proposal_document(goal_ref=prepared.context.goal_ref,methods=(method.candidate_id,),
        actions=tuple(sorted(actions[action].candidate_id for action in selected)),
        steps=tuple((f's{i}',actions[action].candidate_id,() if i==1 else (f's{i-1}',))
                    for i,(action,_,_) in enumerate(calls,1)),
        extra={'output_bindings':[f'final.s{i}' for i in range(1,6)]})
    result=bridge.compile_composition_for_turn(prepared,document,run_context=c.rc,
        tool_source=c.sources.tool_source,validated_at_ms=2300,available_verifiers=VERIFIERS)
    original=result.validation
    assert original.result=='UNKNOWN' and original.unknown_disposition=='REJECT'
    def resolve(profile=PROFILE,verifiers=VERIFIERS,source_result=result):
        return resolve_profile_validation(source_result.plan,source_result.parse_outcome.proposal,
            prepared.candidates,prepared.context,prepared.registry,available_verifiers=verifiers,
            validated_at_ms=2300,**profile)
    assert resolve({})==original
    assert resolve(WRITE).unknown_disposition=='REJECT'
    assert resolve(verifiers=frozenset()).unknown_disposition=='REJECT'
    with pytest.raises(ValueError,match='profile_invalid'):
        resolve({**PROFILE,'execution_profile_sha256':'0'*64})
    admitted=resolve()
    assert admitted.result=='UNKNOWN' and admitted.unknown_disposition=='PROVISIONAL_ALLOW'
    assert admitted.mandatory_verification
    assert tuple(item for item in admitted.findings if item.code!=PROFILE_ADMISSION_CODE)==original.findings
    marker=next(item for item in admitted.findings if item.code==PROFILE_ADMISSION_CODE)
    assert sum(item.code==PROFILE_ADMISSION_CODE for item in admitted.findings)==1
    require_profile_validation_binding(result.plan,admitted,**PROFILE)
    for profile in ({},WRITE,{**PROFILE,'execution_profile_sha256':'0'*64}):
        with pytest.raises(ValueError,match='binding_'):
            require_profile_validation_binding(result.plan,admitted,**profile)
    for findings in (original.findings,(*admitted.findings,marker),
                     tuple(item for item in admitted.findings if item!=original.findings[0]),
                     tuple(item if item!=marker else marker.model_copy(update={'detail_hash':'0'*64}) for item in admitted.findings)):
        forged=admitted.model_copy(update={'findings':findings})
        forged=forged.model_copy(update={'validation_sha256':computed_validation_sha256(forged)})
        with pytest.raises(ValueError,match='binding_'):
            require_profile_validation_binding(result.plan,forged,**PROFILE)
    # A profile never removes real parallel-write invalidity.
    parallel=json.loads(document)
    parallel['steps'][1]['depends_on']=[]
    parallel['dependency_edges']=[edge for edge in parallel['dependency_edges'] if edge[1]!='s2']
    rejected=bridge.compile_composition_for_turn(prepared,json.dumps(parallel),run_context=c.rc,
        tool_source=c.sources.tool_source,validated_at_ms=2300,available_verifiers=VERIFIERS)
    assert resolve(source_result=rejected).result=='PROVED_INVALID'
    assert 'validator.write_set.parallel_conflict' in {item.code for item in rejected.validation.findings}
    result=replace(result,validation=admitted)
    invocations={f's{i}':{'target':str(workspace/name),'args':args} for i,(_,name,args) in enumerate(calls,1)}
    steps,aliases=bind_model_invocations(result,invocations,schemas=c.authority.schema_catalog,
        workspace_root=workspace,**PROFILE)
    evidence=tuple(VerificationIntentEvidenceV1(intent_ref=intent,
        predicate=AcceptancePredicate.create(predicate_type='effect.terminal_succeeded',subject_kind='effect',params={}),
        subject_identity='composition-plan:'+result.plan.plan_id,evaluation_phase='POST_EXECUTION') for intent in result.plan.verification_intents)
    assert all(item.predicate.params == () for item in evidence)  # Canonical empty params, not a dict.
    from total_gateway.composition_admission_lifetime import composition_admission_lifetime_ms
    lifetime=composition_admission_lifetime_ms((step.action_id for step in result.plan.steps),**PROFILE)
    assert lifetime==330_000
    materialization=dict(workspace_root=workspace,user_inputs={},step_inputs=steps,
        final_outputs=aliases,intent_evidence=evidence,issued_at_ms=2400,**PROFILE)
    with pytest.raises(ValueError,match='window_invalid'):
        materialize_admission_inputs(result,expires_at_ms=2401+lifetime,**materialization)
    inputs=materialize_admission_inputs(result,expires_at_ms=2400+lifetime,**materialization)
    with pytest.raises(CompositionRegistrationIntakeError,match='lifetime_invalid'):
        c.sources.resolver.register_composition(result,**{**inputs,'expires_at_ms':2401+lifetime})
    # Self-consistent tampering must fail authoritative intake before registration.
    forged=admitted.model_copy(update={'findings':tuple(item for item in admitted.findings if item!=original.findings[0])})
    forged=forged.model_copy(update={'validation_sha256':computed_validation_sha256(forged)})
    with pytest.raises(CompositionRegistrationIntakeError,match='recompute_mismatch'):
        c.sources.resolver.register_composition(replace(result,validation=forged),**inputs)
    assert c.gateway.get_executable_composition_plan_for_request(c.rc.request_id,run_id=c.rc.run_id,generation=1) is None
    wrong_evidence=tuple(replace(item,subject_identity='composition-plan:wrong') for item in evidence)
    with pytest.raises(CompositionRegistrationIntakeError,match='profile_evidence_invalid'):
        c.sources.resolver.register_composition(result,**{**inputs,'intent_evidence':wrong_evidence})
    outcome=c.sources.resolver.register_composition(result,**inputs)
    assert outcome.registered and outcome.validation_mode=='PROVISIONAL_UNKNOWN'
    record=c.gateway.get_executable_composition_plan_for_request(c.rc.request_id,run_id=c.rc.run_id,generation=1)
    assert record.executable_plan.expires_at_ms==2400+lifetime
    assert all(step.execution_profile_id==WORKSPACE_PYTHON_PROFILE_ID
               and step.execution_profile_sha256==WORKSPACE_PYTHON_PROFILE_SHA256 for step in record.executable_plan.step_bindings)
    assert tuple(item.model_dump(mode='json') for item in actual_primitives)==before
    assert not list(workspace.iterdir())  # Registered eligibility is not execution.
