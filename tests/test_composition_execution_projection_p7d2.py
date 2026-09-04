from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts import (
    ExecutionResult,
    FactRecord,
    canonical_sha256,
    derive_effect_identity,
)
from total_gateway.composition_execution_projection import (
    CompositionAttemptObservationV1,
    CompositionExecutionProjectionError,
    derive_composition_execution_projection,
    materialize_ready_composition_step,
    resolve_final_output_aliases,
)
from total_gateway.composition_execution_binding import (
    COMPOSITION_STEP_PIPELINE_VERSION,
)
from total_gateway.completion_gate import (
    CompletionGate,
    CompletionRequirements,
)
from total_gateway.composition_executable_plan import (
    computed_execution_bindings_sha256,
)
from total_gateway.composition_backend_transport import (
    COMPOSITION_RESULT_PAYLOAD_SCHEMA,
)
from total_gateway.composition_step_execution import (
    CompositionStepExecutionCoordinator,
    CompositionStepExecutionError,
)
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.fact_ledger import FactBatchRecord
from total_gateway.object_store import ObjectReference, derive_object_reference_id
from total_gateway.store import EffectLedgerRecord, GatewayStateStore

from tests.test_composition_executable_plan_p7c0 import (
    _compile_executable,
    _compile_material,
)


H = "a" * 64


def _plan(tmp_path: Path):
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    material = _compile_material(store, tmp_path)
    return store, _compile_executable(material)


def _parallel_root_plan(plan, *, step_count: int = 128):
    """Expand one sealed fixture into the contract's maximum root-only DAG."""

    template = plan.step_bindings[0]
    steps = tuple(
        template.model_copy(
            update={
                "step_id": f"step.{index:03d}",
                "candidate_id": f"candidate.parallel.{index:03d}",
                "depends_on": (),
                "args_skeleton": {
                    "artifact_id": f"artifact-{index:03d}",
                    "mode": "metadata-only",
                },
                "argument_slots": (),
                "output_declarations": (),
                "sha256": "0" * 64,
            }
        ).with_computed_sha256()
        for index in range(step_count)
    )
    expanded = plan.model_copy(
        update={
            "plan_inputs": (),
            "step_bindings": steps,
            "final_output_aliases": (),
        }
    )
    bindings_sha256 = computed_execution_bindings_sha256(
        workspace=expanded.workspace,
        plan_inputs=expanded.plan_inputs,
        step_bindings=expanded.step_bindings,
        final_output_aliases=expanded.final_output_aliases,
    )
    return expanded.model_copy(
        update={"execution_bindings_sha256": bindings_sha256}
    ).with_computed_identity()


def _batch_sha256(
    result: ExecutionResult,
    facts: tuple[FactRecord, ...],
    *,
    observed_at_ms: int,
    tenant_id: str,
    link_account_id: str,
    conversation_scope_hash: str,
    workspace_id: str,
    max_output_bytes: int,
    result_payload_object_id: str,
    result_payload_sha256: str,
    response_sha256: str,
) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.gateway.execution-fact-batch.v1",
            "result_sha256": canonical_sha256(result.model_dump(mode="json")),
            "fact_sha256s": tuple(item.fact_sha256 for item in facts),
            "source_component_id": "tiangong-backend",
            "observed_at_ms": observed_at_ms,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_scope_hash": conversation_scope_hash,
            "workspace_id": workspace_id,
            "max_output_bytes": max_output_bytes,
            "result_payload_object_id": result_payload_object_id,
            "result_payload_sha256": result_payload_sha256,
            "response_sha256": response_sha256,
        }
    )


