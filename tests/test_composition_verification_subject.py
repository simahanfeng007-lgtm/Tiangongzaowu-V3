"""Aggregate read verification over real persisted admission and Effect/Fact rows.

Backend responses are deterministic fixtures; this is boundary verification,
not live model/task acceptance.  No synthetic PASS receipt is installed.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import json
from types import SimpleNamespace
import zipfile

import pytest

from contracts.verification import AcceptancePredicate
from contracts import canonical_json_bytes
from total_gateway.composition_verification_subject import (
    COMPOSITION_SUBJECT_PREFIX,
    CompositionVerificationSubjectError,
    evaluate_read_composition_subject,
)
from total_gateway.composition_registration_intake import VerificationIntentEvidenceV1
from total_gateway.outcome_oracles.effect_state import EffectStateOracle
from total_gateway.verification_plan_executor import VerificationPlanExecutor
from total_gateway.continuity import persist_terminal_completion
from total_gateway.desktop_completion import evaluate_desktop_completion
from total_gateway.desktop_api import DesktopApiRouter
from total_gateway.composition_final_result import encode_composition_final_result
from total_gateway.action_registry import compile_action_authority
from tests.test_source_registration_intake_p12 import _hashed
from tests import test_source_execution_roundtrip_p12 as roundtrip
from tests.test_source_execution_roundtrip_p12 import (  # noqa: F401
    intake_factory, intake, source, publication,
)


@pytest.fixture
def execution(monkeypatch, intake_factory, tmp_path):
    original = roundtrip._noarg_inputs
    original_backend = roundtrip._BackendFixture

    class FloatBackend(original_backend):
        def request(self, *args, **kwargs):
            status, value, _digest = super().request(*args, **kwargs)
            value["result"]["count"] = 1
            value["result"]["entries"] = [{"name": "native_0", "path": value["target"] + "/native_0",
                "rel_path": "native_0", "type": "file", "size_bytes": 1, "modified": 0.125}]
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            return status, value, hashlib.sha256(raw).hexdigest()

    def aggregate_inputs(c, result):
        values = original(c, result)
        # Bind aggregate verification to the explicit published entries schema,
        # including its native floating-point timestamp representation.
        with zipfile.ZipFile(c["tool_source"].bundle_path) as bundle:
            manifest = json.loads(bundle.read("build-report.json"))["build_artifact"]["gateway_manifest"]
        authority = compile_action_authority(manifest, generated_at_ms=0)
        step = values["step_bindings"][0]
        schema = authority.schema_catalog.resolve(step.action_id, step.action_version)
        selectors = [item for item in schema.value_schemas if item.source_kind == "RESULT_PAYLOAD"]
        selected = next(item for item in selectors if item.json_pointer == "/result/entries")
        output = _hashed(step.output_declarations[0].model_copy(update={
            "json_pointer": selected.json_pointer,
            "value_schema_sha256": selected.value_schema_sha256,
        }))
        values["step_bindings"] = (_hashed(step.model_copy(update={"output_declarations": (output,)})),)
        alias = values["final_output_aliases"][0]
        reference = _hashed(alias.value_binding.model_copy(update={"output_declaration_sha256": output.sha256}))
        values["final_output_aliases"] = (_hashed(alias.model_copy(update={"value_binding": reference})),)
        values["intent_evidence"] = tuple(
            VerificationIntentEvidenceV1(
                intent_ref=intent,
                predicate=AcceptancePredicate.create(
                    predicate_type="effect.terminal_succeeded", subject_kind="effect", params={},
                ),
                subject_identity=COMPOSITION_SUBJECT_PREFIX + result.plan.plan_id,
                evaluation_phase="POST_EXECUTION",
            )
            for intent in sorted(result.plan.verification_intents)
        )
        return values

    monkeypatch.setattr(roundtrip, "_noarg_inputs", aggregate_inputs)
    monkeypatch.setattr(roundtrip, "_BackendFixture", FloatBackend)
    yield from roundtrip.execution.__wrapped__(intake_factory, tmp_path)


def _inputs(c, *, dispatch=True):
    if dispatch:
        _receipt, _authorization, outcome, backend = roundtrip._authorize_and_dispatch(c)
        assert outcome.status == "SUCCEEDED"
    else:
        backend = SimpleNamespace(request=lambda *_args, **_kwargs: pytest.fail("verification dispatched a handler"))
    coordinator = roundtrip._coordinator(c, backend)
    plan = c["gateway"].get_verification_plan(c["admission"].verification_plan_id)
    snapshot = c["gateway"].get_verification_registry_snapshot_by_sha256(plan.registry_snapshot_sha256)
    return dict(
        subject=plan.entries[0].subject_identity, entry=plan.entries[0],
        verification_plan=plan, snapshot=snapshot, store=c["gateway"],
        effect_oracle=EffectStateOracle(snapshot=snapshot, store=c["gateway"]),
        projector=coordinator.project_plan, evaluated_at_ms=5500,
    ), coordinator, backend


def test_complete_authorized_read_receipts_produce_ready_aggregate(execution):
    c = execution
    # Admission alone cannot satisfy the terminal execution predicate.
    pending, _coordinator, _backend = _inputs(c, dispatch=False)
    evaluated = evaluate_read_composition_subject(**pending)
    assert evaluated.aggregate.status == "INCONCLUSIVE"
    assert evaluated.children == ()

    inputs, coordinator, backend = _inputs(c)
    _assert_missing_fact_rejected(c, inputs, backend)
    _assert_unbound_subjects_rejected(c, inputs)
    _assert_authorization_substitution_rejected(inputs)
    executor = VerificationPlanExecutor(
        snapshot=inputs["snapshot"], store=c["gateway"], object_store=c["objects"],
        fact_ledger=c["facts"], plan=inputs["verification_plan"],
        composition_projector=coordinator.project_plan,
    )
    readiness = executor.execute(evaluated_at_ms=5501)
    assert readiness.verification_ready, readiness.model_dump()
    assert backend.calls == 1, "oracle must never execute a tool"
    records = c["gateway"].list_verification_records(
        request_id=readiness.request_id, run_id=readiness.run_id, generation=readiness.generation,
    )
    aggregates = [r for r in records if r.subject_identity.startswith(COMPOSITION_SUBJECT_PREFIX)]
    children = [r for r in records if r.subject_identity.startswith("eff_")]
    assert aggregates and children
    passed = [r for r in records if r.evaluated_at_ms == 5501]
    assert passed and all(r.status == "PASS" and not r.model_generated for r in passed)
    aggregate_pass = next(r for r in aggregates if r.status == "PASS")
    assert any(ref.startswith("composition_child_records_sha256:") for ref in aggregate_pass.evidence_refs)

    # Exercise the actual Store -> CompletionDecision -> terminal capsule ->
    # desktop projection path too.  The parent Fact is deliberately unchanged.
    registered = c["gateway"].get_executable_composition_plan_for_request(
        readiness.request_id, run_id=readiness.run_id, generation=readiness.generation,
    ).executable_plan
    finalized = coordinator.finalize_plan(registered)
    reply = encode_composition_final_result(
        finalized.final_output_aliases, parent_reply="registered; child outputs follow",
    )
    decision = evaluate_desktop_completion(
        objects=c["objects"], facts=c["facts"], request_id=readiness.request_id,
        run_id=readiness.run_id, generation=readiness.generation,
        execution_effect_ids=finalized.leaf_effect_ids,
        execution_lineage_effect_ids=tuple(sorted(set((finalized.parent_effect_id, *finalized.lineage_effect_ids)))),
        candidate_text=reply, artifacts=(), head_state_reader=c["gateway"].get_effect_head_state,
        verification_readiness=readiness, active_plan=inputs["verification_plan"],
        readiness_authority_reader=c["gateway"].get_latest_verification_readiness,
    )
    terminal = persist_terminal_completion(
        c["gateway"], decision, life_id="life_r1c4", user_goal="read fixture result",
        final_result=reply, created_at_ms=5502, verified_fact_ids=("fact_desktop_fixture",),
    )
    assert set(decision.supporting_fact_ids).issubset(terminal.capsule.verified_fact_ids)
    router = object.__new__(DesktopApiRouter)
    router._runtime = SimpleNamespace(store=c["gateway"], facts=c["facts"], objects=c["objects"])
    payload = router._desktop_result_payload(readiness.request_id, (SimpleNamespace(
        machine="request", state="COMPLETED", run_id=readiness.run_id, generation=readiness.generation,
    ),))
    assert payload["composition_final_output_aliases"] == finalized.final_output_aliases
    assert "native_0" in payload["reply_text"]
    assert "0.125" in payload["reply_text"]
    assert "registered; child outputs follow" not in payload["reply_text"]
    assert payload["task_completed"] is False


def _assert_missing_fact_rejected(c, base_inputs, backend):
    inputs = dict(base_inputs)
    class MissingFacts:
        def __getattr__(self, name):
            return getattr(c["facts"], name)

        def get_batch_for_effect(self, *_args, **_kwargs):
            return None

    missing_facts = MissingFacts()
    coordinator = roundtrip._coordinator(c, backend, facts=missing_facts)
    inputs["projector"] = coordinator.project_plan
    with pytest.raises(CompositionVerificationSubjectError, match="success_fact_missing"):
        evaluate_read_composition_subject(**inputs)


def _assert_unbound_subjects_rejected(c, base_inputs):
    for mutation in ("subject", "request", "run", "generation", "verification_plan", "projector"):
        inputs = dict(base_inputs)
        if mutation == "subject":
            inputs["subject"] = COMPOSITION_SUBJECT_PREFIX + "other-plan"
        elif mutation in {"request", "run", "generation"}:
            field, value = {
                "request": ("request_id", "req_" + "9" * 64),
                "run": ("run_id", "run_" + "9" * 64),
                "generation": ("generation", 999),
            }[mutation]
            changed = inputs["verification_plan"].model_copy(update={field: value}).with_computed_sha256()
            inputs["verification_plan"] = changed
            # A deliberately wrong store response still cannot grant verification.
            actual = c["gateway"].get_executable_composition_plan_for_request(
                c["rc"].request_id, run_id=c["rc"].run_id, generation=c["rc"].generation,
            )
            inputs["store"] = SimpleNamespace(get_executable_composition_plan_for_request=lambda *_args, **_kwargs: actual)
        elif mutation == "verification_plan":
            inputs["verification_plan"] = inputs["verification_plan"].model_copy(
                update={"registry_snapshot_sha256": "9" * 64}
            ).with_computed_sha256()
        else:
            inputs["projector"] = None
        with pytest.raises(CompositionVerificationSubjectError):
            evaluate_read_composition_subject(**inputs)


def _assert_authorization_substitution_rejected(base_inputs):
    inputs = dict(base_inputs)
    original = inputs["store"]

    class WrongAuthorizationStore:
        def __getattr__(self, name):
            return getattr(original, name)

        def get_current_composition_step_authorization(self, *args, **kwargs):
            record = original.get_current_composition_step_authorization(*args, **kwargs)
            # Keep the real record untouched.  A corrupt read projection is
            # rejected even before the effect oracle is asked about its row.
            request = {field.name: getattr(record.request, field.name) for field in fields(record.request)}
            request["prebound_effect_id"] = "eff_" + "9" * 64
            return SimpleNamespace(authorization_id=record.authorization_id,
                                   request=SimpleNamespace(**request))

    inputs["store"] = WrongAuthorizationStore()
    with pytest.raises(CompositionVerificationSubjectError, match="authorization_binding_invalid"):
        evaluate_read_composition_subject(**inputs)
