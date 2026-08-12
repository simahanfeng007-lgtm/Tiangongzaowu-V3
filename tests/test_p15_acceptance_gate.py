"""P15 final acceptance gate G1-G30 (behavioral + structural checks)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts.world_understanding.memory_candidate import (
    MemoryWorldCandidate,
)
from life_service import memory_promotion
from life_service.memory_coordinator import MemoryCoordinator
from life_service.p15_cutover import verify_cutover_phase
from life_service.store import LifeShadowStore
from life_service.temperament import (
    generate_innate_temperament,
    initial_temperament_state,
)
from tests.life_contract_support import event
from world_understanding.cognition.memory_candidate import (
    MemoryWorldCandidateBridge,
)
from world_understanding.cognition.store import WorldCognitionStore


ROOT = Path(__file__).resolve().parents[1]
LIFE = "life_p15_gate"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class P15AcceptanceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LifeShadowStore.open(
            self.root / "gate.shadow.sqlite3", create=True, now_ms=500
        )
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_g1_g4_traceability_and_never_overwrite(self) -> None:
        value = event(1, None, life_id=LIFE)
        assertion, l1, _c = self.coordinator.commit_life_event_l1(value)
        self.assertEqual(assertion.source_event_ids, (value.event_id,))
        self.assertEqual(l1.origin, "LIFE_EVENT")
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:g1:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        self.assertIsNotNone(l2)
        self.assertNotEqual(l2[1].memory_id, l1.memory_id)
        self.assertTrue(
            self.store.is_derivation_active(l1.derivation_id)
        )

    def test_g2_single_writer(self) -> None:
        result = verify_cutover_phase("B")
        self.assertTrue(result["ok"])

    def test_g3_five_layers_runnable(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:g3:diary",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:g3",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        l4_event = event(2, value.event_hash, life_id=LIFE, suffix="02" * 32)
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(l4_event)
        _a4, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=l4_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，这个模式很重要。",
            plaintext=b"pattern",
            created_at_ms=3_500,
            claim_key="claim:g3",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(l3[1].derivation_id, l4.derivation_id),
            claim_key="claim:g3",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"core",
            created_at_ms=5_000,
            support_weights={
                l3[1].derivation_id: 1000,
                l4.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=3,
        )
        self.assertIsNotNone(l5)
        layers = {
            l1.layer,
            l2[1].layer,
            l3[1].layer,
            l4.layer,
            l5[1].layer,
        }
        self.assertEqual(
            layers,
            {"L1_STREAM", "L2_DIARY", "L3_EXPERIENCE", "L4_EXPLICIT", "L5_CORE"},
        )

    def test_g5_g6_explicit_l4_is_not_verified(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        assertion, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，地球是平的。",
            plaintext=b"earth flat",
            created_at_ms=2_000,
            claim_key="claim:g5",
            semantic_domain="WORLD",
        )
        self.assertEqual(l4.layer, "L4_EXPLICIT")
        self.assertEqual(assertion.epistemic_status, "user_asserted")

    def test_g7_l5_has_semantic_domain(self) -> None:
        self.test_g3_five_layers_runnable()
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT semantic_domain FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L5_CORE'",
            (LIFE,),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(str(rows[0]["semantic_domain"]))

    def test_g8_g9_context_continuity_and_dedupe(self) -> None:
        from life_service.memory_context import (
            dedupe_lineage,
            select_layered_memories,
        )

        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            created_at_ms=2_000,
            claim_key="claim:g8",
            semantic_domain="USER_PREFERENCE",
        )
        instruction, data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertGreaterEqual(len(instruction), 1)
        self.assertFalse(any(item.layer == "L1_STREAM" for item in data))
        self.assertEqual(len(dedupe_lineage(())), 0)

    def test_g10_g11_temperament_gates(self) -> None:
        innate = generate_innate_temperament(
            life_id=LIFE, seed=3, created_at="2026-08-12T00:00:00Z"
        )
        state = initial_temperament_state(innate)
        for _ in range(100):
            adapted, receipts = self.coordinator.adapt_temperament_from_core(
                life_id=LIFE,
                innate=innate,
                current_temperament=state,
                now_ms=1_000,
            )
            self.assertEqual(receipts, ())
            self.assertEqual(adapted, state)

    def test_g12_self_identity_authority(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，你就是我的助手。",
            plaintext=b"assistant",
            created_at_ms=2_000,
            claim_key="claim:g12",
            semantic_domain="SELF_IDENTITY",
        )
        self.assertFalse(l4.self_cognition_eligible)

    def test_g13_g14_learning_closure_and_no_self_confirm(self) -> None:
        from life_service.life_learning_memory import derive_learning_result_ids

        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:g13:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:g13",
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_g13",
            result_sha256="11" * 32,
        )
        learning_event = event(
            1,
            None,
            life_id=LIFE,
            suffix=ids["event_id"].removeprefix("lev_"),
        )
        _a, refined, _audit, created = self.coordinator.commit_learning_result(
            learning_event=learning_event,
            learning_id="learning_g13",
            subject="gate",
            result_sha256="11" * 32,
            source_l3_derivation_ids=(l3[1].derivation_id,),
            refined_plaintext=b"refined",
            created_at_ms=4_000,
        )
        self.assertTrue(created)
        self.assertEqual(refined.origin, "LEARNING_RESULT")
        groups = memory_promotion.fold_independence(
            (l3[1], refined),
            {
                l3[1].derivation_id: 1000,
                refined.derivation_id: 750,
            },
        )
        self.assertEqual(len(groups), 1)

    def test_g15_g16_g17_cascade_principal_secret(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"chinese",
            created_at_ms=2_000,
            claim_key="claim:g15",
            semantic_domain="USER_PREFERENCE",
        )
        correction_event = event(1, None, life_id=LIFE, suffix="03" * 32)
        self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected",
            created_at_ms=3_000,
        )
        self.assertFalse(self.store.is_derivation_active(l4.derivation_id))
        from life_service.memory_context import select_layered_memories

        _instruction, data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref="principal_other",
            privacy_scope=PRIVACY,
            now_ms=4_000,
        )
        self.assertEqual(len(data), 0)
        # Secret memory is rejected at the candidate contract boundary.
        with self.assertRaises(ValueError):
            MemoryWorldCandidate(
                candidate_id="wmc_" + "1" * 64,
                life_id=LIFE,
                world_scope_hash="11" * 32,
                principal_scope_hash="22" * 32,
                source_memory_id="mem_" + "3" * 64,
                source_memory_revision=1,
                source_assertion_sha256="44" * 32,
                source_derivation_id="mdr_" + "5" * 64,
                source_layer="L3_EXPERIENCE",
                claim_key="claim:secret",
                semantic_payload="secret",
                evidence_refs=(),
                lineage_root_hashes=("77" * 32,),
                epistemic_status="user_asserted",
                confidence_milli=750,
                volatility_class="medium",
                valid_from_ms=1_000,
                valid_until_ms=None,
                privacy_scope="secret",
                candidate_sha256="0" * 64,
            ).with_computed_candidate_sha256()

    def test_g18_g19_g22_memory_world_candidate_and_antiloop(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:g18:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:g18",
            semantic_domain="WORLD",
            plaintext=b"world fact",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        self.assertEqual(len(candidates), 1)
        bridge = MemoryWorldCandidateBridge(
            WorldCognitionStore(self.root / "wu")
        )
        self.assertTrue(
            bridge.has_independent_reality_root(candidates[0])
        )
        outcome = bridge.ingest(candidates[0], now_ms=4_000)
        self.assertEqual(outcome["outcome"], "accepted")
        report = bridge.stability_report(candidates[0], now_ms=4_000)
        self.assertEqual(report.direct_support_group_count, 0)

    def test_g20_g21_memory_authority_zero_git_only(self) -> None:
        import re

        text = (
            ROOT
            / "src"
            / "world_understanding"
            / "source_compilers"
            / "p3.py"
        ).read_text(encoding="utf-8")
        memory = re.findall(
            r'CompilerSpec\("MEMORY"[^)]*?,\s*(\d+)\s*,\s*(\d+)\s*\)', text
        )
        self.assertTrue(memory)
        self.assertTrue(all(a == "0" and b == "0" for a, b in memory))
        self.assertIn('CompilerSpec("GIT_CODE"', text)

    def test_g23_outbox_recovery(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:g23:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:g23",
            semantic_domain="WORLD",
            plaintext=b"fact",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        _c, _s, candidates = self.coordinator.project_memory_world_candidates(
            life_id=LIFE, now_ms=4_000
        )
        self.store.close()
        with LifeShadowStore.open(
            self.root / "gate.shadow.sqlite3",
            create=False,
            now_ms=4_000,
        ) as reopened:
            self.assertEqual(
                reopened.count_pending_world_candidates(LIFE), 1
            )
            self.assertTrue(
                reopened.ack_world_candidate_outbox(
                    candidates[0].candidate_id,
                    receipt_id="gate_receipt",
                    delivered_at_ms=4_000,
                )
            )

    def test_g24_deterministic_ids_across_stores(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, d1, _c = self.coordinator.commit_life_event_l1(value)
        with tempfile.TemporaryDirectory() as other:
            other_path = Path(other) / "gate2.shadow.sqlite3"
            with LifeShadowStore.open(
                other_path, create=True, now_ms=500
            ) as other_store:
                _a2, d2, _c2 = MemoryCoordinator(
                    other_store
                ).commit_life_event_l1(value)
                self.assertEqual(d1.derivation_id, d2.derivation_id)

    def test_g26_g27_regression_and_mirror_artifacts(self) -> None:
        self.assertTrue((ROOT / "tests" / "test_world_understanding_p13_full_chain.py").is_file())
        self.assertTrue((ROOT / "tests" / "test_life_repository_bridge_p14.py").is_file())
        self.assertTrue(
            (ROOT / "app/life-service/runtime314/contracts/.tiangong-generated-source.json").is_file()
        )
        self.assertTrue(
            (ROOT / "app/life-service/runtime314/life_service/.tiangong-generated-source.json").is_file()
        )

    def test_g28_g29_no_dual_path_no_second_runtime(self) -> None:
        import re

        self.assertTrue(verify_cutover_phase("F")["ok"])
        life_text = (ROOT / "src/life_service/store.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            len(re.findall(r"class LifeShadowStore\b", life_text)), 1
        )
        world_text = (
            ROOT / "src/world_understanding/world_state/store.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            len(re.findall(r"class WorldStateStore\b", world_text)), 1
        )

    def test_g30_closeout_artifacts_exist(self) -> None:
        for name in (
            "p15-m0-baseline.md",
            "p15-m2-m3-closeout.md",
            "p15-m4-m5-m6-closeout.md",
            "p15-m7-closeout.md",
        ):
            self.assertTrue((ROOT / "docs" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
