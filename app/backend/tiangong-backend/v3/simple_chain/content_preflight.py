"""Retired prose-derived Office content gates; kept for import compatibility."""
from __future__ import annotations


def _office_content_gaps(user_message: str, generated_attachments: list[dict[str, str]] | None) -> list[str]:
    """Content requirements are interpreted by the model, not guessed from text."""
    return []
