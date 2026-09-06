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

    kernel = SimpleNamespace(
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
    local_ctypes = SimpleNamespace(**{key: getattr(ctypes, key) for key in (
        "create_unicode_buffer", "cast", "pointer", "sizeof", "byref",
    )}, get_last_error=lambda: 5, WinError=lambda *args: PermissionError("native operation denied"))
    monkeypatch.setattr(identity, "_windows_path_api", lambda: (local_ctypes, *api[1:]))
    yield state, kernel, native
    factory.cache_clear()


def test_short_alias_is_observed_on_one_no_reparse_handle_without_anchor_open(native_api):
    state, kernel, _ = native_api
    path = PureWindowsPath(r"C:\Users\RUNNER~1\file.txt")
    assert identity._windows_final_path(path) == PureWindowsPath(state.normalized)
    assert state.calls == [(r"\??\C:\Users\RUNNER~1\file.txt", 0x80, 0x1040, 7, 1, 0x4000)]
    assert state.closed == 1
    kernel.CreateFileW.assert_not_called()


def test_extended_namespace_is_converted_once_not_duplicated(native_api):
    state, _, _ = native_api
    identity._windows_final_path(PureWindowsPath(r"\\?\C:\Users\RUNNER~1\file.txt"))
    assert state.calls[0][0] == r"\??\C:\Users\RUNNER~1\file.txt"


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
    state.opened = r"\Device\Mup\server\share\SHORT~1\file.txt"
    state.normalized = r"\Device\Mup\server\share\long-directory\file.txt"
    assert identity._windows_final_path(PureWindowsPath(r"\\?\UNC\server\share\SHORT~1\file.txt")) == PureWindowsPath(state.normalized)
    assert state.calls[0][0] == r"\??\UNC\server\share\SHORT~1\file.txt"


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
