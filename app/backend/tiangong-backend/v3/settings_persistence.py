"""Shared atomic persistence for settings-authority JSON files.

第一性原理：设置权威文件必须原子写（临时文件 + fsync + os.replace），
崩溃时要么是完整旧内容、要么是完整新内容；损坏时读路径必须显式暴露
``settings_integrity`` 状态，绝不静默当作"用户从没设置过"。
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

MISSING = "missing"
OK = "ok"
RECOVERED = "recovered_from_backup"
CORRUPTED = "corrupted"


def atomic_write_json(path: Path, payload: Any, *, backup: bool = True) -> None:
    """Write JSON atomically; optionally keep the previous content as .bak."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        if backup and path.exists():
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_json_authority(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read a settings-authority file with explicit integrity state.

    Returns ``(data, state)`` where data is None for missing/corrupted and
    state is one of missing / ok / recovered_from_backup / corrupted.
    """
    if not path.exists():
        return None, MISSING
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            return raw, OK
        return None, CORRUPTED
    except (OSError, ValueError, TypeError):
        pass
    backup_path = path.with_name(path.name + ".bak")
    if backup_path.exists():
        try:
            raw = json.loads(backup_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                return raw, RECOVERED
        except (OSError, ValueError, TypeError):
            pass
    return None, CORRUPTED