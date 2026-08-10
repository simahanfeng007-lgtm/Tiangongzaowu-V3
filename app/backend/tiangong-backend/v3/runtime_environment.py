"""Runtime environment facts for Tiangong v3.

This module is deliberately factual: it observes the current user, known
folders, drives, and workspace. It does not decide whether an action is
allowed. Permission decisions live in permission_settings.py.
"""
from __future__ import annotations

import ctypes
import os
import platform
import socket
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any


_CACHE: dict[str, Any] = {"snapshot": None, "ts": 0.0}
_CACHE_TTL_SECONDS = 60.0


KNOWN_FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
}

ENV_KNOWN_FOLDER_KEYS = {
    "home": "TIANGONG_HOME_PATH",
    "desktop": "TIANGONG_DESKTOP_PATH",
    "downloads": "TIANGONG_DOWNLOADS_PATH",
    "documents": "TIANGONG_DOCUMENTS_PATH",
    "pictures": "TIANGONG_PICTURES_PATH",
    "videos": "TIANGONG_VIDEOS_PATH",
    "music": "TIANGONG_MUSIC_PATH",
}

DRIVE_TYPES = {
    0: "unknown",
    1: "no_root",
    2: "removable",
    3: "fixed",
    4: "network",
    5: "cdrom",
    6: "ramdisk",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(value: str) -> _GUID:
    parsed = uuid.UUID(str(value).strip("{}"))
    fields = parsed.fields
    return _GUID(
        fields[0],
        fields[1],
        fields[2],
        (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:]),
    )


def _clean_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        raw = os.path.expandvars(raw)
        return str(Path(raw).expanduser().resolve(strict=False))
    except Exception:
        return raw


def _path_fact(value: Any, *, source: str = "") -> dict[str, Any]:
    path = _clean_path(value)
    exists = False
    readable = False
    writable = False
    if path:
        try:
            p = Path(path)
            exists = p.exists()
            readable = os.access(path, os.R_OK)
            writable = os.access(path, os.W_OK)
        except Exception:
            pass
    return {
        "path": path,
        "source": source,
        "exists": exists,
        "readable": readable,
        "writable": writable,
    }


def _known_folder_api(name: str) -> str:
    if os.name != "nt":
        return ""
    guid_text = KNOWN_FOLDER_IDS.get(name)
    if not guid_text:
        return ""
    path_ptr = ctypes.c_wchar_p()
    try:
        folder_id = _guid(guid_text)
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(path_ptr),
        )
        if hr != 0 or not path_ptr.value:
            return ""
        return _clean_path(path_ptr.value)
    except Exception:
        return ""
    finally:
        try:
            if path_ptr:
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass


def _registry_known_folder(name: str) -> str:
    if os.name != "nt":
        return ""
    registry_names = {
        "desktop": "Desktop",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "documents": "Personal",
        "pictures": "My Pictures",
        "videos": "My Video",
        "music": "My Music",
    }
    value_name = registry_names.get(name)
    if not value_name:
        return ""
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
        return _clean_path(value)
    except Exception:
        return ""


def _fallback_known_folder(name: str, home: str) -> str:
    if name == "home":
        return _clean_path(home)
    folder_names = {
        "desktop": "Desktop",
        "downloads": "Downloads",
        "documents": "Documents",
        "pictures": "Pictures",
        "videos": "Videos",
        "music": "Music",
    }
    leaf = folder_names.get(name)
    if not leaf or not home:
        return ""
    return _clean_path(Path(home) / leaf)


def _known_folder(name: str, home: str) -> dict[str, Any]:
    env_key = ENV_KNOWN_FOLDER_KEYS.get(name, "")
    env_value = _clean_path(os.environ.get(env_key, "")) if env_key else ""
    if env_value:
        return _path_fact(env_value, source="environment")
    api_value = _known_folder_api(name)
    if api_value:
        return _path_fact(api_value, source="known_folder_api")
    registry_value = _registry_known_folder(name)
    if registry_value:
        return _path_fact(registry_value, source="registry")
    return _path_fact(_fallback_known_folder(name, home), source="fallback")


def _drive_free_space(root: str) -> tuple[int, int]:
    if os.name != "nt":
        return 0, 0
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    total_free = ctypes.c_ulonglong(0)
    try:
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(root),
            ctypes.byref(free),
            ctypes.byref(total),
            ctypes.byref(total_free),
        )
        if ok:
            return int(free.value), int(total.value)
    except Exception:
        pass
    return 0, 0


def _drive_volume(root: str) -> tuple[str, str]:
    if os.name != "nt":
        return "", ""
    volume_name = ctypes.create_unicode_buffer(260)
    fs_name = ctypes.create_unicode_buffer(260)
    serial = wintypes.DWORD()
    max_component = wintypes.DWORD()
    flags = wintypes.DWORD()
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            volume_name,
            len(volume_name),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            fs_name,
            len(fs_name),
        )
        if ok:
            return volume_name.value, fs_name.value
    except Exception:
        pass
    return "", ""


