"""后端模型错误 hint 可读性回归。"""

from __future__ import annotations

import unittest


class LlmErrorHintTests(unittest.TestCase):
    def test_status_hints_are_explicit_chinese(self) -> None:
        from v3.jineng.http_kehuduan import _http_status_hint

        self.assertIn("HTTP 429", _http_status_hint(429))
        self.assertIn("服务商控制台", _http_status_hint(429))
        self.assertIn("HTTP 401/403", _http_status_hint(401))
        self.assertIn("API Key", _http_status_hint(403))
        self.assertIn("HTTP 404", _http_status_hint(404))
        self.assertIn("HTTP 5xx", _http_status_hint(503))


if __name__ == "__main__":
    unittest.main()
