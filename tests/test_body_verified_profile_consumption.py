"""Signed capability -> real API wrapper -> actual Body configuration/handlers.

The recording hook observes the real runtime constructor; it does not replace
verification, the API wrapper, the Body handlers, or AppContainer execution.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

import pytest

from contracts import OmniCapabilityGrantPayload, canonical_sha256
from contracts.composition_profile import (
    WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256,
    WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256,
)
from runtime_security.composition_path import probe_composition_write_target
from tests.test_composition_execution_binding_p7c1 import composition_binding
from tests.test_omni_capability_guard import CapabilityFixture, load_module


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src" / "omni_body_skill"
PYTHON = (WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256)
WRITE = (WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256)


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    fixture = CapabilityFixture(tmp_path)
    fixture.module = load_module("profile_return_capability", SKILL / "tools" / "omni_capability.py")
    for key, value in fixture.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TIANGONG_OMNI_BODY_ROOT", str(SKILL))
    monkeypatch.setenv("TIANGONG_OMNI_BODY_STATE_ROOT", str(tmp_path / "body-state"))
    wrapper = load_module("profile_return_api", SKILL / "api" / "v1" / "v3" / "tools" / "omni_body.py")
    runtime, _, error = wrapper._import_runtime()
    assert error is None, error
    configs = []
    original = runtime.__init__

    def record(self, config=None):
        configs.append(config)
        original(self, config)

    monkeypatch.setattr(runtime, "__init__", record)
    return fixture, wrapper, configs


def invocation(fixture, *, nonce="profile-consumption", action="file.write", target=None,
               arguments=None, profile=PYTHON):
    target = str(target or fixture.workspace / "written.txt")
    arguments = {"content": "actual signed write"} if arguments is None else arguments
    legacy, _, _, _ = fixture.grant(nonce)
    values = legacy.payload.model_dump(mode="python")
    values.update(action_id=action, arguments_sha256=fixture.module.invocation_arguments_sha256(action, target, arguments),
                  risk_class="A4" if action == "python.run" else "A3",
                  allow_shell=False, allow_python=action == "python.run")
    runtime = fixture.runtime_meta()
    runtime["fact_kernel_enabled"] = False
    binding = None
    if profile is not None:
        binding = composition_binding(
            request_id=values["request_id"], run_id=values["run_id"], generation=values["generation"],
            effect_id=values["effect_id"], action_id=action, action_version=values["action_version"],
            materialized_arguments_sha256=canonical_sha256(arguments),
            canonical_invocation_sha256=values["arguments_sha256"], target_sha256=canonical_sha256(target),
            target_snapshot_sha256=canonical_sha256(probe_composition_write_target(target, fixture.workspace)),
            workspace_id=values["workspace_id"], workspace_scope_hash=values["workspace_scope_hash"],
            execution_profile_id=profile[0], execution_profile_sha256=profile[1])
        values["composition_execution_binding"] = binding
        runtime["composition_execution_binding"] = binding.model_dump(mode="json")
        runtime.update({key: getattr(binding, key) for key in (
            "request_id", "run_id", "generation", "effect_id", "step_id", "executable_plan_id")})
        runtime["composition_binding_sha256"] = binding.binding_sha256
    grant = fixture.signer.sign_omni_capability(OmniCapabilityGrantPayload(**values)).model_dump(mode="json")
    return {"action": action, "target": target, "args": arguments, "workspace": str(fixture.workspace),
            "__capability_grant": grant, "__runtime": runtime}, binding


def verify(fixture, call):
    return fixture.module.verify_capability_grant(call["__capability_grant"], action=call["action"],
        target=call["target"], args=call["args"], workspace=call["workspace"], runtime_meta=call["__runtime"])


def test_verified_binding_is_detached_and_nonce_replay_stays_rejected(consumer):
    fixture, _, _ = consumer
    call, binding = invocation(fixture)
    verified = verify(fixture, call)
    expected = binding.model_dump(mode="json")
    assert verified["composition_execution_binding"] == expected
    with pytest.raises(fixture.module.CapabilityGrantError, match="replay"):
        verify(fixture, call)
    call["__capability_grant"]["payload"]["composition_execution_binding"]["execution_profile_id"] = "model-forgery"
    call["__runtime"]["composition_execution_binding"]["target_snapshot_sha256"] = "0" * 64
    assert verified["composition_execution_binding"] == expected


@pytest.mark.parametrize("profile", [WRITE, PYTHON, None])
def test_real_api_write_consumes_only_verified_profile_and_snapshot(consumer, profile):
    fixture, wrapper, configs = consumer
    call, binding = invocation(fixture, profile=profile)
    # Unbound model/runtime preferences must not select the privileged profile.
    call["execution_profile_id"] = PYTHON[0]
    call["__runtime"]["execution_profile_id"] = PYTHON[0]
    result = wrapper.run_omni_body(call)
    assert result["ok"], result
    assert Path(call["target"]).read_text(encoding="utf-8") == "actual signed write"
    config = configs[-1]
    assert config.execution_profile_id == (profile[0] if profile else None)
    assert config.execution_profile_sha256 == (profile[1] if profile else None)
    assert config.target_snapshot_sha256 == (binding.target_snapshot_sha256 if binding else None)
    assert config.sandbox_max_changed_mb == (4 if profile else 512)
    assert config.sandbox_allow_deletions is (profile is None)


def test_real_api_rechecks_signed_target_snapshot_before_write(consumer):
    fixture, wrapper, configs = consumer
    call, _ = invocation(fixture)
    target = Path(call["target"])
    target.write_text("external change", encoding="utf-8")
    result = wrapper.run_omni_body(call)
    assert not result["ok"], result
    assert "signed target snapshot changed" in str(result)
    assert target.read_text(encoding="utf-8") == "external change"
    assert configs[-1].execution_profile_id == PYTHON[0]


@pytest.mark.parametrize("tamper", ["signature", "runtime_binding"])
def test_invalid_authority_cannot_reach_runtime_constructor(consumer, tamper):
    fixture, wrapper, configs = consumer
    call, _ = invocation(fixture)
    if tamper == "signature":
        value = call["__capability_grant"]["signature"]
        call["__capability_grant"]["signature"] = ("A" if value[0] != "A" else "B") + value[1:]
    else:
        call["__runtime"]["composition_execution_binding"] = deepcopy(call["__runtime"]["composition_execution_binding"])
        call["__runtime"]["composition_execution_binding"]["execution_profile_id"] = WRITE[0]
    result = wrapper.run_omni_body(call)
    assert not result["ok"]
    assert "CAPABILITY_REJECTED" in str(result)
    assert configs == []
    assert not Path(call["target"]).exists()


@pytest.mark.skipif(os.name != "nt", reason="requires actual Windows AppContainer")
def test_real_api_python_profile_forbids_delete_commit(consumer):
    fixture, wrapper, configs = consumer
    retained = fixture.workspace / "retained.txt"
    retained.write_text("keep", encoding="utf-8")
    script = fixture.workspace / "delete_attempt.py"
    script.write_text("from pathlib import Path\nPath('retained.txt').unlink()\nPath('new.txt').write_text('discard')\n", encoding="utf-8")
    call, binding = invocation(fixture, action="python.run", target=script,
        arguments={"argv": [], "timeout": 15})
    result = wrapper.run_omni_body(call)
    assert not result["ok"], result
    assert "sandbox_deletion_forbidden" in str(result)
    assert retained.read_text(encoding="utf-8") == "keep"
    assert not (fixture.workspace / "new.txt").exists()
    config = configs[-1]
    assert config.execution_profile_id == PYTHON[0]
    assert config.target_snapshot_sha256 == binding.target_snapshot_sha256
    assert config.allow_python and config.sandbox_enabled and config.sandbox_require_os_containment
    assert config.sandbox_max_changed_mb == 4 and not config.sandbox_allow_deletions
