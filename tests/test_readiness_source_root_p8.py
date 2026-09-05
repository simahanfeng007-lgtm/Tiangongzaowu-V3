"""Source-root/binary identity contracts; no mocked health is a product PASS."""
import os
from pathlib import Path

import pytest

from runtime_security import path_identity
from total_gateway.readiness_collector import ProductionReadinessCollector
from total_gateway.release_manifest import generate_release_manifest, release_manifest_bytes


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "p8-readiness-source-root-" + "0" * 48
COMPONENTS = {
    "tiangong-backend", "tiangong-life-service",
    "tiangong-communication-service", "tiangong-total-gateway",
}


@pytest.fixture
def source_release(tmp_path):
    release = generate_release_manifest(ROOT)
    source = tmp_path / "selected/source"
    for descriptor in release.component_manifest.components:
        if descriptor.component_id in COMPONENTS:
            target = source / descriptor.executable_relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / descriptor.executable_relative_path).read_bytes())
    manifest = tmp_path / "separate/release/release-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(release_manifest_bytes(release))
    return release, source, manifest


def collector(release, manifest, **kwargs):
    return ProductionReadinessCollector(
        release=release, release_manifest_path=manifest,
        gateway_epoch=7, gateway_instance_id="p8-source-path-test",
        backend_token=TOKEN, life_token=TOKEN, communication_token=TOKEN,
        allow_development_release=True, **kwargs,
    )


def test_detached_manifest_requires_explicit_source_binding(source_release):
    release, source, manifest = source_release
    with pytest.raises(ValueError, match="runtime root cannot be bound"):
        collector(release, manifest)
    result = collector(release, manifest, release_source_root=source)
    assert result.evidence_profile == "development-source"
    assert result._runtime_root == source
    assert result._binary_verified == COMPONENTS


def test_explicit_wrong_source_never_falls_back_to_manifest_ancestors(source_release, tmp_path):
    release, _, _ = source_release
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    # Legacy discovery would find the checkout; an explicit root must not.
    with pytest.raises(ValueError, match="runtime root cannot be bound"):
        collector(release, ROOT / "app/release/release-manifest.json", release_source_root=wrong)


def test_selected_source_binary_drift_still_fails_verification(source_release):
    release, source, manifest = source_release
    result = collector(release, manifest, release_source_root=source)
    descriptor = next(item for item in release.component_manifest.components if item.component_id == "tiangong-backend")
    target = source / descriptor.executable_relative_path
    original = target.read_bytes()
    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    result._refresh_binary_evidence(force=True)
    assert "tiangong-backend" not in result._binary_verified


@pytest.mark.skipif(os.name != "nt", reason="Windows native readiness path contract")
def test_readiness_source_binding_survives_dos_lookup_denial(source_release, monkeypatch):
    release, source, manifest = source_release

    def denied(*args, **kwargs):
        raise PermissionError("controlled DOS-volume lookup denial")

    with monkeypatch.context() as fault:
        fault.setattr(Path, "resolve", denied)
        result = collector(release, manifest, release_source_root=source)
    assert result._binary_verified == COMPONENTS


@pytest.mark.skipif(os.name != "nt", reason="Windows native readiness path contract")
def test_readiness_native_identity_failure_cannot_bless_binaries(source_release, monkeypatch):
    release, source, manifest = source_release
    result = collector(release, manifest, release_source_root=source)

    def denied(path):
        raise PermissionError("native identity unavailable")

    monkeypatch.setattr(path_identity, "_windows_final_path", denied)
    result._refresh_binary_evidence(force=True)
    assert not result._binary_verified
