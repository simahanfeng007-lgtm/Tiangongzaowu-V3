"""Source-byte identities and their existing Manifest/permission projections.

These are component contracts, not production running-lock or model evidence.
"""

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from total_gateway.action_registry import compile_action_authority
from total_gateway.tool_manifest_evolution import review_manifest_evolution
from world_understanding.tool_capability_world import source_inputs
from world_understanding.tool_capability_world.source_candidate import SourceCandidateError

from tests.test_tool_manifest_evolution_p8 import compiled


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "snapshot"
    policy = {
        "schema": "tiangong.source-ownership.v2",
        "authority_policy": {"editable_roots": ["src"], "frozen_roots": []},
        "mappings": [{"id": "body", "source": "src/omni_body_skill", "source_role": "authoritative",
                      "targets": ["mirror/omni_body_skill"]}],
    }
    write(root / "source-ownership.json", json.dumps(policy))
    write(root / "src/omni_body_skill/tools/demo.py", "VALUE = 1\n")
    write(root / "src/omni_body_skill/registry/capability_manifest.generated.json", "{}\n")
    return root


def test_input_identity_is_sorted_detached_and_independent_of_checkout_path(snapshot, tmp_path):
    first = source_inputs.compile_tool_source_inputs(snapshot)
    assert first.has_valid_sha256()
    assert tuple(item.path for item in first.files) == (
        "source-ownership.json", "src/omni_body_skill/tools/demo.py",
    )
    other = tmp_path / "another-checkout"
    shutil.copytree(snapshot, other)
    assert source_inputs.compile_tool_source_inputs(other) == first
    payload = first.payload()
    payload["files"][0]["content_sha256"] = "0" * 64
    assert first.has_valid_sha256()
    assert not replace(first, may_execute=True).has_valid_sha256()
    assert not replace(first, may_authorize=True).has_valid_sha256()


def test_generated_manifest_and_mirrors_never_feed_their_own_revision(snapshot):
    before = source_inputs.compile_tool_source_inputs(snapshot)
    write(snapshot / "src/omni_body_skill/registry/capability_manifest.generated.json",
          json.dumps(compiled().to_gateway_dict(source_inputs_sha256=before.source_inputs_sha256)))
    write(snapshot / "mirror/omni_body_skill/tools/demo.py", "UNREVIEWED_MIRROR = True\n")
    write(snapshot / "docs/progress.md", "pending review\n")
    write(snapshot / "tests/test_candidate.py", "def test_candidate(): pass\n")
    assert source_inputs.compile_tool_source_inputs(snapshot) == before
    # Ignoring generated copies here is not a successful mirror gate. The
    # independent official generated-source check still owns that requirement.


@pytest.mark.parametrize("relative", [
    "src/omni_body_skill/tools/demo.py", "src/omni_body_skill/tools/transitive_helper.py",
    "src/omni_body_skill/templates/instructions.md", "src/runtime_security/helper.py",
    "requirements-source.lock", "pyproject.toml",
])
def test_source_helpers_templates_and_dependency_inputs_change_revision(snapshot, relative):
    before = source_inputs.compile_tool_source_inputs(snapshot)
    write(snapshot / relative, "CHANGED = 2\n")
    assert source_inputs.compile_tool_source_inputs(snapshot).source_inputs_sha256 != before.source_inputs_sha256


def test_source_removal_changes_revision(snapshot):
    extra = snapshot / "src/omni_body_skill/tools/extra.py"
    write(extra, "VALUE = 1\n")
    before = source_inputs.compile_tool_source_inputs(snapshot)
    extra.unlink()
    assert source_inputs.compile_tool_source_inputs(snapshot).source_inputs_sha256 != before.source_inputs_sha256


def test_generated_target_inside_an_editable_root_is_still_an_output(snapshot):
    path = snapshot / "source-ownership.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["mappings"][0]["targets"].append("src/generated-body")
    write(path, json.dumps(policy))
    before = source_inputs.compile_tool_source_inputs(snapshot)
    write(snapshot / "src/generated-body/tools/demo.py", "GENERATED = True\n")
    assert source_inputs.compile_tool_source_inputs(snapshot) == before


