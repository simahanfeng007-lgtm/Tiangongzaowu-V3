"""Preserve private directory ACLs inside an actual Windows AppContainer.

CPython 3.12.4+ gives mkdir(0700) a protected ACL which omits the
AppContainer SID. Keep those owner/system/admin entries and add only this
process token's package SID. Host Python and all other modes are unchanged.
Source reference: CPython v3.12.10 Modules/posixmodule.c os_mkdir_impl.
This module is copied into the bundled Python runtime by the installer.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys


def _enable_private_python_path(sid_text):
    """Restore script/-m import semantics only inside this SID's native store."""
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    ole32 = ctypes.OleDLL("ole32")
    userenv.GetAppContainerFolderPath.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)]
    userenv.GetAppContainerFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    output = wintypes.LPWSTR()
    try:
        if userenv.GetAppContainerFolderPath(sid_text, ctypes.byref(output)) != 0 or not output.value:
            raise OSError("AppContainer private Python import root unavailable")
        # This OS-returned SID root is an authority anchor, not a directory the
        # lowbox may enumerate. Resolve only the accessible candidate handle;
        # probing AC ancestors itself is denied by the AppContainer namespace.
        storage = Path(output.value)
    finally:
        if output: ole32.CoTaskMemFree(output)
    entry = sys.argv[0] if sys.argv else ""
    directory = Path.cwd() if entry in {"", "-c", "-m", "-"} else Path(entry).absolute().parent
    # realpath/GetFinalPathNameByHandle(normalized) probes AC ancestors which
    # the lowbox deliberately cannot enumerate. The broker already verified
    # the copied workspace has no reparse entries before launch. Retain its
    # absolute private spelling, then check the actual directory attributes.
    directory = Path(os.path.abspath(directory))
    def native(path):
        return os.path.normcase(str(path)).removeprefix("\\\\?\\")
    root_text, directory_text = native(storage), native(directory)
    try:
        if os.path.commonpath((root_text, directory_text)) != root_text:
            return
    except ValueError:
        return
    if getattr(directory.lstat(), "st_file_attributes", 0) & 0x400:
        raise OSError("AppContainer Python import directory contains reparse point")
    if directory.is_dir() and str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


def install():
    if os.name != "nt": return False
    if getattr(os.mkdir, "_tiangong_appcontainer_private_acl", False): return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        active, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi.GetTokenInformation(token, 29, ctypes.byref(active), ctypes.sizeof(active), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not active.value: return False
        advapi.GetTokenInformation(token, 31, None, 0, ctypes.byref(size))
        info = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 31, info, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p)).contents.value
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try: sid_text = text.value
        finally: kernel.LocalFree(text)
    finally: kernel.CloseHandle(token)
    original = os.mkdir
    def mkdir(path, mode=0o777, *, dir_fd=None):
        if mode != 0o700 or dir_fd is not None:
            return original(path, mode, dir_fd=dir_fd)
        value = os.fsdecode(path)
        if "\0" in value: raise ValueError("embedded null character")
        sys.audit("os.mkdir", path, mode, -1)
        descriptor, size = ctypes.c_void_p(), wintypes.DWORD()
        sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)(A;OICI;FA;;;" + sid_text + ")"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            sa = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
            if not kernel.CreateDirectoryW(value, ctypes.byref(sa)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally: kernel.LocalFree(descriptor)
    mkdir._tiangong_appcontainer_private_acl = True
    os.mkdir = mkdir
    _enable_private_python_path(sid_text)
    return True
