from __future__ import annotations

import json
import re
from typing import Any


_BIAOXIAN_JSON_RE = re.compile(
    r"<\s*biaoxi[an][ng]\s*>\s*(\{.*?\})\s*<\s*/\s*biaoxi[an][ng]\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_BIAOXIAN_BLOCK_RE = re.compile(
    r"<\s*biaoxi[an][ng]\s*>.*?<\s*/\s*biaoxi[an][ng]\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_BIAOXIAN_TAIL_RE = re.compile(
    r"<\s*biaoxi[an][ng]\s*>.*\Z",
    flags=re.DOTALL | re.IGNORECASE,
)
_MINIMAX_SENTINEL_RE = re.compile(r"\|?<\|minimax\|>\|?", flags=re.IGNORECASE)
_SYSTEM_REMINDER_BLOCK_RE = re.compile(
    r"<\s*system-reminder\b[^>]*>.*?<\s*/\s*system-reminder\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_SYSTEM_REMINDER_TAG_RE = re.compile(
    r"<\s*/?\s*system-reminder\b[^>]*>",
    flags=re.IGNORECASE,
)
_INTERNAL_TAGS = ("expression", "gaze", "posture", "gesture", "tail", "intensity", "duration")


def _clean_control_value(value: str) -> str:
    text = _MINIMAX_SENTINEL_RE.sub("", str(value or ""))
    return text.strip().strip("|").strip()


def extract_biaoxian_payload(reply: Any) -> dict[str, Any] | None:
    """Extract avatar-control payload from either JSON XML or MiniMax segmented XML."""
    raw = str(reply or "")
    if not raw:
        return None
    matches = list(_BIAOXIAN_JSON_RE.finditer(raw))
    if matches:
        try:
            data = json.loads(matches[-1].group(1))
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    blocks = list(_BIAOXIAN_BLOCK_RE.finditer(raw))
    if not blocks:
        return None
    body = blocks[-1].group(0)
    payload: dict[str, Any] = {}
    for tag in _INTERNAL_TAGS:
        match = re.search(
            rf"<\s*{tag}\b[^>]*>(.*?)<\s*/\s*{tag}\s*>",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not match:
            continue
        value = _clean_control_value(match.group(1))
        if not value:
            continue
        if tag in {"intensity", "duration"}:
            try:
                payload[tag] = float(value)
            except Exception:
                continue
        else:
            payload[tag] = value
    return payload or None


def strip_internal_reply_markers(reply: Any) -> str:
    """Remove internal avatar/control markup from text visible to users."""
    text = str(reply or "")
    if not text:
        return ""
    text = _BIAOXIAN_BLOCK_RE.sub("", text)
    text = _BIAOXIAN_TAIL_RE.sub("", text)
    text = _MINIMAX_SENTINEL_RE.sub("", text)
    text = _SYSTEM_REMINDER_BLOCK_RE.sub("", text)
    text = _SYSTEM_REMINDER_TAG_RE.sub("", text)
    for tag in _INTERNAL_TAGS:
        text = re.sub(
            rf"<\s*{tag}\b[^>]*>.*?<\s*/\s*{tag}\s*>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(rf"<\s*/?\s*{tag}\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
