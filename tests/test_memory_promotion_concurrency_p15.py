"""P15 M3: concurrent promotion and ingress produce exactly one child."""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


class PromotionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "concurrent.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_concurrent_l1_ingress_has_one_winner(self) -> None:
        value = event(1, None)

        def commit(_index: int):
            with LifeShadowStore.open(
                self.path, create=False, now_ms=1_000
            ) as store:
                coordinator = MemoryCoordinator(store)
                _a, _d, created = coordinator.commit_life_event_l1(
                    value, event_payload=b"payload"
                )
                return created

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(commit, range(4)))
        self.assertEqual(sorted(outcomes), [False, False, False, True])
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations WHERE life_id = ?",
            (value.life_id,),
        ).fetchone()
        self.assertEqual(int(rows["n"]), 1)

    def test_concurrent_promotion_has_one_winner(self) -> None:
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:conc:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        self.assertIsNotNone(l2)

        def promote(_index: int):
            with LifeShadowStore.open(
                self.path, create=False, now_ms=2_000
            ) as store:
                coordinator = MemoryCoordinator(store)
                result = coordinator.promote_l2_to_l3(
                    life_id=value.life_id,
                    principal_ref=value.principal_ref,
                    privacy_scope=value.privacy_scope,
                    l2_derivation_ids=(l2[1].derivation_id,),
                    claim_key="claim:conc",
                    semantic_domain="SYSTEM",
                    plaintext=b"experience",
                    created_at_ms=3_000,
                    support_weights={l2[1].derivation_id: 1000},
                    counter_weights={},
                    causal_utility_milli={l2[1].derivation_id: 800},
                    recurrence_count=2,
                )
                return result[2] if result is not None else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(promote, range(4)))
        self.assertEqual(sorted(outcomes), [False, False, False, True])
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L3_EXPERIENCE'",
            (value.life_id,),
        ).fetchone()
        self.assertEqual(int(rows["n"]), 1)

    def test_concurrent_correction_and_promotion_do_not_double_write(self) -> None:
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:cc:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:cc",
            semantic_domain="SYSTEM",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        correction_event = event(1, None, suffix="cc" * 32)

        def correct(_index: int):
            with LifeShadowStore.open(
                self.path, create=False, now_ms=4_000
            ) as store:
                coordinator = MemoryCoordinator(store)
                try:
                    result = coordinator.correct_claim(
                        life_id=value.life_id,
                        principal_ref=value.principal_ref,
                        privacy_scope=value.privacy_scope,
                        target_derivation_id=l3[1].derivation_id,
                        user_message_event_id=correction_event.event_id,
                        plaintext=b"corrected",
                        created_at_ms=4_000,
                    )
                    return result[3]
                except Exception:
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(correct, range(2)))
        self.assertIn(
            frozenset(outcomes),
            (frozenset({False, True}), frozenset({None, True})),
        )
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L3_EXPERIENCE'",
            (value.life_id,),
        ).fetchone()
        self.assertEqual(int(rows["n"]), 2)


if __name__ == "__main__":
    unittest.main()
