from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime


class ProactiveLearningShareTests(unittest.TestCase):
    """Post-P15 contract: legacy learning chat delivery is frozen.

    The learning report remains deterministic journal/report metadata, while
    proactive-chat pending/ack stays available as neutral delivery substrate
    for the future native Life initiative path.
    """

    def _runtime(self, temporary: str) -> EmbeddedLifeRuntime:
        root = Path(temporary)
        life = EmbeddedLifeRuntime(
            data_root=root / "life-data",
            runtime_root=root / "life-runtime",
            mode="embedded",
        )
        life.scheduler.stop(timeout_seconds=2)
        return life

    def _record(self, life_id: str) -> dict:
        return {
            "learning_id": "lrn_share_test",
            "status": "published",
            "title": "光合作用机制",
            "target": "knowledge",
            "artifact_id": "art_123",
            "life_id": life_id,
        }

    def test_report_is_suppressed_metadata_without_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                message = life._learning_report(self._record(life_id))
                self.assertEqual(message["kind"], "learning_report")
                self.assertEqual(message["text"], "学习完成：光合作用机制。已写入知识库。")
                self.assertEqual(message["delivery"], "legacy_proactive_frozen")
                self.assertTrue(message["suppressed"])
                self.assertEqual(
                    message["reason_code"],
                    "life.proactive.legacy_producer_frozen",
                )
                self.assertEqual(life._scope_state(life_id)["proactive_chats"], [])
            finally:
                life.close()

    def test_installed_legacy_share_writer_is_not_invoked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                seen_materials: list[dict] = []

                def writer(material: dict) -> str:
                    seen_materials.append(dict(material))
                    return "旧学习分享"

                life.set_learning_share_writer(writer)
                message = life._learning_report(self._record(life_id))
                self.assertEqual(seen_materials, [])
                self.assertEqual(message["delivery"], "legacy_proactive_frozen")
                self.assertEqual(life._scope_state(life_id)["proactive_chats"], [])
            finally:
                life.close()

    def test_writer_failure_or_empty_output_is_irrelevant_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                life.set_learning_share_writer(
                    lambda _material: (_ for _ in ()).throw(RuntimeError("model down"))
                )
                first = life._learning_report(self._record(life_id))
                life.set_learning_share_writer(lambda _material: "   ")
                second = life._learning_report(self._record(life_id))
                self.assertEqual(first["text"], "学习完成：光合作用机制。已写入知识库。")
                self.assertEqual(second["text"], first["text"])
                self.assertEqual(life._scope_state(life_id)["proactive_chats"], [])
            finally:
                life.close()

    def test_set_learning_share_writer_rejects_non_callable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                with self.assertRaises(TypeError):
                    life.set_learning_share_writer("not-a-callable")
            finally:
                life.close()

    def test_pending_ack_substrate_remains_available_for_native_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                message = {
                    "message_id": "native_test_message",
                    "kind": "life_initiative",
                    "text": "native initiative test",
                    "created_at": "2026-08-12T00:00:00Z",
                    "read": False,
                    "delivery": "normal_conversation_pending",
                }
                life._scope_state(life_id)["proactive_chats"].append(message)

                status, payload, _ = life.request(
                    "GET", "/api/v1/v3/life/proactive-chat/pending"
                )
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(
                    [row["message_id"] for row in payload["messages"]],
                    [message["message_id"]],
                )

                status, ack, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/proactive-chat/ack",
                    {"message_id": message["message_id"], "actor": "frontend"},
                )
                self.assertEqual(status, 200)
                self.assertTrue(ack["ok"])
                self.assertTrue(ack["found"])

                status, payload, _ = life.request(
                    "GET", "/api/v1/v3/life/proactive-chat/pending"
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["messages"], [])
            finally:
                life.close()


if __name__ == "__main__":
    unittest.main()
