"""Fail-open observer hook for native commit owners.

Native stores remain transaction owners. They publish a bounded notification
only after their own commit/finalization succeeds. The installed V3 adapter is
the sole component allowed to turn that notification into an envelope for the
one ``WorldUnderstandingFacade.accept`` path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class NativePostCommitEvent:
    source_kind: str
    source_native_id: str
    producer_ref: str
    payload: dict[str, Any]
    occurred_at_ms: int
    identity: dict[str, str] = field(default_factory=dict)


NativePostCommitObserver = Callable[[NativePostCommitEvent], object | None]

_lock = RLock()
_observer: NativePostCommitObserver | None = None


def install_native_post_commit_observer(observer: NativePostCommitObserver | None) -> None:
    if observer is not None and not callable(observer):
        raise TypeError("native post-commit observer must be callable")
    global _observer
    with _lock:
        _observer = observer


def notify_native_post_commit(event: NativePostCommitEvent) -> object | None:
    """Notify the installed observer without changing the native outcome."""
    with _lock:
        observer = _observer
    if observer is None:
        return None
    try:
        return observer(event)
    except Exception:
        return None


__all__ = [
    "NativePostCommitEvent",
    "NativePostCommitObserver",
    "install_native_post_commit_observer",
    "notify_native_post_commit",
]
