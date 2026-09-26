"""Network executor for the single P18.1 transport registry.

The executor owns HTTP mechanics only: request send, bounded transport retry,
SSE iteration, and wall-clock deadline. It stores no provider session and has
no task/effect authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..endpoint_security import EndpointBinding, validate_model_endpoint
from ..model_endpoint import ModelEndpointConfig
from ..model_protocol_contract import ProviderTurnEnvelope
from .model_transport_contract import StreamState
from .model_transport_registry import get_model_transport, parse_sse_data_line
from .model_call_lifecycle import ModelCallStopped, model_call_scope


@dataclass(slots=True)
class TransportExecutionError(RuntimeError):
    reason: str
    url: str
    http_status: int | None = None
    retry_count: int = 0
    response_preview: str = ""
    latency_ms: int = 0
    deadline_exceeded: bool = False
    error_code: str = "transport_error"
    response_metrics: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.reason


@dataclass(slots=True)
class TransportExecutionResult:
    turn: ProviderTurnEnvelope
    url: str
    http_status: int
    retry_count: int
    latency_ms: int
    output_repaired: bool = False


def execute_streaming_turn_with_repair(**kwargs) -> TransportExecutionResult:
    """At most two format repairs under one deadline and transient retry budget.

    A rejected envelope never left this boundary, so none of its tools ran.
    Repairs use the current native tool schema; no JSON is salvaged or executed
    locally, and previously executed turns are never repeated here.
    """
    started = time.perf_counter()
    on_repair = kwargs.pop("on_repair", None)
    allow_output_repair = kwargs.pop("allow_output_repair", True)
    kwargs["retry_uncommitted_stream"] = True
    kwargs["on_attempt_reset"] = on_repair
    retry_limit = max(1, min(3, int(kwargs.get("retry_limit", 3))))
    network_retries = 0
    rejected_attempts = []
    payload = dict(kwargs["canonical_payload"])
    with model_call_scope(float(kwargs.get("max_wall_clock_seconds", 300.0))) as lifecycle:
        for repair_round in range(3):
            lifecycle.check()
            try:
                result = execute_streaming_turn(**{**kwargs, "canonical_payload": payload,
                    "retry_limit": max(1, retry_limit - network_retries),
                    "max_wall_clock_seconds": lifecycle.remaining})
            except TransportExecutionError as exc:
                if (not allow_output_repair or repair_round == 2
                        or exc.error_code not in {"output_truncated", "invalid_tool_arguments"}):
                    if repair_round:
                        exc.retry_count += network_retries
                        exc.latency_ms = int((time.perf_counter() - started) * 1000)
                        exc.response_metrics.update(rejected_attempts=rejected_attempts,
                                                    repair_performed=True, repair_count=repair_round)
                    raise
                network_retries += exc.retry_count
                rejected_attempts.extend(exc.response_metrics.get("attempts", []))
                lifecycle.check()
                if on_repair is not None:
                    lifecycle.guard(on_repair)()
                # The old single-action example contradicted the current
                # composition tool's input schema. Keep the current schema and
                # native history, with only a bounded format correction added.
                payload = dict(kwargs["canonical_payload"])
                payload["__turn_repair_instruction"] = (
                    "刚才的模型响应被截断或工具参数不是完整 JSON，整轮工具均未执行。"
                    "请只返回下一步的一个完整工具调用，严格遵循当前 tools 声明的输入 schema，"
                    "不要改成旧版单动作入口或添加未声明字段。长文件分段，先执行一段并观察回执。"
                    "参数必须是对象；检查每层大括号和方括号配对，禁止在完整 JSON 后附加任何字符。"
                    "使用标准 JSON 双引号，字符串中的双引号和换行必须转义。"
                    "不要重述计划。解析诊断：" + exc.reason
                )
                continue
            result.retry_count += network_retries
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.output_repaired = repair_round > 0
            if repair_round:
                result.turn.stream_metadata.update(rejected_attempts=rejected_attempts,
                                                   repair_count=repair_round)
            return result


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


def _validate_complete_stream(state: StreamState, url: str) -> None:
    if state.finish_reason in {"length", "max_tokens", "incomplete"}:
        raise TransportExecutionError("model_output_truncated", url, error_code="output_truncated")
    if state.finish_reason not in {"stop", "tool_calls", "function_call", "completed", "end_turn", "tool_use", "stop_sequence"}:
        code = "provider_error" if state.finish_reason else "stream_unexpected_eof"
        raise TransportExecutionError(code, url, error_code=code)
    for item in state.tool_items.values():
        try:
            arguments = json.loads(item.get("arguments_text") or "{}")
            # Some OpenAI-compatible endpoints serialize the complete object
            # twice. Decode only complete JSON, with a fixed bound; never mend
            # fragments or infer missing arguments.
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if isinstance(arguments, dict) and isinstance(arguments.get("args"), str):
                arguments["args"] = json.loads(arguments["args"])
        except (TypeError, ValueError) as exc:
            detail = (f":line={exc.lineno}:column={exc.colno}:parser={exc.msg}"
                      if isinstance(exc, json.JSONDecodeError) else "")
            raise TransportExecutionError("invalid_tool_arguments" + detail, url, error_code="invalid_tool_arguments") from exc
        if not isinstance(arguments, dict) or not item.get("name") or not item.get("id"):
            raise TransportExecutionError("invalid_tool_call:missing_object_name_or_id", url, error_code="invalid_tool_arguments")
        if item.get("name") == "omni_body" and "args" in arguments and not isinstance(arguments["args"], dict):
            raise TransportExecutionError("invalid_tool_arguments:args_must_be_object", url, error_code="invalid_tool_arguments")
        item["arguments_text"] = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    if state.finish_reason in {"tool_calls", "function_call", "tool_use"} and not state.tool_items:
        raise TransportExecutionError("missing_tool_call", url, error_code="invalid_tool_arguments")
    if not state.tool_items and not state.visible_text.strip():
        raise TransportExecutionError("empty_model_response", url, error_code="empty_response")


def execute_streaming_turn(
    *,
    client: httpx.Client,
    endpoint: ModelEndpointConfig,
    api_key: str,
    canonical_payload: Mapping[str, Any],
    on_text_chunk: Callable[[str], None] | None = None,
    on_reasoning_chunk: Callable[[str], None] | None = None,
    on_request_built: Callable[[Mapping[str, Any]], None] | None = None,
    on_attempt_reset: Callable[[], None] | None = None,
    retry_uncommitted_stream: bool = False,
    retry_limit: int = 3,
    retry_sleep_seconds: float = 0.5,
    transient_status_codes: set[int] | None = None,
    max_wall_clock_seconds: float = 300.0,
) -> TransportExecutionResult:
    transport = get_model_transport(endpoint.protocol_family)
    canonical = dict(canonical_payload)
    repair_instruction = canonical.pop("__turn_repair_instruction", None)
    request = transport.build_request(endpoint, api_key, canonical)
    if repair_instruction:
        field_name = "input" if endpoint.protocol_family == "openai_responses" else "messages"
        request.payload[field_name] = [*request.payload.get(field_name, []),
            {"role": "user", "content": str(repair_instruction)}]
    transaction = canonical.get("__append_context")
    if transaction is not None:
        transaction.observe_wire(request.payload)
    transient = transient_status_codes or {408, 409, 425, 429, 500, 502, 503, 504}
    attempts = max(1, min(3, int(retry_limit)))
    call_started = time.perf_counter()
    attempt = 1
    status = None
    attempt_metrics: list[dict[str, Any]] = []
    try:
        with model_call_scope(max_wall_clock_seconds) as lifecycle:
            emit_text = lifecycle.guard(on_text_chunk)
            emit_reasoning = lifecycle.guard(on_reasoning_chunk)
            for attempt in range(1, attempts + 1):
                lifecycle.check()
                state = StreamState()
                progress = False
                status = None
                attempt_started = time.perf_counter()
                telemetry: dict[str, Any] = {"attempt": attempt, "first_packet_ms": None,
                    "first_progress_ms": None, "last_progress_ms": None, "max_progress_gap_ms": 0,
                    "text_chunks": 0, "reasoning_chunks": 0, "tool_argument_bytes": 0}
                attempt_metrics.append(telemetry)
                try:
                    # Revalidate and pin each credential-bearing connection.
                    binding = validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=True)
                    lifecycle.check()
                    pinned_url, host_headers, sni_hostname = _pinned_request(request.url, binding)
                    if on_request_built is not None:
                        lifecycle.guard(on_request_built)(request.payload)
                    remaining = lifecycle.remaining
                    pinned_http_request = client.build_request(
                        "POST", pinned_url, json=request.payload,
                        headers={**request.headers, **host_headers},
                        extensions={"sni_hostname": sni_hostname},
                        timeout=httpx.Timeout(connect=min(15.0, remaining), pool=min(5.0, remaining),
                                              write=min(120.0, remaining), read=min(120.0, remaining)),
                    )
                    response = client.send(pinned_http_request, stream=True)
                    try:
                        with lifecycle.response(response.close):
                            status = int(response.status_code)
                            response.raise_for_status()
                            first_progress_ms = None
                            last_progress_ms = None
                            for raw_line in response.iter_lines():
                                lifecycle.check()
                                elapsed_ms = round((time.perf_counter() - attempt_started) * 1000)
                                if telemetry["first_packet_ms"] is None:
                                    telemetry["first_packet_ms"] = elapsed_ms
                                event = parse_sse_data_line(raw_line)
                                if event is None:
                                    continue
                                if event.get("__stream_done"):
                                    state.metadata["stream_terminal"] = "done"
                                    break
                                if event.get("error") or event.get("type") in {"error", "response.failed"}:
                                    raise TransportExecutionError("provider_stream_error", request.url, error_code="provider_error")
                                before = (len(state.visible_parts), len(state.reasoning_parts),
                                          sum(len(str(i.get("arguments_text") or "")) for i in state.tool_items.values()),
                                          len(state.tool_items))
                                text, reasoning = transport.consume_stream_event(state, event)
                                after = (len(state.visible_parts), len(state.reasoning_parts),
                                         sum(len(str(i.get("arguments_text") or "")) for i in state.tool_items.values()),
                                         len(state.tool_items))
                                if before != after:
                                    progress = True
                                    last_progress_ms = round((time.perf_counter() - call_started) * 1000)
                                    if first_progress_ms is None:
                                        first_progress_ms = last_progress_ms
                                    previous = telemetry["last_progress_ms"]
                                    telemetry["max_progress_gap_ms"] = max(telemetry["max_progress_gap_ms"],
                                        elapsed_ms - (previous if previous is not None else 0))
                                    if telemetry["first_progress_ms"] is None:
                                        telemetry["first_progress_ms"] = elapsed_ms
                                    telemetry["last_progress_ms"] = elapsed_ms
                                telemetry["text_chunks"] += int(bool(text))
                                telemetry["reasoning_chunks"] += int(bool(reasoning))
                                telemetry["tool_argument_bytes"] = sum(len(str(i.get("arguments_text") or "").encode("utf-8"))
                                    for i in state.tool_items.values())
                                if text and emit_text:
                                    emit_text(text)
                                if reasoning and emit_reasoning:
                                    emit_reasoning(reasoning)
                                if event.get("type") in {"message_stop", "response.completed", "response.incomplete"}:
                                    state.metadata["stream_terminal"] = str(event["type"])
                                    break
                            state.metadata.update(first_progress_ms=first_progress_ms, last_progress_ms=last_progress_ms)
                            lifecycle.check()
                    finally:
                        response.close()
                        telemetry["elapsed_ms"] = round((time.perf_counter() - attempt_started) * 1000)
                        previous = telemetry["last_progress_ms"]
                        telemetry["max_progress_gap_ms"] = max(telemetry["max_progress_gap_ms"],
                            telemetry["elapsed_ms"] - (previous if previous is not None else 0))
                        telemetry["finish_reason"] = state.finish_reason
                        telemetry["event_count"] = state.raw_events
                        # Usage can arrive before a length/error terminal event.
                        # Keep every attempt, including rejected or partial ones.
                        telemetry["usage"] = dict(state.usage or {})
                except httpx.HTTPStatusError as exc:
                    status = int(exc.response.status_code)
                    lifecycle.check()
                    if status in transient and attempt < attempts:
                        lifecycle.wait(retry_sleep_seconds * attempt)
                        continue
                    raise TransportExecutionError(f"HTTP {status}", request.url, http_status=status,
                                                  error_code="http_error") from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    lifecycle.check()
                    # Only the wrapper that owns an uncommitted complete turn
                    # may retry after deltas. No tool has left this boundary.
                    if (not progress or retry_uncommitted_stream) and attempt < attempts:
                        if on_attempt_reset is not None:
                            lifecycle.guard(on_attempt_reset)()
                        telemetry["retry_reason"] = "transport_error"
                        lifecycle.wait(retry_sleep_seconds * attempt)
                        continue
                    raise TransportExecutionError(str(exc), request.url, http_status=status) from exc
                if not state.finish_reason and retry_uncommitted_stream and attempt < attempts:
                    if on_attempt_reset is not None:
                        lifecycle.guard(on_attempt_reset)()
                    telemetry["retry_reason"] = "stream_unexpected_eof"
                    lifecycle.wait(retry_sleep_seconds * attempt)
                    continue
                _validate_complete_stream(state, request.url)
                turn = transport.finalize_turn(endpoint, state)
                turn.stream_metadata.update(state.metadata)
                turn.stream_metadata["attempts"] = attempt_metrics
                lifecycle.check()
                return TransportExecutionResult(turn=turn, url=request.url, http_status=status,
                    retry_count=attempt - 1, latency_ms=round((time.perf_counter() - call_started) * 1000))
    except ModelCallStopped as exc:
        raise TransportExecutionError(
            "llm_call_wall_clock_deadline" if exc.reason == "deadline_exceeded" else exc.reason,
            request.url, http_status=status, retry_count=attempt - 1,
            latency_ms=round((time.perf_counter() - call_started) * 1000),
            deadline_exceeded=exc.reason == "deadline_exceeded", error_code=exc.reason,
            response_metrics={"attempts": attempt_metrics},
        ) from exc
    except TransportExecutionError as exc:
        exc.retry_count = attempt - 1
        exc.latency_ms = round((time.perf_counter() - call_started) * 1000)
        exc.response_metrics = {"attempts": attempt_metrics}
        raise
    except Exception as exc:
        raise TransportExecutionError(str(exc), request.url, http_status=status,
            retry_count=attempt - 1, latency_ms=round((time.perf_counter() - call_started) * 1000),
            error_code="invalid_stream" if isinstance(exc, ValueError) else "transport_error",
            response_metrics={"attempts": attempt_metrics}) from exc
