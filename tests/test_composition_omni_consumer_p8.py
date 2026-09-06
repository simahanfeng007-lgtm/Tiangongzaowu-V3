"""Real durable issuer -> signed Omni consumer seam, not product evaluation.

The existing P7 harness provides synthetic plans and a controlled clock, but
the Store, continuation issuance, signatures and consumer verification are real.
No runtime handler or paid model is called by these contract tests.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import CompositionExecutionBindingV1, canonical_json_bytes, canonical_sha256
from tests import test_composition_grant_authority_p7c1 as p7c1


SOURCE = Path(__file__).resolve().parents[1] / "src/omni_body_skill/tools/omni_capability.py"


def consumer(monkeypatch, root, now_ms):
    spec = importlib.util.spec_from_file_location("p8_omni_consumer_seam", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "time", SimpleNamespace(time_ns=lambda: now_ms * 1_000_000))
    monkeypatch.setenv("TIANGONG_OMNI_NONCE_ROOT", str(root / "consumer-nonces"))
    return module


def verify(module, harness, response, record):
    return module.verify_capability_grant(
        response["grant"], action=record.request.action_id,
        target=record.request.target,
        args=json.loads(record.request.materialized_arguments_json),
        workspace=str(harness.root), runtime_meta=response["runtime"],
    )


@pytest.mark.parametrize("multi_step", [False, True])
def test_current_gateway_grant_is_accepted_by_real_omni_consumer(tmp_path, monkeypatch, multi_step):
    with p7c1._harness(tmp_path / "issuer-consumer", multi_step=multi_step) as harness:
        response = p7c1._authorize(harness)
        record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id, "step.01", now_ms=1_700,
        )
        assert record is not None
        module = consumer(monkeypatch, harness.root, 1_700)
        result = verify(module, harness, response, record)
        assert result["grant_sha256"] == canonical_sha256(response["grant"])
        assert result["allow_shell"] is result["allow_python"] is False
        binding = response["grant"]["payload"]["composition_execution_binding"]
        assert ("attempt" in binding) is multi_step
        with pytest.raises(module.CapabilityGrantError, match="replay"):
            verify(module, harness, response, record)


def test_durable_prestart_successor_is_accepted_with_its_exact_predecessor_binding(tmp_path, monkeypatch):
    with p7c1._harness(tmp_path / "successor-consumer", multi_step=True,
                       plan_expires_at_ms=61_500) as harness:
        first_response = p7c1._authorize(harness)
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id, "step.01", now_ms=1_700,
        )
        now_ms = first.request.expires_at_ms
        response = harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=first.request.continuation_delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01", now_ms=now_ms,
        )
        second = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id, "step.01", now_ms=now_ms,
        )
        assert second.request.attempt == 2
        module = consumer(monkeypatch, harness.root, now_ms)
        result = verify(module, harness, response, second)
        assert result["grant_sha256"] == canonical_sha256(response["grant"])
        binding = response["grant"]["payload"]["composition_execution_binding"]
        assert binding["supersedes_authorization_id"] == first.authorization_id
        assert binding["supersedes_effect_id"] == first.prebound_effect_id
        # Shared runtime_security verification includes expires_at_ms itself.
        # Store/CAS dispatch eligibility is separate; do not invent a different
        # expiry rule in this independent signature/nonce consumer.
        module.time = SimpleNamespace(time_ns=lambda: (now_ms + 1) * 1_000_000)
        with pytest.raises(module.CapabilityGrantError, match="time or authority"):
            verify(module, harness, first_response, first)


def test_malformed_continuations_match_existing_contract_rejection_before_nonce(tmp_path, monkeypatch):
    with p7c1._harness(tmp_path / "malformed-continuations", multi_step=True) as harness:
        response = p7c1._authorize(harness)
        record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id, "step.01", now_ms=1_700,
        )
        module = consumer(monkeypatch, harness.root, 1_700)
        mutations = [
            {"attempt": value} for value in (None, False, True, 0, -1, "1")
        ] + [
            {"continuation_delegation_id": value} for value in (None, "", " bad ", "x" * 161)
        ] + [
            {"continuation_delegation_sha256": "A" * 64},
            {"dependency_evidence_sha256": False},
            {"dependency_evidence_sha256": "f" * 63},
            {"attempt": 2},
            {"attempt": 2, "supersedes_authorization_id": "previous"},
            {"supersedes_authorization_id": "previous", "supersedes_effect_id": "eff_" + "f" * 64,
             "supersedes_claim_sha256": "f" * 64},
            {"unexpected_authority": True},
        ]
        bindings = []
        original = response["grant"]["payload"]["composition_execution_binding"]
        for update in mutations:
            bindings.append({**original, **update})
        for missing in ("attempt", "continuation_delegation_id", "continuation_delegation_sha256",
                        "dependency_evidence_sha256"):
            bindings.append({key: value for key, value in original.items() if key != missing})
        for binding in bindings:
            binding["binding_sha256"] = canonical_sha256({
                key: value for key, value in binding.items() if key != "binding_sha256"
            })
            with pytest.raises(ValueError):
                CompositionExecutionBindingV1.model_validate_json(canonical_json_bytes(binding), strict=True)
            changed = deepcopy(response)
            changed["grant"]["payload"]["composition_execution_binding"] = binding
            changed["runtime"]["composition_execution_binding"] = deepcopy(binding)
            changed["runtime"]["composition_binding_sha256"] = binding["binding_sha256"]
            with pytest.raises(module.CapabilityGrantError, match="composition"):
                verify(module, harness, changed, record)
        # Every malformed input was rejected before its valid nonce was spent.
        assert verify(module, harness, response, record)["grant_sha256"] == canonical_sha256(response["grant"])


def test_continuation_runtime_types_and_signed_dependency_identity_are_exact(tmp_path, monkeypatch):
    with p7c1._harness(tmp_path / "exact-continuation", multi_step=True) as harness:
        response = p7c1._authorize(harness)
        record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id, "step.01", now_ms=1_700,
        )
        module = consumer(monkeypatch, harness.root, 1_700)
        changed = deepcopy(response)
        changed["runtime"]["composition_execution_binding"]["attempt"] = True
        with pytest.raises(module.CapabilityGrantError, match="runtime composition binding"):
            verify(module, harness, changed, record)
        changed = deepcopy(response)
        binding = changed["grant"]["payload"]["composition_execution_binding"]
        binding["dependency_evidence_sha256"] = "f" * 64
        binding["binding_sha256"] = canonical_sha256({
            key: value for key, value in binding.items() if key != "binding_sha256"
        })
        changed["runtime"]["composition_execution_binding"] = deepcopy(binding)
        changed["runtime"]["composition_binding_sha256"] = binding["binding_sha256"]
        with pytest.raises(module.CapabilityGrantError, match="signature"):
            verify(module, harness, changed, record)
        changed = deepcopy(response)
        changed["grant"]["payload"]["risk_class"] = "A1"
        with pytest.raises(module.CapabilityGrantError, match="A0 read-only ceiling"):
            verify(module, harness, changed, record)
        assert verify(module, harness, response, record)["allow_shell"] is False
