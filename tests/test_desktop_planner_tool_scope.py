"""Planner isolation at the real HTTP client/transport boundary; no live API."""
import json

import httpx

from total_gateway.desktop_composition import request_model_proposal
from v3 import endpoint_security
from v3.jineng import http_kehuduan as client_module
from v3.model_endpoint import ModelEndpointConfig


def test_real_planner_wire_has_no_legacy_tools_and_restores_outer_scope(tmp_path, monkeypatch):
    endpoint = ModelEndpointConfig(
        service_preset="mimo", provider_identity="mimo",
        protocol_family="openai_chat_completions",
        base_url="https://model.example.test/v1", model_name="mimo-v2.6-pro",
        credential_scope="fixture", reasoning_mode="off", endpoint_overrides={},
        optimization_family="mimo", config_fingerprint="a" * 64,
    )
    monkeypatch.setattr(client_module, "duqu_model_endpoint_config", lambda _: endpoint)
    monkeypatch.setattr(client_module, "duqu_moren_provider", lambda _: "mimo")
    monkeypatch.setattr(client_module, "duqu_endpoint_api_miyao", lambda *_: "test-only-noncredential")
    monkeypatch.setattr(endpoint_security, "_resolve", lambda *_: ("93.184.216.34",))
    monkeypatch.setattr(client_module, "duqu_model_reasoning_config", lambda *_a, **_kw: {"supported": False})
    monkeypatch.setattr(client_module, "_learned_skill_context", lambda: "")
    monkeypatch.setattr(client_module, "L4_OPTIMIZATION_TRACE_PATH", tmp_path / "trace.jsonl")
    tools = [
        {"name": name, "description": "Legacy tool fixture", "parameters": {
            "type": "object", "properties": {}, "additionalProperties": False,
        }}
        for name in ("legacy_probe", "other_legacy_probe")
    ]
    monkeypatch.setattr(client_module.GUGE, "suoyou_gongju", lambda: tools)
    expected = {"proposal": {}, "invocations": {}}
    captured = []

    def respond(request):
        # The real transport performed endpoint pinning and native serialization.
        assert request.url.host == "93.184.216.34"
        assert request.url.path == "/v1/chat/completions"
        captured.append(json.loads(request.content))
        chunks = [
            {"id": "fixture", "model": endpoint.model_name, "choices": [{
                "index": 0, "delta": {"content": json.dumps(expected)}, "finish_reason": None,
            }]},
            {"id": "fixture", "model": endpoint.model_name, "choices": [{
                "index": 0, "delta": {}, "finish_reason": "stop",
            }]},
        ]
        content = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=(content + "data: [DONE]\n\n").encode())

    http = client_module.HttpKehuduan(moren_provider="mimo")
    http._kehuduan.close()
    http._kehuduan = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with http.scoped_tools(allowed_tool_names={"legacy_probe"}):
            assert json.loads(http.llm_diaoyong("ordinary", "before")) == expected
            assert request_model_proposal(http, "planner", "plan") == expected
            assert json.loads(http.llm_diaoyong("ordinary", "after")) == expected
        # Leaving both nested scopes restores the original unrestricted client.
        assert json.loads(http.llm_diaoyong("ordinary", "outside")) == expected
    finally:
        http._kehuduan.close()

    assert len(captured) == 4
    for payload in captured:
        assert payload["model"] == "mimo-v2.6-pro"
        assert payload["stream"] is True
    before, planner, after, outside = captured
    assert "tools" not in planner and "tool_choice" not in planner
    for payload in (before, after):
        assert [tool["function"]["name"] for tool in payload["tools"]] == ["legacy_probe"]
        assert payload["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in outside["tools"]] == [
        "legacy_probe", "other_legacy_probe",
    ]
    assert outside["tool_choice"] == "auto"
