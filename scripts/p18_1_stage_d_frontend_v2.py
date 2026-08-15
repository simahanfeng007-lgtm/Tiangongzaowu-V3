"""Run the Stage-D renderer migration with named-function replacement fallback."""
from __future__ import annotations

from pathlib import Path

base = Path(__file__).resolve().parent / "p18_1_stage_d_frontend.py"
source = base.read_text(encoding="utf-8")

helper = r'''
def replace_js_named_function(text: str, name: str, replacement: str) -> str:
    needle = f"function {name}("
    start = text.find(needle)
    if start < 0:
        raise RuntimeError(f"named JS function missing: {name}")
    paren = text.find("(", start)
    depth = 0
    quote = None
    escape = False
    close = -1
    i = paren
    while i < len(text):
        ch = text[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in {'"', "'", '`'}:
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        i += 1
    if close < 0:
        raise RuntimeError(f"unterminated params: {name}")
    brace = text.find("{", close)
    depth = 0
    quote = None
    escape = False
    i = brace
    while i < len(text):
        ch = text[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in {'"', "'", '`'}:
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[:start] + replacement.strip() + text[i + 1:]
        i += 1
    raise RuntimeError(f"unterminated body: {name}")
'''

marker = 'text = PATH.read_text(encoding="utf-8")\n'
if marker not in source:
    raise RuntimeError("frontend migration marker missing")
source = source.replace(marker, helper + "\n" + marker, 1)
old_call = 'text = replace_once(text, old_render, new_render, "thinking renderer")'
new_call = 'text = replace_js_named_function(text, "renderThinkingInput", new_render)'
if source.count(old_call) != 1:
    raise RuntimeError("thinking renderer call marker missing")
source = source.replace(old_call, new_call, 1)

namespace = {"__file__": str(base), "__name__": "__main__"}
exec(compile(source, str(base), "exec"), namespace, namespace)
