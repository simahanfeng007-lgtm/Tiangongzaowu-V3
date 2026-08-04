import json
import tempfile
import unittest
from pathlib import Path

from communication_service.feishu_outbound import (
    FeishuApiResponse,
    FeishuCredentials,
    FeishuTokenResult,
)
from communication_service.wechat_file_outbound import WechatCdnUploadResponse
from communication_service.wechat_text_outbound import WechatIlinkResponse
from tests.protocol_simulators import (
    FeishuProtocolSimulator,
    ProtocolSimulationError,
    StreamScenario,
    WechatProtocolSimulator,
    load_protocol_samples,
)
from tests.security_file_corpus import security_corpus_sha256, security_file_corpus


SAMPLES = Path(__file__).parent / "fixtures" / "communication_protocol_samples.json"


class ProtocolSimulatorTests(unittest.TestCase):
    def test_sample_manifest_is_unique_redacted_and_covers_both_channel_matrices(self):
        samples = load_protocol_samples(SAMPLES)
        self.assertEqual(len(samples), 15)
        required = {
            "wechat.text.inbound.v1",
            "wechat.voice.inbound.v1",
            "wechat.image.inbound.v1",
            "wechat.video.inbound.v1",
            "wechat.file.inbound.v1",
            "feishu.text.inbound.v1",
            "feishu.post.inbound.v1",
            "feishu.image.inbound.v1",
            "feishu.file.inbound.v1",
            "feishu.api.rate-limited.v1",
            "feishu.api.scope-denied.v1",
        }
        self.assertTrue(required.issubset(samples))
        raw = SAMPLES.read_text(encoding="utf-8")
        lowered = raw.lower()
        self.assertNotIn("app_secret", lowered)
        self.assertNotIn("bearer ", lowered)
        self.assertNotIn("bot-token", lowered)
        self.assertNotIn("c:\\users", lowered)
        inbound = tuple(
            item for item in samples.values() if item["direction"] == "inbound"
        )
        self.assertTrue(
            all(
                "synthetic" in json.dumps(item, ensure_ascii=False)
                or "<redacted" in json.dumps(item, ensure_ascii=False)
                for item in inbound
            )
        )

    def test_wechat_simulator_is_scripted_ordered_and_redacts_tokens(self):
        simulator = WechatProtocolSimulator()
        simulator.script(
            "message.send",
            WechatIlinkResponse(200, {"ret": 0, "errcode": 0}, "1" * 64),
        )
        response = simulator.send_message(
            {"msg": {"client_id": "synthetic-client"}},
            bot_token="secret-that-must-not-be-recorded",
            timeout_seconds=5,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(simulator.call_count("message.send"), 1)
        self.assertNotIn(
            b"secret-that-must-not-be-recorded",
            repr(simulator.calls).encode(),
        )
        with self.assertRaises(ProtocolSimulationError):
            simulator.send_message({}, bot_token="x", timeout_seconds=1)

    def test_wechat_stream_and_upload_simulation_cover_truncation_and_progress(self):
        simulator = WechatProtocolSimulator()
        simulator.script(
            "media.open",
            StreamScenario(
                200,
                b"abcdefgh",
                declared_length=12,
                max_chunk_bytes=3,
                fail_after_bytes=6,
            ),
        )
        response = simulator.open("https://synthetic.invalid/media", timeout_seconds=5)
        self.assertEqual(response.read(10), b"abc")
        self.assertEqual(response.read(10), b"def")
        with self.assertRaises(OSError):
            response.read(10)
        response.close()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cipher.part"
            path.write_bytes(b"ciphertext")
            simulator.script(
                "upload.ciphertext",
                WechatCdnUploadResponse(200, "synthetic-ref", "2" * 64, 10),
            )
            progress = []
            result = simulator.upload_ciphertext(
                "https://synthetic.invalid/upload",
                path,
                ciphertext_size=10,
                timeout_seconds=5,
                max_response_bytes=1024,
                progress_callback=progress.append,
            )
        self.assertEqual(result.encrypted_query_param, "synthetic-ref")
        self.assertEqual(progress, [10])

    def test_feishu_simulator_supports_token_resource_send_and_faults(self):
        simulator = FeishuProtocolSimulator()
        simulator.script(
            "token.fetch",
            FeishuTokenResult(200, 0, "synthetic-access-token", 7200, "3" * 64),
        )
        token = simulator.fetch_tenant_token(
            FeishuCredentials("synthetic-app", "secret-not-recorded"),
            timeout_seconds=5,
        )
        self.assertEqual(token.code, 0)
        simulator.script(
            "message.send",
            FeishuApiResponse(429, 99991400, {}, "4" * 64, retry_after_ms=1000),
        )
        response = simulator.send_message(
            chat_id="synthetic-chat",
            reply_to_message_id=None,
            reply_in_thread=False,
            msg_type="text",
            content={"text": "synthetic"},
            dedup_uuid="synthetic-uuid",
            access_token="synthetic-access-token",
            timeout_seconds=5,
        )
        self.assertEqual(response.status_code, 429)
        self.assertNotIn(b"secret-not-recorded", repr(simulator.calls).encode())

    def test_security_file_corpus_is_deterministic_and_has_required_attack_classes(self):
        first = security_file_corpus()
        second = security_file_corpus()
        self.assertEqual(first, second)
        self.assertEqual(
            security_corpus_sha256(),
            "8bfe89b90322ea6cb30bac380cd07473d3028c3ecd2bd2a0d598773a02698fa8",
        )
        ids = {case.case_id for case in first}
        self.assertTrue(
            {
                "valid.docx.1000-chars",
                "invalid.docx.246-byte-placeholder",
                "invalid.docx.1kb-placeholder",
                "invalid.docx.macro",
                "invalid.zip.path-traversal",
                "invalid.zip.compression-ratio",
                "invalid.image.pseudo-png",
                "invalid.declared.oversize",
            }.issubset(ids)
        )
        self.assertTrue(any(case.expected_gate_accept for case in first))
        self.assertTrue(any(not case.expected_gate_accept for case in first))


if __name__ == "__main__":
    unittest.main()
