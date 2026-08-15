from __future__ import annotations

from pathlib import Path

import pytest

from p18_m4_provider_l4_harness import (
    MODEL_ROUNDS,
    MIN_TOOL_STEPS,
    PROVIDER_IDS,
    certify_all_provider_l4,
    certify_provider_l4,
)


@pytest.mark.parametrize("provider_id", PROVIDER_IDS)
def test_m4_each_registered_provider_individually_passes_l4(provider_id: str) -> None:
    evidence = certify_provider_l4(provider_id)
    assert evidence.l4_pass is True, evidence
    assert evidence.model_rounds >= MODEL_ROUNDS
    assert evidence.parse_successes == evidence.model_rounds
    assert evidence.parse_coverage > 0.99
    assert evidence.simulated_tool_steps >= MIN_TOOL_STEPS
    assert evidence.private_reasoning_leaks == 0
    assert evidence.hard_metrics_clean is True
    assert evidence.checkpoint_rehydrated is True
    assert evidence.ambiguous_effect_reconciled is True
    assert evidence.false_completion_prevented is True
    assert evidence.tool_result_poisoning_blocked is True
    assert evidence.stream_disconnect_recovered is True
    assert set(evidence.capability_modes.values()) <= {
        "NATIVE",
        "LOCAL_FALLBACK",
        "UNSUPPORTED",
    }


def test_m4_provider_l4_registry_has_no_uncertified_production_provider() -> None:
    evidence = certify_all_provider_l4()
    assert tuple(item.provider_id for item in evidence) == tuple(PROVIDER_IDS)
    assert len(evidence) == len(PROVIDER_IDS) == 5
    assert all(item.l4_pass for item in evidence)
    assert sum(item.model_rounds for item in evidence) >= 5 * MODEL_ROUNDS
    assert sum(item.simulated_tool_steps for item in evidence) >= 5 * MIN_TOOL_STEPS


def test_m4_provider_l4_cannot_be_declared_by_static_production_flag() -> None:
    root = Path(__file__).resolve().parents[1]
    production_files = (
        root / "app" / "backend" / "tiangong-backend" / "tiangong_kernel" / "l4_action_grounding" / "model_provider_adapter.py",
        root / "app" / "backend" / "tiangong-backend" / "v3" / "jineng" / "moxing_shipei.py",
        root / "src" / "total_gateway" / "regenerative_provider.py",
    )
    forbidden = "LONG_HORIZON_PRODUCTION_READY"
    assert all(forbidden not in path.read_text(encoding="utf-8") for path in production_files)
