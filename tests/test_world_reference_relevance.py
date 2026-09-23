from types import SimpleNamespace

from world_understanding.context_output.world_reference_context import _reference_relevance


def _row(action, title=None, summary=""):
    return (SimpleNamespace(canonical_name=title or action, entity_id="entity." + action),
            SimpleNamespace(semantic_id=action), None, {"semantic_summary": summary})


def test_natural_chinese_file_request_keeps_real_file_actions_ahead_of_alphabetic_catalog():
    rows = [_row("ableton.live.arrangement.render"), _row("file.read"), _row("file.list"),
            _row("file.write"), _row("python.run"), _row("browser.open")]
    focus = "读取目录里的订单 CSV 文件，统计数据并保存报告".casefold()
    ordered = sorted(rows, key=lambda row: _reference_relevance(row, focus))
    assert {row[1].semantic_id for row in ordered[:4]} == {"file.read", "file.list", "file.write", "python.run"}
    assert set(map(id, rows)) == set(map(id, ordered))


def test_explicit_source_and_descriptor_tokens_remain_preferred():
    rows = [_row("file.read"), _row("ableton.live.arrangement.render"),
            _row("custom.report", "Sales report", "summarize quarterly revenue")]
    assert sorted(rows, key=lambda row: _reference_relevance(row, "请执行 ableton.live.arrangement.render 然后保存文件"))[0][1].semantic_id == "ableton.live.arrangement.render"
    assert sorted(rows, key=lambda row: _reference_relevance(row, "summarize quarterly revenue"))[0][1].semantic_id == "custom.report"


def test_unmatched_candidates_have_stable_order():
    rows = [_row("z.action"), _row("a.action")]
    assert [row[1].semantic_id for row in sorted(rows, key=lambda row: _reference_relevance(row, "无关描述"))] == ["a.action", "z.action"]
