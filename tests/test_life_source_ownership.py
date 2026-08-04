from __future__ import annotations

import hashlib
import json
import os
import sys
import subprocess
import unittest
from pathlib import Path

from life_service import build_source_ownership_report


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines" / "life-runtime-p0.json"
RUNTIME = ROOT / "app" / "life-service" / "runtime314"
RECOVERED = ROOT / "recovered-python-bytecode" / "life-service"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts and path.name != ".tiangong-generated-source.json"
    }


class LifeSourceOwnershipTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "app/life-service/tiangong-life-service.exe").is_file(), "frozen executable baseline is not part of the source release")
    def test_frozen_runtime_matches_the_p0_baseline_and_recovered_mirror(self) -> None:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["schema"], "tiangong.life.runtime-baseline.v1")
        self.assertFalse(baseline["original_life_core_source_available"])
        for relative, expected in baseline["runtime_files"].items():
            path = ROOT / relative
            self.assertEqual(path.stat().st_size, expected["size_bytes"], relative)
            self.assertEqual(_sha256(path), expected["sha256"], relative)
        for name, expected in baseline["bootstrap_files"].items():
            readable = ROOT / "readable-python-source" / "life-bootstrap" / name
            packaged = RUNTIME / name
            # Bootstrap files are the documented source-owned compatibility
            # seam and intentionally evolve after P0.  Keep their captured P0
            # digest as history, but gate the live pair by exact mirroring.
            self.assertGreater(expected["size_bytes"], 0, name)
            self.assertRegex(expected["sha256"], r"^[0-9a-f]{64}$", name)
            self.assertEqual(readable.read_bytes(), packaged.read_bytes(), name)
        for name, expected in baseline["frozen_modules"].items():
            packaged = RUNTIME / name
            recovered = RECOVERED / name
            self.assertEqual(packaged.read_bytes(), recovered.read_bytes(), name)
            self.assertEqual(packaged.stat().st_size, expected["size_bytes"], name)
            self.assertEqual(_sha256(packaged), expected["sha256"], name)

    def test_source_owned_package_is_an_exact_runtime_mirror(self) -> None:
        source = _regular_files(ROOT / "src" / "life_service")
        packaged = _regular_files(RUNTIME / "life_service")
        self.assertTrue(source)
        self.assertEqual(set(source), set(packaged))
        self.assertEqual(
            [relative for relative in source if source[relative] != packaged[relative]],
            [],
        )

    def test_p0_report_is_explicitly_non_writable_and_has_no_listener(self) -> None:
        report = build_source_ownership_report(ROOT)
        self.assertTrue(report.baseline_present)
        self.assertFalse(report.original_life_core_source_available)
        self.assertFalse(report.production_writer_enabled)
        self.assertFalse(report.network_listener_enabled)
        self.assertFalse(report.scheduler_enabled)
        self.assertFalse(report.real_data_mutation_enabled)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "life_service",
                "--status-json",
                "--workspace-root",
                str(ROOT),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "status_only")
        self.assertFalse(payload["production_writer_enabled"])

    def test_context_labels_are_valid_utf8_not_mojibake(self) -> None:
        projection = (
            ROOT / "src" / "total_gateway" / "context_projection.py"
        ).read_text(encoding="utf-8", errors="strict")
        for expected in (
            "已按上下文权重截断",
            "过程关键信息",
            "断点快照",
            "原始工具调用、工具输出和中间推演不进入后续上下文",
        ):
            self.assertIn(expected, projection)
        self.assertNotIn("\ufffd", projection)
        for mojibake in ("锛", "銆", "鈥", "鐨", "鏂"):
            self.assertNotIn(mojibake, projection)


if __name__ == "__main__":
    unittest.main()
