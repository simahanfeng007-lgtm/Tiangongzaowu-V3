"""Non-authorizing bridge from legacy v3 compatibility surfaces to Life telemetry.

The observer is installed by the single Total Gateway process and writes through
the existing EmbeddedLifeRuntime signed journal. This module owns no persistence,
no current pointer and no execution/publication authority.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable

from life_service.legacy_learning_migration import EXTERNAL_COMPATIBILITY_ENTRYPOINTS

_LOCK = RLock()
_OBSERVER: Callable[[str], object] | None = None


def set_legacy_learning_usage_observer(observer: Callable[[str], object] | None) -> None:
    global _OBSERVER
    if observer is not None and not callable(observer):
        raise TypeError("legacy learning usage observer must be callable")
    with _LOCK:
        if _OBSERVER is not None and observer is not None and _OBSERVER is not observer:
            raise RuntimeError("legacy_learning_telemetry.observer_already_bound")
        _OBSERVER = observer


def observe_legacy_learning_usage(surface: str) -> bool:
    if surface not in EXTERNAL_COMPATIBILITY_ENTRYPOINTS:
        return False
    with _LOCK:
        observer = _OBSERVER
    if observer is None:
        return False
    try:
        observer(surface)
        return True
    except Exception:
        # Telemetry can never change the compatibility route's business result.
        return False


__all__ = ["observe_legacy_learning_usage", "set_legacy_learning_usage_observer"]
