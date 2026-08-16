from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def endpoint(
    protocol: str,
    *,
    service: str = "custom",
    base: str = "https://example.test/v1",
    model: str = "model-1",
    overrides: dict | None = None,
):
    from v3.model_endpoint import ModelEndpointConfig
    return ModelEndpointConfig(
        service_preset=service,
        provider_identity=service,
        protocol_family=protocol,
        base_url=base,
        model_name=model,
        credential_scope="test",
        reasoning_mode="",
        endpoint_overrides=dict(overrides or {}),
        optimization_family="gpt_5_6",
        config_fingerprint="f" * 64,
    )


def canonical_next_payload(turn, results: list[dict]) -> dict:
    return {
        "model": turn.model_id,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "do it"},
            # Legacy Gutong observation must be removed only after exact native
            # binding has been verified.
            *[{"role": "assistant", "content": f"legacy-result-{index}"} for index, _ in enumerate(results)],
        ],
        "__provider_turn": turn,
        "__provider_tool_results": results,
    }


class NativeToolRoundtripTests(unittest.TestCase):
    def _chat_turn(self):
        from v3.jineng.model_transport_contract import StreamState
        from v3.jineng.model_transport_openai_chat import OpenAIChatTransport

        transport = OpenAIChatTransport()
        state = StreamState()
        transport.consume_stream_event(state, {
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_chat_1",
                "function": {"name": "omni_body", "arguments": "{\"action\":\"file.read\"}"},
            }]}, "finish_reason": "tool_calls"}],
            "usage": {"total_tokens": 3},
        })
        return transport, transport.finalize_turn(endpoint("openai_chat_completions"), state)

    def _responses_turn(self, *, overrides: dict | None = None):
        from v3.jineng.model_transport_contract import StreamState
        from v3.jineng.model_transport_openai_responses import OpenAIResponsesTransport

        ep = endpoint("openai_responses", overrides=overrides)
        transport = OpenAIResponsesTransport()
        state = StreamState()
        transport.consume_stream_event(state, {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_resp_1", "name": "omni_body"},
        })
        transport.consume_stream_event(state, {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_1",
            "call_id": "call_resp_1",
            "arguments": "{\"action\":\"file.read\",\"target\":\"a.txt\"}",
        })
        transport.consume_stream_event(state, {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed", "usage": {"total_tokens": 4}},
        })
        return ep, transport, transport.finalize_turn(ep, state)

    def _anthropic_turn(self):
        from v3.jineng.model_transport_anthropic import AnthropicMessagesTransport
        from v3.jineng.model_transport_contract import StreamState

        transport = AnthropicMessagesTransport()
        state = StreamState()
        transport.consume_stream_event(state, {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "omni_body", "input": {}},
        })
        transport.consume_stream_event(state, {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"action":"file.read"}'},
        })
        transport.consume_stream_event(state, {"type": "message_delta", "delta": {"stop_reason": "tool_use"}})
        return transport, transport.finalize_turn(endpoint("anthropic_messages"), state)

    def test_chat_tool_call_and_result_keep_exact_id(self) -> None:
        transport, turn = self._chat_turn()
        binding = turn.tool_call_bindings[0].as_dict()
        result = transport.encode_tool_result({"ok": True}, binding)
        self.assertEqual(binding["provider_call_id"], "call_chat_1")
        self.assertEqual(result["tool_call_id"], "call_chat_1")
        self.assertEqual(result["role"], "tool")

    def test_chat_next_request_replays_assistant_tool_call_and_exact_tool_result(self) -> None:
        transport, turn = self._chat_turn()
        request = transport.build_request(
            endpoint("openai_chat_completions"),
            "secret",
            canonical_next_payload(turn, [{"ok": True, "value": "x"}]),
        )
        self.assertNotIn("__provider_turn", request.payload)
        self.assertNotIn("__provider_tool_results", request.payload)
        self.assertFalse(any(message.get("content") == "legacy-result-0" for message in request.payload["messages"]))
        assistant = next(message for message in request.payload["messages"] if message.get("tool_calls"))
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_chat_1")
        tool = next(message for message in request.payload["messages"] if message.get("role") == "tool")
        self.assertEqual(tool["tool_call_id"], "call_chat_1")

    def test_responses_function_call_output_keeps_call_id(self) -> None:
        _ep, transport, turn = self._responses_turn()
        binding = turn.tool_call_bindings[0].as_dict()
        result = transport.encode_tool_result({"ok": True}, binding)
        self.assertEqual(binding["provider_call_id"], "call_resp_1")
        self.assertEqual(binding["provider_item_id"], "fc_1")
        self.assertEqual(result, {"type": "function_call_output", "call_id": "call_resp_1", "output": '{"ok":true}'})
        self.assertEqual(turn.provider_continuation_mode, "remote_optional")

    def test_responses_next_request_uses_local_replay_by_default(self) -> None:
        ep, transport, turn = self._responses_turn()
        request = transport.build_request(ep, "secret", canonical_next_payload(turn, [{"ok": True}]))
        self.assertNotIn("__provider_turn", request.payload)
        self.assertNotIn("__provider_tool_results", request.payload)
        self.assertFalse(any(item.get("content") == "legacy-result-0" for item in request.payload["input"] if isinstance(item, dict)))
        calls = [item for item in request.payload["input"] if item.get("type") == "function_call"]
        outputs = [item for item in request.payload["input"] if item.get("type") == "function_call_output"]
        self.assertEqual(calls[0]["call_id"], "call_resp_1")
        self.assertEqual(outputs[0]["call_id"], "call_resp_1")
        self.assertFalse(request.payload["store"])
        self.assertNotIn("previous_response_id", request.payload)

    def test_responses_remote_continuation_is_explicit_optional_override_only(self) -> None:
        overrides = {"responses_use_previous_response_id": True, "responses_store": False}
        ep, transport, turn = self._responses_turn(overrides=overrides)
        request = transport.build_request(ep, "secret", canonical_next_payload(turn, [{"ok": True}]))
        self.assertEqual(request.payload["previous_response_id"], "resp_1")
        self.assertFalse(request.payload["store"])
        self.assertTrue(any(item.get("type") == "function_call_output" for item in request.payload["input"]))

    def test_anthropic_tool_result_keeps_tool_use_id(self) -> None:
        transport, turn = self._anthropic_turn()
        binding = turn.tool_call_bindings[0].as_dict()
        result = transport.encode_tool_result({"ok": True}, binding)
        self.assertEqual(binding["provider_call_id"], "toolu_1")
        self.assertEqual(result["tool_use_id"], "toolu_1")
        self.assertEqual(result["type"], "tool_result")

    def test_anthropic_next_request_replays_tool_use_then_tool_result(self) -> None:
        transport, turn = self._anthropic_turn()
        ep = endpoint("anthropic_messages")
        request = transport.build_request(ep, "secret", canonical_next_payload(turn, [{"ok": True}]))
        self.assertNotIn("__provider_turn", request.payload)
        self.assertNotIn("__provider_tool_results", request.payload)
        assistant = next(
            message for message in request.payload["messages"]
            if message.get("role") == "assistant" and isinstance(message.get("content"), list)
        )
        tool_use = next(block for block in assistant["content"] if block.get("type") == "tool_use")
        self.assertEqual(tool_use["id"], "toolu_1")
        result_message = request.payload["messages"][-1]
        self.assertEqual(result_message["role"], "user")
        self.assertEqual(result_message["content"][0]["type"], "tool_result")
        self.assertEqual(result_message["content"][0]["tool_use_id"], "toolu_1")

    def test_mismatched_provider_discards_soft_continuation_and_never_leaks_internal_keys(self) -> None:
        transport, turn = self._chat_turn()
        mismatched = endpoint("openai_chat_completions", service="other")
        request = transport.build_request(
            mismatched,
            "secret",
            canonical_next_payload(turn, [{"ok": True}]),
        )
        self.assertNotIn("__provider_turn", request.payload)
        self.assertNotIn("__provider_tool_results", request.payload)
        self.assertFalse(any(message.get("role") == "tool" for message in request.payload["messages"]))
        self.assertTrue(any(message.get("content") == "legacy-result-0" for message in request.payload["messages"]))

    def test_parallel_bindings_preserve_provider_sequence_without_guessing_ids(self) -> None:
        from v3.jineng.model_transport_contract import StreamState
        from v3.jineng.model_transport_openai_responses import OpenAIResponsesTransport

        ep = endpoint("openai_responses")
        transport = OpenAIResponsesTransport()
        state = StreamState()
        for index, (item_id, call_id) in enumerate((("fc_a", "call_a"), ("fc_b", "call_b"))):
            transport.consume_stream_event(state, {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": item_id, "call_id": call_id, "name": "omni_body"},
            })
            transport.consume_stream_event(state, {
                "type": "response.function_call_arguments.done",
                "item_id": item_id,
                "call_id": call_id,
                "arguments": f'{{"action":"file.read","target":"{index}.txt"}}',
            })
        turn = transport.finalize_turn(ep, state)
        request = transport.build_request(
            ep,
            "secret",
            canonical_next_payload(turn, [{"value": "A"}, {"value": "B"}]),
        )
        outputs = [item for item in request.payload["input"] if item.get("type") == "function_call_output"]
        self.assertEqual([item["call_id"] for item in outputs], ["call_a", "call_b"])
        self.assertIn('"A"', outputs[0]["output"])
        self.assertIn('"B"', outputs[1]["output"])

    def test_scnet_anthropic_uses_bearer_override_not_x_api_key(self) -> None:
        from v3.jineng.model_transport_anthropic import AnthropicMessagesTransport
        from v3.model_endpoint import ModelEndpointConfig

        ep = ModelEndpointConfig(
            service_preset="scnet",
            provider_identity="scnet",
            protocol_family="anthropic_messages",
            base_url="https://api.scnet.cn/api/llm/anthropic",
            model_name="model",
            credential_scope="test",
            reasoning_mode="",
            endpoint_overrides={
                "auth_scheme_by_protocol": {"anthropic_messages": "bearer"},
                "anthropic_version": "2023-06-01",
            },
            optimization_family="gpt_5_6",
            config_fingerprint="f" * 64,
        )
        headers = AnthropicMessagesTransport().build_headers(ep, "sk-tp-secret")
        self.assertEqual(headers["Authorization"], "Bearer sk-tp-secret")
        self.assertNotIn("x-api-key", headers)
        self.assertEqual(headers["anthropic-version"], "2023-06-01")


class OmniBodyResponsesCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = ROOT / "src" / "omni_body_skill" / "model_adapters" / "core.py"
        spec = importlib.util.spec_from_file_location("p181_omni_adapter", path)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(cls.module)

    def test_responses_profile_roundtrip(self) -> None:
        parsed = self.module.parse_tool_calls(
            payload={"output": [{
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_42",
                "name": "omni_body",
                "arguments": '{"action":"file.read","target":"x.txt"}',
            }]},
            profile_id="gpt_openai_responses",
        )
        self.assertEqual(parsed["calls"][0]["call_id"], "call_42")
        rendered = self.module.render_tool_result(
            {"ok": True}, call_id="call_42", profile_id="gpt_openai_responses"
        )
        self.assertEqual(rendered["tool_result"]["type"], "function_call_output")
        self.assertEqual(rendered["tool_result"]["call_id"], "call_42")

    def test_responses_tool_schema_is_not_chat_nested_function(self) -> None:
        rendered = self.module.render_tool_schema(profile_id="gpt_openai_responses")
        tool = rendered["tool_schema"][0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["name"], "omni_body")
        self.assertIn("parameters", tool)
        self.assertNotIn("function", tool)


if __name__ == "__main__":
    unittest.main()
