from __future__ import annotations

import argparse
import hashlib
import marshal
import os
from pathlib import Path
import sys
import tempfile
import types


_INTERNAL = Path(__file__).resolve().parents[1] / "app" / "backend" / "tiangong-backend" / "_internal"
DEFAULT_TARGETS = tuple(
    _INTERNAL / module_root / "v3" / "execution_kernel" / "policy.pyc"
    for module_root in ("frozen_modules", "legacy_pyz_modules")
)


def _load(path: Path) -> tuple[bytes, types.CodeType, bytes]:
    raw = path.read_bytes()
    if len(raw) < 17:
        raise RuntimeError(f"{path}: truncated pyc")
    code = marshal.loads(raw[16:])
    if not isinstance(code, types.CodeType):
        raise RuntimeError(f"{path}: invalid module code")
    return raw[:16], code, raw


def _budget_values(code: types.CodeType) -> tuple[int, ...]:
    values: list[int] = []
    if code.co_name in {"ExecutionPolicy", "from_environment"}:
        values.extend(value for value in code.co_consts if type(value) is int and 100_000 <= value <= 200_000)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            values.extend(_budget_values(value))
    return tuple(values)


def _replace_budget(code: types.CodeType, old: int, new: int) -> tuple[types.CodeType, int]:
    replaced = 0
    constants: list[object] = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            value, child = _replace_budget(value, old, new)
            replaced += child
        elif code.co_name in {"ExecutionPolicy", "from_environment"} and type(value) is int and value == old:
            value = new
            replaced += 1
        constants.append(value)
    return code.replace(co_consts=tuple(constants)), replaced


def _write_atomic(path: Path, payload: bytes) -> None:
    mode = path.stat().st_mode
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch the frozen automatic context-compaction input budget.")
    parser.add_argument("--target", type=Path, action="append")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--old", type=int, default=160_000)
    parser.add_argument("--new", type=int, default=120_000)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Python 3.12 is required for these frozen modules")
    if not 32_000 <= args.new < args.old:
        raise RuntimeError("new budget must preserve useful context while remaining below the old hard edge")

    for raw in tuple(args.target or DEFAULT_TARGETS):
        target = raw.expanduser().resolve(strict=True)
        header, code, original = _load(target)
        before = _budget_values(code)
        if args.check:
            print(f"target={target}")
            print(f"context_estimated_token_budgets={before}")
            print(f"sha256={hashlib.sha256(original).hexdigest()}")
            continue
        if before == (args.new, args.new):
            print(f"target={target}")
            print(f"context_estimated_token_budget={args.new} (already patched)")
            continue
        if before != (args.old, args.old):
            raise RuntimeError(f"{target}: unexpected budget constants {before!r}")
        patched_code, count = _replace_budget(code, args.old, args.new)
        if count != 2 or _budget_values(patched_code) != (args.new, args.new):
            raise RuntimeError(f"{target}: budget replacement was not exact")
        _write_atomic(target, header + marshal.dumps(patched_code))
        _, verified_code, verified = _load(target)
        if _budget_values(verified_code) != (args.new, args.new):
            raise RuntimeError(f"{target}: post-write verification failed")
        print(f"target={target}")
        print(f"context_estimated_token_budget={args.old}->{args.new}")
        print(f"sha256_before={hashlib.sha256(original).hexdigest()}")
        print(f"sha256_after={hashlib.sha256(verified).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
