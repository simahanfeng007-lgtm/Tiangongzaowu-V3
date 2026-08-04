from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts.cutover import GatePromotionRecord, derive_gate_promotion_id


H = "a" * 64


def record(**overrides: object) -> GatePromotionRecord:
    values: dict[str, object] = {
        "promotion_epoch": 1, "expected_current_promotion_sha256": "0" * 64,
        "from_gate": "BASELINE", "to_gate": "G0", "from_mode": "legacy_observe",
        "to_mode": "legacy_observe", "build_id": "v21-g0-test", "source_manifest_sha256": H,
        "contract_set_hash": H, "config_hash": H, "evidence_refs": ("receipt:g0",),
        "rollback_target": "current_source_baseline", "promoted_at_ms": 1,
        "promotion_sha256": "0" * 64,
    }
    values.update(overrides)
    values["promotion_id"] = derive_gate_promotion_id(
        str(values["to_gate"]), int(values["promotion_epoch"]), str(values["build_id"]), str(values["source_manifest_sha256"]),
    )
    return GatePromotionRecord(**values).with_computed_sha256()


class GatePromotionRecordTests(unittest.TestCase):
    def test_g0_is_zero_head_cas_and_self_hashes(self) -> None:
        item = record()
        self.assertTrue(item.has_valid_sha256())
        self.assertTrue(item.promotion_id.startswith("gpr_"))

    def test_later_gate_requires_exact_predecessor_and_head(self) -> None:
        with self.assertRaises(ValidationError):
            record(to_gate="G2", from_gate="G0", expected_current_promotion_sha256=H)
        with self.assertRaises(ValidationError):
            record(to_gate="G1", from_gate="G0")

    def test_mode_cannot_skip_or_increase_by_more_than_one(self) -> None:
        with self.assertRaises(ValidationError):
            record(to_gate="G1", from_gate="G0", expected_current_promotion_sha256=H, to_mode="canary_internal")
        with self.assertRaises(ValidationError):
            record(to_gate="G1", from_gate="G0", expected_current_promotion_sha256=H, from_mode="shadow", to_mode="legacy_observe")

    def test_identity_is_bound_to_gate_epoch_build_and_manifest(self) -> None:
        item = record()
        with self.assertRaises(ValidationError):
            GatePromotionRecord(**{**item.model_dump(), "build_id": "other"})


if __name__ == "__main__":
    unittest.main()
