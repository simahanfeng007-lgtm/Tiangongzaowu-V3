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

# 已知内部标签：由本模块现有逻辑单独处理，不视为脏输出。
_KNOWN_INTERNAL_TAG_NAMES = frozenset(
    ("biaoxian", "system-reminder") + _INTERNAL_TAGS
)

# 未知 XML/HTML 风格标签：例如 <conversation>…</conversation>、<user>、<message>。
_UNKNOWN_TAG_RE = re.compile(
    r"<\s*/?\s*([A-Za-z_][A-Za-z0-9_-]*)\b[^>]*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_FENCE_LINE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


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


def unknown_internal_markup_tags(reply: Any) -> list[str]:
    """列出回复中除已知内部标签外的未知 XML/HTML 风格标签名。

    已知内部标签（biaoxian、system-reminder、表情控制标签）由本模块
    单独的清洗逻辑处理，不会出现在返回值里。
    """
    raw = str(reply or "")
    tags: list[str] = []
    protected = _protected_markup_ranges(raw)
    for match in _UNKNOWN_TAG_RE.finditer(raw):
        if _inside_protected_ranges(match.start(), protected):
            continue
        name = match.group(1).lower()
        if name in _KNOWN_INTERNAL_TAG_NAMES or name in tags:
            continue
        tags.append(name)
    return tags


def has_unknown_internal_markup(reply: Any) -> bool:
    """判断回复是否包含未知 XML/HTML 风格标签。

    用于“打回重发”：模型输出了类似 <conversation>…</conversation>
    的自创标签时，让上层判定为脏回复并触发重试。
    """
    return bool(unknown_internal_markup_tags(reply))


def strip_unknown_internal_markup(reply: Any) -> str:
    """剥离未知 XML/HTML 风格标签的标签壳，保留标签内文本。

    仅作为兜底：正常情况下脏回复已在聊天链路被“打回重发”，
    这里是重试次数用尽后的最终清理，确保用户侧永远看不到裸标签。
    """
    raw = str(reply or "")
    protected = _protected_markup_ranges(raw)
    parts: list[str] = []
    last = 0
    for match in _UNKNOWN_TAG_RE.finditer(raw):
        if match.group(1).lower() in _KNOWN_INTERNAL_TAG_NAMES:
            continue
        if _inside_protected_ranges(match.start(), protected):
            continue
        parts.append(raw[last:match.start()])
        last = match.end()
    parts.append(raw[last:])
    return "".join(parts)


def _protected_markup_ranges(raw: str) -> list[tuple[int, int]]:
    """返回不应视为脏格式的区间：围栏代码块与行内代码片段。"""
    ranges: list[tuple[int, int]] = []
    lines = raw.splitlines(keepends=True)
    in_fence = False
    fence_start = 0
    pos = 0
    for line in lines:
        if _FENCE_LINE_RE.match(line):
            if not in_fence:
                in_fence = True
                fence_start = pos
            else:
                ranges.append((fence_start, pos + len(line)))
                in_fence = False
        pos += len(line)
    if in_fence:
        ranges.append((fence_start, len(raw)))
    ranges.extend((match.start(), match.end()) for match in _INLINE_CODE_RE.finditer(raw))
    return ranges


def _inside_protected_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start <= pos < end:
            return True
    return False


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
    text = strip_unknown_internal_markup(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
