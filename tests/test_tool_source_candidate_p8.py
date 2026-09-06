from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zlib

import pytest

from contracts import canonical_sha256
from total_gateway.tool_source_candidate import (
    SourceCandidateError,
    inspect_tool_source_candidate,
    materialize_tool_source_candidate,
    read_tool_source_manifests,
    _verify_git_content,
    verify_tool_source_candidate,
)


POLICY = {
    "schema": "tiangong.source-ownership.v2",
    "authority_policy": {"editable_roots": ["src", "backend/v3"], "frozen_roots": ["frozen"]},
    "mappings": [
        {"id": "body", "source": "src/body", "source_role": "authoritative", "targets": ["mirror/body"]},
        {"id": "v3", "source": "backend/v3", "source_role": "authoritative", "targets": [],
         "boundary_policy": {"mode": "closed_world", "implementation_roots": ["fact_kernel"],
                             "non_runtime_artifacts": ["README.md"]}},
        {"id": "old", "source": "frozen/body.py", "source_role": "frozen_authoritative", "targets": []},
    ],
}


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
        encoding="utf-8", timeout=15,
    )
    return completed.stdout.strip()


def write(root: Path, path: str, content: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="\n")


def commit(root: Path) -> str:
    git(root, "add", "--all")
    git(root, "-c", "user.name=Source candidate tests", "-c", "user.email=source-test@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-m", "test source")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "core.autocrlf", "false")
    write(root, "source-ownership.json", json.dumps(POLICY))
    write(root, "src/body/action.py", "def read_value():\n    return 1\n")
    write(root, "mirror/body/action.py", "def read_value():\n    return 1\n")
    write(root, "frozen/body.py", "FROZEN = True\n")
    return root, commit(root)


def inspect(root: Path, base: str, head: str):
    return inspect_tool_source_candidate(root, base_commit=base, candidate_commit=head,
                                         requested_action_ids=("file.read",))


def test_candidate_binds_git_source_and_never_imports_candidate(repository, tmp_path: Path) -> None:
    root, base = repository
    marker = tmp_path / "must-not-execute"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    write(root, "src/body/action.py", source)
    write(root, "mirror/body/action.py", source)
    write(root, "tests/test_action.py", "def test_action():\n    assert True\n")
    head = commit(root)
    candidate = inspect(root, base, head)
    assert candidate.has_valid_sha256()
    assert candidate.base_tree == git(root, "rev-parse", base + "^{tree}")
    assert candidate.candidate_tree == git(root, "rev-parse", head + "^{tree}")
    assert candidate.may_authorize is False and candidate.may_execute is False
    assert [(change.path, change.role) for change in candidate.changes] == [
        ("mirror/body/action.py", "GENERATED"), ("src/body/action.py", "SOURCE"),
        ("tests/test_action.py", "VALIDATION"),
    ]
    assert candidate.changes[1].after.content_sha256 == hashlib.sha256(source.encode()).hexdigest()
    verify_tool_source_candidate(root, candidate)
    assert not marker.exists()


def test_candidate_ignores_mutable_checkout_and_rebuilds_exact_objects(repository) -> None:
    root, base = repository
    write(root, "src/body/action.py", "return_value = 2\n")
    head = commit(root)
    candidate = inspect(root, base, head)
    write(root, "src/body/action.py", "return_value = 'working tree drift'\n")
    assert inspect(root, base, head) == candidate
    verify_tool_source_candidate(root, candidate)


def test_rehashed_forged_blob_is_rejected_against_git(repository) -> None:
    root, base = repository
    write(root, "src/body/action.py", "return_value = 2\n")
    candidate = inspect(root, base, commit(root))
    change = candidate.changes[0]
    forged_blob = replace(change.after, content_sha256="a" * 64)
    forged = replace(candidate, changes=(replace(change, after=forged_blob),))
    forged = replace(forged, candidate_sha256=canonical_sha256(forged.payload()))
    assert forged.has_valid_sha256()
    with pytest.raises(SourceCandidateError, match="authoritative Git"):
        verify_tool_source_candidate(root, forged)


