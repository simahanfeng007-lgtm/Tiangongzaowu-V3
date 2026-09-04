"""P7D.2 restart integration at the first-child durable DAG frontier."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from contracts import new_state_snapshot
from tests import test_composition_grant_authority_p7c1 as p7c1
from tests import test_composition_grant_authority_p7d2 as p7d2
from tests import test_composition_step_execution_p7d1 as p7d1
from total_gateway.completion_gate import CompletionGate, CompletionRequirements
from total_gateway.composition_step_execution import (
    CompositionStepExecutionCoordinator,
    CompositionStepExecutionError,
)
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore


class _CurrentStepBackendProbe:
    """Execute only the current durable step and expose its pre-call state."""

    def __init__(self, store, facts, plan, step_id: str) -> None:
        self._store = store
        self._facts = facts
        self._plan = plan
        self._step_id = step_id
        self.calls = 0
        self.action_ids: list[str] = []
        self.states_at_call: list[tuple[str, bool]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        timeout_seconds: float,
        backend_started: bool = False,
        before_request=None,
    ) -> tuple[int, dict[str, Any], str]:
        del method, path, payload, timeout_seconds, backend_started, before_request
        record = self._store.get_current_composition_step_authorization(
            self._plan.executable_plan_id,
            self._step_id,
        )
        assert record is not None
        effect = self._store.get_effect(record.request.prebound_effect_id)
        assert effect is not None
        batch = self._facts.get_batch_for_effect(record.request.prebound_effect_id)
        self.calls += 1
        self.action_ids.append(record.request.action_id)
        self.states_at_call.append((effect.state, batch is not None))
        value = p7d1._successful_omni_value(
            record.request.action_id,
            target=record.request.target,
        )
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return 200, value, hashlib.sha256(raw).hexdigest()


def _coordinator(
    harness,
    manifest,
    backend,
    *,
    continuation_authorizer=None,
    gateway_epoch: int = 1,
    trust=None,
) -> CompositionStepExecutionCoordinator:
    generation = harness.store.get_generation(harness.plan.request_id)
    assert generation is not None and generation.owner_instance_id is not None
    return CompositionStepExecutionCoordinator(
        store=harness.store,
        objects=harness.objects,
        facts=harness.facts,
        registry=harness.loaded.registry,
        schema_catalog=harness.loaded.schema_catalog,
        capability_manifest=manifest,
        trust_bundle_provider=lambda _now_ms: (
            harness.trust if trust is None else trust
        ),
        backend_compat_client=backend,
        workspace_root=harness.root.resolve(strict=True),
        gateway_epoch=gateway_epoch,
        gateway_instance_id=generation.owner_instance_id,
        append_effect_event=lambda _store, **_kwargs: True,
        continuation_authorizer=continuation_authorizer,
    )


def _prepare_single_step_continuation(harness):
    """Seal V1 attempt 1, then leave only durable parent authorities live."""

    manifest = p7d2._execution_manifest(harness)
    harness.authority.composition_capability_manifest_hash = manifest.sha256
    delegation_id = p7d2._seal(harness)
    p7c1._authorize(harness, now_ms=1_701)
    first = harness.store.get_current_composition_step_authorization(
        harness.plan.executable_plan_id,
        "step.01",
    )
    assert first is not None
    assert first.request.attempt == 1
    p7d2._finish_parent_with_fact(harness)
    harness.authority.unregister(harness.outer.payload.ticket_id)
    return manifest, delegation_id, first


def _reopen_durable_authorities(harness, *, now_ms: int) -> None:
    """Discard all live SQLite/Object handles, then reopen from disk."""

    harness.facts.close()
    harness.objects.close()
    harness.store.close()
    harness.store = GatewayStateStore.open(harness.database_path, now_ms=now_ms)
    harness.objects = ContentAddressedObjectStore.open(
        harness.root / "objects",
        now_ms=now_ms,
    )
    harness.facts = FactLedger.open(
        harness.root / "facts.sqlite3",
        harness.objects,
        now_ms=now_ms,
    )


def test_restart_resumes_after_first_child_without_replaying_it(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "first-child-frontier",
        multi_step=True,
        complete_parent_effect=False,
    ) as harness:
        manifest = p7d2._execution_manifest(harness)
        harness.authority.composition_capability_manifest_hash = manifest.sha256
        delegation_id = p7d2._seal(harness)
        p7d2._finish_parent_with_fact(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)

        harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=1_703,
        )
        first_record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_703,
        )
        assert first_record is not None
        first_backend = p7d1._BackendProbe(
            harness.store,
            harness.facts,
            first_record.prebound_effect_id,
            action_id=first_record.request.action_id,
            target=first_record.request.target,
        )
        first = _coordinator(harness, manifest, first_backend).dispatch_record(
            first_record,
            now_ms=1_704,
        )
        assert first.status == "SUCCEEDED"
        assert first_backend.calls == 1
        assert harness.facts.get_batch_for_effect(first.effect_id) is not None

        # This is the process boundary under test: none of the coordinator,
        # authority, Store, ObjectStore, or FactLedger objects survive it.
        _reopen_durable_authorities(harness, now_ms=1_800)
        restarted_authority = p7d2._fresh_authority_without_parent_registration(
            harness
        )
        restarted_authority.composition_capability_manifest_hash = manifest.sha256
        assert restarted_authority._active == {}  # noqa: SLF001 - restart proof

        second_backend = _CurrentStepBackendProbe(
            harness.store,
            harness.facts,
            harness.plan,
            "step.02",
        )
        restarted = _coordinator(
            harness,
            manifest,
            second_backend,
            continuation_authorizer=(
                restarted_authority.issue_composition_continuation_step
            ),
        )

        frontier = restarted.project_plan(harness.plan)
        assert frontier.by_step_id()["step.01"].state == "SUCCEEDED"
        assert frontier.next_step_id == "step.02"

        second = restarted.dispatch_next(
            now_ms=1_801,
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            generation=harness.plan.generation,
        )
        assert second is not None
        assert second.step_id == "step.02"
        assert second.status == "SUCCEEDED"
        assert first_backend.calls == 1
        assert second_backend.calls == 1
        assert second_backend.action_ids == ["skill.get"]
        assert second_backend.states_at_call == [("SIDE_EFFECT_STARTED", False)]
        assert restarted.dispatch_next(
            now_ms=1_802,
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            generation=harness.plan.generation,
        ) is None
        assert second_backend.calls == 1
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 1

        second_record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.02",
        )
        assert second_record is not None
        assert second_record.request.materialized_arguments == {
            "skill_id": "a" * 64
        }
        assert second_record.request.dependency_evidence[0]["effect_id"] == (
            first.effect_id
        )

        finalization = restarted.finalize_plan(harness.plan)
        assert finalization.parent_effect_id == harness.parent_claim.effect_id
        assert finalization.leaf_effect_ids == (second.effect_id,)
        assert finalization.lineage_effect_ids == (
            first.effect_id,
            second.effect_id,
        )
        assert finalization.fact_ids == (*first.fact_ids, *second.fact_ids)
        assert finalization.completed_at_ms == max(
            harness.facts.get_batch_for_effect(
                first.effect_id,
                verify_payload=True,
            ).observed_at_ms,
            harness.facts.get_batch_for_effect(
                second.effect_id,
                verify_payload=True,
            ).observed_at_ms,
        )
        assert finalization.final_output_aliases == {
            harness.plan.final_output_aliases[0].alias: "# P7D.1 test"
        }

        completion_lineage = tuple(
            sorted(
                (
                    finalization.parent_effect_id,
                    *finalization.lineage_effect_ids,
                )
            )
        )
        required_effects = tuple(
            sorted(
                (
                    finalization.parent_effect_id,
                    *finalization.leaf_effect_ids,
                )
            )
        )
        decision = CompletionGate(
            harness.objects,
            harness.facts,
            head_state_reader=harness.store.get_effect_head_state,
        ).evaluate(
            CompletionRequirements(
                request_id=harness.plan.request_id,
                run_id=harness.plan.run_id,
                generation=harness.plan.generation,
                required_execution_effect_ids=required_effects,
                execution_lineage_effect_ids=completion_lineage,
            )
        )
        assert decision.outcome == "COMPLETED"
        assert decision.execution_ready is True
        assert decision.can_transition_request_completed is True
        assert decision.supporting_fact_ids == tuple(
            sorted(("parent-fact-p7d2", *finalization.fact_ids))
        )


def test_single_step_expired_v1_dispatches_unique_attempt_two(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "single-expired-successor",
        coherent_parent_effect=True,
        complete_parent_effect=False,
    ) as harness:
        manifest, delegation_id, first = _prepare_single_step_continuation(
            harness
        )
        issuer = p7d2._fresh_authority_without_parent_registration(harness)
        issuer.composition_capability_manifest_hash = manifest.sha256
        backend = _CurrentStepBackendProbe(
            harness.store,
            harness.facts,
            harness.plan,
            "step.01",
        )
        coordinator = _coordinator(
            harness,
            manifest,
            backend,
            continuation_authorizer=issuer.issue_composition_continuation_step,
        )

        outcome = coordinator.dispatch_next(
            now_ms=first.request.expires_at_ms,
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            generation=harness.plan.generation,
        )

        assert outcome is not None
        outcome_head = harness.store.get_effect(outcome.effect_id)
        assert outcome.status == "SUCCEEDED", (
            None if outcome_head is None or outcome_head.result is None
            else outcome_head.result.error_code
        )
        chain = harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert tuple(item.request.attempt for item in chain) == (1, 2)
        second = chain[-1]
        assert second.request.continuation_delegation_id == delegation_id
        assert outcome.authorization_id == second.authorization_id
        assert outcome.effect_id == second.prebound_effect_id
        assert outcome.effect_id != first.prebound_effect_id
        predecessor = harness.store.get_effect(first.prebound_effect_id)
        assert predecessor is not None
        assert predecessor.state == "FAILED_FINAL"
        assert predecessor.result is not None
        assert predecessor.result.error_code == (
            "composition.authorization.prestart_superseded"
        )
        assert backend.calls == 1
        assert coordinator.dispatch_next(
            now_ms=first.request.expires_at_ms + 1,
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            generation=harness.plan.generation,
        ) is None
        assert backend.calls == 1


def test_single_step_existing_attempt_two_is_current_and_attempt_three_is_forbidden(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "single-attempt-ceiling",
        coherent_parent_effect=True,
        complete_parent_effect=False,
    ) as harness:
        manifest, delegation_id, first = _prepare_single_step_continuation(
            harness
        )
        issuer = p7d2._fresh_authority_without_parent_registration(harness)
        issuer.composition_capability_manifest_hash = manifest.sha256
        issuer.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=first.request.expires_at_ms,
        )
        chain = harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert tuple(item.request.attempt for item in chain) == (1, 2)
        second = chain[-1]
        backend = _CurrentStepBackendProbe(
            harness.store,
            harness.facts,
            harness.plan,
            "step.01",
        )
        coordinator = _coordinator(
            harness,
            manifest,
            backend,
            continuation_authorizer=issuer.issue_composition_continuation_step,
        )

        with pytest.raises(CompositionStepExecutionError) as caught:
            coordinator.dispatch_next(
                now_ms=second.request.expires_at_ms,
                request_id=harness.plan.request_id,
                run_id=harness.plan.run_id,
                generation=harness.plan.generation,
            )

        assert caught.value.code == "composition.authorization.attempts_exhausted"
        assert tuple(
            item.request.attempt
            for item in harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == (1, 2)
        assert harness.store.get_effect(first.prebound_effect_id).state == (
            "FAILED_FINAL"
        )
        assert harness.store.get_effect(second.prebound_effect_id) is None
        assert backend.calls == 0


def test_single_step_old_epoch_v1_dispatches_current_epoch_attempt_two(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "single-old-epoch-successor",
        coherent_parent_effect=True,
        complete_parent_effect=False,
        outer_expires_at_ms=61_000,
        authority_expires_at_ms=61_000,
        plan_expires_at_ms=61_500,
    ) as harness:
        manifest, delegation_id, first = _prepare_single_step_continuation(
            harness
        )
        harness.store.initialize_snapshot(
            new_state_snapshot(
                "request",
                entity_id=harness.plan.request_id,
                request_id=harness.plan.request_id,
                run_id=harness.plan.run_id,
                generation=harness.plan.generation,
                created_at_ms=1_200,
            )
        )
        recovered = harness.store.recover_expired_active_request(
            gateway_epoch=2,
            owner_instance_id="gateway_p7d2_coordinator_epoch2",
            recovered_at_ms=11_200,
            lease_duration_ms=30_000,
            request_id=harness.plan.request_id,
        )
        assert recovered is not None
        epoch_two_trust = harness.trust.model_copy(
            update={"gateway_epoch": 2, "bundle_sha256": p7c1.ZERO}
        ).with_computed_sha256()
        issuer = p7d2._fresh_authority_without_parent_registration(
            harness,
            gateway_epoch=2,
            trust=epoch_two_trust,
        )
        issuer.composition_capability_manifest_hash = manifest.sha256
        backend = _CurrentStepBackendProbe(
            harness.store,
            harness.facts,
            harness.plan,
            "step.01",
        )
        coordinator = _coordinator(
            harness,
            manifest,
            backend,
            continuation_authorizer=issuer.issue_composition_continuation_step,
            gateway_epoch=2,
            trust=epoch_two_trust,
        )

        outcome = coordinator.dispatch_next(
            now_ms=11_201,
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            generation=harness.plan.generation,
        )

        assert outcome is not None
        assert outcome.status == "SUCCEEDED"
        chain = harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert tuple(item.request.attempt for item in chain) == (1, 2)
        second = chain[-1]
        assert second.request.continuation_delegation_id == delegation_id
        assert second.prebound_effect_id == outcome.effect_id
        assert second.prebound_effect_id != first.prebound_effect_id
        _, _, _, second_ticket, second_grant = second.artifacts.restore_contracts()
        assert second_ticket.payload.gateway_epoch == 2
        assert second_grant.payload.gateway_epoch == 2
        assert backend.calls == 1
