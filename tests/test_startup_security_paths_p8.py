"""Focused startup path fault injection; not production or sandbox approval."""
import os
from pathlib import Path

import pytest

from runtime_security import EphemeralTestProtector, path_identity
from total_gateway.tickets import ProtectedKeyStore


@pytest.fixture
def protected_key(tmp_path):
    store = ProtectedKeyStore(tmp_path / "authority", protector=EphemeralTestProtector())
    created = store.create_key(
        kid="path-contract", purpose="execution_ticket", audience="tiangong-backend",
        issuer="tiangong-total-gateway", not_before_ms=0, not_after_ms=100_000,
        component_manifest_hash="a" * 64, created_at_ms=1_000,
    )
    return store, created


@pytest.mark.skipif(os.name != "nt", reason="Windows native key path contract")
def test_key_load_under_dos_resolution_denial_preserves_key_binding(protected_key, monkeypatch):
    store, created = protected_key

    def denied(*args, **kwargs):
        raise PermissionError("controlled DOS-volume lookup denial")

    with monkeypatch.context() as fault:
        fault.setattr(Path, "resolve", denied)
        key = store.load_private_key(created.private_envelope)
    import hashlib
    assert hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest() == created.public_descriptor.public_key_sha256


@pytest.mark.skipif(os.name != "nt", reason="Windows native key path contract")
def test_native_key_path_failure_precedes_decryption(protected_key, monkeypatch):
    store, created = protected_key

    def denied(path):
        raise PermissionError("native key identity unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("key decryption must not precede native path verification")

    monkeypatch.setattr(path_identity, "_windows_final_path", denied)
    monkeypatch.setattr(store.protector, "unprotect", forbidden)
    with pytest.raises(PermissionError, match="native key identity unavailable"):
        store.load_private_key(created.private_envelope)
