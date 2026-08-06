"""Strict shadow-only SQLite store for P1 life contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from contracts import (
    ActionCandidate,
    ActionImpact,
    AffectIntakeReceipt,
    AffectSignal,
    AffectSourcePolicySnapshot,
    AffectiveStateV3,
    AgencyDecision,
    AppraisalVectorV3,
    AutonomyPolicySnapshot,
    AutonomyUsageSnapshot,
    CapabilityProfile,
    CapabilityEvidence,
    CapabilityLearningDecision,
    CapabilityRollbackRecord,
    CausalEpisode,
    CausalEpisodeVNext,
    CausalHypothesis,
    CausalContextPack,
    CausalNodeV3,
    EpisodeOutcomeEvidence,
    LifeEventEnvelope,
    LifeEventIngress,
    LifeEventIngressReceipt,
    LifeContextAuthorization,
    LifeAuthorityHead,
    LifeTurnCommit,
    LifeRevisionVector,
    RootContinuationBinding,
    RootExperienceHead,
    RunLifeBinding,
    MemoryAssertionV3,
    MemoryRelationV3,
    PrivacyDeletionTombstone,
    ReflectionCard,
    ReflectionQuestionDecision,
    TaskContinuityCapsule,
    ViabilityState,
    ViabilityObservation,
    canonical_json_bytes,
    canonical_sha256,
    retention_priority,
)

from .replay import LifeReplaySummary, advance_replay_sha256, replay_life_events


SHADOW_STORE_SCHEMA_VERSION = 13
SHADOW_STORE_APPLICATION_ID = 0x54474C53

_LIFE_TURN_STAGE_PRECEDENCE = {
    "OUTCOME_COMMITTED_RESPONSE_OPEN": None,
    "RESPONSE_COMMITTED": "OUTCOME_COMMITTED_RESPONSE_OPEN",
    "DELIVERY_OBSERVED": "RESPONSE_COMMITTED",
    "ROOT_TERMINAL": "DELIVERY_OBSERVED",
}
_LIFE_TURN_STAGE_ORDER = {
    "OUTCOME_COMMITTED_RESPONSE_OPEN": 0,
    "RESPONSE_COMMITTED": 1,
    "DELIVERY_OBSERVED": 2,
    "ROOT_TERMINAL": 3,
}
_CAPABILITY_PHASE_TRANSITIONS = {
    ("DRAFT", "COMPILED"),
    ("COMPILED", "EXECUTION_TESTED"),
    ("EXECUTION_TESTED", "QC_PASSED"),
    ("QC_PASSED", "SHADOW"),
    ("SHADOW", "CURRENT"),
    ("SHADOW", "RETIRED"),
    ("QC_PASSED", "RETIRED"),
    ("COMPILED", "RETIRED"),
    ("CURRENT", "RETIRED"),
}

_SCHEMA_SQL = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK(version >= 1),
    migration_id TEXT NOT NULL UNIQUE,
    sql_sha256 TEXT NOT NULL CHECK(length(sql_sha256) = 64),
    applied_at_ms INTEGER NOT NULL CHECK(applied_at_ms >= 0)
) STRICT;

CREATE TABLE schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE life_events (
    event_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    writer_epoch INTEGER NOT NULL CHECK(writer_epoch >= 1),
    event_kind TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL CHECK(observed_at_ms >= 0),
    previous_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE CHECK(length(event_hash) = 64),
    envelope BLOB NOT NULL,
    UNIQUE(life_id, sequence)
) STRICT;

CREATE INDEX life_events_life_observed_idx
ON life_events(life_id, observed_at_ms, sequence);

CREATE TABLE event_evidence (
    evidence_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES life_events(event_id) ON DELETE RESTRICT,
    evidence_class TEXT NOT NULL,
    payload_object_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE causal_nodes (
    node_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    node_kind TEXT NOT NULL,
    source_event_id TEXT REFERENCES life_events(event_id) ON DELETE RESTRICT,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE causal_edge_versions (
    hypothesis_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    life_id TEXT NOT NULL,
    cause_ref TEXT NOT NULL,
    effect_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(hypothesis_id, revision)
) STRICT;

CREATE INDEX causal_edge_lookup_idx
ON causal_edge_versions(life_id, cause_ref, effect_ref, status);

CREATE TABLE causal_episodes (
    episode_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    life_id TEXT NOT NULL,
    terminal_status TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    closed_at_ms INTEGER,
    PRIMARY KEY(episode_id, revision)
) STRICT;

CREATE TABLE viability_snapshots (
    life_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(life_id, revision)
) STRICT;

CREATE TABLE appraisal_events (
    appraisal_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    appraised_at_ms INTEGER NOT NULL CHECK(appraised_at_ms >= 0)
) STRICT;

CREATE TABLE affect_snapshots (
    life_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(life_id, revision)
) STRICT;

CREATE TABLE memory_assertions (
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    life_id TEXT NOT NULL,
    status TEXT NOT NULL,
    privacy_scope TEXT NOT NULL,
    payload_object_id TEXT,
    payload_sha256 TEXT CHECK(payload_sha256 IS NULL OR length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(memory_id, revision)
) STRICT;

CREATE TABLE memory_relations (
    relation_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    source_memory_id TEXT NOT NULL,
    relation_kind TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE agency_decisions (
    decision_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE action_impacts (
    impact_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE authorization_refs (
    authorization_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES agency_decisions(decision_id) ON DELETE RESTRICT,
    ticket_sha256 TEXT NOT NULL CHECK(length(ticket_sha256) = 64),
    status TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE reflection_cards (
    reflection_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE capability_profiles (
    capability_id TEXT NOT NULL,
    version TEXT NOT NULL,
    profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
    life_id TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(capability_id, version, profile_revision, life_id)
) STRICT;

CREATE TABLE capability_evidence (
    evidence_id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    capability_version TEXT NOT NULL,
    life_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE context_capsules (
    capsule_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 0),
    life_id TEXT NOT NULL,
    capsule_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    supersedes_capsule_id TEXT REFERENCES context_capsules(capsule_id) ON DELETE RESTRICT,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE UNIQUE INDEX context_capsules_one_active_idx
ON context_capsules(request_id)
WHERE status = 'ACTIVE';

CREATE TABLE skill_activation_refs (
    activation_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 0),
    skill_id TEXT NOT NULL,
    skill_sha256 TEXT NOT NULL CHECK(length(skill_sha256) = 64),
    status TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;

CREATE TABLE consumer_offsets (
    consumer_id TEXT NOT NULL,
    life_id TEXT NOT NULL,
    last_sequence INTEGER NOT NULL CHECK(last_sequence >= 0),
    last_event_hash TEXT CHECK(last_event_hash IS NULL OR length(last_event_hash) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(consumer_id, life_id)
) STRICT;

CREATE TABLE projection_heads (
    life_id TEXT PRIMARY KEY,
    writer_epoch INTEGER NOT NULL CHECK(writer_epoch >= 1),
    event_count INTEGER NOT NULL CHECK(event_count >= 1),
    head_event_id TEXT NOT NULL REFERENCES life_events(event_id) ON DELETE RESTRICT,
    head_event_hash TEXT NOT NULL CHECK(length(head_event_hash) = 64),
    replay_sha256 TEXT NOT NULL CHECK(length(replay_sha256) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0)
) STRICT;

CREATE TABLE tombstones (
    tombstone_id TEXT PRIMARY KEY,
    life_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref_hash TEXT NOT NULL CHECK(length(target_ref_hash) = 64),
    deletion_proof_sha256 TEXT NOT NULL CHECK(length(deletion_proof_sha256) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
) STRICT;
"""