def _succeeded_observation(
    plan,
    step_id: str,
    payload: object,
    *,
    fact_count: int = 1,
    attempt: int = 1,
    supersedes: CompositionAttemptObservationV1 | None = None,
) -> CompositionAttemptObservationV1:
    step = next(item for item in plan.step_bindings if item.step_id == step_id)
    ordinal = next(
        index for index, item in enumerate(plan.step_bindings) if item.step_id == step_id
    )
    intent_sha256 = canonical_sha256(
        {"case": "p7d2-projection", "step_id": step_id, "attempt": attempt}
    )
    identity = derive_effect_identity(
        request_id=plan.request_id,
        run_id=plan.run_id,
        run_sequence=1,
        generation=plan.generation,
        effect_kind="execution",
        ordinal=ordinal + 1,
        intent_sha256=intent_sha256,
    )
    claim = EffectClaim(
        effect_id=identity.effect_id,
        request_id=plan.request_id,
        run_id=plan.run_id,
        run_sequence=1,
        generation=plan.generation,
        effect_kind="execution",
        ordinal=ordinal + 1,
        intent_sha256=intent_sha256,
        pipeline_version=COMPOSITION_STEP_PIPELINE_VERSION,
        attempt=attempt,
        claim_revision=attempt,
        supersedes_claim_sha256=(
            None
            if supersedes is None
            else supersedes.effect.claim.claim_sha256
        ),
        owner_component_id="tiangong-backend",
        claimed_at_ms=2_000 + ordinal,
        claim_sha256="0" * 64,
    ).with_computed_sha256()
    raw_payload = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    raw_payload_sha256 = hashlib.sha256(raw_payload).hexdigest()
    fact_payload = {
        "schema": COMPOSITION_RESULT_PAYLOAD_SCHEMA,
        "backend_http_status": 200,
        "backend_response_sha256": raw_payload_sha256,
        "execution_boundary": "embedded-omni-body-composition-v1",
        "omni_ok": True,
        "omni_result_json": raw_payload.decode("utf-8"),
        "omni_result_sha256": raw_payload_sha256,
        "omni_result_size_bytes": len(raw_payload),
    }
    payload_sha256 = canonical_sha256(fact_payload)
    fact_ids = tuple(
        sorted(f"fact.p7d2.{step_id}.{index}" for index in range(fact_count))
    )
    result = ExecutionResult(
        result_id=f"execution_result_p7d2_{step_id}_{attempt}",
        ticket_id=f"ticket_p7d2_{step_id}_{attempt}",
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        effect_id=claim.effect_id,
        action_id=step.action_id,
        action_version=step.action_version,
        status="SUCCEEDED",
        attempt=attempt,
        started_at_ms=2_100 + ordinal,
        finished_at_ms=2_200 + ordinal,
        side_effect_started=True,
        result_payload_sha256=payload_sha256,
        receipt_sha256=H,
        output_object_refs=(),
        fact_ids=fact_ids,
    )
    facts = tuple(
        FactRecord(
            fact_id=fact_id,
            fact_type="execution.succeeded",
            source_component_id="tiangong-backend",
            request_id=result.request_id,
            run_id=result.run_id,
            generation=result.generation,
            ticket_id=result.ticket_id,
            effect_id=result.effect_id,
            action_id=result.action_id,
            action_version=result.action_version,
            observed_at_ms=result.finished_at_ms,
            payload_sha256=payload_sha256,
            evidence_sha256="b" * 64,
            verification_method="component_receipt",
            fact_sha256="0" * 64,
        ).with_computed_sha256()
        for fact_id in fact_ids
    )
    result_payload_object_id = f"object.p7d2.{step_id}.{attempt}"
    response_sha256 = "b" * 64
    tenant_id = "tenant.p7d2"
    link_account_id = "account.p7d2"
    conversation_scope_hash = "c" * 64
    max_output_bytes = 1_000_000
    observed_at_ms = result.finished_at_ms
    batch_sha256 = _batch_sha256(
        result,
        facts,
        observed_at_ms=observed_at_ms,
        tenant_id=tenant_id,
        link_account_id=link_account_id,
        conversation_scope_hash=conversation_scope_hash,
        workspace_id=plan.workspace.workspace_id,
        max_output_bytes=max_output_bytes,
        result_payload_object_id=result_payload_object_id,
        result_payload_sha256=payload_sha256,
        response_sha256=response_sha256,
    )
    batch = FactBatchRecord(
        result=result,
        facts=facts,
        source_component_id="tiangong-backend",
        observed_at_ms=observed_at_ms,
        tenant_id=tenant_id,
        link_account_id=link_account_id,
        conversation_scope_hash=conversation_scope_hash,
        workspace_id=plan.workspace.workspace_id,
        max_output_bytes=max_output_bytes,
        result_payload_object_id=result_payload_object_id,
        result_payload_sha256=payload_sha256,
        response_sha256=response_sha256,
        batch_sha256=batch_sha256,
    )
    effect_result = EffectResult(
        result_id="effect-result-" + result.result_id[:120],
        effect_id=claim.effect_id,
        status="SUCCEEDED",
        fact_id=fact_ids[0],
        result_object_id=result_payload_object_id,
        result_object_sha256=payload_sha256,
        evidence_sha256=batch_sha256,
        observed_at_ms=observed_at_ms,
        result_sha256="0" * 64,
    ).with_computed_sha256()
    effect = EffectLedgerRecord(
        claim=claim,
        state="SUCCEEDED",
        side_effect_started_at_ms=result.started_at_ms,
        completed_at_ms=result.finished_at_ms,
        result=effect_result,
    )
    return CompositionAttemptObservationV1(
        authorization_id=f"csa.p7d2.{step_id}.{attempt}",
        step_id=step_id,
        attempt=attempt,
        prebound_effect_id=claim.effect_id,
        supersedes_authorization_id=(
            None if supersedes is None else supersedes.authorization_id
        ),
        supersedes_effect_id=(
            None if supersedes is None else supersedes.prebound_effect_id
        ),
        supersedes_claim_sha256=(
            None
            if supersedes is None
            else supersedes.effect.claim.claim_sha256
        ),
        effect=effect,
        fact_batch=batch,
        result_payload=fact_payload,
    )


