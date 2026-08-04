"""SQLite event/state store with migrations, CAS, and corruption checks."""

from __future__ import annotations

from .diagnostics import diagnostic_log

import os
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    ActionIntentVNext,
    AssistantCommit,
    AssistantSystemEnvelope,
    CompositeExecutionOutcome,
    CONTRACT_SCHEMA_VERSION,
    ChannelCutoverSnapshot,
    ChannelDrainEvidence,
    ChannelOwnershipLease,
    EffectReconciliationRecord,
    GatePromotionRecord,
    GatewayRegistrationReceipt,
    FenceDecision,
    ExecutionTicket,
    GenerationFence,
    InboundEnvelope,
    LifeExecutionProposal,
    ModelAttemptPlan,
    ModelAttemptPlanOutcome,
    ModelAttemptResult,
    ShadowComparison,
    ShadowDecisionObservation,
    ShadowIngressCopy,
    ShadowObservationBatch,
    SkillActivationGrant,
    SkillSelectionRecord,
    StateSnapshot,
    SystemStatusRecord,
    TaskContinuityCapsule,
    TransitionDecision,
    TransitionEvent,
    apply_transition,
    activate_candidate_owner,
    apply_channel_drain,
    canonical_json_bytes,
    canonical_sha256,
    compare_shadow_observations,
    derive_run_identity,
    derive_request_identity,
    derive_generation_fence,
    evaluate_generation_fence,
    new_state_snapshot,
    renew_candidate_owner,
)
from contracts.state_machine import ATTEMPT_RECONCILIATION_VERDICTS
from .coordination import FencedResultDecision, GenerationLeaseView
from .coordination_events import CoordinationEvent, CoordinationRecord, CoordinationResolution
from .outbox import OutboxIntent
from .effects import EffectClaim, EffectResult

if TYPE_CHECKING:
    from .completion_gate import CompletionDecision


APPLICATION_ID = 0x54475633
STORE_SCHEMA_VERSION = 20
CHANNEL_LEASE_CLOCK_SKEW_MS = 5_000
_MIGRATION_V1_ID = "gateway-store-v1"
_MIGRATION_V1_STATEMENTS = (
    """
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY CHECK (version >= 1),
        migration_id TEXT NOT NULL UNIQUE,
        migration_sha256 TEXT NOT NULL CHECK (
            length(migration_sha256) = 64
            AND migration_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE event_log (
        sequence INTEGER PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        machine TEXT NOT NULL CHECK (machine IN ('request','execution','artifact','delivery')),
        entity_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
        resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 0),
        event_type TEXT NOT NULL,
        source_component_id TEXT NOT NULL,
        occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
        recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= occurred_at_ms),
        accepted INTEGER NOT NULL CHECK (accepted IN (0,1)),
        disposition TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        event_json TEXT NOT NULL CHECK (json_valid(event_json)),
        event_sha256 TEXT NOT NULL CHECK (
            length(event_sha256) = 64
            AND event_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
        result_snapshot_json TEXT NOT NULL CHECK (json_valid(result_snapshot_json)),
        result_snapshot_sha256 TEXT NOT NULL CHECK (
            length(result_snapshot_sha256) = 64
            AND result_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX event_applied_revision_unique
    ON event_log(machine, entity_id, resulting_revision)
    WHERE accepted = 1
    """,
    """
    CREATE INDEX event_request_sequence
    ON event_log(request_id, sequence)
    """,
    """
    CREATE TABLE aggregate_state (
        machine TEXT NOT NULL CHECK (machine IN ('request','execution','artifact','delivery')),
        entity_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        revision INTEGER NOT NULL CHECK (revision >= 0),
        state TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
        last_event_id TEXT UNIQUE,
        snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
        snapshot_sha256 TEXT NOT NULL CHECK (
            length(snapshot_sha256) = 64
            AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        PRIMARY KEY (machine, entity_id),
        FOREIGN KEY (last_event_id) REFERENCES event_log(event_id)
    ) STRICT
    """,
    """
    CREATE INDEX aggregate_request_lookup
    ON aggregate_state(request_id, machine, entity_id)
    """,
)
_MIGRATION_V2_ID = "gateway-request-journal-v2"
_MIGRATION_V2_STATEMENTS = (
    """
    CREATE TABLE request_journal (
        request_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        inbound_id TEXT NOT NULL UNIQUE,
        session_scope_hash TEXT NOT NULL,
        ingress_sha256 TEXT NOT NULL CHECK (
            length(ingress_sha256) = 64
            AND ingress_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        entry_json TEXT NOT NULL CHECK (json_valid(entry_json)),
        entry_sha256 TEXT NOT NULL CHECK (
            length(entry_sha256) = 64
            AND entry_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ) STRICT
    """,
    """
    CREATE TABLE session_actor (
        session_scope_hash TEXT PRIMARY KEY,
        active_request_id TEXT,
        next_sequence INTEGER NOT NULL CHECK (next_sequence >= 1),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
        FOREIGN KEY (active_request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    CREATE TABLE session_queue (
        session_scope_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        request_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK (state IN ('ACTIVE','QUEUED','COMPLETED')),
        enqueued_at_ms INTEGER NOT NULL CHECK (enqueued_at_ms >= 0),
        activated_at_ms INTEGER,
        completed_at_ms INTEGER,
        CHECK (
            (state = 'QUEUED' AND activated_at_ms IS NULL AND completed_at_ms IS NULL)
            OR (state = 'ACTIVE' AND activated_at_ms IS NOT NULL AND completed_at_ms IS NULL)
            OR (state = 'COMPLETED' AND activated_at_ms IS NOT NULL AND completed_at_ms IS NOT NULL)
        ),
        PRIMARY KEY (session_scope_hash, sequence),
        FOREIGN KEY (session_scope_hash) REFERENCES session_actor(session_scope_hash),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX session_one_active_request
    ON session_queue(session_scope_hash)
    WHERE state = 'ACTIVE'
    """,
    """
    CREATE INDEX session_pending_order
    ON session_queue(session_scope_hash, state, sequence)
    """,
)
_MIGRATION_V3_ID = "gateway-transactional-outbox-v3"
_MIGRATION_V3_STATEMENTS = (
    """
    CREATE TABLE outbox (
        outbox_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        destination_component_id TEXT NOT NULL,
        intent_kind TEXT NOT NULL CHECK (intent_kind IN ('EXECUTION','LIFE_READ','DELIVERY','CONTROL')),
        payload_object_id TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('PENDING','CLAIMED','ACKED','AMBIGUOUS')),
        attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
        available_at_ms INTEGER NOT NULL CHECK (available_at_ms >= 0),
        claimed_by TEXT,
        claim_expires_at_ms INTEGER,
        dispatched_at_ms INTEGER,
        result_sha256 TEXT,
        intent_json TEXT NOT NULL CHECK (json_valid(intent_json)),
        intent_sha256 TEXT NOT NULL,
        CHECK (
            (state = 'PENDING' AND claimed_by IS NULL AND claim_expires_at_ms IS NULL AND dispatched_at_ms IS NULL AND result_sha256 IS NULL)
            OR (state = 'CLAIMED' AND claimed_by IS NOT NULL AND claim_expires_at_ms IS NOT NULL AND dispatched_at_ms IS NULL AND result_sha256 IS NULL)
            OR (state IN ('ACKED','AMBIGUOUS') AND claimed_by IS NOT NULL AND claim_expires_at_ms IS NOT NULL AND dispatched_at_ms IS NOT NULL AND result_sha256 IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE TABLE event_outbox (
        event_id TEXT NOT NULL,
        outbox_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY (event_id, outbox_id),
        FOREIGN KEY (event_id) REFERENCES event_log(event_id),
        FOREIGN KEY (outbox_id) REFERENCES outbox(outbox_id)
    ) STRICT
    """,
    """
    CREATE INDEX outbox_dispatch_ready
    ON outbox(state, available_at_ms, outbox_id)
    """,
)
_MIGRATION_V4_ID = "gateway-effect-ledger-v4"
_MIGRATION_V4_STATEMENTS = (
    """
    CREATE TABLE effect_ledger (
        effect_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        effect_kind TEXT NOT NULL CHECK (effect_kind IN ('execution','artifact','delivery','control')),
        owner_component_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('CLAIMED','SIDE_EFFECT_STARTED','SUCCEEDED','FAILED_FINAL','AMBIGUOUS','RECONCILED')),
        claimed_at_ms INTEGER NOT NULL CHECK (claimed_at_ms >= 0),
        side_effect_started_at_ms INTEGER,
        completed_at_ms INTEGER,
        claim_json TEXT NOT NULL CHECK (json_valid(claim_json)),
        claim_sha256 TEXT NOT NULL,
        result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
        result_sha256 TEXT,
        CHECK ((result_json IS NULL) = (result_sha256 IS NULL)),
        CHECK (
            (state = 'CLAIMED' AND side_effect_started_at_ms IS NULL AND completed_at_ms IS NULL AND result_json IS NULL)
            OR (state = 'SIDE_EFFECT_STARTED' AND side_effect_started_at_ms IS NOT NULL AND completed_at_ms IS NULL AND result_json IS NULL)
            OR (state IN ('SUCCEEDED','FAILED_FINAL','AMBIGUOUS','RECONCILED') AND completed_at_ms IS NOT NULL AND result_json IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE INDEX effect_request_lookup
    ON effect_ledger(request_id, run_id, generation, effect_kind)
    """,
)
_MIGRATION_V5_ID = "gateway-security-nonce-ledger-v5"
_MIGRATION_V5_STATEMENTS = (
    """
    CREATE TABLE security_nonce_ledger (
        issuer TEXT NOT NULL,
        audience TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK (purpose IN ('execution_ticket','delivery_ticket','service_auth')),
        nonce TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        consumer_instance_id TEXT NOT NULL,
        consumed_at_ms INTEGER NOT NULL CHECK (consumed_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= consumed_at_ms),
        PRIMARY KEY (issuer, audience, purpose, nonce)
    ) STRICT
    """,
    """
    CREATE INDEX security_nonce_expiry
    ON security_nonce_ledger(expires_at_ms, purpose)
    """,
)
_MIGRATION_V6_ID = "gateway-generation-coordination-v6"
_MIGRATION_V6_STATEMENTS = (
    """
    CREATE TABLE request_generation (
        request_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 1),
        current_generation INTEGER NOT NULL CHECK (current_generation >= 0),
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        active_lease_id TEXT,
        owner_instance_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','CANCELLED','RELEASED')),
        current_fence_id TEXT NOT NULL UNIQUE,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
        cancel_reason_code TEXT,
        CHECK ((status = 'ACTIVE') = (active_lease_id IS NOT NULL)),
        CHECK ((status = 'CANCELLED') = (cancel_reason_code IS NOT NULL))
    ) STRICT
    """,
    """
    CREATE TABLE generation_fences (
        fence_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        lease_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('ACTIVE','SUPERSEDED','CANCELLED','RELEASED')),
        fence_json TEXT NOT NULL CHECK (json_valid(fence_json)),
        fence_sha256 TEXT NOT NULL,
        recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
        FOREIGN KEY (request_id) REFERENCES request_generation(request_id)
    ) STRICT
    """,
    """
    CREATE INDEX generation_fence_history
    ON generation_fences(request_id, generation, recorded_at_ms, fence_id)
    """,
    """
    CREATE TABLE fenced_results (
        result_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        fence_id TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK (disposition IN ('ACCEPTED','LATE_IGNORED','CANCELLED_IGNORED','FENCED_IGNORED')),
        reason_code TEXT NOT NULL,
        result_sha256 TEXT NOT NULL,
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
        decision_sha256 TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES request_generation(request_id),
        FOREIGN KEY (fence_id) REFERENCES generation_fences(fence_id)
    ) STRICT
    """,
)
_MIGRATION_V7_ID = "gateway-async-coordination-v7"
_MIGRATION_V7_STATEMENTS = (
    """
    CREATE TABLE coordination_events (
        event_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        kind TEXT NOT NULL CHECK (kind IN ('NEED_SKILL','NEED_CONFIRMATION')),
        consumer TEXT NOT NULL CHECK (consumer IN ('skill_resolver','user_confirmation')),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
        payload_object_id TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > created_at_ms),
        state_event_id TEXT,
        state TEXT NOT NULL CHECK (state IN ('PENDING','CLAIMED','RESOLVED','CANCELLED')),
        attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
        claimed_by TEXT,
        claim_expires_at_ms INTEGER,
        resolution_json TEXT CHECK (resolution_json IS NULL OR json_valid(resolution_json)),
        resolution_sha256 TEXT,
        cancelled_at_ms INTEGER,
        cancel_reason_code TEXT,
        event_json TEXT NOT NULL CHECK (json_valid(event_json)),
        event_sha256 TEXT NOT NULL,
        UNIQUE (request_id, run_id, generation, kind, ordinal),
        FOREIGN KEY (state_event_id) REFERENCES event_log(event_id),
        CHECK ((claimed_by IS NULL) = (claim_expires_at_ms IS NULL)),
        CHECK ((resolution_json IS NULL) = (resolution_sha256 IS NULL)),
        CHECK (
            (state = 'PENDING' AND claimed_by IS NULL AND resolution_json IS NULL AND cancelled_at_ms IS NULL AND cancel_reason_code IS NULL)
            OR (state = 'CLAIMED' AND claimed_by IS NOT NULL AND resolution_json IS NULL AND cancelled_at_ms IS NULL AND cancel_reason_code IS NULL)
            OR (state = 'RESOLVED' AND claimed_by IS NOT NULL AND resolution_json IS NOT NULL AND cancelled_at_ms IS NULL AND cancel_reason_code IS NULL)
            OR (state = 'CANCELLED' AND resolution_json IS NULL AND cancelled_at_ms IS NOT NULL AND cancel_reason_code IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE INDEX coordination_dispatch_ready
    ON coordination_events(consumer, state, expires_at_ms, created_at_ms, event_id)
    """,
)
_MIGRATION_V8_ID = "gateway-shadow-observations-v8"
_MIGRATION_V8_STATEMENTS = (
    """
    CREATE TABLE shadow_ingress (
        shadow_id TEXT PRIMARY KEY,
        inbound_id TEXT NOT NULL UNIQUE,
        idempotency_key TEXT NOT NULL UNIQUE,
        channel TEXT NOT NULL CHECK (channel IN ('wechat','feishu')),
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        envelope_sha256 TEXT NOT NULL CHECK (
            length(envelope_sha256) = 64
            AND envelope_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        source_ingress_sha256 TEXT NOT NULL CHECK (
            length(source_ingress_sha256) = 64
            AND source_ingress_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        source_ack_permit_sha256 TEXT NOT NULL CHECK (
            length(source_ack_permit_sha256) = 64
            AND source_ack_permit_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        copied_at_ms INTEGER NOT NULL CHECK (copied_at_ms >= 0),
        request_creation_permitted INTEGER NOT NULL CHECK (request_creation_permitted = 0),
        effects_permitted INTEGER NOT NULL CHECK (effects_permitted = 0),
        copy_json TEXT NOT NULL CHECK (json_valid(copy_json)),
        payload_sha256 TEXT NOT NULL CHECK (
            length(payload_sha256) = 64
            AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ) STRICT
    """,
    """
    CREATE TABLE shadow_decision (
        observation_id TEXT PRIMARY KEY,
        shadow_id TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('candidate','legacy')),
        source_component_id TEXT NOT NULL,
        source_instance_id TEXT NOT NULL,
        source_decision_sha256 TEXT NOT NULL CHECK (
            length(source_decision_sha256) = 64
            AND source_decision_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        envelope_sha256 TEXT NOT NULL CHECK (
            length(envelope_sha256) = 64
            AND envelope_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        classification TEXT NOT NULL,
        should_forward INTEGER NOT NULL CHECK (should_forward IN (0,1)),
        attachment_count INTEGER NOT NULL CHECK (attachment_count >= 0),
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        request_creation_permitted INTEGER NOT NULL CHECK (request_creation_permitted = 0),
        effects_permitted INTEGER NOT NULL CHECK (effects_permitted = 0),
        observation_json TEXT NOT NULL CHECK (json_valid(observation_json)),
        payload_sha256 TEXT NOT NULL CHECK (
            length(payload_sha256) = 64
            AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        UNIQUE (shadow_id, side),
        FOREIGN KEY (shadow_id) REFERENCES shadow_ingress(shadow_id)
    ) STRICT
    """,
    """
    CREATE INDEX shadow_decision_compare
    ON shadow_decision(shadow_id, side, observed_at_ms)
    """,
)
_MIGRATION_V9_ID = "gateway-channel-cutover-v9"
_MIGRATION_V9_STATEMENTS = (
    """
    CREATE TABLE channel_cutover (
        cutover_id TEXT PRIMARY KEY,
        migration_epoch INTEGER NOT NULL CHECK (migration_epoch >= 1),
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch = migration_epoch),
        channel TEXT NOT NULL CHECK (channel IN ('wechat','feishu')),
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('DRAINING','DRAINED','CANDIDATE_ACTIVE')),
        legacy_owner_component_id TEXT NOT NULL,
        legacy_owner_instance_id TEXT NOT NULL,
        candidate_owner_instance_id TEXT NOT NULL,
        drain_evidence_id TEXT,
        active_lease_id TEXT,
        revision INTEGER NOT NULL CHECK (revision BETWEEN 1 AND 3),
        started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= started_at_ms),
        snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
        snapshot_payload_sha256 TEXT NOT NULL CHECK (
            length(snapshot_payload_sha256) = 64
            AND snapshot_payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        UNIQUE (channel, tenant_id, link_account_id, gateway_epoch),
        CHECK (
            (state = 'DRAINING' AND drain_evidence_id IS NULL AND active_lease_id IS NULL)
            OR (state = 'DRAINED' AND drain_evidence_id IS NOT NULL AND active_lease_id IS NULL)
            OR (state = 'CANDIDATE_ACTIVE' AND drain_evidence_id IS NOT NULL AND active_lease_id IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE TABLE channel_drain_evidence (
        evidence_id TEXT PRIMARY KEY,
        cutover_id TEXT NOT NULL UNIQUE,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
        payload_sha256 TEXT NOT NULL CHECK (
            length(payload_sha256) = 64
            AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        FOREIGN KEY (cutover_id) REFERENCES channel_cutover(cutover_id)
    ) STRICT
    """,
    """
    CREATE TABLE channel_ownership_lease (
        lease_id TEXT PRIMARY KEY,
        cutover_id TEXT NOT NULL,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        owner_instance_id TEXT NOT NULL,
        previous_lease_sha256 TEXT,
        issued_at_ms INTEGER NOT NULL CHECK (issued_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > issued_at_ms),
        is_active INTEGER NOT NULL CHECK (is_active IN (0,1)),
        lease_json TEXT NOT NULL CHECK (json_valid(lease_json)),
        payload_sha256 TEXT NOT NULL CHECK (
            length(payload_sha256) = 64
            AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        FOREIGN KEY (cutover_id) REFERENCES channel_cutover(cutover_id)
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX channel_one_active_lease
    ON channel_ownership_lease(cutover_id)
    WHERE is_active = 1
    """,
    """
    CREATE INDEX channel_cutover_scope_epoch
    ON channel_cutover(channel, tenant_id, link_account_id, gateway_epoch)
    """,
)
_MIGRATION_V10_ID = "gateway-request-inbound-payload-v10"
_MIGRATION_V10_STATEMENTS = (
    """
    CREATE TABLE request_inbound_payload (
        request_id TEXT PRIMARY KEY,
        availability TEXT NOT NULL CHECK (availability IN ('AVAILABLE','LEGACY_UNAVAILABLE')),
        recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
        envelope_json TEXT CHECK (envelope_json IS NULL OR json_valid(envelope_json)),
        envelope_sha256 TEXT CHECK (
            envelope_sha256 IS NULL OR (
                length(envelope_sha256) = 64
                AND envelope_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        CHECK (
            (availability = 'AVAILABLE' AND envelope_json IS NOT NULL AND envelope_sha256 IS NOT NULL)
            OR (
                availability = 'LEGACY_UNAVAILABLE'
                AND envelope_json IS NULL
                AND envelope_sha256 IS NULL
            )
        ),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    INSERT INTO request_inbound_payload(
        request_id, availability, recorded_at_ms, envelope_json, envelope_sha256
    )
    SELECT request_id, 'LEGACY_UNAVAILABLE', created_at_ms, NULL, NULL
    FROM request_journal
    """,
)
_MIGRATION_V11_ID = "gateway-outbox-dispatch-boundary-v11"
_MIGRATION_V11_STATEMENTS = (
    """
    CREATE TABLE outbox_dispatch_boundary (
        outbox_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE,
        worker_id TEXT NOT NULL,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        ticket_object_id TEXT NOT NULL,
        ticket_sha256 TEXT NOT NULL CHECK (
            length(ticket_sha256) = 64
            AND ticket_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
        result_object_id TEXT,
        result_sha256 TEXT CHECK (
            result_sha256 IS NULL OR (
                length(result_sha256) = 64
                AND result_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        completed_at_ms INTEGER,
        finalized_at_ms INTEGER,
        finalization_sha256 TEXT CHECK (
            finalization_sha256 IS NULL OR (
                length(finalization_sha256) = 64
                AND finalization_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        boundary_sha256 TEXT NOT NULL CHECK (
            length(boundary_sha256) = 64
            AND boundary_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            (result_object_id IS NULL AND result_sha256 IS NULL AND completed_at_ms IS NULL)
            OR (result_object_id IS NOT NULL AND result_sha256 IS NOT NULL AND completed_at_ms IS NOT NULL)
        ),
        CHECK ((finalized_at_ms IS NULL) = (finalization_sha256 IS NULL)),
        FOREIGN KEY (outbox_id) REFERENCES outbox(outbox_id)
    ) STRICT
    """,
    """
    CREATE INDEX outbox_dispatch_boundary_started
    ON outbox_dispatch_boundary(started_at_ms, outbox_id)
    """,
)
_MIGRATION_V12_ID = "gateway-life-continuity-v12"
_MIGRATION_V12_STATEMENTS = (
    """
    CREATE TABLE outbox_v12 (
        outbox_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        destination_component_id TEXT NOT NULL,
        intent_kind TEXT NOT NULL CHECK (
            intent_kind IN ('EXECUTION','LIFE_READ','LIFE_EVENT','DELIVERY','CONTROL')
        ),
        payload_object_id TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('PENDING','CLAIMED','ACKED','AMBIGUOUS')),
        attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
        available_at_ms INTEGER NOT NULL CHECK (available_at_ms >= 0),
        claimed_by TEXT,
        claim_expires_at_ms INTEGER,
        dispatched_at_ms INTEGER,
        result_sha256 TEXT,
        intent_json TEXT NOT NULL CHECK (json_valid(intent_json)),
        intent_sha256 TEXT NOT NULL,
        CHECK (
            (state = 'PENDING' AND claimed_by IS NULL AND claim_expires_at_ms IS NULL AND dispatched_at_ms IS NULL AND result_sha256 IS NULL)
            OR (state = 'CLAIMED' AND claimed_by IS NOT NULL AND claim_expires_at_ms IS NOT NULL AND dispatched_at_ms IS NULL AND result_sha256 IS NULL)
            OR (state IN ('ACKED','AMBIGUOUS') AND claimed_by IS NOT NULL AND claim_expires_at_ms IS NOT NULL AND dispatched_at_ms IS NOT NULL AND result_sha256 IS NOT NULL)
        )
    ) STRICT
    """,
    """
    INSERT INTO outbox_v12 SELECT * FROM outbox
    """,
    """
    CREATE TABLE event_outbox_v12 (
        event_id TEXT NOT NULL,
        outbox_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY (event_id, outbox_id),
        FOREIGN KEY (event_id) REFERENCES event_log(event_id),
        FOREIGN KEY (outbox_id) REFERENCES outbox_v12(outbox_id)
    ) STRICT
    """,
    """
    INSERT INTO event_outbox_v12 SELECT * FROM event_outbox
    """,
    """
    CREATE TABLE outbox_dispatch_boundary_v12 (
        outbox_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE,
        worker_id TEXT NOT NULL,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        ticket_object_id TEXT NOT NULL,
        ticket_sha256 TEXT NOT NULL CHECK (
            length(ticket_sha256) = 64
            AND ticket_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
        result_object_id TEXT,
        result_sha256 TEXT CHECK (
            result_sha256 IS NULL OR (
                length(result_sha256) = 64
                AND result_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        completed_at_ms INTEGER,
        finalized_at_ms INTEGER,
        finalization_sha256 TEXT CHECK (
            finalization_sha256 IS NULL OR (
                length(finalization_sha256) = 64
                AND finalization_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
        boundary_sha256 TEXT NOT NULL CHECK (
            length(boundary_sha256) = 64
            AND boundary_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        CHECK (
            (result_object_id IS NULL AND result_sha256 IS NULL AND completed_at_ms IS NULL)
            OR (result_object_id IS NOT NULL AND result_sha256 IS NOT NULL AND completed_at_ms IS NOT NULL)
        ),
        CHECK ((finalized_at_ms IS NULL) = (finalization_sha256 IS NULL)),
        FOREIGN KEY (outbox_id) REFERENCES outbox_v12(outbox_id)
    ) STRICT
    """,
    """
    INSERT INTO outbox_dispatch_boundary_v12 SELECT * FROM outbox_dispatch_boundary
    """,
    """DROP TABLE event_outbox""",
    """DROP TABLE outbox_dispatch_boundary""",
    """DROP TABLE outbox""",
    """ALTER TABLE outbox_v12 RENAME TO outbox""",
    """ALTER TABLE event_outbox_v12 RENAME TO event_outbox""",
    """ALTER TABLE outbox_dispatch_boundary_v12 RENAME TO outbox_dispatch_boundary""",
    """
    CREATE INDEX outbox_dispatch_ready
    ON outbox(state, available_at_ms, outbox_id)
    """,
    """
    CREATE INDEX outbox_dispatch_boundary_started
    ON outbox_dispatch_boundary(started_at_ms, outbox_id)
    """,
    """
    CREATE TABLE completion_decisions (
        decision_sha256 TEXT PRIMARY KEY CHECK (
            length(decision_sha256) = 64
            AND decision_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        outcome TEXT NOT NULL CHECK (
            outcome IN ('IN_PROGRESS','COMPLETED','PARTIAL','FAILED','RECONCILE_REQUIRED')
        ),
        needs_reconciliation INTEGER NOT NULL CHECK (needs_reconciliation IN (0,1)),
        recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
        decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    CREATE INDEX completion_decisions_request_time
    ON completion_decisions(request_id, run_id, generation, recorded_at_ms, decision_sha256)
    """,
    """
    CREATE TABLE request_capsules (
        capsule_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        life_id TEXT NOT NULL,
        capsule_kind TEXT NOT NULL CHECK (
            capsule_kind IN ('WORKING_CHECKPOINT','COMPRESSION_CHECKPOINT','TERMINAL_RESULT')
        ),
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','SUPERSEDED','TERMINAL')),
        supersedes_capsule_id TEXT,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        capsule_sha256 TEXT NOT NULL UNIQUE CHECK (
            length(capsule_sha256) = 64
            AND capsule_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        capsule_json TEXT NOT NULL CHECK (json_valid(capsule_json)),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id),
        FOREIGN KEY (supersedes_capsule_id) REFERENCES request_capsules(capsule_id)
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX request_capsules_one_active
    ON request_capsules(request_id, run_id, generation)
    WHERE status = 'ACTIVE'
    """,
    """
    CREATE UNIQUE INDEX request_capsules_one_terminal
    ON request_capsules(request_id, run_id, generation)
    WHERE status = 'TERMINAL'
    """,
    """
    CREATE TABLE object_owners (
        object_id TEXT NOT NULL,
        object_sha256 TEXT NOT NULL CHECK (
            length(object_sha256) = 64
            AND object_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        owner_kind TEXT NOT NULL CHECK (
            owner_kind IN ('REQUEST','OUTBOX','COMPLETION','CAPSULE','ARTIFACT','LIFE_EVENT')
        ),
        owner_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        ownership_sha256 TEXT NOT NULL UNIQUE CHECK (
            length(ownership_sha256) = 64
            AND ownership_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        PRIMARY KEY (object_id, owner_kind, owner_id),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    CREATE INDEX object_owners_request
    ON object_owners(request_id, run_id, generation, owner_kind, owner_id)
    """,
)

_MIGRATION_V13_ID = "gateway-skill-authority-v13"
_MIGRATION_V13_STATEMENTS = (
    """
    CREATE TABLE skill_selections (
        selection_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        origin TEXT NOT NULL CHECK (origin IN ('system_recommendation','model_request')),
        operation TEXT NOT NULL CHECK (
            operation IN ('system.recommend','skill.route','skill.list','skill.get','skill.read')
        ),
        skill_catalog_hash TEXT NOT NULL CHECK (
            length(skill_catalog_hash) = 64
            AND skill_catalog_hash NOT GLOB '*[^0-9a-f]*'
        ),
        capability_manifest_hash TEXT NOT NULL CHECK (
            length(capability_manifest_hash) = 64
            AND capability_manifest_hash NOT GLOB '*[^0-9a-f]*'
        ),
        decided_at_ms INTEGER NOT NULL CHECK (decided_at_ms >= 0),
        record_json TEXT NOT NULL CHECK (json_valid(record_json)),
        record_sha256 TEXT NOT NULL UNIQUE CHECK (
            length(record_sha256) = 64
            AND record_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    CREATE INDEX skill_selections_request
    ON skill_selections(request_id, run_id, generation, decided_at_ms, selection_id)
    """,
    """
    CREATE TABLE skill_activations (
        activation_id TEXT PRIMARY KEY,
        selection_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        principal_scope_hash TEXT NOT NULL CHECK (
            length(principal_scope_hash) = 64
            AND principal_scope_hash NOT GLOB '*[^0-9a-f]*'
        ),
        skill_catalog_hash TEXT NOT NULL CHECK (
            length(skill_catalog_hash) = 64
            AND skill_catalog_hash NOT GLOB '*[^0-9a-f]*'
        ),
        capability_manifest_hash TEXT NOT NULL CHECK (
            length(capability_manifest_hash) = 64
            AND capability_manifest_hash NOT GLOB '*[^0-9a-f]*'
        ),
        skill_id TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        skill_sha256 TEXT NOT NULL CHECK (
            length(skill_sha256) = 64
            AND skill_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        issued_at_ms INTEGER NOT NULL CHECK (issued_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= issued_at_ms),
        grant_json TEXT NOT NULL CHECK (json_valid(grant_json)),
        activation_sha256 TEXT NOT NULL UNIQUE CHECK (
            length(activation_sha256) = 64
            AND activation_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        FOREIGN KEY (selection_id) REFERENCES skill_selections(selection_id),
        FOREIGN KEY (request_id) REFERENCES request_journal(request_id)
    ) STRICT
    """,
    """
    CREATE INDEX skill_activations_request
    ON skill_activations(request_id, run_id, generation, issued_at_ms, activation_id)
    """,
    """
    CREATE TABLE skill_activation_tickets (
        activation_id TEXT NOT NULL,
        ticket_id TEXT NOT NULL UNIQUE,
        ticket_sha256 TEXT NOT NULL CHECK (
            length(ticket_sha256) = 64
            AND ticket_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        bound_at_ms INTEGER NOT NULL CHECK (bound_at_ms >= 0),
        binding_sha256 TEXT NOT NULL UNIQUE CHECK (
            length(binding_sha256) = 64
            AND binding_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        PRIMARY KEY (activation_id, ticket_id),
        FOREIGN KEY (activation_id) REFERENCES skill_activations(activation_id)
    ) STRICT
    """,
)

_MIGRATION_V14_ID = "gateway-effect-fact-chain-v14"
_MIGRATION_V14_STATEMENTS = (
    # attempt 子表：一个 effect 的多次尝试链（草案 §3：现有单行 effect_ledger 升级为
    # 同库 attempt/fact 子表 + head 投影；effect_ledger 保持为 head 投影）。
    """
    CREATE TABLE effect_attempts (
        effect_id TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        claim_revision INTEGER NOT NULL CHECK (claim_revision >= 1),
        lease_epoch INTEGER,
        pipeline_version TEXT,
        state TEXT NOT NULL CHECK (state IN (
            'RESERVED','CLAIMED','SIDE_EFFECT_STARTED','PRE_START_AUTHORIZATION_FAILED',
            'SUCCEEDED','FAILED_FINAL','AMBIGUOUS','RECONCILE_REQUIRED','RECONCILED','FENCED'
        )),
        ticket_id TEXT,
        ticket_sha256 TEXT,
        grant_sha256 TEXT,
        nonce_sha256 TEXT,
        claimed_at_ms INTEGER,
        side_effect_started_at_ms INTEGER,
        terminal_at_ms INTEGER,
        terminal_kind TEXT,
        PRIMARY KEY (effect_id, attempt),
        UNIQUE (ticket_id),
        CHECK ((ticket_id IS NULL) = (ticket_sha256 IS NULL))
    ) STRICT
    """,
    """
    CREATE INDEX effect_attempts_state
    ON effect_attempts(state, effect_id)
    """,
    # append-only fact chain：claim/STARTED/nonce/receipt/reconciliation 追加事实
    """
    CREATE TABLE effect_facts (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        effect_id TEXT NOT NULL,
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        fact_kind TEXT NOT NULL CHECK (fact_kind IN (
            'CLAIM','STARTED','DISPATCH_PERMIT','RECEIPT','RECONCILIATION',
            'CONTRADICTION','FENCE','AUTHORIZATION_FAILED'
        )),
        verdict TEXT CHECK (verdict IS NULL OR verdict IN (
            'APPLIED','PROVEN_NOT_APPLIED','INCONCLUSIVE'
        )),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        payload_sha256 TEXT NOT NULL CHECK (
            length(payload_sha256) = 64
            AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        prev_fact_sha256 TEXT NOT NULL CHECK (
            length(prev_fact_sha256) = 64
            AND prev_fact_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        UNIQUE (effect_id, attempt, fact_kind, payload_sha256)
    ) STRICT
    """,
    """
    CREATE INDEX effect_facts_chain
    ON effect_facts(effect_id, attempt, seq)
    """,
    # 全局 action fence 单行：dispatch CAS 与 stop 使用同一 store 行（草案 §3.1）
    """
    CREATE TABLE action_fence (
        fence_id INTEGER PRIMARY KEY CHECK (fence_id = 1),
        action_fence_epoch INTEGER NOT NULL CHECK (action_fence_epoch >= 0),
        inflight_count INTEGER NOT NULL CHECK (inflight_count >= 0),
        draining INTEGER NOT NULL CHECK (draining IN (0, 1)),
        reason TEXT,
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0)
    ) STRICT
    """,
    """
    INSERT INTO action_fence(
        fence_id, action_fence_epoch, inflight_count, draining, reason, updated_at_ms
    ) VALUES (1, 0, 0, 0, 'init', 0)
    """,
    # 存量 effect_ledger 行降级为 head 投影：attempt 1 + 合成事实链
    """
    INSERT INTO effect_attempts(
        effect_id, attempt, claim_revision, lease_epoch, pipeline_version,
        state, ticket_id, ticket_sha256, grant_sha256, nonce_sha256,
        claimed_at_ms, side_effect_started_at_ms, terminal_at_ms, terminal_kind
    )
    SELECT
        effect_id, 1, 1, NULL, NULL,
        CASE
            WHEN state = 'CLAIMED' THEN 'CLAIMED'
            WHEN state = 'SIDE_EFFECT_STARTED' THEN 'SIDE_EFFECT_STARTED'
            WHEN state = 'SUCCEEDED' THEN 'SUCCEEDED'
            WHEN state = 'FAILED_FINAL' THEN 'FAILED_FINAL'
            WHEN state = 'AMBIGUOUS' THEN 'AMBIGUOUS'
            WHEN state = 'RECONCILED' THEN 'RECONCILED'
        END,
        NULL, NULL, NULL, NULL,
        claimed_at_ms, side_effect_started_at_ms, completed_at_ms,
        CASE WHEN completed_at_ms IS NULL THEN NULL ELSE state END
    FROM effect_ledger
    """,
    """
    INSERT INTO effect_facts(
        effect_id, attempt, fact_kind, verdict,
        payload_json, payload_sha256, prev_fact_sha256, created_at_ms
    )
    SELECT
        effect_id, 1, 'CLAIM', NULL,
        claim_json, claim_sha256,
        '0000000000000000000000000000000000000000000000000000000000000000',
        claimed_at_ms
    FROM effect_ledger
    """,
    """
    INSERT INTO effect_facts(
        effect_id, attempt, fact_kind, verdict,
        payload_json, payload_sha256, prev_fact_sha256, created_at_ms
    )
    SELECT
        effect_id, 1, 'RECEIPT', NULL,
        result_json, result_sha256,
        claim_sha256,
        completed_at_ms
    FROM effect_ledger
    WHERE result_json IS NOT NULL
    """,
)

_MIGRATION_V15_ID = "gateway-confirmation-retirement-v15"
_MIGRATION_V15_STATEMENTS = (
    # 草案 §4.2：单调 confirmation_retirement_epoch + retirement receipt。
    # receipt 未提交期间 action_ready=false；恢复旧快照只能向前合并该事实。
    """
    CREATE TABLE confirmation_retirement (
        retirement_id INTEGER PRIMARY KEY CHECK (retirement_id = 1),
        confirmation_retirement_epoch INTEGER NOT NULL CHECK (confirmation_retirement_epoch >= 0),
        retired_at_ms INTEGER NOT NULL CHECK (retired_at_ms >= 0),
        reason TEXT,
        receipt_json TEXT CHECK (receipt_json IS NULL OR json_valid(receipt_json)),
        receipt_sha256 TEXT,
        receipt_committed_at_ms INTEGER,
        CHECK ((receipt_json IS NULL) = (receipt_sha256 IS NULL))
    ) STRICT
    """,
    """
    INSERT INTO confirmation_retirement(
        retirement_id, confirmation_retirement_epoch, retired_at_ms,
        reason, receipt_json, receipt_sha256, receipt_committed_at_ms
    ) VALUES (1, 0, 0, 'init', NULL, NULL, NULL)
    """,
)

