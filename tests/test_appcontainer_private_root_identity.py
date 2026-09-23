"""OS-owned anchor selection is fresh, process-only and fail-closed."""
import ctypes
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtime_security import path_identity as identity


@pytest.fixture
def private_root_api(monkeypatch):
    state = SimpleNamespace(thread=False, thread_error=1008, process=True, error=1008,
        flag=1, flag_size=ctypes.sizeof(ctypes.c_ulong), sid_size=64, sid_valid=True,
        sid_text="S-1-15-2-1-2-3-4-5-6-7", folder=r"C:\Users\user\AppData\Local\Packages\test\AC",
        folder_result=0, close=True, sid_free=True)

    def thread(*args):
        args[-1]._obj.value = 456 if state.thread else None
        state.error = state.thread_error
        return state.thread

    def process(*args):
        args[-1]._obj.value = 789 if state.process else None
        return state.process

    def information(token, kind, value, size, returned):
        assert token.value == 789
        if kind == 29:
            value._obj.value, returned._obj.value = state.flag, state.flag_size
            return True
        assert kind == 31
        returned._obj.value = state.sid_size
        if value is None:
            state.error = 122
            return False
        ctypes.c_void_p.from_buffer(value).value = 12345
        return True

    def sid_text(sid, output):
        assert sid == 12345
        output._obj.value = state.sid_text
        return True

    def folder(sid, output):
        assert sid == state.sid_text
        output._obj.value = state.folder
        return state.folder_result

    kernel = SimpleNamespace(GetCurrentThread=Mock(return_value=-2), GetCurrentProcess=Mock(return_value=-1),
        CloseHandle=Mock(side_effect=lambda handle: state.close), LocalFree=Mock(side_effect=lambda value: 0 if state.sid_free else 1))
    security = SimpleNamespace(OpenThreadToken=Mock(side_effect=thread), OpenProcessToken=Mock(side_effect=process),
        GetTokenInformation=Mock(side_effect=information), IsValidSid=Mock(side_effect=lambda sid: state.sid_valid),
        ConvertSidToStringSidW=Mock(side_effect=sid_text))
    userenv = SimpleNamespace(GetAppContainerFolderPath=Mock(side_effect=folder))
    ole32 = SimpleNamespace(CoTaskMemFree=Mock())
    modules = {"kernel32": kernel, "advapi32": security, "userenv": userenv}
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: modules[name], raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", lambda name: ole32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.error, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: PermissionError(f"native error {code}"), raising=False)
    return state, kernel, security, userenv, ole32


def test_private_root_uses_current_sid_and_frees_all_os_allocations(private_root_api):
    state, kernel, security, userenv, ole32 = private_root_api
    assert identity._appcontainer_private_root() == PureWindowsPath(state.folder)
    assert security.OpenThreadToken.call_count == security.OpenProcessToken.call_count == 1
    assert kernel.CloseHandle.call_count == kernel.LocalFree.call_count == ole32.CoTaskMemFree.call_count == 1
    state.sid_text = "S-1-15-2-8-2-3-4-5-6-7"
    state.folder = r"D:\other-sid\AC"
    assert identity._appcontainer_private_root() == PureWindowsPath(state.folder)
    assert userenv.GetAppContainerFolderPath.call_count == 2, "SID/root observations must not be cached"


def test_impersonation_never_uses_process_private_root(private_root_api):
    state, kernel, security, userenv, _ = private_root_api
    state.thread = True
    with pytest.raises(identity.PathIdentityError, match="impersonation_unsupported"):
        identity._appcontainer_private_root()
    security.OpenProcessToken.assert_not_called()
    userenv.GetAppContainerFolderPath.assert_not_called()
    assert kernel.CloseHandle.call_count == 1


@pytest.mark.parametrize("error", [5, 1347])
def test_unobservable_thread_token_never_uses_process_private_root(private_root_api, error):
    state, kernel, security, userenv, _ = private_root_api
    state.thread_error = error
    with pytest.raises(PermissionError):
        identity._appcontainer_private_root()
    security.OpenProcessToken.assert_not_called()
    userenv.GetAppContainerFolderPath.assert_not_called()
    kernel.CloseHandle.assert_not_called()


@pytest.mark.parametrize("field,value", [("flag", 0), ("flag", 2), ("flag_size", 1),
    ("sid_size", 1), ("sid_size", 65537), ("sid_valid", False),
    ("sid_text", "S-1-5-21-1"), ("sid_free", False)])
def test_invalid_principal_cannot_select_storage(private_root_api, field, value):
    state, kernel, _, userenv, _ = private_root_api
    setattr(state, field, value)
    with pytest.raises(identity.PathIdentityError):
        identity._appcontainer_private_root()
    userenv.GetAppContainerFolderPath.assert_not_called()
    assert kernel.CloseHandle.call_count == 1


@pytest.mark.parametrize("field,value", [("folder_result", -1), ("folder", ""),
    ("folder", r"\\server\share\AC"), ("folder", "C:\\"),
    ("folder", r"C:\Users\..\AC"), ("close", False)])
def test_invalid_os_root_or_cleanup_is_not_an_anchor(private_root_api, field, value):
    state, kernel, _, _, ole32 = private_root_api
    setattr(state, field, value)
    with pytest.raises(identity.PathIdentityError):
        identity._appcontainer_private_root()
    assert kernel.CloseHandle.call_count == 1
    # Even an allocated empty string must be freed, unlike a null pointer.
    assert ole32.CoTaskMemFree.call_count == 1


def test_process_token_unavailable_rejects_private_root(private_root_api):
    state, kernel, security, userenv, _ = private_root_api
    state.process = False
    with pytest.raises(PermissionError):
        identity._appcontainer_private_root()
    security.GetTokenInformation.assert_not_called()
    userenv.GetAppContainerFolderPath.assert_not_called()
    kernel.CloseHandle.assert_not_called()
