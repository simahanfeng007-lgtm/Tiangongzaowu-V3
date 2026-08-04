"""V15 confirmation retirement（草案 §4.2）：单调 epoch CAS + receipt 幂等提交。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from total_gateway.store import GatewayStateStore, STORE_SCHEMA_VERSION, StoreConflictError


class ConfirmationRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(Path(self.temporary.name) / "gateway.sqlite3", now_ms=900)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_schema_v15(self) -> None:
        version = self.store._connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, STORE_SCHEMA_VERSION)
        status = self.store.confirmation_retirement_status()
        self.assertEqual(status["confirmation_retirement_epoch"], 0)
        self.assertFalse(status["retired"])
        self.assertFalse(status["receipt_committed"])

    def test_commit_epoch_monotonic(self) -> None:
        epoch1 = self.store.commit_confirmation_retirement(reason="G3 retirement", now_ms=1_000)
        self.assertEqual(epoch1, 1)
        status = self.store.confirmation_retirement_status()
        self.assertTrue(status["retired"])
        self.assertEqual(status["retired_at_ms"], 1_000)
        self.assertFalse(status["receipt_committed"])

    def test_receipt_requires_matching_epoch_and_commits_once(self) -> None:
        with self.assertRaises(StoreConflictError):
            self.store.commit_confirmation_retirement_receipt(
                receipt={"step": "retire"}, expected_epoch=1, now_ms=1_000,
            )
        epoch = self.store.commit_confirmation_retirement(reason="G3", now_ms=1_000)
        digest = self.store.commit_confirmation_retirement_receipt(
            receipt={"epoch": epoch, "steps": ["fence", "410", "archive", "frontend"]},
            expected_epoch=epoch,
            now_ms=1_100,
        )
        self.assertEqual(len(digest), 64)
        # 幂等：同内容重复提交返回同一 digest
        again = self.store.commit_confirmation_retirement_receipt(
            receipt={"epoch": epoch, "steps": ["fence", "410", "archive", "frontend"]},
            expected_epoch=epoch,
            now_ms=1_200,
        )
        self.assertEqual(again, digest)
        # 不同内容 → 冲突
        with self.assertRaises(StoreConflictError):
            self.store.commit_confirmation_retirement_receipt(
                receipt={"epoch": epoch, "steps": ["different"]},
                expected_epoch=epoch,
                now_ms=1_300,
            )
        status = self.store.confirmation_retirement_status()
        self.assertTrue(status["receipt_committed"])
        self.assertEqual(status["receipt_committed_at_ms"], 1_100)


if __name__ == "__main__":
    unittest.main()