def _failed_observation(
    plan,
    step_id: str,
    *,
    code: str = "composition.test.failed",
) -> CompositionAttemptObservationV1:
    succeeded = _succeeded_observation(plan, step_id, {"unused": True})
    fragment = succeeded.prebound_effect_id[4:20]
    observed_at_ms = succeeded.effect.completed_at_ms
    terminal = EffectResult(
        result_id="effect-result-" + fragment,
        effect_id=succeeded.prebound_effect_id,
        status="FAILED_FINAL",
        fact_id="fact-effect-" + fragment,
        result_object_id=None,
        result_object_sha256=None,
        evidence_sha256=canonical_sha256(
            {
                "authorization_id": succeeded.authorization_id,
                "code": code,
                "status": "FAILED_FINAL",
            }
        ),
        error_code=code,
        observed_at_ms=observed_at_ms,
        result_sha256="0" * 64,
    ).with_computed_sha256()
    return replace(
        succeeded,
        effect=EffectLedgerRecord(
            claim=succeeded.effect.claim,
            state="FAILED_FINAL",
            side_effect_started_at_ms=None,
            completed_at_ms=observed_at_ms,
            result=terminal,
        ),
        fact_batch=None,
    )


def _prestart_superseded_observation(
    predecessor: CompositionAttemptObservationV1,
    successor: CompositionAttemptObservationV1,
) -> CompositionAttemptObservationV1:
    """Give attempt 1 the exact durable disposition required by attempt 2."""

    assert predecessor.effect is not None
    assert predecessor.effect.result is not None
    observed_at_ms = predecessor.effect.result.observed_at_ms
    evidence_sha256 = canonical_sha256(
        {
            "domain": "tiangong.composition-prestart-supersession.v1",
            "predecessor_authorization_id": predecessor.authorization_id,
            "predecessor_effect_id": predecessor.prebound_effect_id,
            "predecessor_claim_sha256": predecessor.effect.claim.claim_sha256,
            "successor_authorization_id": successor.authorization_id,
            "successor_effect_id": successor.prebound_effect_id,
            "superseded_at_ms": observed_at_ms,
            "handler_count": 0,
            "fact_ledger_atomicity_claimed": False,
        }
    )
    terminal = EffectResult(
        result_id=(
            "composition-prestart-superseded-"
            + predecessor.authorization_id
        ),
        effect_id=predecessor.prebound_effect_id,
        status="FAILED_FINAL",
        fact_id=(
            "composition-prestart-disposition-"
            + predecessor.authorization_id
        ),
        result_object_id=None,
        result_object_sha256=None,
        evidence_sha256=evidence_sha256,
        error_code="composition.authorization.prestart_superseded",
        observed_at_ms=observed_at_ms,
        result_sha256="0" * 64,
    ).with_computed_sha256()
    return replace(
        predecessor,
        effect=EffectLedgerRecord(
            claim=predecessor.effect.claim,
            state="FAILED_FINAL",
            side_effect_started_at_ms=None,
            completed_at_ms=observed_at_ms,
            result=terminal,
        ),
        fact_batch=None,
    )


