"""Path ABI regressions; emulated handles are not native containment evidence."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path, PureWindowsPath
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtime_security import path_identity as identity


@pytest.fixture
def native_api(monkeypatch):
    state = SimpleNamespace(status=0, closed=0, calls=[], query_error=False,
                            mapping=r"\Device\HarddiskVolume4", mapping_queries=[],
                            normalized=r"\Device\HarddiskVolume4\Users\runneradmin\file.txt",
                            opened=r"\Device\HarddiskVolume4\Users\RUNNER~1\file.txt")

    def create(handle, access, attributes, io, size, file_attributes, share, disposition, options, ea, ea_size):
        item = attributes._obj
        state.calls.append((item.ObjectName.contents.Buffer, access, item.Attributes, share, disposition, options))
        if state.status == 0:
            handle._obj.value = 123
        return state.status

    def query(handle, buffer, capacity, flags):
        assert handle.value == 123
        if state.query_error:
            return 0
        buffer.value = state.normalized if flags == 2 else state.opened
        assert flags in (2, 10)
        return len(buffer.value)

    def close(handle):
        assert handle.value == 123
        state.closed += 1
        return 1

    def query_device(device, buffer, capacity):
        state.mapping_queries.append(device)
        buffer.value = state.mapping
        return len(buffer.value) + 2

    kernel = SimpleNamespace(
        QueryDosDeviceW=Mock(side_effect=query_device),
        CreateFileW=Mock(side_effect=AssertionError("no DOS anchor/following open allowed")),
        GetFinalPathNameByHandleW=Mock(side_effect=query), CloseHandle=Mock(side_effect=close),
    )
    native = SimpleNamespace(NtCreateFile=Mock(side_effect=create), RtlNtStatusToDosError=Mock(return_value=5))
    factory = identity._windows_path_api
    factory.cache_clear()
    # Patch library discovery only while constructing the private test binding,
    # not while other runtime code or test threads might use ctypes.WinDLL.
    with monkeypatch.context() as binding:
        binding.setattr(ctypes, "WinDLL", lambda name, **kw: kernel if name == "kernel32" else native, raising=False)
        api = factory()
    factory.cache_clear()
    def winerror(*args):
        error = PermissionError("native operation denied")
        error.winerror = args[0] if args else 5
        return error
    local_ctypes = SimpleNamespace(**{key: getattr(ctypes, key) for key in (
        "create_unicode_buffer", "cast", "pointer", "sizeof", "byref",
    )}, get_last_error=lambda: 5, WinError=winerror)
    monkeypatch.setattr(identity, "_windows_path_api", lambda: (local_ctypes, *api[1:]))
    monkeypatch.setattr(identity, "_effective_appcontainer", Mock(return_value=False))
    yield state, kernel, native
    factory.cache_clear()


def test_short_alias_is_observed_on_one_no_reparse_handle_without_anchor_open(native_api):
    state, kernel, _ = native_api
    path = PureWindowsPath(r"C:\Users\RUNNER~1\file.txt")
    assert identity._windows_final_path(path) == PureWindowsPath(state.normalized)
    assert state.calls == [(r"\Device\HarddiskVolume4\Users\RUNNER~1\file.txt", 0x80, 0x1040, 7, 1, 0x4000)]
    assert state.mapping_queries == ["C:", "C:"]
    assert state.closed == 1
    kernel.CreateFileW.assert_not_called()


def test_extended_namespace_is_converted_once_not_duplicated(native_api):
    state, _, _ = native_api
    identity._windows_final_path(PureWindowsPath(r"\\?\C:\Users\RUNNER~1\file.txt"))
    assert state.calls[0][0] == r"\Device\HarddiskVolume4\Users\RUNNER~1\file.txt"


@pytest.mark.parametrize("field,value", [
    ("normalized", r"\Device\HarddiskVolume5\Users\runneradmin\file.txt"),
    ("opened", r"\Device\HarddiskVolume4\Users\OTHER~1\file.txt"),
    ("normalized", r"\Device\HarddiskVolume4\injected\Users\runneradmin\file.txt"),
    ("normalized", r"\??\C:\Users\runneradmin\file.txt"),
])
def test_native_volume_location_or_namespace_mismatch_is_rejected(native_api, field, value):
    state, _, _ = native_api
    setattr(state, field, value)
    with pytest.raises(identity.PathIdentityError):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    assert state.closed == 1


@pytest.mark.parametrize("status", [0xC000050B, 0xC0000022, 0xC0000034, 0x103])
def test_native_failure_or_pending_never_becomes_success(native_api, status):
    state, kernel, _ = native_api
    state.status = status
    with pytest.raises((OSError, identity.PathIdentityError)):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    kernel.GetFinalPathNameByHandleW.assert_not_called()
    assert state.closed == 0


def test_query_failure_closes_handle_without_pathlib_fallback(native_api, monkeypatch):
    state, _, _ = native_api
    state.query_error = True
    monkeypatch.setattr(Path, "resolve", Mock(side_effect=AssertionError("no fallback")))
    with pytest.raises(OSError):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    assert state.closed == 1


@pytest.mark.parametrize("text", [r"C:relative", r"\rooted", r"\\.\PhysicalDrive0", r"\\?\GLOBALROOT\Device\Disk\file",
                                  r"C:\safe\..\file", r"C:\safe\file:stream", "C:\\safe\\trailing.",
                                  "C:\\safe\\trailing ", "C:\\safe\\null\x00file"])
def test_ambiguous_or_nonfilesystem_input_rejected_before_native_open(native_api, text):
    _, _, native = native_api
    with pytest.raises(identity.PathIdentityError):
        identity._windows_final_path(PureWindowsPath(text))
    native.NtCreateFile.assert_not_called()


def test_unc_native_spelling_and_binding(native_api):
    state, _, _ = native_api
    state.mapping = r"\Device\Mup"
    state.opened = r"\Device\Mup\server\share\SHORT~1\file.txt"
    state.normalized = r"\Device\Mup\server\share\long-directory\file.txt"
    assert identity._windows_final_path(PureWindowsPath(r"\\?\UNC\server\share\SHORT~1\file.txt")) == PureWindowsPath(state.normalized)
    assert state.calls[0][0] == r"\Device\Mup\server\share\SHORT~1\file.txt"
    assert state.mapping_queries == ["UNC", "UNC"]


@pytest.mark.parametrize("mapping", [r"\??\C:\redirected", r"\Device\HarddiskVolume4\redirected",
                                     r"\Device\..", r"\GLOBAL??\C:", ""])
def test_redirected_or_malformed_device_mapping_is_rejected_before_open(native_api, mapping):
    state, _, native = native_api
    state.mapping = mapping
    with pytest.raises(identity.PathIdentityError, match="device_mapping"):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    native.NtCreateFile.assert_not_called()


def test_changed_drive_mapping_closes_handle_and_rejects_result(native_api):
    state, kernel, _ = native_api
    original = kernel.GetFinalPathNameByHandleW.side_effect
    def remap(*args):
        value = original(*args)
        state.mapping = r"\Device\HarddiskVolume5"
        return value
    kernel.GetFinalPathNameByHandleW.side_effect = remap
    with pytest.raises(identity.PathIdentityError, match="device_mapping_changed"):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    assert state.closed == 1


def test_handle_cannot_report_a_different_volume_even_when_both_names_agree(native_api):
    state, _, _ = native_api
    state.normalized = state.normalized.replace("Volume4", "Volume5")
    state.opened = state.opened.replace("Volume4", "Volume5")
    with pytest.raises(identity.PathIdentityError, match="physical_path_mismatch"):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    assert state.closed == 1


def test_device_mapping_query_failure_does_not_open_or_fall_back(native_api):
    _, kernel, native = native_api
    kernel.QueryDosDeviceW.side_effect = None
    kernel.QueryDosDeviceW.return_value = 0
    with pytest.raises(OSError):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    native.NtCreateFile.assert_not_called()


@pytest.fixture
def container_namespace(native_api):
    state, kernel, native = native_api
    kernel.QueryDosDeviceW.side_effect = None
    kernel.QueryDosDeviceW.return_value = 0
    identity._effective_appcontainer.return_value = True
    return state, kernel, native


def test_observed_container_uses_same_no_reparse_open_when_mapping_access_denied(container_namespace):
    state, kernel, _ = container_namespace
    assert identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt")) == PureWindowsPath(state.normalized)
    assert state.calls == [(r"\??\C:\Users\RUNNER~1\file.txt", 0x80, 0x1040, 7, 1, 0x4000)]
    assert state.closed == 1
    assert kernel.QueryDosDeviceW.call_count == 1
    kernel.CreateFileW.assert_not_called()
    identity._effective_appcontainer.assert_called_once_with()


@pytest.mark.parametrize("normalized,opened", [
    (r"\Device\HarddiskVolume5\Users\runneradmin\file.txt", r"\Device\HarddiskVolume4\Users\RUNNER~1\file.txt"),
    (r"\Device\HarddiskVolume4\hidden\Users\runneradmin\file.txt", r"\Device\HarddiskVolume4\hidden\Users\RUNNER~1\file.txt"),
    (r"\??\C:\Users\runneradmin\file.txt", r"\??\C:\Users\RUNNER~1\file.txt"),
    (r"\Device\HarddiskVolume4\Users\runneradmin\file.txt", r"\Device\HarddiskVolume4\Users\OTHER~1\file.txt"),
])
def test_container_rejects_volume_prefix_namespace_and_location_drift(container_namespace, normalized, opened):
    state, _, _ = container_namespace
    state.normalized, state.opened = normalized, opened
    with pytest.raises(identity.PathIdentityError):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    assert state.closed == 1


@pytest.mark.parametrize("status", [0xC000050B, 0xC0000022, 0xC0000034, 0x103])
def test_container_no_reparse_failure_is_not_retried(container_namespace, status):
    state, kernel, native = container_namespace
    state.status = status
    with pytest.raises((OSError, identity.PathIdentityError)):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    assert native.NtCreateFile.call_count == 1
    kernel.GetFinalPathNameByHandleW.assert_not_called()
    assert state.closed == 0


def test_container_unc_preserves_server_share(container_namespace):
    state, _, _ = container_namespace
    state.opened = r"\Device\Mup\server\share\SHORT~1\file.txt"
    state.normalized = r"\Device\Mup\server\share\long-directory\file.txt"
    path = PureWindowsPath(r"\\server\share\SHORT~1\file.txt")
    assert identity._windows_final_path(path) == PureWindowsPath(state.normalized)
    assert state.calls[0][0] == r"\??\UNC\server\share\SHORT~1\file.txt"
    state.normalized = state.normalized.replace("server", "elsewhere")
    state.opened = state.opened.replace("server", "elsewhere")
    with pytest.raises(identity.PathIdentityError, match="physical_path_mismatch"):
        identity._windows_final_path(path)
    assert state.closed == 2


def test_unobservable_container_identity_never_opens_path(container_namespace):
    _, _, native = container_namespace
    identity._effective_appcontainer.side_effect = PermissionError("token denied")
    with pytest.raises(PermissionError, match="token denied"):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    native.NtCreateFile.assert_not_called()


@pytest.mark.parametrize("error", [2, 122, 1008])
def test_non_access_denied_mapping_failure_does_not_select_container(native_api, error):
    _, kernel, native = native_api
    failure = OSError("mapping error")
    failure.winerror = error
    kernel.QueryDosDeviceW.side_effect = failure
    with pytest.raises(OSError, match="mapping error"):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
    identity._effective_appcontainer.assert_not_called()
    native.NtCreateFile.assert_not_called()


@pytest.fixture
def token_api(monkeypatch):
    state = SimpleNamespace(thread=False, error=1008, process=True, flag=1,
                            level=2, valid_size=True, query=True, close=True)
    def open_thread(thread, access, as_self, token):
        assert access == 0x8 and as_self is True
        token._obj.value = 456 if state.thread else None
        return state.thread
    def open_process(process, access, token):
        assert access == 0x8
        token._obj.value = 456 if state.process else None
        return state.process
    def query(token, kind, value, size, length):
        assert token.value == 456
        assert kind in (9, 29)
        value._obj.value = state.level if kind == 9 else state.flag
        length._obj.value = size if state.valid_size else size - 1
        return state.query
    kernel = SimpleNamespace(GetCurrentThread=Mock(return_value=-2),
        GetCurrentProcess=Mock(return_value=-1), CloseHandle=Mock(side_effect=lambda _: state.close))
    security = SimpleNamespace(OpenThreadToken=Mock(side_effect=open_thread),
        OpenProcessToken=Mock(side_effect=open_process), GetTokenInformation=Mock(side_effect=query))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: kernel if name == "kernel32" else security, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.error, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda *args: PermissionError("token query denied"), raising=False)
    return state, kernel, security


@pytest.mark.parametrize("thread,flag", [(False, 0), (False, 1), (True, 0), (True, 1)])
def test_namespace_selection_observes_effective_token_and_closes_it(token_api, thread, flag):
    state, kernel, security = token_api
    state.thread, state.flag = thread, flag
    assert identity._effective_appcontainer() is bool(flag)
    assert security.OpenProcessToken.call_count == (0 if thread else 1)
    assert kernel.CloseHandle.call_count == 1
    state.flag = 1 - flag
    assert identity._effective_appcontainer() is bool(1 - flag), "token evidence must not be cached"


@pytest.mark.parametrize("error", [5, 1347, 122])
def test_thread_token_failure_never_falls_back_to_process(token_api, error):
    state, kernel, security = token_api
    state.error = error
    with pytest.raises(PermissionError):
        identity._effective_appcontainer()
    security.OpenProcessToken.assert_not_called()
    kernel.CloseHandle.assert_not_called()


@pytest.mark.parametrize("field,value", [("flag", 2), ("valid_size", False), ("query", False), ("close", False)])
def test_invalid_token_evidence_or_cleanup_rejects_namespace(token_api, field, value):
    state, kernel, _ = token_api
    setattr(state, field, value)
    with pytest.raises((OSError, identity.PathIdentityError)):
        identity._effective_appcontainer()
    assert kernel.CloseHandle.call_count == 1


@pytest.mark.parametrize("level", [0, 1, 4])
def test_identification_or_invalid_impersonation_level_rejects_namespace(token_api, level):
    state, kernel, security = token_api
    state.thread, state.level = True, level
    with pytest.raises((OSError, identity.PathIdentityError)):
        identity._effective_appcontainer()
    security.OpenProcessToken.assert_not_called()
    assert kernel.CloseHandle.call_count == 1


def test_process_token_failure_does_not_claim_container(token_api):
    state, kernel, security = token_api
    state.process = False
    with pytest.raises(PermissionError):
        identity._effective_appcontainer()
    security.GetTokenInformation.assert_not_called()
    kernel.CloseHandle.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows leaf junction")
def test_real_leaf_junction_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "leaf-junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(actual)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with pytest.raises(identity.PathIdentityError, match="link_or_junction"):
            identity.resolve_existing_path(link)
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows 8.3 namespace")
def test_real_short_alias_preserves_canonical_workspace_scope(tmp_path):
    from ctypes import wintypes
    root = tmp_path / "long-directory-for-path-regression"
    root.mkdir()
    file = root / "payload.txt"
    file.write_bytes(b"fixture")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    api.GetShortPathNameW.restype = wintypes.DWORD
    output = ctypes.create_unicode_buffer(32768)
    size = api.GetShortPathNameW(str(root), output, len(output))
    assert 0 < size < len(output), "8.3 observation unavailable, not an acceptance pass"
    alias = Path(output.value)
    assert alias != root.resolve(strict=True), "fixture did not create a genuine short alias"
    assert identity.resolve_existing_path(alias) == root.resolve(strict=True)
    assert identity.resolve_existing_path(alias / "payload.txt") == file.resolve(strict=True)
    assert identity.verify_relative_path(alias, alias / "payload.txt") == "payload.txt"
    from total_gateway.omni_grant_authority import OmniGrantAuthority
    assert OmniGrantAuthority._workspace_scope_hash(alias) == OmniGrantAuthority._workspace_scope_hash(root)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows ancestor junction")
def test_real_ancestor_junction_outside_selected_root_is_rejected(tmp_path):
    actual = tmp_path / "actual" / "selected"
    actual.mkdir(parents=True)
    (actual / "payload.txt").write_bytes(b"fixture")
    link = tmp_path / "redirected-ancestor"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(actual.parent)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        root = link / "selected"
        with pytest.raises((OSError, identity.PathIdentityError)):
            identity.verify_relative_path(root, root / "payload.txt")
        with pytest.raises((OSError, identity.PathIdentityError)):
            identity.resolve_existing_path(root)
    finally:
        link.rmdir()


def test_alias_expansion_is_reobserved_and_location_drift_rejected(monkeypatch):
    class WindowsFixturePath(PureWindowsPath):
        def absolute(self):
            return self
    monkeypatch.setattr(identity, "Path", WindowsFixturePath)
    monkeypatch.setattr(identity, "os", SimpleNamespace(name="nt"))
    original = WindowsFixturePath(r"C:\Users\RUNNER~1\workspace")
    canonical = WindowsFixturePath(r"C:\Users\runneradmin\workspace")
    physical = PureWindowsPath(r"\Device\Volume4\Users\runneradmin\workspace")
    observer = Mock(side_effect=[physical, physical])
    monkeypatch.setattr(identity, "_windows_final_path", observer)
    assert identity.resolve_existing_path(original) == canonical
    assert [call.args[0] for call in observer.call_args_list] == [original, canonical]
    observer.side_effect = [physical, PureWindowsPath(r"\Device\Volume5\Users\runneradmin\workspace")]
    with pytest.raises(identity.PathIdentityError, match="physical_path_mismatch"):
        identity.resolve_existing_path(original)


def test_close_failure_cannot_return_success(native_api):
    state, kernel, _ = native_api
    kernel.CloseHandle.side_effect = None
    kernel.CloseHandle.return_value = 0
    with pytest.raises(identity.PathIdentityError, match="native_handle_close_failed"):
        identity._windows_final_path(PureWindowsPath(r"C:\Users\RUNNER~1\file.txt"))
