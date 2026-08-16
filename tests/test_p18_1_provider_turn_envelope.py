from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class ProviderTurnEnvelopeTests(unittest.TestCase):
    def test_reasoning_and_opaque_continuation_never_enter_legacy_wire(self) -> None:
        from v3.model_protocol_contract import ProviderContinuationState, ProviderTurnEnvelope, ToolCallBinding

        private = "secret reasoning"
        opaque = {"previous_response_id": "resp_private_1"}
        continuation = ProviderContinuationState(
            mode="remote_optional",
            opaque_payload=opaque,
            provider_identity="scnet",
            protocol_family="openai_responses",
            model_id="gpt-5.6",
        )
        reply = ProviderTurnEnvelope(
            "visible\n<tool_call>...</tool_call>",
            provider_identity="scnet",
            service_preset="scnet",
            protocol_family="openai_responses",
            optimization_family="gpt_5_6",
            model_id="gpt-5.6",
            visible_text="visible",
            private_reasoning=private,
            tool_calls=[{"name": "omni_body", "arguments": {"action": "file.read"}}],
            tool_call_bindings=[ToolCallBinding(
                canonical_call_id="call_1",
                provider_call_id="call_1",
                tool_name="omni_body",
                protocol_family="openai_responses",
                binding_type="responses_function_call",
            )],
            provider_continuation_state=continuation,
        )
        self.assertNotIn(private, str(reply))
        self.assertNotIn("resp_private_1", str(reply))
        self.assertEqual(reply.provider_continuation_mode, "remote_optional")
        self.assertEqual(reply.tool_call_bindings[0].provider_call_id, "call_1")
        public = reply.public_dict()
        self.assertNotIn("private_reasoning", public)
        self.assertNotIn("opaque_payload", str(public))

    def test_provider_continuation_is_nonportable_and_identity_bound(self) -> None:
        from v3.model_protocol_contract import ProviderContinuationState

        state = ProviderContinuationState(
            mode="opaque_client_state",
            opaque_payload={"items": [{"id": "x"}]},
            provider_identity="openai",
            protocol_family="openai_responses",
            model_id="gpt-5.6",
        )
        self.assertTrue(state.compatible_with(
            provider_identity="openai",
            protocol_family="openai_responses",
            model_id="gpt-5.6",
        ))
        self.assertFalse(state.compatible_with(
            provider_identity="scnet",
            protocol_family="openai_responses",
            model_id="gpt-5.6",
        ))
        with self.assertRaises(ValueError):
            ProviderContinuationState(mode="local_replay", opaque_payload={}, portable=True)


class EffectiveCapabilityTests(unittest.TestCase):
    def test_known_model_protocol_intersection(self) -> None:
        from v3.model_stream_config import resolve_model_capability

        cap = resolve_model_capability(
            "glm-5.2",
            "glm_5_2",
            "openai_chat_completions",
            "scnet",
            {"native_tools_supported": True},
        )
        self.assertTrue(cap.known_model)
        self.assertEqual(cap.model_family, "glm_5_2")
        self.assertTrue(cap.native_tools)
        self.assertIn("high", cap.reasoning_modes)

    def test_unknown_model_is_raw_optional_and_not_native_by_assumption(self) -> None:
        from v3.model_stream_config import resolve_model_capability

        cap = resolve_model_capability(
            "future-qwen-999",
            "gpt_5_6",
            "openai_chat_completions",
            "generic_openai",
        )
        self.assertFalse(cap.known_model)
        self.assertEqual(cap.reasoning_control, "raw_optional")
        self.assertFalse(cap.native_tools)
        self.assertTrue(cap.prompt_contract_tools)

    def test_probe_can_prove_native_tools_for_unknown_model(self) -> None:
        from v3.model_stream_config import resolve_model_capability

        cap = resolve_model_capability(
            "future-model",
            "gpt_5_6",
            "openai_responses",
            "custom",
            {"native_tools_supported": True},
        )
        self.assertTrue(cap.native_tools)
        self.assertEqual(cap.provider_continuation, "remote_optional")


if __name__ == "__main__":
    unittest.main()
