"""Regression for the real M3 write/read acceptance request's false obligations."""
from __future__ import annotations

import pytest

from test_execution_integrity import integrity


@pytest.mark.parametrize("separator", ["，", "；", "。", ",", ";", "\n"])
def test_unquoted_path_ends_at_request_clause(separator):
    target = r"D:\workspace\proof.txt"
    request = f"请创建文件 {target}{separator}内容为验证记录。"
    assert integrity._extract_explicit_targets(request) == [target]


def test_adjacent_paths_remain_distinct_required_targets():
    targets = [r"D:\workspace\first.txt", r"D:\workspace\second.txt"]
    assert integrity._extract_explicit_targets("请读取 " + "，".join(targets)) == targets


def test_quoted_path_keeps_literal_punctuation_and_spaces():
    target = r"D:\my workspace\记录，最终.txt"
    assert integrity._extract_explicit_targets(f'请创建文件 "{target}"。') == [target]


@pytest.mark.parametrize("verb", ["创建一个", "生成一份", "新建", "保存"])
def test_deliverable_noun_does_not_request_a_separate_delivery(verb):
    request = f"请{verb}交付文件 proof.txt"
    assert {item["kind"] for item in integrity.build_action_obligations(request)} == {"effect"}


@pytest.mark.parametrize("user_text", [
    "请交付文件 proof.txt", "请创建文件 proof.txt，然后交付文件", "请生成交付文件 proof.txt，然后上传文件",
])
def test_actual_delivery_still_requires_delivery_evidence(user_text):
    assert "delivery" in {item["kind"] for item in integrity.build_action_obligations(user_text)}
    assert "execution_obligation:delivery:missing_evidence" in integrity.execution_integrity_blockers(
        user_text, [_write_fact("proof.txt")]
    )


def _write_fact(target):
    return {
        "ok": True, "tool_action": "file.write", "tool_args": {"target": target},
        "tool_result_contract": {"ok": True, "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "changed_files": [target]}},
    }


def test_live_request_accepts_only_evidence_for_the_actual_target():
    target = r"D:\workspace\proof.txt"
    request = (f"请使用 file.write 创建一个交付文件 {target}，args.content 为 P8_LIVE_PROOF 加一个换行符。"
               "然后使用 file.read 读回该文件，最后回复文件路径。")
    assert integrity._extract_explicit_targets(request) == [target]
    assert integrity.execution_integrity_blockers(request, [_write_fact(target)]) == []
    for history in ([], [_write_fact(r"D:\workspace\wrong.txt")],
                    [{**_write_fact(target), "ok": False}],
                    [{**_write_fact(target), "tool_result_contract": {"ok": True}}]):
        assert integrity.execution_integrity_blockers(request, history)
