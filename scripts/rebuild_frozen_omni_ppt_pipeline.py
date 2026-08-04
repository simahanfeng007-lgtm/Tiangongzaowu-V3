"""Rebuild the Python 3.12 frozen mirrors for the Omni Body PPT pipeline."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import py_compile
import sys


MODULES = (
    ("tool_contracts.py", "omni_body_skill/tool_contracts.pyc"),
    ("tools/delivery_kernel.py", "omni_body_skill/tools/delivery_kernel.pyc"),
    ("tools/skill_router.py", "omni_body_skill/tools/skill_router.pyc"),
    ("tools/ppt_design.py", "omni_body_skill/tools/ppt_design.pyc"),
    ("tools/omni_body_tool.py", "omni_body_skill/tools/omni_body_tool.pyc"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"frozen Omni modules require Python 3.12, got {sys.version}")

    workspace = args.workspace.resolve()
    source_root = workspace / "app" / "backend" / "tiangong-backend" / "_internal" / "omni_body_skill"
    readable_root = workspace / "readable-python-source" / "omni_body_skill"
    internal = source_root.parent
    destinations = (internal / "frozen_modules", internal / "legacy_pyz_modules")
    compiled: list[dict[str, str | int]] = []

    for source_relative, output_relative in MODULES:
        source = source_root / source_relative
        readable = readable_root / source_relative
        if source.read_bytes() != readable.read_bytes():
            raise RuntimeError(f"source/readable mirror drift: {source_relative}")
        output_hashes: list[str] = []
        for destination in destinations:
            output = destination / output_relative
            output.parent.mkdir(parents=True, exist_ok=True)
            py_compile.compile(
                str(source),
                cfile=str(output),
                dfile=output_relative.replace(".pyc", ".py"),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
            )
            output_hashes.append(_sha256(output))
        if len(set(output_hashes)) != 1:
            raise RuntimeError(f"frozen/legacy bytecode drift: {output_relative}")
        compiled.append({"module": output_relative, "bytes": (destinations[0] / output_relative).stat().st_size, "sha256": output_hashes[0]})

    for item in compiled:
        print(f"{item['module']} {item['bytes']} {item['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
