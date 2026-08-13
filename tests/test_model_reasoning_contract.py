from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class ModelTurnContractTests(unittest.TestCase):
    def test_natural_reply_and_tool_call_share_one_turn_without_reasoning_leak(self) -> None:
        from v3.jineng.http_kehuduan import ModelTurnReply, _render_tool_turn_legacy

        visible = "我先读取配置，再根据结果继续。"
        private = "这段是私有推理，绝不能展示。"
        calls = [{"name": "omni_body", "arguments": {"action": "file.read", "target": "a.txt"}}]
        wire = _render_tool_turn_legacy(visible, calls)
        reply = ModelTurnReply(
            wire,
            visible_text=visible,
            tool_calls=calls,
            private_reasoning=private,
            provider_id="deepseek_v4",
        )

        self.assertTrue(str(reply).startswith(visible))
        self.assertIn("<tool_call>", str(reply))
        self.assertNotIn(private, str(reply))
        self.assertEqual(reply.visible_text, visible)
        self.assertEqual(reply.private_reasoning, private)
        self.assertEqual(reply.tool_calls[0]["name"], "omni_body")

    def test_reasoning_delta_variants_are_private_normalized(self) -> None:
        from v3.jineng.http_kehuduan import _stream_reasoning_text

        self.assertEqual(_stream_reasoning_text({"reasoning_content": "甲"}), "甲")
        self.assertEqual(_stream_reasoning_text({"reasoning": "乙"}), "乙")
        self.assertEqual(
            _stream_reasoning_text({"reasoning_details": [{"text": "丙"}, {"text": "丁"}]}),
            "丙丁",
        )


class ReasoningCapabilityTests(unittest.TestCase):
    def test_provider_capabilities_do_not_fake_uniform_depths(self) -> None:
        from v3.model_stream_config import get_model_reasoning_capability

        self.assertEqual(get_model_reasoning_capability("deepseek_v4")["modes"], ["off", "high", "max"])
        self.assertEqual(get_model_reasoning_capability("mimo")["modes"], ["off", "on"])
        self.assertEqual(get_model_reasoning_capability("minimax_m3")["modes"], ["off", "auto"])
        self.assertFalse(get_model_reasoning_capability("gpt_5_6", "gpt-4o")["supported"])
        self.assertTrue(get_model_reasoning_capability("gpt_5_6", "gpt-5.6")["supported"])

    def test_request_mapping_matches_provider_protocol(self) -> None:
        from v3.jineng import http_kehuduan

        cases = (
            ("deepseek_v4", "max", {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}),
            ("glm_5_2", "low", {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}),
            ("mimo", "off", {"thinking": {"type": "disabled"}}),
            ("gpt_5_6", "high", {"reasoning_effort": "high"}),
        )
        for provider, mode, expected in cases:
            with self.subTest(provider=provider, mode=mode), mock.patch.object(
                http_kehuduan,
                "duqu_model_reasoning_config",
                return_value={
                    "supported": True,
                    "effective_mode": mode,
                    "control": "test",
                    "binding_key": "b",
                },
            ):
                payload: dict = {}
                http_kehuduan._apply_reasoning_profile(
                    provider,
                    payload,
                    base_url="https://example.test/v1",
                    model_name="model",
                )
                for key, value in expected.items():
                    self.assertEqual(payload.get(key), value)

    def test_settings_persist_per_provider_endpoint_and_model(self) -> None:
        from v3 import peizhi
        from v3.duihua_qiaojie import _save_llm_settings

        with tempfile.TemporaryDirectory() as temporary:
            settings_path = Path(temporary) / "api_keys.json"
            with mock.patch.object(peizhi, "API_PEIZHI_LUJING", settings_path):
                saved = _save_llm_settings({
                    "provider": "deepseek_v4",
                    "base_url": "https://api.deepseek.com",
                    "model_name": "deepseek-v4-pro",
                    "reasoning_mode": "max",
                })
                self.assertTrue(saved["ok"])
                self.assertEqual(saved["reasoning"]["effective_mode"], "max")
                config = peizhi.duqu_model_reasoning_config(
                    "deepseek_v4",
                    "https://api.deepseek.com",
                    "deepseek-v4-pro",
                )
                self.assertEqual(config["configured_mode"], "max")
                other = peizhi.duqu_model_reasoning_config(
                    "deepseek_v4",
                    "https://api.deepseek.com",
                    "deepseek-v4-flash",
                )
                self.assertNotEqual(config["binding_key"], other["binding_key"])
                self.assertEqual(other["configured_mode"], "")


if __name__ == "__main__":
    unittest.main()
