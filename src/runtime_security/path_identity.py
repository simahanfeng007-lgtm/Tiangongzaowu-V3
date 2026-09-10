"""Existing-path observations, never permissions or publication grants.

Windows queries the DOS device mapping, then opens the complete native path
with OBJ_DONT_REPARSE, rejecting filesystem reparses at any depth, including
ancestors above the selected root. Device mappings are checked again before
returning and must agree with the opened handle's volume/share. Normalized
and opened NT names are queried on that SAME metadata-only handle. An observed
AppContainer whose mapping query is denied uses its DOS namespace with the
same no-reparse open and exact device/share depth checks. No drive
root access, ACL changes, following fallback or cached observations are used.
A returned pathname is not a durable handle or proof against later mutation;
callers retain their existing scope, byte-integrity and execution checks.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path, PureWindowsPath
import re


class PathIdentityError(ValueError):
    pass


def _windows_name(path: Path | PureWindowsPath) -> tuple[str, tuple[str, ...]]:
    """Translate only explicit DOS/UNC grammar, not arbitrary NT namespaces."""
    text = str(path)
    if text[:8].lower() == "\\\\?\\unc\\":
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    plain = PureWindowsPath(text)
    suffix = plain.parts[1:]
    drive = plain.drive
    local = re.fullmatch(r"[a-zA-Z]:", drive) is not None
    unc = drive.startswith("\\\\") and len(drive[2:].split("\\")) == 2
    components = (*drive[2:].split("\\"), *suffix) if unc else suffix
    if (not plain.is_absolute() or not (local or unc)
            or any(not value or value in (".", "..") or value.endswith((".", " "))
                   or any(char in value for char in '\x00:<>"|?*') for value in components)):
        raise PathIdentityError("path_not_canonical")
    native = "\\??\\UNC\\" + str(plain)[2:] if unc else "\\??\\" + str(plain)
    try:
        size = len(native.encode("utf-16-le"))
    except UnicodeError as exc:
        raise PathIdentityError("native_path_invalid") from exc
    if size > 65532:
        raise PathIdentityError("native_path_size_invalid")
    return native, suffix


@lru_cache(maxsize=1)
def _windows_path_api():
    # Cache API bindings and ABI types only, never handles or path evidence.
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", wintypes.LPVOID), ("SecurityQualityOfService", wintypes.LPVOID)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    native.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock), wintypes.LPVOID,
        wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG]
    native.NtCreateFile.restype = wintypes.LONG
    native.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
    native.RtlNtStatusToDosError.restype = wintypes.ULONG
    kernel.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.QueryDosDeviceW.restype = wintypes.DWORD
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, kernel, native, UnicodeString, ObjectAttributes, IoStatusBlock


def _query_device_mapping(ctypes, kernel, device: str) -> str:
    """Read the current DOS mapping without opening a drive or following a path.

    QueryDosDevice's first string is current; later strings may be old mappings.
    Only a direct device is accepted. SUBST/path redirects and alternate NT
    namespaces cannot smuggle filesystem traversal ahead of the no-reparse open.
    """
    output = ctypes.create_unicode_buffer(32768)
    length = kernel.QueryDosDeviceW(device, output, len(output))
    if length == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    if length >= len(output) or length <= len(output.value):
        raise PathIdentityError("device_mapping_invalid")
    mapping = output.value
    if re.fullmatch(r"\\Device\\[A-Za-z][A-Za-z0-9_-]*", mapping) is None:
        raise PathIdentityError("device_mapping_unsupported")
    return mapping


def _effective_appcontainer() -> bool:
    """Select a namespace from fresh OS evidence, never grant file access."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenThreadToken.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    thread = bool(security.OpenThreadToken(kernel.GetCurrentThread(), 0x8, True, ctypes.byref(token)))
    if not thread:
        error = ctypes.get_last_error()
        if error != 1008:  # Only ERROR_NO_TOKEN permits process-token lookup.
            raise ctypes.WinError(error)
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
    if token.value in (None, 0, wintypes.HANDLE(-1).value):
        raise PathIdentityError("native_token_invalid")
    try:
        def information(kind: int, allowed: tuple[int, ...]) -> int:
            value, length = wintypes.DWORD(), wintypes.DWORD()
            if not security.GetTokenInformation(token, kind, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            if length.value != ctypes.sizeof(value) or value.value not in allowed:
                raise PathIdentityError("native_token_information_invalid")
            return value.value

        if thread and information(9, (0, 1, 2, 3)) < 2:
            raise PermissionError("identification token cannot select a filesystem namespace")
        return bool(information(29, (0, 1)))  # TokenIsAppContainer.
    finally:
        if not kernel.CloseHandle(token):
            raise PathIdentityError("native_token_close_failed")


def _windows_final_path(path: Path | PureWindowsPath) -> PureWindowsPath:
    """Observe the full path without reparsing any filesystem component."""
    name, suffix = _windows_name(path)
    ctypes, wintypes, kernel, native, UnicodeString, ObjectAttributes, IoStatusBlock = _windows_path_api()
    unc = name.startswith("\\??\\UNC\\")
    device = "UNC" if unc else name[4:6]
    try:
        mapping = _query_device_mapping(ctypes, kernel, device)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 5 or not _effective_appcontainer():
            raise
        # AppContainer can deny QueryDosDevice while allowing NtCreateFile in
        # its own namespace. Keep OBJ_DONT_REPARSE; never retry a failed open
        # with weaker flags, a drive-root handle or a following API.
        mapping = None
    anchor = None
    share = PureWindowsPath(*PureWindowsPath(name).parts[3:5]) if unc else None
    if mapping is not None:
        name = mapping + ("\\" + name[8:] if unc else name[6:])
        native_parts = PureWindowsPath(name).parts
        anchor = PureWindowsPath(*native_parts[:-len(suffix)]) if suffix else PureWindowsPath(name)
    buffer = ctypes.create_unicode_buffer(name)
    size = len(name.encode("utf-16-le"))
    if size > 65532:
        raise PathIdentityError("native_path_size_invalid")
    string = UnicodeString(size, size + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE, no root handle or privilege grants.
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), None, ctypes.pointer(string), 0x1040, None, None)
    handle = wintypes.HANDLE()
    io = IoStatusBlock()
    # FILE_READ_ATTRIBUTES; share read/write/delete; FILE_OPEN (never create).
    # BACKUP_INTENT supports directory handles but does not enable privileges.
    status = native.NtCreateFile(ctypes.byref(handle), 0x80, ctypes.byref(attributes), ctypes.byref(io),
                                 None, 0, 7, 1, 0x4000, None, 0)
    if status != 0:
        if status & 0xFFFFFFFF == 0xC000050B:
            raise PathIdentityError("link_or_junction")
        raise ctypes.WinError(native.RtlNtStatusToDosError(status))
    if handle.value in (None, 0, wintypes.HANDLE(-1).value):
        raise PathIdentityError("native_handle_invalid")
    try:
        names = []
        for flags in (2, 10):  # VOLUME_NAME_NT with normalized/opened spelling.
            output = ctypes.create_unicode_buffer(32768)
            length = kernel.GetFinalPathNameByHandleW(handle, output, len(output), flags)
            if length == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if length >= len(output):
                raise PathIdentityError("native_path_size_invalid")
            result = PureWindowsPath(output.value)
            if result.parts[:2] != ("\\", "Device") or len(result.parts) < 3 + len(suffix):
                raise PathIdentityError("native_path_invalid")
            observed_anchor = PureWindowsPath(*result.parts[:-len(suffix)]) if suffix else result
            if anchor is not None and observed_anchor != anchor:
                raise PathIdentityError("physical_path_mismatch")
            if anchor is None:
                # A redirected DOS drive cannot add a hidden filesystem
                # prefix. UNC must preserve the exact caller's server/share.
                parts = observed_anchor.parts
                if (len(parts) != (5 if unc else 3)
                        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", parts[2]) is None
                        or (unc and PureWindowsPath(*parts[3:]) != share)):
                    raise PathIdentityError("physical_path_mismatch")
            names.append(result)
        physical, opened = names
        count = len(suffix)
        if count:
            if (PureWindowsPath(*opened.parts[-count:]) != PureWindowsPath(*suffix)
                    or PureWindowsPath(*opened.parts[:-count]) != PureWindowsPath(*physical.parts[:-count])):
                raise PathIdentityError("physical_path_mismatch")
        elif physical != opened:
            raise PathIdentityError("physical_path_mismatch")
        if mapping is not None and _query_device_mapping(ctypes, kernel, device) != mapping:
            raise PathIdentityError("device_mapping_changed")
        return physical
    finally:
        if not kernel.CloseHandle(handle):
            raise PathIdentityError("native_handle_close_failed")


