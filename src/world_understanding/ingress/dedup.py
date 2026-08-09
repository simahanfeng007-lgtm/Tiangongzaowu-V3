"""In-memory P2 idempotency gate.

No database, worker, daemon, timer or background thread is created here.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import Condition, RLock

from .receipt import IngressReceipt


class DedupGate:
    def __init__(self) -> None:
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._inflight: set[str] = set()
        self._completed: dict[str, IngressReceipt] = {}

    def run_once(self, key: str, operation: Callable[[], IngressReceipt]) -> IngressReceipt:
        with self._condition:
            while key in self._inflight:
                self._condition.wait()
            cached = self._completed.get(key)
            if cached is not None:
                return cached
            self._inflight.add(key)

        try:
            receipt = operation()
        except Exception:
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()
            raise

        with self._condition:
            self._completed[key] = receipt
            self._inflight.discard(key)
            self._condition.notify_all()
            return receipt

    def completed_count(self) -> int:
        with self._lock:
            return len(self._completed)


__all__ = ["DedupGate"]
