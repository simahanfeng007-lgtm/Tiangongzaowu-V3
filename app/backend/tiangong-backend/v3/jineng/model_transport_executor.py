"""Network executor for the single P18.1 transport registry.

The executor owns HTTP mechanics only: request send, bounded transport retry,
SSE iteration, and wall-clock deadline. It stores no provider session and has
no task/effect authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping

import httpx

from ..endpoint_security import validate_model_endpoint
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
            # Revalidate immediately before credential-bearing network release.
            validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=True)
            state = StreamState()
            with client.stream("POST", request.url, json=request.payload, headers=request.headers) as response:
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
