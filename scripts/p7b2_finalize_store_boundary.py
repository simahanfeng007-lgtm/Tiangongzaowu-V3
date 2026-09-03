from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARDENING_SCRIPT = ROOT / "scripts/p7b2_harden_store_write_boundary.py"
TEST_PATH = ROOT / "tests/test_composition_activation_store_p7b2.py"

if not HARDENING_SCRIPT.is_file():
    raise RuntimeError("missing P7B.2 hardening script")

# Apply the already-reviewed Store hardening first.
runpy.run_path(str(HARDENING_SCRIPT), run_name="__main__")

# The rollback regression must inject failure at the new private sink rather
# than at the public port that the hardening deliberately removes.
test = TEST_PATH.read_text(encoding="utf-8")
old = '''            with mock.patch.object(
                GatewayStateStore,
                "put_limited_activation_registration",
                side_effect=RuntimeError("forced registration failure"),
            ):
'''
new = '''            with mock.patch.object(
                GatewayStateStore,
                "_put_limited_activation_registration_from_bundle",
                side_effect=RuntimeError("forced registration failure"),
            ):
'''
count = test.count(old)
if count != 1:
    raise RuntimeError(
        f"rollback failure-injection anchor: expected one match, found {count}"
    )
TEST_PATH.write_text(
    test.replace(old, new, 1),
    encoding="utf-8",
    newline="\n",
)

print('{"ok": true, "finalized": "P7B.2-store-write-boundary"}')
