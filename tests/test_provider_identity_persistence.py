"""Provider identity persistence contract (first-principles regression).

The historical defect: the L4 routing fallback
(``unmatched_openai_compatible_fallback``) was persisted as the provider
identity (``_default_provider``), so any custom provider whose name/base URL/
model lacked recognized keywords silently became ``gpt_5_6`` — and every
later model-only edit kept pinning ``gpt_5_6`` because the UI adopted the
fallback as the user's choice.

The contract locked here separates two concepts permanently:

- provider IDENTITY: the user's configuration, persisted faithfully under its
  own normalized identity (empty/unknown -> ``custom``), never substituted;
- ROUTING family: a derived runtime decision (``provider_match_info``) that
  may fall back to ``gpt_5_6`` for L4 optimization but never writes back.

Any regression that re-couples them fails these tests.
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

    def test_custom_provider_with_unknown_model_keeps_custom_identity(self) -> None:
        result = _save_llm_settings({
            "provider": "custom",
            "base_url": "https://api.siliconflow.cn/v1",
            "model_name": "Qwen/Qwen3-235B",
        })
        self.assertTrue(result.get("ok"), result)
        stored = self._stored()
        # Identity must be the user's own provider, not the routing fallback.
        self.assertEqual("custom", stored["_default_provider"])
        self.assertIn("custom", stored["_provider_inputs"])
        self.assertEqual("custom", stored["_provider_inputs"]["custom"]["provider"])
        self.assertEqual(
            "Qwen/Qwen3-235B",
            stored["_provider_inputs"]["custom"]["model_name"],
        )
        # Routing advice may still be the OpenAI-compatible fallback...
        self.assertEqual("gpt_5_6", result.get("matched_provider"))
        # ...but reading the identity back must round-trip the user's choice.
        self.assertEqual("custom", peizhi.duqu_moren_provider())

    def test_model_only_edit_never_pins_the_fallback_family(self) -> None:
        _save_llm_settings({
            "provider": "custom",
            "base_url": "https://api.siliconflow.cn/v1",
            "model_name": "Qwen/Qwen3-235B",
        })
        # The user later edits only the model name; the panel re-sends the
        # identity it displays (custom), never the fallback family.
        result = _save_llm_settings({
            "provider": "custom",
            "model_name": "Qwen/Qwen3-32B",
        })
        self.assertTrue(result.get("ok"), result)
        stored = self._stored()
        self.assertEqual("custom", stored["_default_provider"])
        self.assertEqual(
            "Qwen/Qwen3-32B",
            stored["_provider_inputs"]["custom"]["model_name"],
        )
        self.assertEqual("custom", peizhi.duqu_moren_provider())

    def test_empty_provider_becomes_custom_identity_not_default_family(self) -> None:
        result = _save_llm_settings({
            "provider": "",
            "base_url": "https://api.kimi-relay.example/v1",
            "model_name": "kimi-k2",
        })
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("custom", self._stored()["_default_provider"])
        self.assertEqual("custom", peizhi.duqu_moren_provider())

    def test_routing_fallback_still_works_for_runtime(self) -> None:
        _save_llm_settings({
            "provider": "custom",
            "base_url": "https://api.siliconflow.cn/v1",
            "model_name": "Qwen/Qwen3-235B",
        })
        # Runtime routing (L4 family) still falls back for unknown families.
        self.assertEqual(
            "gpt_5_6",
            peizhi.infer_provider_id("custom", "https://api.siliconflow.cn/v1", "Qwen/Qwen3-235B"),
        )
        # And the stored base URL / model still resolve through the identity.
        self.assertEqual(
            "https://api.siliconflow.cn/v1",
            peizhi.duqu_provider_base_url("custom"),
        )
        self.assertEqual("Qwen/Qwen3-235B", peizhi.duqu_model_ming("custom"))

    def test_legacy_corrupted_record_self_heals_to_real_identity(self) -> None:
        # Historical defect shape: identity overwritten with the routing
        # fallback while the user's real provider survived inside the record.
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

    def test_known_family_presets_keep_their_identity(self) -> None:
        for provider, base_url, model in (
            ("deepseek_v4", "https://api.deepseek.com/v1", "deepseek-chat"),
            ("glm_5_2", "https://open.bigmodel.cn/api/paas/v4", "glm-5.2"),
        ):
            result = _save_llm_settings({
                "provider": provider,
                "base_url": base_url,
                "model_name": model,
            })
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(provider, self._stored()["_default_provider"])
            self.assertEqual(provider, peizhi.duqu_moren_provider())

    def test_openai_compatible_preset_stays_family_identity(self) -> None:
        result = _save_llm_settings({
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-5.6",
        })
        self.assertTrue(result.get("ok"), result)
        # "openai" is a true family alias, so identity is the family id.
        self.assertEqual("gpt_5_6", self._stored()["_default_provider"])


if __name__ == "__main__":
    unittest.main()