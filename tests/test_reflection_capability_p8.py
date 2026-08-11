from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import CausalEpisode, EpisodeOutcomeEvidence
from life_service import store as life_store_module
from life_service.capability_learning import (
    build_capability_evidence,
    learn_capability,
    rollback_capability,
)
from life_service.reflection import QUESTION_COOLDOWN_MS, close_episode_and_reflect
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore
from life_service.store import LifeShadowStoreError
from tests.life_contract_support import HASH_ZERO, event, impact, open_episode


def episode(marker: str = "1", *, trigger: str = "1") -> CausalEpisode:
    base = open_episode()
    return CausalEpisode(
        **{
            **base.model_dump(mode="python"),
            "episode_id": "cep_" + marker * 64,
            "trigger_event_ids": ("lev_" + trigger * 64,),
            "episode_sha256": HASH_ZERO,
        }
    ).with_computed_episode_sha256()


def outcome(
    marker: str = "2",
    *,
    episode_marker: str = "1",
    status: str = "success",
    supported: bool = True,
    preference_uncertainty: int = 0,
    risk: str = "A1",
) -> EpisodeOutcomeEvidence:
    failed = status != "success"
    return EpisodeOutcomeEvidence(
        outcome_evidence_id="oev_" + marker * 64,
        life_id="life_contract_test",
        episode_id="cep_" + episode_marker * 64,
        outcome_status=status,
        observed_outcome="任务成功。" if not failed else "任务失败。",
        observed_quality_milli=900 if not failed else 100,
        predicted_success_milli=700,
        prediction_snapshot_hash="3" * 64,
        completion_decision_sha256="4" * 64,
        terminal_fact_hashes=("5" * 64,),
        outcome_event_ids=("lev_" + marker * 64,),
        failure_category=None if not failed else "tool_error",
        method_attribution="capability",
        supported_cause_ids=(("chy_" + "6" * 64),) if supported else (),
        counterevidence_refs=(),
        alternative_explanation_refs=() if supported else ("alternative_test",),
        context_fingerprint_sha256=marker * 64,
        preference_domain="workflow_style" if preference_uncertainty else None,
        user_preference_uncertainty_milli=preference_uncertainty,
        action_risk=risk,
        counterfactual_actions=("先执行只读探针。",) if failed else (),
        next_minimal_experiment="执行只读探针。" if failed else None,
        candidate_user_question=(
            "你更希望我下次先询问还是先做只读探针？"
            if preference_uncertainty
            else None
        ),
        occurred_at_ms=2_000,
        evidence_sha256=HASH_ZERO,
    ).with_computed_evidence_sha256()


