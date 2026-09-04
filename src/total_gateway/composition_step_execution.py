"""Durable P7D.1 execution seam for one authorized composition root step.

This module is deliberately not a scheduler or Runtime.  The existing
``GatewayOrchestrationWorker`` calls it, the existing Store owns Effect state,
``FactLedger`` owns machine results, ``BackendClient`` owns the ticket gate,
and Omni ``BodyRuntime`` remains the only action runtime.
"""

from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from contracts import (
    ActionRegistrySnapshot,
    CapabilityManifest,
    TrustBundle,
    canonical_sha256,
)
from runtime_security import verify_omni_capability_grant

from .action_registry import ActionRegistryError, ActionSchemaCatalog
from .backend_client import BackendClient, BackendClientError
from .composition_activation_adapter import (
    CompositionActivationAdapterError,
    materialize_static_root_step,
)
from .composition_backend_transport import CompositionBackendExecutionTransport
from .composition_execution_binding import (
    COMPOSITION_STEP_PIPELINE_VERSION,
    CompositionExecutionBindingError,
    derive_composition_execution_binding,
    rebuild_composition_effect_claim,
)
from .effects import EffectClaim, EffectResult
from .fact_ledger import FactBatchRecord, FactLedger
from .impact_evaluator import probe_target_state


_SAFE_SIDE_EFFECTS = frozenset({"none", "read"})


class CompositionStepExecutionError(RuntimeError):
    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


@dataclass(frozen=True, slots=True)
class CompositionStepExecutionOutcome:
    authorization_id: str
    executable_plan_id: str
    step_id: str
    effect_id: str
    status: str
    fact_ids: tuple[str, ...]
    recovered: bool = False


