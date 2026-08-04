"""Workspace settings shared by desktop UI and backend tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .peizhi import WORKSPACE_SETTINGS_LUJING


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


def _load_raw() -> dict[str, Any]:
    if not WORKSPACE_SETTINGS_LUJING.exists():
        return {}
    try:
        data = json.loads(WORKSPACE_SETTINGS_LUJING.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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
    desktop_authority = _env_workspace_root()
    if desktop_authority:
        configured = ""
        root = _normalize_workspace_path(desktop_authority)
        source = "desktop_authority"
    else:
        data = _load_raw()
        configured = str(data.get("workspace") or "").strip()
        root = _normalize_workspace_path(configured, fallback=_default_workspace_root())
        source = "configured" if configured else "default"
    root.mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "workspace": str(root),
        "configured_workspace": configured,
        "source": source,
        "exists": root.exists(),
        "writable": os.access(root, os.W_OK),
        "settings_path": str(WORKSPACE_SETTINGS_LUJING),
    }


def duqu_workspace_root() -> Path:
    return Path(str(duqu_workspace_settings().get("workspace") or "")).expanduser().resolve(strict=False)


def baocun_workspace_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    raw_workspace = (
        payload.get("workspace")
        if "workspace" in payload
        else payload.get("workspaceRoot")
        if "workspaceRoot" in payload
        else payload.get("path")
    )
    workspace = _normalize_workspace_path(str(raw_workspace or ""))
    workspace.mkdir(parents=True, exist_ok=True)
    data = _load_raw()
    data["workspace"] = str(workspace)
    data["updated_at"] = int(__import__("time").time())
    WORKSPACE_SETTINGS_LUJING.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_SETTINGS_LUJING.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.environ["TIANGONG_DESKTOP_WORKSPACE_ROOT"] = str(workspace)
    os.environ["TIANGONG_WORKSPACE_ROOT"] = str(workspace)
    return duqu_workspace_settings()
