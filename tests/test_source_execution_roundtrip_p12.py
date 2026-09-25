"""P12 R1C4: a registered Source plan executes through the original chain.

The planning/registration world is the real immutable Git/bundle and signed
Method archive fixture from R1C2/R1C3. Execution enters through the ORIGINAL
OmniGrantAuthority (policy/ticket/grant) and CompositionStepExecutionCoordinator
with the real Gateway Store, ObjectStore and FactLedger. Only the embedded
backend response is a deterministic in-process fixture, so a passing result is
chain and state-machine evidence, NOT live model or production acceptance
(those belong to the formal P11 matrix).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import canonical_sha256
from total_gateway.action_registry import compile_action_authority
from total_gateway.capability_manifest import (
    compile_composition_execution_manifest,
    load_model_capability_manifest,
)
from total_gateway.composition_step_execution import (
    CompositionStepExecutionCoordinator,
)
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.omni_grant_authority import (
    OmniGrantAuthority,
    PolicyEvidenceLedger,
    TicketSigner,
)
from tests.test_composition_grant_authority_p7c1 import (
    COMPONENT_MANIFEST_SHA256,
    SKILL_CATALOG_SHA256,
    _trust_bundle,
    execution_ticket,
)
from tests.test_execution_contracts import capability_manifest
from tests.test_gateway_worker_composition_resume_p7d2 import _envelope
from contracts import AttachmentRef
from tests.test_source_registration_intake_p12 import (  # noqa: F401  fixtures/helpers
    intake,
    intake_factory,
    source as registration_source,
    _admission_inputs,
    _compile,
    _prepare,
    _register,
    _workspace_binding,
)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401  fixture



@pytest.fixture
def source(tmp_path):
    root = registration_source.__wrapped__(tmp_path)
    (root / "src/omni_body_skill/fixture-action.txt").write_text("file.list", encoding="utf-8")
    return root


def _noarg_inputs(c, result):
    """Admission inputs for the current file.list atomic contract."""
    from tests.test_source_registration_intake_p12 import (
        _admission_inputs, _hashed)
    from total_gateway.composition_executable_plan import (
        OutputDeclarationV1, StepExecutionBindingV1,
        StepOutputValueBindingV1, FinalOutputAliasV1)
    candidate = result.preparation.candidates.action_candidates[0]
    primitive = candidate.primitive
    permission = next(item for item in result.preparation.registry.permissions
                      if item.action_id == primitive.action_id)
    import zipfile
    with zipfile.ZipFile(c['tool_source'].bundle_path) as bundle:
        manifest = json.loads(bundle.read('build-report.json'))['build_artifact']['gateway_manifest']
    schema = compile_action_authority(manifest, generated_at_ms=0).schema_catalog.resolve(
        primitive.action_id, permission.action_version)
    entries = next(item for item in schema.value_schemas if item.value_schema_id == 'entries')
    output = _hashed(OutputDeclarationV1(
        output_binding_id=result.parse_outcome.proposal.steps[0].output_bindings[0],
        source_kind='RESULT_PAYLOAD', json_pointer=entries.json_pointer,
        value_schema_sha256=entries.value_schema_sha256, sha256='0' * 64))
    step = _hashed(StepExecutionBindingV1(
        step_id=result.plan.steps[0].step_id,
        candidate_id=result.parse_outcome.proposal.steps[0].candidate_id,
        candidate_binding_sha256=candidate.binding_sha256,
        action_id=result.plan.steps[0].action_id,
        action_version=result.plan.steps[0].action_version,
        source_revision=candidate.source_revision,
        argument_schema_sha256=primitive.argument_schema_sha256,
        result_schema_sha256=primitive.result_schema_sha256,
        permission=permission, permission_sha256=permission.permission_sha256,
        depends_on=result.plan.steps[0].depends_on,
        target_skeleton=str(c['workspace_root']),
        args_skeleton={},
        output_declarations=(output,), sha256='0' * 64))
    final_reference = _hashed(StepOutputValueBindingV1(
        producer_step_id=step.step_id, output_binding_id=output.output_binding_id,
        output_declaration_sha256=output.sha256, sha256='0' * 64))
    final_alias = _hashed(FinalOutputAliasV1(
        alias=result.parse_outcome.proposal.output_bindings[0],
        value_binding=final_reference, sha256='0' * 64))
    base = _admission_inputs(c, result)
    base.update(plan_inputs=(), step_bindings=(step,),
                final_output_aliases=(final_alias,))
    return base


@pytest.fixture
def execution(intake_factory, tmp_path):
    """R1C3 registration (with an accepted attachment) plus execution powers."""
    objects = ContentAddressedObjectStore.open(
        tmp_path / 'execution-objects', now_ms=900)
    facts = FactLedger.open(
        tmp_path / 'execution-facts.sqlite3', objects, now_ms=900)
    probe_envelope = _envelope('registration')
    stored = objects.put_bytes(
        b'r1c4 sealed listing object',
        kind='attachment',
        tenant_id=probe_envelope.tenant_id,
        link_account_id=probe_envelope.link_account_id,
        conversation_scope_hash=probe_envelope.conversation_scope_hash,
        created_at_ms=900,
    ).reference
    attachment = AttachmentRef(
        object_id=stored.object_id, revision=1, sha256=stored.sha256,
        size_bytes=stored.size_bytes, mime='text/plain',
        filename='r1c4-listing.txt', tenant_id=stored.tenant_id,
        link_account_id=stored.link_account_id,
        conversation_scope_hash=stored.conversation_scope_hash,
        source_message_ref=probe_envelope.channel_message_ref, created_at_ms=900)
    world = intake_factory(attachments=(attachment,))
    c = next(world)
    try:
        yield from _run_execution(c, objects, facts, stored, attachment, tmp_path)
    finally:
        try:
            next(world, None)
        except StopIteration:
            pass


def _run_execution(c, objects, facts, stored, attachment, tmp_path):
    from contracts import ObjectGrant
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    object_grant = ObjectGrant(
        object_id=stored.object_id, revision=1, sha256=stored.sha256,
        size_bytes=stored.size_bytes, mime='text/plain',
        tenant_id=stored.tenant_id, link_account_id=stored.link_account_id,
        conversation_scope_hash=stored.conversation_scope_hash)
    outcome = _register(c, result, **_noarg_inputs(c, result))
    import zipfile
    with zipfile.ZipFile(c['tool_source'].bundle_path) as z:
        gateway_manifest = json.loads(z.read('build-report.json'))['build_artifact']['gateway_manifest']
    loaded = compile_action_authority(gateway_manifest, generated_at_ms=0)
    assert loaded.registry == c['registry']
    manifest_bytes = json.dumps(
        gateway_manifest, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    manifest_path = tmp_path / 'model-capability-manifest.json'
    manifest_path.write_bytes(manifest_bytes)
    model = load_model_capability_manifest(
        manifest_path,
        expected_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
        generated_at_ms=1_250,
    ).manifest
    exec_manifest = compile_composition_execution_manifest(
        model, c['registry'], loaded.schema_catalog, generated_at_ms=1_250)

    parent_manifest = capability_manifest()
    private = Ed25519PrivateKey.generate()
    signer = TicketSigner('p7c1_execution_key', private)
    trust = _trust_bundle(private)
    envelope = c['gateway'].get_request_envelope(result.preparation.context.request_id)
    parent_intent_sha256 = canonical_sha256({
        'domain': 'tiangong.test.composition-parent.v1',
        'executable_plan_id': outcome.executable_plan_id,
    })
    from total_gateway.composition_execution_binding import derive_run_sequence
    from contracts import derive_effect_identity
    from total_gateway.effects import EffectClaim
    run_sequence = derive_run_sequence(
        result.preparation.context.request_id, result.preparation.context.run_id)
    parent_effect_id = derive_effect_identity(
        request_id=result.preparation.context.request_id,
        run_id=result.preparation.context.run_id,
        run_sequence=run_sequence,
        generation=result.preparation.context.generation,
        effect_kind='execution', ordinal=0,
        intent_sha256=parent_intent_sha256).effect_id
    parent_claim = EffectClaim(
        effect_id=parent_effect_id,
        request_id=result.preparation.context.request_id,
        run_id=result.preparation.context.run_id,
        run_sequence=run_sequence,
        generation=result.preparation.context.generation,
        effect_kind='execution', ordinal=0,
        intent_sha256=parent_intent_sha256,
        pipeline_version='tiangong.test.composition-parent.v1',
        attempt=1, claim_revision=1, lease_epoch=1,
        supersedes_claim_sha256=None,
        owner_component_id='tiangong-total-gateway',
        claimed_at_ms=1500, claim_sha256='0' * 64,
    ).with_computed_sha256()
    outer = execution_ticket(
        manifest=parent_manifest,
        ticket_id='ticket_parent_r1c4',
        nonce='nonce_parent_r1c4',
        issued_at_ms=1000,
        not_before_ms=1000,
        expires_at_ms=5780,
        gateway_epoch=1,
        request_id=result.preparation.context.request_id,
        run_id=result.preparation.context.run_id,
        generation=result.preparation.context.generation,
        channel=envelope.channel,
        tenant_id=envelope.tenant_id,
        link_account_id=envelope.link_account_id,
        conversation_scope_hash=envelope.conversation_scope_hash,
        principal_scope_hash=result.plan.principal_scope_hash,
        capability_manifest_hash=parent_manifest.sha256,
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
        workspace_id=result.preparation.workspace_id,
        effect_id=parent_effect_id,
        claim_sha256=parent_claim.claim_sha256,
        claim_revision=parent_claim.claim_revision,
        claim_lease_epoch=parent_claim.lease_epoch,
        input_objects=(object_grant,),
        arguments_hash=canonical_sha256({'parent': 'composition-authority'}),
        max_output_bytes=1_000_000,
        max_runtime_ms=30_000,
        max_tool_calls=1,
    )
    outer = signer.sign_execution(outer.payload)
    c['gateway'].claim_effect(parent_claim)
    from tests.test_composition_step_execution_p7d1 import _seed_successful_parent
    _seed_successful_parent(
        SimpleNamespace(store=c['gateway'], trust=trust),
        facts, parent_manifest=parent_manifest,
        parent_ticket=outer, parent_claim=parent_claim)

    authority = OmniGrantAuthority(
        registry=c['registry'],
        action_schema_catalog=loaded.schema_catalog,
        capability_manifest_hash=parent_manifest.sha256,
        capability_source_manifest_hash=loaded.manifest_sha256,
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
        skill_catalog_hash=SKILL_CATALOG_SHA256,
        signer=signer,
        gateway_epoch=1,
        workspace_root=c['workspace_root'],
        evidence=PolicyEvidenceLedger(tmp_path / 'policy-evidence'),
        trust_bundle_provider=lambda _now_ms: trust,
        effect_store=c['gateway'],
        object_store=objects,
        fact_ledger=facts,
    )
    authority.register(
        outer,
        life_id='life_r1c4',
        life_evidence_ref='lev_' + 'a' * 64,
        session_id='session_r1c4',
        registered_at_ms=5325,
        authority_expires_at_ms=5780,
    )
    authority.composition_capability_manifest_hash = exec_manifest.sha256

    c.update(objects=objects, facts=facts, loaded=loaded,
             exec_manifest=exec_manifest, signer=signer, outer=outer,
             authority=authority, plan_result=result, admission=outcome,
             object_grant=object_grant, _trust=trust)
    yield c
    facts.close()
    objects.close()


class _BackendFixture:
    """Deterministic in-process backend response; explicitly NOT live I/O."""

    def __init__(self, store, facts, effect_id, action_id):
        self._store = store
        self._facts = facts
        self._effect_id = effect_id
        self._action_id = action_id
        self.calls = 0

    def request(self, method, path, payload, *, timeout_seconds,
                backend_started=False, before_request=None):
        target = payload["execute_ticket"]["arguments"]["target"]
        del method, path, timeout_seconds, backend_started, before_request
        self.calls += 1
        value = {
            'schema': 'tiangong.v3.omni_body.v1',
            'ok': True,
            'zhuangtai': 'wancheng',
            'gongju': 'omni_body',
            'action': self._action_id,
            'target': target,
            'result': {
                'success': True, 'op_id': 'r1c4-fixture',
                'action': self._action_id, 'risk_level': 'A0',
                'elapsed_seconds': 0, 'evidence': {},
                'root': target, 'count': 0, 'entries': [],
            },
            'llm_brief': 'R1C4 fixture listing',
            'evidence': {},
        }
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        return 200, value, hashlib.sha256(raw).hexdigest()


def _coordinator(c, backend, *, facts=None):
    return CompositionStepExecutionCoordinator(
        store=c['gateway'],
        objects=c['objects'],
        facts=c['facts'] if facts is None else facts,
        registry=c['registry'],
        schema_catalog=c['loaded'].schema_catalog,
        capability_manifest=c['exec_manifest'],
        trust_bundle_provider=lambda _now_ms: c['authority']._trust_bundle_provider(0) if False else _trust_bundle_from(c),
        backend_compat_client=backend,
        workspace_root=c['workspace_root'],
        gateway_epoch=1,
        gateway_instance_id=c['gateway'].get_generation(
            c['plan_result'].preparation.context.request_id).owner_instance_id,
    )


def _trust_bundle_from(c):
    return c['_trust']


def _authorize_and_dispatch(c, *, step_id='s1', now_ms=5350, backend=None):
    receipt = c['authority'].issue_composition_step(
        parent_ticket_id=c['outer'].payload.ticket_id,
        registration_id=c['admission'].registration_id,
        step_id=step_id,
        now_ms=now_ms,
    )
    record = c['gateway'].get_composition_step_authorization(
        c['admission'].executable_plan_id, step_id, now_ms=now_ms)
    assert record is not None
    effect_id = record.request.prebound_effect_id
    if backend is None:
        backend = _BackendFixture(
            c['gateway'], c['facts'], effect_id,
            c['plan_result'].plan.steps[0].action_id)
    coordinator = _coordinator(c, backend)
    outcome = coordinator.dispatch_record(record, now_ms=now_ms + 10)
    return receipt, record, outcome, backend


def test_registered_plan_executes_through_original_authority_to_fact(execution):
    """R1C3 admission -> issue_composition_step -> dispatch -> Effect/Fact."""
    c = execution
    # Only the seeded parent Effect exists; admission itself created none.
    assert c['gateway']._connection.execute(
        'SELECT COUNT(*) FROM effect_ledger').fetchone()[0] == 1
    receipt, record, outcome, backend = _authorize_and_dispatch(c)
    print('PROBE outcome status:', outcome.status, 'facts:', outcome.fact_ids)
    assert backend.calls == 1
    assert receipt['status'] == 'OK' and receipt['grant']
    effect = c['gateway'].get_effect(record.request.prebound_effect_id)
    assert effect.state in {'SUCCEEDED', 'COMPLETED'}
    batch = c['facts'].get_batch_for_effect(record.request.prebound_effect_id)
    assert batch is not None
    # Parent plus exactly one child Effect; admission itself created none.
    assert c['gateway']._connection.execute(
        'SELECT COUNT(*) FROM effect_ledger').fetchone()[0] == 2


def test_duplicate_dispatch_of_same_authorization_is_idempotent(execution):
    c = execution
    _receipt, record, _outcome, _backend = _authorize_and_dispatch(c)
    backend2 = _BackendFixture(
        c['gateway'], c['facts'], record.request.prebound_effect_id,
        c['plan_result'].plan.steps[0].action_id)
    coordinator = _coordinator(c, backend2)
    replay = coordinator.dispatch_record(record, now_ms=5400)
    assert backend2.calls == 0
    assert replay.effect_id == record.request.prebound_effect_id
    assert c['gateway']._connection.execute(
        'SELECT COUNT(*) FROM effect_ledger').fetchone()[0] == 2


def test_released_generation_refuses_execution(execution):
    c = execution
    receipt = c['authority'].issue_composition_step(
        parent_ticket_id=c['outer'].payload.ticket_id,
        registration_id=c['admission'].registration_id,
        step_id='s1',
        now_ms=5350,
    )
    assert receipt['status'] == 'OK'
    c['gateway'].release_generation(c['plan_result'].preparation.context.request_id,
                                    released_at_ms=5360)
    # The Store itself refuses to hand out the authorization for a released
    # generation; no dispatch and no backend call can even start.
    with pytest.raises(Exception,
                       match='not on the current generation'):
        c['gateway'].get_composition_step_authorization(
            c['admission'].executable_plan_id, 's1', now_ms=5370)


def test_completion_gate_completes_only_with_required_facts(execution):
    """Execution success reaches business Completion only through the Gate."""
    from total_gateway.completion_gate import CompletionGate, CompletionRequirements
    c = execution
    _receipt, record, outcome, _backend = _authorize_and_dispatch(c)
    assert outcome.status == 'SUCCEEDED'
    gate = CompletionGate(c['objects'], c['facts'],
                          head_state_reader=c['gateway'].get_effect_head_state)
    requirements = CompletionRequirements(
        request_id=record.request.request_id,
        run_id=record.request.run_id,
        generation=record.request.generation,
        required_execution_effect_ids=(record.request.prebound_effect_id,),
    )
    decision = gate.evaluate(requirements)
    assert decision.outcome == 'COMPLETED'
    assert decision.execution_ready and decision.can_transition_request_completed
    # A required Effect without its durable Fact cannot complete: the same
    # requirement pointed at the seeded parent-only world fails closed.
    missing = CompletionRequirements(
        request_id=record.request.request_id,
        run_id=record.request.run_id,
        generation=record.request.generation,
        required_execution_effect_ids=tuple(sorted({
            record.request.prebound_effect_id, 'eff_' + 'e' * 64})),
    )
    refused = gate.evaluate(missing)
    assert refused.outcome != 'COMPLETED'
    assert not refused.can_transition_request_completed


class _FailingBackend:
    """Deterministic backend failure; explicitly NOT live I/O."""

    def __init__(self, action_id):
        self._action_id = action_id
        self.calls = 0

    def request(self, method, path, payload, *, timeout_seconds,
                backend_started=False, before_request=None):
        target = payload["execute_ticket"]["arguments"]["target"]
        del method, path, timeout_seconds, backend_started, before_request
        self.calls += 1
        value = {
            'schema': 'tiangong.v3.omni_body.v1', 'ok': False,
            'zhuangtai': 'shibai', 'gongju': 'omni_body',
            'action': self._action_id, 'target': '',
            'result': {'success': False}, 'llm_brief': 'R1C4 fixture failure',
            'evidence': {},
        }
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        return 200, value, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('commit_first', [False, True])
def test_fact_commit_crash_stays_started_and_reconciles_on_restart(
        execution, commit_first):
    """A Fact-boundary crash never replays; restart reconciles the exact fact.

    commit_first=False leaves no durable Fact, so recovery closes the Effect
    AMBIGUOUS; commit_first=True leaves the exact batch, so recovery completes
    it SUCCEEDED. Neither path calls the backend a second time.
    """
    from tests.test_composition_step_execution_p7d1 import _FactCrashProxy
    c = execution
    receipt = c['authority'].issue_composition_step(
        parent_ticket_id=c['outer'].payload.ticket_id,
        registration_id=c['admission'].registration_id,
        step_id='s1', now_ms=5350)
    assert receipt['status'] == 'OK'
    record = c['gateway'].get_composition_step_authorization(
        c['admission'].executable_plan_id, 's1', now_ms=5350)
    effect_id = record.request.prebound_effect_id
    crash_backend = _BackendFixture(
        c['gateway'], c['facts'], effect_id,
        c['plan_result'].plan.steps[0].action_id)
    crashed = _coordinator(c, crash_backend,
                           facts=_FactCrashProxy(c['facts'], commit_first=commit_first))
    from total_gateway.composition_step_execution import (
        CompositionStepExecutionError)
    with pytest.raises(CompositionStepExecutionError,
                       match='composition.execution.fact_commit_unknown'):
        crashed.dispatch_record(record, now_ms=5360)
    assert crash_backend.calls == 1
    assert c['gateway'].get_effect(effect_id).state == 'SIDE_EFFECT_STARTED'
    assert (c['facts'].get_batch_for_effect(effect_id) is not None) is commit_first

    # A fresh coordinator over the same durable authorities plays restart.
    restart_backend = _BackendFixture(
        c['gateway'], c['facts'], effect_id,
        c['plan_result'].plan.steps[0].action_id)
    restarted = _coordinator(c, restart_backend)
    outcomes = restarted.recover_started(now_ms=5400)
    assert len(outcomes) == 1
    assert outcomes[0].recovered is True
    assert outcomes[0].status == ('SUCCEEDED' if commit_first else 'AMBIGUOUS')
    assert crash_backend.calls == 1
    assert restart_backend.calls == 0
    assert c['gateway'].get_effect(effect_id).state == outcomes[0].status
    assert restarted.dispatch_next(now_ms=5410) is None


def test_backend_failure_is_final_and_never_completes(execution):
    """A failed action is a durable FAILED_FINAL Effect, never a Completion."""
    from total_gateway.completion_gate import CompletionGate, CompletionRequirements
    c = execution
    receipt = c['authority'].issue_composition_step(
        parent_ticket_id=c['outer'].payload.ticket_id,
        registration_id=c['admission'].registration_id,
        step_id='s1', now_ms=5350)
    assert receipt['status'] == 'OK'
    record = c['gateway'].get_composition_step_authorization(
        c['admission'].executable_plan_id, 's1', now_ms=5350)
    failing = _FailingBackend(c['plan_result'].plan.steps[0].action_id)
    coordinator = _coordinator(c, failing)
    outcome = coordinator.dispatch_record(record, now_ms=5360)
    assert failing.calls == 1
    assert outcome.status == 'FAILED_FINAL'
    effect = c['gateway'].get_effect(record.request.prebound_effect_id)
    assert effect.state == 'FAILED_FINAL'
    # The action's failure cannot become business Completion.
    gate = CompletionGate(c['objects'], c['facts'],
                          head_state_reader=c['gateway'].get_effect_head_state)
    decision = gate.evaluate(CompletionRequirements(
        request_id=record.request.request_id,
        run_id=record.request.run_id,
        generation=record.request.generation,
        required_execution_effect_ids=(record.request.prebound_effect_id,),
    ))
    assert decision.outcome == 'FAILED'
    assert decision.can_transition_request_completed is False
