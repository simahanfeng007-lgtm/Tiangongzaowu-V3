from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from total_gateway.bootstrap import GatewayConfig
from total_gateway.desktop_api import DesktopApiConfig, DesktopApiError, DesktopApiRouter
from total_gateway.runtime import GatewayRuntime

ROOT = Path(__file__).resolve().parents[1]


class _NoopOrchestration:
    def status_payload(self):
        return {"configured": True, "running": False}
    def close(self):
        return None


class ExtremeChat15(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.now_ms = int(time.time() * 1000)
        cls.runtime = GatewayRuntime.start(
            GatewayConfig(
                environment="test", port=0, state_root=cls.root / "gateway",
                min_free_bytes=1_048_576, disk_probe_interval_ms=100,
            ),
            now_ms=cls.now_ms,
        )
        cls.runtime.orchestration = _NoopOrchestration()  # type: ignore[assignment]
        cls.router = DesktopApiRouter(
            cls.runtime,
            DesktopApiConfig(
                desktop_token="d" * 48,
                backend_internal_token="b" * 48,
                life_internal_token="l" * 48,
                communication_internal_token="c" * 48,
                artifact_open_token="o" * 48,
                backend_port=1, life_port=1, communication_port=1,
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.runtime.close()
        cls.temporary.cleanup()

    def _payload(self, suffix: str, *, text: str = "你好", session: str | None = None,
                 message: str | None = None, submitted: int | None = None, attachments=None):
        return {
            "attachments": [] if attachments is None else attachments,
            "message_id": message or f"message-{suffix}",
            "presentation_request_id": f"presentation-{suffix}",
            "session_id": session or f"session-{suffix}",
            "submitted_at_ms": self.now_ms if submitted is None else submitted,
            "text": text,
        }

    @staticmethod
    def _decode(response):
        return json.loads(response.body.decode("utf-8"))

    def _register(self, payload, *, now_ms=None):
        return self.router._register_desktop_inbound(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            now_ms=self.now_ms if now_ms is None else now_ms,
        )

    def test_c01_whitespace_only_message_is_rejected(self):
        with self.assertRaisesRegex(DesktopApiError, "text.invalid"):
            self._register(self._payload("c01", text=" \n\t "))

    def test_c02_nul_in_message_is_rejected(self):
        with self.assertRaisesRegex(DesktopApiError, "text.invalid"):
            self._register(self._payload("c02", text="hello\x00world"))

    def test_c03_message_over_100k_characters_is_rejected(self):
        with self.assertRaisesRegex(DesktopApiError, "text.invalid"):
            self._register(self._payload("c03", text="a" * 100_001))

    def test_c04_future_clock_skew_over_five_seconds_is_rejected(self):
        with self.assertRaisesRegex(DesktopApiError, "submitted_at_ms.invalid"):
            self._register(self._payload("c04", submitted=self.now_ms + 5_001))

    def test_c05_retry_with_new_timestamp_is_idempotent(self):
        payload = self._payload("c05")
        first = self._decode(self._register(payload))
        payload["submitted_at_ms"] = self.now_ms + 1000
        second = self._decode(self._register(payload, now_ms=self.now_ms + 1000))
        self.assertEqual(first["gateway_request_id"], second["gateway_request_id"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])

    def test_c06_same_message_identity_with_changed_text_conflicts(self):
        payload = self._payload("c06", text="first")
        self._register(payload)
        payload["text"] = "changed"
        with self.assertRaisesRegex(DesktopApiError, "request.conflict"):
            self._register(payload)

    def test_c07_same_message_label_in_different_sessions_is_isolated(self):
        first = self._decode(self._register(self._payload("c07a", session="session-c07-a", message="shared-message")))
        second = self._decode(self._register(self._payload("c07b", session="session-c07-b", message="shared-message")))
        self.assertNotEqual(first["gateway_request_id"], second["gateway_request_id"])
        self.assertFalse(second["duplicate"])

    def test_c08_raw_attachment_path_is_rejected_at_object_boundary(self):
        with self.assertRaisesRegex(DesktopApiError, "object_ref_required"):
            self._register(self._payload("c08", attachments=[{"path": "C:/secret.txt"}]))

    def test_c09_near_limit_unicode_emoji_message_is_accepted(self):
        payload = self._payload("c09", text="测" * 49_999 + "🧠" * 49_999)
        decoded = self._decode(self._register(payload))
        self.assertTrue(decoded["ok"])
        self.assertFalse(decoded["duplicate"])

    def test_c10_duplicate_json_keys_are_rejected(self):
        payload = self._payload("c10")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        raw = raw[:-1] + ',"text":"shadow"}'
        with self.assertRaisesRegex(DesktopApiError, "json.duplicate_key"):
            self.router._register_desktop_inbound(raw.encode("utf-8"), now_ms=self.now_ms)

    def test_c11_non_finite_json_number_is_rejected(self):
        payload = self._payload("c11")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        raw = raw.replace(str(self.now_ms), "NaN", 1)
        with self.assertRaisesRegex(DesktopApiError, "json.non_finite"):
            self.router._register_desktop_inbound(raw.encode("utf-8"), now_ms=self.now_ms)

    def test_c12_thirty_two_concurrent_sessions_never_share_request_id(self):
        barrier = threading.Barrier(32)
        def submit(index: int):
            barrier.wait(timeout=5)
            return self._decode(self._register(self._payload(f"c12-{index}")))["gateway_request_id"]
        with ThreadPoolExecutor(max_workers=32) as pool:
            request_ids = list(pool.map(submit, range(32)))
        self.assertEqual(len(set(request_ids)), 32)

    def test_c13_concurrent_retries_execute_one_logical_registration(self):
        payload = self._payload("c13")
        barrier = threading.Barrier(24)
        def submit(_index: int):
            barrier.wait(timeout=5)
            return self._decode(self._register(dict(payload)))
        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(submit, range(24)))
        self.assertEqual(len({row["gateway_request_id"] for row in results}), 1)
        self.assertEqual(sum(1 for row in results if row["duplicate"] is False), 1)

    def _run_sse_probe(self, stream_text: str):
        script = self.root / f"sse-{time.time_ns()}.mjs"
        module_uri = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script.write_text(f'''
          globalThis.window = {{
            setTimeout, clearTimeout,
            tiangongDesktop: {{ getGatewayUrl: () => "http://127.0.0.1:7184", getGatewayHeaders: () => ({{}}) }}
          }};
          globalThis.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
          const encoder = new TextEncoder();
          const streamText = {json.dumps(stream_text)};
          globalThis.fetch = async () => new Response(new ReadableStream({{
            start(controller) {{ controller.enqueue(encoder.encode(streamText)); controller.close(); }}
          }}), {{ status: 200, headers: {{ "content-type": "text/event-stream" }} }});
          const {{ fetchSse }} = await import({json.dumps(module_uri)});
          const state = {{ text: "", done: 0, errors: [] }};
          await fetchSse("/probe", {{}}, {{
            onText: (value) => state.text += value,
            onDone: () => state.done += 1,
            onError: (value) => state.errors.push(String(value)),
          }});
          console.log(JSON.stringify(state));
        ''', encoding="utf-8")
        completed = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=20, check=True)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_c14_duplicate_sse_event_id_is_consumed_once(self):
        event = 'id: 1\nevent: delta\ndata: {"content":"A"}\n\n'
        done = 'id: 2\nevent: done\ndata: {"status":"completed"}\n\n'
        result = self._run_sse_probe(event + event + done)
        self.assertEqual(result, {"text": "A", "done": 1, "errors": []})

    def test_c15_sse_disconnect_without_terminal_is_explicit_error(self):
        result = self._run_sse_probe('id: 9\nevent: delta\ndata: {"content":"partial"}\n\n')
        self.assertEqual(result["text"], "partial")
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("before a terminal event", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
