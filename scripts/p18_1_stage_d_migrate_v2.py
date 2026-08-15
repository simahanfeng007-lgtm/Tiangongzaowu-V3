"""Stage-D migration runner with a JS scanner that handles destructured params."""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("p181_stage_d_base", HERE / "p18_1_stage_d_migrate.py")
BASE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BASE)


def fixed_find_js_function(source: str, name: str) -> tuple[int, int]:
    needles = [f"async function {name}(", f"function {name}("]
    start = -1
    for needle in needles:
        candidate = source.find(needle)
        if candidate >= 0:
            start = candidate
            break
    if start < 0:
        raise RuntimeError(f"JS function not found: {name}")

    paren = source.find("(", start)
    depth = 0
    quote = None
    escape = False
    i = paren
    close_paren = -1
    while i < len(source):
        ch = source[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in {'"', "'", "`"}:
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_paren = i
                    break
        i += 1
    if close_paren < 0:
        raise RuntimeError(f"JS function parameter list unterminated: {name}")

    brace = source.find("{", close_paren)
    if brace < 0:
        raise RuntimeError(f"JS function body not found: {name}")
    depth = 0
    quote = None
    escape = False
    i = brace
    while i < len(source):
        ch = source[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in {'"', "'", "`"}:
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    raise RuntimeError(f"unterminated JS function: {name}")


BASE.find_js_function = fixed_find_js_function
BASE.main()
