from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "src/total_gateway/store.py"
UOW = ROOT / "src/total_gateway/store_unit_of_work.py"
TEST = ROOT / "tests/test_total_gateway_store_p17_m3_04.py"
GATE = ROOT / ".github/workflows/architecture-gate.yml"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def write_new(path: Path, content: str) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing candidate file: {path.relative_to(ROOT)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return f"{_name(node.func)}()"
    return type(node).__name__


def patch_store() -> None:
    source = STORE.read_text(encoding="utf-8")
    if source.count("import sqlite3\n") != 1:
        raise RuntimeError("store sqlite3 import anchor is not unique")
    source = source.replace(
        "import sqlite3\n",
        "import sqlite3\n\nfrom .store_unit_of_work import gateway_store_write_transaction\n",
        1,
    )

    tree = ast.parse(source)
    matches: list[ast.FunctionDef] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "GatewayStateStore":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "_write_transaction":
                matches.append(item)
    if len(matches) != 1:
        raise RuntimeError(f"expected one GatewayStateStore._write_transaction, found {len(matches)}")

    method = matches[0]
    if not any(isinstance(dec, ast.Name) and dec.id == "contextmanager" for dec in method.decorator_list):
        raise RuntimeError("_write_transaction lost historical @contextmanager contract")
    if len(method.body) != 3:
        raise RuntimeError(f"unexpected _write_transaction top-level statement count: {len(method.body)}")

    precondition, begin_stmt, try_stmt = method.body

    # Preserve the Store-owned closed-state authority exactly.
    if not isinstance(precondition, ast.If):
        raise RuntimeError("historical Store closed-state precondition is no longer an if")
    if _name(precondition.test) != "self._closed":
        raise RuntimeError(f"historical Store closed-state predicate changed: {_name(precondition.test)}")
    if len(precondition.body) != 1 or not isinstance(precondition.body[0], ast.Raise):
        raise RuntimeError("historical Store closed-state raise shape changed")
    raised = precondition.body[0].exc
    if not isinstance(raised, ast.Call) or _name(raised.func) != "StoreError":
        raise RuntimeError("historical Store closed-state exception type changed")
    if len(raised.args) != 1 or not isinstance(raised.args[0], ast.Constant) or raised.args[0].value != "gateway store is closed":
        raise RuntimeError("historical Store closed-state error message changed")

    # Preserve exact historical transaction lifecycle before extracting it.
    if not isinstance(begin_stmt, ast.Expr) or not isinstance(begin_stmt.value, ast.Call):
        raise RuntimeError("historical BEGIN IMMEDIATE statement shape changed")
    begin_call = begin_stmt.value
    if _name(begin_call.func) != "self._connection.execute":
        raise RuntimeError("historical BEGIN IMMEDIATE connection target changed")
    if len(begin_call.args) != 1 or not isinstance(begin_call.args[0], ast.Constant) or begin_call.args[0].value != "BEGIN IMMEDIATE":
        raise RuntimeError("historical BEGIN IMMEDIATE SQL changed")

    if not isinstance(try_stmt, ast.Try):
        raise RuntimeError("historical transaction try block changed")
    if try_stmt.orelse or try_stmt.finalbody:
        raise RuntimeError("historical transaction unexpectedly has else/finally")
    if len(try_stmt.body) != 2:
        raise RuntimeError("historical transaction try-body shape changed")
    if not isinstance(try_stmt.body[0], ast.Expr) or not isinstance(try_stmt.body[0].value, ast.Yield):
        raise RuntimeError("historical transaction yield position changed")
    commit_stmt = try_stmt.body[1]
    if not isinstance(commit_stmt, ast.Expr) or not isinstance(commit_stmt.value, ast.Call):
        raise RuntimeError("historical COMMIT statement shape changed")
    commit_call = commit_stmt.value
    if _name(commit_call.func) != "self._connection.execute":
        raise RuntimeError("historical COMMIT connection target changed")
    if len(commit_call.args) != 1 or not isinstance(commit_call.args[0], ast.Constant) or commit_call.args[0].value != "COMMIT":
        raise RuntimeError("historical COMMIT SQL changed")

    if len(try_stmt.handlers) != 1:
        raise RuntimeError("historical transaction exception handler count changed")
    handler = try_stmt.handlers[0]
    if not isinstance(handler.type, ast.Name) or handler.type.id != "Exception":
        raise RuntimeError("historical rollback catch boundary changed")
    if len(handler.body) != 2:
        raise RuntimeError("historical rollback handler shape changed")
    rollback_stmt, reraise_stmt = handler.body
    if not isinstance(rollback_stmt, ast.Expr) or not isinstance(rollback_stmt.value, ast.Call):
        raise RuntimeError("historical ROLLBACK statement shape changed")
    rollback_call = rollback_stmt.value
    if _name(rollback_call.func) != "self._connection.execute":
        raise RuntimeError("historical ROLLBACK connection target changed")
    if len(rollback_call.args) != 1 or not isinstance(rollback_call.args[0], ast.Constant) or rollback_call.args[0].value != "ROLLBACK":
        raise RuntimeError("historical ROLLBACK SQL changed")
    if not isinstance(reraise_stmt, ast.Raise) or reraise_stmt.exc is not None:
        raise RuntimeError("historical transaction no-argument re-raise changed")

    lines = source.splitlines(keepends=True)
    start = begin_stmt.lineno - 1
    end = try_stmt.end_lineno
    replacement = [
        "        with gateway_store_write_transaction(self._connection):\n",
        "            yield\n",
    ]
    source = "".join(lines[:start] + replacement + lines[end:])
    STORE.write_text(source, encoding="utf-8", newline="\n")


def create_uow() -> None:
    write_new(
        UOW,
        '''from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom contextlib import contextmanager\nfrom typing import Protocol\n\n\nclass GatewayStoreWriteConnection(Protocol):\n    """Minimum connection surface required by the Gateway write UoW seam."""\n\n    def execute(self, sql: str, parameters: tuple[object, ...] = (), /) -> object:\n        ...\n\n\n@contextmanager\ndef gateway_store_write_transaction(\n    connection: GatewayStoreWriteConnection,\n) -> Iterator[None]:\n    """Own only the existing Gateway SQLite write-transaction lifecycle.\n\n    Locking, connection ownership, Store closed-state validation, schema, health\n    checks, and domain SQL remain responsibilities of ``GatewayStateStore``.\n    The control flow deliberately keeps COMMIT inside the try block so a COMMIT\n    failure follows the historical ROLLBACK path.\n    """\n\n    connection.execute("BEGIN IMMEDIATE")\n    try:\n        yield\n        connection.execute("COMMIT")\n    except Exception:\n        connection.execute("ROLLBACK")\n        raise\n''',
    )


def create_test() -> None:
    write_new(
        TEST,
        '''from __future__ import annotations\n\nimport ast\nimport importlib.util\nfrom pathlib import Path\nimport unittest\n\nROOT = Path(__file__).resolve().parents[1]\nSTORE_PATH = ROOT / "src/total_gateway/store.py"\nUOW_PATH = ROOT / "src/total_gateway/store_unit_of_work.py"\nGATE_PATH = ROOT / ".github/workflows/architecture-gate.yml"\n\n\ndef load_uow_module():\n    spec = importlib.util.spec_from_file_location("p17_m3_04_store_unit_of_work", UOW_PATH)\n    if spec is None or spec.loader is None:\n        raise RuntimeError("unable to load Gateway Store UoW seam")\n    module = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(module)\n    return module\n\n\nclass RecordingConnection:\n    def __init__(self, *, fail_on: str | None = None) -> None:\n        self.sql: list[str] = []\n        self.fail_on = fail_on\n\n    def execute(self, sql: str, parameters: tuple[object, ...] = ()):\n        del parameters\n        self.sql.append(sql)\n        if sql == self.fail_on:\n            self.fail_on = None\n            raise RuntimeError(f"{sql} failure")\n        return self\n\n\ndef find_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef:\n    for node in tree.body:\n        if isinstance(node, ast.ClassDef) and node.name == class_name:\n            for item in node.body:\n                if isinstance(item, ast.FunctionDef) and item.name == method_name:\n                    return item\n    raise AssertionError(f"missing {class_name}.{method_name}")\n\n\ndef expr_name(node: ast.AST) -> str:\n    if isinstance(node, ast.Name):\n        return node.id\n    if isinstance(node, ast.Attribute):\n        prefix = expr_name(node.value)\n        return f"{prefix}.{node.attr}" if prefix else node.attr\n    if isinstance(node, ast.Call):\n        return f"{expr_name(node.func)}()"\n    return type(node).__name__\n\n\nclass GatewayStoreUowBoundaryTests(unittest.TestCase):\n    def test_success_preserves_begin_immediate_then_commit(self) -> None:\n        module = load_uow_module()\n        connection = RecordingConnection()\n        with module.gateway_store_write_transaction(connection):\n            self.assertEqual(connection.sql, ["BEGIN IMMEDIATE"])\n        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "COMMIT"])\n\n    def test_body_failure_preserves_rollback_and_exception_identity(self) -> None:\n        module = load_uow_module()\n        connection = RecordingConnection()\n        marker = RuntimeError("rollback-marker")\n        with self.assertRaises(RuntimeError) as raised:\n            with module.gateway_store_write_transaction(connection):\n                raise marker\n        self.assertIs(raised.exception, marker)\n        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "ROLLBACK"])\n\n    def test_commit_failure_preserves_historical_rollback_path(self) -> None:\n        module = load_uow_module()\n        connection = RecordingConnection(fail_on="COMMIT")\n        with self.assertRaisesRegex(RuntimeError, "COMMIT failure"):\n            with module.gateway_store_write_transaction(connection):\n                pass\n        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"])\n\n    def test_uow_is_transaction_mechanics_only(self) -> None:\n        source = UOW_PATH.read_text(encoding="utf-8")\n        tree = ast.parse(source)\n        self.assertIn('connection.execute("BEGIN IMMEDIATE")', source)\n        self.assertIn('connection.execute("COMMIT")', source)\n        self.assertIn('connection.execute("ROLLBACK")', source)\n        self.assertNotIn("sqlite3.connect", source)\n        self.assertNotIn("self._lock", source)\n        for forbidden in (\n            "CREATE TABLE",\n            "CREATE INDEX",\n            "PRAGMA ",\n            "INSERT INTO",\n            "UPDATE ",\n            "DELETE FROM",\n            "SELECT ",\n            "GatewayStateStore(",\n        ):\n            self.assertNotIn(forbidden, source)\n        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]\n        self.assertEqual([node.name for node in classes], ["GatewayStoreWriteConnection"])
\n    def test_store_keeps_closed_state_authority_and_delegates_only_mechanics(self) -> None:\n        source = STORE_PATH.read_text(encoding="utf-8")\n        method = find_method(ast.parse(source), "GatewayStateStore", "_write_transaction")\n        rendered = ast.get_source_segment(source, method) or ""\n        self.assertIn("if self._closed:", rendered)\n        self.assertIn('raise StoreError("gateway store is closed")', rendered)\n        self.assertIn("gateway_store_write_transaction(self._connection)", rendered)\n        self.assertNotIn('execute("BEGIN IMMEDIATE")', rendered)\n        self.assertNotIn('execute("ROLLBACK")', rendered)\n        self.assertNotIn('execute("COMMIT")', rendered)\n\n    def test_all_write_transaction_callers_preserve_lock_before_uow(self) -> None:\n        source = STORE_PATH.read_text(encoding="utf-8")\n        tree = ast.parse(source)\n        callers = 0\n        for node in ast.walk(tree):\n            if not isinstance(node, ast.With):\n                continue\n            names = [expr_name(item.context_expr) for item in node.items]\n            if "self._write_transaction()" not in names:\n                continue\n            callers += 1\n            self.assertGreaterEqual(len(names), 2)\n            self.assertEqual(names[0], "self._lock")\n            self.assertEqual(names[1], "self._write_transaction()")\n        self.assertGreater(callers, 0)\n\n    def test_health_read_transaction_remains_store_owned(self) -> None:\n        source = STORE_PATH.read_text(encoding="utf-8")\n        health = find_method(ast.parse(source), "GatewayStateStore", "health_check")\n        rendered = ast.get_source_segment(source, health) or ""\n        self.assertIn('execute("BEGIN IMMEDIATE")', rendered)\n        self.assertIn('execute("ROLLBACK")', rendered)\n        self.assertNotIn("gateway_store_write_transaction", rendered)\n\n    def test_architecture_gate_covers_m3_04_and_compiles_seam(self) -> None:\n        gate = GATE_PATH.read_text(encoding="utf-8")\n        self.assertIn("Run P17 M3-04 Gateway Store UoW regression", gate)\n        self.assertIn('test_total_gateway_store_p17*.py', gate)\n        self.assertIn("src/total_gateway/store.py", gate)\n        self.assertIn("src/total_gateway/store_unit_of_work.py", gate)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    )


def patch_gate() -> None:
    text = GATE.read_text(encoding="utf-8")
    anchor = '''      - name: Run P17 M3-03 life memory repository regression\n        run: python tests/test_life_store_p17_m3_03.py -v\n\n'''
    addition = anchor + '''      - name: Run P17 M3-04 Gateway Store UoW regression\n        run: python -m unittest discover -s tests -p "test_total_gateway_store_p17*.py" -v\n\n'''
    text = replace_once(text, anchor, addition, label="architecture gate M3-04 test")
    old_compile = "src/life_service/store_memory_repository.py src/total_gateway/runtime.py"
    new_compile = "src/life_service/store_memory_repository.py src/total_gateway/runtime.py src/total_gateway/store.py src/total_gateway/store_unit_of_work.py"
    text = replace_once(text, old_compile, new_compile, label="architecture gate M3-04 compile")
    GATE.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    patch_store()
    create_uow()
    create_test()
    patch_gate()
    print("P17 M3-04 candidate patch v3-equivalent applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
