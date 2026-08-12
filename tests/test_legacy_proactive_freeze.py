"""Freeze guard for pre-P15 proactive chat producers.

The delivery queue remains a shared substrate. Only the legacy random greeting
and automatic learning-report producers are frozen so a future native Life
initiative path can reuse pending/ack without competing producers.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EMBEDDED = ROOT / "src" / "life_service" / "embedded_runtime.py"


def _method_source(text: str, name: str) -> str:
    start = text.index(f"    def {name}(")
    next_def = text.find("\n    def ", start + 1)
    if next_def < 0:
        return text[start:]
    return text[start:next_def]


def test_legacy_random_greeting_producer_is_hard_frozen() -> None:
    text = EMBEDDED.read_text(encoding="utf-8")
    body = _method_source(text, "_schedule_greeting")
    assert "life.proactive.legacy_producer_frozen" in body
    assert "proactive_chats" not in body
    assert "greeting_published" not in body


def test_legacy_learning_report_does_not_enqueue_chat() -> None:
    text = EMBEDDED.read_text(encoding="utf-8")
    body = _method_source(text, "_learning_report")
    assert '"delivery": "legacy_proactive_frozen"' in body
    assert '"suppressed": True' in body
    assert "life.proactive.legacy_producer_frozen" in body
    assert "proactive_chats" not in body


def test_no_legacy_direct_producer_remains_but_delivery_api_stays() -> None:
    text = EMBEDDED.read_text(encoding="utf-8")
    assert '["proactive_chats"].append(' not in text
    assert "/api/v1/v3/life/proactive-chat/pending" in text
    assert "/api/v1/v3/life/proactive-chat/ack" in text
    assert '"proactive_chats": []' in text
