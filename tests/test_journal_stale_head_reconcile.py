from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from life_service.embedded_runtime import EmbeddedLifeRuntime


class JournalStaleHeadReconcile(unittest.TestCase):
    """锚点滞后头（写者落头前崩溃）前滚重锚；截断/篡改保持 fail-closed。"""

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

    def _add_events(self, n: int) -> None:
        for i in range(n):
            status, value, _ = self.runtime.request(
                "POST",
                "/api/v1/v3/life/memory/assert",
                {
                    "memory_id": f"mem-stale-{i}",
                    "memory_type": "semantic",
                    "content": {"i": i},
                    "provenance": {"source": "stale-head-test"},
                    "relations": [],
                },
            )
            self.assertEqual(status, 200, value)

    def _life_journal(self):
        life_id = str(self.runtime._active()["life_id"])
        journal = self.runtime.system.journal
        return life_id, journal

    def test_stale_prefix_head_is_forward_reanchored_on_restart(self):
        self._add_events(4)
        life_id, journal = self._life_journal()
        chain_before = journal.verify(life_id)
        self.assertTrue(chain_before["valid"])
        self.assertNotIn("head_stale", chain_before)
        total = int(chain_before["event_count"])
        self.assertGreaterEqual(total, 4)

        # 构造"写者落头前崩溃"：用真前缀（前 2 条）重签一个滞后头。
        events = journal._read_events_strict(life_id)
        prefix = events[:2]
        stale_chain = journal._verify_chain_only(life_id, prefix)
        self.assertTrue(stale_chain["valid"])
        journal._write_signed_head(life_id, stale_chain)

        # verify 应识别为锚点滞后（可前滚），而不是 mismatch。
        stale = journal.verify(life_id)
        self.assertTrue(stale["valid"])
        self.assertTrue(stale.get("head_stale"))
        self.assertEqual(int(stale["head_lag"]), total - 2)
        self.assertEqual(stale.get("reason_code"), "journal_head_stale")

        # 重启：ensure_hashed 前滚重锚到链尾，启动不崩，验证恢复 signed。
        self.runtime.close()
        self.runtime = self._reopen()
        reconciled = journal.verify(life_id)
        self.assertTrue(reconciled["valid"])
        self.assertNotIn("head_stale", reconciled)
        self.assertTrue(reconciled.get("journal_head_signed"))
        self.assertEqual(int(reconciled["event_count"]), total)

    def test_tampered_head_stays_fail_closed(self):
        self._add_events(3)
        life_id, journal = self._life_journal()
        # 篡改：用超出链长的伪造 chain 重签头（event_count+1、hash 全零）。
        forged = {
            "event_count": int(journal.verify(life_id)["event_count"]) + 1,
            "head_event_sha256": "0" * 64,
            "journal_sha256": "1" * 64,
        }
        journal._write_signed_head(life_id, forged)
        result = journal.verify(life_id)
        self.assertFalse(result["valid"])
        self.assertEqual(result.get("reason_code"), "journal_head_mismatch")
        self.assertNotIn("head_stale", result)
        with self.assertRaises(Exception):
            journal.ensure_hashed(life_id)

    def test_truncated_chain_stays_fail_closed(self):
        self._add_events(3)
        life_id, journal = self._life_journal()
        # 截断链：真实头不动，删掉最后一条事件文件内容（重写 jsonl 少一条）。
        path = journal._path(life_id)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        self.assertGreaterEqual(len(lines), 3)
        path.write_text("".join(lines[:-1]), encoding="utf-8")
        result = journal.verify(life_id)
        self.assertFalse(result["valid"])
        self.assertNotEqual(result.get("reason_code"), "journal_head_stale")
        with self.assertRaises(Exception):
            journal.ensure_hashed(life_id)


if __name__ == "__main__":
    unittest.main()