_MIGRATION_V17_ID = "gateway-execution-contract-epoch-v17"
_MIGRATION_V17_STATEMENTS = (
    # 草案 §3.3 ExecutionContractCutover：fence+drain+对账完成后 CAS 激活
    # execution_contract_epoch=vNext；新 consumer 只接受新 epoch 后的新 effect。
    """
    CREATE TABLE execution_contract_epoch (
        epoch_id INTEGER PRIMARY KEY CHECK (epoch_id = 1),
        contract_epoch TEXT NOT NULL,
        activated_at_ms INTEGER NOT NULL CHECK (activated_at_ms >= 0),
        fence_epoch_at_activation INTEGER NOT NULL CHECK (fence_epoch_at_activation >= 0),
        nonterminal_disposition_json TEXT NOT NULL CHECK (json_valid(nonterminal_disposition_json)),
        receipt_sha256 TEXT NOT NULL
    ) STRICT
    """,
)

_MIGRATION_V16_ID = "gateway-omni-admission-clarification-v16"
_MIGRATION_V16_STATEMENTS = (
    # D-06 统一 admission：omni grant nonce 落 security_nonce_ledger（purpose 拓宽）。
    # SQLite 不能改 CHECK，按同构重建表；存量行原样搬运。
    """
    CREATE TABLE security_nonce_ledger_v16 (
        issuer TEXT NOT NULL,
        audience TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK (purpose IN (
            'execution_ticket','delivery_ticket','service_auth','omni_capability_grant'
        )),
        nonce TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        consumer_instance_id TEXT NOT NULL,
        consumed_at_ms INTEGER NOT NULL CHECK (consumed_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= consumed_at_ms),
        PRIMARY KEY (issuer, audience, purpose, nonce)
    ) STRICT
    """,
    """
    INSERT INTO security_nonce_ledger_v16
    SELECT issuer, audience, purpose, nonce, payload_sha256, gateway_epoch,
           consumer_instance_id, consumed_at_ms, expires_at_ms
    FROM security_nonce_ledger
    """,
    """
    DROP TABLE security_nonce_ledger
    """,
    """
    ALTER TABLE security_nonce_ledger_v16 RENAME TO security_nonce_ledger
    """,
    """
    CREATE INDEX security_nonce_expiry
    ON security_nonce_ledger(expires_at_ms, purpose)
    """,
    # D-14 澄清不是确认：effect 前的未决问题台账（答复本身不是副作用凭证）。
    """
    CREATE TABLE clarification_questions (
        question_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        question TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('OPEN','ANSWERED','SUPERSEDED')),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        answered_at_ms INTEGER CHECK (answered_at_ms IS NULL OR answered_at_ms >= created_at_ms)
    ) STRICT
    """,
    """
    CREATE INDEX clarification_questions_request
    ON clarification_questions(request_id, state, created_at_ms)
    """,
)

_MIGRATION_V18_ID = "gateway-v21-gate-promotion-v18"
_MIGRATION_V18_STATEMENTS = (
    """
    CREATE TABLE gate_promotion (
        promotion_id TEXT PRIMARY KEY,
        promotion_epoch INTEGER NOT NULL UNIQUE CHECK (promotion_epoch >= 1),
        expected_current_promotion_sha256 TEXT NOT NULL,
        from_gate TEXT NOT NULL,
        to_gate TEXT NOT NULL,
        to_mode TEXT NOT NULL,
        build_id TEXT NOT NULL,
        source_manifest_sha256 TEXT NOT NULL,
        promoted_at_ms INTEGER NOT NULL CHECK (promoted_at_ms >= 0),
        promotion_json TEXT NOT NULL CHECK (json_valid(promotion_json)),
        promotion_payload_sha256 TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE gate_promotion_head (
        head_id INTEGER PRIMARY KEY CHECK (head_id = 1),
        promotion_epoch INTEGER NOT NULL CHECK (promotion_epoch >= 0),
        current_gate TEXT NOT NULL,
        current_mode TEXT NOT NULL,
        current_promotion_sha256 TEXT NOT NULL
    ) STRICT
    """,
    """
    INSERT INTO gate_promotion_head(
        head_id, promotion_epoch, current_gate, current_mode, current_promotion_sha256
    ) VALUES (1, 0, 'BASELINE', 'legacy_observe', lower(hex(zeroblob(32))))
    """,
)

_MIGRATION_V19_ID = "gateway-v21-model-response-saga-v19"
_MIGRATION_V19_STATEMENTS = (
    """CREATE TABLE model_attempt_plan (
        model_attempt_plan_id TEXT PRIMARY KEY,
        model_effect_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 0),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        run_life_binding_sha256 TEXT NOT NULL CHECK (length(run_life_binding_sha256) = 64),
        root_experience_id TEXT NOT NULL,
        response_episode_id TEXT NOT NULL,
        response_episode_sha256 TEXT NOT NULL CHECK (length(response_episode_sha256) = 64),
        context_pack_ref TEXT NOT NULL,
        context_pack_sha256 TEXT NOT NULL CHECK (length(context_pack_sha256) = 64),
        response_basis_kind TEXT NOT NULL CHECK (response_basis_kind IN ('commitment','conversation')),
        response_basis_sha256 TEXT NOT NULL CHECK (length(response_basis_sha256) = 64),
        capability_profile_sha256 TEXT NOT NULL CHECK (length(capability_profile_sha256) = 64),
        provider_slots_json TEXT NOT NULL CHECK (json_valid(provider_slots_json)),
        plan_revision INTEGER NOT NULL CHECK (plan_revision >= 1),
        request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
        completion_delivery_mode TEXT CHECK (completion_delivery_mode IN ('none','response_delivery')),
        completion_decision_ref TEXT,
        completion_decision_sha256 TEXT,
        conversation_basis_ref TEXT,
        plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
        plan_payload_sha256 TEXT NOT NULL CHECK (length(plan_payload_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        UNIQUE (response_episode_id, plan_revision)
    ) STRICT""",
    """CREATE INDEX model_attempt_plan_response
    ON model_attempt_plan(response_episode_id)""",
    """CREATE TABLE model_dispatch_marker (
        marker_id TEXT PRIMARY KEY,
        model_attempt_plan_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL UNIQUE,
        slot_no INTEGER NOT NULL CHECK (slot_no >= 1),
        status TEXT NOT NULL CHECK (status IN ('pending','dispatched')),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        dispatched_at_ms INTEGER CHECK (dispatched_at_ms IS NULL OR dispatched_at_ms >= created_at_ms),
        UNIQUE (model_attempt_plan_id, slot_no)
    ) STRICT""",
    """CREATE TABLE model_attempt_result (
        model_attempt_receipt_id TEXT PRIMARY KEY,
        model_attempt_plan_id TEXT NOT NULL,
        model_attempt_plan_sha256 TEXT NOT NULL CHECK (length(model_attempt_plan_sha256) = 64),
        model_effect_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 0),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        run_life_binding_sha256 TEXT NOT NULL CHECK (length(run_life_binding_sha256) = 64),
        root_experience_id TEXT NOT NULL,
        response_episode_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        slot_no INTEGER NOT NULL CHECK (slot_no >= 1),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('SUCCEEDED','FAILED_RETRYABLE','FAILED_FINAL','AMBIGUOUS','CANCELLED')),
        attempt_plan_revision INTEGER NOT NULL CHECK (attempt_plan_revision >= 1),
        request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
        dispatched INTEGER NOT NULL CHECK (dispatched IN (0, 1)),
        started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
        completed_at_ms INTEGER NOT NULL CHECK (completed_at_ms >= 0),
        response_schema_valid INTEGER NOT NULL CHECK (response_schema_valid IN (0, 1)),
        result_json TEXT NOT NULL CHECK (json_valid(result_json)),
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
        UNIQUE (model_attempt_plan_id, slot_no)
    ) STRICT""",
    """CREATE INDEX model_attempt_result_response
    ON model_attempt_result(response_episode_id)""",
    """CREATE TABLE model_attempt_plan_outcome (
        model_attempt_plan_outcome_id TEXT PRIMARY KEY,
        model_attempt_plan_id TEXT NOT NULL UNIQUE,
        model_attempt_plan_sha256 TEXT NOT NULL CHECK (length(model_attempt_plan_sha256) = 64),
        status TEXT NOT NULL CHECK (status IN ('SUCCEEDED','EXHAUSTED')),
        ordered_attempt_refs_json TEXT NOT NULL CHECK (json_valid(ordered_attempt_refs_json)),
        winner_attempt_ref TEXT,
        completed_at_ms INTEGER NOT NULL CHECK (completed_at_ms >= 0),
        outcome_json TEXT NOT NULL CHECK (json_valid(outcome_json)),
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    ) STRICT""",
    """CREATE TABLE assistant_commit (
        assistant_commit_id TEXT PRIMARY KEY,
        assistant_message_id TEXT NOT NULL,
        life_turn_commit_ref TEXT NOT NULL,
        life_turn_commit_sha256 TEXT NOT NULL CHECK (length(life_turn_commit_sha256) = 64),
        response_episode_id TEXT NOT NULL UNIQUE,
        model_attempt_plan_outcome_ref TEXT NOT NULL,
        model_attempt_receipt_id TEXT NOT NULL,
        output_text_sha256 TEXT NOT NULL CHECK (length(output_text_sha256) = 64),
        committed_text_sha256 TEXT NOT NULL CHECK (length(committed_text_sha256) = 64),
        text_object_id TEXT NOT NULL,
        committed_at_ms INTEGER NOT NULL CHECK (committed_at_ms >= 0),
        commit_json TEXT NOT NULL CHECK (json_valid(commit_json)),
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    ) STRICT""",
    """CREATE TABLE system_status (
        system_status_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 0),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        response_episode_id TEXT NOT NULL,
        status_code TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('info','warning','error','fatal')),
        source_component TEXT NOT NULL,
        source_fact_refs_json TEXT NOT NULL CHECK (json_valid(source_fact_refs_json)),
        display_object_ref TEXT NOT NULL,
        origin TEXT NOT NULL CHECK (origin = 'system'),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        status_json TEXT NOT NULL CHECK (json_valid(status_json)),
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    ) STRICT""",
    """CREATE INDEX system_status_response
    ON system_status(response_episode_id)""",
    """CREATE TABLE effect_outcome_head (
        effect_id TEXT PRIMARY KEY,
        original_execution_result_ref TEXT NOT NULL,
        effective_status TEXT NOT NULL CHECK (effective_status IN (
            'SUCCEEDED','FAILED_RETRYABLE','FAILED_FINAL','AMBIGUOUS','CANCELLED','FENCED'
        )),
        head_revision INTEGER NOT NULL CHECK (head_revision >= 1),
        latest_reconciliation_ref TEXT,
        head_sha256 TEXT NOT NULL CHECK (length(head_sha256) = 64),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0)
    ) STRICT""",
)

_MIGRATION_V20_ID = "gateway-v21-life-proposal-registration-v20"
_MIGRATION_V20_STATEMENTS = (
    """CREATE TABLE life_proposal_registration (
        proposal_id TEXT PRIMARY KEY,
        proposal_sha256 TEXT NOT NULL CHECK (length(proposal_sha256) = 64),
        registration_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 0),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        run_life_binding_sha256 TEXT NOT NULL CHECK (length(run_life_binding_sha256) = 64),
        action_intent_id TEXT NOT NULL,
        action_intent_sha256 TEXT NOT NULL CHECK (length(action_intent_sha256) = 64),
        proposal_json TEXT NOT NULL CHECK (json_valid(proposal_json)),
        receipt_json TEXT NOT NULL CHECK (json_valid(receipt_json)),
        registered_at_ms INTEGER NOT NULL CHECK (registered_at_ms >= 0)
    ) STRICT""",
    """CREATE TABLE effect_reconciliation (
        reconciliation_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL,
        previous_outcome_head_sha256 TEXT NOT NULL CHECK (length(previous_outcome_head_sha256) = 64),
        attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
        strategy_id TEXT NOT NULL,
        observation_status TEXT NOT NULL CHECK (observation_status IN ('APPLIED','PROVEN_NOT_APPLIED','INCONCLUSIVE')),
        observation_ref TEXT NOT NULL,
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        record_json TEXT NOT NULL CHECK (json_valid(record_json)),
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    ) STRICT""",
    """CREATE INDEX effect_reconciliation_effect
    ON effect_reconciliation(effect_id, attempt_no)""",
    """CREATE TABLE composite_execution_outcome (
        composite_execution_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        run_sequence INTEGER NOT NULL CHECK (run_sequence >= 0),
        generation INTEGER NOT NULL CHECK (generation >= 0),
        parent_effect_id TEXT NOT NULL,
        child_result_refs_json TEXT NOT NULL CHECK (json_valid(child_result_refs_json)),
        compensation_effect_refs_json TEXT NOT NULL CHECK (json_valid(compensation_effect_refs_json)),
        warning_refs_json TEXT NOT NULL CHECK (json_valid(warning_refs_json)),
        status TEXT NOT NULL,
        retry_required INTEGER NOT NULL CHECK (retry_required IN (0, 1)),
        summary_sha256 TEXT NOT NULL CHECK (length(summary_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        outcome_json TEXT NOT NULL CHECK (json_valid(outcome_json)),
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    ) STRICT""",
)


def _migration_sha256(version: int, migration_id: str, statements: tuple[str, ...]) -> str:
    return canonical_sha256(
        {"migration_id": migration_id, "statements": statements, "version": version}
    )


_MIGRATIONS = (
    (1, _MIGRATION_V1_ID, _MIGRATION_V1_STATEMENTS),
    (2, _MIGRATION_V2_ID, _MIGRATION_V2_STATEMENTS),
    (3, _MIGRATION_V3_ID, _MIGRATION_V3_STATEMENTS),
    (4, _MIGRATION_V4_ID, _MIGRATION_V4_STATEMENTS),
    (5, _MIGRATION_V5_ID, _MIGRATION_V5_STATEMENTS),
    (6, _MIGRATION_V6_ID, _MIGRATION_V6_STATEMENTS),
    (7, _MIGRATION_V7_ID, _MIGRATION_V7_STATEMENTS),
    (8, _MIGRATION_V8_ID, _MIGRATION_V8_STATEMENTS),
    (9, _MIGRATION_V9_ID, _MIGRATION_V9_STATEMENTS),
    (10, _MIGRATION_V10_ID, _MIGRATION_V10_STATEMENTS),
    (11, _MIGRATION_V11_ID, _MIGRATION_V11_STATEMENTS),
    (12, _MIGRATION_V12_ID, _MIGRATION_V12_STATEMENTS),
    (13, _MIGRATION_V13_ID, _MIGRATION_V13_STATEMENTS),
    (14, _MIGRATION_V14_ID, _MIGRATION_V14_STATEMENTS),
    (15, _MIGRATION_V15_ID, _MIGRATION_V15_STATEMENTS),
    (16, _MIGRATION_V16_ID, _MIGRATION_V16_STATEMENTS),
    (17, _MIGRATION_V17_ID, _MIGRATION_V17_STATEMENTS),
    (18, _MIGRATION_V18_ID, _MIGRATION_V18_STATEMENTS),
    (19, _MIGRATION_V19_ID, _MIGRATION_V19_STATEMENTS),
    (20, _MIGRATION_V20_ID, _MIGRATION_V20_STATEMENTS),
)
_MIGRATION_DIGESTS = {
    version: _migration_sha256(version, migration_id, statements)
    for version, migration_id, statements in _MIGRATIONS
}


class StoreError(RuntimeError):
    pass


class StoreMigrationError(StoreError):
    pass


class StoreCorruptionError(StoreError):
    pass


class StoreConflictError(StoreError):
    pass


class StoreCasConflict(StoreConflictError):
    pass


class StoreNotFoundError(StoreError):
    pass


