"""Life-owned persistence with a trusted, non-authorizing host path observer.

These local contracts do not establish real AppContainer startup or publication.
"""
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from life_service import store_connection
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.store import LifeShadowStore


ROOT = Path(__file__).resolve().parents[1]


def _open(path, **kwargs):
    return store_connection.open_life_shadow_sqlite(
        path, create=True, now_ms=1000, error_factory=ValueError,
        initialize=lambda connection, *, now_ms: None,
        migrate=lambda connection, *, now_ms: None, **kwargs,
    )


def test_standalone_life_import_and_store_need_no_execution_package(tmp_path):
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
forbidden = {'total_gateway', 'runtime_security', 'communication_service', 'omni_body_skill'}
class NoExecutionImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in forbidden:
            raise AssertionError('Life imported execution package: ' + fullname)
sys.meta_path.insert(0, NoExecutionImports())
from life_service.store import LifeShadowStore
with LifeShadowStore.open(Path(sys.argv[2]), create=True, now_ms=1000) as store:
    assert store.path == Path(sys.argv[2])
assert not any(name.split('.')[0] in forbidden for name in sys.modules)
print('standalone-life-store-ok')
"""
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-X', 'utf8', '-c', code, str(ROOT / 'src'),
         str(tmp_path / 'standalone.shadow.sqlite3')],
        capture_output=True, text=True, timeout=30, check=False, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert 'standalone-life-store-ok' in result.stdout


def test_default_parent_resolution_remains_strict(tmp_path, monkeypatch):
    calls = []
    original = Path.resolve

    def observe(path, strict=False):
        calls.append((path, strict))
        return original(path, strict=strict)

    monkeypatch.setattr(Path, 'resolve', observe)
    opened = _open(tmp_path / 'default.shadow.sqlite3')
    opened.connection.close()
    assert calls == [(tmp_path, True)]


def test_store_forwards_observer_without_fallback_resolution(tmp_path, monkeypatch):
    observer = Mock(return_value=tmp_path)
    original = store_connection.sqlite3.connect
    connection_calls = Mock(wraps=original)
    monkeypatch.setattr(store_connection.sqlite3, 'connect', connection_calls)
    with monkeypatch.context() as fault:
        fault.setattr(Path, 'resolve', Mock(side_effect=AssertionError('must use host observer')))
        with LifeShadowStore.open(tmp_path / 'injected.shadow.sqlite3', create=True, now_ms=1000,
                                  existing_path_resolver=observer) as store:
            assert store.path == tmp_path / 'injected.shadow.sqlite3'
    observer.assert_called_once_with(tmp_path)
    assert connection_calls.call_count == 1


@pytest.mark.parametrize('error', [PermissionError('native unavailable'),
                                 ValueError('physical mismatch'), FileNotFoundError('missing parent')])
def test_failed_observer_never_connects_or_creates_database(tmp_path, monkeypatch, error):
    connect = Mock(side_effect=AssertionError('must not connect after identity failure'))
    monkeypatch.setattr(store_connection.sqlite3, 'connect', connect)
    observer = Mock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        _open(tmp_path / 'denied.shadow.sqlite3', existing_path_resolver=observer)
    observer.assert_called_once_with(tmp_path)
    connect.assert_not_called()
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize('kind', ['relative', 'missing', 'string', 'file'])
def test_invalid_observer_result_is_rejected_before_sqlite(tmp_path, monkeypatch, kind):
    target = tmp_path / 'not-a-directory'
    target.write_text('data', encoding='utf-8')
    observed = {'relative': Path('.'), 'missing': tmp_path / 'missing',
                'string': str(tmp_path), 'file': target}[kind]
    connect = Mock(side_effect=AssertionError('invalid path observation must not connect'))
    monkeypatch.setattr(store_connection.sqlite3, 'connect', connect)
    with pytest.raises(ValueError, match='parent identity is invalid'):
        _open(tmp_path / 'invalid.shadow.sqlite3', existing_path_resolver=lambda path: observed)
    connect.assert_not_called()


def test_embedded_host_forwards_observer_and_cleans_failed_initialization(tmp_path):
    environment = {'TIANGONG_LIFE_DATA_ROOT': str(tmp_path / 'data'),
                   'TIANGONG_LIFE_RUNTIME_ROOT': str(tmp_path / 'runtime')}
    denied = Mock(side_effect=PermissionError('host identity unavailable'))
    with pytest.raises(PermissionError, match='host identity unavailable'):
        EmbeddedLifeRuntime.from_environment(
            gateway_state_root=tmp_path / 'gateway', gateway_environment='test',
            environ=environment, existing_path_resolver=denied,
        )
    denied.assert_called_once_with(tmp_path / 'runtime')
    assert not (tmp_path / 'runtime/life-authority.shadow.sqlite3').exists()
    # The failed construction must release the existing writer lease, so the
    # same Life root can be opened normally, not a second/patched Runtime.
    observer = Mock(side_effect=lambda path: path.resolve(strict=True))
    runtime = EmbeddedLifeRuntime.from_environment(
        gateway_state_root=tmp_path / 'gateway', gateway_environment='test',
        environ=environment, existing_path_resolver=observer,
    )
    try:
        observer.assert_called_once_with(tmp_path / 'runtime')
        assert runtime.authority_store.path == tmp_path / 'runtime/life-authority.shadow.sqlite3'
    finally:
        runtime.close()


def test_gateway_injects_native_observer_through_existing_life_factory(tmp_path, monkeypatch):
    from total_gateway.bootstrap import GatewayConfig, InstanceEpochLease
    from total_gateway import runtime as gateway_module
    factory = Mock(side_effect=RuntimeError('stop after observing existing host wiring'))
    monkeypatch.setattr(EmbeddedLifeRuntime, 'from_environment', factory)
    config = GatewayConfig(environment='test', deployment_mode='embedded', port=0,
                           state_root=tmp_path / 'state', workspace_root=tmp_path,
                           backend_internal_token='b' * 32)
    with pytest.raises(RuntimeError, match='stop after observing'):
        gateway_module.GatewayRuntime.start(config)
    assert factory.call_args.kwargs['existing_path_resolver'] is gateway_module.resolve_existing_path
    lease = InstanceEpochLease.acquire(config.state_root, 'after-denied-host', now_ms=999999)
    lease.release()
