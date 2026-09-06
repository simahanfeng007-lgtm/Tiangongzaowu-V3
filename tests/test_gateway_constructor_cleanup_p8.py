"""Actual local Stores/lease cleanup after constructor failure, not boot proof."""
import pytest

from total_gateway import runtime as module
from total_gateway.bootstrap import GatewayConfig


def test_constructor_failure_closes_opened_stores_and_releases_epoch(tmp_path, monkeypatch):
    opened, closed, leases = {}, [], []
    factories = {
        "store": module.GatewayStateStore,
        "objects": module.ContentAddressedObjectStore,
        "facts": module.FactLedger,
    }
    original_close = {name: owner.close for name, owner in factories.items()}
    for name, owner in factories.items():
        original_open = owner.open
        def open_store(*args, _name=name, _open=original_open, **kwargs):
            item = _open(*args, **kwargs)
            opened[_name] = item
            return item
        def close_store(item, _name=name, _close=original_close[name]):
            closed.append(_name)
            return _close(item)
        monkeypatch.setattr(owner, "open", staticmethod(open_store))
        monkeypatch.setattr(owner, "close", close_store)
    original_acquire = module.InstanceEpochLease.acquire
    def acquire(*args, **kwargs):
        lease = original_acquire(*args, **kwargs)
        leases.append(lease)
        return lease
    monkeypatch.setattr(module.InstanceEpochLease, "acquire", staticmethod(acquire))
    monkeypatch.setattr(module, "preflight_source_revision", lambda config: None)
    def fail_constructor(*args, **kwargs):
        raise RuntimeError("constructor fixture: home unavailable")
    monkeypatch.setattr(module.SoulBackupManager, "default_sources", fail_constructor)
    config = GatewayConfig(environment="test", port=0, state_root=tmp_path / "state")
    try:
        with pytest.raises(RuntimeError, match="constructor fixture: home unavailable"):
            module.GatewayRuntime.start(config, now_ms=100)
        assert closed == ["facts", "objects", "store"]
        assert len(leases) == 1 and not leases[0].active
        # A fresh owner can acquire only after the failed initialization releases.
        with original_acquire(config.state_root, "verified-retry", now_ms=101) as lease:
            assert lease.active
    finally:
        # Keep the red-first run hygienic even on the deliberately leaky baseline.
        for name in ("facts", "objects", "store"):
            if name in opened and name not in closed:
                original_close[name](opened[name])
        for lease in leases:
            if lease.active:
                lease.release()
