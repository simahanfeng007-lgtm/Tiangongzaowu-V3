"""Real installed candidates and schemas through the desktop invocation binder."""
import pytest
import json

from total_gateway.composition_admission_materialization import materialize_step_bindings
from total_gateway.desktop_composition import DesktopCompositionError, bind_model_invocations
from tests.test_installed_composition_sources import installed, source_copy  # noqa: F401
from tests.test_capability_composition_p4 import _proposal_document


def _prepared(c):
    from v3.world_context_integration import WorldContextIntegration
    bridge = WorldContextIntegration(
        store=c.runtime.store, facade=c.runtime.facade, output_port=c.port,
        token_budget=16000,
        repository_snapshot_refresher=lambda rc: c.sources.ensure_world(rc, now_ms=2000))
    prepared, _ = bridge.prepare_composition_for_turn(
        run_context=c.rc, user_text=c.user, tool_source=c.sources.tool_source,
        registry=c.authority.registry, now_ms=2100)
    return bridge, prepared


def test_real_source_and_schema_allow_one_argument_correction_without_registration(installed, tmp_path):
    from total_gateway.desktop_composition import run_bounded_proposal_validation, validate_executable_candidate_selection
    from tests.test_desktop_composition_correction import Client
    c = installed
    bridge, prepared = _prepared(c)
    method = next(item for item in prepared.candidates.method_candidates if item.primitive.method_id == "acceptance_review")
    action = next(item for item in prepared.candidates.action_candidates if item.primitive.action_id == "file.read")
    proposal = json.loads(_proposal_document(goal_ref=prepared.context.goal_ref,
        methods=(method.candidate_id,), actions=(action.candidate_id,), steps=(("s1",action.candidate_id,()),)))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "input.txt"
    target.write_text("real input\n",encoding="utf-8")
    wrong = {"proposal": proposal, "invocations": {"s1": {"target": str(target), "args": {"max_chars": True}}}}
    fixed = {"proposal": proposal, "invocations": {"s1": {"target": str(target), "args": {"max_chars": 1000}}}}
    methods = [{"candidate_id":method.candidate_id}]
    actions = [{"candidate_id":action.candidate_id}]
    def validate(model):
        validate_executable_candidate_selection(model,methods=methods,actions=actions)
        compiled = bridge.compile_composition_for_turn(prepared,json.dumps(model["proposal"]),
            run_context=c.rc,tool_source=c.sources.tool_source,validated_at_ms=2300,
            available_verifiers=frozenset(method.primitive.verification_intent))
        return bind_model_invocations(compiled,model["invocations"],schemas=c.authority.schema_catalog,workspace_root=workspace)
    client = Client([wrong,fixed])
    notes=[]
    steps, _ = run_bounded_proposal_validation(client,"system","same source and allowset",validate=validate,
        validate_source=lambda:c.sources.tool_source.load(c.authority.registry),diagnostic=notes.append)
    assert len(client.calls)==2 and steps["s1"].args=={"max_chars":1000}
    assert notes[0]["error_code"]=="desktop_composition.argument_schema_invalid"
    assert c.gateway.get_executable_composition_plan_for_request(c.rc.request_id,run_id=c.rc.run_id,generation=1) is None
    assert target.read_text(encoding="utf-8")=="real input\n"


def test_real_source_drift_between_corrections_is_terminal_and_unregistered(installed):
    from total_gateway.desktop_composition import run_bounded_proposal_validation
    from tests.test_desktop_composition_correction import Client, model
    c=installed
    target=c.root/"src/total_gateway/installed_composition_sources.py"
    original=target.read_bytes()
    class ChangedClient(Client):
        def llm_diaoyong(self,system,prompt):
            reply=super().llm_diaoyong(system,prompt)
            target.write_bytes(original+b"\n# QA changed after first proposal\n")
            return reply
    client=ChangedClient(['{"proposal":invalid',model()])
    try:
        with pytest.raises(ValueError,match="CHECKOUT_CHANGED_RESTART_REQUIRED"):
            run_bounded_proposal_validation(client,"system","fixed source",validate=lambda _:pytest.fail("no valid proposal"),
                validate_source=lambda:c.sources.tool_source.load(c.authority.registry),diagnostic=lambda _:None)
    finally:
        target.write_bytes(original)
    assert len(client.calls)==1
    assert c.gateway.get_executable_composition_plan_for_request(c.rc.request_id,run_id=c.rc.run_id,generation=1) is None


