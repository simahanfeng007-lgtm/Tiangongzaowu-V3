from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from contracts import (
    ActionCandidate,
    AutonomyActionUsage,
    AutonomyPolicySnapshot,
    AutonomyUsageSnapshot,
    ViabilityDelta,
    ViabilityObservation,
    canonical_sha256,
)
from life_service import store as life_store_module
from life_service.agency import (
    advance_autonomy_usage,
    compute_action_risk_floor,
    decide_autonomy,
    rank_action_candidate,
)
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore, LifeShadowStoreError
from life_service.viability import VIABILITY_DIMENSIONS, collect_viability_state
from tests.life_contract_support import HASH_ZERO, dimension, event, impact, viability_state


EPISODE_ID = "cep_" + "1" * 64


def candidate(
    marker: str = "1",
    *,
    action_id: str = "action_test",
    workspace_id: str = "workspace_test",
    candidate_kind: str = "action",
    confidence: int = 900,
    goal_gain: int = 900,
    information_gain: int = 200,
    requires_user_preference: bool = False,
    required_skill_id: str | None = None,
) -> ActionCandidate:
    return ActionCandidate(
        candidate_id="acd_" + marker * 64,
        life_id="life_contract_test",
        episode_id=EPISODE_ID,
        action_id=action_id,
        args_sha256=canonical_sha256({"action": action_id, "marker": marker}),
        workspace_id=workspace_id,
        candidate_kind=candidate_kind,
        objective="维持任务与生命稳态。",
        expected_outcome="产生可验证的改进。",
        goal_gain_milli=goal_gain,
        information_gain_milli=information_gain,
        relationship_value_milli=100,
        benefit_confidence_milli=confidence,
        requires_user_preference=requires_user_preference,
        required_skill_id=required_skill_id,
        evidence_refs=("evidence_" + marker,),
        causal_hypothesis_ids=(),
        proposed_at_ms=1_000,
        expires_at_ms=100_000,
        candidate_sha256=HASH_ZERO,
    ).with_computed_candidate_sha256()


def policy(**overrides) -> AutonomyPolicySnapshot:
    values = {
        "policy_id": "aup_" + "2" * 64,
        "life_id": "life_contract_test",
        "revision": 1,
        "supersedes_policy_sha256": None,
        "autonomy_level": "L4",
        "user_paused": False,
        "shutdown_requested": False,
        "privacy_lockdown": False,
        "allowed_action_ids": ("action_test",),
        "allowed_workspace_ids": ("workspace_test",),
        "active_window_start_minute_utc": 0,
        "active_window_end_minute_utc": 0,
        "daily_execution_budget": 10,
        "daily_resource_budget_milli": 10_000,
        "per_action_daily_limit": 5,
        "minimum_interval_ms": 0,
        "risk_ceiling": "A3",
        "allow_minimal_probes": True,
        "minimum_execute_confidence_milli": 300,
        "effective_at_ms": 0,
        "expires_at_ms": 100_000,
        "policy_sha256": HASH_ZERO,
    }
    values.update(overrides)
    return AutonomyPolicySnapshot(**values).with_computed_policy_sha256()


def usage(active_policy: AutonomyPolicySnapshot, **overrides) -> AutonomyUsageSnapshot:
    values = {
        "life_id": active_policy.life_id,
        "policy_snapshot_hash": active_policy.policy_sha256,
        "revision": 1,
        "supersedes_usage_sha256": None,
        "day_start_ms": 0,
        "day_end_ms": 86_400_000,
        "execution_count": 0,
        "resource_cost_milli": 0,
        "action_usage": (),
        "source_decision_hashes": (),
        "created_at_ms": 1_500,
        "usage_sha256": HASH_ZERO,
    }
    values.update(overrides)
    return AutonomyUsageSnapshot(**values).with_computed_usage_sha256()


def observation(index: int, name: str, *, stale: bool = False) -> ViabilityObservation:
    return ViabilityObservation(
        observation_id="vob_" + f"{index + 1:064x}",
        life_id="life_contract_test",
        dimension=name,
        value_milli=800,
        declared_confidence_milli=1000,
        source_event_id="lev_" + "1" * 64,
        evidence_class="model_inference" if index == 0 else "execution_verified",
        source_kind="system_health",
        source_component_id="viability_probe",
        measured_at_ms=1_000,
        stale_after_ms=1_500 if stale else 5_000,
        observation_sha256=HASH_ZERO,
    ).with_computed_observation_sha256()


