"""Schema and migration authority for the Life shadow SQLite store.

This module owns schema SQL, migration identities/hashes, schema versions and
schema initialization/migration execution. It does not own domain repositories,
Life transactions, connection opening, health policy or the Store facade.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable

SHADOW_STORE_SCHEMA_VERSION = 17
SHADOW_STORE_APPLICATION_ID = 0x54474C53

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
_P14_MEMORY_DERIVATION_MIGRATION_ID = "p15-memory-derivation-layers"
_P14_MEMORY_DERIVATION_STATEMENTS = (
    """CREATE TABLE memory_derivations (
        derivation_id TEXT PRIMARY KEY CHECK(derivation_id LIKE 'mdr_%'),
        life_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        memory_revision INTEGER NOT NULL CHECK(memory_revision >= 1),
        memory_assertion_sha256 TEXT NOT NULL CHECK(length(memory_assertion_sha256) = 64),
        layer TEXT NOT NULL CHECK(layer IN (
            'L1_STREAM','L2_DIARY','L3_EXPERIENCE','L4_EXPLICIT','L5_CORE'
        )),
        semantic_domain TEXT NOT NULL,
        origin TEXT NOT NULL CHECK(origin IN (
            'LIFE_EVENT','PROMOTION','USER_EXPLICIT','LEARNING_RESULT','MIGRATION'
        )),
        principal_ref TEXT NOT NULL,
        workspace_ref TEXT,
        privacy_scope TEXT NOT NULL,
        claim_key TEXT NOT NULL,
        source_event_ids_json TEXT NOT NULL CHECK(json_valid(source_event_ids_json)),
        lineage_root_event_ids_json TEXT NOT NULL CHECK(json_valid(lineage_root_event_ids_json)),
        external_evidence_refs_json TEXT NOT NULL CHECK(json_valid(external_evidence_refs_json)),
        promotion_policy_version TEXT NOT NULL,
        promotion_reason_codes_json TEXT NOT NULL CHECK(json_valid(promotion_reason_codes_json)),
        valid_from_ms INTEGER NOT NULL CHECK(valid_from_ms >= 0),
        expires_at_ms INTEGER CHECK(expires_at_ms IS NULL OR expires_at_ms > valid_from_ms),
        context_eligible INTEGER NOT NULL CHECK(context_eligible IN (0, 1)),
        learning_eligible INTEGER NOT NULL CHECK(learning_eligible IN (0, 1)),
        temperament_eligible INTEGER NOT NULL CHECK(temperament_eligible IN (0, 1)),
        self_cognition_eligible INTEGER NOT NULL CHECK(self_cognition_eligible IN (0, 1)),
        world_candidate_eligible INTEGER NOT NULL CHECK(world_candidate_eligible IN (0, 1)),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        promotion_key TEXT NOT NULL UNIQUE,
        derivation_sha256 TEXT NOT NULL UNIQUE CHECK(length(derivation_sha256) = 64),
        payload BLOB NOT NULL,
        UNIQUE(life_id, memory_id, memory_revision, layer)
    ) STRICT""",
    """CREATE INDEX memory_derivations_life_layer_idx
    ON memory_derivations(life_id, layer)""",
    """CREATE INDEX memory_derivations_principal_idx
    ON memory_derivations(life_id, principal_ref)""",
    """CREATE INDEX memory_derivations_claim_idx
    ON memory_derivations(claim_key)""",
    """CREATE TABLE memory_derivation_parents (
        derivation_id TEXT NOT NULL
            REFERENCES memory_derivations(derivation_id) ON DELETE RESTRICT,
        parent_derivation_id TEXT NOT NULL
            REFERENCES memory_derivations(derivation_id) ON DELETE RESTRICT,
        parent_memory_id TEXT NOT NULL,
        parent_revision INTEGER NOT NULL CHECK(parent_revision >= 1),
        parent_assertion_sha256 TEXT NOT NULL CHECK(length(parent_assertion_sha256) = 64),
        parent_ref_sha256 TEXT NOT NULL CHECK(length(parent_ref_sha256) = 64),
        created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
        PRIMARY KEY(derivation_id, parent_derivation_id),
        CHECK(derivation_id <> parent_derivation_id)
    ) STRICT""",
    """CREATE INDEX memory_derivation_parents_lookup_idx
    ON memory_derivation_parents(parent_memory_id, parent_revision)""",
    """CREATE TABLE memory_active_heads (
        life_id TEXT NOT NULL,
        principal_ref TEXT NOT NULL,
        claim_key TEXT NOT NULL,
        layer TEXT NOT NULL CHECK(layer IN (
            'L1_STREAM','L2_DIARY','L3_EXPERIENCE','L4_EXPLICIT','L5_CORE'
        )),
        derivation_id TEXT NOT NULL
            REFERENCES memory_derivations(derivation_id) ON DELETE RESTRICT,
        memory_id TEXT NOT NULL,
        memory_revision INTEGER NOT NULL CHECK(memory_revision >= 1),
        assertion_sha256 TEXT NOT NULL CHECK(length(assertion_sha256) = 64),
        activated_at_ms INTEGER NOT NULL CHECK(activated_at_ms >= 0),
        PRIMARY KEY(life_id, principal_ref, claim_key, layer)
    ) STRICT""",
    """CREATE INDEX memory_active_heads_principal_idx
    ON memory_active_heads(life_id, principal_ref)""",
    """CREATE TABLE memory_consumer_offsets (
        consumer_id TEXT NOT NULL,
        life_id TEXT NOT NULL,
        last_change_seq INTEGER NOT NULL CHECK(last_change_seq >= 0),
        updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
        PRIMARY KEY(consumer_id, life_id)
    ) STRICT""",
)
_P14_MEMORY_DERIVATION_SQL = ";\n".join(
    statement.strip() for statement in _P14_MEMORY_DERIVATION_STATEMENTS
) + ";\n"
_P14_MEMORY_DERIVATION_SHA256 = hashlib.sha256(
    _P14_MEMORY_DERIVATION_SQL.encode("utf-8")
).hexdigest()
_P15_MEMORY_INVALIDATION_MIGRATION_ID = "p15-memory-invalidation-records"
_P15_MEMORY_INVALIDATION_STATEMENTS = (
    """CREATE TABLE memory_derivation_invalidations (
        invalidation_id TEXT PRIMARY KEY CHECK(invalidation_id LIKE 'miv_%'),
        life_id TEXT NOT NULL,
        principal_ref TEXT NOT NULL,
        derivation_id TEXT NOT NULL
            REFERENCES memory_derivations(derivation_id) ON DELETE RESTRICT,
        memory_id TEXT NOT NULL,
        memory_revision INTEGER NOT NULL CHECK(memory_revision >= 1),
        assertion_sha256 TEXT NOT NULL CHECK(length(assertion_sha256) = 64),
        reason TEXT NOT NULL CHECK(reason IN (
            'corrected','superseded','stale','privacy_erasure','invalidated'
        )),
        source_trigger_ref TEXT,
        invalidated_at_ms INTEGER NOT NULL CHECK(invalidated_at_ms >= 0),
        descendant_derivation_ids_json TEXT NOT NULL
            CHECK(json_valid(descendant_derivation_ids_json)),
        invalidation_sha256 TEXT NOT NULL UNIQUE CHECK(length(invalidation_sha256) = 64),
        payload BLOB NOT NULL
    ) STRICT""",
    """CREATE INDEX memory_derivation_invalidations_derivation_idx
    ON memory_derivation_invalidations(derivation_id)""",
    """CREATE INDEX memory_derivation_invalidations_life_idx
    ON memory_derivation_invalidations(life_id, invalidated_at_ms)""",
)
_P15_MEMORY_INVALIDATION_SQL = ";\n".join(
    statement.strip() for statement in _P15_MEMORY_INVALIDATION_STATEMENTS
) + ";\n"
_P15_MEMORY_INVALIDATION_SHA256 = hashlib.sha256(
    _P15_MEMORY_INVALIDATION_SQL.encode("utf-8")
).hexdigest()
_P16_TEMPERAMENT_RECEIPT_MIGRATION_ID = "p15-temperament-adaptation-receipts"
_P16_TEMPERAMENT_RECEIPT_STATEMENTS = (
    """CREATE TABLE temperament_adaptation_receipts (
        life_id TEXT NOT NULL,
        derivation_id TEXT NOT NULL
            REFERENCES memory_derivations(derivation_id) ON DELETE RESTRICT,
        trait_delta_sha256 TEXT NOT NULL CHECK(length(trait_delta_sha256) = 64),
        adapted_at_ms INTEGER NOT NULL CHECK(adapted_at_ms >= 0),
        receipt_sha256 TEXT NOT NULL UNIQUE CHECK(length(receipt_sha256) = 64),
        payload BLOB NOT NULL,
        PRIMARY KEY(life_id, derivation_id)
    ) STRICT""",
    """CREATE INDEX temperament_adaptation_receipts_life_idx
    ON temperament_adaptation_receipts(life_id, adapted_at_ms)""",
)
_P16_TEMPERAMENT_RECEIPT_SQL = ";\n".join(
    statement.strip() for statement in _P16_TEMPERAMENT_RECEIPT_STATEMENTS
) + ";\n"
_P16_TEMPERAMENT_RECEIPT_SHA256 = hashlib.sha256(
    _P16_TEMPERAMENT_RECEIPT_SQL.encode("utf-8")
).hexdigest()
_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID = "p15-memory-world-candidate-outbox"
_P17_MEMORY_WORLD_CANDIDATE_STATEMENTS = (
    """CREATE TABLE memory_world_candidate_outbox (
        candidate_id TEXT PRIMARY KEY CHECK(candidate_id LIKE 'wmc_%'),
        life_id TEXT NOT NULL,
        derivation_id TEXT NOT NULL
            REFERENCES memory_derivations(derivation_id) ON DELETE RESTRICT,
        candidate_sha256 TEXT NOT NULL UNIQUE CHECK(length(candidate_sha256) = 64),
        status TEXT NOT NULL CHECK(status IN ('pending','delivered','failed')),
        receipt_id TEXT,
        receipt_sha256 TEXT CHECK(receipt_sha256 IS NULL OR length(receipt_sha256) = 64),
        delivered_at_ms INTEGER,
        enqueued_at_ms INTEGER NOT NULL CHECK(enqueued_at_ms >= 0),
        payload BLOB NOT NULL,
        CHECK((receipt_id IS NULL) = (receipt_sha256 IS NULL)),
        CHECK((receipt_id IS NULL) = (delivered_at_ms IS NULL))
    ) STRICT""",
    """CREATE INDEX memory_world_candidate_outbox_status_idx
    ON memory_world_candidate_outbox(status, life_id, enqueued_at_ms)""",
)
_P17_MEMORY_WORLD_CANDIDATE_SQL = ";\n".join(
    statement.strip()
    for statement in _P17_MEMORY_WORLD_CANDIDATE_STATEMENTS
) + ";\n"
_P17_MEMORY_WORLD_CANDIDATE_SHA256 = hashlib.sha256(
    _P17_MEMORY_WORLD_CANDIDATE_SQL.encode("utf-8")
).hexdigest()
_SCHEMA_SQL = (
    _P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n"
    + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n"
    + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL
    + "\n" + _P14_MEMORY_DERIVATION_SQL + "\n" + _P15_MEMORY_INVALIDATION_SQL
    + "\n" + _P16_TEMPERAMENT_RECEIPT_SQL
    + "\n" + _P17_MEMORY_WORLD_CANDIDATE_SQL
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
        "memory_derivations",
        "memory_derivation_parents",
        "memory_derivation_invalidations",
        "memory_active_heads",
        "memory_consumer_offsets",
        "temperament_adaptation_receipts",
        "memory_world_candidate_outbox",
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


LifeStoreSchemaErrorFactory = Callable[[str], Exception]


def initialize_life_shadow_schema(connection: sqlite3.Connection, *, now_ms: int) -> None:
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
            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (14, ?, ?, ?)",
            (_P14_MEMORY_DERIVATION_MIGRATION_ID, _P14_MEMORY_DERIVATION_SHA256, now_ms),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (15, ?, ?, ?)",
            (_P15_MEMORY_INVALIDATION_MIGRATION_ID, _P15_MEMORY_INVALIDATION_SHA256, now_ms),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (16, ?, ?, ?)",
            (_P16_TEMPERAMENT_RECEIPT_MIGRATION_ID, _P16_TEMPERAMENT_RECEIPT_SHA256, now_ms),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (17, ?, ?, ?)",
            (_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256, now_ms),
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


def migrate_life_shadow_schema(
    connection: sqlite3.Connection,
    *,
    now_ms: int,
    error_factory: LifeStoreSchemaErrorFactory,
) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if application_id != SHADOW_STORE_APPLICATION_ID:
        raise error_factory("shadow store application identity is invalid")
    if user_version == SHADOW_STORE_SCHEMA_VERSION:
        return
    if user_version not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}:
        raise error_factory("shadow store schema version cannot be migrated")
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
    p14_tables = {
        "memory_derivations",
        "memory_derivation_parents",
        "memory_active_heads",
        "memory_consumer_offsets",
    }
    p15_tables = {"memory_derivation_invalidations"}
    p16_tables = {"temperament_adaptation_receipts"}
    p17_tables = {"memory_world_candidate_outbox"}
    p2_tables = {"life_ingress_dedupe", "life_ingress_receipts"}
    expected_tables = set(_EXPECTED_TABLES)
    if user_version < 17:
        expected_tables -= p17_tables
    if user_version < 16:
        expected_tables -= p16_tables
    if user_version < 15:
        expected_tables -= p15_tables
    if user_version < 14:
        expected_tables -= p14_tables
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
        raise error_factory(
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
    if user_version >= 14:
        expected_migrations.append((14, _P14_MEMORY_DERIVATION_MIGRATION_ID, _P14_MEMORY_DERIVATION_SHA256))
        expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL + "\n" + _P14_MEMORY_DERIVATION_SQL).encode("utf-8")).hexdigest()
    if user_version >= 15:
        expected_migrations.append((15, _P15_MEMORY_INVALIDATION_MIGRATION_ID, _P15_MEMORY_INVALIDATION_SHA256))
        expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL + "\n" + _P14_MEMORY_DERIVATION_SQL + "\n" + _P15_MEMORY_INVALIDATION_SQL).encode("utf-8")).hexdigest()
    if user_version >= 16:
        expected_migrations.append((16, _P16_TEMPERAMENT_RECEIPT_MIGRATION_ID, _P16_TEMPERAMENT_RECEIPT_SHA256))
        expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL + "\n" + _P14_MEMORY_DERIVATION_SQL + "\n" + _P15_MEMORY_INVALIDATION_SQL + "\n" + _P16_TEMPERAMENT_RECEIPT_SQL).encode("utf-8")).hexdigest()
    if user_version >= 17:
        expected_migrations.append((17, _P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256))
        expected_schema_sha256 = hashlib.sha256((_P7_SCHEMA_SQL + "\n" + _P8_MEMORY_CHANGE_SQL + "\n" + _P9_V21_LIFE_BINDING_SQL + "\n" + _P10_V21_CAUSAL_CHILD_SQL + "\n" + _P11_V21_COGNITION_SHADOW_SQL + "\n" + _P12_V21_LIFE_TURN_COMMIT_SQL + "\n" + _P13_V21_CAPABILITY_LIFECYCLE_SQL + "\n" + _P14_MEMORY_DERIVATION_SQL + "\n" + _P15_MEMORY_INVALIDATION_SQL + "\n" + _P16_TEMPERAMENT_RECEIPT_SQL + "\n" + _P17_MEMORY_WORLD_CANDIDATE_SQL).encode("utf-8")).hexdigest()
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
        raise error_factory(
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
        if user_version < 14:
            for statement in _P14_MEMORY_DERIVATION_STATEMENTS:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (14, ?, ?, ?)", (_P14_MEMORY_DERIVATION_MIGRATION_ID, _P14_MEMORY_DERIVATION_SHA256, now_ms))
        if user_version < 15:
            for statement in _P15_MEMORY_INVALIDATION_STATEMENTS:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (15, ?, ?, ?)", (_P15_MEMORY_INVALIDATION_MIGRATION_ID, _P15_MEMORY_INVALIDATION_SHA256, now_ms))
        if user_version < 16:
            for statement in _P16_TEMPERAMENT_RECEIPT_STATEMENTS:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (16, ?, ?, ?)", (_P16_TEMPERAMENT_RECEIPT_MIGRATION_ID, _P16_TEMPERAMENT_RECEIPT_SHA256, now_ms))
        if user_version < 17:
            for statement in _P17_MEMORY_WORLD_CANDIDATE_STATEMENTS:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations(version, migration_id, sql_sha256, applied_at_ms) VALUES (17, ?, ?, ?)", (_P17_MEMORY_WORLD_CANDIDATE_MIGRATION_ID, _P17_MEMORY_WORLD_CANDIDATE_SHA256, now_ms))
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


__all__ = [
    "LifeStoreSchemaErrorFactory",
    "SHADOW_STORE_APPLICATION_ID",
    "SHADOW_STORE_SCHEMA_VERSION",
    "initialize_life_shadow_schema",
    "migrate_life_shadow_schema",
]
