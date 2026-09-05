"""Real GatewayConfig path limits with simulated services, not native boot PASS."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from total_gateway.bootstrap import GatewayConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def worker_fixture(monkeypatch):
    spec = importlib.util.spec_from_file_location("p8_layout_worker", ROOT / "scripts/_tool_source_launch_probe.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    # The worker is trusted host code. These seams are test-only service stubs;
    # actual GatewayConfig and its unchanged path validators remain in use.
    observed = []
    runtime = SimpleNamespace(health_payload=lambda: {"status": "ALIVE"},
        ready_payload=lambda: (200, {"status": "READY"}), close=Mock())
    def start(config):
        observed.append(config)
        return runtime
    verify = Mock(return_value={"status": "SOURCE_CONSISTENCY_OBSERVED"})
    fake_modules = {
        "total_gateway.tool_source_launch": SimpleNamespace(verify_source_revision=verify),
        "total_gateway.release_manifest": SimpleNamespace(
            write_release_manifest=Mock(return_value=SimpleNamespace(release_manifest_sha256="a" * 64)),
            RELEASE_MANIFEST_FILENAME="release-manifest.json"),
        "total_gateway.runtime": SimpleNamespace(GatewayRuntime=SimpleNamespace(start=start)),
    }
    for name, module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "argv", ["probe.py", "b" * 64, "c" * 64])
    monkeypatch.setenv("TIANGONG_SANDBOX", "1")
    for name in ("APPDATA", "TIANGONG_DOCUMENTS_PATH", "TIANGONG_LIFE_DATA_ROOT", "TIANGONG_LIFE_RUNTIME_ROOT"):
        monkeypatch.setenv(name, "test-only-restored-by-monkeypatch")
    return worker, observed, runtime, verify


def at_length(tmp_path: Path, size: int) -> Path:
    # Build an actual path at the same depth as the retained native failure.
    base = tmp_path.resolve(strict=True)
    assert len(str(base)) + 2 < size
    current = base
    while len(str(current)) < size:
        remaining = size - len(str(current)) - 1
        count = min(60, remaining)
        if remaining - count == 1:
            count -= 1
        current = current / ("x" * count)
        current.mkdir()
    assert len(str(current)) == size
    return current


@pytest.mark.parametrize("depth", [180, 187])
def test_probe_uses_short_authoritative_skill_path_within_existing_limits(worker_fixture, tmp_path, monkeypatch, depth):
    worker, observed, runtime, _ = worker_fixture
    workspace = at_length(tmp_path, depth)
    source = workspace / "r/source"
    (source / "src/omni_body_skill").mkdir(parents=True)
    monkeypatch.setattr(worker, "__file__", str(workspace / "probe.py"))
    assert len(str(source / "app/backend/tiangong-backend/_internal/omni_body_skill")) > 240
    assert worker.main() == 0
    assert len(observed) == 1
    settings = observed[0]
    assert isinstance(settings, GatewayConfig)
    assert settings.skill_root == source / "src/omni_body_skill"
    for field in ("state_root", "workspace_root", "release_source_root", "release_manifest_path", "skill_root"):
        assert len(str(getattr(settings, field))) <= 240
    runtime.close.assert_called_once()


def test_probe_does_not_relax_config_limit_when_own_workspace_is_too_long(worker_fixture, tmp_path, monkeypatch):
    worker, observed, runtime, _ = worker_fixture
    workspace = at_length(tmp_path, 222)
    (workspace / "r/source/src/omni_body_skill").mkdir(parents=True)
    monkeypatch.setattr(worker, "__file__", str(workspace / "probe.py"))
    assert worker.main() == 1
    assert not observed
    runtime.close.assert_not_called()
    report = json.loads((workspace / "launch-observation.json").read_text())
    assert report["failed_phase"] == "gateway_startup"
    assert report["error_type"] == "ValidationError"
    assert report["status"] == "STARTUP_PROBE_FAILED"
    assert report["may_publish"] is report["may_authorize"] is report["may_execute"] is False


def test_release_failure_still_prevents_config_and_runtime_assembly(worker_fixture, tmp_path, monkeypatch):
    worker, observed, runtime, verify = worker_fixture
    monkeypatch.setattr(worker, "__file__", str(tmp_path / "probe.py"))
    # Use a release-generation failure to avoid invoking Windows-only diagnostic
    # methods in this portable service fixture; source is still the first check.
    release = sys.modules["total_gateway.release_manifest"].write_release_manifest
    release.side_effect = RuntimeError("retained release mismatch")
    assert worker.main() == 1
    verify.assert_called_once()
    assert not observed
    runtime.close.assert_not_called()
    report = json.loads((tmp_path / "launch-observation.json").read_text())
    assert report["failed_phase"] == "release_generation"
    assert report["error"] == "retained release mismatch"