_P1_SCHEMA_SQL = _SCHEMA_SQL
_P1_SCHEMA_SHA256 = hashlib.sha256(_P1_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P2_INGRESS_MIGRATION_ID = "p3-source-ingress-ledger"
_P2_INGRESS_STATEMENTS = (
    """
    CREATE TABLE life_ingress_receipts (
        ingress_id TEXT PRIMARY KEY,
        source_component_id TEXT NOT NULL,
        source_epoch INTEGER NOT NULL CHECK(source_epoch >= 1),
        source_sequence INTEGER NOT NULL CHECK(source_sequence >= 1),
        life_id TEXT NOT NULL,
        dedupe_key TEXT NOT NULL CHECK(length(dedupe_key) = 64),
        ingress_sha256 TEXT NOT NULL CHECK(length(ingress_sha256) = 64),
        event_id TEXT NOT NULL REFERENCES life_events(event_id) ON DELETE RESTRICT,
        event_hash TEXT NOT NULL CHECK(length(event_hash) = 64),
        duplicate INTEGER NOT NULL CHECK(duplicate IN (0, 1)),
        received_at_ms INTEGER NOT NULL CHECK(received_at_ms >= 0),
        ingress BLOB NOT NULL,
        receipt BLOB NOT NULL,
        receipt_sha256 TEXT NOT NULL UNIQUE CHECK(length(receipt_sha256) = 64),
        UNIQUE(source_component_id, source_epoch, source_sequence)
    ) STRICT
    """,
    """
    CREATE INDEX life_ingress_receipts_life_source_idx
    ON life_ingress_receipts(life_id, source_component_id, source_epoch, source_sequence)
    """,
    """
    CREATE TABLE life_ingress_dedupe (
        source_component_id TEXT NOT NULL,
        life_id TEXT NOT NULL,
        dedupe_key TEXT NOT NULL CHECK(length(dedupe_key) = 64),
        first_ingress_id TEXT NOT NULL REFERENCES life_ingress_receipts(ingress_id)
            ON DELETE RESTRICT,
        event_id TEXT NOT NULL REFERENCES life_events(event_id) ON DELETE RESTRICT,
        event_hash TEXT NOT NULL CHECK(length(event_hash) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        PRIMARY KEY(source_component_id, life_id, dedupe_key)
    ) STRICT
    """,
)
_P2_INGRESS_SQL = ";\n".join(statement.strip() for statement in _P2_INGRESS_STATEMENTS) + ";\n"
_P2_INGRESS_SHA256 = hashlib.sha256(_P2_INGRESS_SQL.encode("utf-8")).hexdigest()
_P2_SCHEMA_SQL = _P1_SCHEMA_SQL + "\n" + _P2_INGRESS_SQL
_P2_SCHEMA_SHA256 = hashlib.sha256(_P2_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P3_CAUSAL_MEMORY_MIGRATION_ID = "p4-causal-memory-protected-context"
_P3_CAUSAL_MEMORY_STATEMENTS = (
    """
    CREATE TABLE protected_payloads (
        payload_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        privacy_scope TEXT NOT NULL,
        nonce BLOB NOT NULL CHECK(length(nonce) = 12),
        ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17),
        ciphertext_sha256 TEXT NOT NULL UNIQUE CHECK(length(ciphertext_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        key_destroyed_at_ms INTEGER,
        CHECK(key_destroyed_at_ms IS NULL OR key_destroyed_at_ms >= created_at_ms)
    ) STRICT
    """,
    """
    CREATE TABLE protected_payload_keys (
        payload_id TEXT PRIMARY KEY REFERENCES protected_payloads(payload_id)
            ON DELETE RESTRICT,
        key_material BLOB NOT NULL CHECK(length(key_material) = 32),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE life_index_keys (
        life_id TEXT PRIMARY KEY,
        key_material BLOB NOT NULL CHECK(length(key_material) = 32),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE memory_assertion_contracts (
        memory_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        payload BLOB NOT NULL,
        assertion_sha256 TEXT NOT NULL UNIQUE CHECK(length(assertion_sha256) = 64),
        PRIMARY KEY(memory_id, revision),
        FOREIGN KEY(memory_id, revision)
            REFERENCES memory_assertions(memory_id, revision) ON DELETE RESTRICT
    ) STRICT
    """,
    """
    CREATE TABLE memory_search_terms (
        memory_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        term_hmac_sha256 TEXT NOT NULL CHECK(length(term_hmac_sha256) = 64),
        privacy_scope TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        PRIMARY KEY(memory_id, revision, term_hmac_sha256)
    ) STRICT
    """,
    """
    CREATE INDEX memory_search_term_lookup_idx
    ON memory_search_terms(term_hmac_sha256, memory_id, revision)
    """,
    """
    CREATE TABLE causal_node_terms (
        node_id TEXT NOT NULL REFERENCES causal_nodes(node_id) ON DELETE RESTRICT,
        term_hmac_sha256 TEXT NOT NULL CHECK(length(term_hmac_sha256) = 64),
        privacy_scope TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        PRIMARY KEY(node_id, term_hmac_sha256)
    ) STRICT
    """,
    """
    CREATE INDEX causal_node_term_lookup_idx
    ON causal_node_terms(term_hmac_sha256, node_id)
    """,
    """
    CREATE TABLE causal_context_packs (
        pack_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation >= 0),
        source_capsule_id TEXT NOT NULL,
        protected_payload_id TEXT NOT NULL UNIQUE
            REFERENCES protected_payloads(payload_id) ON DELETE RESTRICT,
        pack_sha256 TEXT NOT NULL UNIQUE CHECK(length(pack_sha256) = 64),
        integrity_status TEXT NOT NULL CHECK(integrity_status = 'VERIFIED'),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE causal_context_pack_members (
        pack_id TEXT NOT NULL REFERENCES causal_context_packs(pack_id) ON DELETE RESTRICT,
        item_ref TEXT NOT NULL,
        PRIMARY KEY(pack_id, item_ref)
    ) STRICT
    """,
    """
    CREATE INDEX causal_context_pack_member_lookup_idx
    ON causal_context_pack_members(item_ref, pack_id)
    """,
    """
    CREATE TABLE privacy_deletion_tombstones (
        tombstone_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        target_ref_hash TEXT NOT NULL CHECK(length(target_ref_hash) = 64),
        privacy_scope TEXT NOT NULL,
        payload BLOB NOT NULL,
        deletion_proof_sha256 TEXT NOT NULL UNIQUE CHECK(length(deletion_proof_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE privacy_suppressions (
        target_kind TEXT NOT NULL,
        target_ref_hash TEXT NOT NULL CHECK(length(target_ref_hash) = 64),
        privacy_scope TEXT NOT NULL,
        tombstone_id TEXT NOT NULL
            REFERENCES privacy_deletion_tombstones(tombstone_id) ON DELETE RESTRICT,
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        PRIMARY KEY(target_kind, target_ref_hash, privacy_scope)
    ) STRICT
    """,
)
_P3_CAUSAL_MEMORY_SQL = ";\n".join(
    statement.strip() for statement in _P3_CAUSAL_MEMORY_STATEMENTS
) + ";\n"
_P3_CAUSAL_MEMORY_SHA256 = hashlib.sha256(
    _P3_CAUSAL_MEMORY_SQL.encode("utf-8")
).hexdigest()
_P3_SCHEMA_SQL = _P2_SCHEMA_SQL + "\n" + _P3_CAUSAL_MEMORY_SQL
_P3_SCHEMA_SHA256 = hashlib.sha256(_P3_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P4_AFFECT_MIGRATION_ID = "p5-affect-intake-and-expression"
_P4_AFFECT_STATEMENTS = (
    """
    CREATE TABLE affect_source_policies (
        life_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        payload BLOB NOT NULL,
        policy_sha256 TEXT NOT NULL UNIQUE CHECK(length(policy_sha256) = 64),
        effective_at_ms INTEGER NOT NULL CHECK(effective_at_ms >= 0),
        PRIMARY KEY(life_id, revision)
    ) STRICT
    """,
    """
    CREATE TABLE affect_source_offsets (
        life_id TEXT NOT NULL,
        source_stream_id TEXT NOT NULL,
        source_epoch INTEGER NOT NULL CHECK(source_epoch >= 1),
        source_sequence INTEGER NOT NULL CHECK(source_sequence >= 1),
        last_signal_id TEXT NOT NULL,
        updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
        PRIMARY KEY(life_id, source_stream_id)
    ) STRICT
    """,
    """
    CREATE TABLE affect_signal_receipts (
        signal_id TEXT PRIMARY KEY,
        source_event_id TEXT NOT NULL UNIQUE
            REFERENCES life_events(event_id) ON DELETE RESTRICT,
        life_id TEXT NOT NULL,
        source_family TEXT NOT NULL,
        source_stream_id TEXT NOT NULL,
        source_epoch INTEGER NOT NULL CHECK(source_epoch >= 1),
        source_sequence INTEGER NOT NULL CHECK(source_sequence >= 1),
        dedupe_key TEXT NOT NULL CHECK(length(dedupe_key) = 64),
        accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
        signal BLOB NOT NULL,
        signal_sha256 TEXT NOT NULL UNIQUE CHECK(length(signal_sha256) = 64),
        receipt BLOB NOT NULL,
        receipt_sha256 TEXT NOT NULL UNIQUE CHECK(length(receipt_sha256) = 64),
        received_at_ms INTEGER NOT NULL CHECK(received_at_ms >= 0),
        UNIQUE(life_id, source_stream_id, source_epoch, source_sequence)
    ) STRICT
    """,
    """
    CREATE TABLE affect_dedupe (
        life_id TEXT NOT NULL,
        dedupe_key TEXT NOT NULL CHECK(length(dedupe_key) = 64),
        first_signal_id TEXT NOT NULL
            REFERENCES affect_signal_receipts(signal_id) ON DELETE RESTRICT,
        last_signal_id TEXT NOT NULL
            REFERENCES affect_signal_receipts(signal_id) ON DELETE RESTRICT,
        occurrence_count INTEGER NOT NULL CHECK(occurrence_count >= 1),
        first_seen_at_ms INTEGER NOT NULL CHECK(first_seen_at_ms >= 0),
        last_seen_at_ms INTEGER NOT NULL CHECK(last_seen_at_ms >= first_seen_at_ms),
        PRIMARY KEY(life_id, dedupe_key)
    ) STRICT
    """,
    """
    CREATE INDEX affect_signal_life_family_idx
    ON affect_signal_receipts(life_id, source_family, received_at_ms)
    """,
)
_P4_AFFECT_SQL = ";\n".join(
    statement.strip() for statement in _P4_AFFECT_STATEMENTS
) + ";\n"
_P4_AFFECT_SHA256 = hashlib.sha256(_P4_AFFECT_SQL.encode("utf-8")).hexdigest()
_P4_SCHEMA_SQL = _P3_SCHEMA_SQL + "\n" + _P4_AFFECT_SQL
_P4_SCHEMA_SHA256 = hashlib.sha256(_P4_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P5_AUTONOMY_MIGRATION_ID = "p7-causal-graded-autonomy"
_P5_AUTONOMY_STATEMENTS = (
    """
    CREATE TABLE viability_observations (
        observation_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        dimension TEXT NOT NULL,
        source_event_id TEXT NOT NULL REFERENCES life_events(event_id) ON DELETE RESTRICT,
        payload BLOB NOT NULL,
        observation_sha256 TEXT NOT NULL UNIQUE CHECK(length(observation_sha256) = 64),
        measured_at_ms INTEGER NOT NULL CHECK(measured_at_ms >= 0),
        stale_after_ms INTEGER NOT NULL CHECK(stale_after_ms >= measured_at_ms)
    ) STRICT
    """,
    """
    CREATE INDEX viability_observations_dimension_idx
    ON viability_observations(life_id, dimension, measured_at_ms)
    """,
    """
    CREATE TABLE action_candidates (
        candidate_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        episode_id TEXT NOT NULL,
        action_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        payload BLOB NOT NULL,
        candidate_sha256 TEXT NOT NULL UNIQUE CHECK(length(candidate_sha256) = 64),
        proposed_at_ms INTEGER NOT NULL CHECK(proposed_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > proposed_at_ms)
    ) STRICT
    """,
    """
    CREATE INDEX action_candidates_episode_idx
    ON action_candidates(life_id, episode_id, proposed_at_ms)
    """,
    """
    CREATE TABLE autonomy_policies (
        life_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        policy_id TEXT NOT NULL UNIQUE,
        payload BLOB NOT NULL,
        policy_sha256 TEXT NOT NULL UNIQUE CHECK(length(policy_sha256) = 64),
        effective_at_ms INTEGER NOT NULL CHECK(effective_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > effective_at_ms),
        PRIMARY KEY(life_id, revision)
    ) STRICT
    """,
    """
    CREATE TABLE autonomy_usage_snapshots (
        usage_sha256 TEXT PRIMARY KEY CHECK(length(usage_sha256) = 64),
        life_id TEXT NOT NULL,
        policy_snapshot_hash TEXT NOT NULL CHECK(length(policy_snapshot_hash) = 64),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        supersedes_usage_sha256 TEXT,
        day_start_ms INTEGER NOT NULL CHECK(day_start_ms >= 0),
        day_end_ms INTEGER NOT NULL CHECK(day_end_ms > day_start_ms),
        payload BLOB NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        UNIQUE(life_id, policy_snapshot_hash, day_start_ms, revision)
    ) STRICT
    """,
    """
    CREATE INDEX autonomy_usage_life_day_idx
    ON autonomy_usage_snapshots(life_id, day_start_ms, created_at_ms)
    """,
)
_P5_AUTONOMY_SQL = ";\n".join(
    statement.strip() for statement in _P5_AUTONOMY_STATEMENTS
) + ";\n"
_P5_AUTONOMY_SHA256 = hashlib.sha256(_P5_AUTONOMY_SQL.encode("utf-8")).hexdigest()
_P5_SCHEMA_SQL = _P4_SCHEMA_SQL + "\n" + _P5_AUTONOMY_SQL
_P5_SCHEMA_SHA256 = hashlib.sha256(_P5_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P6_REFLECTION_MIGRATION_ID = "p8-causal-reflection-capability-learning"
_P6_REFLECTION_STATEMENTS = (
    """
    CREATE TABLE episode_outcomes (
        outcome_evidence_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        episode_id TEXT NOT NULL,
        payload BLOB NOT NULL,
        evidence_sha256 TEXT NOT NULL UNIQUE CHECK(length(evidence_sha256) = 64),
        occurred_at_ms INTEGER NOT NULL CHECK(occurred_at_ms >= 0),
        UNIQUE(life_id, episode_id)
    ) STRICT
    """,
    """
    CREATE TABLE reflection_question_decisions (
        question_decision_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        reflection_id TEXT NOT NULL REFERENCES reflection_cards(reflection_id) ON DELETE RESTRICT,
        preference_domain TEXT NOT NULL,
        outcome TEXT NOT NULL,
        cooldown_until_ms INTEGER NOT NULL CHECK(cooldown_until_ms >= 0),
        payload BLOB NOT NULL,
        decision_sha256 TEXT NOT NULL UNIQUE CHECK(length(decision_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        UNIQUE(reflection_id)
    ) STRICT
    """,
    """
    CREATE INDEX reflection_question_domain_idx
    ON reflection_question_decisions(life_id, preference_domain, created_at_ms)
    """,
    """
    CREATE TABLE capability_learning_decisions (
        learning_decision_id TEXT PRIMARY KEY,
        capability_id TEXT NOT NULL,
        capability_version TEXT NOT NULL,
        life_id TEXT NOT NULL,
        evidence_set_sha256 TEXT NOT NULL CHECK(length(evidence_set_sha256) = 64),
        outcome TEXT NOT NULL,
        payload BLOB NOT NULL,
        decision_sha256 TEXT NOT NULL UNIQUE CHECK(length(decision_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        UNIQUE(capability_id, capability_version, life_id, evidence_set_sha256)
    ) STRICT
    """,
    """
    CREATE TABLE capability_rollbacks (
        rollback_id TEXT PRIMARY KEY,
        capability_id TEXT NOT NULL,
        capability_version TEXT NOT NULL,
        life_id TEXT NOT NULL,
        payload BLOB NOT NULL,
        rollback_sha256 TEXT NOT NULL UNIQUE CHECK(length(rollback_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE capability_invalidations (
        rollback_id TEXT NOT NULL REFERENCES capability_rollbacks(rollback_id) ON DELETE RESTRICT,
        target_kind TEXT NOT NULL CHECK(target_kind IN ('context_pack', 'skill_activation')),
        target_ref TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        PRIMARY KEY(target_kind, target_ref)
    ) STRICT
    """,
)
_P6_REFLECTION_SQL = ";\n".join(
    statement.strip() for statement in _P6_REFLECTION_STATEMENTS
) + ";\n"
_P6_REFLECTION_SHA256 = hashlib.sha256(_P6_REFLECTION_SQL.encode("utf-8")).hexdigest()
_P6_SCHEMA_SQL = _P5_SCHEMA_SQL + "\n" + _P6_REFLECTION_SQL
_P6_SCHEMA_SHA256 = hashlib.sha256(_P6_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P7_CONTEXT_AUTHORIZATION_MIGRATION_ID = "p10-atomic-context-authorization"
_P7_CONTEXT_AUTHORIZATION_STATEMENTS = (
    """
    CREATE TABLE context_authorizations (
        authorization_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation >= 0),
        principal_scope_hash TEXT NOT NULL CHECK(length(principal_scope_hash) = 64),
        context_pack_id TEXT NOT NULL REFERENCES causal_context_packs(pack_id) ON DELETE RESTRICT,
        revisions_sha256 TEXT NOT NULL CHECK(length(revisions_sha256) = 64),
        payload BLOB NOT NULL,
        authorization_sha256 TEXT NOT NULL UNIQUE CHECK(length(authorization_sha256) = 64),
        issued_at_ms INTEGER NOT NULL CHECK(issued_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > issued_at_ms),
        UNIQUE(request_id, run_id, generation)
    ) STRICT
    """,
    """
    CREATE INDEX context_authorizations_life_time_idx
    ON context_authorizations(life_id, issued_at_ms, authorization_id)
    """,
)
_P7_CONTEXT_AUTHORIZATION_SQL = ";\n".join(
    statement.strip() for statement in _P7_CONTEXT_AUTHORIZATION_STATEMENTS
) + ";\n"
_P7_CONTEXT_AUTHORIZATION_SHA256 = hashlib.sha256(
    _P7_CONTEXT_AUTHORIZATION_SQL.encode("utf-8")
).hexdigest()
_P7_SCHEMA_SQL = _P6_SCHEMA_SQL + "\n" + _P7_CONTEXT_AUTHORIZATION_SQL
_P7_SCHEMA_SHA256 = hashlib.sha256(_P7_SCHEMA_SQL.encode("utf-8")).hexdigest()
_P8_MEMORY_CHANGE_MIGRATION_ID = "p11-memory-change-seq-outbox"
_P8_MEMORY_CHANGE_STATEMENTS = (
    """
    CREATE TABLE memory_change_log (
        change_seq INTEGER PRIMARY KEY CHECK(change_seq >= 1),
        life_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        change_kind TEXT NOT NULL CHECK(change_kind IN ('assert', 'revise', 'tombstone')),
        assertion_sha256 TEXT NOT NULL CHECK(length(assertion_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        UNIQUE(memory_id, revision)
    ) STRICT
    """,
    """
    CREATE INDEX memory_change_log_life_idx
    ON memory_change_log(life_id, change_seq)
    """,
    """
    CREATE TABLE memory_outbox (
        change_seq INTEGER PRIMARY KEY
            REFERENCES memory_change_log(change_seq) ON DELETE RESTRICT,
        life_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        change_kind TEXT NOT NULL CHECK(change_kind IN ('assert', 'revise', 'tombstone')),
        receipt_id TEXT,
        receipt_sha256 TEXT CHECK(receipt_sha256 IS NULL OR length(receipt_sha256) = 64),
        delivered_at_ms INTEGER,
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        CHECK((receipt_id IS NULL) = (receipt_sha256 IS NULL)),
        CHECK((receipt_id IS NULL) = (delivered_at_ms IS NULL))
    ) STRICT
    """,
    """
    CREATE INDEX memory_outbox_pending_idx
    ON memory_outbox(life_id, change_seq)
    """,
)
_P8_MEMORY_CHANGE_SQL = ";\n".join(
    statement.strip() for statement in _P8_MEMORY_CHANGE_STATEMENTS
) + ";\n"
_P8_MEMORY_CHANGE_SHA256 = hashlib.sha256(
    _P8_MEMORY_CHANGE_SQL.encode("utf-8")
).hexdigest()
_P9_V21_LIFE_BINDING_MIGRATION_ID = "v21-life-authority-binding-root-heads"
_P9_V21_LIFE_BINDING_STATEMENTS = (
    """CREATE TABLE life_authority_heads (life_id TEXT PRIMARY KEY, payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64)) STRICT""",
    """CREATE TABLE run_life_bindings (binding_id TEXT PRIMARY KEY, life_id TEXT NOT NULL, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64), UNIQUE(life_id, subject_kind, subject_id)) STRICT""",
    """CREATE TABLE root_experience_heads (root_experience_id TEXT PRIMARY KEY, life_id TEXT NOT NULL, root_status TEXT NOT NULL, payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64)) STRICT""",
    """CREATE TABLE root_continuation_bindings (continuation_id TEXT PRIMARY KEY, root_experience_id TEXT NOT NULL, previous_root_head_sha256 TEXT NOT NULL CHECK(length(previous_root_head_sha256)=64), payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64), UNIQUE(root_experience_id, previous_root_head_sha256)) STRICT""",
)
_P9_V21_LIFE_BINDING_SQL = ";\n".join(statement.strip() for statement in _P9_V21_LIFE_BINDING_STATEMENTS) + ";\n"
_P9_V21_LIFE_BINDING_SHA256 = hashlib.sha256(_P9_V21_LIFE_BINDING_SQL.encode("utf-8")).hexdigest()
_P10_V21_CAUSAL_CHILD_MIGRATION_ID = "v21-root-ordered-causal-children"
_P10_V21_CAUSAL_CHILD_STATEMENTS = (
    """CREATE TABLE causal_episodes_vnext (episode_id TEXT PRIMARY KEY, root_experience_id TEXT NOT NULL, sequence_no INTEGER NOT NULL CHECK(sequence_no>=1), predecessor_episode_sha256 TEXT, terminal_status TEXT NOT NULL, payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64), UNIQUE(root_experience_id,sequence_no)) STRICT""",
)
_P10_V21_CAUSAL_CHILD_SQL = ";\n".join(statement.strip() for statement in _P10_V21_CAUSAL_CHILD_STATEMENTS) + ";\n"
_P10_V21_CAUSAL_CHILD_SHA256 = hashlib.sha256(_P10_V21_CAUSAL_CHILD_SQL.encode("utf-8")).hexdigest()
_P11_V21_COGNITION_SHADOW_MIGRATION_ID = "v21-unified-cognition-shadow"
_P11_V21_COGNITION_SHADOW_STATEMENTS = (
    """CREATE TABLE stimulus_inbox (
        enqueue_seq INTEGER PRIMARY KEY AUTOINCREMENT,
        life_id TEXT NOT NULL,
        lane TEXT NOT NULL CHECK(lane IN ('foreground','background')),
        base_priority INTEGER NOT NULL CHECK(base_priority>=0),
        event_id TEXT NOT NULL UNIQUE,
        payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
        enqueued_at_ms INTEGER NOT NULL CHECK(enqueued_at_ms>=0),
        selected_at_ms INTEGER,
        claim_token TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending','selected','committed'))
    ) STRICT""",
    """CREATE TABLE cognition_lane_leases (
        life_id TEXT NOT NULL,
        lane TEXT NOT NULL CHECK(lane IN ('foreground','background')),
        lease_id TEXT NOT NULL UNIQUE,
        owner_instance_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>=0),
        acquired_at_ms INTEGER NOT NULL CHECK(acquired_at_ms>=0),
        expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms>=0),
        status TEXT NOT NULL CHECK(status IN ('active','preempted')),
        preempted_by TEXT,
        PRIMARY KEY (life_id, lane)
    ) STRICT""",
    """CREATE TABLE cognition_state (
        life_id TEXT PRIMARY KEY,
        foreground_streak INTEGER NOT NULL CHECK(foreground_streak>=0),
        last_selected_lane TEXT CHECK(last_selected_lane IN ('foreground','background')),
        model_inflight_count INTEGER NOT NULL CHECK(model_inflight_count>=0),
        updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>=0)
    ) STRICT""",
    """CREATE TABLE model_attempt_shadow (
        attempt_shadow_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        root_experience_id TEXT NOT NULL,
        episode_id TEXT NOT NULL,
        lane TEXT NOT NULL CHECK(lane IN ('foreground','background')),
        slot_no INTEGER NOT NULL CHECK(slot_no>=1),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
        status TEXT NOT NULL CHECK(status IN ('plan_frozen','dispatched','succeeded','failed','preflight_unavailable')),
        finish_reason TEXT,
        output_text_sha256 TEXT,
        started_at_ms INTEGER NOT NULL CHECK(started_at_ms>=0),
        completed_at_ms INTEGER CHECK(completed_at_ms IS NULL OR completed_at_ms>=0),
        payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64)
    ) STRICT""",
)
_P11_V21_COGNITION_SHADOW_SQL = ";\n".join(statement.strip() for statement in _P11_V21_COGNITION_SHADOW_STATEMENTS) + ";\n"
_P11_V21_COGNITION_SHADOW_SHA256 = hashlib.sha256(_P11_V21_COGNITION_SHADOW_SQL.encode("utf-8")).hexdigest()
_P12_V21_LIFE_TURN_COMMIT_MIGRATION_ID = "v21-life-turn-commit-stages"
_P12_V21_LIFE_TURN_COMMIT_STATEMENTS = (
    """CREATE TABLE life_turn_commits (
        turn_commit_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        root_experience_id TEXT NOT NULL,
        child_episode_id TEXT NOT NULL,
        response_episode_id TEXT NOT NULL,
        stage TEXT NOT NULL CHECK (stage IN (
            'OUTCOME_COMMITTED_RESPONSE_OPEN','RESPONSE_COMMITTED','DELIVERY_OBSERVED','ROOT_TERMINAL'
        )),
        predecessor_commit_sha256 TEXT,
        payload BLOB NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0),
        UNIQUE(root_experience_id, response_episode_id, stage)
    ) STRICT""",
)
_P12_V21_LIFE_TURN_COMMIT_SQL = ";\n".join(statement.strip() for statement in _P12_V21_LIFE_TURN_COMMIT_STATEMENTS) + ";\n"
_P12_V21_LIFE_TURN_COMMIT_SHA256 = hashlib.sha256(_P12_V21_LIFE_TURN_COMMIT_SQL.encode("utf-8")).hexdigest()
_P13_V21_CAPABILITY_LIFECYCLE_MIGRATION_ID = "v21-capability-lifecycle-shadow"
_P13_V21_CAPABILITY_LIFECYCLE_STATEMENTS = (
    """CREATE TABLE capability_candidate_artifacts (
        candidate_id TEXT PRIMARY KEY,
        life_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
        source_fact_refs_json TEXT NOT NULL CHECK(json_valid(source_fact_refs_json)),
        phase TEXT NOT NULL CHECK(phase IN ('DRAFT','COMPILED','EXECUTION_TESTED','QC_PASSED','SHADOW','CURRENT','RETIRED')),
        payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms>=0)
    ) STRICT""",
    """CREATE TABLE capability_pointer_heads (
        life_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        current_candidate_id TEXT NOT NULL,
        current_artifact_sha256 TEXT NOT NULL CHECK(length(current_artifact_sha256)=64),
        pointer_sha256 TEXT NOT NULL CHECK(length(pointer_sha256)=64),
        revision INTEGER NOT NULL CHECK(revision>=1),
        updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>=0),
        PRIMARY KEY (life_id, skill_id)
    ) STRICT""",
)
_P13_V21_CAPABILITY_LIFECYCLE_SQL = ";\n".join(statement.strip() for statement in _P13_V21_CAPABILITY_LIFECYCLE_STATEMENTS) + ";\n"
_P13_V21_CAPABILITY_LIFECYCLE_SHA256 = hashlib.sha256(_P13_V21_CAPABILITY_LIFECYCLE_SQL.encode("utf-8")).hexdigest()
_SCHEMA_SQL = (
    _P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n"
    + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n"
    + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL
)
_SCHEMA_SHA256 = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()
_EXPECTED_TABLES = frozenset(
    {
        "affect_snapshots",
        "affect_dedupe",
        "affect_signal_receipts",
        "affect_source_offsets",
        "affect_source_policies",
        "action_candidates",
        "action_impacts",
        "agency_decisions",
        "appraisal_events",
        "authorization_refs",
        "autonomy_policies",
        "autonomy_usage_snapshots",
        "capability_evidence",
        "capability_invalidations",
        "capability_learning_decisions",
        "capability_profiles",
        "capability_rollbacks",
        "causal_edge_versions",
        "causal_episodes",
        "causal_nodes",
        "causal_node_terms",
        "causal_context_pack_members",
        "causal_context_packs",
        "consumer_offsets",
        "context_capsules",
        "context_authorizations",
        "event_evidence",
        "episode_outcomes",
        "life_events",
        "life_ingress_dedupe",
        "life_ingress_receipts",
        "life_index_keys",
        "memory_assertions",
        "memory_assertion_contracts",
        "memory_change_log",
        "memory_outbox",
        "life_authority_heads",
        "run_life_bindings",
        "root_experience_heads",
        "root_continuation_bindings",
        "causal_episodes_vnext",
        "stimulus_inbox",
        "cognition_lane_leases",
        "cognition_state",
        "model_attempt_shadow",
        "life_turn_commits",
        "capability_candidate_artifacts",
        "capability_pointer_heads",
        "memory_relations",
        "memory_search_terms",
        "privacy_deletion_tombstones",
        "privacy_suppressions",
        "projection_heads",
        "protected_payload_keys",
        "protected_payloads",
        "reflection_cards",
        "reflection_question_decisions",
        "schema_metadata",
        "schema_migrations",
        "skill_activation_refs",
        "tombstones",
        "viability_snapshots",
        "viability_observations",
    }
)


class LifeShadowStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LifeIngressCommit:
    event: LifeEventEnvelope
    receipt: LifeEventIngressReceipt
    event_created: bool
    receipt_created: bool


@dataclass(frozen=True, slots=True)
class AffectIntakeCommit:
    signal: AffectSignal
    receipt: AffectIntakeReceipt
    appraisal: AppraisalVectorV3 | None
    state: AffectiveStateV3 | None
    signal_created: bool


@dataclass(frozen=True, slots=True)
class ProtectedPayloadRecord:
    payload_id: str
    life_id: str
    privacy_scope: str
    ciphertext_sha256: str
    created_at_ms: int
    key_available: bool
    key_destroyed_at_ms: int | None


@dataclass(frozen=True, slots=True)
class MemoryDeletionResult:
    tombstone: PrivacyDeletionTombstone
    deleted_assertion: MemoryAssertionV3
    destroyed_payload_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextPackPersistRecord:
    pack: CausalContextPack
    protected_payload: ProtectedPayloadRecord
    created_by_this_call: bool


def _revalidate_contract(value, model_type, identity: str):
    try:
        payload = canonical_json_bytes(value)
        validated = model_type.model_validate_json(payload)
    except Exception as exc:
        raise LifeShadowStoreError(f"{identity} contract is invalid") from exc
    if canonical_json_bytes(validated) != payload:
        raise LifeShadowStoreError(f"{identity} contract is not canonical")
    return validated, payload


def _parse_stored_contract(payload: bytes, model_type, identity: str):
    try:
        value = model_type.model_validate_json(payload, strict=True)
    except Exception as exc:
        raise LifeShadowStoreError(f"stored {identity} contract is invalid") from exc
    if canonical_json_bytes(value) != payload:
        raise LifeShadowStoreError(f"stored {identity} contract is not canonical")
    return value


def _normalize_search_term(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("memory search term must be text")
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    if (
        not normalized
        or len(normalized) > 256
        or "\x00" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError("memory search term is invalid")
    return normalized


def _protected_payload_aad(
    *, payload_id: str, life_id: str, privacy_scope: str
) -> bytes:
    return canonical_json_bytes(
        {
            "domain": "tiangong.life.protected-payload.v1",
            "life_id": life_id,
            "payload_id": payload_id,
            "privacy_scope": privacy_scope,
        }
    )


class LifeShadowStore:
    """A fail-closed shadow store; it cannot open a production-looking path."""

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        create: bool,
        now_ms: int,
    ) -> "LifeShadowStore":
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise LifeShadowStoreError("shadow store timestamp is invalid")
        if path.name != path.name.strip() or not path.name.endswith(".shadow.sqlite3"):
            raise LifeShadowStoreError("shadow store path must end with .shadow.sqlite3")
        parent = path.parent.resolve(strict=True)
        candidate = parent / path.name
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_file():
                raise LifeShadowStoreError("shadow store path is unsafe")
        elif not create:
            raise LifeShadowStoreError("shadow store does not exist")
        existed = candidate.exists()
        connection = sqlite3.connect(
            candidate,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA busy_timeout=5000")
            mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise LifeShadowStoreError("shadow store did not enter WAL mode")
            connection.execute("PRAGMA synchronous=FULL")
            if not existed:
                if not create:
                    raise LifeShadowStoreError("shadow store creation was not authorized")
                cls._initialize(connection, now_ms=now_ms)
            else:
                cls._migrate(connection, now_ms=now_ms)
            store = cls(candidate, connection)
            store.health()
            return store
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _initialize(connection: sqlite3.Connection, *, now_ms: int) -> None:
        connection.executescript(_SCHEMA_SQL)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    1,
                    "p1-initial-shadow-schema",
                    _P1_SCHEMA_SHA256,
                    now_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (2, _P2_INGRESS_MIGRATION_ID, _P2_INGRESS_SHA256, now_ms),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (3, _P3_CAUSAL_MEMORY_MIGRATION_ID, _P3_CAUSAL_MEMORY_SHA256, now_ms),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (4, _P4_AFFECT_MIGRATION_ID, _P4_AFFECT_SHA256, now_ms),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (5, _P5_AUTONOMY_MIGRATION_ID, _P5_AUTONOMY_SHA256, now_ms),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (6, _P6_REFLECTION_MIGRATION_ID, _P6_REFLECTION_SHA256, now_ms),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    7,
                    _P7_CONTEXT_AUTHORIZATION_MIGRATION_ID,
                    _P7_CONTEXT_AUTHORIZATION_SHA256,
                    now_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (8, _P8_MEMORY_CHANGE_MIGRATION_ID, _P8_MEMORY_CHANGE_SHA256, now_ms),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (9, ?, ?, ?)",
                (_P9_V21_LIFE_BINDING_MIGRATION_ID, _P9_V21_LIFE_BINDING_SHA256, now_ms),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (10, ?, ?, ?)",
                (_P10_V21_CAUSAL_CHILD_MIGRATION_ID, _P10_V21_CAUSAL_CHILD_SHA256, now_ms),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (11, ?, ?, ?)",
                (_P11_V21_COGNITION_SHADOW_MIGRATION_ID, _P11_V21_COGNITION_SHADOW_SHA256, now_ms),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (12, ?, ?, ?)",
                (_P12_V21_LIFE_TURN_COMMIT_MIGRATION_ID, _P12_V21_LIFE_TURN_COMMIT_SHA256, now_ms),
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (13, ?, ?, ?)",
                (_P13_V21_CAPABILITY_LIFECYCLE_MIGRATION_ID, _P13_V21_CAPABILITY_LIFECYCLE_SHA256, now_ms),
            )
            connection.execute(
                "INSERT INTO schema_metadata(key, value) VALUES ('purpose', 'life-shadow-only')"
            )
            connection.execute(
                "INSERT INTO schema_metadata(key, value) VALUES ('schema_sha256', ?)",
                (_SCHEMA_SHA256,),
            )
            connection.execute(f"PRAGMA application_id={SHADOW_STORE_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SHADOW_STORE_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _migrate(connection: sqlite3.Connection, *, now_ms: int) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id != SHADOW_STORE_APPLICATION_ID:
            raise LifeShadowStoreError("shadow store application identity is invalid")
        if user_version == SHADOW_STORE_SCHEMA_VERSION:
            return
        if user_version not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}:
            raise LifeShadowStoreError("shadow store schema version cannot be migrated")
        tables = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_list").fetchall()
            if str(row["type"]) == "table" and not str(row["name"]).startswith("sqlite_")
        }
        p3_tables = {
            "causal_context_pack_members",
            "causal_context_packs",
            "causal_node_terms",
            "life_index_keys",
            "memory_assertion_contracts",
            "memory_search_terms",
            "privacy_deletion_tombstones",
            "privacy_suppressions",
            "protected_payload_keys",
            "protected_payloads",
        }
        p4_tables = {
            "affect_dedupe",
            "affect_signal_receipts",
            "affect_source_offsets",
            "affect_source_policies",
        }
        p5_tables = {
            "action_candidates",
            "autonomy_policies",
            "autonomy_usage_snapshots",
            "viability_observations",
        }
        p6_tables = {
            "capability_invalidations",
            "capability_learning_decisions",
            "capability_rollbacks",
            "episode_outcomes",
            "reflection_question_decisions",
        }
        p7_tables = {"context_authorizations"}
        p8_tables = {"memory_change_log", "memory_outbox"}
        p9_tables = {"life_authority_heads", "run_life_bindings", "root_experience_heads", "root_continuation_bindings"}
        p10_tables = {"causal_episodes_vnext"}
        p11_tables = {"stimulus_inbox", "cognition_lane_leases", "cognition_state", "model_attempt_shadow"}
        p12_tables = {"life_turn_commits"}
        p13_tables = {"capability_candidate_artifacts", "capability_pointer_heads"}
        p2_tables = {"life_ingress_dedupe", "life_ingress_receipts"}
        expected_tables = set(_EXPECTED_TABLES)
        if user_version < 13:
            expected_tables -= p13_tables
        if user_version < 12:
            expected_tables -= p12_tables
        if user_version < 11:
            expected_tables -= p11_tables
        if user_version < 10:
            expected_tables -= p10_tables
        if user_version < 9:
            expected_tables -= p9_tables
        if user_version < 8:
            expected_tables -= p8_tables
        if user_version < 7:
            expected_tables -= p7_tables
        if user_version < 6:
            expected_tables -= p6_tables
        if user_version < 5:
            expected_tables -= p5_tables
        if user_version < 4:
            expected_tables -= p4_tables
        if user_version < 3:
            expected_tables -= p3_tables
        if user_version < 2:
            expected_tables -= p2_tables
        if tables != expected_tables:
            raise LifeShadowStoreError(
                f"p{user_version} shadow store table set is invalid"
            )
        migration = connection.execute(
            "SELECT version, migration_id, sql_sha256 FROM schema_migrations"
        ).fetchall()
        metadata = dict(
            connection.execute("SELECT key, value FROM schema_metadata").fetchall()
        )
        expected_migrations = [
            (1, "p1-initial-shadow-schema", _P1_SCHEMA_SHA256),
        ]
        expected_schema_sha256 = _P1_SCHEMA_SHA256
        if user_version >= 2:
            expected_migrations.append(
                (2, _P2_INGRESS_MIGRATION_ID, _P2_INGRESS_SHA256)
            )
            expected_schema_sha256 = _P2_SCHEMA_SHA256
        if user_version >= 3:
            expected_migrations.append(
                (3, _P3_CAUSAL_MEMORY_MIGRATION_ID, _P3_CAUSAL_MEMORY_SHA256)
            )
            expected_schema_sha256 = _P3_SCHEMA_SHA256
        if user_version >= 4:
            expected_migrations.append(
                (4, _P4_AFFECT_MIGRATION_ID, _P4_AFFECT_SHA256)
            )
            expected_schema_sha256 = _P4_SCHEMA_SHA256
        if user_version >= 5:
            expected_migrations.append(
                (5, _P5_AUTONOMY_MIGRATION_ID, _P5_AUTONOMY_SHA256)
            )
            expected_schema_sha256 = _P5_SCHEMA_SHA256
        if user_version >= 6:
            expected_migrations.append(
                (6, _P6_REFLECTION_MIGRATION_ID, _P6_REFLECTION_SHA256)
            )
            expected_schema_sha256 = _P6_SCHEMA_SHA256
        if user_version >= 7:
            expected_migrations.append(
                (
                    7,
                    _P7_CONTEXT_AUTHORIZATION_MIGRATION_ID,
                    _P7_CONTEXT_AUTHORIZATION_SHA256,
                )
            )
            expected_schema_sha256 = _P7_SCHEMA_SHA256
        if user_version >= 8:
            expected_migrations.append((8, _P8_MEMORY_CHANGE_MIGRATION_ID, _P8_MEMORY_CHANGE_SHA256))
            expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL).encode("utf-8")).hexdigest()
        if user_version >= 9:
            expected_migrations.append((9, _P9_V21_LIFE_BINDING_MIGRATION_ID, _P9_V21_LIFE_BINDING_SHA256))
            expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL).encode("utf-8")).hexdigest()
        if user_version >= 10:
            expected_migrations.append((10, _P10_V21_CAUSAL_CHILD_MIGRATION_ID, _P10_V21_CAUSAL_CHILD_SHA256))
            expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL).encode("utf-8")).hexdigest()
        if user_version >= 11:
            expected_migrations.append((11, _P11_V21_COGNITION_SHADOW_MIGRATION_ID, _P11_V21_COGNITION_SHADOW_SHA256))
            expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL).encode("utf-8")).hexdigest()
        if user_version >= 12:
            expected_migrations.append((12, _P12_V21_LIFE_TURN_COMMIT_MIGRATION_ID, _P12_V21_LIFE_TURN_COMMIT_SHA256))
            expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n" + _P12_V21_LIFE_TURN_COMMIT_SQL).encode("utf-8")).hexdigest()
        if user_version >= 13:
            expected_migrations.append((13, _P13_V21_CAPABILITY_LIFECYCLE_MIGRATION_ID, _P13_V21_CAPABILITY_LIFECYCLE_SHA256))
            expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL).encode("utf-8")).hexdigest()
        if (
            len(migration) != len(expected_migrations)
            or any(
                (
                    int(row["version"]),
                    str(row["migration_id"]),
                    str(row["sql_sha256"]),
                )
                != expected
                for row, expected in zip(
                    migration, expected_migrations, strict=True
                )
            )
            or metadata
            != {
                "purpose": "life-shadow-only",
                "schema_sha256": expected_schema_sha256,
            }
        ):
            raise LifeShadowStoreError(
                f"p{user_version} shadow store migration evidence is invalid"
            )
        try:
            connection.execute("BEGIN IMMEDIATE")
            if user_version == 1:
                for statement in _P2_INGRESS_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                    VALUES (2, ?, ?, ?)
                    """,
                    (_P2_INGRESS_MIGRATION_ID, _P2_INGRESS_SHA256, now_ms),
                )
            if user_version < 3:
                for statement in _P3_CAUSAL_MEMORY_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                    VALUES (3, ?, ?, ?)
                    """,
                    (
                        _P3_CAUSAL_MEMORY_MIGRATION_ID,
                        _P3_CAUSAL_MEMORY_SHA256,
                        now_ms,
                    ),
                )
            if user_version < 4:
                for statement in _P4_AFFECT_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                    VALUES (4, ?, ?, ?)
                    """,
                    (_P4_AFFECT_MIGRATION_ID, _P4_AFFECT_SHA256, now_ms),
                )
            if user_version < 5:
                for statement in _P5_AUTONOMY_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                    VALUES (5, ?, ?, ?)
                    """,
                    (_P5_AUTONOMY_MIGRATION_ID, _P5_AUTONOMY_SHA256, now_ms),
                )
            if user_version < 6:
                for statement in _P6_REFLECTION_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                    VALUES (6, ?, ?, ?)
                    """,
                    (_P6_REFLECTION_MIGRATION_ID, _P6_REFLECTION_SHA256, now_ms),
                )
            if user_version < 7:
                for statement in _P7_CONTEXT_AUTHORIZATION_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms)
                    VALUES (7, ?, ?, ?)
                    """,
                    (
                        _P7_CONTEXT_AUTHORIZATION_MIGRATION_ID,
                        _P7_CONTEXT_AUTHORIZATION_SHA256,
                        now_ms,
                    ),
                )
            if user_version < 8:
                for statement in _P8_MEMORY_CHANGE_STATEMENTS:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (8, ?, ?, ?)", (_P8_MEMORY_CHANGE_MIGRATION_ID, _P8_MEMORY_CHANGE_SHA256, now_ms))
            if user_version < 9:
                for statement in _P9_V21_LIFE_BINDING_STATEMENTS:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (9, ?, ?, ?)", (_P9_V21_LIFE_BINDING_MIGRATION_ID, _P9_V21_LIFE_BINDING_SHA256, now_ms))
            if user_version < 10:
                for statement in _P10_V21_CAUSAL_CHILD_STATEMENTS:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (10, ?, ?, ?)", (_P10_V21_CAUSAL_CHILD_MIGRATION_ID, _P10_V21_CAUSAL_CHILD_SHA256, now_ms))
            if user_version < 11:
                for statement in _P11_V21_COGNITION_SHADOW_STATEMENTS:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (11, ?, ?, ?)", (_P11_V21_COGNITION_SHADOW_MIGRATION_ID, _P11_V21_COGNITION_SHADOW_SHA256, now_ms))
            if user_version < 12:
                for statement in _P12_V21_LIFE_TURN_COMMIT_STATEMENTS:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (12, ?, ?, ?)", (_P12_V21_LIFE_TURN_COMMIT_MIGRATION_ID, _P12_V21_LIFE_TURN_COMMIT_SHA256, now_ms))
            if user_version < 13:
                for statement in _P13_V21_CAPABILITY_LIFECYCLE_STATEMENTS:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (13, ?, ?, ?)", (_P13_V21_CAPABILITY_LIFECYCLE_MIGRATION_ID, _P13_V21_CAPABILITY_LIFECYCLE_SHA256, now_ms))
            connection.execute(
                "UPDATE schema_metadata SET value = ? WHERE key = 'schema_sha256'",
                (_SCHEMA_SHA256,),
            )
            connection.execute(f"PRAGMA user_version={SHADOW_STORE_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "LifeShadowStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _insert_immutable(
        self,
        *,
        select_sql: str,
        select_values: tuple[object, ...],
        insert_sql: str,
        insert_values: tuple[object, ...],
        payload: bytes,
        identity: str,
    ) -> bool:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(select_sql, select_values).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError(f"{identity} identity was rebound")
                connection.execute("COMMIT")
                return False
            connection.execute(insert_sql, insert_values)
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_life_authority_head(self, head: LifeAuthorityHead, *, expected_head_sha256: str | None) -> bool:
        head, payload = _revalidate_contract(head, LifeAuthorityHead, "life authority head")
        if head.head_sha256 != head.computed_head_sha256():
            raise LifeShadowStoreError("life authority head digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload, payload_sha256 FROM life_authority_heads WHERE life_id=?", (head.life_id,)).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) == payload:
                    connection.execute("COMMIT"); return False
                if expected_head_sha256 != str(existing["payload_sha256"]):
                    raise LifeShadowStoreError("life authority head CAS is stale")
                connection.execute("UPDATE life_authority_heads SET payload=?, payload_sha256=? WHERE life_id=?", (payload, head.head_sha256, head.life_id))
            else:
                if expected_head_sha256 not in {None, "0" * 64}:
                    raise LifeShadowStoreError("life authority genesis CAS is invalid")
                connection.execute("INSERT INTO life_authority_heads(life_id,payload,payload_sha256) VALUES(?,?,?)", (head.life_id, payload, head.head_sha256))
            connection.execute("COMMIT"); return True
        except Exception:
            if connection.in_transaction: connection.execute("ROLLBACK")
            raise

    def put_run_life_binding(self, binding: RunLifeBinding) -> bool:
        binding, payload = _revalidate_contract(binding, RunLifeBinding, "run life binding")
        if binding.binding_sha256 != binding.computed_binding_sha256():
            raise LifeShadowStoreError("run life binding digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM run_life_bindings WHERE binding_id=?",
                (binding.binding_id,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("run life binding identity was rebound")
                connection.execute("COMMIT")
                return False
            head = connection.execute(
                "SELECT payload_sha256 FROM life_authority_heads WHERE life_id=?",
                (binding.life_id,),
            ).fetchone()
            if head is None or str(head["payload_sha256"]) != binding.life_authority_head_sha256:
                raise LifeShadowStoreError("run life binding authority head is stale or missing")
            connection.execute(
                "INSERT INTO run_life_bindings(binding_id,life_id,subject_kind,subject_id,payload,payload_sha256) VALUES(?,?,?,?,?,?)",
                (binding.binding_id, binding.life_id, binding.binding_subject_kind, binding.binding_subject_id, payload, binding.binding_sha256),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_root_experience_head(self, head: RootExperienceHead, *, expected_head_sha256: str | None) -> bool:
        head, payload = _revalidate_contract(head, RootExperienceHead, "root experience head")
        if head.head_sha256 != head.computed_head_sha256():
            raise LifeShadowStoreError("root experience head digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload,payload_sha256,root_status FROM root_experience_heads WHERE root_experience_id=?", (head.root_experience_id,)).fetchone()
            if existing is None:
                if expected_head_sha256 not in {None, "0" * 64}:
                    raise LifeShadowStoreError("root experience genesis CAS is invalid")
                connection.execute("INSERT INTO root_experience_heads(root_experience_id,life_id,root_status,payload,payload_sha256) VALUES(?,?,?,?,?)", (head.root_experience_id,head.life_id,head.root_status,payload,head.head_sha256))
            else:
                if bytes(existing["payload"]) == payload:
                    connection.execute("COMMIT"); return False
                if expected_head_sha256 != str(existing["payload_sha256"]):
                    raise LifeShadowStoreError("root experience head CAS is stale")
                transition = (str(existing["root_status"]), head.root_status)
                legal = {("OPEN", "WAITING"), ("OPEN", "CLOSED"), ("OPEN", "ABORTED"), ("WAITING", "ABORTED")}
                if transition == ("WAITING", "OPEN"):
                    raise LifeShadowStoreError("root continuation binding required for WAITING->OPEN")
                if transition not in legal:
                    raise LifeShadowStoreError("root experience transition is illegal")
                connection.execute("UPDATE root_experience_heads SET root_status=?,payload=?,payload_sha256=? WHERE root_experience_id=?", (head.root_status,payload,head.head_sha256,head.root_experience_id))
            connection.execute("COMMIT"); return True
        except Exception:
            if connection.in_transaction: connection.execute("ROLLBACK")
            raise

    def put_root_continuation_binding(self, binding: RootContinuationBinding, *, next_head: RootExperienceHead) -> bool:
        binding, payload = _revalidate_contract(binding, RootContinuationBinding, "root continuation binding")
        if binding.continuation_sha256 != binding.computed_continuation_sha256():
            raise LifeShadowStoreError("root continuation digest is invalid")
        if binding.root_experience_id != next_head.root_experience_id or next_head.root_status != "OPEN":
            raise LifeShadowStoreError("continuation target root is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload FROM root_continuation_bindings WHERE continuation_id=?", (binding.continuation_id,)).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload: raise LifeShadowStoreError("root continuation identity was rebound")
                connection.execute("COMMIT"); return False
            prior = connection.execute("SELECT payload,payload_sha256 FROM root_experience_heads WHERE root_experience_id=?", (binding.root_experience_id,)).fetchone()
            if prior is None or str(prior["payload_sha256"]) != binding.previous_root_head_sha256:
                raise LifeShadowStoreError("root continuation CAS is stale")
            current = _parse_stored_contract(bytes(prior["payload"]), RootExperienceHead, "root experience head")
            if current.root_status != "WAITING" or current.waiting_question_id != binding.reply_to_question_id or current.active_run_life_binding_sha256 != binding.previous_binding_sha256 or next_head.active_run_life_binding_sha256 != binding.next_binding_sha256:
                raise LifeShadowStoreError("root continuation preconditions are invalid")
            next_head, next_payload = _revalidate_contract(next_head, RootExperienceHead, "continued root head")
            connection.execute("INSERT INTO root_continuation_bindings(continuation_id,root_experience_id,previous_root_head_sha256,payload,payload_sha256) VALUES(?,?,?,?,?)", (binding.continuation_id,binding.root_experience_id,binding.previous_root_head_sha256,payload,binding.continuation_sha256))
            connection.execute("UPDATE root_experience_heads SET root_status=?,payload=?,payload_sha256=? WHERE root_experience_id=?", (next_head.root_status,next_payload,next_head.head_sha256,next_head.root_experience_id))
            connection.execute("COMMIT"); return True
        except Exception:
            if connection.in_transaction: connection.execute("ROLLBACK")
            raise

    def put_causal_episode_vnext(self, episode: CausalEpisodeVNext) -> bool:
        episode, payload = _revalidate_contract(episode, CausalEpisodeVNext, "causal child episode")
        if episode.episode_sha256 != episode.computed_episode_sha256():
            raise LifeShadowStoreError("causal child episode digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload FROM causal_episodes_vnext WHERE episode_id=?", (episode.episode_id,)).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload: raise LifeShadowStoreError("causal child identity was rebound")
                connection.execute("COMMIT"); return False
            root = connection.execute(
                "SELECT payload FROM root_experience_heads WHERE root_experience_id=?",
                (episode.root_experience_id,),
            ).fetchone()
            if root is None:
                raise LifeShadowStoreError("causal child root experience is missing")
            root_contract = _parse_stored_contract(bytes(root["payload"]), RootExperienceHead, "root experience head")
            if root_contract.root_status != "OPEN":
                raise LifeShadowStoreError("causal child root experience is not open")
            if root_contract.active_run_life_binding_sha256 != episode.run_life_binding_sha256:
                raise LifeShadowStoreError("causal child run life binding does not match root")
            binding_row = connection.execute(
                "SELECT 1 FROM run_life_bindings WHERE payload_sha256=?",
                (episode.run_life_binding_sha256,),
            ).fetchone()
            if binding_row is None:
                raise LifeShadowStoreError("causal child run life binding is missing")
            prior = connection.execute("SELECT episode_id,payload_sha256 FROM causal_episodes_vnext WHERE root_experience_id=? AND sequence_no=?", (episode.root_experience_id, episode.sequence_no - 1)).fetchone()
            if episode.sequence_no == 1:
                if prior is not None: raise LifeShadowStoreError("causal child genesis sequence is invalid")
            elif prior is None or str(prior["payload_sha256"]) != episode.predecessor_episode_sha256 or str(prior["episode_id"]) != episode.predecessor_episode_id:
                raise LifeShadowStoreError("causal child predecessor is stale or missing")
            connection.execute("INSERT INTO causal_episodes_vnext(episode_id,root_experience_id,sequence_no,predecessor_episode_sha256,terminal_status,payload,payload_sha256) VALUES(?,?,?,?,?,?,?)", (episode.episode_id,episode.root_experience_id,episode.sequence_no,episode.predecessor_episode_sha256,episode.terminal_status,payload,episode.episode_sha256))
            connection.execute("COMMIT"); return True
        except Exception:
            if connection.in_transaction: connection.execute("ROLLBACK")
            raise

    def enqueue_stimulus(
        self,
        life_id: str,
        event_id: str,
        *,
        lane: str,
        base_priority: int,
        payload_sha256: str,
        enqueued_at_ms: int,
        coalesce: bool = False,
    ) -> bool:
        """Persist one stimulus inbox row; dedupe by event_id, heartbeat coalesce."""
        if lane not in {"foreground", "background"} or base_priority < 0 or len(payload_sha256) != 64 or enqueued_at_ms < 0:
            raise LifeShadowStoreError("stimulus inbox fields are invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM stimulus_inbox WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return False
            if coalesce and lane == "background":
                merged = connection.execute(
                    "SELECT 1 FROM stimulus_inbox WHERE life_id=? AND lane='background' AND payload_sha256=? AND status='pending'",
                    (life_id, payload_sha256),
                ).fetchone()
                if merged is not None:
                    connection.execute("COMMIT")
                    return False
            connection.execute(
                "INSERT INTO stimulus_inbox(life_id,lane,base_priority,event_id,payload_sha256,enqueued_at_ms,status) VALUES(?,?,?,?,?,?,'pending')",
                (life_id, lane, base_priority, event_id, payload_sha256, enqueued_at_ms),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def select_next_stimulus(
        self,
        life_id: str,
        *,
        claim_token: str,
        now_ms: int,
        max_foreground_streak: int,
    ) -> dict[str, object] | None:
        """Select and durably claim one stimulus: foreground FIFO with background anti-starvation."""
        if max_foreground_streak < 1:
            raise LifeShadowStoreError("max foreground streak must be positive")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT foreground_streak FROM cognition_state WHERE life_id=?", (life_id,)
            ).fetchone()
            streak = int(state["foreground_streak"]) if state is not None else 0
            foreground = connection.execute(
                "SELECT enqueue_seq,lane,base_priority,event_id,payload_sha256 FROM stimulus_inbox "
                "WHERE life_id=? AND lane='foreground' AND status='pending' ORDER BY base_priority DESC, enqueue_seq ASC LIMIT 1",
                (life_id,),
            ).fetchone()
            background = connection.execute(
                "SELECT enqueue_seq,lane,base_priority,event_id,payload_sha256 FROM stimulus_inbox "
                "WHERE life_id=? AND lane='background' AND status='pending' ORDER BY base_priority DESC, enqueue_seq ASC LIMIT 1",
                (life_id,),
            ).fetchone()
            chosen = None
            next_lane: str | None = None
            if foreground is not None and background is not None and streak >= max_foreground_streak:
                chosen, next_lane = background, "background"
            elif foreground is not None:
                chosen, next_lane = foreground, "foreground"
            elif background is not None:
                chosen, next_lane = background, "background"
            if chosen is None:
                connection.execute("COMMIT")
                return None
            connection.execute(
                "UPDATE stimulus_inbox SET status='selected', selected_at_ms=?, claim_token=? WHERE enqueue_seq=? AND status='pending'",
                (now_ms, claim_token, int(chosen["enqueue_seq"])),
            )
            new_streak = streak + 1 if next_lane == "foreground" else 0
            connection.execute(
                "INSERT INTO cognition_state(life_id,foreground_streak,last_selected_lane,model_inflight_count,updated_at_ms) VALUES(?,?,?,0,?) "
                "ON CONFLICT(life_id) DO UPDATE SET foreground_streak=excluded.foreground_streak, last_selected_lane=excluded.last_selected_lane, updated_at_ms=excluded.updated_at_ms",
                (life_id, new_streak, next_lane, now_ms),
            )
            connection.execute("COMMIT")
            return {
                "enqueue_seq": int(chosen["enqueue_seq"]),
                "lane": str(chosen["lane"]),
                "base_priority": int(chosen["base_priority"]),
                "event_id": str(chosen["event_id"]),
                "payload_sha256": str(chosen["payload_sha256"]),
            }
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def commit_stimulus(self, life_id: str, *, enqueue_seq: int, claim_token: str, now_ms: int) -> bool:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,claim_token FROM stimulus_inbox WHERE enqueue_seq=? AND life_id=?",
                (enqueue_seq, life_id),
            ).fetchone()
            if row is None or str(row["claim_token"]) != claim_token:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("stimulus claim token is invalid")
            if str(row["status"]) == "committed":
                connection.execute("COMMIT")
                return False
            connection.execute(
                "UPDATE stimulus_inbox SET status='committed' WHERE enqueue_seq=?", (enqueue_seq,)
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def release_stimulus(self, *, enqueue_seq: int, claim_token: str) -> bool:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE stimulus_inbox SET status='pending', selected_at_ms=NULL, claim_token=NULL WHERE enqueue_seq=? AND claim_token=?",
                (enqueue_seq, claim_token),
            )
            connection.execute("COMMIT")
            return updated.rowcount == 1
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def acquire_lane(
        self,
        life_id: str,
        lane: str,
        *,
        owner_instance_id: str,
        now_ms: int,
        duration_ms: int,
    ) -> str | None:
        """Acquire one per-life cognition lane; foreground preempts background."""
        if lane not in {"foreground", "background"} or duration_ms <= 0:
            raise LifeShadowStoreError("cognition lane lease fields are invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM cognition_lane_leases WHERE life_id=? AND lane=?",
                (life_id, lane),
            ).fetchone()
            if existing is not None and str(existing["status"]) == "active":
                connection.execute("COMMIT")
                return None
            if lane == "foreground":
                connection.execute(
                    "DELETE FROM cognition_lane_leases WHERE life_id=? AND lane='background' AND status='active'",
                    (life_id,),
                )
            generation_row = connection.execute(
                "SELECT COALESCE(MAX(generation),0)+1 AS next_generation FROM cognition_lane_leases WHERE life_id=? AND lane=?",
                (life_id, lane),
            ).fetchone()
            generation = int(generation_row["next_generation"])
            lease_id = "cln_" + hashlib.sha256(
                (
                    f"tiangong.v21.cognition-lane.v1\0{life_id}\0{lane}\0{generation}\0"
                    f"{owner_instance_id}\0{now_ms}"
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT OR REPLACE INTO cognition_lane_leases(life_id,lane,lease_id,owner_instance_id,generation,acquired_at_ms,expires_at_ms,status,preempted_by) VALUES(?,?,?,?,?,?,?,'active',NULL)",
                (life_id, lane, lease_id, owner_instance_id, generation, now_ms, now_ms + duration_ms),
            )
            connection.execute("COMMIT")
            return lease_id
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def renew_lane(self, life_id: str, lane: str, *, lease_id: str, now_ms: int, duration_ms: int) -> bool:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE cognition_lane_leases SET expires_at_ms=? WHERE life_id=? AND lane=? AND lease_id=? AND status='active'",
                (now_ms + duration_ms, life_id, lane, lease_id),
            )
            connection.execute("COMMIT")
            return updated.rowcount == 1
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def release_lane(self, life_id: str, lane: str, *, lease_id: str) -> bool:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "DELETE FROM cognition_lane_leases WHERE life_id=? AND lane=? AND lease_id=? AND status='active'",
                (life_id, lane, lease_id),
            )
            connection.execute("COMMIT")
            return updated.rowcount == 1
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_model_attempt_shadow(
        self,
        *,
        attempt_shadow_id: str,
        life_id: str,
        root_experience_id: str,
        episode_id: str,
        lane: str,
        slot_no: int,
        provider: str,
        model: str,
        request_sha256: str,
        status: str,
        finish_reason: str | None,
        output_text_sha256: str | None,
        started_at_ms: int,
        completed_at_ms: int | None,
        payload_sha256: str,
    ) -> bool:
        valid_status = {"plan_frozen", "dispatched", "succeeded", "failed", "preflight_unavailable"}
        if (
            lane not in {"foreground", "background"}
            or slot_no < 1
            or len(request_sha256) != 64
            or status not in valid_status
            or len(payload_sha256) != 64
        ):
            raise LifeShadowStoreError("model attempt shadow fields are invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_sha256 FROM model_attempt_shadow WHERE attempt_shadow_id=?",
                (attempt_shadow_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise LifeShadowStoreError("model attempt shadow identity was rebound")
                connection.execute("COMMIT")
                return False
            connection.execute(
                "INSERT INTO model_attempt_shadow(attempt_shadow_id,life_id,root_experience_id,episode_id,lane,slot_no,provider,model,request_sha256,status,finish_reason,output_text_sha256,started_at_ms,completed_at_ms,payload_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_shadow_id, life_id, root_experience_id, episode_id, lane, slot_no,
                    provider, model, request_sha256, status, finish_reason, output_text_sha256,
                    started_at_ms, completed_at_ms, payload_sha256,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def cognition_health(self, life_id: str) -> dict[str, int]:
        connection = self._connection
        pending = int(connection.execute(
            "SELECT count(*) FROM stimulus_inbox WHERE life_id=? AND status='pending'", (life_id,)
        ).fetchone()[0])
        selected = int(connection.execute(
            "SELECT count(*) FROM stimulus_inbox WHERE life_id=? AND status='selected'", (life_id,)
        ).fetchone()[0])
        committed = int(connection.execute(
            "SELECT count(*) FROM stimulus_inbox WHERE life_id=? AND status='committed'", (life_id,)
        ).fetchone()[0])
        active_lanes = int(connection.execute(
            "SELECT count(*) FROM cognition_lane_leases WHERE life_id=? AND status='active'", (life_id,)
        ).fetchone()[0])
        inflight = 0
        state = connection.execute(
            "SELECT model_inflight_count, foreground_streak FROM cognition_state WHERE life_id=?",
            (life_id,),
        ).fetchone()
        if state is not None:
            inflight = int(state["model_inflight_count"])
        return {
            "pending": pending,
            "selected": selected,
            "committed": committed,
            "active_lanes": active_lanes,
            "model_inflight": inflight,
            "foreground_streak": int(state["foreground_streak"]) if state is not None else 0,
        }

    def put_life_turn_commit(self, commit: LifeTurnCommit, *, now_ms: int) -> bool:
        """Append one journal-authoritative response stage with chain CAS."""
        commit, payload = _revalidate_contract(commit, LifeTurnCommit, "life turn commit")
        if commit.commit_sha256 != commit.computed_commit_sha256():
            raise LifeShadowStoreError("life turn commit digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_sha256 FROM life_turn_commits WHERE turn_commit_id=?",
                (commit.turn_commit_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != commit.commit_sha256:
                    raise LifeShadowStoreError("life turn commit identity was rebound")
                connection.execute("COMMIT")
                return False
            triple = connection.execute(
                "SELECT turn_commit_id FROM life_turn_commits WHERE root_experience_id=? AND response_episode_id=? AND stage=?",
                (commit.root_experience_id, commit.response_episode_id, commit.stage),
            ).fetchone()
            if triple is not None:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("life turn commit stage was reused")
            previous = connection.execute(
                """
                SELECT stage, payload_sha256, payload FROM life_turn_commits
                WHERE root_experience_id=? AND response_episode_id=?
                ORDER BY CASE stage
                    WHEN 'OUTCOME_COMMITTED_RESPONSE_OPEN' THEN 0
                    WHEN 'RESPONSE_COMMITTED' THEN 1
                    WHEN 'DELIVERY_OBSERVED' THEN 2
                    ELSE 3
                END DESC LIMIT 1
                """,
                (commit.root_experience_id, commit.response_episode_id),
            ).fetchone()
            expected_previous = _LIFE_TURN_STAGE_PRECEDENCE[commit.stage]
            if expected_previous is None:
                if previous is not None or commit.predecessor_commit_sha256 is not None:
                    raise LifeShadowStoreError("first turn commit must open the chain")
            else:
                if previous is None or str(previous["stage"]) != expected_previous:
                    raise LifeShadowStoreError("life turn commit stage transition is invalid")
                if commit.predecessor_commit_sha256 != str(previous["payload_sha256"]):
                    raise LifeShadowStoreError("life turn commit predecessor chain is invalid")
                prior = _parse_stored_contract(
                    bytes(previous["payload"]), LifeTurnCommit, "life turn commit"
                )
                preserved = (
                    prior.life_id == commit.life_id
                    and prior.run_life_binding_sha256 == commit.run_life_binding_sha256
                    and prior.root_experience_id == commit.root_experience_id
                    and prior.child_episode_id == commit.child_episode_id
                    and prior.response_episode_id == commit.response_episode_id
                    and prior.response_basis_kind == commit.response_basis_kind
                    and prior.response_basis_sha256 == commit.response_basis_sha256
                    and prior.completion_delivery_mode == commit.completion_delivery_mode
                )
                if not preserved:
                    raise LifeShadowStoreError("life turn commit identity fields changed across stages")
            connection.execute(
                """
                INSERT INTO life_turn_commits(
                    turn_commit_id, life_id, root_experience_id, child_episode_id,
                    response_episode_id, stage, predecessor_commit_sha256, payload,
                    payload_sha256, created_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    commit.turn_commit_id, commit.life_id, commit.root_experience_id,
                    commit.child_episode_id, commit.response_episode_id, commit.stage,
                    commit.predecessor_commit_sha256, payload, commit.commit_sha256, now_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def list_life_turn_commits(
        self, *, root_experience_id: str, response_episode_id: str
    ) -> tuple[LifeTurnCommit, ...]:
        rows = self._connection.execute(
            """
            SELECT payload FROM life_turn_commits
            WHERE root_experience_id=? AND response_episode_id=?
            ORDER BY CASE stage
                WHEN 'OUTCOME_COMMITTED_RESPONSE_OPEN' THEN 0
                WHEN 'RESPONSE_COMMITTED' THEN 1
                WHEN 'DELIVERY_OBSERVED' THEN 2
                ELSE 3
            END
            """,
            (root_experience_id, response_episode_id),
        ).fetchall()
        return tuple(
            _parse_stored_contract(bytes(row["payload"]), LifeTurnCommit, "life turn commit")
            for row in rows
        )

    def put_capability_candidate(
        self,
        *,
        candidate_id: str,
        life_id: str,
        skill_id: str,
        skill_version: str,
        artifact_sha256: str,
        source_fact_refs: tuple[str, ...],
        phase: str,
        payload_sha256: str,
        created_at_ms: int,
    ) -> bool:
        """Register one immutable capability candidate artifact."""
        if phase not in {"DRAFT", "COMPILED"} or len(artifact_sha256) != 64 or len(payload_sha256) != 64:
            raise LifeShadowStoreError("capability candidate fields are invalid")
        refs_json = json.dumps(sorted(set(source_fact_refs)), sort_keys=True, separators=(",", ":"))
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_sha256 FROM capability_candidate_artifacts WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise LifeShadowStoreError("capability candidate identity was rebound")
                connection.execute("COMMIT")
                return False
            connection.execute(
                """
                INSERT INTO capability_candidate_artifacts(
                    candidate_id, life_id, skill_id, skill_version, artifact_sha256,
                    source_fact_refs_json, phase, payload_sha256, created_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id, life_id, skill_id, skill_version, artifact_sha256,
                    refs_json, phase, payload_sha256, created_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def advance_capability_candidate(
        self,
        *,
        candidate_id: str,
        to_phase: str,
        expected_phase: str,
        payload_sha256: str,
    ) -> bool:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT phase, payload_sha256 FROM capability_candidate_artifacts WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if existing is None:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("capability candidate is missing")
            if str(existing["payload_sha256"]) != payload_sha256:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("capability candidate payload mismatch")
            current_phase = str(existing["phase"])
            if current_phase != expected_phase:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("capability candidate phase CAS is stale")
            if (current_phase, to_phase) not in _CAPABILITY_PHASE_TRANSITIONS:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("capability candidate phase transition is illegal")
            connection.execute(
                "UPDATE capability_candidate_artifacts SET phase=? WHERE candidate_id=?",
                (to_phase, candidate_id),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_capability_pointer(
        self,
        *,
        life_id: str,
        skill_id: str,
        candidate_id: str,
        artifact_sha256: str,
        pointer_sha256: str,
        expected_pointer_sha256: str | None,
        now_ms: int,
    ) -> bool:
        """CAS current pointer: mutation without CAS or stale expected is rejected."""
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT pointer_sha256 FROM capability_pointer_heads WHERE life_id=? AND skill_id=?",
                (life_id, skill_id),
            ).fetchone()
            if existing is not None:
                if str(existing["pointer_sha256"]) != (expected_pointer_sha256 or ""):
                    connection.execute("ROLLBACK")
                    raise LifeShadowStoreError("capability pointer CAS is stale")
                connection.execute(
                    """
                    UPDATE capability_pointer_heads SET current_candidate_id=?, current_artifact_sha256=?,
                    pointer_sha256=?, revision=revision+1, updated_at_ms=? WHERE life_id=? AND skill_id=?
                    """,
                    (candidate_id, artifact_sha256, pointer_sha256, now_ms, life_id, skill_id),
                )
                connection.execute("COMMIT")
                return True
            if expected_pointer_sha256 not in {None, "0" * 64}:
                connection.execute("ROLLBACK")
                raise LifeShadowStoreError("capability pointer genesis CAS is invalid")
            connection.execute(
                """
                INSERT INTO capability_pointer_heads(
                    life_id, skill_id, current_candidate_id, current_artifact_sha256,
                    pointer_sha256, revision, updated_at_ms
                ) VALUES (?,?,?,?,?,1,?)
                """,
                (life_id, skill_id, candidate_id, artifact_sha256, pointer_sha256, now_ms),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def get_capability_pointer(self, *, life_id: str, skill_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            """
            SELECT current_candidate_id, current_artifact_sha256, pointer_sha256, revision
            FROM capability_pointer_heads WHERE life_id=? AND skill_id=?
            """,
            (life_id, skill_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "current_candidate_id": str(row["current_candidate_id"]),
            "current_artifact_sha256": str(row["current_artifact_sha256"]),
            "pointer_sha256": str(row["pointer_sha256"]),
            "revision": int(row["revision"]),
        }

    @staticmethod
    def _protected_payload_record_from_row(
        row: sqlite3.Row,
        *,
        key_available: bool,
    ) -> ProtectedPayloadRecord:
        ciphertext = bytes(row["ciphertext"])
        if hashlib.sha256(ciphertext).hexdigest() != str(row["ciphertext_sha256"]):
            raise LifeShadowStoreError("protected payload ciphertext digest is invalid")
        return ProtectedPayloadRecord(
            payload_id=str(row["payload_id"]),
            life_id=str(row["life_id"]),
            privacy_scope=str(row["privacy_scope"]),
            ciphertext_sha256=str(row["ciphertext_sha256"]),
            created_at_ms=int(row["created_at_ms"]),
            key_available=key_available,
            key_destroyed_at_ms=(
                None
                if row["key_destroyed_at_ms"] is None
                else int(row["key_destroyed_at_ms"])
            ),
        )

    def _put_protected_payload_locked(
        self,
        plaintext: bytes,
        *,
        life_id: str,
        privacy_scope: str,
        created_at_ms: int,
    ) -> ProtectedPayloadRecord:
        if (
            not isinstance(plaintext, bytes)
            or not plaintext
            or len(plaintext) > 16 * 1024 * 1024
            or not life_id
            or not privacy_scope
            or isinstance(created_at_ms, bool)
            or not isinstance(created_at_ms, int)
            or created_at_ms < 0
        ):
            raise ValueError("protected payload input is invalid")
        payload_id = "ppd_" + secrets.token_hex(32)
        if self._connection.execute(
            "SELECT 1 FROM protected_payloads WHERE payload_id = ?", (payload_id,)
        ).fetchone() is not None:
            raise LifeShadowStoreError("protected payload random identity collided")
        key = AESGCM.generate_key(bit_length=256)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(
            nonce,
            plaintext,
            _protected_payload_aad(
                payload_id=payload_id,
                life_id=life_id,
                privacy_scope=privacy_scope,
            ),
        )
        digest = hashlib.sha256(ciphertext).hexdigest()
        self._connection.execute(
            """
            INSERT INTO protected_payloads(
                payload_id, life_id, privacy_scope, nonce, ciphertext,
                ciphertext_sha256, created_at_ms, key_destroyed_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                payload_id,
                life_id,
                privacy_scope,
                nonce,
                ciphertext,
                digest,
                created_at_ms,
            ),
        )
        self._connection.execute(
            """
            INSERT INTO protected_payload_keys(payload_id, key_material, created_at_ms)
            VALUES (?, ?, ?)
            """,
            (payload_id, key, created_at_ms),
        )
        row = self._connection.execute(
            "SELECT * FROM protected_payloads WHERE payload_id = ?", (payload_id,)
        ).fetchone()
        assert row is not None
        return self._protected_payload_record_from_row(row, key_available=True)

    def put_protected_payload(
        self,
        plaintext: bytes,
        *,
        life_id: str,
        privacy_scope: str,
        created_at_ms: int,
    ) -> ProtectedPayloadRecord:
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = self._put_protected_payload_locked(
                plaintext,
                life_id=life_id,
                privacy_scope=privacy_scope,
                created_at_ms=created_at_ms,
            )
            connection.execute("COMMIT")
            return record
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def get_protected_payload(self, payload_id: str) -> ProtectedPayloadRecord | None:
        row = self._connection.execute(
            "SELECT * FROM protected_payloads WHERE payload_id = ?", (payload_id,)
        ).fetchone()
        if row is None:
            return None
        key = self._connection.execute(
            "SELECT 1 FROM protected_payload_keys WHERE payload_id = ?", (payload_id,)
        ).fetchone()
        return self._protected_payload_record_from_row(
            row, key_available=key is not None
        )

    def read_protected_payload(self, payload_id: str) -> bytes:
        row = self._connection.execute(
            """
            SELECT p.*, k.key_material
            FROM protected_payloads AS p
            LEFT JOIN protected_payload_keys AS k ON k.payload_id = p.payload_id
            WHERE p.payload_id = ?
            """,
            (payload_id,),
        ).fetchone()
        if row is None:
            raise LifeShadowStoreError("protected payload does not exist")
        self._protected_payload_record_from_row(
            row, key_available=row["key_material"] is not None
        )
        if row["key_material"] is None or row["key_destroyed_at_ms"] is not None:
            raise LifeShadowStoreError("protected payload key is unavailable")
        try:
            return AESGCM(bytes(row["key_material"])).decrypt(
                bytes(row["nonce"]),
                bytes(row["ciphertext"]),
                _protected_payload_aad(
                    payload_id=str(row["payload_id"]),
                    life_id=str(row["life_id"]),
                    privacy_scope=str(row["privacy_scope"]),
                ),
            )
        except Exception as exc:
            raise LifeShadowStoreError("protected payload cannot be decrypted") from exc

    def _get_or_create_index_key_locked(
        self, life_id: str, *, created_at_ms: int
    ) -> bytes:
        row = self._connection.execute(
            "SELECT key_material FROM life_index_keys WHERE life_id = ?", (life_id,)
        ).fetchone()
        if row is not None:
            return bytes(row["key_material"])
        key = secrets.token_bytes(32)
        self._connection.execute(
            "INSERT INTO life_index_keys(life_id, key_material, created_at_ms) VALUES (?, ?, ?)",
            (life_id, key, created_at_ms),
        )
        return key

    @staticmethod
    def _term_digests(key: bytes, terms: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({_normalize_search_term(term) for term in terms}))
        return tuple(
            sorted(
                hmac.new(key, term.encode("utf-8"), hashlib.sha256).hexdigest()
                for term in normalized
            )
        )

    def _assert_protected_payload_binding_locked(
        self,
        *,
        payload_id: str,
        payload_sha256: str,
        life_id: str,
        privacy_scope: str,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT p.*, k.payload_id AS key_id
            FROM protected_payloads AS p
            LEFT JOIN protected_payload_keys AS k ON k.payload_id = p.payload_id
            WHERE p.payload_id = ?
            """,
            (payload_id,),
        ).fetchone()
        if (
            row is None
            or row["key_id"] is None
            or row["key_destroyed_at_ms"] is not None
            or str(row["ciphertext_sha256"]) != payload_sha256
            or str(row["life_id"]) != life_id
            or str(row["privacy_scope"]) != privacy_scope
        ):
            raise LifeShadowStoreError("protected payload binding is invalid")

    def _record_memory_change_locked(
        self,
        *,
        life_id: str,
        memory_id: str,
        revision: int,
        change_kind: str,
        assertion_sha256: str,
        created_at_ms: int,
    ) -> int:
        """Append one globally monotonic memory change plus its outbox row.

        Must be called inside an open transaction so the change and the
        assertion/tombstone it describes commit or roll back together.
        """

        if change_kind not in {"assert", "revise", "tombstone"}:
            raise LifeShadowStoreError("memory change kind is invalid")
        connection = self._connection
        row = connection.execute(
            "SELECT COALESCE(MAX(change_seq), 0) + 1 AS next_seq FROM memory_change_log"
        ).fetchone()
        change_seq = int(row["next_seq"])
        connection.execute(
            """
            INSERT INTO memory_change_log(
                change_seq, life_id, memory_id, revision, change_kind,
                assertion_sha256, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_seq,
                life_id,
                memory_id,
                revision,
                change_kind,
                assertion_sha256,
                created_at_ms,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_outbox(
                change_seq, life_id, memory_id, change_kind, created_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (change_seq, life_id, memory_id, change_kind, created_at_ms),
        )
        return change_seq

    def _put_memory_assertion_locked(
        self,
        assertion: MemoryAssertionV3,
        payload: bytes,
        *,
        search_terms: tuple[str, ...] = (),
    ) -> tuple[bool, int | None]:
        """Transaction-scoped body of ``put_memory_assertion``.

        Returns ``(created, change_seq)``; the change seq is committed in the
        same transaction as the assertion revision it numbers.
        """

        connection = self._connection
        existing = connection.execute(
            """
            SELECT a.*, c.payload, c.assertion_sha256
            FROM memory_assertions AS a
            JOIN memory_assertion_contracts AS c
              ON c.memory_id = a.memory_id AND c.revision = a.revision
            WHERE a.memory_id = ? AND a.revision = ?
            """,
            (assertion.memory_id, assertion.revision),
        ).fetchone()
        if existing is not None:
            if (
                bytes(existing["payload"]) != payload
                or str(existing["assertion_sha256"])
                != assertion.assertion_sha256
            ):
                raise LifeShadowStoreError("memory assertion identity was rebound")
            return False, None
        previous = connection.execute(
            """
            SELECT a.revision, a.status, c.assertion_sha256
            FROM memory_assertions AS a
            JOIN memory_assertion_contracts AS c
              ON c.memory_id = a.memory_id AND c.revision = a.revision
            WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
            """,
            (assertion.memory_id,),
        ).fetchone()
        if assertion.revision == 1:
            if previous is not None:
                raise LifeShadowStoreError("memory assertion genesis already exists")
        elif (
            previous is None
            or int(previous["revision"]) + 1 != assertion.revision
            or str(previous["assertion_sha256"])
            != assertion.supersedes_assertion_sha256
            or str(previous["status"]) == "deleted"
        ):
            raise LifeShadowStoreError("memory assertion revision is discontinuous")
        assert assertion.protected_payload_id is not None
        assert assertion.protected_payload_sha256 is not None
        self._assert_protected_payload_binding_locked(
            payload_id=assertion.protected_payload_id,
            payload_sha256=assertion.protected_payload_sha256,
            life_id=assertion.life_id,
            privacy_scope=assertion.privacy_scope,
        )
        connection.execute(
            "DELETE FROM memory_search_terms WHERE memory_id = ?",
            (assertion.memory_id,),
        )
        connection.execute(
            """
            INSERT INTO memory_assertions(
                memory_id, revision, life_id, status, privacy_scope,
                payload_object_id, payload_sha256, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assertion.memory_id,
                assertion.revision,
                assertion.life_id,
                assertion.lifecycle_status,
                assertion.privacy_scope,
                assertion.protected_payload_id,
                assertion.protected_payload_sha256,
                assertion.created_at_ms,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_assertion_contracts(
                memory_id, revision, payload, assertion_sha256
            ) VALUES (?, ?, ?, ?)
            """,
            (
                assertion.memory_id,
                assertion.revision,
                payload,
                assertion.assertion_sha256,
            ),
        )
        if assertion.lifecycle_status == "active" and search_terms:
            key = self._get_or_create_index_key_locked(
                assertion.life_id, created_at_ms=assertion.created_at_ms
            )
            for digest in self._term_digests(key, search_terms):
                connection.execute(
                    """
                    INSERT INTO memory_search_terms(
                        memory_id, revision, term_hmac_sha256,
                        privacy_scope, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        assertion.memory_id,
                        assertion.revision,
                        digest,
                        assertion.privacy_scope,
                        assertion.created_at_ms,
                    ),
                )
        change_seq = self._record_memory_change_locked(
            life_id=assertion.life_id,
            memory_id=assertion.memory_id,
            revision=assertion.revision,
            change_kind="assert" if assertion.revision == 1 else "revise",
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=assertion.created_at_ms,
        )
        return True, change_seq

    def put_memory_assertion(
        self,
        assertion: MemoryAssertionV3,
        *,
        search_terms: tuple[str, ...] = (),
    ) -> bool:
        assertion, payload = _revalidate_contract(
            assertion, MemoryAssertionV3, "memory assertion"
        )
        if not assertion.has_valid_assertion_sha256():
            raise LifeShadowStoreError("memory assertion digest is invalid")
        if assertion.lifecycle_status == "deleted":
            raise LifeShadowStoreError(
                "deleted memory must be committed by the privacy-deletion boundary"
            )
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            created, _ = self._put_memory_assertion_locked(
                assertion, payload, search_terms=search_terms
            )
            connection.execute("COMMIT")
            return created
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_live_memory_assertion(
        self,
        plaintext: bytes,
        *,
        memory_id: str,
        life_id: str,
        assertion_kind: str,
        epistemic_status: str,
        lifecycle_status: str,
        privacy_scope: str,
        retention_class: str,
        source_event_ids: tuple[str, ...] = (),
        causal_utility_milli: int = 0,
        user_importance_milli: int = 0,
        verification_strength_milli: int = 0,
        future_dependency_milli: int = 0,
        valid_from_ms: int,
        created_at_ms: int,
        search_terms: tuple[str, ...] = (),
    ) -> tuple[MemoryAssertionV3, int, bool]:
        """Commit one live user-fact assertion with its payload atomically.

        The protected payload, the assertion revision, the global
        ``memory_change_seq`` row and the outbox row commit in one
        transaction.  Repeating the same call (same memory, same latest
        revision, same plaintext, same lifecycle status) is an idempotent
        no-op that returns the existing assertion and its original change
        seq instead of opening a new revision.
        """

        if lifecycle_status == "deleted":
            raise LifeShadowStoreError(
                "deleted memory must be committed by the privacy-deletion boundary"
            )
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest_row = connection.execute(
                """
                SELECT a.*, c.payload
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
                """,
                (memory_id,),
            ).fetchone()
            if latest_row is not None:
                latest = _parse_stored_contract(
                    bytes(latest_row["payload"]), MemoryAssertionV3, "memory assertion"
                )
                if latest.lifecycle_status == "deleted":
                    raise LifeShadowStoreError("live memory assertion was deleted")
                assert latest.protected_payload_id is not None
                try:
                    previous_plaintext = self.read_protected_payload(
                        latest.protected_payload_id
                    )
                except LifeShadowStoreError as exc:
                    raise LifeShadowStoreError(
                        "live memory plaintext cannot be verified"
                    ) from exc
                if previous_plaintext != plaintext:
                    raise LifeShadowStoreError("live memory content drifted")
                if latest.lifecycle_status == lifecycle_status:
                    change_row = connection.execute(
                        """
                        SELECT change_seq FROM memory_change_log
                        WHERE memory_id = ? AND revision = ?
                        """,
                        (latest.memory_id, latest.revision),
                    ).fetchone()
                    if change_row is None:
                        # Assertions written before the change ledger existed
                        # (legacy migration data) are enrolled on first touch
                        # so every live assertion is seq-addressable.
                        backfilled_seq = self._record_memory_change_locked(
                            life_id=latest.life_id,
                            memory_id=latest.memory_id,
                            revision=latest.revision,
                            change_kind=(
                                "assert" if latest.revision == 1 else "revise"
                            ),
                            assertion_sha256=latest.assertion_sha256,
                            created_at_ms=latest.created_at_ms,
                        )
                        connection.execute("COMMIT")
                        return latest, backfilled_seq, False
                    connection.execute("COMMIT")
                    return latest, int(change_row["change_seq"]), False
                revision = latest.revision + 1
                supersedes = latest.assertion_sha256
                protected_payload_id = latest.protected_payload_id
                protected_payload_sha256 = latest.protected_payload_sha256
            else:
                latest = None
                revision = 1
                supersedes = None
                protected = self._put_protected_payload_locked(
                    plaintext,
                    life_id=life_id,
                    privacy_scope=privacy_scope,
                    created_at_ms=created_at_ms,
                )
                protected_payload_id = protected.payload_id
                protected_payload_sha256 = protected.ciphertext_sha256
            try:
                assertion = MemoryAssertionV3(
                    memory_id=memory_id,
                    life_id=life_id,
                    revision=revision,
                    supersedes_assertion_sha256=supersedes,
                    assertion_kind=assertion_kind,
                    epistemic_status=epistemic_status,
                    lifecycle_status=lifecycle_status,
                    protected_payload_id=protected_payload_id,
                    protected_payload_sha256=protected_payload_sha256,
                    deletion_tombstone_id=None,
                    privacy_scope=privacy_scope,
                    retention_class=retention_class,
                    source_event_ids=tuple(sorted(set(source_event_ids))),
                    causal_hypothesis_ids=(),
                    causal_utility_milli=causal_utility_milli,
                    user_importance_milli=user_importance_milli,
                    verification_strength_milli=verification_strength_milli,
                    recurrence_count=0,
                    future_dependency_milli=future_dependency_milli,
                    privacy_cost_milli=500,
                    contradiction_penalty_milli=0,
                    staleness_milli=0,
                    valid_from_ms=valid_from_ms,
                    expires_at_ms=None,
                    created_at_ms=created_at_ms,
                    assertion_sha256="0" * 64,
                ).with_computed_assertion_sha256()
            except Exception as exc:
                raise LifeShadowStoreError("live memory assertion contract is invalid") from exc
            payload = canonical_json_bytes(assertion)
            created, change_seq = self._put_memory_assertion_locked(
                assertion, payload, search_terms=search_terms
            )
            assert created and change_seq is not None
            connection.execute("COMMIT")
            return assertion, change_seq, True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def memory_change_head(self, life_id: str | None = None) -> int:
        """Return the greatest committed memory change seq (0 when empty)."""

        if life_id is None:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(change_seq), 0) AS head FROM memory_change_log"
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(change_seq), 0) AS head FROM memory_change_log WHERE life_id = ?",
                (life_id,),
            ).fetchone()
        return int(row["head"])

    def memory_change_seq_for(self, memory_id: str, revision: int) -> int | None:
        row = self._connection.execute(
            """
            SELECT change_seq FROM memory_change_log
            WHERE memory_id = ? AND revision = ?
            """,
            (memory_id, revision),
        ).fetchone()
        return None if row is None else int(row["change_seq"])

    def list_memory_outbox(
        self,
        *,
        life_id: str | None = None,
        pending_only: bool = True,
        limit: int = 256,
    ) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 4096:
            raise ValueError("memory outbox limit is invalid")
        clauses = []
        values: list[object] = []
        if life_id is not None:
            clauses.append("o.life_id = ?")
            values.append(life_id)
        if pending_only:
            clauses.append("o.receipt_id IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT o.change_seq, o.life_id, o.memory_id, o.change_kind,
                   o.receipt_id, o.receipt_sha256, o.delivered_at_ms,
                   o.created_at_ms, l.revision, l.assertion_sha256
            FROM memory_outbox AS o
            JOIN memory_change_log AS l ON l.change_seq = o.change_seq
            {where}
            ORDER BY o.change_seq
            LIMIT ?
            """,
            (*values, limit),
        ).fetchall()
        return tuple(
            {
                "change_seq": int(row["change_seq"]),
                "life_id": str(row["life_id"]),
                "memory_id": str(row["memory_id"]),
                "revision": int(row["revision"]),
                "change_kind": str(row["change_kind"]),
                "assertion_sha256": str(row["assertion_sha256"]),
                "receipt_id": (
                    None if row["receipt_id"] is None else str(row["receipt_id"])
                ),
                "receipt_sha256": (
                    None
                    if row["receipt_sha256"] is None
                    else str(row["receipt_sha256"])
                ),
                "delivered_at_ms": (
                    None
                    if row["delivered_at_ms"] is None
                    else int(row["delivered_at_ms"])
                ),
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        )

    def count_pending_memory_outbox(self, life_id: str | None = None) -> int:
        if life_id is None:
            row = self._connection.execute(
                "SELECT count(*) AS pending FROM memory_outbox WHERE receipt_id IS NULL"
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT count(*) AS pending FROM memory_outbox WHERE receipt_id IS NULL AND life_id = ?",
                (life_id,),
            ).fetchone()
        return int(row["pending"])

    def ack_memory_outbox(
        self,
        change_seq: int,
        *,
        receipt_id: str,
        delivered_at_ms: int,
    ) -> bool:
        """Record an idempotent delivery receipt for one outbox change.

        Repeating the same receipt for the same change is a no-op; a
        different receipt identity for an already-delivered change fails
        closed so delivery ambiguity never goes unnoticed.
        """

        if (
            isinstance(change_seq, bool)
            or not isinstance(change_seq, int)
            or change_seq < 1
            or not receipt_id
            or len(receipt_id) > 160
            or isinstance(delivered_at_ms, bool)
            or not isinstance(delivered_at_ms, int)
            or delivered_at_ms < 0
        ):
            raise ValueError("memory outbox receipt is invalid")
        receipt_sha256 = canonical_sha256(
            {
                "domain": "tiangong.life.memory-outbox-receipt.v1",
                "change_seq": change_seq,
                "receipt_id": receipt_id,
            }
        )
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM memory_outbox WHERE change_seq = ?",
                (change_seq,),
            ).fetchone()
            if row is None:
                raise LifeShadowStoreError("memory outbox change does not exist")
            if row["receipt_id"] is not None:
                if (
                    str(row["receipt_id"]) != receipt_id
                    or str(row["receipt_sha256"]) != receipt_sha256
                ):
                    raise LifeShadowStoreError("memory outbox receipt conflicts")
                connection.execute("COMMIT")
                return False
            if delivered_at_ms < int(row["created_at_ms"]):
                raise LifeShadowStoreError("memory outbox receipt predates the change")
            connection.execute(
                """
                UPDATE memory_outbox
                SET receipt_id = ?, receipt_sha256 = ?, delivered_at_ms = ?
                WHERE change_seq = ?
                """,
                (receipt_id, receipt_sha256, delivered_at_ms, change_seq),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def get_latest_memory_assertion(
        self, memory_id: str
    ) -> MemoryAssertionV3 | None:
        row = self._connection.execute(
            """
            SELECT a.memory_id, a.revision, c.payload
            FROM memory_assertions AS a
            JOIN memory_assertion_contracts AS c
              ON c.memory_id = a.memory_id AND c.revision = a.revision
            WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
            """,
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return _parse_stored_contract(
            bytes(row["payload"]),
            MemoryAssertionV3,
            "memory assertion",
        )

    def _memory_assertion_payload(self, memory_id: str, revision: int) -> bytes:
        row = self._connection.execute(
            """
            SELECT payload FROM memory_assertion_contracts
            WHERE memory_id = ? AND revision = ?
            """,
            (memory_id, revision),
        ).fetchone()
        if row is None:
            raise LifeShadowStoreError("memory assertion payload is missing")
        return bytes(row["payload"])

    def list_latest_memory_assertions(
        self,
        life_id: str,
        *,
        recallable_only: bool = True,
    ) -> tuple[MemoryAssertionV3, ...]:
        rows = self._connection.execute(
            """
            SELECT a.memory_id, a.revision, c.payload
            FROM memory_assertions AS a
            JOIN (
                SELECT memory_id, max(revision) AS revision
                FROM memory_assertions GROUP BY memory_id
            ) AS latest
              ON latest.memory_id = a.memory_id AND latest.revision = a.revision
            JOIN memory_assertion_contracts AS c
              ON c.memory_id = a.memory_id AND c.revision = a.revision
            WHERE a.life_id = ?
            ORDER BY a.memory_id
            """,
            (life_id,),
        ).fetchall()
        values = tuple(
            _parse_stored_contract(
                bytes(row["payload"]), MemoryAssertionV3, "memory assertion"
            )
            for row in rows
        )
        if not recallable_only:
            return values
        return tuple(
            item
            for item in values
            if item.lifecycle_status == "active"
            and item.protected_payload_id is not None
            and (
                (record := self.get_protected_payload(item.protected_payload_id))
                is not None
                and record.key_available
                and record.key_destroyed_at_ms is None
            )
        )

    def search_memory_assertions(
        self,
        life_id: str,
        terms: tuple[str, ...],
        *,
        limit: int = 128,
    ) -> tuple[MemoryAssertionV3, ...]:
        if not 1 <= limit <= 4096:
            raise ValueError("memory search limit is invalid")
        key_row = self._connection.execute(
            "SELECT key_material FROM life_index_keys WHERE life_id = ?", (life_id,)
        ).fetchone()
        if key_row is None or not terms:
            return ()
        digests = self._term_digests(bytes(key_row["key_material"]), terms)
        placeholders = ",".join("?" for _ in digests)
        rows = self._connection.execute(
            f"""
            SELECT memory_id
            FROM memory_search_terms
            WHERE term_hmac_sha256 IN ({placeholders})
            GROUP BY memory_id
            HAVING count(DISTINCT term_hmac_sha256) = ?
            ORDER BY memory_id
            LIMIT ?
            """,
            (*digests, len(digests), limit),
        ).fetchall()
        values = tuple(
            item
            for row in rows
            if (item := self.get_latest_memory_assertion(str(row["memory_id"])))
            is not None
            and item.life_id == life_id
            and item.lifecycle_status == "active"
            and item.protected_payload_id is not None
            and (
                (record := self.get_protected_payload(item.protected_payload_id))
                is not None
                and record.key_available
                and record.key_destroyed_at_ms is None
            )
        )
        return tuple(
            sorted(values, key=lambda item: (-retention_priority(item), item.memory_id))
        )

    def put_memory_relation(self, relation: MemoryRelationV3) -> bool:
        relation, payload = _revalidate_contract(
            relation, MemoryRelationV3, "memory relation"
        )
        if not relation.has_valid_relation_sha256():
            raise LifeShadowStoreError("memory relation digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM memory_relations WHERE relation_id = ?",
                (relation.relation_id,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("memory relation identity was rebound")
                connection.execute("COMMIT")
                return False
            source = connection.execute(
                """
                SELECT life_id, status FROM memory_assertions
                WHERE memory_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (relation.source_memory_id,),
            ).fetchone()
            if (
                source is None
                or str(source["life_id"]) != relation.life_id
                or str(source["status"]) == "deleted"
            ):
                raise LifeShadowStoreError("memory relation source does not exist")
            connection.execute(
                """
                INSERT INTO memory_relations(
                    relation_id, life_id, source_memory_id, relation_kind,
                    target_ref, payload, payload_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    relation.life_id,
                    relation.source_memory_id,
                    relation.relation_kind,
                    relation.target_ref,
                    payload,
                    relation.relation_sha256,
                    relation.created_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def list_memory_relations(
        self, life_id: str, *, recallable_only: bool = True
    ) -> tuple[MemoryRelationV3, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM memory_relations WHERE life_id = ? ORDER BY relation_id",
            (life_id,),
        ).fetchall()
        relations = tuple(
            _parse_stored_contract(
                bytes(row["payload"]), MemoryRelationV3, "memory relation"
            )
            for row in rows
        )
        if not recallable_only:
            return relations
        return tuple(
            relation
            for relation in relations
            if (
                (source := self.get_latest_memory_assertion(
                    relation.source_memory_id
                ))
                is not None
                and source.lifecycle_status == "active"
                and source.protected_payload_id is not None
                and (
                    (record := self.get_protected_payload(
                        source.protected_payload_id
                    ))
                    is not None
                    and record.key_available
                    and record.key_destroyed_at_ms is None
                )
            )
        )

    def put_causal_node(
        self,
        node: CausalNodeV3,
        *,
        search_terms: tuple[str, ...] = (),
    ) -> bool:
        node, payload = _revalidate_contract(node, CausalNodeV3, "causal node")
        if not node.has_valid_node_sha256():
            raise LifeShadowStoreError("causal node digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM causal_nodes WHERE node_id = ?", (node.node_id,)
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("causal node identity was rebound")
                connection.execute("COMMIT")
                return False
            self._assert_protected_payload_binding_locked(
                payload_id=node.protected_payload_id,
                payload_sha256=node.protected_payload_sha256,
                life_id=node.life_id,
                privacy_scope=node.privacy_scope,
            )
            if node.node_kind in {"memory_assertion", "goal", "constraint"}:
                memory = self.get_latest_memory_assertion(node.source_ref)
                if (
                    memory is None
                    or memory.life_id != node.life_id
                    or memory.lifecycle_status != node.recall_status
                    or memory.lifecycle_status == "deleted"
                ):
                    raise LifeShadowStoreError(
                        "causal node memory source is not recallable"
                    )
            connection.execute(
                """
                INSERT INTO causal_nodes(
                    node_id, life_id, node_kind, source_event_id, payload,
                    payload_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.life_id,
                    node.node_kind,
                    None if not node.source_event_ids else node.source_event_ids[0],
                    payload,
                    node.node_sha256,
                    node.created_at_ms,
                ),
            )
            if search_terms and node.recall_status == "active":
                key = self._get_or_create_index_key_locked(
                    node.life_id, created_at_ms=node.created_at_ms
                )
                for digest in self._term_digests(key, search_terms):
                    connection.execute(
                        """
                        INSERT INTO causal_node_terms(
                            node_id, term_hmac_sha256, privacy_scope, created_at_ms
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            node.node_id,
                            digest,
                            node.privacy_scope,
                            node.created_at_ms,
                        ),
                    )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def list_causal_nodes(
        self, life_id: str, *, recallable_only: bool = True
    ) -> tuple[CausalNodeV3, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM causal_nodes WHERE life_id = ? ORDER BY node_id",
            (life_id,),
        ).fetchall()
        nodes = tuple(
            _parse_stored_contract(
                bytes(row["payload"]), CausalNodeV3, "causal node"
            )
            for row in rows
        )
        if not recallable_only:
            return nodes
        return tuple(
            node
            for node in nodes
            if node.recall_status == "active"
            and (
                (record := self.get_protected_payload(node.protected_payload_id))
                is not None
                and record.key_available
                and record.key_destroyed_at_ms is None
            )
        )

    def list_latest_causal_hypotheses(
        self, life_id: str
    ) -> tuple[CausalHypothesis, ...]:
        rows = self._connection.execute(
            """
            SELECT edge.payload
            FROM causal_edge_versions AS edge
            JOIN (
                SELECT hypothesis_id, max(revision) AS revision
                FROM causal_edge_versions GROUP BY hypothesis_id
            ) AS latest
              ON latest.hypothesis_id = edge.hypothesis_id
             AND latest.revision = edge.revision
            WHERE edge.life_id = ?
            ORDER BY edge.hypothesis_id
            """,
            (life_id,),
        ).fetchall()
        return tuple(
            _parse_stored_contract(
                bytes(row["payload"]), CausalHypothesis, "causal hypothesis"
            )
            for row in rows
        )

    def build_revision_vector(
        self,
        life_id: str,
        *,
        writer_epoch: int,
        identity_revision: int,
        soul_revision: int,
    ) -> LifeRevisionVector:
        """Read every context-affecting revision from one SQLite snapshot."""

        if (
            not life_id
            or writer_epoch < 1
            or identity_revision < 1
            or soul_revision < 1
        ):
            raise ValueError("life revision vector authority is invalid")
        row = self._connection.execute(
            """
            SELECT
              coalesce((SELECT max(sequence) FROM life_events WHERE life_id = :life_id), 0),
              (SELECT count(*) FROM memory_assertions WHERE life_id = :life_id),
              coalesce((SELECT max(revision) FROM affect_snapshots WHERE life_id = :life_id), 0),
              (SELECT count(*) FROM causal_edge_versions WHERE life_id = :life_id)
                + (SELECT count(*) FROM causal_episodes WHERE life_id = :life_id),
              coalesce((SELECT max(revision) FROM viability_snapshots WHERE life_id = :life_id), 0),
              coalesce((SELECT max(revision) FROM autonomy_policies WHERE life_id = :life_id), 0),
              (SELECT count(*) FROM reflection_cards WHERE life_id = :life_id),
              coalesce((SELECT max(profile_revision) FROM capability_profiles WHERE life_id = :life_id), 0)
            """,
            {"life_id": life_id},
        ).fetchone()
        assert row is not None

        vector = LifeRevisionVector(
            life_id=life_id,
            writer_epoch=writer_epoch,
            source_sequence=int(row[0]),
            identity_revision=identity_revision,
            soul_revision=soul_revision,
            memory_revision=int(row[1]),
            affect_revision=int(row[2]),
            causal_revision=int(row[3]),
            viability_revision=int(row[4]),
            policy_revision=int(row[5]),
            reflection_revision=int(row[6]),
            capability_revision=int(row[7]),
            vector_sha256="0" * 64,
        ).with_computed_vector_sha256()
        return vector

    def put_causal_context_pack(
        self,
        pack: CausalContextPack,
        *,
        privacy_scope: str,
        authorization: LifeContextAuthorization | None = None,
        expected_revisions: LifeRevisionVector | None = None,
    ) -> ContextPackPersistRecord:
        pack, payload = _revalidate_contract(
            pack, CausalContextPack, "causal context pack"
        )
        if not pack.has_valid_pack_sha256() or not privacy_scope:
            raise LifeShadowStoreError("causal context pack digest or scope is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            if expected_revisions is not None:
                observed_revisions = self.build_revision_vector(
                    pack.life_id,
                    writer_epoch=expected_revisions.writer_epoch,
                    identity_revision=expected_revisions.identity_revision,
                    soul_revision=expected_revisions.soul_revision,
                )
                if observed_revisions != expected_revisions:
                    raise LifeShadowStoreError(
                        "life revisions changed before context authorization commit"
                    )
            existing = connection.execute(
                "SELECT * FROM causal_context_packs WHERE pack_id = ?",
                (pack.pack_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["pack_sha256"]) != pack.pack_sha256:
                    raise LifeShadowStoreError("causal context pack identity was rebound")
                stored_payload = self.read_protected_payload(
                    str(existing["protected_payload_id"])
                )
                if stored_payload != payload:
                    raise LifeShadowStoreError("causal context pack payload diverged")
                protected = self.get_protected_payload(
                    str(existing["protected_payload_id"])
                )
                assert protected is not None
                if authorization is not None:
                    self._put_context_authorization_locked(authorization, pack)
                connection.execute("COMMIT")
                return ContextPackPersistRecord(pack, protected, False)
            capsule_row = connection.execute(
                "SELECT payload FROM context_capsules WHERE capsule_id = ?",
                (pack.continuity.capsule_id,),
            ).fetchone()
            if capsule_row is None:
                raise LifeShadowStoreError(
                    "causal context pack source capsule is not persisted"
                )
            persisted_capsule = _parse_stored_contract(
                bytes(capsule_row["payload"]),
                TaskContinuityCapsule,
                "context capsule",
            )
            if persisted_capsule != pack.continuity:
                raise LifeShadowStoreError(
                    "causal context pack source capsule binding is invalid"
                )
            protected = self._put_protected_payload_locked(
                payload,
                life_id=pack.life_id,
                privacy_scope=privacy_scope,
                created_at_ms=pack.created_at_ms,
            )
            connection.execute(
                """
                INSERT INTO causal_context_packs(
                    pack_id, life_id, request_id, run_id, generation,
                    source_capsule_id, protected_payload_id, pack_sha256,
                    integrity_status, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?)
                """,
                (
                    pack.pack_id,
                    pack.life_id,
                    pack.continuity.request_id,
                    pack.continuity.run_id,
                    pack.continuity.generation,
                    pack.continuity.capsule_id,
                    protected.payload_id,
                    pack.pack_sha256,
                    pack.created_at_ms,
                ),
            )
            for item in pack.items:
                connection.execute(
                    "INSERT INTO causal_context_pack_members(pack_id, item_ref) VALUES (?, ?)",
                    (pack.pack_id, item.item_ref),
                )
            if authorization is not None:
                self._put_context_authorization_locked(authorization, pack)
            connection.execute("COMMIT")
            return ContextPackPersistRecord(pack, protected, True)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _put_context_authorization_locked(
        self,
        authorization: LifeContextAuthorization,
        pack: CausalContextPack,
    ) -> bool:
        authorization, payload = _revalidate_contract(
            authorization,
            LifeContextAuthorization,
            "life context authorization",
        )
        if (
            not authorization.has_valid_authorization_sha256()
            or not authorization.revisions.has_valid_vector_sha256()
            or authorization.life_id != pack.life_id
            or authorization.request_id != pack.continuity.request_id
            or authorization.run_id != pack.continuity.run_id
            or authorization.generation != pack.continuity.generation
            or authorization.continuity_capsule_sha256
            != pack.continuity.capsule_sha256
            or authorization.context_pack_id != pack.pack_id
            or authorization.context_pack_sha256 != pack.pack_sha256
        ):
            raise LifeShadowStoreError(
                "life context authorization binding is invalid"
            )
        existing = self._connection.execute(
            "SELECT payload FROM context_authorizations WHERE authorization_id = ?",
            (authorization.authorization_id,),
        ).fetchone()
        if existing is not None:
            if bytes(existing["payload"]) != payload:
                raise LifeShadowStoreError(
                    "life context authorization identity was rebound"
                )
            return False
        request_existing = self._connection.execute(
            """
            SELECT payload FROM context_authorizations
            WHERE request_id = ? AND run_id = ? AND generation = ?
            """,
            (
                authorization.request_id,
                authorization.run_id,
                authorization.generation,
            ),
        ).fetchone()
        if request_existing is not None:
            raise LifeShadowStoreError(
                "life context generation already has another authorization"
            )
        self._connection.execute(
            """
            INSERT INTO context_authorizations(
                authorization_id, life_id, request_id, run_id, generation,
                principal_scope_hash, context_pack_id, revisions_sha256,
                payload, authorization_sha256, issued_at_ms, expires_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                authorization.authorization_id,
                authorization.life_id,
                authorization.request_id,
                authorization.run_id,
                authorization.generation,
                authorization.principal_scope_hash,
                authorization.context_pack_id,
                authorization.revisions.vector_sha256,
                payload,
                authorization.authorization_sha256,
                authorization.issued_at_ms,
                authorization.expires_at_ms,
            ),
        )
        return True

    def get_context_authorization(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> LifeContextAuthorization | None:
        row = self._connection.execute(
            """
            SELECT payload FROM context_authorizations
            WHERE request_id = ? AND run_id = ? AND generation = ?
            """,
            (request_id, run_id, generation),
        ).fetchone()
        if row is None:
            return None
        authorization = _parse_stored_contract(
            bytes(row["payload"]),
            LifeContextAuthorization,
            "life context authorization",
        )
        if (
            not authorization.has_valid_authorization_sha256()
            or not authorization.revisions.has_valid_vector_sha256()
        ):
            raise LifeShadowStoreError(
                "stored life context authorization digest is invalid"
            )
        return authorization

    def read_causal_context_pack(self, pack_id: str) -> CausalContextPack:
        if self._connection.execute(
            """
            SELECT 1 FROM capability_invalidations
            WHERE target_kind = 'context_pack' AND target_ref = ?
            """,
            (pack_id,),
        ).fetchone() is not None:
            raise LifeShadowStoreError("causal context pack was invalidated by capability rollback")
        row = self._connection.execute(
            "SELECT * FROM causal_context_packs WHERE pack_id = ?", (pack_id,)
        ).fetchone()
        if row is None:
            raise LifeShadowStoreError("causal context pack does not exist")
        pack = _parse_stored_contract(
            self.read_protected_payload(str(row["protected_payload_id"])),
            CausalContextPack,
            "causal context pack",
        )
        if (
            pack.pack_id != str(row["pack_id"])
            or pack.pack_sha256 != str(row["pack_sha256"])
            or pack.integrity_status != str(row["integrity_status"])
        ):
            raise LifeShadowStoreError("causal context pack columns diverged")
        members = tuple(
            str(item["item_ref"])
            for item in self._connection.execute(
                """
                SELECT item_ref FROM causal_context_pack_members
                WHERE pack_id = ? ORDER BY item_ref
                """,
                (pack_id,),
            ).fetchall()
        )
        if members != tuple(item.item_ref for item in pack.items):
            raise LifeShadowStoreError("causal context pack member index diverged")
        return pack

    def get_latest_causal_context_pack(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> CausalContextPack | None:
        row = self._connection.execute(
            """
            SELECT pack_id FROM causal_context_packs
            WHERE request_id = ? AND run_id = ? AND generation = ?
            ORDER BY created_at_ms DESC, pack_id DESC LIMIT 1
            """,
            (request_id, run_id, generation),
        ).fetchone()
        if row is None:
            return None
        try:
            return self.read_causal_context_pack(str(row["pack_id"]))
        except LifeShadowStoreError as exc:
            if "key is unavailable" in str(exc):
                return None
            raise

    def get_latest_causal_context_pack_for_life(
        self,
        life_id: str,
    ) -> CausalContextPack | None:
        """Return the newest verified context projection for one life.

        The panel has no request/run tuple, so looking up a pack through the
        execution-specific API forced it to fall back to the detached legacy
        context store.  This life-scoped reader keeps the authoritative store
        read-only and preserves the existing pack verification path.
        """

        row = self._connection.execute(
            """
            SELECT pack_id FROM causal_context_packs
            WHERE life_id = ?
            ORDER BY created_at_ms DESC, pack_id DESC LIMIT 1
            """,
            (life_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return self.read_causal_context_pack(str(row["pack_id"]))
        except LifeShadowStoreError as exc:
            if "key is unavailable" in str(exc):
                return None
            raise

    def delete_memory(
        self,
        memory_id: str,
        *,
        expected_revision: int,
        deleted_at_ms: int,
    ) -> MemoryDeletionResult:
        if not memory_id or expected_revision < 1 or deleted_at_ms < 0:
            raise ValueError("memory deletion request is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest_row = connection.execute(
                """
                SELECT a.*, c.payload
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
                """,
                (memory_id,),
            ).fetchone()
            if latest_row is None:
                raise LifeShadowStoreError("memory deletion target does not exist")
            latest = _parse_stored_contract(
                bytes(latest_row["payload"]), MemoryAssertionV3, "memory assertion"
            )
            if latest.lifecycle_status == "deleted":
                if expected_revision not in {latest.revision, latest.revision - 1}:
                    raise LifeShadowStoreError("memory deletion revision is stale")
                assert latest.deletion_tombstone_id is not None
                tombstone_row = connection.execute(
                    "SELECT payload FROM privacy_deletion_tombstones WHERE tombstone_id = ?",
                    (latest.deletion_tombstone_id,),
                ).fetchone()
                if tombstone_row is None:
                    raise LifeShadowStoreError("memory deletion tombstone is missing")
                tombstone = _parse_stored_contract(
                    bytes(tombstone_row["payload"]),
                    PrivacyDeletionTombstone,
                    "privacy deletion tombstone",
                )
                connection.execute("COMMIT")
                return MemoryDeletionResult(
                    tombstone, latest, tombstone.destroyed_payload_ids
                )
            if latest.revision != expected_revision:
                raise LifeShadowStoreError("memory deletion revision is stale")
            if latest.retention_class == "LEGAL_HOLD":
                raise LifeShadowStoreError("legal-hold memory cannot be deleted")

            history_rows = connection.execute(
                """
                SELECT payload_object_id FROM memory_assertions
                WHERE memory_id = ? AND payload_object_id IS NOT NULL
                """,
                (memory_id,),
            ).fetchall()
            node_rows = connection.execute(
                "SELECT node_id, payload FROM causal_nodes WHERE life_id = ?",
                (latest.life_id,),
            ).fetchall()
            affected_nodes: list[CausalNodeV3] = []
            for row in node_rows:
                node = _parse_stored_contract(
                    bytes(row["payload"]), CausalNodeV3, "causal node"
                )
                if node.source_ref == memory_id:
                    affected_nodes.append(node)
            pack_rows = connection.execute(
                """
                SELECT p.pack_id, p.source_capsule_id, p.protected_payload_id
                FROM causal_context_packs AS p
                JOIN causal_context_pack_members AS m ON m.pack_id = p.pack_id
                WHERE m.item_ref = ? ORDER BY p.pack_id
                """,
                (memory_id,),
            ).fetchall()
            payload_ids = tuple(
                sorted(
                    {
                        *(str(row["payload_object_id"]) for row in history_rows),
                        *(node.protected_payload_id for node in affected_nodes),
                        *(str(row["protected_payload_id"]) for row in pack_rows),
                    }
                )
            )
            if len(payload_ids) > 4096:
                raise LifeShadowStoreError("memory deletion fanout exceeds safe bound")
            removed_index_count = int(
                connection.execute(
                    "SELECT count(*) FROM memory_search_terms WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()[0]
            )
            if affected_nodes:
                placeholders = ",".join("?" for _ in affected_nodes)
                node_ids = tuple(node.node_id for node in affected_nodes)
                removed_index_count += int(
                    connection.execute(
                        f"SELECT count(*) FROM causal_node_terms WHERE node_id IN ({placeholders})",
                        node_ids,
                    ).fetchone()[0]
                )
                connection.execute(
                    f"DELETE FROM causal_node_terms WHERE node_id IN ({placeholders})",
                    node_ids,
                )
            connection.execute(
                "DELETE FROM memory_search_terms WHERE memory_id = ?", (memory_id,)
            )
            for payload_id in payload_ids:
                payload_row = connection.execute(
                    "SELECT created_at_ms FROM protected_payloads WHERE payload_id = ?",
                    (payload_id,),
                ).fetchone()
                if payload_row is None:
                    raise LifeShadowStoreError("memory deletion payload is missing")
                if deleted_at_ms < int(payload_row["created_at_ms"]):
                    raise LifeShadowStoreError("memory deletion predates protected payload")
                connection.execute(
                    "DELETE FROM protected_payload_keys WHERE payload_id = ?",
                    (payload_id,),
                )
                connection.execute(
                    "UPDATE protected_payloads SET key_destroyed_at_ms = ? WHERE payload_id = ?",
                    (deleted_at_ms, payload_id),
                )

            target_ref_hash = hashlib.sha256(memory_id.encode("utf-8")).hexdigest()
            tombstone_id = "ptm_" + canonical_sha256(
                {
                    "domain": "tiangong.life.memory-deletion.v1",
                    "life_id": latest.life_id,
                    "memory_id_hash": target_ref_hash,
                    "superseded_assertion_sha256": latest.assertion_sha256,
                }
            )
            affected_capsules = tuple(
                sorted({str(row["source_capsule_id"]) for row in pack_rows})
            )
            tombstone = PrivacyDeletionTombstone(
                tombstone_id=tombstone_id,
                life_id=latest.life_id,
                target_kind="memory",
                target_ref_hash=target_ref_hash,
                privacy_scope=latest.privacy_scope,
                destroyed_payload_ids=payload_ids,
                removed_index_entry_count=removed_index_count,
                affected_capsule_ids=affected_capsules,
                created_at_ms=deleted_at_ms,
                deletion_proof_sha256="0" * 64,
            ).with_computed_deletion_proof_sha256()
            tombstone_payload = canonical_json_bytes(tombstone)
            connection.execute(
                """
                INSERT INTO privacy_deletion_tombstones(
                    tombstone_id, life_id, target_kind, target_ref_hash,
                    privacy_scope, payload, deletion_proof_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone.tombstone_id,
                    tombstone.life_id,
                    tombstone.target_kind,
                    tombstone.target_ref_hash,
                    tombstone.privacy_scope,
                    tombstone_payload,
                    tombstone.deletion_proof_sha256,
                    tombstone.created_at_ms,
                ),
            )
            connection.execute(
                "INSERT INTO tombstones VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tombstone.tombstone_id,
                    tombstone.life_id,
                    tombstone.target_kind,
                    tombstone.target_ref_hash,
                    tombstone.deletion_proof_sha256,
                    tombstone.created_at_ms,
                ),
            )
            suppression_targets = [("memory", target_ref_hash)]
            suppression_targets.extend(
                ("causal_node", hashlib.sha256(node.node_id.encode("utf-8")).hexdigest())
                for node in affected_nodes
            )
            suppression_targets.extend(
                (
                    "context_pack",
                    hashlib.sha256(str(row["pack_id"]).encode("utf-8")).hexdigest(),
                )
                for row in pack_rows
            )
            for target_kind, target_hash in suppression_targets:
                connection.execute(
                    """
                    INSERT INTO privacy_suppressions(
                        target_kind, target_ref_hash, privacy_scope,
                        tombstone_id, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        target_kind,
                        target_hash,
                        latest.privacy_scope,
                        tombstone.tombstone_id,
                        deleted_at_ms,
                    ),
                )
            deleted = latest.model_copy(
                update={
                    "revision": latest.revision + 1,
                    "supersedes_assertion_sha256": latest.assertion_sha256,
                    "lifecycle_status": "deleted",
                    "protected_payload_id": None,
                    "protected_payload_sha256": None,
                    "deletion_tombstone_id": tombstone.tombstone_id,
                    "created_at_ms": deleted_at_ms,
                    "assertion_sha256": "0" * 64,
                }
            ).with_computed_assertion_sha256()
            deleted_payload = canonical_json_bytes(deleted)
            connection.execute(
                """
                INSERT INTO memory_assertions(
                    memory_id, revision, life_id, status, privacy_scope,
                    payload_object_id, payload_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    deleted.memory_id,
                    deleted.revision,
                    deleted.life_id,
                    deleted.lifecycle_status,
                    deleted.privacy_scope,
                    deleted.created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_assertion_contracts(
                    memory_id, revision, payload, assertion_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    deleted.memory_id,
                    deleted.revision,
                    deleted_payload,
                    deleted.assertion_sha256,
                ),
            )
            self._record_memory_change_locked(
                life_id=deleted.life_id,
                memory_id=deleted.memory_id,
                revision=deleted.revision,
                change_kind="tombstone",
                assertion_sha256=deleted.assertion_sha256,
                created_at_ms=deleted.created_at_ms,
            )
            connection.execute("COMMIT")
            return MemoryDeletionResult(tombstone, deleted, payload_ids)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_viability_state(self, state: ViabilityState) -> bool:
        state, payload = _revalidate_contract(state, ViabilityState, "viability state")
        if not state.has_valid_state_sha256():
            raise LifeShadowStoreError("viability state digest is invalid")
        return self._insert_immutable(
            select_sql=(
                "SELECT payload FROM viability_snapshots "
                "WHERE life_id = ? AND revision = ?"
            ),
            select_values=(state.life_id, state.revision),
            insert_sql="""
                INSERT INTO viability_snapshots(
                    life_id, revision, payload, payload_sha256, created_at_ms
                )
                VALUES (?, ?, ?, ?, ?)
            """,
            insert_values=(
                state.life_id,
                state.revision,
                payload,
                state.state_sha256,
                state.created_at_ms,
            ),
            payload=payload,
            identity="viability state",
        )

    def put_viability_observation(self, observation: ViabilityObservation) -> bool:
        observation, payload = _revalidate_contract(
            observation, ViabilityObservation, "viability observation"
        )
        if not observation.has_valid_observation_sha256():
            raise LifeShadowStoreError("viability observation digest is invalid")
        return self._insert_immutable(
            select_sql="SELECT payload FROM viability_observations WHERE observation_id = ?",
            select_values=(observation.observation_id,),
            insert_sql="""
                INSERT INTO viability_observations(
                    observation_id, life_id, dimension, source_event_id, payload,
                    observation_sha256, measured_at_ms, stale_after_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                observation.observation_id,
                observation.life_id,
                observation.dimension,
                observation.source_event_id,
                payload,
                observation.observation_sha256,
                observation.measured_at_ms,
                observation.stale_after_ms,
            ),
            payload=payload,
            identity="viability observation",
        )

    def get_latest_viability_state(self, life_id: str) -> ViabilityState | None:
        row = self._connection.execute(
            """
            SELECT payload FROM viability_snapshots
            WHERE life_id = ? ORDER BY revision DESC LIMIT 1
            """,
            (life_id,),
        ).fetchone()
        if row is None:
            return None
        return _parse_stored_contract(
            bytes(row["payload"]), ViabilityState, "viability state"
        )

    def put_affect_source_policy(
        self, policy: AffectSourcePolicySnapshot
    ) -> bool:
        policy, payload = _revalidate_contract(
            policy, AffectSourcePolicySnapshot, "affect source policy"
        )
        if not policy.has_valid_policy_sha256():
            raise LifeShadowStoreError("affect source policy digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT payload FROM affect_source_policies
                WHERE life_id = ? AND revision = ?
                """,
                (policy.life_id, policy.revision),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError(
                        "affect source policy identity was rebound"
                    )
                connection.execute("COMMIT")
                return False
            previous = connection.execute(
                """
                SELECT revision, policy_sha256 FROM affect_source_policies
                WHERE life_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (policy.life_id,),
            ).fetchone()
            if policy.revision == 1:
                if previous is not None:
                    raise LifeShadowStoreError("affect source policy genesis exists")
            elif (
                previous is None
                or int(previous["revision"]) + 1 != policy.revision
                or str(previous["policy_sha256"])
                != policy.supersedes_policy_sha256
            ):
                raise LifeShadowStoreError(
                    "affect source policy revision is discontinuous"
                )
            connection.execute(
                """
                INSERT INTO affect_source_policies(
                    life_id, revision, payload, policy_sha256, effective_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    policy.life_id,
                    policy.revision,
                    payload,
                    policy.policy_sha256,
                    policy.effective_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def get_latest_affect_source_policy(
        self, life_id: str
    ) -> AffectSourcePolicySnapshot | None:
        row = self._connection.execute(
            """
            SELECT payload FROM affect_source_policies
            WHERE life_id = ? ORDER BY revision DESC LIMIT 1
            """,
            (life_id,),
        ).fetchone()
        if row is None:
            return None
        return _parse_stored_contract(
            bytes(row["payload"]),
            AffectSourcePolicySnapshot,
            "affect source policy",
        )

    def get_latest_affective_state(self, life_id: str) -> AffectiveStateV3 | None:
        row = self._connection.execute(
            """
            SELECT payload FROM affect_snapshots
            WHERE life_id = ? ORDER BY revision DESC LIMIT 1
            """,
            (life_id,),
        ).fetchone()
        if row is None:
            return None
        return _parse_stored_contract(
            bytes(row["payload"]), AffectiveStateV3, "affective state"
        )

    def ingest_affect_signal(
        self,
        signal: AffectSignal,
        *,
        received_at_ms: int,
    ) -> AffectIntakeCommit:
        from .affect import build_appraisal, evaluate_affect_gate, update_affective_state

        signal, signal_payload = _revalidate_contract(
            signal, AffectSignal, "affect signal"
        )
        if (
            not signal.has_valid_signal_sha256()
            or received_at_ms < signal.observed_at_ms
        ):
            raise LifeShadowStoreError("affect signal digest or receive time is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM affect_signal_receipts
                WHERE signal_id = ? OR source_event_id = ?
                """,
                (signal.signal_id, signal.source_event_id),
            ).fetchall()
            if existing:
                if len(existing) != 1 or bytes(existing[0]["signal"]) != signal_payload:
                    raise LifeShadowStoreError("affect signal identity was rebound")
                stored_receipt = _parse_stored_contract(
                    bytes(existing[0]["receipt"]),
                    AffectIntakeReceipt,
                    "affect intake receipt",
                )
                duplicate = stored_receipt.model_copy(
                    update={"duplicate": True, "receipt_sha256": "0" * 64}
                ).with_computed_receipt_identity()
                appraisal = None
                state = None
                if stored_receipt.accepted:
                    appraisal_row = connection.execute(
                        "SELECT payload FROM appraisal_events WHERE appraisal_id = ?",
                        (stored_receipt.appraisal_id,),
                    ).fetchone()
                    state_row = connection.execute(
                        """
                        SELECT payload FROM affect_snapshots
                        WHERE life_id = ? AND revision = ?
                        """,
                        (stored_receipt.life_id, stored_receipt.affect_revision),
                    ).fetchone()
                    if appraisal_row is None or state_row is None:
                        raise LifeShadowStoreError(
                            "affect duplicate lacks its committed state"
                        )
                    appraisal = _parse_stored_contract(
                        bytes(appraisal_row["payload"]),
                        AppraisalVectorV3,
                        "appraisal",
                    )
                    state = _parse_stored_contract(
                        bytes(state_row["payload"]),
                        AffectiveStateV3,
                        "affective state",
                    )
                connection.execute("COMMIT")
                return AffectIntakeCommit(signal, duplicate, appraisal, state, False)

            event_row = connection.execute(
                "SELECT envelope FROM life_events WHERE event_id = ?",
                (signal.source_event_id,),
            ).fetchone()
            if event_row is None:
                raise LifeShadowStoreError(
                    "affect signal source event is not durably verified"
                )
            event = _parse_stored_contract(
                bytes(event_row["envelope"]), LifeEventEnvelope, "life event"
            )
            if (
                event.life_id != signal.life_id
                or event.event_hash != signal.source_event_hash
                or event.content_sha256 != signal.content_sha256
                or event.occurred_at_ms != signal.occurred_at_ms
                or event.observed_at_ms != signal.observed_at_ms
            ):
                raise LifeShadowStoreError("affect signal source event binding is invalid")

            offset = connection.execute(
                """
                SELECT * FROM affect_source_offsets
                WHERE life_id = ? AND source_stream_id = ?
                """,
                (signal.life_id, signal.source_stream_id),
            ).fetchone()
            valid_next = (
                offset is None
                and signal.source_sequence == 1
                or offset is not None
                and (
                    signal.source_epoch == int(offset["source_epoch"])
                    and signal.source_sequence == int(offset["source_sequence"]) + 1
                    or signal.source_epoch == int(offset["source_epoch"]) + 1
                    and signal.source_sequence == 1
                )
            )
            if not valid_next:
                raise LifeShadowStoreError("affect source sequence is discontinuous")

            policy_row = connection.execute(
                """
                SELECT payload FROM affect_source_policies
                WHERE life_id = ? AND effective_at_ms <= ?
                ORDER BY revision DESC LIMIT 1
                """,
                (signal.life_id, signal.observed_at_ms),
            ).fetchone()
            policy = (
                None
                if policy_row is None
                else _parse_stored_contract(
                    bytes(policy_row["payload"]),
                    AffectSourcePolicySnapshot,
                    "affect source policy",
                )
            )
            dedupe = connection.execute(
                """
                SELECT * FROM affect_dedupe
                WHERE life_id = ? AND dedupe_key = ?
                """,
                (signal.life_id, signal.dedupe_key),
            ).fetchone()
            if dedupe is not None and received_at_ms < int(dedupe["last_seen_at_ms"]):
                raise LifeShadowStoreError("affect repetition time moved backward")
            repetition_count = 1 if dedupe is None else int(dedupe["occurrence_count"]) + 1
            decision = evaluate_affect_gate(
                signal, policy, repetition_count=repetition_count
            )
            appraisal = None
            state = None
            if decision.accepted:
                viability_row = connection.execute(
                    """
                    SELECT payload FROM viability_snapshots
                    WHERE life_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (signal.life_id,),
                ).fetchone()
                if viability_row is None:
                    raise LifeShadowStoreError(
                        "affect appraisal lacks a viability snapshot"
                    )
                viability = _parse_stored_contract(
                    bytes(viability_row["payload"]),
                    ViabilityState,
                    "viability state",
                )
                previous_row = connection.execute(
                    """
                    SELECT payload FROM affect_snapshots
                    WHERE life_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (signal.life_id,),
                ).fetchone()
                previous = (
                    None
                    if previous_row is None
                    else _parse_stored_contract(
                        bytes(previous_row["payload"]),
                        AffectiveStateV3,
                        "affective state",
                    )
                )
                if previous is not None and received_at_ms < previous.updated_at_ms:
                    raise LifeShadowStoreError("affect state receive time moved backward")
                appraisal = build_appraisal(
                    signal,
                    decision,
                    viability_revision=viability.revision,
                    appraised_at_ms=received_at_ms,
                )
                state = update_affective_state(
                    signal,
                    appraisal,
                    decision,
                    previous,
                    updated_at_ms=received_at_ms,
                )
                appraisal_payload = canonical_json_bytes(appraisal)
                state_payload = canonical_json_bytes(state)
                connection.execute(
                    """
                    INSERT INTO appraisal_events(
                        appraisal_id, life_id, payload, payload_sha256, appraised_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        appraisal.appraisal_id,
                        appraisal.life_id,
                        appraisal_payload,
                        appraisal.appraisal_sha256,
                        appraisal.appraised_at_ms,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO affect_snapshots(
                        life_id, revision, payload, payload_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.life_id,
                        state.revision,
                        state_payload,
                        state.state_sha256,
                        state.updated_at_ms,
                    ),
                )
            receipt_id = "afr_" + canonical_sha256(
                {
                    "domain": "tiangong.life.affect-receipt.v1",
                    "signal_id": signal.signal_id,
                    "source_epoch": signal.source_epoch,
                    "source_sequence": signal.source_sequence,
                }
            )
            receipt = AffectIntakeReceipt(
                receipt_id=receipt_id,
                signal_id=signal.signal_id,
                life_id=signal.life_id,
                source_event_id=signal.source_event_id,
                source_stream_id=signal.source_stream_id,
                source_epoch=signal.source_epoch,
                source_sequence=signal.source_sequence,
                accepted=decision.accepted,
                duplicate=False,
                reason_code=decision.reason_code,
                repetition_count=repetition_count,
                effective_intensity_milli=decision.effective_intensity_milli,
                appraisal_id=None if appraisal is None else appraisal.appraisal_id,
                appraisal_sha256=(
                    None if appraisal is None else appraisal.appraisal_sha256
                ),
                affect_revision=None if state is None else state.revision,
                affect_state_sha256=None if state is None else state.state_sha256,
                received_at_ms=received_at_ms,
                receipt_sha256="0" * 64,
            ).with_computed_receipt_identity()
            receipt_payload = canonical_json_bytes(receipt)
            connection.execute(
                """
                INSERT INTO affect_signal_receipts(
                    signal_id, source_event_id, life_id, source_family,
                    source_stream_id, source_epoch, source_sequence, dedupe_key,
                    accepted, signal, signal_sha256, receipt, receipt_sha256,
                    received_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.source_event_id,
                    signal.life_id,
                    signal.source_family,
                    signal.source_stream_id,
                    signal.source_epoch,
                    signal.source_sequence,
                    signal.dedupe_key,
                    1 if decision.accepted else 0,
                    signal_payload,
                    signal.signal_sha256,
                    receipt_payload,
                    receipt.receipt_sha256,
                    received_at_ms,
                ),
            )
            if dedupe is None:
                connection.execute(
                    """
                    INSERT INTO affect_dedupe(
                        life_id, dedupe_key, first_signal_id, last_signal_id,
                        occurrence_count, first_seen_at_ms, last_seen_at_ms
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        signal.life_id,
                        signal.dedupe_key,
                        signal.signal_id,
                        signal.signal_id,
                        received_at_ms,
                        received_at_ms,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE affect_dedupe
                    SET last_signal_id = ?, occurrence_count = ?, last_seen_at_ms = ?
                    WHERE life_id = ? AND dedupe_key = ?
                    """,
                    (
                        signal.signal_id,
                        repetition_count,
                        received_at_ms,
                        signal.life_id,
                        signal.dedupe_key,
                    ),
                )
            connection.execute(
                """
                INSERT INTO affect_source_offsets(
                    life_id, source_stream_id, source_epoch, source_sequence,
                    last_signal_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(life_id, source_stream_id) DO UPDATE SET
                    source_epoch=excluded.source_epoch,
                    source_sequence=excluded.source_sequence,
                    last_signal_id=excluded.last_signal_id,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    signal.life_id,
                    signal.source_stream_id,
                    signal.source_epoch,
                    signal.source_sequence,
                    signal.signal_id,
                    received_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return AffectIntakeCommit(signal, receipt, appraisal, state, True)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_appraisal(self, appraisal: AppraisalVectorV3) -> bool:
        appraisal, payload = _revalidate_contract(
            appraisal,
            AppraisalVectorV3,
            "appraisal",
        )
        if not appraisal.has_valid_appraisal_sha256():
            raise LifeShadowStoreError("appraisal digest is invalid")
        return self._insert_immutable(
            select_sql="SELECT payload FROM appraisal_events WHERE appraisal_id = ?",
            select_values=(appraisal.appraisal_id,),
            insert_sql="""
                INSERT INTO appraisal_events(
                    appraisal_id, life_id, payload, payload_sha256, appraised_at_ms
                )
                VALUES (?, ?, ?, ?, ?)
            """,
            insert_values=(
                appraisal.appraisal_id,
                appraisal.life_id,
                payload,
                appraisal.appraisal_sha256,
                appraisal.appraised_at_ms,
            ),
            payload=payload,
            identity="appraisal",
        )

    def put_causal_episode(self, episode: CausalEpisode) -> bool:
        episode, payload = _revalidate_contract(
            episode,
            CausalEpisode,
            "causal episode",
        )
        if not episode.has_valid_episode_sha256():
            raise LifeShadowStoreError("causal episode digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT payload FROM causal_episodes
                WHERE episode_id = ? AND revision = ?
                """,
                (episode.episode_id, episode.revision),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("causal episode identity was rebound")
                connection.execute("COMMIT")
                return False
            previous = connection.execute(
                """
                SELECT revision, payload_sha256 FROM causal_episodes
                WHERE episode_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (episode.episode_id,),
            ).fetchone()
            if episode.revision == 1:
                if previous is not None:
                    raise LifeShadowStoreError("causal episode genesis already exists")
            elif (
                previous is None
                or int(previous["revision"]) + 1 != episode.revision
                or str(previous["payload_sha256"]) != episode.supersedes_episode_sha256
            ):
                raise LifeShadowStoreError("causal episode revision is discontinuous")
            connection.execute(
                """
                INSERT INTO causal_episodes(
                    episode_id, revision, life_id, terminal_status, payload,
                    payload_sha256, created_at_ms, closed_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.episode_id,
                    episode.revision,
                    episode.life_id,
                    episode.terminal_status,
                    payload,
                    episode.episode_sha256,
                    episode.created_at_ms,
                    episode.closed_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def commit_episode_reflection(
        self,
        outcome: EpisodeOutcomeEvidence,
        *,
        now_ms: int,
    ):
        """Atomically close one episode and persist its reflection and question gate."""

        from .reflection import ReflectionResult, close_episode_and_reflect

        outcome, outcome_payload = _revalidate_contract(
            outcome, EpisodeOutcomeEvidence, "episode outcome"
        )
        if not outcome.has_valid_evidence_sha256():
            raise LifeShadowStoreError("episode outcome digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM episode_outcomes WHERE outcome_evidence_id = ?",
                (outcome.outcome_evidence_id,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != outcome_payload:
                    raise LifeShadowStoreError("episode outcome identity was rebound")
                episode_row = connection.execute(
                    """
                    SELECT payload FROM causal_episodes
                    WHERE episode_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (outcome.episode_id,),
                ).fetchone()
                reflection_row = connection.execute(
                    "SELECT payload FROM reflection_cards WHERE episode_id = ?",
                    (outcome.episode_id,),
                ).fetchone()
                question_row = connection.execute(
                    """
                    SELECT payload FROM reflection_question_decisions
                    WHERE reflection_id = (
                        SELECT reflection_id FROM reflection_cards WHERE episode_id = ?
                    )
                    """,
                    (outcome.episode_id,),
                ).fetchone()
                if episode_row is None or reflection_row is None:
                    raise LifeShadowStoreError("episode reflection commit is incomplete")
                result = ReflectionResult(
                    _parse_stored_contract(
                        bytes(episode_row["payload"]), CausalEpisode, "causal episode"
                    ),
                    _parse_stored_contract(
                        bytes(reflection_row["payload"]), ReflectionCard, "reflection"
                    ),
                    None
                    if question_row is None
                    else _parse_stored_contract(
                        bytes(question_row["payload"]),
                        ReflectionQuestionDecision,
                        "reflection question decision",
                    ),
                )
                connection.execute("COMMIT")
                return result

            episode_row = connection.execute(
                """
                SELECT payload FROM causal_episodes
                WHERE episode_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (outcome.episode_id,),
            ).fetchone()
            if episode_row is None:
                raise LifeShadowStoreError("episode outcome lacks its open causal episode")
            episode = _parse_stored_contract(
                bytes(episode_row["payload"]), CausalEpisode, "causal episode"
            )
            if episode.terminal_status != "OPEN":
                raise LifeShadowStoreError("causal episode is already terminal")
            event_rows = connection.execute(
                """
                SELECT event_id, life_id FROM life_events
                WHERE event_id IN (%s)
                """ % ",".join("?" for _ in outcome.outcome_event_ids),
                outcome.outcome_event_ids,
            ).fetchall()
            if (
                {str(row["event_id"]) for row in event_rows} != set(outcome.outcome_event_ids)
                or any(str(row["life_id"]) != outcome.life_id for row in event_rows)
            ):
                raise LifeShadowStoreError("episode outcome event evidence is missing")
            last_question_at = None
            if outcome.preference_domain is not None:
                last = connection.execute(
                    """
                    SELECT created_at_ms FROM reflection_question_decisions
                    WHERE life_id = ? AND preference_domain = ? AND outcome = 'ask_user'
                    ORDER BY created_at_ms DESC LIMIT 1
                    """,
                    (outcome.life_id, outcome.preference_domain),
                ).fetchone()
                if last is not None:
                    last_question_at = int(last["created_at_ms"])
            result = close_episode_and_reflect(
                episode,
                outcome,
                now_ms=now_ms,
                last_question_at_ms=last_question_at,
            )
            closed_payload = canonical_json_bytes(result.closed_episode)
            reflection_payload = canonical_json_bytes(result.reflection)
            connection.execute(
                """
                INSERT INTO episode_outcomes(
                    outcome_evidence_id, life_id, episode_id, payload,
                    evidence_sha256, occurred_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.outcome_evidence_id,
                    outcome.life_id,
                    outcome.episode_id,
                    outcome_payload,
                    outcome.evidence_sha256,
                    outcome.occurred_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO causal_episodes(
                    episode_id, revision, life_id, terminal_status, payload,
                    payload_sha256, created_at_ms, closed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.closed_episode.episode_id,
                    result.closed_episode.revision,
                    result.closed_episode.life_id,
                    result.closed_episode.terminal_status,
                    closed_payload,
                    result.closed_episode.episode_sha256,
                    result.closed_episode.created_at_ms,
                    result.closed_episode.closed_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO reflection_cards(
                    reflection_id, life_id, episode_id, payload,
                    payload_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.reflection.reflection_id,
                    result.reflection.life_id,
                    result.reflection.episode_id,
                    reflection_payload,
                    result.reflection.reflection_sha256,
                    result.reflection.created_at_ms,
                ),
            )
            if result.question_decision is not None:
                question = result.question_decision
                connection.execute(
                    """
                    INSERT INTO reflection_question_decisions(
                        question_decision_id, life_id, reflection_id,
                        preference_domain, outcome, cooldown_until_ms, payload,
                        decision_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        question.question_decision_id,
                        question.life_id,
                        question.reflection_id,
                        question.preference_domain,
                        question.outcome,
                        question.cooldown_until_ms,
                        canonical_json_bytes(question),
                        question.decision_sha256,
                        question.created_at_ms,
                    ),
                )
            connection.execute("COMMIT")
            return result
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_causal_hypothesis(self, hypothesis: CausalHypothesis) -> bool:
        hypothesis, payload = _revalidate_contract(
            hypothesis,
            CausalHypothesis,
            "causal hypothesis",
        )
        if not hypothesis.has_valid_hypothesis_sha256():
            raise LifeShadowStoreError("causal hypothesis digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT payload FROM causal_edge_versions
                WHERE hypothesis_id = ? AND revision = ?
                """,
                (hypothesis.hypothesis_id, hypothesis.revision),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("causal hypothesis identity was rebound")
                connection.execute("COMMIT")
                return False
            previous = connection.execute(
                """
                SELECT revision FROM causal_edge_versions
                WHERE hypothesis_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (hypothesis.hypothesis_id,),
            ).fetchone()
            if hypothesis.revision == 1:
                if previous is not None:
                    raise LifeShadowStoreError("causal hypothesis genesis already exists")
            elif previous is None or int(previous["revision"]) + 1 != hypothesis.revision:
                raise LifeShadowStoreError("causal hypothesis revision is discontinuous")
            connection.execute(
                """
                INSERT INTO causal_edge_versions(
                    hypothesis_id, revision, life_id, cause_ref, effect_ref,
                    status, payload, payload_sha256, created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hypothesis.hypothesis_id,
                    hypothesis.revision,
                    hypothesis.life_id,
                    hypothesis.cause_ref,
                    hypothesis.effect_ref,
                    hypothesis.status,
                    payload,
                    hypothesis.hypothesis_sha256,
                    hypothesis.valid_from_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_action_impact(self, impact: ActionImpact) -> bool:
        impact, payload = _revalidate_contract(impact, ActionImpact, "action impact")
        if not impact.has_valid_impact_sha256():
            raise LifeShadowStoreError("action impact digest is invalid")
        return self._insert_immutable(
            select_sql="SELECT payload FROM action_impacts WHERE impact_id = ?",
            select_values=(impact.impact_id,),
            insert_sql="""
                INSERT INTO action_impacts(
                    impact_id, life_id, action_id, payload, payload_sha256, created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                impact.impact_id,
                impact.life_id,
                impact.action_id,
                payload,
                impact.impact_sha256,
                impact.created_at_ms,
            ),
            payload=payload,
            identity="action impact",
        )

    def put_action_candidate(self, candidate: ActionCandidate) -> bool:
        candidate, payload = _revalidate_contract(
            candidate, ActionCandidate, "action candidate"
        )
        if not candidate.has_valid_candidate_sha256():
            raise LifeShadowStoreError("action candidate digest is invalid")
        return self._insert_immutable(
            select_sql="SELECT payload FROM action_candidates WHERE candidate_id = ?",
            select_values=(candidate.candidate_id,),
            insert_sql="""
                INSERT INTO action_candidates(
                    candidate_id, life_id, episode_id, action_id, workspace_id,
                    payload, candidate_sha256, proposed_at_ms, expires_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                candidate.candidate_id,
                candidate.life_id,
                candidate.episode_id,
                candidate.action_id,
                candidate.workspace_id,
                payload,
                candidate.candidate_sha256,
                candidate.proposed_at_ms,
                candidate.expires_at_ms,
            ),
            payload=payload,
            identity="action candidate",
        )

    def put_autonomy_policy(self, policy: AutonomyPolicySnapshot) -> bool:
        policy, payload = _revalidate_contract(
            policy, AutonomyPolicySnapshot, "autonomy policy"
        )
        if not policy.has_valid_policy_sha256():
            raise LifeShadowStoreError("autonomy policy digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM autonomy_policies WHERE life_id = ? AND revision = ?",
                (policy.life_id, policy.revision),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("autonomy policy identity was rebound")
                connection.execute("COMMIT")
                return False
            previous = connection.execute(
                """
                SELECT revision, policy_sha256 FROM autonomy_policies
                WHERE life_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (policy.life_id,),
            ).fetchone()
            if previous is None:
                if policy.revision != 1 or policy.supersedes_policy_sha256 is not None:
                    raise LifeShadowStoreError("autonomy policy chain must start at revision one")
            elif (
                policy.revision != int(previous["revision"]) + 1
                or policy.supersedes_policy_sha256 != str(previous["policy_sha256"])
            ):
                raise LifeShadowStoreError("autonomy policy revision chain is discontinuous")
            connection.execute(
                """
                INSERT INTO autonomy_policies(
                    life_id, revision, policy_id, payload, policy_sha256,
                    effective_at_ms, expires_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.life_id,
                    policy.revision,
                    policy.policy_id,
                    payload,
                    policy.policy_sha256,
                    policy.effective_at_ms,
                    policy.expires_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def get_latest_autonomy_policy(
        self, life_id: str
    ) -> AutonomyPolicySnapshot | None:
        row = self._connection.execute(
            """
            SELECT payload FROM autonomy_policies
            WHERE life_id = ? ORDER BY revision DESC LIMIT 1
            """,
            (life_id,),
        ).fetchone()
        if row is None:
            return None
        return _parse_stored_contract(
            bytes(row["payload"]), AutonomyPolicySnapshot, "autonomy policy"
        )

    def put_autonomy_usage(self, usage: AutonomyUsageSnapshot) -> bool:
        usage, payload = _revalidate_contract(
            usage, AutonomyUsageSnapshot, "autonomy usage"
        )
        if not usage.has_valid_usage_sha256():
            raise LifeShadowStoreError("autonomy usage digest is invalid")
        if usage.revision != 1:
            raise LifeShadowStoreError(
                "autonomy usage revisions after genesis require atomic execution commit"
            )
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM autonomy_usage_snapshots WHERE usage_sha256 = ?",
                (usage.usage_sha256,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("autonomy usage identity was rebound")
                connection.execute("COMMIT")
                return False
            latest = connection.execute(
                """
                SELECT revision, usage_sha256 FROM autonomy_usage_snapshots
                WHERE life_id = ? AND policy_snapshot_hash = ? AND day_start_ms = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (usage.life_id, usage.policy_snapshot_hash, usage.day_start_ms),
            ).fetchone()
            if latest is None:
                if usage.revision != 1 or usage.supersedes_usage_sha256 is not None:
                    raise LifeShadowStoreError("autonomy usage chain must start at revision one")
            elif (
                usage.revision != int(latest["revision"]) + 1
                or usage.supersedes_usage_sha256 != str(latest["usage_sha256"])
            ):
                raise LifeShadowStoreError("autonomy usage revision chain is discontinuous")
            connection.execute(
                """
                INSERT INTO autonomy_usage_snapshots(
                    usage_sha256, life_id, policy_snapshot_hash, revision,
                    supersedes_usage_sha256, day_start_ms, day_end_ms, payload,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.usage_sha256,
                    usage.life_id,
                    usage.policy_snapshot_hash,
                    usage.revision,
                    usage.supersedes_usage_sha256,
                    usage.day_start_ms,
                    usage.day_end_ms,
                    payload,
                    usage.created_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_agency_decision(self, decision: AgencyDecision) -> bool:
        decision, payload = _revalidate_contract(
            decision,
            AgencyDecision,
            "agency decision",
        )
        if not decision.has_valid_decision_sha256():
            raise LifeShadowStoreError("agency decision digest is invalid")
        if decision.outcome == "execute":
            raise LifeShadowStoreError(
                "executing agency decision requires atomic budget consumption"
            )
        return self._insert_immutable(
            select_sql="SELECT payload FROM agency_decisions WHERE decision_id = ?",
            select_values=(decision.decision_id,),
            insert_sql="""
                INSERT INTO agency_decisions(
                    decision_id, life_id, episode_id, outcome, payload,
                    payload_sha256, created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                decision.decision_id,
                decision.life_id,
                decision.episode_id,
                decision.outcome,
                payload,
                decision.decision_sha256,
                decision.created_at_ms,
            ),
            payload=payload,
            identity="agency decision",
        )

    def commit_agency_execution(
        self,
        decision: AgencyDecision,
        *,
        previous_usage: AutonomyUsageSnapshot,
        next_usage: AutonomyUsageSnapshot,
    ) -> bool:
        """Atomically persist execution intent and its compare-and-swap budget fact."""

        from .agency import advance_autonomy_usage

        decision, decision_payload = _revalidate_contract(
            decision, AgencyDecision, "agency decision"
        )
        previous_usage, _ = _revalidate_contract(
            previous_usage, AutonomyUsageSnapshot, "previous autonomy usage"
        )
        next_usage, next_payload = _revalidate_contract(
            next_usage, AutonomyUsageSnapshot, "next autonomy usage"
        )
        if (
            decision.outcome != "execute"
            or not decision.has_valid_decision_sha256()
            or not previous_usage.has_valid_usage_sha256()
            or not next_usage.has_valid_usage_sha256()
        ):
            raise LifeShadowStoreError("atomic agency execution evidence is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            candidate_row = connection.execute(
                "SELECT payload FROM action_candidates WHERE candidate_id = ?",
                (decision.selected_candidate_id,),
            ).fetchone()
            impact_row = connection.execute(
                "SELECT payload FROM action_impacts WHERE payload_sha256 = ?",
                (decision.action_impact_sha256,),
            ).fetchone()
            policy_row = connection.execute(
                "SELECT payload FROM autonomy_policies WHERE policy_sha256 = ?",
                (decision.policy_snapshot_hash,),
            ).fetchone()
            if candidate_row is None or impact_row is None or policy_row is None:
                raise LifeShadowStoreError(
                    "atomic agency execution lacks candidate, impact, or policy facts"
                )
            candidate = _parse_stored_contract(
                bytes(candidate_row["payload"]), ActionCandidate, "action candidate"
            )
            impact = _parse_stored_contract(
                bytes(impact_row["payload"]), ActionImpact, "action impact"
            )
            policy = _parse_stored_contract(
                bytes(policy_row["payload"]), AutonomyPolicySnapshot, "autonomy policy"
            )
            expected_next = advance_autonomy_usage(
                previous_usage,
                policy=policy,
                decision=decision,
                candidate=candidate,
                impact=impact,
            )
            if canonical_json_bytes(expected_next) != next_payload:
                raise LifeShadowStoreError("next autonomy usage is not machine-derived")

            existing_decision = connection.execute(
                "SELECT payload FROM agency_decisions WHERE decision_id = ?",
                (decision.decision_id,),
            ).fetchone()
            existing_usage = connection.execute(
                "SELECT payload FROM autonomy_usage_snapshots WHERE usage_sha256 = ?",
                (next_usage.usage_sha256,),
            ).fetchone()
            if existing_decision is not None or existing_usage is not None:
                if (
                    existing_decision is None
                    or existing_usage is None
                    or bytes(existing_decision["payload"]) != decision_payload
                    or bytes(existing_usage["payload"]) != next_payload
                ):
                    raise LifeShadowStoreError("atomic agency execution was partially rebound")
                connection.execute("COMMIT")
                return False

            latest = connection.execute(
                """
                SELECT revision, usage_sha256 FROM autonomy_usage_snapshots
                WHERE life_id = ? AND policy_snapshot_hash = ? AND day_start_ms = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (
                    previous_usage.life_id,
                    previous_usage.policy_snapshot_hash,
                    previous_usage.day_start_ms,
                ),
            ).fetchone()
            if (
                latest is None
                or int(latest["revision"]) != previous_usage.revision
                or str(latest["usage_sha256"]) != previous_usage.usage_sha256
            ):
                raise LifeShadowStoreError("autonomy budget compare-and-swap failed")
            connection.execute(
                """
                INSERT INTO agency_decisions(
                    decision_id, life_id, episode_id, outcome, payload,
                    payload_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.life_id,
                    decision.episode_id,
                    decision.outcome,
                    decision_payload,
                    decision.decision_sha256,
                    decision.created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO autonomy_usage_snapshots(
                    usage_sha256, life_id, policy_snapshot_hash, revision,
                    supersedes_usage_sha256, day_start_ms, day_end_ms, payload,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_usage.usage_sha256,
                    next_usage.life_id,
                    next_usage.policy_snapshot_hash,
                    next_usage.revision,
                    next_usage.supersedes_usage_sha256,
                    next_usage.day_start_ms,
                    next_usage.day_end_ms,
                    next_payload,
                    next_usage.created_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_reflection_card(self, reflection: ReflectionCard) -> bool:
        reflection, payload = _revalidate_contract(
            reflection,
            ReflectionCard,
            "reflection",
        )
        if not reflection.has_valid_reflection_sha256():
            raise LifeShadowStoreError("reflection digest is invalid")
        return self._insert_immutable(
            select_sql="SELECT payload FROM reflection_cards WHERE reflection_id = ?",
            select_values=(reflection.reflection_id,),
            insert_sql="""
                INSERT INTO reflection_cards(
                    reflection_id, life_id, episode_id, payload,
                    payload_sha256, created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """,
            insert_values=(
                reflection.reflection_id,
                reflection.life_id,
                reflection.episode_id,
                payload,
                reflection.reflection_sha256,
                reflection.created_at_ms,
            ),
            payload=payload,
            identity="reflection",
        )

    def put_capability_profile(self, profile: CapabilityProfile) -> bool:
        profile, payload = _revalidate_contract(
            profile,
            CapabilityProfile,
            "capability profile",
        )
        if not profile.has_valid_profile_sha256():
            raise LifeShadowStoreError("capability profile digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT payload FROM capability_profiles
                WHERE capability_id = ? AND version = ?
                  AND profile_revision = ? AND life_id = ?
                """,
                (
                    profile.capability_id,
                    profile.version,
                    profile.profile_revision,
                    profile.life_id,
                ),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("capability profile identity was rebound")
                connection.execute("COMMIT")
                return False
            previous = connection.execute(
                """
                SELECT profile_revision, payload_sha256 FROM capability_profiles
                WHERE capability_id = ? AND version = ? AND life_id = ?
                ORDER BY profile_revision DESC
                LIMIT 1
                """,
                (profile.capability_id, profile.version, profile.life_id),
            ).fetchone()
            if profile.profile_revision == 1:
                if previous is not None:
                    raise LifeShadowStoreError("capability profile genesis already exists")
            elif (
                previous is None
                or int(previous["profile_revision"]) + 1 != profile.profile_revision
                or str(previous["payload_sha256"]) != profile.supersedes_profile_sha256
            ):
                raise LifeShadowStoreError("capability profile revision is discontinuous")
            connection.execute(
                """
                INSERT INTO capability_profiles(
                    capability_id, version, profile_revision, life_id, payload,
                    payload_sha256, updated_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.capability_id,
                    profile.version,
                    profile.profile_revision,
                    profile.life_id,
                    payload,
                    profile.profile_sha256,
                    profile.updated_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def commit_capability_learning(
        self,
        evidences: tuple[CapabilityEvidence, ...],
        *,
        scope: str,
        now_ms: int,
    ):
        """Merge evidence candidates and atomically persist one profile revision."""

        from .capability_learning import CapabilityLearningResult, learn_capability

        if not evidences:
            raise LifeShadowStoreError("capability learning requires evidence")
        parsed = tuple(
            _revalidate_contract(item, CapabilityEvidence, "capability evidence")[0]
            for item in evidences
        )
        if any(not item.has_valid_evidence_sha256() for item in parsed):
            raise LifeShadowStoreError("capability evidence digest is invalid")
        identities = {
            (item.capability_id, item.capability_version, item.life_id) for item in parsed
        }
        if len(identities) != 1:
            raise LifeShadowStoreError("capability evidence crosses profiles")
        capability_id, version, life_id = next(iter(identities))
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            new_items: list[CapabilityEvidence] = []
            for item in parsed:
                existing = connection.execute(
                    "SELECT payload FROM capability_evidence WHERE evidence_id = ?",
                    (item.evidence_id,),
                ).fetchone()
                if existing is None:
                    new_items.append(item)
                elif bytes(existing["payload"]) != canonical_json_bytes(item):
                    raise LifeShadowStoreError("capability evidence identity was rebound")
            if not new_items:
                profile_row = connection.execute(
                    """
                    SELECT payload FROM capability_profiles
                    WHERE capability_id = ? AND version = ? AND life_id = ?
                    ORDER BY profile_revision DESC LIMIT 1
                    """,
                    (capability_id, version, life_id),
                ).fetchone()
                decision_row = connection.execute(
                    """
                    SELECT payload FROM capability_learning_decisions
                    WHERE capability_id = ? AND capability_version = ? AND life_id = ?
                    ORDER BY created_at_ms DESC, learning_decision_id DESC LIMIT 1
                    """,
                    (capability_id, version, life_id),
                ).fetchone()
                if profile_row is None or decision_row is None:
                    raise LifeShadowStoreError("capability learning commit is incomplete")
                result = CapabilityLearningResult(
                    _parse_stored_contract(
                        bytes(profile_row["payload"]), CapabilityProfile, "capability profile"
                    ),
                    _parse_stored_contract(
                        bytes(decision_row["payload"]),
                        CapabilityLearningDecision,
                        "capability learning decision",
                    ),
                )
                connection.execute("COMMIT")
                return result

            for item in new_items:
                reflection_row = connection.execute(
                    "SELECT life_id, episode_id FROM reflection_cards WHERE reflection_id = ?",
                    (item.reflection_id,),
                ).fetchone()
                outcome_row = connection.execute(
                    "SELECT 1 FROM episode_outcomes WHERE episode_id = ? AND life_id = ?",
                    (item.episode_id, item.life_id),
                ).fetchone()
                impact_row = connection.execute(
                    "SELECT 1 FROM action_impacts WHERE payload_sha256 = ? AND life_id = ?",
                    (item.action_impact_sha256, item.life_id),
                ).fetchone()
                if (
                    reflection_row is None
                    or reflection_row["life_id"] != item.life_id
                    or reflection_row["episode_id"] != item.episode_id
                    or outcome_row is None
                    or impact_row is None
                ):
                    raise LifeShadowStoreError("capability evidence lacks terminal causal facts")

            stored_rows = connection.execute(
                """
                SELECT payload FROM capability_evidence
                WHERE capability_id = ? AND capability_version = ? AND life_id = ?
                ORDER BY evidence_id
                """,
                (capability_id, version, life_id),
            ).fetchall()
            merged = {
                item.evidence_id: item
                for item in (
                    *(
                        _parse_stored_contract(
                            bytes(row["payload"]), CapabilityEvidence, "capability evidence"
                        )
                        for row in stored_rows
                    ),
                    *new_items,
                )
            }
            profile_row = connection.execute(
                """
                SELECT payload FROM capability_profiles
                WHERE capability_id = ? AND version = ? AND life_id = ?
                ORDER BY profile_revision DESC LIMIT 1
                """,
                (capability_id, version, life_id),
            ).fetchone()
            decision_row = connection.execute(
                """
                SELECT payload FROM capability_learning_decisions
                WHERE capability_id = ? AND capability_version = ? AND life_id = ?
                ORDER BY created_at_ms DESC, learning_decision_id DESC LIMIT 1
                """,
                (capability_id, version, life_id),
            ).fetchone()
            previous_profile = (
                None
                if profile_row is None
                else _parse_stored_contract(
                    bytes(profile_row["payload"]), CapabilityProfile, "capability profile"
                )
            )
            previous_decision = (
                None
                if decision_row is None
                else _parse_stored_contract(
                    bytes(decision_row["payload"]),
                    CapabilityLearningDecision,
                    "capability learning decision",
                )
            )
            result = learn_capability(
                tuple(merged[name] for name in sorted(merged)),
                scope=scope,
                now_ms=now_ms,
                previous_profile=previous_profile,
                previous_decision=previous_decision,
            )
            for item in new_items:
                connection.execute(
                    """
                    INSERT INTO capability_evidence(
                        evidence_id, capability_id, capability_version, life_id,
                        episode_id, outcome, payload, payload_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.evidence_id,
                        item.capability_id,
                        item.capability_version,
                        item.life_id,
                        item.episode_id,
                        item.outcome,
                        canonical_json_bytes(item),
                        item.evidence_sha256,
                        item.created_at_ms,
                    ),
                )
            profile = result.profile
            connection.execute(
                """
                INSERT INTO capability_profiles(
                    capability_id, version, profile_revision, life_id, payload,
                    payload_sha256, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.capability_id,
                    profile.version,
                    profile.profile_revision,
                    profile.life_id,
                    canonical_json_bytes(profile),
                    profile.profile_sha256,
                    profile.updated_at_ms,
                ),
            )
            decision = result.decision
            connection.execute(
                """
                INSERT INTO capability_learning_decisions(
                    learning_decision_id, capability_id, capability_version,
                    life_id, evidence_set_sha256, outcome, payload,
                    decision_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.learning_decision_id,
                    decision.capability_id,
                    decision.capability_version,
                    decision.life_id,
                    decision.evidence_set_sha256,
                    decision.outcome,
                    canonical_json_bytes(decision),
                    decision.decision_sha256,
                    decision.created_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return result
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def apply_capability_rollback(
        self,
        *,
        capability_id: str,
        capability_version: str,
        life_id: str,
        trigger_evidence_ids: tuple[str, ...],
        invalidated_context_pack_ids: tuple[str, ...] = (),
        invalidated_skill_activation_ids: tuple[str, ...] = (),
        now_ms: int,
    ):
        """Rollback a profile and invalidate every explicitly related activation/context."""

        from .capability_learning import CapabilityRollbackResult, rollback_capability

        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            profile_row = connection.execute(
                """
                SELECT payload FROM capability_profiles
                WHERE capability_id = ? AND version = ? AND life_id = ?
                ORDER BY profile_revision DESC LIMIT 1
                """,
                (capability_id, capability_version, life_id),
            ).fetchone()
            if profile_row is None:
                raise LifeShadowStoreError("capability rollback lacks an active profile")
            profile = _parse_stored_contract(
                bytes(profile_row["payload"]), CapabilityProfile, "capability profile"
            )
            evidence_rows = connection.execute(
                "SELECT payload FROM capability_evidence WHERE evidence_id IN (%s)"
                % ",".join("?" for _ in trigger_evidence_ids),
                trigger_evidence_ids,
            ).fetchall() if trigger_evidence_ids else ()
            triggers = tuple(
                _parse_stored_contract(
                    bytes(row["payload"]), CapabilityEvidence, "capability evidence"
                )
                for row in evidence_rows
            )
            for pack_id in invalidated_context_pack_ids:
                if connection.execute(
                    "SELECT 1 FROM causal_context_packs WHERE pack_id = ?", (pack_id,)
                ).fetchone() is None:
                    raise LifeShadowStoreError("rollback context pack does not exist")
            for activation_id in invalidated_skill_activation_ids:
                if connection.execute(
                    "SELECT 1 FROM skill_activation_refs WHERE activation_id = ?",
                    (activation_id,),
                ).fetchone() is None:
                    raise LifeShadowStoreError("rollback Skill activation does not exist")
            result = rollback_capability(
                profile,
                triggers,
                invalidated_context_pack_ids=invalidated_context_pack_ids,
                invalidated_skill_activation_ids=invalidated_skill_activation_ids,
                now_ms=now_ms,
            )
            rolled = result.profile
            record = result.record
            connection.execute(
                """
                INSERT INTO capability_profiles(
                    capability_id, version, profile_revision, life_id, payload,
                    payload_sha256, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rolled.capability_id,
                    rolled.version,
                    rolled.profile_revision,
                    rolled.life_id,
                    canonical_json_bytes(rolled),
                    rolled.profile_sha256,
                    rolled.updated_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO capability_rollbacks(
                    rollback_id, capability_id, capability_version, life_id,
                    payload, rollback_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.rollback_id,
                    record.capability_id,
                    record.capability_version,
                    record.life_id,
                    canonical_json_bytes(record),
                    record.rollback_sha256,
                    record.created_at_ms,
                ),
            )
            for target_kind, refs in (
                ("context_pack", invalidated_context_pack_ids),
                ("skill_activation", invalidated_skill_activation_ids),
            ):
                for target_ref in refs:
                    connection.execute(
                        """
                        INSERT INTO capability_invalidations(
                            rollback_id, target_kind, target_ref, created_at_ms
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (record.rollback_id, target_kind, target_ref, now_ms),
                    )
            for activation_id in invalidated_skill_activation_ids:
                connection.execute(
                    "UPDATE skill_activation_refs SET status = 'INVALIDATED' WHERE activation_id = ?",
                    (activation_id,),
                )
            connection.execute("COMMIT")
            return CapabilityRollbackResult(rolled, record)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def put_context_capsule(self, capsule: TaskContinuityCapsule) -> bool:
        capsule, payload = _revalidate_contract(
            capsule,
            TaskContinuityCapsule,
            "context capsule",
        )
        if not capsule.has_valid_capsule_sha256():
            raise LifeShadowStoreError("context capsule digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM context_capsules WHERE capsule_id = ?",
                (capsule.capsule_id,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["payload"]) != payload:
                    raise LifeShadowStoreError("context capsule identity was rebound")
                connection.execute("COMMIT")
                return False
            active = connection.execute(
                """
                SELECT capsule_id FROM context_capsules
                WHERE request_id = ? AND status = 'ACTIVE'
                """,
                (capsule.request_id,),
            ).fetchone()
            active_id = None if active is None else str(active["capsule_id"])
            if active_id != capsule.supersedes_capsule_id:
                raise LifeShadowStoreError("context capsule does not supersede the active checkpoint")
            if active_id is not None:
                connection.execute(
                    "UPDATE context_capsules SET status = 'SUPERSEDED' WHERE capsule_id = ?",
                    (active_id,),
                )
            status = (
                "TERMINAL"
                if capsule.capsule_kind == "TERMINAL_RESULT"
                else "ACTIVE"
            )
            connection.execute(
                """
                INSERT INTO context_capsules(
                    capsule_id, request_id, run_id, generation, life_id,
                    capsule_kind, status, supersedes_capsule_id, payload,
                    payload_sha256, created_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload,
                    capsule.capsule_sha256,
                    capsule.created_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def ingest_source_event(
        self,
        ingress: LifeEventIngress,
        *,
        received_at_ms: int,
        event_factory: Callable[[int, str | None], LifeEventEnvelope],
        receipt_factory: Callable[
            [LifeEventEnvelope, bool], LifeEventIngressReceipt
        ],
    ) -> LifeIngressCommit:
        """Consume one ordered source fact and advance its offset atomically."""
        ingress, ingress_payload = _revalidate_contract(
            ingress, LifeEventIngress, "life event ingress"
        )
        if (
            not ingress.has_valid_ingress_sha256()
            or received_at_ms < ingress.observed_at_ms
        ):
            raise LifeShadowStoreError("life event ingress digest or time is invalid")
        connection = self._connection
        consumer_id = f"{ingress.source_component_id}@{ingress.source_epoch}"
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM life_ingress_receipts WHERE ingress_id = ?",
                (ingress.ingress_id,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["ingress"]) != ingress_payload:
                    raise LifeShadowStoreError("life ingress identity was rebound")
                receipt = _parse_stored_contract(
                    bytes(existing["receipt"]),
                    LifeEventIngressReceipt,
                    "life ingress receipt",
                )
                event_row = connection.execute(
                    "SELECT envelope FROM life_events WHERE event_id = ?",
                    (receipt.event_id,),
                ).fetchone()
                if event_row is None:
                    raise LifeShadowStoreError("life ingress receipt event is missing")
                event = _parse_stored_contract(
                    bytes(event_row["envelope"]), LifeEventEnvelope, "life event"
                )
                connection.execute("COMMIT")
                return LifeIngressCommit(event, receipt, False, False)

            sequence_conflict = connection.execute(
                """
                SELECT ingress_id FROM life_ingress_receipts
                WHERE source_component_id = ? AND source_epoch = ? AND source_sequence = ?
                """,
                (
                    ingress.source_component_id,
                    ingress.source_epoch,
                    ingress.source_sequence,
                ),
            ).fetchone()
            if sequence_conflict is not None:
                raise LifeShadowStoreError("life source sequence was rebound")
            offset = connection.execute(
                """
                SELECT last_sequence FROM consumer_offsets
                WHERE consumer_id = ? AND life_id = ?
                """,
                (consumer_id, ingress.life_id),
            ).fetchone()
            expected_source_sequence = 1 if offset is None else int(offset[0]) + 1
            if ingress.source_sequence != expected_source_sequence:
                raise LifeShadowStoreError("life source sequence is discontinuous")

            dedupe = connection.execute(
                """
                SELECT event_id, event_hash FROM life_ingress_dedupe
                WHERE source_component_id = ? AND life_id = ? AND dedupe_key = ?
                """,
                (ingress.source_component_id, ingress.life_id, ingress.dedupe_key),
            ).fetchone()
            event_created = dedupe is None
            if dedupe is None:
                head = connection.execute(
                    """
                    SELECT event_count, head_event_hash FROM projection_heads
                    WHERE life_id = ?
                    """,
                    (ingress.life_id,),
                ).fetchone()
                next_event_sequence = 1 if head is None else int(head["event_count"]) + 1
                previous_event_hash = None if head is None else str(head["head_event_hash"])
                event = event_factory(next_event_sequence, previous_event_hash)
                event, event_payload = _revalidate_contract(
                    event, LifeEventEnvelope, "life event"
                )
                if not event.has_valid_event_hash():
                    raise LifeShadowStoreError("life event digest is invalid")
            else:
                event_row = connection.execute(
                    "SELECT envelope FROM life_events WHERE event_id = ?",
                    (dedupe["event_id"],),
                ).fetchone()
                if event_row is None:
                    raise LifeShadowStoreError("life ingress dedupe event is missing")
                event_payload = bytes(event_row["envelope"])
                event = _parse_stored_contract(
                    event_payload, LifeEventEnvelope, "life event"
                )
                if event.event_hash != dedupe["event_hash"]:
                    raise LifeShadowStoreError("life ingress dedupe hash is invalid")

            expected_event_fields = {
                "life_id": ingress.life_id,
                "source_service": ingress.source_component_id,
                "source_kind": ingress.source_kind,
                "event_kind": ingress.event_kind,
                "occurred_at_ms": ingress.occurred_at_ms,
                "observed_at_ms": ingress.observed_at_ms,
                "principal_ref": ingress.principal_ref,
                "subject_refs": ingress.subject_refs,
                "evidence_class": ingress.evidence_class,
                "source_credibility_milli": ingress.source_credibility_milli,
                "privacy_scope": ingress.privacy_scope,
                "content_object_id": ingress.content_object_id,
                "content_sha256": ingress.content_sha256,
                "dedupe_key": ingress.dedupe_key,
                "causation_id": ingress.causation_id,
                "correlation_id": ingress.correlation_id,
            }
            if any(
                getattr(event, name) != value
                for name, value in expected_event_fields.items()
            ):
                raise LifeShadowStoreError("life event is not bound to its ingress")

            receipt = receipt_factory(event, not event_created)
            receipt, receipt_payload = _revalidate_contract(
                receipt, LifeEventIngressReceipt, "life ingress receipt"
            )
            if (
                not receipt.has_valid_receipt_sha256()
                or receipt.ingress_id != ingress.ingress_id
                or receipt.life_id != ingress.life_id
                or receipt.source_component_id != ingress.source_component_id
                or receipt.source_epoch != ingress.source_epoch
                or receipt.source_sequence != ingress.source_sequence
                or receipt.duplicate != (not event_created)
                or receipt.event_id != event.event_id
                or receipt.event_hash != event.event_hash
                or receipt.consumer_offset != ingress.source_sequence
                or receipt.received_at_ms != received_at_ms
            ):
                raise LifeShadowStoreError("life ingress receipt binding is invalid")

            if event_created:
                replay_head = connection.execute(
                    """
                    SELECT writer_epoch, event_count, head_event_hash, replay_sha256
                    FROM projection_heads WHERE life_id = ?
                    """,
                    (event.life_id,),
                ).fetchone()
                if replay_head is None:
                    if event.sequence != 1 or event.previous_event_hash is not None:
                        raise LifeShadowStoreError("life event chain lacks its genesis")
                    previous_replay = None
                else:
                    if (
                        event.sequence != int(replay_head["event_count"]) + 1
                        or event.previous_event_hash
                        != str(replay_head["head_event_hash"])
                        or event.writer_epoch < int(replay_head["writer_epoch"])
                    ):
                        raise LifeShadowStoreError("life event chain is discontinuous")
                    previous_replay = str(replay_head["replay_sha256"])
                replay_sha256 = advance_replay_sha256(previous_replay, event.event_hash)
                connection.execute(
                    """
                    INSERT INTO life_events(
                        event_id, life_id, sequence, writer_epoch, event_kind,
                        observed_at_ms, previous_event_hash, event_hash, envelope
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.life_id,
                        event.sequence,
                        event.writer_epoch,
                        event.event_kind,
                        event.observed_at_ms,
                        event.previous_event_hash,
                        event.event_hash,
                        event_payload,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO projection_heads(
                        life_id, writer_epoch, event_count, head_event_id,
                        head_event_hash, replay_sha256, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(life_id) DO UPDATE SET
                        writer_epoch=excluded.writer_epoch,
                        event_count=excluded.event_count,
                        head_event_id=excluded.head_event_id,
                        head_event_hash=excluded.head_event_hash,
                        replay_sha256=excluded.replay_sha256,
                        updated_at_ms=excluded.updated_at_ms
                    """,
                    (
                        event.life_id,
                        event.writer_epoch,
                        event.sequence,
                        event.event_id,
                        event.event_hash,
                        replay_sha256,
                        event.observed_at_ms,
                    ),
                )

            connection.execute(
                """
                INSERT INTO life_ingress_receipts(
                    ingress_id, source_component_id, source_epoch, source_sequence,
                    life_id, dedupe_key, ingress_sha256, event_id, event_hash,
                    duplicate, received_at_ms, ingress, receipt, receipt_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ingress.ingress_id,
                    ingress.source_component_id,
                    ingress.source_epoch,
                    ingress.source_sequence,
                    ingress.life_id,
                    ingress.dedupe_key,
                    ingress.ingress_sha256,
                    event.event_id,
                    event.event_hash,
                    int(not event_created),
                    received_at_ms,
                    ingress_payload,
                    receipt_payload,
                    receipt.receipt_sha256,
                ),
            )
            if event_created:
                connection.execute(
                    """
                    INSERT INTO life_ingress_dedupe(
                        source_component_id, life_id, dedupe_key, first_ingress_id,
                        event_id, event_hash, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ingress.source_component_id,
                        ingress.life_id,
                        ingress.dedupe_key,
                        ingress.ingress_id,
                        event.event_id,
                        event.event_hash,
                        received_at_ms,
                    ),
                )
            connection.execute(
                """
                INSERT INTO consumer_offsets(
                    consumer_id, life_id, last_sequence, last_event_hash, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(consumer_id, life_id) DO UPDATE SET
                    last_sequence=excluded.last_sequence,
                    last_event_hash=excluded.last_event_hash,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    consumer_id,
                    ingress.life_id,
                    ingress.source_sequence,
                    event.event_hash,
                    received_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return LifeIngressCommit(event, receipt, event_created, True)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def append_event(self, event: LifeEventEnvelope) -> bool:
        event, envelope = _revalidate_contract(event, LifeEventEnvelope, "life event")
        if not event.has_valid_event_hash():
            raise LifeShadowStoreError("life event digest is invalid")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT envelope FROM life_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if bytes(existing["envelope"]) != envelope:
                    raise LifeShadowStoreError("life event identity was rebound")
                connection.execute("COMMIT")
                return False
            head = connection.execute(
                """
                SELECT writer_epoch, event_count, head_event_hash, replay_sha256
                FROM projection_heads
                WHERE life_id = ?
                """,
                (event.life_id,),
            ).fetchone()
            if head is None:
                if event.sequence != 1 or event.previous_event_hash is not None:
                    raise LifeShadowStoreError("life event chain lacks its genesis")
                previous_replay = None
            else:
                if (
                    event.sequence != int(head["event_count"]) + 1
                    or event.previous_event_hash != str(head["head_event_hash"])
                    or event.writer_epoch < int(head["writer_epoch"])
                ):
                    raise LifeShadowStoreError("life event chain is discontinuous")
                previous_replay = str(head["replay_sha256"])
            replay_sha256 = advance_replay_sha256(previous_replay, event.event_hash)
            connection.execute(
                """
                INSERT INTO life_events(
                    event_id, life_id, sequence, writer_epoch, event_kind,
                    observed_at_ms, previous_event_hash, event_hash, envelope
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.life_id,
                    event.sequence,
                    event.writer_epoch,
                    event.event_kind,
                    event.observed_at_ms,
                    event.previous_event_hash,
                    event.event_hash,
                    envelope,
                ),
            )
            connection.execute(
                """
                INSERT INTO projection_heads(
                    life_id, writer_epoch, event_count, head_event_id,
                    head_event_hash, replay_sha256, updated_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(life_id) DO UPDATE SET
                    writer_epoch = excluded.writer_epoch,
                    event_count = excluded.event_count,
                    head_event_id = excluded.head_event_id,
                    head_event_hash = excluded.head_event_hash,
                    replay_sha256 = excluded.replay_sha256,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    event.life_id,
                    event.writer_epoch,
                    event.sequence,
                    event.event_id,
                    event.event_hash,
                    replay_sha256,
                    event.observed_at_ms,
                ),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def load_events(self, life_id: str) -> tuple[LifeEventEnvelope, ...]:
        rows = self._connection.execute(
            "SELECT envelope FROM life_events WHERE life_id = ? ORDER BY sequence",
            (life_id,),
        ).fetchall()
        result: list[LifeEventEnvelope] = []
        for row in rows:
            payload = bytes(row["envelope"])
            try:
                event = LifeEventEnvelope.model_validate_json(payload)
            except Exception as exc:
                raise LifeShadowStoreError("stored life event is invalid") from exc
            if canonical_json_bytes(event) != payload:
                raise LifeShadowStoreError("stored life event is not canonical")
            result.append(event)
        return tuple(result)

    def get_ingress_receipt(
        self, ingress_id: str
    ) -> LifeEventIngressReceipt | None:
        row = self._connection.execute(
            "SELECT receipt FROM life_ingress_receipts WHERE ingress_id = ?",
            (ingress_id,),
        ).fetchone()
        if row is None:
            return None
        return _parse_stored_contract(
            bytes(row["receipt"]), LifeEventIngressReceipt, "life ingress receipt"
        )

    def _verify_ingress_ledger(self) -> None:
        rows = self._connection.execute(
            """
            SELECT * FROM life_ingress_receipts
            ORDER BY source_component_id, source_epoch, life_id, source_sequence
            """
        ).fetchall()
        grouped_sequences: dict[tuple[str, int, str], list[int]] = {}
        for row in rows:
            ingress = _parse_stored_contract(
                bytes(row["ingress"]), LifeEventIngress, "life event ingress"
            )
            receipt = _parse_stored_contract(
                bytes(row["receipt"]),
                LifeEventIngressReceipt,
                "life ingress receipt",
            )
            if not ingress.has_valid_ingress_sha256() or not receipt.has_valid_receipt_sha256():
                raise LifeShadowStoreError("life ingress ledger digest is invalid")
            expected = {
                "ingress_id": ingress.ingress_id,
                "source_component_id": ingress.source_component_id,
                "source_epoch": ingress.source_epoch,
                "source_sequence": ingress.source_sequence,
                "life_id": ingress.life_id,
                "dedupe_key": ingress.dedupe_key,
                "ingress_sha256": ingress.ingress_sha256,
                "event_id": receipt.event_id,
                "event_hash": receipt.event_hash,
                "duplicate": int(receipt.duplicate),
                "received_at_ms": receipt.received_at_ms,
                "receipt_sha256": receipt.receipt_sha256,
            }
            if any(row[name] != value for name, value in expected.items()):
                raise LifeShadowStoreError("life ingress ledger columns disagree with contracts")
            if (
                receipt.ingress_id != ingress.ingress_id
                or receipt.life_id != ingress.life_id
                or receipt.source_component_id != ingress.source_component_id
                or receipt.source_epoch != ingress.source_epoch
                or receipt.source_sequence != ingress.source_sequence
            ):
                raise LifeShadowStoreError("life ingress receipt crossed its source binding")
            event_row = self._connection.execute(
                "SELECT envelope FROM life_events WHERE event_id = ?",
                (receipt.event_id,),
            ).fetchone()
            if event_row is None:
                raise LifeShadowStoreError("life ingress event is missing")
            event = _parse_stored_contract(
                bytes(event_row["envelope"]), LifeEventEnvelope, "life event"
            )
            if event.event_hash != receipt.event_hash or event.dedupe_key != ingress.dedupe_key:
                raise LifeShadowStoreError("life ingress event binding is invalid")
            key = (ingress.source_component_id, ingress.source_epoch, ingress.life_id)
            grouped_sequences.setdefault(key, []).append(ingress.source_sequence)

        for (source, epoch, life_id), sequences in grouped_sequences.items():
            if sequences != list(range(1, len(sequences) + 1)):
                raise LifeShadowStoreError("life ingress source sequence cannot be replayed")
            consumer_id = f"{source}@{epoch}"
            offset = self._connection.execute(
                """
                SELECT last_sequence, last_event_hash FROM consumer_offsets
                WHERE consumer_id = ? AND life_id = ?
                """,
                (consumer_id, life_id),
            ).fetchone()
            last = next(
                row for row in reversed(rows)
                if row["source_component_id"] == source
                and row["source_epoch"] == epoch
                and row["life_id"] == life_id
            )
            if (
                offset is None
                or int(offset["last_sequence"]) != sequences[-1]
                or str(offset["last_event_hash"]) != str(last["event_hash"])
            ):
                raise LifeShadowStoreError("life ingress consumer offset is invalid")

        dedupe_rows = self._connection.execute(
            """
            SELECT * FROM life_ingress_dedupe
            ORDER BY source_component_id, life_id, dedupe_key
            """
        ).fetchall()
        expected_dedupe = {
            (row["source_component_id"], row["life_id"], row["dedupe_key"])
            for row in rows
        }
        if {
            (row["source_component_id"], row["life_id"], row["dedupe_key"])
            for row in dedupe_rows
        } != expected_dedupe:
            raise LifeShadowStoreError("life ingress dedupe membership is invalid")
        for row in dedupe_rows:
            first = self._connection.execute(
                "SELECT * FROM life_ingress_receipts WHERE ingress_id = ?",
                (row["first_ingress_id"],),
            ).fetchone()
            if (
                first is None
                or int(first["duplicate"]) != 0
                or first["source_component_id"] != row["source_component_id"]
                or first["life_id"] != row["life_id"]
                or first["dedupe_key"] != row["dedupe_key"]
                or first["event_id"] != row["event_id"]
                or first["event_hash"] != row["event_hash"]
            ):
                raise LifeShadowStoreError("life ingress dedupe anchor is invalid")

    def replay(self, life_id: str) -> LifeReplaySummary:
        try:
            summary = replay_life_events(self.load_events(life_id))
        except ValueError as exc:
            raise LifeShadowStoreError(str(exc)) from exc
        row = self._connection.execute(
            "SELECT * FROM projection_heads WHERE life_id = ?",
            (life_id,),
        ).fetchone()
        if row is None or (
            int(row["writer_epoch"]) != summary.writer_epoch
            or int(row["event_count"]) != summary.event_count
            or str(row["head_event_id"]) != summary.head_event_id
            or str(row["head_event_hash"]) != summary.head_event_hash
            or str(row["replay_sha256"]) != summary.replay_sha256
        ):
            raise LifeShadowStoreError("life projection head disagrees with replay")
        return summary

    def _verify_causal_memory_state(self) -> None:
        tombstones: dict[str, PrivacyDeletionTombstone] = {}
        destroyed_payload_ids: set[str] = set()
        for row in self._connection.execute(
            "SELECT * FROM privacy_deletion_tombstones ORDER BY tombstone_id"
        ).fetchall():
            tombstone = _parse_stored_contract(
                bytes(row["payload"]),
                PrivacyDeletionTombstone,
                "privacy deletion tombstone",
            )
            expected = {
                "tombstone_id": tombstone.tombstone_id,
                "life_id": tombstone.life_id,
                "target_kind": tombstone.target_kind,
                "target_ref_hash": tombstone.target_ref_hash,
                "privacy_scope": tombstone.privacy_scope,
                "deletion_proof_sha256": tombstone.deletion_proof_sha256,
                "created_at_ms": tombstone.created_at_ms,
            }
            if (
                any(row[key] != value for key, value in expected.items())
                or not tombstone.has_valid_deletion_proof_sha256()
            ):
                raise LifeShadowStoreError("privacy deletion tombstone is invalid")
            legacy = self._connection.execute(
                "SELECT * FROM tombstones WHERE tombstone_id = ?",
                (tombstone.tombstone_id,),
            ).fetchone()
            if legacy is None or any(
                legacy[key] != expected[key]
                for key in (
                    "tombstone_id",
                    "life_id",
                    "target_kind",
                    "target_ref_hash",
                    "deletion_proof_sha256",
                    "created_at_ms",
                )
            ):
                raise LifeShadowStoreError("privacy deletion audit proof is missing")
            tombstones[tombstone.tombstone_id] = tombstone
            destroyed_payload_ids.update(tombstone.destroyed_payload_ids)

        protected_rows = self._connection.execute(
            """
            SELECT p.*, k.key_material
            FROM protected_payloads AS p
            LEFT JOIN protected_payload_keys AS k ON k.payload_id = p.payload_id
            ORDER BY p.payload_id
            """
        ).fetchall()
        protected: dict[str, sqlite3.Row] = {}
        for row in protected_rows:
            payload_id = str(row["payload_id"])
            self._protected_payload_record_from_row(
                row, key_available=row["key_material"] is not None
            )
            if row["key_material"] is None:
                if (
                    row["key_destroyed_at_ms"] is None
                    or payload_id not in destroyed_payload_ids
                ):
                    raise LifeShadowStoreError(
                        "protected payload lacks a key-destruction proof"
                    )
            elif row["key_destroyed_at_ms"] is not None:
                raise LifeShadowStoreError(
                    "protected payload retains a key after destruction"
                )
            else:
                try:
                    AESGCM(bytes(row["key_material"])).decrypt(
                        bytes(row["nonce"]),
                        bytes(row["ciphertext"]),
                        _protected_payload_aad(
                            payload_id=payload_id,
                            life_id=str(row["life_id"]),
                            privacy_scope=str(row["privacy_scope"]),
                        ),
                    )
                except Exception as exc:
                    raise LifeShadowStoreError(
                        "protected payload authentication is invalid"
                    ) from exc
            protected[payload_id] = row

        memory_rows = self._connection.execute(
            """
            SELECT a.*, c.payload, c.assertion_sha256
            FROM memory_assertions AS a
            JOIN memory_assertion_contracts AS c
              ON c.memory_id = a.memory_id AND c.revision = a.revision
            ORDER BY a.memory_id, a.revision
            """
        ).fetchall()
        memory_history: dict[str, list[MemoryAssertionV3]] = {}
        memory_by_version: dict[tuple[str, int], MemoryAssertionV3] = {}
        for row in memory_rows:
            assertion = _parse_stored_contract(
                bytes(row["payload"]), MemoryAssertionV3, "memory assertion"
            )
            expected = {
                "memory_id": assertion.memory_id,
                "revision": assertion.revision,
                "life_id": assertion.life_id,
                "status": assertion.lifecycle_status,
                "privacy_scope": assertion.privacy_scope,
                "payload_object_id": assertion.protected_payload_id,
                "payload_sha256": assertion.protected_payload_sha256,
                "created_at_ms": assertion.created_at_ms,
                "assertion_sha256": assertion.assertion_sha256,
            }
            if (
                any(row[key] != value for key, value in expected.items())
                or not assertion.has_valid_assertion_sha256()
            ):
                raise LifeShadowStoreError("memory assertion columns are invalid")
            history = memory_history.setdefault(assertion.memory_id, [])
            previous = None if not history else history[-1]
            if (
                assertion.revision != len(history) + 1
                or (
                    previous is None
                    and assertion.supersedes_assertion_sha256 is not None
                )
                or (
                    previous is not None
                    and assertion.supersedes_assertion_sha256
                    != previous.assertion_sha256
                )
            ):
                raise LifeShadowStoreError("memory assertion revision chain is invalid")
            if assertion.lifecycle_status == "deleted":
                if (
                    assertion.deletion_tombstone_id not in tombstones
                    or tombstones[assertion.deletion_tombstone_id].life_id
                    != assertion.life_id
                ):
                    raise LifeShadowStoreError(
                        "deleted memory lacks its privacy tombstone"
                    )
            else:
                assert assertion.protected_payload_id is not None
                payload_row = protected.get(assertion.protected_payload_id)
                if (
                    payload_row is None
                    or str(payload_row["life_id"]) != assertion.life_id
                    or str(payload_row["privacy_scope"]) != assertion.privacy_scope
                    or str(payload_row["ciphertext_sha256"])
                    != assertion.protected_payload_sha256
                ):
                    raise LifeShadowStoreError(
                        "memory assertion protected binding is invalid"
                    )
            history.append(assertion)
            memory_by_version[(assertion.memory_id, assertion.revision)] = assertion

        for history in memory_history.values():
            latest = history[-1]
            if latest.lifecycle_status == "active":
                assert latest.protected_payload_id is not None
                if protected[latest.protected_payload_id]["key_material"] is None:
                    raise LifeShadowStoreError("active memory payload key is unavailable")
            if latest.lifecycle_status == "deleted":
                for historical in history[:-1]:
                    if historical.protected_payload_id is not None and protected[
                        historical.protected_payload_id
                    ]["key_material"] is not None:
                        raise LifeShadowStoreError(
                            "deleted memory retains a historical payload key"
                        )

        latest_by_memory = {
            memory_id: history[-1] for memory_id, history in memory_history.items()
        }
        for row in self._connection.execute(
            "SELECT * FROM memory_search_terms ORDER BY memory_id, revision, term_hmac_sha256"
        ).fetchall():
            memory_id = str(row["memory_id"])
            latest = latest_by_memory.get(memory_id)
            indexed = memory_by_version.get((memory_id, int(row["revision"])))
            if (
                indexed is None
                or latest != indexed
                or indexed.lifecycle_status != "active"
                or row["privacy_scope"] != indexed.privacy_scope
                or indexed.protected_payload_id is None
                or protected[indexed.protected_payload_id]["key_material"] is None
            ):
                raise LifeShadowStoreError("memory search index exposes stale content")

        for row in self._connection.execute(
            "SELECT * FROM memory_relations ORDER BY relation_id"
        ).fetchall():
            relation = _parse_stored_contract(
                bytes(row["payload"]), MemoryRelationV3, "memory relation"
            )
            if (
                not relation.has_valid_relation_sha256()
                or row["relation_id"] != relation.relation_id
                or row["life_id"] != relation.life_id
                or row["source_memory_id"] != relation.source_memory_id
                or row["relation_kind"] != relation.relation_kind
                or row["target_ref"] != relation.target_ref
                or row["payload_sha256"] != relation.relation_sha256
                or row["created_at_ms"] != relation.created_at_ms
                or relation.source_memory_id not in latest_by_memory
                or latest_by_memory[relation.source_memory_id].life_id != relation.life_id
            ):
                raise LifeShadowStoreError("memory relation is invalid")

        for row in self._connection.execute(
            "SELECT * FROM causal_nodes ORDER BY node_id"
        ).fetchall():
            node = _parse_stored_contract(
                bytes(row["payload"]), CausalNodeV3, "causal node"
            )
            payload_row = protected.get(node.protected_payload_id)
            if (
                not node.has_valid_node_sha256()
                or row["node_id"] != node.node_id
                or row["life_id"] != node.life_id
                or row["node_kind"] != node.node_kind
                or row["payload_sha256"] != node.node_sha256
                or row["created_at_ms"] != node.created_at_ms
                or payload_row is None
                or str(payload_row["life_id"]) != node.life_id
                or str(payload_row["privacy_scope"]) != node.privacy_scope
                or str(payload_row["ciphertext_sha256"])
                != node.protected_payload_sha256
            ):
                raise LifeShadowStoreError("causal memory node is invalid")
            if payload_row["key_material"] is None:
                target_hash = hashlib.sha256(node.node_id.encode("utf-8")).hexdigest()
                suppression = self._connection.execute(
                    """
                    SELECT 1 FROM privacy_suppressions
                    WHERE target_kind = 'causal_node' AND target_ref_hash = ?
                      AND privacy_scope = ?
                    """,
                    (target_hash, node.privacy_scope),
                ).fetchone()
                if suppression is None:
                    raise LifeShadowStoreError(
                        "unreadable causal node lacks privacy suppression"
                    )

        for row in self._connection.execute(
            "SELECT * FROM causal_node_terms ORDER BY node_id, term_hmac_sha256"
        ).fetchall():
            node_row = self._connection.execute(
                "SELECT payload FROM causal_nodes WHERE node_id = ?", (row["node_id"],)
            ).fetchone()
            if node_row is None:
                raise LifeShadowStoreError("causal node index is orphaned")
            node = _parse_stored_contract(
                bytes(node_row["payload"]), CausalNodeV3, "causal node"
            )
            if (
                node.recall_status != "active"
                or row["privacy_scope"] != node.privacy_scope
                or protected[node.protected_payload_id]["key_material"] is None
            ):
                raise LifeShadowStoreError("causal node index exposes stale content")

        for row in self._connection.execute(
            "SELECT * FROM causal_context_packs ORDER BY pack_id"
        ).fetchall():
            payload_row = protected.get(str(row["protected_payload_id"]))
            if payload_row is None:
                raise LifeShadowStoreError("causal context protected payload is missing")
            if payload_row["key_material"] is None:
                target_hash = hashlib.sha256(
                    str(row["pack_id"]).encode("utf-8")
                ).hexdigest()
                if self._connection.execute(
                    """
                    SELECT 1 FROM privacy_suppressions
                    WHERE target_kind = 'context_pack' AND target_ref_hash = ?
                    """,
                    (target_hash,),
                ).fetchone() is None:
                    raise LifeShadowStoreError(
                        "unreadable context pack lacks privacy suppression"
                    )
                continue
            if self._connection.execute(
                """
                SELECT 1 FROM capability_invalidations
                WHERE target_kind = 'context_pack' AND target_ref = ?
                """,
                (str(row["pack_id"]),),
            ).fetchone() is not None:
                continue
            pack = self.read_causal_context_pack(str(row["pack_id"]))
            capsule_row = self._connection.execute(
                "SELECT payload FROM context_capsules WHERE capsule_id = ?",
                (pack.continuity.capsule_id,),
            ).fetchone()
            if (
                not pack.has_valid_pack_sha256()
                or capsule_row is None
                or _parse_stored_contract(
                    bytes(capsule_row["payload"]),
                    TaskContinuityCapsule,
                    "context capsule",
                )
                != pack.continuity
            ):
                raise LifeShadowStoreError("causal context continuity is invalid")

    def _verify_affect_state(self) -> None:
        policy_history: dict[str, list[AffectSourcePolicySnapshot]] = {}
        for row in self._connection.execute(
            "SELECT * FROM affect_source_policies ORDER BY life_id, revision"
        ).fetchall():
            policy = _parse_stored_contract(
                bytes(row["payload"]),
                AffectSourcePolicySnapshot,
                "affect source policy",
            )
            history = policy_history.setdefault(policy.life_id, [])
            previous = None if not history else history[-1]
            if (
                not policy.has_valid_policy_sha256()
                or row["life_id"] != policy.life_id
                or row["revision"] != policy.revision
                or row["policy_sha256"] != policy.policy_sha256
                or row["effective_at_ms"] != policy.effective_at_ms
                or policy.revision != len(history) + 1
                or (
                    previous is None
                    and policy.supersedes_policy_sha256 is not None
                )
                or (
                    previous is not None
                    and policy.supersedes_policy_sha256 != previous.policy_sha256
                )
            ):
                raise LifeShadowStoreError("affect source policy history is invalid")
            history.append(policy)

        appraisals: dict[str, AppraisalVectorV3] = {}
        for row in self._connection.execute(
            "SELECT * FROM appraisal_events ORDER BY appraisal_id"
        ).fetchall():
            appraisal = _parse_stored_contract(
                bytes(row["payload"]), AppraisalVectorV3, "appraisal"
            )
            if (
                not appraisal.has_valid_appraisal_sha256()
                or row["appraisal_id"] != appraisal.appraisal_id
                or row["life_id"] != appraisal.life_id
                or row["payload_sha256"] != appraisal.appraisal_sha256
                or row["appraised_at_ms"] != appraisal.appraised_at_ms
            ):
                raise LifeShadowStoreError("affect appraisal row is invalid")
            appraisals[appraisal.appraisal_id] = appraisal

        states: dict[tuple[str, int], AffectiveStateV3] = {}
        state_history: dict[str, list[AffectiveStateV3]] = {}
        for row in self._connection.execute(
            "SELECT * FROM affect_snapshots ORDER BY life_id, revision"
        ).fetchall():
            state = _parse_stored_contract(
                bytes(row["payload"]), AffectiveStateV3, "affective state"
            )
            history = state_history.setdefault(state.life_id, [])
            previous = None if not history else history[-1]
            if (
                not state.has_valid_state_sha256()
                or row["life_id"] != state.life_id
                or row["revision"] != state.revision
                or row["payload_sha256"] != state.state_sha256
                or row["created_at_ms"] != state.updated_at_ms
                or state.revision != len(history) + 1
                or (
                    previous is None
                    and state.supersedes_state_sha256 is not None
                )
                or (
                    previous is not None
                    and state.supersedes_state_sha256 != previous.state_sha256
                )
                or (
                    previous is not None
                    and state.updated_at_ms < previous.updated_at_ms
                )
            ):
                raise LifeShadowStoreError("affective state history is invalid")
            history.append(state)
            states[(state.life_id, state.revision)] = state

        receipts_by_stream: dict[
            tuple[str, str], list[tuple[AffectSignal, AffectIntakeReceipt]]
        ] = {}
        receipts_by_dedupe: dict[
            tuple[str, str], list[tuple[AffectSignal, AffectIntakeReceipt]]
        ] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM affect_signal_receipts
            ORDER BY life_id, source_stream_id, source_epoch, source_sequence
            """
        ).fetchall():
            signal = _parse_stored_contract(
                bytes(row["signal"]), AffectSignal, "affect signal"
            )
            receipt = _parse_stored_contract(
                bytes(row["receipt"]), AffectIntakeReceipt, "affect intake receipt"
            )
            event_row = self._connection.execute(
                "SELECT envelope FROM life_events WHERE event_id = ?",
                (signal.source_event_id,),
            ).fetchone()
            if event_row is None:
                raise LifeShadowStoreError("affect receipt source event is missing")
            event = _parse_stored_contract(
                bytes(event_row["envelope"]), LifeEventEnvelope, "life event"
            )
            expected = {
                "signal_id": signal.signal_id,
                "source_event_id": signal.source_event_id,
                "life_id": signal.life_id,
                "source_family": signal.source_family,
                "source_stream_id": signal.source_stream_id,
                "source_epoch": signal.source_epoch,
                "source_sequence": signal.source_sequence,
                "dedupe_key": signal.dedupe_key,
                "accepted": 1 if receipt.accepted else 0,
                "signal_sha256": signal.signal_sha256,
                "receipt_sha256": receipt.receipt_sha256,
                "received_at_ms": receipt.received_at_ms,
            }
            if (
                any(row[key] != value for key, value in expected.items())
                or not signal.has_valid_signal_sha256()
                or not receipt.has_valid_receipt_sha256()
                or receipt.duplicate
                or receipt.signal_id != signal.signal_id
                or receipt.life_id != signal.life_id
                or receipt.source_event_id != signal.source_event_id
                or receipt.source_stream_id != signal.source_stream_id
                or receipt.source_epoch != signal.source_epoch
                or receipt.source_sequence != signal.source_sequence
                or event.life_id != signal.life_id
                or event.event_hash != signal.source_event_hash
                or event.content_sha256 != signal.content_sha256
            ):
                raise LifeShadowStoreError("affect signal receipt is invalid")
            if receipt.accepted:
                appraisal = appraisals.get(str(receipt.appraisal_id))
                state = states.get((receipt.life_id, int(receipt.affect_revision)))
                if (
                    appraisal is None
                    or state is None
                    or appraisal.appraisal_sha256 != receipt.appraisal_sha256
                    or state.state_sha256 != receipt.affect_state_sha256
                    or appraisal.source_event_ids != (signal.source_event_id,)
                    or state.last_source_event_id != signal.source_event_id
                    or state.last_effective_intensity_milli
                    != receipt.effective_intensity_milli
                    or state.last_repetition_count != receipt.repetition_count
                ):
                    raise LifeShadowStoreError(
                        "accepted affect receipt state binding is invalid"
                    )
            receipts_by_stream.setdefault(
                (signal.life_id, signal.source_stream_id), []
            ).append((signal, receipt))
            receipts_by_dedupe.setdefault(
                (signal.life_id, signal.dedupe_key), []
            ).append((signal, receipt))

        for key, values in receipts_by_stream.items():
            prior_epoch = None
            prior_sequence = None
            for signal, _receipt in values:
                if prior_epoch is None:
                    if signal.source_sequence != 1:
                        raise LifeShadowStoreError(
                            "affect source stream lacks its genesis"
                        )
                elif signal.source_epoch == prior_epoch:
                    if signal.source_sequence != prior_sequence + 1:
                        raise LifeShadowStoreError(
                            "affect source stream sequence is discontinuous"
                        )
                elif (
                    signal.source_epoch != prior_epoch + 1
                    or signal.source_sequence != 1
                ):
                    raise LifeShadowStoreError(
                        "affect source stream epoch is discontinuous"
                    )
                prior_epoch = signal.source_epoch
                prior_sequence = signal.source_sequence
            offset = self._connection.execute(
                """
                SELECT * FROM affect_source_offsets
                WHERE life_id = ? AND source_stream_id = ?
                """,
                key,
            ).fetchone()
            last_signal, last_receipt = values[-1]
            if (
                offset is None
                or offset["source_epoch"] != last_signal.source_epoch
                or offset["source_sequence"] != last_signal.source_sequence
                or offset["last_signal_id"] != last_signal.signal_id
                or offset["updated_at_ms"] != last_receipt.received_at_ms
            ):
                raise LifeShadowStoreError("affect source offset is invalid")

        offset_count = int(
            self._connection.execute(
                "SELECT count(*) FROM affect_source_offsets"
            ).fetchone()[0]
        )
        if offset_count != len(receipts_by_stream):
            raise LifeShadowStoreError("affect source offset is orphaned")

        for key, values in receipts_by_dedupe.items():
            dedupe = self._connection.execute(
                """
                SELECT * FROM affect_dedupe
                WHERE life_id = ? AND dedupe_key = ?
                """,
                key,
            ).fetchone()
            ordered = sorted(values, key=lambda item: item[1].repetition_count)
            if (
                dedupe is None
                or dedupe["occurrence_count"] != len(values)
                or dedupe["first_signal_id"] != ordered[0][0].signal_id
                or dedupe["last_signal_id"] != ordered[-1][0].signal_id
                or dedupe["first_seen_at_ms"] != ordered[0][1].received_at_ms
                or dedupe["last_seen_at_ms"] != ordered[-1][1].received_at_ms
                or tuple(sorted(receipt.repetition_count for _, receipt in values))
                != tuple(range(1, len(values) + 1))
            ):
                raise LifeShadowStoreError("affect repetition ledger is invalid")
        dedupe_count = int(
            self._connection.execute("SELECT count(*) FROM affect_dedupe").fetchone()[0]
        )
        if dedupe_count != len(receipts_by_dedupe):
            raise LifeShadowStoreError("affect repetition ledger is orphaned")

    def _verify_autonomy_state(self) -> None:
        for row in self._connection.execute(
            "SELECT * FROM viability_snapshots ORDER BY life_id, revision"
        ).fetchall():
            state = _parse_stored_contract(
                bytes(row["payload"]), ViabilityState, "viability state"
            )
            if (
                not state.has_valid_state_sha256()
                or row["life_id"] != state.life_id
                or row["revision"] != state.revision
                or row["payload_sha256"] != state.state_sha256
                or row["created_at_ms"] != state.created_at_ms
            ):
                raise LifeShadowStoreError("viability state row is invalid")

        for row in self._connection.execute(
            "SELECT * FROM viability_observations ORDER BY observation_id"
        ).fetchall():
            observation = _parse_stored_contract(
                bytes(row["payload"]), ViabilityObservation, "viability observation"
            )
            event_row = self._connection.execute(
                "SELECT life_id FROM life_events WHERE event_id = ?",
                (observation.source_event_id,),
            ).fetchone()
            if (
                not observation.has_valid_observation_sha256()
                or event_row is None
                or event_row["life_id"] != observation.life_id
                or row["observation_id"] != observation.observation_id
                or row["life_id"] != observation.life_id
                or row["dimension"] != observation.dimension
                or row["source_event_id"] != observation.source_event_id
                or row["observation_sha256"] != observation.observation_sha256
                or row["measured_at_ms"] != observation.measured_at_ms
                or row["stale_after_ms"] != observation.stale_after_ms
            ):
                raise LifeShadowStoreError("viability observation row is invalid")

        for row in self._connection.execute(
            "SELECT * FROM action_candidates ORDER BY candidate_id"
        ).fetchall():
            candidate = _parse_stored_contract(
                bytes(row["payload"]), ActionCandidate, "action candidate"
            )
            if (
                not candidate.has_valid_candidate_sha256()
                or row["candidate_id"] != candidate.candidate_id
                or row["life_id"] != candidate.life_id
                or row["episode_id"] != candidate.episode_id
                or row["action_id"] != candidate.action_id
                or row["workspace_id"] != candidate.workspace_id
                or row["candidate_sha256"] != candidate.candidate_sha256
                or row["proposed_at_ms"] != candidate.proposed_at_ms
                or row["expires_at_ms"] != candidate.expires_at_ms
            ):
                raise LifeShadowStoreError("action candidate row is invalid")

        histories: dict[str, list[AutonomyPolicySnapshot]] = {}
        policy_hashes: set[str] = set()
        for row in self._connection.execute(
            "SELECT * FROM autonomy_policies ORDER BY life_id, revision"
        ).fetchall():
            policy = _parse_stored_contract(
                bytes(row["payload"]), AutonomyPolicySnapshot, "autonomy policy"
            )
            history = histories.setdefault(policy.life_id, [])
            previous = history[-1] if history else None
            if (
                not policy.has_valid_policy_sha256()
                or row["life_id"] != policy.life_id
                or row["revision"] != policy.revision
                or row["policy_id"] != policy.policy_id
                or row["policy_sha256"] != policy.policy_sha256
                or row["effective_at_ms"] != policy.effective_at_ms
                or row["expires_at_ms"] != policy.expires_at_ms
                or policy.revision != len(history) + 1
                or (previous is None and policy.supersedes_policy_sha256 is not None)
                or (
                    previous is not None
                    and policy.supersedes_policy_sha256 != previous.policy_sha256
                )
            ):
                raise LifeShadowStoreError("autonomy policy history is invalid")
            history.append(policy)
            policy_hashes.add(policy.policy_sha256)

        executing_decision_hashes = {
            str(row["payload_sha256"])
            for row in self._connection.execute(
                "SELECT payload_sha256 FROM agency_decisions WHERE outcome = 'execute'"
            ).fetchall()
        }
        usage_histories: dict[tuple[str, str, int], list[AutonomyUsageSnapshot]] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM autonomy_usage_snapshots
            ORDER BY life_id, policy_snapshot_hash, day_start_ms, revision
            """
        ).fetchall():
            usage = _parse_stored_contract(
                bytes(row["payload"]), AutonomyUsageSnapshot, "autonomy usage"
            )
            history = usage_histories.setdefault(
                (usage.life_id, usage.policy_snapshot_hash, usage.day_start_ms), []
            )
            previous = history[-1] if history else None
            if (
                not usage.has_valid_usage_sha256()
                or row["usage_sha256"] != usage.usage_sha256
                or row["life_id"] != usage.life_id
                or row["policy_snapshot_hash"] != usage.policy_snapshot_hash
                or row["revision"] != usage.revision
                or row["supersedes_usage_sha256"] != usage.supersedes_usage_sha256
                or row["day_start_ms"] != usage.day_start_ms
                or row["day_end_ms"] != usage.day_end_ms
                or row["created_at_ms"] != usage.created_at_ms
                or usage.policy_snapshot_hash not in policy_hashes
                or usage.revision != len(history) + 1
                or (previous is None and usage.supersedes_usage_sha256 is not None)
                or (
                    previous is not None
                    and usage.supersedes_usage_sha256 != previous.usage_sha256
                )
                or (previous is not None and usage.execution_count < previous.execution_count)
                or (previous is not None and usage.resource_cost_milli < previous.resource_cost_milli)
                or (
                    previous is not None
                    and not set(previous.source_decision_hashes).issubset(
                        usage.source_decision_hashes
                    )
                )
                or not set(usage.source_decision_hashes).issubset(
                    executing_decision_hashes
                )
            ):
                raise LifeShadowStoreError("autonomy usage row is invalid")
            history.append(usage)

    def _verify_reflection_learning_state(self) -> None:
        outcomes: dict[str, EpisodeOutcomeEvidence] = {}
        for row in self._connection.execute(
            "SELECT * FROM episode_outcomes ORDER BY outcome_evidence_id"
        ).fetchall():
            outcome = _parse_stored_contract(
                bytes(row["payload"]), EpisodeOutcomeEvidence, "episode outcome"
            )
            episode_row = self._connection.execute(
                """
                SELECT payload FROM causal_episodes
                WHERE episode_id = ? ORDER BY revision DESC LIMIT 1
                """,
                (outcome.episode_id,),
            ).fetchone()
            if episode_row is None:
                raise LifeShadowStoreError("episode outcome lacks terminal episode")
            episode = _parse_stored_contract(
                bytes(episode_row["payload"]), CausalEpisode, "causal episode"
            )
            if (
                not outcome.has_valid_evidence_sha256()
                or row["outcome_evidence_id"] != outcome.outcome_evidence_id
                or row["life_id"] != outcome.life_id
                or row["episode_id"] != outcome.episode_id
                or row["evidence_sha256"] != outcome.evidence_sha256
                or row["occurred_at_ms"] != outcome.occurred_at_ms
                or episode.terminal_status == "OPEN"
                or episode.outcome_event_ids != outcome.outcome_event_ids
            ):
                raise LifeShadowStoreError("episode outcome row is invalid")
            outcomes[outcome.episode_id] = outcome

        reflections: dict[str, ReflectionCard] = {}
        for row in self._connection.execute(
            "SELECT * FROM reflection_cards ORDER BY reflection_id"
        ).fetchall():
            reflection = _parse_stored_contract(
                bytes(row["payload"]), ReflectionCard, "reflection"
            )
            if (
                not reflection.has_valid_reflection_sha256()
                or row["reflection_id"] != reflection.reflection_id
                or row["life_id"] != reflection.life_id
                or row["episode_id"] != reflection.episode_id
                or row["payload_sha256"] != reflection.reflection_sha256
                or row["created_at_ms"] != reflection.created_at_ms
            ):
                raise LifeShadowStoreError("reflection row is invalid")
            reflections[reflection.reflection_id] = reflection

        for row in self._connection.execute(
            "SELECT * FROM reflection_question_decisions ORDER BY question_decision_id"
        ).fetchall():
            question = _parse_stored_contract(
                bytes(row["payload"]),
                ReflectionQuestionDecision,
                "reflection question decision",
            )
            reflection = reflections.get(question.reflection_id)
            if (
                not question.has_valid_decision_sha256()
                or reflection is None
                or reflection.life_id != question.life_id
                or row["question_decision_id"] != question.question_decision_id
                or row["life_id"] != question.life_id
                or row["reflection_id"] != question.reflection_id
                or row["preference_domain"] != question.preference_domain
                or row["outcome"] != question.outcome
                or row["cooldown_until_ms"] != question.cooldown_until_ms
                or row["decision_sha256"] != question.decision_sha256
                or row["created_at_ms"] != question.created_at_ms
            ):
                raise LifeShadowStoreError("reflection question decision row is invalid")

        evidence_by_id: dict[str, CapabilityEvidence] = {}
        for row in self._connection.execute(
            "SELECT * FROM capability_evidence ORDER BY evidence_id"
        ).fetchall():
            evidence = _parse_stored_contract(
                bytes(row["payload"]), CapabilityEvidence, "capability evidence"
            )
            reflection = reflections.get(evidence.reflection_id)
            if (
                not evidence.has_valid_evidence_sha256()
                or reflection is None
                or evidence.episode_id not in outcomes
                or row["evidence_id"] != evidence.evidence_id
                or row["capability_id"] != evidence.capability_id
                or row["capability_version"] != evidence.capability_version
                or row["life_id"] != evidence.life_id
                or row["episode_id"] != evidence.episode_id
                or row["outcome"] != evidence.outcome
                or row["payload_sha256"] != evidence.evidence_sha256
                or row["created_at_ms"] != evidence.created_at_ms
            ):
                raise LifeShadowStoreError("capability evidence row is invalid")
            evidence_by_id[evidence.evidence_id] = evidence

        profiles_by_hash: dict[str, CapabilityProfile] = {}
        histories: dict[tuple[str, str, str], list[CapabilityProfile]] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM capability_profiles
            ORDER BY capability_id, version, life_id, profile_revision
            """
        ).fetchall():
            profile = _parse_stored_contract(
                bytes(row["payload"]), CapabilityProfile, "capability profile"
            )
            history = histories.setdefault(
                (profile.capability_id, profile.version, profile.life_id), []
            )
            previous = history[-1] if history else None
            if (
                not profile.has_valid_profile_sha256()
                or row["capability_id"] != profile.capability_id
                or row["version"] != profile.version
                or row["profile_revision"] != profile.profile_revision
                or row["life_id"] != profile.life_id
                or row["payload_sha256"] != profile.profile_sha256
                or row["updated_at_ms"] != profile.updated_at_ms
                or profile.profile_revision != len(history) + 1
                or (previous is None and profile.supersedes_profile_sha256 is not None)
                or (
                    previous is not None
                    and profile.supersedes_profile_sha256 != previous.profile_sha256
                )
            ):
                raise LifeShadowStoreError("capability profile history is invalid")
            history.append(profile)
            profiles_by_hash[profile.profile_sha256] = profile

        for row in self._connection.execute(
            "SELECT * FROM capability_learning_decisions ORDER BY learning_decision_id"
        ).fetchall():
            decision = _parse_stored_contract(
                bytes(row["payload"]),
                CapabilityLearningDecision,
                "capability learning decision",
            )
            profile = profiles_by_hash.get(decision.resulting_profile_sha256)
            if (
                not decision.has_valid_decision_sha256()
                or profile is None
                or row["learning_decision_id"] != decision.learning_decision_id
                or row["capability_id"] != decision.capability_id
                or row["capability_version"] != decision.capability_version
                or row["life_id"] != decision.life_id
                or row["evidence_set_sha256"] != decision.evidence_set_sha256
                or row["outcome"] != decision.outcome
                or row["decision_sha256"] != decision.decision_sha256
                or row["created_at_ms"] != decision.created_at_ms
                or decision.evidence_set_sha256
                != canonical_sha256(
                    {
                        "domain": "tiangong.life.capability-evidence-set.v1",
                        "evidence_refs": list(profile.evidence_refs),
                    }
                )
            ):
                raise LifeShadowStoreError("capability learning decision row is invalid")

        rollbacks: dict[str, CapabilityRollbackRecord] = {}
        for row in self._connection.execute(
            "SELECT * FROM capability_rollbacks ORDER BY rollback_id"
        ).fetchall():
            record = _parse_stored_contract(
                bytes(row["payload"]), CapabilityRollbackRecord, "capability rollback"
            )
            if (
                not record.has_valid_rollback_sha256()
                or record.resulting_profile_sha256 not in profiles_by_hash
                or any(item not in evidence_by_id for item in record.trigger_evidence_ids)
                or row["rollback_id"] != record.rollback_id
                or row["capability_id"] != record.capability_id
                or row["capability_version"] != record.capability_version
                or row["life_id"] != record.life_id
                or row["rollback_sha256"] != record.rollback_sha256
                or row["created_at_ms"] != record.created_at_ms
            ):
                raise LifeShadowStoreError("capability rollback row is invalid")
            rollbacks[record.rollback_id] = record

        for row in self._connection.execute(
            "SELECT * FROM capability_invalidations ORDER BY target_kind, target_ref"
        ).fetchall():
            record = rollbacks.get(str(row["rollback_id"]))
            target_kind = str(row["target_kind"])
            target_ref = str(row["target_ref"])
            if record is None or (
                target_kind == "context_pack"
                and target_ref not in record.invalidated_context_pack_ids
            ) or (
                target_kind == "skill_activation"
                and target_ref not in record.invalidated_skill_activation_ids
            ):
                raise LifeShadowStoreError("capability invalidation row is invalid")

    def _verify_context_authorizations(self) -> None:
        for row in self._connection.execute(
            """
            SELECT authorization.*, context.pack_sha256, context.life_id AS context_life_id,
                   context.request_id AS context_request_id,
                   context.run_id AS context_run_id,
                   context.generation AS context_generation
            FROM context_authorizations AS authorization
            JOIN causal_context_packs AS context
              ON context.pack_id = authorization.context_pack_id
            ORDER BY authorization.authorization_id
            """
        ).fetchall():
            authorization = _parse_stored_contract(
                bytes(row["payload"]),
                LifeContextAuthorization,
                "life context authorization",
            )
            if (
                not authorization.has_valid_authorization_sha256()
                or not authorization.revisions.has_valid_vector_sha256()
                or row["authorization_id"] != authorization.authorization_id
                or row["life_id"] != authorization.life_id
                or row["request_id"] != authorization.request_id
                or row["run_id"] != authorization.run_id
                or row["generation"] != authorization.generation
                or row["principal_scope_hash"]
                != authorization.principal_scope_hash
                or row["context_pack_id"] != authorization.context_pack_id
                or row["revisions_sha256"]
                != authorization.revisions.vector_sha256
                or row["authorization_sha256"]
                != authorization.authorization_sha256
                or row["issued_at_ms"] != authorization.issued_at_ms
                or row["expires_at_ms"] != authorization.expires_at_ms
                or row["pack_sha256"] != authorization.context_pack_sha256
                or row["context_life_id"] != authorization.life_id
                or row["context_request_id"] != authorization.request_id
                or row["context_run_id"] != authorization.run_id
                or row["context_generation"] != authorization.generation
            ):
                raise LifeShadowStoreError(
                    "life context authorization row is invalid"
                )

    def health(self) -> dict[str, Any]:
        quick = self._connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]).lower() != "ok":
            raise LifeShadowStoreError("shadow store SQLite integrity check failed")
        application_id = int(self._connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if (
            application_id != SHADOW_STORE_APPLICATION_ID
            or user_version != SHADOW_STORE_SCHEMA_VERSION
        ):
            raise LifeShadowStoreError("shadow store identity or version is invalid")
        table_rows = self._connection.execute("PRAGMA table_list").fetchall()
        tables = {
            str(row["name"])
            for row in table_rows
            if str(row["type"]) == "table" and not str(row["name"]).startswith("sqlite_")
        }
        if tables != _EXPECTED_TABLES:
            raise LifeShadowStoreError("shadow store table set is invalid")
        if any(
            int(row["strict"]) != 1
            for row in table_rows
            if str(row["name"]) in _EXPECTED_TABLES
        ):
            raise LifeShadowStoreError("shadow store contains a non-strict table")
        migration = self._connection.execute(
            "SELECT version, migration_id, sql_sha256 FROM schema_migrations"
        ).fetchall()
        expected_migrations = (
            (1, "p1-initial-shadow-schema", _P1_SCHEMA_SHA256),
            (2, _P2_INGRESS_MIGRATION_ID, _P2_INGRESS_SHA256),
            (3, _P3_CAUSAL_MEMORY_MIGRATION_ID, _P3_CAUSAL_MEMORY_SHA256),
            (4, _P4_AFFECT_MIGRATION_ID, _P4_AFFECT_SHA256),
            (5, _P5_AUTONOMY_MIGRATION_ID, _P5_AUTONOMY_SHA256),
            (6, _P6_REFLECTION_MIGRATION_ID, _P6_REFLECTION_SHA256),
            (
                7,
                _P7_CONTEXT_AUTHORIZATION_MIGRATION_ID,
                _P7_CONTEXT_AUTHORIZATION_SHA256,
            ),
            (8, _P8_MEMORY_CHANGE_MIGRATION_ID, _P8_MEMORY_CHANGE_SHA256),
            (9, _P9_V21_LIFE_BINDING_MIGRATION_ID, _P9_V21_LIFE_BINDING_SHA256),
            (10, _P10_V21_CAUSAL_CHILD_MIGRATION_ID, _P10_V21_CAUSAL_CHILD_SHA256),
            (11, _P11_V21_COGNITION_SHADOW_MIGRATION_ID, _P11_V21_COGNITION_SHADOW_SHA256),
            (12, _P12_V21_LIFE_TURN_COMMIT_MIGRATION_ID, _P12_V21_LIFE_TURN_COMMIT_SHA256),
            (13, _P13_V21_CAPABILITY_LIFECYCLE_MIGRATION_ID, _P13_V21_CAPABILITY_LIFECYCLE_SHA256),
        )
        if len(migration) != len(expected_migrations) or any(
            (
                int(row["version"]),
                str(row["migration_id"]),
                str(row["sql_sha256"]),
            )
            != expected
            for row, expected in zip(migration, expected_migrations, strict=True)
        ):
            raise LifeShadowStoreError("shadow store migration ledger is invalid")
        metadata = dict(
            self._connection.execute("SELECT key, value FROM schema_metadata").fetchall()
        )
        if metadata != {
            "purpose": "life-shadow-only",
            "schema_sha256": _SCHEMA_SHA256,
        }:
            raise LifeShadowStoreError("shadow store metadata is invalid")
        life_ids = tuple(
            str(row["life_id"])
            for row in self._connection.execute(
                "SELECT life_id FROM projection_heads ORDER BY life_id"
            ).fetchall()
        )
        summaries = tuple(self.replay(life_id) for life_id in life_ids)
        self._verify_ingress_ledger()
        self._verify_causal_memory_state()
        self._verify_affect_state()
        self._verify_autonomy_state()
        self._verify_reflection_learning_state()
        self._verify_context_authorizations()
        return {
            "application_id": application_id,
            "event_count": sum(item.event_count for item in summaries),
            "life_count": len(summaries),
            "purpose": "life-shadow-only",
            "schema_sha256": _SCHEMA_SHA256,
            "schema_version": user_version,
            "strict_table_count": len(tables),
        }

    def health_cached(self, *, max_age_ms: int = 10_000) -> dict[str, Any]:
        """Bound the full shadow-store audit to a per-change cadence.

        ``health()`` replays every life projection and walks every causal pack
        (seconds on a live store); readiness polling must not pay that cost on
        every call.  ``PRAGMA data_version`` changes on any write, so the
        cached audit is invalidated exactly when the store actually changed,
        and a failing audit is also cached (fail-closed stays visible) instead
        of being retried in a tight loop.
        """
        import threading as _threading
        import time as _time

        lock = getattr(self, "_health_cache_lock", None)
        if lock is None:
            self._health_cache_lock = _threading.Lock()
            self._health_cache: dict[str, Any] | None = None
            self._health_cache_version = -1
            self._health_cache_at_ms = 0
            lock = self._health_cache_lock
        try:
            version = int(self._connection.execute("PRAGMA data_version").fetchone()[0])
        except Exception:
            version = -1
        now_ms = _time.time_ns() // 1_000_000
        with lock:
            cached = self._health_cache
            if (
                cached is not None
                and version >= 0
                and version == self._health_cache_version
                and now_ms - self._health_cache_at_ms < max_age_ms
            ):
                return cached
        try:
            result = self._health_on_snapshot()
        except Exception as exc:
            result = {
                "healthy": False,
                "reason_code": str(exc) or "life.authority_store.health_failed",
                "cached_failure": True,
            }
        with lock:
            self._health_cache = result
            self._health_cache_version = version
            self._health_cache_at_ms = now_ms
        return result

    def _health_on_snapshot(self) -> dict[str, Any]:
        """Run the full shadow-store audit on a private read-only connection.

        ``health()`` replays every projection and walks every causal pack while
        the live connection is also used by concurrent life writers.  Auditing
        the shared connection produced transient ``sqlite3.InterfaceError`` /
        mid-write missing-row failures.  A fresh WAL reader sees a consistent
        snapshot and never interferes with live writes.
        """
        import sqlite3 as _sqlite3
        import threading as _threading

        db_row = self._connection.execute("PRAGMA database_list").fetchone()
        db_path = str(db_row["file"]) if db_row is not None else ""
        if not db_path or db_path in {":memory:", ""}:
            return self.health()
        snapshot = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        snapshot.row_factory = self._connection.row_factory
        shim = object.__new__(type(self))
        shim._connection = snapshot
        shim._lock = _threading.RLock()
        try:
            return shim.health()
        finally:
            snapshot.close()


__all__ = [
    "AffectIntakeCommit",
    "ContextPackPersistRecord",
    "LifeIngressCommit",
    "LifeShadowStore",
    "LifeShadowStoreError",
    "MemoryDeletionResult",
    "ProtectedPayloadRecord",
    "SHADOW_STORE_APPLICATION_ID",
    "SHADOW_STORE_SCHEMA_VERSION",
]
