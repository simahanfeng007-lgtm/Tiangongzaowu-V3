import unittest

from pydantic import ValidationError

from contracts import (
    RequestIdentity,
    derive_artifact_revision_identity,
    derive_delivery_identity,
    derive_effect_identity,
    derive_generation_fence,
    derive_request_identity,
    derive_run_identity,
    evaluate_generation_fence,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def request_and_run():
    request = derive_request_identity(HASH_A)
    run = derive_run_identity(request.request_id, 1)
    return request, run


class StableIdentityTests(unittest.TestCase):
    def test_request_is_deterministic_and_rejects_forged_identity(self) -> None:
        first = derive_request_identity(HASH_A)
        second = derive_request_identity(HASH_A)
        different = derive_request_identity(HASH_B)
        self.assertEqual(first, second)
        self.assertNotEqual(first.request_id, different.request_id)
        with self.assertRaises(ValidationError):
            RequestIdentity(
                inbound_idempotency_key=HASH_A,
                request_id="req_" + "f" * 64,
            )

    def test_run_sequence_creates_a_new_run_without_changing_request(self) -> None:
        request = derive_request_identity(HASH_A)
        first = derive_run_identity(request.request_id, 1)
        retry = derive_run_identity(request.request_id, 1)
        second = derive_run_identity(request.request_id, 2)
        self.assertEqual(first, retry)
        self.assertEqual(first.request_id, second.request_id)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_effect_id_is_stable_for_retry_and_changes_with_exact_intent(self) -> None:
        request, run = request_and_run()
        values = {
            "request_id": request.request_id,
            "run_id": run.run_id,
            "run_sequence": run.run_sequence,
            "generation": 1,
            "effect_kind": "execution",
            "ordinal": 0,
            "intent_sha256": HASH_B,
        }
        first = derive_effect_identity(**values)
        self.assertEqual(first, derive_effect_identity(**values))
        variants = (
            {**values, "generation": 2},
            {**values, "effect_kind": "delivery"},
            {**values, "ordinal": 1},
            {**values, "intent_sha256": HASH_C},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(first.effect_id, derive_effect_identity(**variant).effect_id)
        other_request = derive_request_identity(HASH_C)
        with self.assertRaises(ValidationError):
            derive_effect_identity(**{**values, "request_id": other_request.request_id})

    def test_artifact_revision_and_delivery_bind_immutable_content(self) -> None:
        request, run = request_and_run()
        first = derive_artifact_revision_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=1,
            artifact_intent_id="document_primary",
            revision=1,
            content_sha256=HASH_A,
        )
        second = derive_artifact_revision_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=2,
            artifact_intent_id="document_primary",
            revision=2,
            content_sha256=HASH_B,
        )
        self.assertEqual(first.artifact_id, second.artifact_id)
        self.assertNotEqual(first.artifact_revision_id, second.artifact_revision_id)

        delivery = derive_delivery_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=2,
            recipient_scope_hash=HASH_B,
            reply_to_message_ref="message_001",
            payload_manifest_sha256=HASH_C,
        )
        retry = derive_delivery_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=2,
            recipient_scope_hash=HASH_B,
            reply_to_message_ref="message_001",
            payload_manifest_sha256=HASH_C,
        )
        swapped_recipient = derive_delivery_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=2,
            recipient_scope_hash=HASH_A,
            reply_to_message_ref="message_001",
            payload_manifest_sha256=HASH_C,
        )
        self.assertEqual(delivery, retry)
        self.assertNotEqual(delivery.delivery_id, swapped_recipient.delivery_id)
        changed_reply_target = derive_delivery_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=2,
            recipient_scope_hash=HASH_B,
            reply_to_message_ref="message_002",
            payload_manifest_sha256=HASH_C,
        )
        self.assertNotEqual(delivery.delivery_id, changed_reply_target.delivery_id)
        changed_payload = derive_delivery_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=run.run_sequence,
            generation=2,
            recipient_scope_hash=HASH_B,
            reply_to_message_ref="message_001",
            payload_manifest_sha256=HASH_A,
        )
        self.assertNotEqual(delivery.delivery_id, changed_payload.delivery_id)


class GenerationFenceTests(unittest.TestCase):
    def fence(self, **overrides):
        request, run = request_and_run()
        values = {
            "gateway_epoch": 3,
            "request_id": request.request_id,
            "run_id": run.run_id,
            "run_sequence": run.run_sequence,
            "generation": 1,
            "lease_id": "lease_001",
            "issued_at_ms": 10_000,
            "expires_at_ms": 20_000,
        }
        values.update(overrides)
        return derive_generation_fence(**values)

    def evaluate(self, fence, **overrides):
        values = {
            "current_gateway_epoch": 3,
            "current_request_id": fence.request_id,
            "current_run_id": fence.run_id,
            "current_generation": 1,
            "active_lease_id": "lease_001",
            "now_ms": 15_000,
            "clock_skew_ms": 0,
        }
        values.update(overrides)
        return evaluate_generation_fence(fence, **values)

    def test_only_current_epoch_generation_lease_and_window_is_accepted(self) -> None:
        decision = self.evaluate(self.fence())
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.disposition, "CURRENT")

    def test_late_future_epoch_lease_and_expiry_are_distinct(self) -> None:
        cases = (
            (self.fence(generation=0), {}, "LATE_GENERATION"),
            (self.fence(generation=2), {}, "FUTURE_GENERATION"),
            (self.fence(), {"current_gateway_epoch": 4}, "EPOCH_MISMATCH"),
            (self.fence(), {"active_lease_id": "lease_002"}, "LEASE_MISMATCH"),
            (self.fence(), {"now_ms": 20_001}, "EXPIRED"),
            (self.fence(), {"now_ms": 9_999}, "NOT_YET_VALID"),
        )
        for fence, overrides, expected in cases:
            with self.subTest(expected=expected):
                decision = self.evaluate(fence, **overrides)
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.disposition, expected)

    def test_tampered_or_cross_run_fence_fails_closed(self) -> None:
        fence = self.fence()
        tampered = fence.model_copy(update={"generation": 2})
        self.assertEqual(self.evaluate(tampered).disposition, "DIGEST_INVALID")
        self.assertEqual(
            self.evaluate(fence, current_run_id="run_" + "f" * 64).disposition,
            "CONTEXT_MISMATCH",
        )

    def test_fence_lifetime_is_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            self.fence(expires_at_ms=3_610_001)


if __name__ == "__main__":
    unittest.main()
