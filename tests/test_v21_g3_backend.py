"""G3 backend adapter evidence: structured model result + typed system status."""
from __future__ import annotations

from v3.gateway_links import typed_error_system_status
from v3.jineng.http_kehuduan import structured_model_attempt_result


def test_structured_model_attempt_result_is_additive_and_provenance_bound() -> None:
    wrapped = structured_model_attempt_result(
        provider="minimax",
        model="m1",
        text="你好",
        transport_run_id="trn_1",
        provider_response_id="resp_1",
    )
    assert wrapped["schema"] == "tiangong.v3.model_attempt_result_wrapper.v1"
    assert wrapped["production"] is False
    assert wrapped["provider"] == "minimax"
    assert wrapped["model"] == "m1"
    assert wrapped["output_text_sha256"] == wrapped["text_object_id"].removeprefix("obj_")
    assert len(wrapped["output_text_sha256"]) == 64
    changed = structured_model_attempt_result(
        provider="minimax", model="m1", text="你好！"
    )
    assert changed["output_text_sha256"] != wrapped["output_text_sha256"]
    assert wrapped["finish_reason"] == "stop"


def test_typed_error_system_status_never_becomes_assistant() -> None:
    card = typed_error_system_status(
        code="tool_failed",
        diagnostic="处理过程中出错了，当前任务没有正常完成。",
    )
    assert card["origin"] == "system"
    assert card["assistant_message"] is None
    assert card["code"] == "tool_failed"
    assert card["source_component"] == "backend.v3"
    assert "处理过程中出错了" in card["diagnostic"]
