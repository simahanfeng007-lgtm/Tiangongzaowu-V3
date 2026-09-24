"""Windows AppContainer + Job Object process launcher used by SandboxRunner."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Mapping, Sequence, Any


if os.name != "nt":  # pragma: no cover
    raise ImportError("windows_appcontainer is Windows-only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernelbase = ctypes.WinDLL("KernelBase", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
ole32 = ctypes.OleDLL("ole32")

LPVOID = wintypes.LPVOID
SIZE_T = ctypes.c_size_t
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
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
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JobObjectExtendedLimitInformation = 9
RUNTIME_READ_EXECUTE = 0x1200A9
RUNTIME_ACCESS_RECEIPT = ".tiangong-runtime-reader.json"


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

class ACL(ctypes.Structure):
    _fields_ = [("revision", wintypes.BYTE), ("padding", wintypes.BYTE),
                ("size", wintypes.WORD), ("count", wintypes.WORD), ("reserved", wintypes.WORD)]

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
kernel32.CreateMutexW.argtypes = [LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel32.LocalFree.restype = wintypes.HANDLE
kernel32.LocalFree.argtypes = [LPVOID]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernelbase.DeriveCapabilitySidsFromName.argtypes = [wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.POINTER(LPVOID)), ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(ctypes.POINTER(LPVOID)), ctypes.POINTER(wintypes.DWORD)]
kernelbase.DeriveCapabilitySidsFromName.restype = wintypes.BOOL
advapi32.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ctypes.POINTER(LPVOID), ctypes.POINTER(LPVOID), ctypes.POINTER(LPVOID),
    ctypes.POINTER(LPVOID), ctypes.POINTER(LPVOID)]
advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
advapi32.GetAce.argtypes = [LPVOID, wintypes.DWORD, ctypes.POINTER(LPVOID)]
advapi32.EqualSid.argtypes = [LPVOID, LPVOID]
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


def delete_appcontainer_profile(moniker: str) -> None:
    if not moniker.startswith("TG3.Run."):
        raise ValueError("appcontainer_ephemeral_profile_required")
    hr = userenv.DeleteAppContainerProfile(moniker)
    if hr != 0 and ctypes.c_uint32(hr).value != 0x80070002:
        raise OSError(ctypes.c_uint32(hr).value, "DeleteAppContainerProfile")


def _check_budget(deadline, cancel_check=None):
    if cancel_check is not None and cancel_check():
        raise RuntimeError("sandbox_cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("sandbox_timeout")


@contextmanager
def _acl_update_lock(path: Path, *, deadline=None, cancel_check=None):
    # Only cold provisioning writes shared ACLs. Serialize across host processes
    # as well as threads, and allow cancellation while another installer owns it.
    deadline = time.monotonic() + 30 if deadline is None else deadline
    identity = os.path.normcase(str(path.resolve(strict=True))).encode("utf-8")
    name = "Local\\Tiangong.RuntimeAcl." + hashlib.sha256(identity).hexdigest()
    mutex = wintypes.HANDLE(kernel32.CreateMutexW(None, False, name))
    _check(mutex, "CreateMutexW(runtime ACL)")
    acquired = False
    try:
        while True:
            _check_budget(deadline, cancel_check)
            status = kernel32.WaitForSingleObject(mutex, 50)
            if status in (0, 0x80):
                break
            if status != WAIT_TIMEOUT:
                raise OSError("runtime_acl_update_lock_unavailable")
        acquired = True
        yield
    finally:
        if acquired: kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)


def _run_acl_update(path, sid_text, permission, *, deadline, cancel_check=None):
    _check_budget(deadline, cancel_check)
    with subprocess.Popen(
        ["icacls", str(path), "/grant:r", f"*{sid_text}:{permission}", "/C", "/Q"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        try:
            while True:
                _check_budget(deadline, cancel_check)
                try:
                    _, stderr = process.communicate(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.kill()
            process.communicate()
            raise
        if process.returncode != 0:
            raise OSError(f"icacls_failed:{stderr[-400:].decode('oem', errors='replace')}")


def _grant(path: Path, sid_text: str, permission: str, *, deadline, cancel_check=None) -> None:
    with _acl_update_lock(path, deadline=deadline, cancel_check=cancel_check):
        _run_acl_update(path, sid_text, permission, deadline=deadline, cancel_check=cancel_check)


@contextmanager
def _runtime_read_capability(runtime: Path):
    identity = os.path.normcase(str(runtime.resolve(strict=True))).encode("utf-8")
    name = "tiangong.runtime.read." + hashlib.sha256(identity).hexdigest()
    groups, capabilities = ctypes.POINTER(LPVOID)(), ctypes.POINTER(LPVOID)()
    group_count, capability_count = wintypes.DWORD(), wintypes.DWORD()
    try:
        _check(kernelbase.DeriveCapabilitySidsFromName(name, ctypes.byref(groups),
            ctypes.byref(group_count), ctypes.byref(capabilities), ctypes.byref(capability_count)),
            "DeriveCapabilitySidsFromName(runtime read)")
        if capability_count.value != 1:
            raise OSError("runtime_read_capability_count_invalid")
        yield LPVOID(capabilities[0])
    finally:
        # Each SID and both arrays have independent LocalAlloc lifetimes.
        for array, count in ((groups, group_count.value), (capabilities, capability_count.value)):
            if array:
                for index in range(count): kernel32.LocalFree(array[index])
                kernel32.LocalFree(array)


def _has_runtime_read_acl(runtime: Path, sid) -> bool:
    dacl, descriptor = LPVOID(), LPVOID()
    error = advapi32.GetNamedSecurityInfoW(str(runtime), 1, 4, None, None,
        ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error:
        raise OSError(error, "GetNamedSecurityInfoW(runtime read)")
    try:
        if not dacl:
            return False
        found = False
        for index in range(ctypes.cast(dacl, ctypes.POINTER(ACL)).contents.count):
            ace = LPVOID()
            _check(advapi32.GetAce(dacl, index, ctypes.byref(ace)), "GetAce(runtime read)")
            header = (ctypes.c_ubyte * 4).from_address(ace.value)
            if header[0] not in (0, 1):
                continue
            if not advapi32.EqualSid(LPVOID(ace.value + 8), sid):
                continue
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            # Never accept an unexpectedly broader grant or an explicit deny.
            if header[0] == 1 or mask & ~RUNTIME_READ_EXECUTE:
                return False
            if mask == RUNTIME_READ_EXECUTE and header[1] & 3 == 3 and not header[1] & 8:
                found = True
        return found
    finally:
        if descriptor: kernel32.LocalFree(descriptor)


def _ensure_runtime_access(runtime, sid, *, deadline, cancel_check=None, check=False):
    receipt = runtime / RUNTIME_ACCESS_RECEIPT
    expected = {"schema": "tiangong.runtime-reader.v1", "runtime": os.path.normcase(str(runtime)),
                "capability_sid": _sid_string(sid), "access_mask": RUNTIME_READ_EXECUTE}

    def ready():
        # A receipt proves propagation finished; current ACL readback prevents a
        # stale marker from authorizing a changed directory. No in-memory cache.
        try:
            matched = not receipt.is_symlink() and json.loads(receipt.read_bytes()) == expected
        except (OSError, ValueError):
            return False
        return matched and _has_runtime_read_acl(runtime, sid)

    if ready():
        return {"ok": True, "reused": True, **expected}
    if check:
        return {"ok": False, "reused": False, **expected}
    with _acl_update_lock(runtime, deadline=deadline, cancel_check=cancel_check):
        if ready():
            return {"ok": True, "reused": True, **expected}
        receipt.unlink(missing_ok=True)
        _run_acl_update(runtime, expected["capability_sid"], "(OI)(CI)RX",
            deadline=deadline, cancel_check=cancel_check)
        _check_budget(deadline, cancel_check)
        if not _has_runtime_read_acl(runtime, sid):
            raise OSError("runtime_read_acl_verification_failed")
        fd, name = tempfile.mkstemp(prefix=".tg-runtime-reader-", dir=runtime)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write((json.dumps(expected, sort_keys=True) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, receipt)
        finally:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "reused": False, **expected}


def prepare_runtime_access(runtime: Path, *, check=False, timeout_seconds=120):
    """Provision one path-specific RX capability during installation/startup."""
    runtime = runtime.resolve(strict=True)
    if not runtime.is_dir():
        raise ValueError("runtime_directory_required")
    with _runtime_read_capability(runtime) as sid:
        return _ensure_runtime_access(runtime, sid, check=check,
            deadline=time.monotonic() + max(1, timeout_seconds))


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


def run_appcontainer(command: Sequence[str] | str, *, cwd: Path, env: Mapping[str, str], limits: Any, sandbox_root: Path,
                     moniker: str = "TiangongV3.ToolSandbox", cancel_check=None):
    # Preparation is part of the same deadline; a blocked ACL operation can
    # no longer outlive cancellation or start a fresh execution timeout.
    deadline = time.monotonic() + max(1, int(limits.timeout_seconds))
    resources = ExitStack()
    sid = input_handle = stdout_handle = stderr_handle = job = None
    attributes_initialized = False
    pi = PROCESS_INFORMATION()
    try:
        _check_budget(deadline, cancel_check)
        sid = _appcontainer_sid(moniker)
        sid_text = _sid_string(sid)
        # AppContainer receives explicit access only to this invocation workspace.
        storage_root = _storage_root_for_sid(sid_text)
        try:
            sandbox_root.resolve(strict=False).relative_to(storage_root)
        except ValueError as exc:
            raise OSError("appcontainer_sandbox_root_outside_private_storage") from exc
        _grant(sandbox_root, sid_text, "(OI)(CI)M", deadline=deadline, cancel_check=cancel_check)
        if isinstance(command, str):
            stripped = command.lstrip()
            if stripped.startswith('"') and '"' in stripped[1:]:
                executable_text = stripped[1:stripped.find('"', 1)]
            else:
                executable_text = stripped.split(None, 1)[0]
        else:
            executable_text = str(command[0])
        executable = Path(executable_text).expanduser()
        capability_entries = None
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        if (executable.is_absolute() and executable.exists()
                and not executable.resolve().is_relative_to(system_root)
                and not executable.resolve().is_relative_to(sandbox_root.resolve())):
            runtime = executable.resolve(strict=True).parent
            reader_sid = resources.enter_context(_runtime_read_capability(runtime))
            # Normal startup provisions this once. A newly installed native tool may
            # cold-provision within this invocation's existing deadline.
            _ensure_runtime_access(runtime, reader_sid, deadline=deadline, cancel_check=cancel_check)
            capability_entries = (SID_AND_ATTRIBUTES * 1)(SID_AND_ATTRIBUTES(reader_sid, 4))

        stdout_path = sandbox_root / "stdout.bin"
        stderr_path = sandbox_root / "stderr.bin"
        stdout_handle = _open_inheritable_file(stdout_path)
        stderr_handle = _open_inheritable_file(stderr_path)
        input_handle = _open_inheritable_null()

        attr_size = SIZE_T(0)
        kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attr_size))
        attr_buffer = ctypes.create_string_buffer(attr_size.value)
        _check(kernel32.InitializeProcThreadAttributeList(attr_buffer, 2, 0, ctypes.byref(attr_size)), "InitializeProcThreadAttributeList")
        attributes_initialized = True
        capabilities = SECURITY_CAPABILITIES(sid, capability_entries, 1 if capability_entries is not None else 0, 0)
        _check(kernel32.UpdateProcThreadAttribute(attr_buffer, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None), "UpdateProcThreadAttribute")
        inherited = (wintypes.HANDLE * 3)(input_handle, stdout_handle, stderr_handle)
        _check(kernel32.UpdateProcThreadAttribute(attr_buffer, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            inherited, ctypes.sizeof(inherited), None, None), "UpdateProcThreadAttribute(handles)")

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = input_handle
        si.StartupInfo.hStdOutput = stdout_handle
        si.StartupInfo.hStdError = stderr_handle
        si.lpAttributeList = ctypes.cast(attr_buffer, LPVOID)
        env_block = _environment_block(env)
        command_line = ctypes.create_unicode_buffer(
            command if isinstance(command, str) else subprocess.list2cmdline(list(command))
        )

        job = kernel32.CreateJobObjectW(None, None)
        _check(job, "CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = max(1, int(limits.max_processes))
        info.ProcessMemoryLimit = max(128 * 1024 * 1024, int(limits.max_memory_bytes))
        info.JobMemoryLimit = info.ProcessMemoryLimit
        _check(kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)), "SetInformationJobObject")

        _check_budget(deadline, cancel_check)
        ok = kernel32.CreateProcessW(
            None, command_line, None, None, True,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED | CREATE_NO_WINDOW,
            env_block, str(cwd), ctypes.byref(si), ctypes.byref(pi),
        )
        _check(ok, "CreateProcessW(AppContainer)")
        _check(kernel32.AssignProcessToJobObject(job, pi.hProcess), "AssignProcessToJobObject")
        if cancel_check is not None and cancel_check():
            raise RuntimeError("sandbox_cancelled")
        if kernel32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
        while True:
            if cancel_check is not None and cancel_check():
                raise RuntimeError("sandbox_cancelled")
            if stdout_path.stat().st_size + stderr_path.stat().st_size > limits.max_output_bytes:
                raise RuntimeError("sandbox_process_output_limit")
            wait = kernel32.WaitForSingleObject(pi.hProcess, 25)
            if wait == 0:
                break
            if wait != WAIT_TIMEOUT:
                raise OSError(ctypes.get_last_error(), "WaitForSingleObject")
            if time.monotonic() >= deadline:
                raise TimeoutError("sandbox_timeout")
        exit_code = wintypes.DWORD()
        _check(kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code)), "GetExitCodeProcess")
    except BaseException as exc:
        # An execution/monitoring failure must never be retried without isolation.
        if pi.hProcess:
            exc.execution_started = True
        raise
    finally:
        if job:
            kernel32.TerminateJobObject(job, 125)
        if getattr(pi, "hProcess", None):
            # Also covers failure to assign the newly created suspended process
            # to the Job Object. Such a child must never be orphaned.
            kernel32.TerminateProcess(pi.hProcess, 125)
            kernel32.WaitForSingleObject(pi.hProcess, 5000)
        for handle in (getattr(pi, "hThread", None), getattr(pi, "hProcess", None), input_handle, stdout_handle, stderr_handle, job):
            if handle:
                kernel32.CloseHandle(handle)
        if attributes_initialized:
            kernel32.DeleteProcThreadAttributeList(attr_buffer)
        if sid: advapi32.FreeSid(sid)
        resources.close()
    with stdout_path.open("rb") as stream:
        stdout = stream.read(limits.max_output_bytes + 1)
    with stderr_path.open("rb") as stream:
        stderr = stream.read(limits.max_output_bytes + 1)
    if len(stdout) + len(stderr) > limits.max_output_bytes:
        exc = RuntimeError("sandbox_process_output_limit")
        exc.execution_started = True
        raise exc
    return int(exit_code.value), stdout, stderr, "windows-appcontainer"
