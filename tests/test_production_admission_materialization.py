"""System invocation materialization; fixtures do not claim live acceptance."""
from __future__ import annotations

from dataclasses import replace

import pytest

from contracts.verification import AcceptancePredicate
from total_gateway.composition_admission_materialization import (
    AdmissionMaterializationError,
    StepInvocationInputs,
    StepOutputReference,
    StepOutputSelector,
    materialize_admission_inputs,
    materialize_step_bindings,
    materialize_verifier_evidence,
)
from total_gateway.composition_registration_intake import VerificationIntentEvidenceV1
from total_gateway.verification_registry import VerifierRegistry
from tests.test_source_registration_intake_p12 import (  # noqa: F401
    intake, intake_factory, source, _compile, _prepare, _register,
)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401
from tests.test_capability_composition_p4 import _proposal_document


def _two_step_result(c, *, depends_on=("s1",)):
    preparation = _prepare(c)[0]
    action = preparation.candidates.action_candidates[0].candidate_id
    method = next(item.candidate_id for item in preparation.candidates.method_candidates
                  if item.primitive.method_id == "native_0")
    text = _proposal_document(
        goal_ref=preparation.context.goal_ref, methods=(method,), actions=(action,),
        steps=(("s1", action, ()), ("s2", action, depends_on)))
    return c["bridge"].compile_composition_for_turn(
        preparation, text, run_context=c["rc"], tool_source=c["tool_source"],
        validated_at_ms=5100,
        available_verifiers=frozenset({"verification-intent:plan-bound-acceptance"}))


def _step_inputs(c):
    return {
        "s1": StepInvocationInputs(
            target=str(c["workspace_root"] / "first.json"),
            args={"category": "registry"},
            outputs={"out.s1": StepOutputSelector(json_pointer="/listing")}),
        "s2": StepInvocationInputs(
            target=str(c["workspace_root"] / "second.json"),
            args={"resource": StepOutputReference("s1", "out.s1"),
                  "category": "tools", "nested/key": {"a~b": [42, True]}},
            outputs={"out.s2": StepOutputSelector(json_pointer="/listing")}),
    }


def test_distinct_targets_args_and_dataflow_register(intake):
    c = intake
    result = _two_step_result(c)
    inputs = materialize_admission_inputs(
        result, workspace_root=c["workspace_root"], user_inputs={},
        step_inputs=_step_inputs(c), issued_at_ms=5200, expires_at_ms=5800)
    assert _register(c, result, **inputs).registered
    first, second = inputs["step_bindings"]
    assert first.target_skeleton != second.target_skeleton
    assert first.args_skeleton == {"category": None}
    assert second.args_skeleton == {
        "category": None, "resource": None,
        "nested/key": {"a~b": [None, None]}}
    assert inputs["plan_inputs"] == ()
    assert first.argument_slots[0].value_binding.value == "registry"
    slots = {item.destination_json_pointer: item.value_binding
             for item in second.argument_slots}
    reference = slots["/resource"]
    assert reference.producer_step_id == "s1"
    assert reference.output_declaration_sha256 == first.output_declarations[0].sha256
    assert slots["/category"].value == "tools"
    assert slots["/nested~1key/a~0b/0"].value == 42
    assert slots["/nested~1key/a~0b/1"].value is True
    assert all(item.value_binding.has_valid_sha256() for item in second.argument_slots)
    overrides = _step_inputs(c)
    overrides["s1"] = replace(overrides["s1"], outputs={
        "out.s1": StepOutputSelector(json_pointer="/listing",
                                     value_schema_sha256="a" * 64)})
    _, overridden_steps, _ = materialize_step_bindings(
        result, user_inputs={}, step_inputs=overrides)
    assert overridden_steps[0].output_declarations[0].value_schema_sha256 == "a" * 64


def test_output_reference_without_dependency_is_rejected(intake):
    c = intake
    result = _two_step_result(c, depends_on=())
    with pytest.raises(AdmissionMaterializationError,
                       match="admission.output_dependency_missing"):
        materialize_step_bindings(result, user_inputs={}, step_inputs=_step_inputs(c))


