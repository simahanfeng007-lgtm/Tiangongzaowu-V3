"""Retired legacy confirmation bridge.

A5 is a sovereign hard denial. Natural-language text, stale checkpoints and
legacy UI booleans can never create execution authority. A0-A4 authorization is
issued only by the total gateway PolicyEngine and signed ticket/grant chain.
"""
from __future__ import annotations

from typing import Any


_RETIRED = "legacy_confirmation_bridge.retired_a5_is_hard_denial"


def is_explicit_confirmation(_text: object) -> bool:
    return False


def extract_unique_waiting_call(_state: object) -> dict[str, Any] | None:
    return None


def permission_arguments(_call: object) -> dict[str, Any]:
    return {}


def select_waiting_a5_state(_store: object, _session_id: str) -> dict[str, Any] | None:
    return None


def continuation_patch(_state: object, _call: object, _confirmation_text: str) -> dict[str, Any]:
    raise PermissionError(_RETIRED)


__all__ = [
    "continuation_patch",
    "extract_unique_waiting_call",
    "is_explicit_confirmation",
    "permission_arguments",
    "select_waiting_a5_state",
]
