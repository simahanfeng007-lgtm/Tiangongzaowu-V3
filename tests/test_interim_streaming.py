from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class InterimTextEmitterTests(unittest.TestCase):
    """流式文本节流累积：前端轮询依赖 last_interim_reply_text 实时刷新。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.zongdiaodu = importlib.import_module("v3.zongdiaodu")
        cls.emitter_cls = cls.zongdiaodu.Zongdiaodu._InterimTextEmitter

    def test_cumulative_replacement_per_chunk(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.0, min_chars=1)
        emitter.push("公")
        emitter.push("子")
        emitter.push("，")
        self.assertEqual(received, ["公", "公子", "公子，"])

    def test_throttle_by_chars(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=60, min_chars=10)
        emitter.push("12345")
        emitter.push("1234")
        self.assertEqual(received, [])
        emitter.push("6")
        self.assertEqual(received, ["1234512346"])

    def test_throttle_by_interval(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.2, min_chars=1000)
        emitter.push("a")
        emitter.push("b")
        self.assertEqual(received, [])
        time.sleep(0.25)
        emitter.push("c")
        self.assertEqual(received, ["abc"])

    def test_flush_marks_emitted_and_skips_empty(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=60, min_chars=1000)
        emitter.push("abc")
        emitter.flush()
        self.assertEqual(received, ["abc"])
        received.clear()
        emitter.flush()
        self.assertEqual(received, [])
        emitter.push("def")
        emitter.flush()
        self.assertEqual(received, ["abcdef"])

    def test_empty_chunks_ignored(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.0, min_chars=1)
        emitter.push("")
        emitter.push(None)  # type: ignore[arg-type]
        self.assertEqual(received, [])

    def test_reset_clears_accumulated_text(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.0, min_chars=1)
        emitter.push("思考内容")
        self.assertEqual(received, ["思考内容"])
        emitter.reset()
        emitter.push("正文内容")
        self.assertEqual(received[-1], "正文内容")


class InterimStreamRouterTests(unittest.TestCase):
    """思考先流、正文开始后清空思考只流正文（Codex 式逐段展示）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.zongdiaodu = importlib.import_module("v3.zongdiaodu")
        cls.emitter_cls = cls.zongdiaodu.Zongdiaodu._InterimTextEmitter
        cls.router_cls = cls.zongdiaodu.Zongdiaodu._InterimStreamRouter

    def test_reasoning_streams_then_visible_replaces(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.0, min_chars=1)
        router = self.router_cls(emitter)
        router.push_reasoning("思考第一段")
        router.push_reasoning("思考第二段")
        self.assertEqual(received[-1], "思考第一段思考第二段")
        router.push_visible("正文第一段")
        self.assertEqual(received[-1], "正文第一段")
        router.push_visible("正文第二段")
        self.assertEqual(received[-1], "正文第一段正文第二段")

    def test_reasoning_after_visible_ignored(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.0, min_chars=1)
        router = self.router_cls(emitter)
        router.push_visible("正文")
        router.push_reasoning("后面的思考不再显示")
        self.assertEqual(received, ["正文"])

    def test_visible_without_reasoning(self) -> None:
        received: list[str] = []
        emitter = self.emitter_cls(received.append, min_interval_seconds=0.0, min_chars=1)
        router = self.router_cls(emitter)
        router.push_visible("直接正文")
        self.assertEqual(received, ["直接正文"])


class RunControlInterimReplyTests(unittest.TestCase):
    """interim_reply 写入 run 状态：前端轮询读取 last_interim_reply_text。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = importlib.import_module("v3.duihua_qiaojie")

    def test_long_streaming_text_not_truncated_at_500(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"TIANGONG_RUN_STATE_DIR": temporary}, clear=False):
                manager = self.bridge.RunControlManager()
                request_id = "req_interim_stream_001"
                _handle, disposition, _cached = manager.claim(request_id, "流式测试")
                self.assertEqual(disposition, "started")
                long_text = "公" * 30_000
                result = manager.interim_reply(request_id, long_text)
                self.assertTrue(result.get("ok"))
                run = manager._runs[request_id]
                self.assertEqual(run["interim_reply_count"], 1)
                self.assertGreater(len(run["last_interim_reply_text"]), 500)
                self.assertLessEqual(len(run["last_interim_reply_text"]), self.bridge.INTERIM_REPLY_MAX_CHARS)

    def test_duplicate_interim_reply_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"TIANGONG_RUN_STATE_DIR": temporary}, clear=False):
                manager = self.bridge.RunControlManager()
                request_id = "req_interim_stream_002"
                _handle, disposition, _cached = manager.claim(request_id, "流式测试")
                self.assertEqual(disposition, "started")
                self.assertTrue(manager.interim_reply(request_id, "公子，妾身记下了。").get("ok"))
                skipped = manager.interim_reply(request_id, "公子，妾身记下了。")
                self.assertFalse(skipped.get("ok"))
                self.assertEqual(skipped.get("skipped"), "duplicate_interim_reply")
                self.assertEqual(manager._runs[request_id]["interim_reply_count"], 1)


if __name__ == "__main__":
    unittest.main()
