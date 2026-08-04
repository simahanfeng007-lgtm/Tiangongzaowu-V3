"""Launch Total Gateway from the selected source tree under embedded Python.

The embeddable Windows runtime is isolated by ``python312._pth`` and therefore
ignores ``PYTHONPATH``.  This bootstrap is the explicit source-mode boundary:
it inserts only the configured source root, verifies the resolved module, and
then executes its package entry point.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


def main() -> None:
    raw_source = os.environ.get("TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT", "")
    source_root = Path(raw_source).expanduser().resolve(strict=True)
    entry = source_root / "total_gateway" / "__main__.py"
    if not entry.is_file():
        raise RuntimeError("source_total_gateway.entry_missing")
    sys.path.insert(0, str(source_root))
    runpy.run_module("total_gateway", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
