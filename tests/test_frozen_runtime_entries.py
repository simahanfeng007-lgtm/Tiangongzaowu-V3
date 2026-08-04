from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrozenRuntimeEntryTests(unittest.TestCase):
    def test_windows_version_template_covers_the_native_gateway(self) -> None:
        for name in (
            "total-gateway",
        ):
            with self.subTest(name=name):
                content = (ROOT / "build" / f"version-{name}.txt").read_text(encoding="utf-8")
                self.assertIn("VSVersionInfo", content)
                self.assertIn("StringStruct('ProductName', '天工造物 v3.0 完整版')", content)
                self.assertIn("StringStruct('ProductVersion', '3.0.3')", content)


if __name__ == "__main__":
    unittest.main()
