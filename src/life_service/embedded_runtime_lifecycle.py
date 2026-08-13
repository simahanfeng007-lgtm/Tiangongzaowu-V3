from __future__ import annotations

"""Lifecycle coordination for the single authoritative EmbeddedLifeRuntime.

This module does not create a second Life Runtime, persistence authority, or
scheduler implementation.  It only owns host-level lifecycle coordination
that was previously in ``embedded_runtime.py``.
"""

from collections.abc import Callable, Mapping
from typing import Protocol

from .complete_scheduler import EmbeddedLifeScheduler


_INFLIGHT_KEYS = (
    "autonomy_decision_inflight",
    "learning_decision_inflight",
    "self_iteration_decision_inflight",
    "greeting_inflight",
    "proactive_decision_inflight",
)


class SchedulerStopPort(Protocol):
    def stop(self, *, timeout_seconds: float = 5.0) -> None: ...


class AuthorityStoreClosePort(Protocol):
    def close(self) -> None: ...


class WriterLeaseReleasePort(Protocol):
    def release(self) -> None: ...


def recover_inflight_scheduler_flags(state: object) -> bool:
    """Clear crash-left scheduler inflight markers in the loaded projection.

    This is projection recovery only.  It does not synthesize a completed
    activity or mutate signed journal authority.
    """

    if not isinstance(state, Mapping):
        return False
    identity_states = state.get("identity_states")
    if not isinstance(identity_states, Mapping):
        return False

    recovered = False
    for identity_scope in identity_states.values():
        if not isinstance(identity_scope, dict):
            continue
        scheduler_state = identity_scope.get("scheduler")
        if not isinstance(scheduler_state, dict):
            continue
        for key in _INFLIGHT_KEYS:
            if scheduler_state.get(key) is True:
                scheduler_state[key] = False
                recovered = True
    return recovered


def start_embedded_scheduler(
    tick: Callable[[str], None],
    *,
    interval_seconds: float,
) -> EmbeddedLifeScheduler:
    """Create and start the existing EmbeddedLifeScheduler implementation."""

    scheduler = EmbeddedLifeScheduler(
        tick,
        interval_seconds=interval_seconds,
    )
    scheduler.start()
    return scheduler


def cleanup_partial_initialization(
    *,
    scheduler: SchedulerStopPort | None,
    authority_store: AuthorityStoreClosePort | None,
    lease: WriterLeaseReleasePort | None,
    init_error: BaseException,
) -> None:
    """Preserve historical failed-constructor cleanup order and diagnostics."""

    cleanup_errors: list[Exception] = []
    if scheduler is not None:
        try:
            scheduler.stop()
        except Exception as exc:
            cleanup_errors.append(exc)
    if authority_store is not None:
        try:
            authority_store.close()
        except Exception as exc:
            cleanup_errors.append(exc)
    if lease is not None:
        try:
            lease.release()
        except Exception as exc:
            cleanup_errors.append(exc)
    if cleanup_errors:
        init_error.add_note(
            "life kernel partial-initialization cleanup failed: "
            + ",".join(type(exc).__name__ for exc in cleanup_errors)
        )


__all__ = [
    "AuthorityStoreClosePort",
    "SchedulerStopPort",
    "WriterLeaseReleasePort",
    "cleanup_partial_initialization",
    "recover_inflight_scheduler_flags",
    "start_embedded_scheduler",
]
