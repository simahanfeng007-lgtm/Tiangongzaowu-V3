from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v3.jineng import http_kehuduan
from v3.jineng.model_transport_contract import StreamState
from v3.jineng.model_transport_openai_chat import OpenAIChatTransport
from v3.model_endpoint import ModelEndpointConfig, ProtocolFamily


ROOT = Path(__file__).resolve().parents[1]


class ModelCacheObservabilityTests(unittest.TestCase):
    def test_cache_prefix_fingerprint_ignores_dynamic_user_content(self) -> None:
        base = {
            "tools": [{"type": "function", "function": {"name": "omni_body"}}],
            "messages": [
                {"role": "system", "content": "stable soul and tool policy"},
                {"role": "user", "content": "first task"},
            ],
        }
        changed_user = {
            **base,
            "messages": [
                {"role": "system", "content": "stable soul and tool policy"},
                {"role": "user", "content": "a completely different task"},
            ],
        }
        changed_system = {
            **base,
            "messages": [
                {"role": "system", "content": "different soul and tool policy"},
                {"role": "user", "content": "first task"},
            ],
        }

        first = http_kehuduan._cache_prefix_observation(base)
        second = http_kehuduan._cache_prefix_observation(changed_user)
        third = http_kehuduan._cache_prefix_observation(changed_system)

        self.assertEqual(first["cache_prefix_sha256"], second["cache_prefix_sha256"])
        self.assertNotEqual(first["cache_prefix_sha256"], third["cache_prefix_sha256"])
        self.assertEqual(first["cache_prefix_message_count"], 1)
        self.assertEqual(first["cache_prefix_tool_count"], 1)

    def test_provider_usage_is_persisted_with_real_cache_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_path = Path(temporary) / "l4.jsonl"
            trace = {
                "l4_profile_consumed": True,
                "provider": "deepseek_v4",
                "cache_prefix_sha256": "a" * 64,
            }
            usage = {
                "prompt_tokens": 1_000,
                "completion_tokens": 50,
                "total_tokens": 1_050,
                "prompt_cache_hit_tokens": 720,
                "prompt_cache_miss_tokens": 280,
            }
            with patch.object(http_kehuduan, "L4_OPTIMIZATION_TRACE_PATH", trace_path):
                http_kehuduan._jilu_l4_youhua_zhuizong(
                    trace,
                    api_status="ok",
                    http_status=200,
                    latency_ms=12,
                    retry_count=0,
                    usage=usage,
                )
            row = json.loads(trace_path.read_text(encoding="utf-8").strip())
            self.assertEqual(row["usage"]["prompt_cache_hit_tokens"], 720)
            self.assertEqual(row["usage"]["prompt_cache_miss_tokens"], 280)
            self.assertEqual(row["usage"]["cached_input_tokens"], 720)
            self.assertEqual(row["usage"]["cache_hit_ratio"], 0.72)
            self.assertEqual(row["cache_prefix_sha256"], "a" * 64)

    def test_warm_cache_ratio_excludes_one_compulsory_miss_per_prefix(self) -> None:
        rows = [
            {
                "cache_prefix_sha256": "a" * 64,
                "usage": {"prompt_tokens": 1_000, "cached_input_tokens": 0},
            },
            {
                "cache_prefix_sha256": "a" * 64,
                "usage": {"prompt_tokens": 1_000, "cached_input_tokens": 992},
            },
            {
                "cache_prefix_sha256": "b" * 64,
                "usage": {"prompt_tokens": 500, "cached_input_tokens": 0},
            },
            {
                "cache_prefix_sha256": "b" * 64,
                "usage": {"prompt_tokens": 500, "cached_input_tokens": 496},
            },
        ]
        from v3.jineng.l4_youhua_guancha import _cache_prefix_metrics

        metrics = _cache_prefix_metrics(rows)
        self.assertEqual(metrics["cache_prefix_count"], 2)
        self.assertEqual(metrics["cache_prefix_cold_start_calls"], 2)
        self.assertEqual(metrics["cache_prefix_warm_calls"], 2)
        self.assertEqual(metrics["cache_prefix_reuse_rate"], 0.5)
        self.assertEqual(metrics["warm_cache_hit_ratio"], 0.992)

    def test_dynamic_runtime_context_is_not_appended_to_system_prompt(self) -> None:
        source = (
            ROOT
            / "app"
            / "backend"
            / "tiangong-backend"
            / "v3"
            / "zongdiaodu.py"
        ).read_text(encoding="utf-8")
        omni_start = source.index("def _omni_body_skill_prompt")
        omni_end = source.index("def _minimax_m3_context_packing_enabled", omni_start)
        omni_prompt = source[omni_start:omni_end]
        simple_start = source.index("def _huanxing_simple_chain")
        simple_end = source.index("\n    def ", simple_start + 10)
        simple_chain = source[simple_start:simple_end]

        self.assertNotIn("_recent_local_artifact_context()", omni_prompt)
        self.assertIn(
            "yonghu_tishi = _user_prompt_with_context(yonghu_tishi, dynamic_context)",
            simple_chain,
        )
        self.assertNotIn("system_tishi += dynamic_context", simple_chain)

    def test_streaming_requests_explicitly_request_final_usage_chunk(self) -> None:
        endpoint = ModelEndpointConfig(
            service_preset="deepseek",
            provider_identity="deepseek_v4",
            protocol_family=ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-v4-pro",
            credential_scope="official_provider",
            reasoning_mode="off",
            endpoint_overrides={},
            optimization_family="deepseek_v4",
            config_fingerprint="test",
        )
        transport = OpenAIChatTransport()
        request = transport.build_request(
            endpoint,
            "test-key",
            {"messages": [{"role": "user", "content": "ping"}]},
        )

        self.assertTrue(request.payload["stream"])
        self.assertEqual(request.payload["stream_options"], {"include_usage": True})

        state = StreamState()
        usage = {
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
            "prompt_cache_hit_tokens": 8,
        }
        # OpenAI-compatible providers may send the final usage as a dedicated
        # SSE event with no choices. The protocol transport must still consume
        # it so observability survives the P18.1 transport boundary.
        text, reasoning = transport.consume_stream_event(state, {"choices": [], "usage": usage})
        self.assertEqual(text, "")
        self.assertEqual(reasoning, "")
        self.assertEqual(state.usage, usage)

        turn = transport.finalize_turn(endpoint, state)
        self.assertEqual(turn.usage, usage)
        self.assertEqual(turn.protocol_family, ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value)


if __name__ == "__main__":
    unittest.main()
