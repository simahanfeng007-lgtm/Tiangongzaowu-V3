"""Real file receipts and fixed-profile boundary checks, without model/App calls."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import (
    WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256,
    WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256,
    composition_permission_allowed, composition_authority_allowed,
    validate_composition_arguments,
)
from omni_body_skill.tool_contracts import build_action_schema_catalog
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from runtime_security.composition_path import probe_composition_write_target, resolve_composition_path
from total_gateway.action_registry import compile_action_authority, ActionRegistryError
from total_gateway.composition_planner_mode_authority import PlannerModeConfigV1

WRITE = dict(profile_id=WORKSPACE_WRITE_PROFILE_ID, profile_sha256=WORKSPACE_WRITE_PROFILE_SHA256)
PYTHON = dict(profile_id=WORKSPACE_PYTHON_PROFILE_ID, profile_sha256=WORKSPACE_PYTHON_PROFILE_SHA256)


@pytest.fixture(scope="module")
def authority():
    actions = {"file.read": ("A0", "read"), "file.list": ("A0", "read"), "file.hash": ("A0", "read"),
               "file.write": ("A3", "write"), "code.write": ("A3", "write"),
               "code.patch_replace": ("A3", "write"), "file.mkdir": ("A2", "write"),
               "python.run": ("A4", "write"), "shell.run": ("A4", "execute")}
    rows = {action: {"id": action, "risk": risk, "effect": effect, "handler": "_action_" + action.replace(".", "_"),
                     "alias_to": "", "executable": True} for action, (risk, effect) in actions.items()}
    for action, descriptor in build_action_schema_catalog(rows).items():
        rows[action].update(descriptor)
    digest = canonical_sha256(rows)
    return compile_action_authority({"schema": "tiangong.v3.capability_manifest.v1", "source_hash": digest,
        "total": len(rows), "executable": len(rows), "unavailable": 0, "capabilities": rows,
        "validation": {"ok": True, "source_hash": digest, "executable_without_route": []}}, generated_at_ms=0)


def test_actual_permissions_require_the_exact_system_profile(authority):
    for permission in authority.registry.permissions:
        legacy = composition_permission_allowed(permission)
        assert legacy is (permission.action_id in {"file.read", "file.list", "file.hash"})
        assert composition_permission_allowed(permission, **WRITE) is (permission.action_id not in {"python.run", "shell.run"})
        assert composition_permission_allowed(permission, **PYTHON) is (permission.action_id != "shell.run")
        assert not composition_permission_allowed(permission, profile_id=WRITE["profile_id"], profile_sha256="0" * 64)
        assert not composition_permission_allowed(permission, profile_id="model.allow-all", profile_sha256=WRITE["profile_sha256"])


@pytest.mark.parametrize("action,args", [
    ("file.write", {"content": "ok", "confirmed": True}),
    ("file.write", {"content": "ok", "binary": True}),
    ("code.write", {"content": "pass"}),
    ("code.patch_replace", {"find": "a", "replace": "b", "count": 1, "regex": True}),
    ("code.patch_replace", {"find": "a", "count": 0}),
    ("python.run", {"code": "print(1)"}),
    ("python.run", {"argv": ["ok"], "timeout": True}),
    ("python.run", {"argv": ["ok"], "timeout": 61}),
])
def test_model_arguments_cannot_widen_profile(action, args):
    with pytest.raises(ValueError):
        validate_composition_arguments(action, args, **PYTHON)


@pytest.mark.parametrize("bad", ["../outside.txt", "new/missing.txt", "proof.txt:stream", "NUL", "trailing. ",
    ".omni_workspace.lock", ".OMNI_WORKSPACE.LOCK", ".Tiangong_Emergency_Audit/event.json", ".tiangong_sandboxes/evil.py"])
def test_native_write_path_rejects_before_mutation(tmp_path, bad):
    with pytest.raises((ValueError, OSError)):
        resolve_composition_path(tmp_path, bad, writing=True)
    assert list(tmp_path.iterdir()) == []


def test_hard_link_cannot_expose_an_external_file_through_workspace(tmp_path):
    import os
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    workspace = tmp_path / "task"
    workspace.mkdir()
    linked = workspace / "linked.txt"
    os.link(outside, linked)
    for writing in (False, True):
        with pytest.raises(ValueError, match="hard link"):
            resolve_composition_path(workspace, str(linked), writing=writing)
    assert outside.read_text("utf-8") == "unchanged"


def test_write_snapshot_detects_same_size_replacement(tmp_path):
    file = tmp_path / "proof.txt"
    file.write_bytes(b"old")
    before = probe_composition_write_target(str(file), tmp_path)
    file.write_bytes(b"new")
    after = probe_composition_write_target(str(file), tmp_path)
    assert before["size_bytes"] == after["size_bytes"]
    assert before["content_sha256"] != after["content_sha256"]


@pytest.mark.parametrize("action,args,existing", [
    ("file.write", {"content": "新建文本\n"}, None),
    ("code.write", {"content": "print('ok')\n", "syntax_check": False}, None),
    ("code.patch_replace", {"find": "wrong", "replace": "right", "count": 1}, "wrong\n"),
    ("file.mkdir", {}, None),
])
def test_real_profile_writes_match_explicit_result_and_value_contract(authority, tmp_path, monkeypatch, action, args, existing):
    monkeypatch.delenv("TIANGONG_OMNI_BODY_STATE_ROOT", raising=False)
    target = tmp_path / ("folder" if action == "file.mkdir" else "result.py")
    if existing is not None:
        target.write_text(existing, encoding="utf-8")
    state = probe_composition_write_target(str(target), tmp_path)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), fact_kernel_enabled=False,
        execution_profile_id=WORKSPACE_WRITE_PROFILE_ID, execution_profile_sha256=WORKSPACE_WRITE_PROFILE_SHA256,
        target_snapshot_sha256=canonical_sha256(state)))
    validate_composition_arguments(action, args, **WRITE)
    schema = authority.schema_catalog.resolve(action, "omni-registry-v1", require_explicit=True, require_result_explicit=True)
    schema.validate_exact(action, str(target), args, workspace=tmp_path)
    raw = runtime.run(action, str(target), args)
    assert raw["success"], raw
    payload = {"schema": "tiangong.v3.omni_body.v1", "ok": True, "zhuangtai": "wancheng", "gongju": "omni_body",
               "action": action, "target": str(target), "result": raw, "llm_brief": "completed", "evidence": {}}
    authority.schema_catalog.validate_result_exact(action, "omni-registry-v1", payload)
    selector = next(value for value in schema.value_schemas if value.json_pointer == "/result")
    authority.schema_catalog.validate_value_exact(selector.value_schema_sha256, raw)
    proof = raw["write_evidence"]
    assert proof["post"]["exists"] and proof["rollback"]["available"]
    if action != "file.mkdir":
        assert proof["post"]["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        assert proof["post"]["bytes"] == len(target.read_bytes())
    broken = deepcopy(payload)
    broken["result"]["write_evidence"]["changed"] = not proof["changed"]
    with pytest.raises(ActionRegistryError):
        authority.schema_catalog.validate_result_exact(action, "omni-registry-v1", broken)


def test_mode_cannot_turn_a0_into_arbitrary_execution():
    args = dict(config_version=1, mode="LIMITED", workspace_scope=("workspace.test",), cooldown_ms=0,
                observation_window_ms=0, created_at_ms=1, config_sha256="0" * 64)
    legacy = PlannerModeConfigV1(**args).with_computed_sha256()
    assert legacy.has_valid_sha256() and "execution_profile_id" not in legacy.model_dump(mode="json")
    with pytest.raises(ValueError):
        PlannerModeConfigV1(**args, risk_ceiling="A4")
    config = PlannerModeConfigV1(**args, risk_ceiling="A4", execution_profile_id=WORKSPACE_PYTHON_PROFILE_ID,
                                execution_profile_sha256=WORKSPACE_PYTHON_PROFILE_SHA256).with_computed_sha256()
    assert config.has_valid_sha256()
