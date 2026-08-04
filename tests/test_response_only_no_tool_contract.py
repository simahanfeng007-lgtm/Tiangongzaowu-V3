from __future__ import annotations

import inspect


def test_response_only_no_tool_request_is_not_forced_through_omni_body() -> None:
    from v3.zongdiaodu import (
        _runtime_detects_work_intent,
        _simple_chain_final_hard_gate,
    )

    prompt = (
        "真实连通性与任务执行质检：不要调用任何工具，不要修改文件，"
        "只回复以下唯一文本：MINIMAX_QA_OK_20260730"
    )
    assert not _runtime_detects_work_intent(prompt)
    assert _simple_chain_final_hard_gate(
        prompt,
        [],
        [],
        final_reply="MINIMAX_QA_OK_20260730",
    ) == (True, "complete", [])


def test_renderer_tool_contract_cannot_contaminate_user_intent() -> None:
    from v3.zongdiaodu import (
        _runtime_detects_work_intent,
        _simple_chain_user_goal_text,
    )

    goal = "不要调用任何工具，只回复：QA_OK"
    transport = (
        goal
        + "\n\n【工具批次执行契约】\n"
        + "遇到修改任务时，优先用一个工具完成最小定向修改，再用一个工具验证。"
    )
    assert _simple_chain_user_goal_text(transport) == goal
    assert not _runtime_detects_work_intent(transport)


def test_real_work_requests_still_require_evidence() -> None:
    from v3.zongdiaodu import (
        _runtime_detects_work_intent,
        _simple_chain_final_hard_gate,
    )

    prompt = "请执行测试并修复失败项"
    assert _runtime_detects_work_intent(prompt)
    allowed, status, reasons = _simple_chain_final_hard_gate(
        prompt,
        [],
        [],
        final_reply="已经完成",
    )
    assert not allowed
    assert status == "incomplete"
    assert reasons == ["no omni_body observation exists for this work request"]


def test_response_only_runtime_disables_tools_at_provider_boundary() -> None:
    from v3 import zongdiaodu

    source = inspect.getsource(zongdiaodu.Zongdiaodu._huanxing_simple_chain)
    assert "disable_tools=response_only_without_tools" in source
    assert "tools = [] if response_only_without_tools" in source
    assert '"mode": "chat" if response_only_without_tools else "work"' in source
