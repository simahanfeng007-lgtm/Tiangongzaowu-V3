#!/usr/bin/env python3
"""Rebuild the bundled interpreter's AppContainer-only private-directory fix.

Existing sitecustomize content is retained byte-for-byte outside our block.
The default targets only this source tree's app/runtime/python312. An explicit
CI option can target the current setup-python interpreter inside RUNNER_TOOL_CACHE;
it never implicitly modifies a developer's system or user Python installation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import sysconfig
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


def _ci_runtime_paths() -> tuple[Path, Path, Path]:
    if (os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "win32"
            or sys.version_info[:2] != (3, 12) or sys.flags.no_site):
        raise ValueError("CI compatibility install requires active Windows setup-python 3.12 with site enabled")
    cache_value = os.environ.get("RUNNER_TOOL_CACHE")
    if not cache_value:
        raise ValueError("CI compatibility install requires RUNNER_TOOL_CACHE")
    cache = Path(cache_value).resolve(strict=True)
    runtime = Path(sys.prefix).resolve(strict=True)
    executable = Path(sys.executable).resolve(strict=True)
    if (runtime == cache or not runtime.is_relative_to(cache)
            or Path(sys.base_prefix).resolve(strict=True) != runtime
            or executable.parent != runtime or executable.name.lower() != "python.exe"):
        raise ValueError("CI compatibility install must target the current tool-cache interpreter")
    site = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    if not site.is_dir() or not site.is_relative_to(runtime):
        raise ValueError("CI site-packages is outside the current tool-cache runtime")
    loaded = sys.modules.get("sitecustomize")
    loaded_path = getattr(loaded, "__file__", None) if loaded is not None else None
    if loaded is not None and not loaded_path:
        raise ValueError("CI sitecustomize has no file-backed identity")
    startup = Path(loaded_path) if loaded_path else site / "sitecustomize.py"
    if not startup.resolve(strict=False).is_relative_to(runtime):
        raise ValueError("CI sitecustomize is outside the current tool-cache runtime")
    if startup.suffix != ".py" or not any(
            Path(entry or os.getcwd()).resolve(strict=False) == startup.parent.resolve(strict=True)
            for entry in sys.path):
        raise ValueError("CI sitecustomize must be source in an active runtime import directory")
    return runtime, startup, startup.parent / "_tiangong_windows_python_compat.py"


def _bundled_runtime_paths(root: Path) -> tuple[Path, Path, Path]:
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
    return runtime, startup, runtime / "_tiangong_windows_python_compat.py"


def install(root: Path, *, check: bool = False, backup_dir: Path | None = None,
            ci_current_runtime: bool = False) -> dict:
    root = root.resolve(strict=True)
    runtime, startup, helper = (_ci_runtime_paths() if ci_current_runtime
                                else _bundled_runtime_paths(root))
    if startup.is_symlink() or helper.is_symlink():
        raise ValueError("runtime compatibility files must not be linked")
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
            "helper_sha256": hashlib.sha256(payload).hexdigest(), "check_only": check,
            "runtime_kind": "ci-current-tool-cache" if ci_current_runtime else "bundled"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--ci-current-runtime", action="store_true",
                        help="explicitly install only into this Windows GitHub Actions tool-cache interpreter")
    args = parser.parse_args()
    result = install(Path(__file__).resolve().parents[1], check=args.check, backup_dir=args.backup_dir,
                     ci_current_runtime=args.ci_current_runtime)
    if os.name == "nt":
        # Bootstrap the standalone native helper before the Body/backend import
        # graph is available; no alternative runtime implementation is loaded.
        source = Path(__file__).resolve().parents[1] / "src/omni_body_skill/tools/windows_appcontainer.py"
        spec = importlib.util.spec_from_file_location("_tg_runtime_access_setup", source)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        result["sandbox_runtime_access"] = helper.prepare_runtime_access(Path(result["runtime"]), check=args.check)
        result["ok"] = result["ok"] and result["sandbox_runtime_access"]["ok"]
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
