from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
TERMS = (
    "total_gateway",
    "GatewayStateStore",
    "STORE_SCHEMA_VERSION",
    "gateway_store",
    "request_continuity",
    "outbox",
    "lease",
    "effect",
    "compare_and_swap",
)

for path in sorted(TESTS.rglob("test_*.py")):
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = [term for term in TERMS if term in text]
    if not hits:
        continue
    import_lines = [
        line.strip() for line in text.splitlines()
        if ("import" in line and ("total_gateway" in line or "store" in line.lower()))
    ]
    test_names = re.findall(r"^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", text, flags=re.MULTILINE)
    print(f"FILE {path.relative_to(ROOT)}")
    print(f"  HITS {','.join(hits)}")
    for line in import_lines[:12]:
        print(f"  IMPORT {line}")
    for name in test_names[:80]:
        print(f"  TEST {name}")
