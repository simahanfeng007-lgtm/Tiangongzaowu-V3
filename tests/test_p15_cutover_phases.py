"""P15 M8: A-F cutover phases all verify closed."""

from __future__ import annotations

import unittest

from life_service.p15_cutover import verify_all_phases, verify_cutover_phase


class P15CutoverPhaseTests(unittest.TestCase):
    def test_each_phase_verifies_ok(self) -> None:
        for phase in ("A", "B", "C", "D", "E", "F"):
            result = verify_cutover_phase(phase)
            self.assertTrue(result["ok"], (phase, result["checks"]))
            self.assertEqual(result["phase"], phase)

    def test_all_phases_together(self) -> None:
        results = verify_all_phases()
        self.assertEqual(
            [item["phase"] for item in results],
            ["A", "B", "C", "D", "E", "F"],
        )
        self.assertTrue(all(item["ok"] for item in results))

    def test_unknown_phase_raises(self) -> None:
        with self.assertRaises(ValueError):
            verify_cutover_phase("G")

    def test_phase_b_has_no_direct_runtime_store_write(self) -> None:
        result = verify_cutover_phase("B")
        checks = dict(result["checks"])
        self.assertTrue(checks["no_direct_store_write_in_runtime"])
        self.assertTrue(checks["runtime_delegates_to_coordinator"])

    def test_phase_d_retires_per_turn_adaptation(self) -> None:
        result = verify_cutover_phase("D")
        checks = dict(result["checks"])
        self.assertTrue(checks["per_turn_adaptation_retired"])
        self.assertTrue(checks["core_memory_adaptation_wired"])

    def test_phase_e_activates_memory_world_path(self) -> None:
        result = verify_cutover_phase("E")
        checks = dict(result["checks"])
        self.assertTrue(checks["world_candidate_outbox_table"])
        self.assertTrue(checks["wu_bridge_available"])

    def test_phase_f_leaves_no_dual_path(self) -> None:
        result = verify_cutover_phase("F")
        checks = dict(result["checks"])
        self.assertTrue(checks["no_dual_write_path"])
        self.assertTrue(checks["no_dual_temperament_path"])
        self.assertTrue(checks["no_second_memory_runtime"])


if __name__ == "__main__":
    unittest.main()