class ReflectionCapabilityP8Tests(unittest.TestCase):
    def test_terminal_episode_closes_with_prediction_error_and_failure_counterfactual(self) -> None:
        result = close_episode_and_reflect(
            episode(), outcome(status="failure"), now_ms=2_100
        )
        self.assertEqual(result.closed_episode.terminal_status, "CLOSED")
        self.assertEqual(result.closed_episode.prediction_error_milli, 700)
        self.assertEqual(result.reflection.failure_dimensions, ("tool_error",))
        self.assertEqual(result.reflection.next_minimal_experiment, "执行只读探针。")
        self.assertTrue(result.reflection.counterfactual_actions)
        self.assertEqual(
            result.reflection.capability_evidence_refs,
            outcome(status="failure").terminal_fact_hashes,
        )

    def test_success_without_supported_cause_is_treated_as_correlation(self) -> None:
        evidence = outcome(supported=False)
        result = close_episode_and_reflect(episode(), evidence, now_ms=2_100)
        self.assertLessEqual(result.reflection.confidence_milli, 400)
        capability = build_capability_evidence(
            evidence,
            result.reflection,
            impact(),
            capability_id="capability_test",
            capability_version="v1",
            now_ms=2_100,
        )
        self.assertEqual(capability.causal_support, "plausible")
        self.assertFalse(capability.eligible_success)

    def test_user_question_requires_high_value_and_obeys_cooldown(self) -> None:
        evidence = outcome(preference_uncertainty=900, risk="A4")
        first = close_episode_and_reflect(episode(), evidence, now_ms=2_100)
        self.assertEqual(first.question_decision.outcome, "ask_user")
        self.assertIsNotNone(first.reflection.user_question)
        second = close_episode_and_reflect(
            episode(),
            evidence,
            now_ms=2_200,
            last_question_at_ms=2_100,
        )
        self.assertEqual(second.question_decision.outcome, "suppress")
        self.assertIsNone(second.reflection.user_question)
        self.assertEqual(
            second.question_decision.cooldown_until_ms,
            2_100 + QUESTION_COOLDOWN_MS,
        )

    def test_single_success_correlation_and_same_context_cannot_publish(self) -> None:
        evidence = outcome()
        reflection = close_episode_and_reflect(episode(), evidence, now_ms=2_100).reflection
        base = build_capability_evidence(
            evidence,
            reflection,
            impact(),
            capability_id="capability_test",
            capability_version="v1",
            now_ms=2_100,
        )
        single = learn_capability((base,), scope="测试能力", now_ms=2_200)
        self.assertEqual(single.decision.outcome, "hold")
        self.assertEqual(single.profile.proficiency_lower_bound_milli, 0)

        same_context = tuple(
            base.model_copy(
                update={
                    "evidence_id": "cpe_" + f"{index + 10:064x}",
                    "episode_id": "cep_" + f"{index + 10:064x}",
                    "reflection_id": "rfc_" + f"{index + 10:064x}",
                    "evidence_sha256": HASH_ZERO,
                }
            ).with_computed_evidence_sha256()
            for index in range(10)
        )
        merged = learn_capability(same_context, scope="测试能力", now_ms=3_000)
        self.assertEqual(merged.decision.outcome, "hold")
        self.assertIn(
            "capability.context_diversity_insufficient",
            merged.decision.reason_codes,
        )

    def test_diverse_samples_reach_sandbox_while_high_impact_needs_review(self) -> None:
        evidence = outcome()
        reflection = close_episode_and_reflect(episode(), evidence, now_ms=2_100).reflection
        base = build_capability_evidence(
            evidence,
            reflection,
            impact(),
            capability_id="capability_test",
            capability_version="v1",
            now_ms=2_100,
        )

        def samples(count: int, risk: str = "A1", core: bool = False):
            return tuple(
                base.model_copy(
                    update={
                        "evidence_id": "cpe_" + f"{index + 100:064x}",
                        "episode_id": "cep_" + f"{index + 100:064x}",
                        "reflection_id": "rfc_" + f"{index + 100:064x}",
                        "context_fingerprint_sha256": f"{index + 100:064x}",
                        "impact_floor": risk,
                        "touches_core_code": core,
                        "evidence_sha256": HASH_ZERO,
                    }
                ).with_computed_evidence_sha256()
                for index in range(count)
            )

        low = learn_capability(samples(10), scope="测试能力", now_ms=3_000)
        self.assertEqual(low.decision.outcome, "sandbox_candidate")
        high = learn_capability(samples(12, risk="A3"), scope="测试能力", now_ms=3_000)
        self.assertEqual(high.decision.outcome, "human_review")
        core = learn_capability(samples(30, risk="A5", core=True), scope="测试能力", now_ms=3_000)
        self.assertEqual(core.decision.outcome, "core_review")

    def test_failure_never_raises_proficiency_and_rollback_resets_profile(self) -> None:
        success_outcome = outcome()
        success_reflection = close_episode_and_reflect(
            episode(), success_outcome, now_ms=2_100
        ).reflection
        success = build_capability_evidence(
            success_outcome,
            success_reflection,
            impact(),
            capability_id="capability_test",
            capability_version="v1",
            now_ms=2_100,
        )
        successes = tuple(
            success.model_copy(
                update={
                    "evidence_id": "cpe_" + f"{index + 200:064x}",
                    "episode_id": "cep_" + f"{index + 200:064x}",
                    "reflection_id": "rfc_" + f"{index + 200:064x}",
                    "context_fingerprint_sha256": f"{index + 200:064x}",
                    "evidence_sha256": HASH_ZERO,
                }
            ).with_computed_evidence_sha256()
            for index in range(10)
        )
        learned = learn_capability(successes, scope="测试能力", now_ms=3_000)
        failure = successes[0].model_copy(
            update={
                "evidence_id": "cpe_" + "f" * 64,
                "outcome": "failure",
                "quality_milli": 0,
                "eligible_success": False,
                "eligible_failure": True,
                "evidence_sha256": HASH_ZERO,
            }
        ).with_computed_evidence_sha256()
        degraded = learn_capability(
            (*successes, failure),
            scope="测试能力",
            now_ms=3_000 + QUESTION_COOLDOWN_MS,
            previous_profile=learned.profile,
            previous_decision=learned.decision,
        )
        self.assertLess(degraded.profile.proficiency_mean_milli, learned.profile.proficiency_mean_milli)
        rolled = rollback_capability(
            degraded.profile,
            (failure,),
            invalidated_context_pack_ids=("pack_test",),
            invalidated_skill_activation_ids=("activation_test",),
            now_ms=4_000 + QUESTION_COOLDOWN_MS,
        )
        self.assertEqual(rolled.profile.proficiency_lower_bound_milli, 0)
        self.assertEqual(rolled.profile.rollback_count, 1)
        self.assertEqual(rolled.record.invalidated_context_pack_ids, ("pack_test",))

    def test_store_atomically_closes_reflects_learns_rolls_back_and_migrates_v5(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "reflection.shadow.sqlite3"
            source = event(1, None, suffix="2" * 64)
            open_value = episode()
            success_outcome = outcome()
            action_impact = impact()
            with LifeShadowStore.open(path, create=True, now_ms=1_000) as store:
                store.append_event(source)
                store.put_causal_episode(open_value)
                store.put_action_impact(action_impact)
                reflected = store.commit_episode_reflection(success_outcome, now_ms=2_100)
                evidence = build_capability_evidence(
                    success_outcome,
                    reflected.reflection,
                    action_impact,
                    capability_id="capability_test",
                    capability_version="v1",
                    now_ms=2_100,
                )
                learned = store.commit_capability_learning(
                    (evidence,), scope="测试能力", now_ms=2_200
                )
                repeated = store.commit_capability_learning(
                    (evidence,), scope="测试能力", now_ms=2_300
                )
                self.assertEqual(repeated, learned)
                failure_event = event(2, source.event_hash, suffix="3" * 64)
                store.append_event(failure_event)
                failed_episode = episode("2", trigger="3")
                failed_outcome = outcome(
                    "3", episode_marker="2", status="failure"
                )
                store.put_causal_episode(failed_episode)
                failed_reflection = store.commit_episode_reflection(
                    failed_outcome, now_ms=2_400
                )
                failed_evidence = build_capability_evidence(
                    failed_outcome,
                    failed_reflection.reflection,
                    action_impact,
                    capability_id="capability_test",
                    capability_version="v1",
                    now_ms=2_400,
                )
                store.commit_capability_learning(
                    (failed_evidence,), scope="测试能力", now_ms=2_500
                )
                with self.assertRaises(LifeShadowStoreError):
                    store.apply_capability_rollback(
                        capability_id="capability_test",
                        capability_version="v1",
                        life_id="life_contract_test",
                        trigger_evidence_ids=(failed_evidence.evidence_id,),
                        invalidated_context_pack_ids=("missing_pack",),
                        now_ms=2_600,
                    )
                rolled = store.apply_capability_rollback(
                    capability_id="capability_test",
                    capability_version="v1",
                    life_id="life_contract_test",
                    trigger_evidence_ids=(failed_evidence.evidence_id,),
                    now_ms=2_600,
                )
                self.assertEqual(rolled.profile.proficiency_lower_bound_milli, 0)
                self.assertEqual(rolled.profile.rollback_count, 1)
                self.assertEqual(store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)

            old_path = root / "migration.shadow.sqlite3"
            connection = sqlite3.connect(old_path)
            connection.executescript(life_store_module._P5_SCHEMA_SQL)  # noqa: SLF001
            migrations = (
                (1, "p1-initial-shadow-schema", life_store_module._P1_SCHEMA_SHA256),
                (2, life_store_module._P2_INGRESS_MIGRATION_ID, life_store_module._P2_INGRESS_SHA256),
                (3, life_store_module._P3_CAUSAL_MEMORY_MIGRATION_ID, life_store_module._P3_CAUSAL_MEMORY_SHA256),
                (4, life_store_module._P4_AFFECT_MIGRATION_ID, life_store_module._P4_AFFECT_SHA256),
                (5, life_store_module._P5_AUTONOMY_MIGRATION_ID, life_store_module._P5_AUTONOMY_SHA256),
            )
            connection.executemany(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, 1)", migrations
            )
            connection.executemany(
                "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
                (("purpose", "life-shadow-only"), ("schema_sha256", life_store_module._P5_SCHEMA_SHA256)),
            )
            connection.execute(
                f"PRAGMA application_id={life_store_module.SHADOW_STORE_APPLICATION_ID}"
            )
            connection.execute("PRAGMA user_version=5")
            connection.commit()
            connection.close()
            with LifeShadowStore.open(old_path, create=False, now_ms=2_000) as migrated:
                self.assertEqual(migrated.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
