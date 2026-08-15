"""P18-M1 delivery-boundary regressions for real production budget bridges."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock


class P18M1DeliveryBoundaryTests(unittest.TestCase):
    def test_exact_epoch_boundary_74_75_76_preserves_global_progress(self) -> None:
        from v3.runtime_turn_orchestration import EpochBudgetDisposition, TurnLoopState

        state = TurnLoopState(action_rounds=74, epoch_action_rounds=74)
        at_74 = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertEqual(at_74.disposition, EpochBudgetDisposition.CONTINUE)
        state.reserve_one()
        self.assertEqual((state.action_rounds, state.epoch_action_rounds), (75, 75))

        at_75 = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertEqual(at_75.disposition, EpochBudgetDisposition.CHECKPOINT_CONTINUE)
        self.assertFalse(at_75.terminal)
        state.begin_next_epoch()
        self.assertEqual((state.action_rounds, state.epoch_action_rounds), (75, 0))

        after_rollover = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertTrue(after_rollover.can_schedule)
        state.reserve_one()
        self.assertEqual((state.action_rounds, state.epoch_action_rounds), (76, 1))

    def test_single_tool_real_budget_bridge_rolls_epoch_instead_of_force_stop(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState
        from v3.zongdiaodu import (
            _simple_chain_new_run_state,
            _simple_chain_prepare_tool_budget,
            set_simple_chain_continuity_checkpoint_provider,
        )

        state = TurnLoopState(
            action_rounds=75,
            iteration_count=80,
            epoch_action_rounds=75,
            epoch_iteration_count=80,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(
                os.environ,
                {"TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": temporary},
                clear=False,
            ):
                run_state = _simple_chain_new_run_state("req-p18-single", "session-p18")
                set_simple_chain_continuity_checkpoint_provider(None)
                ready, reasons = _simple_chain_prepare_tool_budget(
                    state,
                    1,
                    run_state=run_state,
                    loop_started_at=time.monotonic(),
                    source="single_tool",
                )
        self.assertTrue(ready, reasons)
        self.assertEqual(reasons, ())
        self.assertEqual(state.action_rounds, 75)
        self.assertEqual(state.epoch_index, 1)
        self.assertEqual(state.epoch_action_rounds, 0)
        self.assertEqual(run_state["continuation"]["status"], "continued")
        self.assertNotEqual(run_state.get("status"), "force_stopped")

    def test_parallel_batch_real_budget_bridge_rolls_before_batch(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState
        from v3.zongdiaodu import (
            _simple_chain_new_run_state,
            _simple_chain_prepare_tool_budget,
            set_simple_chain_continuity_checkpoint_provider,
        )

        # A two-tool parallel batch cannot fit at epoch step 74, but it fits in
        # the next epoch. The bridge must checkpoint/roll over rather than
        # reporting a terminal budget failure.
        state = TurnLoopState(
            action_rounds=74,
            iteration_count=90,
            epoch_action_rounds=74,
            epoch_iteration_count=90,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(
                os.environ,
                {"TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": temporary},
                clear=False,
            ):
                run_state = _simple_chain_new_run_state("req-p18-parallel", "session-p18")
                set_simple_chain_continuity_checkpoint_provider(None)
                ready, reasons = _simple_chain_prepare_tool_budget(
                    state,
                    2,
                    run_state=run_state,
                    loop_started_at=time.monotonic(),
                    source="parallel_tool_batch",
                )
        self.assertTrue(ready, reasons)
        self.assertEqual(reasons, ())
        self.assertEqual(state.action_rounds, 74)
        self.assertEqual(state.epoch_index, 1)
        self.assertEqual(state.epoch_action_rounds, 0)
        self.assertEqual(run_state["continuation"]["requested_tool_rounds"], 2)
        self.assertEqual(run_state["continuation"]["status"], "continued")
        self.assertNotEqual(run_state.get("status"), "force_stopped")

    def test_global_boundary_999_allows_last_step_1000_then_is_terminal(self) -> None:
        from v3.runtime_turn_orchestration import EpochBudgetDisposition, TurnLoopState

        state = TurnLoopState(
            action_rounds=999,
            epoch_index=13,
            epoch_action_rounds=24,
        )
        last = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertEqual(last.disposition, EpochBudgetDisposition.CONTINUE)
        state.reserve_one()
        self.assertEqual(state.action_rounds, 1000)
        exhausted = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertEqual(exhausted.disposition, EpochBudgetDisposition.GLOBAL_EXHAUSTED)
        self.assertTrue(exhausted.terminal)

    def test_gateway_authorized_checkpoint_failure_is_fail_closed_not_local_fallback(self) -> None:
        from v3.run_context import RunContext, bind_run_context
        from v3.runtime_turn_orchestration import TurnLoopState
        from v3.zongdiaodu import (
            _simple_chain_new_run_state,
            _simple_chain_prepare_tool_budget,
            set_simple_chain_continuity_checkpoint_provider,
        )

        context = RunContext(
            request_id="req_" + "1" * 64,
            run_id="run_" + "2" * 64,
            generation=1,
            life_id="life-p18",
            outer_execution_ticket_id="ticket-p18",
        )
        state = TurnLoopState(action_rounds=75, epoch_action_rounds=75)
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(
                os.environ,
                {"TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT": temporary},
                clear=False,
            ):
                run_state = _simple_chain_new_run_state(context.request_id, "session-p18")
                set_simple_chain_continuity_checkpoint_provider(None)
                try:
                    with bind_run_context(context):
                        ready, reasons = _simple_chain_prepare_tool_budget(
                            state,
                            1,
                            run_state=run_state,
                            loop_started_at=time.monotonic(),
                            source="single_tool",
                        )
                finally:
                    set_simple_chain_continuity_checkpoint_provider(None)
        self.assertFalse(ready)
        self.assertEqual(reasons, ("[epoch_checkpoint_failed] checkpoint persistence failed",))
        self.assertEqual(run_state["continuation"]["status"], "canonical_checkpoint_unavailable")
        self.assertEqual(state.epoch_index, 0)


if __name__ == "__main__":
    unittest.main()
