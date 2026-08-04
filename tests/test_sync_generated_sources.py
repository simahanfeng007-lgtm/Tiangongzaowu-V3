"""P1-18 regression: the generated-source sync gate must detect target extras
and marker drift, and --write must prune extras for a deterministic mirror."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "sync_generated_sources_under_test",
        Path(__file__).resolve().parents[1] / "scripts" / "sync-generated-sources.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class SyncGeneratedSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.module = _load_module()
        self.module.ROOT = self.root
        self.module.CONFIG = self.root / "source-ownership.json"
        self.source = self.root / "readable-python-source" / "omni_body_skill"
        self.target = self.root / "app" / "backend" / "tiangong-backend" / "omni_body_skill"
        (self.source / "tools").mkdir(parents=True)
        (self.target / "tools").mkdir(parents=True)
        (self.source / "tools" / "a.py").write_text("A=1\n", encoding="utf-8")
        self.module.atomic_copy(
            self.source / "tools" / "a.py",
            self.target / "tools" / "a.py",
        )
        self.module.write_marker(
            self.target,
            "omni-body-runtime",
            "readable-python-source/omni_body_skill",
            1,
            self.module.tree_hash(self.module.mapping_files(self.source)),
        )
        self.config = {
            "mappings": [
                {
                    "id": "omni-body-runtime",
                    "source": "readable-python-source/omni_body_skill",
                    "targets": ["app/backend/tiangong-backend/omni_body_skill"],
                }
            ]
        }
        self.module.CONFIG.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_clean_check_passes(self) -> None:
        failures = self.module.process(write=False)
        self.assertEqual(failures, [])

    def test_extra_target_file_is_flagged_and_pruned_on_write(self) -> None:
        (self.target / "tools" / "orphan.py").write_text("ORPHAN\n", encoding="utf-8")
        failures = self.module.process(write=False)
        self.assertTrue(any(":extra:" in item for item in failures), failures)
        failures = self.module.process(write=True)
        self.assertEqual(failures, [])
        self.assertFalse((self.target / "tools" / "orphan.py").exists())

    def test_stale_marker_is_flagged(self) -> None:
        marker = self.target / self.module.MARKER
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["tree_sha256"] = "0" * 64
        marker.write_text(json.dumps(payload), encoding="utf-8")
        failures = self.module.process(write=False)
        self.assertTrue(any("marker_drift" in item or "marker_invalid" in item for item in failures), failures)


if __name__ == "__main__":
    unittest.main()
