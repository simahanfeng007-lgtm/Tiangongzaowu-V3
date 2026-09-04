"""P7D.2 durable continuation tests for ``OmniGrantAuthority``.

The caller-facing continuation API receives identities only.  These tests
therefore assert outcomes through the persisted receipt, Effect and Fact
authorities instead of injecting arguments or signed artifacts.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from contracts import ExecutionResult, canonical_sha256, new_state_snapshot
from tests import test_composition_grant_authority_p7c1 as p7c1
from tests import test_composition_step_execution_p7d1 as p7d1
import total_gateway.backend_client as backend_client_module
from total_gateway.backend_client import (
    BACKEND_API_CONTRACT,
    BackendExecutionResponse,
)
from total_gateway.composition_execution_binding import (
    derive_run_sequence,
    rebuild_composition_effect_claim,
)
from total_gateway.composition_step_execution import (
    CompositionStepExecutionCoordinator,
)
from total_gateway.composition_step_authorization import (
    COMPOSITION_STEP_AUTHORIZATION_SCHEMA,
    COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2,
)
from total_gateway.effects import EffectResult
from total_gateway.omni_grant_authority import (
    OmniGrantAuthority,
    OmniGrantAuthorityError,
)
from total_gateway.policy_evidence import PolicyEvidenceLedger
from total_gateway.skill_selection import (
    compile_composition_execution_manifest,
    load_model_capability_manifest,
)


def _seal(harness, *, now_ms: int = 1_700) -> str:
    delegation_id = harness.authority.seal_composition_continuation(
        parent_ticket_id=harness.outer.payload.ticket_id,
        registration_id=harness.plan.registration_id,
        now_ms=now_ms,
    )
    assert delegation_id is not None
    return delegation_id


def _fresh_authority_without_parent_registration(
    harness,
    *,
    gateway_epoch: int = 1,
    trust=None,
) -> OmniGrantAuthority:
    return OmniGrantAuthority(
        registry=harness.loaded.registry,
        action_schema_catalog=harness.loaded.schema_catalog,
        capability_manifest_hash=harness.capability_file_sha256,
        capability_source_manifest_hash=harness.loaded.manifest_sha256,
        component_manifest_hash=p7c1.COMPONENT_MANIFEST_SHA256,
        skill_catalog_hash=p7c1.SKILL_CATALOG_SHA256,
        signer=harness.signer,
        gateway_epoch=gateway_epoch,
        workspace_root=harness.root,
        evidence=PolicyEvidenceLedger(harness.root / "restart-policy-evidence"),
        trust_bundle_provider=lambda _now_ms: harness.trust if trust is None else trust,
        effect_store=harness.store,
        object_store=harness.objects,
        fact_ledger=harness.facts,
    )


def _finish_parent(harness, *, started_at_ms: int, observed_at_ms: int) -> None:
    assert harness.parent_claim is not None
    harness.store.mark_effect_started(
        harness.parent_claim.effect_id,
        started_at_ms=started_at_ms,
    )
    harness.store.complete_effect(
        EffectResult(
            result_id="parent-result-p7d2",
            effect_id=harness.parent_claim.effect_id,
            status="SUCCEEDED",
            fact_id="parent-fact-p7d2",
            evidence_sha256="9" * 64,
            observed_at_ms=observed_at_ms,
            result_sha256="0" * 64,
        ).with_computed_sha256()
    )


def _execution_manifest(harness):
    model = load_model_capability_manifest(
        p7c1.CAPABILITY_MANIFEST,
        expected_sha256=hashlib.sha256(
            p7c1.CAPABILITY_MANIFEST.read_bytes()
        ).hexdigest(),
        component_manifest_hash=p7c1.COMPONENT_MANIFEST_SHA256,
        generated_at_ms=1_250,
    ).manifest
    return compile_composition_execution_manifest(
        model,
        harness.loaded.registry,
        harness.loaded.schema_catalog,
        generated_at_ms=1_250,
    )


def _finish_parent_with_fact(harness) -> None:
    assert harness.parent_claim is not None
    payload = {"parent": "succeeded"}
    result = ExecutionResult(
        result_id="execution-result-parent-p7d2",
        ticket_id=harness.outer.payload.ticket_id,
        request_id=harness.outer.payload.request_id,
        run_id=harness.outer.payload.run_id,
        generation=harness.outer.payload.generation,
        effect_id=harness.parent_claim.effect_id,
        action_id=harness.outer.payload.action_id,
        action_version=harness.outer.payload.action_version,
        status="SUCCEEDED",
        attempt=1,
        started_at_ms=1_701,
        finished_at_ms=1_702,
        side_effect_started=True,
        result_payload_sha256=canonical_sha256(payload),
        receipt_sha256="8" * 64,
        output_object_refs=(),
        fact_ids=("parent-fact-p7d2",),
    )
    response_payload = {
        "ok": True,
        "api_contract": BACKEND_API_CONTRACT,
        "execution_result": result.model_dump(mode="json"),
        "result_payload": payload,
    }
    response = BackendExecutionResponse(
        result=result,
        result_payload=payload,
        response_sha256=canonical_sha256(response_payload),
        ticket=harness.outer,
        _verification_marker=backend_client_module._BACKEND_VERIFIED_RESPONSE,
    )
    batch = harness.facts.record_execution(response, observed_at_ms=1_702).record
    harness.store.mark_effect_started(
        harness.parent_claim.effect_id,
        started_at_ms=result.started_at_ms,
    )
    harness.store.complete_effect(
        EffectResult(
            result_id="effect-result-" + result.result_id[:120],
            effect_id=result.effect_id,
            status="SUCCEEDED",
            fact_id=result.fact_ids[0],
            result_object_id=batch.result_payload_object_id,
            result_object_sha256=batch.result_payload_sha256,
            evidence_sha256=batch.batch_sha256,
            observed_at_ms=batch.observed_at_ms,
            result_sha256="0" * 64,
        ).with_computed_sha256()
    )


def test_claimed_local_write_parent_seals_inert_continuation_and_issues_a0_child(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "claimed-parent",
        multi_step=True,
        complete_parent_effect=False,
    ) as harness:
        assert harness.outer.payload.allowed_side_effects == ("local_write",)
        assert (
            harness.outer.payload.capability_manifest_hash
            == harness.capability_file_sha256
        )
        assert (
            harness.plan.capability_manifest_sha256
            != harness.outer.payload.capability_manifest_hash
        )

        delegation_id = _seal(harness)
        delegation = harness.store.get_composition_continuation_delegation(
            delegation_id,
            now_ms=1_700,
            require_parent_success=False,
        )
        assert delegation is not None
        assert delegation.record_type == "NON_EXECUTABLE_CONTINUATION"
        assert delegation.executable is False
        assert delegation.issuance_context["allowed_side_effects"] == [
            "local_write"
        ]
        assert (
            delegation.parent_ticket_sha256
            == canonical_sha256(harness.outer.model_dump(mode="json"))
        )

        with pytest.raises(OmniGrantAuthorityError) as pending_parent:
            harness.authority.issue_composition_continuation_step(
                continuation_delegation_id=delegation_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=1_701,
            )
        assert pending_parent.value.code == (
            "composition.authorization.continuation_not_live"
        )
        assert harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        ) == ()

        # The process-local parent authority is deliberately gone before the
        # child is issued; only the sealed continuation and durable parent
        # Effect remain authoritative.
        harness.authority.unregister(harness.outer.payload.ticket_id)
        _finish_parent(harness, started_at_ms=1_701, observed_at_ms=1_702)
        restarted = _fresh_authority_without_parent_registration(harness)
        assert restarted._active == {}  # noqa: SLF001 - durable restart proof
        response = restarted.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=1_703,
        )

        record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_703,
        )
        assert record is not None
        assert record.request.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2
        assert record.request.attempt == 1
        assert record.request.continuation_delegation_id == delegation_id
        assert record.request.continuation_delegation_sha256 == delegation.delegation_sha256
        assert record.request.dependency_evidence == []
        assert record.request.dependency_evidence_sha256 == canonical_sha256([])
        _, _, _, ticket, grant = record.artifacts.restore_contracts()
        assert set(ticket.payload.allowed_side_effects) <= {"none", "read"}
        assert "local_write" not in ticket.payload.allowed_side_effects
        assert set(grant.payload.allowed_side_effects) <= {"none", "read"}
        assert response == record.runtime_response


def test_multistep_legacy_entry_seals_then_delegates_to_v2(tmp_path: Path) -> None:
    with p7c1._harness(tmp_path / "legacy-entry", multi_step=True) as harness:
        response = p7c1._authorize(harness)
        continuation = harness.store.get_composition_continuation_for_plan(
            harness.plan.executable_plan_id,
            now_ms=1_700,
            require_parent_success=True,
        )
        record = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_700,
        )
        assert continuation is not None and continuation.executable is False
        assert record is not None
        assert record.request.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2
        assert record.request.continuation_delegation_id == continuation.delegation_id
        assert response == record.runtime_response


def test_continuation_requires_fact_authority_after_parent_unregister(
    tmp_path: Path,
) -> None:
    with p7c1._harness(tmp_path / "missing-facts", multi_step=True) as harness:
        delegation_id = _seal(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)
        harness.authority._fact_ledger = None  # noqa: SLF001 - fail-closed boundary

        with pytest.raises(OmniGrantAuthorityError) as caught:
            harness.authority.issue_composition_continuation_step(
                continuation_delegation_id=delegation_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=1_701,
            )
        assert caught.value.code == (
            "composition.authorization.fact_authority_unavailable"
        )


def test_composition_workspace_path_rejects_even_an_internal_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with p7c1._harness(tmp_path / "symlink-path") as harness:
        link = harness.root / "linked.txt"
        link.write_text("sealed", encoding="utf-8")
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda value: (
                value == link or original_is_symlink(value)
            ),
        )

        with pytest.raises(OmniGrantAuthorityError) as caught:
            harness.authority._validate_composition_workspace_path(  # noqa: SLF001
                link.name
            )
        assert caught.value.code == "composition.authorization.path_policy_exceeded"


def test_expired_prestart_attempt_gets_one_fresh_cas_successor(tmp_path: Path) -> None:
    with p7c1._harness(
        tmp_path / "attempt-two",
        multi_step=True,
        plan_expires_at_ms=61_500,
    ) as harness:
        delegation_id = _seal(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)
        first_response = harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=1_701,
        )
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert first is not None and first.request.attempt == 1
        _, _, _, first_ticket, first_grant = first.artifacts.restore_contracts()
        assert first_response == first.runtime_response

        second_response = harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=first.request.expires_at_ms,
        )
        chain = harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert tuple(item.request.attempt for item in chain) == (1, 2)
        second = chain[-1]
        assert second_response == second.runtime_response
        assert second.prebound_effect_id != first.prebound_effect_id
        assert second.supersedes_authorization_id == first.authorization_id
        assert second.supersedes_effect_id == first.prebound_effect_id
        assert second.supersedes_claim_sha256 == first_ticket.payload.claim_sha256
        _, _, _, second_ticket, second_grant = second.artifacts.restore_contracts()
        assert second_ticket.payload.nonce != first_ticket.payload.nonce
        assert second_grant.payload.nonce != first_grant.payload.nonce

        predecessor = harness.store.get_effect(first.prebound_effect_id)
        assert predecessor is not None
        assert predecessor.state == "FAILED_FINAL"
        assert predecessor.side_effect_started_at_ms is None
        assert predecessor.result is not None
        assert (
            predecessor.result.error_code
            == "composition.authorization.prestart_superseded"
        )
        assert harness.facts.get_batch_for_effect(first.prebound_effect_id) is None

        replay = harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=second.request.issued_at_ms + 1,
        )
        assert replay == second_response
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 2

        with pytest.raises(OmniGrantAuthorityError) as exhausted:
            harness.authority.issue_composition_continuation_step(
                continuation_delegation_id=delegation_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=second.request.expires_at_ms,
            )
        assert exhausted.value.code == "composition.authorization.attempts_exhausted"
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 2


def test_short_remaining_window_uses_one_millisecond_boundary_receipts(
    tmp_path: Path,
) -> None:
    with p7c1._harness(tmp_path / "short-window", multi_step=True) as harness:
        delegation_id = _seal(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)

        harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=harness.plan.expires_at_ms - 2,
        )
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert first is not None
        assert first.request.expires_at_ms == harness.plan.expires_at_ms - 1

        harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=first.request.expires_at_ms,
        )
        second = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert second is not None and second.request.attempt == 2
        assert second.request.expires_at_ms == harness.plan.expires_at_ms
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 2

    with p7c1._harness(
        tmp_path / "no-retry-window",
        multi_step=True,
    ) as harness:
        delegation_id = _seal(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)
        with pytest.raises(OmniGrantAuthorityError) as caught:
            harness.authority.issue_composition_continuation_step(
                continuation_delegation_id=delegation_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=harness.plan.expires_at_ms - 1,
            )
        assert caught.value.code == (
            "composition.authorization.retry_window_unavailable"
        )
        assert harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        ) == ()


def test_success_fact_materializes_step_output_for_downstream_v2_receipt(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "step-output",
        multi_step=True,
        complete_parent_effect=False,
    ) as harness:
        manifest = _execution_manifest(harness)
        # This is current deployment authority selected at construction in
        # production.  The reusable test harness exposes the field directly.
        harness.authority.composition_capability_manifest_hash = manifest.sha256
        delegation_id = _seal(harness)
        _finish_parent_with_fact(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)
        harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=1_701,
        )
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_701,
        )
        assert first is not None

        generation = harness.store.get_generation(first.request.request_id)
        assert generation is not None and generation.owner_instance_id is not None
        backend = p7d1._BackendProbe(
            harness.store,
            harness.facts,
            first.prebound_effect_id,
            action_id=first.request.action_id,
            target=first.request.target,
        )
        coordinator = CompositionStepExecutionCoordinator(
            store=harness.store,
            objects=harness.objects,
            facts=harness.facts,
            registry=harness.loaded.registry,
            schema_catalog=harness.loaded.schema_catalog,
            capability_manifest=manifest,
            trust_bundle_provider=lambda _now_ms: harness.trust,
            backend_compat_client=backend,
            workspace_root=harness.root.resolve(strict=True),
            gateway_epoch=1,
            gateway_instance_id=generation.owner_instance_id,
            append_effect_event=lambda _store, **_kwargs: True,
        )
        outcome = coordinator.dispatch_record(first, now_ms=1_702)
        assert outcome.status == "SUCCEEDED"
        first_batch = harness.facts.get_batch_for_effect(
            first.prebound_effect_id,
            verify_payload=True,
        )
        assert first_batch is not None

        response = harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.02",
            now_ms=1_703,
        )
        second = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.02",
            now_ms=1_703,
        )
        assert second is not None and second.request.attempt == 1
        assert second.request.materialized_arguments == {"skill_id": "a" * 64}
        assert len(second.request.dependency_evidence) == 1
        evidence = second.request.dependency_evidence[0]
        assert evidence["producer_step_id"] == "step.01"
        assert evidence["authorization_id"] == first.authorization_id
        assert evidence["attempt"] == 1
        assert evidence["effect_id"] == first.prebound_effect_id
        assert evidence["fact_batch_sha256"] == first_batch.batch_sha256
        assert evidence["fact_ids"] == list(first_batch.result.fact_ids)
        assert (
            second.request.dependency_evidence_sha256
            == canonical_sha256(second.request.dependency_evidence)
        )
        binding = response["runtime"]["composition_execution_binding"]
        assert (
            binding["dependency_evidence_sha256"]
            == second.request.dependency_evidence_sha256
        )
        assert binding["continuation_delegation_id"] == delegation_id


def test_started_attempt_never_gets_prestart_successor(tmp_path: Path) -> None:
    with p7c1._harness(
        tmp_path / "started-no-retry",
        multi_step=True,
        plan_expires_at_ms=61_500,
    ) as harness:
        delegation_id = _seal(harness)
        harness.authority.unregister(harness.outer.payload.ticket_id)
        harness.authority.issue_composition_continuation_step(
            continuation_delegation_id=delegation_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=1_701,
        )
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert first is not None
        claim = rebuild_composition_effect_claim(
            first.request,
            run_sequence=derive_run_sequence(
                first.request.request_id,
                first.request.run_id,
            ),
            ordinal=1,
            lease_epoch=1,
        )
        harness.store.claim_effect(claim)
        harness.store.mark_effect_started(
            claim.effect_id,
            started_at_ms=first.request.issued_at_ms + 1,
        )

        with pytest.raises(OmniGrantAuthorityError) as caught:
            harness.authority.issue_composition_continuation_step(
                continuation_delegation_id=delegation_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=first.request.expires_at_ms + 1,
            )
        assert caught.value.code == "composition.projection.started_fact_missing"
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 1


def test_single_step_v1_expiry_uses_the_same_unique_v2_successor_chain(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "single-step-successor",
        coherent_parent_effect=True,
        plan_expires_at_ms=61_500,
    ) as harness:
        delegation_id = _seal(harness)
        first_response = p7c1._authorize(harness, now_ms=1_701)
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert first is not None
        assert first.request.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA
        assert first.request.attempt == 1
        assert first_response == first.runtime_response

        harness.authority.unregister(harness.outer.payload.ticket_id)
        second_response = harness.authority.issue_composition_step(
            parent_ticket_id=harness.outer.payload.ticket_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=first.request.expires_at_ms,
        )
        chain = harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert tuple(item.request.attempt for item in chain) == (1, 2)
        second = chain[-1]
        _, _, _, first_ticket, _ = first.artifacts.restore_contracts()
        assert second.request.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2
        assert second_response == second.runtime_response
        assert second.request.continuation_delegation_id == delegation_id
        assert second.prebound_effect_id != first.prebound_effect_id
        assert second.supersedes_authorization_id == first.authorization_id
        assert second.supersedes_effect_id == first.prebound_effect_id
        assert second.supersedes_claim_sha256 == first_ticket.payload.claim_sha256

        predecessor = harness.store.get_effect(first.prebound_effect_id)
        assert predecessor is not None
        assert predecessor.state == "FAILED_FINAL"
        assert predecessor.side_effect_started_at_ms is None
        assert predecessor.result is not None
        assert predecessor.result.error_code == (
            "composition.authorization.prestart_superseded"
        )

        with pytest.raises(OmniGrantAuthorityError) as exhausted:
            harness.authority.issue_composition_step(
                parent_ticket_id=harness.outer.payload.ticket_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=second.request.expires_at_ms,
            )
        assert exhausted.value.code == "composition.authorization.attempts_exhausted"
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 2


def test_single_step_started_v1_never_replays_or_gets_a_successor(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "single-step-started",
        coherent_parent_effect=True,
        plan_expires_at_ms=61_500,
    ) as harness:
        _seal(harness)
        p7c1._authorize(harness, now_ms=1_701)
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert first is not None
        assert first.request.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA
        claim = rebuild_composition_effect_claim(
            first.request,
            run_sequence=derive_run_sequence(
                first.request.request_id,
                first.request.run_id,
            ),
            ordinal=1,
            lease_epoch=1,
        )
        harness.store.claim_effect(claim)
        harness.store.mark_effect_started(
            claim.effect_id,
            started_at_ms=first.request.issued_at_ms + 1,
        )
        harness.authority.unregister(harness.outer.payload.ticket_id)

        with pytest.raises(OmniGrantAuthorityError) as caught:
            harness.authority.issue_composition_step(
                parent_ticket_id=harness.outer.payload.ticket_id,
                registration_id=harness.plan.registration_id,
                step_id="step.01",
                now_ms=first.request.expires_at_ms,
            )
        assert caught.value.code == "composition.projection.started_fact_missing"
        assert len(
            harness.store.list_composition_step_authorizations(
                harness.plan.executable_plan_id,
                "step.01",
            )
        ) == 1


def test_single_step_old_epoch_v1_gets_a_fresh_prestart_successor(
    tmp_path: Path,
) -> None:
    with p7c1._harness(
        tmp_path / "single-step-old-epoch",
        coherent_parent_effect=True,
        outer_expires_at_ms=61_000,
        authority_expires_at_ms=61_000,
        plan_expires_at_ms=61_500,
    ) as harness:
        delegation_id = _seal(harness)
        p7c1._authorize(harness, now_ms=1_701)
        first = harness.store.get_current_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert first is not None and first.request.attempt == 1
        _, _, _, first_ticket, first_grant = first.artifacts.restore_contracts()
        assert first_ticket.payload.gateway_epoch == 1
        assert first_grant.payload.gateway_epoch == 1
        assert 11_201 < first.request.expires_at_ms

        harness.store.initialize_snapshot(
            new_state_snapshot(
                "request",
                entity_id=harness.plan.request_id,
                request_id=harness.plan.request_id,
                run_id=harness.plan.run_id,
                generation=harness.plan.generation,
                created_at_ms=1_200,
            )
        )
        recovered = harness.store.recover_expired_active_request(
            gateway_epoch=2,
            owner_instance_id="gateway_p7d2_epoch2",
            recovered_at_ms=11_200,
            lease_duration_ms=30_000,
            request_id=harness.plan.request_id,
        )
        assert recovered is not None
        assert recovered.generation.generation == harness.plan.generation
        assert recovered.generation.gateway_epoch == 2

        epoch_two_trust = harness.trust.model_copy(
            update={"gateway_epoch": 2, "bundle_sha256": p7c1.ZERO}
        ).with_computed_sha256()
        successor_authority = _fresh_authority_without_parent_registration(
            harness,
            gateway_epoch=2,
            trust=epoch_two_trust,
        )
        response = successor_authority.issue_composition_step(
            parent_ticket_id=harness.outer.payload.ticket_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=11_201,
        )

        chain = harness.store.list_composition_step_authorizations(
            harness.plan.executable_plan_id,
            "step.01",
        )
        assert tuple(item.request.attempt for item in chain) == (1, 2)
        second = chain[-1]
        assert response == second.runtime_response
        assert second.request.continuation_delegation_id == delegation_id
        _, _, _, second_ticket, second_grant = second.artifacts.restore_contracts()
        assert second_ticket.payload.gateway_epoch == 2
        assert second_grant.payload.gateway_epoch == 2
        assert second_ticket.payload.nonce != first_ticket.payload.nonce
        assert second_grant.payload.nonce != first_grant.payload.nonce
