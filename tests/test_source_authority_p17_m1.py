from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "scripts" / "check-source-authority.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("p17_source_authority_guard", GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load source-authority guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_repository_ownership_graph_is_valid(self) -> None:
        config = json.loads(
            (REPO_ROOT / "source-ownership.json").read_text(encoding="utf-8")
        )
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


if __name__ == "__main__":
    unittest.main()
