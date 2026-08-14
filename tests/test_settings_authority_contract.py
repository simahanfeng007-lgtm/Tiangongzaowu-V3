"""Settings authority contract: field-scoped saves never substitute defaults.

Same bug class as the provider identity defect, locked for every settings
authority: saving one field must never overwrite an untouched field with a
derived/default value, and an empty payload value means "no new value" rather
than "reset to default".
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from v3 import workspace_settings
from v3.workspace_settings import baocun_workspace_settings, duqu_workspace_settings


class WorkspaceAuthorityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary.name) / "workspace_settings.json"
        self._original = workspace_settings.WORKSPACE_SETTINGS_LUJING
        workspace_settings.WORKSPACE_SETTINGS_LUJING = self.config_path
        self._env_snapshot = {
            key: os.environ.get(key)
            for key in (
                "TIANGONG_DESKTOP_WORKSPACE_ROOT",
                "TIANGONG_WORKSPACE_ROOT",
                "TIANGONG_WORKSPACE_MODE",
            )
        }
        for key in self._env_snapshot:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        workspace_settings.WORKSPACE_SETTINGS_LUJING = self._original
        for key, value in self._env_snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temporary.cleanup()

    def _stored(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def test_mode_only_save_keeps_configured_workspace_authority(self) -> None:
        workspace = Path(self.temporary.name) / "projects"
        baocun_workspace_settings({"workspace": str(workspace), "workspace_mode": "workspace"})
        # The user later toggles ONLY the write mode; the saved workspace
        # path must stay exactly what the user configured.
        result = baocun_workspace_settings({"workspace_mode": "full"})
        self.assertEqual("full", result["workspace_mode"])
        stored = self._stored()
        self.assertEqual(str(workspace), stored["workspace"])
        self.assertEqual("full", stored["workspace_mode"])
        self.assertEqual("full", duqu_workspace_settings()["workspace_mode"])

    def test_empty_workspace_value_keeps_current_authority(self) -> None:
        workspace = Path(self.temporary.name) / "projects"
        baocun_workspace_settings({"workspace": str(workspace)})
        result = baocun_workspace_settings({"workspace": ""})
        stored = self._stored()
        self.assertEqual(str(workspace), stored["workspace"])
        self.assertEqual(str(workspace), result["workspace"])

    def test_explicit_workspace_change_updates_authority(self) -> None:
        first = Path(self.temporary.name) / "a"
        second = Path(self.temporary.name) / "b"
        baocun_workspace_settings({"workspace": str(first)})
        result = baocun_workspace_settings({"workspace": str(second)})
        self.assertEqual(str(second), result["workspace"])
        self.assertEqual(str(second), self._stored()["workspace"])

    def test_empty_payload_is_a_noop(self) -> None:
        workspace = Path(self.temporary.name) / "projects"
        baocun_workspace_settings({"workspace": str(workspace), "workspace_mode": "full"})
        before = self._stored()
        result = baocun_workspace_settings({})
        after = self._stored()
        self.assertEqual(before, after)
        self.assertEqual(str(workspace), result["workspace"])


if __name__ == "__main__":
    unittest.main()