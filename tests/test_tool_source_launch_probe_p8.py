"""Trusted parent protocol fixtures; fake children are not OS or runtime evidence."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_tool_source_bundle_p8 import source, bundle  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def probe_module():
    spec = importlib.util.spec_from_file_location("p8_probe_parent", ROOT / "scripts/probe-tool-source-launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parent_bootstraps_installed_backend_without_inherited_pythonpath():
    completed = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ROOT / "scripts/probe-tool-source-launch.py"), "--help"],
                               capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "--sha256" in completed.stdout


def test_probe_without_os_backend_rejects_before_staging(probe_module, monkeypatch, tmp_path):
    monkeypatch.setattr(probe_module, "os", SimpleNamespace(name="posix"))
    stage = Mock(side_effect=AssertionError("must not stage or execute"))
    monkeypatch.setattr(probe_module, "stage_tool_source_bundle", stage)
    with pytest.raises(RuntimeError, match="os_containment_unavailable"):
        probe_module.probe(tmp_path / "absent.zip", expected_sha256="0" * 64)
    stage.assert_not_called()


@pytest.fixture
def simulated_probe(source, tmp_path, probe_module, monkeypatch):
    artifact = tmp_path / "fixture.zip"
    package = bundle(source, artifact)
    mode = {"value": "valid"}
    monkeypatch.setattr(probe_module, "os", SimpleNamespace(name="nt"))

    class FakeSandbox:
        def __init__(self, workspace, *args):
            self.workspace = workspace

        def run(self, command, **kwargs):
            assert kwargs == {"require_os_containment": True}
            assert "-I" in command and "-B" in command
            record = {"schema": "tiangong.tool-source-launch-observation.v1",
                      "status": "ISOLATED_STARTUP_OBSERVED", "ready_http_status": 200,
                      "gateway_readiness": {"status": "READY"}, "gateway_health": {"status": "ALIVE"},
                      "release_manifest_sha256": "a" * 64,
                      "may_publish": False, "may_authorize": False, "may_execute": False}
            for field in ("source_consistency", "post_shutdown_source_consistency"):
                record[field] = {"status": "SOURCE_CONSISTENCY_OBSERVED", "observed_file_count": 3,
                                 "source_inputs_sha256": package["source_inputs_sha256"],
                                 "capability_manifest_sha256": package["capability_manifest_sha256"],
                                 "may_publish": False, "may_authorize": False, "may_execute": False}
            if mode["value"].startswith("contradictory_"):
                # A success label must never erase retained startup/cleanup
                # failure evidence, even when the failure field is empty.
                record[mode["value"].removeprefix("contradictory_")] = ""
            elif mode["value"] == "failed_child":
                record.update(status="STARTUP_PROBE_FAILED", failed_phase="gateway_startup", error="fixture_failure")
            elif mode["value"] == "approval_claim":
                record["may_publish"] = True
            elif mode["value"] == "wrong_type":
                record = []
            elif mode["value"] == "post_source_mismatch":
                record["post_shutdown_source_consistency"]["source_inputs_sha256"] = "0" * 64
            elif mode["value"] == "not_ready":
                record["gateway_readiness"]["status"] = "NOT_READY"
            elif mode["value"] == "missing_proof":
                record.pop("source_consistency")
            if mode["value"] != "missing_report":
                (self.workspace / "launch-observation.json").write_text(json.dumps(record), encoding="utf-8")
            if mode["value"] == "source_drift":
                path = self.workspace / "r/source/src/omni_body_skill/tools/handler.py"
                path.chmod(0o644)
                path.write_bytes(b"changed source")
                path.chmod(0o444)
            return {"ok": mode["value"] != "failed_child", "containment": "windows-appcontainer",
                    "network": "denied", "stdout": "controlled fixture", "stderr": ""}

    monkeypatch.setattr(probe_module, "SandboxRunner", FakeSandbox)
    return probe_module, artifact, package, mode


def test_simulated_probe_retains_identity_and_no_approval_flags(simulated_probe):
    module, artifact, package, _ = simulated_probe
    report = module.probe(artifact, expected_sha256=package["sha256"])
    assert report["status"] == "ISOLATED_STARTUP_OBSERVED"
    assert report["staged_source"]["source_inputs_sha256"] == package["source_inputs_sha256"]
    assert report["post_probe_source"] == report["staged_source"]
    assert report["may_publish"] is report["may_authorize"] is report["may_execute"] is False


@pytest.mark.parametrize("failure", ["failed_child", "approval_claim", "wrong_type", "missing_report", "source_drift",
                                     "post_source_mismatch", "not_ready", "missing_proof",
                                     "contradictory_cleanup_error", "contradictory_error",
                                     "contradictory_error_type", "contradictory_failed_phase",
                                     "contradictory_traceback"])
def test_failed_or_inconsistent_child_cannot_be_reported_as_success(simulated_probe, failure):
    module, artifact, package, mode = simulated_probe
    mode["value"] = failure
    report = module.probe(artifact, expected_sha256=package["sha256"])
    assert report["status"] == "STARTUP_PROBE_FAILED"
    assert report["probe_process"]["stdout"] == "controlled fixture"
    if failure == "source_drift":
        assert report["observation"]["status"] == "ISOLATED_STARTUP_OBSERVED"
        assert "staged source" in report["error"]


def test_cli_never_overwrites_an_existing_report(probe_module, tmp_path):
    report = tmp_path / "keep.json"
    report.write_bytes(b"keep original observation")
    with pytest.raises(SystemExit):
        probe_module.main(["--bundle", str(tmp_path / "none.zip"), "--sha256", "0" * 64, "--report", str(report)])
    assert report.read_bytes() == b"keep original observation"


def test_owned_temporary_directory_is_physically_resolved_before_staging(simulated_probe, monkeypatch):
    module, artifact, package, _ = simulated_probe
    original = module.tempfile.TemporaryDirectory

    class AliasedTemporaryDirectory:
        def __init__(self, **kwargs):
            self.owned = original(**kwargs)

        def __enter__(self):
            root = Path(self.owned.__enter__())
            (root / "alias-parent").mkdir()
            return str(root / "alias-parent" / "..")

        def __exit__(self, *args):
            return self.owned.__exit__(*args)

    monkeypatch.setattr(module, "tempfile", SimpleNamespace(TemporaryDirectory=AliasedTemporaryDirectory))
    report = module.probe(artifact, expected_sha256=package["sha256"])
    assert report["status"] == "ISOLATED_STARTUP_OBSERVED", report
    assert ".." not in Path(report["staged_source"]["staging_root"]).parts
    assert report["post_probe_source"] == report["staged_source"]
