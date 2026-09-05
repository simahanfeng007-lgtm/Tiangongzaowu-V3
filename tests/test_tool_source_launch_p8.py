"""Offline consistency/ordering tests, not Source publication or product PASS."""

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sys
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime
from total_gateway import tool_source_launch as launch
from total_gateway.tool_source_inputs import compile_tool_source_inputs
from total_gateway.tool_source_bundle import stage_tool_source_bundle
from tests.test_tool_source_bundle_p8 import source, bundle  # noqa: F401


@pytest.fixture
def installation(source, tmp_path):
    (source / "src/omni_body_skill/__init__.py").write_bytes(b"raise AssertionError('must not import candidate')\n")
    result = bundle(source, tmp_path / "candidate.zip")
    observed = compile_tool_source_inputs(source)
    inputs = {row.path: row.content_sha256 for row in observed.files}
    inputs[launch._MANIFEST] = result["capability_manifest_sha256"]
    policy = json.loads((source / "source-ownership.json").read_bytes())
    return source, result, policy, inputs


def config(root, tmp_path):
    return GatewayConfig(environment="test", port=0, deployment_mode="embedded",
                         state_root=tmp_path / "state", release_source_root=root,
                         skill_root=root / "src/omni_body_skill")


@pytest.mark.parametrize("actual", [r"\Device\HarddiskVolume4\source\module.py",
                                    r"\Device\HarddiskVolume3\other\module.py",
                                    r"\Device\HarddiskVolume3\source\redirected.py"])
def test_physical_path_must_match_both_volume_and_relative_location(actual):
    with pytest.raises(launch.SourceLaunchError, match="physical_path_mismatch"):
        launch._verify_native_relative(PureWindowsPath(r"\Device\HarddiskVolume3\source"),
                                       PureWindowsPath(actual), Path("module.py"))


def test_same_physical_volume_and_relative_path_is_accepted():
    launch._verify_native_relative(PureWindowsPath(r"\Device\HarddiskVolume3\source"),
                                   PureWindowsPath(r"\Device\HarddiskVolume3\source\pkg\module.py"),
                                   Path("pkg/module.py"))


def test_dotdot_path_cannot_be_normalized_into_acceptance(tmp_path):
    with pytest.raises(launch.SourceLaunchError, match="path_not_canonical"):
        launch._safe_path(tmp_path, tmp_path / "unused/../module.py")


def test_native_identity_query_failure_has_no_lexical_or_nonstrict_fallback(tmp_path, monkeypatch):
    path = tmp_path / "module.py"
    path.write_bytes(b"read only probe")
    monkeypatch.setattr(launch, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(launch, "_windows_final_path", Mock(side_effect=PermissionError("native identity unavailable")))
    with pytest.raises(PermissionError, match="native identity unavailable"):
        launch._safe_path(tmp_path, path)


def test_generated_mirrors_are_bound_to_measured_bytes_without_candidate_import(installation):
    root, _, policy, inputs = installation
    verified = launch._mirror_files(root, policy, inputs)
    assert verified["mirror/omni_body_skill/tools/handler.py"] == inputs["src/omni_body_skill/tools/handler.py"]
    modules_before = dict(sys.modules)
    launch._verify_import_origins(root, policy, verified, {}, [])
    assert dict(sys.modules) == modules_before


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra", "bytecode"])
def test_mirror_changes_or_cache_files_fail_even_if_marker_is_unchanged(installation, mutation):
    root, _, policy, inputs = installation
    path = root / "mirror/omni_body_skill/tools/handler.py"
    if mutation == "changed":
        path.write_bytes(b"changed")
    elif mutation == "missing":
        path.unlink()
    else:
        (path.parent / ("handler.pyc" if mutation == "bytecode" else "new_helper.py")).write_bytes(b"unexpected")
    with pytest.raises(launch.SourceLaunchError, match="generated_source"):
        launch._mirror_files(root, policy, inputs)


@pytest.mark.parametrize("mutation", ["source", "manifest"])
def test_changed_authority_or_capability_is_rejected_before_import_checks(installation, mutation, monkeypatch):
    root, result, _, _ = installation
    path = root / (launch._MANIFEST if mutation == "manifest" else "src/omni_body_skill/tools/handler.py")
    path.write_bytes(path.read_bytes() + b" ")
    guard = Mock()
    monkeypatch.setattr(launch, "_verify_import_origins", guard)
    with pytest.raises(launch.SourceLaunchError, match="drift"):
        launch.verify_source_revision(root, source_inputs_sha256=result["source_inputs_sha256"],
                                      capability_sha256=result["capability_manifest_sha256"])
    guard.assert_not_called()


@pytest.mark.parametrize("kind", ["file", "namespace", "spec"])
def test_cached_module_from_another_installation_is_rejected(installation, tmp_path, kind):
    root, _, policy, inputs = installation
    verified = launch._mirror_files(root, policy, inputs)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "__init__.py").write_bytes(b"")
    module = SimpleNamespace(__file__=str(root / "src/omni_body_skill/__init__.py"))
    if kind == "file":
        module.__file__ = str(foreign / "__init__.py")
    elif kind == "namespace":
        module.__path__ = [str(foreign)]
    else:
        module.__spec__ = SimpleNamespace(origin=str(foreign / "__init__.py"), submodule_search_locations=None)
    with pytest.raises(launch.SourceLaunchError, match="outside_installation"):
        launch._verify_import_origins(root, policy, verified, {"omni_body_skill": module}, [])


