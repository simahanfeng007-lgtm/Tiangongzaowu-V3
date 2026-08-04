from __future__ import annotations

import unittest

from contracts import (
    DeliveryPartReceipt,
    DeliveryReceipt,
    OutboundPart,
    OutboundPlan,
    text_sha256,
)
from total_gateway.completion_gate import (
    CompletionGate,
    CompletionGateError,
    CompletionRequirements,
)
from total_gateway.docx_qc import DocxQcPolicy
from tests import test_docx_qc as docx_test_support


HASH_A = "a" * 64
HASH_B = "b" * 64
DELIVERY_ID = "del_" + "d" * 64
DELIVERY_EFFECT_ID = "eff_" + "e" * 64


class CompletionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = docx_test_support.DocxQcTests(
            methodName="test_1000_real_words_pass_and_deleted_text_is_not_counted"
        )
        self.fixture.setUp()
        gate_result = self.fixture.prepare(docx_test_support.docx_bytes("字" * 1000))
        self.manifest = self.fixture.qc.evaluate(
            gate_result,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1000),
            checked_at_ms=20_500,
        ).registration.record.manifest
        self.gate = CompletionGate(self.fixture.object_store, self.fixture.fact_ledger)

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def requirements(
        self,
        *,
        text_required: bool = False,
        include_execution: bool = True,
        include_artifact: bool = True,
        delivery_requirement: str = "NONE",
    ) -> CompletionRequirements:
        return CompletionRequirements(
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
            text_required=text_required,
            required_execution_effect_ids=(
                (self.fixture.effect.effect_id,) if include_execution else ()
            ),
            required_artifact_revision_ids=(
                (self.manifest.artifact_revision_id,) if include_artifact else ()
            ),
            delivery_requirement=delivery_requirement,
        )

    def plan(self, *, include_text: bool = True) -> OutboundPlan:
        parts = []
        if include_text:
            text = "文档已经生成并通过质检。"
            parts.append(
                OutboundPart(
                    part_id="part_text_001",
                    index=len(parts),
                    kind="text",
                    text=text,
                    text_sha256=text_sha256(text),
                )
            )
        parts.append(
            OutboundPart(
                part_id="part_artifact_001",
                index=len(parts),
                kind="artifact",
                artifact=self.manifest,
            )
        )
        return OutboundPlan(
            outbound_plan_id="outbound_plan_completion_001",
            delivery_id=DELIVERY_ID,
            effect_id=DELIVERY_EFFECT_ID,
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
            channel="wechat",
            tenant_id=self.manifest.tenant_id,
            link_account_id=self.manifest.link_account_id,
            conversation_ref="conversation_001",
            conversation_scope_hash=self.manifest.conversation_scope_hash,
            recipient_scope_hash=HASH_B,
            channel_policy_hash=HASH_A,
            created_at_ms=21_000,
            parts=tuple(parts),
            plan_sha256=HASH_A,
        ).with_computed_plan_sha256()

    @staticmethod
    def receipt(plan: OutboundPlan, stages: tuple[str, ...]) -> DeliveryReceipt:
        parts = []
        for planned, stage in zip(plan.parts, stages, strict=True):
            success = stage in {"CHANNEL_ACCEPTED", "DELIVERED"}
            failure = stage in {"FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS"}
            parts.append(
                DeliveryPartReceipt(
                    part_id=planned.part_id,
                    index=planned.index,
                    kind=planned.kind,
                    artifact_id=(None if planned.artifact is None else planned.artifact.artifact_id),
                    artifact_revision_id=(
                        None if planned.artifact is None else planned.artifact.artifact_revision_id
                    ),
                    stage=stage,
                    attempt=1,
                    started_at_ms=21_100,
                    finished_at_ms=21_200,
                    channel_message_ref=("channel_message_001" if success else None),
                    evidence_sha256=HASH_A,
                    platform_receipt_sha256=(HASH_B if success else None),
                    error_code=("channel.failure" if failure else None),
                )
            )
        if all(stage == "DELIVERED" for stage in stages):
            status = "DELIVERED"
        elif all(stage in {"CHANNEL_ACCEPTED", "DELIVERED"} for stage in stages):
            status = "CHANNEL_ACCEPTED"
        elif "AMBIGUOUS" in stages:
            status = "RECONCILE_REQUIRED"
        elif "FAILED_FINAL" in stages:
            status = "FAILED_FINAL"
        else:
            status = "FAILED_RETRYABLE"
        return DeliveryReceipt(
            receipt_id="delivery_receipt_completion_001",
            ticket_id="delivery_ticket_completion_001",
            delivery_id=plan.delivery_id,
            effect_id=plan.effect_id,
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
            channel=plan.channel,
            status=status,
            parts=tuple(parts),
            observed_at_ms=21_300,
            error_code=(None if status in {"CHANNEL_ACCEPTED", "DELIVERED"} else "channel.failure"),
            receipt_sha256=HASH_A,
        ).with_computed_receipt_sha256()

    def test_local_file_completion_requires_execution_fact_and_qc_artifact(self) -> None:
        decision = self.gate.evaluate(
            self.requirements(),
            artifacts=(self.manifest,),
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        self.assertTrue(decision.execution_ready)
        self.assertTrue(decision.artifacts_ready)
        self.assertTrue(decision.can_transition_request_completed)
        self.assertFalse(decision.can_claim_platform_delivered)
        self.assertTrue(decision.has_valid_sha256())
        self.assertEqual(len(decision.supporting_fact_ids), 2)

    def test_model_completion_text_cannot_fill_missing_execution_or_artifact(self) -> None:
        missing_effect = "eff_" + "9" * 64
        requirements = CompletionRequirements(
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
            text_required=True,
            required_execution_effect_ids=(missing_effect,),
            required_artifact_revision_ids=(self.manifest.artifact_revision_id,),
            delivery_requirement="NONE",
        )
        decision = self.gate.evaluate(requirements, candidate_text="已经生成成功。")
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        self.assertTrue(decision.text_ready)
        self.assertFalse(decision.execution_ready)
        self.assertFalse(decision.artifacts_ready)
        self.assertFalse(decision.can_transition_request_completed)
        self.assertEqual(decision.supporting_fact_ids, ())

    def test_text_only_chat_can_complete_without_fake_tool_fact(self) -> None:
        requirements = self.requirements(
            text_required=True,
            include_execution=False,
            include_artifact=False,
        )
        decision = self.gate.evaluate(requirements, candidate_text="这是正常聊天回复。")
        self.assertEqual(decision.outcome, "COMPLETED")
        self.assertEqual(decision.supporting_fact_ids, ())
        self.assertIsNotNone(decision.candidate_text_sha256)

    def test_channel_accepted_and_delivered_are_distinct_success_levels(self) -> None:
        plan = self.plan()
        requirements = self.requirements(
            text_required=True,
            delivery_requirement="CHANNEL_ACCEPTED",
        )
        accepted = self.gate.evaluate(
            requirements,
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_receipt=self.receipt(plan, ("CHANNEL_ACCEPTED", "CHANNEL_ACCEPTED")),
        )
        self.assertEqual(accepted.outcome, "COMPLETED")
        self.assertTrue(accepted.delivery_ready)
        self.assertFalse(accepted.can_claim_platform_delivered)

        delivered_requirements = requirements.model_copy(update={"delivery_requirement": "DELIVERED"})
        not_yet_delivered = self.gate.evaluate(
            delivered_requirements,
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_receipt=self.receipt(plan, ("CHANNEL_ACCEPTED", "CHANNEL_ACCEPTED")),
        )
        self.assertEqual(not_yet_delivered.outcome, "IN_PROGRESS")
        delivered = self.gate.evaluate(
            delivered_requirements,
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_receipt=self.receipt(plan, ("DELIVERED", "DELIVERED")),
        )
        self.assertEqual(delivered.outcome, "COMPLETED")
        self.assertTrue(delivered.can_claim_platform_delivered)

    def test_text_success_and_attachment_failure_is_partial_not_complete(self) -> None:
        plan = self.plan()
        decision = self.gate.evaluate(
            self.requirements(text_required=True, delivery_requirement="CHANNEL_ACCEPTED"),
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_receipt=self.receipt(plan, ("CHANNEL_ACCEPTED", "FAILED_FINAL")),
        )
        self.assertEqual(decision.outcome, "PARTIAL")
        self.assertFalse(decision.can_transition_request_completed)
        self.assertEqual(
            tuple(item.stage for item in decision.delivery_parts),
            ("CHANNEL_ACCEPTED", "FAILED_FINAL"),
        )

    def test_ambiguous_delivery_requires_reconciliation_and_swaps_are_rejected(self) -> None:
        plan = self.plan(include_text=False)
        requirements = self.requirements(delivery_requirement="CHANNEL_ACCEPTED")
        ambiguous = self.gate.evaluate(
            requirements,
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_receipt=self.receipt(plan, ("AMBIGUOUS",)),
        )
        self.assertEqual(ambiguous.outcome, "RECONCILE_REQUIRED")
        self.assertTrue(ambiguous.needs_reconciliation)

        valid = self.receipt(plan, ("CHANNEL_ACCEPTED",))
        swapped_part = valid.parts[0].model_copy(
            update={"artifact_revision_id": "arv_" + "f" * 64}
        )
        swapped = valid.model_copy(
            update={"parts": (swapped_part,), "receipt_sha256": HASH_A}
        ).with_computed_receipt_sha256()
        with self.assertRaisesRegex(CompletionGateError, "part_binding_invalid"):
            self.gate.evaluate(
                requirements,
                artifacts=(self.manifest,),
                outbound_plan=plan,
                delivery_receipt=swapped,
            )

        forged_manifest = self.manifest.model_copy(
            update={"filename": "forged.docx", "manifest_sha256": HASH_A}
        ).with_computed_manifest_sha256()
        with self.assertRaisesRegex(CompletionGateError, "qc_fact_invalid"):
            self.gate.evaluate(requirements.model_copy(update={"delivery_requirement": "NONE"}), artifacts=(forged_manifest,))

    def test_machine_transport_failure_uses_the_same_completion_gate(self) -> None:
        plan = self.plan()
        requirements = self.requirements(
            text_required=True,
            delivery_requirement="CHANNEL_ACCEPTED",
        )
        ambiguous = self.gate.evaluate(
            requirements,
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_failure="AMBIGUOUS",
        )
        self.assertEqual(ambiguous.outcome, "RECONCILE_REQUIRED")
        self.assertTrue(ambiguous.needs_reconciliation)
        self.assertEqual(
            tuple(item.stage for item in ambiguous.delivery_parts),
            ("AMBIGUOUS", "AMBIGUOUS"),
        )
        failed = self.gate.evaluate(
            requirements,
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
            outbound_plan=plan,
            delivery_failure="FAILED_FINAL",
        )
        self.assertEqual(failed.outcome, "FAILED")
        self.assertFalse(failed.can_transition_request_completed)


if __name__ == "__main__":
    unittest.main()
