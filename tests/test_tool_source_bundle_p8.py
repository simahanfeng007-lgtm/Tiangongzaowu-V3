"""Actual package bytes and official mirror generation; no product approval."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from contracts import canonical_json_bytes, canonical_sha256
from omni_body_skill.tool_contracts import build_action_schema_catalog
from total_gateway.tool_source_bundle import write_tool_source_bundle, verify_tool_source_bundle
from total_gateway.tool_source_candidate import SourceCandidateError
from total_gateway.tool_source_inputs import compile_tool_source_inputs
from v3.fact_kernel import compile_manifest

from tests.test_sync_generated_sources import _load_module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "private-source"
    tools = root / "src/omni_body_skill/tools"
    tools.mkdir(parents=True)
    (tools / "handler.py").write_bytes(b"raise AssertionError('source must not be imported')\n")
    registry = root / "src/omni_body_skill/registry"
    registry.mkdir()
    (registry / "capability_manifest.generated.json").write_bytes(b"{}\n")
    policy = {
        "schema": "tiangong.source-ownership.v2",
        "authority_policy": {"editable_roots": ["src"], "frozen_roots": []},
        "mappings": [{"id": "body", "source": "src/omni_body_skill", "source_role": "authoritative",
                      "targets": ["mirror/omni_body_skill"]}],
    }
    (root / "source-ownership.json").write_text(json.dumps(policy), encoding="utf-8")
    return root


def prepare(source):
    inputs = compile_tool_source_inputs(source)
    metadata = {"skill.list": {"risk": "A0", "effect": "read", "implemented": True}}
    manifest = compile_manifest(metadata, object, dynamic_actions=("skill.list",),
                                action_schema_catalog=build_action_schema_catalog(metadata)).to_gateway_dict(
        source_inputs_sha256=inputs.source_inputs_sha256,
    )
    report = {
        "status": "ISOLATED_BUILD_OBSERVED", "may_publish": False, "may_authorize": False, "may_execute": False,
        "build_artifact": {"source_inputs": asdict(inputs), "gateway_manifest": manifest},
    }
    return inputs, report


def bundle(source, path, *, synchronize=None):
    inputs, report = prepare(source)
    official = _load_module()

    def sync(snapshot):
        assert official.process(write=True, workspace_root=snapshot) == []
        assert official.process(write=False, workspace_root=snapshot) == []

    return write_tool_source_bundle(
        source, source_inputs=inputs, report=report, output_path=path,
        synchronize_mirrors=sync if synchronize is None else synchronize,
        mirror_generator_sha256=hashlib.sha256(Path(official.__file__).read_bytes()).hexdigest(),
    )


def test_package_keeps_exact_manifest_sources_and_official_mirrors(source, tmp_path):
    inputs, report = prepare(source)
    path = tmp_path / "revision-x.zip"
    result = bundle(source, path)
    index = verify_tool_source_bundle(path, expected_sha256=result["sha256"])
    assert result["source_inputs_sha256"] == inputs.source_inputs_sha256
    assert result["may_publish"] is result["may_execute"] is result["may_authorize"] is False
    with zipfile.ZipFile(path) as archive:
        manifest = canonical_json_bytes(report["build_artifact"]["gateway_manifest"]) + b"\n"
        assert archive.read("source/src/omni_body_skill/registry/capability_manifest.generated.json") == manifest
        assert archive.read("source/mirror/omni_body_skill/registry/capability_manifest.generated.json") == manifest
        assert archive.read("source/src/omni_body_skill/tools/handler.py") == (
            source / "src/omni_body_skill/tools/handler.py").read_bytes()
        marker = json.loads(archive.read("source/mirror/omni_body_skill/.tiangong-generated-source.json"))
        assert marker["mapping_id"] == "body"
        assert archive.read("build-report.json") == canonical_json_bytes(report) + b"\n"
    assert compile_tool_source_inputs(source) == inputs
    assert index["file_count"] == result["file_count"]


def test_next_version_does_not_overwrite_or_mutate_the_old_package(source, tmp_path):
    first_path = tmp_path / "revision-x.zip"
    first = bundle(source, first_path)
    original = first_path.read_bytes()
    second_source = tmp_path / "next-private-source"
    shutil.copytree(source, second_source)
    (second_source / "src/omni_body_skill/tools/handler.py").write_bytes(b"NEXT_VERSION = True\n")
    second = bundle(second_source, tmp_path / "revision-y.zip")
    assert first["sha256"] != second["sha256"]
    assert first["source_inputs_sha256"] != second["source_inputs_sha256"]
    assert first["capability_manifest_sha256"] != second["capability_manifest_sha256"]
    assert first_path.read_bytes() == original
    verify_tool_source_bundle(first_path, expected_sha256=first["sha256"])
    with pytest.raises(SourceCandidateError, match="new absolute file"):
        bundle(second_source, first_path)
    assert first_path.read_bytes() == original


def test_bundle_verification_rejects_changed_bytes(source, tmp_path):
    path = tmp_path / "revision.zip"
    result = bundle(source, path)
    path.write_bytes(path.read_bytes() + b"unreviewed bytes")
    with pytest.raises(SourceCandidateError, match="digest"):
        verify_tool_source_bundle(path, expected_sha256=result["sha256"])


def test_official_sync_api_does_not_retarget_its_own_module_globals(source):
    official = _load_module()
    original = official.ROOT, official.CONFIG
    assert official.process(write=True, workspace_root=source) == []
    assert official.process(write=False, workspace_root=source) == []
    assert (official.ROOT, official.CONFIG) == original


def test_mirror_generation_cannot_change_measured_source_inputs(source, tmp_path):
    def wrong_generator(snapshot):
        (snapshot / "src/omni_body_skill/tools/handler.py").write_bytes(b"UNREVIEWED = True\n")
    with pytest.raises(SourceCandidateError, match="changed compiler inputs"):
        bundle(source, tmp_path / "bad.zip", synchronize=wrong_generator)
    assert not (tmp_path / "bad.zip").exists()


def test_input_changes_after_compilation_are_rejected(source, tmp_path):
    inputs, report = prepare(source)
    (source / "src/omni_body_skill/tools/handler.py").write_bytes(b"CHANGED = True\n")
    with pytest.raises(SourceCandidateError, match="differ from the isolated build"):
        write_tool_source_bundle(source, source_inputs=inputs, report=report, output_path=tmp_path / "bad.zip",
                                 synchronize_mirrors=lambda _: None, mirror_generator_sha256="a" * 64)


@pytest.mark.parametrize("key", ["may_publish", "may_authorize", "may_execute"])
def test_caller_cannot_turn_packaging_into_approval(source, tmp_path, key):
    inputs, report = prepare(source)
    report[key] = True
    with pytest.raises(SourceCandidateError, match="unapproved"):
        write_tool_source_bundle(source, source_inputs=inputs, report=report, output_path=tmp_path / "bad.zip",
                                 synchronize_mirrors=lambda _: None, mirror_generator_sha256="a" * 64)


def rewrite_package(source_path, output_path, change):
    with zipfile.ZipFile(source_path) as original:
        rows = {info.filename: (info, original.read(info)) for info in original.infolist()}
    change(rows)
    with zipfile.ZipFile(output_path, "w") as changed:
        for info, raw in rows.values():
            changed.writestr(info, raw)
    return hashlib.sha256(output_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("relative", ["src/omni_body_skill/tools/handler.py",
                                      "src/omni_body_skill/registry/capability_manifest.generated.json"])
def test_rehashing_the_archive_and_index_cannot_detach_source_from_the_compiler_report(source, tmp_path, relative):
    original = tmp_path / "original.zip"
    bundle(source, original)

    def tamper(rows):
        name = "source/" + relative
        info, raw = rows[name]
        changed = raw + b"\nUNREVIEWED\n"
        rows[name] = info, changed
        index_info, index_raw = rows["bundle-manifest.json"]
        index = json.loads(index_raw)
        for entry in index["entries"]:
            if entry["path"] == name:
                entry["sha256"] = hashlib.sha256(changed).hexdigest()
                entry["size_bytes"] = len(changed)
        if relative.endswith(".generated.json"):
            index["capability_manifest_sha256"] = hashlib.sha256(changed).hexdigest()
        index["size_bytes"] += len(changed) - len(raw)
        index.pop("bundle_manifest_sha256")
        index["bundle_manifest_sha256"] = canonical_sha256(index)
        rows["bundle-manifest.json"] = index_info, canonical_json_bytes(index) + b"\n"

    path = tmp_path / "rehashed.zip"
    digest = rewrite_package(original, path, tamper)
    with pytest.raises(SourceCandidateError, match="structure or content"):
        verify_tool_source_bundle(path, expected_sha256=digest)


@pytest.mark.parametrize("name", ["source/../../escape.py", "source/C:/escape.py",
                                   "source/.git/hooks/execute", "source/aux.txt"])
def test_nonportable_or_escaping_archive_paths_are_rejected_without_extraction(source, tmp_path, name):
    original = tmp_path / "original.zip"
    bundle(source, original)

    def tamper(rows):
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        rows[name] = info, b"not-executable"

    path = tmp_path / "unsafe.zip"
    digest = rewrite_package(original, path, tamper)
    with pytest.raises(SourceCandidateError, match="structure or content"):
        verify_tool_source_bundle(path, expected_sha256=digest)
    assert not (tmp_path / "escape.py").exists()


def test_link_entries_are_rejected(source, tmp_path):
    original = tmp_path / "original.zip"
    bundle(source, original)

    def tamper(rows):
        name = "source/src/omni_body_skill/tools/handler.py"
        info, _ = rows[name]
        info.external_attr = 0o120777 << 16
        rows[name] = info, b"../../outside.py"

    path = tmp_path / "link.zip"
    digest = rewrite_package(original, path, tamper)
    with pytest.raises(SourceCandidateError, match="structure or content"):
        verify_tool_source_bundle(path, expected_sha256=digest)


def test_packaging_is_deterministic_for_the_same_inputs_and_build_report(source, tmp_path):
    first = bundle(source, tmp_path / "first.zip")
    second = bundle(source, tmp_path / "second.zip")
    assert first["sha256"] == second["sha256"]
    assert (tmp_path / "first.zip").read_bytes() == (tmp_path / "second.zip").read_bytes()
