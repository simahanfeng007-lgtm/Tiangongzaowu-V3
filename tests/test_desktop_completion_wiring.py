from __future__ import annotations

import unittest
from pathlib import Path

from total_gateway.desktop_completion import (
    DesktopCompletionError,
    evaluate_desktop_completion,
)
from total_gateway.docx_qc import DocxQcPolicy
from tests import test_docx_qc as docx_test_support


ROOT = Path(__file__).resolve().parents[1]


class DesktopCompletionWiringTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_desktop_result_uses_machine_completion_evidence(self) -> None:
        decision = evaluate_desktop_completion(
            objects=self.fixture.object_store,
            facts=self.fixture.fact_ledger,
            request_id=self.manifest.request_id,
            run_id=self.manifest.run_id,
            generation=self.manifest.generation,
            execution_effect_id=self.fixture.effect.effect_id,
            candidate_text="文档已经生成并通过质检。",
            artifacts=(self.manifest,),
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        self.assertTrue(decision.has_valid_sha256())
        self.assertEqual(len(decision.supporting_fact_ids), 2)

    def test_desktop_result_fails_closed_without_execution_evidence(self) -> None:
        with self.assertRaisesRegex(
            DesktopCompletionError,
            "completion.required_evidence_pending",
        ):
            evaluate_desktop_completion(
                objects=self.fixture.object_store,
                facts=self.fixture.fact_ledger,
                request_id=self.manifest.request_id,
                run_id=self.manifest.run_id,
                generation=self.manifest.generation,
                execution_effect_id="eff_" + "9" * 64,
                candidate_text="文档已经生成并通过质检。",
                artifacts=(self.manifest,),
            )

    def test_orchestration_desktop_branch_calls_the_completion_adapter(self) -> None:
        source = (
            ROOT / "src" / "total_gateway" / "orchestration.py"
        ).read_text(encoding="utf-8")
        desktop_branch = source[source.index('if envelope.channel == "desktop":') :]
        desktop_branch = desktop_branch[: desktop_branch.index("delivery_now =")]
        self.assertIn("evaluate_desktop_completion(", desktop_branch)
        self.assertIn('"completion_decision_sha256": decision.decision_sha256', desktop_branch)
        terminal_write = desktop_branch.index("persist_terminal_completion(")
        completed_transition = desktop_branch.index('"COMPLETED",', terminal_write)
        session_completion = desktop_branch.index(
            "complete_session_request(", terminal_write
        )
        self.assertLess(terminal_write, completed_transition)
        self.assertLess(terminal_write, session_completion)


if __name__ == "__main__":
    unittest.main()