def _verify_native_relative(root: PureWindowsPath, path: PureWindowsPath, relative: Path) -> None:
    if path != root.joinpath(*relative.parts):
        raise PathIdentityError("physical_path_mismatch")


def verify_relative_path(root: Path, path: Path) -> str:
    """Verify exact canonical relative names below an observed (possibly 8.3) root."""
    if (not root.is_absolute() or not path.is_absolute()
            or any(os.path.normcase(os.path.normpath(str(item))) != os.path.normcase(str(item))
                   for item in (root, path))):
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
        # The root's entire ancestry is now covered by the no-reparse open.
        # Preserve exact relative location/volume checks; do not open root.anchor.
        physical_root = _windows_final_path(root)
        _verify_native_relative(physical_root, _windows_final_path(path), relative)
    elif path.resolve(strict=True) != path:
        raise PathIdentityError("path_not_canonical")
    return relative.as_posix()


def resolve_existing_path(path: Path) -> Path:
    """Return observed long spelling, preserving canonical workspace bindings."""
    if os.name != "nt":
        return path.resolve(strict=True)
    absolute = path.absolute()
    physical = _windows_final_path(absolute)
    _, suffix = _windows_name(absolute)
    # No-reparse observation binds depth and spelling on the same handle. Keep
    # the caller's DOS/share anchor; independently reobserve any expanded alias.
    canonical = Path(absolute.anchor).joinpath(*physical.parts[-len(suffix):]) if suffix else absolute
    if canonical != absolute and _windows_final_path(canonical) != physical:
        raise PathIdentityError("physical_path_mismatch")
    return canonical


__all__ = ["PathIdentityError", "resolve_existing_path", "verify_relative_path"]
