"""Source launcher for the Tiangong v3 desktop execution backend.

This module is the canonical development/source entry used by Electron when a
frozen ``tiangong-backend.exe`` is not present.  It starts the historical v3
scheduler and keeps the process alive until Windows/Electron asks it to stop.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path


def _install_source_paths() -> None:
    here = Path(__file__).resolve()
    repository_root = here.parents[4]
    src = repository_root / "src"
    backend_root = here.parents[1]
    for candidate in (str(src), str(backend_root)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def main() -> int:
    _install_source_paths()
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ.setdefault("PORT", "7174")

    from .duihua_qiaojie import QIAOJIE
    from .zongdiaodu import Zongdiaodu

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()
        server = getattr(QIAOJIE, "_fuwuqi", None)
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if signum is not None:
            try:
                signal.signal(signum, request_stop)
            except (OSError, RuntimeError, ValueError):
                pass

    scheduler = Zongdiaodu()
    scheduler.qidong()
    while not stop_event.wait(0.5):
        server = getattr(QIAOJIE, "_fuwuqi", None)
        thread = getattr(QIAOJIE, "_xiancheng", None)
        if server is None or thread is None or not thread.is_alive():
            return 3
    try:
        server = getattr(QIAOJIE, "_fuwuqi", None)
        if server is not None:
            server.server_close()
    finally:
        # Stop engines when the corresponding implementation provides a
        # lifecycle method; old versions simply end with the process.
        for target in (getattr(scheduler, "xintiao", None),):
            for name in ("tingzhi", "stop", "shutdown"):
                function = getattr(target, name, None)
                if callable(function):
                    try:
                        function()
                    except Exception:
                        pass
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
