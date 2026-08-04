from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from v3 import body_settings, voice_output  # noqa: E402
from total_gateway.desktop_api import DesktopApiRouter  # noqa: E402


class VoiceOutputContractTests(unittest.TestCase):
    def test_capabilities_do_not_claim_voice_cloning_from_a_local_sample(self):
        with mock.patch.object(voice_output, "_native_configured", return_value=False), mock.patch.object(
            voice_output, "_edge_tts_available", return_value=False
        ):
            result = voice_output.capabilities()
        self.assertEqual(result["schema"], "tiangong.v3.voice-output.v1")
        self.assertFalse(result["capabilities"]["voice_cloning"])
        self.assertEqual(result["reasons"]["voice_cloning"], "no_authorized_cloning_provider_configured")

    def test_auto_prefers_native_model_and_never_receives_sample_path(self):
        native = mock.Mock(return_value=(b"ID3voice", "audio/mpeg"))
        with mock.patch.object(voice_output, "_native_configured", return_value=True), mock.patch.object(
            voice_output, "_native_synthesize", native
        ), mock.patch.object(voice_output, "_edge_tts_available", return_value=True):
            result = voice_output.synthesize(
                {"text": "测试", "mode": "auto", "voice_id": "approved-voice"},
                {"custom_voice_path": "C:/private/sample.wav", "sample_consent": True},
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "native_model")
        self.assertEqual(base64.b64decode(result["audio_base64"]), b"ID3voice")
        native.assert_called_once_with("测试", "approved-voice")

    def test_unavailable_service_returns_explicit_browser_fallback(self):
        with mock.patch.object(voice_output, "_native_configured", return_value=False), mock.patch.object(
            voice_output, "_edge_tts_available", return_value=False
        ):
            result = voice_output.synthesize({"text": "测试", "mode": "auto"}, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "voice_output.browser_fallback_required")
        self.assertIn("native_endpoint_not_configured", result["attempts"])
        self.assertIn("edge_tts_not_installed", result["attempts"])

    def test_voice_settings_round_trip_engine_and_authorization_without_persisting_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "body-settings.json"
            soul_path = Path(temp_dir) / "soul.txt"
            with mock.patch.object(body_settings, "BODY_SETTINGS_LUJING", settings_path), mock.patch.object(
                body_settings, "SOUL_LUJING", soul_path
            ):
                saved = body_settings.save_body_settings({
                    "voice": {
                        "output_mode": "native_model",
                        "native_voice_id": "approved-voice",
                        "sample_consent": True,
                        "custom_voice_path": "C:/private/sample.wav",
                    }
                })
            voice = saved["voice"]
        self.assertEqual(voice["output_mode"], "native_model")
        self.assertEqual(voice["native_voice_id"], "approved-voice")
        self.assertTrue(voice["sample_consent"])
        self.assertEqual(voice["custom_voice_path"], "C:/private/sample.wav")

    def test_frontend_has_one_reply_owner_and_does_not_replay_sample_file(self):
        body_panel = (ROOT / "app/frontend-v2/renderer/plugins/body-panel.mjs").read_text(encoding="utf-8")
        chat_panel = (ROOT / "app/frontend-v2/renderer/plugins/conversation-panel.mjs").read_text(encoding="utf-8")
        vrm_panel = (ROOT / "app/frontend-v2/renderer/plugins/vrm-inspector-panel.mjs").read_text(encoding="utf-8")
        runtime = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").read_text(encoding="utf-8")
        self.assertIn('requestVoiceOutput({', chat_panel)
        self.assertIn('requestVoiceOutput({', body_panel)
        self.assertNotIn("function playCustomVoiceSample", body_panel)
        self.assertNotIn("function localFileUrl", body_panel)
        self.assertNotIn('state.on("messages", speakLatestAssistant);', vrm_panel)
        self.assertIn("export async function requestVoiceOutput", runtime)

    def test_gateway_exposes_the_reviewed_voice_capability_and_synthesis_routes(self):
        capability = DesktopApiRouter.route_for("GET", "/api/v1/body/voice/capabilities")
        synthesize = DesktopApiRouter.route_for("POST", "/api/v1/body/voice/synthesize")
        self.assertIsNotNone(capability)
        self.assertIsNotNone(synthesize)
        self.assertEqual(capability.upstream, "backend")
        self.assertEqual(synthesize.upstream, "backend")
        self.assertGreaterEqual(synthesize.timeout_seconds, 45)


if __name__ == "__main__":
    unittest.main()
