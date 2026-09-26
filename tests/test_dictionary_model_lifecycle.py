"""Fault injection at the production transport boundary; no provider calls."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from v3.endpoint_security import EndpointBinding
from v3.jineng import model_transport_executor as executor
from v3.jineng.model_call_lifecycle import ModelCallStopped, run_model_call
from v3.model_endpoint import ModelEndpointConfig
from v3.model_protocol_contract import ProviderTurnEnvelope, model_turn_failure


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setattr(executor, "validate_model_endpoint", lambda *a, **k: EndpointBinding(
        provider_id="custom", base_url="https://example.test/v1", origin="https://example.test",
        host="example.test", port=443, official=False, custom_scope="test", resolved_ips=("203.0.113.10",),
    ))
    return ModelEndpointConfig(service_preset="custom", provider_identity="custom",
        protocol_family="openai_chat_completions", base_url="https://example.test/v1", model_name="test",
        credential_scope="test", reasoning_mode="", endpoint_overrides={}, optimization_family="gpt_5_6",
        config_fingerprint="f" * 64)


def event(delta=None, finish=None, **extra):
    return "data: " + json.dumps({"choices": [{"delta": delta or {}, "finish_reason": finish}], **extra})


class Response:
    status_code = 200

    def __init__(self, lines):
        self.lines = lines
        self.closed = threading.Event()

    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield from self.lines()

    def close(self):
        self.closed.set()


class Client:
    def __init__(self, lines):
        self.response = Response(lines)
        self.sent = 0

    def build_request(self, *a, **k):
        return object()

    def send(self, *a, **k):
        self.sent += 1
        return self.response


def execute(endpoint, client, **kw):
    return executor.execute_streaming_turn(client=client, endpoint=endpoint, api_key="test",
        canonical_payload={"messages": [{"role": "user", "content": "test"}]}, **kw)


def test_done_stops_reading_after_final_usage(endpoint):
    def lines():
        yield ": ping"
        yield event({"content": "OK"}, "stop")
        yield 'data: {"choices": [], "usage": {"total_tokens": 3}}'
        yield "data: [DONE]"
        raise AssertionError("must not wait for socket EOF after DONE")
    client = Client(lines)
    result = execute(endpoint, client)
    assert result.turn.visible_text == "OK"
    assert result.turn.usage["total_tokens"] == 3
    assert client.response.closed.is_set()


def test_valid_tool_generation_after_215_seconds_uses_shared_300_second_deadline(endpoint, monkeypatch):
    from types import SimpleNamespace
    from v3.jineng import model_call_lifecycle
    elapsed = [0.0]
    monkeypatch.setattr(model_call_lifecycle, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    def lines():
        yield event({"reasoning_content": "working"})
        elapsed[0] = 215.0
        yield event({"tool_calls": [{"index": 0, "id": "call1", "function": {
            "name": "omni_body", "arguments": '{"action":"file.read","target":"input.txt"}'}}]}, "tool_calls")
        yield "data: [DONE]"
    client = Client(lines)
    result = run_model_call(lambda lifecycle: execute(endpoint, client), seconds=300)
    assert len(result.turn.tool_calls) == 1
    assert client.sent == 1


@pytest.mark.parametrize("lines,code", [
    ([event({"content": "partial"})], "stream_unexpected_eof"),
    (['data: {"error": {"message": "failed"}}'], "provider_error"),
    (["data: {broken"], "invalid_stream"),
    ([event({"content": "partial"}, "length"), "data: [DONE]"], "output_truncated"),
    ([event({"tool_calls": [{"index": 0, "id": "call1", "function": {"name": "omni_body", "arguments": '{"action":'}}]}, "tool_calls"), "data: [DONE]"], "invalid_tool_arguments"),
])
def test_incomplete_stream_cannot_return_executable_turn(endpoint, lines, code):
    client = Client(lambda: iter(lines))
    with pytest.raises(executor.TransportExecutionError) as caught:
        execute(endpoint, client)
    assert caught.value.error_code == code
    assert client.sent == 1


def test_partial_tool_arguments_prevent_blind_transport_retry(endpoint):
    def lines():
        yield event({"tool_calls": [{"index": 0, "id": "call1", "function": {"name": "omni_body", "arguments": '{"action":'}}]})
        raise httpx.ReadError("dropped")
    client = Client(lines)
    with pytest.raises(executor.TransportExecutionError):
        execute(endpoint, client)
    assert client.sent == 1


def test_rejected_stream_preserves_reported_usage_without_private_reasoning(endpoint):
    usage = {"prompt_tokens": 1000, "completion_tokens": 8192,
             "prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 360}
    client = Client(lambda: iter([
        event({"reasoning_content": "private reasoning must never be in metrics"}, "length"),
        'data: ' + json.dumps({"choices": [], "usage": usage}), "data: [DONE]",
    ]))
    with pytest.raises(executor.TransportExecutionError) as caught:
        execute(endpoint, client)
    assert caught.value.error_code == "output_truncated"
    assert caught.value.response_metrics["attempts"][0]["usage"] == usage
    assert "private reasoning" not in json.dumps(caught.value.response_metrics)


def test_auxiliary_interpretation_does_not_trigger_tool_output_repair(endpoint):
    client = Client(lambda: iter([event({"content": '{"hypotheses":['}, "length"), "data: [DONE]"]))
    with pytest.raises(executor.TransportExecutionError) as caught:
        executor.execute_streaming_turn_with_repair(client=client, endpoint=endpoint, api_key="test",
            canonical_payload={"messages": []}, allow_output_repair=False, retry_limit=1,
            max_wall_clock_seconds=1)
    assert caught.value.error_code == "output_truncated"
    assert client.sent == 1


def test_uncommitted_network_turn_restarts_without_replaying_partial_tool(endpoint):
    client = Client(lambda: iter(()))
    resets = []
    def lines():
        if client.sent == 1:
            yield event({"reasoning_content": "private draft"})
            yield event({"tool_calls": [{"index": 0, "id": "discarded", "function": {
                "name": "omni_body", "arguments": '{"action":"file.write"'}}]})
            raise httpx.RemoteProtocolError("incomplete chunked read")
        yield event({"tool_calls": [{"index": 0, "id": "accepted", "function": {
            "name": "omni_body", "arguments": json.dumps({"action": "file.read", "args": {}, "target": "input.txt"})}}]}, "tool_calls")
        yield "data: [DONE]"
    client.response.lines = lines
    result = executor.execute_streaming_turn_with_repair(client=client, endpoint=endpoint, api_key="test",
        canonical_payload={"messages": []}, retry_sleep_seconds=0, max_wall_clock_seconds=1,
        on_repair=lambda: resets.append(True))
    assert client.sent == 2 and result.retry_count == 1 and resets == [True]
    assert [c["id"] for c in result.turn.tool_calls] == ["accepted"]
    assert "discarded" not in json.dumps(result.turn.public_dict())
    metrics = result.turn.stream_metadata["attempts"]
    assert len(metrics) == 2 and metrics[0]["reasoning_chunks"] == 1
    assert metrics[0]["tool_argument_bytes"] > 0
    assert "private draft" not in json.dumps(metrics)


def test_uncommitted_stream_retry_is_bounded_and_failure_metrics_survive(endpoint):
    def lines():
        yield event({"reasoning_content": "never log this"})
        raise httpx.ReadError("dropped")
    client = Client(lines)
    with pytest.raises(executor.TransportExecutionError) as caught:
        executor.execute_streaming_turn_with_repair(client=client, endpoint=endpoint, api_key="test",
            canonical_payload={"messages": []}, retry_sleep_seconds=0, max_wall_clock_seconds=1)
    assert client.sent == 3 and caught.value.retry_count == 2
    assert len(caught.value.response_metrics["attempts"]) == 3
    assert "never log this" not in json.dumps(caught.value.response_metrics)


@pytest.mark.parametrize("encoding", ["outer_twice", "nested_args"])
def test_complete_double_encoded_tool_arguments_keep_file_content(endpoint, encoding):
    arguments = {"action": "file.write", "target": "out.txt", "args": {"content": "real content\n中文"}}
    if encoding == "nested_args":
        arguments["args"] = json.dumps(arguments["args"])
    wire = json.dumps(arguments)
    if encoding == "outer_twice":
        wire = json.dumps(wire)
    client = Client(lambda: iter([event({"tool_calls": [{"index": 0, "id": "call1", "function": {
        "name": "omni_body", "arguments": wire}}]}, "tool_calls"), "data: [DONE]"]))
    result = execute(endpoint, client)
    assert result.turn.tool_calls[0]["arguments"]["args"]["content"] == "real content\n中文"


def test_output_repair_is_bounded_and_shares_deadline(endpoint):
    client = Client(lambda: iter(()))
    payloads = []
    def build(*a, **k):
        payloads.append(k["json"])
        return object()
    client.build_request = build
    def lines():
        if client.sent == 1:
            time.sleep(0.02)
            yield event({"content": "partial"}, "length")
        else:
            yield event({"content": "repaired"}, "stop")
        yield "data: [DONE]"
    client.response.lines = lines
    result = executor.execute_streaming_turn_with_repair(client=client, endpoint=endpoint, api_key="test",
        canonical_payload={"messages": []}, max_wall_clock_seconds=1)
    assert result.output_repaired and result.turn.visible_text == "repaired"
    assert client.sent == 2
    assert "均未执行" in payloads[1]["messages"][-1]["content"]
    client = Client(lambda: iter([event({"content": "partial"}, "length"), "data: [DONE]"]))
    with pytest.raises(executor.TransportExecutionError, match="truncated"):
        executor.execute_streaming_turn_with_repair(client=client, endpoint=endpoint, api_key="test",
            canonical_payload={"messages": []}, max_wall_clock_seconds=1)
    assert client.sent == 3


def test_silent_stream_is_closed_on_deadline(endpoint):
    client = Client(lambda: iter(()))
    def silent():
        client.response.closed.wait(1)
        yield ": ping"
    client.response.lines = silent
    started = time.monotonic()
    with pytest.raises(executor.TransportExecutionError) as caught:
        execute(endpoint, client, max_wall_clock_seconds=0.05)
    assert caught.value.deadline_exceeded
    assert time.monotonic() - started < 0.6
    assert client.response.closed.is_set()


def test_two_format_repairs_keep_current_schema_and_share_network_retry_budget(endpoint, tmp_path):
    client = Client(lambda: iter(()))
    payloads, resets = [], []
    valid = {"composition": {"skill": {"id": "s", "steps": [{"id": "step", "tool": "t", "depends_on": []}]},
             "tools": [{"id": "t", "actions": [{"action": "file.write", "target": str(tmp_path / "output.txt"), "args": {"content": "ok"}}]}]}}
    def build(*a, **kw):
        payloads.append(kw["json"])
        return object()
    client.build_request = build
    def lines():
        if client.sent <= 2:
            raise httpx.ReadError("transient fixture")
        arguments = json.dumps(valid) + "}" if client.sent in (3, 4) else json.dumps(valid)
        yield event({"tool_calls": [{"index": 0, "id": "c", "function": {"name": "omni_body", "arguments": arguments}}]}, "tool_calls")
        yield "data: [DONE]"
    client.response.lines = lines
    result = executor.execute_streaming_turn_with_repair(client=client, endpoint=endpoint, api_key="test",
        canonical_payload={"messages": []}, max_wall_clock_seconds=2, retry_sleep_seconds=0,
        on_repair=lambda: resets.append(True))
    assert client.sent == 5 and result.retry_count == 2 and len(resets) == 4
    assert result.turn.tool_calls[0]["arguments"]["composition"] == valid["composition"]
    assert result.turn.stream_metadata["repair_count"] == 2
    assert len(result.turn.stream_metadata["rejected_attempts"]) == 4
    assert "当前 tools" in payloads[-1]["messages"][-1]["content"]
    assert not (tmp_path / "output.txt").exists()  # Transport cannot execute a rejected or accepted tool.


def test_cancel_fences_late_callback_and_result():
    cancelled = threading.Event()
    worker_finished = threading.Event()
    output = []
    def work(call):
        cancelled.set()
        time.sleep(0.1)
        try:
            call.guard(output.append)("late")
        finally:
            worker_finished.set()
        return "late value"
    with pytest.raises(ModelCallStopped, match="cancelled"):
        run_model_call(work, seconds=1, cancel_check=cancelled.is_set)
    assert worker_finished.wait(1)
    assert output == []


def test_retry_and_backoff_share_parent_budget(endpoint):
    class Broken(Client):
        def send(self, *a, **k):
            self.sent += 1
            raise httpx.ConnectError("offline")
    client = Broken(lambda: iter(()))
    with pytest.raises(executor.TransportExecutionError) as caught:
        execute(endpoint, client, max_wall_clock_seconds=0.05, retry_sleep_seconds=0.2)
    assert caught.value.deadline_exceeded
    assert client.sent == 1


def test_calls_do_not_share_cancellation():
    with ThreadPoolExecutor(2) as pool:
        failed = pool.submit(run_model_call, lambda call: call.wait(1), seconds=0.05)
        good = pool.submit(run_model_call, lambda call: (call.wait(0.12), "OK")[1], seconds=1)
        with pytest.raises(ModelCallStopped):
            failed.result()
        assert good.result() == "OK"


def test_typed_and_legacy_errors_are_never_chat_success():
    assert model_turn_failure(ProviderTurnEnvelope("unrelated text", stop_semantics="deadline_exceeded"))
    assert model_turn_failure("[LLM错误: exceeded 180s]")
    assert model_turn_failure("[llm错误: network]")
    assert not model_turn_failure("HTTP 500 means a server error")


def test_expired_gateway_deadline_cannot_acquire_five_more_seconds():
    from contracts.reliability import reset_execution_deadline, set_execution_deadline_ms
    from v3.jineng.http_kehuduan import _effective_llm_deadline_seconds
    token = set_execution_deadline_ms(int(time.time() * 1000) - 1000)
    try:
        assert _effective_llm_deadline_seconds() == 0
    finally:
        reset_execution_deadline(token)