def test_installed_multistep_outputs_all_reach_final_aliases(installed, tmp_path):
    c = installed
    bridge, prepared = _prepared(c)
    by_action = {item.primitive.action_id: item for item in prepared.candidates.action_candidates}
    method = prepared.candidates.method_candidates[0]
    action_list = by_action["file.list"].candidate_id
    action_read = by_action["file.read"].candidate_id
    proposal = _proposal_document(
        goal_ref=prepared.context.goal_ref, methods=(method.candidate_id,),
        actions=tuple(sorted((action_list, action_read))),
        steps=(("s1", action_list, ()), ("s2", action_read, ("s1",))),
        extra={"output_bindings": ["final.list", "final.read"]})
    result = bridge.compile_composition_for_turn(
        prepared, proposal, run_context=c.rc, tool_source=c.sources.tool_source,
        validated_at_ms=2200,
        available_verifiers=frozenset(method.primitive.verification_intent))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    orders = workspace / "orders.csv"
    orders.write_bytes(b"id,amount\nA1,10\n")
    steps, aliases = bind_model_invocations(result, {
        "s1": {"target": str(workspace), "args": {"pattern": "*.csv"}},
        "s2": {"target": str(orders), "args": {"max_chars": 2000}},
    }, schemas=c.authority.schema_catalog, workspace_root=workspace)
    assert set(aliases) == {"final.list", "final.read"}
    assert {value.producer_step_id for value in aliases.values()} == {"s1", "s2"}
    inputs, bindings, sealed_aliases = materialize_step_bindings(
        result, user_inputs={}, step_inputs=steps, final_outputs=aliases)
    assert len(bindings) == len(sealed_aliases) == 2
    assert {binding.target_skeleton for binding in bindings} == {str(workspace), str(orders)}
    assert inputs == () and all(row.has_valid_sha256() for row in bindings)
    for binding in bindings:
        declaration = binding.output_declarations[0]
        assert declaration.json_pointer == "/result"
        expected = c.authority.schema_catalog.resolve_value_schema(
            binding.action_id, binding.action_version, declaration.value_schema_sha256)
        assert expected.json_pointer == declaration.json_pointer

    # A raw model object cannot add target permissions or change the action.
    with pytest.raises(DesktopCompositionError, match="invocation_fields"):
        bind_model_invocations(result, {
            "s1": {"target": str(workspace), "args": {}, "risk": "A0"},
            "s2": {"target": str(orders), "args": {}},
        }, schemas=c.authority.schema_catalog, workspace_root=workspace)


def test_installed_legacy_skill_candidate_is_refused(installed, tmp_path):
    c = installed
    bridge, prepared = _prepared(c)
    skill = next((item for item in prepared.candidates.action_candidates
                  if item.primitive.action_id in {"skill.list", "skill.get", "skill.read"}), None)
    if skill is None:
        # Candidate retrieval itself can exclude legacy actions for this goal.
        assert all(not item.primitive.action_id.startswith("skill.")
                   for item in prepared.candidates.action_candidates)
        return
    method = prepared.candidates.method_candidates[0]
    proposal = _proposal_document(goal_ref=prepared.context.goal_ref,
        methods=(method.candidate_id,), actions=(skill.candidate_id,),
        steps=(("s1", skill.candidate_id, ()),))
    result = bridge.compile_composition_for_turn(
        prepared, proposal, run_context=c.rc, tool_source=c.sources.tool_source,
        validated_at_ms=2200,
        available_verifiers=frozenset(method.primitive.verification_intent))
    with pytest.raises(DesktopCompositionError, match="legacy_skill_forbidden"):
        bind_model_invocations(result, {"s1": {"target": "", "args": {}}},
            schemas=c.authority.schema_catalog, workspace_root=tmp_path)


