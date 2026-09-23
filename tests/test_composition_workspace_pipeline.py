"""Actual registered/signed continuation -> native Body -> committed Facts.

The embedded HTTP port is replaced with an in-process Body adapter; no model,
desktop App, publication or production workspace is involved.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from tests import test_composition_grant_authority_p7c1 as p7c1
from tests import test_composition_grant_authority_p7d2 as p7d2
from tests import test_composition_executable_plan_p7c0 as p7c0
from tests import test_capability_composition_p4 as p4
from total_gateway.composition_executable_plan import StepExecutionBindingV1, OutputDeclarationV1, FinalOutputAliasV1, StepOutputValueBindingV1
from total_gateway.composition_step_execution import CompositionStepExecutionCoordinator, CompositionStepExecutionError

PROFILE = dict(execution_profile_id=WORKSPACE_PYTHON_PROFILE_ID,
               execution_profile_sha256=WORKSPACE_PYTHON_PROFILE_SHA256)
ZERO = "0" * 64


def material_for_calls(calls, *, source_worlds=None, admission_lifetime_ms=60_000):
    def material(store, objects, workspace_root, loaded, **_kwargs):
        envelope, request, run = p7c0._register_request_lineage(store)
        actions = tuple(sorted({action for action, _, _ in calls}))
        permissions = {item.action_id: item for item in loaded.registry.permissions if item.action_id in actions}
        schemas = {action: loaded.schema_catalog.resolve(action, permissions[action].action_version,
                    require_explicit=True, require_result_explicit=True) for action in actions}
        specs = tuple(dict(action_id=action, risk=permissions[action].registry_risk,
                      effect=permissions[action].effect, side_effects=permissions[action].allowed_side_effects,
                      allow_python=permissions[action].allow_python,
                      resource_scope=("workspace_only",)) for action in actions)
        if source_worlds is None:
            _, world, methods = p4._worlds(specs, manifest_sha256=loaded.registry.source_manifest_sha256)
            method_id = "generate_then_verify"
        else:
            world, methods = source_worlds(loaded)
            method_id = "acceptance_review"
        if source_worlds is None:
            primitives = tuple(p7c1._rehash_primitive(p, argument_schema_sha256=schemas[p.action_id].argument_schema_sha256,
                               result_schema_sha256=schemas[p.action_id].result_schema_sha256) for p in world.primitives)
            world = replace(world, action_registry_sha256=loaded.registry.registry_sha256, primitives=primitives,
                            snapshot_sha256=ZERO)
            world = replace(world, snapshot_sha256=canonical_sha256(world.payload()))
        else:
            assert world.action_registry_sha256 == loaded.registry.registry_sha256
        candidates = p7c0.build_candidate_snapshot(world, methods, method_ids=(method_id,), action_ids=actions)
        by_action = {item.primitive.action_id: item for item in candidates.action_candidates}
        context = replace(p4._context(goal_ref="goal.controlled-workspace", manifest_sha256=loaded.registry.source_manifest_sha256),
                          request_id=request.request_id, run_id=run.run_id, generation=1,
                          principal_scope_hash=envelope.principal_scope_hash, created_at_ms=1250,
                          context_sha256=ZERO).with_computed_sha256()
        specs = tuple((f"step.{i:02}", by_action[action].candidate_id, () if i == 1 else (f"step.{i-1:02}",))
                      for i, (action, _, _) in enumerate(calls, 1))
        proposal = p7c0.parse_composition_proposal(p4._proposal_document(goal_ref=context.goal_ref,
            methods=("M01",), actions=tuple(sorted(c.candidate_id for c in candidates.action_candidates)), steps=specs), candidates)
        legacy = p7c0.compile_capability_composition_plan(proposal, candidates, context, loaded.registry)
        validation = p7c0.validate_capability_composition_plan(legacy, proposal, candidates, context, loaded.registry,
                        available_verifiers=frozenset(legacy.verification_intents), validated_at_ms=1300)
        if source_worlds is not None:
            from total_gateway.composition_profile_admission import resolve_profile_validation
            assert validation.result == "UNKNOWN" and validation.unknown_disposition == "REJECT"
            validation = resolve_profile_validation(legacy, proposal, candidates, context, loaded.registry,
                available_verifiers=frozenset(legacy.verification_intents), validated_at_ms=1300, **PROFILE)
            assert validation.result == "UNKNOWN" and validation.unknown_disposition == "PROVISIONAL_ALLOW"
        else:
            assert validation.result == "PROVED_VALID", validation
        verifiers = p7c0.VerifierRegistry.with_defaults().snapshot(captured_at_ms=1350)
        verifier_bindings = tuple(p7c0.build_system_verification_binding(intent_ref=intent,
            predicate=p7c0.AcceptancePredicate.create(predicate_type="effect.terminal_succeeded" if source_worlds else "artifact.nonempty",
                subject_kind="effect" if source_worlds else "artifact", params={}),
            subject_identity="composition-plan:" + legacy.plan_id if source_worlds else f"object:test-{i}",
            evaluation_phase="POST_EXECUTION", registry_snapshot=verifiers)
            for i, intent in enumerate(legacy.verification_intents))
        shadow = p7c0.propose_shadow_composition_activation(legacy, validation, loaded.registry, verifiers, verifier_bindings,
                    current_world_state_sha256=legacy.world_state_sha256, expected_principal_scope_hash=legacy.principal_scope_hash,
                    issued_at_ms=1500, expires_at_ms=1500+admission_lifetime_ms, **PROFILE)
        steps = []
        aliases = []
        # Every result is consumed by a final alias; dependency edges carry real
        # execution evidence without fabricating argument dataflow.
        document = json.loads(p4._proposal_document(goal_ref=context.goal_ref, methods=("M01",),
            actions=tuple(sorted(c.candidate_id for c in candidates.action_candidates)), steps=specs))
        document["output_bindings"] = [f"final.{i:02}" for i in range(1, len(calls)+1)]
        proposal = p7c0.parse_composition_proposal(json.dumps(document), candidates)
        legacy = p7c0.compile_capability_composition_plan(proposal, candidates, context, loaded.registry)
        validation = p7c0.validate_capability_composition_plan(legacy, proposal, candidates, context, loaded.registry,
                        available_verifiers=frozenset(legacy.verification_intents), validated_at_ms=1300)
        if source_worlds is not None:
            validation = resolve_profile_validation(legacy, proposal, candidates, context, loaded.registry,
                available_verifiers=frozenset(legacy.verification_intents), validated_at_ms=1300, **PROFILE)
            verifier_bindings = tuple(p7c0.build_system_verification_binding(intent_ref=intent,
                predicate=p7c0.AcceptancePredicate.create(predicate_type="effect.terminal_succeeded", subject_kind="effect", params={}),
                subject_identity="composition-plan:" + legacy.plan_id, evaluation_phase="POST_EXECUTION", registry_snapshot=verifiers)
                for intent in legacy.verification_intents)
        shadow = p7c0.propose_shadow_composition_activation(legacy, validation, loaded.registry, verifiers, verifier_bindings,
                    current_world_state_sha256=legacy.world_state_sha256, expected_principal_scope_hash=legacy.principal_scope_hash,
                    issued_at_ms=1500, expires_at_ms=1500+admission_lifetime_ms, **PROFILE)
        for i, ((action, target, args), proposed) in enumerate(zip(calls, proposal.steps, strict=True), 1):
            candidate, schema, permission = by_action[action], schemas[action], permissions[action]
            selector = next(item for item in schema.value_schemas if item.json_pointer == "/result")
            output = OutputDeclarationV1(output_binding_id=proposed.output_bindings[0], source_kind="RESULT_PAYLOAD",
                         json_pointer="/result", value_schema_sha256=selector.value_schema_sha256, sha256=ZERO).with_computed_sha256()
            step = StepExecutionBindingV1(step_id=proposed.step_id, candidate_id=proposed.candidate_id,
                candidate_binding_sha256=candidate.binding_sha256, action_id=action, action_version=permission.action_version,
                source_revision=candidate.source_revision, argument_schema_sha256=schema.argument_schema_sha256,
                result_schema_sha256=schema.result_schema_sha256, permission=permission, permission_sha256=permission.permission_sha256,
                depends_on=proposed.depends_on, target_skeleton=str(target), args_skeleton=args,
                output_declarations=(output,), sha256=ZERO, **PROFILE).with_computed_sha256()
            steps.append(step)
            ref = StepOutputValueBindingV1(producer_step_id=step.step_id, output_binding_id=output.output_binding_id,
                    output_declaration_sha256=output.sha256, sha256=ZERO).with_computed_sha256()
            aliases.append(FinalOutputAliasV1(alias=f"final.{i:02}", value_binding=ref, sha256=ZERO).with_computed_sha256())
        values = dict(proposal=proposal, candidates=candidates, context=context, legacy_plan=legacy, validation=validation,
                      action_registry=loaded.registry, verification_registry=verifiers, verification_bindings=verifier_bindings,
                      shadow=shadow, plan_inputs=(), step_bindings=tuple(steps), final_output_aliases=tuple(aliases),
                      workspace=p7c0._workspace(workspace_root))
        return values, p7c0._persist_executable(store, values).record.executable_plan, None
    return material


@pytest.mark.parametrize("run_python,use_subdirectory", [(False, False), (True, False), (True, True)])
def test_new_script_write_patch_run_read_are_real_signed_steps(tmp_path, monkeypatch, run_python, use_subdirectory):
    from tests import test_composition_activation_store_p7b2 as lineage_fixture
    envelope_type = lineage_fixture.InboundEnvelope
    text = "请保留现有产物 answer.txt。请实际运行脚本。" if run_python else "请写入 generated.py。"
    monkeypatch.setattr(lineage_fixture, "InboundEnvelope", lambda **values: envelope_type(**(values | {"text": text})))
    root = tmp_path / "workspace"
    root.mkdir()
    target = (root / "project" if use_subdirectory else root) / "generated.py"
    script = "from pathlib import Path\nPath('answer.txt').write_text('41', encoding='utf-8')\nprint('executed')\n"
    calls = ([("file.mkdir", root / "project", {}),
              ("code.write", target, {"content": script, "syntax_check": False})] if use_subdirectory
             else [("file.write", target, {"content": script})])
    calls.append(("code.patch_replace", target, {"find": "'41'", "replace": "'42'", "count": 1}))
    if run_python:
        calls.extend([("python.run", target, {"argv": [], "timeout": 30}),
                      ("file.read", root / "answer.txt", {})])
    else:
        calls.append(("file.read", target, {}))
    monkeypatch.setattr(p7c1, "_production_material", material_for_calls(calls))
    monkeypatch.setenv("TIANGONG_OMNI_BODY_STATE_ROOT", str(tmp_path / "body-state"))
    import omni_body_skill.tools.omni_capability as capability
    clock = SimpleNamespace(value=1800)
    monkeypatch.setattr(capability, "time", SimpleNamespace(time_ns=lambda: clock.value * 1_000_000))
    with p7c1._harness(root, multi_step=True, complete_parent_effect=False,
                      outer_expires_at_ms=61000, authority_expires_at_ms=61400, plan_expires_at_ms=61500) as harness:
        manifest = p7d2._execution_manifest(harness)
        harness.authority.composition_capability_manifest_hash = manifest.sha256
        delegation = p7d2._seal(harness)
        p7d2._finish_parent_with_fact(harness)
        executed = []
        dispatched = []

        class BodyPort:
            def request(self, _method, _path, payload, **_kwargs):
                dispatched.append(payload)
                invocation = payload["execute_ticket"]["arguments"]
                grant = capability.verify_capability_grant(payload["capability_grant"], action=invocation["action"],
                    target=invocation["target"], args=invocation["args"], workspace=str(root), runtime_meta=payload["runtime"])
                binding = payload["capability_grant"]["payload"]["composition_execution_binding"]
                runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(root), fact_kernel_enabled=False,
                    allow_absolute_paths=True, allow_python=grant["allow_python"],
                    sandbox_enabled=True, sandbox_require_os_containment=True, sandbox_allow_deletions=False,
                    sandbox_max_changed_mb=4, **PROFILE, target_snapshot_sha256=binding["target_snapshot_sha256"]))
                raw = runtime.run(invocation["action"], invocation["target"], invocation["args"])
                value = {"schema": "tiangong.v3.omni_body.v1", "ok": raw["success"], "zhuangtai": "wancheng",
                    "gongju": "omni_body", "action": invocation["action"], "target": invocation["target"],
                    "result": raw, "llm_brief": "actual result", "evidence": raw.get("evidence", {})}
                executed.append(value)
                encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                return 200, value, hashlib.sha256(encoded).hexdigest()

        generation = harness.store.get_generation(harness.plan.request_id)
        coordinator = CompositionStepExecutionCoordinator(store=harness.store, objects=harness.objects, facts=harness.facts,
            registry=harness.loaded.registry, schema_catalog=harness.loaded.schema_catalog, capability_manifest=manifest,
            trust_bundle_provider=lambda _now: harness.trust, backend_compat_client=BodyPort(), workspace_root=root,
            gateway_epoch=1, gateway_instance_id=generation.owner_instance_id, append_effect_event=lambda *_a, **_kw: True)
        for i, step in enumerate(harness.plan.step_bindings):
            clock.value = 1800 + i * 500
            harness.authority.issue_composition_continuation_step(continuation_delegation_id=delegation,
                registration_id=harness.plan.registration_id, step_id=step.step_id, now_ms=clock.value)
            record = harness.store.get_current_composition_step_authorization(harness.plan.executable_plan_id, step.step_id)
            if i == 0:
                assert record.request.target_snapshot["exists"] is False
            elif step.action_id in {"code.patch_replace", "python.run"}:
                assert record.request.target_snapshot["exists"] is True
                assert record.request.target_snapshot["content_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
            result = coordinator.dispatch_record(record, now_ms=clock.value)
            assert result.status == "SUCCEEDED", json.dumps({"step": step.step_id, "raw": executed[-1:]}, ensure_ascii=False)
            assert harness.facts.get_batch_for_effect(result.effect_id, verify_payload=True) is not None
            sent = dispatched[-1]
            with pytest.raises(Exception, match="(?i)nonce|replay|consumed"):
                capability.verify_capability_grant(sent["capability_grant"], action=step.action_id,
                    target=str(calls[i][1]), args=calls[i][2], workspace=str(root), runtime_meta=sent["runtime"])
        assert coordinator.project_plan(harness.plan).all_steps_succeeded
        assert len(executed) == len(calls)
        finalization = coordinator.finalize_plan(harness.plan)
        proof = finalization.execution_requirements_attestation
        assert proof["request_id"] == harness.plan.request_id
        assert proof["execution_requirements_verified"] is True
        assert proof["business_outcome_verified"] is False
        assert proof["supporting_fact_ids"] == list(finalization.fact_ids)
        from total_gateway.composition_final_result import encode_composition_final_result
        first_reply = encode_composition_final_result(finalization.final_output_aliases, parent_reply="admitted",
            execution_requirements_attestation=proof)
        repeated = coordinator.finalize_plan(harness.plan)
        assert encode_composition_final_result(repeated.final_output_aliases, parent_reply="admitted",
            execution_requirements_attestation=repeated.execution_requirements_attestation) == first_reply
        assert proof["execution_completed_at_ms"] == finalization.completed_at_ms
        if run_python:
            assert (root / "answer.txt").read_text("utf-8") == "42"
            assert next(value for value in executed if value["action"] == "python.run")["result"]["execution"]["containment"] == "windows-appcontainer"
            # A changed output obtains a different sealed identity. It cannot
            # reuse the prior Completion/Life payload or pretend the old bytes
            # are still current even though the historical read Fact exists.
            (root / "answer.txt").write_text("43", encoding="utf-8")
            changed = coordinator.finalize_plan(harness.plan)
            assert changed.execution_requirements_attestation["sha256"] != proof["sha256"]
            assert changed.execution_requirements_attestation["output_witnesses"][0]["sha256"] == hashlib.sha256(b"43").hexdigest()
            assert encode_composition_final_result(changed.final_output_aliases, parent_reply="admitted",
                execution_requirements_attestation=changed.execution_requirements_attestation) != first_reply
            # An old successful read cannot excuse a now-missing deliverable.
            (root / "answer.txt").unlink()
            with pytest.raises(CompositionStepExecutionError, match="required_output_unverified"):
                coordinator.finalize_plan(harness.plan)
        else:
            assert "'42'" in target.read_text("utf-8")


def test_signed_missing_target_snapshot_rejects_a_later_external_creation(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "result.txt"
    monkeypatch.setattr(p7c1, "_production_material", material_for_calls([("file.write", target, {"content": "authorized"})]))
    with p7c1._harness(root, multi_step=True, complete_parent_effect=False, outer_expires_at_ms=61000,
                      authority_expires_at_ms=61400, plan_expires_at_ms=61500) as harness:
        manifest = p7d2._execution_manifest(harness)
        harness.authority.composition_capability_manifest_hash = manifest.sha256
        delegation = p7d2._seal(harness)
        p7d2._finish_parent_with_fact(harness)
        step = harness.plan.step_bindings[0]
        harness.authority.issue_composition_continuation_step(continuation_delegation_id=delegation,
            registration_id=harness.plan.registration_id, step_id=step.step_id, now_ms=1800)
        record = harness.store.get_current_composition_step_authorization(harness.plan.executable_plan_id, step.step_id)
        assert record.request.target_snapshot["exists"] is False
        target.write_text("external content", encoding="utf-8")

        class ForbiddenBody:
            called = False
            def request(self, *_args, **_kwargs):
                self.called = True
                raise AssertionError("changed signed target must not reach Body")

        body = ForbiddenBody()
        generation = harness.store.get_generation(harness.plan.request_id)
        coordinator = CompositionStepExecutionCoordinator(store=harness.store, objects=harness.objects, facts=harness.facts,
            registry=harness.loaded.registry, schema_catalog=harness.loaded.schema_catalog, capability_manifest=manifest,
            trust_bundle_provider=lambda _now: harness.trust, backend_compat_client=body, workspace_root=root,
            gateway_epoch=1, gateway_instance_id=generation.owner_instance_id)
        with pytest.raises(CompositionStepExecutionError, match="composition.execution.target_changed"):
            coordinator.dispatch_record(record, now_ms=1800)
        assert body.called is False
        assert target.read_text("utf-8") == "external content"
        assert harness.store.get_effect(record.request.prebound_effect_id) is None