@pytest.mark.parametrize("case", ["missing_step", "extra_step", "raw_object", "mixed_inputs"])
def test_incomplete_or_ambiguous_step_input_is_rejected(intake, case):
    c = intake
    result = _two_step_result(c)
    steps = _step_inputs(c)
    user_inputs = {}
    if case == "missing_step":
        del steps["s2"]
    elif case == "extra_step":
        steps["s3"] = StepInvocationInputs()
    elif case == "raw_object":
        steps["s2"] = {"target": "", "args": {}}
    else:
        user_inputs = {"category": "shared"}
    with pytest.raises(AdmissionMaterializationError, match="admission.(step_inputs|input_modes)"):
        materialize_step_bindings(result, user_inputs=user_inputs, step_inputs=steps)


def test_model_shaped_reference_remains_literal_and_unused_output_is_rejected(intake):
    c = intake
    result = _two_step_result(c)
    steps = _step_inputs(c)
    steps["s2"] = replace(steps["s2"], args={"resource": {
        "producer_step_id": "s1", "output_binding_id": "out.s1"}})
    with pytest.raises(ValueError, match="every declared step output must feed"):
        materialize_step_bindings(result, user_inputs={}, step_inputs=steps)


def test_explicit_verification_predicate_survives_admission(intake):
    c = intake
    result = _compile(c, _prepare(c)[0], intents=frozenset(
        {"verification-intent:plan-bound-acceptance"}))
    predicate = AcceptancePredicate.create(
        predicate_type="repository.forbidden_paths_unchanged", subject_kind="repository",
        params={"paths": ["test_labels.py"]})
    evidence = tuple(VerificationIntentEvidenceV1(
        intent_ref=intent, predicate=predicate,
        subject_identity="repository:code-repair", evaluation_phase="POST_EXECUTION")
        for intent in result.plan.verification_intents)
    inputs = materialize_admission_inputs(
        result, workspace_root=c["workspace_root"], user_inputs={},
        intent_evidence=evidence, issued_at_ms=5200, expires_at_ms=5800)
    assert inputs["intent_evidence"] == evidence
    assert _register(c, result, **inputs).registered


def test_verifier_evidence_requires_exact_supported_system_binding(intake):
    c = intake
    result = _compile(c, _prepare(c)[0], intents=frozenset(
        {"verification-intent:plan-bound-acceptance"}))
    registry = VerifierRegistry.with_defaults().snapshot(captured_at_ms=5200)
    with pytest.raises(AdmissionMaterializationError,
                       match="admission.verifier_evidence_incomplete"):
        materialize_verifier_evidence(
            result, registry, subject_identity="unused", intent_evidence=())
    predicate = AcceptancePredicate.create(
        predicate_type="artifact.nonempty", subject_kind="artifact", params={})
    evidence = tuple(VerificationIntentEvidenceV1(
        intent_ref=intent, predicate=predicate,
        subject_identity="artifact:unsupported-by-this-registry", evaluation_phase="POST_EXECUTION")
        for intent in result.plan.verification_intents)
    registry = VerifierRegistry(tuple(item for item in registry.verifiers
        if item.verifier_id != "verifier.artifact_content")).snapshot(captured_at_ms=5200)
    with pytest.raises(AdmissionMaterializationError,
                       match="admission.verifier_evidence_rejected"):
        materialize_verifier_evidence(
            result, registry, subject_identity="unused", intent_evidence=evidence)


def test_final_alias_cannot_be_silently_dropped(intake):
    c = intake
    result = _two_step_result(c)
    with pytest.raises(AdmissionMaterializationError,
                       match="admission.final_outputs_incomplete"):
        materialize_step_bindings(result, user_inputs={}, step_inputs=_step_inputs(c),
                                  final_outputs={"wrong-alias": StepOutputReference("s2", "out.s2")})
