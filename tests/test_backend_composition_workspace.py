"""Signed production reader authority reaches the real BackendClient boundary."""
import json
from pathlib import Path

import pytest

from total_gateway.backend_client import BackendClient, BackendClientError
from tests.test_backend_client import FakeBackendTransport, backend_response
from tests.test_composition_step_execution_p7d1 import _runtime_fixture, _nonce_count


@pytest.fixture
def authorized_reader(tmp_path):
    target = tmp_path / "proof.txt"
    target.write_text("actual workspace file", encoding="utf-8")
    with _runtime_fixture(tmp_path, action_id="file.read", target=str(target.resolve()), arguments={}) as fixture:
        prepared = fixture.coordinator._preflight(fixture.record, now_ms=1700)
        yield fixture, prepared, target


def execute(fixture, prepared, transport, *, root=None, **overrides):
    client = BackendClient(transport, fixture.p7c.store,
                           ticket_consumer_instance_id="workspace-boundary-test",
                           composition_workspace_root=root)
    kwargs = dict(
        capability_manifest=fixture.manifest, trust_bundle=prepared["trust_bundle"],
        now_ms=1700, expected_gateway_epoch=1, minimum_generation=1,
        grant=prepared["grant"], intent=prepared["intent"], impact=prepared["impact"],
        decision=prepared["decision"], claim=prepared["claim"],
        expected_fence_epoch=prepared["ticket"].payload.fence_epoch, active_lease_epoch=1,
        expected_target_snapshot_sha256=prepared["target_snapshot_sha256"],
        actual_target_snapshot_sha256=prepared["target_snapshot_sha256"],
        expected_composition_binding=prepared["binding"],
    )
    kwargs.update(overrides)
    return client.execute(prepared["ticket"], prepared["invocation"], **kwargs)


def test_real_signed_workspace_read_crosses_client_with_legacy_hotfix(authorized_reader, monkeypatch):
    fixture, prepared, target = authorized_reader
    # Install the same real legacy replacement used by the desktop startup.
    from total_gateway import backend_client
    from v3 import hotfix_20260727
    monkeypatch.setattr(backend_client, "_reject_host_paths", backend_client._reject_host_paths)
    monkeypatch.setattr(hotfix_20260727, "_log", lambda *_: None)
    hotfix_20260727._patch_backend_host_path_guard()
    assert getattr(backend_client._reject_host_paths, "_hotfix_20260727", False)
    transport = FakeBackendTransport()
    transport.response = backend_response(prepared["ticket"], {"read": target.read_text("utf-8")})
    response = execute(fixture, prepared, transport, root=fixture.p7c.root)
    assert response.result.status == "SUCCEEDED"
    assert len(transport.calls) == 1
    assert json.loads(transport.calls[0][0])["arguments"] == prepared["invocation"]


@pytest.mark.parametrize("bad", ["no_root", "wrong_root", "tampered_grant", "wrong_policy", "incomplete", "changed", "missing", "linked"])
def test_workspace_exception_rejects_without_dispatch_or_nonce(authorized_reader, tmp_path, monkeypatch, bad):
    fixture, prepared, target = authorized_reader
    root = fixture.p7c.root
    overrides = {}
    if bad == "no_root":
        root = None
    elif bad == "wrong_root":
        root = tmp_path.parent
    elif bad == "tampered_grant":
        overrides["grant"] = prepared["grant"].model_copy(update={"signature": "A" * 86})
    elif bad == "wrong_policy":
        payload = prepared["grant"].payload.model_copy(update={"path_policy": "object_grant_only"})
        overrides["grant"] = fixture.p7c.signer.sign_omni_capability(payload)
    elif bad == "incomplete":
        overrides["claim"] = None
    elif bad == "changed":
        target.write_text("changed target with different size", encoding="utf-8")
    elif bad == "missing":
        target.unlink()
    elif bad == "linked":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == target or original(p))
    transport = FakeBackendTransport()
    nonces = _nonce_count(fixture.p7c.store)
    dispatched = []
    with pytest.raises(BackendClientError):
        execute(fixture, prepared, transport, root=root,
                before_dispatch=lambda *_: dispatched.append(True), **overrides)
    assert not dispatched and not transport.calls
    assert _nonce_count(fixture.p7c.store) == nonces


@pytest.mark.parametrize("action", ["file.list", "file.read", "file.hash"])
def test_actual_reader_coordinator_reaches_body_and_commits_fact(tmp_path, monkeypatch, action):
    """Actual reader handlers after real signed coordinator/client dispatch.

    The embedded service port is replaced with an in-process Body call; this
    exercises the formerly blocked client, not a claimed desktop acceptance.
    """
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
    from tests.test_composition_backend_transport_p7d1 import _canonical_legacy_json
    import hashlib

    target = tmp_path / "input.txt"
    target.write_text("read-only boundary proof", encoding="utf-8")
    selected = tmp_path if action == "file.list" else target
    monkeypatch.setenv("TIANGONG_OMNI_BODY_STATE_ROOT", str(tmp_path / "body-state"))
    with _runtime_fixture(tmp_path, action_id=action, target=str(selected.resolve()), arguments={}) as fixture:
        calls = []

        class BodyPort:
            def request(self, _method, _path, payload, **_kwargs):
                invocation = payload["execute_ticket"]["arguments"]
                effect = fixture.p7c.store.get_effect(fixture.record.prebound_effect_id)
                assert effect.state == "SIDE_EFFECT_STARTED"
                runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), fact_kernel_enabled=False))
                raw = runtime.run(invocation["action"], invocation["target"], invocation["args"])
                assert raw["success"] is True
                value = {"schema": "tiangong.v3.omni_body.v1", "ok": True,
                         "zhuangtai": "wancheng", "gongju": "omni_body",
                         "action": action, "target": invocation["target"], "result": raw,
                         "llm_brief": "reader completed", "evidence": raw.get("evidence", {})}
                calls.append(invocation)
                return 200, value, hashlib.sha256(_canonical_legacy_json(value)).hexdigest()

        fixture.coordinator._backend = BodyPort()
        outcome = fixture.coordinator.dispatch_record(fixture.record, now_ms=1700)
        assert outcome.status == "SUCCEEDED"
        assert len(calls) == 1
        assert fixture.p7c.store.get_effect(outcome.effect_id).state == "SUCCEEDED"
        assert fixture.facts.get_batch_for_effect(outcome.effect_id, verify_payload=True) is not None