class CompositionStepExecutionCoordinator:
    """Project persisted P7C authority through the one existing runtime chain."""

    def __init__(
        self,
        *,
        store: Any,
        objects: Any,
        facts: FactLedger,
        registry: ActionRegistrySnapshot,
        schema_catalog: ActionSchemaCatalog,
        capability_manifest: CapabilityManifest,
        trust_bundle_provider: Callable[[int], TrustBundle],
        backend_compat_client: Any,
        workspace_root: Path,
        gateway_epoch: int,
        gateway_instance_id: str,
        append_effect_event: Callable[..., bool] | None = None,
        transport_runner: Callable[
            [Callable[[], dict[str, Any]], float], dict[str, Any]
        ]
        | None = None,
    ) -> None:
        if (
            not registry.has_valid_sha256()
            or not schema_catalog.has_valid_sha256()
            or schema_catalog.source_manifest_sha256
            != registry.source_manifest_sha256
            or not capability_manifest.has_valid_sha256()
        ):
            raise ValueError("composition execution authority is invalid")
        if (
            not workspace_root.is_absolute()
            or not workspace_root.is_dir()
            or workspace_root.is_symlink()
            or gateway_epoch < 1
            or not gateway_instance_id
            or not callable(trust_bundle_provider)
            or not callable(getattr(backend_compat_client, "request", None))
            or (transport_runner is not None and not callable(transport_runner))
        ):
            raise ValueError("composition execution runtime is invalid")
        required_store = (
            "recover_live_composition_step_authorizations",
            "get_executable_composition_plan_for_request",
            "get_active_executable_composition_plan",
            "get_composition_step_authorization",
            "get_composition_step_authorization_for_effect",
            "list_effects_for_pipeline",
            "get_effect",
            "claim_effect",
            "acquire_dispatch_permit",
            "release_dispatch_permit",
            "recover_terminal_dispatch_permits",
            "complete_effect",
            "action_fence_status",
        )
        if any(not callable(getattr(store, name, None)) for name in required_store):
            raise ValueError("composition execution Store is incomplete")
        if any(
            not callable(getattr(objects, name, None))
            for name in ("get_reference", "read_bytes")
        ):
            raise ValueError("composition execution object authority is invalid")
        if any(
            not callable(getattr(facts, name, None))
            for name in (
                "record_execution",
                "get_batch_for_effect",
                "get_batch_for_ticket",
            )
        ):
            raise ValueError("composition execution FactLedger is incomplete")
        self._store = store
        self._objects = objects
        self._facts = facts
        self._registry = registry
        self._schemas = schema_catalog
        self._manifest = capability_manifest
        self._trust_bundle_provider = trust_bundle_provider
        self._backend = backend_compat_client
        self._workspace_root = workspace_root.resolve(strict=True)
        self._gateway_epoch = gateway_epoch
        self._instance_id = gateway_instance_id
        self._append_effect_event = append_effect_event
        self._transport_runner = transport_runner

    def _event(
        self,
        *,
        event_type: str,
        effect_id: str,
        request_id: str,
        run_id: str,
        generation: int,
        created_at_ms: int,
        payload: dict[str, object],
    ) -> None:
        if self._append_effect_event is None:
            return
        # The execution ledger is a projection when its P18 task authority is
        # present.  Its absence must not replace or invalidate the canonical
        # Effect/Fact commit.
        self._append_effect_event(
            self._store,
            event_key=f"{event_type}:{effect_id}",
            event_type=event_type,
            payload=payload,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            effect_id=effect_id,
            created_at_ms=created_at_ms,
        )

    @staticmethod
    def _permission_for_step(registry: ActionRegistrySnapshot, step: Any) -> Any:
        matches = tuple(
            permission
            for permission in registry.permissions
            if permission.action_id == step.action_id
            and permission.action_version == step.action_version
        )
        if len(matches) != 1:
            raise CompositionStepExecutionError(
                "composition.execution.permission_missing"
            )
        permission = matches[0]
        if (
            not permission.has_valid_sha256()
            or permission != step.permission
            or permission.permission_sha256 != step.permission_sha256
            or permission.registry_risk != "A0"
            or permission.effective_risk != "A0"
            or permission.effect not in {"read", "verify"}
            or not set(permission.allowed_side_effects).issubset(
                _SAFE_SIDE_EFFECTS
            )
            or permission.allow_shell
            or permission.allow_python
            or permission.requires_confirmation
        ):
            raise CompositionStepExecutionError(
                "composition.execution.a0_ceiling_exceeded"
            )
        return permission

    def _verify_objects(self, request: Any, ticket: Any) -> None:
        expected = [item.model_dump(mode="json") for item in ticket.payload.input_objects]
        if request.object_grants != expected:
            raise CompositionStepExecutionError(
                "composition.execution.object_grants_mismatch"
            )
        for grant in ticket.payload.input_objects:
            try:
                reference = self._objects.get_reference(grant.object_id)
                body = self._objects.read_bytes(grant.object_id)
            except Exception as exc:
                raise CompositionStepExecutionError(
                    "composition.execution.object_unavailable"
                ) from exc
            if (
                reference is None
                or not reference.has_valid_sha256()
                or reference.object_id != grant.object_id
                or reference.sha256 != grant.sha256
                or reference.size_bytes != grant.size_bytes
                or reference.tenant_id != grant.tenant_id
                or reference.link_account_id != grant.link_account_id
                or reference.conversation_scope_hash
                != grant.conversation_scope_hash
                or len(body) != grant.size_bytes
                or hashlib.sha256(body).hexdigest() != grant.sha256
            ):
                raise CompositionStepExecutionError(
                    "composition.execution.object_changed"
                )

    def _verify_parent_success(self, request: Any) -> str:
        """Bind child dispatch to the exact successful parent Effect and Fact."""

        batch = self._facts.get_batch_for_ticket(
            request.parent_ticket_id,
            verify_payload=True,
        )
        if (
            batch is None
            or batch.result.ticket_id != request.parent_ticket_id
            or batch.result.request_id != request.request_id
            or batch.result.run_id != request.run_id
            or batch.result.generation != request.generation
            or batch.result.status != "SUCCEEDED"
            or batch.workspace_id != request.workspace_id
            or not batch.facts
            or tuple(item.fact_id for item in batch.facts)
            != batch.result.fact_ids
        ):
            raise CompositionStepExecutionError(
                "composition.execution.parent_not_succeeded"
            )
        parent = self._store.get_effect(batch.result.effect_id)
        if (
            parent is None
            or parent.state != "SUCCEEDED"
            or parent.result is None
            or parent.result.status != "SUCCEEDED"
            or parent.claim.effect_kind != "execution"
            or parent.claim.request_id != request.request_id
            or parent.claim.run_id != request.run_id
            or parent.claim.generation != request.generation
            or parent.result.fact_id not in batch.result.fact_ids
            or parent.result.result_object_id
            != batch.result_payload_object_id
            or parent.result.result_object_sha256
            != batch.result_payload_sha256
        ):
            raise CompositionStepExecutionError(
                "composition.execution.parent_effect_mismatch"
            )
        return parent.claim.effect_id

    def _preflight(self, record: Any, *, now_ms: int) -> dict[str, Any]:
        request = record.request
        if not record.has_valid_record_sha256():
            raise CompositionStepExecutionError(
                "composition.execution.authorization_invalid"
            )
        plan_record = self._store.get_active_executable_composition_plan(
            request.registration_id, now_ms=now_ms
        )
        if plan_record is None:
            raise CompositionStepExecutionError(
                "composition.execution.plan_inactive"
            )
        plan = plan_record.executable_plan
        try:
            plan_workspace = Path(plan.workspace.workspace_root).resolve(
                strict=True
            )
        except (OSError, ValueError) as exc:
            raise CompositionStepExecutionError(
                "composition.execution.plan_workspace_invalid"
            ) from exc
        if (
            plan.executable_plan_id != request.executable_plan_id
            or plan.executable_plan_sha256 != request.executable_plan_sha256
            or plan.registration_id != request.registration_id
            or plan.request_id != request.request_id
            or plan.run_id != request.run_id
            or plan.generation != request.generation
            or plan.principal_scope_hash != request.principal_scope_hash
            or plan.action_registry_sha256 != self._registry.registry_sha256
            or plan.capability_manifest_sha256
            != self._registry.source_manifest_sha256
            or plan_workspace != self._workspace_root
            or plan.workspace.workspace_id != request.workspace_id
            or plan.workspace.workspace_scope_sha256
            != request.workspace_scope_sha256
            or len(plan.step_bindings) != 1
        ):
            raise CompositionStepExecutionError(
                "composition.execution.plan_mismatch"
            )
        try:
            materialized = materialize_static_root_step(
                plan, step_id=request.step_id
            )
        except CompositionActivationAdapterError as exc:
            raise CompositionStepExecutionError(exc.code) from exc
        # A non-empty target is not necessarily a host path (for example
        # ``skill.get(target="word_delivery")``).  P7C's current
        # object-grant-only path policy has already rejected raw host paths;
        # P7D rechecks that immutable permission and the live target snapshot
        # below instead of weakening the signed target to an empty-string
        # special case.  STEP_OUTPUT materialization remains unavailable in
        # this first slice because ``materialize_static_root_step`` rejects it.
        permission = self._permission_for_step(self._registry, materialized.step)
        try:
            schema = self._schemas.resolve(
                permission.action_id,
                permission.action_version,
                expected_sha256=materialized.step.argument_schema_sha256,
                require_explicit=True,
            )
            validated = schema.validate_exact(
                permission.action_id,
                materialized.target,
                materialized.arguments,
                workspace=self._workspace_root,
                available_actions=(
                    item.action_id for item in self._registry.permissions
                ),
                user_roots=(),
            )
        except ActionRegistryError as exc:
            raise CompositionStepExecutionError(
                "composition.execution.schema_rejected"
            ) from exc
        if (
            validated.get("action") != permission.action_id
            or validated.get("target") != materialized.target
            or validated.get("args") != materialized.arguments
        ):
            raise CompositionStepExecutionError(
                "composition.execution.arguments_normalized"
            )

        target_state = probe_target_state(
            materialized.target, self._workspace_root
        )
        target_snapshot_sha256 = (
            None if target_state is None else canonical_sha256(target_state)
        )
        if (
            request.target_snapshot != target_state
            or request.target_snapshot_sha256 != target_snapshot_sha256
        ):
            raise CompositionStepExecutionError(
                "composition.execution.target_changed"
            )
        try:
            derived = derive_composition_execution_binding(
                plan,
                materialized,
                parent_ticket_id=request.parent_ticket_id,
                workspace_id=request.workspace_id,
                workspace_scope_hash=request.workspace_scope_sha256,
                target_snapshot_sha256=target_snapshot_sha256,
            )
            claim = rebuild_composition_effect_claim(
                request,
                run_sequence=derived.run_sequence,
                ordinal=derived.ordinal,
                lease_epoch=self._gateway_epoch,
            )
        except CompositionExecutionBindingError as exc:
            raise CompositionStepExecutionError(exc.code) from exc
        if (
            request.prebound_effect_id != derived.effect_id
            or request.prebound_effect_intent_sha256
            != derived.effect_intent_sha256
            or request.composition_binding_sha256
            != derived.binding.binding_sha256
            or request.materialized_arguments != materialized.arguments
            or request.arguments_sha256
            != canonical_sha256(
                {
                    "action": permission.action_id,
                    "target": materialized.target,
                    "args": materialized.arguments,
                }
            )
        ):
            raise CompositionStepExecutionError(
                "composition.execution.binding_mismatch"
            )

        try:
            intent, impact, decision, ticket, grant = (
                record.artifacts.restore_contracts()
            )
            record.artifacts.validate_for_request(request)
        except Exception as exc:
            raise CompositionStepExecutionError(
                "composition.execution.signed_chain_invalid"
            ) from exc
        if (
            ticket.payload.capability_manifest_hash != self._manifest.sha256
            or ticket.payload.component_manifest_hash
            != self._manifest.component_manifest_hash
            or ticket.payload.claim_sha256 != claim.claim_sha256
            or ticket.payload.claim_revision != claim.claim_revision
            or ticket.payload.claim_lease_epoch != claim.lease_epoch
            or ticket.payload.composition_execution_binding != derived.binding
            or grant.payload.capability_manifest_hash != self._manifest.sha256
            or grant.payload.action_registry_sha256
            != self._registry.registry_sha256
            or request.result_schema_sha256
            != next(
                (
                    action.result_schema_sha256
                    for action in self._manifest.actions
                    if action.action_id == request.action_id
                    and action.version == request.action_version
                ),
                None,
            )
        ):
            raise CompositionStepExecutionError(
                "composition.execution.current_authority_mismatch"
            )
        current_fence = self._store.action_fence_status()
        if (
            int(current_fence.get("action_fence_epoch", -1))
            != request.action_fence_epoch
            or bool(current_fence.get("fenced"))
            or bool(current_fence.get("draining"))
        ):
            raise CompositionStepExecutionError(
                "composition.execution.fence_closed"
            )

        self._verify_objects(request, ticket)
        parent_effect_id = self._verify_parent_success(request)
        trust_bundle = self._trust_bundle_provider(now_ms)
        if (
            not isinstance(trust_bundle, TrustBundle)
            or not trust_bundle.has_valid_sha256()
            or trust_bundle.gateway_epoch != self._gateway_epoch
        ):
            raise CompositionStepExecutionError(
                "composition.execution.trust_invalid"
            )
        try:
            verify_omni_capability_grant(grant, trust_bundle, now_ms=now_ms)
        except Exception as exc:
            raise CompositionStepExecutionError(
                "composition.execution.grant_invalid"
            ) from exc

        runtime_response = record.runtime_response
        if (
            set(runtime_response) != {"status", "grant", "runtime", "decision"}
            or runtime_response.get("status") != "OK"
            or runtime_response.get("grant")
            != grant.model_dump(mode="json")
            or not isinstance(runtime_response.get("runtime"), Mapping)
        ):
            raise CompositionStepExecutionError(
                "composition.execution.runtime_receipt_invalid"
            )
        runtime = deepcopy(dict(runtime_response["runtime"]))
        runtime["trust_bundle"] = trust_bundle.model_dump(mode="json")
        runtime["trust_bundle_sha256"] = trust_bundle.bundle_sha256
        runtime["gateway_epoch"] = self._gateway_epoch
        # Gateway FactLedger is the sole machine-fact writer.  The immutable
        # authorization receipt remains untouched; only this detached runtime
        # execution copy disables the BodyRuntime fact kernel.
        runtime["fact_kernel_enabled"] = False
        invocation = {
            "action": permission.action_id,
            "target": materialized.target,
            "args": materialized.arguments,
        }
        return {
            "record": record,
            "plan": plan,
            "permission": permission,
            "binding": derived.binding,
            "claim": claim,
            "intent": intent,
            "impact": impact,
            "decision": decision,
            "ticket": ticket,
            "grant": grant,
            "trust_bundle": trust_bundle,
            "runtime": runtime,
            "invocation": invocation,
            "target_snapshot_sha256": target_snapshot_sha256,
            "parent_effect_id": parent_effect_id,
        }

    @staticmethod
    def _effect_result_from_batch(
        batch: FactBatchRecord, *, observed_at_ms: int
    ) -> EffectResult:
        result = batch.result
        if not batch.facts or tuple(item.fact_id for item in batch.facts) != result.fact_ids:
            raise CompositionStepExecutionError(
                "composition.execution.fact_batch_invalid"
            )
        status = (
            "SUCCEEDED"
            if result.status == "SUCCEEDED"
            else "AMBIGUOUS"
            if result.status == "AMBIGUOUS"
            else "FAILED_FINAL"
        )
        return EffectResult(
            result_id="effect-result-" + result.result_id[:120],
            effect_id=result.effect_id,
            status=status,
            fact_id=result.fact_ids[0],
            result_object_id=batch.result_payload_object_id,
            result_object_sha256=batch.result_payload_sha256,
            evidence_sha256=batch.batch_sha256,
            error_code=(
                None
                if status == "SUCCEEDED"
                else result.error_code or "composition.execution.failed"
            ),
            observed_at_ms=max(observed_at_ms, batch.observed_at_ms),
            result_sha256="0" * 64,
        ).with_computed_sha256()

    def _terminal_event(self, record: Any, result: EffectResult) -> None:
        event = (
            "step.committed"
            if result.status == "SUCCEEDED"
            else "step.ambiguous"
            if result.status == "AMBIGUOUS"
            else "step.failed"
        )
        self._event(
            event_type=event,
            effect_id=result.effect_id,
            request_id=record.request.request_id,
            run_id=record.request.run_id,
            generation=record.request.generation,
            created_at_ms=result.observed_at_ms,
            payload={
                "authorization_id": record.authorization_id,
                "effect_state": result.status,
                "source": "composition_step_execution",
            },
        )

    def _complete_without_backend_fact(
        self,
        record: Any,
        *,
        status: str,
        code: str,
        observed_at_ms: int,
        expected_state: str | None = None,
        release_dispatch_permit: bool = True,
    ) -> EffectResult:
        effect_id = record.request.prebound_effect_id
        result = EffectResult(
            result_id="effect-result-" + effect_id[4:20],
            effect_id=effect_id,
            status=status,
            fact_id="fact-effect-" + effect_id[4:20],
            evidence_sha256=canonical_sha256(
                {
                    "authorization_id": record.authorization_id,
                    "code": code,
                    "status": status,
                }
            ),
            error_code=code,
            observed_at_ms=observed_at_ms,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        self._store.complete_effect(
            result,
            expected_state=expected_state,
            release_dispatch_permit=release_dispatch_permit,
        )
        self._terminal_event(record, result)
        return result

    def dispatch_record(
        self, record: Any, *, now_ms: int
    ) -> CompositionStepExecutionOutcome:
        try:
            prepared = self._preflight(record, now_ms=now_ms)
        except Exception as exc:
            current = self._store.get_effect(
                record.request.prebound_effect_id
            )
            if current is None or current.state != "CLAIMED":
                raise
            code = str(
                getattr(
                    exc,
                    "code",
                    "composition.execution.preflight_failed",
                )
            )[:160]
            completed = self._complete_without_backend_fact(
                record,
                status="FAILED_FINAL",
                code=code,
                observed_at_ms=max(
                    now_ms,
                    current.claim.claimed_at_ms,
                ),
                expected_state="CLAIMED",
            )
            return CompositionStepExecutionOutcome(
                record.authorization_id,
                record.request.executable_plan_id,
                record.request.step_id,
                record.request.prebound_effect_id,
                completed.status,
                (completed.fact_id,),
            )
        claim: EffectClaim = prepared["claim"]
        current = self._store.get_effect(claim.effect_id)
        if current is not None and current.result is not None:
            return self._existing_outcome(record, current)
        if current is not None and current.state == "SIDE_EFFECT_STARTED":
            raise CompositionStepExecutionError(
                "composition.execution.already_started", ambiguous=True
            )

        permit_crossed = False

        def before_dispatch(crossed_at_ms: int) -> None:
            nonlocal permit_crossed
            effect, _created = self._store.claim_effect(claim)
            if effect.state != "CLAIMED":
                raise BackendClientError(
                    "backend.composition.effect_not_claimable",
                    ambiguous=effect.state == "SIDE_EFFECT_STARTED",
                )
            self._event(
                event_type="step.prepared",
                effect_id=claim.effect_id,
                request_id=claim.request_id,
                run_id=claim.run_id,
                generation=claim.generation,
                created_at_ms=crossed_at_ms,
                payload={
                    "authorization_id": record.authorization_id,
                    "effect_state": "CLAIMED",
                    "source": "composition_step_execution",
                },
            )
            ticket = prepared["ticket"]
            grant = prepared["grant"]
            try:
                self._store.acquire_dispatch_permit(
                    effect_id=claim.effect_id,
                    attempt=claim.attempt,
                    expected_fence_epoch=record.request.action_fence_epoch,
                    nonce_sha256=canonical_sha256(
                        {
                            "execution_ticket_nonce": ticket.payload.nonce,
                            "omni_grant_nonce": grant.payload.nonce,
                        }
                    ),
                    ticket_id=ticket.payload.ticket_id,
                    ticket_sha256=canonical_sha256(
                        ticket.model_dump(mode="json")
                    ),
                    grant_sha256=canonical_sha256(
                        grant.model_dump(mode="json")
                    ),
                    expected_request_id=claim.request_id,
                    expected_run_id=claim.run_id,
                    expected_generation=claim.generation,
                    expected_gateway_epoch=self._gateway_epoch,
                    expected_owner_instance_id=self._instance_id,
                    required_parent_effect_id=prepared[
                        "parent_effect_id"
                    ],
                    now_ms=crossed_at_ms,
                )
                permit_crossed = True
            except Exception as exc:
                raise BackendClientError(
                    "backend.composition.dispatch_permit_failed",
                    ambiguous=permit_crossed,
                ) from exc
            self._event(
                event_type="step.dispatched",
                effect_id=claim.effect_id,
                request_id=claim.request_id,
                run_id=claim.run_id,
                generation=claim.generation,
                created_at_ms=crossed_at_ms,
                payload={
                    "authorization_id": record.authorization_id,
                    "effect_state": "SIDE_EFFECT_STARTED",
                    "source": "composition_step_execution",
                },
            )

        transport = CompositionBackendExecutionTransport(
            self._backend,
            signed_grant=prepared["grant"].model_dump(mode="json"),
            runtime_meta=prepared["runtime"],
        )
        client = BackendClient(
            transport,
            self._store,
            ticket_consumer_instance_id=(
                "composition-inprocess-" + self._instance_id
            ),
        )
        try:
            response = client.execute(
                prepared["ticket"],
                prepared["invocation"],
                capability_manifest=self._manifest,
                trust_bundle=prepared["trust_bundle"],
                now_ms=now_ms,
                expected_gateway_epoch=self._gateway_epoch,
                minimum_generation=record.request.generation,
                grant=prepared["grant"],
                intent=prepared["intent"],
                decision=prepared["decision"],
                impact=prepared["impact"],
                claim=claim,
                expected_fence_epoch=prepared["ticket"].payload.fence_epoch,
                active_lease_epoch=self._gateway_epoch,
                expected_target_snapshot_sha256=prepared[
                    "target_snapshot_sha256"
                ],
                expected_composition_binding=prepared["binding"],
                actual_target_snapshot_sha256=prepared[
                    "target_snapshot_sha256"
                ],
                before_dispatch=before_dispatch,
                transport_runner=self._transport_runner,
            )
        except Exception as exc:
            effect = self._store.get_effect(claim.effect_id)
            if effect is None:
                raise CompositionStepExecutionError(
                    getattr(exc, "code", "composition.execution.preflight_failed")
                ) from exc
            status = (
                "AMBIGUOUS"
                if effect.state == "SIDE_EFFECT_STARTED"
                else "FAILED_FINAL"
            )
            code = str(
                getattr(exc, "code", "composition.execution.runtime_failed")
            )[:160]
            pending_transport = getattr(
                exc,
                "pending_transport_future",
                None,
            )
            completed = self._complete_without_backend_fact(
                record,
                status=status,
                code=code,
                observed_at_ms=max(now_ms, time.time_ns() // 1_000_000),
                release_dispatch_permit=pending_transport is None,
            )
            if pending_transport is not None:
                def release_after_transport(_future: object) -> None:
                    try:
                        self._store.release_dispatch_permit(
                            effect_id=claim.effect_id,
                            attempt=claim.attempt,
                            now_ms=max(
                                completed.observed_at_ms,
                                time.time_ns() // 1_000_000,
                            ),
                        )
                    except Exception:
                        # Fail closed: a release failure leaves inflight > 0;
                        # startup recovery can reconcile the durable permit.
                        return

                pending_transport.add_done_callback(release_after_transport)
            return CompositionStepExecutionOutcome(
                record.authorization_id,
                record.request.executable_plan_id,
                record.request.step_id,
                claim.effect_id,
                completed.status,
                (completed.fact_id,),
            )

        observed_at_ms = max(
            now_ms,
            response.result.finished_at_ms,
            time.time_ns() // 1_000_000,
        )
        try:
            registration = self._facts.record_execution(
                response, observed_at_ms=observed_at_ms
            )
        except Exception as exc:
            # The handler has returned but the canonical Fact commit is not
            # proven.  Keep STARTED intact; recovery will either find the exact
            # idempotent batch or close it AMBIGUOUS without replay.
            raise CompositionStepExecutionError(
                "composition.execution.fact_commit_unknown", ambiguous=True
            ) from exc
        effect_result = self._effect_result_from_batch(
            registration.record, observed_at_ms=observed_at_ms
        )
        self._store.complete_effect(effect_result)
        self._terminal_event(record, effect_result)
        return CompositionStepExecutionOutcome(
            record.authorization_id,
            record.request.executable_plan_id,
            record.request.step_id,
            claim.effect_id,
            effect_result.status,
            tuple(item.fact_id for item in registration.record.facts),
        )

    def _existing_outcome(
        self, record: Any, effect: Any
    ) -> CompositionStepExecutionOutcome:
        """Verify and project an already-terminal exact child Effect."""

        request = record.request
        if (
            effect.claim.effect_id != request.prebound_effect_id
            or effect.claim.request_id != request.request_id
            or effect.claim.run_id != request.run_id
            or effect.claim.generation != request.generation
            or effect.claim.intent_sha256
            != request.prebound_effect_intent_sha256
            or effect.claim.pipeline_version
            != COMPOSITION_STEP_PIPELINE_VERSION
            or effect.claim.attempt != request.attempt
            or effect.claim.effect_kind != "execution"
            or effect.result is None
            or effect.result.effect_id != request.prebound_effect_id
            or effect.state != effect.result.status
        ):
            raise CompositionStepExecutionError(
                "composition.execution.existing_effect_mismatch",
                ambiguous=True,
            )
        fact_ids = (effect.result.fact_id,)
        if effect.state == "SUCCEEDED":
            batch = self._facts.get_batch_for_effect(
                effect.claim.effect_id, verify_payload=True
            )
            try:
                ticket = record.artifacts.restore_contracts()[3]
            except Exception as exc:
                raise CompositionStepExecutionError(
                    "composition.execution.existing_authority_invalid",
                    ambiguous=True,
                ) from exc
            if (
                batch is None
                or batch.result.effect_id != effect.claim.effect_id
                or batch.result.ticket_id != ticket.payload.ticket_id
                or batch.result.request_id != request.request_id
                or batch.result.run_id != request.run_id
                or batch.result.generation != request.generation
                or batch.result.action_id != request.action_id
                or batch.result.action_version != request.action_version
                or batch.result.attempt != request.attempt
                or batch.result.status != "SUCCEEDED"
                or batch.workspace_id != request.workspace_id
            ):
                raise CompositionStepExecutionError(
                    "composition.execution.existing_fact_mismatch",
                    ambiguous=True,
                )
            expected = self._effect_result_from_batch(
                batch, observed_at_ms=effect.result.observed_at_ms
            )
            if expected != effect.result:
                raise CompositionStepExecutionError(
                    "composition.execution.existing_fact_effect_mismatch",
                    ambiguous=True,
                )
            fact_ids = tuple(item.fact_id for item in batch.facts)
        return CompositionStepExecutionOutcome(
            record.authorization_id,
            request.executable_plan_id,
            request.step_id,
            request.prebound_effect_id,
            effect.state,
            fact_ids,
            recovered=True,
        )

    def dispatch_next(
        self,
        *,
        now_ms: int,
        request_id: str | None = None,
        run_id: str | None = None,
        generation: int | None = None,
    ) -> CompositionStepExecutionOutcome | None:
        scoped = (request_id, run_id, generation)
        if any(value is not None for value in scoped) and any(
            value is None for value in scoped
        ):
            raise ValueError("composition dispatch scope is incomplete")
        if all(value is not None for value in scoped):
            plan_record = self._store.get_executable_composition_plan_for_request(
                request_id,
                run_id=run_id,
                generation=generation,
            )
            if plan_record is None:
                return None
            plan = plan_record.executable_plan
            if len(plan.step_bindings) != 1:
                raise CompositionStepExecutionError(
                    "composition.execution.p7d1_step_count_unsupported"
                )
            step_id = plan.step_bindings[0].step_id
            record = self._store.get_composition_step_authorization(
                plan.executable_plan_id,
                step_id,
            )
            if record is None:
                raise CompositionStepExecutionError(
                    "composition.execution.authorization_missing"
                )
            effect = self._store.get_effect(record.request.prebound_effect_id)
            if effect is not None and effect.result is not None:
                return self._existing_outcome(record, effect)
            if effect is not None and effect.state == "SIDE_EFFECT_STARTED":
                raise CompositionStepExecutionError(
                    "composition.execution.already_started", ambiguous=True
                )
            try:
                live_record = self._store.get_composition_step_authorization(
                    plan.executable_plan_id,
                    step_id,
                    now_ms=now_ms,
                )
            except Exception as exc:
                if effect is not None and effect.state == "CLAIMED":
                    completed = self._complete_without_backend_fact(
                        record,
                        status="FAILED_FINAL",
                        code="composition.execution.authorization_not_live",
                        observed_at_ms=max(
                            now_ms,
                            effect.claim.claimed_at_ms,
                        ),
                        expected_state="CLAIMED",
                    )
                    return CompositionStepExecutionOutcome(
                        record.authorization_id,
                        record.request.executable_plan_id,
                        record.request.step_id,
                        record.request.prebound_effect_id,
                        completed.status,
                        (completed.fact_id,),
                    )
                raise CompositionStepExecutionError(
                    "composition.execution.authorization_not_live"
                ) from exc
            if live_record is None:
                raise CompositionStepExecutionError(
                    "composition.execution.authorization_missing"
                )
            return self.dispatch_record(live_record, now_ms=now_ms)

        records = self._store.recover_live_composition_step_authorizations(
            now_ms=now_ms
        )
        for record in records:
            request = record.request
            if request_id is not None and request.request_id != request_id:
                continue
            if run_id is not None and request.run_id != run_id:
                continue
            if generation is not None and request.generation != generation:
                continue
            effect = self._store.get_effect(request.prebound_effect_id)
            if effect is not None and effect.result is not None:
                continue
            if effect is not None and effect.state == "SIDE_EFFECT_STARTED":
                continue
            return self.dispatch_record(record, now_ms=now_ms)
        return None

    def recover_started(
        self, *, now_ms: int
    ) -> tuple[CompositionStepExecutionOutcome, ...]:
        # A previous process may have committed an AMBIGUOUS timeout while its
        # unkillable in-process Future still owned the dispatch permit.  No
        # such handler survives the single-instance process restart, so close
        # that durable release gap before recovering STARTED Effects.
        self._store.recover_terminal_dispatch_permits(
            pipeline_version=COMPOSITION_STEP_PIPELINE_VERSION,
            now_ms=now_ms,
        )
        outcomes: list[CompositionStepExecutionOutcome] = []
        effects = self._store.list_effects_for_pipeline(
            COMPOSITION_STEP_PIPELINE_VERSION,
            states=("SIDE_EFFECT_STARTED",),
        )
        for effect in effects:
            record = self._store.get_composition_step_authorization_for_effect(
                effect.claim.effect_id
            )
            if record is None:
                raise CompositionStepExecutionError(
                    "composition.execution.recovery_receipt_missing",
                    ambiguous=True,
                )
            batch = self._facts.get_batch_for_effect(
                effect.claim.effect_id, verify_payload=True
            )
            if batch is None:
                result = self._complete_without_backend_fact(
                    record,
                    status="AMBIGUOUS",
                    code="composition.execution.result_missing_after_restart",
                    observed_at_ms=max(
                        now_ms,
                        effect.side_effect_started_at_ms
                        or effect.claim.claimed_at_ms,
                    ),
                )
                fact_ids = (result.fact_id,)
            else:
                if (
                    batch.result.effect_id != effect.claim.effect_id
                    or batch.result.request_id != effect.claim.request_id
                    or batch.result.run_id != effect.claim.run_id
                    or batch.result.generation != effect.claim.generation
                    or batch.result.ticket_id
                    != record.artifacts.restore_contracts()[3].payload.ticket_id
                    or batch.result.action_id != record.request.action_id
                    or batch.result.action_version
                    != record.request.action_version
                    or batch.result.attempt != record.request.attempt
                    or batch.workspace_id != record.request.workspace_id
                ):
                    raise CompositionStepExecutionError(
                        "composition.execution.recovery_fact_mismatch",
                        ambiguous=True,
                    )
                result = self._effect_result_from_batch(
                    batch, observed_at_ms=now_ms
                )
                self._store.complete_effect(result)
                self._terminal_event(record, result)
                fact_ids = tuple(item.fact_id for item in batch.facts)
            outcomes.append(
                CompositionStepExecutionOutcome(
                    record.authorization_id,
                    record.request.executable_plan_id,
                    record.request.step_id,
                    effect.claim.effect_id,
                    result.status,
                    fact_ids,
                    recovered=True,
                )
            )
        return tuple(outcomes)


__all__ = [
    "CompositionStepExecutionCoordinator",
    "CompositionStepExecutionError",
    "CompositionStepExecutionOutcome",
]
