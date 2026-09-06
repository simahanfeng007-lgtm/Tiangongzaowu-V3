"""Publication preparation uses real Git and bundle bytes; no approval claims."""

from dataclasses import asdict
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from contracts import canonical_json_bytes, canonical_sha256
from total_gateway.tool_manifest_evolution import review_manifest_evolution
from total_gateway.tool_source_bundle import write_tool_source_bundle
from total_gateway.tool_source_candidate import inspect_tool_source_candidate
from total_gateway.tool_source_publication import (
    SourcePublicationError,
    prepare_tool_source_publication,
)

from tests.test_sync_generated_sources import _load_module
from tests.test_tool_source_bundle_p8 import source, prepare  # noqa: F401
from tests.test_tool_source_candidate_p8 import commit, git


@pytest.fixture
def publication(source, tmp_path):
    git(source, "init", "--initial-branch=main")
    git(source, "config", "core.autocrlf", "false")
    _, baseline = prepare(source)
    manifest = source / "src/omni_body_skill/registry/capability_manifest.generated.json"
    before = baseline["build_artifact"]["gateway_manifest"]
    manifest.write_bytes(canonical_json_bytes(before) + b"\n")
    base = commit(source)
    marker = tmp_path / "candidate-must-not-execute"
    (source / "src/omni_body_skill/tools/handler.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8",
    )
    head = commit(source)
    candidate = inspect_tool_source_candidate(
        source, base_commit=base, candidate_commit=head, requested_action_ids=("skill.list",),
    )
    official = _load_module()

    def package(name="candidate.zip", *, snapshot=source, mutate=lambda value: None):
        # Production packaging receives a private native-Git materialization,
        # never a repository containing .git. Preserve that boundary here.
        private = tmp_path / (Path(name).stem + "-build")
        shutil.copytree(snapshot, private, ignore=shutil.ignore_patterns(".git"))
        snapshot = private
        inputs, report = prepare(snapshot)
        report.update(
            schema="tiangong.tool-source-isolated-build-report.v1",
            source_candidate=asdict(candidate),
            build_process={"ok": True, "containment": "windows-appcontainer", "network": "denied"},
            trusted_static_checks={"python_ast_files": 1, "source_topology_valid": True},
            committed_manifest_matches_build=False,
            manifest_review=asdict(review_manifest_evolution(
                before, report["build_artifact"]["gateway_manifest"], requested_action_ids=("skill.list",),
            )),
        )
        mutate(report)

        def synchronize(root):
            assert official.process(write=True, workspace_root=root) == []
            assert official.process(write=False, workspace_root=root) == []

        bundle_path = tmp_path / name
        result = write_tool_source_bundle(
            snapshot, source_inputs=inputs, report=report, output_path=bundle_path,
            synchronize_mirrors=synchronize,
            mirror_generator_sha256=hashlib.sha256(Path(official.__file__).read_bytes()).hexdigest(),
        )
        return bundle_path, result["sha256"]

    return source, base, head, package, marker


def inspect(publication, *, name="candidate.zip", snapshot=None, mutate=lambda value: None, **overrides):
    root, base, head, package, _ = publication
    options = {"mutate": mutate}
    if snapshot is not None:
        options["snapshot"] = snapshot
    path, digest = package(name, **options)
    arguments = dict(bundle_path=path, expected_sha256=digest, base_commit=base,
                     candidate_commit=head, requested_action_ids=("skill.list",))
    arguments.update(overrides)
    return prepare_tool_source_publication(root, **arguments)


def test_valid_source_is_review_material_and_cannot_self_award_exit_gates(publication):
    result = inspect(publication)
    assert result["status"] == "SOURCE_PUBLICATION_REVIEW_REQUIRED"
    assert result["source_bytes_verified_against_git"] is True
    assert result["evidence_contract_action_ids"] == ["skill.list"]
    assert result["required_review_action_ids"] == ["skill.list"]
    assert result["committed_manifest_matches_build"] is False
    assert "COMMITTED_MANIFEST_DIFFERS_FROM_BUILD" in result["blockers"]
    for key in ("build_attestation_verified", "evidence_contract_tests_verified",
                "review_approval_verified", "running_manifest_lock_verified",
                "may_publish", "may_authorize", "may_execute"):
        assert result[key] is False
    payload = copy.deepcopy(result)
    digest = payload.pop("proposal_sha256")
    assert canonical_sha256(payload) == digest
    assert not publication[-1].exists()


