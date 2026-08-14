from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "src" / "total_gateway" / "store.py"


def node_text(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _expr_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _expr_name(node.func) + "()"
    return type(node).__name__


def main() -> None:
    source = STORE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    report: dict[str, object] = {
        "store_lines": len(lines),
        "top_level": [],
        "classes": {},
        "transaction_methods": [],
        "connection_methods": [],
        "contextmanagers": [],
        "write_transaction_callers": [],
        "lock_users": [],
    }

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            report["top_level"].append({"name": node.name, "kind": type(node).__name__, "line": node.lineno})
        if not isinstance(node, ast.ClassDef):
            continue
        methods = []
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            text = node_text(source, item)
            sql_ops = len(re.findall(r"\.(?:execute|executemany)\(", text))
            begins = len(re.findall(r"BEGIN(?: IMMEDIATE)?", text))
            commits = len(re.findall(r"\.commit\(\)|\bCOMMIT\b", text))
            rollbacks = len(re.findall(r"\.rollback\(\)|\bROLLBACK\b", text))
            has_connection = "_connection" in text or "connection" in text
            decorated_contextmanager = any(
                isinstance(dec, ast.Name) and dec.id == "contextmanager"
                for dec in item.decorator_list
            )
            entry = {
                "name": item.name,
                "line": item.lineno,
                "end_line": getattr(item, "end_lineno", item.lineno),
                "sql_ops": sql_ops,
                "begin": begins,
                "commit": commits,
                "rollback": rollbacks,
                "connection": has_connection,
                "contextmanager": decorated_contextmanager,
            }
            methods.append(entry)
            if begins or commits or rollbacks:
                report["transaction_methods"].append({"class": node.name, **entry})
            if has_connection and sql_ops:
                report["connection_methods"].append({"class": node.name, **entry})
            if decorated_contextmanager:
                report["contextmanagers"].append({"class": node.name, **entry})

            transaction_withs: list[dict[str, object]] = []
            lock_withs: list[dict[str, object]] = []
            for child in ast.walk(item):
                if not isinstance(child, ast.With):
                    continue
                names = tuple(_expr_name(with_item.context_expr) for with_item in child.items)
                if "self._write_transaction()" in names:
                    transaction_withs.append({"line": child.lineno, "items": names})
                if "self._lock" in names:
                    lock_withs.append({"line": child.lineno, "items": names})
            if transaction_withs:
                report["write_transaction_callers"].append({
                    "method": item.name,
                    "line": item.lineno,
                    "withs": transaction_withs,
                })
            if lock_withs:
                report["lock_users"].append({
                    "method": item.name,
                    "line": item.lineno,
                    "withs": lock_withs,
                })
        report["classes"][node.name] = methods

    print("=== transaction summary ===")
    print(json.dumps({
        "store_lines": report["store_lines"],
        "transaction_methods": report["transaction_methods"],
        "contextmanagers": report["contextmanagers"],
        "write_transaction_callers": report["write_transaction_callers"],
        "lock_users": report["lock_users"],
    }, ensure_ascii=False, indent=2))

    print("\n=== external store references ===")
    patterns = (
        "GatewayStore",
        "SQLiteGatewayStore",
        "GatewayStateStore",
        "_connection",
        "_write_transaction",
        "._lock",
        "store.transaction",
        "store._",
    )
    for path in sorted((ROOT / "src").rglob("*.py")):
        if path == STORE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = [pattern for pattern in patterns if pattern in text]
        if not hits:
            continue
        print(f"{path.relative_to(ROOT)} :: {','.join(hits)}")


if __name__ == "__main__":
    main()
