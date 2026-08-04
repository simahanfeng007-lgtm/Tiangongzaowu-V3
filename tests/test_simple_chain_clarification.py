"""草案 §4.3 NEEDS_CLARIFICATION：澄清不是失败。

模型对指代/target/recipient/来源不明的请求直接反问澄清（零工具调用）时，
run 必须以 clarify/awaiting_user 泊车并保留原问题，不得套"零观察值"失败模板。
"""

from __future__ import annotations

import unittest


class SimpleChainClarificationTests(unittest.TestCase):
    def test_clarification_question_parks_as_clarify_not_failure(self) -> None:
        from v3.zongdiaodu import _simple_chain_final_hard_gate

        allowed, status, reasons = _simple_chain_final_hard_gate(
            "把那个文件改成英文版",
            [],
            [],
            final_reply="请问您指的是哪个文件？工作区里有好几个。",
        )
        self.assertTrue(allowed)
        self.assertEqual(status, "clarify")
        self.assertEqual(reasons, [])

    def test_substantive_non_answer_still_fails(self) -> None:
        from v3.zongdiaodu import _simple_chain_final_hard_gate

        allowed, status, reasons = _simple_chain_final_hard_gate(
            "把那个文件改成英文版",
            [],
            [],
            final_reply="我无法完成这个任务。",
        )
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertTrue(any("no omni_body observation" in item for item in reasons))

    def test_tool_call_tag_is_not_a_clarification(self) -> None:
        from v3.zongdiaodu import _simple_chain_is_clarification_question

        self.assertFalse(
            _simple_chain_is_clarification_question(
                '好的，先做这个。<omni_body action="file.read" target="a.txt"></omni_body> 请问是哪个？'
            )
        )

    def test_question_marks(self) -> None:
        from v3.zongdiaodu import _simple_chain_is_clarification_question

        self.assertTrue(_simple_chain_is_clarification_question("请问您指哪一位王总？"))
        self.assertTrue(_simple_chain_is_clarification_question("Which file do you mean?"))
        self.assertFalse(_simple_chain_is_clarification_question("已完成。"))
        self.assertFalse(_simple_chain_is_clarification_question(""))
        self.assertFalse(_simple_chain_is_clarification_question(None))

    def test_empty_history_with_tools_keeps_old_logic(self) -> None:
        from v3.zongdiaodu import _simple_chain_final_hard_gate

        # 非澄清的实质短答仍按原失败路径（保护 BUG-9 行为不回退）
        allowed, status, _ = _simple_chain_final_hard_gate(
            "创建 report.docx",
            [],
            [],
            final_reply="好的",
        )
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")

    def test_explicit_learning_card_phrasing_sets_intent(self) -> None:
        from v3.zongdiaodu import _simple_chain_has_explicit_learning_intent

        # The real user phrasing "记录一条学习卡片" must be recognized as an
        # explicit learning intent so learning.ingest receives the host
        # verified ContextVar (the packaged build otherwise blocks it).
        self.assertTrue(
            _simple_chain_has_explicit_learning_intent(
                "记录一条学习卡片：三明治阅读法，来源为读书笔记。"
            )
        )
        self.assertTrue(
            _simple_chain_has_explicit_learning_intent("请帮我生成学习卡片，主题：时间管理。")
        )
        self.assertFalse(
            _simple_chain_has_explicit_learning_intent("今天天气怎么样？")
        )


if __name__ == "__main__":
    unittest.main()
