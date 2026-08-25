from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "app" / "backend" / "tiangong-backend" / "v3"
BOUNDARY = V3 / "runtime_tool_result_boundary.py"
ZONG = V3 / "zongdiaodu.py"
KERNEL = V3 / "simple_chain" / "kernel.py"  # P17-M2 拆分：simple-chain 机器已迁出
OWNERSHIP = ROOT / "source-ownership.json"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise AssertionError(f"expected one top-level function {name}, got {len(matches)}")
    return matches[0]


def _calls(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        target = item.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names



def _zd_combined_source():
    """P17-M2 拆分后：逻辑总调度源 = zongdiaodu + simple_chain/kernel 拼接。"""
    v3 = Path(__file__).resolve().parents[1] / "app" / "backend" / "tiangong-backend" / "v3"
    return (v3 / "zongdiaodu.py").read_text(encoding="utf-8") + "\n\n" + (v3 / "simple_chain" / "kernel.py").read_text(encoding="utf-8")


class ZongdiaoduM203Tests(unittest.TestCase):
    def test_boundary_consumes_canonical_contract_without_own_schema_or_executor(self) -> None:
        source = BOUNDARY.read_text(encoding="utf-8")
        tree = _tree(BOUNDARY)
        imports = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                imports.append((node.module or "", {alias.name for alias in node.names}))
        self.assertIn(("tool_result_contract", {"normalize_tool_result"}), imports)
        self.assertNotIn("TOOL_RESULT_SCHEMA", source)
        self.assertNotIn("_jineng_zhixing", source)
        self.assertNotIn("check_tool_permission", source)
        self.assertNotIn("AuthorityGate", source)
        self.assertNotIn("from .zongdiaodu", source)

    def test_dispatch_projection_preserves_historical_shape(self) -> None:
        fn = _function(_tree(BOUNDARY), "project_tool_dispatch")
        source = ast.unparse(fn)
        for token in ("status", "done", "failed", "resultStatus", "resultContract", "resultSummary"):
            self.assertIn(token, source)
        self.assertIn("canonical_tool_result", _calls(fn))

    def test_contract_envelope_keeps_world_post_commit_downstream_of_contract(self) -> None:
        fn = _function(_tree(BOUNDARY), "attach_tool_result_contract")
        source = ast.unparse(fn)
        self.assertIn("canonical_tool_result", _calls(fn))
        self.assertIn("notify_native_post_commit", _calls(fn))
        self.assertIn("NativePostCommitEvent", _calls(fn))
        self.assertIn("TOOL_RESULT", source)
        self.assertIn("v3.tool_result_contract", source)
        self.assertIn("source_inquiry_id", source)
        self.assertIn("outer_execution_ticket_id", source)

    def test_observed_write_requires_authoritative_evidence_and_keeps_legacy_resume(self) -> None:
        fn = _function(_tree(BOUNDARY), "contract_observed_write")
        source = ast.unparse(fn)
        self.assertIn("observed_write_effect", source)
        self.assertIn("write_evidence", source)
        self.assertIn("authoritative", source)
        self.assertIn("changed_files", source)
        self.assertIn("deleted_files", source)
        self.assertIn("verified_unchanged_files", source)
        self.assertIn("write_effect", source)

    def test_completion_boundary_delegates_single_terminal_authority(self) -> None:
        fn = _function(_tree(BOUNDARY), "decide_simple_chain_completion")
        calls = _calls(fn)
        self.assertIn("evidence_check", calls)
        self.assertIn("decide_task_contract_completion", calls)
        source = ast.unparse(fn)
        self.assertIn("has_real_observation=bool(quality_history)", source)
        forbidden = ("_runtime_detects_work_intent", "_requires_real_mutation", "_has_delivery_intent")
        for name in forbidden:
            self.assertNotIn(name, source)

    def test_zongdiaodu_facades_delegate_but_executor_authority_stays_local(self) -> None:
        tree = ast.parse(_zd_combined_source())
        expected = {
            "_tool_dispatch_with_result": "project_tool_dispatch",
            "_tool_result_with_contract": "attach_tool_result_contract",
            "_contract_observed_write": "contract_observed_write",
            "_tool_write_verified": "tool_write_verified",
            "_simple_chain_life_completion_gate": "decide_simple_chain_completion",
        }
        for name, delegated in expected.items():
            fn = _function(tree, name)
            self.assertIn(delegated, _calls(fn), name)
        zong_source = _zd_combined_source()
        self.assertIn("self._jineng_zhixing(", zong_source)
        self.assertNotIn("decide_task_contract_completion(", zong_source)
        self.assertNotIn("normalize_tool_result(", zong_source)

    def test_closed_world_boundary_owns_new_module(self) -> None:
        data = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
        mapping = next(item for item in data["mappings"] if item.get("id") == "v3-backend-main")
        roots = mapping["boundary_policy"]["implementation_roots"]
        self.assertIn("runtime_tool_result_boundary.py", roots)


if __name__ == "__main__":
    unittest.main()
