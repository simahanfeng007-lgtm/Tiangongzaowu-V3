from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "src/total_gateway/store.py"
UOW_PATH = ROOT / "src/total_gateway/store_unit_of_work.py"
GATE_PATH = ROOT / ".github/workflows/architecture-gate.yml"


def load_uow_module():
    spec = importlib.util.spec_from_file_location("p17_m3_04_store_unit_of_work", UOW_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Gateway Store UoW seam")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingConnection:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.sql: list[str] = []
        self.fail_on = fail_on

    def execute(self, sql: str, parameters: tuple[object, ...] = ()):
        del parameters
        self.sql.append(sql)
        if sql == self.fail_on:
            self.fail_on = None
            raise RuntimeError(f"{sql} failure")
        return self


def find_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"missing {class_name}.{method_name}")


def expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = expr_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return f"{expr_name(node.func)}()"
    return type(node).__name__


class GatewayStoreUowBoundaryTests(unittest.TestCase):
    def test_success_preserves_begin_immediate_then_commit(self) -> None:
        module = load_uow_module()
        connection = RecordingConnection()
        with module.gateway_store_write_transaction(connection):
            self.assertEqual(connection.sql, ["BEGIN IMMEDIATE"])
        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "COMMIT"])

    def test_body_failure_preserves_rollback_and_exception_identity(self) -> None:
        module = load_uow_module()
        connection = RecordingConnection()
        marker = RuntimeError("rollback-marker")
        with self.assertRaises(RuntimeError) as raised:
            with module.gateway_store_write_transaction(connection):
                raise marker
        self.assertIs(raised.exception, marker)
        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "ROLLBACK"])

    def test_commit_failure_preserves_historical_rollback_path(self) -> None:
        module = load_uow_module()
        connection = RecordingConnection(fail_on="COMMIT")
        with self.assertRaisesRegex(RuntimeError, "COMMIT failure"):
            with module.gateway_store_write_transaction(connection):
                pass
        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"])

    def test_uow_is_transaction_mechanics_only(self) -> None:
        source = UOW_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn('connection.execute("BEGIN IMMEDIATE")', source)
        self.assertIn('connection.execute("COMMIT")', source)
        self.assertIn('connection.execute("ROLLBACK")', source)
        self.assertNotIn("sqlite3.connect", source)
        self.assertNotIn("self._lock", source)
        for forbidden in (
            "CREATE TABLE",
            "CREATE INDEX",
            "PRAGMA ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "SELECT ",
            "GatewayStateStore(",
        ):
            self.assertNotIn(forbidden, source)
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual([node.name for node in classes], ["GatewayStoreWriteConnection"])

    def test_store_keeps_closed_state_authority_and_delegates_only_mechanics(self) -> None:
        source = STORE_PATH.read_text(encoding="utf-8")
        method = find_method(ast.parse(source), "GatewayStateStore", "_write_transaction")
        rendered = ast.get_source_segment(source, method) or ""
        self.assertIn("if self._closed:", rendered)
        self.assertIn('raise StoreError("gateway store is closed")', rendered)
        self.assertIn("gateway_store_write_transaction(self._connection)", rendered)
        self.assertNotIn('execute("BEGIN IMMEDIATE")', rendered)
        self.assertNotIn('execute("ROLLBACK")', rendered)
        self.assertNotIn('execute("COMMIT")', rendered)

    def test_all_write_transaction_callers_preserve_lock_before_uow(self) -> None:
        source = STORE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        callers = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            names = [expr_name(item.context_expr) for item in node.items]
            if "self._write_transaction()" not in names:
                continue
            callers += 1
            self.assertGreaterEqual(len(names), 2)
            self.assertEqual(names[0], "self._lock")
            self.assertEqual(names[1], "self._write_transaction()")
        self.assertGreater(callers, 0)

    def test_health_read_transaction_remains_store_owned(self) -> None:
        source = STORE_PATH.read_text(encoding="utf-8")
        health = find_method(ast.parse(source), "GatewayStateStore", "health_check")
        rendered = ast.get_source_segment(source, health) or ""
        self.assertIn('execute("BEGIN IMMEDIATE")', rendered)
        self.assertIn('execute("ROLLBACK")', rendered)
        self.assertNotIn("gateway_store_write_transaction", rendered)

    def test_architecture_gate_covers_m3_04_and_compiles_seam(self) -> None:
        gate = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("Run P17 M3-04 Gateway Store UoW regression", gate)
        self.assertIn('test_total_gateway_store_p17*.py', gate)
        self.assertIn("src/total_gateway/store.py", gate)
        self.assertIn("src/total_gateway/store_unit_of_work.py", gate)


if __name__ == "__main__":
    unittest.main()
