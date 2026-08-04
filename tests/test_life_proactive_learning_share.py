from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime


class ProactiveLearningShareTests(unittest.TestCase):
    """Regression: learning reports must reach the proactive-chat queue with
    model-written copy when a share writer is installed, and degrade to the
    deterministic template otherwise."""

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

    def test_report_falls_back_to_template_without_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                message = life._learning_report(self._record(life_id))
                self.assertEqual(message["kind"], "learning_report")
                self.assertEqual(
                    message["text"],
                    "学习完成：光合作用机制。已写入知识库，想听的话我跟你说说。",
                )
                queued = life._scope_state(life_id)["proactive_chats"]
                self.assertEqual([row["message_id"] for row in queued], [message["message_id"]])
            finally:
                life.close()

    def test_report_uses_installed_model_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                seen_materials = []

                def writer(material: dict) -> str:
                    seen_materials.append(dict(material))
                    return "我刚研究了光合作用机制，把光反应和碳反应的要点整理进了知识库，随时可以讲给你听。"

                life.set_learning_share_writer(writer)
                message = life._learning_report(self._record(life_id))
                self.assertEqual(
                    message["text"],
                    "我刚研究了光合作用机制，把光反应和碳反应的要点整理进了知识库，随时可以讲给你听。",
                )
                self.assertEqual(seen_materials[0]["share_request"], True)
                self.assertEqual(seen_materials[0]["title"], "光合作用机制")
            finally:
                life.close()

    def test_report_writer_failure_and_empty_output_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                template = "学习完成：光合作用机制。已写入知识库，想听的话我跟你说说。"

                life.set_learning_share_writer(lambda _material: (_ for _ in ()).throw(RuntimeError("model down")))
                self.assertEqual(life._learning_report(self._record(life_id))["text"], template)

                life.set_learning_share_writer(lambda _material: "   ")
                self.assertEqual(life._learning_report(self._record(life_id))["text"], template)
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

    def test_pending_endpoint_then_ack_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(temporary)
            try:
                life_id = str(life._active()["life_id"])
                message = life._learning_report(self._record(life_id))

                status, payload, _ = life.request("GET", "/api/v1/v3/life/proactive-chat/pending")
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual([row["message_id"] for row in payload["messages"]], [message["message_id"]])

                status, ack, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/proactive-chat/ack",
                    {"message_id": message["message_id"], "actor": "frontend"},
                )
                self.assertEqual(status, 200)
                self.assertTrue(ack["ok"])
                self.assertTrue(ack["found"])

                status, payload, _ = life.request("GET", "/api/v1/v3/life/proactive-chat/pending")
                self.assertEqual(status, 200)
                self.assertEqual(payload["messages"], [])
            finally:
                life.close()


if __name__ == "__main__":
    unittest.main()
