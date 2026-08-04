from __future__ import annotations

import argparse
import importlib.util
import marshal
import os
from pathlib import Path
import tempfile
import types


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "frozen_modules" / "v3" / "execution_kernel" / "tool_scheduler.pyc",
    ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "legacy_pyz_modules" / "v3" / "execution_kernel" / "tool_scheduler.pyc",
)
OLD_PATTERN = r"\$tool|\{\{.*result|previous[_ -]?result|待返回|上一步返回"
NEW_PATTERN = r"\$tool(?:\.[a-z0-9_.-]+)?|\{\{\s*(?:\$tool|tool_result|previous_result)(?:[.\s][^{}]*)?\}\}|previous[_ -]?result|待返回|上一步返回"


def _rewrite(code: types.CodeType) -> tuple[types.CodeType, int]:
    changed = 0
    constants = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            value, nested = _rewrite(value)
            changed += nested
        elif value == OLD_PATTERN:
            value = NEW_PATTERN
            changed += 1
        constants.append(value)
    return code.replace(co_consts=tuple(constants)), changed


def _load(path: Path) -> tuple[bytes, types.CodeType]:
    raw = path.read_bytes()
    if raw[:4] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError(f"{path} requires its matching Python interpreter")
    return raw, marshal.loads(raw[16:])


def _inspect(code: types.CodeType) -> tuple[bool, bool]:
    old = False
    new = False
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            nested_old, nested_new = _inspect(value)
            old = old or nested_old
            new = new or nested_new
        elif value == OLD_PATTERN:
            old = True
        elif value == NEW_PATTERN:
            new = True
    return old, new


def patch(path: Path) -> None:
    raw, code = _load(path)
    rewritten, changed = _rewrite(code)
    if changed == 0:
        old, new = _inspect(code)
        if old or not new:
            raise RuntimeError(f"tool-result dependency pattern was not found in {path}")
        return
    if changed != 1:
        raise RuntimeError(f"unexpected replacement count {changed} in {path}")
    data = raw[:16] + marshal.dumps(rewritten)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for target in TARGETS:
        if args.check:
            _, code = _load(target)
            old, new = _inspect(code)
            if old or not new:
                raise RuntimeError(f"tool-result dependency detector is stale in {target}")
        else:
            patch(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
