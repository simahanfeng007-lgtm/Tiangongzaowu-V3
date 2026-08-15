"""P18.1 provider identity persistence contract.

Connection identity and L4 optimization family are independent. Service names
remain literal persisted identities; historical family IDs remain readable for
upgrade compatibility. A routing fallback may never be written back.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v3 import peizhi
from v3.duihua_qiaojie import _save_llm_settings


class ProviderIdentityPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary.name) / "api_keys.json"
        self._original = peizhi.API_PEIZHI_LUJING
        peizhi.API_PEIZHI_LUJING = self.config_path

    def tearDown(self) -> None:
        peizhi.API_PEIZHI_LUJING = self._original
        self.temporary.cleanup()

    def _stored(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    @staticmethod
    def _custom(**kwargs) -> dict:
        return {
            "provider": "custom",
            "service_preset": "custom",
            "protocol_family": "openai_chat_completions",
            **kwargs,
        }

    def test_service_aliases_preserve_connection_identity(self) -> None:
        self.assertEqual(peizhi.normalize_provider_identity("OpenAI"), "openai")
        self.assertEqual(peizhi.normalize_provider_identity("deepseek"), "deepseek")
        self.assertEqual(peizhi.normalize_provider_identity("zhipu"), "zhipu")
        self.assertEqual(peizhi.normalize_provider_identity("MiniMax"), "minimax")
        self.assertEqual(peizhi.normalize_provider_identity("SCNet"), "scnet")
        self.assertEqual(peizhi.normalize_provider_identity("custom"), "custom")

    def test_historical_family_ids_remain_readable_but_are_not_service_aliases(self) -> None:
        for family in ("deepseek_v4", "glm_5_2", "minimax_m3", "gpt_5_6"):
            self.assertEqual(peizhi.normalize_provider_identity(family), family)

    def test_custom_provider_with_unknown_model_keeps_custom_identity(self) -> None:
        result = _save_llm_settings(self._custom(
            base_url="https://api.siliconflow.cn/v1",
            model_name="Qwen/Qwen3-235B",
        ))
        self.assertTrue(result.get("ok"), result)
        stored = self._stored()
        self.assertEqual("custom", stored["_default_provider"])
        self.assertEqual("custom", stored["_provider_inputs"]["custom"]["provider"])
        self.assertEqual("openai_chat_completions", stored["_provider_inputs"]["custom"]["protocol_family"])
        self.assertEqual("Qwen/Qwen3-235B", stored["_provider_inputs"]["custom"]["model_name"])
        self.assertEqual("gpt_5_6", result.get("matched_provider"))
        self.assertEqual("custom", peizhi.duqu_moren_provider())

    def test_custom_requires_explicit_protocol(self) -> None:
        result = _save_llm_settings({
            "provider": "custom",
            "service_preset": "custom",
            "base_url": "https://api.example.test/v1",
            "model_name": "future-model",
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "protocol_family_required")

    def test_model_only_edit_never_pins_fallback_family(self) -> None:
        _save_llm_settings(self._custom(
            base_url="https://api.siliconflow.cn/v1",
            model_name="Qwen/Qwen3-235B",
        ))
        result = _save_llm_settings(self._custom(model_name="Qwen/Qwen3-32B"))
        self.assertTrue(result.get("ok"), result)
        stored = self._stored()
        self.assertEqual("custom", stored["_default_provider"])
        self.assertEqual("Qwen/Qwen3-32B", stored["_provider_inputs"]["custom"]["model_name"])
        self.assertEqual("custom", peizhi.duqu_moren_provider())

    def test_empty_provider_with_custom_preset_becomes_custom(self) -> None:
        result = _save_llm_settings({
            "provider": "",
            "service_preset": "custom",
            "protocol_family": "openai_chat_completions",
            "base_url": "https://api.kimi-relay.example/v1",
            "model_name": "kimi-k2",
        })
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("custom", self._stored()["_default_provider"])

    def test_routing_fallback_still_works_without_identity_writeback(self) -> None:
        result = _save_llm_settings(self._custom(
            base_url="https://api.siliconflow.cn/v1",
            model_name="Qwen/Qwen3-235B",
        ))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(
            "gpt_5_6",
            peizhi.infer_provider_id("custom", "https://api.siliconflow.cn/v1", "Qwen/Qwen3-235B"),
        )
        self.assertEqual("custom", self._stored()["_default_provider"])
        self.assertEqual("https://api.siliconflow.cn/v1", peizhi.duqu_provider_base_url("custom"))

    def test_openai_service_identity_is_not_gpt_family(self) -> None:
        result = _save_llm_settings({
            "provider": "openai",
            "service_preset": "openai",
            "protocol_family": "openai_responses",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-5.6",
        })
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("openai", self._stored()["_default_provider"])
        self.assertEqual("gpt_5_6", result.get("optimization_family"))

    def test_scnet_identity_survives_glm_and_deepseek_optimization(self) -> None:
        for model, expected_family in (("glm-5.2", "glm_5_2"), ("deepseek-v4-pro", "deepseek_v4")):
            result = _save_llm_settings({
                "provider": "scnet",
                "service_preset": "scnet",
                "protocol_family": "openai_chat_completions",
                "base_url": "https://api.scnet.cn/api/llm/v1",
                "model_name": model,
            })
            self.assertTrue(result.get("ok"), result)
            self.assertEqual("scnet", self._stored()["_default_provider"])
            self.assertEqual(expected_family, result.get("optimization_family"))

    def test_legacy_corrupted_record_self_heals_to_real_identity(self) -> None:
        self.config_path.write_text(json.dumps({
            "_default_provider": "gpt_5_6",
            "_provider_inputs": {
                "gpt_5_6": {
                    "provider": "custom",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "model_name": "Qwen/Qwen3-235B",
                },
            },
            "_base_urls": {"gpt_5_6": "https://api.siliconflow.cn/v1"},
            "_model_names": {"gpt_5_6": "Qwen/Qwen3-235B"},
        }), encoding="utf-8")
        self.assertEqual("custom", peizhi.duqu_moren_provider())
        inputs = peizhi.duqu_provider_input_config("custom")
        self.assertEqual("Qwen/Qwen3-235B", inputs["model_name"])


if __name__ == "__main__":
    unittest.main()
