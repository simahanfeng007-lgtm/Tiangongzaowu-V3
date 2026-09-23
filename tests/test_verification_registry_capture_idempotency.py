"""A later registry observation must remain idempotent across Gateway restarts."""
from __future__ import annotations

import json

import pytest

from total_gateway.store import GatewayStateStore, StoreConflictError
from total_gateway.verification_registry import VerifierRegistry


def _row(store, snapshot_id):
    return tuple(store._connection.execute(
        "SELECT registry_snapshot_id, snapshot_json, snapshot_sha256, "
        "captured_at_ms, recorded_at_ms FROM verification_registry_snapshot "
        "WHERE registry_snapshot_id = ?", (snapshot_id,),
    ).fetchone())


@pytest.mark.parametrize("restart", [False, True])
def test_recapture_preserves_first_observation_and_is_idempotent(tmp_path, restart):
    path = tmp_path / "gateway.sqlite3"
    store = GatewayStateStore.open(path, now_ms=900)
    try:
        first = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1_000)
        assert store.put_registry_snapshot(first, recorded_at_ms=1_001)
        before = _row(store, first.registry_snapshot_id)
        if restart:
            store.close()
            store = GatewayStateStore.open(path, now_ms=133_000)
        # Production admission creates a fresh registry at each issued_at_ms.
        later = VerifierRegistry.with_defaults().snapshot(captured_at_ms=133_000)
        assert later != first
        assert later.registry_snapshot_id == first.registry_snapshot_id
        assert later.snapshot_sha256 == first.snapshot_sha256
        assert store.put_registry_snapshot(later, recorded_at_ms=133_001) is False
        assert _row(store, first.registry_snapshot_id) == before
        assert store.get_registry_snapshot(first.registry_snapshot_id) == first
        assert store._connection.execute(
            "SELECT count(*) FROM verification_registry_snapshot"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_real_descriptor_change_gets_new_identity_and_cannot_reuse_old_one(tmp_path):
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        registry = VerifierRegistry.with_defaults()
        first = registry.snapshot(captured_at_ms=1_000)
        assert store.put_registry_snapshot(first, recorded_at_ms=1_001)
        descriptor = registry.descriptors[0]
        changed = descriptor.model_copy(update={
            "timeout_ms": descriptor.timeout_ms + 1,
        }).with_computed_sha256()
        later = VerifierRegistry((changed, *registry.descriptors[1:])).snapshot(
            captured_at_ms=133_000,
        )
        assert later.has_valid_identity()
        assert later.registry_snapshot_id != first.registry_snapshot_id
        forged = later.model_copy(update={
            "registry_snapshot_id": first.registry_snapshot_id,
        })
        with pytest.raises(ValueError, match="identity does not match"):
            store.put_registry_snapshot(forged, recorded_at_ms=133_001)
        assert store.get_registry_snapshot(first.registry_snapshot_id) == first
        assert store.put_registry_snapshot(later, recorded_at_ms=133_001)
        assert store._connection.execute(
            "SELECT count(*) FROM verification_registry_snapshot"
        ).fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize("tamper", [
    "descriptor", "snapshot_id", "stored_hash", "stored_capture", "invalid_schema",
])
def test_recapture_never_masks_corrupt_or_conflicting_retained_content(tmp_path, tamper):
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        registry = VerifierRegistry.with_defaults()
        first = registry.snapshot(captured_at_ms=1_000)
        store.put_registry_snapshot(first, recorded_at_ms=1_001)
        payload = first.model_dump(mode="json")
        column, value = "snapshot_json", None
        if tamper == "descriptor":
            payload["verifiers"][0]["timeout_ms"] += 1
        elif tamper == "snapshot_id":
            payload["registry_snapshot_id"] = "vrg_" + "0" * 64
        elif tamper == "stored_hash":
            column, value = "snapshot_sha256", "0" * 64
        elif tamper == "stored_capture":
            column, value = "captured_at_ms", 999
        elif tamper == "invalid_schema":
            value = "[]"  # Valid SQLite JSON, invalid RegistrySnapshot contract.
        if value is None:
            value = json.dumps(payload, separators=(",", ":"))
        # Simulate damaged persisted evidence only in this test's temporary DB.
        store._connection.execute(
            f"UPDATE verification_registry_snapshot SET {column} = ? "
            "WHERE registry_snapshot_id = ?", (value, first.registry_snapshot_id),
        )
        store._connection.commit()
        before = _row(store, first.registry_snapshot_id)
        later = registry.snapshot(captured_at_ms=133_000)
        with pytest.raises(StoreConflictError):
            store.put_registry_snapshot(later, recorded_at_ms=133_001)
        assert _row(store, first.registry_snapshot_id) == before
    finally:
        store.close()


@pytest.mark.parametrize("capture", [-1, True])
def test_recapture_revalidates_metadata_bypassed_by_model_copy(tmp_path, capture):
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        first = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1_000)
        store.put_registry_snapshot(first, recorded_at_ms=1_001)
        before = _row(store, first.registry_snapshot_id)
        invalid = first.model_copy(update={"captured_at_ms": capture})
        assert invalid.has_valid_identity()  # Time is deliberately not hashed.
        with pytest.raises(ValueError):
            store.put_registry_snapshot(invalid, recorded_at_ms=133_001)
        assert _row(store, first.registry_snapshot_id) == before
    finally:
        store.close()
