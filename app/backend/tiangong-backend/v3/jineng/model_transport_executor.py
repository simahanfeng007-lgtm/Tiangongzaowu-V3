"""Network executor for the single P18.1 transport registry.

The executor owns HTTP mechanics only: request send, bounded transport retry,
SSE iteration, and wall-clock deadline. It stores no provider session and has
no task/effect authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..endpoint_security import EndpointBinding, validate_model_endpoint
from ..model_endpoint import ModelEndpointConfig
from ..model_protocol_contract import ProviderTurnEnvelope
from .model_transport_contract import StreamState
from .model_transport_registry import get_model_transport, parse_sse_data_line


@dataclass(slots=True)
class TransportExecutionError(RuntimeError):
    reason: str
    url: str
    http_status: int | None = None
    retry_count: int = 0
    response_preview: str = ""
    latency_ms: int = 0
    deadline_exceeded: bool = False

    def __str__(self) -> str:
        return self.reason


@dataclass(slots=True)
class TransportExecutionResult:
    turn: ProviderTurnEnvelope
    url: str
    http_status: int
    retry_count: int
    latency_ms: int


def _response_preview(response: Any) -> str:
    try:
        return str(response.text or "")[:240]
    except Exception:
        try:
            return response.read().decode("utf-8", errors="replace")[:240]
        except Exception:
            return ""


def _pinned_request(url: str, binding: EndpointBinding) -> tuple[str, dict[str, str], str]:
    """把请求钉扎到已验证 IP：连接层用 IP，Host 头与 TLS SNI 仍用原域名。

    安全性质：validate_model_endpoint 解析并拒绝过私网/回环地址，本次连接
    固定到该已验证 IP——校验与真实连接成为同一事实，DNS rebinding 在
    "校验解析"与"客户端二次解析"之间的 TOCTOU 窗口被消除。证书校验以
    SNI 域名为准，不受 IP 直连影响。
    """
    ips = tuple(binding.resolved_ips or ())
    if not ips:
        raise TransportExecutionError("endpoint_pinning_no_validated_ip", str(url))
    ip = str(ips[0]).strip()
    parts = urlsplit(str(url))
    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise TransportExecutionError("endpoint_pinning_url_invalid", str(url))
    scheme = parts.scheme.lower() or "https"
    port = parts.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = f"[{ip}]" if ":" in ip else ip
    if port is not None and not default_port:
        netloc = f"{netloc}:{port}"
    pinned_url = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    host_header = hostname if port is None or default_port else f"{hostname}:{port}"
    return pinned_url, {"Host": host_header}, hostname


def execute_streaming_turn(
    *,
    client: httpx.Client,
    endpoint: ModelEndpointConfig,
    api_key: str,
    canonical_payload: Mapping[str, Any],
    on_text_chunk: Callable[[str], None] | None = None,
    on_reasoning_chunk: Callable[[str], None] | None = None,
    retry_limit: int = 3,
    retry_sleep_seconds: float = 0.5,
    transient_status_codes: set[int] | None = None,
    max_wall_clock_seconds: float = 300.0,
) -> TransportExecutionResult:
    transport = get_model_transport(endpoint.protocol_family)
    transient = transient_status_codes or {408, 409, 425, 429, 500, 502, 503, 504}
    call_started = time.perf_counter()
    last_reason = "empty_response"
    request = transport.build_request(endpoint, api_key, canonical_payload)

    for attempt in range(1, max(1, int(retry_limit)) + 1):
        elapsed = time.perf_counter() - call_started
        if max_wall_clock_seconds > 0 and elapsed > max_wall_clock_seconds:
            raise TransportExecutionError(
                f"llm_call_wall_clock_deadline exceeded {max_wall_clock_seconds:g}s",
                request.url,
                retry_count=attempt - 1,
                latency_ms=round(elapsed * 1000),
                deadline_exceeded=True,
            )
        started = time.perf_counter()
        try:
            # Revalidate immediately before credential-bearing network release,
            # then pin this attempt's connection to the validated IP.  Every
            # retry re-resolves and re-pins; the credential and body only ever
            # travel to an address that passed the private/loopback checks.
            binding = validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=True)
            pinned_url, host_headers, sni_hostname = _pinned_request(request.url, binding)
            state = StreamState()
            pinned_http_request = client.build_request(
                "POST",
                pinned_url,
                json=request.payload,
                headers={**request.headers, **host_headers},
                extensions={"sni_hostname": sni_hostname},
            )
            # ``Client.send(..., stream=True)`` returns an ``httpx.Response``;
            # unlike ``Client.stream(...)``, that object is not a context
            # manager.  Own its lifetime explicitly so every success, retry,
            # and exception path releases the streaming connection.
            response = client.send(pinned_http_request, stream=True)
            try:
                status = int(response.status_code)
                if status in transient and attempt < retry_limit:
                    last_reason = f"HTTP {status}"
                    time.sleep(retry_sleep_seconds * attempt)
                    continue
                response.raise_for_status()
                for raw_line in response.iter_lines():
                    if max_wall_clock_seconds > 0 and (time.perf_counter() - call_started) > max_wall_clock_seconds:
                        raise httpx.TimeoutException(
                            f"llm_call_wall_clock_deadline exceeded {max_wall_clock_seconds:g}s"
                        )
                    event = parse_sse_data_line(raw_line)
                    if event is None:
                        continue
                    text, reasoning = transport.consume_stream_event(state, event)
                    if text and on_text_chunk:
                        on_text_chunk(text)
                    if reasoning and on_reasoning_chunk:
                        on_reasoning_chunk(reasoning)
            finally:
                response.close()
            turn = transport.finalize_turn(endpoint, state)
            latency = round((time.perf_counter() - started) * 1000)
            return TransportExecutionResult(
                turn=turn,
                url=request.url,
                http_status=status,
                retry_count=attempt - 1,
                latency_ms=latency,
            )
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code)
            last_reason = f"HTTP {status}"
            preview = _response_preview(exc.response)
            if status in transient and attempt < retry_limit:
                time.sleep(retry_sleep_seconds * attempt)
                continue
            raise TransportExecutionError(
                last_reason,
                request.url,
                http_status=status,
                retry_count=attempt - 1,
                response_preview=preview,
                latency_ms=round((time.perf_counter() - started) * 1000),
            ) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            elapsed = time.perf_counter() - call_started
            last_reason = str(exc)
            deadline = max_wall_clock_seconds > 0 and elapsed > max_wall_clock_seconds
            if not deadline and attempt < retry_limit:
                time.sleep(retry_sleep_seconds * attempt)
                continue
            raise TransportExecutionError(
                last_reason,
                request.url,
                retry_count=attempt - 1,
                latency_ms=round(elapsed * 1000),
                deadline_exceeded=deadline,
            ) from exc
        except TransportExecutionError:
            raise
        except Exception as exc:
            last_reason = str(exc)
            if attempt < retry_limit:
                time.sleep(retry_sleep_seconds * attempt)
                continue
            raise TransportExecutionError(
                last_reason,
                request.url,
                retry_count=attempt - 1,
                latency_ms=round((time.perf_counter() - started) * 1000),
            ) from exc

    raise TransportExecutionError(
        last_reason,
        request.url,
        retry_count=max(0, retry_limit - 1),
        latency_ms=round((time.perf_counter() - call_started) * 1000),
    )
