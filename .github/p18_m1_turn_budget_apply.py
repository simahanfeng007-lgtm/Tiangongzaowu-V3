from __future__ import annotations

from pathlib import Path

ZONG = Path("app/backend/tiangong-backend/v3/zongdiaodu.py")
P18_TEST = Path("tests/test_p18_m1_execution_epoch.py")
LEGACY_TEST = Path("tests/test_simple_chain_loop_budget.py")


def expect(source: str, needle: str, count: int = 1) -> None:
    actual = source.count(needle)
    if actual != count:
        raise SystemExit(f"anchor mismatch count={actual}, expected={count}: {needle[:100]!r}")


def patch_zong() -> None:
    text = ZONG.read_text(encoding="utf-8")

    budget_limit_line = '            "tool_rounds_max": _SIMPLE_CHAIN_MAX_TOOL_ROUNDS,'
    expect(text, budget_limit_line, 2)
    budget_limit_replacement = '''            "tool_rounds_max": _SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS,
            "global_tool_rounds_max": _SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS,
            "epoch_tool_rounds_max": _SIMPLE_CHAIN_MAX_TOOL_ROUNDS,
            "global_tool_rounds": 0,
            "epoch_index": 0,
            "epoch_rounds_used": 0,
            "epoch_tool_rounds": 0,'''
    text = text.replace(budget_limit_line, budget_limit_replacement)

    projection = '''            budget.update({
                "rounds_used": int(live.get("iteration_count") or 0),
                "tool_rounds": int(live.get("tool_rounds") or 0),
                "wall_clock_used_s": max(0.0, wall),
            })'''
    expect(text, projection)
    projection_new = '''            global_rounds = int(live.get("global_iteration_count") or live.get("iteration_count") or 0)
            global_tools = int(live.get("global_tool_rounds") or live.get("tool_rounds") or 0)
            budget.update({
                "rounds_used": global_rounds,
                "tool_rounds": global_tools,
                "global_rounds_used": global_rounds,
                "global_tool_rounds": global_tools,
                "global_tool_rounds_max": _SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS,
                "epoch_index": int(live.get("epoch_index") or 0),
                "epoch_rounds_used": int(live.get("epoch_iteration_count") or 0),
                "epoch_rounds_max": _SIMPLE_CHAIN_MAX_LOOP_TURNS,
                "epoch_tool_rounds": int(live.get("epoch_tool_rounds") or 0),
                "epoch_tool_rounds_max": _SIMPLE_CHAIN_MAX_TOOL_ROUNDS,
                "wall_clock_used_s": max(0.0, wall),
            })'''
    text = text.replace(projection, projection_new, 1)

    view_anchor = '        "generated_attachments": list(run_state.get("generated_attachments") or [])[-8:],\n        "failures": list(run_state.get("failures") or [])[-8:],'
    expect(text, view_anchor)
    view_new = '''        "generated_attachments": list(run_state.get("generated_attachments") or [])[-8:],
        "budget": run_state.get("budget") if isinstance(run_state.get("budget"), dict) else {},
        "authority_identity": (
            run_state.get("authority_identity")
            if isinstance(run_state.get("authority_identity"), dict)
            else {}
        ),
        "continuation": (
            run_state.get("continuation")
            if isinstance(run_state.get("continuation"), dict)
            else {}
        ),
        "failures": list(run_state.get("failures") or [])[-8:],'''
    text = text.replace(view_anchor, view_new, 1)

    rollover = '''    next_epoch = turn_loop.begin_next_epoch()
    turn_loop.project_live(run_state, loop_started_at)'''
    expect(text, rollover)
    rollover_new = '''    next_epoch = turn_loop.begin_next_epoch()
    # The model decision that triggered rollover is executed in the new Epoch.
    # Keep the global iteration unchanged while accounting it once locally.
    turn_loop.epoch_iteration_count = 1
    turn_loop.project_live(run_state, loop_started_at)'''
    text = text.replace(rollover, rollover_new, 1)

    old_budget = '''            budget_decision = evaluate_turn_budget(
                iteration_count=iteration_count,
                elapsed_seconds=loop_elapsed,
                max_iterations=_SIMPLE_CHAIN_MAX_LOOP_TURNS,
                max_wall_clock_seconds=effective_wall_clock_seconds,
            )
            if budget_decision.exhausted:
                budget_reasons = list(budget_decision.reasons)
                final_guard_exhausted = True
                final_chain_status = "force_stopped"
                shenti, huifu = _natural_closeout("force_stopped", budget_reasons)
                if run_control:
                    run_control.step(
                        "simple_chain_budget",
                        "Platform execution budget",
                        "failed",
                        "; ".join(budget_reasons)[:500],
                        meta={
                            "iteration_count": iteration_count,
                            "tool_rounds": gongju_cishu,
                            "elapsed_seconds": round(loop_elapsed, 1),
                            "max_iterations": _SIMPLE_CHAIN_MAX_LOOP_TURNS,
                            "max_wall_clock_seconds": round(effective_wall_clock_seconds, 1),
                        },
                    )
                break'''
    expect(text, old_budget)
    new_budget = '''            # P18-M1: the historical loop-turn cap is an Epoch-local context
            # budget. Hitting it checkpoints and continues the same authoritative Run.
            epoch_turn_decision = evaluate_turn_budget(
                iteration_count=turn_loop.epoch_iteration_count,
                elapsed_seconds=0.0,
                max_iterations=_SIMPLE_CHAIN_MAX_LOOP_TURNS,
                max_wall_clock_seconds=float("inf"),
            )
            if epoch_turn_decision.exhausted:
                checkpointed = _simple_chain_checkpoint_continue(
                    run_state,
                    turn_loop,
                    requested=0,
                    loop_started_at=loop_started_at,
                    source="epoch_turn_budget",
                )
                if not checkpointed:
                    budget_reasons = ["[epoch_checkpoint_failed] epoch turn checkpoint persistence failed"]
                    final_guard_exhausted = True
                    final_chain_status = "force_stopped"
                    shenti, huifu = _natural_closeout("force_stopped", budget_reasons)
                    if run_control:
                        run_control.step(
                            "simple_chain_epoch_turn_checkpoint",
                            "Epoch turn checkpoint",
                            "failed",
                            budget_reasons[0],
                            meta={
                                "global_iteration_count": iteration_count,
                                "epoch_index": turn_loop.epoch_index,
                                "max_epoch_iterations": _SIMPLE_CHAIN_MAX_LOOP_TURNS,
                            },
                        )
                    break

            # Wall clock remains an absolute platform/Authority deadline. Epoch
            # rollover must never extend or bypass it.
            wall_clock_decision = evaluate_turn_budget(
                iteration_count=0,
                elapsed_seconds=loop_elapsed,
                max_iterations=_SIMPLE_CHAIN_MAX_LOOP_TURNS,
                max_wall_clock_seconds=effective_wall_clock_seconds,
            )
            if wall_clock_decision.exhausted:
                budget_reasons = list(wall_clock_decision.reasons)
                final_guard_exhausted = True
                final_chain_status = "force_stopped"
                shenti, huifu = _natural_closeout("force_stopped", budget_reasons)
                if run_control:
                    run_control.step(
                        "simple_chain_budget",
                        "Platform execution budget",
                        "failed",
                        "; ".join(budget_reasons)[:500],
                        meta={
                            "global_iteration_count": iteration_count,
                            "epoch_iteration_count": turn_loop.epoch_iteration_count,
                            "epoch_index": turn_loop.epoch_index,
                            "tool_rounds": gongju_cishu,
                            "elapsed_seconds": round(loop_elapsed, 1),
                            "max_epoch_iterations": _SIMPLE_CHAIN_MAX_LOOP_TURNS,
                            "max_wall_clock_seconds": round(effective_wall_clock_seconds, 1),
                        },
                    )
                break'''
    text = text.replace(old_budget, new_budget, 1)

    if "iteration_count=iteration_count,\n                elapsed_seconds=loop_elapsed" in text:
        raise SystemExit("legacy global iteration terminal budget remains")
    if 'source="epoch_turn_budget"' not in text:
        raise SystemExit("epoch-turn continuation source missing")
    ZONG.write_text(text, encoding="utf-8")


