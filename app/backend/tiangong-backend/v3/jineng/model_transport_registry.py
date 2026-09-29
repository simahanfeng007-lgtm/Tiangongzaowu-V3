"""Single P18.1 protocol transport registry and endpoint probe."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping

from ..model_endpoint import ModelEndpointConfig, ProtocolFamily
from .model_transport_anthropic import AnthropicMessagesTransport
from .model_transport_openai_chat import OpenAIChatTransport
from .model_transport_openai_responses import OpenAIResponsesTransport


_TRANSPORTS = {
    ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: OpenAIChatTransport(),
    ProtocolFamily.OPENAI_RESPONSES.value: OpenAIResponsesTransport(),
    ProtocolFamily.ANTHROPIC_MESSAGES.value: AnthropicMessagesTransport(),
}


def get_model_transport(protocol_family: str):
    protocol = str(protocol_family or "").strip()
    try:
        return _TRANSPORTS[protocol]
    except KeyError as exc:
        raise ValueError(f"unsupported model protocol transport: {protocol}") from exc


def registered_protocol_families() -> tuple[str, ...]:
    return tuple(_TRANSPORTS.keys())


def parse_sse_data_line(raw_line: str) -> dict[str, Any] | None:
    line = str(raw_line or "").strip()
    if not line or line.startswith(":") or line.startswith("event:") or line.startswith("id:"):
        return None
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data:
        return None
    if data == "[DONE]":
        return {"__stream_done": True}
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_stream_json") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid_stream_event")
    return parsed


def _probe_response_valid(protocol: str, body: Any) -> bool:
    if not isinstance(body, dict) or body.get("error"):
        return False
    if protocol == ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value:
        choices = body.get("choices")
        return bool(isinstance(choices, list) and choices and
                    isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict))
    if protocol == ProtocolFamily.OPENAI_RESPONSES.value:
        return (body.get("object") == "response" and isinstance(body.get("output"), list)
                and body.get("status") in {"completed", "incomplete"})
    return body.get("type") == "message" and isinstance(body.get("content"), list)


def _probe_usage(body: Any) -> dict[str, Any] | None:
    raw = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(raw, dict):
        return None
    names = {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
             "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cached_input_tokens",
             "cache_read_input_tokens", "cache_creation_input_tokens", "cached_tokens",
             "cache_read_tokens", "reasoning_tokens"}
    clean = {key: value for key, value in raw.items()
             if key in names and type(value) is int and 0 <= value <= 2**53 - 1}
    for name in ("prompt_tokens_details", "completion_tokens_details", "input_tokens_details"):
        if isinstance(raw.get(name), dict):
            clean[name] = {key: value for key, value in raw[name].items()
                           if key in names and type(value) is int and 0 <= value <= 2**53 - 1}
    return clean or None


def probe_endpoint(client: Any, endpoint: ModelEndpointConfig, api_key: str, *, timeout: float = 20.0,
                   on_provider_request_id: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One bounded request using the execution transport and endpoint authority.

    A provider may bill this text request. It does not prove streaming, tools,
    task quality or completion; failed/unknown usage stays separate from zero.
    """
    import httpx
    from ..endpoint_security import EndpointSecurityError, validate_model_endpoint
    from .model_call_lifecycle import ModelCallStopped, run_model_call
    from .model_transport_executor import (_bounded_http_error_category, _pinned_request,
                                           _provider_error_category, _provider_request_id_from_headers)

    transport = get_model_transport(endpoint.protocol_family)
    payload = transport.probe_payload(endpoint)
    url = transport.build_url(endpoint)
    headers = transport.build_headers(endpoint, api_key)
    payload["stream"] = False
    started = time.monotonic()
    result = {
        "ok": False,
        "provider_identity": endpoint.provider_identity,
        "service_preset": endpoint.service_preset,
        "protocol_family": endpoint.protocol_family,
        "http_status": None,
        "usage": None,
        "endpoint_reachable": False,
        "auth_valid": None,
        "protocol_valid": False,
        "model_valid": False,
        "streaming_supported": False,
        "native_tools_supported": False,
        "reasoning_control_supported": False,
        "parallel_tool_calls_supported": False,
        "structured_output_supported": False,
        "continuation_supported": False,
        "probe_evidence": {
            "protocol_family": endpoint.protocol_family,
            "http_status": None,
            "latency_ms": None,
        },
    }

    def request(lifecycle):
        binding = validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=True)
        lifecycle.check()
        pinned_url, host_headers, sni = _pinned_request(url, binding)
        request = client.build_request(
            "POST", pinned_url, headers={**headers, **host_headers}, json=payload,
            extensions={"sni_hostname": sni}, timeout=lifecycle.remaining,
        )
        lifecycle.check()
        response = client.send(request, stream=True, follow_redirects=False)
        try:
            with lifecycle.response(response.close):
                provider_request_id = _provider_request_id_from_headers(response.headers, api_key)
                if provider_request_id and on_provider_request_id is not None:
                    try:
                        on_provider_request_id(provider_request_id)
                    except Exception:
                        pass  # optional diagnostics cannot replace a known HTTP result
                if response.status_code >= 400:
                    category = _bounded_http_error_category(response, lifecycle.remaining)
                    return response.status_code, None, "", category
                if 300 <= response.status_code < 400:
                    return response.status_code, None, "", "unknown"
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    lifecycle.check()
                    size += len(chunk)
                    if size > 256 * 1024:
                        return response.status_code, None, "provider_response_too_large", "unknown"
                    chunks.append(chunk)
                lifecycle.check()
                try:
                    body = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    return response.status_code, None, "provider_response_invalid", "unknown"
                return response.status_code, body, "", ""
        finally:
            response.close()

    try:
        status, body, body_error, error_category = run_model_call(
            request, seconds=min(20.0, max(0.01, timeout)), child=True)
        result["http_status"] = status
        result["probe_evidence"]["http_status"] = status
        result["endpoint_reachable"] = status > 0
        result["usage"] = _probe_usage(body)
        valid = 200 <= status < 300 and not body_error and _probe_response_valid(endpoint.protocol_family, body)
        result.update(ok=bool(valid), model_valid=bool(valid), protocol_valid=bool(valid),
                      configured_model_available=True if valid else None,
                      auth_valid=True if valid else False if status in {401, 403} else None)
        if not valid:
            # The body is only consulted for a finite classification. Never
            # return any provider-controlled code or message from the probe.
            result["provider_error_category"] = (error_category or _provider_error_category(
                body if isinstance(body, dict) and len(json.dumps(body).encode("utf-8")) <= 4096 else None,
                status,
            ))
            result["error"] = (
                "provider_auth_failed" if status == 401 else
                "provider_permission_denied" if status == 403 else
                "provider_endpoint_or_model_not_found" if status == 404 else
                "provider_rate_limited_or_quota_exhausted" if status == 429 else
                "provider_unavailable" if status >= 500 else
                "provider_redirect_refused" if 300 <= status < 400 else
                "provider_request_rejected" if status >= 400 else
                body_error or "provider_response_invalid"
            )
    except EndpointSecurityError as exc:
        result["error"] = str(exc)
    except (httpx.TimeoutException, ModelCallStopped):
        result["error"] = "provider_request_timeout"
    except Exception as exc:
        result["error"] = "provider_transport_failed"
        result["error_type"] = type(exc).__name__
    finally:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["probe_evidence"]["latency_ms"] = result["latency_ms"]
    return result


def native_tool_probe_payload(endpoint: ModelEndpointConfig, tool_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return protocol-native low-cost payload for an explicit tool probe."""
    protocol = endpoint.protocol_family
    if protocol == ProtocolFamily.OPENAI_RESPONSES.value:
        return {
            "model": endpoint.model_name,
            "input": "Call the probe tool once with value=1.",
            "tools": [dict(tool_schema)],
            "max_output_tokens": 64,
            "store": False,
        }
    if protocol == ProtocolFamily.ANTHROPIC_MESSAGES.value:
        return {
            "model": endpoint.model_name,
            "messages": [{"role": "user", "content": "Call the probe tool once with value=1."}],
            "tools": [dict(tool_schema)],
            "max_tokens": 64,
        }
    return {
        "model": endpoint.model_name,
        "messages": [{"role": "user", "content": "Call the probe tool once with value=1."}],
        "tools": [dict(tool_schema)],
        "max_tokens": 64,
    }
