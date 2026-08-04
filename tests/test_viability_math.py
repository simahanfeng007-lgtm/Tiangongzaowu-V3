from __future__ import annotations

import unittest

from life_service.agency import compute_action_risk_floor, compute_agency_score
from life_service.viability import compute_viability_deficit
from tests.life_contract_support import dimension, impact, viability_state


class ViabilityMathTests(unittest.TestCase):
    def test_critical_failure_cannot_be_averaged_away(self) -> None:
        state = viability_state(
            data_integrity=dimension(value=0, low=900, high=1000),
        )
        weights = {name: 100 for name in state.dimensions()}
        result = compute_viability_deficit(
            state,
            weights=weights,
            critical_weight_milli=1000,
        )
        self.assertEqual(result.critical_deficit_milli, 900)
        self.assertGreaterEqual(result.total_deficit_milli, 900)
        healthy = compute_viability_deficit(
            viability_state(),
            weights=weights,
            critical_weight_milli=1000,
        )
        self.assertEqual(healthy.total_deficit_milli, 0)

    def test_viability_policy_rejects_missing_float_bool_and_zero_weights(self) -> None:
        state = viability_state()
        weights = {name: 100 for name in state.dimensions()}
        with self.assertRaises(ValueError):
            compute_viability_deficit(
                state,
                weights={name: 100 for name in tuple(weights)[:-1]},
                critical_weight_milli=1000,
            )
        with self.assertRaises(ValueError):
            compute_viability_deficit(
                state,
                weights={name: 0 for name in weights},
                critical_weight_milli=1000,
            )
        with self.assertRaises(ValueError):
            compute_viability_deficit(
                state,
                weights={**weights, "runtime_availability": True},
                critical_weight_milli=1000,
            )

    def test_action_risk_uses_the_max_critical_dimension(self) -> None:
        self.assertEqual(compute_action_risk_floor(impact()), "A1")
        external = impact(
            impact_id="impact_external",
            external_recipient_count=1,
        )
        self.assertEqual(compute_action_risk_floor(external), "A4")
        core = impact(
            impact_id="impact_core",
            touches_core_code=True,
        )
        self.assertEqual(compute_action_risk_floor(core), "A5")
        identity = impact(
            impact_id="impact_identity",
            touches_identity=True,
        )
        self.assertEqual(compute_action_risk_floor(identity), "A5")

    def test_agency_score_is_exact_integer_arithmetic(self) -> None:
        score = compute_agency_score(
            goal_gain_milli=500,
            viability_gain_milli=300,
            information_gain_milli=100,
            relationship_value_milli=50,
            resource_cost_milli=100,
            expected_harm_milli=200,
            uncertainty_penalty_milli=150,
            irreversibility_penalty_milli=50,
        )
        self.assertEqual(score.expected_utility_milli, 600)
        self.assertEqual(score.utility_lcb_milli, 450)
        with self.assertRaises(ValueError):
            compute_agency_score(
                goal_gain_milli=1.0,
                viability_gain_milli=0,
                information_gain_milli=0,
                relationship_value_milli=0,
                resource_cost_milli=0,
                expected_harm_milli=0,
                uncertainty_penalty_milli=0,
                irreversibility_penalty_milli=0,
            )


if __name__ == "__main__":
    unittest.main()
