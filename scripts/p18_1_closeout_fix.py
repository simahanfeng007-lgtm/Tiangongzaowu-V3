"""One-shot P18.1 closeout migration. Safe to run repeatedly."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "app/backend/tiangong-backend/v3/duihua_qiaojie.py"

OLD = '''    service_preset = normalize_service_preset(\n        service_value or current_input.get("service_preset") or "custom",\n        raw_provider or current_identity,\n    )\n'''
NEW = '''    # Legacy callers may provide only a historical provider/family identity.\n    # Do not pre-empt provider->service alias resolution with an eager custom\n    # default; an explicit service_preset still remains authoritative.\n    service_preset = normalize_service_preset(\n        service_value or current_input.get("service_preset") or "",\n        raw_provider or current_identity,\n    )\n'''

text = BRIDGE.read_text(encoding="utf-8")
if OLD in text:
    text = text.replace(OLD, NEW, 1)
elif NEW not in text:
    raise RuntimeError("P18.1 closeout seam not found")
BRIDGE.write_text(text, encoding="utf-8")
print("P18.1 closeout compatibility migration applied")
