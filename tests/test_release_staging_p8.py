"""Release staging regressions; injected tokens are not native OS evidence."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from total_gateway import release_manifest as release


@pytest.fixture(params=['development', 'production'])
def writer(request, tmp_path, monkeypatch):
    manifest = object()
    generator = Mock(return_value=manifest)
    verifier = Mock(return_value=manifest)
    monkeypatch.setattr(release, 'release_manifest_bytes', lambda value: b'fixture-not-production\n')
    monkeypatch.setattr(release, 'verify_release_manifest_file', verifier)
    if request.param == 'development':
        monkeypatch.setattr(release, 'generate_release_manifest', generator)
        call = lambda: release.write_release_manifest(tmp_path/'release', tmp_path)
    else:
        monkeypatch.setattr(release, 'generate_production_release_manifest', generator)
        call = lambda: release.write_production_release_manifest(
            tmp_path/'release', tmp_path, tmp_path, platform_name='win32',
            architecture='x64', desktop_archive_path=tmp_path/'app.asar')
    return call, generator, verifier, manifest


def test_generation_failure_leaves_no_private_stage_or_published_target(writer, tmp_path):
    call, generator, verifier, _ = writer
    failure = ValueError('original generation failure')
    generator.side_effect = failure
    with pytest.raises(ValueError) as caught:
        call()
    assert caught.value is failure
    assert not (tmp_path/'release').exists()
    assert not list(tmp_path.glob('.release-*'))
    verifier.assert_not_called()


def test_cleanup_failure_keeps_original_write_error_and_records_cleanup(writer, tmp_path, monkeypatch):
    call, _, verifier, _ = writer
    original = PermissionError('original staged write denial')
    write = Path.write_bytes
    def denied(path, data):
        if path.name == release.RELEASE_MANIFEST_FILENAME:
            raise original
        return write(path, data)
    monkeypatch.setattr(Path, 'write_bytes', denied)
    monkeypatch.setattr(release.shutil, 'rmtree', Mock(side_effect=PermissionError('cleanup denial')))
    with pytest.raises(PermissionError) as caught:
        call()
    assert caught.value is original
    assert any('release_stage_cleanup_failed' in note and 'cleanup denial' in note
               for note in getattr(original, '__notes__', []))
    assert not (tmp_path/'release').exists()
    verifier.assert_not_called()


def test_stage_contains_one_file_and_final_verification_is_still_required(writer, tmp_path):
    call, _, verifier, manifest = writer
    assert call() is manifest
    assert [p.name for p in (tmp_path/'release').iterdir()] == [release.RELEASE_MANIFEST_FILENAME]
    assert not list(tmp_path.glob('.release-*'))
    verifier.assert_called_once()


def test_nonempty_target_is_not_overwritten(writer, tmp_path):
    call, generator, verifier, _ = writer
    target = tmp_path/'release'; target.mkdir(); (target/'keep').write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        call()
    assert (target/'keep').read_bytes() == b'keep'
    generator.assert_not_called(); verifier.assert_not_called()


def test_replace_failure_cleans_stage_without_publishing(writer, tmp_path, monkeypatch):
    call, _, verifier, _ = writer
    monkeypatch.setattr(release.os, 'replace', Mock(side_effect=OSError('rename failure')))
    with pytest.raises(OSError, match='rename failure'):
        call()
    assert not (tmp_path/'release').exists()
    assert not list(tmp_path.glob('.release-*'))
    verifier.assert_not_called()


def test_os_confirmed_appcontainer_uses_only_private_descriptor_not_env(monkeypatch, tmp_path):
    from total_gateway import release_manifest as module
    descriptor = 'D:P(A;OICI;FA;;;S-1-5-21-1)(A;OICI;FA;;;S-1-15-2-1-2-3-4-5-6-7)'
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(module, '_windows_appcontainer_sddl', Mock(return_value=descriptor))
    expected = tmp_path/'.release-private'
    native = Mock(return_value=expected)
    monkeypatch.setattr(module, '_windows_private_stage', native)
    forbidden = Mock(side_effect=AssertionError('stdlib private-dir regression must not execute'))
    monkeypatch.setattr(module, 'tempfile', SimpleNamespace(mkdtemp=forbidden))
    assert module._create_release_stage(tmp_path/'release') == expected
    native.assert_called_once_with(tmp_path/'release', descriptor)
    forbidden.assert_not_called()


def test_normal_host_keeps_stdlib_private_directory_even_with_spoofed_env(monkeypatch, tmp_path):
    monkeypatch.setenv('TIANGONG_SANDBOX', '1')
    monkeypatch.setattr(release, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(release, '_windows_appcontainer_sddl', Mock(return_value=None))
    native = Mock(side_effect=AssertionError('no AppContainer token'))
    monkeypatch.setattr(release, '_windows_private_stage', native)
    ordinary = Mock(return_value=str(tmp_path/'.release-private'))
    monkeypatch.setattr(release, 'tempfile', SimpleNamespace(mkdtemp=ordinary))
    assert release._create_release_stage(tmp_path/'release') == tmp_path/'.release-private'
    ordinary.assert_called_once_with(prefix='.release-', dir=tmp_path)
    native.assert_not_called()


def test_token_evidence_denial_does_not_fall_back_or_create_stage(monkeypatch, tmp_path):
    monkeypatch.setattr(release, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(release, '_windows_appcontainer_sddl', Mock(side_effect=PermissionError('token evidence denied')))
    ordinary = Mock(side_effect=AssertionError('no fallback'))
    monkeypatch.setattr(release, 'tempfile', SimpleNamespace(mkdtemp=ordinary))
    with pytest.raises(PermissionError, match='token evidence denied'):
        release._create_release_stage(tmp_path/'release')
    ordinary.assert_not_called()


def test_posix_uses_unchanged_stdlib_private_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(release, 'os', SimpleNamespace(name='posix'))
    windows = Mock(side_effect=AssertionError('no Windows token probe on POSIX'))
    monkeypatch.setattr(release, '_windows_appcontainer_sddl', windows)
    path = release._create_release_stage(tmp_path/'release')
    assert path.is_dir()
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o700
    windows.assert_not_called()


@pytest.fixture
def native_api(monkeypatch):
    import ctypes
    from ctypes import wintypes
    state = SimpleNamespace(error=0, thread_error=1008, process_ok=True, token_flag=1,
                            token_query_ok=True, user_sid='S-1-5-21-10-20-30-1001',
                            app_sid='S-1-15-2-1-2-3-4-5-6-7', sid_valid=True,
                            required=ctypes.sizeof(ctypes.c_void_p)+8, buffers=[],
                            closed=[], freed=[], descriptor=None, directory_errors=[], created=[],
                            security_ok=True)
    def thread(handle, rights, self_query, output):
        assert rights == 8 and self_query is True
        if state.thread_error:
            state.error = state.thread_error
            return 0
        output._obj.value = 100
        return 1
    def process(handle, rights, output):
        assert rights == 8
        if not state.process_ok:
            state.error = 5
            return 0
        output._obj.value = 100
        return 1
    def information(token, kind, output, size, required):
        assert token.value == 100
        if not state.token_query_ok:
            state.error = 5
            return 0
        if kind == 29:
            output._obj.value = state.token_flag
            required._obj.value = ctypes.sizeof(wintypes.DWORD)
            return 1
        assert kind in (1, 31)
        required._obj.value = state.required
        if output is None:
            state.error = 122
            return 0
        ctypes.c_void_p.from_buffer(output).value = 101 if kind == 1 else 131
        return 1
    def sid_string(sid, output):
        text = state.user_sid if sid == 101 else state.app_sid
        data = ctypes.create_unicode_buffer(text)
        state.buffers.append(data)
        ctypes.cast(output, ctypes.POINTER(wintypes.LPWSTR))[0] = ctypes.cast(data, wintypes.LPWSTR)
        return 1
    def descriptor(text, revision, output, length):
        assert revision == 1 and length is None
        if not state.security_ok:
            state.error = 5
            return 0
        state.descriptor = text
        output._obj.value = 1234
        return 1
    def create(path, attributes):
        assert attributes._obj.lpSecurityDescriptor == 1234
        assert not attributes._obj.bInheritHandle
        state.created.append(path)
        error = state.directory_errors.pop(0) if state.directory_errors else 0
        state.error = error
        return not error
    kernel = SimpleNamespace(
        GetCurrentProcess=Mock(return_value=11), GetCurrentThread=Mock(return_value=12),
        CloseHandle=Mock(side_effect=lambda token: state.closed.append(token.value) or 1),
        LocalFree=Mock(side_effect=lambda item: state.freed.append(item) or None),
        CreateDirectoryW=Mock(side_effect=create),
    )
    security = SimpleNamespace(
        OpenThreadToken=Mock(side_effect=thread), OpenProcessToken=Mock(side_effect=process),
        GetTokenInformation=Mock(side_effect=information), IsValidSid=Mock(side_effect=lambda p: state.sid_valid),
        ConvertSidToStringSidW=Mock(side_effect=sid_string),
        ConvertStringSecurityDescriptorToSecurityDescriptorW=Mock(side_effect=descriptor),
    )
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kw: kernel if name=='kernel32' else security, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: state.error, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda code: PermissionError(f'native error {code}'), raising=False)
    return state, kernel, security


def test_descriptor_uses_effective_token_only_not_broad_groups(native_api):
    state, _, security = native_api
    expected = 'D:P' + ''.join(f'(A;OICI;FA;;;{sid})' for sid in ('SY', 'BA', state.user_sid, state.app_sid))
    assert release._windows_appcontainer_sddl() == expected
    assert state.closed == [100] and len(state.freed) == 2
    security.OpenProcessToken.assert_called_once()


def test_impersonation_token_not_replaced_with_process_identity(native_api):
    state, _, security = native_api
    state.thread_error = 0
    assert state.app_sid in release._windows_appcontainer_sddl()
    security.OpenProcessToken.assert_not_called()


def test_access_denied_thread_token_does_not_use_process_or_create(native_api):
    state, kernel, security = native_api
    state.thread_error = 5
    with pytest.raises(PermissionError):
        release._windows_appcontainer_sddl()
    security.OpenProcessToken.assert_not_called()
    kernel.CreateDirectoryW.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('process_ok', False), ('token_query_ok', False), ('token_flag', 2),
    ('sid_valid', False), ('required', 999999), ('app_sid', 'S-1-15-2-1'),
    ('app_sid', 'S-1-1-0'), ('user_sid', 'S-1-5-1)(A;;FA;;;WD)'),
])
def test_unavailable_or_invalid_identity_never_yields_descriptor(native_api, field, value):
    state, _, _ = native_api
    setattr(state, field, value)
    with pytest.raises((OSError, release.ReleaseManifestError)):
        release._windows_appcontainer_sddl()
    if state.process_ok:
        assert state.closed == [100]


def test_noncontainer_does_not_request_sid_or_make_descriptor(native_api):
    state, _, security = native_api
    state.token_flag = 0
    assert release._windows_appcontainer_sddl() is None
    security.ConvertSidToStringSidW.assert_not_called()
    assert state.closed == [100]


def test_new_private_stage_has_explicit_descriptor_and_bounded_exclusive_retry(native_api, tmp_path):
    state, kernel, _ = native_api
    state.directory_errors = [183, 80, 0]
    expected = 'D:P(A;OICI;FA;;;SID_FROM_OS_FIXTURE)'
    stage = release._windows_private_stage(tmp_path/'release', expected)
    assert state.descriptor == expected
    assert len(state.created) == 3
    assert len(set(state.created)) == 3
    assert stage.parent == tmp_path and stage.name.startswith('.release-')
    assert len(stage.name.split('-')[-1]) == 32
    assert len(state.freed) == 1
    kernel.CloseHandle.assert_not_called()


def test_create_denial_does_not_retry_with_inherited_or_null_dacl(native_api, tmp_path):
    state, kernel, _ = native_api
    state.directory_errors = [5]
    with pytest.raises(PermissionError):
        release._windows_private_stage(tmp_path/'release', 'private-fixture')
    assert len(state.created) == 1 and len(state.freed) == 1
    kernel.CreateDirectoryW.assert_called_once()


def test_collision_limit_does_not_reuse_an_existing_directory(native_api, tmp_path):
    state, _, _ = native_api
    state.directory_errors = [183]*16
    with pytest.raises(FileExistsError, match='collision limit'):
        release._windows_private_stage(tmp_path/'release', 'private-fixture')
    assert len(state.created) == 16 and len(state.freed) == 1


def test_descriptor_conversion_failure_does_not_create_directory(native_api, tmp_path):
    state, kernel, _ = native_api
    state.security_ok = False
    with pytest.raises(PermissionError):
        release._windows_private_stage(tmp_path/'release', 'private-fixture')
    kernel.CreateDirectoryW.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows token probe')
def test_native_ordinary_host_has_no_appcontainer_descriptor():
    assert release._windows_appcontainer_sddl() is None
