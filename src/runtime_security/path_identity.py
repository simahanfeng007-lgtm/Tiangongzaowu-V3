"""Shared existing-path identity checks, not permission or publication grants.

Windows AppContainer can open contained files but deny the DOS-volume query
used by pathlib strict resolution. Query normalized physical NT identities on
metadata-read handles instead. Never fall back to an unverified lexical path,
grant ACLs, cache path observations, or change process-wide pathlib behavior.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path, PureWindowsPath


class PathIdentityError(ValueError):
    pass


@lru_cache(maxsize=1)
def _windows_path_api():
    # Cache API bindings only, never a path, handle or observed file identity.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, kernel


def _windows_final_path(path: Path) -> PureWindowsPath:
    """Query a normalized physical NT path, including a drive/share anchor."""
    ctypes, wintypes, kernel = _windows_path_api()
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_unicode_buffer(8192)
        size = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 2)
        if size == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if size >= len(buffer):
            raise PathIdentityError("native_path_size_invalid")
        result = PureWindowsPath(buffer.value)
        if (result.parts[:2] != ("\\", "Device") or len(result.parts) < 3
                or len(result.parts) == 3 and path != Path(path.anchor)):
            raise PathIdentityError("native_path_invalid")
        return result
    finally:
        kernel.CloseHandle(handle)


def _verify_native_relative(root: PureWindowsPath, path: PureWindowsPath, relative: Path) -> None:
    if path != root.joinpath(*relative.parts):
        raise PathIdentityError("physical_path_mismatch")


def verify_relative_path(root: Path, path: Path) -> str:
    """Verify an existing canonical root-relative path without following links."""
    if (not root.is_absolute() or not path.is_absolute()
            or os.path.normcase(os.path.normpath(str(path))) != os.path.normcase(str(path))):
        raise PathIdentityError("path_not_canonical")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PathIdentityError("path_outside_installation") from exc
    for current in (path, *path.parents):
        if (current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)())
                or getattr(current.lstat(), "st_file_attributes", 0) & 0x400):
            raise PathIdentityError("link_or_junction")
        if current == root:
            break
    if os.name == "nt":
        physical_root = _windows_final_path(root)
        # Bind the entire root suffix to its own physical drive/share. This
        # catches redirected ancestors without asking for profile-directory ACLs.
        _verify_native_relative(_windows_final_path(Path(root.anchor)), physical_root, Path(*root.parts[1:]))
        _verify_native_relative(physical_root, _windows_final_path(path), relative)
    elif path.resolve(strict=True) != path:
        raise PathIdentityError("path_not_canonical")
    return relative.as_posix()


def resolve_existing_path(path: Path) -> Path:
    """Return an observed existing path; Windows aliases/redirects fail closed.

    POSIX retains pathlib's strict resolution. Windows accepts an already
    canonical path only after native volume/root checks; lexical absolute()
    alone is never evidence. Callers still own file type, scope and permissions.
    """
    if os.name != "nt":
        return path.resolve(strict=True)
    absolute = path.absolute()
    verify_relative_path(absolute, absolute)
    return absolute


__all__ = ["PathIdentityError", "resolve_existing_path", "verify_relative_path"]