def test_bundle_self_hash_cannot_substitute_other_source_for_pinned_git(publication, tmp_path):
    root = publication[0]
    other = tmp_path / "other-source"
    shutil.copytree(root, other, ignore=shutil.ignore_patterns(".git"))
    (other / "src/omni_body_skill/tools/handler.py").write_bytes(b"FOREIGN_REVISION = True\n")
    with pytest.raises(SourcePublicationError, match="pinned Git bytes"):
        inspect(publication, snapshot=other)


def test_mutable_checkout_cannot_change_immutable_publication_source(publication):
    root, base, head, package, marker = publication
    path, digest = package()
    (root / "src/omni_body_skill/tools/handler.py").write_bytes(b"WORKTREE_ONLY = True\n")
    result = prepare_tool_source_publication(
        root, bundle_path=path, expected_sha256=digest, base_commit=base,
        candidate_commit=head, requested_action_ids=("skill.list",),
    )
    assert result["source_bytes_verified_against_git"] is True
    assert not marker.exists()


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["source_candidate"].update(candidate_sha256="0" * 64), "pinned Git scope"),
    (lambda r: r["source_candidate"].update(requested_action_ids=["file.read"]), "pinned Git scope"),
    (lambda r: r.update(schema="invented"), "pinned Git scope"),
    (lambda r: r["build_process"].update(ok=1), "contained static"),
    (lambda r: r["build_process"].update(containment="portable"), "contained static"),
    (lambda r: r["build_process"].update(network="allowed"), "contained static"),
    (lambda r: r["trusted_static_checks"].update(source_topology_valid=1), "contained static"),
    (lambda r: r["trusted_static_checks"].update(python_ast_files=True), "contained static"),
    (lambda r: r.update(cleanup_error="retained failure"), "contained static"),
    (lambda r: r["manifest_review"].update(unexpected_action_ids=["forged"]), "independent review"),
    (lambda r: r.update(committed_manifest_matches_build=True), "comparison is inconsistent"),
])
def test_plausible_packaged_claims_are_independently_rejected(publication, mutate, message):
    with pytest.raises(SourcePublicationError, match=message):
        inspect(publication, mutate=mutate)


def test_untrusted_boolean_claims_do_not_become_behavioral_or_human_approval(publication):
    def forge(report):
        report.update(evidence_contract_tests_verified=True, review_approval_verified=True,
                      running_manifest_lock_verified=True)
    result = inspect(publication, mutate=forge)
    assert result["review_approval_verified"] is False
    assert result["evidence_contract_tests_verified"] is False
    assert result["running_manifest_lock_verified"] is False
    assert result["may_publish"] is False


def test_cli_preserves_pending_proposal_and_never_overwrites_it(publication, tmp_path, capsys):
    root, base, head, package, _ = publication
    path, digest = package()
    script = Path(__file__).resolve().parents[1] / "scripts/prepare-tool-source-publication.py"
    spec = importlib.util.spec_from_file_location("p8_publication_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "proposal.json"
    args = ["--repository", str(root), "--bundle", str(path), "--sha256", digest,
            "--base", base, "--candidate", head, "--action", "skill.list", "--report", str(output)]
    assert module.main(args) == 2
    original = output.read_bytes()
    assert json.loads(original)["status"] == "SOURCE_PUBLICATION_REVIEW_REQUIRED"
    with pytest.raises(SystemExit):
        module.main(args)
    assert output.read_bytes() == original
    assert "SOURCE_PUBLICATION_REVIEW_REQUIRED" in capsys.readouterr().out
