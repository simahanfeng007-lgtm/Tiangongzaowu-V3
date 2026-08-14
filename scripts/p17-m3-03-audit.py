from __future__ import annotations

import ast
from pathlib import Path

path = Path("src/life_service/store.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LifeShadowStore")
for node in store.body:
    if isinstance(node, ast.FunctionDef):
        print(f"{node.lineno:05d}-{node.end_lineno:05d} {node.name}")
