from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import InboundEnvelope, InboundScope, canonical_sha256, derive_inbound_scope_keys, derive_run_identity
from total_gateway.effects import EffectResult
from total_gateway.regenerative_execution import derive_logical_effect_id
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority
from total_gateway.store import GatewayStateStore

HASH_A = "a" * 64


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop", tenant_id="tenant_crash_matrix", link_account_id="link_crash_matrix",
        conversation_ref="conversation_crash_matrix", channel_message_ref="message_crash_matrix",
        sender_ref="sender_crash_matrix",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_crash_matrix", channel=scope.channel, tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id, conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash, message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref, sender_ref=scope.sender_ref,
        received_at_ms=1_000, idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A, text="crash recovery matrix",
    )


class CrashMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(inbound(), ingress_sha256=HASH_A, created_at_ms=1_100)
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.generation = 1
        self.life_id = "life_crash_matrix"
        self.ticket = "ticket_crash_matrix"
        self.store.acquire_generation_lease(
            request_id=self.request_id, run_id=self.run_id, run_sequence=1, generation=1,
            gateway_epoch=1, lease_id="lease_crash_matrix", owner_instance_id="gateway_crash_matrix",
            issued_at_ms=1_200, lease_duration_ms=500_000,
        )
        self.provider = RegenerativeExecutionAuthority(self.store)
        self.provider(self.base(
            "initialize", now_ms=1_300,
            root_goal_hash=canonical_sha256({"goal": "crash matrix"}),
            task_contract_hash=canonical_sha256({"task": "crash matrix"}), epoch_index=0,
        ))

    def tearDown(self) -> None:
        try:
            self.store.close()
        finally:
            self.temp.cleanup()

    def reopen(self, *, now_ms: int) -> None:
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=now_ms)
        self.provider = RegenerativeExecutionAuthority(self.store)

    def base(self, operation: str, **extra) -> dict:
        return {
            "operation": operation,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "life_id": self.life_id,
            "outer_execution_ticket_id": self.ticket,
            **extra,
        }

    def effect_payload(self, operation: str, *, step: int, now_ms: int) -> dict:
        postcondition = canonical_sha256({"target": "C:/tmp/crash-matrix.txt", "content": "once"})
        logical = derive_logical_effect_id(
            request_id=self.request_id, run_id=self.run_id, generation=self.generation,
            obligation_key="write-once", effect_namespace="omni_body:file.write",
            normalized_target="path:C:/tmp/crash-matrix.txt",
            desired_postcondition_sha256=postcondition,
        )
        return self.base(
            operation, now_ms=now_ms, epoch_index=0, global_step=step, attempt=step,
            logical_effect_id=logical, obligation_key="write-once",
            effect_namespace="omni_body:file.write", normalized_target="path:C:/tmp/crash-matrix.txt",
            desired_postcondition_sha256=postcondition,
        )

    def prepare(self, *, step: int = 1, now_ms: int = 2_000) -> dict:
        result = self.provider(self.effect_payload("prepare_effect", step=step, now_ms=now_ms))
        self.assertEqual(result["disposition"], "prepared")
        return result

    def start(self, prepared: dict, *, now_ms: int = 2_100) -> dict:
        return self.provider(self.base(
            "start_effect", now_ms=now_ms, epoch_index=0,
            effect_id=prepared["effect_id"], logical_effect_id=prepared["logical_effect_id"],
            attempt_id=prepared["attempt_id"], step_id=prepared["step_id"],
        ))

    def terminal_result(self, prepared: dict, *, status: str, now_ms: int) -> EffectResult:
        evidence = canonical_sha256({"effect": prepared["effect_id"], "status": status})
        return EffectResult(
            result_id="rlt_" + canonical_sha256({"effect": prepared["effect_id"], "status": status}),
            effect_id=prepared["effect_id"], status=status,
            fact_id="fact_" + canonical_sha256({"effect": prepared["effect_id"], "evidence": evidence}),
            result_object_id=None, result_object_sha256=None, evidence_sha256=evidence,
            error_code=None if status == "SUCCEEDED" else f"recovered_{status.lower()}",
            observed_at_ms=now_ms, model_generated=False, result_sha256="0" * 64,
        ).with_computed_sha256()

    def events_for_effect(self, effect_id: str):
        return [
            event for event in self.store.list_execution_events(
                self.request_id, run_id=self.run_id, generation=self.generation
            ) if event.effect_id == effect_id
        ]

    def test_restart_after_prepared_before_start_marks_physical_attempt_not_applied_and_allows_new_attempt(self) -> None:
        first = self.prepare(step=1)
        self.assertEqual(self.store.get_effect(first["effect_id"]).state, "CLAIMED")
        self.reopen(now_ms=2_200)
        recovered = self.provider(self.base("recover", now_ms=2_300))
        self.assertFalse(recovered["recoverable"])
        self.assertEqual(self.store.get_effect(first["effect_id"]).state, "FAILED_FINAL")
        self.assertIn("step.failed", [event.event_type for event in self.events_for_effect(first["effect_id"])])
        second = self.provider(self.effect_payload("prepare_effect", step=2, now_ms=2_400))
        self.assertEqual(second["disposition"], "prepared")
        self.assertNotEqual(second["effect_id"], first["effect_id"])
        self.assertEqual(second["logical_effect_id"], first["logical_effect_id"])

    def test_restart_after_started_before_dispatch_event_reconstructs_ambiguity_from_prepared_event(self) -> None:
        first = self.prepare(step=1)
        # Simulate crash between durable Effect STARTED and execution step.dispatched append.
        self.store.mark_effect_started(first["effect_id"], started_at_ms=2_100)
        self.reopen(now_ms=2_200)
        self.provider(self.base("recover", now_ms=2_300))
        self.assertEqual(self.store.get_effect(first["effect_id"]).state, "AMBIGUOUS")
        types = [event.event_type for event in self.events_for_effect(first["effect_id"])]
        self.assertIn("step.dispatched", types)
        self.assertIn("step.ambiguous", types)
        retry = self.provider(self.effect_payload("prepare_effect", step=2, now_ms=2_400))
        self.assertEqual(retry["disposition"], "reconcile_required")
        self.assertEqual(retry["effect_id"], first["effect_id"])

    def test_restart_heals_effect_succeeded_before_step_committed(self) -> None:
        first = self.prepare(step=1)
        self.assertTrue(self.start(first)["dispatch_permitted"])
        self.store.complete_effect(self.terminal_result(first, status="SUCCEEDED", now_ms=2_200))
        self.reopen(now_ms=2_300)
        self.provider(self.base("recover", now_ms=2_400))
        types = [event.event_type for event in self.events_for_effect(first["effect_id"])]
        self.assertIn("step.committed", types)
        retry = self.provider(self.effect_payload("prepare_effect", step=2, now_ms=2_500))
        self.assertEqual(retry["disposition"], "already_committed")
        self.assertEqual(retry["effect_id"], first["effect_id"])

    def test_restart_heals_effect_ambiguous_before_step_ambiguous(self) -> None:
        first = self.prepare(step=1)
        self.assertTrue(self.start(first)["dispatch_permitted"])
        self.store.complete_effect(self.terminal_result(first, status="AMBIGUOUS", now_ms=2_200))
        self.reopen(now_ms=2_300)
        self.provider(self.base("recover", now_ms=2_400))
        types = [event.event_type for event in self.events_for_effect(first["effect_id"])]
        self.assertIn("step.ambiguous", types)
        retry = self.provider(self.effect_payload("prepare_effect", step=2, now_ms=2_500))
        self.assertEqual(retry["disposition"], "reconcile_required")

    def test_restart_heals_effect_failed_final_before_step_failed(self) -> None:
        first = self.prepare(step=1)
        self.assertTrue(self.start(first)["dispatch_permitted"])
        self.store.complete_effect(self.terminal_result(first, status="FAILED_FINAL", now_ms=2_200))
        self.reopen(now_ms=2_300)
        self.provider(self.base("recover", now_ms=2_400))
        types = [event.event_type for event in self.events_for_effect(first["effect_id"])]
        self.assertIn("step.failed", types)
        second = self.provider(self.effect_payload("prepare_effect", step=2, now_ms=2_500))
        self.assertEqual(second["disposition"], "prepared")
        self.assertNotEqual(second["effect_id"], first["effect_id"])


if __name__ == "__main__":
    unittest.main()