class _BoundaryFactLedger:
    def __init__(self, batches: tuple[FactBatchRecord, ...]) -> None:
        self._by_effect = {
            item.result.effect_id: item
            for item in batches
        }

    def list_request_facts(self, request_id, *, run_id, generation):
        return tuple(
            fact
            for batch in self._by_effect.values()
            if (
                batch.result.request_id,
                batch.result.run_id,
                batch.result.generation,
            )
            == (request_id, run_id, generation)
            for fact in batch.facts
        )

    def get_batch_for_effect(self, effect_id, *, verify_payload=True):
        assert verify_payload is True
        return self._by_effect.get(effect_id)


def _parent_success_batch(
    template: FactBatchRecord,
    *,
    parent_effect_id: str,
) -> FactBatchRecord:
    fact_id = "fact.p7d2.parent"
    ticket_id = "ticket.p7d2.parent"
    result = template.result.model_copy(
        update={
            "result_id": "execution_result_p7d2_parent",
            "ticket_id": ticket_id,
            "effect_id": parent_effect_id,
            "fact_ids": (fact_id,),
        }
    )
    fact = template.facts[0].model_copy(
        update={
            "fact_id": fact_id,
            "ticket_id": ticket_id,
            "effect_id": parent_effect_id,
            "fact_sha256": "0" * 64,
        }
    ).with_computed_sha256()
    return replace(template, result=result, facts=(fact,))


def _accept_value(_schema_sha256: str, _value: object) -> None:
    return None


def _accept_result(
    _action_id: str,
    _action_version: str,
    _result_schema_sha256: str,
    _value: object,
) -> None:
    return None


def _value_schema_resolver(plan):
    def resolve(action_id: str, action_version: str, digest: str):
        matches = [
            declaration
            for step in plan.step_bindings
            if step.action_id == action_id
            and step.action_version == action_version
            for declaration in step.output_declarations
            if declaration.value_schema_sha256 == digest
        ]
        if len(matches) != 1:
            raise ValueError("value schema missing")
        return SimpleNamespace(
            source_kind=matches[0].source_kind,
            json_pointer=matches[0].json_pointer,
        )

    return resolve


def _plan_with_first_output_selector(plan, *, source_kind: str, ordinal: int):
    first, second = plan.step_bindings
    declaration = first.output_declarations[0].model_copy(
        update={
            "source_kind": source_kind,
            "json_pointer": None,
            "ordinal": ordinal,
        }
    ).with_computed_sha256()
    first = first.model_copy(
        update={"output_declarations": (declaration,)}
    ).with_computed_sha256()
    reference = second.argument_slots[0].value_binding.model_copy(
        update={"output_declaration_sha256": declaration.sha256}
    ).with_computed_sha256()
    slot = second.argument_slots[0].model_copy(
        update={"value_binding": reference}
    ).with_computed_sha256()
    second = second.model_copy(update={"argument_slots": (slot,)}).with_computed_sha256()
    steps = (first, second)
    updated = plan.model_copy(update={"step_bindings": steps})
    bindings_sha256 = computed_execution_bindings_sha256(
        workspace=updated.workspace,
        plan_inputs=updated.plan_inputs,
        step_bindings=steps,
        final_output_aliases=updated.final_output_aliases,
    )
    return updated.model_copy(
        update={"execution_bindings_sha256": bindings_sha256}
    ).with_computed_identity()


