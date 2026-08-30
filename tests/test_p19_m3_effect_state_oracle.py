"""P19-R2 M3 EffectStateOracle tests — review completion gate.

Covers: effect not found / corrupt ledger row (tamper); SUCCEEDED with
and without v2 evidence; target missing / sha mismatch; real pre/post
change; AMBIGUOUS / FAILED_FINAL; lineage always from the ledger claim;
full chain Oracle -> Recorder -> Store; RECORD only with zero state
impact.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import (
    derive_effect_identity,
    derive_request_identity,
    derive_run_identity,
)
from contracts.verification import AcceptancePredicate
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.outcome_oracles.effect_state import EffectStateOracle
from total_gateway.store import GatewayStateStore
from total_gateway.verification_registry import VerifierRegistry
from total_gateway.verification_recording import VerificationRecorder

from tests.test_p19_m3_write_evidence_v2 import (
    HASH_B,
    authoritative_v1,
    bind_write_evidence_v2,
)


class EffectOracleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=900)
        # A REAL request whose lineage the effect claim will carry.
        from tests.test_p19_m3_write_evidence_v2 import WriteEvidenceV2StoreTests  # noqa: F401

        self._seed_request()
        self.snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        self.oracle = EffectStateOracle(snapshot=self.snapshot, store=self.store)
        self._effect_counter = 0

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _seed_request(self) -> None:
        from contracts import (
            InboundEnvelope,
            InboundScope,
            derive_inbound_scope_keys,
        )

        scope = InboundScope(
            channel="desktop",
            tenant_id="tenant_m3e",
            link_account_id="desktop_m3e",
            conversation_ref="conversation_m3e",
            channel_message_ref="message_m3e",
            sender_ref="sender_m3e",
        )
        keys = derive_inbound_scope_keys(scope)
        envelope = InboundEnvelope(
            inbound_id="inbound_m3e",
            channel=scope.channel,
            tenant_id=scope.tenant_id,
            link_account_id=scope.link_account_id,
            conversation_ref=scope.conversation_ref,
            conversation_scope_hash=keys.conversation_scope_hash,
            principal_scope_hash=keys.principal_scope_hash,
            message_scope_hash=keys.message_scope_hash,
            channel_message_ref=scope.channel_message_ref,
            sender_ref=scope.sender_ref,
            received_at_ms=1_000,
            idempotency_key="7" * 64,
            channel_metadata_hash=HASH_B,
            text="write and verify the file",
        )
        registration = self.store.register_request(
            envelope, ingress_sha256=HASH_B, created_at_ms=1_100
        )
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=2,
            gateway_epoch=1,
            lease_id="lease_m3e",
            owner_instance_id="gateway_m3e",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )

    def _new_effect(self, *, status: str = "SUCCEEDED", error_code: str | None = None):
        """Claim + complete a fresh execution effect in the ledger."""
        self._effect_counter += 1
        identity = derive_effect_identity(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=self._effect_counter - 1,
            intent_sha256="6" * 64,
        )
        claim = EffectClaim(
            effect_id=identity.effect_id,
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=identity.ordinal,
            intent_sha256="6" * 64,
            owner_component_id="tiangong-backend",
            claimed_at_ms=20_000,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=20_100)
        result = EffectResult(
            result_id=f"result_m3_{self._effect_counter}",
            effect_id=identity.effect_id,
            status=status,
            fact_id=f"fact_m3_{self._effect_counter}",
            result_object_id=None,
            result_object_sha256=None,
            evidence_sha256="e" * 64,
            error_code=error_code,
            observed_at_ms=20_500,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.complete_effect(result)
        return claim

    def _attach_evidence(self, claim, **v1_overrides):
        v1 = authoritative_v1(**v1_overrides)
        payload = bind_write_evidence_v2(
            v1,
            request_id=claim.request_id,
            run_id=claim.run_id,
            generation=claim.generation,
            effect_id=claim.effect_id,
            tool_name="write_file",
            action="write",
            observed_at_ms=20_600,
        )
        self.store.put_write_evidence_v2(payload, recorded_at_ms=20_700)
        return payload

    def _evaluate(self, effect_id, predicate):
        return self.oracle.evaluate(
            effect_id, predicate, evaluated_at_ms=21_000
        )

    @staticmethod
    def _predicate(kind, **params):
        return AcceptancePredicate.create(
            predicate_type=kind, subject_kind="effect", params=params or None
        )


class EffectAuthorityTests(EffectOracleTestBase):
    def test_unknown_effect_is_error(self) -> None:
        record = self._evaluate(
            "eff_" + "9" * 64, self._predicate("effect.terminal_succeeded")
        )
        self.assertEqual(record.status, "ERROR")
        self.assertIn("authority:effect_not_found", record.reason_codes)

    def test_corrupt_ledger_row_is_error(self) -> None:
        claim = self._new_effect()
        connection = sqlite3.connect(self.store.path)
        try:
            row = connection.execute(
                "SELECT result_json FROM effect_ledger WHERE effect_id = ?",
                (claim.effect_id,),
            ).fetchone()
            import json as _json

            payload = _json.loads(row[0])
            payload["status"] = "FAILED_FINAL"  # state/result tamper
            connection.execute(
                "UPDATE effect_ledger SET result_json = ? WHERE effect_id = ?",
                (_json.dumps(payload), claim.effect_id),
            )
            connection.commit()
        finally:
            connection.close()
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.terminal_succeeded")
        )
        self.assertEqual(record.status, "ERROR")
        self.assertTrue(any("authority" in code for code in record.reason_codes))


class TerminalStateTests(EffectOracleTestBase):
    def test_succeeded_terminal_passes(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(claim)
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.terminal_succeeded")
        )
        self.assertEqual(record.status, "PASS")

    def test_failed_final_is_proven_fail(self) -> None:
        claim = self._new_effect(status="FAILED_FINAL", error_code="tool.failed")
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.terminal_succeeded")
        )
        self.assertEqual(record.status, "FAIL")

    def test_ambiguous_never_passes(self) -> None:
        claim = self._new_effect(status="AMBIGUOUS", error_code="outcome.unknown")
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.terminal_succeeded")
        )
        self.assertNotEqual(record.status, "PASS")
        self.assertEqual(record.status, "INCONCLUSIVE")


class TargetPredicateTests(EffectOracleTestBase):
    def test_succeeded_without_evidence_is_inconclusive(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.target_exists", target_path="a.txt")
        )
        self.assertEqual(record.status, "INCONCLUSIVE")
        self.assertIn("effect.write_evidence_missing", record.reason_codes)

    def test_broker_only_evidence_cannot_verify_target(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(claim, source="sandbox_broker", post_rows=[])
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.target_exists", target_path="a.txt")
        )
        self.assertEqual(record.status, "INCONCLUSIVE")
        self.assertIn("effect.target_state_unverified", record.reason_codes)

    def test_succeeded_but_target_missing_is_fail(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(
            claim,
            post_rows=[
                {
                    "path": "a.txt",
                    "exists": False,
                    "is_file": False,
                    "is_dir": False,
                    "size_bytes": 0,
                    "sha256": "",
                }
            ],
        )
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.target_exists", target_path="a.txt")
        )
        self.assertEqual(record.status, "FAIL")
        self.assertIn("effect.target_missing", record.reason_codes)

    def test_succeeded_but_sha_wrong_is_fail(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(claim)  # post sha = f*64
        record = self._evaluate(
            claim.effect_id,
            self._predicate(
                "effect.target_sha256_matches", target_path="a.txt", sha256="9" * 64
            ),
        )
        self.assertEqual(record.status, "FAIL")
        self.assertIn("effect.target_sha256_mismatch", record.reason_codes)
        matching = self._evaluate(
            claim.effect_id,
            self._predicate(
                "effect.target_sha256_matches", target_path="a.txt", sha256="f" * 64
            ),
        )
        self.assertEqual(matching.status, "PASS")

    def test_real_pre_post_change_passes_required_change(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(claim, changed=["a.txt", "b/c.txt"])
        record = self._evaluate(
            claim.effect_id,
            self._predicate(
                "effect.required_change_observed", target_paths=["a.txt", "b/c.txt"]
            ),
        )
        self.assertEqual(record.status, "PASS")
        missing = self._evaluate(
            claim.effect_id,
            self._predicate(
                "effect.required_change_observed", target_paths=["a.txt", "zzz.txt"]
            ),
        )
        self.assertEqual(missing.status, "INCONCLUSIVE")

    def test_required_change_proven_absent_when_deleted(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(
            claim, changed=[], deleted=["zzz.txt"], post_rows=[]
        )
        record = self._evaluate(
            claim.effect_id,
            self._predicate("effect.required_change_observed", target_paths=["zzz.txt"]),
        )
        self.assertEqual(record.status, "FAIL")


class LineageAndPersistenceTests(EffectOracleTestBase):
    def test_lineage_comes_from_ledger_claim(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(claim)
        record = self._evaluate(
            claim.effect_id, self._predicate("effect.terminal_succeeded")
        )
        self.assertEqual(record.request_id, claim.request_id)
        self.assertEqual(record.run_id, claim.run_id)
        self.assertEqual(record.generation, claim.generation)
        self.assertEqual(record.subject_identity, claim.effect_id)
        self.assertTrue(record.has_valid_identity())

    def test_full_chain_oracle_recorder_store_record_only(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        self._attach_evidence(claim, changed=["a.txt"])
        record = self._evaluate(
            claim.effect_id,
            self._predicate("effect.required_change_observed", target_paths=["a.txt"]),
        )
        self.assertEqual(record.status, "PASS")
        self.store.put_registry_snapshot(self.snapshot, recorded_at_ms=1_500)
        recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)
        outcome = recorder.record(record, recorded_at_ms=2_000)
        self.assertTrue(outcome.created_by_this_call)

        connection = sqlite3.connect(self.store.path)
        try:
            decisions = connection.execute(
                "SELECT COUNT(*) FROM completion_decisions"
            ).fetchone()[0]
            aggregates = connection.execute(
                "SELECT COUNT(*) FROM aggregate_state"
            ).fetchone()[0]
            outbox_rows = connection.execute(
                "SELECT COUNT(*) FROM outbox"
            ).fetchone()[0]
            enforcements = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT enforcement FROM verification_record"
                )
            }
        finally:
            connection.close()
        self.assertEqual(decisions, 0)
        self.assertEqual(aggregates, 0)
        self.assertEqual(outbox_rows, 0)
        self.assertEqual(enforcements, {"RECORD"})

    def test_records_reference_write_evidence_digest(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        evidence = self._attach_evidence(claim, changed=["a.txt"])
        record = self._evaluate(
            claim.effect_id,
            self._predicate("effect.required_change_observed", target_paths=["a.txt"]),
        )
        self.assertIn(
            f"write_evidence:{evidence['evidence_sha256']}",
            record.evidence_refs,
        )
        # The evidence itself is retrievable from the authoritative store
        # by that exact reference — records are re-verifiable, not just
        # digest mentions.
        digest = evidence["evidence_sha256"]
        fetched = self.store.get_write_evidence_v2(digest)
        assert fetched is not None
        self.assertEqual(fetched["evidence_sha256"], digest)


if __name__ == "__main__":
    unittest.main()
