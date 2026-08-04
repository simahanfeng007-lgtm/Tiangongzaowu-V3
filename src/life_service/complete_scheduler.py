"""Background scheduler for the authoritative LifeKernel.

The scheduler is host-agnostic: the same component runs when LifeKernel is
embedded in the 7184 application or hosted by the standalone maintenance
entrypoint.  It owns no network listener and cannot bypass Policy/Grant for
external actions; its local heartbeat is a journal maintenance event only.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class EmbeddedLifeScheduler:
    """Small cooperative scheduler with explicit lifecycle and diagnostics."""

    def __init__(self, tick: Callable[[str], None], *, interval_seconds: float = 30.0) -> None:
        self._tick = tick
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_tick_monotonic_ns = 0
        self._last_error_type = ""
        self._last_error_code = ""
        self._tick_count = 0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="tiangong-life-scheduler",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._tick("scheduled")
            except Exception as exc:  # diagnostic only; the next tick must remain possible
                with self._lock:
                    self._last_error_type = type(exc).__name__
                    # Public readiness must explain a domain failure without
                    # exposing arbitrary exception messages, which can carry
                    # paths or user data.  LifeCoreError-style failures expose
                    # a stable machine-readable code; unknown exceptions keep
                    # the existing type-only diagnostic.
                    code = getattr(exc, "code", "")
                    self._last_error_code = code if isinstance(code, str) else ""
            else:
                with self._lock:
                    self._tick_count += 1
                    self._last_tick_monotonic_ns = time.monotonic_ns()
                    self._last_error_type = ""
                    self._last_error_code = ""

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        """Stop the scheduler and prove that its worker has quiesced.

        Clearing ``_thread`` before the worker has actually exited creates a
        false-stop condition: the Life stores and writer lease could then be
        closed while an old tick is still mutating them.  Fail closed instead
        and retain the live thread reference so a later shutdown attempt can
        still join it.
        """

        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is None:
            return
        if thread is threading.current_thread():
            raise RuntimeError("life.scheduler.self_stop_forbidden")
        thread.join(timeout=max(0.0, float(timeout_seconds)))
        if thread.is_alive():
            raise TimeoutError("life.scheduler.stop_timeout")
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def status(self) -> dict[str, object]:
        thread = self._thread
        with self._lock:
            return {
                "running": bool(thread is not None and thread.is_alive() and not self._stop.is_set()),
                "interval_seconds": self.interval_seconds,
                "tick_count": self._tick_count,
                "last_tick_monotonic_ns": self._last_tick_monotonic_ns,
                "last_error_type": self._last_error_type,
                "last_error_code": self._last_error_code,
            }


__all__ = ["EmbeddedLifeScheduler"]
