from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "app/backend/tiangong-backend/v3"
ZONG = V3 / "zongdiaodu.py"
KERNEL = V3 / "simple_chain" / "kernel.py"  # P17-M2 拆分：simple-chain 机器已迁出
COMPOSITION = V3 / "runtime_composition.py"
LIFECYCLE = V3 / "runtime_lifecycle.py"
BOOTSTRAP = V3 / "runtime_bootstrap.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"missing {class_name}.{method_name}")


def _function(tree: ast.Module, function_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    raise AssertionError(f"missing function {function_name}")


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



def _zd_combined_source():
    """P17-M2 拆分后：逻辑总调度源 = zongdiaodu + simple_chain/kernel 拼接。"""
    v3 = Path(__file__).resolve().parents[1] / "app" / "backend" / "tiangong-backend" / "v3"
    return (v3 / "zongdiaodu.py").read_text(encoding="utf-8") + "\n\n" + (v3 / "simple_chain" / "kernel.py").read_text(encoding="utf-8")


class ZongdiaoduM201Tests(unittest.TestCase):
    def test_import_bootstrap_is_delegated_but_semantics_are_preserved(self) -> None:
        source = _zd_combined_source()
        self.assertNotIn(
            "from .world_understanding_production import install_world_understanding_observer",
            source,
        )
        self.assertNotIn("install_world_understanding_observer()", source)
        self.assertIn("install_zongdiaodu_import_observers", source)

        tree = ast.parse(_zd_combined_source())
        top_level_calls = [
            _qualified_name(node.value.func)
            for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        self.assertIn("install_zongdiaodu_import_observers", top_level_calls)
        self.assertIn(
            "install_world_understanding_observer",
            _call_names(_function(_tree(BOOTSTRAP), "install_zongdiaodu_import_observers")),
        )

    def test_constructor_consumes_composition_instead_of_building_engines(self) -> None:
        init = _method(ast.parse(_zd_combined_source()), "Zongdiaodu", "__init__")
        calls = _call_names(init)
        self.assertEqual(1, calls.count("build_zongdiaodu_composition"))
        forbidden = {
            "HttpKehuduan",
            "GuanchaYinqing",
            "JinhuaYinqing",
            "JinhuaBiaodaRouter",
            "JinhuaBihuanYinqing",
            "ZiyuYinqing",
            "threading.Lock",
        }
        self.assertEqual(set(), forbidden.intersection(calls), calls)

    def test_composition_root_owns_concrete_dependency_construction(self) -> None:
        tree = _tree(COMPOSITION)
        imported_modules = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(any(name.endswith("zongdiaodu") for name in imported_modules))
        calls = set(_call_names(tree))
        required = {
            "HttpKehuduan",
            "GutongCeng",
            "GuanchaYinqing",
            "JinhuaYinqing",
            "JinhuaBiaodaRouter",
            "JinhuaBihuanYinqing",
            "ZiyuYinqing",
            "threading.Lock",
        }
        self.assertTrue(required.issubset(calls), (required - calls, calls))

    def test_start_and_stop_are_lifecycle_port_delegations(self) -> None:
        tree = ast.parse(_zd_combined_source())
        start_calls = set(_call_names(_method(tree, "Zongdiaodu", "qidong")))
        stop_calls = set(_call_names(_method(tree, "Zongdiaodu", "tingzhi")))
        self.assertIn("start_zongdiaodu_runtime", start_calls)
        self.assertIn("stop_zongdiaodu_runtime", stop_calls)
        self.assertNotIn("TONGBU.qidong", start_calls)
        self.assertNotIn("QIAOJIE.qidong", start_calls)
        self.assertNotIn("self.xintiao.tingzhi", stop_calls)

    def test_lifecycle_boundary_is_typed_and_keeps_historical_order(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn("class ZongdiaoduLifecycleHost(Protocol)", source)
        self.assertNotIn("from typing import Any", source)
        calls = _call_names(_function(_tree(LIFECYCLE), "start_zongdiaodu_runtime"))
        expected = [
            "host._cleanup_stale_run_states",
            "host.xintiao.gengxin_shenti",
            "host.xintiao.qidong",
            "TONGBU.qidong",
            "QIAOJIE.shezhi_zongdiaodu",
            "QIAOJIE.qidong",
        ]
        positions = [calls.index(name) for name in expected]
        self.assertEqual(sorted(positions), positions, calls)

    def test_closed_world_boundary_owns_new_modules(self) -> None:
        config = json.loads((ROOT / "source-ownership.json").read_text(encoding="utf-8"))
        v3 = next(row for row in config["mappings"] if row.get("id") == "v3-backend-main")
        roots = set(v3["boundary_policy"]["implementation_roots"])
        self.assertTrue(
            {"runtime_bootstrap.py", "runtime_composition.py", "runtime_lifecycle.py"}.issubset(roots)
        )


if __name__ == "__main__":
    unittest.main()
