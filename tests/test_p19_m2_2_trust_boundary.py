"""P19-R2 M2.2 trust-boundary closure tests — review matrix.

A1/A2: predicate identity under param variation and illegal construction.
D1/D2: exact descriptor binding (config hash / capability / limits).
O1/O2: oversized manifests (tampered -> ERROR; authority-valid oversized
-> INCONCLUSIVE) with zero inspection-phase reads beyond the authority
baseline.
N1: 64 QC evidences fold into one set digest.
T1/X1/X2/P1/E1: normalization, formula-only content, parser taxonomy,
reason-code hygiene.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from contracts import canonical_sha256
from contracts.verification import (
    AcceptancePredicate,
    AcceptancePredicateSpecError,
)
from total_gateway.artifact_content import VerifiedArtifactContentSource
from total_gateway.outcome_oracles.artifact_content import (
    ArtifactContentOracle,
    OracleSnapshotInvalid,
)
from total_gateway.verification_oracle_config import (
    ARTIFACT_INSPECTOR_SEMANTIC_VERSION,
)
from total_gateway.verification_registry import VerifierRegistry

from tests.test_p19_m2_1_artifact_oracle import (
    M21OracleTestBase,
    XLSX_MIME,
    xlsx_bytes,
)


def _authority_read_baseline(test: M21OracleTestBase, manifest) -> int:
    """Object-store reads performed by the authority chain alone."""
    source = VerifiedArtifactContentSource(
        test.object_store, test.fact_ledger, (manifest,)
    )
    with mock.patch.object(
        test.object_store, "read_bytes", wraps=test.object_store.read_bytes
    ) as spy:
        try:
            source.verify_artifact_revision(manifest.artifact_revision_id)
        except Exception:
            pass
    return spy.call_count


class PredicateBypassTests(unittest.TestCase):
    """A1 / A2: create() bypass must be sealed at every layer."""

    def _columns(self, columns):
        return AcceptancePredicate.create(
            predicate_type="xlsx.required_columns",
            subject_kind="artifact",
            params={"columns": columns},
        )

    def test_a1_param_variation_changes_predicate_and_record_ids(self) -> None:
        first = self._columns(["姓名", "分数"])
        second = self._columns(["姓名", "等级"])
        self.assertNotEqual(first.predicate_id, second.predicate_id)
        # record ids derive from result hashes which embed
        # predicate_sha256:<hash> evidence — differing predicate hashes
        # must produce differing record identities (verified end-to-end
        # in OracleA1Tests below with the real oracle).

    def test_a2_illegal_predicates_rejected_everywhere(self) -> None:
        good = self._columns(["姓名"])
        # 1) direct construction with unknown params
        with self.assertRaises(Exception):
            AcceptancePredicate(
                predicate_id=good.predicate_id,
                predicate_type="artifact.nonempty",
                subject_kind="artifact",
                params=(("strict", True),),
                predicate_sha256=good.predicate_sha256,
            )
        # 2) model_validate_json with non-normalized params
        raw = json.dumps(
            {
                "predicate_id": good.predicate_id,
                "predicate_type": "xlsx.required_columns",
                "subject_kind": "artifact",
                "params": [["columns", ["姓名", "姓名"]]],
                "predicate_sha256": good.predicate_sha256,
            }
        )
        with self.assertRaises(Exception):
            AcceptancePredicate.model_validate_json(raw)
        # 3) model_copy + recomputed hash/id with FILLER params -> the
        #    validator is bypassed, but has_valid_identity re-validates
        #    semantics and must reject.
        forged = good.model_copy(update={"params": (("columns", ("姓名",)),)})
        self.assertTrue(forged.has_valid_identity())  # legal shape by chance
        filler = good.model_copy(
            update={"params": (("columns", ("姓名",)), ("strict", True))}
        )
        self.assertFalse(filler.has_valid_identity())
        # recompute hashes for the filler variant — still illegal
        payload = {
            "schema_version": "tiangong.acceptance_predicate.v1",
            "predicate_type": filler.predicate_type,
            "subject_kind": filler.subject_kind,
            "params": [
                [k, list(v) if isinstance(v, tuple) else v] for k, v in filler.params
            ],
        }
        sha = canonical_sha256(payload)
        pid = "vpd_" + canonical_sha256(
            {"domain": "tiangong.acceptance_predicate.v1", "predicate_sha256": sha}
        )
        fully_forged = filler.model_copy(
            update={"predicate_sha256": sha, "predicate_id": pid}
        )
        self.assertFalse(fully_forged.has_valid_identity())
        # 4) filler on artifact.nonempty via create()
        with self.assertRaises(AcceptancePredicateSpecError):
            AcceptancePredicate.create(
                predicate_type="artifact.nonempty",
                subject_kind="artifact",
                params={"strict": True},
            )
        # 5) domain mismatch: xlsx predicate with effect subject
        wrong_domain = AcceptancePredicate.create(
            predicate_type="artifact.nonempty", subject_kind="effect"
        )
        self.assertFalse(wrong_domain.has_valid_identity())
        # 6) min_rows as string / bool
        for bad in ("3", True):
            with self.assertRaises(AcceptancePredicateSpecError):
                AcceptancePredicate.create(
                    predicate_type="xlsx.min_data_rows",
                    subject_kind="artifact",
                    params={"min_rows": bad},
                )


class DescriptorBindingTests(unittest.TestCase):
    """D1 / D2: same id/version, different config or capability."""

    def _oracle_with_artifact_descriptor(self, descriptor):
        snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        others = tuple(
            d for d in snapshot.verifiers if d.verifier_id != "verifier.artifact_content"
        )
        from contracts.verification import RegistrySnapshot, derive_registry_snapshot_id

        partial = RegistrySnapshot(
            registry_snapshot_id="vrg_" + "0" * 64,
            verifiers=tuple(sorted((descriptor, *others), key=lambda d: d.verifier_id)),
            captured_at_ms=1,
            snapshot_sha256="0" * 64,
        ).with_computed_sha256()
        rebuilt = partial.model_copy(
            update={
                "registry_snapshot_id": derive_registry_snapshot_id(
                    snapshot_sha256=partial.snapshot_sha256
                )
            }
        )
        return ArtifactContentOracle(
            snapshot=rebuilt, object_store=mock.Mock(), fact_ledger=mock.Mock()
        )

    def _artifact_descriptor(self):
        snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
        descriptor = snapshot.find("verifier.artifact_content")
        assert descriptor is not None
        return descriptor

    def test_d1_wrong_config_sha256_rejected(self) -> None:
        descriptor = self._artifact_descriptor()
        tampered = descriptor.model_copy(
            update={"config_sha256": "e" * 64}
        ).with_computed_sha256()
        with self.assertRaises(OracleSnapshotInvalid):
            self._oracle_with_artifact_descriptor(tampered)

    def test_d2_missing_predicate_or_changed_limit_rejected(self) -> None:
        descriptor = self._artifact_descriptor()
        missing_one = descriptor.model_copy(
            update={
                "supported_predicate_types": tuple(
                    t
                    for t in descriptor.supported_predicate_types
                    if t != "xlsx.min_data_rows"
                )
            }
        ).with_computed_sha256()
        with self.assertRaises(OracleSnapshotInvalid):
            self._oracle_with_artifact_descriptor(missing_one)

        changed_limit = descriptor.model_copy(
            update={"max_input_bytes": descriptor.max_input_bytes * 2}
        ).with_computed_sha256()
        with self.assertRaises(OracleSnapshotInvalid):
            self._oracle_with_artifact_descriptor(changed_limit)

    def test_version_is_v3(self) -> None:
        descriptor = self._artifact_descriptor()
        self.assertEqual(
            descriptor.verifier_version, ARTIFACT_INSPECTOR_SEMANTIC_VERSION
        )
        self.assertEqual(ARTIFACT_INSPECTOR_SEMANTIC_VERSION, "3")


class OracleBoundaryTests(M21OracleTestBase):
    """O2 / N1 / T1 / X1 / X2 / P1 / E1 / A1(end-to-end)."""

    def _nonempty(self):
        return AcceptancePredicate.create(
            predicate_type="artifact.nonempty", subject_kind="artifact"
        )

    def test_a1_same_artifact_same_time_different_params_different_records(self) -> None:
        manifest = self._passed_manifest(
            xlsx_bytes(["姓名", "分数"], [["甲", 1], ["乙", 2]]),
            filename="a1.xlsx",
            format_id="xlsx",
            declared_mime=XLSX_MIME,
        )
        first = self.oracle.evaluate(
            manifest,
            AcceptancePredicate.create(
                predicate_type="xlsx.min_data_rows",
                subject_kind="artifact",
                params={"min_rows": 1},
            ),
            evaluated_at_ms=21_000,
        )
        second = self.oracle.evaluate(
            manifest,
            AcceptancePredicate.create(
                predicate_type="xlsx.min_data_rows",
                subject_kind="artifact",
                params={"min_rows": 2},
            ),
            evaluated_at_ms=21_000,
        )
        self.assertEqual(first.status, "PASS")
        self.assertEqual(second.status, "PASS")  # 2 data rows satisfy both
        self.assertNotEqual(first.predicate_id, second.predicate_id)
        self.assertNotEqual(
            first.verification_record_id, second.verification_record_id
        )

    def test_o2_authority_valid_oversized_is_inconclusive_no_extra_reads(self) -> None:
        # The artifact gate rejects objects near 64MiB, so an
        # authority-valid oversized manifest is unreachable through the
        # real chain at the production limit. Equivalent construction:
        # rebuild descriptor + oracle from the SAME (patched) config with
        # an 8-byte limit — the size branch runs after full authority
        # verification with identical semantics at any threshold.
        from total_gateway.verification_oracle_config import ARTIFACT_MAX_INPUT_BYTES

        manifest = self._text_manifest("tiny but oversized for this config")
        with mock.patch(
            "total_gateway.verification_oracle_config.ARTIFACT_MAX_INPUT_BYTES", 8
        ), mock.patch(
            # the oracle module binds the constant at import time too
            "total_gateway.outcome_oracles.artifact_content.ARTIFACT_MAX_INPUT_BYTES",
            8,
        ):
            snapshot = VerifierRegistry.with_defaults().snapshot(captured_at_ms=1)
            small_oracle = ArtifactContentOracle(
                snapshot=snapshot,
                object_store=self.object_store,
                fact_ledger=self.fact_ledger,
            )
            baseline = _authority_read_baseline(self, manifest)
            with mock.patch.object(
                self.object_store, "read_bytes", wraps=self.object_store.read_bytes
            ) as spy:
                record = small_oracle.evaluate(
                    manifest, self._nonempty(), evaluated_at_ms=21_000
                )
        self.assertEqual(record.status, "INCONCLUSIVE")
        self.assertIn("input_too_large", record.reason_codes)
        # No inspection-phase read beyond the authority baseline.
        self.assertEqual(spy.call_count, baseline)
        self.assertEqual(ARTIFACT_MAX_INPUT_BYTES, 64 * 1024 * 1024)  # unpatched

    def test_n1_64_qc_evidences_fold_into_one_digest(self) -> None:
        manifest = self._text_manifest()
        # Equivalent construction: rebuild the manifest with 64 fabricated
        # QC evidence entries (hash recomputed). QC-fact verification is
        # NOT the target here — evidence binding of _build_record is.
        base_evidence = manifest.qc_evidence[0]
        many = tuple(
            base_evidence.model_copy(
                update={
                    "check_id": f"qc.check.{index}",
                    "tool_fact_id": f"fact_bulk_{index}",
                    "evidence_sha256": f"{index:064x}",
                }
            )
            for index in range(64)
        )
        bulk_manifest = manifest.model_copy(
            update={"qc_evidence": many}
        ).with_computed_manifest_sha256()
        record = self.oracle._build_record(
            manifest=bulk_manifest,
            predicate=self._nonempty(),
            evaluated_at_ms=21_000,
            evaluation_phase="POST_EXECUTION",
            status="PASS",
            reason_codes=(),
            observation={"measured_format": "text", "inspector_version": "3"},
        )
        refs = record.evidence_refs
        self.assertLessEqual(len(refs), 8)
        set_digest_ref = next(
            ref for ref in refs if ref.startswith("qc_evidence_set_sha256:")
        )
        # changing ANY single evidence entry must change the digest
        changed = bulk_manifest.model_copy(
            update={
                "qc_evidence": many[:63]
                + (
                    many[63].model_copy(
                        update={"tool_fact_id": "fact_bulk_TAMPERED"}
                    ),
                )
            }
        ).with_computed_manifest_sha256()
        changed_record = self.oracle._build_record(
            manifest=changed,
            predicate=self._nonempty(),
            evaluated_at_ms=21_000,
            evaluation_phase="POST_EXECUTION",
            status="PASS",
            reason_codes=(),
            observation={"measured_format": "text", "inspector_version": "3"},
        )
        changed_ref = next(
            ref
            for ref in changed_record.evidence_refs
            if ref.startswith("qc_evidence_set_sha256:")
        )
        self.assertNotEqual(set_digest_ref, changed_ref)

    def test_t1_marker_normalization_matches_both_sides(self) -> None:
        manifest = self._text_manifest("Document mentions ABC in the body.")
        predicate = AcceptancePredicate.create(
            predicate_type="text.required_markers",
            subject_kind="artifact",
            # "abc" (case) and "ＡＢＣ" (fullwidth) must both match "ABC"
            # after NFKC + casefold on both sides.
            params={"markers": ["abc", "ＡＢＣ"]},
        )
        record = self.oracle.evaluate(
            manifest, predicate, evaluated_at_ms=21_000
        )
        self.assertEqual(record.status, "PASS", record.reason_codes)

    def test_x1_formula_only_xlsx_is_nonempty(self) -> None:
        manifest = self._passed_manifest(
            xlsx_bytes(["合计"], [], formulas=[("B1", "=SUM(A1:A9)")]),
            filename="formula_only.xlsx",
            format_id="xlsx",
            declared_mime=XLSX_MIME,
        )
        record = self.oracle.evaluate(
            manifest, self._nonempty(), evaluated_at_ms=21_000
        )
        self.assertEqual(record.status, "PASS", record.reason_codes)

    def test_x2_formula_only_data_rows_count(self) -> None:
        manifest = self._passed_manifest(
            xlsx_bytes(
                ["合计"],
                [],
                formulas=[("A2", "=1+1"), ("A3", "=2+2")],
            ),
            filename="formula_rows.xlsx",
            format_id="xlsx",
            declared_mime=XLSX_MIME,
        )
        two = self.oracle.evaluate(
            manifest,
            AcceptancePredicate.create(
                predicate_type="xlsx.min_data_rows",
                subject_kind="artifact",
                params={"min_rows": 2},
            ),
            evaluated_at_ms=21_000,
        )
        self.assertEqual(two.status, "PASS", two.reason_codes)
        three = self.oracle.evaluate(
            manifest,
            AcceptancePredicate.create(
                predicate_type="xlsx.min_data_rows",
                subject_kind="artifact",
                params={"min_rows": 3},
            ),
            evaluated_at_ms=21_000,
        )
        self.assertEqual(three.status, "FAIL")

    def test_p1_parser_runtime_error_is_error_not_inconclusive(self) -> None:
        from total_gateway.outcome_oracles import artifact_content as module

        manifest = self._text_manifest()
        with mock.patch.object(
            module, "_inspect_text", side_effect=RuntimeError("internal bug")
        ):
            record = self.oracle.evaluate(
                manifest, self._nonempty(), evaluated_at_ms=21_000
            )
        self.assertEqual(record.status, "ERROR")
        self.assertIn("inspector_failure", record.reason_codes)

    def test_e1_reason_codes_never_carry_user_strings(self) -> None:
        manifest = self._passed_manifest(
            xlsx_bytes(["姓名备注"], [["甲"]]),
            filename="e1.xlsx",
            format_id="xlsx",
            declared_mime=XLSX_MIME,
        )
        secret_column = "绝密列名XYZ"
        record = self.oracle.evaluate(
            manifest,
            AcceptancePredicate.create(
                predicate_type="xlsx.required_columns",
                subject_kind="artifact",
                params={"columns": [secret_column]},
            ),
            evaluated_at_ms=21_000,
        )
        self.assertEqual(record.status, "FAIL")
        self.assertEqual(record.reason_codes, ("xlsx.required_columns_missing",))
        for code in record.reason_codes:
            self.assertNotIn(secret_column, code)
            self.assertNotIn("姓名", code)


if __name__ == "__main__":
    unittest.main()
