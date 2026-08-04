from __future__ import annotations

from contracts import (
    ActionImpact,
    CausalEpisode,
    LifeEventEnvelope,
    ViabilityDelta,
    ViabilityDimension,
    ViabilityState,
    canonical_sha256,
)


HASH_ZERO = "0" * 64
SIGNATURE = "a" * 128
LIFE_ID = "life_contract_test"


def event(
    sequence: int,
    previous_event_hash: str | None,
    *,
    life_id: str = LIFE_ID,
    writer_epoch: int = 1,
    suffix: str | None = None,
) -> LifeEventEnvelope:
    marker = suffix or f"{sequence:064x}"
    value = LifeEventEnvelope(
        event_id="lev_" + marker,
        life_id=life_id,
        sequence=sequence,
        writer_epoch=writer_epoch,
        source_service="test_harness",
        source_kind="user_message",
        event_kind="user.message.observed",
        occurred_at_ms=1_000 + sequence,
        observed_at_ms=1_000 + sequence,
        principal_ref="principal_test",
        subject_refs=("subject_test",),
        evidence_class="observed",
        source_credibility_milli=1000,
        privacy_scope="private",
        content_object_id="object_test_" + str(sequence),
        content_sha256=canonical_sha256({"content": sequence}),
        dedupe_key=canonical_sha256({"dedupe": sequence, "life_id": life_id}),
        causation_id=None,
        correlation_id="correlation_test",
        previous_event_hash=previous_event_hash,
        event_hash=HASH_ZERO,
        signer_key_id="test_signer",
        signature=SIGNATURE,
    )
    return value.with_computed_event_hash()


def dimension(
    *,
    value: int = 800,
    low: int = 700,
    high: int = 900,
    event_id: str = "lev_" + "1" * 64,
) -> ViabilityDimension:
    return ViabilityDimension(
        value_milli=value,
        target_low_milli=low,
        target_high_milli=high,
        confidence_milli=900,
        source_event_ids=(event_id,),
        measured_at_ms=2_000,
        stale_after_ms=3_000,
    )


def viability_state(**overrides: ViabilityDimension) -> ViabilityState:
    values = {
        "runtime_availability": dimension(),
        "recoverability": dimension(),
        "identity_continuity": dimension(),
        "data_integrity": dimension(),
        "memory_integrity": dimension(),
        "context_continuity": dimension(),
        "resource_headroom": dimension(),
        "cognitive_certainty": dimension(),
        "trust_and_authorization": dimension(),
        "commitment_continuity": dimension(),
        "security_margin": dimension(),
    }
    values.update(overrides)
    state = ViabilityState(
        life_id=LIFE_ID,
        revision=1,
        **values,
        created_at_ms=2_000,
        state_sha256=HASH_ZERO,
    )
    return state.with_computed_state_sha256()


def impact(**overrides) -> ActionImpact:
    values = {
        "impact_id": "impact_test",
        "life_id": LIFE_ID,
        "action_id": "action_test",
        "affected_internal_nodes": (),
        "touches_identity": False,
        "touches_soul": False,
        "touches_memory_keys": False,
        "touches_policy": False,
        "touches_core_code": False,
        "workspace_scope_milli": 100,
        "external_recipient_count": 0,
        "credential_scope_milli": 0,
        "privacy_scope_milli": 100,
        "blast_radius_milli": 100,
        "irreversibility_milli": 100,
        "uncertainty_milli": 100,
        "rollback_proof_ref": "rollback_test",
        "estimated_resource_cost_milli": 100,
        "predicted_viability_deltas": (
            ViabilityDelta(
                dimension="runtime_availability",
                delta_milli=100,
                confidence_milli=800,
                causal_hypothesis_ids=(),
            ),
        ),
        "source_event_ids": ("lev_" + "1" * 64,),
        "created_at_ms": 2_000,
        "impact_sha256": HASH_ZERO,
    }
    values.update(overrides)
    return ActionImpact(**values).with_computed_impact_sha256()


def open_episode() -> CausalEpisode:
    return CausalEpisode(
        episode_id="cep_" + "1" * 64,
        life_id=LIFE_ID,
        revision=1,
        supersedes_episode_sha256=None,
        trigger_event_ids=("lev_" + "1" * 64,),
        context_state_hashes=("1" * 64,),
        intention="验证生命契约",
        prior_prediction="严格输入会被接受。",
        candidate_action_ids=("action_test",),
        selected_action_id="action_test",
        authorization_ref=None,
        mediator_event_ids=(),
        outcome_event_ids=(),
        outcome_evaluation=None,
        prediction_error_milli=None,
        terminal_status="OPEN",
        created_at_ms=2_000,
        closed_at_ms=None,
        episode_sha256=HASH_ZERO,
    ).with_computed_episode_sha256()
