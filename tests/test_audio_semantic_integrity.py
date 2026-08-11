from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from v3.jineng.http_kehuduan import _inject_native_audio_input
from v3.zongdiaodu import (
    _simple_chain_evidence_check,
    _simple_chain_native_audio_payload,
    _simple_chain_requests_audio_semantics,
    _simple_chain_safe_audio_unavailable_reply,
)


def _payload() -> dict:
    return {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "总结这个音频"},
        ],
    }


def test_native_audio_input_contains_verified_bytes_without_leaking_them_in_receipt(tmp_path: Path) -> None:
    audio = tmp_path / "sample.mp3"
    body = b"ID3" + bytes(range(64))
    audio.write_bytes(body)
    payload = _payload()

    receipt = _inject_native_audio_input(payload, (str(audio),))

    assert receipt is not None
    assert receipt["semantic_visibility"] == "submitted"
    assert receipt["sha256"] == hashlib.sha256(body).hexdigest()
    assert receipt["size_bytes"] == len(body)
    parts = payload["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "总结这个音频"}
    assert parts[1]["type"] == "input_audio"
    assert base64.b64decode(parts[1]["input_audio"]["data"]) == body
    assert "data" not in receipt


def test_unsupported_audio_container_is_unavailable_and_not_attached(tmp_path: Path) -> None:
    audio = tmp_path / "sample.m4a"
    audio.write_bytes(b"not-a-supported-native-container")
    payload = _payload()

    receipt = _inject_native_audio_input(payload, (str(audio),))

    assert receipt is not None
    assert receipt["semantic_visibility"] == "unavailable"
    assert receipt["reason"] == "unsupported_audio_container"
    assert payload["messages"][-1]["content"] == "总结这个音频"


def test_audio_semantic_request_detection_does_not_capture_editing_tasks() -> None:
    paths = [r"C:\workspace\lesson.mp3"]
    assert _simple_chain_requests_audio_semantics("总结这个音频讲了什么", paths)
    assert _simple_chain_requests_audio_semantics("把录音转写成文字", paths)
    assert not _simple_chain_requests_audio_semantics("把这个音频裁掉前十秒", paths)


def test_conversion_only_cannot_satisfy_audio_summary_completion() -> None:
    mp3 = r"C:\workspace\lesson.mp3"
    history = [
        {
            "ok": True,
            "tool_action": "video.extract_audio",
            "tool_args": {"target": mp3},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "paths": [mp3, r"C:\workspace\lesson.wav"],
            },
            "source_text_map": {"entries": []},
            "failures": [],
            "final_requirement_gaps": [],
            "gaps": [],
        }
    ]

    allowed, status, reasons = _simple_chain_evidence_check(
        "帮我总结这个音频讲了什么",
        history,
        [{"path": r"C:\workspace\lesson.wav", "suffix": ".wav"}],
        required_read_paths=[mp3],
        final_reply="这段音频讲的是被偏爱的三层含义。",
    )

    assert not allowed
    assert status == "incomplete"
    assert "audio_semantic_evidence_missing" in reasons


def test_verified_native_model_audio_reply_can_complete() -> None:
    mp3 = r"C:\workspace\lesson.mp3"
    reply = "课程主要讨论如何让儿童制定目标、复盘过程并逐渐形成自主学习习惯。"
    native = _simple_chain_native_audio_payload(
        {
            "path": mp3,
            "format": "mp3",
            "size_bytes": 1234,
            "sha256": "a" * 64,
            "semantic_visibility": "visible",
        },
        reply,
    )

    allowed, status, reasons = _simple_chain_evidence_check(
        "帮我总结这个音频讲了什么",
        [native],
        [],
        required_read_paths=[mp3],
        final_reply=reply,
    )

    assert allowed, reasons
    assert status == "complete"


def test_unavailable_reply_never_preserves_hallucinated_summary() -> None:
    hallucination = "这段音频讲的是被偏爱的三层含义。"
    safe = _simple_chain_safe_audio_unavailable_reply(hallucination)
    assert "没有可用的音频识别功能" in safe
    assert "被偏爱" not in safe

    model_honest = "当前没有可用的音频识别功能，无法识别音频，也不会猜测内容。"
    assert _simple_chain_safe_audio_unavailable_reply(model_honest) == model_honest
