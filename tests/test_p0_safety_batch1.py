"""P0 安全批次一（2026-08-22）故障注入测试。

覆盖三项修复：
1. knowledge index 读损坏 → fail-closed 隔离 + 从 contexts 显式重建
2. api_keys.json 读损坏 → 拒绝保存（不再空对象覆盖）
3. 微信 callback 无签名直通 → 强制签名 + 防重放 + 监听边界
4. Endpoint DNS 连接钉扎 → 校验与连接同一事实
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from v3 import knowledge_store as ks
from v3 import peizhi
from v3.duihua_qiaojie import _save_llm_settings
from v3.endpoint_security import EndpointBinding
from v3.gateway_links import (
    _WechatHTTPServer,
    _WeChatCallbackHandler,
    _is_loopback_host,
    _sha1_sorted,
    _wechat_replay_guard,
)
from v3.jineng.model_transport_executor import (
    TransportExecutionError,
    _pinned_request,
    execute_streaming_turn,
)


# ---------- 1. knowledge index fail-closed ----------


def _seed_knowledge(root: Path, document_id: str, text: str) -> None:
    ctx = {
        "document_id": document_id,
        "created_at": "2026-08-22T10:00:00",
        "updated_at": "2026-08-22T10:00:00",
        "card": {"title": document_id, "summary": text[:40]},
        "text": text,
    }
    (root / "contexts").mkdir(parents=True, exist_ok=True)
    (root / "contexts" / f"{document_id}.json").write_text(
        json.dumps(ctx, ensure_ascii=False), encoding="utf-8"
    )


def test_corrupt_index_is_quarantined_and_rebuilt_from_contexts(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(parents=True)
    _seed_knowledge(root, "doc_alpha", "知识库正文 alpha：" + "长" * 100)
    # 故障注入：已有的 index.json 被写成半截 JSON（模拟磁盘抖动/掉电半写）。
    (root / "index.json").write_text('{"schema": "tiangong.v3.knowledge.index.v1", "docu', encoding="utf-8")
    # 旧行为：这里静默返回空索引，下一次保存把登记无声蒸发。
    recovered = ks.knowledge_list({"knowledgeRoot": str(root)})
    assert recovered["count"] == 1
    assert recovered["documents"][0]["document_id"] == "doc_alpha"
    # 损坏件被隔离保留，索引被重建为合法 JSON。
    quarantines = list(root.glob("index.corrupt-*.json"))
    assert len(quarantines) == 1
    assert "docu" in quarantines[0].read_text(encoding="utf-8")
    rebuilt = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert "doc_alpha" in rebuilt["documents"]
    assert (root / "index.recovery.log").exists()


def test_load_index_raises_on_corruption_instead_of_empty(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir(parents=True)
    (root / "index.json").write_text("not json at all", encoding="utf-8")
    with pytest.raises(ks.KnowledgeIndexCorrupted):
        ks._load_index(root)
    # 非 JSON 对象同样拒绝。
    (root / "index.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ks.KnowledgeIndexCorrupted):
        ks._load_index(root)


# ---------- 2. api_keys.json 读损坏拒绝保存 ----------


def _valid_model_payload() -> dict:
    return {
        "provider_identity": "custom",
        "service_preset": "custom",
        "protocol_family": "openai_chat_completions",
        "base_url": "https://api.example.test/v1",
        "model_name": "test-model",
    }


def test_save_llm_settings_refuses_when_config_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "api_keys.json"
    config.write_text('{"_default_provider": "openai", "_custom_endp', encoding="utf-8")
    monkeypatch.setattr(peizhi, "API_PEIZHI_LUJING", config)
    result = _save_llm_settings(_valid_model_payload())
    assert result["ok"] is False
    assert result["error"] == "config_file_unreadable_refused_save"
    # 原文件保持原样（未被覆盖、未被清空），隔离副本保留证据。
    assert "openai" in config.read_text(encoding="utf-8")
    assert len(list(tmp_path.glob("api_keys.corrupt-*.json"))) == 1


def test_save_llm_settings_refuses_when_config_not_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "api_keys.json"
    config.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(peizhi, "API_PEIZHI_LUJING", config)
    result = _save_llm_settings(_valid_model_payload())
    assert result["ok"] is False
    assert result["error"] == "config_file_invalid_refused_save"
    assert config.read_text(encoding="utf-8") == "[1, 2, 3]"


# ---------- 3. 微信 callback 强制签名 + 防重放 + 监听边界 ----------


class _FakeManager:
    """最小回调管理器：记录 dispatch 与状态，不触网。"""

    def __init__(self, settings: dict):
        self.settings = {"wechat": {"callback": settings}}
        self.dispatched: list[dict] = []
        self.statuses: list[tuple] = []

    def dispatch_inbound(self, **kwargs) -> dict:
        self.dispatched.append(kwargs)
        return {"ok": True, "reply": "ok"}

    def _set_status(self, channel: str, status: str, **extra) -> None:
        self.statuses.append((channel, status, extra))


def _plain_message_xml(content: str = "你好", from_user: str = "tester") -> str:
    return (
        f"<xml><ToUserName><![CDATA[gh_test]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<Content><![CDATA[{content}]]></Content></xml>"
    )


@pytest.fixture()
def callback_server(tmp_path: Path):
    manager = _FakeManager(
        {
            "path": "/wechat/callback",
            "token": "unit-test-token",
            "sync_reply": True,
            "auto_reply": False,
        }
    )

    class Handler(_WeChatCallbackHandler):
        pass

    Handler.manager = manager
    server = _WechatHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/wechat/callback"
    try:
        yield base, manager
    finally:
        server.shutdown()
        server.server_close()


def _signed_query(token: str, timestamp: int, nonce: str) -> str:
    signature = _sha1_sorted(token, str(timestamp), nonce)
    return f"signature={signature}&timestamp={timestamp}&nonce={nonce}"


def test_unsigned_plaintext_post_is_rejected(callback_server) -> None:
    base, manager = callback_server
    # 故障注入：无任何签名的明文 POST（旧行为直接进入 dispatch 触发 AI）。
    response = httpx.post(base, content=_plain_message_xml().encode("utf-8"), timeout=5)
    assert response.status_code == 403
    assert "missing_signature" in response.text
    assert manager.dispatched == []


def test_wrong_signature_is_rejected(callback_server) -> None:
    base, manager = callback_server
    response = httpx.post(
        base + "?signature=deadbeef&timestamp=1&nonce=n1",
        content=_plain_message_xml().encode("utf-8"),
        timeout=5,
    )
    assert response.status_code == 403
    assert manager.dispatched == []


def test_signed_fresh_message_dispatches_then_replay_rejected(callback_server) -> None:
    base, manager = callback_server
    now = int(time.time())
    query = _signed_query("unit-test-token", now, "nonce-once")
    response = httpx.post(f"{base}?{query}", content=_plain_message_xml().encode("utf-8"), timeout=5)
    assert response.status_code == 200
    assert len(manager.dispatched) == 1
    assert manager.dispatched[0]["text"]
    # 同一 timestamp+nonce 重放 → 拒绝，不二次触发 AI。
    replay = httpx.post(f"{base}?{query}", content=_plain_message_xml().encode("utf-8"), timeout=5)
    assert replay.status_code == 403
    assert "replayed_nonce" in replay.text
    assert len(manager.dispatched) == 1


def test_stale_timestamp_is_rejected(callback_server) -> None:
    base, manager = callback_server
    stale = int(time.time()) - 3600
    query = _signed_query("unit-test-token", stale, "nonce-stale")
    response = httpx.post(f"{base}?{query}", content=_plain_message_xml().encode("utf-8"), timeout=5)
    assert response.status_code == 403
    assert "stale_timestamp" in response.text
    assert manager.dispatched == []


def test_replay_guard_units() -> None:
    now = int(time.time())
    assert _wechat_replay_guard("not-a-number", "n") == "invalid_timestamp"
    assert _wechat_replay_guard(str(now - 7200), "n") == "stale_timestamp"
    assert _wechat_replay_guard(str(now), "") == "missing_nonce"
    assert _wechat_replay_guard(str(now), f"unit-{now}-a") is None
    assert _wechat_replay_guard(str(now), f"unit-{now}-a") == "replayed_nonce"


def test_loopback_boundary_units() -> None:
    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("localhost") is True
    assert _is_loopback_host("::1") is True
    assert _is_loopback_host("0.0.0.0") is False
    assert _is_loopback_host("192.168.1.10") is False
    assert _is_loopback_host("example.com") is False


# ---------- 4. Endpoint DNS 连接钉扎 ----------


def _binding(ips: tuple[str, ...]) -> EndpointBinding:
    return EndpointBinding(
        provider_id="custom",
        base_url="https://api.example.test/v1",
        origin="https://api.example.test",
        host="api.example.test",
        port=443,
        official=False,
        custom_scope="endpoint_test",
        resolved_ips=ips,
    )


def test_pinned_request_pins_connection_keeps_domain_identity() -> None:
    pinned_url, headers, sni = _pinned_request("https://api.example.test/v1/chat", _binding(("93.184.216.34",)))
    assert pinned_url == "https://93.184.216.34/v1/chat"
    assert headers == {"Host": "api.example.test"}
    assert sni == "api.example.test"


def test_pinned_request_handles_ipv6_and_custom_port() -> None:
    pinned_url, headers, sni = _pinned_request(
        "https://api.example.test:8443/v1/chat", _binding(("2606:4700::6810:85e5",))
    )
    assert pinned_url == "https://[2606:4700::6810:85e5]:8443/v1/chat"
    assert headers == {"Host": "api.example.test:8443"}
    assert sni == "api.example.test"


def test_pinned_request_fails_closed_without_validated_ip() -> None:
    with pytest.raises(TransportExecutionError):
        _pinned_request("https://api.example.test/v1/chat", _binding(()))


def test_execute_streaming_turn_sends_to_pinned_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    from v3.jineng import model_transport_executor as executor

    captured: dict = {}

    class _StubResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter([])

    class _StubClient:
        def build_request(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            captured["extensions"] = kwargs.get("extensions")
            captured["payload"] = kwargs.get("json")
            return object()

        def send(self, request, stream: bool = False):
            captured["stream"] = stream
            return _StubResponse()

    class _StubTransport:
        protocol_family = "stub"

        def build_request(self, endpoint, api_key, payload):
            from v3.jineng.model_transport_contract import TransportRequest

            return TransportRequest(
                url="https://api.example.test/v1/chat",
                headers={"Authorization": "Bearer test-key"},
                payload=dict(payload),
                protocol_family="stub",
            )

        def consume_stream_event(self, state, event):
            return "", ""

        def finalize_turn(self, endpoint, state):
            return {"finish_reason": "stop"}

    monkeypatch.setattr(executor, "get_model_transport", lambda family: _StubTransport())
    monkeypatch.setattr(
        executor,
        "validate_model_endpoint",
        lambda provider, base_url, resolve_dns=True: _binding(("203.0.113.10", "203.0.113.11")),
    )
    endpoint = type("E", (), {"provider_identity": "custom", "base_url": "https://api.example.test/v1", "protocol_family": "stub"})()
    result = executor.execute_streaming_turn(
        client=_StubClient(),
        endpoint=endpoint,
        api_key="test-key",
        canonical_payload={"model": "m", "messages": []},
        max_wall_clock_seconds=10,
    )
    assert result.http_status == 200
    # 连接目标是被验证过的 IP，Host/SNI 保持原域名——校验与连接同一事实。
    assert captured["url"] == "https://203.0.113.10/v1/chat"
    assert captured["headers"]["Host"] == "api.example.test"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["extensions"] == {"sni_hostname": "api.example.test"}
    assert captured["stream"] is True
