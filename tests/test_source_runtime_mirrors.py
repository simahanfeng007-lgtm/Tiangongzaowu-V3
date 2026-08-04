from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def regular_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts and path.name != ".tiangong-generated-source.json"
    }


class SourceRuntimeMirrorTests(unittest.TestCase):
    def assert_tree_mirror(self, readable: str, runtime: str) -> None:
        readable_files = regular_files(ROOT / readable)
        runtime_files = regular_files(ROOT / runtime)
        self.assertEqual(set(readable_files), set(runtime_files))
        mismatched = [path for path in readable_files if readable_files[path] != runtime_files[path]]
        self.assertEqual(mismatched, [])

    def test_omni_body_readable_source_matches_packaged_runtime(self) -> None:
        self.assert_tree_mirror(
            "readable-python-source/omni_body_skill",
            "app/backend/tiangong-backend/_internal/omni_body_skill",
        )

    def test_novel_skill_readable_source_matches_packaged_runtime(self) -> None:
        self.assert_tree_mirror(
            "readable-python-source/bundled-skills/novel-creation",
            "app/backend/tiangong-backend/_internal/v3/bundled_skills/novel-creation",
        )


if __name__ == "__main__":
    unittest.main()
