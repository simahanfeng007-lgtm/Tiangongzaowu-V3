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
    ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "frozen_modules" / "tiangong_life" / "permissions.pyc",
    ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "legacy_pyz_modules" / "tiangong_life" / "permissions.pyc",
)
EXISTING_MARKERS = frozenset({"skill.get", "skill.read", "skill.list", "file.read", "system.health"})
REQUIRED_META_ACTIONS = frozenset({"skill.route", "skill.step.check", "skill.progress.report"})


def _is_read_action_set(value: object) -> bool:
    return isinstance(value, frozenset) and EXISTING_MARKERS.issubset(value)


def _rewrite(code: types.CodeType) -> tuple[types.CodeType, int]:
    changed = 0
    constants = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            value, nested = _rewrite(value)
            changed += nested
        elif _is_read_action_set(value) and not REQUIRED_META_ACTIONS.issubset(value):
            value = frozenset(set(value) | set(REQUIRED_META_ACTIONS))
            changed += 1
        constants.append(value)
    return code.replace(co_consts=tuple(constants)), changed


def _load(path: Path) -> tuple[bytes, types.CodeType]:
    raw = path.read_bytes()
    if raw[:4] != importlib.util.MAGIC_NUMBER:
        raise RuntimeError(f"{path} requires its matching Python interpreter")
    return raw, marshal.loads(raw[16:])


def _inspect(code: types.CodeType) -> bool:
    for value in code.co_consts:
        if isinstance(value, types.CodeType) and _inspect(value):
            return True
        if _is_read_action_set(value):
            return REQUIRED_META_ACTIONS.issubset(value)
    return False


def patch(path: Path) -> None:
    raw, code = _load(path)
    rewritten, changed = _rewrite(code)
    if changed == 0:
        if not _inspect(code):
            raise RuntimeError(f"Skill read-action set was not found in {path}")
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
        raw, code = _load(target)
        del raw
        if args.check:
            if not _inspect(code):
                raise RuntimeError(f"Skill meta actions are not registered as read actions in {target}")
        else:
            patch(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
