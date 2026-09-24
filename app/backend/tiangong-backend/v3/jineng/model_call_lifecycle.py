"""One deadline and cancellation fence for a model call, including its retries.

This is an in-memory child of the Gateway run, not a task state authority.
Closing a local response does not assert that a provider stopped billing.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import threading
import time
from typing import Callable, TypeVar


class ModelCallStopped(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ModelCallLifecycle:
    def __init__(self, seconds: float, cancel_check: Callable[[], bool] | None = None):
        self.started_at = time.monotonic()
        self.deadline = self.started_at + max(0.0, seconds)
        self.cancel_check = cancel_check
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._reason = ""
        self._closers: list[Callable[[], None]] = []

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def stop(self, reason: str) -> None:
        with self._lock:
            if self._reason or self._done.is_set():
                return
            self._reason = reason
            closers, self._closers = self._closers, []
        # Never hold the callback fence while closing a socket.
        for close in closers:
            try:
                close()
            except Exception:
                pass

    def check(self) -> None:
        if self.cancel_check and self.cancel_check():
            self.stop("cancelled")
        if not self.remaining:
            self.stop("deadline_exceeded")
        with self._lock:
            if self._reason:
                raise ModelCallStopped(self._reason)
            if self._done.is_set():
                raise ModelCallStopped("call_closed")

    @contextmanager
    def response(self, close: Callable[[], None]):
        self.check()
        with self._lock:
            if self._reason or self._done.is_set():
                raise ModelCallStopped(self._reason or "call_closed")
            self._closers.append(close)
        try:
            yield
        finally:
            with self._lock:
                if close in self._closers:
                    self._closers.remove(close)

    def guard(self, callback):
        if callback is None:
            return None

        def guarded(*args, **kwargs):
            self.check()
            with self._lock:
                if self._reason or self._done.is_set():
                    raise ModelCallStopped(self._reason or "call_closed")
                return callback(*args, **kwargs)
        return guarded

    def wait(self, seconds: float) -> None:
        until = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < until:
            self.check()
            self._done.wait(min(0.05, self.remaining, until - time.monotonic()))
        self.check()

    def start(self) -> None:
        def watch():
            while not self._done.wait(min(0.05, self.remaining)):
                try:
                    self.check()
                except ModelCallStopped:
                    return
        threading.Thread(target=watch, daemon=True, name="model-call-cancel").start()

    def finish(self) -> None:
        with self._lock:
            self._done.set()


_CURRENT: ContextVar[ModelCallLifecycle | None] = ContextVar("model_call_lifecycle", default=None)


def current_model_call() -> ModelCallLifecycle | None:
    return _CURRENT.get()


@contextmanager
def model_call_scope(seconds: float, cancel_check=None):
    parent = current_model_call()
    if parent is not None:
        parent.check()
        yield parent
        return
    lifecycle = ModelCallLifecycle(seconds, cancel_check)
    token = _CURRENT.set(lifecycle)
    lifecycle.start()
    try:
        lifecycle.check()
        yield lifecycle
    finally:
        lifecycle.finish()
        _CURRENT.reset(token)


T = TypeVar("T")


def run_model_call(call: Callable[[ModelCallLifecycle], T], *, seconds: float, cancel_check=None) -> T:
    """Fence uncooperative network/DNS code without accepting a late result."""
    with model_call_scope(seconds, cancel_check) as lifecycle:
        finished = threading.Event()
        holder = {}

        def run():
            try:
                holder["value"] = call(lifecycle)
            except Exception as exc:
                holder["error"] = exc
            finally:
                finished.set()

        context = copy_context()
        threading.Thread(target=lambda: context.run(run), daemon=True, name="model-call").start()
        while not finished.wait(min(0.05, lifecycle.remaining)):
            lifecycle.check()
        lifecycle.check()
        if "error" in holder:
            raise holder["error"]
        return holder["value"]
