"""P15 M7: world candidate temporal validity and volatility."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event
from world_understanding.cognition import stability
from world_understanding.cognition.memory_candidate import (
    MemoryWorldCandidateBridge,
)
from world_understanding.cognition.store import WorldCognitionStore


LIFE = "life_p15_world_temporal"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class MemoryWorldTemporalValidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LifeShadowStore.open(
            self.root / "life.shadow.sqlite3", create=True, now_ms=500
        )
        self.coordinator = MemoryCoordinator(self.store)
        self.cognition = WorldCognitionStore(self.root / "wu")
        self.bridge = MemoryWorldCandidateBridge(self.cognition)

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

    def test_expired_l4_is_not_projected(self) -> None:
        value = event(1, None, life_id=LIFE, suffix="21" * 32)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="今天先记住版本是2。",
            plaintext=b"today version 2",
            created_at_ms=2_000,
            claim_key="claim:today-ver",
            semantic_domain="WORLD",
        )
        self.assertIsNotNone(l4.expires_at_ms)
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=200_000_000
        )
        self.assertEqual(len(candidates), 0)

    def test_expiry_sets_short_volatility_and_valid_until(self) -> None:
        value = event(1, None, life_id=LIFE, suffix="22" * 32)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="今天先记住版本是3。",
            plaintext=b"today version 3",
            created_at_ms=2_000,
            claim_key="claim:today-ver-3",
            semantic_domain="WORLD",
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=2_000
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].volatility_class, "short")
        self.assertIsNotNone(candidates[0].valid_until_ms)
        self.assertEqual(
            candidates[0].valid_until_ms, l4.expires_at_ms
        )

    def test_stable_l3_uses_medium_volatility(self) -> None:
        self._l3_world(
            suffix="23" * 32,
            claim_key="claim:stable-ver",
            plaintext=b"stable version",
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].volatility_class, "medium")
        self.assertIsNone(candidates[0].valid_until_ms)

    def test_evidence_expiry_drops_at_evaluation_time(self) -> None:
        self._l3_world(
            suffix="24" * 32,
            claim_key="claim:expiry-evidence",
            plaintext=b"temporary fact",
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        candidate = candidates[0]
        evidence = self.bridge.to_cognition_evidence(
            candidate, now_ms=4_000
        )
        # Force an expiry in the near future.
        expired = evidence.model_copy(
            update={"valid_until_ms": 4_500}
        ).with_computed_evidence_sha256()
        fresh = stability.evaluate_evidence(
            cognition_id="cog_test",
            life_id=LIFE,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            support=(expired,),
            now_ms=4_000,
        )
        stale = stability.evaluate_evidence(
            cognition_id="cog_test",
            life_id=LIFE,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            support=(expired,),
            now_ms=5_000,
        )
        self.assertGreater(fresh.support_milli, 0)
        self.assertIn(expired.evidence_id, stale.dropped_expired)

    def test_validity_interval_is_forward(self) -> None:
        self._l3_world(
            suffix="25" * 32,
            claim_key="claim:forward",
            plaintext=b"forward fact",
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        self.assertLessEqual(
            candidates[0].valid_from_ms,
            candidates[0].valid_until_ms
            if candidates[0].valid_until_ms is not None
            else candidates[0].valid_from_ms,
        )

    def test_short_volatility_decays_support_over_time(self) -> None:
        _value, _l3 = self._l3_world(
            suffix="26" * 32,
            claim_key="claim:decay",
            plaintext=b"short-lived fact",
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        candidate = candidates[0]
        evidence = self.bridge.to_cognition_evidence(
            candidate, now_ms=4_000
        )
        fresh = stability.evaluate_evidence(
            cognition_id="cog_decay",
            life_id=LIFE,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            support=(evidence,),
            now_ms=4_000,
        )
        stale = stability.evaluate_evidence(
            cognition_id="cog_decay",
            life_id=LIFE,
            domain="external",
            world_scope_hash=candidate.world_scope_hash,
            principal_scope_hash=candidate.principal_scope_hash,
            support=(evidence,),
            now_ms=4_000 + 7 * 60 * 60 * 1000,
        )
        self.assertGreater(fresh.support_milli, stale.support_milli)

    def test_medium_volatility_survives_moderate_delay(self) -> None:
        _value, _l3 = self._l3_world(
            suffix="27" * 32,
            claim_key="claim:medium",
            plaintext=b"medium fact",
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        self.assertEqual(candidates[0].volatility_class, "medium")


if __name__ == "__main__":
    unittest.main()
