"""Portable lifecycle/ACL rejection tests; native AppContainer evidence is separate."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from runtime_security import EphemeralTestProtector
from total_gateway import tickets

USER='S-1-5-21-1-2-3-1001'
APP='S-1-15-2-1-2-3-4-5-6-7'


def create(store, kid='fixture'):
    return store.create_key(kid=kid,purpose='execution_ticket',audience='tiangong-backend',
        issuer='tiangong-total-gateway',not_before_ms=0,not_after_ms=10000,
        component_manifest_hash='a'*64,created_at_ms=0)


def test_container_creation_does_not_restrict_away_its_own_principal(tmp_path,monkeypatch):
    from total_gateway import windows_private_files as private
    observed=[]
    def write(path,content):
        observed.append(path.suffix)
        with path.open('xb') as output: output.write(content)
        return USER,'b'*64
    monkeypatch.setattr(private,'write_container_private_file',write)
    monkeypatch.setattr(tickets,'os',SimpleNamespace(name='nt',replace=__import__('os').replace))
    monkeypatch.setattr(tickets,'_protect_key_file',Mock(side_effect=AssertionError('host-only ACL invoked')))
    store=tickets.ProtectedKeyStore(tmp_path/'keys',protector=EphemeralTestProtector())
    created=create(store)
    assert observed==['.dtmp','.jtmp']
    assert created.private_envelope.acl_sha256=='b'*64
    assert len(list(store.keys_root.iterdir()))==2


def test_unavailable_container_identity_rejects_before_any_key_write(tmp_path,monkeypatch):
    from total_gateway import windows_private_files as private
    monkeypatch.setattr(private,'write_container_private_file',Mock(side_effect=PermissionError('identity unavailable')))
    monkeypatch.setattr(tickets,'os',SimpleNamespace(name='nt',replace=__import__('os').replace))
    store=tickets.ProtectedKeyStore(tmp_path/'keys',protector=EphemeralTestProtector())
    with pytest.raises(PermissionError,match='identity unavailable'): create(store)
    assert not list(store.keys_root.iterdir())


@pytest.mark.parametrize('mutation',[
    'D:(A;;FA;;;{u})(A;;FA;;;{a})',
    'D:P(A;;FA;;;{u})',
    'D:P(A;;FA;;;{u})(A;;FA;;;S-1-15-2-1)',
    'D:P(A;;FA;;;{u})(A;;FA;;;S-1-15-2-1-2-3-4-5-6-8)',
    'D:P(A;;FA;;;{u})(A;;FA;;;{a})(A;;FA;;;WD)',
    'D:P(A;ID;FA;;;{u})(A;;FA;;;{a})',
    'D:P(D;;FA;;;{u})(A;;FA;;;{a})',
    'D:P(A;;FR;;;{u})(A;;FA;;;{a})',
    'D:P(A;;FA;;;{u})(A;;FA;;;{u})',
    'D:P(A;;FA;;;{u})(A;;FA;;;{a})S:(ML;;NW;;;LW)',
])
def test_acl_must_be_exact_protected_user_and_single_package(mutation):
    from total_gateway.windows_private_files import validate_private_dacl
    with pytest.raises(ValueError): validate_private_dacl(mutation.format(u=USER,a=APP),(USER,APP))


def test_exact_acl_normalization_ignores_only_order_not_permissions():
    from total_gateway.windows_private_files import validate_private_dacl
    validate_private_dacl(f'D:P(A;;FA;;;{USER})(A;;FA;;;{APP})',(USER,APP))
    validate_private_dacl(f'D:PAI(A;;FA;;;{APP})(A;;0x1f01ff;;;{USER})',(USER,APP))


def test_key_load_checks_container_principal_and_acl_before_decryption(tmp_path,monkeypatch):
    from total_gateway import windows_private_files as private
    protector=EphemeralTestProtector()
    store=tickets.ProtectedKeyStore(tmp_path/'keys',protector=protector)
    made=create(store)
    path=store.root/made.private_envelope.storage_relative_path
    monkeypatch.setattr(tickets,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(private,'read_container_private_file',lambda path:(path.read_bytes(),USER,'e'*64))
    decrypt=Mock(side_effect=AssertionError('must reject before decrypt'))
    monkeypatch.setattr(protector,'unprotect',decrypt)
    with pytest.raises(OSError,match='principal or ACL'): store.load_private_key(made.private_envelope)
    decrypt.assert_not_called()


def test_preexisting_temporary_file_is_never_deleted(tmp_path):
    store=tickets.ProtectedKeyStore(tmp_path/'keys',protector=EphemeralTestProtector())
    _,_,temporary,_=store._storage_paths('fixture')
    temporary.write_bytes(b'owned by another operation')
    with pytest.raises(FileExistsError): create(store)
    assert temporary.read_bytes()==b'owned by another operation'


def test_package_identity_cannot_change_between_blob_and_metadata(tmp_path,monkeypatch):
    from total_gateway import windows_private_files as private
    count=0
    def write(path,content):
        nonlocal count
        count+=1
        path.write_bytes(content)
        return USER,('a' if count==1 else 'b')*64
    monkeypatch.setattr(private,'write_container_private_file',write)
    monkeypatch.setattr(tickets,'os',SimpleNamespace(name='nt',replace=__import__('os').replace))
    store=tickets.ProtectedKeyStore(tmp_path/'keys',protector=EphemeralTestProtector())
    with pytest.raises(OSError,match='metadata principal'): create(store)
    assert not list(store.keys_root.glob('*.jtmp'))

@pytest.fixture
def file_api(monkeypatch):
    import ctypes
    from ctypes import wintypes
    from total_gateway import windows_private_files as private
    state = SimpleNamespace(opened=[], deleted=[], closed=[], value=55, dacl=f'D:P(A;;FA;;;{USER})(A;;FA;;;{APP})')
    def create(name, access, share, attributes, disposition, flags, template):
        state.opened.append((name, access, share, attributes, disposition, flags, template))
        return state.value
    def convert(text, revision, output, size):
        output._obj.value = 456
        return 1
    kernel = SimpleNamespace(CreateFileW=Mock(side_effect=create), LocalFree=Mock(return_value=0),
        CloseHandle=Mock(side_effect=lambda h: state.closed.append(h) or 1),
        SetFileInformationByHandle=Mock(side_effect=lambda h,*args: state.deleted.append(h) or 1))
    security = SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=Mock(side_effect=convert))
    local_ctypes = SimpleNamespace(**{k:getattr(ctypes,k) for k in
        ('Structure','sizeof','byref','create_string_buffer')},
        WinError=lambda *args:PermissionError('native file denied'), get_last_error=lambda:5)
    monkeypatch.setattr(private,'_private_file_api',lambda:(local_ctypes,wintypes,kernel,security))
    monkeypatch.setattr(private,'_observed_dacl',lambda *args:state.dacl)
    return state,kernel,private


def test_native_create_uses_explicit_noninheritable_new_handle(file_api,tmp_path):
    state,kernel,private=file_api
    with private._file_handle(tmp_path/'fixture', (USER,APP), create=True) as (handle,_):
        assert handle==55
        assert state.closed==[]
    call=state.opened[0]
    assert call[1]==0x40030000 and call[2]==0 and call[4]==1 and call[5]==0x00200080
    assert call[3]._obj.inherit==0
    assert state.closed==[55] and state.deleted==[]


def test_failed_new_handle_deletes_only_the_owned_object(file_api,tmp_path):
    state,kernel,private=file_api
    failure=RuntimeError('original write failure')
    with pytest.raises(RuntimeError) as caught:
        with private._file_handle(tmp_path/'fixture',(USER,APP),create=True):
            raise failure
    assert caught.value is failure and state.deleted==[55] and state.closed==[55]


def test_failed_open_never_deletes_preexisting_file(file_api,tmp_path):
    import ctypes
    state,kernel,private=file_api
    state.value=ctypes.c_void_p(-1).value
    target=tmp_path/'fixture';target.write_bytes(b'keep')
    with pytest.raises(OSError):
        with private._file_handle(target,(USER,APP),create=True):
            raise AssertionError('unreachable')
    assert not state.deleted and not state.closed and target.read_bytes()==b'keep'


def test_cleanup_fault_preserves_primary_failure_with_notes(file_api,tmp_path):
    state,kernel,private=file_api
    kernel.SetFileInformationByHandle.side_effect=None
    kernel.SetFileInformationByHandle.return_value=0
    kernel.CloseHandle.side_effect=None
    kernel.CloseHandle.return_value=0
    failure=ValueError('original')
    with pytest.raises(ValueError) as caught:
        with private._file_handle(tmp_path/'fixture',(USER,APP),create=True):raise failure
    assert caught.value is failure
    assert len(failure.__notes__)==2


def test_acl_rejection_closes_and_discards_new_unusable_file(file_api,tmp_path):
    state,kernel,private=file_api
    state.dacl='D:(A;;FA;;;WD)'
    with pytest.raises(ValueError):
        with private._file_handle(tmp_path/'fixture',(USER,APP),create=True):
            raise AssertionError('must reject before I/O')
    assert state.deleted==[55] and state.closed==[55]