@pytest.mark.parametrize("path", ["src/unregistered/action.py", "backend/v3/new_runtime/x.py", "frozen/body.py"])
def test_unowned_closed_world_and_frozen_source_changes_rejected(repository, path: str) -> None:
    root, base = repository
    write(root, path, "candidate = True\n")
    with pytest.raises(SourceCandidateError, match="unowned|closed-world|frozen"):
        inspect(root, base, commit(root))


def test_candidate_cannot_amend_ownership_to_authorize_itself(repository) -> None:
    root, base = repository
    changed = json.loads(json.dumps(POLICY))
    changed["mappings"].append({"id": "new", "source": "src/new_runtime", "source_role": "authoritative", "targets": []})
    write(root, "source-ownership.json", json.dumps(changed))
    write(root, "src/new_runtime/action.py", "candidate = True\n")
    with pytest.raises(SourceCandidateError, match="ownership policy"):
        inspect(root, base, commit(root))


def test_mirror_only_change_cannot_be_a_source_candidate(repository) -> None:
    root, base = repository
    write(root, "mirror/body/action.py", "candidate = True\n")
    with pytest.raises(SourceCandidateError, match="no authoritative source"):
        inspect(root, base, commit(root))


def test_closed_world_existing_implementation_is_a_valid_source(repository) -> None:
    root, base = repository
    write(root, "backend/v3/fact_kernel/new_compiler.py", "candidate = True\n")
    candidate = inspect(root, base, commit(root))
    assert candidate.changes[0].authority_id == "v3"
    assert candidate.changes[0].role == "SOURCE"


@pytest.mark.parametrize("ref", ["HEAD", "main", "--all", "f" * 39, "9" * 40])
def test_only_real_full_commits_can_identify_candidate(repository, ref: str) -> None:
    root, base = repository
    with pytest.raises(SourceCandidateError):
        inspect(root, base, ref)


def test_base_and_candidate_must_be_distinct_and_in_ancestry_order(repository) -> None:
    root, base = repository
    write(root, "src/body/action.py", "new = True\n")
    head = commit(root)
    with pytest.raises(SourceCandidateError, match="distinct base"):
        inspect(root, base, base)
    with pytest.raises(SourceCandidateError, match="distinct base"):
        inspect(root, head, base)


def test_candidate_records_addition_and_deletion(repository) -> None:
    root, base = repository
    (root / "src/body/action.py").unlink()
    write(root, "src/body/renamed.py", "read = 2\n")
    candidate = inspect(root, base, commit(root))
    assert candidate.changes[0].before is not None
    assert candidate.changes[0].after is None
    assert candidate.changes[1].before is None
    assert candidate.changes[1].after is not None


@pytest.mark.parametrize("ids", [(), ("file.read", "file.read"), ("z.read", "a.read"), ("--evil",)])
def test_candidate_action_identity_is_bounded_and_canonical(repository, ids) -> None:
    root, base = repository
    with pytest.raises(SourceCandidateError, match="Action IDs"):
        inspect_tool_source_candidate(root, base_commit=base, candidate_commit=base,
                                      requested_action_ids=ids)


def test_git_symlink_object_is_rejected_without_following_it(repository) -> None:
    root, base = repository
    write(root, "src/body/action.py", "changed = True\n")
    git(root, "add", "src/body/action.py")
    # Construct the Git symlink without needing Windows symlink privileges.
    blob = git(root, "hash-object", "src/body/action.py")
    git(root, "update-index", "--add", "--cacheinfo", f"120000,{blob},src/body/linked.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "symlink candidate")
    with pytest.raises(SourceCandidateError, match="symlink"):
        inspect(root, base, git(root, "rev-parse", "HEAD"))


def test_git_replace_object_cannot_substitute_reviewed_source(repository) -> None:
    root, base = repository
    write(root, "src/body/action.py", "reviewed = 2\n")
    head = commit(root)
    expected = inspect(root, base, head)
    write(root, "src/body/action.py", "replacement = 3\n")
    replaced = commit(root)
    git(root, "replace", head, replaced)
    assert inspect(root, base, head) == expected


def test_inherited_git_environment_cannot_redirect_candidate_repository(repository, monkeypatch, tmp_path):
    root, base = repository
    write(root, "src/body/action.py", "changed = True\n")
    head = commit(root)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "absent-repo.git"))
    assert inspect(root, base, head).candidate_commit == head


