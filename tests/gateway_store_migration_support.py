"""Exact historical-schema reconstruction helpers for migration tests."""

from __future__ import annotations

import sqlite3

from total_gateway import store as store_module


def downgrade_v12_to_v11(connection: sqlite3.Connection) -> None:
    """Undo current additive layers through the exact v12 fixture, then rebuild v11.

    Migration tests start from the current schema so they can preserve selected
    rows, then reconstruct an older on-disk version.  Because v12 rebuilds the
    outbox constraint, merely dropping its new tables would leave a false v11
    schema.  This helper first removes all post-v12 additive/rebuild layers,
    including the P18-M2 v21 regenerative-execution layer, then restores the
    exact v11 DDL before an individual test removes any still-older layers.
    """

    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == 25:
        # v25 additive: P19-R2 M3.1 observation binding table.
        connection.execute("DROP TABLE repository_observation_binding")
        connection.execute("DELETE FROM schema_migrations WHERE version = 25")
        connection.execute("PRAGMA user_version = 24")
        version = 24
    if version == 24:
        # v24 additive: P19-R2 M3 evidence tables.
        connection.execute("DROP TABLE write_evidence_v2")
        connection.execute("DROP TABLE repository_observation")
        connection.execute("DELETE FROM schema_migrations WHERE version = 24")
        connection.execute("PRAGMA user_version = 23")
        version = 23
    if version == 23:
        # v23 additive: P19-R2 verification plane.  Drop indexes with
        # their table order (indexes vanish with tables in SQLite, but
        # keep the explicit order deterministic), then step back to v22.
        connection.execute("DROP TABLE verification_record")
        connection.execute("DROP TABLE verification_registry_snapshot")
        connection.execute("DELETE FROM schema_migrations WHERE version = 23")
        connection.execute("PRAGMA user_version = 22")
        version = 22
    if version == 22:
        # v22 additive: dispatch-permit release accounting for action fence
        # inflight.  Purely additive, drop the table and step back to v21.
        connection.execute("DROP TABLE dispatch_permit_release")
        connection.execute("DELETE FROM schema_migrations WHERE version = 22")
        connection.execute("PRAGMA user_version = 21")
        version = 21
    if version == 21:
        # v21 is additive inside the existing GatewayStateStore.  Drop the
        # checkpoint head before its referenced checkpoint table, then remove
        # Frontier/Ledger/immutable execution-contract state.  The indexes on
        # execution_ledger/regenerative_checkpoint disappear with their tables.
        connection.execute("DROP TABLE regenerative_checkpoint_head")
        connection.execute("DROP TABLE regenerative_checkpoint")
        connection.execute("DROP TABLE execution_frontier")
        connection.execute("DROP TABLE execution_ledger")
        connection.execute("DROP TABLE execution_ledger_head")
        connection.execute("DROP TABLE execution_task_contract")
        connection.execute("DELETE FROM schema_migrations WHERE version = 21")
        connection.execute("PRAGMA user_version = 20")
        version = 20
    if version == 20:
        # v20 additive: remove the v2.1 life proposal registration layer.
        connection.execute("DROP TABLE life_proposal_registration")
        connection.execute("DROP TABLE effect_reconciliation")
        connection.execute("DROP TABLE composite_execution_outcome")
        connection.execute("DELETE FROM schema_migrations WHERE version = 20")
        connection.execute("PRAGMA user_version = 19")
        version = 19
    if version == 19:
        # v19 additive: remove the v2.1 model response saga layer before the
        # existing v18 fixture path.
        connection.execute("DROP TABLE effect_outcome_head")
        connection.execute("DROP TABLE system_status")
        connection.execute("DROP TABLE assistant_commit")
        connection.execute("DROP TABLE model_attempt_plan_outcome")
        connection.execute("DROP TABLE model_attempt_result")
        connection.execute("DROP TABLE model_dispatch_marker")
        connection.execute("DROP TABLE model_attempt_plan")
        connection.execute("DELETE FROM schema_migrations WHERE version = 19")
        connection.execute("PRAGMA user_version = 18")
        version = 18
    if version == 18:
        # v18 is additive: remove only the v2.1 gate-promotion receipt and
        # singleton CAS head before continuing the existing v17 fixture path.
        connection.execute("DROP TABLE gate_promotion_head")
        connection.execute("DROP TABLE gate_promotion")
        connection.execute("DELETE FROM schema_migrations WHERE version = 18")
        connection.execute("PRAGMA user_version = 17")
        version = 17
    if version == 17:
        connection.execute("DROP TABLE execution_contract_epoch")
        connection.execute("DELETE FROM schema_migrations WHERE version = 17")
        connection.execute("PRAGMA user_version = 16")
        version = 16
    if version == 16:
        # v16 逆向：nonce 表重建层按 v5 原 DDL恢复，clarification 表整层移除
        connection.execute("DROP TABLE clarification_questions")
        connection.execute("ALTER TABLE security_nonce_ledger RENAME TO security_nonce_ledger_v16_old")
        connection.execute(store_module._MIGRATION_V5_STATEMENTS[0])  # noqa: SLF001
        connection.execute(
            "INSERT INTO security_nonce_ledger "
            "SELECT issuer, audience, purpose, nonce, payload_sha256, gateway_epoch,"
            "       consumer_instance_id, consumed_at_ms, expires_at_ms FROM security_nonce_ledger_v16_old"
        )
        connection.execute("DROP TABLE security_nonce_ledger_v16_old")
        connection.execute(store_module._MIGRATION_V5_STATEMENTS[1])  # noqa: SLF001
        connection.execute("DELETE FROM schema_migrations WHERE version = 16")
        connection.execute("PRAGMA user_version = 15")
        version = 15
    if version == 15:
        connection.execute("DROP TABLE confirmation_retirement")
        connection.execute("DELETE FROM schema_migrations WHERE version = 15")
        connection.execute("PRAGMA user_version = 14")
        version = 14
    if version == 14:
        # v14 事实链层逆向：attempt/fact/action_fence 均为新增表；存量 effect_ledger
        # 行已合成为 attempt 1 + CLAIM/RECEIPT 锚点事实，逆向时直接整层移除
        # （迁移测试只关心更老 schema 的行保留语义，v14 合成事实可重建）。
        connection.execute("DROP TABLE effect_facts")
        connection.execute("DROP TABLE effect_attempts")
        connection.execute("DROP TABLE action_fence")
        connection.execute("DELETE FROM schema_migrations WHERE version = 14")
        connection.execute("PRAGMA user_version = 13")
        version = 13
    if version == 13:
        connection.execute("DROP TABLE skill_activation_tickets")
        connection.execute("DROP TABLE skill_activations")
        connection.execute("DROP TABLE skill_selections")
        connection.execute("DELETE FROM schema_migrations WHERE version = 13")
        connection.execute("PRAGMA user_version = 12")
        version = 12
    if version != 12:
        raise AssertionError(f"expected a v12 fixture, got v{version}")
    unsupported = int(
        connection.execute(
            "SELECT count(*) FROM outbox WHERE intent_kind = 'LIFE_EVENT'"
        ).fetchone()[0]
    )
    if unsupported:
        raise AssertionError("a v12 LIFE_EVENT cannot be represented by the v11 fixture")

    connection.execute("DROP TABLE object_owners")
    connection.execute("DROP TABLE request_capsules")
    connection.execute("DROP TABLE completion_decisions")

    connection.execute("DROP INDEX outbox_dispatch_ready")
    connection.execute("DROP INDEX outbox_dispatch_boundary_started")
    connection.execute("ALTER TABLE event_outbox RENAME TO event_outbox_v12_old")
    connection.execute(
        "ALTER TABLE outbox_dispatch_boundary RENAME TO outbox_dispatch_boundary_v12_old"
    )
    connection.execute("ALTER TABLE outbox RENAME TO outbox_v12_old")

    connection.execute(store_module._MIGRATION_V3_STATEMENTS[0])  # noqa: SLF001
    connection.execute("INSERT INTO outbox SELECT * FROM outbox_v12_old")
    connection.execute(store_module._MIGRATION_V3_STATEMENTS[1])  # noqa: SLF001
    connection.execute("INSERT INTO event_outbox SELECT * FROM event_outbox_v12_old")
    connection.execute(store_module._MIGRATION_V11_STATEMENTS[0])  # noqa: SLF001
    connection.execute(
        "INSERT INTO outbox_dispatch_boundary SELECT * FROM outbox_dispatch_boundary_v12_old"
    )

    connection.execute("DROP TABLE event_outbox_v12_old")
    connection.execute("DROP TABLE outbox_dispatch_boundary_v12_old")
    connection.execute("DROP TABLE outbox_v12_old")
    connection.execute(store_module._MIGRATION_V3_STATEMENTS[2])  # noqa: SLF001
    connection.execute(store_module._MIGRATION_V11_STATEMENTS[1])  # noqa: SLF001
    connection.execute("DELETE FROM schema_migrations WHERE version = 12")
    connection.execute("PRAGMA user_version = 11")


__all__ = ["downgrade_v12_to_v11"]
