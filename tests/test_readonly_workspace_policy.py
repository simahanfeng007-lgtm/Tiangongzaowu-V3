"""Workspace path constraints do not grant fixed-profile execution authority."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import (
    WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256,
    WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256,
    composition_permission_allowed,
)
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from total_gateway.action_registry import compile_action_registry
from total_gateway.omni_grant_authority import OmniGrantAuthorityError
from tests import test_composition_grant_authority_p7c1 as authority_support


READERS = frozenset({"file.list", "file.read", "file.hash"})
EXPECTED_READERS = READERS | {"core.filesystem." + action for action in READERS}
WRITERS = frozenset({"file.write", "code.write", "code.patch_replace", "file.mkdir"})
EXPECTED_WRITER_RISKS = {
    "file.write": "A3", "code.write": "A3", "code.patch_replace": "A3", "file.mkdir": "A2",
    "core.filesystem.file.write": "A3", "core.filesystem.file.mkdir": "A3",
    "core.code.code.write": "A3", "core.code.code.patch_replace": "A3", "file.patch_replace": "A3",
}
EXPECTED_PYTHON = frozenset({"python.run", "core.code.python.run"})
EXPECTED_WORKSPACE = EXPECTED_READERS | set(EXPECTED_WRITER_RISKS) | EXPECTED_PYTHON


@pytest.fixture(scope="module")
def manifest():
    return json.loads(authority_support.CAPABILITY_MANIFEST.read_text(encoding="utf-8"))


def test_only_supported_workspace_actions_and_their_real_aliases_get_workspace_policy(manifest):
    registry = compile_action_registry(manifest, generated_at_ms=1_250)
    allowed = {p.action_id for p in registry.permissions if p.path_policy == "workspace_only"}
    assert allowed == EXPECTED_WORKSPACE
    assert registry.has_valid_sha256()
    for permission in registry.permissions:
        assert permission.has_valid_sha256()
        if permission.action_id in EXPECTED_READERS:
            assert permission.registry_risk == permission.effective_risk == "A0"
            assert permission.effect == "read"
            assert permission.allowed_side_effects == ("read",)
            assert not permission.allow_shell and not permission.allow_python
        elif permission.action_id in EXPECTED_WRITER_RISKS:
            assert permission.registry_risk == permission.effective_risk == EXPECTED_WRITER_RISKS[permission.action_id]
            assert permission.effect == "write"
            assert permission.allowed_side_effects == ("local_write", "read")
            assert not permission.allow_shell and not permission.allow_python
        elif permission.action_id in EXPECTED_PYTHON:
            assert permission.registry_risk == permission.effective_risk == "A4"
            assert permission.effect == "write"
            assert permission.allowed_side_effects == ("local_write", "read")
            assert not permission.allow_shell and permission.allow_python
        else:
            assert permission.path_policy == "object_grant_only"


def test_workspace_path_policy_does_not_authorize_writes_or_python_without_exact_profile(manifest):
    registry = compile_action_registry(manifest, generated_at_ms=1_250)
    permissions = [p for p in registry.permissions if p.path_policy == "workspace_only"]
    # Alias path constraints inherit the native target boundary, not permission
    # to invoke an action outside the fixed profile's canonical action set.
    assert {p.action_id for p in permissions if composition_permission_allowed(p)} == EXPECTED_READERS
    for profile_id, profile_sha256, expected in (
        (WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256, READERS | WRITERS),
        (WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256, READERS | WRITERS | {"python.run"}),
    ):
        assert {p.action_id for p in permissions if composition_permission_allowed(
            p, profile_id=profile_id, profile_sha256=profile_sha256)} == expected
        for permission in permissions:
            assert not composition_permission_allowed(
                permission, profile_id=profile_id, profile_sha256="0" * 64)
            assert not composition_permission_allowed(
                permission, profile_id="model.allow-all", profile_sha256=profile_sha256)


@pytest.mark.parametrize("change", ["risk", "effect", "alias_risk"])
def test_workspace_exception_cannot_survive_elevated_risk_or_effect(manifest, change):
    changed = deepcopy(manifest)
    capabilities = changed["capabilities"]
    if change == "alias_risk":
        capabilities["core.filesystem.file.read"]["risk"] = "A1"
        expected = {"core.filesystem.file.read"}
    else:
        capabilities["file.read"][change] = "A1" if change == "risk" else "write"
        expected = {"file.read", "core.filesystem.file.read"}
    changed["source_hash"] = canonical_sha256(capabilities)
    changed["validation"]["source_hash"] = changed["source_hash"]
    registry = compile_action_registry(changed, generated_at_ms=1_250)
    for permission in registry.permissions:
        if permission.action_id in expected:
            assert permission.path_policy == "object_grant_only"


@pytest.mark.parametrize("action", sorted(READERS))
def test_existing_absolute_workspace_target_receives_real_composition_authorization(
    tmp_path, action,
):
    target = tmp_path if action == "file.list" else tmp_path / "input.txt"
    if action != "file.list":
        target.write_text("real workspace data", encoding="utf-8")
    with authority_support._harness(
        tmp_path, action_id=action, target=str(target.resolve()), arguments={},
    ) as harness:
        assert not harness.outer.payload.input_objects
        assert all(item.object_grant is None for item in harness.plan.plan_inputs)
        response = authority_support._authorize(harness)
        assert response["status"] == "OK"
        assert response["decision"]["risk_class"] == "A0"
        assert authority_support._authorization_count(harness.store) == 1
        assert response["grant"]["payload"]["composition_execution_binding"][
            "executable_plan_sha256"
        ] == harness.plan.executable_plan_sha256


def test_workspace_reader_still_rejects_escape_and_symlink_targets(tmp_path, monkeypatch):
    target = tmp_path / "input.txt"
    target.write_text("retained", encoding="utf-8")
    with authority_support._harness(
        tmp_path, action_id="file.read", target=str(target.resolve()), arguments={},
    ) as harness:
        for outside in (str(tmp_path.parent / "outside.txt"), "../outside.txt"):
            with pytest.raises(OmniGrantAuthorityError, match="path_policy_exceeded"):
                harness.authority._validate_composition_path_policy(
                    target=outside, args={}, path_policy="workspace_only", object_ids=frozenset(),
                )
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == target or original(p))
        with pytest.raises(OmniGrantAuthorityError, match="path_policy_exceeded"):
            authority_support._authorize(harness)
        assert authority_support._authorization_count(harness.store) == 0


@pytest.mark.parametrize("action", sorted(READERS))
def test_native_readers_still_require_existing_paths(tmp_path, monkeypatch, action):
    monkeypatch.delenv("TIANGONG_OMNI_BODY_STATE_ROOT", raising=False)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), fact_kernel_enabled=False))
    result = runtime.run(action, str(tmp_path / "missing"), {})
    assert result["success"] is False
