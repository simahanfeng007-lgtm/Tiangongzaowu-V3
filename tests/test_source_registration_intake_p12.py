"""P12 R1C3: real Source->P4 results into the original P7 registration chain.

The immutable Git/bundle and signed Method archive fixtures are inherited from
the R1C2 suite; only the workspace identity is derived the way the
executable-plan contract requires. Fixtures are evidence, not live acceptance:
no candidate code is imported, no Action is dispatched, no Grant is issued and
no Completion is claimed by any admission in this module.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
import unicodedata
from types import SimpleNamespace

import pytest

from contracts import canonical_json_bytes, canonical_sha256, derive_run_identity
from contracts.world_understanding.time import WorldTime
from contracts.verification import AcceptancePredicate
from total_gateway.action_registry import compile_action_authority
from total_gateway.composition_executable_plan import (
    ArgumentSlotV1,
    FinalOutputAliasV1,
    OutputDeclarationV1,
    PlanInputV1,
    PlanInputValueBindingV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
    WorkspaceBindingV1,
)
from total_gateway.composition_registration_intake import (
    CompositionRegistrationIntakeError,
    VerificationIntentEvidenceV1,
)
from total_gateway.composition_source_preparation import PlanningToolSource
from total_gateway.method_source_publication import (
    MethodPublicationResolver,
    PUBLICATION_DOMAIN,
    build_method_publication_body,
    stage_method_publication,
    method_publication_envelope,
)
from total_gateway.method_source_run_binding import MethodRunSourceResolver
from total_gateway.store import GatewayStateStore
from total_gateway.verification_registry import VerifierRegistry
from world_understanding.capability_composition import (
    compile_capability_composition_plan,
    validate_capability_composition_plan,
)
from world_understanding.context_output import ContextOutputPort, WorldContextProjector, WorldContextRequestHandler
from world_understanding.domain_contribution import compile_tool_capability_contribution
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.skill_method_world.compiler import compile_native_method_source
from world_understanding.software_world import SoftwareWorldFrame, SparseWorldGraph
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import WorldStateStore, WorldStateMaterializer, MaterializationInput, materialize_one_world_state

from tests.test_method_source_review_p9 import _inputs, _source, _sign_report, _bind_review
from tests.test_skill_method_source_lifecycle_p9 import _snapshot
from tests.test_gateway_worker_composition_resume_p7d2 import _envelope
from tests.test_capability_composition_p4 import _proposal_document
from tests.test_tool_source_publication_p8 import publication  # noqa: F401  fixture

ZERO_SHA256 = "0" * 64
H = "a" * 64


def _hashed(value):
    return value.with_computed_sha256()


def _workspace_binding(root) -> WorkspaceBindingV1:
    resolved = str(root.resolve(strict=True))
    normalized = os.path.normcase(unicodedata.normalize("NFC", resolved))
    return _hashed(
        WorkspaceBindingV1(
            workspace_id="workspace-" + canonical_sha256(resolved),
            workspace_root=resolved,
            workspace_scope_sha256=canonical_sha256({"normalized_workspace": normalized}),
            sha256=ZERO_SHA256,
        )
    )


@pytest.fixture
def source(tmp_path):
    """The R1C2 source fixture: P8 tree plus signed native Method sources."""
    from tests.test_tool_source_bundle_p8 import source as original_tool_source
    root = original_tool_source.__wrapped__(tmp_path)
    policy = json.loads((root / 'source-ownership.json').read_bytes())
    policy['mappings'].append({'id': 'methods', 'source': 'src/world_understanding',
                               'source_role': 'authoritative', 'targets': []})
    (root / 'source-ownership.json').write_bytes(canonical_json_bytes(policy))
    for mid in ('native_0', 'native_1', 'acceptance_review', 'decompose_goal'):
        for version in ('v1', 'v2'):
            (_old_path, raw), _primitive = _source(mid, version)
            path = root / f'src/world_understanding/skill_method_world/sources/{mid}.{version}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    return root


@pytest.fixture
def intake_factory(source, publication, tmp_path, monkeypatch):
    """Build the R1C2 planning world with an authority-derived workspace id."""

    def build():
        from v3 import world_understanding_production as installed
        from v3.world_context_integration import WorldContextIntegration
        repo, base, head, package, marker = publication
        bundle, digest = package()
        import zipfile
        with zipfile.ZipFile(bundle) as z:
            artifact = json.loads(z.read('build-report.json'))['build_artifact']
        registry = compile_action_authority(artifact['gateway_manifest'], generated_at_ms=0).registry
        tool_source = PlanningToolSource(repo, bundle, digest, base, head, ('skill.list',),
                                         'src/omni_body_skill/tools/handler.py', 'repo.fixture', 'worktree.fixture')
        tools, _ = tool_source.load(registry)
        workspace_root = tmp_path / 'registered-workspace'
        workspace_root.mkdir()
        workspace_id = _workspace_binding(workspace_root).workspace_id
        gateway = GatewayStateStore.open(tmp_path / 'registered-gateway.sqlite3', now_ms=1000)
        inbound = _envelope('registration').model_copy(update={'text': '请用 native_0，查看 skill.list。'})
        registered = gateway.register_request(inbound, ingress_sha256='b' * 64, created_at_ms=1100)
        request = registered.entry.request_id
        run = derive_run_identity(request, 1).run_id
        gateway.acquire_generation_lease(request_id=request, run_id=run, run_sequence=1, generation=1,
                                         gateway_epoch=1, lease_id='lease.registration',
                                         owner_instance_id='gateway.registration', issued_at_ms=1200,
                                         lease_duration_ms=100000)
        rc = SimpleNamespace(request_id=request, run_id=run, generation=1, life_id='life.main',
                             principal_scope_hash=inbound.principal_scope_hash, workspace_id=workspace_id,
                             session_id=inbound.conversation_ref, conversation_id=inbound.conversation_ref)
        scope = installed._scope(installed._run_identity(rc))
        args, reviewer, observer = _inputs()
        archive = tmp_path / 'method-archives'
        archive.mkdir()
        method_resolver = MethodPublicationResolver(archive, repo, 'repo.fixture', 'worktree.fixture', base,
                                                    _snapshot().snapshot_sha256, reviewer.public_key().public_bytes_raw(),
                                                    observer.public_key().public_bytes_raw(), lambda: 30)

        def frame_factory(envelope, cut):
            return SoftwareWorldFrame.build(scope=envelope.scope_hint, workspace=rc.workspace_id,
                                            repository='repo.fixture', worktree='worktree.fixture', branch='main',
                                            commit=head, environment='test-env', time=envelope.source_time, world_cut=cut)
        store = WorldStateStore(root=tmp_path / 'registered-world')
        port = ContextOutputPort()

        def resolve(q):
            s = store.get(q.basis_world_state_ref.record_id)
            return s if s is not None and s.state_ref == q.basis_world_state_ref and s.state.scope == q.scope else None
        handler = WorldContextRequestHandler(state_resolver=resolve, projector=WorldContextProjector(), output_port=port)
        world = ProductionWorldUnderstandingRuntime(store=store, frame_factory=frame_factory,
                                                    method_revision_resolver=method_resolver,
                                                    context_request_handler=handler)
        initial = build_post_commit_source_envelope(
            source_kind='FACT_EXECUTION', source_native_id='registration.genesis',
            producer_ref='v3.fact_kernel',
            payload={'fact_transaction': {'operation_id': 'registration.genesis', 'action': 'write_file',
                                          'state': 'OBSERVED'}},
            source_time=WorldTime(valid_from_ms=1, observed_at_ms=1, recorded_at_ms=1), scope=scope,
            correlation_id='registration.genesis', workspace_id=rc.workspace_id)
        assert world.facade.accept(initial).processed
        previous = store.current_candidates(life_id=rc.life_id, principal_scope_hash=scope.principal_scope_hash)[0]
        candidates = []
        for candidate in args['candidates']:
            path = f'src/world_understanding/skill_method_world/sources/{candidate.method_id}.v1.json'
            raw = (repo / path).read_bytes()
            primitive = compile_native_method_source(path, raw, expected_source_sha256=hashlib.sha256(raw).hexdigest())
            candidate = replace(candidate, primitive=primitive).with_computed_sha256()
            evidence = _sign_report(candidate, observer)
            candidate = replace(candidate, simulation_evidence_sha256=hashlib.sha256(evidence.report_bytes).hexdigest()).with_computed_sha256()
            candidates.append(candidate)
            args['source_documents'][candidate.method_id] = (path, raw)
            args['simulation_evidence'][candidate.method_id] = evidence
        args['candidates'] = tuple(candidates)
        _bind_review(args, reviewer)
        frame = frame_factory(initial, previous.cut)
        body = build_method_publication_body(frame=frame, previous=previous, review_inputs=args, publication_at_ms=25)
        archive_sha = stage_method_publication(archive_root=archive, body=body,
                                               publication_signature=reviewer.sign(PUBLICATION_DOMAIN + body))
        event = method_publication_envelope(archive_sha256=archive_sha, frame=frame, at_ms=25)
        receipt = world.facade.accept(event)
        assert receipt.processed, receipt
        current = store.current_candidates(life_id=rc.life_id, principal_scope_hash=scope.principal_scope_hash)[0]
        frame = frame_factory(event, current.cut)
        graph = SparseWorldGraph(frame)
        for e in current.entities:
            graph.upsert_entity(e)
        for r in current.relations:
            graph.upsert_relation(r)
        current = materialize_one_world_state(
            WorldStateMaterializer(store),
            MaterializationInput(frame=frame, cut=current.cut, graph=graph,
                                 dependency_bindings=current.dependencies.bindings,
                                 preserve_previous_domains=True,
                                 source_transaction_id='registration.tool.observation', materialized_at_ms=25),
            (compile_tool_capability_contribution(frame, current.cut, tools),))
        resolver = MethodRunSourceResolver(gateway, world)
        gateway.configure_method_source_lifecycle(resolver)
        monkeypatch.setattr(installed, '_method_run_resolver', resolver)
        bridge = WorldContextIntegration(store=store, facade=world.facade, output_port=port, token_budget=8000)
        data = dict(gateway=gateway, world=world, bridge=bridge, rc=rc, query_scope=scope, registry=registry,
                    tool_source=tool_source, resolver=resolver, current=current, user=inbound.text,
                    marker=marker, workspace_root=workspace_root, method_resolver=method_resolver, port=port)
        try:
            yield data
        finally:
            gateway.close()
    return build


@pytest.fixture
def intake(intake_factory):
    yield from intake_factory()


def _prepare(c):
    return c['bridge'].prepare_composition_for_turn(run_context=c['rc'], user_text=c['user'],
                                                    tool_source=c['tool_source'], registry=c['registry'],
                                                    now_ms=5000)


def _compile(c, p, **kw):
    validated_at_ms = kw.pop('validated_at_ms', 5100)
    intents = kw.pop('intents', None)
    if intents is None:
        probe = c['bridge'].compile_composition_for_turn(p, _proposal(p), run_context=c['rc'],
                                                          tool_source=c['tool_source'],
                                                          validated_at_ms=validated_at_ms)
        intents = frozenset(probe.plan.verification_intents)
    return c['bridge'].compile_composition_for_turn(p, _proposal(p), run_context=c['rc'],
                                                    tool_source=c['tool_source'],
                                                    validated_at_ms=validated_at_ms,
                                                    available_verifiers=intents, **kw)


def _proposal(p):
    action = p.candidates.action_candidates[0].candidate_id
    method = next(m.candidate_id for m in p.candidates.method_candidates if m.primitive.method_id == 'native_0')
    return _proposal_document(goal_ref=p.context.goal_ref, methods=(method,), actions=(action,),
                              steps=(("s1", action, ()),))


def _admission_inputs(c, result):
    registry_snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=5200)
    evidence = tuple(
        VerificationIntentEvidenceV1(
            intent_ref=intent,
            predicate=AcceptancePredicate.create(predicate_type="artifact.nonempty",
                                                 subject_kind="artifact", params={}),
            subject_identity="object:registration-intake",
            evaluation_phase="POST_EXECUTION")
        for intent in sorted(result.plan.verification_intents))
    workspace = _workspace_binding(c['workspace_root'])
    candidate = result.preparation.candidates.action_candidates[0]
    primitive = candidate.primitive
    permission = next(item for item in result.preparation.registry.permissions
                      if item.action_id == primitive.action_id)
    plan_input = _hashed(PlanInputV1(input_id="input.resource", input_kind="INLINE_JSON",
                                     inline_value={"resource": "skill://registered"},
                                     value_schema_sha256=H,
                                     value_sha256=canonical_sha256({"resource": "skill://registered"}),
                                     sha256=ZERO_SHA256))
    input_reference = _hashed(PlanInputValueBindingV1(input_id=plan_input.input_id, input_sha256=plan_input.sha256,
                                                      json_pointer="/resource", sha256=ZERO_SHA256))
    output = _hashed(OutputDeclarationV1(output_binding_id=result.parse_outcome.proposal.steps[0].output_bindings[0],
                                         source_kind="RESULT_PAYLOAD", json_pointer="/listing",
                                         value_schema_sha256=H, sha256=ZERO_SHA256))
    step = _hashed(StepExecutionBindingV1(
        step_id=result.plan.steps[0].step_id,
        candidate_id=result.parse_outcome.proposal.steps[0].candidate_id,
        candidate_binding_sha256=candidate.binding_sha256,
        action_id=result.plan.steps[0].action_id,
        action_version=result.plan.steps[0].action_version,
        source_revision=candidate.source_revision,
        argument_schema_sha256=primitive.argument_schema_sha256,
        result_schema_sha256=primitive.result_schema_sha256,
        permission=permission,
        permission_sha256=permission.permission_sha256,
        depends_on=result.plan.steps[0].depends_on,
        target_skeleton=str(c['workspace_root'] / 'listing.json'),
        args_skeleton={"resource": None},
        argument_slots=(_hashed(ArgumentSlotV1(destination_json_pointer="/resource",
                                               value_binding=input_reference, sha256=ZERO_SHA256)),),
        output_declarations=(output,),
        sha256=ZERO_SHA256))
    final_reference = _hashed(StepOutputValueBindingV1(producer_step_id=step.step_id,
                                                       output_binding_id=output.output_binding_id,
                                                       output_declaration_sha256=output.sha256,
                                                       sha256=ZERO_SHA256))
    final_alias = _hashed(FinalOutputAliasV1(alias=result.parse_outcome.proposal.output_bindings[0],
                                             value_binding=final_reference, sha256=ZERO_SHA256))
    return dict(verification_registry=registry_snapshot, intent_evidence=evidence, workspace=workspace,
                plan_inputs=(plan_input,), step_bindings=(step,), final_output_aliases=(final_alias,),
                issued_at_ms=5200, expires_at_ms=5800, recorded_at_ms=5300)


def _register(c, result, **overrides):
    from v3 import world_understanding_production as installed
    inputs = _admission_inputs(c, result)
    inputs.update(overrides)
    return installed.register_production_composition(result, run_context=c['rc'], **inputs)


def _store_count(c, sql):
    return c['gateway']._connection.execute(sql).fetchone()[0]


def test_provisional_unknown_plan_registers_through_original_p7_chain(intake):
    """A real UNKNOWN/PROVISIONAL_ALLOW result reaches registration + sealing."""
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    assert result.validation.result == 'UNKNOWN'
    assert result.validation.unknown_disposition == 'PROVISIONAL_ALLOW'
    assert result.validation.mandatory_verification
    outcome = _register(c, result)
    assert outcome.compiled_and_validated and outcome.registered
    assert outcome.validation_mode == 'PROVISIONAL_UNKNOWN'
    assert outcome.created_by_this_call and not outcome.idempotent_replay
    record = c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1)
    assert record is not None and record.executable_plan.executable_plan_id == outcome.executable_plan_id
    assert record.executable_plan.legacy_plan == result.plan
    registration = c['gateway'].get_limited_activation_registration(
        outcome.registration_id)
    assert registration is not None
    assert registration.validation_mode == 'PROVISIONAL_UNKNOWN'
    assert registration.provisional_verification_required
    # P19 authorities and the P9 retention pin both exist after one admission.
    assert c['gateway'].get_verification_plan(outcome.verification_plan_id) is not None
    assert any(pin.owner_id == 'method-plan:' + outcome.executable_plan_id
               for pin in c['world'].store.retained_states())


def test_proved_valid_semantics_stay_with_the_original_p7_contract(intake):
    """PROVED_VALID keeps its original meaning on this seam, too.

    The real fixture action (skill.list) is NONDETERMINISTIC by P8 observation,
    so this chain admits PROVISIONAL_UNKNOWN only; the PROVED_VALID admission
    path itself is exercised by the original P7A/P7B suites against the same
    ``register_executable_composition_plan_bundle`` entry this seam calls.
    The registered mode must be carried through, never upgraded or widened.
    """
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    outcome = _register(c, result)
    assert outcome.validation_mode == 'PROVISIONAL_UNKNOWN'
    registration = c['gateway'].get_limited_activation_registration(outcome.registration_id)
    assert registration.validation_mode == 'PROVISIONAL_UNKNOWN'
    assert registration.provisional_verification_required is True
    assert registration.authorizes is False and registration.may_execute is False


def test_registration_distinguishes_stages_and_never_grants_or_executes(intake):
    c = intake
    p, _prompt = _prepare(c)
    outcome = _register(c, _compile(c, p))
    assert outcome.compiled_and_validated and outcome.registered
    assert outcome.granted is False and outcome.executed is False and outcome.completed is False
    # Admission created no Effect and no execution attempt: eligibility only.
    assert c['gateway']._connection.execute(
        "SELECT COUNT(*) FROM effect_ledger WHERE request_id = ?",
        (c['rc'].request_id,)).fetchone()[0] == 0
    assert c['gateway']._connection.execute(
        "SELECT COUNT(*) FROM effect_attempts a JOIN effect_ledger e ON e.effect_id = a.effect_id "
        "WHERE e.request_id = ?", (c['rc'].request_id,)).fetchone()[0] == 0


def test_proved_invalid_is_refused_with_original_findings(intake):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p, validated_at_ms=4999)
    assert result.validation.result == 'PROVED_INVALID'
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.validation.proved_invalid'):
        _register(c, result)
    assert c['gateway'].get_executable_composition_plan_for_request(
        c['rc'].request_id, run_id=c['rc'].run_id, generation=1) is None


def test_unknown_without_provisional_allow_is_refused(intake):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p, intents=frozenset())
    assert result.validation.result == 'UNKNOWN'
    assert result.validation.unknown_disposition == 'REJECT'
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.validation.unknown_not_provisional'):
        _register(c, result)


def test_missing_or_extra_verifier_evidence_is_refused(intake):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.verification_evidence.incomplete'):
        _register(c, result, intent_evidence=())
    duplicated = tuple(
        VerificationIntentEvidenceV1(
            intent_ref=sorted(result.plan.verification_intents)[0],
            predicate=AcceptancePredicate.create(predicate_type="artifact.nonempty",
                                                 subject_kind="artifact", params={}),
            subject_identity="object:registration-intake",
            evaluation_phase="POST_EXECUTION")
        for _ in range(2))
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.verification_evidence.duplicate'):
        _register(c, result, intent_evidence=duplicated)


def test_workspace_binding_mismatch_is_refused(intake, tmp_path):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    other = _workspace_binding(tmp_path)
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.workspace.binding_mismatch'):
        _register(c, result, workspace=other)


def test_duplicate_admission_is_idempotent_and_conflicts_are_refused(intake):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    first = _register(c, result)
    second = _register(c, result)
    assert second.registration_id == first.registration_id
    assert second.executable_plan_id == first.executable_plan_id
    assert second.idempotent_replay and not second.created_by_this_call
    # The same logical registration cannot be re-sealed with different
    # invocation bindings: that is an identity reuse, not a second admission.
    tampered_step = _admission_inputs(c, result)
    tampered_step['step_bindings'] = (
        tampered_step['step_bindings'][0].model_copy(
            update={'target_skeleton': str(c['workspace_root'] / 'other-listing.json'),
                    'sha256': ZERO_SHA256}).with_computed_sha256(),)
    from v3 import world_understanding_production as installed
    with pytest.raises(Exception, match='identity was reused'):
        installed.register_production_composition(result, run_context=c['rc'], **tampered_step)


def test_generation_release_refuses_late_admission(intake):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    c['gateway'].release_generation(c['rc'].request_id, released_at_ms=5250)
    with pytest.raises(ValueError, match='COMPOSITION_SOURCE_GENERATION_NOT_ACTIVE'):
        _register(c, result)


def test_tampered_result_cannot_register(intake):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    drifted = replace(result, plan=result.plan.model_copy(
        update={'request_id': 'req_' + 'c' * 64}))
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.p4.identity_invalid'):
        _register(c, drifted)
    # Even a self-consistently rehashed plan stays bound to the request the
    # preparation was compiled for; a new hash cannot smuggle it in.
    from world_understanding.capability_composition import computed_plan_sha256
    rehashed_plan = result.plan.model_copy(
        update={'request_id': 'req_' + 'c' * 64,
                'plan_sha256': computed_plan_sha256(
                    result.plan.model_copy(update={'request_id': 'req_' + 'c' * 64}))})
    rehashed = replace(result, plan=rehashed_plan)
    with pytest.raises(CompositionRegistrationIntakeError,
                       match='intake.plan.binding_mismatch'):
        _register(c, rehashed)


def test_restart_recovers_sealed_plan_and_retention(intake, tmp_path):
    c = intake
    p, _prompt = _prepare(c)
    result = _compile(c, p)
    outcome = _register(c, result)
    plan_sha256 = result.plan.plan_sha256
    c['gateway'].close()
    reopened = GatewayStateStore.open(
        tmp_path / 'registered-gateway.sqlite3', now_ms=5400)
    try:
        record = reopened.get_executable_composition_plan_record(outcome.executable_plan_id)
        assert record is not None
        assert record.executable_plan.executable_plan_id == outcome.executable_plan_id
        assert record.executable_plan.legacy_plan.plan_sha256 == plan_sha256
        # The P9 retention pin survived in the durable World store.
        assert any(pin.owner_id == 'method-plan:' + outcome.executable_plan_id
                   for pin in c['world'].store.retained_states())
        assert reopened._connection.execute(
            "SELECT COUNT(*) FROM composition_activation_registration").fetchone()[0] == 1
    finally:
        reopened.close()


def test_registered_plan_reads_exact_sources_but_grants_nothing(intake):
    """After admission the sealed-Plan reader works; execution still needs A5."""
    c = intake
    p, _prompt = _prepare(c)
    outcome = _register(c, _compile(c, p))
    methods = c['resolver'].read(request_id=c['rc'].request_id, run_id=c['rc'].run_id,
                                 generation=1, scope=c['query_scope'])
    assert methods is not None
    assert c['gateway']._connection.execute(
        "SELECT COUNT(*) FROM effect_ledger WHERE request_id = ?",
        (c['rc'].request_id,)).fetchone()[0] == 0
    assert c['gateway']._connection.execute(
        "SELECT COUNT(*) FROM effect_attempts a JOIN effect_ledger e ON e.effect_id = a.effect_id "
        "WHERE e.request_id = ?", (c['rc'].request_id,)).fetchone()[0] == 0
    assert outcome.executed is False and outcome.completed is False
