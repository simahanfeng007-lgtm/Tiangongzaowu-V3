"""Real Windows DPAPI/file lifecycle under both host and AppContainer tokens.

No model, production credentials, alternate Runtime or publication. Raw test
observations contain only statuses; encrypted test keys never enter reports.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
import hashlib, json, os, pathlib, sys, traceback
home = pathlib.Path(__file__).absolute().parent
sys.path[:0] = [str(home/'src'), str(home/'backend')]
from contracts import ComponentManifest, ProtectedPrivateKeyEnvelope
from total_gateway.runtime_authority import RuntimeTicketAuthority
from total_gateway.tickets import ProtectedKeyStore
from total_gateway import windows_private_files as private
from runtime_security.path_identity import PathIdentityError, resolve_existing_path

def observe_failed_write_cleanup(home, private, encrypted, report):
    # Reaching the injected failure is separate evidence from file cleanup.
    factory = private._private_file_api
    flushed_handles = []
    class KernelFault:
        def __init__(self, kernel):self.kernel = kernel
        def __getattr__(self, name):return getattr(self.kernel, name)
        def FlushFileBuffers(self, handle):
            flushed_handles.append(handle)
            return 0
    def failing_api():
        c,w,k,a = factory();return c,w,KernelFault(k),a
    failed = home/'failed-write.bin'
    private._private_file_api = failing_api
    try:
        try:private.write_container_private_file(failed, encrypted)
        except OSError:pass
        else:raise AssertionError('failed flush accepted')
    finally:private._private_file_api = factory
    assert len(flushed_handles) == 1, 'write failed before the injected flush'
    report['failed_write_injection_observed'] = True
    assert not failed.exists(), 'failed write left its owned file'
    report['failed_write_cleanup'] = True

def observe_other_package(home, principal, private, report):
    # The host creates and independently reads this exact protected fixture
    # AFTER the broker copies the workspace. A creation failure is never a
    # successful negative read, and copying cannot silently discard its DACL.
    fixture = json.loads((home/'different-package-fixture.json').read_text(encoding='utf-8'))
    assert fixture['schema'] == 'tiangong.p8-foreign-private-file.v1'
    assert fixture['prepared'] is True
    assert fixture['user_sha256'] == hashlib.sha256(principal[0].encode()).hexdigest()
    assert fixture['reader_package_sha256'] == hashlib.sha256(principal[1].encode()).hexdigest()
    assert fixture['fixture_package_sha256'] != fixture['reader_package_sha256']
    report['other_package_fixture_prepared'] = True
    foreign = home/'different-package.bin'
    # Record the OS access check separately from the product reader's guard.
    try:foreign.read_bytes()
    except PermissionError:report['other_package_os_denied'] = True
    else:raise AssertionError('OS allowed another package to read fixture')
    try:private.read_container_private_file(foreign)
    except PermissionError:report['other_package_denial_kind'] = 'os_access_denied'
    except private.WindowsPrivateFileError as exc:
        # pathlib can report an inaccessible file as non-file before the
        # reader obtains a handle. That is distinct from an observed ACE
        # mismatch; unrelated validation errors must still fail the test.
        reasons = {
            'private file is missing, linked or unsafe': 'unsafe_file_rejected',
            'private file DACL principal mismatch': 'acl_principal_rejected',
        }
        if str(exc) not in reasons:raise
        report['other_package_denial_kind'] = reasons[str(exc)]
    else:raise AssertionError('other package accepted')
    report['other_package_denied'] = True

report = {'schema':'tiangong.p8-key-lifecycle.v1', 'may_publish':False, 'may_authorize':False, 'may_execute':False}
try:
    principal = private.current_appcontainer_principal()
    report['appcontainer'] = principal is not None
    if principal is not None:
        # The host prepared a real junction after the broker copy. First prove
        # its target is readable under this exact token, then require the
        # product observer to reject both leaf and ancestor traversal.
        normal = home/'path-target'/'selected'/'payload.txt'
        assert normal.read_bytes() == b'path-identity-control'
        assert resolve_existing_path(normal) == normal
        assert (home/'path-junction'/'selected'/'payload.txt').read_bytes() == normal.read_bytes()
        report['path_target_readable'] = True
        for path, label in ((home/'path-junction', 'path_leaf_junction_rejected'),
                            (home/'path-junction'/'selected'/'payload.txt', 'path_ancestor_junction_rejected')):
            try:resolve_existing_path(path)
            except PathIdentityError as exc:
                assert str(exc) == 'link_or_junction', str(exc)
                report[label] = True
            else:raise AssertionError('container path observer followed a junction')
    store = ProtectedKeyStore(home/'private-keys')
    def create(kid):
        return store.create_key(kid=kid,purpose='execution_ticket',audience='omni_body',
            issuer='tiangong-total-gateway',not_before_ms=0,not_after_ms=999999,
            component_manifest_hash='a'*64,created_at_ms=0)
    def reload_from_disk(kid):
        reopened = ProtectedKeyStore(home/'private-keys')
        metadata_path = reopened._storage_paths(kid)[1]
        observed = private.read_container_private_file(metadata_path) if principal is not None else None
        raw = observed[0] if observed is not None else metadata_path.read_bytes()
        envelope = ProtectedPrivateKeyEnvelope.model_validate_json(raw,strict=True)
        assert envelope.kid == kid and envelope.has_valid_sha256()
        return envelope,reopened.load_private_key(envelope)
    first = create('first')
    first_public = first.public_descriptor.public_key_sha256
    del first
    first_envelope,key = reload_from_disk('first')
    assert hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest() == first_public
    key.public_key().verify(key.sign(b'private-key-lifecycle'),b'private-key-lifecycle')
    report['create_reload_sign'] = True
    create('rotation')
    second_envelope,new_key = reload_from_disk('rotation')
    assert key.public_key().public_bytes_raw() != new_key.public_key().public_bytes_raw()
    new_key.public_key().verify(new_key.sign(b'rotated-key-lifecycle'),b'rotated-key-lifecycle')
    report['new_version_reload'] = True
    report['metadata_disk_reopen'] = True
    # Reopen the actual authority record using new objects and disk metadata.
    # This is deliberately not labelled a process restart/resume observation.
    manifest = ComponentManifest.model_validate_json((home/'component-manifest.json').read_bytes(),strict=True)
    authority_root = home/'ticket-authority'
    authority = RuntimeTicketAuthority.open(authority_root,manifest,now_ms=1000)
    identity = [(signer.kid,signer.private_key.public_key().public_bytes_raw())
        for signer in (authority.execution_signer,authority.delivery_signer)]
    authority_bytes = (authority_root/'authority.json').read_bytes()
    del authority
    reopened = RuntimeTicketAuthority.open(authority_root,manifest,now_ms=2000)
    assert (authority_root/'authority.json').read_bytes() == authority_bytes
    for signer,(kid,public_bytes) in zip((reopened.execution_signer,reopened.delivery_signer),identity,strict=True):
        assert signer.kid == kid and signer.private_key.public_key().public_bytes_raw() == public_bytes
        signer.private_key.public_key().verify(signer.private_key.sign(b'reopened-authority'),b'reopened-authority')
    report['authority_disk_reopen'] = True
    try:create('first')
    except FileExistsError:report['duplicate_rejected'] = True
    else:raise AssertionError('duplicate accepted')
    if principal is not None:
        # A genuine ciphertext, protected before it becomes visible, survives
        # metadata change, atomic replacement and deletion under this token.
        encrypted = (store.root/first_envelope.storage_relative_path).read_bytes()
        target=home/'scratch.bin'; replacement=home/'replacement.bin'
        private.write_container_private_file(target,encrypted)
        private.write_container_private_file(replacement,encrypted)
        os.chmod(target,0o600)
        os.replace(replacement,target)
        assert private.read_container_private_file(target)[0]==encrypted
        target.unlink()
        report['replace_read_cleanup'] = True
        # Failure injection uses the actual opened file, actual failed-write
        # cleanup and actual OS handle. Only FlushFileBuffers is denied.
        observe_failed_write_cleanup(home,private,encrypted,report)
        observe_other_package(home,principal,private,report)
    # Truncated/tampered ciphertext must fail before returning a signing key.
    path=store.root/second_envelope.storage_relative_path
    path.write_bytes(b'corrupted')
    try:reload_from_disk('rotation')
    except OSError:report['tamper_rejected'] = True
    else:raise AssertionError('tampered key accepted')
    for path in store.keys_root.iterdir():path.unlink()
    assert not tuple(store.keys_root.iterdir())
    for path in (authority_root/'keys').iterdir():path.unlink()
    (authority_root/'authority.json').unlink()
    assert not tuple((authority_root/'keys').iterdir())
    report['key_cleanup'] = True
    report['status']='KEY_LIFECYCLE_OBSERVED'
except Exception as exc:
    report.update(status='FAILED',error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
(home/'key-lifecycle.json').write_text(json.dumps(report,sort_keys=True),encoding='utf-8')
print(json.dumps(report))
raise SystemExit(0 if report['status']=='KEY_LIFECYCLE_OBSERVED' else 1)
'''


@pytest.mark.filterwarnings('error::pytest.PytestUnhandledThreadExceptionWarning')
def test_native_worker_compiles_and_trusted_imports_are_available():
    compile(WORKER, '<private-key-worker>', 'exec')
    completed = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c',
        "import sys;sys.path[:0]=sys.argv[1:3];"
        "from omni_body_skill.tools.sandbox_runtime import SandboxRunner;"
        "from total_gateway.windows_private_files import validate_private_dacl;"
        "from total_gateway.tickets import ProtectedKeyStore",
        str(ROOT/'src'), str(ROOT/'app/backend/tiangong-backend')],
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr


_COMMON_OBSERVATIONS = (
    'create_reload_sign', 'new_version_reload', 'duplicate_rejected',
    'metadata_disk_reopen', 'authority_disk_reopen', 'tamper_rejected', 'key_cleanup',
)
_CONTAINER_OBSERVATIONS = (
    'path_target_readable', 'path_leaf_junction_rejected', 'path_ancestor_junction_rejected',
    'replace_read_cleanup', 'failed_write_injection_observed', 'failed_write_cleanup', 'other_package_fixture_prepared',
    'other_package_os_denied', 'other_package_denied',
)


def _validate_lifecycle_report(report: object, *, contained: bool) -> None:
    """Validate test observations only; this never authorizes product execution."""
    assert isinstance(report, dict), 'native observation is not an object'
    assert report.get('schema') == 'tiangong.p8-key-lifecycle.v1'
    assert report.get('status') == 'KEY_LIFECYCLE_OBSERVED', report
    assert report.get('appcontainer') is contained, report
    assert not any(name in report for name in
        ('error', 'error_type', 'traceback', 'cleanup_error', 'failed_phase')), report
    assert all(report.get(name) is False for name in
        ('may_publish', 'may_authorize', 'may_execute')), report
    required = _COMMON_OBSERVATIONS + (_CONTAINER_OBSERVATIONS if contained else ())
    assert all(report.get(name) is True for name in required), report
    if contained:
        assert report.get('other_package_denial_kind') in (
            'os_access_denied', 'unsafe_file_rejected', 'acl_principal_rejected'), report


def _fixture_report(*, contained: bool) -> dict:
    return {
        'schema': 'tiangong.p8-key-lifecycle.v1',
        'status': 'KEY_LIFECYCLE_OBSERVED', 'appcontainer': contained,
        'may_publish': False, 'may_authorize': False, 'may_execute': False,
        **dict.fromkeys(_COMMON_OBSERVATIONS, True),
        **(dict.fromkeys(_CONTAINER_OBSERVATIONS, True) if contained else {}),
        **({'other_package_denial_kind': 'os_access_denied'} if contained else {}),
    }


@pytest.mark.parametrize('contained', [False, True])
def test_lifecycle_report_accepts_only_complete_matching_fixture(contained):
    _validate_lifecycle_report(_fixture_report(contained=contained), contained=contained)


@pytest.mark.parametrize('field,value', [
    ('status', 'SKIPPED'), ('schema', 'unbound'), ('appcontainer', False),
    ('appcontainer', 1), ('create_reload_sign', 1), ('new_version_reload', False),
    ('replace_read_cleanup', False), ('failed_write_cleanup', None),
    ('failed_write_injection_observed', False),
    ('other_package_denied', 'True'), ('may_publish', True), ('may_execute', 0),
    ('other_package_fixture_prepared', False), ('other_package_os_denied', False),
    ('other_package_denial_kind', 'creation_denied'),
    ('metadata_disk_reopen', False), ('authority_disk_reopen', False),
    ('error', ''), ('cleanup_error', ''), ('failed_phase', ''), ('traceback', ''),
])
def test_lifecycle_report_rejects_missing_or_contradictory_evidence(field, value):
    report = _fixture_report(contained=True)
    report[field] = value
    with pytest.raises(AssertionError):
        _validate_lifecycle_report(report, contained=True)


_FOREIGN_PAYLOAD = b'tiangong-private-file-access-control-probe'


def _foreign_fixture_metadata(principal, other_package):
    user, reader_package = principal
    assert other_package != reader_package
    return {
        'schema': 'tiangong.p8-foreign-private-file.v1', 'prepared': True,
        'user_sha256': hashlib.sha256(user.encode()).hexdigest(),
        'reader_package_sha256': hashlib.sha256(reader_package.encode()).hexdigest(),
        'fixture_package_sha256': hashlib.sha256(other_package.encode()).hexdigest(),
    }


def _read_foreign_fixture(path, principal):
    from total_gateway import windows_private_files as private
    # Opening checks the actual binary DACL on this same handle. The host
    # control proves the fixture exists, is complete and keeps its exact ACL.
    with private._file_handle(path, principal, create=False) as (handle, api):
        c, w, kernel, _ = api
        count = w.DWORD()
        data = c.create_string_buffer(len(_FOREIGN_PAYLOAD) + 1)
        if not kernel.ReadFile(handle, data, len(data), c.byref(count), None):
            raise c.WinError(c.get_last_error())
        assert data.raw[:count.value] == _FOREIGN_PAYLOAD


def _prepare_foreign_fixture(workspace):
    from omni_body_skill.tools import windows_appcontainer
    from total_gateway import windows_private_files as private
    from total_gateway.tickets import _current_user_sid
    assert private.current_appcontainer_principal() is None, 'fixture requires host token'
    sid = windows_appcontainer._appcontainer_sid('TiangongV3.ToolSandbox')
    try:
        reader_package = windows_appcontainer._sid_string(sid)
    finally:
        windows_appcontainer.advapi32.FreeSid(sid)
    user = _current_user_sid()
    prefix, suffix = reader_package.rsplit('-', 1)
    other_package = prefix + '-' + str(int(suffix) ^ 1)
    foreign_principal = user, other_package
    foreign = workspace / 'different-package.bin'
    with private._file_handle(foreign, foreign_principal, create=True) as (handle, api):
        c, w, kernel, _ = api
        written = w.DWORD()
        data = c.create_string_buffer(_FOREIGN_PAYLOAD)
        if not kernel.WriteFile(handle, data, len(_FOREIGN_PAYLOAD), c.byref(written), None):
            raise c.WinError(c.get_last_error())
        assert written.value == len(_FOREIGN_PAYLOAD), 'foreign fixture short write'
        if not kernel.FlushFileBuffers(handle):
            raise c.WinError(c.get_last_error())
    _read_foreign_fixture(foreign, foreign_principal)
    # Emit the preparation witness only after independent host read/ACL checks.
    with (workspace / 'different-package-fixture.json').open('x', encoding='utf-8') as output:
        json.dump(_foreign_fixture_metadata((user, reader_package), other_package), output)
    return foreign_principal


def _launch_with_foreign_fixture(launcher, command, cwd, env, limits, sandbox_root,
                                *, require_os_containment):
    # Test-only preparation at the existing launch boundary. It does not
    # replace the launcher, containment requirements, token or network policy.
    foreign_principal = _prepare_foreign_fixture(cwd)
    result = launcher(command, cwd, env, limits, sandbox_root,
                      require_os_containment=require_os_containment)
    # A reader that repaired or replaced the fixture cannot satisfy this check.
    _read_foreign_fixture(cwd / 'different-package.bin', foreign_principal)
    return result


@pytest.mark.parametrize('failure', [PermissionError('fixture create denied'),
                                   OSError('fixture flush failed')])
def test_foreign_fixture_preparation_failure_never_counts_as_read_denial(monkeypatch, tmp_path, failure):
    launches = []

    def fail_preparation(workspace):
        raise failure

    monkeypatch.setattr(sys.modules[__name__], '_prepare_foreign_fixture', fail_preparation)
    with pytest.raises(type(failure), match=str(failure)):
        _launch_with_foreign_fixture(lambda *args, **kwargs: launches.append(args),
                                    [], tmp_path, {}, None, tmp_path,
                                    require_os_containment=True)
    assert launches == []
    assert not (tmp_path / 'different-package-fixture.json').exists()


def _worker_function(name):
    # Execute the actual worker function for portable failure-path regression,
    # without importing/running the Windows worker or reproducing its logic.
    function = next(node for node in ast.parse(WORKER).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {'hashlib': hashlib, 'json': json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<foreign-read-observer>', 'exec'), namespace)
    return namespace[name]


def _worker_foreign_observer():
    return _worker_function('observe_other_package')


@pytest.mark.parametrize('phase', ['create', 'write', 'flush', 'residue', 'accepted'])
def test_failed_write_requires_observed_flush_and_actual_cleanup(tmp_path, phase):
    private = SimpleNamespace()
    factory = lambda: (None, None, SimpleNamespace(), None)
    private._private_file_api = factory

    def write(path, payload):
        if phase in ('create', 'write'):
            raise OSError(phase + ' failed before flush')
        path.write_bytes(payload)
        _, _, kernel, _ = private._private_file_api()
        assert kernel.FlushFileBuffers(55) == 0
        if phase != 'residue':
            path.unlink()
        if phase != 'accepted':
            raise OSError('injected flush failed')

    private.write_container_private_file = write
    report = {}
    observe = _worker_function('observe_failed_write_cleanup')
    if phase == 'flush':
        observe(tmp_path, private, b'fixture', report)
        assert report == {'failed_write_injection_observed': True, 'failed_write_cleanup': True}
    else:
        with pytest.raises(AssertionError):
            observe(tmp_path, private, b'fixture', report)
        assert report.get('failed_write_cleanup') is not True
    assert private._private_file_api is factory


@pytest.mark.parametrize('prepared', [None, False])
def test_worker_does_not_accept_denial_without_successful_fixture_preparation(tmp_path, prepared):
    principal = ('S-1-5-21-1-2-3-1001', 'S-1-15-2-1-2-3-4-5-6-7')
    if prepared is not None:
        metadata = _foreign_fixture_metadata(principal, 'S-1-15-2-1-2-3-4-5-6-8')
        metadata['prepared'] = prepared
        (tmp_path / 'different-package-fixture.json').write_text(json.dumps(metadata), encoding='utf-8')
    reads, report = [], {}
    private = SimpleNamespace(read_container_private_file=lambda path: reads.append(path))
    with pytest.raises((FileNotFoundError, AssertionError)):
        _worker_foreign_observer()(tmp_path, principal, private, report)
    assert reads == [] and report.get('other_package_denied') is not True


@pytest.mark.parametrize('failure,kind', [
    (PermissionError('access denied'), 'os_access_denied'),
    (ValueError('private file DACL principal mismatch'), 'acl_principal_rejected'),
    (ValueError('private file is missing, linked or unsafe'), 'unsafe_file_rejected'),
    (ValueError('private file SID bounds are invalid'), None),
    (None, None),
])
def test_worker_distinguishes_os_denial_from_reader_validation(tmp_path, monkeypatch, failure, kind):
    principal = ('S-1-5-21-1-2-3-1001', 'S-1-15-2-1-2-3-4-5-6-7')
    metadata = _foreign_fixture_metadata(principal, 'S-1-15-2-1-2-3-4-5-6-8')
    (tmp_path / 'different-package-fixture.json').write_text(json.dumps(metadata), encoding='utf-8')
    foreign = tmp_path / 'different-package.bin'
    original_read = Path.read_bytes

    def denied_read(path):
        if path == foreign:
            raise PermissionError('OS denied another package')
        return original_read(path)

    def product_read(path):
        assert path == foreign
        if failure is not None:
            raise failure
        return _FOREIGN_PAYLOAD, principal[0], 'unexpected-accepted-acl'

    monkeypatch.setattr(Path, 'read_bytes', denied_read)
    private = SimpleNamespace(read_container_private_file=product_read, WindowsPrivateFileError=ValueError)
    report = {}
    if kind is None:
        with pytest.raises((ValueError, AssertionError)):
            _worker_foreign_observer()(tmp_path, principal, private, report)
        assert report.get('other_package_denied') is not True
    else:
        _worker_foreign_observer()(tmp_path, principal, private, report)
        assert report['other_package_denied'] is True
        assert report['other_package_denial_kind'] == kind
    assert report['other_package_fixture_prepared'] is True
    assert report['other_package_os_denied'] is True


def _preserve_native_evidence(label: str, process: dict, workspace: Path) -> None:
    # Optional CI-owned destination, outside the sandbox and only for test
    # statuses/stdout. Ciphertext and plaintext test keys are never copied.
    destination = os.environ.get('TIANGONG_P8_KEY_EVIDENCE_DIR')
    if not destination:
        return
    root = Path(destination)
    assert root.is_absolute() and root.is_dir() and not root.is_symlink()
    with (root / f'key-{label}-process.json').open('x', encoding='utf-8') as output:
        json.dump(process, output, sort_keys=True)
        output.write('\n')
    observed = workspace / 'key-lifecycle.json'
    if observed.is_file() and not observed.is_symlink():
        assert observed.stat().st_size <= 2 * 1024 * 1024
        with (root / f'key-{label}-observation.json').open('xb') as output:
            output.write(observed.read_bytes())


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Windows DPAPI and AppContainer')
@pytest.mark.parametrize('label', ['host', 'contained'])
def test_real_protected_key_lifecycle_host_and_container(tmp_path, label, monkeypatch):
    # Separate cases collect both independent outcomes: a failed host control
    # must not hide the contained observation, or vice versa.
    from omni_body_skill.tools.sandbox_runtime import SandboxRunner, SandboxLimits
    from omni_body_skill.tools import sandbox_runtime
    from tests.test_delivery_contracts import component_manifest
    base = tmp_path.resolve(strict=True)
    workspace = base / label
    workspace.mkdir()
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(ROOT / 'src', workspace / 'src', ignore=ignore)
    shutil.copytree(ROOT / 'app/backend/tiangong-backend', workspace / 'backend', ignore=ignore)
    (workspace / 'worker.py').write_text(WORKER, encoding='utf-8')
    (workspace / 'component-manifest.json').write_text(component_manifest().model_dump_json(), encoding='utf-8')
    command = [sys.executable, '-I', '-B', '-X', 'utf8', str(workspace / 'worker.py')]
    if label == 'host':
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=120, check=False)
        process = {'ok': result.returncode == 0, 'returncode': result.returncode,
                   'stdout': result.stdout, 'stderr': result.stderr}
    else:
        launcher = sandbox_runtime._run_windows_appcontainer
        phase = {'name': 'other_package_fixture_prepare'}

        def launch_prepared(*args, **kwargs):
            def observe_launch(*launch_args, **launch_kwargs):
                phase['name'] = 'contained_worker'
                actual_workspace = launch_args[1]
                target = actual_workspace / 'path-target'
                (target / 'selected').mkdir(parents=True)
                (target / 'selected' / 'payload.txt').write_bytes(b'path-identity-control')
                junction = actual_workspace / 'path-junction'
                prepared = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
                    capture_output=True, text=True, check=False)
                assert prepared.returncode == 0, prepared.stdout + prepared.stderr
                assert junction.is_junction()
                try:
                    result = launcher(*launch_args, **launch_kwargs)
                finally:
                    # Remove only the test link, before broker output scanning.
                    junction.rmdir()
                phase['name'] = 'other_package_fixture_recheck'
                return result
            return _launch_with_foreign_fixture(observe_launch, *args, **kwargs)

        monkeypatch.setattr(sandbox_runtime, '_run_windows_appcontainer', launch_prepared)
        try:
            process = SandboxRunner(workspace, base / 'sandbox', base / 'trash',
                SandboxLimits(timeout_seconds=120, max_changed_bytes=67108864)).run(
                    command, require_os_containment=True)
        except Exception as exc:
            _preserve_native_evidence(label, {'ok': False, 'failed_phase': phase['name'],
                'error_type': type(exc).__name__, 'error': str(exc)}, workspace)
            raise
    # Preserve original failure evidence before any PASS assertions.
    _preserve_native_evidence(label, process, workspace)
    assert process['ok'] is True, process
    if label == 'contained':
        assert process['containment'] == 'windows-appcontainer' and process['network'] == 'denied'
    report = json.loads((workspace / 'key-lifecycle.json').read_text(encoding='utf-8'))
    _validate_lifecycle_report(report, contained=label == 'contained')
    print('P8_KEY_LIFECYCLE=' + json.dumps({label: report}, sort_keys=True))
