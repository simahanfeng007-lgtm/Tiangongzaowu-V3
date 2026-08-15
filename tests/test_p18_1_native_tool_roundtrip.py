from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def endpoint(protocol: str, *, service: str = "custom", base: str = "https://example.test/v1"):
    from v3.model_endpoint import ModelEndpointConfig
    return ModelEndpointConfig(
        service_preset=service,
        provider_identity=service,
        protocol_family=protocol,
        base_url=base,
        model_name="model-1",
        credential_scope="test",
        reasoning_mode="",
        endpoint_overrides={},
        optimization_family="gpt_5_6",
        config_fingerprint="f" * 64,
    )


class NativeToolRoundtripTests(unittest.TestCase):
    def test_chat_tool_call_and_result_keep_exact_id(self) -> None:
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
        turn = transport.finalize_turn(endpoint("openai_chat_completions"), state)
        binding = turn.tool_call_bindings[0].as_dict()
        result = transport.encode_tool_result({"ok": True}, binding)
        self.assertEqual(binding["provider_call_id"], "call_chat_1")
        self.assertEqual(result["tool_call_id"], "call_chat_1")
        self.assertEqual(result["role"], "tool")

    def test_responses_function_call_output_keeps_call_id(self) -> None:
        from v3.jineng.model_transport_contract import StreamState
        from v3.jineng.model_transport_openai_responses import OpenAIResponsesTransport

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
        turn = transport.finalize_turn(endpoint("openai_responses"), state)
        binding = turn.tool_call_bindings[0].as_dict()
        result = transport.encode_tool_result({"ok": True}, binding)
        self.assertEqual(binding["provider_call_id"], "call_resp_1")
        self.assertEqual(binding["provider_item_id"], "fc_1")
        self.assertEqual(result, {"type": "function_call_output", "call_id": "call_resp_1", "output": '{"ok":true}'})
        self.assertEqual(turn.provider_continuation_mode, "remote_optional")

    def test_anthropic_tool_result_keeps_tool_use_id(self) -> None:
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
        turn = transport.finalize_turn(endpoint("anthropic_messages"), state)
        binding = turn.tool_call_bindings[0].as_dict()
        result = transport.encode_tool_result({"ok": True}, binding)
        self.assertEqual(binding["provider_call_id"], "toolu_1")
        self.assertEqual(result["tool_use_id"], "toolu_1")
        self.assertEqual(result["type"], "tool_result")

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
