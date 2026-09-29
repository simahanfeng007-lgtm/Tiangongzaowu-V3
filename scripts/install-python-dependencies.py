"""Install locked Python dependencies with an explicit TUNA fallback.

The caller's/default pip index is always attempted first.  Only a failed
installation is retried against the Tsinghua University PyPI mirror, so users
outside mainland China keep the normal upstream path and no indexes are mixed
within one resolver run.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Sequence

from embedded_python_integrity import audit_embedded_install


TUNA_PYPI_INDEX = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"


def _disabled() -> bool:
    return str(os.environ.get("TIANGONG_DISABLE_DEPENDENCY_FALLBACK") or "").strip() == "1"


def _run_pip(arguments: Sequence[str], *, env: dict[str, str] | None = None) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pip", *arguments],
        env=env,
        check=False,
    )
    return int(completed.returncode)


def install_with_fallback(arguments: Sequence[str], *, label: str) -> None:
    if _run_pip(arguments) == 0:
        return
    if _disabled():
        raise RuntimeError(f"{label} failed and dependency fallback is disabled")

    fallback = str(
        os.environ.get("TIANGONG_PYPI_FALLBACK_INDEX") or TUNA_PYPI_INDEX
    ).strip()
    if not fallback:
        raise RuntimeError(f"{label} failed and no PyPI fallback is configured")
    print(f"[dependency-fallback] {label}: retrying with {fallback}", flush=True)
    fallback_env = os.environ.copy()
    fallback_env["PIP_INDEX_URL"] = fallback
    # Keep each resolver run on one authority.  Combining indexes can select a
    # same-named package from the wrong repository.
    fallback_env.pop("PIP_EXTRA_INDEX_URL", None)
    if _run_pip(arguments, env=fallback_env) != 0:
        raise RuntimeError(f"{label} failed with both the primary and TUNA indexes")


def _embedded_build_pins(requirements: Path, project: Path) -> tuple[str, str]:
    """Use the existing source/build lock, not an unpinned build environment."""
    source_lock = requirements.parent / "requirements-source.lock"
    lines = source_lock.read_text(encoding="utf-8").splitlines()
    pins = {}
    for name in ("setuptools", "wheel"):
        matches = [line.strip() for line in lines if re.fullmatch(
            rf"{name}==\d+(?:\.\d+)+", line.strip(), re.IGNORECASE
        )]
        if len(matches) != 1:
            raise RuntimeError(f"Embedded Python requires exactly one pinned {name} in requirements-source.lock")
        pins[name] = matches[0]
    build_system = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["build-system"]
    if build_system["build-backend"] != "setuptools.build_meta" or pins["setuptools"] not in build_system["requires"]:
        raise RuntimeError("Embedded setuptools pin differs from the project's build-system requirement")
    return pins["setuptools"], pins["wheel"]


def _repair_embedded_integrity(requirements: Path, project: Path) -> None:
    """Reinstall damaged wheels; ordinary pip install trusts surviving dist-info."""
    site_packages = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
    audit = audit_embedded_install(site_packages, requirements, project)
    if not audit.problems:
        return
    print("[embedded-python] repairing incomplete installed packages", flush=True)
    common = ["--disable-pip-version-check", "install", "--force-reinstall", "--no-deps"]
    build_specs = [spec for spec in audit.repair_specs if spec.split("==", 1)[0].lower() in {"pip", "setuptools", "wheel"}]
    other_specs = [spec for spec in audit.repair_specs if spec not in build_specs]
    if build_specs:
        install_with_fallback([*common, "--only-binary=:all:", *build_specs], label="embedded Python damaged build packages")
    if other_specs:
        install_with_fallback([*common, "--no-build-isolation", *other_specs], label="embedded Python damaged packages")
    if audit.repair_project:
        install_with_fallback([*common, "--no-build-isolation", str(project)], label="embedded Python damaged project")
    remaining = audit_embedded_install(site_packages, requirements, project)
    if remaining.problems:
        raise RuntimeError("Embedded Python remains incomplete after repair: " + "; ".join(remaining.problems[:3]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--upgrade-pip", action="store_true")
    parser.add_argument("--embedded-python", action="store_true")
    args = parser.parse_args()

    if not args.requirements and not args.project and not args.upgrade_pip:
        parser.error("at least one install action is required")
    if args.embedded_python and (not args.requirements or not args.project):
        parser.error("--embedded-python requires --requirements and --project")
    common = ["--disable-pip-version-check"]
    requirements = args.requirements.resolve(strict=True) if args.requirements else None
    project = args.project.resolve(strict=True) if args.project else None
    build_pins: tuple[str, str] = ()
    if args.embedded_python:
        assert requirements is not None and project is not None
        build_pins = _embedded_build_pins(requirements, project)
    if args.upgrade_pip:
        install_with_fallback([*common, "install", "--upgrade", "pip"], label="pip upgrade")
    build_option: list[str] = []
    if build_pins:
        install_with_fallback(
            [*common, "install", "--only-binary=:all:", *build_pins],
            label="embedded Python build dependencies",
        )
        # python312._pth ignores PYTHONPATH, including pip's temporary build
        # environment. Build sdists with the pinned backend inside this isolated
        # embedded interpreter instead of the unreachable temporary environment.
        build_option = ["--no-build-isolation"]
    if requirements:
        install_with_fallback(
            [*common, "install", *build_option, "-r", str(requirements)],
            label=f"Python requirements {requirements.name}",
        )
    if project:
        install_with_fallback(
            [*common, "install", *build_option, "--no-deps", str(project)],
            label=f"Python project {project.name}",
        )
    if args.embedded_python:
        assert requirements is not None and project is not None
        _repair_embedded_integrity(requirements, project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
