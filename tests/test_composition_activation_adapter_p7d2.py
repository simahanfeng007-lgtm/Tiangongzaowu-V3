"""P7D.2 narrow continuation-authorization adapter contract."""

from __future__ import annotations

import pytest

from total_gateway.composition_activation_adapter import (
    CompositionActivationAdapter,
    CompositionActivationAdapterError,
)


def test_continuation_adapter_exposes_only_durable_ids() -> None:
    calls: list[dict[str, object]] = []

    class Issuer:
        def issue_composition_step(self, **_kwargs):  # pragma: no cover
            raise AssertionError("legacy issuance was not requested")

        def issue_composition_continuation_step(self, **kwargs):
            calls.append(kwargs)
            return {"status": "OK"}

    adapter = CompositionActivationAdapter(Issuer())
    result = adapter.authorize_continuation_step(
        continuation_delegation_id="ccd_" + "a" * 64,
        registration_id="registration.p7d2",
        step_id="step.02",
        now_ms=2_000,
    )

    assert result == {"status": "OK"}
    assert calls == [
        {
            "continuation_delegation_id": "ccd_" + "a" * 64,
            "registration_id": "registration.p7d2",
            "step_id": "step.02",
            "now_ms": 2_000,
        }
    ]
    assert not {
        "arguments",
        "target",
        "effect_id",
        "dependency_evidence",
        "manifest",
        "gateway_epoch",
    }.intersection(calls[0])


def test_continuation_adapter_fails_closed_when_issuer_does_not_support_it() -> None:
    class LegacyIssuer:
        def issue_composition_step(self, **_kwargs):
            return {"status": "OK"}

    with pytest.raises(
        CompositionActivationAdapterError,
        match="composition.authorization.continuation_unavailable",
    ):
        CompositionActivationAdapter(LegacyIssuer()).authorize_continuation_step(
            continuation_delegation_id="ccd_" + "b" * 64,
            registration_id="registration.p7d2",
            step_id="step.01",
            now_ms=2_000,
        )