class CausalAutonomyP7Tests(unittest.TestCase):
    def test_model_candidate_cannot_supply_risk_or_authority(self) -> None:
        payload = candidate().model_dump(mode="json")
        payload["risk"] = "A0"
        with self.assertRaises(ValidationError):
            ActionCandidate(**payload)
        payload = candidate().model_dump(mode="json")
        payload["confirmed"] = True
        with self.assertRaises(ValidationError):
            ActionCandidate(**payload)

    def test_viability_collection_caps_source_confidence_and_is_deterministic(self) -> None:
        observations = [
            observation(index, name, stale=(name == "security_margin"))
            for index, name in enumerate(VIABILITY_DIMENSIONS)
        ]
        bands = {name: (700, 900) for name in VIABILITY_DIMENSIONS}
        first = collect_viability_state(
            observations, target_bands=bands, revision=1, now_ms=2_000
        )
        second = collect_viability_state(
            reversed(observations), target_bands=bands, revision=1, now_ms=2_000
        )
        self.assertEqual(first, second)
        self.assertEqual(first.effective_source_confidences[0][1], 400)
        self.assertEqual(first.state.runtime_availability.confidence_milli, 400)
        self.assertEqual(first.state.security_margin.confidence_milli, 0)
        self.assertEqual(first.stale_dimensions, ("security_margin",))

    def test_same_actions_change_explainable_order_with_steady_state(self) -> None:
        runtime_candidate = candidate("1", action_id="action_runtime")
        context_candidate = candidate("2", action_id="action_context")
        runtime_impact = impact(
            impact_id="impact_runtime",
            action_id="action_runtime",
            predicted_viability_deltas=(
                ViabilityDelta(
                    dimension="runtime_availability",
                    delta_milli=500,
                    confidence_milli=900,
                    causal_hypothesis_ids=(),
                ),
            ),
        )
        context_impact = impact(
            impact_id="impact_context",
            action_id="action_context",
            predicted_viability_deltas=(
                ViabilityDelta(
                    dimension="context_continuity",
                    delta_milli=500,
                    confidence_milli=900,
                    causal_hypothesis_ids=(),
                ),
            ),
        )
        runtime_deficit = viability_state(
            runtime_availability=dimension(value=0, low=900, high=1000)
        )
        context_deficit = viability_state(
            context_continuity=dimension(value=0, low=900, high=1000)
        )
        self.assertGreater(
            rank_action_candidate(runtime_candidate, runtime_impact, runtime_deficit).score.utility_lcb_milli,
            rank_action_candidate(context_candidate, context_impact, runtime_deficit).score.utility_lcb_milli,
        )
        self.assertGreater(
            rank_action_candidate(context_candidate, context_impact, context_deficit).score.utility_lcb_milli,
            rank_action_candidate(runtime_candidate, runtime_impact, context_deficit).score.utility_lcb_milli,
        )

    def test_risk_is_monotonic_and_l5_never_executes(self) -> None:
        floors = [
            compute_action_risk_floor(impact(impact_id=f"impact_{value}", privacy_scope_milli=value))
            for value in (0, 200, 400, 600, 800, 1000)
        ]
        order = {name: index for index, name in enumerate(("A0", "A1", "A2", "A3", "A4", "A5"))}
        self.assertEqual([order[item] for item in floors], sorted(order[item] for item in floors))
        active = policy(autonomy_level="L5")
        decision = decide_autonomy(
            (candidate(),),
            impacts_by_action={"action_test": impact()},
            viability=viability_state(),
            policy=active,
            usage=usage(active),
            now_ms=2_000,
        )
        self.assertEqual(decision.outcome, "ask_user")

    def test_state_machine_obeys_shutdown_pause_scope_budget_and_frequency(self) -> None:
        cases = (
            ({"shutdown_requested": True}, "reject", "agency.user_shutdown"),
            ({"privacy_lockdown": True}, "reject", "agency.privacy_lockdown"),
            ({"user_paused": True}, "wait", "agency.user_paused"),
            ({"allowed_action_ids": ()}, "reject", "agency.action_out_of_scope"),
        )
        for overrides, outcome, reason in cases:
            with self.subTest(reason=reason):
                active = policy(**overrides)
                decision = decide_autonomy(
                    (candidate(),),
                    impacts_by_action={"action_test": impact()},
                    viability=viability_state(),
                    policy=active,
                    usage=usage(active),
                    now_ms=2_000,
                )
                self.assertEqual(decision.outcome, outcome)
                self.assertIn(reason, decision.reason_codes)

        active = policy(daily_execution_budget=1, per_action_daily_limit=1)
        consumed = usage(
            active,
            execution_count=1,
            action_usage=(
                AutonomyActionUsage(
                    action_id="action_test", execution_count=1, last_executed_at_ms=1_900
                ),
            ),
            source_decision_hashes=("a" * 64,),
        )
        decision = decide_autonomy(
            (candidate(),),
            impacts_by_action={"action_test": impact()},
            viability=viability_state(),
            policy=active,
            usage=consumed,
            now_ms=2_000,
        )
        self.assertEqual(decision.outcome, "wait")
        self.assertIn("agency.daily_execution_budget_exhausted", decision.reason_codes)

    def test_low_confidence_degrades_to_observation_or_minimal_probe(self) -> None:
        active = policy(minimum_execute_confidence_milli=700)
        low = candidate(confidence=200)
        observed = decide_autonomy(
            (low,),
            impacts_by_action={"action_test": impact()},
            viability=viability_state(),
            policy=active,
            usage=usage(active),
            now_ms=2_000,
        )
        self.assertEqual(observed.outcome, "observe")
        probe = candidate(confidence=200, candidate_kind="minimal_probe")
        experimented = decide_autonomy(
            (probe,),
            impacts_by_action={"action_test": impact()},
            viability=viability_state(),
            policy=active,
            usage=usage(active),
            now_ms=2_000,
        )
        self.assertEqual(experimented.outcome, "execute")
        self.assertIn("agency.low_confidence_minimal_probe", experimented.reason_codes)

    def test_time_resource_frequency_and_skill_controls_are_effective(self) -> None:
        scenarios = []
        timed = policy(active_window_start_minute_utc=1, active_window_end_minute_utc=2)
        scenarios.append((timed, usage(timed), candidate(), impact(), "agency.outside_time_window"))
        resource = policy(daily_resource_budget_milli=50)
        scenarios.append((resource, usage(resource), candidate(), impact(), "agency.daily_resource_budget_exhausted"))
        frequency = policy(per_action_daily_limit=1)
        frequency_usage = usage(
            frequency,
            execution_count=1,
            action_usage=(AutonomyActionUsage(action_id="action_test", execution_count=1, last_executed_at_ms=1_000),),
            source_decision_hashes=("a" * 64,),
        )
        scenarios.append((frequency, frequency_usage, candidate(), impact(), "agency.action_frequency_exhausted"))
        interval = policy(minimum_interval_ms=500)
        interval_usage = usage(
            interval,
            execution_count=1,
            action_usage=(AutonomyActionUsage(action_id="action_test", execution_count=1, last_executed_at_ms=1_900),),
            source_decision_hashes=("b" * 64,),
        )
        scenarios.append((interval, interval_usage, candidate(), impact(), "agency.minimum_interval_active"))
        for active, consumed, proposed, action_impact, reason in scenarios:
            with self.subTest(reason=reason):
                decision = decide_autonomy(
                    (proposed,),
                    impacts_by_action={"action_test": action_impact},
                    viability=viability_state(),
                    policy=active,
                    usage=consumed,
                    now_ms=2_000,
                )
                self.assertNotEqual(decision.outcome, "execute")
                self.assertIn(reason, decision.reason_codes)

        active = policy()
        skill_candidate = candidate(required_skill_id="skill_test")
        missing = decide_autonomy(
            (skill_candidate,),
            impacts_by_action={"action_test": impact()},
            viability=viability_state(),
            policy=active,
            usage=usage(active),
            now_ms=2_000,
        )
        self.assertEqual(missing.outcome, "wait")
        activated = decide_autonomy(
            (skill_candidate,),
            impacts_by_action={"action_test": impact()},
            viability=viability_state(),
            policy=active,
            usage=usage(active),
            now_ms=2_000,
            skill_activation_ref="activation_test",
        )
        self.assertEqual(activated.outcome, "execute")

    def test_store_persists_p7_facts_and_migrates_v4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "autonomy.shadow.sqlite3"
            active = policy()
            with LifeShadowStore.open(path, create=True, now_ms=1_000) as store:
                source = event(1, None, suffix="1" * 64)
                store.append_event(source)
                item = observation(0, "runtime_availability")
                self.assertTrue(store.put_viability_observation(item))
                self.assertTrue(store.put_action_candidate(candidate()))
                self.assertTrue(store.put_autonomy_policy(active))
                self.assertTrue(store.put_autonomy_usage(usage(active)))
                self.assertEqual(store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)

            old_path = root / "migration.shadow.sqlite3"
            connection = sqlite3.connect(old_path)
            connection.executescript(life_store_module._P4_SCHEMA_SQL)  # noqa: SLF001
            migrations = (
                (1, "p1-initial-shadow-schema", life_store_module._P1_SCHEMA_SHA256),
                (2, life_store_module._P2_INGRESS_MIGRATION_ID, life_store_module._P2_INGRESS_SHA256),
                (3, life_store_module._P3_CAUSAL_MEMORY_MIGRATION_ID, life_store_module._P3_CAUSAL_MEMORY_SHA256),
                (4, life_store_module._P4_AFFECT_MIGRATION_ID, life_store_module._P4_AFFECT_SHA256),
            )
            connection.executemany(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, 1)", migrations
            )
            connection.executemany(
                "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
                (("purpose", "life-shadow-only"), ("schema_sha256", life_store_module._P4_SCHEMA_SHA256)),
            )
            connection.execute(
                f"PRAGMA application_id={life_store_module.SHADOW_STORE_APPLICATION_ID}"
            )
            connection.execute("PRAGMA user_version=4")
            connection.commit()
            connection.close()
            with LifeShadowStore.open(old_path, create=False, now_ms=2_000) as migrated:
                self.assertEqual(migrated.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE action_candidates SET payload = ?", (b"{}",)
            )
            connection.commit()
            connection.close()
            with self.assertRaises(LifeShadowStoreError):
                LifeShadowStore.open(path, create=False, now_ms=3_000)

    def test_shared_budget_compare_and_swap_allows_only_one_agent(self) -> None:
        active = policy()
        initial = usage(active)
        action_impact = impact()
        first_candidate = candidate("1")
        second_candidate = candidate("2")
        first_decision = decide_autonomy(
            (first_candidate,),
            impacts_by_action={"action_test": action_impact},
            viability=viability_state(),
            policy=active,
            usage=initial,
            now_ms=2_000,
        )
        second_decision = decide_autonomy(
            (second_candidate,),
            impacts_by_action={"action_test": action_impact},
            viability=viability_state(),
            policy=active,
            usage=initial,
            now_ms=2_001,
        )
        self.assertEqual(first_decision.outcome, "execute")
        self.assertEqual(second_decision.outcome, "execute")
        first_next = advance_autonomy_usage(
            initial,
            policy=active,
            decision=first_decision,
            candidate=first_candidate,
            impact=action_impact,
        )
        second_next = advance_autonomy_usage(
            initial,
            policy=active,
            decision=second_decision,
            candidate=second_candidate,
            impact=action_impact,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=1_000) as store:
                store.put_action_impact(action_impact)
                store.put_action_candidate(first_candidate)
                store.put_action_candidate(second_candidate)
                store.put_autonomy_policy(active)
                store.put_autonomy_usage(initial)
                self.assertTrue(
                    store.commit_agency_execution(
                        first_decision,
                        previous_usage=initial,
                        next_usage=first_next,
                    )
                )
                self.assertFalse(
                    store.commit_agency_execution(
                        first_decision,
                        previous_usage=initial,
                        next_usage=first_next,
                    )
                )
                with self.assertRaisesRegex(
                    LifeShadowStoreError, "compare-and-swap"
                ):
                    store.commit_agency_execution(
                        second_decision,
                        previous_usage=initial,
                        next_usage=second_next,
                    )
                self.assertEqual(store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