def _with_output_reference(
    observation: CompositionAttemptObservationV1,
) -> tuple[CompositionAttemptObservationV1, ObjectReference]:
    assert observation.fact_batch is not None
    assert observation.effect is not None
    assert observation.effect.result is not None
    batch = observation.fact_batch
    content_sha256 = "d" * 64
    reference = ObjectReference(
        object_id=derive_object_reference_id(
            kind="artifact",
            content_sha256=content_sha256,
            tenant_id=batch.tenant_id,
            link_account_id=batch.link_account_id,
            conversation_scope_hash=batch.conversation_scope_hash,
        ),
        content_object_id="obj_" + content_sha256,
        kind="artifact",
        sha256=content_sha256,
        size_bytes=1,
        tenant_id=batch.tenant_id,
        link_account_id=batch.link_account_id,
        conversation_scope_hash=batch.conversation_scope_hash,
        created_at_ms=batch.observed_at_ms,
        reference_sha256="0" * 64,
    ).with_computed_sha256()
    result = batch.result.model_copy(
        update={"output_object_refs": (reference.object_id,)}
    )
    batch = replace(
        batch,
        result=result,
        batch_sha256=_batch_sha256(
            result,
            batch.facts,
            observed_at_ms=batch.observed_at_ms,
            tenant_id=batch.tenant_id,
            link_account_id=batch.link_account_id,
            conversation_scope_hash=batch.conversation_scope_hash,
            workspace_id=batch.workspace_id,
            max_output_bytes=batch.max_output_bytes,
            result_payload_object_id=batch.result_payload_object_id,
            result_payload_sha256=batch.result_payload_sha256,
            response_sha256=batch.response_sha256,
        ),
    )
    terminal = observation.effect.result.model_copy(
        update={
            "evidence_sha256": batch.batch_sha256,
            "result_sha256": "0" * 64,
        }
    ).with_computed_sha256()
    return (
        replace(
            observation,
            effect=replace(observation.effect, result=terminal),
            fact_batch=batch,
            output_object_references=(reference,),
        ),
        reference,
    )


def test_projection_derives_ready_order_and_graph_leaves(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        initial = derive_composition_execution_projection(
            plan, (), validate_result=_accept_result
        )
        assert tuple(item.state for item in initial.steps) == (
            "READY_UNAUTHORIZED",
            "WAITING_DEPENDENCIES",
        )
        assert initial.next_step_id == "step.01"
        assert initial.leaf_step_ids == ("step.02",)
        assert initial.leaf_effect_ids == ()

        first = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )
        after_first = derive_composition_execution_projection(
            plan, (first,), validate_result=_accept_result
        )
        assert tuple(item.state for item in after_first.steps) == (
            "SUCCEEDED",
            "READY_UNAUTHORIZED",
        )
        assert after_first.next_step_id == "step.02"

        dispatch = materialize_ready_composition_step(
            plan,
            step_id="step.02",
            committed={"step.01": first},
            validate_value=_accept_value,
            validate_result=_accept_result,
            resolve_value_schema=_value_schema_resolver(plan),
        )
        assert dispatch.step.arguments == {
            "upstream_artifact_id": "artifact-from-fact"
        }
        assert tuple(item.producer_step_id for item in dispatch.dependency_evidence) == (
            "step.01",
        )
        assert dispatch.dependency_evidence_sha256 == canonical_sha256(
            tuple(item.payload() for item in dispatch.dependency_evidence)
        )
    finally:
        store.close()


def test_projection_is_independent_of_non_topological_plan_tuple_order(
    tmp_path: Path,
) -> None:
    store, plan = _plan(tmp_path)
    try:
        reversed_plan = plan.model_copy(
            update={"step_bindings": tuple(reversed(plan.step_bindings))}
        ).with_computed_identity()
        first = _succeeded_observation(
            reversed_plan,
            "step.01",
            {"artifact": {"id": "artifact-from-fact"}},
        )

        projection = derive_composition_execution_projection(
            reversed_plan,
            (first,),
            validate_result=_accept_result,
        )

        assert tuple(item.step_id for item in projection.steps) == (
            "step.02",
            "step.01",
        )
        assert tuple(item.state for item in projection.steps) == (
            "READY_UNAUTHORIZED",
            "SUCCEEDED",
        )
        assert projection.next_step_id == "step.02"
    finally:
        store.close()


def test_all_leaf_success_and_final_alias_use_exact_fact_payload(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        first = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )
        second = _succeeded_observation(plan, "step.02", {"verified": True})
        projection = derive_composition_execution_projection(
            plan, (first, second), validate_result=_accept_result
        )
        assert projection.all_steps_succeeded is True
        assert projection.next_step_id is None
        assert projection.leaf_effect_ids == (second.prebound_effect_id,)
        aliases = resolve_final_output_aliases(
            plan,
            committed={"step.01": first, "step.02": second},
            validate_value=_accept_value,
            validate_result=_accept_result,
            resolve_value_schema=_value_schema_resolver(plan),
        )
        assert aliases == {plan.final_output_aliases[0].alias: True}
    finally:
        store.close()