def test_owned_authority_and_generated_imports_are_accepted_as_locations_only(installation):
    root, _, policy, inputs = installation
    verified = launch._mirror_files(root, policy, inputs)
    for prefix in ("src", "mirror"):
        module = SimpleNamespace(__file__=str(root / prefix / "omni_body_skill/tools/handler.py"))
        launch._verify_import_origins(root, policy, verified, {"omni_body_skill.tools.handler": module}, [])


def test_namespace_search_cannot_aggregate_another_installation(installation, tmp_path):
    root, _, policy, inputs = installation
    (root / "src/omni_body_skill/__init__.py").unlink()
    foreign = tmp_path / "foreign"
    (foreign / "omni_body_skill").mkdir(parents=True)
    with pytest.raises(launch.SourceLaunchError, match="outside_installation"):
        launch._verify_import_origins(root, policy, inputs, {}, [str(foreign)])


def test_skill_root_cannot_be_an_unowned_same_manifest_copy(installation, tmp_path):
    root, result, _, _ = installation
    with pytest.raises(launch.SourceLaunchError, match="skill_root_not_owned"):
        launch.verify_source_revision(root, source_inputs_sha256=result["source_inputs_sha256"],
                                      capability_sha256=result["capability_manifest_sha256"], skill_root=root / "unowned")


def test_source_byte_match_does_not_accept_a_writable_installation(installation):
    root, result, _, _ = installation
    with pytest.raises(launch.SourceLaunchError, match="writable_or_hardlinked"):
        launch.verify_source_revision(root, source_inputs_sha256=result["source_inputs_sha256"],
                                      capability_sha256=result["capability_manifest_sha256"])


def test_even_measured_authority_bytecode_is_not_accepted_as_source(installation):
    root, result, _, _ = installation
    (root / "src/cache.pyc").write_bytes(b"opaque cached code")
    inputs = compile_tool_source_inputs(root)
    with pytest.raises(launch.SourceLaunchError, match="bytecode_cache_present"):
        launch.verify_source_revision(root, source_inputs_sha256=inputs.source_inputs_sha256,
                                      capability_sha256=result["capability_manifest_sha256"])


def test_frozen_compatibility_bytecode_is_retained_as_data_not_imported(source, tmp_path):
    (source / "src/omni_body_skill/__init__.py").write_bytes(b"raise AssertionError('must not import candidate')\n")
    policy_path = source / "source-ownership.json"
    policy = json.loads(policy_path.read_bytes())
    policy["authority_policy"]["frozen_roots"] = ["frozen"]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    frozen = source / "frozen/old_runtime.pyc"
    frozen.parent.mkdir()
    frozen.write_bytes(b"retained frozen bytes; not a loadable Python cache")
    package = bundle(source, tmp_path / "frozen.zip")
    staged = stage_tool_source_bundle(Path(package["path"]), expected_sha256=package["sha256"],
                                     staging_root=tmp_path / "staged-frozen")
    root = Path(staged["source_root"])
    code = (
        "import json,sys; from pathlib import Path; "
        "import ctypes.wintypes, encodings.utf_16_be; "
        "from total_gateway.tool_source_launch import verify_source_revision; "
        "before = dict(sys.modules); "
        "observed = verify_source_revision(Path(sys.argv[1]), "
        "source_inputs_sha256=sys.argv[2], capability_sha256=sys.argv[3]); "
        "assert dict(sys.modules) == before, sorted(set(sys.modules) - set(before)); print(json.dumps(observed))"
    )
    completed = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(root),
                                package["source_inputs_sha256"], package["capability_manifest_sha256"]],
                               capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["retained_frozen_bytecode_count"] == 1
    assert observed["may_publish"] is observed["may_authorize"] is observed["may_execute"] is False
    assert (root / "frozen/old_runtime.pyc").read_bytes() == frozen.read_bytes()


@pytest.mark.parametrize("name", ["frozen/__pycache__/old.pyc", "frozen/old.pyo", "frozen-elsewhere/old.pyc"])
def test_frozen_root_does_not_whitelist_caches_optimized_or_sibling_bytecode(name):
    with pytest.raises(launch.SourceLaunchError, match="bytecode_cache_present"):
        launch._verify_bytecode_inventory({"authority_policy": {"frozen_roots": ["frozen"]}}, {name: "a" * 64})


@pytest.mark.parametrize("alias", ["omni_body_skill.cached", "unknown_private_loader_alias"])
@pytest.mark.parametrize("origin_field", ["file", "spec"])
def test_retaining_bytecode_never_accepts_it_as_an_import_origin(installation, alias, origin_field):
    root, _, policy, inputs = installation
    path = root / "src/omni_body_skill/cached.pyc"
    path.write_bytes(b"not executable evidence")
    module = SimpleNamespace(__file__=str(path)) if origin_field == "file" else SimpleNamespace(
        __spec__=SimpleNamespace(origin=str(path), submodule_search_locations=None))
    with pytest.raises(launch.SourceLaunchError, match="bytecode_import_origin"):
        launch._verify_import_origins(root, policy, inputs, {alias: module}, [])


