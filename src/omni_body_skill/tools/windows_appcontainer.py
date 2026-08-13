"""Windows AppContainer + Job Object process launcher used by SandboxRunner."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Mapping, Sequence, Any


if os.name != "nt":  # pragma: no cover
    raise ImportError("windows_appcontainer is Windows-only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
ole32 = ctypes.OleDLL("ole32")

LPVOID = wintypes.LPVOID
SIZE_T = ctypes.c_size_t
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
INFINITE = 0xFFFFFFFF
WAIT_TIMEOUT = 0x00000102
ERROR_ALREADY_EXISTS = 183
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JobObjectExtendedLimitInformation = 9


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", LPVOID), ("bInheritHandle", wintypes.BOOL)]

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]

class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", LPVOID)]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", LPVOID), ("Attributes", wintypes.DWORD)]

class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", LPVOID), ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)), ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", SIZE_T), ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T), ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


# ctypes otherwise assumes a 32-bit integer return value.  On 64-bit Windows
# that truncates inherited file/job handles and the child dies during DLL
# initialization before user code starts.
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.LocalFree.restype = wintypes.HANDLE
userenv.CreateAppContainerProfile.restype = ctypes.c_long
userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
userenv.GetAppContainerFolderPath.restype = ctypes.c_long
advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
advapi32.FreeSid.restype = LPVOID
ole32.CoTaskMemFree.argtypes = [LPVOID]


def _check(ok: Any, label: str) -> None:
    if not ok:
        raise OSError(ctypes.get_last_error(), label)


def _environment_block(env: Mapping[str, str]) -> ctypes.Array:
    text = "\0".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0"
    return ctypes.create_unicode_buffer(text)


def _appcontainer_sid(moniker: str) -> LPVOID:
    sid = LPVOID()
    hr = userenv.CreateAppContainerProfile(moniker, moniker, "Tiangong isolated tool process", None, 0, ctypes.byref(sid))
    if hr != 0:
        # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS) == 0x800700B7
        if ctypes.c_uint32(hr).value != 0x800700B7:
            raise OSError(ctypes.c_uint32(hr).value, "CreateAppContainerProfile")
        hr = userenv.DeriveAppContainerSidFromAppContainerName(moniker, ctypes.byref(sid))
        if hr != 0:
            raise OSError(ctypes.c_uint32(hr).value, "DeriveAppContainerSidFromAppContainerName")
    return sid


def _sid_string(sid: LPVOID) -> str:
    output = wintypes.LPWSTR()
    _check(advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)), "ConvertSidToStringSidW")
    try:
        return str(output.value)
    finally:
        kernel32.LocalFree(output)


def _storage_root_for_sid(sid_text: str) -> Path:
    output = wintypes.LPWSTR()
    try:
        hr = userenv.GetAppContainerFolderPath(sid_text, ctypes.byref(output))
        if hr != 0 or not output.value:
            raise OSError(
                ctypes.c_uint32(hr).value,
                "GetAppContainerFolderPath",
            )
        return Path(str(output.value)).resolve(strict=False)
    finally:
        if output:
            ole32.CoTaskMemFree(output)


def appcontainer_storage_root(moniker: str = "TiangongV3.ToolSandbox") -> Path:
    """Return the profile-local folder naturally accessible to the container."""
    sid = _appcontainer_sid(moniker)
    try:
        return _storage_root_for_sid(_sid_string(sid))
    finally:
        advapi32.FreeSid(sid)


def _grant(path: Path, sid_text: str, permission: str) -> None:
    cp = subprocess.run(
        ["icacls", str(path), "/grant", f"*{sid_text}:{permission}", "/C", "/Q"],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if cp.returncode != 0:
        raise OSError(f"icacls_failed:{cp.stderr[-400:]}")


def _open_inheritable_file(path: Path) -> wintypes.HANDLE:
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    handle = kernel32.CreateFileW(str(path), 0x40000000 | 0x80000000, 0x00000001 | 0x00000002, ctypes.byref(sa), 2, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW")
    return handle


def _open_inheritable_null() -> wintypes.HANDLE:
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    handle = kernel32.CreateFileW(
        "NUL",
        0x80000000,
        0x00000001 | 0x00000002,
        ctypes.byref(sa),
        3,
        0x80,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW(NUL)")
    return handle


def run_appcontainer(command: Sequence[str] | str, *, cwd: Path, env: Mapping[str, str], limits: Any, sandbox_root: Path):
    moniker = "TiangongV3.ToolSandbox"
    sid = _appcontainer_sid(moniker)
    sid_text = _sid_string(sid)
    # AppContainer receives explicit access only to this invocation workspace.
    storage_root = _storage_root_for_sid(sid_text)
    try:
        sandbox_root.resolve(strict=False).relative_to(storage_root)
    except ValueError as exc:
        raise OSError("appcontainer_sandbox_root_outside_private_storage") from exc
    _grant(sandbox_root, sid_text, "(OI)(CI)M")
    if isinstance(command, str):
        stripped = command.lstrip()
        if stripped.startswith('"') and '"' in stripped[1:]:
            executable_text = stripped[1:stripped.find('"', 1)]
        else:
            executable_text = stripped.split(None, 1)[0]
    else:
        executable_text = str(command[0])
    executable = Path(executable_text).expanduser()
    if executable.is_absolute() and executable.exists():
        _grant(executable.parent, sid_text, "(OI)(CI)RX")

    stdout_path = sandbox_root / "stdout.bin"
    stderr_path = sandbox_root / "stderr.bin"
    stdout_handle = _open_inheritable_file(stdout_path)
    stderr_handle = _open_inheritable_file(stderr_path)
    input_handle = _open_inheritable_null()

    attr_size = SIZE_T(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_size))
    attr_buffer = ctypes.create_string_buffer(attr_size.value)
    _check(kernel32.InitializeProcThreadAttributeList(attr_buffer, 1, 0, ctypes.byref(attr_size)), "InitializeProcThreadAttributeList")
    capabilities = SECURITY_CAPABILITIES(sid, None, 0, 0)
    _check(kernel32.UpdateProcThreadAttribute(attr_buffer, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None), "UpdateProcThreadAttribute")

    si = STARTUPINFOEXW()
    si.StartupInfo.cb = ctypes.sizeof(si)
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
    si.StartupInfo.hStdInput = input_handle
    si.StartupInfo.hStdOutput = stdout_handle
    si.StartupInfo.hStdError = stderr_handle
    si.lpAttributeList = ctypes.cast(attr_buffer, LPVOID)
    pi = PROCESS_INFORMATION()
    env_block = _environment_block(env)
    command_line = ctypes.create_unicode_buffer(
        command if isinstance(command, str) else subprocess.list2cmdline(list(command))
    )

    job = kernel32.CreateJobObjectW(None, None)
    _check(job, "CreateJobObjectW")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    info.BasicLimitInformation.ActiveProcessLimit = max(1, int(limits.max_processes))
    info.ProcessMemoryLimit = max(128 * 1024 * 1024, int(limits.max_memory_bytes))
    _check(kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)), "SetInformationJobObject")

    try:
        ok = kernel32.CreateProcessW(
            None, command_line, None, None, True,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED | CREATE_NO_WINDOW,
            env_block, str(cwd), ctypes.byref(si), ctypes.byref(pi),
        )
        _check(ok, "CreateProcessW(AppContainer)")
        _check(kernel32.AssignProcessToJobObject(job, pi.hProcess), "AssignProcessToJobObject")
        kernel32.ResumeThread(pi.hThread)
        wait = kernel32.WaitForSingleObject(pi.hProcess, max(1, int(limits.timeout_seconds)) * 1000)
        if wait == WAIT_TIMEOUT:
            kernel32.TerminateJobObject(job, 124)
            raise TimeoutError("sandbox_timeout")
        exit_code = wintypes.DWORD()
        _check(kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code)), "GetExitCodeProcess")
    finally:
        for handle in (getattr(pi, "hThread", None), getattr(pi, "hProcess", None), input_handle, stdout_handle, stderr_handle, job):
            if handle:
                kernel32.CloseHandle(handle)
        kernel32.DeleteProcThreadAttributeList(attr_buffer)
        advapi32.FreeSid(sid)
    return int(exit_code.value), stdout_path.read_bytes(), stderr_path.read_bytes(), "windows-appcontainer"