def test_final_aliases_reject_partial_committed_step_set(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        alias_producer = _succeeded_observation(
            plan,
            "step.02",
            {"verified": True},
        )
        with pytest.raises(
            CompositionExecutionProjectionError,
            match="composition.projection.final_outputs_before_completion",
        ):
            resolve_final_output_aliases(
                plan,
                committed={"step.02": alias_producer},
                validate_value=_accept_value,
                validate_result=_accept_result,
                resolve_value_schema=_value_schema_resolver(plan),
            )
    finally:
        store.close()


def _finalization_coordinator(plan, observations):
    coordinator = object.__new__(CompositionStepExecutionCoordinator)
    coordinator._observations_for_plan = lambda _plan: observations
    coordinator._validate_result_exact = _accept_result
    coordinator._schemas = SimpleNamespace(
        validate_value_exact=_accept_value,
        resolve_value_schema=_value_schema_resolver(plan),
    )
    records = tuple(
        SimpleNamespace(
            authorization_id=item.authorization_id,
            request=SimpleNamespace(step_id=item.step_id),
        )
        for item in observations
    )
    coordinator._store = SimpleNamespace(
        list_composition_authorizations_for_plan=(
            lambda _plan_id, *, current_only: records
        )
    )
    coordinator._verify_parent_success = (
        lambda _request: "eff_" + "f" * 64
    )
    return coordinator


def test_finalization_exports_exact_leaves_aliases_and_attempt_lineage(
    tmp_path: Path,
) -> None:
    store, plan = _plan(tmp_path)
    try:
        first = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )
        second = _succeeded_observation(plan, "step.02", {"verified": True})
        coordinator = _finalization_coordinator(plan, (first, second))

        finalization = coordinator.finalize_plan(plan)

        assert finalization.parent_effect_id == "eff_" + "f" * 64
        assert finalization.leaf_effect_ids == (second.prebound_effect_id,)
        assert finalization.lineage_effect_ids == (
            first.prebound_effect_id,
            second.prebound_effect_id,
        )
        assert finalization.fact_ids == (
            *first.fact_batch.result.fact_ids,
            *second.fact_batch.result.fact_ids,
        )
        assert finalization.completed_at_ms == max(
            first.fact_batch.observed_at_ms,
            first.fact_batch.result.finished_at_ms,
            second.fact_batch.observed_at_ms,
            second.fact_batch.result.finished_at_ms,
        )
        assert finalization.final_output_aliases == {
            plan.final_output_aliases[0].alias: True
        }
    finally:
        store.close()


def test_128_parallel_roots_finalize_256_attempts_and_complete_at_257_boundary(
    tmp_path: Path,
) -> None:
    store, fixture_plan = _plan(tmp_path)
    try:
        plan = _parallel_root_plan(fixture_plan)
        observations: list[CompositionAttemptObservationV1] = []
        successful: list[CompositionAttemptObservationV1] = []
        for index, step in enumerate(plan.step_bindings):
            predecessor = _succeeded_observation(
                plan,
                step.step_id,
                {"root": index, "attempt": 1},
            )
            successor = _succeeded_observation(
                plan,
                step.step_id,
                {"root": index, "attempt": 2},
                attempt=2,
                supersedes=predecessor,
            )
            observations.extend(
                (_prestart_superseded_observation(predecessor, successor), successor)
            )
            successful.append(successor)

        coordinator = _finalization_coordinator(plan, tuple(observations))
        finalization = coordinator.finalize_plan(plan)

        assert len(plan.step_bindings) == 128
        assert len(finalization.leaf_effect_ids) == 128
        assert len(set(finalization.leaf_effect_ids)) == 128
        assert len(finalization.lineage_effect_ids) == 256
        assert len(set(finalization.lineage_effect_ids)) == 256
        assert finalization.leaf_effect_ids == tuple(
            item.prebound_effect_id for item in successful
        )
        assert finalization.lineage_effect_ids == tuple(
            item.prebound_effect_id for item in observations
        )
        assert finalization.final_output_aliases == {}

        parent_batch = _parent_success_batch(
            successful[0].fact_batch,
            parent_effect_id=finalization.parent_effect_id,
        )
        successful_batches = tuple(item.fact_batch for item in successful)
        assert all(item is not None for item in successful_batches)
        ledger = _BoundaryFactLedger(
            (parent_batch, *successful_batches)  # type: ignore[arg-type]
        )
        required = tuple(
            sorted(
                (
                    finalization.parent_effect_id,
                    *finalization.leaf_effect_ids,
                )
            )
        )
        exact_lineage = tuple(
            sorted(
                (
                    finalization.parent_effect_id,
                    *finalization.lineage_effect_ids,
                )
            )
        )
        assert len(required) == 129
        assert len(exact_lineage) == 257
        head_states = {
            finalization.parent_effect_id: "SUCCEEDED",
            **{
                item.prebound_effect_id: (
                    "SUCCEEDED" if item.attempt == 2 else "FAILED_FINAL"
                )
                for item in observations
            },
        }
        gate = CompletionGate(
            object(),
            ledger,
            head_state_reader=head_states.get,
        )
        decision = gate.evaluate(
            CompletionRequirements(
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
                required_execution_effect_ids=required,
                execution_lineage_effect_ids=exact_lineage,
            )
        )
        assert decision.outcome == "COMPLETED"
        assert decision.can_transition_request_completed is True

        overflow_effect_id = "eff_" + "0" * 64
        assert overflow_effect_id not in exact_lineage
        with pytest.raises(ValidationError):
            CompletionRequirements(
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
                required_execution_effect_ids=required,
                execution_lineage_effect_ids=tuple(
                    sorted((*exact_lineage, overflow_effect_id))
                ),
            )
    finally:
        store.close()


