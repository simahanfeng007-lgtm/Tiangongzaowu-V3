from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".venv", "runtime", "node_modules", ".pytest_cache", "__pycache__", "release-artifacts", "release-stage"}


def source_files(suffix: str):
    for path in ROOT.rglob(f"*{suffix}"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.is_file():
            yield path


def verify_python() -> int:
    failures = []
    count = 0
    for path in source_files(".py"):
        count += 1
        try:
            ast.parse(path.read_text(encoding="utf-8-sig", errors="strict"), filename=str(path))
        except Exception as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        raise RuntimeError("Python syntax failures:\n" + "\n".join(failures))
    return count


def verify_javascript() -> int:
    node = "node.exe" if os.name == "nt" else "node"
    count = 0
    failures = []
    for suffix in (".js", ".mjs", ".cjs"):
        for path in source_files(suffix):
            count += 1
            completed = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            if completed.returncode:
                failures.append(f"{path.relative_to(ROOT)}: {(completed.stderr or completed.stdout).strip()}")
    if failures:
        raise RuntimeError("JavaScript syntax failures:\n" + "\n".join(failures))
    return count


def verify_imports() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "app" / "backend" / "tiangong-backend"))
    import communication_service  # noqa: F401
    import total_gateway  # noqa: F401
    import v3.desktop_daemon  # noqa: F401
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    import tempfile
    with tempfile.TemporaryDirectory(prefix="tiangong-source-verify-") as temporary:
        runtime = BodyRuntime(BodyRuntimeConfig(workspace=temporary, require_confirmation_for_a4=False))
        result = runtime.run("system.action_schema", "docx.create", {})
        if result.get("success") is not True or result.get("executable") is not True:
            raise RuntimeError("Omni Body capability manifest is not executable")




def verify_cross_platform_source() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_cross_platform_source.py"), "--root", str(ROOT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "cross-platform source verification failed").strip()
        raise RuntimeError("Cross-platform source verification failed:\n" + detail)


def verify_generated_sources() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync-generated-sources.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "generated source mirror drift").strip()
        raise RuntimeError("Generated source verification failed:\n" + detail)


def verify_release_manifest() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from total_gateway.release_manifest import generate_release_manifest
    manifest = generate_release_manifest(ROOT)
    if manifest.production_claim is not False or len(manifest.component_manifest.components) != 5:
        raise RuntimeError("source release manifest is inconsistent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    py_count = verify_python()
    js_count = verify_javascript()
    verify_imports()
    verify_cross_platform_source()
    verify_generated_sources()
    verify_release_manifest()
    summary = {
        "ok": True,
        "python_files": py_count,
        "javascript_files": js_count,
        "full_tests": not args.quick,
    }
    if not args.quick:
        # On Windows, os.execv is implemented as spawn-and-exit rather than a
        # true process replacement.  When the verifier itself is hosted by a
        # redirected PowerShell pipeline, that path can leave pytest with an
        # invalid stdout handle and still let the wrapper report success.
        # Launch pytest explicitly and return its exact status so release
        # automation cannot turn a broken test runner into a false green gate.
        summary["phase"] = "preflight_passed_running_full_tests"
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        test_roots = ["tests"]
        bundled_skill_tests = ROOT / "app/backend/tiangong-backend/v3/bundled_skills/omni_body_skill/tests"
        if bundled_skill_tests.is_dir():
            test_roots.append(str(bundled_skill_tests.relative_to(ROOT)))
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-vv", "--maxfail=1", *test_roots],
            cwd=ROOT,
        )
        return int(completed.returncode)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
