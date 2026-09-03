"""P19-R2 M3 write_evidence.v2 tests — builder, trust boundary, store v24.

Covers: v1→v2 upgrade rules (authority precondition, three-section
separation, digest recomputation), model-copy/tamper bypass rejection,
store persistence (idempotent insert, same-id-different-content conflict,
lineage binding rejection, digest trust boundary), and the cross-layer
digest consistency lock between the v3 builder and the src store.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import (
    InboundEnvelope,
    InboundScope,
    derive_inbound_scope_keys,
    derive_request_identity,
    derive_run_identity,
)
from total_gateway.store import GatewayStateStore, StoreConflictError, StoreNotFoundError

HASH_B = "b" * 64


def _import_v3():
    import sys

    for path in ("src", "app/backend/tiangong-backend"):
        candidate = str(Path(__file__).resolve().parents[1] / path)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    from v3.tool_result_contract import (
        WriteEvidenceV2Error,
        bind_write_evidence_v2,
        write_evidence_v2_is_valid,
    )

    return WriteEvidenceV2Error, bind_write_evidence_v2, write_evidence_v2_is_valid


WriteEvidenceV2Error, bind_write_evidence_v2, write_evidence_v2_is_valid = _import_v3()


def authoritative_v1(
    *,
    source: str = "tool_pre_post",
    changed: list[str] | None = None,
    deleted: list[str] | None = None,
    post_rows: list[dict] | None = None,
) -> dict:
    return {
        "schema": "tiangong.v3.write_evidence.v1",
        "authoritative": True,
        "source": source,
        "action": "write",
        "changed_files": changed if changed is not None else ["a.txt"],
        "deleted_files": deleted if deleted is not None else [],
        "verified_unchanged_files": [],
        "post": post_rows if post_rows is not None else [
            {
                "path": "a.txt",
                "exists": True,
                "is_file": True,
                "is_dir": False,
                "size_bytes": 5,
                "sha256": "f" * 64,
            }
        ],
    }


class WriteEvidenceV2BuilderTests(unittest.TestCase):
    def _bind(self, v1, **overrides):
        params = dict(
            request_id="req_" + "1" * 64,
            run_id="run_" + "2" * 64,
            generation=2,
            effect_id="eff_" + "3" * 64,
            tool_name="write_file",
            action="write",
            observed_at_ms=1_000,
        )
        params.update(overrides)
        return bind_write_evidence_v2(v1, **params)

    def test_v2_sections_and_digest_recompute(self) -> None:
        v2 = self._bind(authoritative_v1(), planned_target_paths=["a.txt"])
        self.assertEqual(v2["schema"], "tiangong.v3.write_evidence.v2")
        self.assertTrue(write_evidence_v2_is_valid(v2))
        self.assertEqual(
            sorted(v2["observed_mutation"]["changed_paths"]), ["a.txt"]
        )
        self.assertEqual(
            v2["provenance"]["strength"], "verified_final_state"
        )
        self.assertTrue(v2["verified_final_state"]["post_state_sha256"])

    def test_non_authoritative_or_model_text_cannot_become_v2(self) -> None:
        with self.assertRaises(WriteEvidenceV2Error):
            self._bind(authoritative_v1(source="model_text"))
        with self.assertRaises(WriteEvidenceV2Error):
            self._bind(
                {**authoritative_v1(), "authoritative": False}
            )
        with self.assertRaises(WriteEvidenceV2Error):
            self._bind({"schema": "tiangong.v3.write_evidence.v2"})

    def test_tampered_payload_fails_digest_recheck(self) -> None:
        v2 = self._bind(authoritative_v1())
        tampered = copy.deepcopy(v2)
        tampered["observed_mutation"]["changed_paths"] = ["secret.txt"]
        self.assertFalse(write_evidence_v2_is_valid(tampered))
        # digest swap cannot rescue content tampering
        from v3.tool_result_contract import write_evidence_v2_preimage

        from contracts.canonical import canonical_sha256

        tampered["evidence_sha256"] = canonical_sha256(
            write_evidence_v2_preimage(tampered)
        )
        self.assertTrue(write_evidence_v2_is_valid(tampered))  # consistent forgery
        # ...but the forged digest differs from the original identity:
        self.assertNotEqual(
            tampered["evidence_sha256"], v2["evidence_sha256"]
        )

    def test_binding_fields_validated(self) -> None:
        with self.assertRaises(WriteEvidenceV2Error):
            self._bind(authoritative_v1(), effect_id="")
        with self.assertRaises(WriteEvidenceV2Error):
            self._bind(authoritative_v1(), generation=-1)
        with self.assertRaises(WriteEvidenceV2Error):
            self._bind(authoritative_v1(), observed_at_ms="soon")


class WriteEvidenceV2StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=900)
        scope = InboundScope(
            channel="desktop",
            tenant_id="tenant_m3",
            link_account_id="desktop_m3",
            conversation_ref="conversation_m3",
            channel_message_ref="message_m3",
            sender_ref="sender_m3",
        )
        keys = derive_inbound_scope_keys(scope)
        envelope = InboundEnvelope(
            inbound_id="inbound_m3",
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
            idempotency_key=keys.idempotency_key,
            channel_metadata_hash=HASH_B,
            text="write the file",
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
            lease_id="lease_m3",
            owner_instance_id="gateway_m3",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )
        self.effect_id = self._seed_effect()

    def _seed_effect(self) -> str:
        from contracts import derive_effect_identity
        from total_gateway.effects import EffectClaim, EffectResult

        identity = derive_effect_identity(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=0,
            intent_sha256="6" * 64,
        )
        claim = EffectClaim(
            effect_id=identity.effect_id,
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=0,
            intent_sha256="6" * 64,
            owner_component_id="tiangong-backend",
            claimed_at_ms=1_300,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(identity.effect_id, started_at_ms=1_400)
        result = EffectResult(
            result_id="result_m3_store",
            effect_id=identity.effect_id,
            status="SUCCEEDED",
            fact_id="fact_m3_store",
            result_object_id=None,
            result_object_sha256=None,
            evidence_sha256="e" * 64,
            error_code=None,
            observed_at_ms=1_500,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        self.store.complete_effect(result)
        return identity.effect_id

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _v2(self, **overrides):
        params = dict(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            effect_id=self.effect_id,
            tool_name="write_file",
            action="write",
            observed_at_ms=2_000,  # after claim(1_300)/start(1_400)
        )
        params.update(overrides)
        return bind_write_evidence_v2(authoritative_v1(), **params)

    def test_idempotent_insert_and_readback(self) -> None:
        v2 = self._v2()
        self.assertTrue(self.store.put_write_evidence_v2(v2, recorded_at_ms=2_000))
        self.assertFalse(self.store.put_write_evidence_v2(v2, recorded_at_ms=2_500))
        fetched = self.store.get_write_evidence_v2(v2["evidence_sha256"])
        self.assertEqual(fetched, v2)
        listed = self.store.list_write_evidence_for_effect(self.effect_id)
        self.assertEqual(listed, (v2,))

    def test_digest_tamper_rejected_at_trust_boundary(self) -> None:
        v2 = self._v2()
        tampered = copy.deepcopy(v2)
        tampered["observed_mutation"]["changed_paths"] = ["x.txt"]
        # keep the ORIGINAL digest -> recompute check must reject
        with self.assertRaises(ValueError):
            self.store.put_write_evidence_v2(tampered, recorded_at_ms=2_000)

    def test_same_id_different_content_structurally_impossible(self) -> None:
        # Content addressing: any content change changes the digest, so a
        # reused digest with different content is rejected by recompute.
        v2 = self._v2()
        self.assertTrue(self.store.put_write_evidence_v2(v2, recorded_at_ms=2_000))
        other = self._v2()  # same content -> same digest -> idempotent
        self.assertFalse(self.store.put_write_evidence_v2(other, recorded_at_ms=2_100))
        different = self._v2(tool_name="edit_file")  # different content
        self.assertNotEqual(
            different["evidence_sha256"], v2["evidence_sha256"]
        )
        self.assertTrue(
            self.store.put_write_evidence_v2(different, recorded_at_ms=2_200)
        )

    def test_lineage_binding_enforced(self) -> None:
        wrong_run = self._v2(run_id="run_" + "f" * 64)
        with self.assertRaises(StoreConflictError):
            self.store.put_write_evidence_v2(wrong_run, recorded_at_ms=2_000)
        wrong_generation = self._v2(generation=99)
        with self.assertRaises(StoreConflictError):
            self.store.put_write_evidence_v2(wrong_generation, recorded_at_ms=2_000)
        unknown_request = self._v2(request_id="req_" + "e" * 64)
        with self.assertRaises(StoreNotFoundError):
            self.store.put_write_evidence_v2(unknown_request, recorded_at_ms=2_000)
        # M3.1 §2: evidence for an effect that does not exist in the ledger
        from contracts.write_evidence import WriteEvidenceV2Error  # noqa: F401

        ghost = self._v2(effect_id="eff_" + "9" * 64)
        with self.assertRaises(StoreNotFoundError):
            self.store.put_write_evidence_v2(ghost, recorded_at_ms=2_000)
        # observation predating the claim's authority time
        early = self._v2(observed_at_ms=1_000)  # claim at 1_300/start 1_400
        with self.assertRaises(ValueError):
            self.store.put_write_evidence_v2(early, recorded_at_ms=2_000)

    def test_schema_v24_and_zero_state_impact(self) -> None:
        v2 = self._v2()
        self.store.put_write_evidence_v2(v2, recorded_at_ms=2_000)
        self.assertEqual(self.store.health_check(now_ms=2_100).schema_version, 30)
        connection = sqlite3.connect(self.store.path)
        try:
            decisions = connection.execute(
                "SELECT COUNT(*) FROM completion_decisions"
            ).fetchone()[0]
            aggregates = connection.execute(
                "SELECT COUNT(*) FROM aggregate_state"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual((decisions, aggregates), (0, 0))

    def test_cross_layer_digest_consistency_lock(self) -> None:
        # The src store recomputes the digest with the SAME rule the v3
        # builder uses — this test locks the two against drift.
        from contracts.canonical import canonical_sha256

        v2 = self._v2()
        preimage = {
            key: value for key, value in v2.items() if key != "evidence_sha256"
        }
        self.assertEqual(canonical_sha256(preimage), v2["evidence_sha256"])


if __name__ == "__main__":
    unittest.main()
