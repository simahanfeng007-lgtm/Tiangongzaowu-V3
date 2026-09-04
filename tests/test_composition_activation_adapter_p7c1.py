from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import test_composition_executable_plan_p7c0 as p7c0
from contracts import canonical_sha256
from total_gateway.composition_activation_adapter import (
    CompositionActivationAdapter,
    CompositionActivationAdapterError,
    materialize_static_root_step,
)
from total_gateway.store import GatewayStateStore


def _plan(tmp_path: Path):
    with GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=1_000) as store:
        material = p7c0._compile_material(store, tmp_path)
        return p7c0._compile_executable(material)


def test_static_root_step_materializes_only_sealed_inputs(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    materialized = materialize_static_root_step(plan, step_id="step.01")

    assert materialized.target == str(tmp_path / "artifact-001")
    assert materialized.arguments == {
        "artifact_id": "artifact-001",
        "mode": "metadata-only",
    }
    assert materialized.arguments_sha256 == canonical_sha256(materialized.arguments)
    assert materialized.target_sha256 == canonical_sha256(materialized.target)
    assert materialized.executable_plan_sha256 == plan.executable_plan_sha256
    assert materialized.step.sha256 == plan.step_bindings[0].sha256


def test_materialization_returns_a_deep_copy(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = materialize_static_root_step(plan, step_id="step.01")
    first.arguments["artifact_id"] = "mutated"

    second = materialize_static_root_step(plan, step_id="step.01")

    assert second.arguments["artifact_id"] == "artifact-001"


def test_dependent_step_is_reserved_for_p7d(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    with pytest.raises(CompositionActivationAdapterError) as caught:
        materialize_static_root_step(plan, step_id="step.02")

    assert caught.value.code == "composition.authorization.dependencies_not_ready"


def test_rehashed_argument_slot_tamper_fails_closed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    step = plan.step_bindings[0]
    tampered_args = deepcopy(step.args_skeleton)
    tampered_args["artifact_id"] = "already-filled"
    tampered_step = step.model_copy(
        update={"args_skeleton": tampered_args, "sha256": "0" * 64}
    ).with_computed_sha256()
    tampered = plan.model_copy(
        update={
            "step_bindings": (tampered_step, *plan.step_bindings[1:]),
            "execution_bindings_sha256": "0" * 64,
            "executable_plan_id": "ecp_" + "0" * 64,
            "executable_plan_sha256": "0" * 64,
        }
    )

    with pytest.raises(CompositionActivationAdapterError) as caught:
        materialize_static_root_step(tampered, step_id="step.01")

    assert caught.value.code == "composition.authorization.plan_invalid"


def test_adapter_exposes_only_ids_to_the_existing_issuer() -> None:
    calls = []

    class Issuer:
        def issue_composition_step(self, **kwargs):
            calls.append(kwargs)
            return {"status": "OK"}

    adapter = CompositionActivationAdapter(Issuer())
    result = adapter.authorize_step(
        parent_ticket_id="ticket_parent",
        registration_id="car_registration",
        step_id="step.01",
        now_ms=1_700,
    )

    assert result == {"status": "OK"}
    assert calls == [
        {
            "parent_ticket_id": "ticket_parent",
            "registration_id": "car_registration",
            "step_id": "step.01",
            "now_ms": 1_700,
        }
    ]


@pytest.mark.parametrize(
    "field,value",
    (
        ("parent_ticket_id", ""),
        ("registration_id", "bad/id"),
        ("step_id", " step.01"),
    ),
)
def test_adapter_rejects_invalid_identity_before_calling_issuer(field, value) -> None:
    class Issuer:
        def issue_composition_step(self, **kwargs):  # pragma: no cover
            raise AssertionError("issuer must not be reached")

    values = {
        "parent_ticket_id": "ticket_parent",
        "registration_id": "car_registration",
        "step_id": "step.01",
    }
    values[field] = value
    with pytest.raises(CompositionActivationAdapterError) as caught:
        CompositionActivationAdapter(Issuer()).authorize_step(**values, now_ms=1)
    assert caught.value.code == "composition.authorization.identity_invalid"
