"""P7D.2 durable continuation and pre-start successor Store gates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from contracts import (
    ExecutionTicket,
    canonical_sha256,
    derive_effect_identity,
)
from total_gateway.composition_execution_binding import (
    derive_run_sequence,
    rebuild_composition_effect_claim,
)
from total_gateway.composition_step_authorization import (
    COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2,
    CompositionContinuationDelegation,
    CompositionStepAuthorizationArtifacts,
    build_composition_continuation_issuance_context,
    canonical_json_text,
)
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.store import (
    GatewayStateStore,
    StoreCasConflict,
    StoreConflictError,
)

from tests.test_composition_step_authorization_store_p7c1 import (
    ZERO,
    _artifacts,
    _composition_binding,
    _receipt_fixture,
    _table_counts,
)
from tests.gateway_store_migration_support import downgrade_v33_to_v32
from tests.test_composition_executable_plan_p7c0 import (
    _compile_material,
    _persist_executable,
)


def _claim_bound_artifacts(
    request,
    claim: EffectClaim,
    *,
    nonce_suffix: str,
) -> CompositionStepAuthorizationArtifacts:
    artifacts = _artifacts(request, nonce_suffix=nonce_suffix)
    ticket = artifacts.signed_ticket
    grant = artifacts.signed_grant
    runtime = artifacts.runtime_response
    ticket["payload"].update(
        {
            "claim_sha256": claim.claim_sha256,
            "claim_revision": claim.claim_revision,
            "claim_lease_epoch": claim.lease_epoch,
        }
    )
    grant["payload"]["ticket_sha256"] = canonical_sha256(ticket["payload"])
    runtime["grant"] = grant
    return CompositionStepAuthorizationArtifacts.build(
        intent=artifacts.intent,
        impact=artifacts.impact,
        decision=artifacts.decision,
        signed_ticket=ticket,
        signed_grant=grant,
        runtime_response=runtime,
    )


def test_executable_plan_registration_is_closed_after_parent_effect_claim(
    tmp_path: Path,
) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        material = _compile_material(store, tmp_path)
        legacy = material["legacy_plan"]
        run_sequence = derive_run_sequence(legacy.request_id, legacy.run_id)
        intent_sha256 = canonical_sha256(
            {"domain": "test.p7d2.registration-cutoff.v1"}
        )
        identity = derive_effect_identity(
            request_id=legacy.request_id,
            run_id=legacy.run_id,
            run_sequence=run_sequence,
            generation=legacy.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=intent_sha256,
        )
        claim = EffectClaim(
            effect_id=identity.effect_id,
            request_id=legacy.request_id,
            run_id=legacy.run_id,
            run_sequence=run_sequence,
            generation=legacy.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=intent_sha256,
            owner_component_id="tiangong-backend",
            claimed_at_ms=1_500,
            claim_sha256=ZERO,
        ).with_computed_sha256()
        store.claim_effect(claim)

        with pytest.raises(StoreConflictError, match="too late"):
            _persist_executable(store, material, recorded_at_ms=1_600)

        assert store.get_executable_composition_plan_for_request(
            legacy.request_id,
            run_id=legacy.run_id,
            generation=legacy.generation,
        ) is None


def _action_versions(plan) -> list[dict]:
    return [
        {
            "step_id": step.step_id,
            "step_binding_sha256": step.sha256,
            "action_id": step.action_id,
            "action_version": step.action_version,
            "source_revision_sha256": canonical_sha256(
                step.source_revision.model_dump(mode="json")
            ),
            "action_permission_sha256": step.permission_sha256,
            "argument_schema_sha256": step.argument_schema_sha256,
            "result_schema_sha256": step.result_schema_sha256,
        }
        for step in plan.step_bindings
    ]


def _continuation_context(ticket: ExecutionTicket) -> dict:
    return build_composition_continuation_issuance_context(
        ticket,
        life_id="life-p7d2",
        life_evidence_ref="lev_" + "8" * 64,
        session_id="session-p7d2",
    )


def _prepare_chain(
    store: GatewayStateStore,
    root: Path,
    *,
    commit_predecessor: bool = True,
):
    plan, step, raw_parent, raw_request, _ = _receipt_fixture(store, root)
    run_sequence = derive_run_sequence(plan.request_id, plan.run_id)
    parent_intent_sha256 = canonical_sha256(
        {"domain": "test.p7d2.parent-effect.v1", "plan": plan.executable_plan_id}
    )
    parent_effect_id = derive_effect_identity(
        request_id=plan.request_id,
        run_id=plan.run_id,
        run_sequence=run_sequence,
        generation=plan.generation,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=parent_intent_sha256,
    ).effect_id
    parent_claim = EffectClaim(
        effect_id=parent_effect_id,
        request_id=plan.request_id,
        run_id=plan.run_id,
        run_sequence=run_sequence,
        generation=plan.generation,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=parent_intent_sha256,
        pipeline_version="test.p7d2.parent.v1",
        attempt=1,
        claim_revision=1,
        lease_epoch=1,
        supersedes_claim_sha256=None,
        owner_component_id="tiangong-backend",
        claimed_at_ms=1_000,
        claim_sha256=ZERO,
    ).with_computed_sha256()
    parent_ticket = raw_parent.model_copy(
        update={
            "payload": raw_parent.payload.model_copy(
                update={
                    "effect_id": parent_effect_id,
                    "claim_sha256": parent_claim.claim_sha256,
                    "claim_revision": parent_claim.claim_revision,
                    "claim_lease_epoch": parent_claim.lease_epoch,
                    "component_manifest_hash": "7" * 64,
                }
            )
        }
    )
    store.claim_effect(parent_claim)
    store.mark_effect_started(parent_effect_id, started_at_ms=1_200)
    store.complete_effect(
        EffectResult(
            result_id="parent-result-p7d2",
            effect_id=parent_effect_id,
            status="SUCCEEDED",
            fact_id="parent-fact-p7d2",
            evidence_sha256="9" * 64,
            observed_at_ms=1_300,
            result_sha256=ZERO,
        ).with_computed_sha256()
    )

    delegation = CompositionContinuationDelegation.build(
        registration_id=plan.registration_id,
        registration_sha256=plan.registration_sha256,
        executable_plan_id=plan.executable_plan_id,
        executable_plan_sha256=plan.executable_plan_sha256,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash,
        parent_ticket_id=parent_ticket.payload.ticket_id,
        parent_ticket_sha256=canonical_sha256(
            parent_ticket.model_dump(mode="json")
        ),
        parent_ticket_expires_at_ms=parent_ticket.payload.expires_at_ms,
        parent_effect_id=parent_effect_id,
        parent_effect_claim_sha256=parent_claim.claim_sha256,
        source_manifest_sha256=plan.source_manifest_sha256,
        capability_manifest_sha256=plan.capability_manifest_sha256,
        action_registry_sha256=plan.action_registry_sha256,
        schema_catalog_sha256="2" * 64,
        composition_execution_manifest_sha256="6" * 64,
        component_manifest_sha256="7" * 64,
        verification_plan_id=plan.verification_plan_id,
        verification_plan_sha256=plan.verification_plan_sha256,
        verification_plan_activation_id=plan.verification_plan_activation_id,
        workspace_id=plan.workspace.workspace_id,
        workspace_scope_sha256=plan.workspace.workspace_scope_sha256,
        object_grants_sha256=canonical_sha256(
            [
                item.object_grant.model_dump(mode="json")
                for item in plan.plan_inputs
                if item.object_grant is not None
            ]
        ),
        issuance_context=_continuation_context(parent_ticket),
        allowed_action_versions=_action_versions(plan),
        issued_at_ms=1_600,
        expires_at_ms=2_450,
    )
    persisted, created = store.commit_composition_continuation_delegation(
        delegation, parent_ticket=parent_ticket, now_ms=1_600
    )
    assert created and persisted == delegation

    predecessor_intent_sha256 = canonical_sha256(
        {"domain": "test.p7d2.predecessor-effect.v1", "step": step.step_id}
    )
    predecessor_effect_id = derive_effect_identity(
        request_id=plan.request_id,
        run_id=plan.run_id,
        run_sequence=run_sequence,
        generation=plan.generation,
        effect_kind="execution",
        ordinal=1,
        intent_sha256=predecessor_intent_sha256,
    ).effect_id
    predecessor = replace(
        raw_request,
        parent_ticket_id=parent_ticket.payload.ticket_id,
        parent_ticket_sha256=delegation.parent_ticket_sha256,
        parent_ticket_expires_at_ms=parent_ticket.payload.expires_at_ms,
        prebound_effect_id=predecessor_effect_id,
        prebound_effect_intent_sha256=predecessor_intent_sha256,
        composition_binding_sha256=ZERO,
        authorization_request_sha256=ZERO,
    )
    predecessor = replace(
        predecessor,
        composition_binding_sha256=_composition_binding(predecessor)[
            "binding_sha256"
        ],
        authorization_request_sha256=ZERO,
    ).with_computed_sha256()
    predecessor_claim = rebuild_composition_effect_claim(
        predecessor, run_sequence=run_sequence, ordinal=1, lease_epoch=1
    )
    predecessor_artifacts = _claim_bound_artifacts(
        predecessor, predecessor_claim, nonce_suffix="predecessor"
    )
    predecessor_record = None
    if commit_predecessor:
        predecessor_record, created = store.commit_composition_step_authorization(
            predecessor,
            parent_ticket=parent_ticket,
            artifacts=predecessor_artifacts,
            now_ms=1_700,
        )
        assert created
    return (
        plan,
        step,
        delegation,
        predecessor_record,
        predecessor,
        predecessor_claim,
    )


def _successor(
    predecessor_record,
    predecessor_claim: EffectClaim,
    delegation: CompositionContinuationDelegation,
    *,
    salt: str = "winner",
):
    predecessor = predecessor_record.request
    intent_sha256 = canonical_sha256(
        {
            "domain": "test.p7d2.successor-effect.v1",
            "predecessor": predecessor_record.authorization_id,
            "salt": salt,
        }
    )
    effect_id = derive_effect_identity(
        request_id=predecessor.request_id,
        run_id=predecessor.run_id,
        run_sequence=derive_run_sequence(
            predecessor.request_id, predecessor.run_id
        ),
        generation=predecessor.generation,
        effect_kind="execution",
        ordinal=1,
        intent_sha256=intent_sha256,
    ).effect_id
    request = replace(
        predecessor,
        schema_version=COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2,
        attempt=2,
        continuation_delegation_id=delegation.delegation_id,
        continuation_delegation_sha256=delegation.delegation_sha256,
        dependency_evidence_json=canonical_json_text([]),
        dependency_evidence_sha256=canonical_sha256([]),
        supersedes_authorization_id=predecessor_record.authorization_id,
        supersedes_effect_id=predecessor.prebound_effect_id,
        supersedes_claim_sha256=predecessor_claim.claim_sha256,
        prebound_effect_id=effect_id,
        prebound_effect_intent_sha256=intent_sha256,
        composition_binding_sha256=ZERO,
        issued_at_ms=1_800,
        expires_at_ms=2_300,
        authorization_ceiling_ms=2_450,
        authorization_request_sha256=ZERO,
    )
    request = replace(
        request,
        composition_binding_sha256=_composition_binding(request)[
            "binding_sha256"
        ],
        authorization_request_sha256=ZERO,
    ).with_computed_sha256()
    claim = rebuild_composition_effect_claim(
        request,
        run_sequence=derive_run_sequence(request.request_id, request.run_id),
        ordinal=1,
        lease_epoch=1,
    )
    return request, _claim_bound_artifacts(
        request, claim, nonce_suffix=f"successor-{salt}"
    ), claim


def test_continuation_is_inert_and_carries_detached_issuance_context(
    tmp_path: Path,
) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        plan, _, delegation, _, _, _ = _prepare_chain(store, tmp_path)
        loaded = store.get_composition_continuation_for_plan(
            plan.executable_plan_id,
            now_ms=1_700,
            require_parent_success=True,
        )
        assert loaded == delegation
        assert loaded is not None
        assert loaded.executable is False
        assert _table_counts(store._connection)["security_nonce_ledger"] == 0
        assert _table_counts(store._connection)["effect_ledger"] == 1
        columns = {
            row[1]
            for row in store._connection.execute(
                "PRAGMA table_info(composition_continuation_delegation)"
            ).fetchall()
        }
        assert not {
            "ticket_nonce",
            "grant_nonce",
            "signed_ticket_json",
            "signed_grant_json",
            "runtime_response_json",
        }.intersection(columns)
        context = loaded.issuance_context
        context["life_id"] = "tampered"
        assert loaded.issuance_context["life_id"] == "life-p7d2"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._connection.execute(
                "UPDATE composition_continuation_delegation "
                "SET expires_at_ms = expires_at_ms + 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._connection.execute(
                "DELETE FROM composition_continuation_delegation"
            )
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_v32_v1_receipt_migrates_byte_identically_to_v33(tmp_path: Path) -> None:
    path = tmp_path / "gateway-v32.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        expected, created = store.commit_composition_step_authorization(
            request,
            parent_ticket=parent_ticket,
            artifacts=artifacts,
            now_ms=1_700,
        )
        assert created
        old_projection = dict(
            store._connection.execute(
                "SELECT * FROM composition_step_authorization"
            ).fetchone()
        )
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        downgrade_v33_to_v32(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 32
    finally:
        connection.close()

    with GatewayStateStore.open(path, now_ms=1_701) as upgraded:
        restored = upgraded.get_composition_step_authorization(
            request.executable_plan_id, request.step_id, attempt=1
        )
        assert restored == expected
        current = dict(
            upgraded._connection.execute(
                "SELECT * FROM composition_step_authorization"
            ).fetchone()
        )
        for field, value in old_projection.items():
            if field not in {
                "continuation_delegation_id",
                "continuation_delegation_sha256",
                "dependency_evidence_json",
                "dependency_evidence_sha256",
                "supersedes_authorization_id",
                "supersedes_effect_id",
                "supersedes_claim_sha256",
            }:
                assert current[field] == value
        assert upgraded.health_check(now_ms=1_701, full=True).healthy


def test_v2_attempt_one_commits_without_live_parent_ticket(tmp_path: Path) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        plan, step, delegation, _, base, _ = _prepare_chain(
            store, tmp_path, commit_predecessor=False
        )
        intent_sha256 = canonical_sha256(
            {"domain": "test.p7d2.continuation-first-attempt.v1"}
        )
        effect_id = derive_effect_identity(
            request_id=base.request_id,
            run_id=base.run_id,
            run_sequence=derive_run_sequence(base.request_id, base.run_id),
            generation=base.generation,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=intent_sha256,
        ).effect_id
        request = replace(
            base,
            schema_version=COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2,
            continuation_delegation_id=delegation.delegation_id,
            continuation_delegation_sha256=delegation.delegation_sha256,
            dependency_evidence_json=canonical_json_text([]),
            dependency_evidence_sha256=canonical_sha256([]),
            prebound_effect_id=effect_id,
            prebound_effect_intent_sha256=intent_sha256,
            composition_binding_sha256=ZERO,
            issued_at_ms=1_800,
            expires_at_ms=2_300,
            authorization_ceiling_ms=2_450,
            authorization_request_sha256=ZERO,
        )
        request = replace(
            request,
            composition_binding_sha256=_composition_binding(request)[
                "binding_sha256"
            ],
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        stale_claim = rebuild_composition_effect_claim(
            request,
            run_sequence=derive_run_sequence(request.request_id, request.run_id),
            ordinal=1,
            lease_epoch=2,
        )
        stale_artifacts = _claim_bound_artifacts(
            request, stale_claim, nonce_suffix="continuation-stale-epoch"
        )
        with pytest.raises(StoreConflictError, match="current-epoch"):
            store.commit_composition_step_authorization(
                request,
                artifacts=stale_artifacts,
                now_ms=1_800,
            )
        assert store.list_composition_step_authorizations(
            plan.executable_plan_id, step.step_id
        ) == ()

        claim = rebuild_composition_effect_claim(
            request,
            run_sequence=derive_run_sequence(request.request_id, request.run_id),
            ordinal=1,
            lease_epoch=1,
        )
        artifacts = _claim_bound_artifacts(
            request, claim, nonce_suffix="continuation-first"
        )

        record, created = store.commit_composition_step_authorization(
            request,
            artifacts=artifacts,
            now_ms=1_800,
        )
        replay, replay_created = store.commit_composition_step_authorization(
            request,
            artifacts=artifacts,
            now_ms=1_801,
        )
        assert created is True and replay_created is False and replay == record
        assert record.attempt == 1 and record.dependency_evidence == []
        assert store.get_current_composition_step_authorization(
            plan.executable_plan_id, step.step_id, now_ms=1_801
        ) == record


def test_prestart_cas_disposes_predecessor_and_exposes_unique_current_head(
    tmp_path: Path,
) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        _, step, delegation, predecessor, _, predecessor_claim = _prepare_chain(
            store, tmp_path
        )
        assert predecessor is not None
        request, artifacts, successor_claim = _successor(
            predecessor, predecessor_claim, delegation
        )
        record, created = store.supersede_composition_step_authorization_prestart(
            request,
            artifacts=artifacts,
            expected_predecessor_authorization_id=predecessor.authorization_id,
            now_ms=1_800,
        )
        replay, replay_created = (
            store.supersede_composition_step_authorization_prestart(
                request,
                artifacts=artifacts,
                expected_predecessor_authorization_id=(
                    predecessor.authorization_id
                ),
                now_ms=1_801,
            )
        )

        assert created is True and replay_created is False and replay == record
        assert record.step_id == step.step_id
        assert record.attempt == 2
        assert record.prebound_effect_id == successor_claim.effect_id
        assert record.supersedes_authorization_id == predecessor.authorization_id
        assert record.supersedes_effect_id == predecessor.prebound_effect_id
        assert record.supersedes_claim_sha256 == predecessor_claim.claim_sha256
        assert record.dependency_evidence == []
        chain = store.list_composition_step_authorizations(
            predecessor.request.executable_plan_id, step.step_id
        )
        assert tuple(item.attempt for item in chain) == (1, 2)
        assert store.get_current_composition_step_authorization(
            predecessor.request.executable_plan_id, step.step_id
        ) == record
        old_effect = store.get_effect(predecessor.prebound_effect_id)
        assert old_effect is not None
        assert old_effect.state == "FAILED_FINAL"
        assert old_effect.side_effect_started_at_ms is None
        assert old_effect.result is not None
        assert (
            old_effect.result.error_code
            == "composition.authorization.prestart_superseded"
        )
        assert store.get_effect(successor_claim.effect_id) is None
        assert store.health_check(now_ms=1_801, full=True).healthy


def test_two_different_successors_have_one_cas_winner(tmp_path: Path) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        _, step, delegation, predecessor, _, predecessor_claim = _prepare_chain(
            store, tmp_path
        )
        assert predecessor is not None
        candidates = tuple(
            _successor(predecessor, predecessor_claim, delegation, salt=salt)
            for salt in ("left", "right")
        )

        def write(candidate):
            request, artifacts, _ = candidate
            try:
                record, created = (
                    store.supersede_composition_step_authorization_prestart(
                        request,
                        artifacts=artifacts,
                        expected_predecessor_authorization_id=(
                            predecessor.authorization_id
                        ),
                        now_ms=1_800,
                    )
                )
                return ("won", record.authorization_id, created)
            except StoreCasConflict:
                return ("lost", None, False)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(pool.map(write, candidates))
        assert sorted(item[0] for item in outcomes) == ["lost", "won"]
        chain = store.list_composition_step_authorizations(
            predecessor.request.executable_plan_id, step.step_id
        )
        assert len(chain) == 2
        assert chain[-1].authorization_id in {
            item[1] for item in outcomes if item[0] == "won"
        }


def test_already_claimed_but_never_started_predecessor_is_disposed(
    tmp_path: Path,
) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        _, _, delegation, predecessor, _, predecessor_claim = _prepare_chain(
            store, tmp_path
        )
        assert predecessor is not None
        store.claim_effect(predecessor_claim)
        request, artifacts, _ = _successor(
            predecessor, predecessor_claim, delegation
        )
        record, created = store.supersede_composition_step_authorization_prestart(
            request,
            artifacts=artifacts,
            expected_predecessor_authorization_id=predecessor.authorization_id,
            now_ms=1_800,
        )
        assert created and record.attempt == 2
        old_effect = store.get_effect(predecessor_claim.effect_id)
        assert old_effect is not None and old_effect.state == "FAILED_FINAL"
        facts = store.list_effect_facts(predecessor_claim.effect_id)
        assert tuple(item["fact_kind"] for item in facts) == (
            "CLAIM",
            "AUTHORIZATION_FAILED",
            "RECEIPT",
        )


def test_started_predecessor_is_never_superseded(tmp_path: Path) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        _, _, delegation, predecessor, _, predecessor_claim = _prepare_chain(
            store, tmp_path
        )
        assert predecessor is not None
        store.claim_effect(predecessor_claim)
        store.acquire_dispatch_permit(
            effect_id=predecessor_claim.effect_id,
            attempt=1,
            expected_fence_epoch=0,
            nonce_sha256="a" * 64,
            now_ms=1_750,
        )
        before = _table_counts(store._connection)
        request, artifacts, _ = _successor(
            predecessor, predecessor_claim, delegation
        )
        with pytest.raises(
            StoreConflictError, match="prestart boundary"
        ):
            store.supersede_composition_step_authorization_prestart(
                request,
                artifacts=artifacts,
                expected_predecessor_authorization_id=(
                    predecessor.authorization_id
                ),
                now_ms=1_800,
            )
        assert _table_counts(store._connection) == before
        assert store.list_composition_step_authorizations(
            request.executable_plan_id, request.step_id
        ) == (predecessor,)


def test_consumed_predecessor_nonce_is_never_superseded(tmp_path: Path) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        _, _, delegation, predecessor, _, predecessor_claim = _prepare_chain(
            store, tmp_path
        )
        assert predecessor is not None
        ticket = predecessor.artifacts.restore_contracts()[3]
        store.consume_security_nonce(
            issuer=ticket.payload.issuer,
            audience=ticket.payload.audience,
            purpose="execution_ticket",
            nonce=ticket.payload.nonce,
            payload_sha256=canonical_sha256(ticket.payload.model_dump(mode="json")),
            gateway_epoch=ticket.payload.gateway_epoch,
            consumer_instance_id="test-p7d2",
            consumed_at_ms=1_750,
            expires_at_ms=ticket.payload.expires_at_ms,
        )
        request, artifacts, _ = _successor(
            predecessor, predecessor_claim, delegation
        )
        with pytest.raises(StoreConflictError, match="already consumed"):
            store.supersede_composition_step_authorization_prestart(
                request,
                artifacts=artifacts,
                expected_predecessor_authorization_id=(
                    predecessor.authorization_id
                ),
                now_ms=1_800,
            )
        assert store.get_effect(predecessor.prebound_effect_id) is None


def test_successor_effect_attempt_two_updates_head_and_receipt(tmp_path: Path) -> None:
    with GatewayStateStore.open(
        tmp_path / "gateway.sqlite3", now_ms=1_000
    ) as store:
        _, _, delegation, predecessor, _, predecessor_claim = _prepare_chain(
            store, tmp_path
        )
        assert predecessor is not None
        request, artifacts, claim = _successor(
            predecessor, predecessor_claim, delegation
        )
        store.supersede_composition_step_authorization_prestart(
            request,
            artifacts=artifacts,
            expected_predecessor_authorization_id=predecessor.authorization_id,
            now_ms=1_800,
        )
        store.claim_effect(claim)
        store.acquire_dispatch_permit(
            effect_id=claim.effect_id,
            attempt=2,
            expected_fence_epoch=0,
            nonce_sha256="b" * 64,
            now_ms=1_850,
        )
        started = store.get_effect(claim.effect_id)
        assert started is not None and started.state == "SIDE_EFFECT_STARTED"
        result = EffectResult(
            result_id="successor-result-p7d2",
            effect_id=claim.effect_id,
            status="SUCCEEDED",
            fact_id="successor-fact-p7d2",
            evidence_sha256="c" * 64,
            observed_at_ms=1_900,
            result_sha256=ZERO,
        ).with_computed_sha256()
        completed = store.complete_effect(result)
        assert completed.state == "SUCCEEDED"
        attempt = store.get_effect_attempt(claim.effect_id, 2)
        assert attempt is not None and attempt["state"] == "SUCCEEDED"
        assert tuple(
            fact["fact_kind"] for fact in store.list_effect_facts(claim.effect_id)
        ) == ("CLAIM", "DISPATCH_PERMIT", "STARTED", "RECEIPT")
        assert store.health_check(now_ms=1_901, full=True).healthy