def _windows_drives() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    drives: list[dict[str, Any]] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        mask = 0
    system_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\/")
    for idx in range(26):
        if not (mask & (1 << idx)):
            continue
        letter = chr(ord("A") + idx)
        root = f"{letter}:\\"
        try:
            dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        except Exception:
            dtype = 0
        free_bytes, total_bytes = _drive_free_space(root)
        label, filesystem = _drive_volume(root)
        drives.append({
            "root": root,
            "letter": letter,
            "label": label,
            "type": DRIVE_TYPES.get(int(dtype), "unknown"),
            "filesystem": filesystem,
            "exists": Path(root).exists(),
            "readable": os.access(root, os.R_OK),
            "writable": os.access(root, os.W_OK),
            "free_bytes": free_bytes,
            "total_bytes": total_bytes,
            "is_system_drive": f"{letter}:".upper() == system_drive,
            "is_removable": int(dtype) == 2,
            "is_network": int(dtype) == 4,
        })
    return drives


def _portable_drives() -> list[dict[str, Any]]:
    roots = []
    if os.name != "nt":
        roots.append("/")
    rows = []
    for root in roots:
        try:
            stat = os.statvfs(root)
            free_bytes = int(stat.f_bavail * stat.f_frsize)
            total_bytes = int(stat.f_blocks * stat.f_frsize)
        except Exception:
            free_bytes = 0
            total_bytes = 0
        rows.append({
            "root": root,
            "letter": "",
            "label": "",
            "type": "fixed",
            "filesystem": "",
            "exists": Path(root).exists(),
            "readable": os.access(root, os.R_OK),
            "writable": os.access(root, os.W_OK),
            "free_bytes": free_bytes,
            "total_bytes": total_bytes,
            "is_system_drive": True,
            "is_removable": False,
            "is_network": False,
        })
    return rows


def _workspace() -> dict[str, Any]:
    try:
        from .workspace_settings import duqu_workspace_settings

        return duqu_workspace_settings()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "workspace": ""}


def collect_runtime_environment(*, refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if not refresh and _CACHE.get("snapshot") and now - float(_CACHE.get("ts") or 0) < _CACHE_TTL_SECONDS:
        return dict(_CACHE["snapshot"])

    home = _clean_path(os.environ.get("USERPROFILE") or str(Path.home()))
    known_folders = {
        name: _known_folder(name, home)
        for name in ("home", "desktop", "downloads", "documents", "pictures", "videos", "music")
    }
    workspace = _workspace()
    drives = _windows_drives() if os.name == "nt" else _portable_drives()
    snapshot = {
        "ok": True,
        "schema": "tiangong.v3.runtime_environment.v1",
        "collected_at": int(now),
        "platform": {
            "os": os.name,
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "machine": socket.gethostname(),
        "user": {
            "username": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "domain": os.environ.get("USERDOMAIN") or "",
            "whoami": "\\".join(
                item for item in (os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")) if item
            ),
            "home": known_folders["home"].get("path") or home,
        },
        "known_folders": known_folders,
        "drives": drives,
        "workspace": workspace,
    }
    _CACHE["snapshot"] = snapshot
    _CACHE["ts"] = now
    from world_understanding.post_commit import NativePostCommitEvent, notify_native_post_commit

    notify_native_post_commit(NativePostCommitEvent(
        source_kind="RUNTIME_ENVIRONMENT",
        source_native_id=f"runtime.environment.{snapshot['collected_at']}",
        producer_ref="v3.runtime_environment",
        payload=snapshot,
        occurred_at_ms=int(now * 1000),
    ))
    return dict(snapshot)


def runtime_environment_summary(*, refresh: bool = False) -> dict[str, Any]:
    env = collect_runtime_environment(refresh=refresh)
    folders = env.get("known_folders", {}) if isinstance(env, dict) else {}
    drives = env.get("drives", []) if isinstance(env, dict) else []
    workspace = env.get("workspace", {}) if isinstance(env, dict) else {}
    return {
        "ok": bool(env.get("ok")),
        "user": env.get("user", {}),
        "home": (folders.get("home") or {}).get("path") or "",
        "desktop": (folders.get("desktop") or {}).get("path") or "",
        "downloads": (folders.get("downloads") or {}).get("path") or "",
        "documents": (folders.get("documents") or {}).get("path") or "",
        "workspace": workspace.get("workspace") or "",
        "drives": [
            {
                "root": item.get("root"),
                "type": item.get("type"),
                "label": item.get("label"),
                "readable": item.get("readable"),
                "writable": item.get("writable"),
                "free_bytes": item.get("free_bytes"),
                "total_bytes": item.get("total_bytes"),
            }
            for item in drives
        ],
        "drive_roots": [str(item.get("root") or "") for item in drives if item.get("root")],
    }
