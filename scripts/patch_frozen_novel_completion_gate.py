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
    ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "frozen_modules" / "v3" / "execution_kernel" / "orchestrator.pyc",
    ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "legacy_pyz_modules" / "v3" / "execution_kernel" / "orchestrator.pyc",
)
OLD_PATTERN = "(?:写|撰写|创作|编写|续写).{0,40}(?:小说|章节|正文)|(?:小说|章节|正文).{0,40}(?:写|撰写|创作|编写|续写)|(?:write|draft|create).{0,40}(?:novel|chapter)"
NEW_PATTERN = "(?:写|撰写|创作|编写|续写).{0,40}(?:小说|网文|故事章节)|(?:小说|网文|故事章节).{0,40}(?:写|撰写|创作|编写|续写)|(?:write|draft|create).{0,40}(?:novel|fiction chapter)"


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


def _inspect(path: Path) -> tuple[bool, bool]:
    with path.open("rb") as stream:
        magic = stream.read(4)
        if magic != importlib.util.MAGIC_NUMBER:
            raise RuntimeError(f"{path} requires its matching Python interpreter")
        stream.read(12)
        code = marshal.load(stream)
    found_old = False
    found_new = False

    def walk(item: types.CodeType) -> None:
        nonlocal found_old, found_new
        for value in item.co_consts:
            if isinstance(value, types.CodeType):
                walk(value)
            elif value == OLD_PATTERN:
                found_old = True
            elif value == NEW_PATTERN:
                found_new = True

    walk(code)
    return found_old, found_new


def patch(path: Path) -> None:
    raw = path.read_bytes()
    if raw[:4] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError(f"{path} requires its matching Python interpreter")
    code = marshal.loads(raw[16:])
    rewritten, changed = _rewrite(code)
    if changed == 0:
        old, new = _inspect(path)
        if old or not new:
            raise RuntimeError(f"novel completion pattern was not found in {path}")
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
            old, new = _inspect(target)
            if old or not new:
                raise RuntimeError(f"novel completion gate is stale in {target}")
        else:
            patch(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
