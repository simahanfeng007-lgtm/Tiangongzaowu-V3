from __future__ import annotations

import hashlib
import marshal
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTERNAL = ROOT / "app" / "backend" / "tiangong-backend" / "_internal"


def _strings(code: types.CodeType) -> set[str]:
    values: set[str] = set()
    for constant in code.co_consts:
        if isinstance(constant, str):
            values.add(constant)
        elif isinstance(constant, types.CodeType):
            values.update(_strings(constant))
    return values


class FrozenOmniBodyMirrorTests(unittest.TestCase):
    def test_frozen_and_legacy_omni_modules_are_identical_and_contain_hardening(self) -> None:
        expectations = {
            "omni_body_skill/tool_contracts.pyc": {
                "bounded_positive_integer",
                " must be a JSON integer from 1 to 3600 seconds",
                "pptx.read",
            },
            "omni_body_skill/tools/skill_router.pyc": {
                "Skill authority origin must be exact loopback port 7184",
                "/api/v1/internal/skills/",
                "skill.step.check requires an execution-bound Skill activation",
                "tiangong-total-gateway",
            },
            "omni_body_skill/tools/delivery_kernel.pyc": {
                "managed_long_document_delivery",
                "miniapp_test_command_required",
                "miniapp_page_path_unsafe",
                "no_meaningful_visuals",
                "default_placeholder_layout",
            },
            "omni_body_skill/tools/ppt_design.pyc": {
                "tiangong.v3.ppt_design.v1",
                "TGVisual:card:",
                "builtin:fallback",
            },
            "omni_body_skill/tools/omni_body_tool.pyc": {
                "CapabilityUnavailable",
                "pptx.read",
                "required action missing from delivery registry: ",
            },
        }
        for relative, required in expectations.items():
            frozen = INTERNAL / "frozen_modules" / relative
            legacy = INTERNAL / "legacy_pyz_modules" / relative
            self.assertEqual(hashlib.sha256(frozen.read_bytes()).digest(), hashlib.sha256(legacy.read_bytes()).digest())
            payload = frozen.read_bytes()
            self.assertGreaterEqual(len(payload), 16)
            code = marshal.loads(payload[16:])
            self.assertIsInstance(code, types.CodeType)
            strings = _strings(code)
            self.assertTrue(required.issubset(strings), (relative, sorted(required - strings)))

    def test_windows_release_rebuilds_all_frozen_overlays_with_cpython_312(self) -> None:
        rebuild = (ROOT / "scripts" / "rebuild_frozen_release_overlays.py").read_text(encoding="utf-8")
        release = (ROOT / "scripts" / "release-common.mjs").read_text(encoding="utf-8")
        for marker in (
            "sys.version_info[:2] != (3, 12)",
            "omni_body_skill/tool_contracts.pyc",
            "v3/execution_kernel/orchestrator.py",
            "v3/execution_kernel/tool_scheduler.py",
            "tiangong_life/permissions.py",
            "PycInvalidationMode.UNCHECKED_HASH",
        ):
            self.assertIn(marker, rebuild)
        self.assertIn('rebuild_frozen_release_overlays.py', release)


if __name__ == "__main__":
    unittest.main()
