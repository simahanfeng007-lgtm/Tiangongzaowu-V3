"""Deterministic P15 L4 explicit-memory intent detection.

Explicit authority must come from a real ``user_message`` LifeEvent; this
detector never fabricates intent.  It only classifies the exact user text
span into deterministic reason codes and an optional expiry window, so the
coordinator can persist an L4_EXPLICIT derivation bound to that event.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from contracts import canonical_sha256


EXPLICIT_PATTERNS = (
    (re.compile(r"(?:请|帮我)?(?:记住|记下)(?:[:：]?\s*)?"), "explicit_remember"),
    (re.compile(r"(?:以后|今后|将来)(?:请|要|都)?记得"), "future_remember"),
    (re.compile(r"(?:请)?长期(?:记住|保存)"), "long_term_remember"),
    (re.compile(r"(?:请)?永久(?:记住|保存)"), "long_term_remember"),
    (re.compile(r"不要忘记"), "do_not_forget"),
    (re.compile(r"我的长期偏好是"), "long_term_preference"),
    (re.compile(r"以后一直"), "ongoing_behavior"),
    (re.compile(r"请一直"), "ongoing_behavior"),
    (re.compile(r"(?:请)?(?:叫我|称呼我|喊我)"), "address_alias"),
)

EXPIRY_PATTERNS = (
    (re.compile(r"今天(?:先|之内|以前)"), "today"),
    (re.compile(r"这次(?:先)?"), "this_session"),
    (re.compile(r"暂时(?:先)?"), "temporary"),
    (re.compile(r"(?:仅)?(?:这一次|本轮)"), "this_turn"),
)

EXPIRY_WINDOW_MS = {
    "today": None,  # end of the UTC calendar day, computed by the coordinator
    "this_session": 24 * 60 * 60 * 1000,
    "temporary": 24 * 60 * 60 * 1000,
    "this_turn": 60 * 60 * 1000,
}


@dataclass(frozen=True, slots=True)
class ExplicitIntentResult:
    triggered: bool
    reason_codes: tuple[str, ...]
    expiry_kind: str | None
    span_text: str
    span_sha256: str


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    if "\x00" in text or any(
        ord(char) < 32 and char not in "\t\n\r" for char in text
    ):
        raise ValueError("explicit memory text contains a control character")
    return text


def detect_explicit_intent(user_text: str) -> ExplicitIntentResult:
    """Return deterministic explicit-intent metadata for one user span."""

    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("explicit memory user text is empty")
    text = _normalize(user_text)
    reason_codes: list[str] = []
    for pattern, code in EXPLICIT_PATTERNS:
        if pattern.search(text):
            reason_codes.append(code)
    expiry_kind: str | None = None
    for pattern, kind in EXPIRY_PATTERNS:
        if pattern.search(text):
            expiry_kind = kind
            break
    return ExplicitIntentResult(
        triggered=bool(reason_codes),
        reason_codes=tuple(sorted(set(reason_codes))),
        expiry_kind=expiry_kind,
        span_text=text,
        span_sha256=canonical_sha256(
            {"domain": "tiangong.life.explicit-span.v1", "text": text}
        ),
    )


def expiry_deadline_ms(expiry_kind: str | None, created_at_ms: int) -> int | None:
    """Deterministic L4 expiry deadline from a detection expiry kind."""

    if expiry_kind is None:
        return None
    if expiry_kind == "today":
        day = created_at_ms // 86_400_000
        return (day + 1) * 86_400_000
    window = EXPIRY_WINDOW_MS.get(expiry_kind)
    if window is None:
        return None
    return created_at_ms + window


__all__ = [
    "EXPIRY_PATTERNS",
    "EXPIRY_WINDOW_MS",
    "EXPLICIT_PATTERNS",
    "ExplicitIntentResult",
    "detect_explicit_intent",
    "expiry_deadline_ms",
]
