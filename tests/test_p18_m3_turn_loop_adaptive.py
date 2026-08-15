"""P18-M3 integration regressions for the existing TurnLoopState."""
from __future__ import annotations

import unittest

from v3.runtime_adaptive_control import HorizonControlMetrics
from v3.runtime_turn_orchestration import TurnLoopState


class P18M3TurnLoopAdaptiveTests(unittest.TestCase):
    def test_existing_turn_loop_owns_adaptive_state_without_new_runtime(self) -> None:
        state = TurnLoopState()
        self.assertEqual(state.current_epoch_round_limit(75), 48)
        state.adaptive_horizon.current_epoch_steps = 64
        self.assertEqual(state.current_epoch_round_limit(75), 64)
        self.assertEqual(state.current_epoch_round_limit(50), 50)

    def test_adaptive_schedule_preserves_global_terminal_budget(self) -> None:
        state = TurnLoopState(action_rounds=999, epoch_action_rounds=47)
        self.assertTrue(
            state.decide_adaptive_schedule(
                1,
                configured_max_epoch_rounds=75,
                max_global_rounds=1000,
            ).can_schedule
        )
        state.reserve_one()
        decision = state.decide_adaptive_schedule(
            1,
            configured_max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertTrue(decision.terminal)
        self.assertTrue(decision.global_exhausted)

    def test_adaptive_local_horizon_remains_nonterminal_checkpoint_continue(self) -> None:
        state = TurnLoopState(epoch_action_rounds=48)
        decision = state.decide_adaptive_schedule(
            1,
            configured_max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertTrue(decision.should_checkpoint_continue)
        self.assertFalse(decision.terminal)

    def test_epoch_metrics_change_only_local_horizon_not_run_identity_counters(self) -> None:
        state = TurnLoopState(
            action_rounds=120,
            iteration_count=220,
            epoch_index=4,
            epoch_action_rounds=20,
            epoch_iteration_count=30,
        )
        before = (
            state.action_rounds,
            state.iteration_count,
            state.epoch_index,
            state.epoch_action_rounds,
            state.epoch_iteration_count,
        )
        high = HorizonControlMetrics(
            mtbf_model_steps=2,
            mtbf_tool_steps=2,
            provider_timeout_rate=1.0,
            tool_failure_rate=1.0,
            context_pressure=1.0,
            semantic_drift_score=1.0,
            repeat_risk=1.0,
            ambiguous_effect_rate=1.0,
            progress_velocity=0.0,
        )
        state.observe_epoch_metrics(high)
        state.observe_epoch_metrics(high)
        after = (
            state.action_rounds,
            state.iteration_count,
            state.epoch_index,
            state.epoch_action_rounds,
            state.epoch_iteration_count,
        )
        self.assertEqual(before, after)
        self.assertLess(state.adaptive_horizon.current_epoch_steps, 48)

    def test_live_projection_exposes_adaptive_observability(self) -> None:
        state = TurnLoopState()
        state.observe_epoch_metrics(HorizonControlMetrics(context_pressure=0.95))
        run_state: dict[str, object] = {}
        state.project_live(run_state, 5.0)
        live = run_state["_live"]
        self.assertEqual(live["adaptive_epoch_tool_limit"], state.adaptive_horizon.current_epoch_steps)
        self.assertEqual(live["adaptive_ewma_risk"], round(state.adaptive_horizon.ewma_risk, 6))


if __name__ == "__main__":
    unittest.main()
