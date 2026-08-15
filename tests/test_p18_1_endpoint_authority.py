from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class EndpointAuthorityTests(unittest.TestCase):
    def _resolve(self, data: dict, identity: str):
        from v3 import peizhi
        from v3.model_endpoint import duqu_model_endpoint_config

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "api_keys.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(peizhi, "API_PEIZHI_LUJING", path):
                return duqu_model_endpoint_config(identity)

    def test_custom_unknown_model_keeps_literal_endpoint_protocol_and_identity(self) -> None:
        cfg = self._resolve({
            "_default_provider": "custom",
            "_provider_inputs": {
                "custom": {
                    "provider": "custom",
                    "base_url": "https://gateway.example.test/v9",
                    "model_name": "qwen-future-unknown",
                }
            },
            "_endpoint_profiles": {
                "custom": {
                    "service_preset": "custom",
                    "protocol_family": "anthropic_messages",
                }
            },
        }, "custom")
        self.assertEqual(cfg.provider_identity, "custom")
        self.assertEqual(cfg.base_url, "https://gateway.example.test/v9")
        self.assertEqual(cfg.model_name, "qwen-future-unknown")
        self.assertEqual(cfg.protocol_family, "anthropic_messages")
        # The fallback family may be gpt_5_6, but is never connection authority.
        self.assertEqual(cfg.optimization_family, "gpt_5_6")

    def test_scnet_glm_keeps_scnet_endpoint_while_deriving_glm_optimization(self) -> None:
        cfg = self._resolve({
            "_default_provider": "scnet",
            "_provider_inputs": {
                "scnet": {
                    "provider": "scnet",
                    "base_url": "https://api.scnet.cn/api/llm/v1",
                    "model_name": "glm-5.2",
                }
            },
            "_endpoint_profiles": {
                "scnet": {
                    "service_preset": "scnet",
                    "protocol_family": "openai_responses",
                }
            },
        }, "scnet")
        self.assertEqual(cfg.provider_identity, "scnet")
        self.assertEqual(cfg.base_url, "https://api.scnet.cn/api/llm/v1")
        self.assertEqual(cfg.protocol_family, "openai_responses")
        self.assertEqual(cfg.optimization_family, "glm_5_2")

    def test_scnet_deepseek_keeps_scnet_identity(self) -> None:
        cfg = self._resolve({
            "_provider_inputs": {
                "scnet": {
                    "provider": "scnet",
                    "base_url": "https://api.scnet.cn/api/llm/v1",
                    "model_name": "deepseek-v4-pro",
                }
            },
            "_endpoint_profiles": {
                "scnet": {
                    "service_preset": "scnet",
                    "protocol_family": "openai_chat_completions",
                }
            },
        }, "scnet")
        self.assertEqual(cfg.provider_identity, "scnet")
        self.assertEqual(cfg.optimization_family, "deepseek_v4")
        self.assertEqual(cfg.base_url, "https://api.scnet.cn/api/llm/v1")

    def test_generic_openai_qwen_does_not_promote_fallback_to_identity(self) -> None:
        cfg = self._resolve({
            "_provider_inputs": {
                "custom": {
                    "provider": "custom",
                    "base_url": "https://qwen-gateway.example.test/v1",
                    "model_name": "qwen-max-next",
                }
            },
            "_endpoint_profiles": {
                "custom": {
                    "service_preset": "generic_openai",
                    "protocol_family": "openai_chat_completions",
                }
            },
        }, "custom")
        self.assertEqual(cfg.provider_identity, "custom")
        self.assertEqual(cfg.protocol_family, "openai_chat_completions")
        self.assertEqual(cfg.base_url, "https://qwen-gateway.example.test/v1")
        self.assertEqual(cfg.model_name, "qwen-max-next")


if __name__ == "__main__":
    unittest.main()
