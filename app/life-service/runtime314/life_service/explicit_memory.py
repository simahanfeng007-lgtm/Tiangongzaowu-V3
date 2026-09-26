"""Structured explicit-memory selection and expiry metadata.

Text is preserved as evidence, never classified by keywords. Persistence is
selected by a typed memory operation; an ordinary message implies no consent.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from contracts import canonical_sha256


# Kept as empty exports for integrations using the old detector API.
EXPLICIT_PATTERNS = ()
EXPIRY_PATTERNS = ()

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


def detect_explicit_intent(
    user_text: str, *, explicit: bool = False, expiry_kind: str | None = None,
) -> ExplicitIntentResult:
    """Validate an explicitly selected memory operation, without interpreting text."""

    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("explicit memory user text is empty")
    text = _normalize(user_text)
    if expiry_kind is not None and expiry_kind not in EXPIRY_WINDOW_MS:
        raise ValueError("unknown explicit memory expiry kind")
    reason_codes = ("explicit_memory_request",) if explicit else ()
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