def patch_p18_test() -> None:
    text = P18_TEST.read_text(encoding="utf-8")
    marker = '\n\nif __name__ == "__main__":\n    unittest.main()\n'
    expect(text, marker)
    addition = '''

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
            "iteration_count=iteration_count,\\n                elapsed_seconds=loop_elapsed",
            source,
        )
        self.assertIn('"global_tool_rounds_max": _SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS', source)
        self.assertIn('"epoch_tool_rounds_max": _SIMPLE_CHAIN_MAX_TOOL_ROUNDS', source)
'''
    P18_TEST.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


def patch_legacy_test() -> None:
    text = LEGACY_TEST.read_text(encoding="utf-8")
    import_anchor = '''            _SIMPLE_CHAIN_MAX_COMPLETION_CORRECTIONS,
            _SIMPLE_CHAIN_MAX_LOOP_TURNS,'''
    expect(text, import_anchor)
    text = text.replace(
        import_anchor,
        '''            _SIMPLE_CHAIN_MAX_COMPLETION_CORRECTIONS,
            _SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS,
            _SIMPLE_CHAIN_MAX_LOOP_TURNS,''',
        1,
    )
    assert_anchor = "        self.assertEqual(_SIMPLE_CHAIN_MAX_TOOL_ROUNDS, 75)\n"
    expect(text, assert_anchor)
    text = text.replace(
        assert_anchor,
        assert_anchor + "        self.assertEqual(_SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS, 1000)\n",
        1,
    )
    LEGACY_TEST.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_zong()
    patch_p18_test()
    patch_legacy_test()
