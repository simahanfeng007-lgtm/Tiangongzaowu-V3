"""P19-R2 M3.1 effect-side trust-boundary regressions — review §11.

Items 1/3/4/5/6/15: evidence rebinding, forged inner digests with a
recomputed total hash, smuggled post state, oracle readback lineage
tamper, and no fabricated records without trusted lineage.
"""

from __future__ import annotations

import copy
import sqlite3
import unittest

from contracts.canonical import canonical_sha256
from contracts.write_evidence import WriteEvidenceV2, WriteEvidenceV2Error
from total_gateway.outcome_oracles.effect_state import (
    EffectStateOracle,
    OracleInvocationError,
)
from total_gateway.store import GatewayStateStore, StoreConflictError

from tests.test_p19_m3_effect_state_oracle import EffectOracleTestBase
from tests.test_p19_m3_write_evidence_v2 import authoritative_v1, bind_write_evidence_v2


class EvidenceRebindingTests(EffectOracleTestBase):
    """§11 item 1/2: rebinding evidence across effects/lineages."""

    def _evidence_for(self, claim, **overrides):
        params = dict(
            request_id=claim.request_id,
            run_id=claim.run_id,
            generation=claim.generation,
            effect_id=claim.effect_id,
            tool_name="write_file",
            action="write",
            observed_at_ms=20_600,
        )
        params.update(overrides)
        return bind_write_evidence_v2(authoritative_v1(), **params)

    def test_item1_effect_a_evidence_rejected_for_effect_b(self) -> None:
        claim_a = self._new_effect(status="SUCCEEDED")
        claim_b = self._new_effect(status="SUCCEEDED")
        evidence_a = self._evidence_for(claim_a)
        self.store.put_write_evidence_v2(evidence_a, recorded_at_ms=20_700)
        # same lineage ids but effect swapped onto claim B's payload
        forged = copy.deepcopy(evidence_a)
        forged["effect_id"] = claim_b.effect_id
        pre = {k: v for k, v in forged.items() if k != "evidence_sha256"}
        forged["evidence_sha256"] = canonical_sha256(pre)
        with self.assertRaises(StoreConflictError):
            self.store.put_write_evidence_v2(forged, recorded_at_ms=20_800)

    def test_item2_effect_id_ok_but_lineage_differs_rejected(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        evidence = self._evidence_for(claim, run_id="run_" + "e" * 64)
        with self.assertRaises(Exception):
            self.store.put_write_evidence_v2(evidence, recorded_at_ms=20_700)


class ForgedDigestTests(unittest.TestCase):
    """§11 items 3/4/5: model_copy + recomputed TOTAL hash forgeries."""

    def _valid_payload(self) -> dict:
        mutation_digest = canonical_sha256(["a.txt"])
        empty = canonical_sha256([])
        post = canonical_sha256([["a.txt", True, "f" * 64, 5]])
        payload = {
            "schema": "tiangong.v3.write_evidence.v2",
            "request_id": "req_" + "1" * 64,
            "run_id": "run_" + "2" * 64,
            "generation": 2,
            "effect_id": "eff_" + "3" * 64,
            "tool_name": "write_file",
            "action": "write",
            "provenance": {
                "upgraded_from": "tiangong.v3.write_evidence.v1",
                "source": "tool_pre_post",
                "strength": "verified_final_state",
            },
            "planned": {"target_paths": ["a.txt"]},
            "observed_mutation": {
                "changed_paths": ["a.txt"],
                "deleted_paths": [],
                "verified_unchanged_paths": [],
                "changed_paths_digest": mutation_digest,
                "deleted_paths_digest": empty,
                "verified_unchanged_digest": empty,
            },
            "verified_final_state": {
                "post_rows": [
                    {
                        "path": "a.txt",
                        "exists": True,
                        "sha256": "f" * 64,
                        "size_bytes": 5,
                        "is_file": True,
                        "is_dir": False,
                    }
                ],
                "post_state_sha256": post,
            },
            "observed_at_ms": 1_000,
        }
        payload["evidence_sha256"] = canonical_sha256(payload)
        return payload

    def _retotal(self, payload: dict) -> dict:
        pre = {k: v for k, v in payload.items() if k != "evidence_sha256"}
        payload["evidence_sha256"] = canonical_sha256(pre)
        return payload

    def test_item3_wrong_inner_mutation_digest_rejected(self) -> None:
        forged = self._valid_payload()
        forged["observed_mutation"]["changed_paths_digest"] = canonical_sha256(["zzz"])
        forged = self._retotal(forged)
        with self.assertRaises(WriteEvidenceV2Error):
            WriteEvidenceV2.from_wire(forged)

    def test_item4_wrong_post_state_digest_rejected(self) -> None:
        forged = self._valid_payload()
        forged["verified_final_state"]["post_state_sha256"] = "9" * 64
        forged = self._retotal(forged)
        with self.assertRaises(WriteEvidenceV2Error):
            WriteEvidenceV2.from_wire(forged)

    def test_item5_mutation_only_smuggling_post_rows_rejected(self) -> None:
        forged = self._valid_payload()
        forged["provenance"]["source"] = "sandbox_broker"
        forged["provenance"]["strength"] = "observed_mutation_only"
        forged = self._retotal(forged)  # rows still present under mutation-only
        with self.assertRaises(WriteEvidenceV2Error):
            WriteEvidenceV2.from_wire(forged)


class ReadbackLineageTests(EffectOracleTestBase):
    """§11 item 6: oracle re-validates evidence lineage after readback."""

    def test_lineage_tamper_after_persist_is_error(self) -> None:
        claim = self._new_effect(status="SUCCEEDED")
        evidence = bind_write_evidence_v2(
            authoritative_v1(),
            request_id=claim.request_id,
            run_id=claim.run_id,
            generation=claim.generation,
            effect_id=claim.effect_id,
            tool_name="write_file",
            action="write",
            observed_at_ms=20_600,
        )
        self.store.put_write_evidence_v2(evidence, recorded_at_ms=20_700)
        # raw tamper: re-point the stored row at another effect's list entry
        import json as _json

        connection = sqlite3.connect(self.store.path)
        try:
            tampered = copy.deepcopy(evidence)
            tampered["request_id"] = "req_" + "e" * 64
            connection.execute(
                "UPDATE write_evidence_v2 SET evidence_json = ?"
                " WHERE evidence_sha256 = ?",
                (
                    _json.dumps(
                        tampered, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"),
                    ),
                    evidence["evidence_sha256"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        record = self._evaluate(
            claim.effect_id,
            __import__("contracts.verification", fromlist=["AcceptancePredicate"])
            .AcceptancePredicate.create(
                predicate_type="effect.terminal_succeeded", subject_kind="effect"
            ),
        )
        self.assertEqual(record.status, "ERROR")
        self.assertIn("authority:evidence_lineage_mismatch", record.reason_codes)

    def test_item15_no_fake_record_without_trusted_lineage(self) -> None:
        with self.assertRaises(OracleInvocationError):
            self.oracle.evaluate(
                "eff_" + "0" * 64,
                __import__("contracts.verification", fromlist=["AcceptancePredicate"])
                .AcceptancePredicate.create(
                    predicate_type="effect.terminal_succeeded", subject_kind="effect"
                ),
                evaluated_at_ms=21_000,
            )


if __name__ == "__main__":
    unittest.main()
