from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "src" / "total_gateway" / "store.py"


def node_text(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


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
        report["classes"][node.name] = methods

    print(json.dumps(report, ensure_ascii=False, indent=2))

    print("\n=== external store references ===")
    patterns = (
        "GatewayStore",
        "SQLiteGatewayStore",
        "GatewayStateStore",
        "_connection",
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
