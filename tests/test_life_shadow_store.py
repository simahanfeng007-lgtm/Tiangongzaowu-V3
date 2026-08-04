from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import (
    AgencyDecision,
    CapabilityProfile,
    CausalEpisode,
    CausalHypothesis,
    ReflectionCard,
)
from life_service.agency import compute_agency_score
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore, LifeShadowStoreError
from tests.life_contract_support import HASH_ZERO, event, impact, open_episode, viability_state
from tests.test_affect_appraisal_v3 import appraisal
from tests.test_continuity_capsule import capsule


class LifeShadowStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "life-test.shadow.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_schema_is_strict_shadow_only_and_survives_reopen(self) -> None:
        with LifeShadowStore.open(self.path, create=True, now_ms=1_000) as store:
            health = store.health()
            self.assertEqual(health["purpose"], "life-shadow-only")
            self.assertEqual(health["schema_version"], SHADOW_STORE_SCHEMA_VERSION)
            self.assertGreaterEqual(health["strict_table_count"], 49)
            first = event(1, None)
            second = event(2, first.event_hash, writer_epoch=2)
            self.assertTrue(store.append_event(first))
            self.assertTrue(store.append_event(second))
            self.assertFalse(store.append_event(second))
            summary = store.replay(first.life_id)
            self.assertEqual(summary.event_count, 2)
            self.assertEqual(summary.writer_epoch, 2)
        with LifeShadowStore.open(self.path, create=False, now_ms=2_000) as reopened:
            self.assertEqual(reopened.health()["event_count"], 2)
            self.assertEqual(reopened.replay(first.life_id), summary)

    def test_path_random_database_and_schema_drift_fail_closed(self) -> None:
        with self.assertRaisesRegex(LifeShadowStoreError, "must end"):
            LifeShadowStore.open(
                self.root / "production.sqlite3",
                create=True,
                now_ms=1_000,
            )
        random = self.root / "random.shadow.sqlite3"
        connection = sqlite3.connect(random)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaises(LifeShadowStoreError):
            LifeShadowStore.open(random, create=False, now_ms=1_000)

        with LifeShadowStore.open(self.path, create=True, now_ms=1_000):
            pass
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE schema_migrations SET sql_sha256 = ?",
            ("0" * 64,),
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(LifeShadowStoreError, "migration"):
            LifeShadowStore.open(self.path, create=False, now_ms=2_000)

    def test_chain_gap_rebinding_and_payload_tamper_are_detected(self) -> None:
        first = event(1, None)
        with LifeShadowStore.open(self.path, create=True, now_ms=1_000) as store:
            store.append_event(first)
            with self.assertRaisesRegex(LifeShadowStoreError, "discontinuous"):
                store.append_event(event(3, first.event_hash))
            with self.assertRaisesRegex(LifeShadowStoreError, "rebound"):
                store.append_event(
                    first.model_copy(
                        update={
                            "content_sha256": "f" * 64,
                            "event_hash": "0" * 64,
                        }
                    ).with_computed_event_hash()
                )
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE life_events SET envelope = ? WHERE event_id = ?",
            (b"{}", first.event_id),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(LifeShadowStoreError):
            LifeShadowStore.open(self.path, create=False, now_ms=2_000)

    def test_two_connections_converge_on_one_idempotent_append(self) -> None:
        with LifeShadowStore.open(self.path, create=True, now_ms=1_000):
            pass
        first = event(1, None)

        def append_once() -> bool:
            with LifeShadowStore.open(self.path, create=False, now_ms=2_000) as store:
                return store.append_event(first)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: append_once(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        with LifeShadowStore.open(self.path, create=False, now_ms=3_000) as store:
            self.assertEqual(store.health()["event_count"], 1)

    def test_projection_contracts_are_immutable_versioned_and_capsules_supersede(self) -> None:
        with LifeShadowStore.open(self.path, create=True, now_ms=1_000) as store:
            state = viability_state()
            self.assertTrue(store.put_viability_state(state))
            self.assertFalse(store.put_viability_state(state))

            action_impact = impact()
            self.assertTrue(store.put_action_impact(action_impact))
            self.assertFalse(store.put_action_impact(action_impact))

            opened = open_episode()
            self.assertTrue(store.put_causal_episode(opened))
            closed = CausalEpisode(
                **{
                    **opened.model_dump(mode="python"),
                    "revision": 2,
                    "supersedes_episode_sha256": opened.episode_sha256,
                    "outcome_event_ids": ("lev_" + "2" * 64,),
                    "outcome_evaluation": "结果已经验证。",
                    "prediction_error_milli": 100,
                    "terminal_status": "CLOSED",
                    "closed_at_ms": 3_000,
                    "episode_sha256": "0" * 64,
                }
            ).with_computed_episode_sha256()
            self.assertTrue(store.put_causal_episode(closed))
            self.assertFalse(store.put_causal_episode(closed))

            checkpoint = capsule().with_computed_capsule_sha256()
            self.assertTrue(store.put_context_capsule(checkpoint))
            terminal = capsule(
                capsule_id="lcp_" + "9" * 64,
                capsule_kind="TERMINAL_RESULT",
                pending_effect_ids=(),
                latest_safe_step=None,
                next_step=None,
                recovery_preconditions=(),
                continuation_token_sha256=None,
                final_result="最终结果。",
                supersedes_capsule_id=checkpoint.capsule_id,
                retention_class="TERMINAL_RESULT",
            ).with_computed_capsule_sha256()
            self.assertTrue(store.put_context_capsule(terminal))

    def test_canonical_roundtrip_rejects_semantically_invalid_model_copy(self) -> None:
        invalid = impact().model_copy(
            update={
                "irreversibility_milli": 900,
                "impact_sha256": "0" * 64,
            }
        ).with_computed_impact_sha256()
        with LifeShadowStore.open(self.path, create=True, now_ms=1_000) as store:
            with self.assertRaisesRegex(LifeShadowStoreError, "contract"):
                store.put_action_impact(invalid)

    def test_all_p1_projection_writers_are_digest_bound_and_idempotent(self) -> None:
        causal = CausalHypothesis(
            hypothesis_id="chy_" + "1" * 64,
            life_id="life_contract_test",
            cause_ref="cause_test",
            effect_ref="effect_test",
            relation="correlated_with",
            causal_basis="correlation",
            mechanism_summary="",
            confidence_milli=600,
            evidence_class="observed",
            supporting_event_ids=("lev_" + "1" * 64,),
            counterevidence_event_ids=(),
            alternative_hypothesis_ids=(),
            confounder_refs=(),
            intervention_status="none",
            valid_from_ms=2_000,
            valid_until_ms=None,
            supersedes_id=None,
            status="supported",
            revision=1,
            hypothesis_sha256=HASH_ZERO,
        ).with_computed_hypothesis_sha256()
        score = compute_agency_score(
            goal_gain_milli=500,
            viability_gain_milli=100,
            information_gain_milli=100,
            relationship_value_milli=100,
            resource_cost_milli=100,
            expected_harm_milli=100,
            uncertainty_penalty_milli=100,
            irreversibility_penalty_milli=100,
        )
        decision = AgencyDecision(
            decision_id="agd_" + "2" * 64,
            life_id="life_contract_test",
            episode_id="cep_" + "1" * 64,
            candidate_set_sha256="3" * 64,
            selected_candidate_id="action_test",
            action_impact_sha256="4" * 64,
            score_breakdown=score,
            computed_risk="A3",
            policy_ceiling="A4",
            required_confirmation=False,
            confirmation_grant_ref=None,
            required_skill_activation=False,
            skill_activation_ref=None,
            outcome="wait",
            reason_codes=("agency.shadow_only",),
            state_revision_hashes=("5" * 64,),
            policy_snapshot_hash="6" * 64,
            created_at_ms=2_000,
            decision_sha256=HASH_ZERO,
        ).with_computed_decision_sha256()
        reflection = ReflectionCard(
            reflection_id="rfc_" + "7" * 64,
            life_id="life_contract_test",
            episode_id="cep_" + "1" * 64,
            expected_outcome="成功。",
            observed_outcome="成功。",
            prediction_error_milli=0,
            success_dimensions=("goal",),
            failure_dimensions=(),
            candidate_cause_ids=("chy_" + "1" * 64,),
            counterevidence_refs=(),
            alternative_explanations=(),
            counterfactual_actions=(),
            next_minimal_experiment=None,
            lessons=("仍需更多独立样本。",),
            memory_candidate_refs=(),
            capability_evidence_refs=("evidence_test",),
            user_question=None,
            user_question_value_of_information_milli=0,
            confidence_milli=600,
            reviewer="model_assisted",
            created_at_ms=2_100,
            reflection_sha256=HASH_ZERO,
        ).with_computed_reflection_sha256()
        profile = CapabilityProfile(
            capability_id="capability_test",
            life_id="life_contract_test",
            version="v1",
            profile_revision=1,
            supersedes_profile_sha256=None,
            scope="测试场景。",
            verified_successes=1,
            verified_failures=0,
            independent_context_count=1,
            calibration_error_milli=400,
            rollback_count=0,
            last_regression_at_ms=None,
            proficiency_mean_milli=500,
            proficiency_lower_bound_milli=100,
            evidence_refs=("evidence_test",),
            impact_floor="A1",
            review_level="OBSERVE",
            updated_at_ms=2_200,
            profile_sha256=HASH_ZERO,
        ).with_computed_profile_sha256()
        appraisal_value = appraisal().with_computed_appraisal_sha256()

        with LifeShadowStore.open(self.path, create=True, now_ms=1_000) as store:
            for writer, value in (
                (store.put_appraisal, appraisal_value),
                (store.put_causal_hypothesis, causal),
                (store.put_agency_decision, decision),
                (store.put_reflection_card, reflection),
                (store.put_capability_profile, profile),
            ):
                self.assertTrue(writer(value))
                self.assertFalse(writer(value))


if __name__ == "__main__":
    unittest.main()
