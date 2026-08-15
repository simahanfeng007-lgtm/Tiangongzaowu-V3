"""P18-M3 production wiring regressions for the one authoritative TurnLoopState."""
from __future__ import annotations

import unittest

from v3.runtime_adaptive_control import ResourceBudget, ResourceUsage
from v3.runtime_adaptive_governance import SemanticDriftSignals
from v3.runtime_turn_orchestration import (
    EpochBudgetDisposition,
    TurnLoopState,
)


class P18M3ProductionControlWiringTests(unittest.TestCase):
    def test_first_legacy_epoch_preserves_m1_budget_contract(self) -> None:
        state = TurnLoopState()
        for _ in range(75):
            self.assertTrue(
                state.decide_schedule(
                    1,
                    max_epoch_rounds=75,
                    max_global_rounds=1000,
                ).can_schedule
            )
            state.reserve_one()
        self.assertTrue(
            state.decide_schedule(
                1,
                max_epoch_rounds=75,
                max_global_rounds=1000,
            ).should_checkpoint_continue
        )
        self.assertFalse(state.adaptive_execution_active)

    def test_checkpoint_rollover_upgrades_same_scheduler_to_adaptive_horizon(self) -> None:
        state = TurnLoopState(action_rounds=75, epoch_action_rounds=75)
        state.begin_next_epoch()
        self.assertTrue(state.adaptive_execution_active)
        self.assertEqual(state.current_epoch_round_limit(75), 48)
        for _ in range(48):
            self.assertTrue(
                state.decide_schedule(
                    1,
                    max_epoch_rounds=75,
                    max_global_rounds=1000,
                ).can_schedule
            )
            state.reserve_one()
        decision = state.decide_schedule(
            1,
            max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertEqual(decision.disposition, EpochBudgetDisposition.CHECKPOINT_CONTINUE)
        self.assertTrue(decision.epoch_exhausted)

    def test_semantic_drift_forces_checkpoint_before_more_tool_dispatch(self) -> None:
        state = TurnLoopState()
        decision = state.observe_semantic_drift(
            SemanticDriftSignals(
                root_goal_similarity=0.0,
                task_contract_match=False,
                active_obligation_consistency=0.0,
                authority_reference_match=False,
                frontier_contradiction=True,
                semantic_handoff_contradiction=True,
                repeated_strategy_collapse=1.0,
                unverified_claim_accumulation=1.0,
            )
        )
        self.assertTrue(decision.high_risk)
        schedule = state.decide_schedule(
            1,
            max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertTrue(schedule.should_checkpoint_continue)
        self.assertIn("semantic_drift_audit_replan", schedule.reasons[0])

    def test_resource_governor_runaway_blocks_same_scheduler(self) -> None:
        state = TurnLoopState()
        governor = state.observe_resource_governor(
            usage=ResourceUsage(regenerations=4),
            budget=ResourceBudget(regeneration_budget=100),
            progress_delta=0.0,
            regeneration_streak=4,
        )
        self.assertTrue(governor.runaway_guard)
        schedule = state.decide_schedule(
            1,
            max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertEqual(schedule.disposition, EpochBudgetDisposition.CONTROL_BLOCKED)
        self.assertTrue(schedule.terminal)
        self.assertFalse(schedule.global_exhausted)
        self.assertIn("runaway_guard", schedule.reasons[0])

    def test_resource_budget_exhaustion_blocks_dispatch(self) -> None:
        state = TurnLoopState()
        governor = state.observe_resource_governor(
            usage=ResourceUsage(tokens=101),
            budget=ResourceBudget(token_budget=100),
            progress_delta=10.0,
            regeneration_streak=0,
        )
        self.assertFalse(governor.allowed)
        schedule = state.decide_schedule(
            1,
            max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertEqual(schedule.disposition, EpochBudgetDisposition.CONTROL_BLOCKED)
        self.assertIn("token_budget", schedule.reasons[0])

    def test_live_projection_exposes_adaptive_governance_state(self) -> None:
        state = TurnLoopState()
        state.begin_next_epoch()
        run_state: dict[str, object] = {}
        state.project_live(run_state, 1.0)
        live = run_state["_live"]
        assert isinstance(live, dict)
        self.assertTrue(live["adaptive_control_active"])
        self.assertEqual(live["adaptive_epoch_tool_limit"], 48)
        self.assertEqual(live["semantic_drift_score"], 0.0)
        self.assertTrue(live["resource_governor_allowed"])


if __name__ == "__main__":
    unittest.main()
