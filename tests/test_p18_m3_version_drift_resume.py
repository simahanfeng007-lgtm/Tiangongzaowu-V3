"""P18-M3.12 production resume/version-drift regressions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import (
    InboundEnvelope,
    InboundScope,
    canonical_sha256,
    derive_inbound_scope_keys,
    derive_run_identity,
)
from total_gateway.continuity import persist_working_checkpoint
from total_gateway.regenerative_execution import ExecutionFrontier, ZERO_HASH
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority, authority_hash
from total_gateway.store import GatewayStateStore


HASH_A = "a" * 64


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_p18_m3_version",
        link_account_id="link_p18_m3_version",
        conversation_ref="conversation_p18_m3_version",
        channel_message_ref="message_p18_m3_version",
        sender_ref="sender_p18_m3_version",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_p18_m3_version",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=1000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A,
        text="resume with explicit version compatibility",
    )


class P18M3VersionDriftResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(inbound(), ingress_sha256=HASH_A, created_at_ms=1100)
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.generation = 1
        self.life_id = "life_p18_m3_version"
        self.ticket = "ticket_p18_m3_version"
        self.root_hash = canonical_sha256({"goal": "version drift"})
        self.task_hash = canonical_sha256({"task": "resume contract"})
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=self.generation,
            gateway_epoch=1,
            lease_id="lease_p18_m3_version",
            owner_instance_id="gateway_p18_m3_version",
            issued_at_ms=1200,
            lease_duration_ms=500_000,
        )
        self.provider = RegenerativeExecutionAuthority(self.store)
        initialized = self.provider(
            self.payload(
                "initialize",
                now_ms=1300,
                root_goal_hash=self.root_hash,
                task_contract_hash=self.task_hash,
                epoch_index=0,
            )
        )
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

    def frontier(self) -> ExecutionFrontier:
        return ExecutionFrontier(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            life_id=self.life_id,
            root_goal_hash=self.root_hash,
            task_contract_hash=self.task_hash,
            authority_hash=authority_hash(self.ticket),
            global_step=10,
            epoch_index=1,
            epoch_step=2,
            completed_obligation_ids=(),
            active_obligation_id=None,
            pending_obligation_ids=(),
            verified_fact_head=None,
            artifact_revision_head=None,
            pending_effect_ids=(),
            ambiguous_effect_ids=(),
            active_blockers=(),
            failed_strategy_ids=(),
            latest_safe_step="step 10",
            next_action_hint="resume",
            provider_turn_state_ref=None,
            frontier_version=1,
            frontier_hash=ZERO_HASH,
        ).with_computed_hash()

    def commit_checkpoint(self) -> None:
        frontier = self.frontier()
        continuity = persist_working_checkpoint(
            self.store,
            life_id=self.life_id,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            user_goal="version drift",
            hard_constraints=(),
            active_plan=(),
            latest_safe_step="step 10",
            next_step="resume",
            recovery_preconditions=(),
            created_at_ms=2000,
        )
        committed = self.provider(
            self.payload(
                "commit_checkpoint",
                now_ms=2100,
                frontier=frontier.model_dump(mode="json"),
                continuity_capsule_id=continuity.capsule.capsule_id,
                recovery_preconditions=[],
                critical_fact_status="verified",
                runtime_version="rt-v1",
                provider_version="provider-v1",
                model_version="model-v1",
                tool_contract_version="tools-v1",
                skill_contract_version="skills-v1",
                task_contract_version="task-v1",
            )
        )
        self.assertTrue(committed["committed"])

    def current_versions(self) -> dict[str, object]:
        return {
            "runtime_version": "rt-v1",
            "provider_version": "provider-v1",
            "model_version": "model-v1",
            "tool_contract_version": "tools-v1",
            "skill_contract_version": "skills-v1",
            "task_contract_version": "task-v1",
        }

    def test_exact_version_vector_resumes(self) -> None:
        self.commit_checkpoint()
        recovered = self.provider(self.payload("recover", now_ms=2200, **self.current_versions()))
        self.assertTrue(recovered["recoverable"])
        self.assertTrue(recovered["resume_allowed"])
        self.assertFalse(recovered["reconcile_required"])

    def test_runtime_version_mismatch_cannot_silently_resume(self) -> None:
        self.commit_checkpoint()
        versions = self.current_versions()
        versions["runtime_version"] = "rt-v2"
        recovered = self.provider(self.payload("recover", now_ms=2200, **versions))
        self.assertTrue(recovered["recoverable"])
        self.assertFalse(recovered["resume_allowed"])
        self.assertTrue(recovered["reconcile_required"])
        self.assertIn("runtime_version", recovered["version_mismatches"])
        events = self.store.list_execution_events(
            self.request_id,
            run_id=self.run_id,
            generation=self.generation,
        )
        self.assertFalse(any(event.event_type == "run.resumed" for event in events))

    def test_declared_compatible_drift_still_requires_revalidation(self) -> None:
        self.commit_checkpoint()
        versions = self.current_versions()
        versions["runtime_version"] = "rt-v2"
        recovered = self.provider(
            self.payload(
                "recover",
                now_ms=2200,
                compatible_version_mismatches=["runtime_version"],
                **versions,
            )
        )
        self.assertFalse(recovered["resume_allowed"])
        self.assertTrue(recovered["revalidation_required"])
        self.assertFalse(recovered["reconcile_required"])

    def test_revalidated_compatible_drift_may_resume(self) -> None:
        self.commit_checkpoint()
        versions = self.current_versions()
        versions["runtime_version"] = "rt-v2"
        recovered = self.provider(
            self.payload(
                "recover",
                now_ms=2200,
                compatible_version_mismatches=["runtime_version"],
                version_revalidated=True,
                **versions,
            )
        )
        self.assertTrue(recovered["resume_allowed"])
        self.assertFalse(recovered["reconcile_required"])


if __name__ == "__main__":
    unittest.main()
