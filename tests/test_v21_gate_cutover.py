from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import GatePromotionRecord
from contracts.cutover import derive_gate_promotion_id
from total_gateway.cutover_coordinator import CutoverCoordinatorError, V21GateCutoverCoordinator
from total_gateway.store import GatewayStateStore


H = "a" * 64


def record() -> GatePromotionRecord:
    values = {
        "promotion_epoch": 1, "expected_current_promotion_sha256": "0" * 64,
        "from_gate": "BASELINE", "to_gate": "G0", "from_mode": "legacy_observe",
        "to_mode": "legacy_observe", "build_id": "v21-g0-test", "source_manifest_sha256": H,
        "contract_set_hash": H, "config_hash": H, "evidence_refs": ("receipt:g0",),
        "rollback_target": "current_source_baseline", "promoted_at_ms": 1,
        "promotion_sha256": "0" * 64,
    }
    values["promotion_id"] = derive_gate_promotion_id("G0", 1, "v21-g0-test", H)
    return GatePromotionRecord(**values).with_computed_sha256()


class V21GateCutoverCoordinatorTests(unittest.TestCase):
    def test_receipt_and_persistent_head_are_both_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with GatewayStateStore.open(Path(temporary) / "gateway.sqlite3", now_ms=1) as store:
                coordinator = V21GateCutoverCoordinator(store)
                item = record()
                receipt = {"status": "PASS", "promotion_allowed": True, "gate": "G0", "build_id": item.build_id, "source_manifest_sha256": item.source_manifest_sha256}
                self.assertTrue(coordinator.promote(item, receipt))
                self.assertFalse(coordinator.promote(item, receipt))
                self.assertEqual(coordinator.head()[1], "G0")
                with self.assertRaises(CutoverCoordinatorError):
                    coordinator.promote(item, {**receipt, "source_manifest_sha256": H[:-1] + "b"})


if __name__ == "__main__":
    unittest.main()
