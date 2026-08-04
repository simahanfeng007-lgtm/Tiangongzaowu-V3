from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from contracts import canonical_sha256
from life_service.embedded_runtime import EmbeddedLifeRuntime


class LifeJournalProjectionRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = mock.patch.dict(os.environ, {"TIANGONG_LIFE_HEARTBEAT_SECONDS": "3600"})
        self.env.start()
        self.runtime = EmbeddedLifeRuntime(
            data_root=self.root / "life-data",
            runtime_root=self.root / "runtime",
            mode="embedded",
        )

    def tearDown(self) -> None:
        try:
            self.runtime.close()
        except Exception:
            pass
        self.env.stop()
        self.temporary.cleanup()

    def _reopen(self) -> EmbeddedLifeRuntime:
        return EmbeddedLifeRuntime(
            data_root=self.root / "life-data",
            runtime_root=self.root / "runtime",
            mode="embedded",
        )

    def test_memory_journal_event_recovers_projection_after_persist_failure(self):
        original = self.runtime._persist
        self.runtime._persist = mock.Mock(side_effect=OSError("disk-full-after-journal"))  # type: ignore[method-assign]
        status, value, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/assert",
            {
                "memory_id": "mem-recovery",
                "memory_type": "semantic",
                "content": {"survives": True},
                "provenance": {"source": "fault-injection"},
                "relations": [],
            },
        )
        self.assertEqual(status, 500)
        self.assertEqual(value["error_code"], "life.embedded.failed")
        self.runtime._persist = original  # type: ignore[method-assign]
        self.runtime.close()
        self.runtime = self._reopen()
        status, stats, _ = self.runtime.request("GET", "/api/v1/v3/life/memory/stats", {})
        self.assertEqual(status, 200)
        self.assertEqual(stats["total"], 1)
        status, search, _ = self.runtime.request(
            "POST", "/api/v1/v3/life/memory/search", {"query": "survives"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(search["results"][0]["memory_id"], "mem-recovery")

    def test_execution_journal_event_recovers_projection_after_persist_failure(self):
        life_id = str(self.runtime.system.identities.active(required=True)["life_id"])
        payload = {
            "schema": "tiangong.life.execution-terminal.v1",
            "request_id": "request-recovery",
            "run_id": "run-recovery",
            "generation": 1,
            "life_id": life_id,
            "session_scope_hash": canonical_sha256({"session": "recovery"}),
            "status": "completed",
            "user_goal_sha256": canonical_sha256({"goal": "recovery"}),
            "final_result_sha256": canonical_sha256({"result": "recovery"}),
            "fact_ids": ["fact-recovery"],
            "completed_at_ms": 1_780_000_000_001,
        }
        original = self.runtime._persist
        self.runtime._persist = mock.Mock(side_effect=OSError("disk-full-after-journal"))  # type: ignore[method-assign]
        status, value, _ = self.runtime.request(
            "POST", "/api/v1/v3/life/execution/commit", payload
        )
        self.assertEqual(status, 500)
        self.assertEqual(value["error_code"], "life.embedded.failed")
        self.runtime._persist = original  # type: ignore[method-assign]
        self.runtime.close()
        self.runtime = self._reopen()
        status, recovered, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/execution/recover",
            {"request_id": "request-recovery"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(recovered["found"])
        self.assertEqual(recovered["execution"]["final_result_sha256"], payload["final_result_sha256"])
        self.assertTrue(self.runtime.system.journal.verify(life_id)["valid"])

    def test_signed_head_deletion_after_migration_fails_closed_on_restart(self):
        status, _, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/assert",
            {
                "memory_id": "mem-head-deletion",
                "memory_type": "semantic",
                "content": {"anchor": True},
                "provenance": {"source": "fault-injection"},
                "relations": [],
            },
        )
        self.assertEqual(status, 200)
        life_id = str(self.runtime.system.identities.active(required=True)["life_id"])
        head_path = self.runtime.system.journal._head_path(life_id)
        self.assertTrue(head_path.is_file())
        self.runtime.close()
        head_path.unlink()
        with self.assertRaises(Exception) as caught:
            self._reopen()
        self.assertEqual(getattr(caught.exception, "code", ""), "journal_head_missing")



if __name__ == "__main__":
    unittest.main()
