import tempfile
import unittest
from pathlib import Path

from communication_service.delivery_ledger import DeliveryLedger
from communication_service.wechat_text_outbound import default_wechat_text_policy
from communication_service.wechat_transfer_control import (
    WechatProgressRecorder,
    WechatTransferBudget,
    WechatTransferControlError,
    compute_wechat_upload_timeout,
)
from tests.test_delivery_contracts import (
    consume_verified_delivery_for_test,
    delivery_ticket,
)


class _Clock:
    def __init__(self, value=23_000):
        self.value = value

    def now(self):
        self.value += 1
        return self.value


class WechatTransferControlTests(unittest.TestCase):
    def test_dynamic_timeout_scales_caps_and_refuses_short_ticket_budget(self):
        policy = default_wechat_text_policy()
        small = compute_wechat_upload_timeout(
            policy,
            payload_bytes=1_000,
            ticket_timeout_ms=300_000,
            observed_throughput_bps=None,
        )
        self.assertTrue(small.allowed)
        self.assertEqual(small.timeout_ms, 7_502)
        large_slow = compute_wechat_upload_timeout(
            policy,
            payload_bytes=134_217_728,
            ticket_timeout_ms=300_000,
            observed_throughput_bps=1,
        )
        self.assertTrue(large_slow.allowed)
        self.assertTrue(large_slow.deadline_limited)
        self.assertEqual(large_slow.timeout_ms, 300_000)
        refused = compute_wechat_upload_timeout(
            policy,
            payload_bytes=1,
            ticket_timeout_ms=1_000,
            observed_throughput_bps=None,
        )
        self.assertFalse(refused.allowed)

    def test_account_budget_blocks_concurrency_and_reserved_byte_overflow(self):
        budget = WechatTransferBudget()
        with budget.reserve(
            "tenant:account", size_bytes=100, max_concurrent=1, max_reserved_bytes=200
        ):
            with self.assertRaisesRegex(
                WechatTransferControlError, "account_concurrency.exceeded"
            ):
                with budget.reserve(
                    "tenant:account",
                    size_bytes=1,
                    max_concurrent=1,
                    max_reserved_bytes=200,
                ):
                    pass
            with budget.reserve(
                "tenant:other", size_bytes=100, max_concurrent=1, max_reserved_bytes=200
            ):
                pass
        with self.assertRaisesRegex(
            WechatTransferControlError, "account_byte_budget.exceeded"
        ):
            with budget.reserve(
                "tenant:account",
                size_bytes=201,
                max_concurrent=2,
                max_reserved_bytes=200,
            ):
                pass

    def test_observed_bandwidth_is_smoothed_and_reused(self):
        budget = WechatTransferBudget()
        self.assertIsNone(budget.observed_throughput("a"))
        self.assertEqual(
            budget.observe("a", bytes_transferred=1_000_000, elapsed_ms=1_000),
            1_000_000,
        )
        self.assertEqual(
            budget.observe("a", bytes_transferred=100_000, elapsed_ms=1_000),
            775_000,
        )
        decision = compute_wechat_upload_timeout(
            default_wechat_text_policy(),
            payload_bytes=10_000_000,
            ticket_timeout_ms=300_000,
            observed_throughput_bps=budget.observed_throughput("a"),
        )
        self.assertEqual(decision.effective_throughput_bps, 775_000)

    def test_progress_is_thresholded_monotonic_and_persistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = DeliveryLedger.open(Path(temporary) / "delivery.sqlite3", now_ms=1_000)
            try:
                ticket = delivery_ticket()
                consume_verified_delivery_for_test(ledger, ticket, at_ms=22_000)
                clock = _Clock()
                progress = WechatProgressRecorder(
                    ledger,
                    effect_id=ticket.payload.effect_id,
                    part_id=ticket.payload.parts[0].part_id,
                    part_index=0,
                    phase="UPLOAD",
                    total_bytes=10,
                    interval_bytes=4,
                    clock_ms=clock.now,
                )
                progress.update(1)
                progress.update(4)
                progress.update(7)
                progress.update(8)
                progress.update(10, force=True)
                facts = ledger.list_transfer_progress(ticket.payload.effect_id)
                self.assertEqual([fact.bytes_completed for fact in facts], [4, 8, 10])
                self.assertTrue(ledger.health_check(now_ms=24_000, full=True).healthy)
                with self.assertRaises(WechatTransferControlError):
                    progress.update(9)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
