from __future__ import annotations

import importlib
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


class ContextAttachmentRegressionTests(unittest.TestCase):
    def test_new_request_does_not_inherit_previous_run_state(self) -> None:
        bridge = importlib.import_module("v3.duihua_qiaojie")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            state_root = home / ".tiangong" / "v3" / "simple_chain_run_state"
            state_root.mkdir(parents=True)
            (state_root / "req_previous.json").write_text(
                json.dumps({
                    "schema": "tiangong.v3.simple_chain.run_state.v2",
                    "run_id": "req_previous",
                    "request_id": "req_previous",
                    "session_id": "session_same",
                    "status": "complete",
                    "work_intent": {"message_preview": "stale previous request"},
                }),
                encoding="utf-8",
            )
            context = {
                "request_id": "req_current",
                "session_id": "session_same",
                "current_user_message": "fresh current request",
            }
            with mock.patch.object(bridge.Path, "home", return_value=home):
                envelope = bridge._build_context_envelope(context, "fresh current request")
            self.assertEqual(envelope["current_user_text"], "fresh current request")
            self.assertEqual(envelope["run_state"], {})

    def test_current_attachment_parser_skips_source_partition_sentinel(self) -> None:
        scheduler = importlib.import_module("v3.zongdiaodu")
        bridge = importlib.import_module("v3.duihua_qiaojie")
        attachment = [{
            "filename": "screen.png",
            "path": r"C:\workspace\.in\screen.png",
            "content_ref": r"C:\workspace\.in\screen.png",
        }]
        context = (
            "【本轮附件】\n"
            + bridge._source_partition_wrap(
                bridge.SOURCE_TYPE_EXTERNAL_DATA,
                json.dumps(attachment, ensure_ascii=False, indent=2),
                object_id="current_attachments",
            )
        )
        self.assertEqual(
            scheduler._simple_chain_attachment_paths_from_context(context),
            [r"C:\workspace\.in\screen.png"],
        )

    def test_image_attachment_adds_untrusted_visual_observation(self) -> None:
        scheduler = importlib.import_module("v3.zongdiaodu")
        bridge = importlib.import_module("v3.duihua_qiaojie")
        image_path = r"C:\workspace\.in\screen.png"
        context = (
            "【本轮附件】\n"
            + bridge._source_partition_wrap(
                bridge.SOURCE_TYPE_EXTERNAL_DATA,
                json.dumps([{"filename": "screen.png", "path": image_path}], ensure_ascii=False),
                object_id="current_attachments",
            )
        )
        vision_result = {
            "zhuangtai": "wancheng",
            "vision_state": "ok",
            "neirong": "visible screenshot text",
            "xinxi": {"width": 100, "height": 80},
        }
        with mock.patch.object(scheduler.JIROU, "_tupianjiance", return_value=vision_result) as inspect:
            rendered = scheduler._simple_chain_with_current_image_observations(context, "read the screenshot")
        inspect.assert_called_once_with(image_path=image_path, question=mock.ANY)
        self.assertIn("current_attachment_visual_observations", rendered)
        self.assertIn("visible screenshot text", rendered)
        self.assertIn('"semantic_visibility": "visible"', rendered)
        self.assertIn('"source_type":"EXTERNAL_DATA"', rendered)


if __name__ == "__main__":
    unittest.main()
