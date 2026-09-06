"""Separate on-disk source versions; not publication or live-Run acceptance."""

import importlib.util
from pathlib import Path
import shutil

import pytest

from total_gateway.tool_source_bundle import stage_tool_source_bundle, verify_staged_tool_source_bundle
from total_gateway.tool_source_candidate import SourceCandidateError

from tests.test_tool_source_bundle_p8 import source, bundle  # noqa: F401


@pytest.fixture
def staged(source, tmp_path):
    package = tmp_path / "revision-x.zip"
    result = bundle(source, package)
    root = tmp_path / "version-x"
    observation = stage_tool_source_bundle(package, expected_sha256=result["sha256"], staging_root=root)
    return package, result["sha256"], root, observation


def test_staging_preserves_compiled_revision_and_only_sets_readonly_file_flags(staged):
    package, digest, root, observation = staged
    assert observation["status"] == "STAGED_VERIFIED_UNAPPROVED"
    assert observation["source_root"] == str(root / "source")
    assert observation["read_only_file_flags_verified"] is True
    assert observation["may_publish"] is observation["may_authorize"] is observation["may_execute"] is False
    assert verify_staged_tool_source_bundle(package, expected_sha256=digest, staging_root=root) == observation
    for file in root.rglob("*"):
        if file.is_file():
            assert file.stat().st_mode & 0o222 == 0
    assert not (root.parent / "current").exists()


def test_staging_never_overwrites_an_existing_or_partial_version(staged):
    package, digest, root, _ = staged
    before = (root / "bundle-manifest.json").read_bytes()
    with pytest.raises(SourceCandidateError, match="new directory"):
        stage_tool_source_bundle(package, expected_sha256=digest, staging_root=root)
    assert (root / "bundle-manifest.json").read_bytes() == before
    partial = root.parent / "partial"
    partial.mkdir()
    with pytest.raises(SourceCandidateError, match="new directory"):
        stage_tool_source_bundle(package, expected_sha256=digest, staging_root=partial)
    assert not list(partial.iterdir())


def test_next_version_does_not_change_the_previous_version_source_directory(source, staged, tmp_path):
    old_package, old_digest, old_root, first = staged
    other_source = tmp_path / "private-y"
    shutil.copytree(source, other_source)
    (other_source / "src/omni_body_skill/tools/handler.py").write_bytes(b"NEW_REVISION = True\n")
    new_package = tmp_path / "revision-y.zip"
    new_bundle = bundle(other_source, new_package)
    second = stage_tool_source_bundle(new_package, expected_sha256=new_bundle["sha256"],
                                      staging_root=tmp_path / "version-y")
    assert first["source_inputs_sha256"] != second["source_inputs_sha256"]
    assert first["capability_manifest_sha256"] != second["capability_manifest_sha256"]
    assert verify_staged_tool_source_bundle(old_package, expected_sha256=old_digest, staging_root=old_root) == first
    with pytest.raises(SourceCandidateError, match="staged source"):
        verify_staged_tool_source_bundle(new_package, expected_sha256=new_bundle["sha256"], staging_root=old_root)


@pytest.mark.parametrize("relative", ["source/src/omni_body_skill/tools/handler.py",
                                      "source/mirror/omni_body_skill/tools/handler.py",
                                      "bundle-manifest.json"])
def test_changed_source_mirror_or_local_index_cannot_replace_the_original_bundle_pin(staged, relative):
    package, digest, root, _ = staged
    path = root / relative
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b"unreviewed")
    path.chmod(0o444)
    with pytest.raises(SourceCandidateError, match="staged source"):
        verify_staged_tool_source_bundle(package, expected_sha256=digest, staging_root=root)


def test_writable_file_is_detected_even_when_its_bytes_have_not_changed(staged):
    package, digest, root, _ = staged
    (root / "source/src/omni_body_skill/tools/handler.py").chmod(0o644)
    with pytest.raises(SourceCandidateError, match="writable"):
        verify_staged_tool_source_bundle(package, expected_sha256=digest, staging_root=root)


@pytest.mark.parametrize("is_directory", [False, True])
def test_unexpected_files_and_empty_directories_are_detected(staged, is_directory):
    package, digest, root, _ = staged
    root.chmod(0o755)
    path = root / "unreviewed"
    if is_directory:
        path.mkdir()
    else:
        path.write_bytes(b"unreviewed")
    with pytest.raises(SourceCandidateError, match="inventory differs"):
        verify_staged_tool_source_bundle(package, expected_sha256=digest, staging_root=root)


def test_bad_bundle_pin_is_rejected_before_creating_a_destination(source, tmp_path):
    package = tmp_path / "revision.zip"
    bundle(source, package)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(SourceCandidateError, match="digest"):
        stage_tool_source_bundle(package, expected_sha256="0" * 64, staging_root=destination)
    assert not destination.exists()


def test_stage_cli_can_reverify_without_overwriting_or_starting_runtime(staged, capsys):
    package, digest, root, _ = staged
    path = Path(__file__).resolve().parents[1] / "scripts/stage-tool-source.py"
    spec = importlib.util.spec_from_file_location("p8_stage_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = ["--bundle", str(package), "--sha256", digest, "--destination", str(root)]
    assert module.main([*args, "--verify-only"]) == 0
    assert "STAGED_VERIFIED_UNAPPROVED" in capsys.readouterr().out
    assert module.main(args) == 1
    assert "SOURCE_STAGE_REJECTED" in capsys.readouterr().out
