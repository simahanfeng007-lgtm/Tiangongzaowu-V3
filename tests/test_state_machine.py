import unittest

from contracts import (
    StateSnapshot,
    TransitionEvent,
    aggregate_request_status,
    apply_transition,
    new_state_snapshot,
)


HASH_A = "a" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64


def transition_event(snapshot, to_state, **overrides):
    default_owner = {
        "request": "tiangong-total-gateway",
        "execution": "tiangong-backend",
        "artifact": "tiangong-total-gateway",
        "delivery": "tiangong-communication-service",
    }[snapshot.machine]
    values = {
        "event_id": f"event_{snapshot.machine}_{snapshot.revision + 1}",
        "event_type": f"{snapshot.machine}.state_changed",
        "source_component_id": default_owner,
        "machine": snapshot.machine,
        "entity_id": snapshot.entity_id,
        "request_id": snapshot.request_id,
        "run_id": snapshot.run_id,
        "generation": snapshot.generation,
        "expected_revision": snapshot.revision,
        "to_state": to_state,
        "occurred_at_ms": snapshot.updated_at_ms + 1,
        "event_sha256": HASH_A,
    }
    values.update(overrides)
    return TransitionEvent(**values).with_computed_event_sha256()


def state_snapshot(machine, state, *, entity_id=None, generation=1, revision=1):
    return StateSnapshot(
        machine=machine,
        entity_id=entity_id or f"{machine}_001",
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=generation,
        revision=revision,
        state=state,
        created_at_ms=10_000,
        updated_at_ms=10_000 + revision,
        last_event_id=f"event_{machine}_{revision}" if revision else None,
    )


class TransitionTests(unittest.TestCase):
    def test_request_follows_only_declared_path(self) -> None:
        snapshot = new_state_snapshot(
            "request",
            entity_id=REQUEST_ID,
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            created_at_ms=10_000,
        )
        illegal = apply_transition(
            snapshot,
            transition_event(snapshot, "COMPLETED", fact_id="fact_completion_001"),
        )
        self.assertEqual(illegal.disposition, "ILLEGAL_TRANSITION")
        self.assertEqual(illegal.current, snapshot)

        for state in ("PLANNING", "EXECUTING", "VALIDATING_ARTIFACTS", "DELIVERING"):
            decision = apply_transition(snapshot, transition_event(snapshot, state))
            self.assertTrue(decision.accepted)
            snapshot = decision.current
        completed = apply_transition(
            snapshot,
            transition_event(snapshot, "COMPLETED", fact_id="fact_completion_001"),
        )
        self.assertTrue(completed.accepted)
        self.assertTrue(completed.current.is_terminal)

    def test_terminal_state_rejects_more_transitions(self) -> None:
        snapshot = state_snapshot("request", "COMPLETED")
        decision = apply_transition(snapshot, transition_event(snapshot, "FAILED", fact_id="fact_001"))
        self.assertEqual(decision.disposition, "TERMINAL_REJECTED")

    def test_late_generation_and_duplicate_do_not_mutate_state(self) -> None:
        snapshot = state_snapshot("delivery", "SENDING", generation=3)
        late = apply_transition(
            snapshot,
            transition_event(
                snapshot,
                "CHANNEL_ACCEPTED",
                generation=2,
                evidence_sha256=HASH_A,
            ),
        )
        self.assertEqual(late.disposition, "LATE_IGNORED")
        self.assertEqual(late.current.revision, snapshot.revision)

        duplicate = apply_transition(
            snapshot,
            transition_event(snapshot, "CHANNEL_ACCEPTED", evidence_sha256=HASH_A),
            event_already_applied=True,
        )
        self.assertEqual(duplicate.disposition, "DUPLICATE")

    def test_rejects_wrong_owner_or_tampered_event(self) -> None:
        snapshot = state_snapshot("artifact", "QC_PENDING")
        wrong_owner = apply_transition(
            snapshot,
            transition_event(
                snapshot,
                "QC_PASSED",
                source_component_id="tiangong-backend",
                fact_id="fact_qc_001",
            ),
        )
        self.assertEqual(wrong_owner.disposition, "OWNER_REJECTED")

        event = transition_event(snapshot, "QC_PASSED", fact_id="fact_qc_001")
        tampered = event.model_copy(update={"event_type": "artifact.forged"})
        rejected = apply_transition(snapshot, tampered)
        self.assertEqual(rejected.disposition, "EVENT_DIGEST_REJECTED")

    def test_successful_or_failed_fact_state_requires_evidence(self) -> None:
        snapshot = state_snapshot("delivery", "SENDING")
        decision = apply_transition(snapshot, transition_event(snapshot, "CHANNEL_ACCEPTED"))
        self.assertEqual(decision.disposition, "EVIDENCE_REJECTED")

    def test_started_side_effect_cannot_be_cancelled_or_retried(self) -> None:
        snapshot = state_snapshot("execution", "RUNNING")
        for target in ("CANCELLED", "FENCED", "FAILED_RETRYABLE"):
            with self.subTest(target=target):
                decision = apply_transition(
                    snapshot,
                    transition_event(
                        snapshot,
                        target,
                        side_effect_started=True,
                        evidence_sha256=HASH_A if target == "FAILED_RETRYABLE" else None,
                    ),
                )
                self.assertEqual(decision.disposition, "AMBIGUOUS_REQUIRED")
        ambiguous = apply_transition(
            snapshot,
            transition_event(
                snapshot,
                "AMBIGUOUS",
                side_effect_started=True,
                evidence_sha256=HASH_A,
            ),
        )
        self.assertTrue(ambiguous.accepted)


