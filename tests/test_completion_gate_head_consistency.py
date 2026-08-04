"""草案不变量 11：CompletionGate 必须同时验证 effect head 与证据投影一致。

仅 FactLedger 有成功证据而 effect head 缺失/仍为 STARTED/AMBIGUOUS 时不得报成功。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from total_gateway.completion_gate import CompletionGate, CompletionRequirements


@dataclass
class _FakeFact:
    fact_id: str
    fact_type: str
    effect_id: str


@dataclass
class _FakeResult:
    effect_id: str
    status: str


@dataclass
class _FakeBatch:
    result: _FakeResult


class _FakeFactLedger:
    def __init__(self, effect_id: str, status: str) -> None:
        self._fact = _FakeFact(fact_id="fact_x", fact_type="execution.succeeded", effect_id=effect_id)
        self._batch = _FakeBatch(_FakeResult(effect_id=effect_id, status=status))

    def list_request_facts(self, request_id, *, run_id, generation):
        return (self._fact,)

    def get_batch_for_fact(self, fact_id: str):
        assert fact_id == self._fact.fact_id
        return self._batch


class _NullObjectStore:
    pass


EFFECT_ID = "eff_" + "a" * 64


def _requirements(effect_id: str) -> CompletionRequirements:
    return CompletionRequirements(
        request_id="req_" + "1" * 64,
        run_id="run_" + "2" * 64,
        generation=1,
        text_required=False,
        required_execution_effect_ids=(effect_id,),
        required_artifact_revision_ids=(),
        delivery_requirement="NONE",
    )


class CompletionGateHeadConsistencyTests(unittest.TestCase):
    def _evaluate(self, head_state: str | None, fact_status: str = "SUCCEEDED"):
        gate = CompletionGate(
            _NullObjectStore(),
            _FakeFactLedger(EFFECT_ID, fact_status),
            head_state_reader=lambda _eid: head_state,
        )
        return gate.evaluate(_requirements(EFFECT_ID))

    def test_head_succeeded_and_fact_succeeded_completes(self) -> None:
        self.assertEqual(self._evaluate("SUCCEEDED").outcome, "COMPLETED")

    def test_head_reconciled_and_fact_succeeded_completes(self) -> None:
        self.assertEqual(self._evaluate("RECONCILED").outcome, "COMPLETED")

    def test_fact_succeeded_but_head_started_is_not_success(self) -> None:
        self.assertNotEqual(self._evaluate("SIDE_EFFECT_STARTED").outcome, "COMPLETED")

    def test_fact_succeeded_but_head_ambiguous_is_not_success(self) -> None:
        self.assertNotEqual(self._evaluate("AMBIGUOUS").outcome, "COMPLETED")

    def test_fact_succeeded_but_head_missing_is_not_success(self) -> None:
        self.assertNotEqual(self._evaluate(None).outcome, "COMPLETED")

    def test_fact_succeeded_but_head_failed_is_not_success(self) -> None:
        self.assertNotEqual(self._evaluate("FAILED_FINAL").outcome, "COMPLETED")

    def test_legacy_path_without_reader_unchanged(self) -> None:
        gate = CompletionGate(_NullObjectStore(), _FakeFactLedger(EFFECT_ID, "SUCCEEDED"))
        self.assertEqual(gate.evaluate(_requirements(EFFECT_ID)).outcome, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
