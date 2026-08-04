from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (b"skill.route", b"skill.step.check", b"skill.progress.report")


class FrozenSkillPermissionTests(unittest.TestCase):
    def test_model_skill_meta_actions_are_present_in_both_permission_modules(self) -> None:
        for relative in (
            "app/backend/tiangong-backend/_internal/frozen_modules/tiangong_life/permissions.pyc",
            "app/backend/tiangong-backend/_internal/legacy_pyz_modules/tiangong_life/permissions.pyc",
        ):
            data = (ROOT / relative).read_bytes()
            for action in REQUIRED:
                self.assertIn(action, data, f"{action!r} missing from {relative}")


if __name__ == "__main__":
    unittest.main()