def test_frozen_source_is_an_input_but_is_not_made_editable(snapshot):
    path = snapshot / "source-ownership.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["authority_policy"]["frozen_roots"] = ["frozen/body"]
    policy["mappings"].append({"id": "frozen-body", "source": "frozen/body",
                               "source_role": "frozen_authoritative", "targets": []})
    write(snapshot / "frozen/body/compat.py", "FROZEN = True\n")
    write(path, json.dumps(policy))
    inputs = source_inputs.compile_tool_source_inputs(snapshot)
    assert "frozen/body/compat.py" in {item.path for item in inputs.files}
    assert inputs.may_authorize is inputs.may_execute is False


def test_handler_only_change_updates_existing_authority_without_permission_expansion(snapshot):
    old_inputs = source_inputs.compile_tool_source_inputs(snapshot)
    result = compiled()
    before = result.to_gateway_dict(source_inputs_sha256=old_inputs.source_inputs_sha256)
    pinned = compile_action_authority(before, generated_at_ms=0)
    write(snapshot / "src/omni_body_skill/tools/demo.py", "VALUE = 2\n")
    new_inputs = source_inputs.compile_tool_source_inputs(snapshot)
    after = result.to_gateway_dict(source_inputs_sha256=new_inputs.source_inputs_sha256)
    newer = compile_action_authority(after, generated_at_ms=0)
    review = review_manifest_evolution(before, after, requested_action_ids=("demo.read",))
    assert before["capabilities"] == after["capabilities"]
    assert before["source_hash"] == after["source_hash"]
    assert pinned.manifest_sha256 != newer.manifest_sha256
    assert pinned.registry.registry_sha256 != newer.registry.registry_sha256
    assert pinned.manifest == before
    assert review.manifest_changed_fields == ("source_inputs_sha256",)
    assert review.deltas == ()
    assert review.newly_a0_action_ids == review.risk_downgraded_action_ids == ()
    assert review.may_publish is False
    assert result.to_gateway_dict().get("source_inputs_sha256") is None


@pytest.mark.parametrize("invalid", ["", "x" * 64, "A" * 64, "0" * 63, "0" * 65, True, 1])
def test_manifest_projection_rejects_invalid_source_revision(invalid):
    with pytest.raises(ValueError, match="source input revision"):
        compiled().to_gateway_dict(source_inputs_sha256=invalid)


def test_duplicate_ownership_json_is_rejected(snapshot):
    write(snapshot / "source-ownership.json", '{"schema": 1, "schema": 2}')
    with pytest.raises(SourceCandidateError, match="policy is invalid"):
        source_inputs.compile_tool_source_inputs(snapshot)


@pytest.mark.parametrize("roots", [None, [], "src", ["../outside"]])
def test_missing_or_unsafe_source_roots_are_rejected(snapshot, roots):
    path = snapshot / "source-ownership.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["authority_policy"]["editable_roots"] = roots
    write(path, json.dumps(policy))
    with pytest.raises(SourceCandidateError):
        source_inputs.compile_tool_source_inputs(snapshot)


@pytest.mark.parametrize("directory", [False, True])
def test_linked_source_inputs_are_rejected_before_reading_their_target(snapshot, tmp_path, directory):
    outside = tmp_path / "outside"
    if directory:
        write(outside / "private.py", "PRIVATE = True\n")
    else:
        write(outside, "PRIVATE = True\n")
    link = snapshot / "src/omni_body_skill/tools/linked"
    try:
        link.symlink_to(outside, target_is_directory=directory)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    with pytest.raises(SourceCandidateError, match="regular file|link or junction"):
        source_inputs.compile_tool_source_inputs(snapshot)


def test_snapshot_relative_path_is_rejected():
    with pytest.raises(SourceCandidateError, match="missing or unsafe"):
        source_inputs.compile_tool_source_inputs(Path("relative"))


@pytest.mark.parametrize("limit", ["_MAX_FILE_BYTES", "_MAX_TOTAL_BYTES", "_MAX_FILES"])
def test_input_size_limits_fail_closed(snapshot, monkeypatch, limit):
    monkeypatch.setattr(source_inputs, limit, 1)
    with pytest.raises(SourceCandidateError, match="size limit"):
        source_inputs.compile_tool_source_inputs(snapshot)
