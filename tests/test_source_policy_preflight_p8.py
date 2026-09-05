"""Policy-only maintenance is not candidate self-authorization or publication."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from total_gateway.tool_source_candidate import SourceCandidateError, _classify
from tests.test_tool_source_candidate_p8 import git, write, commit, POLICY

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def preflight_module():
    spec = importlib.util.spec_from_file_location("p8_policy_preflight", ROOT / "scripts/preflight-tool-source-candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lineage(tmp_path, preflight_module):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "core.autocrlf", "false")
    write(root, "source-ownership.json", json.dumps(POLICY))
    write(root, "src/body/action.py", "OLD_ACTION = True\n")
    write(root, preflight_module.PATH_SOURCE, "OLD_OBSERVER = True\n")
    original = commit(root)
    policy = json.loads(json.dumps(POLICY))
    policy["mappings"].append(preflight_module.PATH_MAPPING)
    write(root, "source-ownership.json", json.dumps(policy))
    policy_base = commit(root)
    git(root, "reset", "--hard", original)
    write(root, preflight_module.PATH_SOURCE, "raise AssertionError('candidate must not execute')\n")
    work = commit(root)
    write(root, "source-ownership.json", json.dumps(policy))
    git(root, "add", "--all")
    tree = git(root, "write-tree")
    candidate = git(root, "-c", "user.name=Preflight fixture", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", work, "-p", policy_base, "-m", "explicit policy integration")
    git(root, "reset", "--hard", candidate)
    return root, original, policy_base, candidate


def observe(module, lineage):
    root, original, policy_base, candidate = lineage
    return module.preflight(root, original_base=original, policy_base=policy_base, candidate=candidate)


def test_policy_baseline_preserves_source_and_merge_candidate_exposes_whole_diff(preflight_module, lineage):
    result = observe(preflight_module, lineage)
    assert result["status"] == "SOURCE_CANDIDATE_OBSERVED"
    assert result["core_policy_changed_paths"] == ["source-ownership.json"]
    assert result["may_publish"] is result["may_authorize"] is result["may_execute"] is False
    changes = result["candidate"]["changes"]
    assert len(changes) == 1
    assert changes[0]["path"] == preflight_module.PATH_SOURCE
    assert changes[0]["before"]["git_oid"] != changes[0]["after"]["git_oid"]


def test_current_policy_maps_only_the_named_existing_file(preflight_module):
    policy = json.loads((ROOT / "source-ownership.json").read_text(encoding="utf-8"))
    assert _classify(preflight_module.PATH_SOURCE, policy) == ("SOURCE", "existing-path-security")
    with pytest.raises(SourceCandidateError, match="unowned"):
        _classify("src/runtime_security/unregistered_future_code.py", policy)


@pytest.mark.parametrize("path", ["source-ownership.json", "src/runtime_security/unregistered.py", "frozen/body.py"])
def test_candidate_cannot_amend_policy_expand_sibling_or_modify_frozen(preflight_module, lineage, path):
    root, original, policy_base, _ = lineage
    if path == "source-ownership.json":
        policy = json.loads((root / path).read_text())
        policy["mappings"].append({"id": "new", "source": "src/new", "source_role": "authoritative", "targets": []})
        write(root, path, json.dumps(policy))
    else:
        write(root, path, "unexpected = True\n")
    with pytest.raises(SourceCandidateError, match="ownership policy|unowned|frozen"):
        observe(preflight_module, (root, original, policy_base, commit(root)))


@pytest.mark.parametrize("mutation", ["source_bytes", "other_root", "directory_mapping", "extra_target", "remove_old_mapping"])
def test_core_baseline_cannot_hide_source_change_or_widen_authority(preflight_module, lineage, mutation):
    root, original, policy_base, _ = lineage
    git(root, "reset", "--hard", policy_base)
    if mutation == "source_bytes":
        write(root, preflight_module.PATH_SOURCE, "REPAIR_HIDDEN_IN_BASE = True\n")
    else:
        policy = json.loads((root / "source-ownership.json").read_text())
        if mutation == "other_root":
            policy["authority_policy"]["editable_roots"].append("extra-root")
        elif mutation == "directory_mapping":
            policy["mappings"][-1]["source"] = "src/runtime_security"
        elif mutation == "extra_target":
            policy["mappings"][-1]["targets"] = ["mirror/path.py"]
        else:
            policy["mappings"].pop(0)
        write(root, "source-ownership.json", json.dumps(policy))
    bad_base = commit(root)
    write(root, preflight_module.PATH_SOURCE, "REPAIRED = True\n")
    with pytest.raises(SourceCandidateError, match="only source-ownership|exactly the existing|another authority"):
        observe(preflight_module, (root, original, bad_base, commit(root)))


def test_unmerged_policy_branch_is_not_a_candidate_ancestor(preflight_module, lineage):
    root, original, policy_base, candidate = lineage
    work = git(root, "rev-parse", candidate + "^1")
    with pytest.raises(SourceCandidateError, match="distinct base"):
        observe(preflight_module, (root, original, policy_base, work))


def test_worktree_edits_do_not_replace_immutable_policy_evidence(preflight_module, lineage):
    root, *_ = lineage
    expected = observe(preflight_module, lineage)
    write(root, "source-ownership.json", "invalid mutable policy")
    write(root, preflight_module.PATH_SOURCE, "unknown mutable bytes")
    assert observe(preflight_module, lineage) == expected


def test_cli_rejection_preserves_identity_no_permission_and_does_not_overwrite(preflight_module, lineage, tmp_path):
    root, original, policy_base, _ = lineage
    report = tmp_path / "report.json"
    args = ["--repository", str(root), "--original-base", original, "--policy-base", policy_base,
            "--candidate", policy_base, "--report", str(report)]
    assert preflight_module.main(args) == 1
    data = report.read_bytes()
    parsed = json.loads(data)
    assert parsed["status"] == "PREFLIGHT_REJECTED"
    assert parsed["candidate_commit"] == policy_base
    assert parsed["may_publish"] is parsed["may_authorize"] is parsed["may_execute"] is False
    with pytest.raises(SystemExit):
        preflight_module.main(args)
    assert report.read_bytes() == data


def test_cli_isolated_bootstrap_has_no_candidate_import():
    result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/preflight-tool-source-candidate.py"), "--help"],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "--policy-base" in result.stdout


def test_file_owner_cannot_be_replaced_with_a_directory(preflight_module, lineage):
    root, original, policy_base, _ = lineage
    (root / preflight_module.PATH_SOURCE).unlink()
    write(root, preflight_module.PATH_SOURCE + "/unregistered.py", "unexpected = True\n")
    with pytest.raises(SourceCandidateError, match="original-to-repaired"):
        observe(preflight_module, (root, original, policy_base, commit(root)))


def test_unchanged_helper_cannot_be_hidden_behind_another_source_change(preflight_module, lineage):
    root, original, policy_base, _ = lineage
    git(root, "reset", "--hard", policy_base)
    write(root, "src/body/action.py", "ONLY_OTHER_SOURCE_CHANGED = True\n")
    with pytest.raises(SourceCandidateError, match="original-to-repaired"):
        observe(preflight_module, (root, original, policy_base, commit(root)))
