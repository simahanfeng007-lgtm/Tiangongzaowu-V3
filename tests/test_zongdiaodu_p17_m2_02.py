from __future__ import annotations

import ast
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "app/backend/tiangong-backend/v3"
ZONG = V3 / "zongdiaodu.py"
KERNEL = V3 / "simple_chain" / "kernel.py"  # P17-M2 拆分：simple-chain 机器已迁出
TURN = V3 / "runtime_turn_orchestration.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"missing {class_name}.{method_name}")


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _call_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    class Collector(ast.NodeVisitor):
        def visit_Call(self, call: ast.Call) -> None:
            names.append(_qualified_name(call.func))
            self.generic_visit(call)
    Collector().visit(node)
    return names


def _load_turn_module():
    spec = importlib.util.spec_from_file_location("p17_m2_turn_orchestration", TURN)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load turn orchestration module")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module



def _zd_combined_source():
    """P17-M2 拆分后：逻辑总调度源 = zongdiaodu + simple_chain/kernel 拼接。"""
    v3 = Path(__file__).resolve().parents[1] / "app" / "backend" / "tiangong-backend" / "v3"
    return (v3 / "zongdiaodu.py").read_text(encoding="utf-8") + "\n\n" + (v3 / "simple_chain" / "kernel.py").read_text(encoding="utf-8")


class ZongdiaoduM202Tests(unittest.TestCase):
    def test_turn_boundary_is_pure_coordination(self) -> None:
        source = TURN.read_text(encoding="utf-8")
        self.assertNotIn("from typing import Any", source)
        for forbidden in ("zongdiaodu", "check_tool_permission", "GUGE", "JIROU", "_jineng_zhixing"):
            self.assertNotIn(forbidden, source)

    def test_turn_state_preserves_counter_and_live_projection_semantics(self) -> None:
        module = _load_turn_module()
        state = module.TurnLoopState()
        self.assertEqual(0, state.action_rounds)
        self.assertEqual(1, state.reserve_one())
        self.assertEqual(2, state.record_batch_result())
        self.assertEqual(1, state.bump_iteration())
        self.assertEqual(1, state.bump_repeat("same"))
        self.assertEqual(2, state.bump_repeat("same"))
        run_state: dict[str, object] = {}
        state.project_live(run_state, 12.5)
        self.assertEqual(1, run_state["_live"]["iteration_count"])
        self.assertEqual(2, run_state["_live"]["tool_rounds"])
        self.assertEqual(12.5, run_state["_live"]["loop_started_at"])

    def test_budget_keeps_historical_strict_greater_than_semantics(self) -> None:
        module = _load_turn_module()
        at_limit = module.evaluate_turn_budget(
            iteration_count=20,
            elapsed_seconds=100.0,
            max_iterations=20,
            max_wall_clock_seconds=100.0,
        )
        self.assertFalse(at_limit.exhausted)
        over = module.evaluate_turn_budget(
            iteration_count=21,
            elapsed_seconds=101.0,
            max_iterations=20,
            max_wall_clock_seconds=100.0,
        )
        self.assertTrue(over.exhausted)
        self.assertEqual(2, len(over.reasons))

    def test_parallel_coordination_preserves_first_reuse_guard_ready_order(self) -> None:
        module = _load_turn_module()
        Step = module.PreparedStep
        result = module.coordinate_parallel_steps([
            Step("omni_body", {}, "file.read", (), "a"),
            Step("omni_body", {}, "file.delete_to_trash", (), "a", artifact_guard_hits=("x",)),
            Step("omni_body", {}, "file.read", (), "b", reuse_prior_fact=True, artifact_guard_hits=("x",)),
            Step("omni_body", {}, "file.move", (), "c", artifact_guard_hits=("x",)),
            Step("omni_body", {}, "file.read", (), "d"),
        ])
        self.assertEqual(["a", "d"], [item.identity_key for item in result.ready])
        self.assertEqual(["b"], [item.identity_key for item in result.reused])
        self.assertEqual(["c"], [item.identity_key for item in result.guarded])

    def test_zongdiaodu_delegates_coordination_but_keeps_executor_authority(self) -> None:
        method = _method(ast.parse(_zd_combined_source()), "Zongdiaodu", "_huanxing_simple_chain")
        calls = set(_call_names(method))
        required = {
            "TurnLoopState",
            "turn_loop.bump_iteration",
            "turn_loop.bump_repeat",
            "_simple_chain_prepare_tool_budget",
            "turn_loop.reserve_one",
            "turn_loop.record_batch_result",
            "turn_loop.project_live",
            "evaluate_turn_budget",
            "coordinate_parallel_steps",
            "_simple_chain_regenerative_execute_tool",
        }
        self.assertTrue(required.issubset(calls), (required - calls, calls))
        # P18-M1 upgrades the old single global can_schedule cutoff; P18-M2
        # adds the Gateway-backed transactional dispatch boundary. Zongdiaodu
        # still owns orchestration, but it may no longer call the physical
        # executor directly from the main loop.
        self.assertNotIn("turn_loop.can_schedule", calls)
        self.assertNotIn("self._jineng_zhixing", calls)
        source = ast.get_source_segment(_zd_combined_source(), method) or ""
        self.assertNotIn("repeat_observation_counts", source)
        self.assertNotIn("seen_parallel", source)
        self.assertNotIn("iteration_count > _SIMPLE_CHAIN_MAX_LOOP_TURNS", source)

    def test_closed_world_boundary_owns_turn_module(self) -> None:
        config = json.loads((ROOT / "source-ownership.json").read_text(encoding="utf-8"))
        v3 = next(row for row in config["mappings"] if row.get("id") == "v3-backend-main")
        roots = set(v3["boundary_policy"]["implementation_roots"])
        self.assertIn("runtime_turn_orchestration.py", roots)


if __name__ == "__main__":
    unittest.main()
