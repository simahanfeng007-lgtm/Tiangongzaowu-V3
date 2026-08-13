from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "source-ownership.json"
base = subprocess.check_output(
    [
        "git",
        "show",
        "c5682695635139e4fea05fe4f6136f66891f1c66:source-ownership.json",
    ],
    cwd=ROOT,
    text=True,
    encoding="utf-8",
)
old = '"runtime_turn_orchestration.py","safe_io.py"'
new = '"runtime_turn_orchestration.py","runtime_tool_result_boundary.py","safe_io.py"'
if base.count(old) != 1:
    raise SystemExit(f"ownership raw anchor count={base.count(old)}")
path.write_text(base.replace(old, new, 1), encoding="utf-8")
print("M2-03 ownership formatting preserved")
