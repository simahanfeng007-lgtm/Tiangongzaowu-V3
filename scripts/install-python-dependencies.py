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
import subprocess
import sys
from typing import Sequence


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--upgrade-pip", action="store_true")
    args = parser.parse_args()

    if not args.requirements and not args.project and not args.upgrade_pip:
        parser.error("at least one install action is required")
    common = ["--disable-pip-version-check"]
    if args.upgrade_pip:
        install_with_fallback([*common, "install", "--upgrade", "pip"], label="pip upgrade")
    if args.requirements:
        requirements = args.requirements.resolve(strict=True)
        install_with_fallback(
            [*common, "install", "-r", str(requirements)],
            label=f"Python requirements {requirements.name}",
        )
    if args.project:
        project = args.project.resolve(strict=True)
        install_with_fallback(
            [*common, "install", "--no-deps", str(project)],
            label=f"Python project {project.name}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
