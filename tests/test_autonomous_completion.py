from __future__ import annotations

import unittest

from contracts import InboundEnvelope
from total_gateway.autonomous_completion import evaluate_autonomous_completion
from tests import test_docx_qc as docx_test_support
from total_gateway.docx_qc import DocxQcPolicy


class AutonomousCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = docx_test_support.DocxQcTests(
            methodName="test_1000_real_words_pass_and_deleted_text_is_not_counted"
        )
        self.fixture.setUp()
        accepted = self.fixture.prepare(docx_test_support.docx_bytes("字" * 1000))
        self.manifest = self.fixture.qc.evaluate(
            accepted,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1000),
            checked_at_ms=20_500,
        ).registration.record.manifest
        envelope = InboundEnvelope(
            inbound_id="inbound_autonomous_completion",
            channel="system",
            tenant_id="tenant_autonomous",
            link_account_id="account_autonomous",
            conversation_ref="conversation_autonomous",
            conversation_scope_hash="1" * 64,
            principal_scope_hash="2" * 64,
            message_scope_hash="3" * 64,
            channel_message_ref="message_autonomous",
            sender_ref="life_scheduler",
            received_at_ms=500,
            idempotency_key="7" * 64,
            channel_metadata_hash="4" * 64,
            text="自主完成经授权的文档任务",
        )
        registration = self.fixture.gateway_store.register_request(
            envelope,
            ingress_sha256="5" * 64,
            created_at_ms=600,
        )
        self.assertEqual(registration.entry.request_id, self.manifest.request_id)
        self.fixture.gateway_store.acquire_generation_lease(
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            run_sequence=1,
            generation=self.manifest.generation,
            gateway_epoch=1,
            lease_id="lease_autonomous_completion",
            owner_instance_id="autonomous_completion_test",
            issued_at_ms=700,
            lease_duration_ms=60_000,
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_verified_autonomous_result_persists_the_same_terminal_evidence(self) -> None:
        decision = evaluate_autonomous_completion(
            store=self.fixture.gateway_store,
            objects=self.fixture.object_store,
            facts=self.fixture.fact_ledger,
            life_id="life_autonomous",
            user_goal="自主完成经授权的文档任务",
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
            execution_effect_ids=(self.fixture.effect.effect_id,),
            candidate_text="文档已生成并通过质检。",
            artifacts=(self.manifest,),
            evaluated_at_ms=21_000,
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        stored = self.fixture.gateway_store.list_completion_decisions(
            self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
        )
        terminal = self.fixture.gateway_store.get_terminal_request_capsule(
            self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
        )
        self.assertEqual(stored[0].decision, decision)
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal.capsule.final_result, "文档已生成并通过质检。")

    def test_missing_autonomous_evidence_persists_a_recoverable_checkpoint(self) -> None:
        missing_effect = "eff_" + "9" * 64
        decision = evaluate_autonomous_completion(
            store=self.fixture.gateway_store,
            objects=self.fixture.object_store,
            facts=self.fixture.fact_ledger,
            life_id="life_autonomous",
            user_goal="自主完成经授权的文档任务",
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
            execution_effect_ids=(missing_effect,),
            candidate_text="文档可能已完成。",
            artifacts=(self.manifest,),
            evaluated_at_ms=21_000,
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        active = self.fixture.gateway_store.get_active_request_capsule(
            self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
        )
        self.assertIsNotNone(active)
        self.assertEqual(active.capsule.pending_effect_ids, (missing_effect,))
        self.assertIsNone(
            self.fixture.gateway_store.get_terminal_request_capsule(
                self.manifest.request_id,
                run_id=self.manifest.run_id,
                generation=self.manifest.generation,
            )
        )


if __name__ == "__main__":
    unittest.main()