def test_finalization_rejects_an_incomplete_dag(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        first = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )
        coordinator = _finalization_coordinator(plan, (first,))

        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.projection.final_outputs_before_completion",
        ):
            coordinator.finalize_plan(plan)
    finally:
        store.close()


def test_started_exact_fact_is_recoverable_but_never_replayed(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        committed = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )
        started = replace(
            committed,
            effect=replace(
                committed.effect,
                state="SIDE_EFFECT_STARTED",
                completed_at_ms=None,
                result=None,
            ),
        )
        projection = derive_composition_execution_projection(
            plan, (started,), validate_result=_accept_result
        )
        assert projection.steps[0].state == "STARTED_RECOVERABLE"
        assert projection.recoverable_step_ids == ("step.01",)
        assert projection.next_step_id is None
    finally:
        store.close()


def test_payload_or_fact_disagreement_blocks_descendants(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        committed = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )
        corrupt = replace(
            committed,
            result_payload={"artifact": {"id": "substituted"}},
        )
        projection = derive_composition_execution_projection(
            plan, (corrupt,), validate_result=_accept_result
        )
        assert projection.steps[0].state == "RECONCILE_REQUIRED"
        assert projection.steps[1].state == "WAITING_DEPENDENCIES"
        assert projection.reconcile_step_ids == ("step.01",)
        assert projection.next_step_id is None
    finally:
        store.close()


