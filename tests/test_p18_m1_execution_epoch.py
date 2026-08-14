"""P18-M1 execution-epoch budget regression tests.

These tests cover the first long-chain governance primitive: a bounded local
execution window (epoch) inside one authoritative request/run, with a separate
global tool budget. Local exhaustion must be recoverable through checkpoint
continuation; only the global limit is terminal for budget purposes.
"""

from __future__ import annotations

import unittest
from pathlib import Path


class ExecutionEpochBudgetTests(unittest.TestCase):
    def test_epoch_exhaustion_is_non_terminal_checkpoint_continue(self) -> None:
        from v3.runtime_turn_orchestration import (
            EpochBudgetDisposition,
            TurnLoopState,
        )

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

        decision = state.decide_schedule(
            1,
            max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertEqual(decision.disposition, EpochBudgetDisposition.CHECKPOINT_CONTINUE)
        self.assertTrue(decision.epoch_exhausted)
        self.assertFalse(decision.global_exhausted)
        self.assertTrue(decision.should_checkpoint_continue)
        self.assertFalse(decision.terminal)
        self.assertEqual(state.action_rounds, 75)
        self.assertEqual(state.epoch_action_rounds, 75)

    def test_epoch_rollover_preserves_global_authority_counters(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState

        state = TurnLoopState()
        for _ in range(75):
            state.reserve_one()
        for _ in range(12):
            state.bump_iteration()
        state.bump_repeat("same-read")

        next_epoch = state.begin_next_epoch()
        self.assertEqual(next_epoch, 1)
        self.assertEqual(state.action_rounds, 75)
        self.assertEqual(state.iteration_count, 12)
        self.assertEqual(state.epoch_action_rounds, 0)
        self.assertEqual(state.epoch_iteration_count, 0)
        self.assertEqual(state.repeat_counts, {})

        self.assertTrue(
            state.decide_schedule(
                1,
                max_epoch_rounds=75,
                max_global_rounds=1000,
            ).can_schedule
        )
        state.reserve_one()
        self.assertEqual(state.action_rounds, 76)
        self.assertEqual(state.epoch_action_rounds, 1)

    def test_global_budget_is_terminal_across_many_epochs(self) -> None:
        from v3.runtime_turn_orchestration import (
            EpochBudgetDisposition,
            TurnLoopState,
        )

        state = TurnLoopState()
        while state.action_rounds < 1000:
            decision = state.decide_schedule(
                1,
                max_epoch_rounds=75,
                max_global_rounds=1000,
            )
            if decision.should_checkpoint_continue:
                state.begin_next_epoch()
                continue
            self.assertTrue(decision.can_schedule)
            state.reserve_one()

        self.assertEqual(state.action_rounds, 1000)
        terminal = state.decide_schedule(
            1,
            max_epoch_rounds=75,
            max_global_rounds=1000,
        )
        self.assertEqual(terminal.disposition, EpochBudgetDisposition.GLOBAL_EXHAUSTED)
        self.assertTrue(terminal.global_exhausted)
        self.assertTrue(terminal.terminal)
        self.assertFalse(terminal.can_schedule)

    def test_batch_reservations_count_against_both_budgets(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState

        state = TurnLoopState()
        for _ in range(73):
            state.record_batch_result()

        self.assertTrue(
            state.decide_schedule(
                2,
                max_epoch_rounds=75,
                max_global_rounds=1000,
            ).can_schedule
        )
        self.assertTrue(
            state.decide_schedule(
                3,
                max_epoch_rounds=75,
                max_global_rounds=1000,
            ).should_checkpoint_continue
        )

    def test_live_projection_exposes_epoch_and_global_counters(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState

        state = TurnLoopState()
        state.bump_iteration()
        state.reserve_one()
        state.begin_next_epoch()
        state.bump_iteration()
        state.reserve_one()

        run_state: dict[str, object] = {}
        state.project_live(run_state, 123.5)
        live = run_state["_live"]
        self.assertIsInstance(live, dict)
        assert isinstance(live, dict)
        self.assertEqual(live["tool_rounds"], 2)
        self.assertEqual(live["global_tool_rounds"], 2)
        self.assertEqual(live["epoch_tool_rounds"], 1)
        self.assertEqual(live["iteration_count"], 2)
        self.assertEqual(live["global_iteration_count"], 2)
        self.assertEqual(live["epoch_iteration_count"], 1)
        self.assertEqual(live["epoch_index"], 1)
        self.assertEqual(live["loop_started_at"], 123.5)

    def test_legacy_single_budget_semantics_remain_unchanged(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState

        state = TurnLoopState(action_rounds=74)
        self.assertTrue(state.can_schedule(1, 75))
        self.assertFalse(state.can_schedule(2, 75))


    def test_global_boundary_999_then_1000(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState
        state = TurnLoopState(action_rounds=999, epoch_index=13, epoch_action_rounds=24)
        self.assertTrue(state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000).can_schedule)
        state.reserve_one()
        decision = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertTrue(decision.terminal)
        self.assertTrue(decision.global_exhausted)

    def test_real_zongdiaodu_paths_use_p18_dual_budget_bridge(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "backend" / "tiangong-backend" / "v3" / "zongdiaodu.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("turn_loop.can_schedule(", source)
        self.assertIn("_SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS", source)
        self.assertIn('source="parallel_tool_batch"', source)
        self.assertIn('source="single_tool"', source)
        self.assertIn("epoch.checkpoint_committed", source)
        self.assertIn("run.continued", source)


    def test_production_checkpoint_rollover_preserves_global_counters(self) -> None:
        import os
        import tempfile
        import time
        from unittest import mock
        from v3.runtime_turn_orchestration import TurnLoopState
        from v3.zongdiaodu import _simple_chain_checkpoint_continue, _simple_chain_new_run_state

        state = TurnLoopState(
            action_rounds=75,
            iteration_count=181,
            epoch_index=0,
            epoch_action_rounds=75,
            epoch_iteration_count=181,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": temp_dir}, clear=False):
                run_state = _simple_chain_new_run_state("req-p18-turn", "session-p18")
                with mock.patch("v3.zongdiaodu._simple_chain_emit_event", return_value=True):
                    ok = _simple_chain_checkpoint_continue(
                        run_state,
                        state,
                        requested=0,
                        loop_started_at=time.monotonic(),
                        source="epoch_turn_budget",
                    )
        self.assertTrue(ok)
        self.assertEqual(state.action_rounds, 75)
        self.assertEqual(state.iteration_count, 181)
        self.assertEqual(state.epoch_index, 1)
        self.assertEqual(state.epoch_action_rounds, 0)
        self.assertEqual(state.epoch_iteration_count, 1)
        self.assertEqual(run_state["budget"]["global_tool_rounds"], 75)
        self.assertEqual(run_state["budget"]["global_rounds_used"], 181)
        self.assertEqual(run_state["budget"]["epoch_index"], 1)
        self.assertEqual(run_state["budget"]["epoch_tool_rounds"], 0)
        self.assertEqual(run_state["budget"]["epoch_rounds_used"], 1)
        self.assertEqual(run_state["continuation"]["status"], "continued")

    def test_loop_turn_cutoff_is_epoch_local_but_wall_clock_remains_global(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "backend" / "tiangong-backend" / "v3" / "zongdiaodu.py"
        ).read_text(encoding="utf-8")
        self.assertIn("iteration_count=turn_loop.epoch_iteration_count", source)
        self.assertIn('source="epoch_turn_budget"', source)
        self.assertIn("wall_clock_decision = evaluate_turn_budget", source)
        self.assertIn("effective_wall_clock_seconds", source)
        self.assertNotIn(
            "iteration_count=iteration_count,\n                elapsed_seconds=loop_elapsed",
            source,
        )
        self.assertIn('"global_tool_rounds_max": _SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS', source)
        self.assertIn('"epoch_tool_rounds_max": _SIMPLE_CHAIN_MAX_TOOL_ROUNDS', source)


if __name__ == "__main__":
    unittest.main()
