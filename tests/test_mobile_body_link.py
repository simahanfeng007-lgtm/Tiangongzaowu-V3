from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from action_registry import load_action_registry
from mobile_capabilities import MOBILE_CAPABILITY_DEFINITIONS
from mobile_link import MobileBodyBroker, MobileLinkError, _safe_mobile_host


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = (
    REPO_ROOT
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
    / "omni_body_skill"
    / "registry"
    / "capability_manifest.generated.json"
)


def test_mobile_capabilities_are_absent_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIANGONG_MOBILE_LINK", raising=False)
    snapshot = load_action_registry(MANIFEST.resolve(), generated_at_ms=1)
    ids = {item.action_id for item in snapshot.permissions}
    assert not (set(MOBILE_CAPABILITY_DEFINITIONS) & ids)


def test_mobile_capabilities_enter_native_registry_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIANGONG_MOBILE_LINK", "1")
    snapshot = load_action_registry(MANIFEST.resolve(), generated_at_ms=1)
    permissions = {item.action_id: item for item in snapshot.permissions}
    assert set(MOBILE_CAPABILITY_DEFINITIONS) <= set(permissions)
    assert permissions["mobile.observe_ui"].effective_risk == "A0"
    assert permissions["mobile.screenshot"].effective_risk == "A0"
    assert permissions["mobile.tap"].effective_risk == "A4"
    assert permissions["mobile.input_text"].effective_risk == "A4"
    assert "external_write" in permissions["mobile.tap"].allowed_side_effects


def test_pair_auth_task_result_roundtrip(tmp_path: Path) -> None:
    broker = MobileBodyBroker(tmp_path)
    pairing = broker.create_pairing_code()
    paired = broker.pair(
        pairing["code"],
        "Android test",
        ["mobile.observe_ui", "mobile.tap", "not.real"],
    )
    assert broker.authenticate(paired["device_token"])["device_id"] == paired["device_id"]
    assert paired["capabilities"] == ["mobile.observe_ui", "mobile.tap"]
    broker.heartbeat(paired["device_id"], paired["capabilities"])

    worker_error: list[BaseException] = []

    def worker() -> None:
        try:
            task = broker.next_task(paired["device_id"], 3000)
            assert task is not None
            assert task["action"] == "mobile.observe_ui"
            assert broker.submit_result(
                paired["device_id"],
                task["task_id"],
                {"ok": True, "data": {"package": "com.example"}},
            )
        except BaseException as exc:  # pragma: no cover - propagated below
            worker_error.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    result = broker.enqueue("mobile.observe_ui", {}, timeout_ms=5000)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not worker_error
    assert result["ok"] is True
    assert result["data"]["package"] == "com.example"


def test_pairing_code_is_one_use(tmp_path: Path) -> None:
    broker = MobileBodyBroker(tmp_path)
    pairing = broker.create_pairing_code()
    broker.pair(pairing["code"], "one", ["mobile.observe_ui"])
    with pytest.raises(MobileLinkError):
        broker.pair(pairing["code"], "two", ["mobile.observe_ui"])


def test_public_bind_is_rejected() -> None:
    assert _safe_mobile_host("127.0.0.1") == "127.0.0.1"
    assert _safe_mobile_host("192.168.1.25") == "192.168.1.25"
    assert _safe_mobile_host("100.64.1.25") == "100.64.1.25"
    with pytest.raises(MobileLinkError):
        _safe_mobile_host("8.8.8.8")
