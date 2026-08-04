import unittest

from pydantic import ValidationError

from contracts import (
    CircuitBreakerPolicy,
    DynamicTimeoutPolicy,
    ErrorDescriptor,
    RetryPolicy,
    acquire_circuit_permission,
    compute_dynamic_timeout,
    decide_retry,
    new_circuit_breaker,
    record_circuit_outcome,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def error_descriptor(**overrides):
    values = {
        "error_id": "error_001",
        "error_code": "channel.rate_limited",
        "source_component_id": "tiangong-communication-service",
        "phase": "channel_send",
        "disposition": "RETRYABLE",
        "observed_at_ms": 10_000,
        "attempt": 1,
        "side_effect_started": False,
        "evidence_sha256": HASH_A,
    }
    values.update(overrides)
    return ErrorDescriptor(**values)


def retry_policy(**overrides):
    values = {
        "policy_id": "channel_send_retry_v1",
        "revision": 1,
        "max_attempts": 4,
        "base_delay_ms": 1_000,
        "max_delay_ms": 8_000,
        "multiplier_milli": 2_000,
        "jitter_mode": "none",
        "respect_retry_after": True,
        "max_retry_after_ms": 60_000,
        "retry_budget_ms": 120_000,
        "minimum_attempt_runtime_ms": 2_000,
        "policy_sha256": HASH_C,
    }
    values.update(overrides)
    return RetryPolicy(**values).with_computed_sha256()


def timeout_policy(**overrides):
    values = {
        "policy_id": "channel_upload_timeout_v1",
        "revision": 1,
        "phase": "channel_upload",
        "base_timeout_ms": 5_000,
        "min_timeout_ms": 1_000,
        "max_timeout_ms": 600_000,
        "nominal_throughput_bps": 1_000_000,
        "minimum_throughput_bps": 100_000,
        "safety_factor_milli": 1_500,
        "idle_timeout_ms": 30_000,
        "policy_sha256": HASH_C,
    }
    values.update(overrides)
    return DynamicTimeoutPolicy(**values).with_computed_sha256()


def circuit_policy(**overrides):
    values = {
        "policy_id": "communication_breaker_v1",
        "revision": 1,
        "failure_threshold": 2,
        "rolling_window_ms": 60_000,
        "open_duration_ms": 30_000,
        "half_open_max_in_flight": 1,
        "half_open_success_threshold": 1,
        "policy_sha256": HASH_C,
    }
    values.update(overrides)
    return CircuitBreakerPolicy(**values).with_computed_sha256()


class FailureAndRetryTests(unittest.TestCase):
    def test_side_effect_semantics_fail_closed(self) -> None:
        invalid = (
            {"disposition": "RETRYABLE", "side_effect_started": True},
            {"disposition": "AMBIGUOUS", "side_effect_started": False},
            {"disposition": "FENCED", "side_effect_started": True},
            {"disposition": "TERMINAL", "retry_after_ms": 1_000, "retry_after_source": "header"},
            {"model_generated": True},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    error_descriptor(**values)

    def test_retry_uses_exponential_backoff_and_retry_after_floor(self) -> None:
        policy = retry_policy()
        second_failure = error_descriptor(attempt=2)
        decision = decide_retry(second_failure, policy, now_ms=20_000, elapsed_ms=5_000)
        self.assertTrue(decision.should_retry)
        self.assertEqual(decision.next_attempt, 3)
        self.assertEqual(decision.delay_ms, 2_000)

        rate_limited = error_descriptor(
            retry_after_ms=7_000,
            retry_after_source="header",
        )
        decision = decide_retry(rate_limited, policy, now_ms=20_000, elapsed_ms=5_000)
        self.assertEqual(decision.delay_ms, 7_000)
        self.assertEqual(decision.scheduled_at_ms, 27_000)

    def test_retry_stops_at_attempt_budget_deadline_or_oversized_retry_after(self) -> None:
        cases = (
            (
                error_descriptor(attempt=4),
                retry_policy(),
                {"now_ms": 20_000, "elapsed_ms": 5_000},
                "STOP_EXHAUSTED",
            ),
            (
                error_descriptor(attempt=2),
                retry_policy(retry_budget_ms=8_000),
                {"now_ms": 20_000, "elapsed_ms": 5_000},
                "STOP_EXHAUSTED",
            ),
            (
                error_descriptor(attempt=2),
                retry_policy(),
                {"now_ms": 20_000, "elapsed_ms": 5_000, "deadline_at_ms": 23_000},
                "STOP_DEADLINE",
            ),
            (
                error_descriptor(retry_after_ms=61_000, retry_after_source="platform"),
                retry_policy(),
                {"now_ms": 20_000, "elapsed_ms": 5_000},
                "STOP_EXHAUSTED",
            ),
        )
        for error, policy, arguments, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(decide_retry(error, policy, **arguments).action, expected)

    def test_ambiguous_outcome_requires_reconciliation_not_retry(self) -> None:
        error = error_descriptor(
            disposition="AMBIGUOUS",
            side_effect_started=True,
            error_code="channel.send_outcome_unknown",
        )
        decision = decide_retry(error, retry_policy(), now_ms=20_000, elapsed_ms=1_000)
        self.assertFalse(decision.should_retry)
        self.assertTrue(decision.should_reconcile)
        self.assertEqual(decision.action, "RECONCILE_REQUIRED")

    def test_jitter_is_deterministic_for_same_policy_and_attempt(self) -> None:
        policy = retry_policy(jitter_mode="full", jitter_seed_sha256=HASH_B)
        error = error_descriptor(attempt=2)
        first = decide_retry(error, policy, now_ms=20_000, elapsed_ms=1_000)
        second = decide_retry(error, policy, now_ms=20_000, elapsed_ms=1_000)
        self.assertEqual(first, second)
        self.assertLessEqual(first.delay_ms or 0, 2_000)


class DynamicTimeoutTests(unittest.TestCase):
    def test_timeout_scales_with_payload_and_observed_bandwidth(self) -> None:
        decision = compute_dynamic_timeout(
            timeout_policy(),
            payload_bytes=1_000_000,
            observed_throughput_bps=500_000,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.effective_throughput_bps, 500_000)
        self.assertEqual(decision.timeout_ms, 10_500)
        self.assertEqual(decision.idle_timeout_ms, 10_500)

        slow_large_file = compute_dynamic_timeout(
            timeout_policy(),
            payload_bytes=100_000_000,
            observed_throughput_bps=10_000,
        )
        self.assertEqual(slow_large_file.effective_throughput_bps, 100_000)
        self.assertEqual(slow_large_file.timeout_ms, 600_000)

    def test_timeout_respects_remaining_deadline_or_refuses_to_start(self) -> None:
        limited = compute_dynamic_timeout(
            timeout_policy(),
            payload_bytes=1_000_000,
            observed_throughput_bps=500_000,
            remaining_deadline_ms=8_000,
        )
        self.assertTrue(limited.allowed)
        self.assertTrue(limited.deadline_limited)
        self.assertEqual(limited.timeout_ms, 8_000)
        self.assertEqual(limited.idle_timeout_ms, 8_000)

        refused = compute_dynamic_timeout(
            timeout_policy(),
            payload_bytes=1,
            remaining_deadline_ms=999,
        )
        self.assertFalse(refused.allowed)
        self.assertEqual(refused.disposition, "DEADLINE_TOO_SHORT")


class CircuitBreakerTests(unittest.TestCase):
    def open_breaker(self):
        policy = circuit_policy()
        snapshot = new_circuit_breaker(
            policy,
            breaker_id="breaker_communication",
            target_component_id="tiangong-communication-service",
            now_ms=1_000,
        )
        first = record_circuit_outcome(
            snapshot,
            policy,
            outcome="COUNTED_FAILURE",
            probe=False,
            observed_at_ms=2_000,
            evidence_sha256=HASH_A,
        )
        second = record_circuit_outcome(
            first.current,
            policy,
            outcome="COUNTED_FAILURE",
            probe=False,
            observed_at_ms=3_000,
            evidence_sha256=HASH_B,
        )
        return policy, snapshot, first, second

    def test_failure_threshold_opens_and_returns_retry_after(self) -> None:
        policy, initial, first, second = self.open_breaker()
        self.assertEqual(first.current.state, "CLOSED")
        self.assertEqual(first.current.counted_failures, 1)
        self.assertEqual(second.current.state, "OPEN")
        self.assertEqual(second.current.next_probe_at_ms, 33_000)
        self.assertTrue(initial.has_valid_sha256())
        self.assertTrue(second.current.has_valid_sha256())

        denied = acquire_circuit_permission(second.current, policy, now_ms=4_000)
        self.assertFalse(denied.accepted)
        self.assertEqual(denied.disposition, "DENY_OPEN")
        self.assertEqual(denied.retry_after_ms, 29_000)

    def test_half_open_allows_bounded_probe_and_success_closes(self) -> None:
        policy, _, _, opened = self.open_breaker()
        permission = acquire_circuit_permission(opened.current, policy, now_ms=33_000)
        self.assertTrue(permission.accepted)
        self.assertTrue(permission.probe)
        self.assertEqual(permission.current.state, "HALF_OPEN")

        denied = acquire_circuit_permission(permission.current, policy, now_ms=33_001)
        self.assertEqual(denied.disposition, "DENY_PROBE_LIMIT")
        recovered = record_circuit_outcome(
            permission.current,
            policy,
            outcome="SUCCESS",
            probe=True,
            observed_at_ms=34_000,
            evidence_sha256=HASH_C,
        )
        self.assertEqual(recovered.current.state, "CLOSED")
        self.assertEqual(recovered.current.counted_failures, 0)

    def test_failed_probe_reopens_and_late_outcome_is_ignored(self) -> None:
        policy, _, _, opened = self.open_breaker()
        permission = acquire_circuit_permission(opened.current, policy, now_ms=33_000)
        failed = record_circuit_outcome(
            permission.current,
            policy,
            outcome="COUNTED_FAILURE",
            probe=True,
            observed_at_ms=34_000,
            evidence_sha256=HASH_A,
        )
        self.assertEqual(failed.current.state, "OPEN")
        self.assertEqual(failed.disposition, "REOPENED")
        late = record_circuit_outcome(
            failed.current,
            policy,
            outcome="SUCCESS",
            probe=True,
            observed_at_ms=35_000,
            evidence_sha256=HASH_B,
        )
        self.assertEqual(late.disposition, "IGNORED")
        self.assertEqual(late.current, failed.current)

    def test_tampered_snapshot_and_backdated_permission_fail_closed(self) -> None:
        policy = circuit_policy()
        snapshot = new_circuit_breaker(
            policy,
            breaker_id="breaker_communication",
            target_component_id="tiangong-communication-service",
            now_ms=1_000,
        )
        tampered = snapshot.model_copy(update={"counted_failures": 1})
        self.assertEqual(
            acquire_circuit_permission(tampered, policy, now_ms=2_000).disposition,
            "DIGEST_INVALID",
        )
        self.assertEqual(
            acquire_circuit_permission(snapshot, policy, now_ms=999).disposition,
            "TIME_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
