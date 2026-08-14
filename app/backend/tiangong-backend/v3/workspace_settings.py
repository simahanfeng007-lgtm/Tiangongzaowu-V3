"""Workspace settings shared by desktop UI and backend tools."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .peizhi import WORKSPACE_SETTINGS_LUJING
from .settings_persistence import atomic_write_json, read_json_authority


def _env_workspace_root() -> str:
    return (
        os.environ.get("TIANGONG_DESKTOP_WORKSPACE_ROOT")
        or os.environ.get("TIANGONG_WORKSPACE_ROOT")
        or ""
    )


def _default_workspace_root() -> Path:
    raw = _env_workspace_root()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    return (Path.home() / ".tiangong" / "v3" / "workspaces").resolve(strict=False)


def _normalize_workspace_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return "full" if raw == "full" else "workspace"


def _load_raw() -> dict[str, Any]:
    data, _state = read_json_authority(WORKSPACE_SETTINGS_LUJING)
    return data if isinstance(data, dict) else {}


def _normalize_workspace_path(value: str | os.PathLike[str] | None, *, fallback: Path | None = None) -> Path:
    raw = str(value or "").strip()
    path = Path(raw).expanduser() if raw else (fallback or _default_workspace_root())
    resolved = path.resolve(strict=False)
    anchor = Path(resolved.anchor).resolve(strict=False) if resolved.anchor else resolved
    if resolved == anchor:
        raise ValueError("workspace_cannot_be_filesystem_root")
    return resolved


def duqu_workspace_settings() -> dict[str, Any]:
    # In the embedded desktop runtime Electron validates a workspace change,
    # restarts 7184, and injects this value into both the Gateway and backend.
    # That authority must outrank this legacy backend-local preference; if it
    # did not, the tool could request an Omni grant for a different directory
    # from the one bound into the execution ticket.
    data, integrity = read_json_authority(WORKSPACE_SETTINGS_LUJING)
    if not isinstance(data, dict):
        data = {}
    desktop_authority = _env_workspace_root()
    if desktop_authority:
        configured = ""
        root = _normalize_workspace_path(desktop_authority)
        source = "desktop_authority"
    else:
        configured = str(data.get("workspace") or "").strip()
        root = _normalize_workspace_path(configured, fallback=_default_workspace_root())
        source = "configured" if configured else "default"
    root.mkdir(parents=True, exist_ok=True)
    mode_raw = os.environ.get("TIANGONG_WORKSPACE_MODE") or data.get("workspace_mode") or ""
    workspace_mode = _normalize_workspace_mode(mode_raw)
    return {
        "ok": True,
        "workspace": str(root),
        "configured_workspace": configured,
        "source": source,
        "workspace_mode": workspace_mode,
        "exists": root.exists(),
        "writable": os.access(root, os.W_OK),
        "settings_path": str(WORKSPACE_SETTINGS_LUJING),
        "settings_integrity": integrity,
        "error_code": "SETTINGS_AUTHORITY_CORRUPTED" if integrity == "corrupted" else "",
    }


def duqu_workspace_root() -> Path:
    return Path(str(duqu_workspace_settings().get("workspace") or "")).expanduser().resolve(strict=False)


def baocun_workspace_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    has_workspace = any(key in payload for key in ("workspace", "workspaceRoot", "path"))
    has_mode = any(key in payload for key in ("workspace_mode", "mode"))
    if not has_workspace and not has_mode:
        return duqu_workspace_settings()
    data = _load_raw()
    # 第一性原理：字段级更新。只改 workspace_mode 时绝不触碰已保存的
    # workspace 权威；字段为空表示"未提供新值"，保留既有配置，绝不用
    # 默认工作区覆盖用户权威。
    if has_workspace:
        raw_workspace = (
            payload.get("workspace")
            if "workspace" in payload
            else payload.get("workspaceRoot")
            if "workspaceRoot" in payload
            else payload.get("path")
        )
        raw_text = str(raw_workspace or "").strip()
        if raw_text:
            workspace = _normalize_workspace_path(raw_text)
            workspace.mkdir(parents=True, exist_ok=True)
            data["workspace"] = str(workspace)
            os.environ["TIANGONG_DESKTOP_WORKSPACE_ROOT"] = str(workspace)
            os.environ["TIANGONG_WORKSPACE_ROOT"] = str(workspace)
    if has_mode:
        workspace_mode = _normalize_workspace_mode(
            payload.get("workspace_mode")
            if "workspace_mode" in payload
            else payload.get("mode")
        )
        data["workspace_mode"] = workspace_mode
        os.environ["TIANGONG_WORKSPACE_MODE"] = workspace_mode
    data["updated_at"] = int(time.time())
    atomic_write_json(WORKSPACE_SETTINGS_LUJING, data)
    return duqu_workspace_settings()
