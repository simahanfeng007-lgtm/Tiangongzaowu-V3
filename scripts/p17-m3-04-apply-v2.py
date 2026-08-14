from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "src/total_gateway/store.py"
V1 = ROOT / "scripts/p17-m3-04-apply.py"


def load_v1():
    spec = importlib.util.spec_from_file_location("p17_m3_04_apply_v1", V1)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load M3-04 candidate helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "_write_transaction":
                matches.append(item)
    if len(matches) != 1:
        raise RuntimeError(f"expected one class-owned _write_transaction, found {len(matches)}")
    method = matches[0]
    if not any(isinstance(dec, ast.Name) and dec.id == "contextmanager" for dec in method.decorator_list):
        raise RuntimeError("_write_transaction lost historical @contextmanager contract")
    if len(method.body) != 3:
        raise RuntimeError(f"unexpected _write_transaction top-level statement count: {len(method.body)}")

    precondition, begin_stmt, try_stmt = method.body
    precondition_text = ast.get_source_segment(source, precondition) or ""
    begin_text = ast.get_source_segment(source, begin_stmt) or ""
    try_text = ast.get_source_segment(source, try_stmt) or ""
    if "STORE_NOT_OPEN" not in precondition_text or "store must be opened before running write transactions" not in precondition_text:
        raise RuntimeError("Store-owned open precondition changed")
    if 'execute("BEGIN IMMEDIATE")' not in begin_text:
        raise RuntimeError("historical BEGIN IMMEDIATE statement not found")
    if not isinstance(try_stmt, ast.Try) or 'execute("ROLLBACK")' not in try_text or 'execute("COMMIT")' not in try_text:
        raise RuntimeError("historical COMMIT/ROLLBACK lifecycle not found")
    if len(try_stmt.handlers) != 1 or try_stmt.handlers[0].type is None:
        raise RuntimeError("historical transaction exception handler changed")
    handler_type = ast.get_source_segment(source, try_stmt.handlers[0].type) or ""
    if handler_type != "Exception":
        raise RuntimeError(f"historical rollback catch boundary changed: {handler_type}")

    lines = source.splitlines(keepends=True)
    start = begin_stmt.lineno - 1
    end = try_stmt.end_lineno
    replacement = [
        "        with gateway_store_write_transaction(self._connection):\n",
        "            yield\n",
    ]
    source = "".join(lines[:start] + replacement + lines[end:])
    STORE.write_text(source, encoding="utf-8", newline="\n")


def main() -> int:
    helpers = load_v1()
    patch_store()
    helpers.create_uow()
    helpers.create_test()
    helpers.patch_gate()
    print("P17 M3-04 candidate patch v2 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