def test_terminal_failure_blocks_other_ready_branches(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        first, second = plan.step_bindings
        independent_second = second.model_copy(
            update={"depends_on": ()}
        ).with_computed_sha256()
        independent_plan = plan.model_copy(
            update={"step_bindings": (first, independent_second)}
        ).with_computed_identity()
        failed = _failed_observation(independent_plan, "step.01")

        projection = derive_composition_execution_projection(
            independent_plan,
            (failed,),
            validate_result=_accept_result,
        )

        assert tuple(item.state for item in projection.steps) == (
            "FAILED_FINAL",
            "READY_UNAUTHORIZED",
        )
        assert projection.failed_step_ids == ("step.01",)
        assert projection.next_step_id is None
    finally:
        store.close()


def test_invalid_prestart_successor_chain_is_rejected(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        predecessor = _succeeded_observation(
            plan,
            "step.01",
            {"artifact": {"id": "first"}},
        )
        successor = _succeeded_observation(
            plan,
            "step.01",
            {"artifact": {"id": "second"}},
            attempt=2,
            supersedes=predecessor,
        )

        with pytest.raises(
            CompositionExecutionProjectionError,
            match="composition.projection.attempt_chain_invalid",
        ):
            derive_composition_execution_projection(
                plan,
                (predecessor, successor),
                validate_result=_accept_result,
            )
    finally:
        store.close()


def test_step_output_value_schema_rejection_is_fail_closed(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        committed = _succeeded_observation(
            plan, "step.01", {"artifact": {"id": "artifact-from-fact"}}
        )

        def reject(_schema_sha256: str, _value: object) -> None:
            raise ValueError("wrong type")

        with pytest.raises(
            CompositionExecutionProjectionError,
            match="composition.projection.output_value_schema_rejected",
        ):
            materialize_ready_composition_step(
                plan,
                step_id="step.02",
                committed={"step.01": committed},
                validate_value=reject,
                validate_result=_accept_result,
                resolve_value_schema=_value_schema_resolver(plan),
            )
    finally:
        store.close()


def test_fact_id_step_output_uses_exact_committed_ordinal(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        plan = _plan_with_first_output_selector(
            plan,
            source_kind="FACT_ID",
            ordinal=1,
        )
        committed = _succeeded_observation(
            plan,
            "step.01",
            {"artifact": {"id": "ignored-for-fact-id"}},
            fact_count=2,
        )

        dispatch = materialize_ready_composition_step(
            plan,
            step_id="step.02",
            committed={"step.01": committed},
            validate_value=_accept_value,
            validate_result=_accept_result,
            resolve_value_schema=_value_schema_resolver(plan),
        )

        assert dispatch.step.arguments["upstream_artifact_id"] == (
            committed.fact_batch.facts[1].fact_id
        )
    finally:
        store.close()


def test_fact_id_step_output_rejects_out_of_range_ordinal(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        plan = _plan_with_first_output_selector(
            plan,
            source_kind="FACT_ID",
            ordinal=1,
        )
        committed = _succeeded_observation(
            plan,
            "step.01",
            {"artifact": {"id": "ignored-for-fact-id"}},
        )

        with pytest.raises(
            CompositionExecutionProjectionError,
            match="composition.projection.fact_ordinal_missing",
        ):
            materialize_ready_composition_step(
                plan,
                step_id="step.02",
                committed={"step.01": committed},
                validate_value=_accept_value,
                validate_result=_accept_result,
                resolve_value_schema=_value_schema_resolver(plan),
            )
    finally:
        store.close()


def test_output_object_ref_uses_exact_scoped_reference(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        plan = _plan_with_first_output_selector(
            plan,
            source_kind="OUTPUT_OBJECT_REF",
            ordinal=0,
        )
        committed, reference = _with_output_reference(
            _succeeded_observation(
                plan,
                "step.01",
                {"artifact": {"id": "ignored-for-object-ref"}},
            )
        )

        dispatch = materialize_ready_composition_step(
            plan,
            step_id="step.02",
            committed={"step.01": committed},
            validate_value=_accept_value,
            validate_result=_accept_result,
            resolve_value_schema=_value_schema_resolver(plan),
        )

        assert dispatch.step.arguments["upstream_artifact_id"] == reference.object_id
        assert dispatch.dependency_evidence[0].output_object_refs == (
            reference.object_id,
        )
        assert dispatch.dependency_evidence[0].output_object_reference_sha256s == (
            reference.reference_sha256,
        )
    finally:
        store.close()


def test_step_output_rejects_cross_action_selector_authority(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path)
    try:
        committed = _succeeded_observation(
            plan,
            "step.01",
            {"artifact": {"id": "artifact-from-fact"}},
        )

        def forged_resolver(_action_id: str, _version: str, _digest: str):
            return SimpleNamespace(source_kind="FACT_ID", json_pointer=None)

        with pytest.raises(
            CompositionExecutionProjectionError,
            match="composition.projection.output_selector_authority_mismatch",
        ):
            materialize_ready_composition_step(
                plan,
                step_id="step.02",
                committed={"step.01": committed},
                validate_value=_accept_value,
                validate_result=_accept_result,
                resolve_value_schema=forged_resolver,
            )
    finally:
        store.close()


def test_projection_has_no_second_frontier_or_scheduler_write_surface() -> None:
    path = (
        Path(__file__).parents[1]
        / "src"
        / "total_gateway"
        / "composition_execution_projection.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "RegenerativeExecutionAuthority" not in imported
    assert "regenerative_provider" not in imported
    assert "commit_execution_frontier" not in calls
    assert "commit_execution_frontier" not in path.read_text(encoding="utf-8")