class AggregateStatusTests(unittest.TestCase):
    def request(self):
        return state_snapshot("request", "DELIVERING", entity_id=REQUEST_ID)

    def test_channel_accepted_is_complete_but_not_delivered(self) -> None:
        aggregate = aggregate_request_status(
            self.request(),
            executions=(state_snapshot("execution", "SUCCEEDED"),),
            artifacts=(state_snapshot("artifact", "QC_PASSED"),),
            deliveries=(
                state_snapshot("delivery", "CHANNEL_ACCEPTED", entity_id="delivery_text_001"),
                state_snapshot("delivery", "DELIVERED", entity_id="delivery_file_001"),
            ),
        )
        self.assertEqual(aggregate.recommended_request_state, "COMPLETED")
        self.assertEqual(aggregate.display_phase, "channel_accepted")
        self.assertTrue(aggregate.can_claim_complete)
        self.assertFalse(aggregate.can_claim_delivered)

    def test_new_request_without_children_is_not_shown_as_delivering(self) -> None:
        request = state_snapshot("request", "RECEIVED", entity_id=REQUEST_ID)
        aggregate = aggregate_request_status(request)
        self.assertEqual(aggregate.display_phase, "received")
        self.assertEqual(aggregate.recommended_request_state, "RECEIVED")

    def test_completed_chat_without_deliveries_is_delivered_not_stuck_delivering(self) -> None:
        # 纯聊天等无 delivery 机的完成请求：聚合相位必须落在 delivered，
        # 不得卡在 delivering 让前端无法核验完成态。
        request = state_snapshot("request", "COMPLETED", entity_id=REQUEST_ID)
        aggregate = aggregate_request_status(request)
        self.assertEqual(aggregate.recommended_request_state, "COMPLETED")
        self.assertEqual(aggregate.display_phase, "delivered")
        self.assertTrue(aggregate.can_claim_complete)
        self.assertTrue(aggregate.can_claim_delivered)

    def test_all_parts_need_delivery_evidence_to_claim_delivered(self) -> None:
        aggregate = aggregate_request_status(
            self.request(),
            deliveries=(
                state_snapshot("delivery", "DELIVERED", entity_id="delivery_text_001"),
                state_snapshot("delivery", "DELIVERED", entity_id="delivery_file_001"),
            ),
        )
        self.assertEqual(aggregate.display_phase, "delivered")
        self.assertTrue(aggregate.can_claim_delivered)

    def test_partial_delivery_is_not_full_completion(self) -> None:
        aggregate = aggregate_request_status(
            self.request(),
            deliveries=(
                state_snapshot("delivery", "CHANNEL_ACCEPTED", entity_id="delivery_text_001"),
                state_snapshot("delivery", "FAILED_FINAL", entity_id="delivery_file_001"),
            ),
        )
        self.assertEqual(aggregate.recommended_request_state, "PARTIAL")
        self.assertEqual(aggregate.display_phase, "partial")
        self.assertFalse(aggregate.can_claim_complete)

    def test_ambiguous_child_forces_reconciliation(self) -> None:
        aggregate = aggregate_request_status(
            self.request(),
            deliveries=(state_snapshot("delivery", "AMBIGUOUS"),),
        )
        self.assertEqual(aggregate.display_phase, "reconcile_required")
        self.assertTrue(aggregate.needs_reconciliation)

    def test_failed_artifact_blocks_delivery_claim(self) -> None:
        aggregate = aggregate_request_status(
            self.request(),
            executions=(state_snapshot("execution", "SUCCEEDED"),),
            artifacts=(state_snapshot("artifact", "QC_FAILED"),),
        )
        self.assertEqual(aggregate.recommended_request_state, "FAILED")
        self.assertFalse(aggregate.can_claim_complete)

    def test_orphaned_cancelled_child_fails_parent_aggregate(self) -> None:
        aggregate = aggregate_request_status(
            self.request(),
            executions=(state_snapshot("execution", "CANCELLED"),),
        )
        self.assertEqual(aggregate.recommended_request_state, "FAILED")

    def test_rejects_child_from_other_generation(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_request_status(
                self.request(),
                deliveries=(state_snapshot("delivery", "DELIVERED", generation=2),),
            )


if __name__ == "__main__":
    unittest.main()