class RequestJournalEntry(BaseModel):
    """Immutable mapping from one durable inbound fact to one gateway request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    inbound_id: str = Field(min_length=1, max_length=160)
    session_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ingress_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if derive_request_identity(self.idempotency_key).request_id != self.request_id:
            raise ValueError("journal request ID is not derived from the inbound idempotency key")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"entry_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.entry_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"entry_sha256": self.computed_sha256()})


@dataclass(frozen=True)
class StoreApplyResult:
    decision: TransitionDecision
    persisted_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class JournalRegistration:
    entry: RequestJournalEntry
    queue_sequence: int
    queue_state: str
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class ActiveRequestCandidate:
    entry: RequestJournalEntry
    envelope: InboundEnvelope
    queue: SessionQueueSnapshot


@dataclass(frozen=True)
class ActiveRequestActivation:
    entry: RequestJournalEntry
    envelope: InboundEnvelope
    queue: SessionQueueSnapshot
    generation: GenerationLeaseView
    request_snapshot: StateSnapshot
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class ShadowBatchRegistration:
    comparison: ShadowComparison
    copy_created: bool
    observations_created: int
    duplicate: bool


@dataclass(frozen=True)
class ChannelOwnershipRegistration:
    snapshot: ChannelCutoverSnapshot
    lease: ChannelOwnershipLease
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class SessionQueueSnapshot:
    session_scope_hash: str
    request_id: str
    sequence: int
    state: str
    enqueued_at_ms: int
    activated_at_ms: int | None
    completed_at_ms: int | None


@dataclass(frozen=True)
class OutboxRecord:
    intent: OutboxIntent
    state: str
    attempt_count: int
    available_at_ms: int
    claimed_by: str | None
    claim_expires_at_ms: int | None
    dispatched_at_ms: int | None
    result_sha256: str | None


@dataclass(frozen=True)
class OutboxDispatchBoundary:
    outbox_id: str
    effect_id: str
    worker_id: str
    gateway_epoch: int
    ticket_object_id: str
    ticket_sha256: str
    started_at_ms: int
    result_object_id: str | None
    result_sha256: str | None
    completed_at_ms: int | None
    finalized_at_ms: int | None
    finalization_sha256: str | None
    boundary_sha256: str


@dataclass(frozen=True)
class CompletionDecisionRecord:
    decision: CompletionDecision
    recorded_at_ms: int
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class RequestCapsuleRecord:
    capsule: TaskContinuityCapsule
    status: str
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class SkillSelectionRegistration:
    record: SkillSelectionRecord
    record_sha256: str
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class SkillActivationRegistration:
    grant: SkillActivationGrant
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class SkillActivationTicketBinding:
    activation_id: str
    ticket_id: str
    ticket_sha256: str
    bound_at_ms: int
    binding_sha256: str
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class ObjectOwnerRecord:
    object_id: str
    object_sha256: str
    owner_kind: str
    owner_id: str
    request_id: str
    run_id: str
    generation: int
    created_at_ms: int
    ownership_sha256: str
    created_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class EffectLedgerRecord:
    claim: EffectClaim
    state: str
    side_effect_started_at_ms: int | None
    completed_at_ms: int | None
    result: EffectResult | None


@dataclass(frozen=True)
class NonceConsumption:
    issuer: str
    audience: str
    purpose: str
    nonce: str
    payload_sha256: str
    gateway_epoch: int
    consumer_instance_id: str
    consumed_at_ms: int
    expires_at_ms: int
    consumed_by_this_call: bool


@dataclass(frozen=True)
class StoreHealthEvidence:
    healthy: bool
    reason_code: str
    checked_at_ms: int
    schema_version: int
    schema_sha256: str | None
    journal_mode: str | None
    writable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "reason_code": self.reason_code,
            "checked_at_ms": self.checked_at_ms,
            "schema_version": self.schema_version,
            "schema_sha256": self.schema_sha256,
            "journal_mode": self.journal_mode,
            "writable": self.writable,
        }


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return canonical_sha256(
        tuple(
            {
                "type": row[0],
                "name": row[1],
                "table": row[2],
                "sql": row[3],
            }
            for row in rows
        )
    )


@lru_cache(maxsize=1)
def expected_store_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for _, _, statements in _MIGRATIONS:
            for statement in statements:
                connection.execute(statement)
        return _schema_fingerprint(connection)
    finally:
        connection.close()


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA synchronous = FULL")
    journal = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(journal).lower() != "wal":
        raise StoreMigrationError("gateway store could not enable WAL mode")


def _validate_metadata(connection: sqlite3.Connection) -> None:
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if application_id != APPLICATION_ID:
        raise StoreMigrationError("gateway store application ID is invalid")
    if user_version != STORE_SCHEMA_VERSION:
        raise StoreMigrationError("gateway store schema version is unsupported")
    rows = connection.execute(
        "SELECT version, migration_id, migration_sha256 FROM schema_migrations ORDER BY version"
    ).fetchall()
    if len(rows) != len(_MIGRATIONS):
        raise StoreMigrationError("gateway store migration history is incomplete")
    for row, (version, migration_id, _) in zip(rows, _MIGRATIONS, strict=True):
        if (
            row["version"] != version
            or row["migration_id"] != migration_id
            or row["migration_sha256"] != _MIGRATION_DIGESTS[version]
        ):
            raise StoreMigrationError("gateway store migration record is invalid")
    if _schema_fingerprint(connection) != expected_store_schema_sha256():
        raise StoreMigrationError("gateway store schema fingerprint is invalid")


def _migrate(connection: sqlite3.Connection, *, applied_at_ms: int) -> None:
    if applied_at_ms < 0:
        raise ValueError("migration time is invalid")
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    objects = connection.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if application_id not in {0, APPLICATION_ID}:
        raise StoreMigrationError("database belongs to another application")
    if user_version > STORE_SCHEMA_VERSION:
        raise StoreMigrationError("gateway store is newer than this binary")
    if user_version == 0:
        if objects:
            raise StoreMigrationError("unversioned gateway store is not empty")
        connection.execute("BEGIN EXCLUSIVE")
        try:
            for version, migration_id, statements in _MIGRATIONS:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(
                        version, migration_id, migration_sha256, applied_at_ms
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (version, migration_id, _MIGRATION_DIGESTS[version], applied_at_ms),
                )
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    elif user_version < STORE_SCHEMA_VERSION:
        existing = connection.execute(
            "SELECT version, migration_id, migration_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        if len(existing) != user_version:
            raise StoreMigrationError("gateway store prior migration history is incomplete")
        for row, (version, migration_id, _) in zip(
            existing,
            _MIGRATIONS[:user_version],
            strict=True,
        ):
            if (
                row["version"] != version
                or row["migration_id"] != migration_id
                or row["migration_sha256"] != _MIGRATION_DIGESTS[version]
            ):
                raise StoreMigrationError("gateway store prior migration record is invalid")
        expected_prior = sqlite3.connect(":memory:", isolation_level=None)
        try:
            for _, _, statements in _MIGRATIONS[:user_version]:
                for statement in statements:
                    expected_prior.execute(statement)
            if _schema_fingerprint(connection) != _schema_fingerprint(expected_prior):
                raise StoreMigrationError("gateway store prior schema fingerprint is invalid")
        finally:
            expected_prior.close()
        connection.execute("BEGIN EXCLUSIVE")
        try:
            for version, migration_id, statements in _MIGRATIONS[user_version:]:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(
                        version, migration_id, migration_sha256, applied_at_ms
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (version, migration_id, _MIGRATION_DIGESTS[version], applied_at_ms),
                )
                connection.execute(f"PRAGMA user_version = {version}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    _validate_metadata(connection)


def _snapshot_payload(snapshot: StateSnapshot) -> tuple[str, str]:
    payload = canonical_json_bytes(snapshot.model_dump(mode="json"))
    return payload.decode("utf-8"), canonical_sha256(snapshot.model_dump(mode="json"))


def _event_payload(event: TransitionEvent) -> str:
    return canonical_json_bytes(event.model_dump(mode="json")).decode("utf-8")


def _decision_payload(decision: TransitionDecision) -> str:
    return canonical_json_bytes(decision.model_dump(mode="json")).decode("utf-8")


def _journal_payload(entry: RequestJournalEntry) -> tuple[str, str]:
    data = entry.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _inbound_envelope_payload(envelope: InboundEnvelope) -> tuple[str, str]:
    data = envelope.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _desktop_retry_equivalent(
    stored: InboundEnvelope,
    candidate: InboundEnvelope,
) -> bool:
    """Match a desktop transport retry without weakening message identity.

    The renderer may lose the 202 response and resubmit the same stable
    ``message_id`` with a fresh wall-clock timestamp.  The timestamp is
    transport evidence, not business content.  Every other envelope field,
    including session scope, presentation metadata hash, text and attachments,
    must remain byte-for-byte canonical-equivalent.
    """

    if stored.channel != "desktop" or candidate.channel != "desktop":
        return False
    left = stored.model_dump(mode="json", exclude={"received_at_ms"})
    right = candidate.model_dump(mode="json", exclude={"received_at_ms"})
    return canonical_json_bytes(left) == canonical_json_bytes(right)


def _parse_inbound_envelope(payload: str, claimed_sha256: str) -> InboundEnvelope:
    try:
        envelope = InboundEnvelope.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored inbound envelope is invalid") from exc
    canonical, actual = _inbound_envelope_payload(envelope)
    if canonical != payload or actual != claimed_sha256:
        raise StoreCorruptionError("stored inbound envelope digest is invalid")
    return envelope


def _parse_journal_entry(payload: str, claimed_sha256: str) -> RequestJournalEntry:
    try:
        entry = RequestJournalEntry.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored request journal entry is invalid") from exc
    canonical, actual = _journal_payload(entry)
    if canonical != payload or actual != claimed_sha256 or not entry.has_valid_sha256():
        raise StoreCorruptionError("stored request journal entry digest is invalid")
    return entry


def _shadow_payload(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _parse_shadow_copy(payload: str, claimed_sha256: str) -> ShadowIngressCopy:
    try:
        copy = ShadowIngressCopy.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored shadow ingress copy is invalid") from exc
    canonical, actual = _shadow_payload(copy)
    if canonical != payload or actual != claimed_sha256 or not copy.has_valid_sha256():
        raise StoreCorruptionError("stored shadow ingress copy digest is invalid")
    return copy


def _parse_shadow_observation(
    payload: str,
    claimed_sha256: str,
) -> ShadowDecisionObservation:
    try:
        observation = ShadowDecisionObservation.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored shadow decision observation is invalid") from exc
    canonical, actual = _shadow_payload(observation)
    if (
        canonical != payload
        or actual != claimed_sha256
        or not observation.has_valid_sha256()
    ):
        raise StoreCorruptionError("stored shadow decision observation digest is invalid")
    return observation


def _cutover_payload(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _parse_channel_cutover(
    payload: str,
    claimed_sha256: str,
) -> ChannelCutoverSnapshot:
    try:
        snapshot = ChannelCutoverSnapshot.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored channel cutover is invalid") from exc
    canonical, actual = _cutover_payload(snapshot)
    if canonical != payload or actual != claimed_sha256 or not snapshot.has_valid_sha256():
        raise StoreCorruptionError("stored channel cutover digest is invalid")
    return snapshot


def _parse_gate_promotion(payload: str, claimed_sha256: str) -> GatePromotionRecord:
    try:
        record = GatePromotionRecord.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored gate promotion is invalid") from exc
    canonical, actual = _cutover_payload(record)
    if canonical != payload or actual != claimed_sha256 or not record.has_valid_sha256():
        raise StoreCorruptionError("stored gate promotion digest is invalid")
    return record


def _parse_channel_drain(
    payload: str,
    claimed_sha256: str,
) -> ChannelDrainEvidence:
    try:
        evidence = ChannelDrainEvidence.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored channel drain evidence is invalid") from exc
    canonical, actual = _cutover_payload(evidence)
    if canonical != payload or actual != claimed_sha256 or not evidence.has_valid_sha256():
        raise StoreCorruptionError("stored channel drain evidence digest is invalid")
    return evidence


def _parse_channel_lease(
    payload: str,
    claimed_sha256: str,
) -> ChannelOwnershipLease:
    try:
        lease = ChannelOwnershipLease.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored channel ownership lease is invalid") from exc
    canonical, actual = _cutover_payload(lease)
    if canonical != payload or actual != claimed_sha256 or not lease.has_valid_sha256():
        raise StoreCorruptionError("stored channel ownership lease digest is invalid")
    return lease


def _outbox_payload(intent: OutboxIntent) -> tuple[str, str]:
    data = intent.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _parse_outbox_intent(payload: str, claimed_sha256: str) -> OutboxIntent:
    try:
        intent = OutboxIntent.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored outbox intent is invalid") from exc
    canonical, actual = _outbox_payload(intent)
    if canonical != payload or actual != claimed_sha256 or not intent.has_valid_sha256():
        raise StoreCorruptionError("stored outbox intent digest is invalid")
    return intent


def _outbox_record_from_row(row: sqlite3.Row) -> OutboxRecord:
    return OutboxRecord(
        intent=_parse_outbox_intent(row["intent_json"], row["intent_sha256"]),
        state=row["state"],
        attempt_count=row["attempt_count"],
        available_at_ms=row["available_at_ms"],
        claimed_by=row["claimed_by"],
        claim_expires_at_ms=row["claim_expires_at_ms"],
        dispatched_at_ms=row["dispatched_at_ms"],
        result_sha256=row["result_sha256"],
    )


def _completion_decision_payload(decision: CompletionDecision) -> str:
    return canonical_json_bytes(decision.model_dump(mode="json")).decode("utf-8")


def _parse_completion_decision(payload: str, claimed_sha256: str) -> CompletionDecision:
    from .completion_gate import CompletionDecision

    try:
        decision = CompletionDecision.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored completion decision is invalid") from exc
    if (
        _completion_decision_payload(decision) != payload
        or decision.decision_sha256 != claimed_sha256
        or not decision.has_valid_sha256()
    ):
        raise StoreCorruptionError("stored completion decision digest is invalid")
    return decision


def _capsule_payload(capsule: TaskContinuityCapsule) -> str:
    return canonical_json_bytes(capsule.model_dump(mode="json")).decode("utf-8")


def _parse_capsule(payload: str, claimed_sha256: str) -> TaskContinuityCapsule:
    try:
        capsule = TaskContinuityCapsule.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored continuity capsule is invalid") from exc
    if (
        _capsule_payload(capsule) != payload
        or capsule.capsule_sha256 != claimed_sha256
        or not capsule.has_valid_capsule_sha256()
    ):
        raise StoreCorruptionError("stored continuity capsule digest is invalid")
    return capsule


def _capsule_record_from_row(
    row: sqlite3.Row,
    *,
    created_by_this_call: bool = False,
    duplicate: bool = False,
) -> RequestCapsuleRecord:
    return RequestCapsuleRecord(
        capsule=_parse_capsule(row["capsule_json"], row["capsule_sha256"]),
        status=row["status"],
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
    )


def _skill_selection_payload(record: SkillSelectionRecord) -> tuple[str, str]:
    value = record.model_dump(mode="json")
    return canonical_json_bytes(value).decode("utf-8"), canonical_sha256(value)


def _parse_skill_selection(payload: str, claimed_sha256: str) -> SkillSelectionRecord:
    try:
        record = SkillSelectionRecord.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored Skill selection is invalid") from exc
    canonical, actual = _skill_selection_payload(record)
    if canonical != payload or actual != claimed_sha256:
        raise StoreCorruptionError("stored Skill selection digest is invalid")
    return record


def _skill_selection_from_row(
    row: sqlite3.Row, *, created_by_this_call: bool = False, duplicate: bool = False
) -> SkillSelectionRegistration:
    return SkillSelectionRegistration(
        record=_parse_skill_selection(row["record_json"], row["record_sha256"]),
        record_sha256=row["record_sha256"],
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
    )


def _skill_activation_payload(grant: SkillActivationGrant) -> str:
    return canonical_json_bytes(grant.model_dump(mode="json")).decode("utf-8")


def _parse_skill_activation(payload: str, claimed_sha256: str) -> SkillActivationGrant:
    try:
        grant = SkillActivationGrant.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored Skill activation is invalid") from exc
    if (
        _skill_activation_payload(grant) != payload
        or grant.activation_sha256 != claimed_sha256
        or not grant.has_valid_sha256()
    ):
        raise StoreCorruptionError("stored Skill activation digest is invalid")
    return grant


def _skill_activation_from_row(
    row: sqlite3.Row, *, created_by_this_call: bool = False, duplicate: bool = False
) -> SkillActivationRegistration:
    return SkillActivationRegistration(
        grant=_parse_skill_activation(row["grant_json"], row["activation_sha256"]),
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
    )


def _object_ownership_sha256(
    *,
    object_id: str,
    object_sha256: str,
    owner_kind: str,
    owner_id: str,
    request_id: str,
    run_id: str,
    generation: int,
    created_at_ms: int,
) -> str:
    return canonical_sha256(
        {
            "created_at_ms": created_at_ms,
            "domain": "tiangong.gateway.object-owner.v1",
            "generation": generation,
            "object_id": object_id,
            "object_sha256": object_sha256,
            "owner_id": owner_id,
            "owner_kind": owner_kind,
            "request_id": request_id,
            "run_id": run_id,
        }
    )


def _object_owner_from_row(
    row: sqlite3.Row, *, created_by_this_call: bool = False, duplicate: bool = False
) -> ObjectOwnerRecord:
    expected = _object_ownership_sha256(
        object_id=row["object_id"],
        object_sha256=row["object_sha256"],
        owner_kind=row["owner_kind"],
        owner_id=row["owner_id"],
        request_id=row["request_id"],
        run_id=row["run_id"],
        generation=row["generation"],
        created_at_ms=row["created_at_ms"],
    )
    if expected != row["ownership_sha256"]:
        raise StoreCorruptionError("stored object owner digest is invalid")
    return ObjectOwnerRecord(
        object_id=row["object_id"],
        object_sha256=row["object_sha256"],
        owner_kind=row["owner_kind"],
        owner_id=row["owner_id"],
        request_id=row["request_id"],
        run_id=row["run_id"],
        generation=row["generation"],
        created_at_ms=row["created_at_ms"],
        ownership_sha256=row["ownership_sha256"],
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
    )


def _ensure_outbox_object_owner_locked(
    connection: sqlite3.Connection,
    intent: OutboxIntent,
) -> None:
    digest = _object_ownership_sha256(
        object_id=intent.payload_object_id,
        object_sha256=intent.payload_sha256,
        owner_kind="OUTBOX",
        owner_id=intent.outbox_id,
        request_id=intent.request_id,
        run_id=intent.run_id,
        generation=intent.generation,
        created_at_ms=intent.created_at_ms,
    )
    row = connection.execute(
        """
        SELECT * FROM object_owners
        WHERE object_id = ? AND owner_kind = 'OUTBOX' AND owner_id = ?
        """,
        (intent.payload_object_id, intent.outbox_id),
    ).fetchone()
    if row is not None:
        if _object_owner_from_row(row).ownership_sha256 != digest:
            raise StoreConflictError("life outbox object ownership changed")
        return
    connection.execute(
        """
        INSERT INTO object_owners(
            object_id, object_sha256, owner_kind, owner_id,
            request_id, run_id, generation, created_at_ms, ownership_sha256
        ) VALUES (?, ?, 'OUTBOX', ?, ?, ?, ?, ?, ?)
        """,
        (
            intent.payload_object_id,
            intent.payload_sha256,
            intent.outbox_id,
            intent.request_id,
            intent.run_id,
            intent.generation,
            intent.created_at_ms,
            digest,
        ),
    )


def _outbox_boundary_sha256(
    *,
    outbox_id: str,
    effect_id: str,
    worker_id: str,
    gateway_epoch: int,
    ticket_object_id: str,
    ticket_sha256: str,
    started_at_ms: int,
) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.gateway.outbox-dispatch-boundary.v1",
            "effect_id": effect_id,
            "gateway_epoch": gateway_epoch,
            "outbox_id": outbox_id,
            "started_at_ms": started_at_ms,
            "ticket_object_id": ticket_object_id,
            "ticket_sha256": ticket_sha256,
            "worker_id": worker_id,
        }
    )


def _outbox_boundary_from_row(row: sqlite3.Row) -> OutboxDispatchBoundary:
    boundary = OutboxDispatchBoundary(
        outbox_id=row["outbox_id"],
        effect_id=row["effect_id"],
        worker_id=row["worker_id"],
        gateway_epoch=row["gateway_epoch"],
        ticket_object_id=row["ticket_object_id"],
        ticket_sha256=row["ticket_sha256"],
        started_at_ms=row["started_at_ms"],
        result_object_id=row["result_object_id"],
        result_sha256=row["result_sha256"],
        completed_at_ms=row["completed_at_ms"],
        finalized_at_ms=row["finalized_at_ms"],
        finalization_sha256=row["finalization_sha256"],
        boundary_sha256=row["boundary_sha256"],
    )
    expected = _outbox_boundary_sha256(
        outbox_id=boundary.outbox_id,
        effect_id=boundary.effect_id,
        worker_id=boundary.worker_id,
        gateway_epoch=boundary.gateway_epoch,
        ticket_object_id=boundary.ticket_object_id,
        ticket_sha256=boundary.ticket_sha256,
        started_at_ms=boundary.started_at_ms,
    )
    if expected != boundary.boundary_sha256:
        raise StoreCorruptionError("stored outbox dispatch boundary digest is invalid")
    return boundary


def _coordination_payload(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _coordination_record_from_row(row: sqlite3.Row) -> CoordinationRecord:
    try:
        event = CoordinationEvent.model_validate_json(row["event_json"], strict=True)
        resolution = (
            None
            if row["resolution_json"] is None
            else CoordinationResolution.model_validate_json(row["resolution_json"], strict=True)
        )
    except ValueError as exc:
        raise StoreCorruptionError("stored coordination event is invalid") from exc
    event_json, event_digest = _coordination_payload(event)
    if (
        event_json != row["event_json"]
        or event_digest != row["event_sha256"]
        or not event.has_valid_sha256()
    ):
        raise StoreCorruptionError("stored coordination event digest is invalid")
    expected = {
        "event_id": event.event_id,
        "request_id": event.request_id,
        "run_id": event.run_id,
        "generation": event.generation,
        "kind": event.kind,
        "consumer": event.consumer,
        "ordinal": event.ordinal,
        "payload_object_id": event.payload_object_id,
        "payload_sha256": event.payload_sha256,
        "created_at_ms": event.created_at_ms,
        "expires_at_ms": event.expires_at_ms,
    }
    if any(row[name] != value for name, value in expected.items()):
        raise StoreCorruptionError("coordination event columns disagree with canonical payload")
    if resolution is not None:
        resolution_json, resolution_digest = _coordination_payload(resolution)
        if (
            resolution_json != row["resolution_json"]
            or resolution_digest != row["resolution_sha256"]
            or not resolution.has_valid_sha256()
            or resolution.event_id != event.event_id
        ):
            raise StoreCorruptionError("stored coordination resolution digest is invalid")
    return CoordinationRecord(
        event=event,
        state=row["state"],
        attempt_count=row["attempt_count"],
        claimed_by=row["claimed_by"],
        claim_expires_at_ms=row["claim_expires_at_ms"],
        resolution=resolution,
        cancelled_at_ms=row["cancelled_at_ms"],
        cancel_reason_code=row["cancel_reason_code"],
    )


def _insert_coordination_row(
    connection: sqlite3.Connection,
    event: CoordinationEvent,
    *,
    state_event_id: str | None,
) -> tuple[CoordinationRecord, bool]:
    event_json, event_digest = _coordination_payload(event)
    row = connection.execute(
        """
        SELECT * FROM coordination_events
        WHERE event_id = ? OR (
            request_id = ? AND run_id = ? AND generation = ? AND kind = ? AND ordinal = ?
        )
        """,
        (
            event.event_id, event.request_id, event.run_id, event.generation,
            event.kind, event.ordinal,
        ),
    ).fetchone()
    if row is not None:
        record = _coordination_record_from_row(row)
        if record.event != event or row["state_event_id"] != state_event_id:
            raise StoreConflictError("coordination identity was reused with different intent")
        return record, False
    connection.execute(
        """
        INSERT INTO coordination_events(
            event_id, request_id, run_id, generation, kind, consumer, ordinal,
            payload_object_id, payload_sha256, created_at_ms, expires_at_ms,
            state_event_id, state, attempt_count, claimed_by, claim_expires_at_ms,
            resolution_json, resolution_sha256, cancelled_at_ms, cancel_reason_code,
            event_json, event_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0,
                  NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
        """,
        (
            event.event_id, event.request_id, event.run_id, event.generation,
            event.kind, event.consumer, event.ordinal, event.payload_object_id,
            event.payload_sha256, event.created_at_ms, event.expires_at_ms,
            state_event_id, event_json, event_digest,
        ),
    )
    row = connection.execute(
        "SELECT * FROM coordination_events WHERE event_id = ?", (event.event_id,)
    ).fetchone()
    return _coordination_record_from_row(row), True


def _effect_model_payload(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _effect_claim_payload(claim: EffectClaim) -> tuple[str, str]:
    # vNext 字段全默认的历史行按 vOld 字段集重放字节与摘要（草案 §3：head 投影兼容）
    if claim._is_vold_shape():
        data = claim.model_dump(mode="json", exclude=claim._VOLD_FIELDS)
        return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)
    return _effect_model_payload(claim)


def _effect_record_from_row(row: sqlite3.Row) -> EffectLedgerRecord:
    try:
        claim = EffectClaim.model_validate_json(row["claim_json"], strict=True)
        result = None if row["result_json"] is None else EffectResult.model_validate_json(
            row["result_json"], strict=True
        )
    except ValueError as exc:
        raise StoreCorruptionError("stored effect ledger payload is invalid") from exc
    claim_json, claim_digest = _effect_claim_payload(claim)
    if claim_json != row["claim_json"] or claim_digest != row["claim_sha256"] or not claim.has_valid_sha256():
        raise StoreCorruptionError("stored effect claim digest is invalid")
    if result is not None:
        result_json, result_digest = _effect_model_payload(result)
        if (
            result_json != row["result_json"]
            or result_digest != row["result_sha256"]
            or not result.has_valid_sha256()
            or result.effect_id != claim.effect_id
            or result.status != row["state"]
        ):
            raise StoreCorruptionError("stored effect result digest or binding is invalid")
    return EffectLedgerRecord(
        claim,
        row["state"],
        row["side_effect_started_at_ms"],
        row["completed_at_ms"],
        result,
    )


def _fence_payload(fence: GenerationFence) -> tuple[str, str]:
    data = fence.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _parse_fence(payload: str, claimed_sha256: str) -> GenerationFence:
    try:
        fence = GenerationFence.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored generation fence is invalid") from exc
    canonical, digest = _fence_payload(fence)
    if canonical != payload or digest != claimed_sha256 or not fence.has_valid_fence():
        raise StoreCorruptionError("stored generation fence digest is invalid")
    return fence


def _generation_view(connection: sqlite3.Connection, row: sqlite3.Row) -> GenerationLeaseView:
    fence_row = connection.execute(
        "SELECT * FROM generation_fences WHERE fence_id = ?",
        (row["current_fence_id"],),
    ).fetchone()
    if fence_row is None:
        raise StoreCorruptionError("request generation references a missing fence")
    fence = _parse_fence(fence_row["fence_json"], fence_row["fence_sha256"])
    return GenerationLeaseView(
        row["request_id"], row["run_id"], row["run_sequence"], row["current_generation"],
        row["gateway_epoch"], row["active_lease_id"], row["owner_instance_id"], row["status"],
        fence, row["revision"], row["updated_at_ms"], row["cancel_reason_code"],
    )


def _fenced_result_payload(
    *,
    result_id: str,
    request_id: str,
    run_id: str,
    generation: int,
    fence_id: str,
    disposition: str,
    reason_code: str,
    result_sha256: str,
    observed_at_ms: int,
    fence_decision: FenceDecision | None,
) -> dict[str, object]:
    return {
        "result_id": result_id,
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "fence_id": fence_id,
        "disposition": disposition,
        "reason_code": reason_code,
        "result_sha256": result_sha256,
        "observed_at_ms": observed_at_ms,
        "fence_decision": None if fence_decision is None else fence_decision.model_dump(mode="json"),
    }


def _parse_fenced_result(row: sqlite3.Row, *, duplicate: bool) -> FencedResultDecision:
    try:
        data = json.loads(row["decision_json"])
        fence_decision = (
            None
            if data["fence_decision"] is None
            else FenceDecision.model_validate(data["fence_decision"], strict=True)
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StoreCorruptionError("stored fenced-result decision is invalid") from exc
    canonical = canonical_json_bytes(data).decode("utf-8")
    if canonical != row["decision_json"] or canonical_sha256(data) != row["decision_sha256"]:
        raise StoreCorruptionError("stored fenced-result decision digest is invalid")
    expected = _fenced_result_payload(
        result_id=row["result_id"], request_id=row["request_id"], run_id=row["run_id"],
        generation=row["generation"], fence_id=row["fence_id"], disposition=row["disposition"],
        reason_code=row["reason_code"], result_sha256=row["result_sha256"],
        observed_at_ms=row["observed_at_ms"], fence_decision=fence_decision,
    )
    if data != expected:
        raise StoreCorruptionError("fenced-result columns disagree with decision payload")
    return FencedResultDecision(
        row["result_id"], row["request_id"], row["run_id"], row["generation"], row["fence_id"],
        row["disposition"], row["reason_code"], row["result_sha256"], row["observed_at_ms"],
        not duplicate, duplicate, fence_decision,
    )


def _parse_snapshot(payload: str, claimed_sha256: str) -> StateSnapshot:
    try:
        snapshot = StateSnapshot.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored state snapshot is invalid") from exc
    canonical, actual = _snapshot_payload(snapshot)
    if canonical != payload or actual != claimed_sha256:
        raise StoreCorruptionError("stored state snapshot digest is invalid")
    return snapshot


def _verify_snapshot_columns(row: sqlite3.Row, snapshot: StateSnapshot) -> None:
    expected = {
        "machine": snapshot.machine,
        "entity_id": snapshot.entity_id,
        "request_id": snapshot.request_id,
        "run_id": snapshot.run_id,
        "generation": snapshot.generation,
        "revision": snapshot.revision,
        "state": snapshot.state,
        "created_at_ms": snapshot.created_at_ms,
        "updated_at_ms": snapshot.updated_at_ms,
        "last_event_id": snapshot.last_event_id,
    }
    if any(row[name] != value for name, value in expected.items()):
        raise StoreCorruptionError("state snapshot columns disagree with canonical payload")


def _verify_event_row(
    row: sqlite3.Row,
) -> tuple[TransitionEvent, TransitionDecision, StateSnapshot]:
    try:
        event = TransitionEvent.model_validate_json(row["event_json"], strict=True)
        decision = TransitionDecision.model_validate_json(row["decision_json"], strict=True)
    except ValueError as exc:
        raise StoreCorruptionError("stored event or transition decision is invalid") from exc
    if _event_payload(event) != row["event_json"] or not event.has_valid_event_sha256():
        raise StoreCorruptionError("stored event payload is noncanonical or has an invalid digest")
    if event.event_sha256 != row["event_sha256"]:
        raise StoreCorruptionError("stored event digest column disagrees with event payload")
    if _decision_payload(decision) != row["decision_json"]:
        raise StoreCorruptionError("stored transition decision is not canonical")
    result = _parse_snapshot(row["result_snapshot_json"], row["result_snapshot_sha256"])
    if result != decision.current:
        raise StoreCorruptionError("stored event result disagrees with transition decision")
    if apply_transition(decision.previous, event) != decision:
        raise StoreCorruptionError("stored transition decision cannot be reproduced")
    event_columns = {
        "event_id": event.event_id,
        "machine": event.machine,
        "entity_id": event.entity_id,
        "request_id": event.request_id,
        "run_id": event.run_id,
        "generation": event.generation,
        "expected_revision": event.expected_revision,
        "event_type": event.event_type,
        "source_component_id": event.source_component_id,
        "occurred_at_ms": event.occurred_at_ms,
    }
    if any(row[name] != value for name, value in event_columns.items()):
        raise StoreCorruptionError("stored event columns disagree with canonical event")
    if (
        bool(row["accepted"]) != decision.accepted
        or row["disposition"] != decision.disposition
        or row["reason_code"] != decision.reason_code
        or row["resulting_revision"] != decision.current.revision
    ):
        raise StoreCorruptionError("stored transition columns disagree with decision")
    if decision.accepted:
        if (
            event.expected_revision + 1 != decision.current.revision
            or decision.previous.revision != event.expected_revision
        ):
            raise StoreCorruptionError("accepted event revision chain is invalid")
    elif decision.previous != decision.current:
        raise StoreCorruptionError("rejected event changed persistent state")
    return event, decision, result


def _verify_current_state_rows(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT * FROM aggregate_state ORDER BY machine, entity_id").fetchall()
    for row in rows:
        snapshot = _parse_snapshot(row["snapshot_json"], row["snapshot_sha256"])
        _verify_snapshot_columns(row, snapshot)
        if snapshot.revision == 0:
            if snapshot.last_event_id is not None:
                raise StoreCorruptionError("revision-zero state references an event")
            continue
        if snapshot.last_event_id is None:
            raise StoreCorruptionError("advanced state is missing its last event")
        event_row = connection.execute(
            "SELECT * FROM event_log WHERE event_id = ? AND accepted = 1",
            (snapshot.last_event_id,),
        ).fetchone()
        if event_row is None:
            raise StoreCorruptionError("state last event is missing or was rejected")
        _, decision, result = _verify_event_row(event_row)
        if result != snapshot or decision.current != snapshot:
            raise StoreCorruptionError("state snapshot does not equal its last applied event")


def _verify_request_journal_rows(connection: sqlite3.Connection) -> None:
    journal: dict[str, RequestJournalEntry] = {}
    for row in connection.execute("SELECT * FROM request_journal ORDER BY request_id").fetchall():
        entry = _parse_journal_entry(row["entry_json"], row["entry_sha256"])
        expected = {
            "request_id": entry.request_id,
            "idempotency_key": entry.idempotency_key,
            "inbound_id": entry.inbound_id,
            "session_scope_hash": entry.session_scope_hash,
            "ingress_sha256": entry.ingress_sha256,
            "created_at_ms": entry.created_at_ms,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("request journal columns disagree with canonical entry")
        journal[entry.request_id] = entry

    queued_ids: set[str] = set()
    for actor in connection.execute("SELECT * FROM session_actor ORDER BY session_scope_hash").fetchall():
        rows = connection.execute(
            "SELECT * FROM session_queue WHERE session_scope_hash = ? ORDER BY sequence",
            (actor["session_scope_hash"],),
        ).fetchall()
        if not rows or [row["sequence"] for row in rows] != list(range(1, len(rows) + 1)):
            raise StoreCorruptionError("session queue sequence is not contiguous")
        active = [row for row in rows if row["state"] == "ACTIVE"]
        if len(active) > 1:
            raise StoreCorruptionError("session has more than one active request")
        active_request_id = active[0]["request_id"] if active else None
        if actor["active_request_id"] != active_request_id:
            raise StoreCorruptionError("session actor active request disagrees with queue")
        if actor["next_sequence"] != len(rows) + 1:
            raise StoreCorruptionError("session actor next sequence is invalid")
        completed_count = sum(row["state"] == "COMPLETED" for row in rows)
        if actor["revision"] != len(rows) + completed_count:
            raise StoreCorruptionError("session actor revision cannot be reproduced")
        seen_queued = False
        for row in rows:
            entry = journal.get(row["request_id"])
            if entry is None or entry.session_scope_hash != actor["session_scope_hash"]:
                raise StoreCorruptionError("session queue request is missing or crosses scope")
            if row["request_id"] in queued_ids:
                raise StoreCorruptionError("request appears in more than one session queue")
            queued_ids.add(row["request_id"])
            if row["state"] == "QUEUED":
                seen_queued = True
            elif seen_queued:
                raise StoreCorruptionError("session queue state order is invalid")
    if queued_ids != set(journal):
        raise StoreCorruptionError("request journal and session queue membership disagree")
    payload_ids: set[str] = set()
    for row in connection.execute(
        "SELECT * FROM request_inbound_payload ORDER BY request_id"
    ).fetchall():
        entry = journal.get(row["request_id"])
        if entry is None or row["request_id"] in payload_ids:
            raise StoreCorruptionError("request inbound payload has invalid journal membership")
        if row["recorded_at_ms"] != entry.created_at_ms:
            raise StoreCorruptionError("request inbound payload time disagrees with journal")
        if row["availability"] == "AVAILABLE":
            envelope = _parse_inbound_envelope(
                row["envelope_json"],
                row["envelope_sha256"],
            )
            if (
                derive_request_identity(envelope.idempotency_key).request_id != entry.request_id
                or envelope.idempotency_key != entry.idempotency_key
                or envelope.inbound_id != entry.inbound_id
                or envelope.conversation_scope_hash != entry.session_scope_hash
                or envelope.received_at_ms > entry.created_at_ms
            ):
                raise StoreCorruptionError("request inbound envelope disagrees with journal")
        elif (
            row["availability"] != "LEGACY_UNAVAILABLE"
            or row["envelope_json"] is not None
            or row["envelope_sha256"] is not None
        ):
            raise StoreCorruptionError("legacy request payload marker is invalid")
        payload_ids.add(row["request_id"])
    if payload_ids != set(journal):
        raise StoreCorruptionError("request journal and inbound payload membership disagree")


def _verify_outbox_rows(connection: sqlite3.Connection) -> None:
    outbox_ids: set[str] = set()
    for row in connection.execute("SELECT * FROM outbox ORDER BY outbox_id").fetchall():
        intent = _parse_outbox_intent(row["intent_json"], row["intent_sha256"])
        expected = {
            "outbox_id": intent.outbox_id,
            "effect_id": intent.effect_id,
            "request_id": intent.request_id,
            "run_id": intent.run_id,
            "generation": intent.generation,
            "destination_component_id": intent.destination_component_id,
            "intent_kind": intent.intent_kind,
            "payload_object_id": intent.payload_object_id,
            "payload_sha256": intent.payload_sha256,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("outbox columns disagree with canonical intent")
        link = connection.execute(
            """
            SELECT e.request_id, e.run_id, e.generation, e.accepted
            FROM event_outbox eo JOIN event_log e ON e.event_id = eo.event_id
            WHERE eo.outbox_id = ?
            """,
            (intent.outbox_id,),
        ).fetchone()
        if (
            link is None
            or link["accepted"] != 1
            or link["request_id"] != intent.request_id
            or link["run_id"] != intent.run_id
            or link["generation"] != intent.generation
        ):
            raise StoreCorruptionError("outbox intent is not bound to its accepted state event")
        if row["state"] in {"ACKED", "AMBIGUOUS"} and row["dispatched_at_ms"] < intent.created_at_ms:
            raise StoreCorruptionError("outbox result predates intent creation")
        outbox_ids.add(intent.outbox_id)
    linked = {
        row[0]
        for row in connection.execute("SELECT outbox_id FROM event_outbox").fetchall()
    }
    if linked != outbox_ids:
        raise StoreCorruptionError("outbox and event binding membership disagree")


def _verify_outbox_dispatch_boundaries(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        "SELECT * FROM outbox_dispatch_boundary ORDER BY outbox_id"
    ).fetchall():
        boundary = _outbox_boundary_from_row(row)
        outbox = connection.execute(
            "SELECT * FROM outbox WHERE outbox_id = ?",
            (boundary.outbox_id,),
        ).fetchone()
        if outbox is None:
            raise StoreCorruptionError("outbox dispatch boundary has no intent")
        intent = _parse_outbox_intent(outbox["intent_json"], outbox["intent_sha256"])
        if (
            boundary.effect_id != intent.effect_id
            or boundary.worker_id != outbox["claimed_by"]
            or boundary.started_at_ms < intent.created_at_ms
            or outbox["state"] == "PENDING"
            or (
                outbox["dispatched_at_ms"] is not None
                and outbox["dispatched_at_ms"] < boundary.started_at_ms
            )
            or ((boundary.result_object_id is None) != (outbox["dispatched_at_ms"] is None))
            or (
                boundary.completed_at_ms is not None
                and outbox["dispatched_at_ms"] != boundary.completed_at_ms
            )
            or (
                boundary.result_sha256 is not None
                and outbox["result_sha256"] != boundary.result_sha256
            )
            or (
                boundary.finalized_at_ms is not None
                and (
                    boundary.completed_at_ms is None
                    or boundary.finalized_at_ms < boundary.completed_at_ms
                )
            )
        ):
            raise StoreCorruptionError("outbox dispatch boundary disagrees with its intent")


def _verify_life_continuity_rows(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        "SELECT * FROM completion_decisions ORDER BY recorded_at_ms, decision_sha256"
    ).fetchall():
        decision = _parse_completion_decision(row["decision_json"], row["decision_sha256"])
        generation = connection.execute(
            """
            SELECT 1 FROM generation_fences
            WHERE request_id = ? AND run_id = ? AND generation = ?
            LIMIT 1
            """,
            (decision.request_id, decision.run_id, decision.generation),
        ).fetchone()
        if (
            row["request_id"] != decision.request_id
            or row["run_id"] != decision.run_id
            or row["generation"] != decision.generation
            or row["outcome"] != decision.outcome
            or bool(row["needs_reconciliation"]) != decision.needs_reconciliation
            or generation is None
        ):
            raise StoreCorruptionError("completion decision columns disagree with payload")
    capsules: dict[str, tuple[sqlite3.Row, TaskContinuityCapsule]] = {}
    for row in connection.execute(
        "SELECT * FROM request_capsules ORDER BY created_at_ms, capsule_id"
    ).fetchall():
        capsule = _parse_capsule(row["capsule_json"], row["capsule_sha256"])
        valid_status = (
            row["status"] == "TERMINAL"
            if capsule.capsule_kind == "TERMINAL_RESULT"
            else row["status"] in {"ACTIVE", "SUPERSEDED"}
        )
        generation = connection.execute(
            """
            SELECT 1 FROM generation_fences
            WHERE request_id = ? AND run_id = ? AND generation = ?
            LIMIT 1
            """,
            (capsule.request_id, capsule.run_id, capsule.generation),
        ).fetchone()
        if (
            row["capsule_id"] != capsule.capsule_id
            or row["request_id"] != capsule.request_id
            or row["run_id"] != capsule.run_id
            or row["generation"] != capsule.generation
            or row["life_id"] != capsule.life_id
            or row["capsule_kind"] != capsule.capsule_kind
            or row["supersedes_capsule_id"] != capsule.supersedes_capsule_id
            or row["created_at_ms"] != capsule.created_at_ms
            or not valid_status
            or generation is None
        ):
            raise StoreCorruptionError("continuity capsule columns disagree with payload")
        capsules[capsule.capsule_id] = (row, capsule)
    for row, capsule in capsules.values():
        if capsule.supersedes_capsule_id is None:
            continue
        previous = capsules.get(capsule.supersedes_capsule_id)
        if previous is None:
            raise StoreCorruptionError("continuity capsule supersedes a missing capsule")
        previous_row, previous_capsule = previous
        if (
            previous_capsule.request_id != capsule.request_id
            or previous_capsule.run_id != capsule.run_id
            or previous_capsule.generation != capsule.generation
            or previous_capsule.life_id != capsule.life_id
            or previous_row["status"] != "SUPERSEDED"
            or previous_capsule.created_at_ms > capsule.created_at_ms
        ):
            raise StoreCorruptionError("continuity capsule supersession chain is invalid")
    referenced = {
        capsule.supersedes_capsule_id
        for _, capsule in capsules.values()
        if capsule.supersedes_capsule_id is not None
    }
    for capsule_id, (row, _) in capsules.items():
        if (row["status"] == "SUPERSEDED") != (capsule_id in referenced):
            raise StoreCorruptionError("continuity capsule status is not chain-derived")
    for row in connection.execute(
        "SELECT * FROM object_owners ORDER BY object_id, owner_kind, owner_id"
    ).fetchall():
        owner = _object_owner_from_row(row)
        journal = connection.execute(
            "SELECT request_id FROM request_journal WHERE request_id = ?",
            (owner.request_id,),
        ).fetchone()
        if journal is None:
            raise StoreCorruptionError("object owner references a missing request")
        generation = connection.execute(
            """
            SELECT 1 FROM generation_fences
            WHERE request_id = ? AND run_id = ? AND generation = ?
            LIMIT 1
            """,
            (owner.request_id, owner.run_id, owner.generation),
        ).fetchone()
        if generation is None:
            raise StoreCorruptionError("object owner crossed a generation fence")
        if owner.owner_kind == "OUTBOX":
            outbox = connection.execute(
                "SELECT payload_object_id, payload_sha256 FROM outbox WHERE outbox_id = ?",
                (owner.owner_id,),
            ).fetchone()
            if (
                outbox is None
                or outbox["payload_object_id"] != owner.object_id
                or outbox["payload_sha256"] != owner.object_sha256
            ):
                raise StoreCorruptionError("outbox object owner binding is invalid")
        elif owner.owner_kind == "CAPSULE" and owner.owner_id not in capsules:
            raise StoreCorruptionError("capsule object owner references a missing capsule")
        elif owner.owner_kind == "COMPLETION":
            decision = connection.execute(
                "SELECT 1 FROM completion_decisions WHERE decision_sha256 = ?",
                (owner.owner_id,),
            ).fetchone()
            if decision is None:
                raise StoreCorruptionError("completion object owner references a missing decision")
        elif owner.owner_kind == "LIFE_EVENT":
            event = connection.execute(
                "SELECT 1 FROM event_log WHERE event_id = ? AND accepted = 1",
                (owner.owner_id,),
            ).fetchone()
            if event is None:
                raise StoreCorruptionError("life-event object owner references a missing fact")


def _verify_effect_rows(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT * FROM effect_ledger ORDER BY effect_id").fetchall():
        record = _effect_record_from_row(row)
        claim = record.claim
        expected = {
            "effect_id": claim.effect_id,
            "request_id": claim.request_id,
            "run_id": claim.run_id,
            "generation": claim.generation,
            "effect_kind": claim.effect_kind,
            "owner_component_id": claim.owner_component_id,
            "claimed_at_ms": claim.claimed_at_ms,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("effect ledger columns disagree with canonical claim")
        if record.side_effect_started_at_ms is not None and record.side_effect_started_at_ms < claim.claimed_at_ms:
            raise StoreCorruptionError("effect side-effect start predates claim")
        if record.completed_at_ms is not None:
            minimum = record.side_effect_started_at_ms or claim.claimed_at_ms
            if record.completed_at_ms < minimum:
                raise StoreCorruptionError("effect result predates its durable boundary")


def _verify_nonce_rows(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT * FROM security_nonce_ledger").fetchall():
        if (
            not row["issuer"]
            or not row["audience"]
            or not row["nonce"]
            or not row["consumer_instance_id"]
            or len(row["payload_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in row["payload_sha256"])
            or row["expires_at_ms"] < row["consumed_at_ms"]
        ):
            raise StoreCorruptionError("security nonce ledger row is invalid")


def _verify_generation_rows(connection: sqlite3.Connection) -> None:
    requests = {
        row["request_id"]: row
        for row in connection.execute("SELECT * FROM request_generation").fetchall()
    }
    fences: dict[str, tuple[sqlite3.Row, GenerationFence]] = {}
    for row in connection.execute("SELECT * FROM generation_fences ORDER BY recorded_at_ms, fence_id").fetchall():
        fence = _parse_fence(row["fence_json"], row["fence_sha256"])
        expected = {
            "fence_id": fence.fence_id,
            "request_id": fence.request_id,
            "run_id": fence.run_id,
            "generation": fence.generation,
            "gateway_epoch": fence.gateway_epoch,
            "lease_id": fence.lease_id,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("generation fence columns disagree with canonical fence")
        if fence.supersedes_fence_id is not None and fence.supersedes_fence_id not in fences:
            raise StoreCorruptionError("generation fence supersedes a missing or future fence")
        fences[fence.fence_id] = (row, fence)
    for request_id, row in requests.items():
        view = _generation_view(connection, row)
        if (
            view.fence.request_id != request_id
            or view.fence.run_id != view.run_id
            or view.fence.run_sequence != view.run_sequence
            or view.fence.generation != view.generation
            or view.fence.gateway_epoch != view.gateway_epoch
        ):
            raise StoreCorruptionError("request generation disagrees with current fence")
        expected_fence_state = {
            "ACTIVE": "ACTIVE",
            "CANCELLED": "CANCELLED",
            "RELEASED": "RELEASED",
        }[view.status]
        if fences[view.fence.fence_id][0]["state"] != expected_fence_state:
            raise StoreCorruptionError("request generation status disagrees with current fence")
        active_count = connection.execute(
            "SELECT count(*) FROM generation_fences WHERE request_id = ? AND state = 'ACTIVE'",
            (request_id,),
        ).fetchone()[0]
        if active_count != (1 if view.status == "ACTIVE" else 0):
            raise StoreCorruptionError("request generation has an invalid active-fence count")
    for row in connection.execute("SELECT * FROM fenced_results").fetchall():
        if row["request_id"] not in requests or row["fence_id"] not in fences:
            raise StoreCorruptionError("fenced result references missing coordination state")
        _parse_fenced_result(row, duplicate=False)


def _verify_coordination_rows(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT * FROM coordination_events ORDER BY created_at_ms, event_id").fetchall():
        record = _coordination_record_from_row(row)
        event = record.event
        if row["state_event_id"] is not None:
            state_row = connection.execute(
                "SELECT * FROM event_log WHERE event_id = ?", (row["state_event_id"],)
            ).fetchone()
            if (
                state_row is None
                or state_row["accepted"] != 1
                or state_row["request_id"] != event.request_id
                or state_row["run_id"] != event.run_id
                or state_row["generation"] != event.generation
            ):
                raise StoreCorruptionError("coordination event is not bound to its accepted state event")
            if event.kind == "NEED_CONFIRMATION":
                state_result = _parse_snapshot(
                    state_row["result_snapshot_json"], state_row["result_snapshot_sha256"]
                )
                if state_result.machine != "request" or state_result.state != "WAITING_CONFIRMATION":
                    raise StoreCorruptionError("confirmation event is not bound to a waiting request")
        if record.claim_expires_at_ms is not None and record.claim_expires_at_ms <= event.created_at_ms:
            raise StoreCorruptionError("coordination claim expiry predates event creation")
        if record.resolution is not None:
            resolution = record.resolution
            allowed = {
                "NEED_SKILL": {"SKILL_SELECTED", "NO_SKILL", "SKILL_REJECTED"},
                "NEED_CONFIRMATION": {"CONFIRMED", "DENIED", "EXPIRED"},
            }[event.kind]
            expected_resolver = {
                "NEED_SKILL": "tiangong-total-gateway",
                "NEED_CONFIRMATION": "tiangong-desktop",
            }[event.kind]
            if resolution.outcome not in allowed or resolution.resolver_component_id != expected_resolver:
                raise StoreCorruptionError("coordination resolution exceeds its event authority")
            if resolution.resolved_at_ms < event.created_at_ms:
                raise StoreCorruptionError("coordination resolution predates its event")
        if record.cancelled_at_ms is not None and record.cancelled_at_ms < event.created_at_ms:
            raise StoreCorruptionError("coordination cancellation predates its event")


def _verify_shadow_rows(connection: sqlite3.Connection) -> None:
    copies: dict[str, ShadowIngressCopy] = {}
    for row in connection.execute("SELECT * FROM shadow_ingress ORDER BY shadow_id").fetchall():
        copy = _parse_shadow_copy(row["copy_json"], row["payload_sha256"])
        expected = {
            "shadow_id": copy.shadow_id,
            "inbound_id": copy.envelope.inbound_id,
            "idempotency_key": copy.envelope.idempotency_key,
            "channel": copy.envelope.channel,
            "tenant_id": copy.envelope.tenant_id,
            "link_account_id": copy.envelope.link_account_id,
            "envelope_sha256": copy.envelope_sha256,
            "source_ingress_sha256": copy.source_ingress_sha256,
            "source_ack_permit_sha256": copy.source_ack_permit_sha256,
            "copied_at_ms": copy.copied_at_ms,
            "request_creation_permitted": 0,
            "effects_permitted": 0,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("shadow ingress columns disagree with canonical copy")
        copies[copy.shadow_id] = copy

    sides: dict[str, set[str]] = {shadow_id: set() for shadow_id in copies}
    for row in connection.execute(
        "SELECT * FROM shadow_decision ORDER BY shadow_id, side"
    ).fetchall():
        observation = _parse_shadow_observation(
            row["observation_json"], row["payload_sha256"]
        )
        copy = copies.get(observation.shadow_id)
        if copy is None:
            raise StoreCorruptionError("shadow decision references a missing ingress copy")
        expected = {
            "observation_id": observation.observation_id,
            "shadow_id": observation.shadow_id,
            "side": observation.side,
            "source_component_id": observation.source_component_id,
            "source_instance_id": observation.source_instance_id,
            "source_decision_sha256": observation.source_decision_sha256,
            "envelope_sha256": observation.envelope_sha256,
            "classification": observation.classification,
            "should_forward": int(observation.should_forward),
            "attachment_count": observation.attachment_count,
            "observed_at_ms": observation.observed_at_ms,
            "request_creation_permitted": 0,
            "effects_permitted": 0,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("shadow decision columns disagree with observation")
        if (
            observation.envelope_sha256 != copy.envelope_sha256
            or observation.observed_at_ms < copy.envelope.received_at_ms
            or observation.side in sides[observation.shadow_id]
        ):
            raise StoreCorruptionError("shadow decision is not bound to its ingress copy")
        sides[observation.shadow_id].add(observation.side)
    if any(not observed_sides for observed_sides in sides.values()):
        raise StoreCorruptionError("shadow ingress copy has no decision observation")


def _verify_channel_cutover_rows(connection: sqlite3.Connection) -> None:
    cutovers: dict[str, ChannelCutoverSnapshot] = {}
    for row in connection.execute(
        "SELECT * FROM channel_cutover ORDER BY cutover_id"
    ).fetchall():
        snapshot = _parse_channel_cutover(
            row["snapshot_json"], row["snapshot_payload_sha256"]
        )
        expected = {
            "cutover_id": snapshot.cutover_id,
            "migration_epoch": snapshot.migration_epoch,
            "gateway_epoch": snapshot.gateway_epoch,
            "channel": snapshot.channel,
            "tenant_id": snapshot.tenant_id,
            "link_account_id": snapshot.link_account_id,
            "state": snapshot.state,
            "legacy_owner_component_id": snapshot.legacy_owner_component_id,
            "legacy_owner_instance_id": snapshot.legacy_owner_instance_id,
            "candidate_owner_instance_id": snapshot.candidate_owner_instance_id,
            "drain_evidence_id": snapshot.drain_evidence_id,
            "active_lease_id": snapshot.active_lease_id,
            "revision": snapshot.revision,
            "started_at_ms": snapshot.started_at_ms,
            "updated_at_ms": snapshot.updated_at_ms,
        }
        if any(row[name] != value for name, value in expected.items()):
            raise StoreCorruptionError("channel cutover columns disagree with snapshot")
        cutovers[snapshot.cutover_id] = snapshot

    evidence_by_cutover: dict[str, ChannelDrainEvidence] = {}
    for row in connection.execute(
        "SELECT * FROM channel_drain_evidence ORDER BY evidence_id"
    ).fetchall():
        evidence = _parse_channel_drain(row["evidence_json"], row["payload_sha256"])
        if (
            row["evidence_id"] != evidence.evidence_id
            or row["cutover_id"] != evidence.cutover_id
            or row["gateway_epoch"] != evidence.gateway_epoch
            or row["observed_at_ms"] != evidence.observed_at_ms
            or evidence.cutover_id not in cutovers
            or evidence.cutover_id in evidence_by_cutover
        ):
            raise StoreCorruptionError("channel drain evidence columns are invalid")
        evidence_by_cutover[evidence.cutover_id] = evidence

    leases_by_cutover: dict[str, list[tuple[ChannelOwnershipLease, int]]] = {}
    for row in connection.execute(
        "SELECT * FROM channel_ownership_lease ORDER BY cutover_id, issued_at_ms, lease_id"
    ).fetchall():
        lease = _parse_channel_lease(row["lease_json"], row["payload_sha256"])
        if (
            row["lease_id"] != lease.lease_id
            or row["cutover_id"] != lease.cutover_id
            or row["gateway_epoch"] != lease.gateway_epoch
            or row["owner_instance_id"] != lease.owner_instance_id
            or row["previous_lease_sha256"] != lease.previous_lease_sha256
            or row["issued_at_ms"] != lease.issued_at_ms
            or row["expires_at_ms"] != lease.expires_at_ms
            or row["is_active"] not in {0, 1}
            or lease.cutover_id not in cutovers
        ):
            raise StoreCorruptionError("channel ownership lease columns are invalid")
        leases_by_cutover.setdefault(lease.cutover_id, []).append(
            (lease, row["is_active"])
        )

    for cutover_id, snapshot in cutovers.items():
        evidence = evidence_by_cutover.get(cutover_id)
        lease_rows = leases_by_cutover.get(cutover_id, [])
        if snapshot.state == "DRAINING":
            if evidence is not None or lease_rows or snapshot.updated_at_ms != snapshot.started_at_ms:
                raise StoreCorruptionError("draining channel cutover has premature facts")
            continue
        if (
            evidence is None
            or evidence.evidence_id != snapshot.drain_evidence_id
            or evidence.evidence_sha256 != snapshot.drain_evidence_sha256
            or evidence.gateway_epoch != snapshot.gateway_epoch
            or evidence.channel != snapshot.channel
            or evidence.tenant_id != snapshot.tenant_id
            or evidence.link_account_id != snapshot.link_account_id
            or evidence.legacy_owner_component_id != snapshot.legacy_owner_component_id
            or evidence.legacy_owner_instance_id != snapshot.legacy_owner_instance_id
        ):
            raise StoreCorruptionError("channel cutover is not bound to exact drain evidence")
        if snapshot.state == "DRAINED":
            if lease_rows or snapshot.updated_at_ms != evidence.observed_at_ms:
                raise StoreCorruptionError("drained channel cutover has invalid lease state")
            continue

        previous: ChannelOwnershipLease | None = None
        active: ChannelOwnershipLease | None = None
        for lease, is_active in lease_rows:
            if (
                lease.gateway_epoch != snapshot.gateway_epoch
                or lease.channel != snapshot.channel
                or lease.tenant_id != snapshot.tenant_id
                or lease.link_account_id != snapshot.link_account_id
                or lease.owner_instance_id != snapshot.candidate_owner_instance_id
                or lease.drain_evidence_id != evidence.evidence_id
                or lease.drain_evidence_sha256 != evidence.evidence_sha256
            ):
                raise StoreCorruptionError("channel ownership lease exceeds cutover scope")
            if previous is None:
                if lease.previous_lease_sha256 is not None:
                    raise StoreCorruptionError("initial channel lease has a predecessor")
            elif (
                lease.previous_lease_sha256 != previous.lease_sha256
                or not previous.issued_at_ms < lease.issued_at_ms <= previous.expires_at_ms
            ):
                raise StoreCorruptionError("channel ownership lease chain is discontinuous")
            if is_active:
                if active is not None:
                    raise StoreCorruptionError("channel cutover has multiple active leases")
                active = lease
            previous = lease
        if (
            not lease_rows
            or active is None
            or active != lease_rows[-1][0]
            or active.lease_id != snapshot.active_lease_id
            or active.lease_sha256 != snapshot.active_lease_sha256
            or snapshot.updated_at_ms != active.issued_at_ms
        ):
            raise StoreCorruptionError("active channel cutover lease binding is invalid")

    active_by_scope: dict[
        tuple[str, str, str],
        list[tuple[ChannelCutoverSnapshot, ChannelOwnershipLease]],
    ] = {}
    for cutover_id, lease_rows in leases_by_cutover.items():
        snapshot = cutovers[cutover_id]
        active = next((lease for lease, flag in lease_rows if flag), None)
        if active is not None:
            key = (snapshot.channel, snapshot.tenant_id, snapshot.link_account_id)
            active_by_scope.setdefault(key, []).append((snapshot, active))
    for ownerships in active_by_scope.values():
        ordered = sorted(ownerships, key=lambda item: item[0].gateway_epoch)
        for previous, current in zip(ordered, ordered[1:]):
            if (
                current[0].gateway_epoch <= previous[0].gateway_epoch
                or current[0].started_at_ms
                < previous[1].expires_at_ms + CHANNEL_LEASE_CLOCK_SKEW_MS
            ):
                raise StoreCorruptionError("channel ownership epochs overlap")


def _verify_full_event_chain(connection: sqlite3.Connection) -> None:
    _verify_current_state_rows(connection)
    grouped: dict[tuple[str, str], list[TransitionDecision]] = {}
    for row in connection.execute("SELECT * FROM event_log ORDER BY sequence").fetchall():
        event, decision, _ = _verify_event_row(row)
        if decision.accepted:
            grouped.setdefault((event.machine, event.entity_id), []).append(decision)
    for (machine, entity_id), decisions in grouped.items():
        ordered = sorted(decisions, key=lambda item: item.current.revision)
        for index, decision in enumerate(ordered, start=1):
            if decision.current.revision != index or decision.previous.revision != index - 1:
                raise StoreCorruptionError("aggregate accepted-event revisions are not contiguous")
            if index > 1 and decision.previous != ordered[index - 2].current:
                raise StoreCorruptionError("aggregate accepted-event snapshots do not form a chain")
        row = connection.execute(
            """
            SELECT snapshot_json, snapshot_sha256
            FROM aggregate_state
            WHERE machine = ? AND entity_id = ?
            """,
            (machine, entity_id),
        ).fetchone()
        if row is None:
            raise StoreCorruptionError("applied events reference a missing aggregate")
        current = _parse_snapshot(row["snapshot_json"], row["snapshot_sha256"])
        if current != ordered[-1].current:
            raise StoreCorruptionError("aggregate state does not match its accepted-event chain")


class GatewayStateStore:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, now_ms: int) -> "GatewayStateStore":
        if now_ms < 0 or not path.is_absolute():
            raise ValueError("gateway store path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise StoreCorruptionError("gateway store path is not a regular file")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                path,
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
            _configure_connection(connection)
            _migrate(connection, applied_at_ms=now_ms)
            store = cls(path, connection)
            health = store.health_check(now_ms=now_ms, full=True)
            if not health.healthy:
                raise StoreCorruptionError(health.reason_code)
            try:
                os.chmod(path, 0o600)
            except OSError as exc:
                raise StoreCorruptionError("gateway store permissions cannot be restricted") from exc
            return store
        except (sqlite3.DatabaseError, StoreError, OSError) as exc:
            if "connection" in locals():
                connection.close()
            if isinstance(exc, StoreError):
                raise
            raise StoreCorruptionError("gateway store could not be opened safely") from exc

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        if self._closed:
            raise StoreError("gateway store is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _assert_request_binding_locked(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        recorded_at_ms: int,
    ) -> sqlite3.Row:
        journal = self._connection.execute(
            "SELECT created_at_ms FROM request_journal WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if journal is None:
            raise StoreNotFoundError("request continuity binding does not exist")
        if recorded_at_ms < journal["created_at_ms"]:
            raise ValueError("request continuity fact predates its request")
        current = self._connection.execute(
            "SELECT * FROM request_generation WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if current is None:
            raise StoreNotFoundError("request generation does not exist")
        if current["run_id"] != run_id or current["current_generation"] != generation:
            raise StoreConflictError("request continuity fact crossed a generation fence")
        return current

    def _assert_known_request_binding_locked(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        recorded_at_ms: int,
    ) -> None:
        journal = self._connection.execute(
            "SELECT created_at_ms FROM request_journal WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if journal is None:
            raise StoreNotFoundError("request binding does not exist")
        if recorded_at_ms < journal["created_at_ms"]:
            raise ValueError("request-bound fact predates its request")
        fence = self._connection.execute(
            """
            SELECT 1 FROM generation_fences
            WHERE request_id = ? AND run_id = ? AND generation = ?
            LIMIT 1
            """,
            (request_id, run_id, generation),
        ).fetchone()
        if fence is None:
            raise StoreConflictError("request-bound fact crossed a generation fence")

    def initialize_snapshot(self, snapshot: StateSnapshot) -> bool:
        if snapshot.revision != 0 or snapshot.last_event_id is not None:
            raise ValueError("initial state snapshot must be revision zero without an event")
        payload, digest = _snapshot_payload(snapshot)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                """
                SELECT snapshot_json, snapshot_sha256
                FROM aggregate_state
                WHERE machine = ? AND entity_id = ?
                """,
                (snapshot.machine, snapshot.entity_id),
            ).fetchone()
            if row is not None:
                if row["snapshot_json"] == payload and row["snapshot_sha256"] == digest:
                    return False
                raise StoreConflictError("aggregate identity already belongs to another snapshot")
            self._connection.execute(
                """
                INSERT INTO aggregate_state(
                    machine, entity_id, request_id, run_id, generation, revision,
                    state, created_at_ms, updated_at_ms, last_event_id,
                    snapshot_json, snapshot_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.machine,
                    snapshot.entity_id,
                    snapshot.request_id,
                    snapshot.run_id,
                    snapshot.generation,
                    snapshot.revision,
                    snapshot.state,
                    snapshot.created_at_ms,
                    snapshot.updated_at_ms,
                    snapshot.last_event_id,
                    payload,
                    digest,
                ),
            )
        return True

    def _get_snapshot_row(self, machine: str, entity_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT snapshot_json, snapshot_sha256
            FROM aggregate_state
            WHERE machine = ? AND entity_id = ?
            """,
            (machine, entity_id),
        ).fetchone()

    def get_snapshot(self, machine: str, entity_id: str) -> StateSnapshot | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._get_snapshot_row(machine, entity_id)
            if row is None:
                return None
            return _parse_snapshot(row["snapshot_json"], row["snapshot_sha256"])

    def list_request_snapshots(self, request_id: str) -> tuple[StateSnapshot, ...]:
        if not request_id:
            raise ValueError("request snapshot lookup identity is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT snapshot_json, snapshot_sha256
                FROM aggregate_state
                WHERE request_id = ?
                ORDER BY CASE machine
                    WHEN 'request' THEN 0
                    WHEN 'execution' THEN 1
                    WHEN 'artifact' THEN 2
                    ELSE 3
                END, entity_id
                """,
                (request_id,),
            ).fetchall()
            return tuple(
                _parse_snapshot(row["snapshot_json"], row["snapshot_sha256"])
                for row in rows
            )

    def apply_event(self, event: TransitionEvent, *, recorded_at_ms: int) -> StoreApplyResult:
        return self.apply_event_with_outbox(event, (), recorded_at_ms=recorded_at_ms)

    def apply_event_with_coordination(
        self,
        event: TransitionEvent,
        coordination_events: tuple[CoordinationEvent, ...],
        *,
        recorded_at_ms: int,
    ) -> StoreApplyResult:
        return self.apply_event_with_outbox(
            event,
            (),
            coordination_events=coordination_events,
            recorded_at_ms=recorded_at_ms,
        )

    def apply_event_with_outbox(
        self,
        event: TransitionEvent,
        outbox_intents: tuple[OutboxIntent, ...],
        *,
        coordination_events: tuple[CoordinationEvent, ...] = (),
        recorded_at_ms: int,
    ) -> StoreApplyResult:
        if recorded_at_ms < event.occurred_at_ms:
            raise ValueError("event record time predates event occurrence")
        if not event.has_valid_event_sha256():
            raise ValueError("event digest is invalid")
        ordered_intents = tuple(sorted(outbox_intents, key=lambda item: item.outbox_id))
        if len({item.outbox_id for item in ordered_intents}) != len(ordered_intents):
            raise ValueError("outbox intents contain a duplicate identity")
        for intent in ordered_intents:
            if not intent.has_valid_sha256():
                raise ValueError("outbox intent digest is invalid")
            if (
                intent.request_id != event.request_id
                or intent.run_id != event.run_id
                or intent.generation != event.generation
                or intent.created_at_ms < event.occurred_at_ms
            ):
                raise ValueError("outbox intent is not bound to its state event")
        ordered_coordination = tuple(sorted(coordination_events, key=lambda item: item.event_id))
        if len({item.event_id for item in ordered_coordination}) != len(ordered_coordination):
            raise ValueError("coordination events contain a duplicate identity")
        for item in ordered_coordination:
            if not item.has_valid_sha256():
                raise ValueError("coordination event digest is invalid")
            if (
                item.request_id != event.request_id
                or item.run_id != event.run_id
                or item.generation != event.generation
                or item.created_at_ms < event.occurred_at_ms
            ):
                raise ValueError("coordination event is not bound to its state event")
        event_json = _event_payload(event)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                """
                SELECT event_json, event_sha256, decision_json
                FROM event_log
                WHERE event_id = ?
                """,
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["event_json"] != event_json or existing["event_sha256"] != event.event_sha256:
                    raise StoreConflictError("event ID was reused with different content")
                try:
                    decision = TransitionDecision.model_validate_json(
                        existing["decision_json"],
                        strict=True,
                    )
                except ValueError as exc:
                    raise StoreCorruptionError("stored transition decision is invalid") from exc
                if _decision_payload(decision) != existing["decision_json"]:
                    raise StoreCorruptionError("stored transition decision is not canonical")
                linked = self._connection.execute(
                    """
                    SELECT o.outbox_id, o.intent_json, o.intent_sha256
                    FROM event_outbox eo JOIN outbox o ON o.outbox_id = eo.outbox_id
                    WHERE eo.event_id = ? ORDER BY o.outbox_id
                    """,
                    (event.event_id,),
                ).fetchall()
                caller_includes_life = any(
                    item.intent_kind == "LIFE_EVENT" for item in ordered_intents
                )
                comparable_linked = (
                    linked
                    if caller_includes_life
                    else [
                        row
                        for row in linked
                        if _parse_outbox_intent(
                            row["intent_json"], row["intent_sha256"]
                        ).intent_kind
                        != "LIFE_EVENT"
                    ]
                )
                expected_outbox_ids = (
                    [item.outbox_id for item in ordered_intents]
                    if decision.accepted
                    else []
                )
                if [row["outbox_id"] for row in comparable_linked] != expected_outbox_ids:
                    raise StoreConflictError("duplicate event changed its transactional outbox set")
                for row, intent in zip(
                    comparable_linked,
                    ordered_intents if decision.accepted else (),
                    strict=True,
                ):
                    payload, digest = _outbox_payload(intent)
                    if row["intent_json"] != payload or row["intent_sha256"] != digest:
                        raise StoreConflictError("duplicate event changed an outbox intent")
                linked_coordination = self._connection.execute(
                    """
                    SELECT * FROM coordination_events
                    WHERE state_event_id = ? ORDER BY event_id
                    """,
                    (event.event_id,),
                ).fetchall()
                expected_coordination = ordered_coordination if decision.accepted else ()
                if [row["event_id"] for row in linked_coordination] != [
                    item.event_id for item in expected_coordination
                ]:
                    raise StoreConflictError("duplicate event changed its coordination event set")
                for row, item in zip(linked_coordination, expected_coordination, strict=True):
                    if _coordination_record_from_row(row).event != item:
                        raise StoreConflictError("duplicate state event changed a coordination intent")
                return StoreApplyResult(decision, False, True)

            row = self._get_snapshot_row(event.machine, event.entity_id)
            if row is None:
                raise StoreNotFoundError("event aggregate does not exist")
            previous = _parse_snapshot(row["snapshot_json"], row["snapshot_sha256"])
            decision = apply_transition(previous, event)
            if decision.accepted and ordered_coordination:
                if decision.current.machine != "request":
                    raise ValueError("coordination events must be attached to a request transition")
                for item in ordered_coordination:
                    expected_state = "PLANNING" if item.kind == "NEED_SKILL" else "WAITING_CONFIRMATION"
                    if decision.current.state != expected_state:
                        raise ValueError("coordination event does not match the resulting request state")
            current_json, current_sha256 = _snapshot_payload(decision.current)
            self._connection.execute(
                """
                INSERT INTO event_log(
                    event_id, machine, entity_id, request_id, run_id, generation,
                    expected_revision, resulting_revision, event_type,
                    source_component_id, occurred_at_ms, recorded_at_ms,
                    accepted, disposition, reason_code, event_json, event_sha256,
                    decision_json, result_snapshot_json, result_snapshot_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.machine,
                    event.entity_id,
                    event.request_id,
                    event.run_id,
                    event.generation,
                    event.expected_revision,
                    decision.current.revision,
                    event.event_type,
                    event.source_component_id,
                    event.occurred_at_ms,
                    recorded_at_ms,
                    int(decision.accepted),
                    decision.disposition,
                    decision.reason_code,
                    event_json,
                    event.event_sha256,
                    _decision_payload(decision),
                    current_json,
                    current_sha256,
                ),
            )
            if decision.accepted:
                updated = self._connection.execute(
                    """
                    UPDATE aggregate_state
                    SET request_id = ?, run_id = ?, generation = ?, revision = ?,
                        state = ?, created_at_ms = ?, updated_at_ms = ?,
                        last_event_id = ?, snapshot_json = ?, snapshot_sha256 = ?
                    WHERE machine = ? AND entity_id = ? AND revision = ?
                    """,
                    (
                        decision.current.request_id,
                        decision.current.run_id,
                        decision.current.generation,
                        decision.current.revision,
                        decision.current.state,
                        decision.current.created_at_ms,
                        decision.current.updated_at_ms,
                        decision.current.last_event_id,
                        current_json,
                        current_sha256,
                        event.machine,
                        event.entity_id,
                        event.expected_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("aggregate revision changed before CAS update")
                for intent in ordered_intents:
                    payload, digest = _outbox_payload(intent)
                    conflict = self._connection.execute(
                        "SELECT intent_json, intent_sha256 FROM outbox WHERE outbox_id = ? OR effect_id = ?",
                        (intent.outbox_id, intent.effect_id),
                    ).fetchone()
                    if conflict is not None:
                        raise StoreConflictError("outbox effect identity is already bound")
                    self._connection.execute(
                        """
                        INSERT INTO outbox(
                            outbox_id, effect_id, request_id, run_id, generation,
                            destination_component_id, intent_kind, payload_object_id,
                            payload_sha256, state, attempt_count, available_at_ms,
                            claimed_by, claim_expires_at_ms, dispatched_at_ms,
                            result_sha256, intent_json, intent_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, NULL, NULL, NULL, NULL, ?, ?)
                        """,
                        (
                            intent.outbox_id,
                            intent.effect_id,
                            intent.request_id,
                            intent.run_id,
                            intent.generation,
                            intent.destination_component_id,
                            intent.intent_kind,
                            intent.payload_object_id,
                            intent.payload_sha256,
                            intent.created_at_ms,
                            payload,
                            digest,
                        ),
                    )
                    self._connection.execute(
                        "INSERT INTO event_outbox(event_id, outbox_id) VALUES (?, ?)",
                        (event.event_id, intent.outbox_id),
                    )
                for coordination_event in ordered_coordination:
                    _insert_coordination_row(
                        self._connection,
                        coordination_event,
                        state_event_id=event.event_id,
                    )
        return StoreApplyResult(decision, True, False)

    def attach_life_event_outbox(
        self,
        state_event_id: str,
        intent: OutboxIntent,
    ) -> tuple[OutboxRecord, bool]:
        """Durably attach a missing life publication to an accepted state fact."""
        if (
            not state_event_id
            or intent.intent_kind != "LIFE_EVENT"
            or intent.destination_component_id != "tiangong-life-service"
            or not intent.has_valid_sha256()
        ):
            raise ValueError("life event outbox intent is invalid")
        payload, digest = _outbox_payload(intent)
        with self._lock, self._write_transaction():
            event_row = self._connection.execute(
                "SELECT * FROM event_log WHERE event_id = ?",
                (state_event_id,),
            ).fetchone()
            if event_row is None:
                raise StoreNotFoundError("life outbox state event does not exist")
            event, decision, _ = _verify_event_row(event_row)
            if not decision.accepted:
                raise StoreConflictError("rejected state event cannot publish a life fact")
            if (
                intent.request_id != event.request_id
                or intent.run_id != event.run_id
                or intent.generation != event.generation
                or intent.created_at_ms < event.occurred_at_ms
            ):
                raise ValueError("life outbox intent is not bound to its state event")
            linked = self._connection.execute(
                """
                SELECT o.* FROM event_outbox AS eo
                JOIN outbox AS o ON o.outbox_id = eo.outbox_id
                WHERE eo.event_id = ? AND o.intent_kind = 'LIFE_EVENT'
                ORDER BY o.outbox_id
                """,
                (state_event_id,),
            ).fetchall()
            if linked:
                if len(linked) != 1:
                    raise StoreCorruptionError("state event has multiple life publications")
                existing = _outbox_record_from_row(linked[0])
                if existing.intent != intent:
                    raise StoreConflictError("state event life publication changed")
                _ensure_outbox_object_owner_locked(self._connection, intent)
                return existing, False
            conflict = self._connection.execute(
                "SELECT * FROM outbox WHERE outbox_id = ? OR effect_id = ?",
                (intent.outbox_id, intent.effect_id),
            ).fetchone()
            if conflict is not None:
                raise StoreConflictError("life outbox identity is already bound")
            self._connection.execute(
                """
                INSERT INTO outbox(
                    outbox_id, effect_id, request_id, run_id, generation,
                    destination_component_id, intent_kind, payload_object_id,
                    payload_sha256, state, attempt_count, available_at_ms,
                    claimed_by, claim_expires_at_ms, dispatched_at_ms,
                    result_sha256, intent_json, intent_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, 'LIFE_EVENT', ?, ?, 'PENDING', 0, ?,
                          NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    intent.outbox_id,
                    intent.effect_id,
                    intent.request_id,
                    intent.run_id,
                    intent.generation,
                    intent.destination_component_id,
                    intent.payload_object_id,
                    intent.payload_sha256,
                    intent.created_at_ms,
                    payload,
                    digest,
                ),
            )
            self._connection.execute(
                "INSERT INTO event_outbox(event_id, outbox_id) VALUES (?, ?)",
                (state_event_id, intent.outbox_id),
            )
            _ensure_outbox_object_owner_locked(self._connection, intent)
            row = self._connection.execute(
                "SELECT * FROM outbox WHERE outbox_id = ?",
                (intent.outbox_id,),
            ).fetchone()
            return _outbox_record_from_row(row), True

    def list_state_events_missing_life_outbox(
        self,
        *,
        limit: int = 100,
    ) -> tuple[TransitionEvent, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("life outbox recovery limit is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT e.* FROM event_log AS e
                WHERE e.accepted = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM event_outbox AS eo
                      JOIN outbox AS o ON o.outbox_id = eo.outbox_id
                      WHERE eo.event_id = e.event_id AND o.intent_kind = 'LIFE_EVENT'
                  )
                ORDER BY e.sequence
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(_verify_event_row(row)[0] for row in rows)

    def get_state_event_sequence(self, event_id: str) -> int | None:
        if not event_id:
            raise ValueError("state event identity is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT sequence FROM event_log WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return None if row is None else int(row[0])

    def get_life_source_sequence(self, event_id: str) -> int | None:
        """Return the stable, contiguous ordinal of an accepted gateway fact."""
        if not event_id:
            raise ValueError("state event identity is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT sequence, accepted FROM event_log WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            if int(row["accepted"]) != 1:
                raise StoreConflictError("rejected state event has no life source ordinal")
            return int(
                self._connection.execute(
                    "SELECT count(*) FROM event_log WHERE accepted = 1 AND sequence <= ?",
                    (row["sequence"],),
                ).fetchone()[0]
            )

    def get_outbox(self, outbox_id: str) -> OutboxRecord | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute("SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            return None if row is None else _outbox_record_from_row(row)

    def list_dispatchable_outbox(self, *, now_ms: int, limit: int = 100) -> tuple[OutboxRecord, ...]:
        if now_ms < 0 or not 1 <= limit <= 1_000:
            raise ValueError("outbox dispatch query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM outbox
                WHERE (state = 'PENDING' AND available_at_ms <= ?)
                   OR (state = 'CLAIMED' AND claim_expires_at_ms <= ?)
                ORDER BY available_at_ms, outbox_id LIMIT ?
                """,
                (now_ms, now_ms, limit),
            ).fetchall()
            return tuple(_outbox_record_from_row(row) for row in rows)

    def list_unfinished_life_outbox(
        self,
        *,
        limit: int = 1_000,
    ) -> tuple[OutboxRecord, ...]:
        if not 1 <= limit <= 10_000:
            raise ValueError("life outbox query limit is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM outbox
                WHERE intent_kind = 'LIFE_EVENT' AND state != 'ACKED'
                ORDER BY available_at_ms, outbox_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(_outbox_record_from_row(row) for row in rows)

    def list_outbox_for_request(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[OutboxRecord, ...]:
        if not request_id or not run_id or generation < 0:
            raise ValueError("request outbox query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM outbox
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY available_at_ms, outbox_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(_outbox_record_from_row(row) for row in rows)

    def claim_outbox(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> OutboxRecord:
        if not worker_id or len(worker_id) > 160 or now_ms < 0 or not 1_000 <= lease_ms <= 300_000:
            raise ValueError("outbox claim arguments are invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute("SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            if row is None:
                raise StoreNotFoundError("outbox intent does not exist")
            if row["state"] in {"ACKED", "AMBIGUOUS"}:
                return _outbox_record_from_row(row)
            claimable = (
                (row["state"] == "PENDING" and row["available_at_ms"] <= now_ms)
                or (row["state"] == "CLAIMED" and row["claim_expires_at_ms"] <= now_ms)
            )
            if not claimable:
                raise StoreConflictError("outbox intent is not currently claimable")
            expires = now_ms + lease_ms
            updated = self._connection.execute(
                """
                UPDATE outbox
                SET state = 'CLAIMED', attempt_count = attempt_count + 1,
                    claimed_by = ?, claim_expires_at_ms = ?
                WHERE outbox_id = ? AND state = ? AND attempt_count = ?
                """,
                (worker_id, expires, outbox_id, row["state"], row["attempt_count"]),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("outbox changed before claim")
            claimed = self._connection.execute("SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            return _outbox_record_from_row(claimed)

    def get_outbox_dispatch_boundary(
        self,
        outbox_id: str,
    ) -> OutboxDispatchBoundary | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            return None if row is None else _outbox_boundary_from_row(row)

    def mark_outbox_dispatch_started(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        gateway_epoch: int,
        ticket_object_id: str,
        ticket_sha256: str,
        started_at_ms: int,
    ) -> OutboxDispatchBoundary:
        if (
            not 1 <= len(worker_id) <= 160
            or gateway_epoch < 1
            or not 4 <= len(ticket_object_id) <= 160
            or len(ticket_sha256) != 64
            or any(char not in "0123456789abcdef" for char in ticket_sha256)
            or started_at_ms < 0
        ):
            raise ValueError("outbox dispatch boundary arguments are invalid")
        with self._lock, self._write_transaction():
            outbox = self._connection.execute(
                "SELECT * FROM outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if outbox is None:
                raise StoreNotFoundError("outbox intent does not exist")
            intent = _parse_outbox_intent(outbox["intent_json"], outbox["intent_sha256"])
            if (
                outbox["state"] != "CLAIMED"
                or outbox["claimed_by"] != worker_id
                or started_at_ms < intent.created_at_ms
            ):
                raise StoreConflictError("outbox dispatch boundary is not owned by this worker")
            boundary_digest = _outbox_boundary_sha256(
                outbox_id=outbox_id,
                effect_id=intent.effect_id,
                worker_id=worker_id,
                gateway_epoch=gateway_epoch,
                ticket_object_id=ticket_object_id,
                ticket_sha256=ticket_sha256,
                started_at_ms=started_at_ms,
            )
            existing = self._connection.execute(
                "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ? OR effect_id = ?",
                (outbox_id, intent.effect_id),
            ).fetchone()
            if existing is not None:
                boundary = _outbox_boundary_from_row(existing)
                if boundary.boundary_sha256 != boundary_digest:
                    raise StoreConflictError("outbox dispatch boundary already crossed differently")
                return boundary
            self._connection.execute(
                """
                INSERT INTO outbox_dispatch_boundary(
                    outbox_id, effect_id, worker_id, gateway_epoch,
                    ticket_object_id, ticket_sha256, started_at_ms, boundary_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outbox_id,
                    intent.effect_id,
                    worker_id,
                    gateway_epoch,
                    ticket_object_id,
                    ticket_sha256,
                    started_at_ms,
                    boundary_digest,
                ),
            )
            return _outbox_boundary_from_row(
                self._connection.execute(
                    "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )

    def mark_expired_outbox_ambiguous(
        self,
        outbox_id: str,
        *,
        observed_at_ms: int,
        result_object_id: str,
        result_sha256: str,
    ) -> OutboxRecord:
        if (
            observed_at_ms < 0
            or not 4 <= len(result_object_id) <= 160
            or len(result_sha256) != 64
            or any(char not in "0123456789abcdef" for char in result_sha256)
        ):
            raise ValueError("expired outbox ambiguity fact is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("outbox intent does not exist")
            if row["state"] in {"ACKED", "AMBIGUOUS"}:
                if row["state"] != "AMBIGUOUS" or row["result_sha256"] != result_sha256:
                    raise StoreConflictError("outbox ambiguity fact changed")
                return _outbox_record_from_row(row)
            boundary_row = self._connection.execute(
                "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if boundary_row is None:
                raise StoreConflictError("outbox has not crossed the dispatch boundary")
            boundary = _outbox_boundary_from_row(boundary_row)
            if (
                row["state"] != "CLAIMED"
                or row["claim_expires_at_ms"] is None
                or row["claim_expires_at_ms"] > observed_at_ms
                or observed_at_ms < boundary.started_at_ms
            ):
                raise StoreConflictError("outbox dispatch boundary is not orphaned")
            updated = self._connection.execute(
                """
                UPDATE outbox
                SET state = 'AMBIGUOUS', dispatched_at_ms = ?, result_sha256 = ?
                WHERE outbox_id = ? AND state = 'CLAIMED'
                """,
                (observed_at_ms, result_sha256, outbox_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("outbox changed before ambiguity recovery")
            boundary_update = self._connection.execute(
                """
                UPDATE outbox_dispatch_boundary
                SET result_object_id = ?, result_sha256 = ?, completed_at_ms = ?
                WHERE outbox_id = ? AND result_object_id IS NULL
                """,
                (result_object_id, result_sha256, observed_at_ms, outbox_id),
            )
            if boundary_update.rowcount != 1:
                raise StoreCasConflict("outbox boundary changed before ambiguity recovery")
            return _outbox_record_from_row(
                self._connection.execute(
                    "SELECT * FROM outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )

    def record_outbox_dispatch_result(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        outcome: Literal["ACKED", "AMBIGUOUS"],
        result_object_id: str,
        result_sha256: str,
        completed_at_ms: int,
    ) -> OutboxRecord:
        if (
            not 1 <= len(worker_id) <= 160
            or not 4 <= len(result_object_id) <= 160
            or len(result_sha256) != 64
            or any(char not in "0123456789abcdef" for char in result_sha256)
            or completed_at_ms < 0
        ):
            raise ValueError("outbox dispatch result arguments are invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            boundary_row = self._connection.execute(
                "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None or boundary_row is None:
                raise StoreNotFoundError("outbox dispatch result has no boundary")
            boundary = _outbox_boundary_from_row(boundary_row)
            if row["state"] in {"ACKED", "AMBIGUOUS"}:
                if (
                    row["state"] != outcome
                    or row["result_sha256"] != result_sha256
                    or boundary.result_object_id != result_object_id
                    or boundary.completed_at_ms != completed_at_ms
                ):
                    raise StoreConflictError("outbox dispatch result changed")
                return _outbox_record_from_row(row)
            if (
                row["state"] != "CLAIMED"
                or row["claimed_by"] != worker_id
                or boundary.worker_id != worker_id
                or boundary.result_object_id is not None
                or completed_at_ms < boundary.started_at_ms
            ):
                raise StoreConflictError("outbox dispatch result is not owned by this boundary")
            outbox_update = self._connection.execute(
                """
                UPDATE outbox SET state = ?, dispatched_at_ms = ?, result_sha256 = ?
                WHERE outbox_id = ? AND state = 'CLAIMED' AND claimed_by = ?
                """,
                (outcome, completed_at_ms, result_sha256, outbox_id, worker_id),
            )
            boundary_update = self._connection.execute(
                """
                UPDATE outbox_dispatch_boundary
                SET result_object_id = ?, result_sha256 = ?, completed_at_ms = ?
                WHERE outbox_id = ? AND result_object_id IS NULL
                """,
                (result_object_id, result_sha256, completed_at_ms, outbox_id),
            )
            if outbox_update.rowcount != 1 or boundary_update.rowcount != 1:
                raise StoreCasConflict("outbox changed before dispatch result commit")
            return _outbox_record_from_row(
                self._connection.execute(
                    "SELECT * FROM outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )

    def list_unfinalized_outbox_results(
        self,
        *,
        limit: int = 100,
    ) -> tuple[OutboxDispatchBoundary, ...]:
        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("unfinalized outbox result limit is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT b.*
                FROM outbox_dispatch_boundary AS b
                JOIN outbox AS o ON o.outbox_id = b.outbox_id
                WHERE o.state IN ('ACKED','AMBIGUOUS')
                  AND b.result_object_id IS NOT NULL
                  AND b.finalized_at_ms IS NULL
                ORDER BY b.completed_at_ms, b.outbox_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(_outbox_boundary_from_row(row) for row in rows)

    def mark_outbox_finalized(
        self,
        outbox_id: str,
        *,
        finalized_at_ms: int,
        finalization_sha256: str,
        release_generation: bool = False,
    ) -> OutboxDispatchBoundary:
        if (
            finalized_at_ms < 0
            or len(finalization_sha256) != 64
            or any(char not in "0123456789abcdef" for char in finalization_sha256)
            or type(release_generation) is not bool
        ):
            raise ValueError("outbox finalization fact is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("outbox dispatch boundary does not exist")
            boundary = _outbox_boundary_from_row(row)
            if boundary.finalized_at_ms is not None:
                if (
                    boundary.finalized_at_ms != finalized_at_ms
                    or boundary.finalization_sha256 != finalization_sha256
                ):
                    raise StoreConflictError("outbox finalization fact changed")
            else:
                if boundary.completed_at_ms is None or finalized_at_ms < boundary.completed_at_ms:
                    raise StoreConflictError("outbox cannot finalize before its dispatch result")
                updated = self._connection.execute(
                    """
                    UPDATE outbox_dispatch_boundary
                    SET finalized_at_ms = ?, finalization_sha256 = ?
                    WHERE outbox_id = ? AND finalized_at_ms IS NULL
                    """,
                    (finalized_at_ms, finalization_sha256, outbox_id),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("outbox changed before finalization")
                boundary = _outbox_boundary_from_row(
                    self._connection.execute(
                        "SELECT * FROM outbox_dispatch_boundary WHERE outbox_id = ?",
                        (outbox_id,),
                    ).fetchone()
                )
            if release_generation:
                outbox = self._connection.execute(
                    "SELECT request_id, run_id, generation FROM outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                if outbox is None:
                    raise StoreCorruptionError("finalized outbox intent is missing")
                self._release_generation_locked(
                    outbox["request_id"],
                    released_at_ms=finalized_at_ms,
                    expected_run_id=outbox["run_id"],
                    expected_generation=outbox["generation"],
                )
            return boundary

    def record_outbox_result(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        outcome: Literal["ACKED", "AMBIGUOUS"],
        result_sha256: str,
        dispatched_at_ms: int,
    ) -> OutboxRecord:
        if len(result_sha256) != 64 or any(char not in "0123456789abcdef" for char in result_sha256):
            raise ValueError("outbox result digest is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute("SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            if row is None:
                raise StoreNotFoundError("outbox intent does not exist")
            if row["state"] in {"ACKED", "AMBIGUOUS"}:
                if row["state"] != outcome or row["result_sha256"] != result_sha256:
                    raise StoreConflictError("outbox result identity was reused with different facts")
                return _outbox_record_from_row(row)
            if row["state"] != "CLAIMED" or row["claimed_by"] != worker_id:
                raise StoreConflictError("outbox result is not owned by this worker")
            intent = _parse_outbox_intent(row["intent_json"], row["intent_sha256"])
            if dispatched_at_ms < intent.created_at_ms:
                raise ValueError("outbox dispatch predates intent creation")
            updated = self._connection.execute(
                """
                UPDATE outbox SET state = ?, dispatched_at_ms = ?, result_sha256 = ?
                WHERE outbox_id = ? AND state = 'CLAIMED' AND claimed_by = ?
                """,
                (outcome, dispatched_at_ms, result_sha256, outbox_id, worker_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("outbox changed before result commit")
            completed = self._connection.execute("SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            return _outbox_record_from_row(completed)

    def claim_effect(self, claim: EffectClaim) -> tuple[EffectLedgerRecord, bool]:
        diagnostic_log(f"[EFFECT-CLAIM] effect_id={claim.effect_id} request_id={claim.request_id}")
        if not claim.has_valid_sha256():
            raise ValueError("effect claim digest is invalid")
        claim_json, claim_digest = _effect_claim_payload(claim)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM effect_ledger WHERE effect_id = ?",
                (claim.effect_id,),
            ).fetchone()
            if row is not None:
                record = _effect_record_from_row(row)
                stable_excludes = {"claimed_at_ms", "claim_sha256"}
                if record.claim.model_dump(exclude=stable_excludes) != claim.model_dump(exclude=stable_excludes):
                    raise StoreConflictError("effect identity was reused with different intent")
                return record, False
            self._connection.execute(
                """
                INSERT INTO effect_ledger(
                    effect_id, request_id, run_id, generation, effect_kind,
                    owner_component_id, state, claimed_at_ms,
                    side_effect_started_at_ms, completed_at_ms,
                    claim_json, claim_sha256, result_json, result_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, 'CLAIMED', ?, NULL, NULL, ?, ?, NULL, NULL)
                """,
                (
                    claim.effect_id,
                    claim.request_id,
                    claim.run_id,
                    claim.generation,
                    claim.effect_kind,
                    claim.owner_component_id,
                    claim.claimed_at_ms,
                    claim_json,
                    claim_digest,
                ),
            )
            row = self._connection.execute(
                'SELECT * FROM effect_ledger WHERE effect_id = ?', (claim.effect_id,)
            ).fetchone()
            diagnostic_log(f"[EFFECT-INSERTED] effect_id={claim.effect_id} row={'yes' if row else 'no'}")
            # V14：attempt 1 + CLAIM fact（append-only 链锚点）
            self._connection.execute(
                """
                INSERT INTO effect_attempts(
                    effect_id, attempt, claim_revision, lease_epoch, pipeline_version,
                    state, ticket_id, ticket_sha256, grant_sha256, nonce_sha256,
                    claimed_at_ms, side_effect_started_at_ms, terminal_at_ms, terminal_kind
                ) VALUES (?, 1, 1, NULL, NULL, 'CLAIMED', NULL, NULL, NULL, NULL, ?, NULL, NULL, NULL)
                """,
                (claim.effect_id, claim.claimed_at_ms),
            )
            claim_payload = json.loads(claim_json)
            claim_payload["action_fence_epoch"] = int(self._action_fence_row_locked()["action_fence_epoch"])
            self._append_effect_fact_locked(
                effect_id=claim.effect_id, attempt=1, fact_kind="CLAIM", verdict=None,
                payload=claim_payload, created_at_ms=claim.claimed_at_ms,
            )
            return _effect_record_from_row(row), True

    def get_effect(self, effect_id: str) -> EffectLedgerRecord | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM effect_ledger WHERE effect_id = ?", (effect_id,)
            ).fetchone()
            return None if row is None else _effect_record_from_row(row)

    def get_effect_head_state(self, effect_id: str) -> str | None:
        """effect head 投影的当前状态（CompletionGate 一致性校验用，草案不变量 11）。"""
        with self._lock:
            row = self._connection.execute(
                "SELECT state FROM effect_ledger WHERE effect_id = ?", (effect_id,)
            ).fetchone()
            return None if row is None else str(row["state"])

    def list_stale_non_terminal_effect_ids(self, *, stale_before_ms: int) -> tuple[str, ...]:
        """Effects stuck in CLAIMED/SIDE_EFFECT_STARTED beyond the watchdog window."""
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT effect_id FROM effect_ledger
                WHERE state IN ('CLAIMED', 'SIDE_EFFECT_STARTED')
                  AND COALESCE(side_effect_started_at_ms, claimed_at_ms) < ?
                ORDER BY claimed_at_ms
                """,
                (stale_before_ms,),
            ).fetchall()
            return tuple(str(row["effect_id"]) for row in rows)

    def list_effects_for_request(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[EffectLedgerRecord, ...]:
        if not request_id or not run_id or generation < 0:
            raise ValueError("effect request query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM effect_ledger
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY claimed_at_ms, effect_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(_effect_record_from_row(row) for row in rows)

    def list_started_execution_effects_for_request(
        self,
        request_id: str,
    ) -> tuple[EffectLedgerRecord, ...]:
        """Return execution effects that are currently across the side-effect boundary.

        This deliberately narrow query is used by the frozen 7174 life bridge.  It
        does not accept a caller-supplied run or generation because the legacy
        compiler only carries the authoritative gateway request id.
        """

        if not request_id:
            raise ValueError("effect request query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM effect_ledger
                WHERE request_id = ?
                  AND effect_kind = 'execution'
                  AND state = 'SIDE_EFFECT_STARTED'
                ORDER BY claimed_at_ms, effect_id
                """,
                (request_id,),
            ).fetchall()
            return tuple(_effect_record_from_row(row) for row in rows)

    def mark_effect_started(self, effect_id: str, *, started_at_ms: int) -> EffectLedgerRecord:
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM effect_ledger WHERE effect_id = ?", (effect_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("effect is not claimed")
            record = _effect_record_from_row(row)
            if record.state == "SIDE_EFFECT_STARTED":
                if record.side_effect_started_at_ms != started_at_ms:
                    raise StoreConflictError("effect start fact changed")
                return record
            if record.state != "CLAIMED" or started_at_ms < record.claim.claimed_at_ms:
                raise StoreConflictError("effect cannot cross the side-effect boundary")
            # V14 草案 §3.1：dispatch 与全局 action_fence_epoch 同一 store 行。
            # claim 时锚定的 fence epoch 若已推进（stop 先提交），旧票据永不复活 → handler=0。
            claim_fact = self._connection.execute(
                """
                SELECT payload_json FROM effect_facts
                WHERE effect_id = ? AND attempt = 1 AND fact_kind = 'CLAIM'
                ORDER BY seq DESC LIMIT 1
                """,
                (effect_id,),
            ).fetchone()
            anchor_epoch = 0
            if claim_fact is not None:
                try:
                    anchor_epoch = int(json.loads(claim_fact["payload_json"]).get("action_fence_epoch") or 0)
                except Exception:
                    anchor_epoch = 0
            current_epoch = int(self._action_fence_row_locked()["action_fence_epoch"])
            if current_epoch != anchor_epoch:
                raise StoreConflictError(
                    f"action fence epoch advanced since claim: {anchor_epoch} -> {current_epoch}"
                )
            updated = self._connection.execute(
                """
                UPDATE effect_ledger
                SET state = 'SIDE_EFFECT_STARTED', side_effect_started_at_ms = ?
                WHERE effect_id = ? AND state = 'CLAIMED'
                """,
                (started_at_ms, effect_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("effect changed before side-effect start")
            # V14：attempt 状态 + STARTED fact
            self._connection.execute(
                """
                UPDATE effect_attempts SET state = 'SIDE_EFFECT_STARTED', side_effect_started_at_ms = ?
                WHERE effect_id = ? AND attempt = 1 AND state = 'CLAIMED'
                """,
                (started_at_ms, effect_id),
            )
            self._append_effect_fact_locked(
                effect_id=effect_id, attempt=1, fact_kind="STARTED", verdict=None,
                payload={
                    "domain": "tiangong.gateway.side-effect-started.v1",
                    "side_effect_started_at_ms": started_at_ms,
                },
                created_at_ms=started_at_ms,
            )
            return _effect_record_from_row(
                self._connection.execute(
                    "SELECT * FROM effect_ledger WHERE effect_id = ?", (effect_id,)
                ).fetchone()
            )

    def complete_effect(self, result: EffectResult) -> EffectLedgerRecord:
        if not result.has_valid_sha256():
            raise ValueError("effect result digest is invalid")
        result_json, result_digest = _effect_model_payload(result)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM effect_ledger WHERE effect_id = ?", (result.effect_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("effect result has no claim")
            record = _effect_record_from_row(row)
            if record.result is not None:
                if record.result != result:
                    raise StoreConflictError("effect already has a different first result")
                return record
            if result.observed_at_ms < (record.side_effect_started_at_ms or record.claim.claimed_at_ms):
                raise ValueError("effect result predates its durable boundary")
            if result.status in {"SUCCEEDED", "AMBIGUOUS", "RECONCILED"} and record.state != "SIDE_EFFECT_STARTED":
                raise StoreConflictError("effect success or ambiguity requires side-effect start")
            if record.state not in {"CLAIMED", "SIDE_EFFECT_STARTED"}:
                raise StoreConflictError("effect is not awaiting a result")
            updated = self._connection.execute(
                """
                UPDATE effect_ledger
                SET state = ?, completed_at_ms = ?, result_json = ?, result_sha256 = ?
                WHERE effect_id = ? AND result_json IS NULL
                """,
                (result.status, result.observed_at_ms, result_json, result_digest, result.effect_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("effect changed before result commit")
            # V14：attempt 终态 + RECEIPT fact + inflight 归还
            attempt_terminal_state = {
                "SUCCEEDED": "SUCCEEDED",
                "FAILED_FINAL": "FAILED_FINAL",
                "AMBIGUOUS": "AMBIGUOUS",
                "RECONCILED": "RECONCILED",
            }[result.status]
            self._connection.execute(
                """
                UPDATE effect_attempts SET state = ?, terminal_at_ms = ?, terminal_kind = ?
                WHERE effect_id = ? AND attempt = 1 AND terminal_at_ms IS NULL
                """,
                (attempt_terminal_state, result.observed_at_ms, attempt_terminal_state, result.effect_id),
            )
            self._append_effect_fact_locked(
                effect_id=result.effect_id, attempt=1, fact_kind="RECEIPT", verdict=None,
                payload=json.loads(result_json), created_at_ms=result.observed_at_ms,
            )
            self._connection.execute(
                """
                UPDATE action_fence
                SET inflight_count = MAX(inflight_count - 1, 0),
                    draining = CASE WHEN inflight_count - 1 <= 0 THEN 0 ELSE draining END,
                    updated_at_ms = ?
                WHERE fence_id = 1 AND inflight_count > 0
                """,
                (result.observed_at_ms,),
            )
            return _effect_record_from_row(
                self._connection.execute(
                    "SELECT * FROM effect_ledger WHERE effect_id = ?", (result.effect_id,)
                ).fetchone()
            )

    def recover_started_effects(self, *, now_ms: int) -> tuple[EffectLedgerRecord, ...]:
        # V14：先把 attempt 层 STARTED 标记为 RECONCILE_REQUIRED + INCONCLUSIVE
        self.recover_started_attempts(now_ms=now_ms)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM effect_ledger WHERE state = 'SIDE_EFFECT_STARTED' ORDER BY effect_id"
            ).fetchall()
        recovered = []
        for row in rows:
            claim = _effect_record_from_row(row).claim
            evidence = canonical_sha256(
                {
                    "domain": "tiangong.gateway.effect-recovery.v1",
                    "effect_id": claim.effect_id,
                    "reason": "result_missing_after_restart",
                }
            )
            result = EffectResult(
                result_id="effect_result_ambiguous_" + claim.effect_id[4:20],
                effect_id=claim.effect_id,
                status="AMBIGUOUS",
                fact_id="fact_effect_recovery_" + claim.effect_id[4:20],
                evidence_sha256=evidence,
                error_code="effect.result_missing_after_restart",
                observed_at_ms=now_ms,
                result_sha256="0" * 64,
            ).with_computed_sha256()
            recovered.append(self.complete_effect(result))
        return tuple(recovered)

    # ------------------------------------------------------------------
    # effect fact chain（V14，草案 §3：gateway.sqlite3 内 attempt/fact 子表 +
    # head 投影 + 全局 action_fence 单行）
    # ------------------------------------------------------------------

    def _append_effect_fact_locked(
        self,
        *,
        effect_id: str,
        attempt: int,
        fact_kind: str,
        verdict: str | None,
        payload: dict,
        created_at_ms: int,
    ) -> dict:
        """在写事务内向 effect 的 append-only 链追加一条事实（链式 prev 哈希）。"""
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        payload_digest = canonical_sha256(payload)
        row = self._connection.execute(
            """
            SELECT payload_sha256 FROM effect_facts
            WHERE effect_id = ? AND attempt = ? ORDER BY seq DESC LIMIT 1
            """,
            (effect_id, attempt),
        ).fetchone()
        prev = row["payload_sha256"] if row is not None else "0" * 64
        self._connection.execute(
            """
            INSERT INTO effect_facts(
                effect_id, attempt, fact_kind, verdict,
                payload_json, payload_sha256, prev_fact_sha256, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (effect_id, attempt, fact_kind, verdict, payload_json, payload_digest, prev, created_at_ms),
        )
        return {
            "effect_id": effect_id,
            "attempt": attempt,
            "fact_kind": fact_kind,
            "verdict": verdict,
            "payload_sha256": payload_digest,
            "prev_fact_sha256": prev,
            "created_at_ms": created_at_ms,
        }

    def _get_effect_attempt_locked(self, effect_id: str, attempt: int) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM effect_attempts WHERE effect_id = ? AND attempt = ?",
            (effect_id, attempt),
        ).fetchone()

    def get_effect_attempt(self, effect_id: str, attempt: int) -> dict | None:
        with self._lock:
            row = self._get_effect_attempt_locked(effect_id, attempt)
            return dict(row) if row is not None else None

    def list_effect_attempts(self, effect_id: str) -> tuple[dict, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM effect_attempts WHERE effect_id = ? ORDER BY attempt",
                (effect_id,),
            ).fetchall()
            return tuple(dict(r) for r in rows)

    def list_effect_facts(self, effect_id: str, attempt: int | None = None) -> tuple[dict, ...]:
        with self._lock:
            if attempt is None:
                rows = self._connection.execute(
                    "SELECT * FROM effect_facts WHERE effect_id = ? ORDER BY seq",
                    (effect_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM effect_facts WHERE effect_id = ? AND attempt = ? ORDER BY seq",
                    (effect_id, attempt),
                ).fetchall()
            return tuple(dict(r) for r in rows)

    def latest_effect_verdict(self, effect_id: str, attempt: int) -> str | None:
        """该 attempt 已落地的对账结论（APPLIED/PROVEN_NOT_APPLIED/INCONCLUSIVE），无则 None。"""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT verdict FROM effect_facts
                WHERE effect_id = ? AND attempt = ? AND fact_kind = 'RECONCILIATION' AND verdict IS NOT NULL
                ORDER BY seq DESC LIMIT 1
                """,
                (effect_id, attempt),
            ).fetchone()
            return row["verdict"] if row is not None else None

    def record_effect_reconciliation(
        self,
        *,
        effect_id: str,
        attempt: int,
        verdict: str,
        evidence: dict,
        observed_at_ms: int,
    ) -> dict:
        """追加 attempt 级 reconciliation fact（草案 §3.2）。

        APPLIED 与 PROVEN_NOT_APPLIED 对同一 revision first-CAS-wins；
        PNA 后出现 APPLIED 证据 → 记录 CONTRADICTION（调用方必须全局 fence）。
        """
        if verdict not in ATTEMPT_RECONCILIATION_VERDICTS:
            raise ValueError("invalid reconciliation verdict")
        with self._lock, self._write_transaction():
            row = self._get_effect_attempt_locked(effect_id, attempt)
            if row is None:
                raise StoreNotFoundError("reconciliation target attempt is missing")
            existing = self.latest_effect_verdict(effect_id, attempt)
            contradiction = False
            if existing == verdict and existing in {"APPLIED", "PROVEN_NOT_APPLIED"}:
                # 幂等：同结论重复落地不产生新事实
                return {"fact": None, "contradiction": False,
                        "attempt_state": self._get_effect_attempt_locked(effect_id, attempt)["state"],
                        "idempotent": True}
            if existing == "APPLIED" and verdict == "PROVEN_NOT_APPLIED":
                # APPLIED 与 PNA first-CAS-wins：已证实施后不得改判未施加
                raise StoreConflictError("reconciliation verdict is already final: APPLIED")
            if existing == "PROVEN_NOT_APPLIED" and verdict == "APPLIED":
                # PNA 后出现真实 APPLIED 证据：记录 contradiction（调用方必须全局 fence），不丢弃迟到 receipt
                contradiction = True
                self._append_effect_fact_locked(
                    effect_id=effect_id, attempt=attempt, fact_kind="CONTRADICTION", verdict=verdict,
                    payload={
                        "domain": "tiangong.gateway.effect-reconciliation.v1",
                        "prior_verdict": existing,
                        "late_verdict": verdict,
                        "evidence": evidence,
                        "rule": "PNA 后出现真实 APPLIED 证据：不得丢弃迟到 receipt，全局 fence 并按 P0 对账",
                    },
                    created_at_ms=observed_at_ms,
                )
            elif existing in {"APPLIED", "PROVEN_NOT_APPLIED"}:
                raise StoreConflictError(f"reconciliation verdict is already final: {existing}")
            fact = self._append_effect_fact_locked(
                effect_id=effect_id, attempt=attempt, fact_kind="RECONCILIATION", verdict=verdict,
                payload={
                    "domain": "tiangong.gateway.effect-reconciliation.v1",
                    "verdict": verdict,
                    "evidence": evidence,
                },
                created_at_ms=observed_at_ms,
            )
            new_state = {
                "APPLIED": "RECONCILED",
                "PROVEN_NOT_APPLIED": "RECONCILE_REQUIRED",
                "INCONCLUSIVE": "RECONCILE_REQUIRED",
            }[verdict]
            self._connection.execute(
                "UPDATE effect_attempts SET state = ? WHERE effect_id = ? AND attempt = ?",
                (new_state, effect_id, attempt),
            )
            return {"fact": fact, "contradiction": contradiction, "attempt_state": new_state}

    def continue_effect_after_pna(
        self,
        *,
        effect_id: str,
        old_attempt: int,
        now_ms: int,
    ) -> dict:
        """PNA 后续作（草案 §3.2）：一次 CAS 事务。

        1. 旧 attempt 必须处于 RECONCILE_REQUIRED 且已有 PROVEN_NOT_APPLIED；
        2. terminal-fence 旧 lease/ticket（nonce 永久保留）；
        3. 创建唯一 RESERVED/PRE_START 新 attempt（崩溃后只能恢复同一 reservation）。
        """
        with self._lock, self._write_transaction():
            old = self._get_effect_attempt_locked(effect_id, old_attempt)
            if old is None:
                raise StoreNotFoundError("continuation source attempt is missing")
            if old["state"] != "RECONCILE_REQUIRED":
                raise StoreConflictError("continuation requires RECONCILE_REQUIRED source attempt")
            verdict = self.latest_effect_verdict(effect_id, old_attempt)
            if verdict != "PROVEN_NOT_APPLIED":
                raise StoreConflictError("continuation requires PROVEN_NOT_APPLIED evidence")
            updated = self._connection.execute(
                """
                UPDATE effect_attempts SET state = 'FENCED', terminal_at_ms = ?, terminal_kind = 'FENCED'
                WHERE effect_id = ? AND attempt = ? AND state = 'RECONCILE_REQUIRED'
                """,
                (now_ms, effect_id, old_attempt),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("source attempt changed before continuation")
            new_attempt = old_attempt + 1
            self._connection.execute(
                """
                INSERT INTO effect_attempts(
                    effect_id, attempt, claim_revision, lease_epoch, pipeline_version,
                    state, ticket_id, ticket_sha256, grant_sha256, nonce_sha256,
                    claimed_at_ms, side_effect_started_at_ms, terminal_at_ms, terminal_kind
                ) VALUES (?, ?, ?, NULL, NULL, 'RESERVED', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
                """,
                (effect_id, new_attempt, old["claim_revision"] + 1),
            )
            fact = self._append_effect_fact_locked(
                effect_id=effect_id, attempt=new_attempt, fact_kind="CLAIM", verdict=None,
                payload={
                    "domain": "tiangong.gateway.effect-continuation.v1",
                    "continues_attempt": old_attempt,
                    "claim_revision": old["claim_revision"] + 1,
                    "note": "PNA 后续作 reservation；崩溃后只能恢复同一 reservation",
                },
                created_at_ms=now_ms,
            )
            return {"new_attempt": new_attempt, "claim_revision": old["claim_revision"] + 1, "fact": fact}

    def admit_sub_effect(
        self,
        *,
        claim: EffectClaim,
        result: EffectResult,
        started_at_ms: int,
        receipt_response: dict | None = None,
        nonces: tuple[dict, ...] = (),
        ticket_id: str | None = None,
        ticket_sha256: str | None = None,
        grant_sha256: str | None = None,
        nonce_sha256: str | None = None,
        expected_fence_epoch: int | None = None,
    ) -> tuple[EffectLedgerRecord, bool, dict | None]:
        """原子 admission（D-06 统一 admission）：claim + STARTED + nonce 落库 + RECEIPT
        首结果在同一个写事务内提交，admission 无崩溃窗口。

        同一 effect 重复 admission（同 call_id 幂等命中）：返回既有 head 与台账记录的
        首个响应（receipt_response），不产生新 effect_id、不消耗新 nonce。
        identity 不同的同 effect_id → StoreConflictError。
        """
        if not claim.has_valid_sha256():
            raise ValueError("effect claim digest is invalid")
        if not result.has_valid_sha256():
            raise ValueError("effect result digest is invalid")
        if result.effect_id != claim.effect_id:
            raise ValueError("admission result is not bound to its claim")
        if result.status != "SUCCEEDED":
            raise ValueError("admission receipt must record a committed first result")
        if not (claim.claimed_at_ms <= started_at_ms <= result.observed_at_ms):
            raise ValueError("admission timeline is invalid")
        for nonce in nonces:
            if (
                not nonce.get("issuer")
                or not nonce.get("audience")
                or nonce.get("purpose")
                not in {"execution_ticket", "delivery_ticket", "service_auth", "omni_capability_grant"}
                or not nonce.get("nonce")
                or not nonce.get("consumer_instance_id")
                or len(str(nonce.get("payload_sha256") or "")) != 64
                or int(nonce.get("gateway_epoch") or 0) < 1
                or int(nonce.get("consumed_at_ms") or -1) < 0
                or int(nonce.get("expires_at_ms") or -1) < int(nonce.get("consumed_at_ms") or 0)
            ):
                raise ValueError("admission nonce consumption arguments are invalid")
        if (ticket_id is None) != (ticket_sha256 is None):
            raise ValueError("admission ticket binding must be complete")
        if expected_fence_epoch is not None and expected_fence_epoch < 0:
            raise ValueError("admission fence epoch is invalid")
        claim_json, claim_digest = _effect_claim_payload(claim)
        result_json, result_digest = _effect_model_payload(result)
        with self._lock, self._write_transaction():
            if expected_fence_epoch is not None:
                current_epoch = int(self._action_fence_row_locked()["action_fence_epoch"])
                if current_epoch != expected_fence_epoch:
                    raise StoreCasConflict(
                        f"action fence epoch advanced: admission epoch {expected_fence_epoch} < current {current_epoch}"
                    )
            row = self._connection.execute(
                "SELECT * FROM effect_ledger WHERE effect_id = ?",
                (claim.effect_id,),
            ).fetchone()
            if row is not None:
                record = _effect_record_from_row(row)
                stable_excludes = {"claimed_at_ms", "claim_sha256"}
                if record.claim.model_dump(exclude=stable_excludes) != claim.model_dump(exclude=stable_excludes):
                    raise StoreConflictError("effect identity was reused with different intent")
                if record.result is None:
                    raise StoreConflictError("sub-effect admission is claimed but has no first result")
                recorded = self._connection.execute(
                    """
                    SELECT payload_json FROM effect_facts
                    WHERE effect_id = ? AND fact_kind = 'RECEIPT'
                    ORDER BY seq DESC LIMIT 1
                    """,
                    (claim.effect_id,),
                ).fetchone()
                response = None
                if recorded is not None:
                    response = json.loads(recorded["payload_json"]).get("omni_admission_response")
                return record, False, response
            self._connection.execute(
                """
                INSERT INTO effect_ledger(
                    effect_id, request_id, run_id, generation, effect_kind,
                    owner_component_id, state, claimed_at_ms,
                    side_effect_started_at_ms, completed_at_ms,
                    claim_json, claim_sha256, result_json, result_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, 'SUCCEEDED', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim.effect_id,
                    claim.request_id,
                    claim.run_id,
                    claim.generation,
                    claim.effect_kind,
                    claim.owner_component_id,
                    claim.claimed_at_ms,
                    started_at_ms,
                    result.observed_at_ms,
                    claim_json,
                    claim_digest,
                    result_json,
                    result_digest,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO effect_attempts(
                    effect_id, attempt, claim_revision, lease_epoch, pipeline_version,
                    state, ticket_id, ticket_sha256, grant_sha256, nonce_sha256,
                    claimed_at_ms, side_effect_started_at_ms, terminal_at_ms, terminal_kind
                ) VALUES (?, ?, ?, ?, ?, 'SUCCEEDED', ?, ?, ?, ?, ?, ?, ?, 'SUCCEEDED')
                """,
                (
                    claim.effect_id,
                    claim.attempt,
                    claim.claim_revision,
                    claim.lease_epoch,
                    claim.pipeline_version,
                    ticket_id,
                    ticket_sha256,
                    grant_sha256,
                    nonce_sha256,
                    claim.claimed_at_ms,
                    started_at_ms,
                    result.observed_at_ms,
                ),
            )
            claim_payload = json.loads(claim_json)
            claim_payload["action_fence_epoch"] = int(self._action_fence_row_locked()["action_fence_epoch"])
            self._append_effect_fact_locked(
                effect_id=claim.effect_id, attempt=claim.attempt, fact_kind="CLAIM", verdict=None,
                payload=claim_payload, created_at_ms=claim.claimed_at_ms,
            )
            self._append_effect_fact_locked(
                effect_id=claim.effect_id, attempt=claim.attempt, fact_kind="STARTED", verdict=None,
                payload={
                    "domain": "tiangong.gateway.side-effect-started.v1",
                    "side_effect_started_at_ms": started_at_ms,
                },
                created_at_ms=started_at_ms,
            )
            receipt_payload = json.loads(result_json)
            if receipt_response is not None:
                receipt_payload["omni_admission_response"] = receipt_response
            self._append_effect_fact_locked(
                effect_id=claim.effect_id, attempt=claim.attempt, fact_kind="RECEIPT", verdict=None,
                payload=receipt_payload, created_at_ms=result.observed_at_ms,
            )
            for nonce in nonces:
                try:
                    self._connection.execute(
                        """
                        INSERT INTO security_nonce_ledger(
                            issuer, audience, purpose, nonce, payload_sha256, gateway_epoch,
                            consumer_instance_id, consumed_at_ms, expires_at_ms
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            nonce["issuer"],
                            nonce["audience"],
                            nonce["purpose"],
                            nonce["nonce"],
                            nonce["payload_sha256"],
                            int(nonce["gateway_epoch"]),
                            nonce["consumer_instance_id"],
                            int(nonce["consumed_at_ms"]),
                            int(nonce["expires_at_ms"]),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise StoreConflictError("security nonce was replayed") from exc
            return _effect_record_from_row(
                self._connection.execute(
                    "SELECT * FROM effect_ledger WHERE effect_id = ?", (claim.effect_id,)
                ).fetchone()
            ), True, None

    # ------------------------------------------------------------------
    # clarification questions（D-14：澄清不是确认）
    # 澄清发生在 effect 前，只写未决问题；答复创建 generation+1（由调用方
    # 经 acquire_generation_lease 翻代），答复本身不是副作用凭证。
    # ------------------------------------------------------------------

    def record_clarification_question(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        question: str,
        now_ms: int,
    ) -> dict:
        """登记 effect 前的未决问题（幂等：同内容问题返回既有记录）。

        硬约束：该 (request_id, run_id, generation) 不得已有任何 effect head ——
        澄清必须发生在副作用之前。
        """
        if not request_id or not run_id or generation < 0 or not question.strip() or now_ms < 0:
            raise ValueError("clarification question fact is invalid")
        question_id = "clq_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.clarification-question.v1",
                "request_id": request_id,
                "run_id": run_id,
                "generation": generation,
                "question": question,
            }
        )
        with self._lock, self._write_transaction():
            effects = self._connection.execute(
                """
                SELECT COUNT(*) AS n FROM effect_ledger
                WHERE request_id = ? AND run_id = ? AND generation = ?
                """,
                (request_id, run_id, generation),
            ).fetchone()
            if int(effects["n"]) > 0:
                raise StoreConflictError("clarification must precede any effect of its generation")
            existing = self._connection.execute(
                "SELECT * FROM clarification_questions WHERE question_id = ?",
                (question_id,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            self._connection.execute(
                """
                INSERT INTO clarification_questions(
                    question_id, request_id, run_id, generation, question,
                    state, created_at_ms, answered_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, NULL)
                """,
                (question_id, request_id, run_id, generation, question, now_ms),
            )
            return dict(
                self._connection.execute(
                    "SELECT * FROM clarification_questions WHERE question_id = ?",
                    (question_id,),
                ).fetchone()
            )

    def answer_clarification_question(
        self,
        *,
        question_id: str,
        answered_at_ms: int,
    ) -> dict:
        """登记澄清答复：OPEN → ANSWERED（CAS，幂等）；同 generation 其余 OPEN
        问题一并 SUPERSEDED（旧 generation 将被翻代）。

        本方法不创建任何 effect / fact —— 澄清答复本身不是副作用凭证；
        generation+1 由调用方经 acquire_generation_lease 完成。
        """
        if not question_id or answered_at_ms < 0:
            raise ValueError("clarification answer fact is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM clarification_questions WHERE question_id = ?",
                (question_id,),
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("clarification question does not exist")
            if row["state"] == "ANSWERED":
                return dict(row)
            if row["state"] != "OPEN":
                raise StoreConflictError("clarification question is no longer open")
            updated = self._connection.execute(
                """
                UPDATE clarification_questions
                SET state = 'ANSWERED', answered_at_ms = ?
                WHERE question_id = ? AND state = 'OPEN'
                """,
                (answered_at_ms, question_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("clarification question changed before answer")
            self._connection.execute(
                """
                UPDATE clarification_questions
                SET state = 'SUPERSEDED'
                WHERE request_id = ? AND run_id = ? AND generation = ?
                  AND state = 'OPEN' AND question_id != ?
                """,
                (row["request_id"], row["run_id"], row["generation"], question_id),
            )
            return dict(
                self._connection.execute(
                    "SELECT * FROM clarification_questions WHERE question_id = ?",
                    (question_id,),
                ).fetchone()
            )

    def list_clarification_questions(
        self,
        request_id: str,
        *,
        run_id: str | None = None,
        generation: int | None = None,
        state: str | None = None,
    ) -> tuple[dict, ...]:
        if not request_id or (state is not None and state not in {"OPEN", "ANSWERED", "SUPERSEDED"}):
            raise ValueError("clarification query is invalid")
        clauses = ["request_id = ?"]
        params: list[object] = [request_id]
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if generation is not None:
            clauses.append("generation = ?")
            params.append(generation)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM clarification_questions WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at_ms, question_id",
                params,
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def count_unreconciled_attempts(self) -> int:
        """未对账 attempt 数（STARTED 悬挂或 RECONCILE_REQUIRED 未决），action_ready 输入。"""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS n FROM effect_attempts
                WHERE state IN ('SIDE_EFFECT_STARTED', 'RECONCILE_REQUIRED')
                """
            ).fetchone()
            return int(row["n"])

    # ------------------------------------------------------------------
    # confirmation retirement（草案 §4.2：单调 epoch + receipt；fence-aware）
    # ------------------------------------------------------------------

    def confirmation_retirement_status(self) -> dict:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM confirmation_retirement WHERE retirement_id = 1"
            ).fetchone()
            return {
                "confirmation_retirement_epoch": int(row["confirmation_retirement_epoch"]),
                "retired": int(row["confirmation_retirement_epoch"]) > 0,
                "retired_at_ms": int(row["retired_at_ms"]),
                "reason": row["reason"],
                "receipt_committed": row["receipt_sha256"] is not None,
                "receipt_sha256": row["receipt_sha256"],
                "receipt_committed_at_ms": row["receipt_committed_at_ms"],
            }

    def commit_confirmation_retirement(self, *, reason: str, now_ms: int) -> int:
        """CAS 提交单调 confirmation_retirement_epoch（禁止 new issue/resolve/consume/重发）。"""
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT confirmation_retirement_epoch FROM confirmation_retirement WHERE retirement_id = 1"
            ).fetchone()
            epoch = int(row["confirmation_retirement_epoch"])
            updated = self._connection.execute(
                """
                UPDATE confirmation_retirement
                SET confirmation_retirement_epoch = ?, retired_at_ms = ?, reason = ?
                WHERE retirement_id = 1 AND confirmation_retirement_epoch = ?
                """,
                (epoch + 1, now_ms, reason, epoch),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("confirmation retirement epoch changed concurrently")
            return epoch + 1

    def commit_confirmation_retirement_receipt(self, *, receipt: dict, expected_epoch: int, now_ms: int) -> str:
        """提交 retirement receipt（幂等；epoch 必须匹配；receipt 只可提交一次）。"""
        receipt_digest = canonical_sha256(receipt)
        receipt_json = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT confirmation_retirement_epoch, receipt_sha256 FROM confirmation_retirement WHERE retirement_id = 1"
            ).fetchone()
            epoch = int(row["confirmation_retirement_epoch"])
            if epoch != expected_epoch or epoch == 0:
                raise StoreConflictError("confirmation retirement epoch mismatch for receipt")
            if row["receipt_sha256"] is not None:
                if row["receipt_sha256"] != receipt_digest:
                    raise StoreConflictError("confirmation retirement receipt already committed differently")
                return receipt_digest
            self._connection.execute(
                """
                UPDATE confirmation_retirement
                SET receipt_json = ?, receipt_sha256 = ?, receipt_committed_at_ms = ?
                WHERE retirement_id = 1 AND receipt_sha256 IS NULL
                """,
                (receipt_json, receipt_digest, now_ms),
            )
            return receipt_digest

    # ------------------------------------------------------------------
    # execution contract epoch（草案 §3.3 ExecutionContractCutover）
    # ------------------------------------------------------------------

    def execution_contract_epoch_status(self) -> dict:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM execution_contract_epoch WHERE epoch_id = 1"
            ).fetchone()
            if row is None:
                return {"activated": False, "contract_epoch": None}
            return {
                "activated": True,
                "contract_epoch": row["contract_epoch"],
                "activated_at_ms": row["activated_at_ms"],
                "fence_epoch_at_activation": row["fence_epoch_at_activation"],
                "receipt_sha256": row["receipt_sha256"],
            }

    def activate_execution_contract_epoch(
        self, *, contract_epoch: str, dispositions: list[dict], now_ms: int
    ) -> str:
        """CAS 激活 execution_contract_epoch=vNext（草案 §3.3 第 5 步）。

        前置（调用方举证）：fence 已提交、inflight=0、所有非终态 effect 已有 disposition。
        幂等：同 contract_epoch 同 disposition 重复激活返回同一 receipt。
        """
        receipt_payload = {
            "domain": "tiangong.gateway.execution-contract-epoch.v1",
            "contract_epoch": contract_epoch,
            "dispositions": dispositions,
        }
        digest = canonical_sha256(receipt_payload)
        fence_epoch = int(self._action_fence_row_locked()["action_fence_epoch"])
        if fence_epoch == 0:
            raise StoreConflictError("execution contract cutover requires an active fence first")
        if int(self._action_fence_row_locked()["inflight_count"]) != 0:
            raise StoreConflictError("execution contract cutover requires drained inflight")
        nonterminal = self._connection.execute(
            "SELECT COUNT(*) AS n FROM effect_attempts WHERE state NOT IN ('SUCCEEDED','FAILED_FINAL','RECONCILED','FENCED')"
        ).fetchone()["n"]
        if nonterminal != 0:
            raise StoreConflictError(f"execution contract cutover has {nonterminal} effects without disposition")
        dispo_json = json.dumps(dispositions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT contract_epoch, receipt_sha256 FROM execution_contract_epoch WHERE epoch_id = 1"
            ).fetchone()
            if row is not None:
                if row["contract_epoch"] != contract_epoch or row["receipt_sha256"] != digest:
                    raise StoreConflictError("execution contract epoch already activated differently")
                return digest
            self._connection.execute(
                """
                INSERT INTO execution_contract_epoch(
                    epoch_id, contract_epoch, activated_at_ms,
                    fence_epoch_at_activation, nonterminal_disposition_json, receipt_sha256
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (contract_epoch, now_ms, fence_epoch, dispo_json, digest),
            )
            # 激活即完成 drain（前置已要求 inflight=0）：fenced/draining → fenced（边界）
            self._connection.execute(
                """
                UPDATE action_fence SET draining = 0, updated_at_ms = ?
                WHERE fence_id = 1 AND inflight_count = 0
                """,
                (now_ms,),
            )
            return digest

    # ------------------------------------------------------------------
    # 全局 action fence（草案 §3.1/§12：stop/dispatch 同一 store 行；
    # fence 提交 = 新 admission/dispatch 归零；inflight 清零前只称 fenced/draining）
    # ------------------------------------------------------------------

    def _action_fence_row_locked(self) -> sqlite3.Row:
        return self._connection.execute(
            "SELECT * FROM action_fence WHERE fence_id = 1"
        ).fetchone()

    def action_fence_status(self) -> dict:
        with self._lock:
            row = self._action_fence_row_locked()
            inflight = int(row["inflight_count"])
            fenced = int(row["action_fence_epoch"]) > 0
            return {
                "action_fence_epoch": int(row["action_fence_epoch"]),
                "inflight_count": inflight,
                "fenced": fenced,
                "draining": bool(row["draining"]) or (fenced and inflight > 0),
                "zero_traffic_declared": bool(fenced and inflight == 0 and not row["draining"]),
                "display": "fenced/draining" if (fenced and (inflight > 0 or row["draining"])) else ("fenced" if fenced else "open"),
                "reason": row["reason"],
                "updated_at_ms": row["updated_at_ms"],
            }

    def increment_action_fence(self, *, reason: str, now_ms: int) -> int:
        """全局递增 action_fence_epoch：新 admission/dispatch 立即归零（CAS）。"""
        with self._lock, self._write_transaction():
            row = self._action_fence_row_locked()
            epoch = int(row["action_fence_epoch"])
            updated = self._connection.execute(
                """
                UPDATE action_fence
                SET action_fence_epoch = ?, draining = 1, reason = ?, updated_at_ms = ?
                WHERE fence_id = 1 AND action_fence_epoch = ?
                """,
                (epoch + 1, reason, now_ms, epoch),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("action fence epoch changed concurrently")
            return epoch + 1

    def acquire_dispatch_permit(
        self,
        *,
        effect_id: str,
        attempt: int,
        expected_fence_epoch: int,
        nonce_sha256: str,
        ticket_id: str | None = None,
        ticket_sha256: str | None = None,
        grant_sha256: str | None = None,
        now_ms: int,
    ) -> dict:
        """dispatch CAS（与 action_fence_epoch 同一事务、同一 store 行）。

        stop 先提交则 epoch 已变 → 拒绝（handler=0）；dispatch 先提交则该 attempt
        按"可能已施加"对账。STARTED 与 dispatch fact 在此同事务持久化（先发前记）。
        """
        with self._lock, self._write_transaction():
            fence = self._action_fence_row_locked()
            epoch = int(fence["action_fence_epoch"])
            if epoch != expected_fence_epoch:
                raise StoreConflictError(
                    f"action fence epoch advanced: ticket epoch {expected_fence_epoch} < current {epoch}"
                )
            row = self._get_effect_attempt_locked(effect_id, attempt)
            if row is None:
                raise StoreNotFoundError("dispatch target attempt is missing")
            if row["state"] != "CLAIMED":
                raise StoreConflictError("dispatch permit requires a pre-start claim")
            updated = self._connection.execute(
                """
                UPDATE effect_attempts
                SET state = 'SIDE_EFFECT_STARTED', side_effect_started_at_ms = ?,
                    nonce_sha256 = ?, ticket_id = COALESCE(ticket_id, ?),
                    ticket_sha256 = COALESCE(ticket_sha256, ?), grant_sha256 = COALESCE(grant_sha256, ?)
                WHERE effect_id = ? AND attempt = ? AND state = 'CLAIMED'
                """,
                (now_ms, nonce_sha256, ticket_id, ticket_sha256, grant_sha256, effect_id, attempt),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("dispatch permit lost the race")
            if attempt == 1:
                # head 投影同步越过边界（receipt 判定以 head 为准）
                head = self._connection.execute(
                    """
                    UPDATE effect_ledger
                    SET state = 'SIDE_EFFECT_STARTED', side_effect_started_at_ms = ?
                    WHERE effect_id = ? AND state = 'CLAIMED'
                    """,
                    (now_ms, effect_id),
                )
                if head.rowcount != 1:
                    raise StoreCasConflict("effect head changed before dispatch permit")
            self._connection.execute(
                """
                UPDATE action_fence SET inflight_count = inflight_count + 1, updated_at_ms = ?
                WHERE fence_id = 1 AND action_fence_epoch = ?
                """,
                (now_ms, epoch),
            )
            self._append_effect_fact_locked(
                effect_id=effect_id, attempt=attempt, fact_kind="DISPATCH_PERMIT", verdict=None,
                payload={
                    "domain": "tiangong.gateway.dispatch-permit.v1",
                    "fence_epoch": epoch,
                    "nonce_sha256": nonce_sha256,
                    "ticket_id": ticket_id,
                },
                created_at_ms=now_ms,
            )
            started = self._append_effect_fact_locked(
                effect_id=effect_id, attempt=attempt, fact_kind="STARTED", verdict=None,
                payload={
                    "domain": "tiangong.gateway.side-effect-started.v1",
                    "side_effect_started_at_ms": now_ms,
                    "fence_epoch": epoch,
                },
                created_at_ms=now_ms,
            )
            return {"fence_epoch": epoch, "started_fact": started}

    def release_dispatch_permit(self, *, effect_id: str, attempt: int, now_ms: int) -> None:
        """attempt 收尾：inflight 归还（receipt 落地后调用）。"""
        with self._lock, self._write_transaction():
            self._connection.execute(
                """
                UPDATE action_fence
                SET inflight_count = MAX(inflight_count - 1, 0),
                    draining = CASE WHEN inflight_count - 1 <= 0 THEN 0 ELSE draining END,
                    updated_at_ms = ?
                WHERE fence_id = 1
                """,
                (now_ms,),
            )

    def recover_started_attempts(self, *, now_ms: int) -> tuple[dict, ...]:
        """启动恢复：SIDE_EFFECT_STARTED 的 attempt 标记 RECONCILE_REQUIRED 并落 INCONCLUSIVE。

        只读对账由协调方随后用 record_effect_reconciliation 推进；
        未对账清零前不得宣称副作用流量为零（fenced/draining）。
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT effect_id, attempt FROM effect_attempts WHERE state = 'SIDE_EFFECT_STARTED' ORDER BY effect_id, attempt"
            ).fetchall()
        recovered = []
        for row in rows:
            with self._lock, self._write_transaction():
                self._connection.execute(
                    """
                    UPDATE effect_attempts SET state = 'RECONCILE_REQUIRED'
                    WHERE effect_id = ? AND attempt = ? AND state = 'SIDE_EFFECT_STARTED'
                    """,
                    (row["effect_id"], row["attempt"]),
                )
                self._append_effect_fact_locked(
                    effect_id=row["effect_id"], attempt=row["attempt"],
                    fact_kind="RECONCILIATION", verdict="INCONCLUSIVE",
                    payload={
                        "domain": "tiangong.gateway.effect-recovery.v1",
                        "reason": "started_attempt_missing_terminal_fact_after_restart",
                        "note": "进入只读对账；APPLIED/PROVEN_NOT_APPLIED 结论须由动作专属证据适配器给出",
                    },
                    created_at_ms=now_ms,
                )
                recovered.append({"effect_id": row["effect_id"], "attempt": row["attempt"]})
        return tuple(recovered)

    def consume_security_nonce(
        self,
        *,
        issuer: str,
        audience: str,
        purpose: Literal["execution_ticket", "delivery_ticket", "service_auth"],
        nonce: str,
        payload_sha256: str,
        gateway_epoch: int,
        consumer_instance_id: str,
        consumed_at_ms: int,
        expires_at_ms: int,
    ) -> NonceConsumption:
        if (
            not issuer
            or not audience
            or not nonce
            or not consumer_instance_id
            or len(payload_sha256) != 64
            or any(char not in "0123456789abcdef" for char in payload_sha256)
            or gateway_epoch < 1
            or consumed_at_ms < 0
            or expires_at_ms < consumed_at_ms
        ):
            raise ValueError("security nonce consumption arguments are invalid")
        key = (issuer, audience, purpose, nonce)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                """
                SELECT * FROM security_nonce_ledger
                WHERE issuer = ? AND audience = ? AND purpose = ? AND nonce = ?
                """,
                key,
            ).fetchone()
            if row is not None:
                if (
                    row["payload_sha256"] != payload_sha256
                    or row["gateway_epoch"] != gateway_epoch
                    or row["expires_at_ms"] != expires_at_ms
                ):
                    raise StoreConflictError("security nonce was replayed with different claims")
                return NonceConsumption(
                    row["issuer"], row["audience"], row["purpose"], row["nonce"],
                    row["payload_sha256"], row["gateway_epoch"], row["consumer_instance_id"],
                    row["consumed_at_ms"], row["expires_at_ms"], False,
                )
            self._connection.execute(
                """
                INSERT INTO security_nonce_ledger(
                    issuer, audience, purpose, nonce, payload_sha256, gateway_epoch,
                    consumer_instance_id, consumed_at_ms, expires_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issuer, audience, purpose, nonce, payload_sha256, gateway_epoch,
                    consumer_instance_id, consumed_at_ms, expires_at_ms,
                ),
            )
            return NonceConsumption(
                issuer, audience, purpose, nonce, payload_sha256, gateway_epoch,
                consumer_instance_id, consumed_at_ms, expires_at_ms, True,
            )

    def acquire_generation_lease(
        self,
        *,
        request_id: str,
        run_id: str,
        run_sequence: int,
        generation: int,
        gateway_epoch: int,
        lease_id: str,
        owner_instance_id: str,
        issued_at_ms: int,
        lease_duration_ms: int,
    ) -> tuple[GenerationLeaseView, bool]:
        if (
            not lease_id
            or not owner_instance_id
            or issued_at_ms < 0
            or not 1_000 <= lease_duration_ms <= 3_600_000
        ):
            raise ValueError("generation lease arguments are invalid")
        expires_at_ms = issued_at_ms + lease_duration_ms
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is not None:
                current = _generation_view(self._connection, row)
                if (
                    current.status == "ACTIVE"
                    and current.run_id == run_id
                    and current.run_sequence == run_sequence
                    and current.generation == generation
                    and current.gateway_epoch == gateway_epoch
                    and current.lease_id == lease_id
                    and current.owner_instance_id == owner_instance_id
                ):
                    return current, False
                if generation != current.generation + 1:
                    raise StoreConflictError("new generation must advance exactly once")
                if gateway_epoch < current.gateway_epoch:
                    raise StoreConflictError("generation lease cannot move to an older gateway epoch")
                supersedes = current.fence.fence_id
                fence = derive_generation_fence(
                    gateway_epoch=gateway_epoch,
                    request_id=request_id,
                    run_id=run_id,
                    run_sequence=run_sequence,
                    generation=generation,
                    lease_id=lease_id,
                    issued_at_ms=issued_at_ms,
                    expires_at_ms=expires_at_ms,
                    supersedes_fence_id=supersedes,
                )
                self._connection.execute(
                    "UPDATE generation_fences SET state = 'SUPERSEDED' WHERE fence_id = ?",
                    (supersedes,),
                )
                updated = self._connection.execute(
                    """
                    UPDATE request_generation
                    SET run_id = ?, run_sequence = ?, current_generation = ?, gateway_epoch = ?,
                        active_lease_id = ?, owner_instance_id = ?, status = 'ACTIVE',
                        current_fence_id = ?, revision = revision + 1, updated_at_ms = ?,
                        cancel_reason_code = NULL
                    WHERE request_id = ? AND revision = ?
                    """,
                    (
                        run_id, run_sequence, generation, gateway_epoch, lease_id, owner_instance_id,
                        fence.fence_id, issued_at_ms, request_id, current.revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("request generation changed before lease acquisition")
            else:
                fence = derive_generation_fence(
                    gateway_epoch=gateway_epoch,
                    request_id=request_id,
                    run_id=run_id,
                    run_sequence=run_sequence,
                    generation=generation,
                    lease_id=lease_id,
                    issued_at_ms=issued_at_ms,
                    expires_at_ms=expires_at_ms,
                )
                self._connection.execute(
                    """
                    INSERT INTO request_generation(
                        request_id, run_id, run_sequence, current_generation, gateway_epoch,
                        active_lease_id, owner_instance_id, status, current_fence_id,
                        revision, updated_at_ms, cancel_reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, 1, ?, NULL)
                    """,
                    (
                        request_id, run_id, run_sequence, generation, gateway_epoch,
                        lease_id, owner_instance_id, fence.fence_id, issued_at_ms,
                    ),
                )
            fence_json, fence_digest = _fence_payload(fence)
            self._connection.execute(
                """
                INSERT INTO generation_fences(
                    fence_id, request_id, run_id, generation, gateway_epoch, lease_id,
                    state, fence_json, fence_sha256, recorded_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    fence.fence_id, fence.request_id, fence.run_id, fence.generation,
                    fence.gateway_epoch, fence.lease_id, fence_json, fence_digest, issued_at_ms,
                ),
            )
            current_row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
            ).fetchone()
            return _generation_view(self._connection, current_row), True

    def heartbeat_generation_lease(
        self,
        request_id: str,
        *,
        lease_id: str,
        owner_instance_id: str,
        now_ms: int,
        lease_duration_ms: int,
    ) -> GenerationLeaseView:
        if not 1_000 <= lease_duration_ms <= 3_600_000:
            raise ValueError("generation heartbeat duration is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("request generation does not exist")
            current = _generation_view(self._connection, row)
            if (
                current.status != "ACTIVE"
                or current.lease_id != lease_id
                or current.owner_instance_id != owner_instance_id
                or now_ms > current.fence.expires_at_ms
            ):
                raise StoreConflictError("generation lease heartbeat is not authorized")
            fence = derive_generation_fence(
                gateway_epoch=current.gateway_epoch,
                request_id=current.request_id,
                run_id=current.run_id,
                run_sequence=current.run_sequence,
                generation=current.generation,
                lease_id=lease_id,
                issued_at_ms=now_ms,
                expires_at_ms=now_ms + lease_duration_ms,
                supersedes_fence_id=current.fence.fence_id,
            )
            self._connection.execute(
                "UPDATE generation_fences SET state = 'SUPERSEDED' WHERE fence_id = ? AND state = 'ACTIVE'",
                (current.fence.fence_id,),
            )
            fence_json, fence_digest = _fence_payload(fence)
            self._connection.execute(
                "INSERT INTO generation_fences VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
                (
                    fence.fence_id, fence.request_id, fence.run_id, fence.generation,
                    fence.gateway_epoch, fence.lease_id, fence_json, fence_digest, now_ms,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE request_generation
                SET current_fence_id = ?, revision = revision + 1, updated_at_ms = ?
                WHERE request_id = ? AND revision = ? AND active_lease_id = ?
                """,
                (fence.fence_id, now_ms, request_id, current.revision, lease_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("generation lease changed before heartbeat")
            return _generation_view(
                self._connection,
                self._connection.execute(
                    "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
                ).fetchone(),
            )

    def cancel_generation(
        self,
        request_id: str,
        *,
        reason_code: str,
        cancelled_at_ms: int,
    ) -> GenerationLeaseView:
        if not reason_code or cancelled_at_ms < 0:
            raise ValueError("generation cancellation fact is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("request generation does not exist")
            current = _generation_view(self._connection, row)
            if current.status == "CANCELLED":
                if current.cancel_reason_code != reason_code:
                    raise StoreConflictError("generation cancellation reason changed")
                return current
            if current.status != "ACTIVE" or cancelled_at_ms < current.updated_at_ms:
                raise StoreConflictError("generation cannot be cancelled")
            self._connection.execute(
                "UPDATE generation_fences SET state = 'CANCELLED' WHERE fence_id = ? AND state = 'ACTIVE'",
                (current.fence.fence_id,),
            )
            self._connection.execute(
                """
                UPDATE request_generation
                SET active_lease_id = NULL, status = 'CANCELLED', revision = revision + 1,
                    updated_at_ms = ?, cancel_reason_code = ?
                WHERE request_id = ? AND revision = ?
                """,
                (cancelled_at_ms, reason_code, request_id, current.revision),
            )
            return _generation_view(
                self._connection,
                self._connection.execute(
                    "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
                ).fetchone(),
            )

    def _release_generation_locked(
        self,
        request_id: str,
        *,
        released_at_ms: int,
        expected_run_id: str | None = None,
        expected_generation: int | None = None,
    ) -> GenerationLeaseView:
        row = self._connection.execute(
            "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise StoreNotFoundError("request generation does not exist")
        current = _generation_view(self._connection, row)
        if (
            (expected_run_id is not None and current.run_id != expected_run_id)
            or (expected_generation is not None and current.generation != expected_generation)
        ):
            raise StoreConflictError("generation release binding changed")
        if current.status in {"RELEASED", "CANCELLED"}:
            # CANCELLED is a terminal generation state: the lease and fence
            # are already gone, so a late finalization (watchdog AMBIGUOUS or
            # an unhandled error after a user cancel) must be idempotent
            # instead of raising StoreConflictError and wedging the request.
            return current
        if current.status != "ACTIVE" or released_at_ms < current.updated_at_ms:
            raise StoreConflictError("generation lease cannot be released")
        fence_updated = self._connection.execute(
            "UPDATE generation_fences SET state = 'RELEASED' WHERE fence_id = ? AND state = 'ACTIVE'",
            (current.fence.fence_id,),
        )
        generation_updated = self._connection.execute(
            """
            UPDATE request_generation
            SET active_lease_id = NULL, status = 'RELEASED', revision = revision + 1,
                updated_at_ms = ?, cancel_reason_code = NULL
            WHERE request_id = ? AND revision = ? AND status = 'ACTIVE'
            """,
            (released_at_ms, request_id, current.revision),
        )
        if fence_updated.rowcount != 1 or generation_updated.rowcount != 1:
            raise StoreCasConflict("generation changed before release")
        return _generation_view(
            self._connection,
            self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
            ).fetchone(),
        )

    def release_generation(self, request_id: str, *, released_at_ms: int) -> GenerationLeaseView:
        with self._lock, self._write_transaction():
            return self._release_generation_locked(request_id, released_at_ms=released_at_ms)

    def get_generation(self, request_id: str) -> GenerationLeaseView | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (request_id,)
            ).fetchone()
            return None if row is None else _generation_view(self._connection, row)

    def get_run_sequence_for_binding(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> int | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT fence_json, fence_sha256 FROM generation_fences
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY recorded_at_ms, fence_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            if not rows:
                return None
            sequences = {
                _parse_fence(row["fence_json"], row["fence_sha256"]).run_sequence
                for row in rows
            }
            if len(sequences) != 1:
                raise StoreCorruptionError("generation fence run sequence diverged")
            return next(iter(sequences))

    def record_fenced_result(
        self,
        fence: GenerationFence,
        *,
        result_id: str,
        result_sha256: str,
        observed_at_ms: int,
    ) -> FencedResultDecision:
        if (
            not result_id
            or len(result_sha256) != 64
            or any(char not in "0123456789abcdef" for char in result_sha256)
            or observed_at_ms < 0
        ):
            raise ValueError("fenced result fact is invalid")
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT * FROM fenced_results WHERE result_id = ?", (result_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["fence_id"] != fence.fence_id
                    or existing["result_sha256"] != result_sha256
                    or existing["observed_at_ms"] != observed_at_ms
                ):
                    raise StoreConflictError("fenced result ID was reused with different content")
                return _parse_fenced_result(existing, duplicate=True)
            generation_row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?", (fence.request_id,)
            ).fetchone()
            stored_fence = self._connection.execute(
                "SELECT fence_id FROM generation_fences WHERE fence_id = ?", (fence.fence_id,)
            ).fetchone()
            if generation_row is None or stored_fence is None:
                raise StoreNotFoundError("fenced result references unknown coordination state")
            current = _generation_view(self._connection, generation_row)
            fence_decision = None
            if current.status == "CANCELLED":
                disposition = "CANCELLED_IGNORED"
                reason_code = "generation.cancelled"
            elif current.status != "ACTIVE" or current.lease_id is None:
                disposition = "FENCED_IGNORED"
                reason_code = "generation.lease_inactive"
            elif fence.generation < current.generation:
                fence_decision = evaluate_generation_fence(
                    fence,
                    current_gateway_epoch=current.gateway_epoch,
                    current_request_id=current.request_id,
                    current_run_id=fence.run_id,
                    current_generation=current.generation,
                    active_lease_id=current.lease_id,
                    now_ms=observed_at_ms,
                )
                disposition = "LATE_IGNORED"
                reason_code = "generation.late_ignored"
            else:
                fence_decision = evaluate_generation_fence(
                    fence,
                    current_gateway_epoch=current.gateway_epoch,
                    current_request_id=current.request_id,
                    current_run_id=current.run_id,
                    current_generation=current.generation,
                    active_lease_id=current.lease_id,
                    now_ms=observed_at_ms,
                )
                if fence_decision.accepted:
                    disposition = "ACCEPTED"
                    reason_code = "generation.current"
                elif fence_decision.disposition == "LATE_GENERATION":
                    disposition = "LATE_IGNORED"
                    reason_code = "generation.late_ignored"
                else:
                    disposition = "FENCED_IGNORED"
                    reason_code = fence_decision.reason_code
            payload = _fenced_result_payload(
                result_id=result_id,
                request_id=fence.request_id,
                run_id=fence.run_id,
                generation=fence.generation,
                fence_id=fence.fence_id,
                disposition=disposition,
                reason_code=reason_code,
                result_sha256=result_sha256,
                observed_at_ms=observed_at_ms,
                fence_decision=fence_decision,
            )
            decision_json = canonical_json_bytes(payload).decode("utf-8")
            decision_digest = canonical_sha256(payload)
            self._connection.execute(
                """
                INSERT INTO fenced_results(
                    result_id, request_id, run_id, generation, fence_id,
                    disposition, reason_code, result_sha256, observed_at_ms,
                    decision_json, decision_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id, fence.request_id, fence.run_id, fence.generation, fence.fence_id,
                    disposition, reason_code, result_sha256, observed_at_ms,
                    decision_json, decision_digest,
                ),
            )
            return _parse_fenced_result(
                self._connection.execute(
                    "SELECT * FROM fenced_results WHERE result_id = ?", (result_id,)
                ).fetchone(),
                duplicate=False,
            )

    def emit_coordination_event(self, event: CoordinationEvent) -> tuple[CoordinationRecord, bool]:
        if not event.has_valid_sha256():
            raise ValueError("coordination event digest is invalid")
        with self._lock, self._write_transaction():
            return _insert_coordination_row(self._connection, event, state_event_id=None)

    def get_coordination_event(self, event_id: str) -> CoordinationRecord | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return None if row is None else _coordination_record_from_row(row)

    def list_dispatchable_coordination(
        self,
        *,
        consumer: Literal["skill_resolver", "user_confirmation"],
        now_ms: int,
        limit: int = 100,
    ) -> tuple[CoordinationRecord, ...]:
        if now_ms < 0 or not 1 <= limit <= 1_000:
            raise ValueError("coordination dispatch query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM coordination_events
                WHERE consumer = ? AND expires_at_ms > ? AND (
                    state = 'PENDING' OR (state = 'CLAIMED' AND claim_expires_at_ms <= ?)
                )
                ORDER BY created_at_ms, event_id LIMIT ?
                """,
                (consumer, now_ms, now_ms, limit),
            ).fetchall()
            return tuple(_coordination_record_from_row(row) for row in rows)

    def claim_coordination_event(
        self,
        event_id: str,
        *,
        consumer: Literal["skill_resolver", "user_confirmation"],
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> CoordinationRecord:
        if not worker_id or len(worker_id) > 160 or now_ms < 0 or not 1_000 <= lease_ms <= 300_000:
            raise ValueError("coordination claim arguments are invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("coordination event does not exist")
            record = _coordination_record_from_row(row)
            if record.event.consumer != consumer:
                raise StoreConflictError("coordination consumer is not authorized for this event")
            if record.state in {"RESOLVED", "CANCELLED"}:
                return record
            if now_ms >= record.event.expires_at_ms:
                self._connection.execute(
                    """
                    UPDATE coordination_events
                    SET state = 'CANCELLED', cancelled_at_ms = ?,
                        cancel_reason_code = 'coordination.expired'
                    WHERE event_id = ? AND state IN ('PENDING','CLAIMED')
                    """,
                    (now_ms, event_id),
                )
                expired = self._connection.execute(
                    "SELECT * FROM coordination_events WHERE event_id = ?", (event_id,)
                ).fetchone()
                return _coordination_record_from_row(expired)
            claimable = record.state == "PENDING" or (
                record.state == "CLAIMED"
                and record.claim_expires_at_ms is not None
                and record.claim_expires_at_ms <= now_ms
            )
            if not claimable:
                raise StoreConflictError("coordination event is not currently claimable")
            claim_expires = min(now_ms + lease_ms, record.event.expires_at_ms)
            updated = self._connection.execute(
                """
                UPDATE coordination_events
                SET state = 'CLAIMED', attempt_count = attempt_count + 1,
                    claimed_by = ?, claim_expires_at_ms = ?
                WHERE event_id = ? AND state = ? AND attempt_count = ?
                """,
                (worker_id, claim_expires, event_id, record.state, record.attempt_count),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("coordination event changed before claim")
            claimed = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return _coordination_record_from_row(claimed)

    def resolve_coordination_event(
        self,
        resolution: CoordinationResolution,
        *,
        worker_id: str,
    ) -> CoordinationRecord:
        if not worker_id or not resolution.has_valid_sha256():
            raise ValueError("coordination resolution or worker identity is invalid")
        resolution_json, resolution_digest = _coordination_payload(resolution)
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (resolution.event_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("coordination event does not exist")
            record = _coordination_record_from_row(row)
            if record.state == "RESOLVED":
                if record.resolution != resolution:
                    raise StoreConflictError("coordination result is already immutable")
                return record
            if record.state != "CLAIMED" or record.claimed_by != worker_id:
                raise StoreConflictError("coordination result is not owned by this worker")
            event = record.event
            allowed = {
                "NEED_SKILL": {"SKILL_SELECTED", "NO_SKILL", "SKILL_REJECTED"},
                "NEED_CONFIRMATION": {"CONFIRMED", "DENIED", "EXPIRED"},
            }[event.kind]
            expected_resolver = {
                "NEED_SKILL": "tiangong-total-gateway",
                "NEED_CONFIRMATION": "tiangong-desktop",
            }[event.kind]
            if resolution.outcome not in allowed or resolution.resolver_component_id != expected_resolver:
                raise StoreConflictError("coordination result exceeds event authority")
            if (
                resolution.resolved_at_ms < event.created_at_ms
                or record.claim_expires_at_ms is None
                or resolution.resolved_at_ms > record.claim_expires_at_ms
            ):
                raise StoreConflictError("coordination result is outside its claim lease")
            if resolution.outcome == "EXPIRED":
                if resolution.resolved_at_ms < event.expires_at_ms:
                    raise StoreConflictError("confirmation cannot expire before its deadline")
            elif resolution.resolved_at_ms >= event.expires_at_ms:
                raise StoreConflictError("coordination result arrived after event expiry")
            updated = self._connection.execute(
                """
                UPDATE coordination_events
                SET state = 'RESOLVED', resolution_json = ?, resolution_sha256 = ?
                WHERE event_id = ? AND state = 'CLAIMED' AND claimed_by = ?
                  AND claim_expires_at_ms >= ?
                """,
                (
                    resolution_json, resolution_digest, resolution.event_id,
                    worker_id, resolution.resolved_at_ms,
                ),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("coordination event changed before resolution")
            resolved = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (resolution.event_id,)
            ).fetchone()
            return _coordination_record_from_row(resolved)

    def cancel_coordination_event(
        self,
        event_id: str,
        *,
        cancelled_at_ms: int,
        reason_code: str,
    ) -> CoordinationRecord:
        if cancelled_at_ms < 0 or not reason_code or len(reason_code) > 160:
            raise ValueError("coordination cancellation is invalid")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("coordination event does not exist")
            record = _coordination_record_from_row(row)
            if record.state == "RESOLVED":
                raise StoreConflictError("resolved coordination event cannot be cancelled")
            if record.state == "CANCELLED":
                if record.cancel_reason_code != reason_code:
                    raise StoreConflictError("coordination cancellation is already immutable")
                return record
            if cancelled_at_ms < record.event.created_at_ms:
                raise ValueError("coordination cancellation predates event")
            updated = self._connection.execute(
                """
                UPDATE coordination_events
                SET state = 'CANCELLED', cancelled_at_ms = ?, cancel_reason_code = ?
                WHERE event_id = ? AND state IN ('PENDING','CLAIMED')
                """,
                (cancelled_at_ms, reason_code, event_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("coordination event changed before cancellation")
            cancelled = self._connection.execute(
                "SELECT * FROM coordination_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return _coordination_record_from_row(cancelled)

    def begin_channel_cutover(
        self,
        snapshot: ChannelCutoverSnapshot,
        *,
        current_gateway_epoch: int,
    ) -> bool:
        if (
            snapshot.state != "DRAINING"
            or not snapshot.has_valid_sha256()
            or snapshot.gateway_epoch != current_gateway_epoch
        ):
            raise ValueError("channel cutover must begin in the current gateway epoch")
        payload, payload_sha256 = _cutover_payload(snapshot)
        _parse_channel_cutover(payload, payload_sha256)
        with self._lock, self._write_transaction():
            rows = self._connection.execute(
                """
                SELECT * FROM channel_cutover
                WHERE cutover_id = ? OR (
                    channel = ? AND tenant_id = ? AND link_account_id = ?
                    AND gateway_epoch = ?
                )
                """,
                (
                    snapshot.cutover_id,
                    snapshot.channel,
                    snapshot.tenant_id,
                    snapshot.link_account_id,
                    snapshot.gateway_epoch,
                ),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise StoreCorruptionError(
                        "channel cutover identities point to different rows"
                    )
                stored = _parse_channel_cutover(
                    rows[0]["snapshot_json"], rows[0]["snapshot_payload_sha256"]
                )
                if stored != snapshot:
                    raise StoreConflictError(
                        "channel scope and epoch are already bound to another cutover"
                    )
                return False
            if self._connection.execute(
                """
                SELECT 1 FROM channel_cutover
                WHERE channel = ? AND tenant_id = ? AND link_account_id = ?
                  AND gateway_epoch > ? LIMIT 1
                """,
                (
                    snapshot.channel,
                    snapshot.tenant_id,
                    snapshot.link_account_id,
                    snapshot.gateway_epoch,
                ),
            ).fetchone() is not None:
                raise StoreConflictError("channel scope already has a newer migration epoch")
            prior_active = self._connection.execute(
                """
                SELECT max(l.expires_at_ms) AS expires_at_ms
                FROM channel_cutover AS c
                JOIN channel_ownership_lease AS l
                  ON l.lease_id = c.active_lease_id AND l.is_active = 1
                WHERE c.channel = ? AND c.tenant_id = ?
                  AND c.link_account_id = ?
                  AND c.gateway_epoch < ?
                """,
                (
                    snapshot.channel,
                    snapshot.tenant_id,
                    snapshot.link_account_id,
                    snapshot.gateway_epoch,
                ),
            ).fetchone()
            if (
                prior_active is not None
                and prior_active["expires_at_ms"] is not None
                and snapshot.started_at_ms
                < int(prior_active["expires_at_ms"]) + CHANNEL_LEASE_CLOCK_SKEW_MS
            ):
                raise StoreConflictError(
                    "prior channel ownership epoch has not expired safely"
                )
            self._connection.execute(
                """
                INSERT INTO channel_cutover(
                    cutover_id, migration_epoch, gateway_epoch, channel,
                    tenant_id, link_account_id, state,
                    legacy_owner_component_id, legacy_owner_instance_id,
                    candidate_owner_instance_id, drain_evidence_id,
                    active_lease_id, revision, started_at_ms, updated_at_ms,
                    snapshot_json, snapshot_payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.cutover_id,
                    snapshot.migration_epoch,
                    snapshot.gateway_epoch,
                    snapshot.channel,
                    snapshot.tenant_id,
                    snapshot.link_account_id,
                    snapshot.state,
                    snapshot.legacy_owner_component_id,
                    snapshot.legacy_owner_instance_id,
                    snapshot.candidate_owner_instance_id,
                    snapshot.revision,
                    snapshot.started_at_ms,
                    snapshot.updated_at_ms,
                    payload,
                    payload_sha256,
                ),
            )
            return True

    def record_channel_drain(
        self,
        evidence: ChannelDrainEvidence,
        *,
        current_gateway_epoch: int,
    ) -> ChannelCutoverSnapshot:
        if (
            not evidence.has_valid_sha256()
            or evidence.gateway_epoch != current_gateway_epoch
        ):
            raise ValueError("channel drain evidence is not for the current gateway epoch")
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM channel_cutover WHERE cutover_id = ?",
                (evidence.cutover_id,),
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("channel cutover does not exist")
            snapshot = _parse_channel_cutover(
                row["snapshot_json"], row["snapshot_payload_sha256"]
            )
            existing = self._connection.execute(
                "SELECT * FROM channel_drain_evidence WHERE cutover_id = ?",
                (evidence.cutover_id,),
            ).fetchone()
            if existing is not None:
                stored = _parse_channel_drain(
                    existing["evidence_json"], existing["payload_sha256"]
                )
                if stored != evidence:
                    raise StoreConflictError(
                        "channel cutover already has different drain evidence"
                    )
                if (
                    snapshot.state not in {"DRAINED", "CANDIDATE_ACTIVE"}
                    or snapshot.drain_evidence_id != evidence.evidence_id
                    or snapshot.drain_evidence_sha256 != evidence.evidence_sha256
                ):
                    raise StoreCorruptionError(
                        "stored channel drain is not reflected by the cutover"
                    )
                return snapshot
            drained = apply_channel_drain(snapshot, evidence)
            evidence_json, evidence_payload_sha256 = _cutover_payload(evidence)
            drained_json, drained_payload_sha256 = _cutover_payload(drained)
            self._connection.execute(
                """
                INSERT INTO channel_drain_evidence(
                    evidence_id, cutover_id, gateway_epoch, observed_at_ms,
                    evidence_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.cutover_id,
                    evidence.gateway_epoch,
                    evidence.observed_at_ms,
                    evidence_json,
                    evidence_payload_sha256,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE channel_cutover
                SET state = ?, drain_evidence_id = ?, revision = ?,
                    updated_at_ms = ?, snapshot_json = ?,
                    snapshot_payload_sha256 = ?
                WHERE cutover_id = ? AND state = 'DRAINING'
                  AND snapshot_payload_sha256 = ?
                """,
                (
                    drained.state,
                    drained.drain_evidence_id,
                    drained.revision,
                    drained.updated_at_ms,
                    drained_json,
                    drained_payload_sha256,
                    drained.cutover_id,
                    row["snapshot_payload_sha256"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("channel cutover changed before drain commit")
            return drained

    def activate_channel_candidate(
        self,
        cutover_id: str,
        *,
        current_gateway_epoch: int,
        component_manifest_sha256: str,
        issued_at_ms: int,
        lease_ttl_ms: int = 30_000,
    ) -> ChannelOwnershipRegistration:
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM channel_cutover WHERE cutover_id = ?", (cutover_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("channel cutover does not exist")
            snapshot = _parse_channel_cutover(
                row["snapshot_json"], row["snapshot_payload_sha256"]
            )
            if snapshot.gateway_epoch != current_gateway_epoch:
                raise StoreConflictError("channel cutover belongs to an old gateway epoch")
            newer = self._connection.execute(
                """
                SELECT 1 FROM channel_cutover
                WHERE channel = ? AND tenant_id = ? AND link_account_id = ?
                  AND gateway_epoch > ? LIMIT 1
                """,
                (
                    snapshot.channel,
                    snapshot.tenant_id,
                    snapshot.link_account_id,
                    snapshot.gateway_epoch,
                ),
            ).fetchone()
            if newer is not None:
                raise StoreConflictError("channel cutover was superseded by a newer epoch")
            evidence_row = self._connection.execute(
                "SELECT * FROM channel_drain_evidence WHERE cutover_id = ?", (cutover_id,)
            ).fetchone()
            if evidence_row is None:
                raise StoreConflictError("channel candidate cannot activate before drain")
            evidence = _parse_channel_drain(
                evidence_row["evidence_json"], evidence_row["payload_sha256"]
            )
            if snapshot.state == "CANDIDATE_ACTIVE":
                lease_row = self._connection.execute(
                    """
                    SELECT * FROM channel_ownership_lease
                    WHERE cutover_id = ? AND is_active = 1
                    """,
                    (cutover_id,),
                ).fetchone()
                if lease_row is None:
                    raise StoreCorruptionError("active channel cutover has no active lease")
                lease = _parse_channel_lease(
                    lease_row["lease_json"], lease_row["payload_sha256"]
                )
                if (
                    lease.previous_lease_sha256 is None
                    and lease.component_manifest_sha256 == component_manifest_sha256
                    and lease.issued_at_ms == issued_at_ms
                    and lease.expires_at_ms == issued_at_ms + lease_ttl_ms
                ):
                    return ChannelOwnershipRegistration(snapshot, lease, False, True)
                raise StoreConflictError("channel candidate is already active")
            active, lease = activate_candidate_owner(
                snapshot,
                evidence,
                component_manifest_sha256=component_manifest_sha256,
                issued_at_ms=issued_at_ms,
                lease_ttl_ms=lease_ttl_ms,
            )
            lease_json, lease_payload_sha256 = _cutover_payload(lease)
            active_json, active_payload_sha256 = _cutover_payload(active)
            self._connection.execute(
                """
                INSERT INTO channel_ownership_lease(
                    lease_id, cutover_id, gateway_epoch, owner_instance_id,
                    previous_lease_sha256, issued_at_ms, expires_at_ms,
                    is_active, lease_json, payload_sha256
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, 1, ?, ?)
                """,
                (
                    lease.lease_id,
                    lease.cutover_id,
                    lease.gateway_epoch,
                    lease.owner_instance_id,
                    lease.issued_at_ms,
                    lease.expires_at_ms,
                    lease_json,
                    lease_payload_sha256,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE channel_cutover
                SET state = ?, active_lease_id = ?, revision = ?,
                    updated_at_ms = ?, snapshot_json = ?,
                    snapshot_payload_sha256 = ?
                WHERE cutover_id = ? AND state = 'DRAINED'
                  AND snapshot_payload_sha256 = ?
                """,
                (
                    active.state,
                    active.active_lease_id,
                    active.revision,
                    active.updated_at_ms,
                    active_json,
                    active_payload_sha256,
                    active.cutover_id,
                    row["snapshot_payload_sha256"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("channel cutover changed before activation")
            return ChannelOwnershipRegistration(active, lease, True, False)

    def renew_channel_candidate(
        self,
        cutover_id: str,
        *,
        current_gateway_epoch: int,
        issued_at_ms: int,
        lease_ttl_ms: int = 30_000,
    ) -> ChannelOwnershipRegistration:
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM channel_cutover WHERE cutover_id = ?", (cutover_id,)
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("channel cutover does not exist")
            snapshot = _parse_channel_cutover(
                row["snapshot_json"], row["snapshot_payload_sha256"]
            )
            if (
                snapshot.state != "CANDIDATE_ACTIVE"
                or snapshot.gateway_epoch != current_gateway_epoch
            ):
                raise StoreConflictError("channel candidate is not active in this epoch")
            newer = self._connection.execute(
                """
                SELECT 1 FROM channel_cutover
                WHERE channel = ? AND tenant_id = ? AND link_account_id = ?
                  AND gateway_epoch > ? LIMIT 1
                """,
                (
                    snapshot.channel,
                    snapshot.tenant_id,
                    snapshot.link_account_id,
                    snapshot.gateway_epoch,
                ),
            ).fetchone()
            if newer is not None:
                raise StoreConflictError("channel ownership was superseded by a newer epoch")
            evidence_row = self._connection.execute(
                "SELECT * FROM channel_drain_evidence WHERE cutover_id = ?", (cutover_id,)
            ).fetchone()
            lease_row = self._connection.execute(
                """
                SELECT * FROM channel_ownership_lease
                WHERE cutover_id = ? AND is_active = 1
                """,
                (cutover_id,),
            ).fetchone()
            if evidence_row is None or lease_row is None:
                raise StoreCorruptionError("active channel ownership facts are incomplete")
            evidence = _parse_channel_drain(
                evidence_row["evidence_json"], evidence_row["payload_sha256"]
            )
            current = _parse_channel_lease(
                lease_row["lease_json"], lease_row["payload_sha256"]
            )
            if (
                current.previous_lease_sha256 is not None
                and current.issued_at_ms == issued_at_ms
                and current.expires_at_ms == issued_at_ms + lease_ttl_ms
            ):
                return ChannelOwnershipRegistration(snapshot, current, False, True)
            renewed, lease = renew_candidate_owner(
                snapshot,
                evidence,
                current,
                issued_at_ms=issued_at_ms,
                lease_ttl_ms=lease_ttl_ms,
            )
            lease_json, lease_payload_sha256 = _cutover_payload(lease)
            renewed_json, renewed_payload_sha256 = _cutover_payload(renewed)
            deactivated = self._connection.execute(
                """
                UPDATE channel_ownership_lease SET is_active = 0
                WHERE lease_id = ? AND is_active = 1
                """,
                (current.lease_id,),
            )
            if deactivated.rowcount != 1:
                raise StoreCasConflict("active channel lease changed before renewal")
            self._connection.execute(
                """
                INSERT INTO channel_ownership_lease(
                    lease_id, cutover_id, gateway_epoch, owner_instance_id,
                    previous_lease_sha256, issued_at_ms, expires_at_ms,
                    is_active, lease_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    lease.lease_id,
                    lease.cutover_id,
                    lease.gateway_epoch,
                    lease.owner_instance_id,
                    lease.previous_lease_sha256,
                    lease.issued_at_ms,
                    lease.expires_at_ms,
                    lease_json,
                    lease_payload_sha256,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE channel_cutover
                SET active_lease_id = ?, updated_at_ms = ?, snapshot_json = ?,
                    snapshot_payload_sha256 = ?
                WHERE cutover_id = ? AND state = 'CANDIDATE_ACTIVE'
                  AND active_lease_id = ? AND snapshot_payload_sha256 = ?
                """,
                (
                    renewed.active_lease_id,
                    renewed.updated_at_ms,
                    renewed_json,
                    renewed_payload_sha256,
                    renewed.cutover_id,
                    current.lease_id,
                    row["snapshot_payload_sha256"],
                ),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("channel cutover changed before lease renewal")
            return ChannelOwnershipRegistration(renewed, lease, True, False)

    def get_channel_cutover(self, cutover_id: str) -> ChannelCutoverSnapshot | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM channel_cutover WHERE cutover_id = ?", (cutover_id,)
            ).fetchone()
            if row is None:
                return None
            return _parse_channel_cutover(
                row["snapshot_json"], row["snapshot_payload_sha256"]
            )

    def get_active_channel_lease(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        current_gateway_epoch: int,
        now_ms: int,
    ) -> ChannelOwnershipLease | None:
        if now_ms < 0:
            raise ValueError("channel lease observation time is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                """
                SELECT l.lease_json, l.payload_sha256
                FROM channel_cutover AS c
                JOIN channel_ownership_lease AS l
                  ON l.lease_id = c.active_lease_id AND l.is_active = 1
                WHERE c.channel = ? AND c.tenant_id = ?
                  AND c.link_account_id = ? AND c.gateway_epoch = ?
                  AND c.state = 'CANDIDATE_ACTIVE'
                """,
                (channel, tenant_id, link_account_id, current_gateway_epoch),
            ).fetchone()
            if row is None:
                return None
            lease = _parse_channel_lease(row["lease_json"], row["payload_sha256"])
            if not lease.not_before_ms <= now_ms < lease.expires_at_ms:
                return None
            return lease

    def count_channel_cutover_records(self) -> tuple[int, int, int]:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            return tuple(
                int(self._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "channel_cutover",
                    "channel_drain_evidence",
                    "channel_ownership_lease",
                )
            )

    def promote_v21_gate(self, record: GatePromotionRecord) -> bool:
        """Persist one immutable v2.1 gate promotion with a singleton-head CAS."""
        if not record.has_valid_sha256():
            raise ValueError("gate promotion digest is invalid")
        payload, payload_sha256 = _cutover_payload(record)
        _parse_gate_promotion(payload, payload_sha256)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT promotion_json, promotion_payload_sha256 FROM gate_promotion WHERE promotion_id = ?",
                (record.promotion_id,),
            ).fetchone()
            if existing is not None:
                stored = _parse_gate_promotion(existing["promotion_json"], existing["promotion_payload_sha256"])
                if stored != record:
                    raise StoreConflictError("gate promotion identity was reused")
                return False
            head = self._connection.execute(
                "SELECT * FROM gate_promotion_head WHERE head_id = 1"
            ).fetchone()
            if head is None:
                raise StoreCorruptionError("gate promotion head is missing")
            if (
                head["current_promotion_sha256"] != record.expected_current_promotion_sha256
                or head["current_gate"] != record.from_gate
                or head["current_mode"] != record.from_mode
                or record.promotion_epoch != head["promotion_epoch"] + 1
            ):
                raise StoreCasConflict("gate promotion head changed before commit")
            self._connection.execute(
                """
                INSERT INTO gate_promotion(
                    promotion_id, promotion_epoch, expected_current_promotion_sha256,
                    from_gate, to_gate, to_mode, build_id, source_manifest_sha256,
                    promoted_at_ms, promotion_json, promotion_payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.promotion_id, record.promotion_epoch,
                    record.expected_current_promotion_sha256, record.from_gate,
                    record.to_gate, record.to_mode, record.build_id,
                    record.source_manifest_sha256, record.promoted_at_ms,
                    payload, payload_sha256,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE gate_promotion_head
                SET promotion_epoch = ?, current_gate = ?, current_mode = ?,
                    current_promotion_sha256 = ?
                WHERE head_id = 1 AND promotion_epoch = ?
                  AND current_gate = ? AND current_mode = ?
                  AND current_promotion_sha256 = ?
                """,
                (
                    record.promotion_epoch, record.to_gate, record.to_mode,
                    record.promotion_sha256, head["promotion_epoch"],
                    record.from_gate, record.from_mode,
                    record.expected_current_promotion_sha256,
                ),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("gate promotion head changed during commit")
            return True

    def get_v21_gate_promotion_head(self) -> tuple[int, str, str, str]:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT promotion_epoch, current_gate, current_mode, current_promotion_sha256 FROM gate_promotion_head WHERE head_id = 1"
            ).fetchone()
            if row is None:
                raise StoreCorruptionError("gate promotion head is missing")
            return (row["promotion_epoch"], row["current_gate"], row["current_mode"], row["current_promotion_sha256"])

    def put_model_attempt_plan(self, plan: ModelAttemptPlan, *, now_ms: int) -> bool:
        """Persist one immutable frozen model attempt plan before any dispatch."""
        if not plan.has_valid_plan_sha256():
            raise ValueError("model attempt plan digest is invalid")
        payload, payload_sha256 = _cutover_payload(plan)
        slots_json = json.dumps(
            [slot.model_dump(mode="json") for slot in plan.provider_slots],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT plan_payload_sha256 FROM model_attempt_plan WHERE model_attempt_plan_id=?",
                (plan.model_attempt_plan_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["plan_payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("model attempt plan identity was reused")
                return False
            natural = self._connection.execute(
                "SELECT 1 FROM model_attempt_plan WHERE response_episode_id=? AND plan_revision=?",
                (plan.response_episode_id, plan.plan_revision),
            ).fetchone()
            if natural is not None:
                raise StoreConflictError("model attempt plan natural key was reused")
            self._connection.execute(
                """
                INSERT INTO model_attempt_plan(
                    model_attempt_plan_id, model_effect_id, request_id, run_id, run_sequence,
                    generation, run_life_binding_sha256, root_experience_id, response_episode_id,
                    response_episode_sha256, context_pack_ref, context_pack_sha256,
                    response_basis_kind, response_basis_sha256, capability_profile_sha256,
                    provider_slots_json, plan_revision, request_sha256, completion_delivery_mode,
                    completion_decision_ref, completion_decision_sha256, conversation_basis_ref,
                    plan_json, plan_payload_sha256, created_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    plan.model_attempt_plan_id, plan.model_effect_id, plan.request_id, plan.run_id,
                    plan.run_sequence, plan.generation, plan.run_life_binding_sha256,
                    plan.root_experience_id, plan.response_episode_id, plan.response_episode_sha256,
                    plan.context_pack_ref, plan.context_pack_sha256, plan.response_basis_kind,
                    plan.response_basis_sha256, plan.capability_profile_sha256, slots_json,
                    plan.plan_revision, plan.request_sha256, plan.completion_delivery_mode,
                    plan.completion_decision_ref, plan.completion_decision_sha256,
                    plan.conversation_basis_ref, payload, payload_sha256, now_ms,
                ),
            )
            return True

    def get_model_attempt_plan(self, plan_id: str) -> ModelAttemptPlan | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT plan_json FROM model_attempt_plan WHERE model_attempt_plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                return None
        return ModelAttemptPlan.model_validate_json(str(row["plan_json"]))

    def create_dispatch_marker(self, *, marker_id: str, plan_id: str, attempt_id: str, slot_no: int, now_ms: int) -> bool:
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT marker_id FROM model_dispatch_marker WHERE attempt_id=? OR (model_attempt_plan_id=? AND slot_no=?)",
                (attempt_id, plan_id, slot_no),
            ).fetchone()
            if existing is not None:
                return False
            self._connection.execute(
                "INSERT INTO model_dispatch_marker(marker_id,model_attempt_plan_id,attempt_id,slot_no,status,created_at_ms) VALUES(?,?,?,?,'pending',?)",
                (marker_id, plan_id, attempt_id, slot_no, now_ms),
            )
            return True

    def mark_dispatch_marker_dispatched(self, *, marker_id: str, now_ms: int) -> bool:
        with self._lock, self._write_transaction():
            updated = self._connection.execute(
                "UPDATE model_dispatch_marker SET status='dispatched', dispatched_at_ms=? WHERE marker_id=? AND status='pending'",
                (now_ms, marker_id),
            )
            return updated.rowcount == 1

    def get_dispatch_marker(self, *, plan_id: str, slot_no: int) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT marker_id, attempt_id, status FROM model_dispatch_marker WHERE model_attempt_plan_id=? AND slot_no=?",
                (plan_id, slot_no),
            ).fetchone()
            if row is None:
                return None
            return {
                "marker_id": str(row["marker_id"]),
                "attempt_id": str(row["attempt_id"]),
                "status": str(row["status"]),
            }

    def put_model_attempt_result(self, result: ModelAttemptResult) -> bool:
        """Persist one immutable transport-adapter result; never rewritten."""
        payload, payload_sha256 = _cutover_payload(result)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM model_attempt_result WHERE model_attempt_receipt_id=?",
                (result.model_attempt_receipt_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("model attempt result identity was reused")
                return False
            natural = self._connection.execute(
                "SELECT 1 FROM model_attempt_result WHERE model_attempt_plan_id=? AND slot_no=?",
                (result.model_attempt_plan_id, result.slot_no),
            ).fetchone()
            if natural is not None:
                raise StoreConflictError("model attempt result slot was reused")
            self._connection.execute(
                """
                INSERT INTO model_attempt_result(
                    model_attempt_receipt_id, model_attempt_plan_id, model_attempt_plan_sha256,
                    model_effect_id, request_id, run_id, run_sequence, generation,
                    run_life_binding_sha256, root_experience_id, response_episode_id, attempt_id,
                    slot_no, provider, model, status, attempt_plan_revision, request_sha256,
                    dispatched, started_at_ms, completed_at_ms, response_schema_valid,
                    result_json, payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result.model_attempt_receipt_id, result.model_attempt_plan_id,
                    result.model_attempt_plan_sha256, result.model_effect_id, result.request_id,
                    result.run_id, result.run_sequence, result.generation,
                    result.run_life_binding_sha256, result.root_experience_id,
                    result.response_episode_id, result.attempt_id, result.slot_no,
                    result.provider, result.model, result.status, result.attempt_plan_revision,
                    result.request_sha256, int(result.dispatched), result.started_at_ms,
                    result.completed_at_ms, int(result.response_schema_valid),
                    payload, payload_sha256,
                ),
            )
            return True

    def get_model_attempt_result(self, *, plan_id: str, slot_no: int) -> ModelAttemptResult | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT result_json FROM model_attempt_result WHERE model_attempt_plan_id=? AND slot_no=?",
                (plan_id, slot_no),
            ).fetchone()
            if row is None:
                return None
        return ModelAttemptResult.model_validate_json(str(row["result_json"]))

    def put_model_attempt_plan_outcome(self, outcome: ModelAttemptPlanOutcome) -> bool:
        if not outcome.has_valid_outcome_sha256():
            raise ValueError("model attempt plan outcome digest is invalid")
        payload, payload_sha256 = _cutover_payload(outcome)
        refs_json = json.dumps(list(outcome.ordered_attempt_refs), sort_keys=True, separators=(",", ":"))
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM model_attempt_plan_outcome WHERE model_attempt_plan_id=?",
                (outcome.model_attempt_plan_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("model attempt plan outcome was reused")
                return False
            self._connection.execute(
                """
                INSERT INTO model_attempt_plan_outcome(
                    model_attempt_plan_outcome_id, model_attempt_plan_id, model_attempt_plan_sha256,
                    status, ordered_attempt_refs_json, winner_attempt_ref, completed_at_ms,
                    outcome_json, payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    outcome.model_attempt_plan_outcome_id, outcome.model_attempt_plan_id,
                    outcome.model_attempt_plan_sha256, outcome.status, refs_json,
                    outcome.winner_attempt_ref, outcome.completed_at_ms, payload, payload_sha256,
                ),
            )
            return True

    def put_assistant_commit(self, commit: AssistantCommit) -> bool:
        """Persist one durable assistant commit; unique per response episode."""
        if not commit.has_valid_sha256():
            raise ValueError("assistant commit digest is invalid")
        payload, payload_sha256 = _cutover_payload(commit)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM assistant_commit WHERE assistant_commit_id=?",
                (commit.assistant_commit_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("assistant commit identity was reused")
                return False
            natural = self._connection.execute(
                "SELECT 1 FROM assistant_commit WHERE response_episode_id=?",
                (commit.response_episode_id,),
            ).fetchone()
            if natural is not None:
                raise StoreConflictError("assistant commit response episode was reused")
            self._connection.execute(
                """
                INSERT INTO assistant_commit(
                    assistant_commit_id, assistant_message_id, life_turn_commit_ref,
                    life_turn_commit_sha256, response_episode_id, model_attempt_plan_outcome_ref,
                    model_attempt_receipt_id, output_text_sha256, committed_text_sha256,
                    text_object_id, committed_at_ms, commit_json, payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    commit.assistant_commit_id, commit.assistant_message_id,
                    commit.life_turn_commit_ref, commit.life_turn_commit_sha256,
                    commit.response_episode_id, commit.model_attempt_plan_outcome_ref,
                    commit.model_attempt_receipt_id, commit.output_text_sha256,
                    commit.committed_text_sha256, commit.text_object_id, commit.committed_at_ms,
                    payload, payload_sha256,
                ),
            )
            return True

    def put_system_status(self, status: SystemStatusRecord) -> bool:
        payload, payload_sha256 = _cutover_payload(status)
        refs_json = json.dumps(list(status.source_fact_refs), sort_keys=True, separators=(",", ":"))
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM system_status WHERE system_status_id=?",
                (status.system_status_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("system status identity was reused")
                return False
            self._connection.execute(
                """
                INSERT INTO system_status(
                    system_status_id, request_id, run_id, run_sequence, generation,
                    response_episode_id, status_code, severity, source_component,
                    source_fact_refs_json, display_object_ref, origin, created_at_ms,
                    status_json, payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    status.system_status_id, status.request_id, status.run_id,
                    status.run_sequence, status.generation, status.response_episode_id,
                    status.status_code, status.severity, status.source_component,
                    refs_json, status.display_object_ref, status.origin, status.created_at_ms,
                    payload, payload_sha256,
                ),
            )
            return True

    def put_effect_outcome_head(
        self,
        *,
        effect_id: str,
        original_execution_result_ref: str,
        effective_status: str,
        head_revision: int,
        head_sha256: str,
        latest_reconciliation_ref: str | None,
        updated_at_ms: int,
        expected_head_sha256: str | None,
    ) -> bool:
        """Mutable per-effect outcome head with strict CAS (never rewrites history)."""
        if effective_status not in {"SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED", "FENCED"}:
            raise ValueError("effect outcome status is invalid")
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT head_sha256, effective_status FROM effect_outcome_head WHERE effect_id=?",
                (effect_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["head_sha256"]) != (expected_head_sha256 or ""):
                    raise StoreConflictError("effect outcome head CAS is stale")
                if str(existing["effective_status"]) == effective_status:
                    return False
                self._connection.execute(
                    "UPDATE effect_outcome_head SET original_execution_result_ref=?, effective_status=?, head_revision=?, latest_reconciliation_ref=?, head_sha256=?, updated_at_ms=? WHERE effect_id=?",
                    (
                        original_execution_result_ref, effective_status, head_revision,
                        latest_reconciliation_ref, head_sha256, updated_at_ms, effect_id,
                    ),
                )
                return True
            if expected_head_sha256 not in {None, "0" * 64}:
                raise StoreConflictError("effect outcome head genesis CAS is invalid")
            self._connection.execute(
                """
                INSERT INTO effect_outcome_head(
                    effect_id, original_execution_result_ref, effective_status, head_revision,
                    latest_reconciliation_ref, head_sha256, updated_at_ms
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    effect_id, original_execution_result_ref, effective_status, head_revision,
                    latest_reconciliation_ref, head_sha256, updated_at_ms,
                ),
            )
            return True

    def get_effect_outcome_head(self, effect_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT original_execution_result_ref, effective_status, head_revision, head_sha256 FROM effect_outcome_head WHERE effect_id=?",
                (effect_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "original_execution_result_ref": str(row["original_execution_result_ref"]),
                "effective_status": str(row["effective_status"]),
                "head_revision": int(row["head_revision"]),
                "head_sha256": str(row["head_sha256"]),
            }

    def register_life_execution_proposal(
        self,
        proposal: LifeExecutionProposal,
        intent: ActionIntentVNext,
        receipt: GatewayRegistrationReceipt,
        *,
        now_ms: int,
    ) -> bool:
        """One transaction: proposal + intent mapping + registration receipt."""
        if not proposal.has_valid_sha256():
            raise ValueError("life execution proposal digest is invalid")
        if not intent.has_valid_sha256():
            raise ValueError("vNext action intent digest is invalid")
        if not receipt.has_valid_sha256():
            raise ValueError("gateway registration receipt digest is invalid")
        if (
            receipt.proposal_id != proposal.proposal_id
            or receipt.action_intent_sha256 != intent.intent_sha256
            or receipt.run_life_binding_sha256 != proposal.run_life_binding_sha256
        ):
            raise ValueError("registration binding is inconsistent")
        proposal_json, proposal_sha256 = _cutover_payload(proposal)
        receipt_json, _ = _cutover_payload(receipt)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT 1 FROM life_proposal_registration WHERE proposal_id=? OR registration_id=?",
                (proposal.proposal_id, receipt.registration_id),
            ).fetchone()
            if existing is not None:
                return False
            self._connection.execute(
                """
                INSERT INTO life_proposal_registration(
                    proposal_id, proposal_sha256, registration_id, request_id, run_id,
                    run_sequence, generation, run_life_binding_sha256, action_intent_id,
                    action_intent_sha256, proposal_json, receipt_json, registered_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal.proposal_id, proposal_sha256, receipt.registration_id,
                    receipt.request_id, receipt.run_id, receipt.run_sequence,
                    receipt.generation, receipt.run_life_binding_sha256,
                    intent.intent_id, intent.intent_sha256,
                    proposal_json, receipt_json, now_ms,
                ),
            )
            return True

    def put_effect_reconciliation_record(self, record: EffectReconciliationRecord) -> bool:
        """Append-only reconciliation observation with monotonic attempt_no."""
        if not record.has_valid_sha256():
            raise ValueError("effect reconciliation digest is invalid")
        payload, payload_sha256 = _cutover_payload(record)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM effect_reconciliation WHERE reconciliation_id=?",
                (record.reconciliation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("effect reconciliation identity was reused")
                return False
            latest = self._connection.execute(
                "SELECT MAX(attempt_no) AS latest FROM effect_reconciliation WHERE effect_id=?",
                (record.effect_id,),
            ).fetchone()
            latest_no = int(latest["latest"]) if latest and latest["latest"] is not None else 0
            if record.attempt_no != latest_no + 1:
                raise StoreConflictError("effect reconciliation attempt_no is not monotonic")
            self._connection.execute(
                """
                INSERT INTO effect_reconciliation(
                    reconciliation_id, effect_id, previous_outcome_head_sha256, attempt_no,
                    strategy_id, observation_status, observation_ref, observed_at_ms,
                    record_json, payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.reconciliation_id, record.effect_id,
                    record.previous_outcome_head_sha256, record.attempt_no,
                    record.strategy_id, record.observation_status, record.observation_ref,
                    record.observed_at_ms, payload, payload_sha256,
                ),
            )
            return True

    def put_composite_execution_outcome(self, outcome: CompositeExecutionOutcome) -> bool:
        """Persist one immutable machine-computed parent aggregate."""
        if not outcome.has_valid_sha256():
            raise ValueError("composite execution outcome digest is invalid")
        payload, payload_sha256 = _cutover_payload(outcome)
        child_json = json.dumps(list(outcome.child_result_refs), sort_keys=True, separators=(",", ":"))
        compensation_json = json.dumps(list(outcome.compensation_effect_refs), sort_keys=True, separators=(",", ":"))
        warning_json = json.dumps(list(outcome.warning_refs), sort_keys=True, separators=(",", ":"))
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM composite_execution_outcome WHERE composite_execution_id=?",
                (outcome.composite_execution_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StoreConflictError("composite execution outcome was reused")
                return False
            self._connection.execute(
                """
                INSERT INTO composite_execution_outcome(
                    composite_execution_id, request_id, run_id, run_sequence, generation,
                    parent_effect_id, child_result_refs_json, compensation_effect_refs_json,
                    warning_refs_json, status, retry_required, summary_sha256, created_at_ms,
                    outcome_json, payload_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    outcome.composite_execution_id, outcome.request_id, outcome.run_id,
                    outcome.run_sequence, outcome.generation, outcome.parent_effect_id,
                    child_json, compensation_json, warning_json, outcome.status,
                    int(outcome.retry_required), outcome.summary_sha256, outcome.created_at_ms,
                    payload, payload_sha256,
                ),
            )
            return True

    def record_shadow_batch(
        self,
        batch: ShadowObservationBatch,
        *,
        compared_at_ms: int,
    ) -> ShadowBatchRegistration:
        if not batch.has_valid_sha256():
            raise ValueError("shadow observation batch digest is invalid")
        copy = batch.ingress_copy
        if copy.envelope.channel not in {"wechat", "feishu"}:
            raise ValueError("shadow observations are limited to communication channels")
        latest_observation_ms = max(item.observed_at_ms for item in batch.observations)
        if compared_at_ms < max(copy.copied_at_ms, latest_observation_ms):
            raise ValueError("shadow comparison time predates its observations")
        copy_json, copy_payload_sha256 = _shadow_payload(copy)
        copy_created = False
        observations_created = 0
        with self._lock, self._write_transaction():
            rows = self._connection.execute(
                """
                SELECT * FROM shadow_ingress
                WHERE shadow_id = ? OR inbound_id = ? OR idempotency_key = ?
                """,
                (
                    copy.shadow_id,
                    copy.envelope.inbound_id,
                    copy.envelope.idempotency_key,
                ),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise StoreCorruptionError(
                        "shadow ingress identities point to different copies"
                    )
                stored_copy = _parse_shadow_copy(
                    rows[0]["copy_json"], rows[0]["payload_sha256"]
                )
                if stored_copy != copy:
                    raise StoreConflictError(
                        "shadow ingress identity was reused for another copy"
                    )
            else:
                self._connection.execute(
                    """
                    INSERT INTO shadow_ingress(
                        shadow_id, inbound_id, idempotency_key, channel, tenant_id,
                        link_account_id, envelope_sha256, source_ingress_sha256,
                        source_ack_permit_sha256, copied_at_ms,
                        request_creation_permitted, effects_permitted,
                        copy_json, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        copy.shadow_id,
                        copy.envelope.inbound_id,
                        copy.envelope.idempotency_key,
                        copy.envelope.channel,
                        copy.envelope.tenant_id,
                        copy.envelope.link_account_id,
                        copy.envelope_sha256,
                        copy.source_ingress_sha256,
                        copy.source_ack_permit_sha256,
                        copy.copied_at_ms,
                        copy_json,
                        copy_payload_sha256,
                    ),
                )
                copy_created = True

            for observation in batch.observations:
                observation_json, observation_payload_sha256 = _shadow_payload(observation)
                existing = self._connection.execute(
                    """
                    SELECT * FROM shadow_decision
                    WHERE observation_id = ? OR (shadow_id = ? AND side = ?)
                    """,
                    (
                        observation.observation_id,
                        observation.shadow_id,
                        observation.side,
                    ),
                ).fetchall()
                if existing:
                    if len(existing) != 1:
                        raise StoreCorruptionError(
                            "shadow decision identities point to different observations"
                        )
                    stored = _parse_shadow_observation(
                        existing[0]["observation_json"], existing[0]["payload_sha256"]
                    )
                    if stored != observation:
                        raise StoreConflictError(
                            "shadow decision side is already bound to another observation"
                        )
                    continue
                self._connection.execute(
                    """
                    INSERT INTO shadow_decision(
                        observation_id, shadow_id, side, source_component_id,
                        source_instance_id, source_decision_sha256, envelope_sha256,
                        classification, should_forward, attachment_count,
                        observed_at_ms, request_creation_permitted, effects_permitted,
                        observation_json, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        observation.observation_id,
                        observation.shadow_id,
                        observation.side,
                        observation.source_component_id,
                        observation.source_instance_id,
                        observation.source_decision_sha256,
                        observation.envelope_sha256,
                        observation.classification,
                        int(observation.should_forward),
                        observation.attachment_count,
                        observation.observed_at_ms,
                        observation_json,
                        observation_payload_sha256,
                    ),
                )
                observations_created += 1

            decision_rows = self._connection.execute(
                "SELECT * FROM shadow_decision WHERE shadow_id = ? ORDER BY side",
                (copy.shadow_id,),
            ).fetchall()
            decisions = {
                row["side"]: _parse_shadow_observation(
                    row["observation_json"], row["payload_sha256"]
                )
                for row in decision_rows
            }
            comparison = compare_shadow_observations(
                copy,
                decisions.get("legacy"),
                decisions.get("candidate"),
                compared_at_ms=compared_at_ms,
            )
        return ShadowBatchRegistration(
            comparison=comparison,
            copy_created=copy_created,
            observations_created=observations_created,
            duplicate=not copy_created and observations_created == 0,
        )

    def get_shadow_comparison(
        self,
        shadow_id: str,
        *,
        compared_at_ms: int,
    ) -> ShadowComparison | None:
        if compared_at_ms < 0:
            raise ValueError("shadow comparison time is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM shadow_ingress WHERE shadow_id = ?", (shadow_id,)
            ).fetchone()
            if row is None:
                return None
            copy = _parse_shadow_copy(row["copy_json"], row["payload_sha256"])
            decision_rows = self._connection.execute(
                "SELECT * FROM shadow_decision WHERE shadow_id = ? ORDER BY side",
                (shadow_id,),
            ).fetchall()
            decisions = {
                item["side"]: _parse_shadow_observation(
                    item["observation_json"], item["payload_sha256"]
                )
                for item in decision_rows
            }
            latest = max(
                (item.observed_at_ms for item in decisions.values()),
                default=copy.copied_at_ms,
            )
            if compared_at_ms < max(copy.copied_at_ms, latest):
                raise ValueError("shadow comparison time predates stored observations")
            return compare_shadow_observations(
                copy,
                decisions.get("legacy"),
                decisions.get("candidate"),
                compared_at_ms=compared_at_ms,
            )

    def count_shadow_records(self) -> tuple[int, int]:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            copies = int(
                self._connection.execute("SELECT count(*) FROM shadow_ingress").fetchone()[0]
            )
            observations = int(
                self._connection.execute("SELECT count(*) FROM shadow_decision").fetchone()[0]
            )
            return copies, observations

    def register_request(
        self,
        envelope: InboundEnvelope,
        *,
        ingress_sha256: str,
        created_at_ms: int,
    ) -> JournalRegistration:
        if created_at_ms < envelope.received_at_ms:
            raise ValueError("request journal time predates inbound receipt")
        if len(ingress_sha256) != 64 or any(char not in "0123456789abcdef" for char in ingress_sha256):
            raise ValueError("request journal ingress digest is invalid")
        identity = derive_request_identity(envelope.idempotency_key)
        entry = RequestJournalEntry(
            request_id=identity.request_id,
            idempotency_key=envelope.idempotency_key,
            inbound_id=envelope.inbound_id,
            session_scope_hash=envelope.conversation_scope_hash,
            ingress_sha256=ingress_sha256,
            created_at_ms=created_at_ms,
            entry_sha256="0" * 64,
        ).with_computed_sha256()
        entry_json, entry_digest = _journal_payload(entry)
        envelope_json, envelope_digest = _inbound_envelope_payload(envelope)
        with self._lock, self._write_transaction():
            existing = self._connection.execute(
                """
                SELECT * FROM request_journal
                WHERE request_id = ? OR idempotency_key = ? OR inbound_id = ?
                """,
                (entry.request_id, entry.idempotency_key, entry.inbound_id),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise StoreCorruptionError("request journal identities point to different entries")
                stored = _parse_journal_entry(existing[0]["entry_json"], existing[0]["entry_sha256"])
                stable_fields = (
                    "request_id",
                    "idempotency_key",
                    "inbound_id",
                    "session_scope_hash",
                )
                if any(getattr(stored, name) != getattr(entry, name) for name in stable_fields):
                    raise StoreConflictError("inbound identity was reused for another request")
                inbound_row = self._connection.execute(
                    "SELECT * FROM request_inbound_payload WHERE request_id = ?",
                    (stored.request_id,),
                ).fetchone()
                if inbound_row is None:
                    raise StoreCorruptionError("journal request is missing its inbound payload marker")
                retry_equivalent = False
                if inbound_row["availability"] == "AVAILABLE":
                    stored_envelope = _parse_inbound_envelope(
                        inbound_row["envelope_json"],
                        inbound_row["envelope_sha256"],
                    )
                    retry_equivalent = _desktop_retry_equivalent(stored_envelope, envelope)
                    if stored_envelope != envelope and not retry_equivalent:
                        raise StoreConflictError("inbound identity was reused with another envelope")
                    if stored.ingress_sha256 != entry.ingress_sha256 and not retry_equivalent:
                        raise StoreConflictError("inbound identity was reused with another request")
                elif inbound_row["availability"] == "LEGACY_UNAVAILABLE":
                    if stored.ingress_sha256 != entry.ingress_sha256:
                        raise StoreConflictError("legacy inbound identity digest changed")
                    self._connection.execute(
                        """
                        UPDATE request_inbound_payload
                        SET availability = 'AVAILABLE', envelope_json = ?, envelope_sha256 = ?
                        WHERE request_id = ? AND availability = 'LEGACY_UNAVAILABLE'
                        """,
                        (envelope_json, envelope_digest, stored.request_id),
                    )
                else:
                    raise StoreCorruptionError("journal request has an invalid inbound payload marker")
                queue = self._connection.execute(
                    "SELECT sequence, state FROM session_queue WHERE request_id = ?",
                    (stored.request_id,),
                ).fetchone()
                if queue is None:
                    raise StoreCorruptionError("journal request is missing its session queue entry")
                return JournalRegistration(stored, queue["sequence"], queue["state"], False, True)

            self._connection.execute(
                """
                INSERT INTO request_journal(
                    request_id, idempotency_key, inbound_id, session_scope_hash,
                    ingress_sha256, created_at_ms, entry_json, entry_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.request_id,
                    entry.idempotency_key,
                    entry.inbound_id,
                    entry.session_scope_hash,
                    entry.ingress_sha256,
                    entry.created_at_ms,
                    entry_json,
                    entry_digest,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO request_inbound_payload(
                    request_id, availability, recorded_at_ms, envelope_json, envelope_sha256
                ) VALUES (?, 'AVAILABLE', ?, ?, ?)
                """,
                (
                    entry.request_id,
                    entry.created_at_ms,
                    envelope_json,
                    envelope_digest,
                ),
            )
            actor = self._connection.execute(
                "SELECT * FROM session_actor WHERE session_scope_hash = ?",
                (entry.session_scope_hash,),
            ).fetchone()
            if actor is None:
                sequence = 1
                queue_state = "ACTIVE"
                self._connection.execute(
                    """
                    INSERT INTO session_actor(
                        session_scope_hash, active_request_id, next_sequence, revision, updated_at_ms
                    ) VALUES (?, ?, 2, 1, ?)
                    """,
                    (entry.session_scope_hash, entry.request_id, created_at_ms),
                )
            else:
                sequence = int(actor["next_sequence"])
                queue_state = "ACTIVE" if actor["active_request_id"] is None else "QUEUED"
                active_request_id = entry.request_id if queue_state == "ACTIVE" else actor["active_request_id"]
                updated = self._connection.execute(
                    """
                    UPDATE session_actor
                    SET active_request_id = ?, next_sequence = ?, revision = ?, updated_at_ms = ?
                    WHERE session_scope_hash = ? AND revision = ? AND next_sequence = ?
                    """,
                    (
                        active_request_id,
                        sequence + 1,
                        int(actor["revision"]) + 1,
                        created_at_ms,
                        entry.session_scope_hash,
                        actor["revision"],
                        actor["next_sequence"],
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("session actor changed before request registration")
            self._connection.execute(
                """
                INSERT INTO session_queue(
                    session_scope_hash, sequence, request_id, state,
                    enqueued_at_ms, activated_at_ms, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    entry.session_scope_hash,
                    sequence,
                    entry.request_id,
                    queue_state,
                    created_at_ms,
                    created_at_ms if queue_state == "ACTIVE" else None,
                ),
            )
        return JournalRegistration(entry, sequence, queue_state, True, False)

    def get_request_entry(self, request_id: str) -> RequestJournalEntry | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT entry_json, entry_sha256 FROM request_journal WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return None if row is None else _parse_journal_entry(row["entry_json"], row["entry_sha256"])

    def get_request_envelope(self, request_id: str) -> InboundEnvelope | None:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM request_inbound_payload WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                if self._connection.execute(
                    "SELECT 1 FROM request_journal WHERE request_id = ?",
                    (request_id,),
                ).fetchone() is not None:
                    raise StoreCorruptionError("journal request is missing its inbound payload marker")
                return None
            if row["availability"] == "LEGACY_UNAVAILABLE":
                return None
            if row["availability"] != "AVAILABLE":
                raise StoreCorruptionError("request inbound payload availability is invalid")
            return _parse_inbound_envelope(row["envelope_json"], row["envelope_sha256"])

    def get_session_queue(self, session_scope_hash: str) -> tuple[SessionQueueSnapshot, ...]:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                "SELECT * FROM session_queue WHERE session_scope_hash = ? ORDER BY sequence",
                (session_scope_hash,),
            ).fetchall()
            return tuple(
                SessionQueueSnapshot(
                    session_scope_hash=row["session_scope_hash"],
                    request_id=row["request_id"],
                    sequence=row["sequence"],
                    state=row["state"],
                    enqueued_at_ms=row["enqueued_at_ms"],
                    activated_at_ms=row["activated_at_ms"],
                    completed_at_ms=row["completed_at_ms"],
                )
                for row in rows
            )

    def list_unclaimed_active_requests(
        self,
        *,
        limit: int = 64,
    ) -> tuple[ActiveRequestCandidate, ...]:
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("active request candidate limit is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT
                    j.entry_json,
                    j.entry_sha256,
                    i.envelope_json,
                    i.envelope_sha256,
                    q.session_scope_hash,
                    q.request_id,
                    q.sequence,
                    q.state,
                    q.enqueued_at_ms,
                    q.activated_at_ms,
                    q.completed_at_ms
                FROM session_queue AS q
                JOIN request_journal AS j ON j.request_id = q.request_id
                JOIN request_inbound_payload AS i ON i.request_id = q.request_id
                LEFT JOIN request_generation AS g ON g.request_id = q.request_id
                WHERE
                    q.state = 'ACTIVE'
                    AND g.request_id IS NULL
                    AND i.availability = 'AVAILABLE'
                ORDER BY q.activated_at_ms, q.session_scope_hash, q.sequence
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            candidates: list[ActiveRequestCandidate] = []
            for row in rows:
                entry = _parse_journal_entry(row["entry_json"], row["entry_sha256"])
                envelope = _parse_inbound_envelope(
                    row["envelope_json"],
                    row["envelope_sha256"],
                )
                queue = SessionQueueSnapshot(
                    session_scope_hash=row["session_scope_hash"],
                    request_id=row["request_id"],
                    sequence=row["sequence"],
                    state=row["state"],
                    enqueued_at_ms=row["enqueued_at_ms"],
                    activated_at_ms=row["activated_at_ms"],
                    completed_at_ms=row["completed_at_ms"],
                )
                if (
                    entry.request_id != queue.request_id
                    or entry.session_scope_hash != queue.session_scope_hash
                    or envelope.inbound_id != entry.inbound_id
                    or envelope.idempotency_key != entry.idempotency_key
                    or envelope.conversation_scope_hash != entry.session_scope_hash
                    or queue.activated_at_ms is None
                    or queue.completed_at_ms is not None
                ):
                    raise StoreCorruptionError("active request candidate disagrees with its journal")
                candidates.append(
                    ActiveRequestCandidate(entry=entry, envelope=envelope, queue=queue)
                )
            return tuple(candidates)

    def list_cancelled_active_session_request_ids(
        self,
        *,
        limit: int = 64,
    ) -> tuple[str, ...]:
        """Return session heads fenced as cancelled but not queue-completed.

        Cancellation and backend interruption are separate durable facts. If a
        process exits between them, the ACTIVE session head must still be
        retired so later QUEUED requests can be promoted.
        """
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("cancelled session request limit is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT q.request_id
                FROM session_queue AS q
                JOIN request_generation AS g ON g.request_id = q.request_id
                WHERE q.state = 'ACTIVE' AND g.status = 'CANCELLED'
                ORDER BY q.activated_at_ms, q.sequence
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(str(row["request_id"]) for row in rows)

    def activate_request_run(
        self,
        request_id: str,
        session_scope_hash: str,
        *,
        gateway_epoch: int,
        owner_instance_id: str,
        activated_at_ms: int,
        lease_duration_ms: int = 30_000,
    ) -> ActiveRequestActivation:
        if (
            not request_id
            or len(session_scope_hash) != 64
            or any(char not in "0123456789abcdef" for char in session_scope_hash)
            or gateway_epoch < 1
            or not 1 <= len(owner_instance_id) <= 160
            or activated_at_ms < 0
            or not 1_000 <= lease_duration_ms <= 3_600_000
        ):
            raise ValueError("active request activation arguments are invalid")
        run = derive_run_identity(request_id, 1)
        generation = 1
        lease_id = "activation-" + canonical_sha256(
            {
                "domain": "tiangong.gateway.active-request-lease.v1",
                "request_id": request_id,
                "run_id": run.run_id,
                "run_sequence": run.run_sequence,
                "generation": generation,
                "gateway_epoch": gateway_epoch,
                "owner_instance_id": owner_instance_id,
                "session_scope_hash": session_scope_hash,
            }
        )
        with self._lock, self._write_transaction():
            journal_row = self._connection.execute(
                "SELECT * FROM request_journal WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            queue_row = self._connection.execute(
                "SELECT * FROM session_queue WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            actor_row = self._connection.execute(
                "SELECT * FROM session_actor WHERE session_scope_hash = ?",
                (session_scope_hash,),
            ).fetchone()
            inbound_row = self._connection.execute(
                "SELECT * FROM request_inbound_payload WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if journal_row is None or queue_row is None or actor_row is None:
                raise StoreNotFoundError("active request does not exist")
            if inbound_row is None:
                raise StoreCorruptionError("active request is missing its inbound payload marker")
            if inbound_row["availability"] != "AVAILABLE":
                raise StoreConflictError("active request inbound payload is unavailable")
            entry = _parse_journal_entry(
                journal_row["entry_json"],
                journal_row["entry_sha256"],
            )
            envelope = _parse_inbound_envelope(
                inbound_row["envelope_json"],
                inbound_row["envelope_sha256"],
            )
            queue = SessionQueueSnapshot(
                session_scope_hash=queue_row["session_scope_hash"],
                request_id=queue_row["request_id"],
                sequence=queue_row["sequence"],
                state=queue_row["state"],
                enqueued_at_ms=queue_row["enqueued_at_ms"],
                activated_at_ms=queue_row["activated_at_ms"],
                completed_at_ms=queue_row["completed_at_ms"],
            )
            if (
                entry.session_scope_hash != session_scope_hash
                or envelope.inbound_id != entry.inbound_id
                or envelope.idempotency_key != entry.idempotency_key
                or envelope.conversation_scope_hash != entry.session_scope_hash
                or queue.session_scope_hash != session_scope_hash
                or queue.state != "ACTIVE"
                or queue.activated_at_ms is None
                or queue.completed_at_ms is not None
                or actor_row["active_request_id"] != request_id
            ):
                raise StoreConflictError("only the current session request may be activated")
            if activated_at_ms < max(entry.created_at_ms, queue.activated_at_ms):
                raise ValueError("request activation predates durable session activation")

            generation_row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            snapshot_row = self._get_snapshot_row("request", request_id)
            if generation_row is not None:
                current = _generation_view(self._connection, generation_row)
                if (
                    current.status != "ACTIVE"
                    or current.run_id != run.run_id
                    or current.run_sequence != run.run_sequence
                    or current.generation != generation
                    or current.gateway_epoch != gateway_epoch
                    or current.lease_id != lease_id
                    or current.owner_instance_id != owner_instance_id
                ):
                    raise StoreConflictError("active request is already owned by another run")
                if activated_at_ms > current.fence.expires_at_ms:
                    raise StoreConflictError("active request generation lease has expired")
                if snapshot_row is None:
                    raise StoreCorruptionError("active request generation is missing request authority")
                request_snapshot = _parse_snapshot(
                    snapshot_row["snapshot_json"],
                    snapshot_row["snapshot_sha256"],
                )
                if (
                    request_snapshot.machine != "request"
                    or request_snapshot.entity_id != request_id
                    or request_snapshot.request_id != request_id
                    or request_snapshot.run_id != run.run_id
                    or request_snapshot.generation != generation
                ):
                    raise StoreCorruptionError("request authority disagrees with active generation")
                return ActiveRequestActivation(
                    entry=entry,
                    envelope=envelope,
                    queue=queue,
                    generation=current,
                    request_snapshot=request_snapshot,
                    created_by_this_call=False,
                    duplicate=True,
                )
            if snapshot_row is not None or self._connection.execute(
                "SELECT 1 FROM aggregate_state WHERE request_id = ? LIMIT 1",
                (request_id,),
            ).fetchone() is not None:
                raise StoreCorruptionError("unclaimed request already has aggregate state")

            fence = derive_generation_fence(
                gateway_epoch=gateway_epoch,
                request_id=request_id,
                run_id=run.run_id,
                run_sequence=run.run_sequence,
                generation=generation,
                lease_id=lease_id,
                issued_at_ms=activated_at_ms,
                expires_at_ms=activated_at_ms + lease_duration_ms,
            )
            request_snapshot = new_state_snapshot(
                "request",
                entity_id=request_id,
                request_id=request_id,
                run_id=run.run_id,
                generation=generation,
                created_at_ms=activated_at_ms,
            )
            self._connection.execute(
                """
                INSERT INTO request_generation(
                    request_id, run_id, run_sequence, current_generation, gateway_epoch,
                    active_lease_id, owner_instance_id, status, current_fence_id,
                    revision, updated_at_ms, cancel_reason_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, 1, ?, NULL)
                """,
                (
                    request_id,
                    run.run_id,
                    run.run_sequence,
                    generation,
                    gateway_epoch,
                    lease_id,
                    owner_instance_id,
                    fence.fence_id,
                    activated_at_ms,
                ),
            )
            fence_json, fence_digest = _fence_payload(fence)
            self._connection.execute(
                """
                INSERT INTO generation_fences(
                    fence_id, request_id, run_id, generation, gateway_epoch, lease_id,
                    state, fence_json, fence_sha256, recorded_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    fence.fence_id,
                    fence.request_id,
                    fence.run_id,
                    fence.generation,
                    fence.gateway_epoch,
                    fence.lease_id,
                    fence_json,
                    fence_digest,
                    activated_at_ms,
                ),
            )
            snapshot_json, snapshot_digest = _snapshot_payload(request_snapshot)
            self._connection.execute(
                """
                INSERT INTO aggregate_state(
                    machine, entity_id, request_id, run_id, generation, revision,
                    state, created_at_ms, updated_at_ms, last_event_id,
                    snapshot_json, snapshot_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_snapshot.machine,
                    request_snapshot.entity_id,
                    request_snapshot.request_id,
                    request_snapshot.run_id,
                    request_snapshot.generation,
                    request_snapshot.revision,
                    request_snapshot.state,
                    request_snapshot.created_at_ms,
                    request_snapshot.updated_at_ms,
                    request_snapshot.last_event_id,
                    snapshot_json,
                    snapshot_digest,
                ),
            )
            current_row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return ActiveRequestActivation(
                entry=entry,
                envelope=envelope,
                queue=queue,
                generation=_generation_view(self._connection, current_row),
                request_snapshot=request_snapshot,
                created_by_this_call=True,
                duplicate=False,
            )

    def recover_expired_active_request(
        self,
        *,
        gateway_epoch: int,
        owner_instance_id: str,
        recovered_at_ms: int,
        lease_duration_ms: int = 30_000,
    ) -> ActiveRequestActivation | None:
        if (
            gateway_epoch < 1
            or not 1 <= len(owner_instance_id) <= 160
            or recovered_at_ms < 0
            or not 1_000 <= lease_duration_ms <= 3_600_000
        ):
            raise ValueError("active request recovery arguments are invalid")
        with self._lock, self._write_transaction():
            candidate = self._connection.execute(
                """
                SELECT g.request_id
                FROM request_generation AS g
                JOIN generation_fences AS f ON f.fence_id = g.current_fence_id
                JOIN session_queue AS q ON q.request_id = g.request_id
                JOIN request_inbound_payload AS i ON i.request_id = g.request_id
                JOIN aggregate_state AS s
                  ON s.machine = 'request' AND s.entity_id = g.request_id
                WHERE g.status = 'ACTIVE'
                  AND g.gateway_epoch < ?
                  AND f.state = 'ACTIVE'
                  AND json_extract(f.fence_json, '$.expires_at_ms') <= ?
                  AND q.state = 'ACTIVE'
                  AND i.availability = 'AVAILABLE'
                  AND s.state NOT IN ('COMPLETED','PARTIAL','FAILED','CANCELLED','SUPERSEDED')
                ORDER BY q.activated_at_ms, q.session_scope_hash, q.sequence
                LIMIT 1
                """,
                (gateway_epoch, recovered_at_ms),
            ).fetchone()
            if candidate is None:
                return None
            request_id = candidate["request_id"]
            generation_row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            current = _generation_view(self._connection, generation_row)
            if (
                current.status != "ACTIVE"
                or current.gateway_epoch >= gateway_epoch
                or current.fence.expires_at_ms > recovered_at_ms
                or current.lease_id is None
            ):
                raise StoreConflictError("active request is not recoverable")
            journal_row = self._connection.execute(
                "SELECT * FROM request_journal WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            inbound_row = self._connection.execute(
                "SELECT * FROM request_inbound_payload WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            queue_row = self._connection.execute(
                "SELECT * FROM session_queue WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            snapshot_row = self._get_snapshot_row("request", request_id)
            if any(item is None for item in (journal_row, inbound_row, queue_row, snapshot_row)):
                raise StoreCorruptionError("recoverable request is incomplete")
            entry = _parse_journal_entry(journal_row["entry_json"], journal_row["entry_sha256"])
            envelope = _parse_inbound_envelope(
                inbound_row["envelope_json"],
                inbound_row["envelope_sha256"],
            )
            request_snapshot = _parse_snapshot(
                snapshot_row["snapshot_json"],
                snapshot_row["snapshot_sha256"],
            )
            queue = SessionQueueSnapshot(
                session_scope_hash=queue_row["session_scope_hash"],
                request_id=queue_row["request_id"],
                sequence=queue_row["sequence"],
                state=queue_row["state"],
                enqueued_at_ms=queue_row["enqueued_at_ms"],
                activated_at_ms=queue_row["activated_at_ms"],
                completed_at_ms=queue_row["completed_at_ms"],
            )
            if (
                entry.request_id != request_id
                or envelope.inbound_id != entry.inbound_id
                or envelope.idempotency_key != entry.idempotency_key
                or queue.state != "ACTIVE"
                or queue.session_scope_hash != entry.session_scope_hash
                or request_snapshot.request_id != request_id
                or request_snapshot.run_id != current.run_id
                or request_snapshot.generation != current.generation
                or request_snapshot.is_terminal
            ):
                raise StoreCorruptionError("recoverable request scope is inconsistent")
            lease_id = "recovery-" + canonical_sha256(
                {
                    "gateway_epoch": gateway_epoch,
                    "generation": current.generation,
                    "owner_instance_id": owner_instance_id,
                    "previous_fence_id": current.fence.fence_id,
                    "request_id": request_id,
                    "run_id": current.run_id,
                }
            )
            fence = derive_generation_fence(
                gateway_epoch=gateway_epoch,
                request_id=request_id,
                run_id=current.run_id,
                run_sequence=current.run_sequence,
                generation=current.generation,
                lease_id=lease_id,
                issued_at_ms=recovered_at_ms,
                expires_at_ms=recovered_at_ms + lease_duration_ms,
                supersedes_fence_id=current.fence.fence_id,
            )
            old_update = self._connection.execute(
                "UPDATE generation_fences SET state = 'SUPERSEDED' WHERE fence_id = ? AND state = 'ACTIVE'",
                (current.fence.fence_id,),
            )
            fence_json, fence_digest = _fence_payload(fence)
            self._connection.execute(
                """
                INSERT INTO generation_fences(
                    fence_id, request_id, run_id, generation, gateway_epoch, lease_id,
                    state, fence_json, fence_sha256, recorded_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    fence.fence_id,
                    request_id,
                    current.run_id,
                    current.generation,
                    gateway_epoch,
                    lease_id,
                    fence_json,
                    fence_digest,
                    recovered_at_ms,
                ),
            )
            generation_update = self._connection.execute(
                """
                UPDATE request_generation
                SET gateway_epoch = ?, active_lease_id = ?, owner_instance_id = ?,
                    current_fence_id = ?, revision = revision + 1, updated_at_ms = ?
                WHERE request_id = ? AND current_fence_id = ? AND status = 'ACTIVE'
                """,
                (
                    gateway_epoch,
                    lease_id,
                    owner_instance_id,
                    fence.fence_id,
                    recovered_at_ms,
                    request_id,
                    current.fence.fence_id,
                ),
            )
            if old_update.rowcount != 1 or generation_update.rowcount != 1:
                raise StoreCasConflict("active request changed before recovery")
            recovered_row = self._connection.execute(
                "SELECT * FROM request_generation WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return ActiveRequestActivation(
                entry=entry,
                envelope=envelope,
                queue=queue,
                generation=_generation_view(self._connection, recovered_row),
                request_snapshot=request_snapshot,
                created_by_this_call=True,
                duplicate=False,
            )

    def complete_session_request(
        self,
        session_scope_hash: str,
        request_id: str,
        *,
        completed_at_ms: int,
        release_generation: bool = False,
    ) -> SessionQueueSnapshot | None:
        if completed_at_ms < 0 or type(release_generation) is not bool:
            raise ValueError("session completion time is invalid")
        with self._lock, self._write_transaction():
            actor = self._connection.execute(
                "SELECT * FROM session_actor WHERE session_scope_hash = ?",
                (session_scope_hash,),
            ).fetchone()
            row = self._connection.execute(
                "SELECT * FROM session_queue WHERE session_scope_hash = ? AND request_id = ?",
                (session_scope_hash, request_id),
            ).fetchone()
            if actor is None or row is None:
                raise StoreNotFoundError("session request does not exist")
            if row["state"] == "QUEUED":
                raise StoreConflictError("queued request cannot complete before activation")
            if row["state"] == "COMPLETED":
                if release_generation:
                    self._release_generation_locked(request_id, released_at_ms=completed_at_ms)
                next_row = self._connection.execute(
                    "SELECT * FROM session_queue WHERE request_id = ?",
                    (actor["active_request_id"],),
                ).fetchone() if actor["active_request_id"] is not None else None
                return None if next_row is None else SessionQueueSnapshot(
                    next_row["session_scope_hash"], next_row["request_id"], next_row["sequence"],
                    next_row["state"], next_row["enqueued_at_ms"], next_row["activated_at_ms"],
                    next_row["completed_at_ms"],
                )
            if actor["active_request_id"] != request_id:
                raise StoreCorruptionError("active session request disagrees with actor")
            if completed_at_ms < row["activated_at_ms"]:
                raise ValueError("session completion predates activation")
            updated = self._connection.execute(
                """
                UPDATE session_queue SET state = 'COMPLETED', completed_at_ms = ?
                WHERE session_scope_hash = ? AND request_id = ? AND state = 'ACTIVE'
                """,
                (completed_at_ms, session_scope_hash, request_id),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("session request changed before completion")
            next_row = self._connection.execute(
                """
                SELECT * FROM session_queue
                WHERE session_scope_hash = ? AND state = 'QUEUED'
                ORDER BY sequence LIMIT 1
                """,
                (session_scope_hash,),
            ).fetchone()
            next_request_id = None
            if next_row is not None:
                next_request_id = next_row["request_id"]
                activated = self._connection.execute(
                    """
                    UPDATE session_queue SET state = 'ACTIVE', activated_at_ms = ?
                    WHERE session_scope_hash = ? AND request_id = ? AND state = 'QUEUED'
                    """,
                    (completed_at_ms, session_scope_hash, next_request_id),
                )
                if activated.rowcount != 1:
                    raise StoreCasConflict("next session request changed before activation")
            actor_updated = self._connection.execute(
                """
                UPDATE session_actor
                SET active_request_id = ?, revision = ?, updated_at_ms = ?
                WHERE session_scope_hash = ? AND revision = ? AND active_request_id = ?
                """,
                (
                    next_request_id,
                    int(actor["revision"]) + 1,
                    completed_at_ms,
                    session_scope_hash,
                    actor["revision"],
                    request_id,
                ),
            )
            if actor_updated.rowcount != 1:
                raise StoreCasConflict("session actor changed before completion")
            if release_generation:
                self._release_generation_locked(request_id, released_at_ms=completed_at_ms)
            if next_request_id is None:
                return None
            return SessionQueueSnapshot(
                session_scope_hash,
                next_request_id,
                next_row["sequence"],
                "ACTIVE",
                next_row["enqueued_at_ms"],
                completed_at_ms,
                None,
            )

    def record_completion_decision(
        self,
        decision: CompletionDecision,
        *,
        recorded_at_ms: int,
    ) -> CompletionDecisionRecord:
        if recorded_at_ms < 0 or not decision.has_valid_sha256():
            raise ValueError("completion decision persistence fact is invalid")
        payload = _completion_decision_payload(decision)
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=decision.request_id,
                run_id=decision.run_id,
                generation=decision.generation,
                recorded_at_ms=recorded_at_ms,
            )
            existing = self._connection.execute(
                "SELECT * FROM completion_decisions WHERE decision_sha256 = ?",
                (decision.decision_sha256,),
            ).fetchone()
            if existing is not None:
                stored = _parse_completion_decision(
                    existing["decision_json"], existing["decision_sha256"]
                )
                if stored != decision:
                    raise StoreConflictError("completion decision identity was reused")
                return CompletionDecisionRecord(
                    stored,
                    existing["recorded_at_ms"],
                    False,
                    True,
                )
            self._connection.execute(
                """
                INSERT INTO completion_decisions(
                    decision_sha256, request_id, run_id, generation, outcome,
                    needs_reconciliation, recorded_at_ms, decision_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_sha256,
                    decision.request_id,
                    decision.run_id,
                    decision.generation,
                    decision.outcome,
                    int(decision.needs_reconciliation),
                    recorded_at_ms,
                    payload,
                ),
            )
            return CompletionDecisionRecord(decision, recorded_at_ms, True, False)

    def list_completion_decisions(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[CompletionDecisionRecord, ...]:
        if not request_id or not run_id or generation < 0:
            raise ValueError("completion decision query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM completion_decisions
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY recorded_at_ms, decision_sha256
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(
                CompletionDecisionRecord(
                    _parse_completion_decision(
                        row["decision_json"], row["decision_sha256"]
                    ),
                    row["recorded_at_ms"],
                    False,
                    False,
                )
                for row in rows
            )

    def record_skill_selection(
        self,
        record: SkillSelectionRecord,
    ) -> SkillSelectionRegistration:
        payload, record_sha256 = _skill_selection_payload(record)
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=record.request_id,
                run_id=record.run_id,
                generation=record.generation,
                recorded_at_ms=record.decided_at_ms,
            )
            existing = self._connection.execute(
                "SELECT * FROM skill_selections WHERE selection_id = ? OR record_sha256 = ?",
                (record.selection_id, record_sha256),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise StoreCorruptionError("Skill selection identities diverged")
                stored = _skill_selection_from_row(existing[0], duplicate=True)
                if stored.record != record:
                    raise StoreConflictError("Skill selection identity was reused")
                return stored
            self._connection.execute(
                """
                INSERT INTO skill_selections(
                    selection_id, request_id, run_id, generation, origin, operation,
                    skill_catalog_hash, capability_manifest_hash, decided_at_ms,
                    record_json, record_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.selection_id,
                    record.request_id,
                    record.run_id,
                    record.generation,
                    record.origin,
                    record.operation,
                    record.skill_catalog_hash,
                    record.capability_manifest_hash,
                    record.decided_at_ms,
                    payload,
                    record_sha256,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM skill_selections WHERE selection_id = ?",
                (record.selection_id,),
            ).fetchone()
            return _skill_selection_from_row(row, created_by_this_call=True)

    def get_skill_selection(self, selection_id: str) -> SkillSelectionRegistration | None:
        if not selection_id:
            raise ValueError("Skill selection identity is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM skill_selections WHERE selection_id = ?",
                (selection_id,),
            ).fetchone()
            return None if row is None else _skill_selection_from_row(row)

    def list_skill_selections(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[SkillSelectionRegistration, ...]:
        if not request_id or not run_id or generation < 0:
            raise ValueError("Skill selection query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM skill_selections
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY decided_at_ms, selection_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(_skill_selection_from_row(row) for row in rows)

    def record_skill_activation(
        self,
        grant: SkillActivationGrant,
    ) -> SkillActivationRegistration:
        if not grant.has_valid_sha256():
            raise ValueError("Skill activation digest is invalid")
        payload = _skill_activation_payload(grant)
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=grant.request_id,
                run_id=grant.run_id,
                generation=grant.generation,
                recorded_at_ms=grant.issued_at_ms,
            )
            selection_row = self._connection.execute(
                "SELECT * FROM skill_selections WHERE selection_id = ?",
                (grant.selection_id,),
            ).fetchone()
            if selection_row is None:
                raise StoreNotFoundError("Skill activation selection does not exist")
            selection = _skill_selection_from_row(selection_row).record
            if (
                selection.request_id != grant.request_id
                or selection.run_id != grant.run_id
                or selection.generation != grant.generation
                or selection.decision != "activate"
                or selection.activation_state != "active"
                or selection.skill_catalog_hash != grant.skill_catalog_hash
                or selection.capability_manifest_hash != grant.capability_manifest_hash
                or selection.selected_skill_id != grant.skill_id
                or selection.selected_skill_version != grant.skill_version
                or selection.selected_skill_sha256 != grant.skill_sha256
            ):
                raise StoreConflictError("Skill activation crossed its selection authority")
            selected = next(
                (item for item in selection.candidates if item.skill_id == grant.skill_id),
                None,
            )
            if (
                selected is None
                or not selected.compatible
                or selected.required_actions != grant.allowed_action_ids
            ):
                raise StoreConflictError("Skill activation actions do not match the compatible candidate")
            existing = self._connection.execute(
                """
                SELECT * FROM skill_activations
                WHERE activation_id = ? OR selection_id = ? OR activation_sha256 = ?
                """,
                (grant.activation_id, grant.selection_id, grant.activation_sha256),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise StoreCorruptionError("Skill activation identities diverged")
                stored = _skill_activation_from_row(existing[0], duplicate=True)
                if stored.grant != grant:
                    raise StoreConflictError("Skill activation identity was reused")
                return stored
            self._connection.execute(
                """
                INSERT INTO skill_activations(
                    activation_id, selection_id, request_id, run_id, generation,
                    principal_scope_hash, skill_catalog_hash, capability_manifest_hash,
                    skill_id, skill_version, skill_sha256, issued_at_ms, expires_at_ms,
                    grant_json, activation_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.activation_id,
                    grant.selection_id,
                    grant.request_id,
                    grant.run_id,
                    grant.generation,
                    grant.principal_scope_hash,
                    grant.skill_catalog_hash,
                    grant.capability_manifest_hash,
                    grant.skill_id,
                    grant.skill_version,
                    grant.skill_sha256,
                    grant.issued_at_ms,
                    grant.expires_at_ms,
                    payload,
                    grant.activation_sha256,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM skill_activations WHERE activation_id = ?",
                (grant.activation_id,),
            ).fetchone()
            return _skill_activation_from_row(row, created_by_this_call=True)

    def get_skill_activation(self, activation_id: str) -> SkillActivationRegistration | None:
        if not activation_id:
            raise ValueError("Skill activation identity is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM skill_activations WHERE activation_id = ?",
                (activation_id,),
            ).fetchone()
            return None if row is None else _skill_activation_from_row(row)

    def get_skill_activation_by_sha256(
        self, activation_sha256: str
    ) -> SkillActivationRegistration | None:
        if len(activation_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in activation_sha256
        ):
            raise ValueError("Skill activation digest is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM skill_activations WHERE activation_sha256 = ?",
                (activation_sha256,),
            ).fetchone()
            return None if row is None else _skill_activation_from_row(row)

    def bind_skill_activation_ticket(
        self,
        activation_id: str,
        ticket: ExecutionTicket,
        *,
        bound_at_ms: int,
    ) -> SkillActivationTicketBinding:
        if not activation_id or bound_at_ms < 0:
            raise ValueError("Skill activation ticket binding is invalid")
        ticket_value = ticket.model_dump(mode="json")
        ticket_sha256 = canonical_sha256(ticket_value)
        payload = ticket.payload
        binding_sha256 = canonical_sha256(
            {
                "activation_id": activation_id,
                "bound_at_ms": bound_at_ms,
                "domain": "tiangong.gateway.skill-activation-ticket.v1",
                "ticket_id": payload.ticket_id,
                "ticket_sha256": ticket_sha256,
            }
        )
        with self._lock, self._write_transaction():
            row = self._connection.execute(
                "SELECT * FROM skill_activations WHERE activation_id = ?",
                (activation_id,),
            ).fetchone()
            if row is None:
                raise StoreNotFoundError("Skill activation does not exist")
            grant = _skill_activation_from_row(row).grant
            if (
                payload.request_id != grant.request_id
                or payload.run_id != grant.run_id
                or payload.generation != grant.generation
                or payload.principal_scope_hash != grant.principal_scope_hash
                or payload.capability_manifest_hash != grant.capability_manifest_hash
                or payload.skill_id != grant.skill_id
                or payload.skill_version != grant.skill_version
                or payload.skill_sha256 != grant.skill_sha256
                or payload.skill_activation_id != grant.activation_id
                or payload.skill_activation_sha256 != grant.activation_sha256
                or payload.action_id not in grant.allowed_action_ids
                or not grant.issued_at_ms <= bound_at_ms <= grant.expires_at_ms
                or not payload.issued_at_ms <= bound_at_ms <= payload.expires_at_ms
            ):
                raise StoreConflictError("execution ticket crossed its Skill activation")
            existing = self._connection.execute(
                """
                SELECT * FROM skill_activation_tickets
                WHERE ticket_id = ? OR binding_sha256 = ?
                """,
                (payload.ticket_id, binding_sha256),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise StoreCorruptionError("Skill ticket binding identities diverged")
                item = existing[0]
                if (
                    item["activation_id"] != activation_id
                    or item["ticket_sha256"] != ticket_sha256
                    or item["bound_at_ms"] != bound_at_ms
                    or item["binding_sha256"] != binding_sha256
                ):
                    raise StoreConflictError("Skill ticket binding identity was reused")
                return SkillActivationTicketBinding(
                    activation_id, payload.ticket_id, ticket_sha256, bound_at_ms,
                    binding_sha256, False, True,
                )
            self._connection.execute(
                """
                INSERT INTO skill_activation_tickets(
                    activation_id, ticket_id, ticket_sha256, bound_at_ms, binding_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (activation_id, payload.ticket_id, ticket_sha256, bound_at_ms, binding_sha256),
            )
            return SkillActivationTicketBinding(
                activation_id, payload.ticket_id, ticket_sha256, bound_at_ms,
                binding_sha256, True, False,
            )

    def put_request_capsule(
        self,
        capsule: TaskContinuityCapsule,
    ) -> RequestCapsuleRecord:
        if not capsule.has_valid_capsule_sha256():
            raise ValueError("continuity capsule digest is invalid")
        payload = _capsule_payload(capsule)
        terminal = capsule.capsule_kind == "TERMINAL_RESULT"
        status = "TERMINAL" if terminal else "ACTIVE"
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=capsule.request_id,
                run_id=capsule.run_id,
                generation=capsule.generation,
                recorded_at_ms=capsule.created_at_ms,
            )
            existing = self._connection.execute(
                """
                SELECT * FROM request_capsules
                WHERE capsule_id = ? OR capsule_sha256 = ?
                """,
                (capsule.capsule_id, capsule.capsule_sha256),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise StoreCorruptionError("continuity capsule identities diverged")
                record = _capsule_record_from_row(existing[0], duplicate=True)
                if record.capsule != capsule:
                    raise StoreConflictError("continuity capsule identity was reused")
                return record
            prior_terminal = self._connection.execute(
                """
                SELECT capsule_id FROM request_capsules
                WHERE request_id = ? AND run_id = ? AND generation = ?
                  AND status = 'TERMINAL'
                """,
                (capsule.request_id, capsule.run_id, capsule.generation),
            ).fetchone()
            if prior_terminal is not None:
                raise StoreConflictError("terminal continuity capsule already exists")
            active = self._connection.execute(
                """
                SELECT * FROM request_capsules
                WHERE request_id = ? AND run_id = ? AND generation = ?
                  AND status = 'ACTIVE'
                """,
                (capsule.request_id, capsule.run_id, capsule.generation),
            ).fetchone()
            expected_supersedes = None if active is None else active["capsule_id"]
            if capsule.supersedes_capsule_id != expected_supersedes:
                raise StoreConflictError(
                    "continuity capsule does not extend the active recovery chain"
                )
            if active is not None:
                if active["life_id"] != capsule.life_id:
                    raise StoreConflictError("continuity capsule crossed a life identity")
                if capsule.created_at_ms < active["created_at_ms"]:
                    raise StoreConflictError("continuity capsule time moved backwards")
                updated = self._connection.execute(
                    """
                    UPDATE request_capsules SET status = 'SUPERSEDED'
                    WHERE capsule_id = ? AND status = 'ACTIVE'
                    """,
                    (active["capsule_id"],),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("active continuity capsule changed")
            self._connection.execute(
                """
                INSERT INTO request_capsules(
                    capsule_id, request_id, run_id, generation, life_id,
                    capsule_kind, status, supersedes_capsule_id, created_at_ms,
                    capsule_sha256, capsule_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capsule.capsule_id,
                    capsule.request_id,
                    capsule.run_id,
                    capsule.generation,
                    capsule.life_id,
                    capsule.capsule_kind,
                    status,
                    capsule.supersedes_capsule_id,
                    capsule.created_at_ms,
                    capsule.capsule_sha256,
                    payload,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM request_capsules WHERE capsule_id = ?",
                (capsule.capsule_id,),
            ).fetchone()
            return _capsule_record_from_row(row, created_by_this_call=True)

    def get_active_request_capsule(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> RequestCapsuleRecord | None:
        return self._get_request_capsule_by_status(
            request_id,
            run_id=run_id,
            generation=generation,
            status="ACTIVE",
        )

    def get_terminal_request_capsule(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> RequestCapsuleRecord | None:
        return self._get_request_capsule_by_status(
            request_id,
            run_id=run_id,
            generation=generation,
            status="TERMINAL",
        )

    def _get_request_capsule_by_status(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
        status: Literal["ACTIVE", "TERMINAL"],
    ) -> RequestCapsuleRecord | None:
        if not request_id or not run_id or generation < 0:
            raise ValueError("continuity capsule query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                """
                SELECT * FROM request_capsules
                WHERE request_id = ? AND run_id = ? AND generation = ? AND status = ?
                """,
                (request_id, run_id, generation, status),
            ).fetchone()
            return None if row is None else _capsule_record_from_row(row)

    def list_request_capsules(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[RequestCapsuleRecord, ...]:
        if not request_id or not run_id or generation < 0:
            raise ValueError("continuity capsule query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM request_capsules
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY created_at_ms, capsule_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(_capsule_record_from_row(row) for row in rows)

    def record_object_owner(
        self,
        *,
        object_id: str,
        object_sha256: str,
        owner_kind: Literal[
            "REQUEST", "OUTBOX", "COMPLETION", "CAPSULE", "ARTIFACT", "LIFE_EVENT"
        ],
        owner_id: str,
        request_id: str,
        run_id: str,
        generation: int,
        created_at_ms: int,
    ) -> ObjectOwnerRecord:
        if (
            not object_id
            or len(object_id) > 160
            or not owner_id
            or len(owner_id) > 160
            or len(object_sha256) != 64
            or any(char not in "0123456789abcdef" for char in object_sha256)
            or created_at_ms < 0
        ):
            raise ValueError("object ownership fact is invalid")
        ownership_sha256 = _object_ownership_sha256(
            object_id=object_id,
            object_sha256=object_sha256,
            owner_kind=owner_kind,
            owner_id=owner_id,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            created_at_ms=created_at_ms,
        )
        with self._lock, self._write_transaction():
            self._assert_known_request_binding_locked(
                request_id=request_id,
                run_id=run_id,
                generation=generation,
                recorded_at_ms=created_at_ms,
            )
            existing = self._connection.execute(
                """
                SELECT * FROM object_owners
                WHERE (object_id = ? AND owner_kind = ? AND owner_id = ?)
                   OR ownership_sha256 = ?
                """,
                (object_id, owner_kind, owner_id, ownership_sha256),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise StoreCorruptionError("object ownership identities diverged")
                record = _object_owner_from_row(existing[0], duplicate=True)
                if record.ownership_sha256 != ownership_sha256:
                    raise StoreConflictError("object ownership identity was reused")
                return record
            if owner_kind == "OUTBOX":
                owner = self._connection.execute(
                    "SELECT payload_object_id, payload_sha256 FROM outbox WHERE outbox_id = ?",
                    (owner_id,),
                ).fetchone()
                if owner is None or (owner[0], owner[1]) != (object_id, object_sha256):
                    raise StoreConflictError("outbox object ownership binding is invalid")
            elif owner_kind == "CAPSULE":
                if self._connection.execute(
                    "SELECT 1 FROM request_capsules WHERE capsule_id = ?",
                    (owner_id,),
                ).fetchone() is None:
                    raise StoreNotFoundError("capsule object owner does not exist")
            elif owner_kind == "COMPLETION":
                if self._connection.execute(
                    "SELECT 1 FROM completion_decisions WHERE decision_sha256 = ?",
                    (owner_id,),
                ).fetchone() is None:
                    raise StoreNotFoundError("completion object owner does not exist")
            self._connection.execute(
                """
                INSERT INTO object_owners(
                    object_id, object_sha256, owner_kind, owner_id,
                    request_id, run_id, generation, created_at_ms, ownership_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    object_id,
                    object_sha256,
                    owner_kind,
                    owner_id,
                    request_id,
                    run_id,
                    generation,
                    created_at_ms,
                    ownership_sha256,
                ),
            )
            row = self._connection.execute(
                """
                SELECT * FROM object_owners
                WHERE object_id = ? AND owner_kind = ? AND owner_id = ?
                """,
                (object_id, owner_kind, owner_id),
            ).fetchone()
            return _object_owner_from_row(row, created_by_this_call=True)

    def list_object_owners(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[ObjectOwnerRecord, ...]:
        if not request_id or not run_id or generation < 0:
            raise ValueError("object ownership query is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM object_owners
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY created_at_ms, object_id, owner_kind, owner_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(_object_owner_from_row(row) for row in rows)

    def list_all_object_owners(self) -> tuple[ObjectOwnerRecord, ...]:
        """Verified global owner view used only by conservative GC dry-runs."""

        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM object_owners
                ORDER BY object_id, owner_kind, owner_id
                """
            ).fetchall()
            return tuple(_object_owner_from_row(row) for row in rows)

    def count_journal_entries(self) -> int:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            return int(self._connection.execute("SELECT count(*) FROM request_journal").fetchone()[0])

    def count_events(self) -> int:
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            return int(self._connection.execute("SELECT count(*) FROM event_log").fetchone()[0])

    def health_check(self, *, now_ms: int, full: bool = False) -> StoreHealthEvidence:
        if now_ms < 0:
            raise ValueError("store health time is invalid")
        with self._lock:
            if self._closed:
                return StoreHealthEvidence(
                    False,
                    "store.closed",
                    now_ms,
                    0,
                    None,
                    None,
                    False,
                )
            try:
                check = "integrity_check" if full else "quick_check"
                rows = self._connection.execute(f"PRAGMA {check}").fetchall()
                if [row[0] for row in rows] != ["ok"]:
                    return StoreHealthEvidence(
                        False,
                        "store.integrity.failed",
                        now_ms,
                        int(self._connection.execute("PRAGMA user_version").fetchone()[0]),
                        None,
                        None,
                        False,
                    )
                if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    return StoreHealthEvidence(
                        False,
                        "store.foreign_key.failed",
                        now_ms,
                        STORE_SCHEMA_VERSION,
                        None,
                        None,
                        False,
                    )
                _validate_metadata(self._connection)
                _verify_current_state_rows(self._connection)
                _verify_request_journal_rows(self._connection)
                _verify_outbox_rows(self._connection)
                _verify_outbox_dispatch_boundaries(self._connection)
                _verify_life_continuity_rows(self._connection)
                _verify_effect_rows(self._connection)
                _verify_nonce_rows(self._connection)
                _verify_generation_rows(self._connection)
                _verify_coordination_rows(self._connection)
                _verify_shadow_rows(self._connection)
                _verify_channel_cutover_rows(self._connection)
                if full:
                    _verify_full_event_chain(self._connection)
                schema_sha256 = _schema_fingerprint(self._connection)
                journal = str(self._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                foreign_keys = int(self._connection.execute("PRAGMA foreign_keys").fetchone()[0])
                synchronous = int(self._connection.execute("PRAGMA synchronous").fetchone()[0])
                trusted_schema = int(self._connection.execute("PRAGMA trusted_schema").fetchone()[0])
                if journal != "wal" or foreign_keys != 1 or synchronous < 2 or trusted_schema != 0:
                    return StoreHealthEvidence(
                        False,
                        "store.pragma.invalid",
                        now_ms,
                        STORE_SCHEMA_VERSION,
                        schema_sha256,
                        journal,
                        False,
                    )
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute(
                        """
                        UPDATE schema_migrations
                        SET applied_at_ms = applied_at_ms
                        WHERE version = ?
                        """,
                        (STORE_SCHEMA_VERSION,),
                    )
                finally:
                    self._connection.execute("ROLLBACK")
                return StoreHealthEvidence(
                    True,
                    "store.ok",
                    now_ms,
                    STORE_SCHEMA_VERSION,
                    schema_sha256,
                    journal,
                    True,
                )
            except (sqlite3.DatabaseError, StoreError, OSError):
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return StoreHealthEvidence(
                    False,
                    "store.check.failed",
                    now_ms,
                    0,
                    None,
                    None,
                    False,
                )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "GatewayStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "APPLICATION_ID",
    "ActiveRequestActivation",
    "ActiveRequestCandidate",
    "CHANNEL_LEASE_CLOCK_SKEW_MS",
    "ChannelOwnershipRegistration",
    "CompletionDecisionRecord",
    "CoordinationRecord",
    "GatewayStateStore",
    "EffectLedgerRecord",
    "FencedResultDecision",
    "GenerationLeaseView",
    "JournalRegistration",
    "NonceConsumption",
    "OutboxDispatchBoundary",
    "OutboxRecord",
    "ObjectOwnerRecord",
    "RequestCapsuleRecord",
    "RequestJournalEntry",
    "SessionQueueSnapshot",
    "ShadowBatchRegistration",
    "SkillActivationRegistration",
    "SkillActivationTicketBinding",
    "SkillSelectionRegistration",
    "STORE_SCHEMA_VERSION",
    "StoreApplyResult",
    "StoreCasConflict",
    "StoreConflictError",
    "StoreCorruptionError",
    "StoreError",
    "StoreHealthEvidence",
    "StoreMigrationError",
    "StoreNotFoundError",
    "expected_store_schema_sha256",
]
