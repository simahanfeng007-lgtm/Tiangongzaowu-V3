from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "scripts" / "check-source-authority.py"
SYNC_PATH = REPO_ROOT / "scripts" / "sync-generated-sources.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class V3SourceBoundaryCloseoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = _load(GUARD_PATH, "p17_m1_03_guard")
        cls.sync = _load(SYNC_PATH, "p17_m1_03_sync")
        cls.config = json.loads(
            (REPO_ROOT / "source-ownership.json").read_text(encoding="utf-8")
        )

    def _by_id(self, config=None):
        data = self.config if config is None else config
        return {row["id"]: row for row in data["mappings"]}

    def test_v3_boundary_is_closed_world(self) -> None:
        v3 = self._by_id()["v3-backend-main"]
        boundary = v3["boundary_policy"]
        self.assertEqual("closed_world", boundary["mode"])
        self.assertEqual(
            ["bundled_skills", "endpoint_security.py"],
            v3["generated_exclusions"],
        )
        self.assertIn("zongdiaodu.py", boundary["implementation_roots"])
        self.assertIn("hotfix_20260727.py", boundary["implementation_roots"])
        self.assertNotIn("world_cognition", boundary["implementation_roots"])
        errors = self.guard.validate_source_authority(
            self.config, repo_root=REPO_ROOT, require_sources=True
        )
        self.assertEqual([], errors)

    def test_unclassified_v3_child_is_rejected(self) -> None:
        config = deepcopy(self.config)
        v3 = self._by_id(config)["v3-backend-main"]
        v3["boundary_policy"]["implementation_roots"].remove("body_settings.py")
        errors = self.guard.validate_source_authority(
            config, repo_root=REPO_ROOT, require_sources=True
        )
        self.assertTrue(
            any(
                "unclassified immediate children" in error
                and "body_settings.py" in error
                for error in errors
            ),
            errors,
        )

    def test_v3_novel_copy_is_generated_mirror(self) -> None:
        novel = self._by_id()["managed-novel-skill-runtime"]
        target = "app/backend/tiangong-backend/v3/bundled_skills/novel-creation"
        self.assertIn(target, novel["targets"])
        root = REPO_ROOT / target
        marker = json.loads(
            (root / self.sync.MARKER).read_text(encoding="utf-8")
        )
        self.assertEqual("managed-novel-skill-runtime", marker["mapping_id"])
        self.assertEqual("src/bundled_skills/novel-creation", marker["source"])
        self.assertFalse((root / "references/source-map.md").exists())
        self.assertTrue(
            (REPO_ROOT / "docs/source-provenance/novel-creation-source-map.md").is_file()
        )

    def test_world_cognition_is_reexport_only(self) -> None:
        v3 = self._by_id()["v3-backend-main"]
        adapters = v3["boundary_policy"]["compatibility_adapters"]
        self.assertEqual(1, len(adapters))
        adapter = adapters[0]
        self.assertEqual("world_cognition", adapter["path"])
        self.assertEqual(
            "world-understanding-embedded-python",
            adapter["implementation_authority"],
        )
        self.assertEqual("python_reexport_only", adapter["contract"])
        errors = self.guard._validate_reexport_tree(
            REPO_ROOT / "app/backend/tiangong-backend/v3/world_cognition",
            mapping_id="v3-backend-main",
            relative=Path("world_cognition"),
            import_prefix="world_understanding.cognition",
        )
        self.assertEqual([], errors)

    def test_reexport_contract_rejects_business_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "compat"
            root.mkdir()
            (root / "bad.py").write_text(
                "from canonical.mod import value\n\n"
                "def implementation():\n"
                "    return value\n",
                encoding="utf-8",
            )
            errors = self.guard._validate_reexport_tree(
                root,
                mapping_id="fixture",
                relative=Path("compat"),
                import_prefix="canonical",
            )
        self.assertTrue(any("FunctionDef" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
