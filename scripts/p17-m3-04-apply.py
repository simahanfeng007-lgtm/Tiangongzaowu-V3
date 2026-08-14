from __future__ import annotations

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


def patch_store() -> None:
    text = STORE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import sqlite3\n",
        "import sqlite3\n\nfrom .store_unit_of_work import gateway_store_write_transaction\n",
        label="store UoW import",
    )
    old = '''    @contextmanager\n    def _write_transaction(self) -> Iterator[None]:\n        if self._connection is None:\n            raise StructuredStoreError(\n                "STORE_NOT_OPEN",\n                "store must be opened before running write transactions",\n            )\n        self._connection.execute("BEGIN IMMEDIATE")\n        try:\n            yield\n        except Exception:\n            self._connection.execute("ROLLBACK")\n            raise\n        else:\n            self._connection.execute("COMMIT")\n'''
    new = '''    @contextmanager\n    def _write_transaction(self) -> Iterator[None]:\n        if self._connection is None:\n            raise StructuredStoreError(\n                "STORE_NOT_OPEN",\n                "store must be opened before running write transactions",\n            )\n        with gateway_store_write_transaction(self._connection):\n            yield\n'''
    text = replace_once(text, old, new, label="store write transaction facade")
    STORE.write_text(text, encoding="utf-8", newline="\n")


def create_uow() -> None:
    write_new(
        UOW,
        '''from __future__ import annotations\n\nfrom collections.abc import Iterator\nfrom contextlib import contextmanager\nimport sqlite3\n\n\n@contextmanager\ndef gateway_store_write_transaction(\n    connection: sqlite3.Connection,\n) -> Iterator[None]:\n    """Own only the existing Gateway SQLite write-transaction lifecycle.\n\n    Locking, connection ownership, Store-open validation, schema, health checks,\n    and domain SQL remain responsibilities of ``GatewayStateStore``.\n    """\n\n    connection.execute("BEGIN IMMEDIATE")\n    try:\n        yield\n    except Exception:\n        connection.execute("ROLLBACK")\n        raise\n    else:\n        connection.execute("COMMIT")\n''',
    )


def create_test() -> None:
    write_new(
        TEST,
        '''from __future__ import annotations\n\nimport ast\nimport importlib.util\nfrom pathlib import Path\nimport unittest\n\nROOT = Path(__file__).resolve().parents[1]\nSTORE_PATH = ROOT / "src/total_gateway/store.py"\nUOW_PATH = ROOT / "src/total_gateway/store_unit_of_work.py"\nGATE_PATH = ROOT / ".github/workflows/architecture-gate.yml"\n\n\ndef load_uow_module():\n    spec = importlib.util.spec_from_file_location("p17_m3_04_store_unit_of_work", UOW_PATH)\n    if spec is None or spec.loader is None:\n        raise RuntimeError("unable to load Gateway Store UoW seam")\n    module = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(module)\n    return module\n\n\nclass RecordingConnection:\n    def __init__(self) -> None:\n        self.sql: list[str] = []\n\n    def execute(self, sql: str):\n        self.sql.append(sql)\n        return self\n\n\ndef find_method(tree: ast.AST, class_name: str, method_name: str) -> ast.FunctionDef:\n    for node in ast.walk(tree):\n        if isinstance(node, ast.ClassDef) and node.name == class_name:\n            for item in node.body:\n                if isinstance(item, ast.FunctionDef) and item.name == method_name:\n                    return item\n    raise AssertionError(f"missing {class_name}.{method_name}")\n\n\nclass GatewayStoreUowBoundaryTests(unittest.TestCase):\n    def test_success_preserves_begin_immediate_then_commit(self) -> None:\n        module = load_uow_module()\n        connection = RecordingConnection()\n        with module.gateway_store_write_transaction(connection):\n            self.assertEqual(connection.sql, ["BEGIN IMMEDIATE"])\n        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "COMMIT"])\n\n    def test_exception_preserves_rollback_and_identity(self) -> None:\n        module = load_uow_module()\n        connection = RecordingConnection()\n        marker = RuntimeError("rollback-marker")\n        with self.assertRaises(RuntimeError) as raised:\n            with module.gateway_store_write_transaction(connection):\n                raise marker\n        self.assertIs(raised.exception, marker)\n        self.assertEqual(connection.sql, ["BEGIN IMMEDIATE", "ROLLBACK"])\n\n    def test_uow_is_transaction_lifecycle_only(self) -> None:\n        source = UOW_PATH.read_text(encoding="utf-8")\n        self.assertIn('connection.execute("BEGIN IMMEDIATE")', source)\n        self.assertIn('connection.execute("ROLLBACK")', source)\n        self.assertIn('connection.execute("COMMIT")', source)\n        for forbidden in (\n            "RLock",\n            "Lock(",\n            "sqlite3.connect",\n            "CREATE TABLE",\n            "CREATE INDEX",\n            "PRAGMA ",\n            "INSERT INTO",\n            "UPDATE ",\n            "DELETE FROM",\n            "SELECT ",\n        ):\n            self.assertNotIn(forbidden, source)\n\n    def test_store_keeps_open_precondition_and_delegates_transaction(self) -> None:\n        source = STORE_PATH.read_text(encoding="utf-8")\n        tree = ast.parse(source)\n        method = find_method(tree, "GatewayStateStore", "_write_transaction")\n        rendered = ast.get_source_segment(source, method) or ""\n        self.assertIn('"STORE_NOT_OPEN"', rendered)\n        self.assertIn("gateway_store_write_transaction(self._connection)", rendered)\n        self.assertNotIn('execute("BEGIN IMMEDIATE")', rendered)\n        self.assertNotIn('execute("ROLLBACK")', rendered)\n        self.assertNotIn('execute("COMMIT")', rendered)\n\n    def test_lock_order_and_health_read_transaction_remain_store_owned(self) -> None:\n        source = STORE_PATH.read_text(encoding="utf-8")\n        self.assertGreater(source.count("with self._lock, self._write_transaction():"), 0)\n        self.assertNotIn("self._lock", UOW_PATH.read_text(encoding="utf-8"))\n        health = find_method(ast.parse(source), "GatewayStateStore", "health_check")\n        rendered = ast.get_source_segment(source, health) or ""\n        self.assertIn('execute("BEGIN")', rendered)\n\n    def test_architecture_gate_covers_m3_04_and_compiles_seam(self) -> None:\n        gate = GATE_PATH.read_text(encoding="utf-8")\n        self.assertIn("Run P17 M3-04 Gateway Store UoW regression", gate)\n        self.assertIn('test_total_gateway_store_p17*.py', gate)\n        self.assertIn("src/total_gateway/store_unit_of_work.py", gate)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
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
    print("P17 M3-04 candidate patch applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
