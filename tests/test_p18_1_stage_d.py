from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class StageDProviderPresetTests(unittest.TestCase):
    def test_scnet_has_three_protocols_and_protocol_specific_bases(self) -> None:
        from v3.model_endpoint import SERVICE_PRESETS
        p = SERVICE_PRESETS["scnet"]
        self.assertEqual(p.provider_identity, "scnet")
        self.assertEqual(set(p.base_urls), {
            "openai_chat_completions", "openai_responses", "anthropic_messages"
        })
        self.assertEqual(p.base_urls["openai_chat_completions"], "https://api.scnet.cn/api/llm/v1")
        self.assertEqual(p.base_urls["openai_responses"], "https://api.scnet.cn/api/llm/v1")
        self.assertEqual(p.base_urls["anthropic_messages"], "https://api.scnet.cn/api/llm/anthropic")
        self.assertFalse(p.endpoint_overrides["responses_store"])
        self.assertEqual(p.endpoint_overrides["auth_scheme_by_protocol"]["anthropic_messages"], "bearer")

    def test_official_services_use_literal_identity_not_optimization_family(self) -> None:
        from v3.model_endpoint import SERVICE_PRESETS
        self.assertEqual(SERVICE_PRESETS["openai"].provider_identity, "openai")
        self.assertEqual(SERVICE_PRESETS["deepseek"].provider_identity, "deepseek")
        self.assertEqual(SERVICE_PRESETS["zhipu"].provider_identity, "zhipu")
        self.assertEqual(SERVICE_PRESETS["minimax"].provider_identity, "minimax")
        self.assertEqual(SERVICE_PRESETS["mimo"].provider_identity, "mimo")

    def test_unknown_model_reasoning_is_endpoint_scoped_raw_optional(self) -> None:
        from v3.model_stream_config import resolve_model_capability
        cap = resolve_model_capability(
            "brand-new-model-2099", "gpt_5_6", "openai_chat_completions", "custom"
        )
        self.assertFalse(cap.known_model)
        self.assertEqual(cap.reasoning_control, "raw_optional")
        self.assertFalse(cap.native_tools)
        self.assertTrue(cap.prompt_contract_tools)


class StageDEndpointPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        from v3 import peizhi
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "api_keys.json"
        self.original = peizhi.API_PEIZHI_LUJING
        peizhi.API_PEIZHI_LUJING = self.path

    def tearDown(self) -> None:
        from v3 import peizhi
        peizhi.API_PEIZHI_LUJING = self.original
        self.tmp.cleanup()

    def test_scnet_glm_and_deepseek_never_rewrite_identity_or_url(self) -> None:
        from v3.duihua_qiaojie import _save_llm_settings
        for model, family in (("glm-5.2", "glm_5_2"), ("deepseek-v4-pro", "deepseek_v4")):
            result = _save_llm_settings({
                "service_preset": "scnet",
                "provider_identity": "scnet",
                "protocol_family": "openai_chat_completions",
                "base_url": "https://api.scnet.cn/api/llm/v1",
                "model_name": model,
            })
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result["provider_identity"], "scnet")
            self.assertEqual(result["optimization_family"], family)
            self.assertEqual(result["base_url"], "https://api.scnet.cn/api/llm/v1")

    def test_custom_unknown_raw_reasoning_blank_and_nonblank_roundtrip(self) -> None:
        from v3.duihua_qiaojie import _save_llm_settings
        from v3.model_endpoint import duqu_model_endpoint_config
        common = {
            "service_preset": "custom",
            "provider_identity": "custom",
            "protocol_family": "openai_responses",
            "base_url": "https://gateway.example.test/v1",
            "model_name": "future-model-x",
        }
        result = _save_llm_settings({**common, "reasoning_mode": ""})
        self.assertTrue(result.get("ok"), result)
        cfg = duqu_model_endpoint_config("custom")
        self.assertEqual(cfg.reasoning_mode, "")
        result = _save_llm_settings({**common, "reasoning_mode": "ultra"})
        self.assertTrue(result.get("ok"), result)
        cfg = duqu_model_endpoint_config("custom")
        self.assertEqual(cfg.reasoning_mode, "ultra")


class StageDDesktopProbeStaticTests(unittest.TestCase):
    def test_desktop_probe_is_protocol_aware_and_conservative(self) -> None:
        text = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        start = text.index("async function probeProviderApiConnection()")
        end = text.index("\nfunction ", start + 20) if "\nfunction " in text[start + 20:] else len(text)
        body = text[start:end]
        self.assertIn('protocolFamily === "openai_responses"', body)
        self.assertIn('protocolFamily === "anthropic_messages"', body)
        self.assertIn('protocolFamily === "openai_chat_completions"', body)
        self.assertIn('suffix = "responses"', body)
        self.assertIn('suffix = "v1/messages"', body)
        self.assertIn('suffix = "chat/completions"', body)
        self.assertIn('native_tools_supported: false', body)
        self.assertIn('store: false', body)
        self.assertNotIn('suffix = "models"', body)

    def test_renderer_exposes_one_service_field_and_protocol_field(self) -> None:
        text = (ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "settings-panel.mjs").read_text(encoding="utf-8")
        self.assertIn('<span>服务商</span>', text)
        self.assertIn('id="settingsModelProtocol"', text)
        self.assertIn('id="settingsModelProvider" type="hidden"', text)
        self.assertNotIn('<span>服务预设</span>', text)
        self.assertIn('id="settingsModelThinkingRaw"', text)
        self.assertIn('baseUrlInput.dataset.userEdited = "true"', text)
        self.assertIn('protocol_family: protocolInput.value', text)
        self.assertIn('service_preset: selectedPreset', text)

    def test_provider_preset_source_declares_scnet_and_generics(self) -> None:
        text = (ROOT / "app" / "frontend-v2" / "renderer" / "plugins" / "provider-presets.mjs").read_text(encoding="utf-8")
        for token in ("scnet:", "generic_openai:", "generic_anthropic:", "OPENAI_RESPONSES", "ANTHROPIC_MESSAGES"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
