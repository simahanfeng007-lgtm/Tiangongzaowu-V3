"""Single P18.1 protocol transport registry and endpoint probe."""
from __future__ import annotations

import json
import time
from typing import Any, Mapping

from ..model_endpoint import ModelEndpointConfig, ProtocolFamily
from .model_transport_anthropic import AnthropicMessagesTransport
from .model_transport_contract import StreamState
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
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def probe_endpoint(client: Any, endpoint: ModelEndpointConfig, api_key: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Protocol-specific, side-effect-free capability probe.

    The probe never upgrades unknown capabilities unless the endpoint actually
    accepts the tested shape. Tool support remains false until a dedicated tool
    probe succeeds; a chat/text success alone does not imply function calling.
    """
    transport = get_model_transport(endpoint.protocol_family)
    payload = transport.probe_payload(endpoint)
    request = transport.build_request(endpoint, api_key, payload)
    request.payload["stream"] = False
    started = time.monotonic()
    result = {
        "endpoint_reachable": False,
        "auth_valid": False,
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
            "url": request.url,
            "http_status": None,
            "latency_ms": None,
        },
    }
    try:
        response = client.post(request.url, headers=request.headers, json=request.payload, timeout=timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        result["probe_evidence"]["http_status"] = status
        result["endpoint_reachable"] = status > 0
        result["auth_valid"] = status not in {401, 403}
        result["protocol_valid"] = status not in {404, 405, 415, 422} and status < 500
        result["model_valid"] = status < 400
        result["streaming_supported"] = status < 400
        if status >= 400:
            result["probe_evidence"]["error_preview"] = str(getattr(response, "text", ""))[:500]
    except Exception as exc:
        result["probe_evidence"]["error"] = f"{type(exc).__name__}:{str(exc)[:240]}"
    finally:
        result["probe_evidence"]["latency_ms"] = int((time.monotonic() - started) * 1000)
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
