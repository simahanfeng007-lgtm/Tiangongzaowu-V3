from __future__ import annotations

import runpy
from pathlib import Path

legacy_builder = Path(__file__).with_name("p17-m2-05-candidate-v1.py")
runpy.run_path(str(legacy_builder), run_name="__main__")

runtime = Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "runtime.py"
text = runtime.read_text(encoding="utf-8")
normalized = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
compile(normalized, str(runtime), "exec")
runtime.write_text(normalized, encoding="utf-8", newline="\n")

# Construction-only helper: remove it from the working tree so candidate
# collection/diff reflects only formal M2-05 files.
legacy_builder.unlink()
print("P17-M2-05 trailing whitespace normalized")
