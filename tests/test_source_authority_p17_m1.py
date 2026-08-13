from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "scripts" / "check-source-authority.py"
SYNC_PATH = REPO_ROOT / "scripts" / "sync-generated-sources.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_guard():
    return _load_module(GUARD_PATH, "p17_source_authority_guard")


def _mapping(
    mapping_id: str,
    source: str,
    *,
    role: str = "authoritative",
    targets: list[str] | None = None,
    exclusions: list[str] | None = None,
    parent: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": mapping_id,
        "source": source,
        "source_role": role,
        "targets": list(targets or []),
    }
    if exclusions is not None:
        row["generated_exclusions"] = list(exclusions)
    if parent:
        row["authority_parent"] = parent
    return row


class SourceAuthorityGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = _load_guard()
        cls.sync = _load_module(SYNC_PATH, "p17_generated_source_sync")

    def _validate(self, mappings: list[dict[str, Any]]) -> list[str]:
        config = {
            "schema": self.guard.EXPECTED_SCHEMA,
            "principle": "test",
            "mappings": mappings,
        }
        with tempfile.TemporaryDirectory() as tmp:
            return self.guard.validate_source_authority(
                config,
                repo_root=Path(tmp),
                require_sources=False,
            )

    def _repository_config(self) -> dict[str, Any]:
        return json.loads(
            (REPO_ROOT / "source-ownership.json").read_text(encoding="utf-8")
        )

    def test_repository_ownership_graph_is_valid(self) -> None:
        config = self._repository_config()
        errors = self.guard.validate_source_authority(
            config, repo_root=REPO_ROOT, require_sources=True
        )
        self.assertEqual([], errors)

    def test_nested_independent_authority_is_rejected(self) -> None:
        errors = self._validate(
            [
                _mapping("parent", "src/runtime"),
                _mapping("child", "src/runtime/internal"),
            ]
        )
        self.assertTrue(
            any("independent authority overlap" in error for error in errors),
            errors,
        )

    def test_alias_makes_nested_source_non_authoritative(self) -> None:
        errors = self._validate(
            [
                _mapping("parent", "src/runtime"),
                _mapping(
                    "child",
                    "src/runtime/internal",
                    role="authoritative_alias",
                    parent="parent",
                ),
            ]
        )
        self.assertEqual([], errors)

    def test_generated_target_inside_authority_requires_exclusion(self) -> None:
        errors = self._validate(
            [
                _mapping("runtime", "app/runtime"),
                _mapping(
                    "generated",
                    "src/generated-source",
                    targets=["app/runtime/generated"],
                ),
            ]
        )
        self.assertTrue(
            any("without generated_exclusions coverage" in error for error in errors),
            errors,
        )

    def test_generated_exclusion_allows_explicit_nested_mirror(self) -> None:
        errors = self._validate(
            [
                _mapping(
                    "runtime",
                    "app/runtime",
                    exclusions=["generated"],
                ),
                _mapping(
                    "generated",
                    "src/generated-source",
                    targets=["app/runtime/generated"],
                ),
            ]
        )
        self.assertEqual([], errors)

    def test_source_inside_generated_target_is_rejected(self) -> None:
        errors = self._validate(
            [
                _mapping(
                    "generator",
                    "src/source",
                    targets=["app/runtime/generated"],
                ),
                _mapping("bad-source", "app/runtime/generated/manual"),
            ]
        )
        self.assertTrue(
            any("is inside generated target" in error for error in errors),
            errors,
        )

    def test_marker_tree_hash_uses_portable_windows_order(self) -> None:
        names = [
            Path("SOURCE_OWNERSHIP.md"),
            Path("__init__.py"),
            Path("store.py"),
        ]
        ordered = sorted(names, key=self.sync.logical_tree_path_sort_key)
        self.assertEqual(
            ["__init__.py", "SOURCE_OWNERSHIP.md", "store.py"],
            [item.as_posix() for item in ordered],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for index, name in enumerate(names):
                target = root / name
                target.write_text(f"fixture-{index}\n", encoding="utf-8")
                rows.append((name, target))
            self.assertEqual(
                self.sync.tree_hash(rows),
                self.sync.tree_hash(list(reversed(rows))),
            )

    def test_alias_cannot_own_generated_targets(self) -> None:
        errors = self._validate(
            [
                _mapping("parent", "src/runtime"),
                _mapping(
                    "alias",
                    "src/runtime/part",
                    role="authoritative_alias",
                    parent="parent",
                    targets=["dist/part"],
                ),
            ]
        )
        self.assertTrue(
            any("authoritative_alias must not own" in error for error in errors),
            errors,
        )

    def test_readable_python_source_has_no_independent_authority(self) -> None:
        config = self._repository_config()
        offenders = [
            row["id"]
            for row in config["mappings"]
            if row.get("source_role") in self.guard.INDEPENDENT_ROLES
            and (
                row.get("source") == "readable-python-source"
                or str(row.get("source", "")).startswith("readable-python-source/")
            )
        ]
        self.assertEqual([], offenders)

    def test_p17_m1_02_migrated_authorities_live_under_src(self) -> None:
        config = self._repository_config()
        by_id = {row["id"]: row for row in config["mappings"]}
        expected = {
            "life-bootstrap-runtime": "src/life_bootstrap/tiangong_life_bootstrap.py",
            "life-runtime-fixes": "src/life_bootstrap/tiangong_life_runtime_fixes.py",
            "omni-body-runtime": "src/omni_body_skill",
            "managed-novel-skill-runtime": "src/bundled_skills/novel-creation",
        }
        for mapping_id, expected_source in expected.items():
            with self.subTest(mapping_id=mapping_id):
                self.assertEqual(expected_source, by_id[mapping_id]["source"])
                self.assertTrue((REPO_ROOT / expected_source).exists())

    def test_p17_m1_02_legacy_readable_paths_are_generated_targets(self) -> None:
        config = self._repository_config()
        by_id = {row["id"]: row for row in config["mappings"]}
        expected_targets = {
            "life-bootstrap-runtime":
                "readable-python-source/life-bootstrap/tiangong_life_bootstrap.py",
            "life-runtime-fixes":
                "readable-python-source/life-bootstrap/tiangong_life_runtime_fixes.py",
            "omni-body-runtime": "readable-python-source/omni_body_skill",
            "managed-novel-skill-runtime":
                "readable-python-source/bundled-skills/novel-creation",
        }
        for mapping_id, expected_target in expected_targets.items():
            with self.subTest(mapping_id=mapping_id):
                self.assertIn(expected_target, by_id[mapping_id]["targets"])

    def test_authority_policy_roots_match_converged_layout(self) -> None:
        config = self._repository_config()
        policy = config.get("authority_policy", {})
        self.assertEqual(
            [
                "src",
                "app/backend/tiangong-backend/v3",
                "app/backend/tiangong-backend/tiangong_kernel",
            ],
            policy.get("editable_roots"),
        )
        self.assertEqual(
            ["app/backend/tiangong-backend/_internal/frozen_modules"],
            policy.get("frozen_roots"),
        )
        self.assertEqual(
            ["readable-python-source"],
            policy.get("compatibility_mirror_roots"),
        )

        def at_or_below(path: str, root: str) -> bool:
            return path == root or path.startswith(root + "/")

        for row in config["mappings"]:
            role = row.get("source_role")
            source = str(row.get("source", ""))
            if role == "authoritative":
                self.assertTrue(
                    any(at_or_below(source, root) for root in policy["editable_roots"]),
                    (row["id"], source),
                )
            elif role == "frozen_authoritative":
                self.assertTrue(
                    any(at_or_below(source, root) for root in policy["frozen_roots"]),
                    (row["id"], source),
                )

    def test_directory_markers_record_current_authority_source(self) -> None:
        config = self._repository_config()
        for row in config["mappings"]:
            source = REPO_ROOT / row["source"]
            if not source.is_dir():
                continue
            source_rows = self.sync.mapping_files(source)
            expected_hash = self.sync.tree_hash(source_rows)
            expected_count = len(source_rows)
            for target_rel in row.get("targets", []):
                target = REPO_ROOT / target_rel
                if not target.is_dir():
                    continue
                marker = target / self.sync.MARKER
                with self.subTest(mapping_id=row["id"], target=target_rel):
                    self.assertTrue(marker.is_file())
                    payload = json.loads(marker.read_text(encoding="utf-8"))
                    self.assertEqual("tiangong.generated-source-marker.v1", payload.get("schema"))
                    self.assertEqual(row["id"], payload.get("mapping_id"))
                    self.assertEqual(row["source"], payload.get("source"))
                    self.assertEqual(expected_count, payload.get("file_count"))
                    self.assertEqual(expected_hash, payload.get("tree_sha256"))


if __name__ == "__main__":
    unittest.main()
