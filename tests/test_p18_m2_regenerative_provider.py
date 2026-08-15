from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import InboundEnvelope, InboundScope, canonical_sha256, derive_inbound_scope_keys, derive_run_identity
from total_gateway.continuity import persist_working_checkpoint
from total_gateway.regenerative_execution import ExecutionFrontier, ZERO_HASH, derive_logical_effect_id
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority, authority_hash
from total_gateway.store import GatewayStateStore


HASH_A = "a" * 64


def inbound(tag: str) -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop", tenant_id=f"tenant_{tag}", link_account_id=f"link_{tag}",
        conversation_ref=f"conversation_{tag}", channel_message_ref=f"message_{tag}",
        sender_ref=f"sender_{tag}",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id=f"inbound_{tag}", channel=scope.channel, tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id, conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash, message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref, sender_ref=scope.sender_ref,
        received_at_ms=1000, idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A, text="perform a durable tool action",
    )


class RegenerativeProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(inbound("provider"), ingress_sha256=HASH_A, created_at_ms=1100)
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.generation = 1
        self.life_id = "life_provider"
        self.ticket = "ticket_p18_m2_provider"
        self.store.acquire_generation_lease(
            request_id=self.request_id, run_id=self.run_id, run_sequence=1,
            generation=self.generation, gateway_epoch=1, lease_id="lease_provider",
            owner_instance_id="gateway_provider", issued_at_ms=1200, lease_duration_ms=500_000,
        )
        self.root_hash = canonical_sha256({"goal": "durable effect"})
        self.task_hash = canonical_sha256({"task": "provider contract"})
        self.provider = RegenerativeExecutionAuthority(self.store)
        initialized = self.provider(self.payload(
            "initialize", now_ms=1300, root_goal_hash=self.root_hash,
            task_contract_hash=self.task_hash, epoch_index=0,
        ))
        self.assertTrue(initialized["initialized"])

    def tearDown(self) -> None:
        try:
            self.store.close()
        finally:
            self.temp.cleanup()

    def payload(self, operation: str, **extra):
        return {
            "operation": operation,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "life_id": self.life_id,
            "outer_execution_ticket_id": self.ticket,
            **extra,
        }

    def logical(self, *, target: str = "C:/tmp/result.txt") -> tuple[str, str]:
        post = canonical_sha256({"target": target, "condition": "exists with expected bytes"})
        logical = derive_logical_effect_id(
            request_id=self.request_id, run_id=self.run_id, generation=self.generation,
            obligation_key="deliver-result", effect_namespace="omni_body:file.write",
            normalized_target=target, desired_postcondition_sha256=post,
        )
        return logical, post

    def effect_payload(self, operation: str, *, global_step: int, now_ms: int, target: str = "C:/tmp/result.txt", **extra):
        logical, post = self.logical(target=target)
        return self.payload(
            operation, now_ms=now_ms, epoch_index=global_step // 75, global_step=global_step,
            logical_effect_id=logical, obligation_key="deliver-result",
            effect_namespace="omni_body:file.write", normalized_target=target,
            desired_postcondition_sha256=post, attempt=global_step, **extra,
        )

    def test_prepare_is_durable_before_start_and_committed_effect_is_not_dispatchable_twice(self) -> None:
        prepared = self.provider(self.effect_payload("prepare_effect", global_step=1, now_ms=2000))
        self.assertEqual(prepared["disposition"], "prepared")
        self.assertEqual(self.store.get_effect(prepared["effect_id"]).state, "CLAIMED")
        started = self.provider(self.payload(
            "start_effect", now_ms=2100, epoch_index=0,
            effect_id=prepared["effect_id"], logical_effect_id=prepared["logical_effect_id"],
            attempt_id=prepared["attempt_id"], step_id=prepared["step_id"],
        ))
        self.assertTrue(started["dispatch_permitted"])
        self.assertEqual(self.store.get_effect(prepared["effect_id"]).state, "SIDE_EFFECT_STARTED")
        finished = self.provider(self.payload(
            "finish_effect", now_ms=2200, epoch_index=0,
            effect_id=prepared["effect_id"], logical_effect_id=prepared["logical_effect_id"],
            attempt_id=prepared["attempt_id"], step_id=prepared["step_id"], outcome="succeeded",
            result_summary={"ok": True, "path": "C:/tmp/result.txt"},
        ))
        self.assertEqual(finished["effect_state"], "SUCCEEDED")
        duplicate = self.provider(self.effect_payload("prepare_effect", global_step=2, now_ms=2300))
        self.assertEqual(duplicate["effect_id"], prepared["effect_id"])
        self.assertEqual(duplicate["disposition"], "already_committed")
        second_start = self.provider(self.payload(
            "start_effect", now_ms=2400, epoch_index=0,
            effect_id=duplicate["effect_id"], logical_effect_id=duplicate["logical_effect_id"],
            attempt_id=duplicate["attempt_id"], step_id=duplicate["step_id"],
        ))
        self.assertFalse(second_start["dispatch_permitted"])
        self.assertEqual(second_start["disposition"], "already_committed")

    def test_restart_after_started_boundary_becomes_ambiguous_and_blocks_retry(self) -> None:
        prepared = self.provider(self.effect_payload("prepare_effect", global_step=1, now_ms=2000))
        self.provider(self.payload(
            "start_effect", now_ms=2100, epoch_index=0,
            effect_id=prepared["effect_id"], logical_effect_id=prepared["logical_effect_id"],
            attempt_id=prepared["attempt_id"], step_id=prepared["step_id"],
        ))
        frontier = self.frontier(version=1, global_step=1, pending=(prepared["effect_id"],))
        self.store.commit_execution_frontier(frontier, expected_revision=0, updated_at_ms=2150)
        continuity = persist_working_checkpoint(
            self.store, life_id=self.life_id, request_id=self.request_id, run_id=self.run_id,
            generation=self.generation, user_goal="durable effect", hard_constraints=(), active_plan=(),
            latest_safe_step="effect started", next_step="reconcile before retry",
            recovery_preconditions=("reconcile started effect",), created_at_ms=2160,
        )
        checkpoint = self.provider(self.payload(
            "commit_checkpoint", now_ms=2170, frontier=frontier.model_dump(mode="json"),
            continuity_capsule_id=continuity.capsule.capsule_id,
            recovery_preconditions=["reconcile started effect"], critical_fact_status="verified",
        ))
        self.assertTrue(checkpoint["committed"])
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=2300)
        self.provider = RegenerativeExecutionAuthority(self.store)
        recovered = self.provider(self.payload("recover", now_ms=2400))
        self.assertTrue(recovered["recoverable"])
        self.assertIn(prepared["effect_id"], recovered["ambiguous_effect_ids"])
        duplicate = self.provider(self.effect_payload("prepare_effect", global_step=2, now_ms=2500))
        self.assertEqual(duplicate["disposition"], "reconcile_required")

    def test_proven_not_applied_reconciliation_allows_new_physical_attempt(self) -> None:
        first = self.provider(self.effect_payload("prepare_effect", global_step=1, now_ms=2000))
        self.provider(self.payload(
            "start_effect", now_ms=2100, epoch_index=0, effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"], attempt_id=first["attempt_id"], step_id=first["step_id"],
        ))
        self.provider(self.payload(
            "finish_effect", now_ms=2200, epoch_index=0, effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"], attempt_id=first["attempt_id"], step_id=first["step_id"],
            outcome="ambiguous", result_summary={"timeout": True},
        ))
        blocked = self.provider(self.effect_payload("prepare_effect", global_step=2, now_ms=2300))
        self.assertEqual(blocked["disposition"], "reconcile_required")
        reconciled = self.provider(self.payload(
            "reconcile_effect", now_ms=2400, epoch_index=0, effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"], attempt_id=first["attempt_id"], step_id=first["step_id"],
            verdict="PROVEN_NOT_APPLIED", evidence={"target_absent": True},
        ))
        self.assertTrue(reconciled["retry_allowed"])
        second = self.provider(self.effect_payload("prepare_effect", global_step=3, now_ms=2500))
        self.assertEqual(second["disposition"], "prepared")
        self.assertNotEqual(second["effect_id"], first["effect_id"])
        self.assertEqual(second["logical_effect_id"], first["logical_effect_id"])

    def test_applied_reconciliation_commits_logical_effect_and_blocks_retry(self) -> None:
        first = self.provider(self.effect_payload("prepare_effect", global_step=1, now_ms=2000))
        self.provider(self.payload(
            "start_effect", now_ms=2100, epoch_index=0, effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"], attempt_id=first["attempt_id"], step_id=first["step_id"],
        ))
        self.provider(self.payload(
            "finish_effect", now_ms=2200, epoch_index=0, effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"], attempt_id=first["attempt_id"], step_id=first["step_id"],
            outcome="ambiguous", result_summary={"timeout": True},
        ))
        reconciled = self.provider(self.payload(
            "reconcile_effect", now_ms=2300, epoch_index=0, effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"], attempt_id=first["attempt_id"], step_id=first["step_id"],
            verdict="APPLIED", evidence={"target_hash_matches": True},
        ))
        self.assertTrue(reconciled["logical_committed"])
        duplicate = self.provider(self.effect_payload("prepare_effect", global_step=2, now_ms=2400))
        self.assertEqual(duplicate["disposition"], "already_committed")
        self.assertEqual(duplicate["effect_id"], first["effect_id"])

    def frontier(self, *, version: int, global_step: int, pending=(), ambiguous=(), pending_obligations=()):
        return ExecutionFrontier(
            request_id=self.request_id, run_id=self.run_id, generation=self.generation,
            life_id=self.life_id, root_goal_hash=self.root_hash, task_contract_hash=self.task_hash,
            authority_hash=authority_hash(self.ticket), global_step=global_step,
            epoch_index=global_step // 75, epoch_step=global_step % 75,
            completed_obligation_ids=(), active_obligation_id=None,
            pending_obligation_ids=tuple(sorted(pending_obligations)),
            verified_fact_head=None, artifact_revision_head=None,
            pending_effect_ids=tuple(sorted(pending)), ambiguous_effect_ids=tuple(sorted(ambiguous)),
            active_blockers=(), failed_strategy_ids=(), latest_safe_step=f"step {global_step}",
            next_action_hint="continue", provider_turn_state_ref=None,
            frontier_version=version, frontier_hash=ZERO_HASH,
        ).with_computed_hash()

    def test_checkpoint_reality_audit_rejects_stale_critical_fact(self) -> None:
        frontier = self.frontier(version=1, global_step=5)
        continuity = persist_working_checkpoint(
            self.store, life_id=self.life_id, request_id=self.request_id, run_id=self.run_id,
            generation=self.generation, user_goal="durable effect", hard_constraints=(), active_plan=(),
            latest_safe_step="step 5", next_step="continue", recovery_preconditions=(), created_at_ms=2000,
        )
        rejected = self.provider(self.payload(
            "commit_checkpoint", now_ms=2100, frontier=frontier.model_dump(mode="json"),
            continuity_capsule_id=continuity.capsule.capsule_id,
            recovery_preconditions=[], critical_fact_status="stale",
        ))
        self.assertFalse(rejected["committed"])
        self.assertEqual(rejected["reason"], "checkpoint_reality_audit_failed")

    def test_completion_proof_rejects_pending_obligation_then_accepts_clean_frontier(self) -> None:
        blocked = self.frontier(version=1, global_step=10, pending_obligations=("ob_pending",))
        self.store.commit_execution_frontier(blocked, expected_revision=0, updated_at_ms=2000)
        rejected = self.provider(self.payload(
            "verify_completion", now_ms=2100, epoch_index=0, proposal_key="p1",
            runtime_blockers=[], life_gate_allowed=True, required_evidence_ready=True,
        ))
        self.assertFalse(rejected["verified_complete"])
        self.assertIn("task_obligations_pending", rejected["reasons"])
        clean = self.frontier(version=2, global_step=11)
        self.store.commit_execution_frontier(clean, expected_revision=1, updated_at_ms=2200)
        accepted = self.provider(self.payload(
            "verify_completion", now_ms=2300, epoch_index=0, proposal_key="p2",
            runtime_blockers=[], life_gate_allowed=True, required_evidence_ready=True,
        ))
        self.assertTrue(accepted["verified_complete"])
        self.assertEqual(accepted["reasons"], [])


if __name__ == "__main__":
    unittest.main()
