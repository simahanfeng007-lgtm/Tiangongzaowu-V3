"""Parent-side build guards; simulated children are NOT isolation evidence."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_body_skill.tool_contracts import build_action_schema_catalog
from v3.fact_kernel import compile_manifest


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[1] / "scripts/build-tool-source.py"
    spec = importlib.util.spec_from_file_location("p8_build_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_without_os_containment_stops_before_inspection_or_export(builder, monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "os", SimpleNamespace(name="posix"))
    inspect = Mock(side_effect=AssertionError("must not start build preparation"))
    monkeypatch.setattr(builder, "inspect_tool_source_candidate", inspect)
    with pytest.raises(RuntimeError, match="os_containment_unavailable"):
        builder.build_candidate(tmp_path, base="a" * 40, head="b" * 40, action_ids=("skill.list",))
    inspect.assert_not_called()


@dataclass(frozen=True)
class SimulatedCandidate:
    requested_action_ids: tuple[str, ...] = ("skill.list",)
    candidate_sha256: str = "c" * 64


@pytest.fixture
def simulated_build(builder, monkeypatch, tmp_path):
    snapshot = tmp_path / "immutable-source"
    for relative in builder.AUTHORITY_FILES.values():
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SIMULATED = True\n", encoding="utf-8")
    policy = {"schema": "tiangong.source-ownership.v2", "authority_policy": {
        "editable_roots": ["src", "app/backend/tiangong-backend/v3"], "frozen_roots": [],
    }, "mappings": [
        {"id": "body", "source": "src/omni_body_skill", "source_role": "authoritative", "targets": []},
        {"id": "fact", "source": "app/backend/tiangong-backend/v3/fact_kernel", "source_role": "authoritative", "targets": []},
    ]}
    (snapshot / "source-ownership.json").write_text(json.dumps(policy), encoding="utf-8")
    metadata = {"skill.list": {"risk": "A0", "effect": "read", "implemented": True}}
    source_inputs = builder.compile_tool_source_inputs(snapshot)
    manifest = compile_manifest(metadata, object, dynamic_actions=("skill.list",),
                                action_schema_catalog=build_action_schema_catalog(metadata)).to_gateway_dict(
        source_inputs_sha256=source_inputs.source_inputs_sha256,
    )
    monkeypatch.setattr(builder, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(builder, "inspect_tool_source_candidate", Mock(return_value=SimulatedCandidate()))
    monkeypatch.setattr(builder, "read_tool_source_manifests", Mock(return_value=(manifest, manifest)))

    @contextmanager
    def materialize(*_):
        yield snapshot

    monkeypatch.setattr(builder, "materialize_tool_source_candidate", materialize)
    mode = {"value": "valid"}

    class SimulatedChild:
        def __init__(self, *args):
            pass

        def run(self, command, **kwargs):
            assert kwargs == {"require_os_containment": True}
            assert "-I" in command and "-B" in command
            assert Path(command[-1]).parent == snapshot
            artifact = {
                "schema": "tiangong.tool-source-build-artifact.v1",
                "compiler": "v3.fact_kernel.compile_manifest",
                "authority_bindings": {
                    name: {"path": relative, "sha256": hashlib.sha256((snapshot / relative).read_bytes()).hexdigest()}
                    for name, relative in builder.AUTHORITY_FILES.items()
                },
                "source_inputs": builder.asdict(source_inputs),
                "gateway_manifest": dict(manifest),
            }
            if mode["value"] == "bad_compiler":
                artifact["compiler"] = "unrelated.compiler"
            if mode["value"] == "bad_bindings":
                artifact["authority_bindings"]["compiler"]["sha256"] = "0" * 64
            if mode["value"] == "bad_source_inputs":
                artifact["source_inputs"]["may_execute"] = 0
            if mode["value"] == "bad_revision":
                artifact["gateway_manifest"]["source_inputs_sha256"] = "0" * 64
            if mode["value"] == "missing_revision":
                artifact["gateway_manifest"].pop("source_inputs_sha256")
            (snapshot / builder.ARTIFACT_NAME).write_text(json.dumps(artifact), encoding="utf-8")
            return {
                "ok": mode["value"] != "failed",
                "returncode": 1 if mode["value"] == "failed" else 0,
                "containment": "compat-workspace-job-sandbox" if mode["value"] == "compat" else "windows-appcontainer",
                "network": "denied",
                "changed_files": [builder.ARTIFACT_NAME] + (["src/tampered.py"] if mode["value"] == "writes" else []),
                "deleted_files": ["src/original.py"] if mode["value"] == "deletes" else [],
            }

    monkeypatch.setattr(builder, "SandboxRunner", SimulatedChild)
    return mode


def test_observed_build_is_not_review_approval_publication_or_execution(builder, simulated_build, tmp_path):
    result = builder.build_candidate(tmp_path, base="a" * 40, head="b" * 40, action_ids=("skill.list",))
    assert result["status"] == "ISOLATED_BUILD_OBSERVED"
    assert result["trusted_static_checks"]["python_ast_files"] == 3
    assert result["committed_manifest_matches_build"] is True
    assert result["tool_world_ingested"] is False
    world = result["source_bound_tool_world"]
    assert world["may_authorize"] is world["may_execute"] is False
    assert [row["action_id"] for row in world["primitives"]] == ["skill.list"]
    assert world["primitives"][0]["implementation_refs"] == [{
        "path": builder.AUTHORITY_FILES["actions"], "start_line": None, "end_line": None,
    }]
    for name in ("may_publish", "may_authorize", "may_execute", "review_approval_verified",
                 "evidence_contract_tests_verified", "running_manifest_lock_verified"):
        assert result[name] is False


@pytest.mark.parametrize("mode", ["bad_compiler", "bad_bindings", "bad_source_inputs", "bad_revision",
                                 "missing_revision", "writes", "deletes", "compat"])
def test_parent_rejects_foreign_compilers_input_changes_and_compatibility_evidence(builder, simulated_build, tmp_path, mode):
    simulated_build["value"] = mode
    with pytest.raises((ValueError, RuntimeError), match="bindings|revision|immutable inputs|containment evidence"):
        builder.build_candidate(tmp_path, base="a" * 40, head="b" * 40, action_ids=("skill.list",))


def test_failed_child_process_preserves_failure_and_does_not_produce_pass(builder, simulated_build, tmp_path):
    simulated_build["value"] = "failed"
    result = builder.build_candidate(tmp_path, base="a" * 40, head="b" * 40, action_ids=("skill.list",))
    assert result["status"] == "BUILD_FAILED"
    assert result["build_process"]["returncode"] == 1
    assert result["may_publish"] is False
    assert "manifest_review" not in result


def test_successful_build_can_keep_a_verified_unapproved_source_revision_bundle(builder, simulated_build, tmp_path):
    from total_gateway.tool_source_bundle import verify_tool_source_bundle

    manifest_path = tmp_path / "immutable-source/src/omni_body_skill/registry/capability_manifest.generated.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(b"{}\n")
    output = tmp_path / "source-revision.zip"
    result = builder.build_candidate(tmp_path, base="a" * 40, head="b" * 40,
                                     action_ids=("skill.list",), bundle_path=output)
    artifact = result["source_bundle"]
    assert result["status"] == "ISOLATED_BUILD_OBSERVED"
    assert artifact["path"] == str(output)
    assert artifact["may_publish"] is result["may_publish"] is False
    index = verify_tool_source_bundle(output, expected_sha256=artifact["sha256"])
    assert index["source_inputs_sha256"] == result["build_artifact"]["source_inputs"]["source_inputs_sha256"]


def test_failed_build_does_not_export_a_source_bundle(builder, simulated_build, tmp_path):
    simulated_build["value"] = "failed"
    output = tmp_path / "must-not-exist.zip"
    result = builder.build_candidate(tmp_path, base="a" * 40, head="b" * 40,
                                     action_ids=("skill.list",), bundle_path=output)
    assert result["status"] == "BUILD_FAILED"
    assert not output.exists()
    assert "source_bundle" not in result
