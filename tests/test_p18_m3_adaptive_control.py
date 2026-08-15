"""P18-M3 adaptive horizon/progress/resource policy regressions."""
from __future__ import annotations

import unittest
from pathlib import Path

from v3.runtime_adaptive_control import (
    AdaptiveHorizonState,
    ExecutionPotential,
    FrontierProgressMonitor,
    FrontierProgressSample,
    HorizonControlMetrics,
    ResourceBudget,
    ResourceUsage,
    evaluate_resource_governor,
)


class P18M3AdaptiveControlTests(unittest.TestCase):
    def test_stable_readonly_work_grows_horizon_only_after_hysteresis(self) -> None:
        state = AdaptiveHorizonState(min_dwell_epochs=0)
        metrics = HorizonControlMetrics(
            mtbf_model_steps=500,
            mtbf_tool_steps=500,
            readonly_fraction=1.0,
            progress_velocity=1.0,
            cache_hit_rate=1.0,
            checkpoint_cost=4.0,
            recovery_cost=8.0,
        )
        self.assertEqual(state.observe_epoch(metrics).action, "hold")
        self.assertEqual(state.observe_epoch(metrics).action, "hold")
        third = state.observe_epoch(metrics)
        self.assertEqual(third.action, "grow")
        self.assertGreater(third.epoch_steps, 48)
        self.assertLessEqual(third.epoch_steps, 96)

    def test_high_failure_work_shrinks_only_after_sustained_risk(self) -> None:
        state = AdaptiveHorizonState(min_dwell_epochs=0)
        metrics = HorizonControlMetrics(
            mtbf_model_steps=2,
            mtbf_tool_steps=2,
            provider_timeout_rate=1.0,
            tool_failure_rate=1.0,
            context_pressure=1.0,
            semantic_drift_score=1.0,
            repeat_risk=1.0,
            ambiguous_effect_rate=1.0,
            frontier_complexity=1.0,
            progress_velocity=0.0,
            cache_hit_rate=0.0,
        )
        self.assertEqual(state.observe_epoch(metrics).action, "hold")
        second = state.observe_epoch(metrics)
        self.assertEqual(second.action, "shrink")
        self.assertLess(second.epoch_steps, 48)
        self.assertGreater(second.epoch_steps, 16)

    def test_jittering_risk_does_not_bounce_horizon(self) -> None:
        state = AdaptiveHorizonState(min_dwell_epochs=2)
        low = HorizonControlMetrics(
            mtbf_model_steps=500,
            mtbf_tool_steps=500,
            readonly_fraction=1.0,
        )
        high = HorizonControlMetrics(
            mtbf_model_steps=2,
            mtbf_tool_steps=2,
            provider_timeout_rate=1.0,
            tool_failure_rate=1.0,
            context_pressure=0.9,
            semantic_drift_score=0.9,
            repeat_risk=1.0,
            ambiguous_effect_rate=0.8,
            progress_velocity=0.0,
        )
        values = [
            state.observe_epoch(metrics).epoch_steps
            for metrics in (high, low, high, low, high, low, high, low)
        ]
        changes = sum(1 for left, right in zip(values, values[1:]) if left != right)
        self.assertLessEqual(changes, 1)
        self.assertFalse(any(left < right for left, right in zip(values, values[1:])))

    def test_context_pressure_requests_early_regeneration(self) -> None:
        state = AdaptiveHorizonState()
        metrics = HorizonControlMetrics(context_pressure=0.95)
        decision = state.observe_epoch(metrics)
        self.assertTrue(state.should_regenerate_early(metrics))
        self.assertIn("context_pressure_regeneration", decision.reasons)

    def test_semantic_drift_requests_audit_without_expansion(self) -> None:
        state = AdaptiveHorizonState(min_dwell_epochs=0)
        metrics = HorizonControlMetrics(semantic_drift_score=0.95, readonly_fraction=1.0)
        prior = state.current_epoch_steps
        decision = state.observe_epoch(metrics)
        self.assertTrue(state.should_regenerate_early(metrics))
        self.assertIn("semantic_drift_audit", decision.reasons)
        self.assertLessEqual(decision.epoch_steps, prior)

    def test_frontier_progress_prevents_test_edit_test_false_stuck(self) -> None:
        monitor = FrontierProgressMonitor(exhaustion_epochs=3)
        samples = (
            FrontierProgressSample(0, "rev-a", "art-1", failure_signature="fail-A", strategy_id="fix"),
            FrontierProgressSample(0, "rev-b", "art-2", failure_signature="fail-A", strategy_id="fix"),
            FrontierProgressSample(0, "rev-c", "art-3", failure_signature="fail-B", strategy_id="fix"),
            FrontierProgressSample(1, "rev-d", "art-4", failure_signature="", strategy_id="fix"),
        )
        decisions = [monitor.observe(sample) for sample in samples]
        self.assertTrue(all(not decision.strategy_exhausted for decision in decisions))
        self.assertTrue(all(not decision.fatal_exhaustion for decision in decisions))

    def test_unchanged_frontier_strategy_failure_exhausts_strategy(self) -> None:
        monitor = FrontierProgressMonitor(exhaustion_epochs=3)
        sample = FrontierProgressSample(
            0,
            "rev-a",
            "art-1",
            failure_signature="same-failure",
            strategy_id="s1",
        )
        monitor.observe(sample)
        self.assertFalse(monitor.observe(sample).strategy_exhausted)
        self.assertFalse(monitor.observe(sample).strategy_exhausted)
        exhausted = monitor.observe(sample)
        self.assertTrue(exhausted.strategy_exhausted)
        self.assertFalse(exhausted.fatal_exhaustion)
        self.assertEqual(exhausted.failed_strategy_count, 1)

    def test_multiple_distinct_strategy_exhaustions_required_before_fatal(self) -> None:
        monitor = FrontierProgressMonitor(exhaustion_epochs=1, fatal_strategy_count=3)
        for index, strategy in enumerate(("s1", "s2", "s3"), start=1):
            sample = FrontierProgressSample(
                active_obligation_revision="same",
                artifact_revision_head="same",
                failure_signature="same",
                strategy_id=strategy,
            )
            monitor.observe(sample)
            decision = monitor.observe(sample)
            self.assertTrue(decision.strategy_exhausted)
            self.assertEqual(decision.fatal_exhaustion, index >= 3)

    def test_resource_governor_blocks_hard_budget_exhaustion(self) -> None:
        decision = evaluate_resource_governor(
            usage=ResourceUsage(tokens=101),
            budget=ResourceBudget(token_budget=100),
            progress_delta=1.0,
            regeneration_streak=0,
        )
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.runaway_guard)
        self.assertIn("token_budget", decision.exhausted_dimensions)

    def test_resource_governor_blocks_regenerative_livelock(self) -> None:
        decision = evaluate_resource_governor(
            usage=ResourceUsage(tokens=50, api_cost=1, regenerations=5),
            budget=ResourceBudget(
                token_budget=1000,
                api_cost_budget=100,
                regeneration_budget=20,
            ),
            progress_delta=0.0,
            regeneration_streak=5,
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.runaway_guard)
        self.assertEqual(decision.exhausted_dimensions, ())

    def test_execution_potential_declines_when_frontier_improves(self) -> None:
        before = ExecutionPotential(5, 2, 1, 0.8, 0.7, 2).value()
        after = ExecutionPotential(3, 1, 0, 0.4, 0.2, 1).value()
        self.assertLess(after, before)

    def test_controller_is_pure_policy_not_authority_or_persistence(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "backend"
            / "tiangong-backend"
            / "v3"
            / "runtime_adaptive_control.py"
        ).read_text(encoding="utf-8")
        for token in (
            "GatewayStateStore",
            "sqlite3",
            "subprocess",
            "_jineng_zhixing",
            "execution_ticket_id",
        ):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
