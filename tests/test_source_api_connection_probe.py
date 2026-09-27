"""Connection diagnostics use real execution authority, never a second key store."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
import time

import httpx
import pytest

from v3 import endpoint_security, peizhi
from v3.model_endpoint import ModelEndpointConfig
from v3.jineng import http_kehuduan
from v3.jineng.model_transport_registry import get_model_transport, probe_endpoint


def endpoint(base, protocol="openai_chat_completions", provider="custom", overrides=None):
    return ModelEndpointConfig("custom", provider, protocol, base, "probe-model", "test-scope", "",
                               overrides or {}, "gpt_5_6", "test-fingerprint")


def reply(protocol):
    if protocol == "openai_responses":
        return {"object": "response", "status": "completed", "output": []}
    if protocol == "anthropic_messages":
        return {"type": "message", "content": [{"type": "text", "text": "ok"}]}
    return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


@contextmanager
def provider_server(body, status=200, delay=0.0):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append({"path": self.path, "headers": dict(self.headers),
                          "payload": json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
            if delay:
                time.sleep(delay)
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if 300 <= status < 400:
                self.send_header("Location", "/credential-leak-target")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("protocol,suffix", [
    ("openai_chat_completions", "/v1/chat/completions"),
    ("openai_responses", "/v1/responses"),
    ("anthropic_messages", "/v1/messages"),
])
@pytest.mark.parametrize("base_form", ["base", "version", "full", "query"])
def test_real_http_protocol_paths_headers_and_conservative_result(monkeypatch, protocol, suffix, base_form):
    monkeypatch.setenv("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT", "1")
    with provider_server(reply(protocol)) as (base, calls), httpx.Client(trust_env=False) as client:
        if base_form in {"version", "base"}:
            tail = "" if protocol == "anthropic_messages" and base_form == "base" else "/v1"
        else:
            tail = suffix
        query = "?api-version=test" if base_form == "query" else ""
        result = probe_endpoint(client, endpoint(base + tail + query, protocol), "local-test-key")
    assert result["ok"] and result["http_status"] == 200
    assert len(calls) == 1 and calls[0]["path"] == suffix + query
    assert calls[0]["payload"]["stream"] is False
    assert "tools" not in calls[0]["payload"]
    if protocol == "anthropic_messages":
        assert calls[0]["headers"]["x-api-key"] == "local-test-key"
        assert "Authorization" not in calls[0]["headers"]
    else:
        assert calls[0]["headers"]["Authorization"] == "Bearer local-test-key"
    if protocol == "openai_responses":
        assert calls[0]["payload"]["max_output_tokens"] >= 16
        assert calls[0]["payload"]["store"] is False
    assert result["streaming_supported"] is result["native_tools_supported"] is False
    assert "local-test-key" not in json.dumps(result)


def test_custom_anthropic_auth_override_is_used_by_the_probe(monkeypatch):
    monkeypatch.setenv("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT", "1")
    with provider_server(reply("anthropic_messages")) as (base, calls), httpx.Client(trust_env=False) as client:
        cfg = endpoint(base + "/proxy/anthropic", "anthropic_messages", overrides={
            "auth_scheme_by_protocol": {"anthropic_messages": "bearer"}, "anthropic_version": "test-version"})
        result = probe_endpoint(client, cfg, "local-test-key")
    assert result["ok"]
    assert calls[0]["path"] == "/proxy/anthropic/v1/messages"
    assert calls[0]["headers"]["Authorization"] == "Bearer local-test-key"
    assert calls[0]["headers"]["anthropic-version"] == "test-version"
    assert "x-api-key" not in calls[0]["headers"]


@pytest.mark.parametrize("status,body,error", [
    (401, {"error": {"message": "local-test-key"}}, "provider_auth_failed"),
    (403, {}, "provider_permission_denied"),
    (404, {}, "provider_endpoint_or_model_not_found"),
    (429, {}, "provider_rate_limited_or_quota_exhausted"),
    (503, {}, "provider_unavailable"),
    (302, {}, "provider_redirect_refused"),
    (400, {}, "provider_request_rejected"),
    (200, b"<html>login</html>", "provider_response_invalid"),
    (200, {"error": {"code": "bad_key"}}, "provider_response_invalid"),
    (200, {}, "provider_response_invalid"),
    # Keep the real oversized body out of PYTEST_CURRENT_TEST on Windows.
    pytest.param(200, b"x" * (256 * 1024 + 1), "provider_response_too_large",
                 id="oversized-response"),
])
def test_http_errors_html_and_oversized_bodies_never_report_model_success(monkeypatch, status, body, error):
    monkeypatch.setenv("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT", "1")
    with provider_server(body, status) as (base, calls), httpx.Client(trust_env=False) as client:
        result = probe_endpoint(client, endpoint(base + "/v1"), "local-test-key")
    assert not result["ok"] and not result["model_valid"]
    assert result["error"] == error and result["http_status"] == status
    assert len(calls) == 1
    assert "local-test-key" not in json.dumps(result)
    if status not in {401, 403}:
        assert result["auth_valid"] is None


def test_probe_reuses_endpoint_pinning_and_refuses_official_key_to_private_dns(monkeypatch):
    calls = []
    monkeypatch.setenv("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT", "1")
    monkeypatch.setattr(endpoint_security, "_resolve", lambda *_: ("127.0.0.1",))
    with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r)), trust_env=False) as client:
        result = probe_endpoint(client, endpoint("https://api.deepseek.com", provider="deepseek"), "never-send")
    assert result["error"] == "endpoint_private_or_local_address_forbidden"
    assert calls == []


def test_deadline_returns_no_success_and_does_not_retry(monkeypatch):
    monkeypatch.setenv("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT", "1")
    with provider_server(reply("openai_chat_completions"), delay=0.3) as (base, calls), httpx.Client(trust_env=False) as client:
        started = time.monotonic()
        result = probe_endpoint(client, endpoint(base), "local-test-key", timeout=0.05)
        assert time.monotonic() - started < 1
        assert result["error"] == "provider_request_timeout"
        assert not result["ok"] and result["usage"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("identity,credential_source", [
    ("deepseek", "legacy_env"), ("deepseek_v4", "identity_env"), ("deepseek", "legacy_config"),
])
def test_configured_probe_uses_execution_credential_migration(monkeypatch, tmp_path, identity, credential_source):
    for name in list(__import__('os').environ):
        if name.endswith('_API_KEY'):
            monkeypatch.delenv(name, raising=False)
    cfg = {"_default_provider": identity, "_provider_inputs": {identity: {
        "provider": identity, "base_url": "https://api.deepseek.com/v1", "model_name": "deepseek-chat",
        "protocol_family": "openai_chat_completions", "service_preset": "deepseek"}}}
    if credential_source == "legacy_config":
        cfg["deepseek_v4"] = "test-migration-key"
    else:
        name = 'TIANGONG_DEEPSEEK_V4_API_KEY' if credential_source == 'legacy_env' else 'TIANGONG_DEEPSEEK_API_KEY'
        monkeypatch.setenv(name, 'test-migration-key')
    path = tmp_path / 'api_keys.json'
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr(peizhi, 'API_PEIZHI_LUJING', path)
    monkeypatch.setattr(http_kehuduan, 'L4_OPTIMIZATION_TRACE_PATH', tmp_path / 'trace.jsonl')
    monkeypatch.setattr(endpoint_security, '_resolve', lambda *_: ('1.1.1.1',))
    requests = []

    def send(request):
        requests.append(request)
        return httpx.Response(200, json={**reply('openai_chat_completions'), 'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})

    model = http_kehuduan.HttpKehuduan()
    model._kehuduan.close()
    model._kehuduan = httpx.Client(transport=httpx.MockTransport(send), trust_env=False)
    try:
        result = model.probe_connection()
    finally:
        model.guanbi()
    assert result['ok']
    assert requests[0].headers['Authorization'] == 'Bearer test-migration-key'
    assert requests[0].url.host == '1.1.1.1'
    assert requests[0].headers['Host'] == 'api.deepseek.com'
    assert requests[0].extensions['sni_hostname'] == 'api.deepseek.com'
    trace = json.loads((tmp_path / 'trace.jsonl').read_text())
    assert trace['purpose'] == 'connection_probe' and trace['usage']['prompt_tokens'] == 2
    assert 'test-migration-key' not in json.dumps(result) + json.dumps(trace)


def test_custom_origin_cannot_inherit_official_credential(monkeypatch, tmp_path):
    monkeypatch.setenv('TIANGONG_DEEPSEEK_API_KEY', 'must-not-inherit')
    cfg = {'_default_provider': 'deepseek', '_provider_inputs': {'deepseek': {
        'provider': 'deepseek', 'base_url': 'https://different.example/v1', 'model_name': 'model'}}}
    path = tmp_path / 'api_keys.json'
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr(peizhi, 'API_PEIZHI_LUJING', path)
    model = http_kehuduan.HttpKehuduan()
    try:
        assert model.probe_connection()['error'] == 'provider_api_key_missing'
    finally:
        model.guanbi()


def test_explicit_proxy_reaches_the_test_proxy_and_loopback_stays_direct(monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TIANGONG_HTTP_TRUST_ENV', '1')
    monkeypatch.setenv('TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT', '1')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost,::1')
    original_resolve = endpoint_security._resolve
    monkeypatch.setattr(endpoint_security, '_resolve', lambda host, port: ('1.1.1.1',) if host == 'proxy-target.example' else original_resolve(host, port))
    with provider_server(reply('openai_chat_completions')) as (proxy, calls):
        monkeypatch.setenv('HTTP_PROXY', proxy)
        model = http_kehuduan.HttpKehuduan()
        try:
            result = probe_endpoint(model._kehuduan, endpoint('http://proxy-target.example/v1'), 'local-test-key')
            assert result['ok']
            assert calls[0]['path'] == 'http://1.1.1.1/v1/chat/completions'
            assert calls[0]['headers']['Host'] == 'proxy-target.example'
            with provider_server(reply('openai_chat_completions')) as (local, local_calls):
                assert probe_endpoint(model._kehuduan, endpoint(local), 'local-test-key')['ok']
                assert len(local_calls) == 1
            assert len(calls) == 1
        finally:
            model.guanbi()


def test_backend_http_probe_requires_auth_and_saved_configuration(monkeypatch, tmp_path):
    from v3.duihua_qiaojie import _ChuliQi

    monkeypatch.setenv('TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT', '1')
    monkeypatch.setenv('TIANGONG_DESKTOP_TOKEN', 'internal-test-token')
    monkeypatch.setattr(peizhi, 'API_PEIZHI_LUJING', tmp_path / 'api_keys.json')
    monkeypatch.setattr(http_kehuduan, 'L4_OPTIMIZATION_TRACE_PATH', tmp_path / 'trace.jsonl')
    with provider_server(reply('openai_chat_completions')) as (provider, calls):
        base = provider + '/v1'
        peizhi.API_PEIZHI_LUJING.write_text(json.dumps({'_default_provider': 'custom', '_provider_inputs': {
            'custom': {'provider': 'custom', 'base_url': base, 'model_name': 'probe-model',
                       'service_preset': 'custom', 'protocol_family': 'openai_chat_completions'}}}))
        scope = endpoint_security.custom_scope_id(base)
        monkeypatch.setenv('TIANGONG_' + scope.upper() + '_API_KEY', 'local-test-key')
        server = ThreadingHTTPServer(('127.0.0.1', 0), _ChuliQi)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            with httpx.Client(trust_env=False) as client:
                url = f'http://127.0.0.1:{server.server_port}/api/v1/llm/probe'
                assert client.post(url, json={}).status_code == 401
                auth = {'X-Tiangong-Token': 'internal-test-token'}
                assert client.post(url, json={}, headers={**auth, 'Origin': 'https://untrusted.example'}).status_code == 403
                assert client.post(url, json={'base_url': 'https://wrong.example'}, headers=auth).status_code == 400
                assert client.post(url, content=b'{bad', headers=auth).status_code == 400
                assert calls == []
                response = client.post(url, json={}, headers=auth)
                assert response.status_code == 200 and response.json()['ok']
                assert len(calls) == 1 and calls[0]['path'] == '/v1/chat/completions'
                assert 'local-test-key' not in response.text
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
