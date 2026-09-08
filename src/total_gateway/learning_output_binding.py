"""Existing Life/Gateway binding for P10 inputs; not an approval or Memory writer.

Selectors come from learning cards, but identities come from the signed Life
journal, configured World source resolver, registered plans and machine ledgers.
Only Knowledge uses the already-existing publication path. Source proposals are
still review material. Collected execution references still require P5/Memory
admission: missing provenance is never replaced with reassuring model flags.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
import re
from typing import Any

from contracts import canonical_sha256
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.learning_workflow import legacy_publication_blocked
from .composition_step_execution import CompositionStepExecutionCoordinator
from .learning_output_preparation import (
    LearningOutputBasisV1, learning_record_sha256,
    prepare_knowledge_learning_output, prepare_method_source_learning_output,
    prepare_tool_source_learning_output,
)
from .method_source_publication import MethodPublicationResolver, method_publication_envelope
from .method_source_run_binding import MethodRunSourceResolver
from .store import GatewayStateStore


class LearningOutputBindingError(ValueError):
    """Stable error code, without leaking source paths or untrusted text."""


def _waiting(reason: str) -> dict[str, Any]:
    return {"status": "SOURCE_INPUT_REQUIRED", "reason_code": reason,
            "may_publish": False, "may_authorize": False, "may_execute": False,
            "may_write_store": False, "context_section": "DATA"}


def collect_composition_learning_evidence(
    gateway: GatewayStateStore, coordinator: CompositionStepExecutionCoordinator,
    *, request_id: str, run_id: str, generation: int,
) -> dict[str, Any]:
    """Reconstruct machine references, accepting IDs rather than observations.

    Reuse the exact dispatch finalizer's Fact/Object validation and existing
    P19 authoritative readiness checker. No CompletionDecision is constructed
    here and no P5 success/provenance flags are inferred from an LLM summary.
    Missing P19/completion returns explicit non-admissible material, not PASS.
    """
    if (not isinstance(gateway, GatewayStateStore)
            or type(coordinator) is not CompositionStepExecutionCoordinator
            or coordinator._store is not gateway
            or type(request_id) is not str or re.fullmatch(r"req_[0-9a-f]{64}", request_id) is None
            or type(run_id) is not str or re.fullmatch(r"run_[0-9a-f]{64}", run_id) is None
            or type(generation) is not int or generation < 1):
        raise LearningOutputBindingError("learning_output.machine_authorities_or_identity_invalid")
    # This is the existing Gateway lock, not a second transaction/state owner.
    with gateway._lock:
        record = gateway.get_executable_composition_plan_for_request(
            request_id, run_id=run_id, generation=generation)
        if record is None:
            raise LearningOutputBindingError("learning_output.registered_plan_required")
        plan = record.executable_plan
        if (not plan.has_valid_identity() or (plan.request_id, plan.run_id, plan.generation)
                != (request_id, run_id, generation)):
            raise LearningOutputBindingError("learning_output.registered_plan_drift")
        # This reads committed Effect/Fact rows and verified object bytes; it
        # never dispatches a backend request or consumes another permission.
        final = coordinator.finalize_plan(plan)
        effect_ids = tuple(sorted({final.parent_effect_id, *final.lineage_effect_ids}))
        effects, facts = [], {}
        for effect_id in effect_ids:
            effect = gateway.get_effect(effect_id)
            batch = coordinator._facts.get_batch_for_effect(effect_id, verify_payload=True)
            if effect is None or batch is None:
                raise LearningOutputBindingError("learning_output.machine_effect_or_fact_missing")
            if (effect.claim.request_id, effect.claim.run_id, effect.claim.generation) != (request_id, run_id, generation):
                raise LearningOutputBindingError("learning_output.machine_effect_scope_mismatch")
            coordinator._load_fact_payload(batch)
            effects.append({"effect_id": effect_id, "state": effect.state,
                "fact_batch_sha256": batch.batch_sha256,
                "result_payload_sha256": batch.result_payload_sha256})
            for fact in batch.facts:
                facts[fact.fact_id] = canonical_sha256(fact.model_dump(mode="json"))
        decisions = tuple(r.decision for r in gateway.list_completion_decisions(
            request_id, run_id=run_id, generation=generation))
        if any(not d.has_valid_sha256() for d in decisions):
            raise LearningOutputBindingError("learning_output.completion_identity_invalid")
        completed = tuple(d for d in decisions if d.outcome == "COMPLETED")
        if len(completed) > 1:
            raise LearningOutputBindingError("learning_output.completion_ambiguous")
        decision = completed[0] if completed else None
        verification_plan = gateway.get_active_verification_plan(
            request_id=request_id, run_id=run_id, generation=generation)
        readiness = gateway.get_latest_verification_readiness(
            request_id=request_id, run_id=run_id, generation=generation, require_authoritative=True)
        blockers = ["P5_PROVENANCE_AND_MEMORY_ADMISSION_REQUIRED",
                    "CURRENT_SOURCE_REVALIDATION_REQUIRED", "MEMORY_PARENT_LINEAGE_AND_CAS_REQUIRED"]
        if decision is None:
            blockers.append("SEALED_COMPLETION_REQUIRED")
        elif (not decision.execution_ready or not decision.can_transition_request_completed
                or not set(final.fact_ids).issubset(decision.supporting_fact_ids)
                or not set(final.leaf_effect_ids).issubset(
                    {e for e, state in decision.execution_effect_states if state == "SUCCEEDED"})):
            raise LearningOutputBindingError("learning_output.completion_lineage_mismatch")
        if verification_plan is None or readiness is None or readiness.verification_ready is not True:
            blockers.append("AUTHORITATIVE_P19_READINESS_REQUIRED")
        elif (decision is None or decision.verification_mode != "PLAN_BOUND"
                or decision.verification_readiness_id != readiness.verification_readiness_id
                or decision.verification_readiness_sha256 != readiness.readiness_sha256
                or decision.verification_plan_sha256 != verification_plan.plan_sha256):
            blockers.append("COMPLETION_P19_BINDING_REQUIRED")
        payload = {"schema": "tiangong.learning-machine-evidence.v1",
            "status": "MACHINE_EVIDENCE_COLLECTED", "request_id": request_id, "run_id": run_id,
            "generation": generation, "executable_plan_id": plan.executable_plan_id,
            "executable_plan_sha256": plan.executable_plan_sha256,
            "principal_scope_hash": plan.principal_scope_hash,
            "workspace_id": plan.workspace.workspace_id,
            "world_state_sha256": plan.world_state_sha256,
            "effect_refs": effects, "fact_refs": dict(sorted(facts.items())),
            "finalization_sha256": canonical_sha256(asdict(final)),
            "completion_sha256": None if decision is None else decision.decision_sha256,
            "verification_readiness_sha256": None if readiness is None else readiness.readiness_sha256,
            "completed_at_ms": final.completed_at_ms, "blockers": sorted(blockers),
            "may_authorize": False, "may_execute": False, "may_write_memory": False,
            "context_section": "DATA"}
        payload["evidence_sha256"] = canonical_sha256(payload)
        return payload


class LearningOutputProductionBinding:
    """One composition hook on the existing Gateway instance, not a service.

    Only internal ID selectors are accepted. The production root installs this
    hook; standalone Life without it preserves its existing Knowledge behavior.
    """

    def __init__(self, runtime: Any):
        if (type(runtime.life_service) is not EmbeddedLifeRuntime
                or not isinstance(runtime.store, GatewayStateStore)):
            raise TypeError("learning_output.existing_life_gateway_required")
        self._runtime = runtime

    def prepare_from_life(self, life_id: str, learning_id: str) -> dict[str, Any]:
        life = self._runtime.life_service
        with life._lock:
            record, identity = life.verified_learning_output_input(life_id, learning_id)
            workspace = "workspace-" + canonical_sha256(str(self._runtime.config.workspace_root))
            if identity.get("workspace_id") != workspace:
                raise LearningOutputBindingError("learning_output.configured_workspace_mismatch")
            basis = LearningOutputBasisV1(life_id=life_id, learning_id=learning_id,
                learning_scope_sha256=record["scope_sha256"],
                learning_record_sha256=learning_record_sha256(record),
                principal_ref=life_id, principal_scope_hash=identity["principal_scope_hash"],
                privacy_scope="private", privacy_scope_hash=canonical_sha256("private"))
            if not legacy_publication_blocked(record):
                return prepare_knowledge_learning_output(record, basis=basis).payload()
            # Source selectors remain data. They cannot select a trust key or
            # fabricate a current WorldState; admission below rereads authorities.
            artifact = record.get("draft_artifact")
            selector = artifact.get("source_evolution") if isinstance(artifact, Mapping) else None
            if not isinstance(selector, Mapping):
                return _waiting("learning_output.source_selector_required")
            return self._source(record, basis, dict(selector), workspace)

    def _source(self, record, basis, selector, workspace):
        bound = self._runtime.store._method_source_resolver
        if type(bound) is not MethodRunSourceResolver or bound.gateway is not self._runtime.store:
            return _waiting("learning_output.world_source_operator_required")
        world = bound.world
        operator = world._method_revision_resolver
        if type(operator) is not MethodPublicationResolver:
            return _waiting("learning_output.world_source_operator_required")
        kind = selector.get("kind")
        fields = ({"kind", "world_state_id", "candidate_commit", "requested_action_ids"}
                  if kind == "TOOL_SOURCE" else {"kind", "world_state_id", "archive_sha256"})
        if (kind not in {"TOOL_SOURCE", "METHOD_SOURCE"} or set(selector) != fields
                or type(selector["world_state_id"]) is not str
                or re.fullmatch(r"wst_[0-9a-f]{64}", selector["world_state_id"]) is None):
            raise LearningOutputBindingError("learning_output.source_selector_invalid")
        # Use the existing Store lock, never acquire the World Runtime lock
        # under Life: its post-commit observer can itself call Life. A cache
        # that has not caught up with its durable head defers rather than waits.
        with world.store.retention_transaction():
            state = world.store.get(selector["world_state_id"])
            if state is None:
                raise LearningOutputBindingError("learning_output.current_world_required")
            scope = state.state.scope
            active = world.store.current(life_id=scope.life_id, world_scope_hash=scope.world_scope_hash,
                principal_scope_hash=scope.principal_scope_hash, frame_id=state.frame_id)
            if (active is None or active.state_ref != state.state_ref or scope.life_id != basis.life_id
                    or scope.principal_scope_hash != basis.principal_scope_hash
                    or dict((b.key,b.value) for b in scope.scope_bindings).get("workspace_id") != workspace):
                raise LearningOutputBindingError("learning_output.current_world_scope_mismatch")
            live = world._streams.get(state.frame_id)
            if live is None or live.frame.frame_revision_hash != state.state.frame_ref.sha256:
                return _waiting("learning_output.repository_frame_refresh_required")
            frame = live.frame
            if frame.repository != operator.repository_id or frame.worktree != operator.worktree_id:
                raise LearningOutputBindingError("learning_output.repository_operator_mismatch")
            if kind == "TOOL_SOURCE":
                actions = selector["requested_action_ids"]
                if (type(actions) is not list or not actions or any(type(x) is not str for x in actions)
                        or actions != sorted(set(actions))):
                    raise LearningOutputBindingError("learning_output.action_scope_invalid")
                return prepare_tool_source_learning_output(record, basis=basis,
                    repository=operator.source_repository, base_commit=frame.commit,
                    candidate_commit=selector["candidate_commit"], requested_action_ids=tuple(actions)).payload()
            body, archive_frame, expected, inputs, _revision = operator._verified_archive(
                selector["archive_sha256"], at_ms=operator.clock_ms())
            envelope = method_publication_envelope(archive_sha256=selector["archive_sha256"],
                frame=archive_frame, at_ms=body["publication_at_ms"])
            # Calls the existing independent Git/current-head verifier, NOT
            # facade.accept: validation cannot move the selected World head.
            operator(envelope, state)
            return prepare_method_source_learning_output(record, basis=basis,
                base_snapshot=inputs["base_snapshot"],
                expected_base_snapshot_sha256=inputs["expected_base_snapshot_sha256"],
                corpus=inputs["corpus"], candidates=inputs["candidates"],
                source_documents=inputs["source_documents"], simulation_evidence=inputs["simulation_evidence"],
                trusted_observer_public_key=operator.trusted_observer_public_key,
                now_ms=operator.clock_ms()).payload()

    def commit_execution(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Audit only AFTER the original Life terminal commit succeeds.

        Audit failure must not erase the already-durable execution. A duplicate
        terminal callback can retry collection; it cannot fabricate a Memory
        success or re-execute a tool. No independent recovery worker is spawned.
        """
        result = self._runtime.life_service.commit_execution(payload)
        execution = result.get("execution") if isinstance(result, Mapping) else None
        if result.get("ok") is not True or not isinstance(execution, Mapping):
            return result
        request_id, run_id, generation = (execution[k] for k in ("request_id", "run_id", "generation"))
        try:
            registered = self._runtime.store.get_executable_composition_plan_for_request(
                request_id, run_id=run_id, generation=generation)
            if registered is None:
                return result
            entry = self._runtime.store.get_request_entry(request_id)
            plan = registered.executable_plan
            workspace = "workspace-" + canonical_sha256(str(self._runtime.config.workspace_root))
            if (entry is None or entry.session_scope_hash != execution["session_scope_hash"]
                    or plan.workspace.workspace_id != workspace):
                raise LearningOutputBindingError("learning_output.life_gateway_scope_mismatch")
            coordinator = getattr(self._runtime.orchestration, "_composition_steps", None)
            evidence = collect_composition_learning_evidence(self._runtime.store, coordinator,
                request_id=request_id, run_id=run_id, generation=generation)
            if (not set(execution["fact_ids"]).issubset(evidence["fact_refs"])
                    or execution["completed_at_ms"] < evidence["completed_at_ms"]):
                raise LearningOutputBindingError("learning_output.life_machine_fact_mismatch")
            event = self._runtime.life_service.record_learning_execution_evidence(execution["life_id"], evidence)
            audit = {"status": "MACHINE_EVIDENCE_AUDITED", "event_id": event["event_id"],
                "evidence_sha256": evidence["evidence_sha256"], "may_write_memory": False,
                "blockers": evidence["blockers"]}
        except Exception as exc:
            # Report the stable exception class, not arbitrary model/host text.
            audit = {"status": "LEARNING_EVIDENCE_DEFERRED", "reason_code": (str(exc) if isinstance(exc, LearningOutputBindingError)
                    else "learning_output.machine_collection_unavailable"),
                "error_type": type(exc).__name__, "may_write_memory": False}
        return {**result, "learning_evidence": audit}