def test_real_filesystem_literals_project_with_actual_schema_catalog(installed, tmp_path, monkeypatch):
    """Regression for the live zero input-schema digest, through sealed plans.

    Actual source/candidates/registration/projection/schema and Body handlers
    are exercised. This is not a signed Policy/Ticket/Grant acceptance test.
    """
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    from total_gateway.action_registry import ActionRegistryError
    from total_gateway.composition_admission_materialization import (
        materialize_admission_inputs, materialize_workspace,
    )
    from total_gateway.composition_execution_projection import materialize_ready_composition_step

    c = installed
    workspace = tmp_path / "read-workspace"
    workspace.mkdir()
    # Unlike the candidate-only fixture, admission needs the exact derived
    # workspace identity before the World context and plan are compiled.
    c.rc.workspace_id = materialize_workspace(workspace).workspace_id
    bridge, prepared = _prepared(c)
    by_action = {item.primitive.action_id: item for item in prepared.candidates.action_candidates}
    actions = ("file.list", "file.read", "file.hash")
    candidates = tuple(by_action[action].candidate_id for action in actions)
    method = prepared.candidates.method_candidates[0]
    proposal = _proposal_document(
        goal_ref=prepared.context.goal_ref, methods=(method.candidate_id,),
        actions=tuple(sorted(candidates)),
        steps=tuple((f"s{index}", candidate, ()) for index, candidate in enumerate(candidates, 1)),
        extra={"output_bindings": ["final.list", "final.read", "final.hash"]})
    result = bridge.compile_composition_for_turn(
        prepared, proposal, run_context=c.rc, tool_source=c.sources.tool_source,
        validated_at_ms=2200, available_verifiers=frozenset(method.primitive.verification_intent))
    fixture = workspace / "evidence.txt"
    fixture.write_text("real filesystem input projection\n", encoding="utf-8")
    invocations = {
        "s1": {"target": str(workspace), "args": {"pattern": "*.txt", "max_results": 10}},
        "s2": {"target": str(fixture), "args": {"max_chars": 2000, "binary": False}},
        "s3": {"target": str(fixture), "args": {}},
    }
    steps, aliases = bind_model_invocations(
        result, invocations, schemas=c.authority.schema_catalog, workspace_root=workspace)
    inputs = materialize_admission_inputs(result, workspace_root=workspace,
        user_inputs={}, step_inputs=steps, final_outputs=aliases,
        issued_at_ms=2300, expires_at_ms=6300)
    outcome = c.sources.resolver.register_composition(result, **inputs)
    assert outcome.registered
    record = c.gateway.get_executable_composition_plan_for_request(
        c.rc.request_id, run_id=c.rc.run_id, generation=c.rc.generation)
    plan = record.executable_plan
    assert plan.has_valid_identity() and plan.plan_inputs == ()
    assert all(slot.value_binding.binding_kind == "LITERAL"
        for step in plan.step_bindings for slot in step.argument_slots)
    schemas = c.authority.schema_catalog
    # The fix must not create an accept-all zero schema or relax projection.
    with pytest.raises(ActionRegistryError):
        schemas.validate_value_exact("0" * 64, "*.txt")
    monkeypatch.setenv("TIANGONG_OMNI_BODY_STATE_ROOT", str(tmp_path / "body-state"))
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(workspace), fact_kernel_enabled=False,
        emergency_audit_dir=str(tmp_path / "body-emergency")))
    for index, action in enumerate(actions, 1):
        step_id = f"s{index}"
        dispatch = materialize_ready_composition_step(plan, step_id=step_id, committed={},
            validate_value=schemas.validate_value_exact,
            validate_result=schemas.validate_result_exact,
            resolve_value_schema=schemas.resolve_value_schema)
        materialized = dispatch.step
        assert materialized.arguments == invocations[step_id]["args"]
        entry = schemas.resolve(action, materialized.step.action_version,
            expected_sha256=materialized.step.argument_schema_sha256, require_explicit=True)
        entry.validate_exact(action, materialized.target, materialized.arguments,
            workspace=workspace, available_actions=actions)
        value = runtime.run(action, materialized.target, materialized.arguments)
        assert value["success"] is True
        selector = materialized.step.output_declarations[0]
        schemas.validate_value_exact(selector.value_schema_sha256, value)
    # A literal is still subject to the unchanged explicit action schema.
    bad = {**invocations, "s2": {"target": str(fixture), "args": {"max_chars": True}}}
    with pytest.raises(ActionRegistryError):
        bind_model_invocations(result, bad, schemas=schemas, workspace_root=workspace)
