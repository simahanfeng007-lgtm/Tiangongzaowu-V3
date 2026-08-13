from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class ReplySanitizerMarkupTests(unittest.TestCase):
    """未知 XML/HTML 风格标签的检测、剥离与“打回重发”接线。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sanitizer = importlib.import_module("v3.reply_sanitizer")

    def test_conversation_wrapper_detected(self) -> None:
        reply = "<conversation>喆哥是老师，负责交付黄总那个项目。</conversation>"
        self.assertTrue(self.sanitizer.has_unknown_internal_markup(reply))
        self.assertEqual(self.sanitizer.unknown_internal_markup_tags(reply), ["conversation"])

    def test_unknown_tags_with_attributes_and_self_closing_detected(self) -> None:
        cases = (
            '<user id="7">内容</user>',
            "<assistant/>",
            "<message>内容</message>",
            "<conversation>内容</conversation>",
        )
        for reply in cases:
            self.assertTrue(self.sanitizer.has_unknown_internal_markup(reply), reply)

    def test_known_internal_tags_not_flagged(self) -> None:
        replies = (
            '<biaoxian>{"expression":"soft","gaze":"user"}</biaoxian>',
            "<system-reminder>提醒内容</system-reminder>",
            "<expression>soft</expression>",
            "<gesture>nod</gesture>",
        )
        for reply in replies:
            self.assertFalse(self.sanitizer.has_unknown_internal_markup(reply), reply)

    def test_minimax_sentinel_and_plain_text_not_flagged(self) -> None:
        replies = ("|<|minimax|>|", "公子，妾身记下了。", "1 < 2 且 3 > 2", "")
        for reply in replies:
            self.assertFalse(self.sanitizer.has_unknown_internal_markup(reply), reply)

    def test_case_insensitive_detection(self) -> None:
        self.assertTrue(self.sanitizer.has_unknown_internal_markup("<Conversation>x</CONVERSATION>"))

    def test_fenced_code_block_content_immune(self) -> None:
        reply = "示例：\n```html\n<div class=\"box\">内容</div>\n<conversation>代码里的标签</conversation>\n```"
        self.assertFalse(self.sanitizer.has_unknown_internal_markup(reply))
        cleaned = self.sanitizer.strip_internal_reply_markers(reply)
        self.assertIn("<div class=\"box\">内容</div>", cleaned)
        self.assertIn("<conversation>代码里的标签</conversation>", cleaned)

    def test_inline_code_span_immune(self) -> None:
        reply = "类型参数写法是 `<typename T>`，调用是 `<T>`。"
        self.assertFalse(self.sanitizer.has_unknown_internal_markup(reply))
        cleaned = self.sanitizer.strip_internal_reply_markers(reply)
        self.assertIn("<typename T>", cleaned)

    def test_dirty_wrapper_outside_fence_still_detected(self) -> None:
        reply = "```\n<conversation>代码内容</conversation>\n```\n<conversation>脏回复</conversation>"
        self.assertTrue(self.sanitizer.has_unknown_internal_markup(reply))
        cleaned = self.sanitizer.strip_internal_reply_markers(reply)
        self.assertIn("<conversation>代码内容</conversation>", cleaned)
        self.assertNotIn("<conversation>脏回复</conversation>", cleaned)

    def test_strip_removes_unknown_tags_keeps_inner_text(self) -> None:
        cleaned = self.sanitizer.strip_internal_reply_markers(
            "<conversation>喆哥是老师，负责交付黄总那个项目。</conversation>"
        )
        self.assertEqual(cleaned, "喆哥是老师，负责交付黄总那个项目。")

    def test_strip_combines_known_and_unknown_markers(self) -> None:
        raw = (
            "公子，妾身知道了。\n"
            "<conversation>原文复读</conversation>\n"
            '<biaoxian>{"expression":"soft"}</biaoxian>'
        )
        cleaned = self.sanitizer.strip_internal_reply_markers(raw)
        self.assertNotIn("conversation", cleaned)
        self.assertNotIn("biaoxian", cleaned)
        self.assertIn("公子，妾身知道了。", cleaned)

    def test_strip_empty_and_none(self) -> None:
        self.assertEqual(self.sanitizer.strip_internal_reply_markers(None), "")
        self.assertEqual(self.sanitizer.strip_internal_reply_markers(""), "")
        self.assertFalse(self.sanitizer.has_unknown_internal_markup(None))


class ReplySanitizerRetryWiringTests(unittest.TestCase):
    """确认聊天入口确实接上了“打回重发”逻辑。"""

    def test_markup_retry_constant_wired(self) -> None:
        bridge = importlib.import_module("v3.duihua_qiaojie")
        self.assertGreaterEqual(bridge.CHAT_MARKUP_RETRY_LIMIT, 2)
        sanitizer = importlib.import_module("v3.reply_sanitizer")
        self.assertIs(bridge.has_unknown_internal_markup, sanitizer.has_unknown_internal_markup)


if __name__ == "__main__":
    unittest.main()
