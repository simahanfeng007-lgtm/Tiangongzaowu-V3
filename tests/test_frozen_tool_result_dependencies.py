from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
OLD = rb"\$tool|\{\{.*result|previous[_ -]?result|" + "待返回|上一步返回".encode("utf-8")
NEW = rb"\$tool(?:\.[a-z0-9_.-]+)?|\{\{\s*(?:\$tool|tool_result|previous_result)(?:[.\s][^{}]*)?\}\}|previous[_ -]?result|" + "待返回|上一步返回".encode("utf-8")


class FrozenToolResultDependencyTests(unittest.TestCase):
    def test_wxml_moustache_fields_are_not_tool_result_dependencies(self) -> None:
        pattern = re.compile(NEW.decode("utf-8"))
        wxml = '{{form.location}} <view class="result-row">{{evaluation.level}}</view>'.lower()
        self.assertIsNone(pattern.search(wxml))
        self.assertIsNotNone(pattern.search("{{tool_result.output.path}}"))
        self.assertIsNotNone(pattern.search("use previous_result"))

    def test_both_schedulers_contain_the_strict_pattern(self) -> None:
        for relative in (
            "app/backend/tiangong-backend/_internal/frozen_modules/v3/execution_kernel/tool_scheduler.pyc",
            "app/backend/tiangong-backend/_internal/legacy_pyz_modules/v3/execution_kernel/tool_scheduler.pyc",
        ):
            data = (ROOT / relative).read_bytes()
            self.assertNotIn(OLD, data)
            self.assertIn(NEW, data)


if __name__ == "__main__":
    unittest.main()
