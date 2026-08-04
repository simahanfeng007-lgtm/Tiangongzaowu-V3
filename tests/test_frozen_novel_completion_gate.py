from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OLD = "(?:写|撰写|创作|编写|续写).{0,40}(?:小说|章节|正文)|(?:小说|章节|正文).{0,40}(?:写|撰写|创作|编写|续写)|(?:write|draft|create).{0,40}(?:novel|chapter)".encode("utf-8")
NEW = "(?:写|撰写|创作|编写|续写).{0,40}(?:小说|网文|故事章节)|(?:小说|网文|故事章节).{0,40}(?:写|撰写|创作|编写|续写)|(?:write|draft|create).{0,40}(?:novel|fiction chapter)".encode("utf-8")


class FrozenNovelCompletionGateTests(unittest.TestCase):
    def test_generic_document_chapters_do_not_activate_novel_completion(self) -> None:
        for relative in (
            "app/backend/tiangong-backend/_internal/frozen_modules/v3/execution_kernel/orchestrator.pyc",
            "app/backend/tiangong-backend/_internal/legacy_pyz_modules/v3/execution_kernel/orchestrator.pyc",
        ):
            data = (ROOT / relative).read_bytes()
            self.assertNotIn(OLD, data)
            self.assertIn(NEW, data)


if __name__ == "__main__":
    unittest.main()
