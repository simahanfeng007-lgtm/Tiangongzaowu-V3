from __future__ import annotations

import argparse
import hashlib
import marshal
import os
from pathlib import Path
import sys
import tempfile
import types


_INTERNAL_ROOT = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
)
DEFAULT_TARGETS = tuple(
    path
    for module_root in ("frozen_modules", "legacy_pyz_modules")
    for path in (
        _INTERNAL_ROOT / module_root / "v3" / "jineng" / "minimax_m3_adapter.pyc",
        _INTERNAL_ROOT / module_root / "v3" / "peizhi.pyc",
    )
)


def _timeout_owner(path: Path) -> str:
    return "ProviderConfig" if path.name == "peizhi.pyc" else "MiniMaxAdapter"


def _class_timeout(code: types.CodeType, owner: str) -> tuple[float, ...]:
    matches: list[float] = []
    if code.co_name == owner:
        matches.extend(value for value in code.co_consts if isinstance(value, float) and value >= 30.0)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            matches.extend(_class_timeout(value, owner))
    return tuple(matches)


def _replace_timeout(code: types.CodeType, owner: str, old: float, new: float) -> tuple[types.CodeType, int]:
    replacements = 0
    constants: list[object] = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            value, child_replacements = _replace_timeout(value, owner, old, new)
            replacements += child_replacements
        elif code.co_name == owner and type(value) is float and value == old:
            value = new
            replacements += 1
        constants.append(value)
    return code.replace(co_consts=tuple(constants)), replacements


def _load(path: Path) -> tuple[bytes, types.CodeType, bytes]:
    raw = path.read_bytes()
    if len(raw) < 17:
        raise RuntimeError("target pyc is truncated")
    try:
        code = marshal.loads(raw[16:])
    except Exception as exc:
        raise RuntimeError(
            "target pyc is not compatible with this interpreter; use the packaged Python 3.12 runtime"
        ) from exc
    if not isinstance(code, types.CodeType):
        raise RuntimeError("target pyc does not contain a module code object")
    return raw[:16], code, raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or patch the frozen MiniMax read timeout safely.")
    parser.add_argument(
        "--target",
        type=Path,
        action="append",
        help="Frozen adapter to inspect or patch. Repeat for multiple copies.",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--old", type=float, default=120.0)
    parser.add_argument("--new", type=float, default=300.0)
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Python 3.12 is required, got {sys.version_info.major}.{sys.version_info.minor}")
    targets = tuple(args.target or DEFAULT_TARGETS)
    for raw_target in targets:
        target = raw_target.expanduser().resolve(strict=True)
        owner = _timeout_owner(target)
        header, code, original = _load(target)
        before = _class_timeout(code, owner)
        if len(before) != 1:
            raise RuntimeError(f"{target}: expected one {owner} timeout constant, found {before!r}")
        if args.check:
            print(f"target={target}")
            print(f"timeout_seconds={before[0]:g}")
            print(f"sha256={hashlib.sha256(original).hexdigest()}")
            continue
        if before == (args.new,):
            print(f"target={target}")
            print(f"timeout_seconds={args.new:g} (already patched)")
            print(f"sha256={hashlib.sha256(original).hexdigest()}")
            continue
        if before != (args.old,):
            raise RuntimeError(f"{target}: refusing unexpected timeout value {before[0]!r}")

        patched_code, replacements = _replace_timeout(code, owner, args.old, args.new)
        if replacements != 1 or _class_timeout(patched_code, owner) != (args.new,):
            raise RuntimeError(f"{target}: timeout patch did not produce one verified replacement")
        patched = header + marshal.dumps(patched_code)
        mode = target.stat().st_mode
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(patched)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, target)
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass

        _, verified_code, verified = _load(target)
        if _class_timeout(verified_code, owner) != (args.new,):
            raise RuntimeError(f"{target}: post-write timeout verification failed")
        print(f"target={target}")
        print(f"timeout_seconds={args.old:g}->{args.new:g}")
        print(f"sha256_before={hashlib.sha256(original).hexdigest()}")
        print(f"sha256_after={hashlib.sha256(verified).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
