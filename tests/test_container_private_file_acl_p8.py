"""Binary ACL parsing fixtures; these do not establish Windows API acceptance."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from total_gateway import windows_private_files as private

USER = "S-1-5-21-1-2-3-1001"
APP = "S-1-15-2-1-2-3-4-5-6-7"


def sid_bytes(text: str) -> bytes:
    _, revision, authority, *parts = text.split("-")
    return (bytes([int(revision), len(parts)]) + int(authority).to_bytes(6, "big")
            + b"".join(int(part).to_bytes(4, "little") for part in parts))


@pytest.fixture
def binary_acl():
    state = SimpleNamespace(protected=True, present=True, defaulted=False,
        null_acl=False, revision=2, count=2, size_adjust=0,
        first_type=0, first_flags=0, first_rights=0x1F01FF,
        first_sid=USER, first_sid_revision=1, first_sid_count=None,
        length_adjust=0, ace_error=False, descriptor_error=False,
        freed=[], sid_conversions=[], buffers=[])

    def security_info(handle, kind, fields, *outputs):
        assert (handle, kind, fields) == (123, 1, 4)
        if state.descriptor_error:
            return 5
        outputs[-1]._obj.value = 456
        return 0

    def control(descriptor, flags, revision):
        assert descriptor.value == 456
        flags._obj.value = 0x1000 if state.protected else 0
        revision._obj.value = 1
        return 1

    def dacl(descriptor, present, output, defaulted):
        present._obj.value = state.present
        defaulted._obj.value = state.defaulted
        rows = []
        for index, trustee in enumerate((state.first_sid, APP)):
            sid = bytearray(sid_bytes(trustee))
            if index == 0:
                sid[0] = state.first_sid_revision
                if state.first_sid_count is not None:
                    sid[1] = state.first_sid_count
            ace = (bytes([state.first_type if index == 0 else 0,
                          state.first_flags if index == 0 else 0])
                   + (8 + len(sid)).to_bytes(2, "little")
                   + (state.first_rights if index == 0 else 0x1F01FF).to_bytes(4, "little")
                   + sid)
            rows.append(ace)
        size = 8 + sum(map(len, rows))
        header = (bytes([state.revision, 0])
                  + (size + state.size_adjust).to_bytes(2, "little")
                  + state.count.to_bytes(2, "little") + b"\x00\x00")
        state.buffer = ctypes.create_string_buffer(header + b"".join(rows))
        state.buffers.append(state.buffer)
        address = ctypes.addressof(state.buffer)
        state.aces = (address + 8, address + 8 + len(rows[0]))
        state.sids = {state.aces[0] + 8: state.first_sid, state.aces[1] + 8: APP}
        output._obj.value = 0 if state.null_acl else address
        return 1

    def ace(acl, index, output):
        if state.ace_error:
            return 0
        output._obj.value = state.aces[index]
        return 1

    def convert(sid, output):
        # Real numeric SID text, not an SDDL alias such as LA.
        value = state.sids[sid]
        state.sid_conversions.append(value)
        text = ctypes.create_unicode_buffer(value)
        state.buffers.append(text)
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(text)
        return 1

    fake_ctypes = SimpleNamespace(**{name: getattr(ctypes, name) for name in
        ("byref", "string_at")}, get_last_error=lambda: 5,
        WinError=lambda *args: PermissionError("controlled native query denial"))
    kernel = SimpleNamespace(LocalFree=lambda pointer: state.freed.append(pointer) or 0)
    security = SimpleNamespace(GetSecurityInfo=security_info,
        GetSecurityDescriptorControl=control, GetSecurityDescriptorDacl=dacl,
        GetAce=ace, IsValidSid=lambda sid: True,
        GetLengthSid=lambda sid: len(sid_bytes(state.sids[sid])) + state.length_adjust,
        ConvertSidToStringSidW=convert)
    return state, (fake_ctypes, wintypes, kernel, security)


def observe(fixture):
    state, api = fixture
    value = private._observed_dacl(123, api)
    private.validate_private_dacl(value, (USER, APP))
    return value


def test_binary_acl_binds_numeric_sids_on_requested_handle(binary_acl):
    state, _ = binary_acl
    assert observe(binary_acl) == f"D:P(A;;FA;;;{USER})(A;;FA;;;{APP})"
    assert state.sid_conversions == [USER, APP]
    assert len(state.freed) == 3  # Two SID strings and the queried descriptor.


@pytest.mark.parametrize("field,value", [
    ("protected", False), ("present", False), ("null_acl", True),
    ("defaulted", True), ("revision", 0), ("count", 1), ("count", 3),
    ("size_adjust", -1), ("first_type", 1), ("first_flags", 0x10),
    ("first_rights", 0x120089), ("first_sid_revision", 2),
    ("first_sid_count", 16), ("length_adjust", 4),
    ("first_sid", "S-1-1-0"), ("first_sid", APP),
])
def test_binary_acl_rejects_missing_extra_inherited_or_wrong_evidence(binary_acl, field, value):
    state, _ = binary_acl
    setattr(state, field, value)
    with pytest.raises(private.WindowsPrivateFileError):
        observe(binary_acl)
    assert state.freed, "The acquired descriptor must be released on rejection."


def test_native_ace_query_failure_is_not_accepted(binary_acl):
    state, _ = binary_acl
    state.ace_error = True
    with pytest.raises(PermissionError):
        observe(binary_acl)
    assert len(state.freed) == 1


def test_unavailable_descriptor_does_not_fabricate_or_free_evidence(binary_acl):
    state, _ = binary_acl
    state.descriptor_error = True
    with pytest.raises(PermissionError):
        observe(binary_acl)
    assert not state.freed and not state.sid_conversions
