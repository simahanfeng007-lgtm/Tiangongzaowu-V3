from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import (
    InboundEnvelope,
    InboundScope,
    TaskContinuityCapsule,
    TransitionEvent,
    derive_inbound_scope_keys,
    derive_run_identity,
    new_state_snapshot,
)
from contracts.verification import (
    AcceptancePredicate,
    VerificationPlan,
    VerificationPlanEntryV2,
    VerificationRecord,
    derive_verification_record_id,
)
from total_gateway import store as store_module
from total_gateway.completion_gate import CompletionDecision
from total_gateway.continuity import (
    persist_compression_checkpoint,
    persist_terminal_completion,
    persist_working_checkpoint,
)
from total_gateway.outbox import OutboxIntent, derive_outbox_id
from total_gateway.store import (
    APPLICATION_ID,
    GatewayStateStore,
    StoreConflictError,
)
from total_gateway.verification_readiness import build_readiness
from total_gateway.verification_registry import VerifierRegistry


HASH_A = "a" * 64
HASH_B = "b" * 64


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_life_test",
        link_account_id="desktop_life_test",
        conversation_ref="conversation_life_test",
        channel_message_ref="message_life_test",
        sender_ref="sender_life_test",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_life_test",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=1_000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A,
        text="continue the durable task",
    )


def completion(request_id: str, run_id: str) -> CompletionDecision:
    return CompletionDecision(
        request_id=request_id,
        run_id=run_id,
        generation=1,
        outcome="COMPLETED",
        reason_code="completion.requirements_satisfied",
        text_ready=True,
        execution_ready=True,
        artifacts_ready=True,
        delivery_ready=True,
        can_transition_request_completed=True,
        can_claim_platform_delivered=False,
        needs_reconciliation=False,
        execution_effect_states=(),
        artifact_revision_states=(),
        delivery_parts=(),
        supporting_fact_ids=(),
        decision_sha256=HASH_A,
    ).with_computed_sha256()


def checkpoint(
    request_id: str,
    run_id: str,
    *,
    capsule_id: str,
    created_at_ms: int,
    supersedes: str | None = None,
    terminal: bool = False,
) -> TaskContinuityCapsule:
    return TaskContinuityCapsule(
        capsule_id=capsule_id,
        life_id="life_gateway_test",
        capsule_kind="TERMINAL_RESULT" if terminal else "WORKING_CHECKPOINT",
        request_id=request_id,
        run_id=run_id,
        generation=1,
        episode_id="episode_gateway_test",
        user_goal="finish and verify the durable task",
        hard_constraints=("do not lose verified state",),
        active_plan=() if terminal else ("resume from the latest safe step",),
        verified_fact_ids=(),
        causal_hypothesis_ids=(),
        workspace_manifest=(),
        artifact_refs=(),
        unresolved_questions=(),
        pending_effect_ids=(),
        latest_safe_step=None if terminal else "request binding verified",
        next_step=None if terminal else "continue the next planned operation",
        recovery_preconditions=() if terminal else ("generation fence remains current",),
        continuation_token_sha256=None if terminal else HASH_B,
        final_result="durable task completed and verified" if terminal else None,
        supersedes_capsule_id=supersedes,
        retention_class="TERMINAL_RESULT" if terminal else "CHECKPOINT",
        created_at_ms=created_at_ms,
        capsule_sha256=HASH_A,
    ).with_computed_capsule_sha256()


class GatewayLifeContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(
            inbound(), ingress_sha256=HASH_A, created_at_ms=1_100
        )
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            gateway_epoch=1,
            lease_id="lease_life_test",
            owner_instance_id="gateway_life_test",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_completion_and_capsule_chain_are_idempotent_and_terminal(self) -> None:
        decision = completion(self.request_id, self.run_id)
        first_decision = self.store.record_completion_decision(
            decision, recorded_at_ms=2_000
        )
        duplicate_decision = self.store.record_completion_decision(
            decision, recorded_at_ms=2_100
        )
        self.assertTrue(first_decision.created_by_this_call)
        self.assertTrue(duplicate_decision.duplicate)
        self.assertEqual(duplicate_decision.recorded_at_ms, 2_000)

        first = checkpoint(
            self.request_id,
            self.run_id,
            capsule_id="lcp_" + "1" * 64,
            created_at_ms=2_200,
        )
        terminal = checkpoint(
            self.request_id,
            self.run_id,
            capsule_id="lcp_" + "2" * 64,
            created_at_ms=2_300,
            supersedes=first.capsule_id,
            terminal=True,
        )
        self.store.put_request_capsule(first)
        self.store.put_request_capsule(terminal)
        history = self.store.list_request_capsules(
            self.request_id, run_id=self.run_id, generation=1
        )
        self.assertEqual(tuple(item.status for item in history), ("SUPERSEDED", "TERMINAL"))
        self.assertEqual(
            self.store.get_terminal_request_capsule(
                self.request_id, run_id=self.run_id, generation=1
            ).capsule,
            terminal,
        )
        self.assertIsNone(
            self.store.get_active_request_capsule(
                self.request_id, run_id=self.run_id, generation=1
            )
        )
        with self.assertRaises(StoreConflictError):
            self.store.put_request_capsule(
                checkpoint(
                    self.request_id,
                    self.run_id,
                    capsule_id="lcp_" + "3" * 64,
                    created_at_ms=2_400,
                )
            )
        self.assertTrue(self.store.health_check(now_ms=2_500, full=True).healthy)

    def test_fenced_completion_atomically_rejects_superseded_readiness(self) -> None:
        snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1_400)
        self.store.put_registry_snapshot(snapshot, recorded_at_ms=1_500)
        predicate = AcceptancePredicate.create(
            predicate_type="artifact.nonempty",
            subject_kind="artifact",
        )
        entry = VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.artifact_content",
            verifier_version="3",
            predicate=predicate,
            subject_identity="arv_" + "a" * 64,
            evaluation_phase="POST_EXECUTION",
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()
        plan = VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            registry_snapshot_sha256=snapshot.snapshot_sha256,
            entries=(entry,),
            plan_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.put_verification_plan(plan, recorded_at_ms=1_600)
        self.store.activate_verification_plan(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            verification_plan_id=plan.verification_plan_id,
            verification_plan_sha256=plan.plan_sha256,
            registry_snapshot_sha256=snapshot.snapshot_sha256,
            activated_at_ms=1_700,
        )

        def pass_record(evaluated_at_ms: int) -> VerificationRecord:
            partial = VerificationRecord(
                verification_record_id="vrs_" + "0" * 64,
                request_id=self.request_id,
                run_id=self.run_id,
                generation=1,
                verifier_id=entry.verifier_id,
                verifier_version=entry.verifier_version,
                registry_snapshot_sha256=snapshot.snapshot_sha256,
                predicate_id=predicate.predicate_id,
                predicate_type=predicate.predicate_type,
                subject_kind=predicate.subject_kind,
                subject_identity=entry.subject_identity,
                evaluation_phase=entry.evaluation_phase,
                status="PASS",
                enforcement="RECORD",
                reason_codes=(),
                evidence_refs=(
                    f"predicate_sha256:{predicate.predicate_sha256}",
                ),
                evidence_sha256=predicate.predicate_sha256,
                producer_component_id="tiangong-gateway",
                model_generated=False,
                evaluated_at_ms=evaluated_at_ms,
                result_sha256="0" * 64,
            ).with_computed_sha256()
            return partial.model_copy(
                update={
                    "verification_record_id": derive_verification_record_id(
                        result_sha256=partial.result_sha256
                    )
                }
            )

        self.store.put_verification_record(
            pass_record(2_000), recorded_at_ms=2_000
        )
        old = build_readiness(
            plan=plan,
            snapshot=snapshot,
            store=self.store,
            evaluated_at_ms=2_000,
        )
        self.store.put_verification_readiness(old, recorded_at_ms=2_010)
        self.store.put_verification_record(
            pass_record(2_100), recorded_at_ms=2_100
        )
        current = build_readiness(
            plan=plan,
            snapshot=snapshot,
            store=self.store,
            evaluated_at_ms=2_100,
        )
        self.store.put_verification_readiness(current, recorded_at_ms=2_110)

        stale_decision = completion(self.request_id, self.run_id).model_copy(
            update={
                "verification_mode": "PLAN_BOUND",
                "verification_plan_sha256": plan.plan_sha256,
                "verification_readiness_id": old.verification_readiness_id,
                "verification_readiness_sha256": old.readiness_sha256,
                "decision_sha256": HASH_A,
            }
        ).with_computed_sha256()
        with self.assertRaisesRegex(
            StoreConflictError,
            "completion decision readiness is no longer current",
        ):
            self.store.record_completion_decision(
                stale_decision, recorded_at_ms=2_200
            )

        current_decision = stale_decision.model_copy(
            update={
                "verification_readiness_id": current.verification_readiness_id,
                "verification_readiness_sha256": current.readiness_sha256,
                "decision_sha256": HASH_A,
            }
        ).with_computed_sha256()
        persisted = self.store.record_completion_decision(
            current_decision, recorded_at_ms=2_200
        )
        self.assertTrue(persisted.created_by_this_call)
        self.assertTrue(self.store.health_check(now_ms=2_300, full=True).healthy)

    def test_capsule_insert_fault_keeps_previous_checkpoint_active(self) -> None:
        first = checkpoint(
            self.request_id,
            self.run_id,
            capsule_id="lcp_" + "4" * 64,
            created_at_ms=2_000,
        )
        self.store.put_request_capsule(first)
        second = checkpoint(
            self.request_id,
            self.run_id,
            capsule_id="lcp_" + "5" * 64,
            created_at_ms=2_100,
            supersedes=first.capsule_id,
        )
        self.store._connection.execute(  # noqa: SLF001 - fault injection
            """
            CREATE TRIGGER abort_capsule_insert BEFORE INSERT ON request_capsules
            BEGIN SELECT RAISE(ABORT, 'fault injection'); END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.put_request_capsule(second)
        finally:
            self.store._connection.execute("DROP TRIGGER abort_capsule_insert")  # noqa: SLF001
        active = self.store.get_active_request_capsule(
            self.request_id, run_id=self.run_id, generation=1
        )
        self.assertEqual(active.capsule, first)
        self.assertEqual(len(self.store.list_request_capsules(
            self.request_id, run_id=self.run_id, generation=1
        )), 1)

    def test_missing_life_outbox_is_recoverable_once(self) -> None:
        initial = new_state_snapshot(
            "request",
            entity_id="request_state_life_test",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            created_at_ms=1_300,
        )
        self.store.initialize_snapshot(initial)
        event = TransitionEvent(
            event_id="event_life_test",
            event_type="request.planning_started",
            source_component_id="tiangong-total-gateway",
            machine="request",
            entity_id=initial.entity_id,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            expected_revision=0,
            to_state="PLANNING",
            occurred_at_ms=2_000,
            event_sha256=HASH_A,
        ).with_computed_event_sha256()
        self.store.apply_event(event, recorded_at_ms=2_050)
        self.assertEqual(self.store.list_state_events_missing_life_outbox(), (event,))
        effect_id = "eff_" + "7" * 64
        outgoing = OutboxIntent(
            outbox_id=derive_outbox_id(
                effect_id, "tiangong-life-service", HASH_B
            ),
            effect_id=effect_id,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            destination_component_id="tiangong-life-service",
            intent_kind="LIFE_EVENT",
            payload_object_id="life_ingress_object_test",
            payload_sha256=HASH_B,
            created_at_ms=2_100,
            intent_sha256=HASH_A,
        ).with_computed_sha256()
        _, created = self.store.attach_life_event_outbox(event.event_id, outgoing)
        _, duplicate_created = self.store.attach_life_event_outbox(event.event_id, outgoing)
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(self.store.list_state_events_missing_life_outbox(), ())
        replay = self.store.apply_event(event, recorded_at_ms=2_200)
        self.assertTrue(replay.duplicate)

    def test_normal_work_is_deduplicated_then_compressed_to_one_recovery_chain(self) -> None:
        working = persist_working_checkpoint(
            self.store,
            life_id="life_gateway_test",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            user_goal="finish the long mixed task",
            active_plan=("verify inputs", "continue execution"),
            pending_effect_ids=("eff_" + "6" * 64,),
            latest_safe_step="input verification completed",
            next_step="resume the durable effect",
            recovery_preconditions=("effect receipt must be reconciled",),
            created_at_ms=2_000,
        )
        duplicate = persist_working_checkpoint(
            self.store,
            life_id="life_gateway_test",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            user_goal="finish the long mixed task",
            active_plan=("verify inputs", "continue execution"),
            pending_effect_ids=("eff_" + "6" * 64,),
            latest_safe_step="input verification completed",
            next_step="resume the durable effect",
            recovery_preconditions=("effect receipt must be reconciled",),
            created_at_ms=2_050,
        )
        self.assertEqual(duplicate.capsule.capsule_id, working.capsule.capsule_id)
        compressed = persist_compression_checkpoint(
            self.store,
            life_id="life_gateway_test",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            user_goal="finish the long mixed task",
            active_plan=("continue execution",),
            pending_effect_ids=("eff_" + "6" * 64,),
            latest_safe_step="verified files and effect boundary retained",
            next_step="continue from the next plan item",
            recovery_preconditions=("reload only the verified checkpoint",),
            created_at_ms=2_100,
        )
        self.assertEqual(compressed.capsule.capsule_kind, "COMPRESSION_CHECKPOINT")
        self.assertEqual(len(self.store.list_request_capsules(
            self.request_id, run_id=self.run_id, generation=1
        )), 2)

        decision = completion(self.request_id, self.run_id)
        terminal = persist_terminal_completion(
            self.store,
            decision,
            life_id="life_gateway_test",
            user_goal="finish the long mixed task",
            final_result="final verified result only",
            created_at_ms=2_200,
        )
        self.assertEqual(terminal.capsule.final_result, "final verified result only")
        self.assertEqual(terminal.capsule.active_plan, ())
        self.assertEqual(terminal.capsule.pending_effect_ids, ())
        self.assertIsNone(terminal.capsule.next_step)
        retry = persist_terminal_completion(
            self.store,
            decision,
            life_id="life_gateway_test",
            user_goal="finish the long mixed task",
            final_result="final verified result only",
            created_at_ms=2_250,
        )
        self.assertEqual(retry.capsule.capsule_id, terminal.capsule.capsule_id)
        self.assertTrue(retry.duplicate)
        self.assertEqual(len(self.store.list_request_capsules(
            self.request_id, run_id=self.run_id, generation=1
        )), 3)
        self.assertTrue(self.store.health_check(now_ms=2_300, full=True).healthy)


class GatewayV12MigrationTests(unittest.TestCase):
    def test_v11_outbox_survives_life_continuity_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gateway.sqlite3"
            destination = "tiangong-backend"
            effect_id = "eff_" + "8" * 64
            old_intent = OutboxIntent(
                outbox_id=derive_outbox_id(effect_id, destination, HASH_A),
                effect_id=effect_id,
                request_id="req_" + "1" * 64,
                run_id="run_" + "2" * 64,
                generation=1,
                destination_component_id=destination,
                intent_kind="EXECUTION",
                payload_object_id="legacy_payload",
                payload_sha256=HASH_A,
                created_at_ms=600,
                intent_sha256=HASH_B,
            ).with_computed_sha256()
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.execute("BEGIN EXCLUSIVE")
                for version, migration_id, statements in store_module._MIGRATIONS[:11]:  # noqa: SLF001
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                        (
                            version,
                            migration_id,
                            store_module._MIGRATION_DIGESTS[version],  # noqa: SLF001
                            500,
                        ),
                    )
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 11")
                connection.execute("COMMIT")
                store_module._configure_connection(connection)  # noqa: SLF001
                legacy_store = GatewayStateStore(path, connection)
                legacy_snapshot = new_state_snapshot(
                    "request",
                    entity_id="legacy_request_state",
                    request_id=old_intent.request_id,
                    run_id=old_intent.run_id,
                    generation=old_intent.generation,
                    created_at_ms=500,
                )
                legacy_store.initialize_snapshot(legacy_snapshot)
                legacy_event = TransitionEvent(
                    event_id="legacy_event_v11",
                    event_type="request.planning_started",
                    source_component_id="tiangong-total-gateway",
                    machine="request",
                    entity_id=legacy_snapshot.entity_id,
                    request_id=old_intent.request_id,
                    run_id=old_intent.run_id,
                    generation=old_intent.generation,
                    expected_revision=0,
                    to_state="PLANNING",
                    occurred_at_ms=600,
                    event_sha256=HASH_A,
                ).with_computed_event_sha256()
                legacy_store.apply_event_with_outbox(
                    legacy_event, (old_intent,), recorded_at_ms=650
                )
                legacy_store.close()
                connection = None
            finally:
                if connection is not None:
                    connection.close()
            store = GatewayStateStore.open(path, now_ms=1_000)
            try:
                self.assertEqual(store.get_outbox(old_intent.outbox_id).intent, old_intent)
                self.assertTrue(store.health_check(now_ms=1_100, full=True).healthy)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
