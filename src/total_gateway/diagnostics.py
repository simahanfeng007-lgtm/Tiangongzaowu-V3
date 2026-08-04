"""Portable best-effort diagnostics for the source and frozen gateway builds."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()


def runtime_log_dir() -> Path:
    explicit = str(os.environ.get("TIANGONG_GATEWAY_LOG_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    state_root = str(os.environ.get("TIANGONG_DESKTOP_STATE_DIR") or "").strip()
    if state_root:
        return Path(state_root).expanduser() / "logs"
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if appdata:
        return Path(appdata) / "tiangong-v3-qiyuan" / "runtime" / "logs"
    return Path.home() / ".tiangong" / "v3" / "runtime" / "logs"


def diagnostic_log(message: object, *, filename: str = "gateway_req.log") -> None:
    """Append a diagnostic line without ever affecting the request path."""
    try:
        safe_name = Path(filename).name or "gateway_req.log"
        path = runtime_log_dir() / safe_name
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        line = str(message or "").replace("\x00", "").rstrip("\r\n")
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except Exception:
        return
