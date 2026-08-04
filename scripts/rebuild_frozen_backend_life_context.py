"""Rebuild the frozen 7174 entry with the source-owned life authority consumer.

Run this script with CPython 3.12, matching the embedded backend runtime.
"""

from __future__ import annotations

import hashlib
import importlib.util
import marshal
import os
from pathlib import Path
import sys
from types import CodeType


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "backend_entry_patched_v2.pyc"
SOURCE = ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "backend_life_context_authority.py"


def _function_code(source: str) -> CodeType:
    module = compile(source, str(SOURCE), "exec", dont_inherit=True, optimize=0)
    matches = [
        value
        for value in module.co_consts
        if isinstance(value, CodeType) and value.co_name == "_compile_life_context"
    ]
    if len(matches) != 1:
        raise RuntimeError("authority source must define exactly one _compile_life_context")
    return matches[0]


def _replace(module: CodeType, replacement: CodeType) -> CodeType:
    replaced = 0

    def visit(code: CodeType) -> CodeType:
        nonlocal replaced
        if code.co_name == "_compile_life_context":
            replaced += 1
            return replacement
        constants = []
        changed = False
        for value in code.co_consts:
            new_value = visit(value) if isinstance(value, CodeType) else value
            if new_value == "tiangong.backend-life-skill-bridge.v4":
                new_value = "tiangong.backend-life-skill-bridge.v5"
            elif new_value == "backend_compiler":
                new_value = "gateway_or_scheduler_authority"
            changed = changed or new_value is not value
            constants.append(new_value)
        return code.replace(co_consts=tuple(constants)) if changed else code

    rebuilt = visit(module)
    if replaced != 1:
        raise RuntimeError(f"expected one frozen _compile_life_context, found {replaced}")
    return rebuilt


def main() -> int:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("the frozen backend entry must be rebuilt with CPython 3.12")
    raw = TARGET.read_bytes()
    if len(raw) < 17 or raw[:4] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError("the frozen backend bytecode does not match this interpreter")
    module = marshal.loads(raw[16:])
    if not isinstance(module, CodeType):
        raise RuntimeError("the frozen backend bytecode has no module code object")
    replacement = _function_code(SOURCE.read_text(encoding="utf-8"))
    if "_life_post" in replacement.co_names:
        raise RuntimeError("the authority consumer must not call the life compiler")
    rebuilt = _replace(module, replacement)
    output = raw[:16] + marshal.dumps(rebuilt)
    marshal.loads(output[16:])
    before = hashlib.sha256(raw).hexdigest()
    after = hashlib.sha256(output).hexdigest()
    temporary = TARGET.with_suffix(".pyc.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, TARGET)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"rebuilt={TARGET}")
    print(f"sha256_before={before}")
    print(f"sha256_after={after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
