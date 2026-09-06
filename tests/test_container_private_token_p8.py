"""Controlled effective-token queries; no native token or ACL mutations."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from total_gateway import windows_private_files as private


USER = "S-1-5-21-10-20-30-1001"
APP = "S-1-15-2-1-2-3-4-5-6-7"


@pytest.fixture
def token_api(monkeypatch):
    state = SimpleNamespace(
        thread_error=1008, process_error=0, query_error=None, error=0,
        flag=0, level=2, level_size=ctypes.sizeof(wintypes.DWORD),
        queries=[], closed=[], freed=[], buffers=[],
    )

    def thread(handle, access, as_self, output):
        assert (handle, access, as_self) == (12, 8, True)
        if state.thread_error:
            state.error = state.thread_error
            return 0
        output._obj.value = 101
        return 1

    def process(handle, access, output):
        assert (handle, access) == (11, 8)
        if state.process_error:
            state.error = state.process_error
            return 0
        output._obj.value = 102
        return 1

    def information(token, kind, output, size, required):
        expected = 101 if not state.thread_error else 102
        assert token.value == expected
        state.queries.append((token.value, kind))
        if kind == state.query_error:
            state.error = 5
            return 0
        if kind in (9, 29):  # TokenImpersonationLevel, TokenIsAppContainer.
            assert size == ctypes.sizeof(wintypes.DWORD)
            output._obj.value = state.level if kind == 9 else state.flag
            required._obj.value = state.level_size if kind == 9 else size
            return 1
        assert kind in (1, 31)
        required._obj.value = ctypes.sizeof(ctypes.c_void_p) + 8
        if output is None:
            state.error = 122
            return 0
        ctypes.c_void_p.from_buffer(output).value = 201 if kind == 1 else 231
        return 1

    def convert_sid(sid, output):
        value = USER if sid == 201 else APP
        buffer = ctypes.create_unicode_buffer(value)
        state.buffers.append(buffer)
        ctypes.cast(output, ctypes.POINTER(wintypes.LPWSTR))[0] = ctypes.cast(
            buffer, wintypes.LPWSTR,
        )
        return 1

    kernel = SimpleNamespace(
        GetCurrentProcess=Mock(return_value=11), GetCurrentThread=Mock(return_value=12),
        CloseHandle=Mock(side_effect=lambda handle: state.closed.append(handle.value) or 1),
        LocalFree=Mock(side_effect=lambda pointer: state.freed.append(pointer) or 0),
    )
    security = SimpleNamespace(
        OpenThreadToken=Mock(side_effect=thread), OpenProcessToken=Mock(side_effect=process),
        GetTokenInformation=Mock(side_effect=information), IsValidSid=Mock(return_value=1),
        ConvertSidToStringSidW=Mock(side_effect=convert_sid),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs:
        kernel if name == "kernel32" else security, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.error, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda error:
        PermissionError(f"controlled native error {error}"), raising=False)
    return state, kernel, security


def test_absent_thread_token_preserves_ordinary_primary_host(token_api):
    state, _, security = token_api
    assert private.current_appcontainer_principal() is None
    security.OpenProcessToken.assert_called_once()
    assert state.queries == [(102, 29)]
    assert state.closed == [102]


@pytest.mark.parametrize("thread_error", [0, 1008])
def test_exact_container_principal_uses_the_observed_token(token_api, thread_error):
    state, _, security = token_api
    state.thread_error, state.flag = thread_error, 1
    assert private.current_appcontainer_principal() == (USER, APP)
    expected_handle = 101 if not thread_error else 102
    assert {handle for handle, _ in state.queries} == {expected_handle}
    assert state.closed == [expected_handle] and len(state.freed) == 2
    assert security.OpenProcessToken.call_count == bool(thread_error)


@pytest.mark.parametrize("level", [2, 3])
def test_impersonation_and_delegation_levels_keep_existing_host_behavior(token_api, level):
    state, _, security = token_api
    state.thread_error, state.level = 0, level
    assert private.current_appcontainer_principal() is None
    assert state.queries == [(101, 29), (101, 9)]
    security.OpenProcessToken.assert_not_called()
    assert state.closed == [101]


@pytest.mark.parametrize("level", [0, 1])
def test_noncontainer_anonymous_or_identification_cannot_become_host_fallback(token_api, level):
    state, _, security = token_api
    state.thread_error, state.level = 0, level
    with pytest.raises(PermissionError, match="impersonation"):
        private.current_appcontainer_principal()
    security.OpenProcessToken.assert_not_called()
    assert state.closed == [101]


@pytest.mark.parametrize("field,value", [("level", 4), ("level_size", 0), ("level_size", 8)])
def test_invalid_impersonation_evidence_rejects_fallback(token_api, field, value):
    state, _, security = token_api
    state.thread_error = 0
    setattr(state, field, value)
    with pytest.raises(private.WindowsPrivateFileError, match="impersonation"):
        private.current_appcontainer_principal()
    security.OpenProcessToken.assert_not_called()
    assert state.closed == [101]


@pytest.mark.parametrize("kind", [9, 29])
def test_failed_effective_token_query_closes_without_process_fallback(token_api, kind):
    state, _, security = token_api
    state.thread_error, state.query_error = 0, kind
    with pytest.raises(PermissionError, match="controlled native error 5"):
        private.current_appcontainer_principal()
    security.OpenProcessToken.assert_not_called()
    assert state.closed == [101]


@pytest.mark.parametrize("error", [5, 1347])
def test_denied_or_anonymous_thread_open_never_uses_process_token(token_api, error):
    state, _, security = token_api
    state.thread_error = error
    with pytest.raises(PermissionError, match=f"controlled native error {error}"):
        private.current_appcontainer_principal()
    security.OpenProcessToken.assert_not_called()
    assert not state.queries and not state.closed


def test_process_token_denial_has_no_fallback_or_owned_handle(token_api):
    state, _, security = token_api
    state.process_error = 5
    with pytest.raises(PermissionError, match="controlled native error 5"):
        private.current_appcontainer_principal()
    security.OpenProcessToken.assert_called_once()
    assert not state.queries and not state.closed
