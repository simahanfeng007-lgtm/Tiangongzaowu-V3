"""Private Windows file primitives; no ticket, crypto or execution authority.

An AppContainer is a dual principal. New encrypted files must retain both the
actual user and the exact container, not Everyone or all application packages.
Never amend an existing object's ACL to make a failed access succeed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re

from contracts import canonical_sha256
from runtime_security.path_identity import resolve_existing_path


class WindowsPrivateFileError(ValueError):
    pass


def current_appcontainer_principal() -> tuple[str, str] | None:
    """Observe the effective token, never environment variables or cached identity.

    Moved from release staging so encrypted files and private directories use
    the same OS principal observation. Only ERROR_NO_TOKEN permits fallback
    from the effective thread token to the process token.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    security.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenThreadToken.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.IsValidSid.argtypes = [wintypes.LPVOID]
    security.IsValidSid.restype = wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    # Respect effective impersonation. Only ERROR_NO_TOKEN permits process
    # fallback; access-denied or unobservable identity must reject creation.
    thread_token = bool(security.OpenThreadToken(kernel.GetCurrentThread(), 0x8, True, ctypes.byref(token)))
    if not thread_token:
        error = ctypes.get_last_error()
        if error != 1008:
            raise ctypes.WinError(error)
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        flag, length = wintypes.DWORD(), wintypes.DWORD()
        if not security.GetTokenInformation(token, 29, ctypes.byref(flag), ctypes.sizeof(flag), ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        if length.value != ctypes.sizeof(flag) or flag.value not in (0, 1):
            raise WindowsPrivateFileError("release staging token flag is invalid")
        if not flag.value:
            # TokenIsAppContainer=0 cannot authorize an identification-only
            # caller. OpenThreadToken already establishes an impersonation
            # token; the ordinary process-token path keeps its old behavior.
            if thread_token:
                level = wintypes.DWORD()
                if not security.GetTokenInformation(token, 9, ctypes.byref(level), ctypes.sizeof(level), ctypes.byref(length)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if length.value != ctypes.sizeof(level) or level.value not in (0, 1, 2, 3):
                    raise WindowsPrivateFileError("private file token impersonation evidence is invalid")
                if level.value < 2:  # SecurityAnonymous / SecurityIdentification.
                    raise PermissionError("private file token impersonation level cannot authorize access")
            return None

        def sid_text(kind: int) -> str:
            required = wintypes.DWORD()
            ok = security.GetTokenInformation(token, kind, None, 0, ctypes.byref(required))
            if ok or ctypes.get_last_error() != 122 or not ctypes.sizeof(ctypes.c_void_p) <= required.value <= 65536:
                raise WindowsPrivateFileError("release staging SID evidence is unavailable")
            data = ctypes.create_string_buffer(required.value)
            if not security.GetTokenInformation(token, kind, data, len(data), ctypes.byref(required)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not ctypes.sizeof(ctypes.c_void_p) <= required.value <= len(data):
                raise WindowsPrivateFileError("release staging SID evidence size changed")
            # TOKEN_USER and TOKEN_APPCONTAINER_INFORMATION start with a SID*.
            sid = ctypes.c_void_p.from_buffer(data).value
            if not sid or not security.IsValidSid(sid):
                raise WindowsPrivateFileError("release staging SID is invalid")
            text = wintypes.LPWSTR()
            if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                value = text.value or ""
                if re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", value) is None:
                    raise WindowsPrivateFileError("release staging SID text is invalid")
                return value
            finally:
                if kernel.LocalFree(text):
                    raise WindowsPrivateFileError("release staging SID cleanup failed")

        user_sid, app_sid = sid_text(1), sid_text(31)
        if re.fullmatch(r"S-1-15-2-(?:[0-9]+-){6}[0-9]+", app_sid) is None:
            raise WindowsPrivateFileError("release staging requires an exact AppContainer SID")
        return user_sid, app_sid
    finally:
        if not kernel.CloseHandle(token):
            raise WindowsPrivateFileError("release staging token cleanup failed")


def validate_private_dacl(descriptor: str, principal: tuple[str, str]) -> None:
    """Accept only a protected, explicit, two-principal full-control file DACL."""
    user, package = principal
    if (re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", user) is None
            or re.fullmatch(r"S-1-15-2-(?:[0-9]+-){6}[0-9]+", package) is None
            or user == package):
        raise WindowsPrivateFileError("private file principal is invalid")
    # ConvertSecurityDescriptorToStringSecurityDescriptorW canonicalizes rights.
    # Permit only equivalent full-control spelling and ACE order. AI is a
    # historical auto-inherited control flag, never an inherited ACE (ID).
    ace = r"\(A;;(?:FA|0x1f01ff);;;(S-1-(?:[0-9]+-)+[0-9]+)\)"
    if re.fullmatch(r"D:P(?:AI)?(?:" + ace + r"){2}", descriptor) is None:
        raise WindowsPrivateFileError("private file DACL is not exact and protected")
    trustees = re.findall(ace, descriptor)
    if len(trustees) != 2 or set(trustees) != {user, package}:
        raise WindowsPrivateFileError("private file DACL principal mismatch")


def _private_file_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    signatures = {
        "CreateFileW": ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "LocalFree": ([wintypes.LPVOID], wintypes.LPVOID),
        "WriteFile": ([wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID], wintypes.BOOL),
        "ReadFile": ([wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID], wintypes.BOOL),
        "FlushFileBuffers": ([wintypes.HANDLE], wintypes.BOOL),
        "SetFileInformationByHandle": ([wintypes.HANDLE, ctypes.c_int,
            wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    signatures = {
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": ([wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID], wintypes.BOOL),
        "GetSecurityInfo": ([wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID)], wintypes.DWORD),
        "GetSecurityDescriptorControl": ([wintypes.LPVOID, ctypes.POINTER(wintypes.WORD),
            ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "GetSecurityDescriptorDacl": ([wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        "GetAce": ([wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)], wintypes.BOOL),
        "IsValidSid": ([wintypes.LPVOID], wintypes.BOOL),
        "GetLengthSid": ([wintypes.LPVOID], wintypes.DWORD),
        "ConvertSidToStringSidW": ([wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(security, name)
        function.argtypes, function.restype = arguments, result
    return ctypes, wintypes, kernel, security


def _observed_dacl(handle, api) -> str:
    """Inspect binary ACEs, not localized or abbreviated SDDL trustee names.

    Windows can render the current user as LA rather than its numeric SID.
    Never guess which account an abbreviation denotes: read each ACE's SID.
    """
    ctypes, wintypes, kernel, security = api
    descriptor = wintypes.LPVOID()
    error = security.GetSecurityInfo(handle, 1, 4, None, None, None, None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        if not descriptor.value:
            raise WindowsPrivateFileError("private file descriptor missing")
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not control.value & 0x1000:  # SE_DACL_PROTECTED
            raise WindowsPrivateFileError("private file DACL is not protected")
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
        if not security.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not present.value or not acl.value or defaulted.value:
            raise WindowsPrivateFileError("private file DACL missing or defaulted")
        # ACL header is eight bytes, with bounded size and exact ACE count.
        header = ctypes.string_at(acl.value, 8)
        size = int.from_bytes(header[2:4], "little")
        count = int.from_bytes(header[4:6], "little")
        if header[0] not in (2, 4) or not 8 <= size <= 65535 or count != 2:
            raise WindowsPrivateFileError("private file ACL shape is invalid")
        trustees = []
        for index in range(count):
            ace = wintypes.LPVOID()
            if not security.GetAce(acl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not ace.value or not acl.value + 8 <= ace.value <= acl.value + size - 16:
                raise WindowsPrivateFileError("private file ACE bounds are invalid")
            prefix = ctypes.string_at(ace.value, 8)
            ace_size = int.from_bytes(prefix[2:4], "little")
            if (prefix[0] != 0 or prefix[1] != 0 or int.from_bytes(prefix[4:8], "little") != 0x1F01FF
                    or ace_size < 16 or ace.value + ace_size > acl.value + size):
                raise WindowsPrivateFileError("private file ACE rights or inheritance are invalid")
            sid = ace.value + 8
            sid_header = ctypes.string_at(sid, 8)
            if sid_header[0] != 1 or sid_header[1] > 15 or 8 + 4 * sid_header[1] != ace_size - 8:
                raise WindowsPrivateFileError("private file SID bounds are invalid")
            if not security.IsValidSid(sid) or security.GetLengthSid(sid) != ace_size - 8:
                raise WindowsPrivateFileError("private file SID is invalid")
            text = wintypes.LPWSTR()
            if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                trustees.append(text.value or "")
            finally:
                if kernel.LocalFree(text):
                    raise WindowsPrivateFileError("private file SID cleanup failed")
        return "D:P" + "".join(f"(A;;FA;;;{sid})" for sid in trustees)
    finally:
        if descriptor and kernel.LocalFree(descriptor):
            raise WindowsPrivateFileError("private file descriptor cleanup failed")


def _acl_evidence(principal: tuple[str, str]) -> tuple[str, str]:
    user, package = principal
    return user, canonical_sha256({
        "domain": "tiangong.container-private-file.v1",
        "owner_sid_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
        "appcontainer_sid_sha256": hashlib.sha256(package.encode("utf-8")).hexdigest(),
        "protected_dacl": True, "explicit_full_control_aces": 2,
    })


@contextmanager
def _file_handle(path: Path, principal: tuple[str, str], *, create: bool):
    """Own the handle through I/O and clean ONLY an object this call created."""
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise WindowsPrivateFileError("private file path must be absolute")
    parent = resolve_existing_path(path.parent)
    target = parent / path.name
    if not create and (target.is_symlink() or not target.is_file() or target.stat().st_nlink != 1):
        raise WindowsPrivateFileError("private file is missing, linked or unsafe")
    api = _private_file_api()
    ctypes, wintypes, kernel, security = api
    descriptor, handle, failure = wintypes.LPVOID(), None, None
    try:
        attributes = None
        if create:
            user, package = principal
            sddl = f"D:P(A;;FA;;;{user})(A;;FA;;;{package})"
            validate_private_dacl(sddl, principal)
            if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not descriptor.value:
                raise WindowsPrivateFileError("private file descriptor missing")
            class SecurityAttributes(ctypes.Structure):
                _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID), ("inherit", wintypes.BOOL)]
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        # New files request DELETE solely for handle-owned failed-write cleanup.
        # No sharing, no handle inheritance, no overwrite or ACL repair fallback.
        access = (0x40000000 | 0x10000 if create else 0x80000000) | 0x20000
        handle = kernel.CreateFileW(str(target), access, 0,
            None if attributes is None else ctypes.byref(attributes), 1 if create else 3, 0x00200080, None)
        if handle in (None, 0, wintypes.HANDLE(-1).value):
            handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        validate_private_dacl(_observed_dacl(handle, api), principal)
        yield handle, api
    except BaseException as error:
        failure = error
        if create and handle is not None:
            delete = wintypes.BOOL(True)  # FILE_DISPOSITION_INFO / FileDispositionInfo
            if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
                error.add_note("private_file_cleanup_failed: " + str(ctypes.get_last_error()))
        raise
    finally:
        errors = []
        if handle is not None and not kernel.CloseHandle(handle):
            errors.append("private file handle cleanup failed")
        if descriptor and kernel.LocalFree(descriptor):
            errors.append("private file descriptor cleanup failed")
        if errors:
            if failure is not None:
                for message in errors:
                    failure.add_note(message)
            else:
                raise WindowsPrivateFileError("; ".join(errors))


def write_container_private_file(path: Path, payload: bytes) -> tuple[str, str] | None:
    """Create only a new encrypted blob/metadata; None means an ordinary token."""
    principal = current_appcontainer_principal()
    if principal is None:
        return None
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 1048576:
        raise WindowsPrivateFileError("private payload size or type is invalid")
    with _file_handle(path, principal, create=True) as (handle, api):
        ctypes, wintypes, kernel, _ = api
        written = wintypes.DWORD()
        data = ctypes.create_string_buffer(payload)
        if not kernel.WriteFile(handle, data, len(payload), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(payload):
            raise WindowsPrivateFileError("private file short write")
        if not kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    return _acl_evidence(principal)


def read_container_private_file(path: Path) -> tuple[bytes, str, str] | None:
    """Check actual ACEs on the same read handle before returning ciphertext."""
    principal = current_appcontainer_principal()
    if principal is None:
        return None
    with _file_handle(path, principal, create=False) as (handle, api):
        ctypes, wintypes, kernel, _ = api
        count = wintypes.DWORD()
        data = ctypes.create_string_buffer(1048577)
        if not kernel.ReadFile(handle, data, len(data), ctypes.byref(count), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not 0 < count.value <= 1048576:
            raise WindowsPrivateFileError("private file exceeds size limit or is empty")
        payload = data.raw[:count.value]
    user, digest = _acl_evidence(principal)
    return payload, user, digest
