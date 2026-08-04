from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (
    ROOT
    / "app/backend/tiangong-backend/_internal/frozen_modules/v3/execution_kernel/confirmation_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("tiangong_confirmation_bridge", BRIDGE)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class RetiredConfirmationBridgeTests(unittest.TestCase):
    def test_natural_language_never_creates_authority(self) -> None:
        for value in ("确认", "继续执行", "yes", "同意"):
            with self.subTest(value=value):
                self.assertFalse(bridge.is_explicit_confirmation(value))

    def test_stale_waiting_state_is_never_resumed(self) -> None:
        state = {
            "status": "WAITING_FOR_USER",
            "stage": "LEGACY_AUTHORITY_WAIT",
            "pending_tool_calls": [{"call_id": "call_1", "action": "shell.run"}],
        }
        self.assertIsNone(bridge.extract_unique_waiting_call(state))
        self.assertIsNone(bridge.select_waiting_a5_state(object(), "session_1"))
        self.assertEqual(bridge.permission_arguments(state), {})

    def test_continuation_fails_closed(self) -> None:
        with self.assertRaisesRegex(PermissionError, "retired_a5_is_hard_denial"):
            bridge.continuation_patch({}, {}, "continue")


if __name__ == "__main__":
    unittest.main()
