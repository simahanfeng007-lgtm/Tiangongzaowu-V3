"""P15 M7: durable outbox recovery and stable-memory WorldState influence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts.cognition_evidence import (
    CognitionSourceRef,
    derive_cognition_evidence_id,
)
from contracts.canonical import canonical_sha256
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore, LifeShadowStoreError
from tests.life_contract_support import event
from tests.test_memory_world_evidence_independence_p15 import direct_evidence
from world_understanding.cognition.memory_candidate import (
    MemoryWorldCandidateBridge,
)
from world_understanding.cognition.store import WorldCognitionStore
from world_understanding.world_state.store import WorldStateStore


LIFE = "life_p15_world_outbox"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class MemoryWorldOutboxRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "life.shadow.sqlite3"
        self.store = LifeShadowStore.open(
            self.path, create=True, now_ms=500
        )
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l3_world(self, *, suffix: str, claim_key: str, plaintext: bytes):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key=claim_key + ":diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key=claim_key,
            semantic_domain="WORLD",
            plaintext=plaintext,
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        return value, l3[1]

    def _two_candidates(self):
        self._l3_world(
            suffix="31" * 32,
            claim_key="claim:rec-a",
            plaintext=b"fact A stable",
        )
        self._l3_world(
            suffix="32" * 32,
            claim_key="claim:rec-b",
            plaintext=b"fact B stable",
        )
        _c, _s, candidates = (
            self.coordinator.project_memory_world_candidates(
                life_id=LIFE, now_ms=4_000
            )
        )
        self.assertEqual(len(candidates), 2)
        return candidates

    def test_outbox_survives_reopen_and_redelivers(self) -> None:
        candidates = self._two_candidates()
        self.store.close()
        with LifeShadowStore.open(
            self.path, create=False, now_ms=4_000
        ) as reopened:
            self.assertEqual(
                reopened.count_pending_world_candidates(LIFE), 2
            )
            rows = reopened.list_world_candidate_outbox(life_id=LIFE)
            self.assertEqual(len(rows), 2)
            first = rows[0][0]
            self.assertTrue(
                reopened.ack_world_candidate_outbox(
                    first.candidate_id,
                    receipt_id="receipt_reopen",
                    delivered_at_ms=4_000,
                )
            )
            self.assertEqual(
                reopened.count_pending_world_candidates(LIFE), 1
            )
        with LifeShadowStore.open(
            self.path, create=False, now_ms=4_000
        ) as reopened_again:
            delivered = reopened_again.list_world_candidate_outbox(
                status="delivered", life_id=LIFE
            )
            self.assertEqual(len(delivered), 1)

    def test_unknown_candidate_ack_raises(self) -> None:
        with self.assertRaises(LifeShadowStoreError):
            self.store.ack_world_candidate_outbox(
                "wmc_" + "0" * 64,
                receipt_id="r",
                delivered_at_ms=4_000,
            )

    def test_stable_memory_evidence_materializes_world_patch(self) -> None:
        candidates = self._two_candidates()
        cognition = WorldCognitionStore(self.root / "wu")
        bridge = MemoryWorldCandidateBridge(cognition)
        evidence_ids: list[str] = []
        directs = []
        for index, candidate in enumerate(candidates):
            outcome = bridge.ingest(candidate, now_ms=4_000)
            self.assertEqual(outcome["outcome"], "accepted")
            evidence_ids.append(outcome["evidence"].evidence_id)
            direct = direct_evidence(
                life_id=LIFE,
                world_scope_hash=candidate.world_scope_hash,
                principal_scope_hash=candidate.principal_scope_hash,
                root_hash=candidate.lineage_root_hashes[0],
                object_id=f"exec_{index}",
            )
            directs.append(direct)
        report = bridge.stability_report(
            candidates[0],
            now_ms=4_000,
            extra_support=tuple(directs),
        )
        self.assertGreaterEqual(report.support_group_count, 2)
        self.assertGreaterEqual(report.direct_support_group_count, 1)
        world_state = WorldStateStore(root=self.root / "ws")
        patch = bridge.materialize_world_patch(
            candidate=candidates[0],
            now_ms=4_000,
            world_state_store=world_state,
            extra_support=tuple(directs),
            evidence_ids=tuple(evidence_ids),
        )
        self.assertIsNotNone(patch)
        self.assertIn(
            patch["stability_level"],
            {"C2", "C3", "C4"},
        )
        records = world_state.active_cognition_records()
        self.assertTrue(
            any(
                record.get("patch_id") == patch["patch_id"]
                for record in records
            )
        )

    def test_memory_alone_cannot_reach_stable_world_patch(self) -> None:
        candidates = self._two_candidates()
        cognition = WorldCognitionStore(self.root / "wu")
        bridge = MemoryWorldCandidateBridge(cognition)
        for candidate in candidates:
            bridge.ingest(candidate, now_ms=4_000)
        report = bridge.stability_report(
            candidates[0], now_ms=4_000
        )
        self.assertEqual(report.direct_support_group_count, 0)
        patch = bridge.materialize_world_patch(
            candidate=candidates[0],
            now_ms=4_000,
            world_state_store=WorldStateStore(root=self.root / "ws2"),
        )
        self.assertIsNone(patch)

    def test_full_loop_project_ingest_ack(self) -> None:
        candidates = self._two_candidates()
        cognition = WorldCognitionStore(self.root / "wu")
        bridge = MemoryWorldCandidateBridge(cognition)
        for candidate in candidates:
            outcome = bridge.ingest(candidate, now_ms=4_000)
            self.assertEqual(outcome["outcome"], "accepted")
            self.assertTrue(
                self.store.ack_world_candidate_outbox(
                    candidate.candidate_id,
                    receipt_id="receipt_" + candidate.candidate_id[-8:],
                    delivered_at_ms=4_000,
                )
            )
        self.assertEqual(self.store.count_pending_world_candidates(LIFE), 0)
        self.assertEqual(
            len(self.store.list_world_candidate_outbox(status="delivered")),
            2,
        )

    def test_receipt_conflict_after_reopen(self) -> None:
        candidates = self._two_candidates()
        candidate = candidates[0]
        self.store.close()
        with LifeShadowStore.open(
            self.path, create=False, now_ms=4_000
        ) as reopened:
            self.assertTrue(
                reopened.ack_world_candidate_outbox(
                    candidate.candidate_id,
                    receipt_id="receipt_first",
                    delivered_at_ms=4_000,
                )
            )
            with self.assertRaises(LifeShadowStoreError):
                reopened.ack_world_candidate_outbox(
                    candidate.candidate_id,
                    receipt_id="receipt_second",
                    delivered_at_ms=4_100,
                )

    def test_failed_status_is_not_pending(self) -> None:
        self._two_candidates()
        rows = self.store.list_world_candidate_outbox(status="failed")
        self.assertEqual(len(rows), 0)
        self.assertEqual(self.store.count_pending_world_candidates(LIFE), 2)


if __name__ == "__main__":
    unittest.main()