def test_corrupted_native_tree_identity_is_rejected(repository):
    root, base = repository
    write(root, "src/body/action.py", "changed = True\n")
    head = commit(root)
    old_tree = git(root, "rev-parse", base + "^{tree}")
    new_tree = git(root, "rev-parse", head + "^{tree}")
    raw = subprocess.run(["git", "-C", str(root), "cat-file", "tree", new_tree],
                         check=True, capture_output=True).stdout
    corrupt = root / ".git/objects" / old_tree[:2] / old_tree[2:]
    corrupt.chmod(0o644)
    corrupt.write_bytes(zlib.compress(f"tree {len(raw)}\0".encode("ascii") + raw))
    # Git versions may reject a corrupt loose object before our independent
    # byte verifier sees it. Both boundaries must reject the candidate.
    with pytest.raises(SourceCandidateError, match="object bytes|object is absent or incompatible"):
        inspect(root, base, head)


@pytest.mark.parametrize("kind", ["blob", "tree", "commit"])
def test_native_hash_verifier_independently_rejects_substituted_bytes(kind):
    raw = b"immutable object contents"
    oid = hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()
    _verify_git_content(oid, kind, raw)
    with pytest.raises(SourceCandidateError, match="object bytes"):
        _verify_git_content(oid, kind, raw + b"substituted")


def test_materialized_source_is_exact_private_and_never_executed(repository, tmp_path):
    root, base = repository
    marker = tmp_path / "untrusted-import"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    write(root, "src/body/action.py", source)
    candidate = inspect(root, base, commit(root))
    write(root, "src/body/action.py", "dirty_checkout = True\n")
    with materialize_tool_source_candidate(root, candidate) as snapshot:
        assert snapshot != root
        assert not (snapshot / ".git").exists()
        assert (snapshot / "src/body/action.py").read_bytes() == source.encode()
        assert not marker.exists()
    assert not snapshot.exists()
    assert (root / "src/body/action.py").read_text().strip() == "dirty_checkout = True"


def test_git_archive_attributes_cannot_hide_source_inputs(repository):
    root, _ = repository
    write(root, ".gitattributes", "src/body/action.py export-ignore\n")
    base = commit(root)
    write(root, "src/body/action.py", "hidden_candidate = True\n")
    candidate = inspect(root, base, commit(root))
    with materialize_tool_source_candidate(root, candidate) as snapshot:
        assert (snapshot / "src/body/action.py").read_bytes() == b"hidden_candidate = True\n"
        assert (snapshot / ".gitattributes").read_bytes() == b"src/body/action.py export-ignore\n"


def test_manifest_reader_uses_committed_artifacts_not_dirty_checkout(repository):
    root, _ = repository
    path = "src/omni_body_skill/registry/capability_manifest.generated.json"
    write(root, path, '{"committed":true}')
    base = commit(root)
    write(root, "src/body/action.py", "changed = True\n")
    candidate = inspect(root, base, commit(root))
    write(root, path, '{"dirty":true}')
    before, after = read_tool_source_manifests(root, candidate)
    assert before == after == {"committed": True}


def test_read_only_cli_never_imports_candidate_and_does_not_claim_build_or_publication(repository, tmp_path):
    from omni_body_skill.tool_contracts import build_action_schema_catalog
    from v3.fact_kernel import compile_manifest

    root, _ = repository
    metadata = {"file.read": {"risk": "A0", "effect": "read", "implemented": True}}
    manifest = compile_manifest(metadata, object, dynamic_actions=("file.read",),
                                action_schema_catalog=build_action_schema_catalog(metadata)).to_gateway_dict()
    write(root, "src/omni_body_skill/registry/capability_manifest.generated.json", json.dumps(manifest))
    base = commit(root)
    marker = tmp_path / "must-never-import"
    write(root, "src/body/action.py", f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    head = commit(root)
    script = Path(__file__).resolve().parents[1] / "scripts/review-tool-source.py"
    result = subprocess.run([sys.executable, "-X", "utf8", str(script), "--repository", str(root), "--base", base,
                             "--candidate", head, "--action", "file.read"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["report_created"] is True
    assert report["source_compilation_verified"] is False
    assert report["sandbox_verified"] is False
    assert report["may_publish"] is False
    assert report["manifest_review"]["requested_without_manifest_delta"] == ["file.read"]
    assert not marker.exists()
