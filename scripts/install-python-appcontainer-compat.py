#!/usr/bin/env python3
"""Rebuild the bundled interpreter's AppContainer-only private-directory fix.

Existing sitecustomize content is retained byte-for-byte outside our block.
This installer only targets this source tree's app/runtime/python312.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

BEGIN = b"# BEGIN TIANGONG APPCONTAINER PRIVATE DIRECTORY COMPAT\n"
END = b"# END TIANGONG APPCONTAINER PRIVATE DIRECTORY COMPAT\n"
BLOCK = BEGIN + (b"from _tiangong_windows_python_compat import install as _tg_install_private_acl\n"
                 b"_tg_install_private_acl()\n"
                 b"del _tg_install_private_acl\n") + END


def _atomic(path: Path, data: bytes) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("runtime compatibility target must not be linked")
    descriptor, name = tempfile.mkstemp(prefix=".tg-python-compat-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(root: Path, *, check: bool = False, backup_dir: Path | None = None) -> dict:
    root = root.resolve(strict=True)
    runtime = root / "app/runtime/python312"
    settings = runtime / "python312._pth"
    entries = settings.read_text(encoding="utf-8-sig").splitlines()
    if "import site" not in [line.strip() for line in entries]:
        raise ValueError("bundled Python must enable its site startup")
    paths = [runtime / line.strip() for line in entries
             if line.strip() and not line.strip().startswith(("#", "import "))]
    existing = [item / "sitecustomize.py" for item in paths if (item / "sitecustomize.py").is_file()]
    startup = existing[0] if existing else runtime / "sitecustomize.py"
    if not startup.resolve(strict=False).is_relative_to(runtime.resolve()):
        raise ValueError("existing sitecustomize is outside the bundled runtime")
    if startup.is_symlink():
        raise ValueError("sitecustomize must not be linked")
    old = startup.read_bytes() if startup.exists() else b""
    # Accept a block materialized with either native newline convention while
    # leaving any pre-existing application startup logic untouched.
    start_marker, end_marker = BEGIN.rstrip(b"\n"), END.rstrip(b"\n")
    if old.count(start_marker) or old.count(end_marker):
        if old.count(start_marker) != 1 or old.count(end_marker) != 1:
            raise ValueError("runtime compatibility markers are malformed")
        start, end = old.index(start_marker), old.index(end_marker)
        if end < start:
            raise ValueError("runtime compatibility markers are malformed")
        end += len(end_marker)
        if old[end:end+2] == b"\r\n": end += 2
        elif old[end:end+1] == b"\n": end += 1
        updated = old[:start] + BLOCK + old[end:]
    else:
        updated = old + (b"\n" if old and not old.endswith(b"\n") else b"") + BLOCK
    canonical = root / "src/omni_body_skill/tools/windows_python_compat.py"
    helper = runtime / "_tiangong_windows_python_compat.py"
    payload = canonical.read_bytes()
    current_helper = helper.read_bytes() if helper.exists() else None
    matched = updated == old and current_helper == payload
    if not check:
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            for path in (startup, helper):
                if path.exists():
                    backup = backup_dir / (path.name + "." + hashlib.sha256(path.read_bytes()).hexdigest()[:12] + ".bak")
                    if not backup.exists(): backup.write_bytes(path.read_bytes())
        if current_helper != payload: _atomic(helper, payload)
        if updated != old: _atomic(startup, updated)
        matched = True
    return {"ok": matched, "runtime": str(runtime), "sitecustomize": str(startup),
            "helper_sha256": hashlib.sha256(payload).hexdigest(), "check_only": check}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    result = install(Path(__file__).resolve().parents[1], check=args.check, backup_dir=args.backup_dir)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