def test_fresh_probe_verifies_readonly_fixture_without_importing_its_raising_package(installation, tmp_path):
    _, result, _, _ = installation
    staged = stage_tool_source_bundle(Path(result["path"]), expected_sha256=result["sha256"],
                                     staging_root=tmp_path / "staged")
    code = (
        "import json,sys; from pathlib import Path; "
        "from total_gateway.tool_source_launch import verify_source_revision; "
        "print(json.dumps(verify_source_revision(Path(sys.argv[1]), "
        "source_inputs_sha256=sys.argv[2], capability_sha256=sys.argv[3])))"
    )
    probe = subprocess.run([sys.executable, "-B", "-c", code, staged["source_root"],
                            result["source_inputs_sha256"], result["capability_manifest_sha256"]],
                           capture_output=True, text=True, timeout=30, check=False)
    assert probe.returncode == 0, probe.stderr
    observed = json.loads(probe.stdout)
    assert observed["status"] == "SOURCE_CONSISTENCY_OBSERVED"
    assert observed["may_publish"] is observed["may_authorize"] is observed["may_execute"] is False
    assert not list(Path(staged["source_root"]).rglob("__pycache__"))


@pytest.mark.parametrize("missing", ["bytecode_control", "release"])
def test_preflight_failure_occurs_before_lease_or_state_creation(installation, tmp_path, monkeypatch, missing):
    root, _, _, _ = installation
    settings = config(root, tmp_path)
    monkeypatch.setattr(sys, "dont_write_bytecode", missing != "bytecode_control")
    acquire = Mock(side_effect=AssertionError("must not acquire a lease"))
    monkeypatch.setattr("total_gateway.runtime.InstanceEpochLease.acquire", acquire)
    expected = "bytecode_writes" if missing == "bytecode_control" else "explicit_release"
    with pytest.raises(launch.SourceLaunchError, match=expected):
        GatewayRuntime.start(settings)
    acquire.assert_not_called()
    assert not settings.state_root.exists()


def test_source_pin_without_explicit_root_is_not_legacy(installation, tmp_path):
    root, _, _, _ = installation
    settings = config(root, tmp_path).model_copy(update={"release_source_root": None})
    with pytest.raises(launch.SourceLaunchError, match="explicit_source_root"):
        launch.preflight_source_revision(settings)


def test_source_pin_cannot_claim_to_bind_a_remote_standalone_service(installation, tmp_path):
    root, _, _, _ = installation
    settings = config(root, tmp_path).model_copy(update={"deployment_mode": "standalone_services"})
    with pytest.raises(launch.SourceLaunchError, match="embedded_mode"):
        launch.preflight_source_revision(settings)


def test_legacy_manifest_remains_unbound_not_source_verified(tmp_path):
    root = tmp_path / "legacy"
    path = root / launch._MANIFEST
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"schema":"legacy-fixture"}')
    assert launch.preflight_source_revision(config(root, tmp_path)) is None


def test_explicit_release_pin_mismatch_is_rejected_before_source_observation(installation, tmp_path, monkeypatch):
    root, _, _, _ = installation
    from total_gateway import release_manifest
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(release_manifest, "select_latest_release_manifest_with_path",
                        lambda *a, **kw: (tmp_path / "release.json", SimpleNamespace(capability_manifest_sha256="0" * 64)))
    settings = config(root, tmp_path).model_copy(update={"release_manifest_path": tmp_path / "release.json"})
    guard = Mock()
    monkeypatch.setattr(launch, "verify_source_revision", guard)
    with pytest.raises(launch.SourceLaunchError, match="release_capability_mismatch"):
        launch.preflight_source_revision(settings)
    guard.assert_not_called()


def test_assembled_release_change_stops_before_worker_start_and_closes_resources(tmp_path, monkeypatch):
    settings = GatewayConfig(environment="test", port=0, state_root=tmp_path / "state",
                             workspace_root=tmp_path, release_manifest_path=tmp_path / "release.json",
                             backend_internal_token="b" * 32, communication_api_token="c" * 32,
                             life_internal_token="l" * 32)
    monkeypatch.setattr("total_gateway.runtime.preflight_source_revision",
                        lambda config: {"release_manifest_sha256": "X"})
    worker = SimpleNamespace(release_manifest=SimpleNamespace(release_manifest_sha256="Y"), start=Mock(), close=Mock())
    monkeypatch.setattr("total_gateway.runtime.GatewayOrchestrationWorker.from_runtime_config", lambda **kw: worker)
    with pytest.raises(RuntimeError, match="assembled_release_changed"):
        GatewayRuntime.start(settings)
    worker.start.assert_not_called()
    worker.close.assert_called_once()
    from total_gateway.bootstrap import InstanceEpochLease
    lease = InstanceEpochLease.acquire(settings.state_root, "after-rejected-start", now_ms=999999)
    lease.release()
