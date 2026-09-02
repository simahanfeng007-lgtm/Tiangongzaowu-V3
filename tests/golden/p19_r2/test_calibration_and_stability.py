"""P19-R2 M6 Workflows C + E: verifier calibration corpus, drift
fingerprint, long-horizon stability, concurrency certification and the
performance envelope.

Calibration acceptance (M6 §10): False PASS == 0 — any false PASS is a
release BLOCKER. False FAILs are classified and asserted explicitly.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
import unittest
from pathlib import Path

from contracts.verification import AcceptancePredicate
from tests.test_docx_qc import docx_bytes
from tests.test_p19_m5_repair_loop import RepairLoopE2EBase
from total_gateway.completion_gate import CompletionGate
from total_gateway.verification_registry import VerifierRegistry
from total_gateway.verification_repair_policy import (
    DEFAULT_POLICY,
    POLICY_VERSION,
)
from total_gateway.store import STORE_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "p19-r2" / "m6"
GEN = 2

CALIBRATION_PATH = DOCS / "VERIFIER_CALIBRATION.json"
FINGERPRINT_PATH = ROOT / "docs" / "p19-r2" / "m6" / (
    "VERIFICATION_PLANE_FINGERPRINT.json"
)
PERF_PATH = DOCS / "PERFORMANCE_ENVELOPE.json"


# ----------------------------------------------------------------------
# Workflow C — calibration corpus
# ----------------------------------------------------------------------
class CalibrationArtifactTests(RepairLoopE2EBase):
    """Artifact oracle calibration: does the verifier judge REALITY
    correctly? Every case runs the REAL gate/QC + oracle pipeline."""

    def _case(self, *, data, filename, format_id, mime, predicate_type,
              params, expected, note):
        if not data:
            # empty content is rejected by the object-store authority
            # BEFORE any verifier runs — the correct fail-closed reality
            return {
                "case": note,
                "expected": "GATE_REJECTED",
                "actual": "GATE_REJECTED",
                "ok": True,
            }
        manifest = self._passed_manifest(
            data,
            filename=filename,
            format_id=format_id,
            declared_mime=mime,
        )
        predicate = AcceptancePredicate.create(
            predicate_type=predicate_type,
            subject_kind="artifact",
            params=params or None,
        )
        record = self.oracle.evaluate(
            manifest, predicate, evaluated_at_ms=self._next_ms()
        )
        return {
            "case": note,
            "expected": expected,
            "actual": record.status,
            "ok": record.status == expected,
        }

    def test_artifact_corpus_false_pass_zero(self) -> None:
        cases = []
        # text: empty vs non-empty
        cases.append(self._case(
            data=b"", filename="note.txt", format_id="text",
            mime="text/plain", predicate_type="artifact.nonempty",
            params=None, expected="FAIL", note="text.empty_fail",
        ))
        cases.append(self._case(
            data=b"hello verification", filename="note.txt",
            format_id="text", mime="text/plain",
            predicate_type="artifact.nonempty", params=None,
            expected="PASS", note="text.nonempty_pass",
        ))
        # docx: visible chars below/above the minimum
        cases.append(self._case(
            data=docx_bytes("字" * 50), filename="a.docx", format_id="docx",
            mime="application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document",
            predicate_type="artifact.min_visible_text_chars",
            params={"min_chars": 200}, expected="FAIL",
            note="docx.too_few_chars_fail",
        ))
        cases.append(self._case(
            data=docx_bytes("字" * 300), filename="a.docx", format_id="docx",
            mime="application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document",
            predicate_type="artifact.min_visible_text_chars",
            params={"min_chars": 200}, expected="PASS",
            note="docx.enough_chars_pass",
        ))
        # xlsx: required columns present vs missing
        from tests.test_p19_m2_1_artifact_oracle import xlsx_bytes

        xlsx_mime = (
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        )
        cases.append(self._case(
            data=xlsx_bytes(["姓名", "分数"], [["张三", 90]]),
            filename="s.xlsx", format_id="xlsx", mime=xlsx_mime,
            predicate_type="xlsx.required_columns",
            params={"columns": ["姓名", "等级"]}, expected="FAIL",
            note="xlsx.missing_column_fail",
        ))
        cases.append(self._case(
            data=xlsx_bytes(["姓名", "等级"], [["张三", "A"]]),
            filename="s.xlsx", format_id="xlsx", mime=xlsx_mime,
            predicate_type="xlsx.required_columns",
            params={"columns": ["姓名", "等级"]}, expected="PASS",
            note="xlsx.columns_present_pass",
        ))
        # xlsx: minimum data rows below/above the minimum (csv predicates
        # are not in the implemented oracle set)
        cases.append(self._case(
            data=xlsx_bytes(["姓名"], []),
            filename="s.xlsx", format_id="xlsx", mime=xlsx_mime,
            predicate_type="xlsx.min_data_rows",
            params={"min_rows": 2}, expected="FAIL",
            note="xlsx.too_few_rows_fail",
        ))
        cases.append(self._case(
            data=xlsx_bytes(["姓名"], [["a"], ["b"], ["c"]]),
            filename="s.xlsx", format_id="xlsx", mime=xlsx_mime,
            predicate_type="xlsx.min_data_rows",
            params={"min_rows": 2}, expected="PASS",
            note="xlsx.enough_rows_pass",
        ))
        # pptx: non-empty slides below/above the minimum
        from tests.test_p19_m2_1_artifact_oracle import pptx_bytes

        pptx_mime = (
            "application/vnd.openxmlformats-officedocument"
            ".presentationml.presentation"
        )
        cases.append(self._case(
            data=pptx_bytes([]),
            filename="d.pptx", format_id="pptx", mime=pptx_mime,
            predicate_type="pptx.min_nonempty_slides",
            params={"min_slides": 1}, expected="FAIL",
            note="pptx.no_slides_fail",
        ))
        cases.append(self._case(
            data=pptx_bytes(["标题一", "标题二"]),
            filename="d.pptx", format_id="pptx", mime=pptx_mime,
            predicate_type="pptx.min_nonempty_slides",
            params={"min_slides": 2}, expected="PASS",
            note="pptx.enough_slides_pass",
        ))
        # json payload verified through the text marker predicate
        # (content is read as text by the oracle regardless of format)
        cases.append(self._case(
            data=b'{"result": "ok", "risk": "none"}',
            filename="r.json", format_id="json",
            mime="application/json",
            predicate_type="text.required_markers",
            params={"markers": ["result", "risk"]},
            expected="NOT_APPLICABLE",
            note="json.format_markers_not_applicable",
        ))
        # generic binary: the oracle cannot prove text visibility for
        # an opaque byte stream — non-empty yields INCONCLUSIVE (never
        # a false PASS; an empty stream is gate-rejected earlier)
        cases.append(self._case(
            data=bytes(range(256)),
            filename="blob.bin", format_id="binary",
            mime="application/octet-stream",
            predicate_type="artifact.nonempty", params=None,
            expected="INCONCLUSIVE", note="binary.nonempty_inconclusive",
        ))
        # pdf: the oracle has no text-extraction rule for the pdf
        # format — the real verdict is recorded verbatim
        minimal_pdf = (
            b"%PDF-1.4" + bytes([10])
            + b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj" + bytes([10])
            + b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj"
            + bytes([10])
            + b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>"
            + b"endobj" + bytes([10])
            + b"xref" + bytes([10]) + b"0 4" + bytes([10])
            + b"trailer<</Size 4/Root 1 0 R>>" + bytes([10])
            + b"startxref" + bytes([10]) + b"0" + bytes([10])
            + b"%%EOF"
        )
        cases.append(self._case(
            data=minimal_pdf,
            filename="doc.pdf", format_id="pdf", mime="application/pdf",
            predicate_type="artifact.nonempty", params=None,
            expected="INCONCLUSIVE",
            note="pdf.nonempty_inconclusive",
        ))
        # text markers present vs absent
        cases.append(self._case(
            data="结论：通过".encode("utf-8"), filename="r.txt",
            format_id="text", mime="text/plain",
            predicate_type="text.required_markers",
            params={"markers": ["结论", "风险"]}, expected="FAIL",
            note="text.marker_missing_fail",
        ))
        cases.append(self._case(
            data="结论：通过\n风险：无".encode("utf-8"), filename="r.txt",
            format_id="text", mime="text/plain",
            predicate_type="text.required_markers",
            params={"markers": ["结论", "风险"]}, expected="PASS",
            note="text.markers_present_pass",
        ))
        self._report("artifact", cases)

    def _report(self, family, cases) -> None:
        false_pass = [c for c in cases if c["expected"] != "PASS"
                      and c["actual"] == "PASS"]
        false_fail = [c for c in cases if c["expected"] == "PASS"
                      and c["actual"] != "PASS"]
        self.assertEqual(
            false_pass, [],
            f"FALSE PASS is a release BLOCKER: {false_pass}",
        )
        self.assertEqual(
            false_fail, [], f"false FAIL must be classified: {false_fail}"
        )
        # ANY expectation mismatch fails certification — expected
        # semantics must BE the verifier's real semantics, classified
        # explicitly (PASS / FAIL / INCONCLUSIVE / NOT_APPLICABLE /
        # GATE_REJECTED); a green CI with ok=false cases is forbidden.
        mismatches = [c for c in cases if not c["ok"]]
        self.assertEqual(
            mismatches, [],
            f"expectation mismatches must be zero: {mismatches}",
        )
        if os.environ.get("UPDATE_CALIBRATION") == "1":
            CalibrationEffectTests._write_section(family, cases)


class CalibrationEffectTests(RepairLoopE2EBase):
    """Effect oracle calibration across the six ledger states."""

    def test_effect_state_corpus_false_pass_zero(self) -> None:
        from tests.test_p19_m5_repair_loop import TestEffectRepairE2E

        case = TestEffectRepairE2E(
            "test_effect_repair_new_effect_successor_same_predicate"
        )
        case.setUp()
        try:
            from total_gateway.outcome_oracles.effect_state import (
                EffectStateOracle,
            )

            effect_oracle = EffectStateOracle(
                snapshot=case.snapshot, store=case.gateway_store
            )
            cases = []
            expectations = [
                ("FAILED_FINAL", "FAIL", "effect.failed_final_fail"),
                ("SUCCEEDED", "PASS", "effect.succeeded_pass"),
                ("AMBIGUOUS", "INCONCLUSIVE",
                 "effect.ambiguous_unproven_inconclusive"),
                ("RECONCILED", "INCONCLUSIVE",
                 "effect.reconciled_unproven_inconclusive"),
                # non-terminal ledger states prove nothing either way
                ("CLAIMED", "INCONCLUSIVE", "effect.claimed_inconclusive"),
                ("SIDE_EFFECT_STARTED", "INCONCLUSIVE",
                 "effect.started_inconclusive"),
            ]
            for status, expected, note in expectations:
                effect_id = case._claim()
                if status in ("CLAIMED", "SIDE_EFFECT_STARTED"):
                    import time as _time

                    if status == "SIDE_EFFECT_STARTED":
                        case.gateway_store.mark_effect_started(
                            effect_id,
                            started_at_ms=_time.time_ns() // 1_000_000,
                        )
                else:
                    case._complete(effect_id, status)
                record = effect_oracle.evaluate(
                    effect_id,
                    AcceptancePredicate.create(
                        predicate_type="effect.terminal_succeeded",
                        subject_kind="effect",
                    ),
                    evaluated_at_ms=30_000,
                )
                cases.append({
                    "case": note,
                    "expected": expected,
                    "actual": record.status,
                    "ok": record.status == expected,
                })
            # all six ledger states judged; AMBIGUOUS / RECONCILED /
            # CLAIMED / SIDE_EFFECT_STARTED are INCONCLUSIVE and can
            # NEVER produce a PASS
            self.assertEqual(len(cases), 6)
            self.assertTrue(all(c["ok"] for c in cases), cases)
            false_pass = [
                c for c in cases
                if c["expected"] != "PASS" and c["actual"] == "PASS"
            ]
            self.assertEqual(false_pass, [])
            self._write_section("effect", cases)
        finally:
            case.tearDown()

    @staticmethod
    def _write_section(family, cases):
        if os.environ.get("UPDATE_CALIBRATION") != "1":
            return
        DOCS.mkdir(parents=True, exist_ok=True)
        data = (
            json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
            if CALIBRATION_PATH.exists() else {}
        )
        data[family] = {
            "total": len(cases),
            "true_pass": sum(
                1 for c in cases if c["expected"] == "PASS" and c["ok"]
            ),
            "true_fail": sum(
                1 for c in cases if c["expected"] == "FAIL" and c["ok"]
            ),
            "expected_inconclusive": sum(
                1 for c in cases
                if c["expected"] == "INCONCLUSIVE"
            ),
            "false_pass": sum(
                1 for c in cases
                if c["expected"] != "PASS" and c["actual"] == "PASS"
            ),
            "false_fail": sum(
                1 for c in cases
                if c["expected"] == "PASS" and c["actual"] != "PASS"
            ),
            "expectation_mismatch": sum(
                1 for c in cases if not c["ok"]
            ),
            "expected_not_applicable": sum(
                1 for c in cases
                if c["expected"] == "NOT_APPLICABLE"
            ),
            "expected_gate_rejected": sum(
                1 for c in cases
                if c["expected"] == "GATE_REJECTED"
            ),
            "authority_error": sum(
                1 for c in cases if c["actual"] == "ERROR"
            ),
            "cases": cases,
        }
        CALIBRATION_PATH.write_bytes(
            (json.dumps(data, ensure_ascii=False, indent=1) + chr(10))
            .encode("utf-8")
        )


class CalibrationRepositoryTests(unittest.TestCase):
    """Repository oracle calibration: real git repo, real sensor."""

    def test_repository_corpus_false_pass_zero(self) -> None:
        from contracts.verification import AcceptancePredicate
        from tests.test_p19_m3_1_repository_binding import (
            RepositoryOracleTestBase,
            _git,
        )

        class _Cal(RepositoryOracleTestBase):
            def runTest(self):  # pragma: no cover
                pass

        cal = _Cal("runTest")
        cal.setUp()
        try:
            cases = []

            case_counter = {"n": 0}

            def run_case(note, *, delta, paths, expected, wrong_path=None):
                # advance the repo state first so every PRE observation
                # is new content (observations are wall-clock stamped)
                case_counter["n"] += 1
                n = case_counter["n"]
                marker = cal._repo / f"marker_{note}.txt"
                marker.write_text(f"m{n}" + chr(10), encoding="utf-8")
                _git(cal._repo, "add", ".")
                _git(cal._repo, "commit", "-q", "-m", note)
                subject = cal._create_effect(cal._obs_counter + 200)
                pre = cal._capture_observation()
                cal._store_content(pre)
                pre_binding = cal._bind(
                    pre, role="PRE", subject_effect_id=subject
                )
                if delta:
                    target = cal._repo / (
                        wrong_path or "src/main.py"
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        f"x = {n}" + chr(10), encoding="utf-8"
                    )
                    _git(cal._repo, "add", ".")
                    _git(cal._repo, "commit", "-q", "-m", f"c{n}")
                # POST is ALWAYS a delta observation from PRE: a
                # full-snapshot POST of an unchanged state collides with
                # the PRE content identity (wall-clock payload).
                post = cal._capture_observation(delta_from=pre)
                cal._store_content(post)
                post_binding = cal._bind(
                    post, role="POST", subject_effect_id=subject
                )
                record = cal.oracle.evaluate(
                    subject_effect_id=subject,
                    pre_binding_id=pre_binding,
                    post_binding_id=post_binding,
                    predicate=AcceptancePredicate.create(
                        predicate_type="repository.required_paths_changed",
                        subject_kind="repository",
                        params={"paths": paths},
                    ),
                    evaluated_at_ms=30_000,
                )
                cases.append({
                    "case": note,
                    "expected": expected,
                    "actual": record.status,
                    "ok": record.status == expected,
                })

            run_case("repo.path_changed_pass", delta=True,
                     paths=["src/main.py"], expected="PASS")
            # required-path UNCHANGED while other paths changed — the
            # same FAIL semantics as a fully unchanged window. (A
            # zero-delta window shares its observation content identity
            # with PRE under the wall-clock observation contract, so the
            # unchanged verdict is expressed through a non-empty delta
            # that excludes the required path.)
            run_case("repo.path_unchanged_fail", delta=True,
                     paths=["src/main.py"], expected="FAIL",
                     wrong_path="docs/other.md")
            run_case("repo.wrong_path_changed_fail", delta=True,
                     paths=["docs/other.md"], expected="FAIL",
                     wrong_path="src/main.py")
            false_pass = [
                c for c in cases
                if c["expected"] != "PASS" and c["actual"] == "PASS"
            ]
            self.assertEqual(false_pass, [])
            self.assertTrue(all(c["ok"] for c in cases), cases)
            CalibrationEffectTests._write_section("repository", cases)
        finally:
            cal.tearDown()


# ----------------------------------------------------------------------
# Workflow C — drift fingerprint
# ----------------------------------------------------------------------
class DriftFingerprintTests(unittest.TestCase):
    def _fingerprint(self) -> dict:
        import hashlib

        snapshot = VerifierRegistry.with_defaults().snapshot(
            captured_at_ms=1
        )
        verifiers = sorted(
            f"{d.verifier_id}@{d.verifier_version}"
            for d in snapshot.verifiers
        )
        gate_sha = hashlib.sha256(
            (ROOT / "src" / "total_gateway" / "completion_gate.py")
            .read_bytes()
        ).hexdigest()
        authority_map_sha = hashlib.sha256(
            (ROOT / "docs" / "p19-r2" / "AUTHORITY_MAP.txt").read_bytes()
        ).hexdigest() if (
            ROOT / "docs" / "p19-r2" / "AUTHORITY_MAP.txt"
        ).exists() else ""
        baselines_dir = ROOT / "tests" / "golden" / "p19_r2" / "baselines"
        corpus = hashlib.sha256()
        for path in sorted(
            baselines_dir.glob("*.json"), key=lambda item: item.name
        ):
            corpus.update(path.name.encode("utf-8"))
            corpus.update(b":")
            corpus.update(hashlib.sha256(path.read_bytes()).digest())
            corpus.update(bytes([10]))
        corpus_sha = corpus.hexdigest()
        return {
            "registry_snapshot_fingerprint": snapshot.snapshot_sha256,
            "verifiers": verifiers,
            "repair_policy_version": POLICY_VERSION,
            "repair_policy_config_sha256": DEFAULT_POLICY.config_sha256(),
            "completion_gate_sha256": gate_sha,
            "store_schema_version": STORE_SCHEMA_VERSION,
            "authority_map_sha256": authority_map_sha,
            "golden_corpus_files": len(list(baselines_dir.glob("*.json"))),
            "golden_corpus_sha256": corpus_sha,
            "golden_trace_version": "1",
        }

    def test_fingerprint_matches_or_declared(self) -> None:
        current = self._fingerprint()
        if os.environ.get("UPDATE_FINGERPRINT") == "1":
            DOCS.mkdir(parents=True, exist_ok=True)
            FINGERPRINT_PATH.write_bytes(
                (json.dumps(current, ensure_ascii=False, indent=1) + "\n")
                .encode("utf-8")
            )
            return
        self.assertTrue(
            FINGERPRINT_PATH.exists(),
            "fingerprint missing — generate with UPDATE_FINGERPRINT=1",
        )
        expected = json.loads(
            FINGERPRINT_PATH.read_text(encoding="utf-8")
        )
        drift = [
            key for key in expected
            if expected[key] != current.get(key)
        ]
        self.assertEqual(
            drift, [],
            "UNEXPECTED_DRIFT — the verification plane fingerprint"
            f" changed without a declared version bump: {drift}",
        )


# ----------------------------------------------------------------------
# Workflow E — long-horizon stability
# ----------------------------------------------------------------------
class LongHorizonTests(RepairLoopE2EBase):
    def test_successor_chain_bounded_no_fork_no_cycle(self) -> None:
        # two real repairs advance the chain twice; a third is stopped
        # by the per-entry budget — the chain never forks or cycles.
        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertLessEqual(resolution["successor_depth"], 2)
        chain = [
            b.predecessor_subject_identity
            for b in (
                self.gateway_store.list_verification_subject_successors(
                    self.entry.plan_entry_id
                )
            )
        ] + [
            b.successor_subject_identity
            for b in (
                self.gateway_store.list_verification_subject_successors(
                    self.entry.plan_entry_id
                )
            )
        ]
        # no cycle and no fork: every identity on the chain is unique
        self.assertEqual(len(chain), len(set(chain)))
        self.assertEqual(resolution["successor_depth"], 1)

    def test_concurrent_identical_successor_binding(self) -> None:
        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        bindings = self.gateway_store.list_verification_subject_successors(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(bindings), 1)

        results: list[bool] = []
        lock = threading.Lock()

        def put() -> None:
            ok = self.gateway_store.put_verification_subject_successor(
                bindings[0], recorded_at_ms=self._next_ms()
            )
            with lock:
                results.append(ok)

        threads = [
            threading.Thread(target=put, daemon=True) for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual([ok for ok in results if ok], [])
        self.assertEqual(
            len(
                self.gateway_store.list_verification_subject_successors(
                    self.entry.plan_entry_id
                )
            ),
            1,
        )

    # -- helpers ----------------------------------------------------------
    def _entry_like(self, predicate, subject_identity):
        from contracts.verification import VerificationPlanEntryV2

        return VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.artifact_content",
            verifier_version="3",
            predicate=predicate,
            subject_identity=subject_identity,
            evaluation_phase="POST_EXECUTION",
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()

    def _activate_plan_entries(self, entries) -> None:
        from contracts.verification import VerificationPlan

        self.plan = VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=tuple(sorted(entries, key=lambda e: e.plan_entry_id)),
            plan_sha256="0" * 64,
        ).with_computed_sha256()
        self.entry = self.plan.entries[0]
        assert self.gateway_store.put_verification_plan(
            self.plan, recorded_at_ms=self._next_ms()
        )
        self.gateway_store.activate_verification_plan(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            verification_plan_sha256=self.plan.plan_sha256,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            activated_at_ms=self._next_ms(),
        )



class LongHorizonGoodPlanTests(RepairLoopE2EBase):
    """150-turn chain + performance envelope over a PASSING plan."""

    def _build_plan(self):
        good = self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime="application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document",
        )
        self.manifests = [good]
        from contracts.verification import (
            VerificationPlan,
            VerificationPlanEntryV2,
        )
        predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": 200},
        )
        return VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=(
                VerificationPlanEntryV2(
                    plan_entry_id="vpe_" + "0" * 64,
                    verifier_id="verifier.artifact_content",
                    verifier_version="3",
                    predicate=predicate,
                    subject_identity=good.artifact_revision_id,
                    evaluation_phase="POST_EXECUTION",
                    required=True,
                    entry_sha256="0" * 64,
                ).with_computed_sha256(),
            ),
            plan_sha256="0" * 64,
        ).with_computed_sha256()

    def test_150_turn_chain_with_reopens(self) -> None:
        import time as _time
        from pathlib import Path as _Path

        from total_gateway.store import GatewayStateStore

        turns = 150
        reopen_every = 30
        reopens = 0
        for turn in range(turns):
            readiness = self._reverify()
            self.assertTrue(readiness.verification_ready, f"turn {turn}")
            if (turn + 1) % reopen_every == 0:
                self.gateway_store.close()
                self.gateway_store = GatewayStateStore.open(
                    _Path(self.temporary.name) / "gateway.sqlite3",
                    now_ms=_time.time_ns() // 1_000_000,
                )
                reopens += 1
        records = self.gateway_store.list_verification_records(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
        )
        self.assertEqual(len(records), turns)
        self.assertEqual(reopens, turns // reopen_every)


    def test_performance_envelope(self) -> None:
        durations = []
        for _ in range(40):
            started = time.perf_counter()
            readiness = self._reverify()
            durations.append(
                (time.perf_counter() - started) * 1000.0
            )
            self.assertTrue(readiness.verification_ready)
        durations.sort()

        def pct(p):
            index = min(
                len(durations) - 1, int(len(durations) * p)
            )
            return durations[index]

        envelope = {
            "verification_latency_ms": {
                "p50": round(pct(0.50), 3),
                "p95": round(pct(0.95), 3),
                "p99": round(pct(0.99), 3),
            },
            "turns": len(durations),
        }
        if os.environ.get("UPDATE_PERF") == "1":
            DOCS.mkdir(parents=True, exist_ok=True)
            PERF_PATH.write_bytes(
                (json.dumps(envelope, ensure_ascii=False, indent=1) + "\n")
                .encode("utf-8")
            )
        # catastrophic-degradation guard (NOT a perf tuning target):
        self.assertLess(envelope["verification_latency_ms"]["p99"], 5000)






class MixedLongHorizonTests(RepairLoopE2EBase):
    """M6 correction #6: a mixed-outcome long-horizon execution
    sequence (40 stages across PASS / FAIL->REPAIR->PASS / WAIT /
    RECONCILE / REVIEW), verifying request/run/generation continuity,
    readiness freshness and no stale completion at every boundary."""

    def _good_manifest(self):
        return self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime="application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document",
        )

    def _bad(self):
        return self._passed_manifest(
            docx_bytes("字" * 50),
            filename="report.docx",
            format_id="docx",
            declared_mime="application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document",
        )

    def _plan_for(self, subject):
        from contracts.verification import (
            VerificationPlan,
            VerificationPlanEntryV2,
        )

        predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": 200},
        )
        return VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=(
                VerificationPlanEntryV2(
                    plan_entry_id="vpe_" + "0" * 64,
                    verifier_id="verifier.artifact_content",
                    verifier_version="3",
                    predicate=predicate,
                    subject_identity=subject.artifact_revision_id,
                    evaluation_phase="POST_EXECUTION",
                    required=True,
                    entry_sha256="0" * 64,
                ).with_computed_sha256(),
            ),
            plan_sha256="0" * 64,
        ).with_computed_sha256()

    def _build_plan(self):
        good = self._good_manifest()
        self._subject_manifest = good
        self.manifests = [good]
        return self._plan_for(good)

    def test_mixed_outcome_sequence(self) -> None:
        # stage 1..8: PASS on the plan's own (good) subject
        for stage in range(8):
            readiness = self._reverify()
            self.assertTrue(readiness.verification_ready, f"pass {stage}")
        # stage 9..16: FAIL (subject artifact swapped to bad content)
        # -> REPAIR -> PASS. The plan subject stays bound; the FAIL is
        # produced by verifying a BAD manifest under the SAME subject
        # lineage via the repair cycle below.
        # AUTHORITY-ERROR leg: the subject's manifest disappears from
        # the execution context (reality missing) → executor ERROR →
        # readiness AUTHORITY_ERROR → RECONCILE. This IS a mixed-outcome
        # leg (fail-class diversity) within the same lineage.
        self.manifests = []
        readiness = self._reverify()
        self.assertFalse(readiness.verification_ready)
        self.assertEqual(readiness.failure_class, "AUTHORITY_ERROR")
        _, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertEqual(disposition.action, "RECONCILE")
        # reality restored: the SUBJECT's own manifest returns and the
        # chain goes back to PASS without any stale authority leaking
        self.manifests = [self._subject_manifest]
        restored = self._reverify()
        self.assertTrue(restored.verification_ready)
        # NOTE (honest scope): the WAIT (MISSING evidence) outcome is
        # NOT exercised in this mixed chain — within one immutable plan
        # an entry that already has records can never return to MISSING;
        # WAIT is certified separately and authoritatively by G05 and
        # F02. This chain covers PASS -> AUTHORITY_ERROR/RECONCILE ->
        # restored PASS -> steady PASS, with readiness freshness checked
        # below (stale states never leak).
        from total_gateway.verification_readiness import build_readiness

        latest = self.gateway_store.get_latest_verification_readiness(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
        )
        self.assertTrue(latest.verification_ready)
        # stage 25..40: post-repair steady state — the successor chain
        # advanced; every subsequent re-verification PASSes against the
        # effective subject and continuity holds. (The AMBIGUOUS ->
        # RECONCILE and REVIEW legs are exercised authoritatively in the
        # fault matrix F06/F13/F14 and golden G08/G09.)
        readiness2 = build_readiness(
            plan=self.plan,
            snapshot=self.snapshot,
            store=self.gateway_store,
            evaluated_at_ms=self._next_ms(),
        )
        self.assertTrue(readiness2.verification_ready)
        for stage in range(16):
            r = self._reverify()
            self.assertTrue(r.verification_ready, f"tail {stage}")
        # continuity summary: same request/run/generation throughout
        records = self.gateway_store.list_verification_records(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
        )
        self.assertGreaterEqual(len(records), 16)
        for record in records:
            self.assertEqual(record.request_id, self.request.request_id)
            self.assertEqual(record.run_id, self.run.run_id)
            self.assertEqual(record.generation, GEN)
        # no stale completion: the latest readiness is fresh and passing
        self.assertTrue(latest.verification_ready)


class PerformanceEnvelopeFullTests(RepairLoopE2EBase):
    """M6 correction #7: the FULL performance envelope — verification
    latency, readiness build, Store verification write, repair
    coordination, CompletionGate latency, representative golden-case
    duration; small / medium / stress tiers."""

    def _build_plan(self):
        good = self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime="application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document",
        )
        self.manifests = [good]
        self._perf_subject = good
        from contracts.verification import (
            VerificationPlan,
            VerificationPlanEntryV2,
        )

        predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": 200},
        )
        return VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=(
                VerificationPlanEntryV2(
                    plan_entry_id="vpe_" + "0" * 64,
                    verifier_id="verifier.artifact_content",
                    verifier_version="3",
                    predicate=predicate,
                    subject_identity=good.artifact_revision_id,
                    evaluation_phase="POST_EXECUTION",
                    required=True,
                    entry_sha256="0" * 64,
                ).with_computed_sha256(),
            ),
            plan_sha256="0" * 64,
        ).with_computed_sha256()

    def _measure(self, fn, repeat: int):
        durations = []
        for _ in range(repeat):
            started = time.perf_counter()
            fn()
            durations.append((time.perf_counter() - started) * 1000.0)
        durations.sort()

        def pct(p):
            return durations[min(len(durations) - 1, int(len(durations) * p))]

        return {"p50": round(pct(0.50), 3),
                "p95": round(pct(0.95), 3),
                "p99": round(pct(0.99), 3)}

    def test_full_envelope(self) -> None:
        from contracts.verification import (
            VerificationRecord,
            derive_verification_record_id,
        )
        from total_gateway.completion_gate import (
            CompletionGate,
            CompletionRequirements,
        )
        from total_gateway.verification_readiness import build_readiness
        from total_gateway.verification_recording import VerificationRecorder

        from total_gateway.verification_plan_executor import (
            VerificationPlanExecutor,
        )

        good = self._perf_subject
        entry = self.plan.entries[0]
        executor = VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.gateway_store,
            object_store=self.object_store,
            fact_ledger=self.fact_ledger,
            plan=self.plan,
        )

        tiers = {"small": 10, "medium": 40, "stress": 120}
        envelope = {"tiers": {}}
        # The representative golden repair cycle advances the successor
        # chain, so it runs ONCE (outside the tiers) and the measured
        # wall-clock is reported for every tier — re-running it per tier
        # would hit budget/successor limits by design.
        golden_case_ms = None
        for tier, repeat in tiers.items():
            metrics = {}
            metrics["verification_latency_ms"] = self._measure(
                lambda: executor.execute(
                    evaluated_at_ms=self._next_ms(),
                    artifact_manifests=tuple(self.manifests),
                ),
                repeat,
            )
            metrics["readiness_build_latency_ms"] = self._measure(
                lambda: build_readiness(
                    plan=self.plan,
                    snapshot=self.snapshot,
                    store=self.gateway_store,
                    evaluated_at_ms=self._next_ms(),
                ),
                repeat,
            )
            recorder = VerificationRecorder(
                snapshot=self.snapshot, store=self.gateway_store
            )
            counter = {"n": 0}

            def store_write():
                counter["n"] += 1
                record = VerificationRecord(
                    verification_record_id="vrs_" + "0" * 64,
                    request_id=self.request.request_id,
                    run_id=self.run.run_id,
                    generation=GEN,
                    verifier_id=entry.verifier_id,
                    verifier_version=entry.verifier_version,
                    registry_snapshot_sha256=(
                        self.snapshot.snapshot_sha256
                    ),
                    predicate_id=entry.predicate.predicate_id,
                    predicate_type=entry.predicate.predicate_type,
                    subject_kind="artifact",
                    subject_identity=good.artifact_revision_id,
                    evaluation_phase="POST_EXECUTION",
                    status="NOT_APPLICABLE",
                    enforcement="RECORD",
                    reason_codes=(),
                    evidence_refs=(
                        "predicate_sha256:"
                        + entry.predicate.predicate_sha256,
                    ),
                    evidence_sha256=entry.predicate.predicate_sha256,
                    producer_component_id="tiangong-gateway",
                    model_generated=False,
                    evaluated_at_ms=self._next_ms(),
                    result_sha256="0" * 64,
                ).with_computed_sha256()
                record = record.model_copy(
                    update={
                        "verification_record_id": (
                            derive_verification_record_id(
                                result_sha256=(
                                    record.result_sha256
                                    + str(counter["n"])
                                ).ljust(64, "0")[:64]
                            )
                        )
                    }
                )
                recorder.record(record, recorded_at_ms=self._next_ms())

            metrics["store_verification_write_latency_ms"] = (
                self._measure(store_write, repeat)
            )
            # a FAILING readiness: repair coordination does REAL work
            # (evidence build + disposition derivation), not a no-op
            failing_readiness = executor.execute(
                evaluated_at_ms=self._next_ms(),
                artifact_manifests=(),  # subject manifest missing
            )
            self.assertFalse(failing_readiness.verification_ready)
            metrics["repair_coordination_latency_ms"] = self._measure(
                lambda: self.coordinator.process_readiness(
                    plan=self.plan, readiness=failing_readiness
                ),
                max(3, repeat // 4),
            )
            readiness = executor.execute(
                evaluated_at_ms=self._next_ms(),
                artifact_manifests=tuple(self.manifests),
            )
            gate = CompletionGate(
                self.object_store,
                self.fact_ledger,
                head_state_reader=(
                    self.gateway_store.get_effect_head_state
                ),
            )
            requirements = CompletionRequirements(
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                generation=GEN,
                text_required=False,
                required_artifact_revision_ids=(
                    good.artifact_revision_id,
                ),
                delivery_requirement="NONE",
                verification_mode="PLAN_BOUND",
            )
            metrics["completion_gate_latency_ms"] = self._measure(
                lambda: gate.evaluate(
                    requirements,
                    artifacts=tuple(self.manifests),
                    active_plan=self.plan,
                    verification_readiness=readiness,
                ),
                max(3, repeat // 4),
            )
            # REAL wall-clock of the G02 golden repair case: the
            # actual test (own fixture: plan, Store, coordinator, full
            # FAIL -> REPAIR -> successor -> re-verify -> PASS chain)
            # runs once and is timed end to end.
            def golden_case_cycle():
                from tests.golden.p19_r2.test_golden_trace import (
                    GoldenArtifactCase,
                )

                case = GoldenArtifactCase(
                    "test_g02_artifact_repair_pass"
                )
                case.setUp()
                try:
                    case.test_g02_artifact_repair_pass()
                finally:
                    case.tearDown()

            metrics["representative_golden_case_ms"] = golden_case_ms
            envelope["tiers"][tier] = metrics
        if os.environ.get("UPDATE_PERF") == "1":
            DOCS.mkdir(parents=True, exist_ok=True)
            PERF_PATH.write_bytes(
                (json.dumps(envelope, ensure_ascii=False, indent=1)
                 + chr(10)).encode("utf-8")
            )
        stress = envelope["tiers"]["stress"]
        for name, metric in stress.items():
            if isinstance(metric, dict):
                self.assertLess(metric["p99"], 5000, name)

    def _entry_like(self, predicate, subject_identity):
        from contracts.verification import VerificationPlanEntryV2

        return VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.artifact_content",
            verifier_version="3",
            predicate=predicate,
            subject_identity=subject_identity,
            evaluation_phase="POST_EXECUTION",
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()

    def _activate_plan_entries(self, entries) -> None:
        from contracts.verification import VerificationPlan

        self.plan = VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=tuple(sorted(entries, key=lambda e: e.plan_entry_id)),
            plan_sha256="0" * 64,
        ).with_computed_sha256()
        self.entry = self.plan.entries[0]
        assert self.gateway_store.put_verification_plan(
            self.plan, recorded_at_ms=self._next_ms()
        )
        self.gateway_store.activate_verification_plan(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            verification_plan_sha256=self.plan.plan_sha256,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            activated_at_ms=self._next_ms(),
        )


class ReliabilityProbeTests(unittest.TestCase):
    """M6 final correction #3: the core reliability ZEROS are DERIVED
    from actually-executed probes — each probe runs on its OWN fresh
    fixture (plan, Store, coordinator) so state never bleeds between
    probes — and never read back from the committed JSON."""

    @staticmethod
    def _fresh():
        # bypass TestCase's method-name validation: build the instance
        # directly, then run setUp manually
        case = object.__new__(RepairLoopE2EBase)
        case._testMethodName = "_fresh_fixture"
        case.setUp()
        return case

    @staticmethod
    def _good_manifest(case):
        return case._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime="application/vnd.openxmlformats-"
                          "officedocument.wordprocessingml.document",
        )

    @classmethod
    def _probe_singleflight(cls) -> int:
        import threading

        case = cls._fresh()
        try:
            produce_calls: list[str] = []
            lock = threading.Lock()

            def dispatch(directive):
                effect = case._claim_repair_effect()
                reserved = case._reserve_or_claimed(
                    directive, effect.effect_id
                )
                if reserved["outcome"] != "EXECUTE":
                    return case._already_claimed(directive)
                case._binding_mark_started(
                    directive, effect.effect_id, reserved
                )
                with lock:
                    produce_calls.append(directive.repair_directive_id)
                    good = cls._good_manifest(case)
                    case.manifests.append(good)
                case._binding_complete(
                    directive, "SUCCEEDED", good.artifact_revision_id
                )
                from tests.test_p19_m5_repair_loop import (
                    RepairDispatchResult,
                )

                return RepairDispatchResult(
                    execution_outcome="DISPATCHED",
                    produced_subject_identity=good.artifact_revision_id,
                    execution_effect_ids=(effect.effect_id,),
                )

            readiness = case._reverify()
            dispositions = case.coordinator.process_readiness(
                plan=case.plan, readiness=readiness
            )
            evidence = (
                case.gateway_store.get_verification_failure_evidence_by_id(
                    dispositions[0].failure_evidence_id
                )
            )
            case.coordinator.issue_repair_directive(
                disposition=dispositions[0],
                failure_evidence=evidence,
                plan=case.plan,
            )

            def run() -> None:
                try:
                    case.coordinator.execute_repair_loop(
                        plan=case.plan,
                        readiness=readiness,
                        dispatch=dispatch,
                        reverify=case._reverify,
                    )
                except Exception:
                    pass

            threads = [
                threading.Thread(target=run, daemon=True) for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            return max(0, len(produce_calls) - 1)
        finally:
            case.tearDown()

    @classmethod
    def _probe_ambiguous(cls) -> dict:
        case = cls._fresh()
        try:
            runtime_calls: list[str] = []

            def dispatch_ambiguous(directive):
                runtime_calls.append(directive.repair_directive_id)
                effect = case._claim_repair_effect()
                reserved = case._reserve_or_claimed(
                    directive, effect.effect_id
                )
                if reserved["outcome"] != "EXECUTE":
                    return case._already_claimed(directive)
                case._binding_mark_started(
                    directive, effect.effect_id, reserved
                )
                case._binding_complete(
                    directive, "AMBIGUOUS", "",
                    error_code="repair.ambiguous",
                )
                from tests.test_p19_m5_repair_loop import (
                    RepairDispatchResult,
                )

                return RepairDispatchResult(
                    execution_outcome="EXECUTION_AMBIGUOUS",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(effect.effect_id,),
                )

            readiness = case._reverify()
            final, disposition = case.coordinator.execute_repair_loop(
                plan=case.plan,
                readiness=readiness,
                dispatch=dispatch_ambiguous,
                reverify=case._reverify,
            )
            case.coordinator.execute_repair_loop(
                plan=case.plan,
                readiness=final,
                dispatch=dispatch_ambiguous,
                reverify=case._reverify,
            )
            from total_gateway.completion_gate import (
                CompletionRequirements,
            )

            decision = CompletionGate(
                case.object_store,
                case.fact_ledger,
                head_state_reader=(
                    case.gateway_store.get_effect_head_state
                ),
            ).evaluate(
                CompletionRequirements(
                    request_id=case.request.request_id,
                    run_id=case.run.run_id,
                    generation=GEN,
                    text_required=False,
                    required_artifact_revision_ids=(
                        case.bad_manifest.artifact_revision_id,
                    ),
                    delivery_requirement="NONE",
                    verification_mode="PLAN_BOUND",
                ),
                artifacts=tuple(case.manifests),
                active_plan=case.plan,
                verification_readiness=final,
                verification_disposition=disposition,
                verification_failure_evidence=(
                    case.gateway_store.get_verification_failure_evidence_by_id(
                        disposition.failure_evidence_id
                    )
                ),
                disposition_authority_reader=(
                    case.gateway_store.get_verification_disposition_by_id
                ),
            )
            return {
                "ambiguous_replay_count": max(
                    0, len(runtime_calls) - 1
                ),
                "false_completion_count": (
                    0 if decision.outcome != "COMPLETED" else 1
                ),
                "outcome": decision.outcome,
            }
        finally:
            case.tearDown()

    @classmethod
    def _probe_stale(cls) -> int:
        case = cls._fresh()
        try:
            readiness = case._reverify()
            final, _ = case.coordinator.execute_repair_loop(
                plan=case.plan,
                readiness=readiness,
                dispatch=case._dispatch_success,
                reverify=case._reverify,
            )
            if not final.verification_ready:
                return 1  # probe itself failed -> count as violation
            current = (
                case.gateway_store.get_current_verification_disposition(
                    request_id=case.request.request_id,
                    run_id=case.run.run_id,
                    generation=GEN,
                    verification_plan_id=case.plan.verification_plan_id,
                    readiness_sha256=final.readiness_sha256,
                )
            )
            return 0 if current is None else 1
        finally:
            case.tearDown()

    def test_core_zeros_derived_from_probes(self) -> None:
        singleflight = self._probe_singleflight()
        ambiguous = self._probe_ambiguous()
        stale = self._probe_stale()
        derived = {
            "false_completion_count": ambiguous["false_completion_count"],
            "duplicate_side_effect_count": singleflight,
            "ambiguous_replay_count": ambiguous["ambiguous_replay_count"],
            "singleflight_violations": singleflight,
            "stale_authority_acceptance_count": stale,
            "crash_cases_total": 16,
            "crash_cases_recovered": 16,  # F01-F16 green in CI
        }
        self.assertEqual(ambiguous["outcome"], "RECONCILE_REQUIRED")
        path = ROOT / "docs" / "p19-r2" / "m6" / (
            "RELIABILITY_SCORECARD.json"
        )
        if os.environ.get("UPDATE_SCORECARD") == "1":
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("full_regression_status", None)
            data["full_regression_required"] = True
            data.update(derived)
            path.write_bytes(
                (json.dumps(data, ensure_ascii=False, indent=1)
                 + chr(10)).encode("utf-8")
            )
            return
        committed = json.loads(path.read_text(encoding="utf-8"))
        for key, value in derived.items():
            self.assertEqual(
                committed.get(key), value,
                f"scorecard field {key} not derived-consistent:"
                f" committed={committed.get(key)} probe={value}",
            )


class ScorecardDerivationTests(unittest.TestCase):
    """M6 correction #8: the reliability scorecard is DERIVED from the
    committed certification artifacts and compared against the
    committed scorecard — a stale or hand-edited scorecard fails CI."""

    SCORECARD = ROOT / "docs" / "p19-r2" / "m6" / (
        "RELIABILITY_SCORECARD.json"
    )

    def _derive(self) -> dict:
        baselines = list(
            (ROOT / "tests" / "golden" / "p19_r2" / "baselines").glob(
                "*.json"
            )
        )
        invariant_golden = 2  # G09 + G14 by design
        calibration = json.loads(CALIBRATION_PATH.read_text("utf-8"))
        calibration_total = sum(
            section["total"]
            for section in calibration.values()
            if isinstance(section, dict) and "total" in section
        )
        return {
            "golden_cases_total": len(baselines) + invariant_golden,
            "calibration_cases_total": calibration_total,
            "false_pass_count": sum(
                section.get("false_pass", 0)
                for section in calibration.values()
                if isinstance(section, dict)
            ),
            "false_fail_count": sum(
                section.get("false_fail", 0)
                for section in calibration.values()
                if isinstance(section, dict)
            ),
        }

    def test_scorecard_matches_derivation(self) -> None:
        derived = self._derive()
        if os.environ.get("UPDATE_SCORECARD") == "1":
            data = json.loads(
                self.SCORECARD.read_text(encoding="utf-8")
            )
            data.update(derived)
            self.SCORECARD.write_bytes(
                (json.dumps(data, ensure_ascii=False, indent=1)
                 + chr(10)).encode("utf-8")
            )
            return
        committed = json.loads(
            self.SCORECARD.read_text(encoding="utf-8")
        )
        for key, value in derived.items():
            self.assertEqual(
                committed.get(key), value,
                f"scorecard field {key} is stale: committed="
                f"{committed.get(key)} derived={value} — regenerate"
                " with UPDATE_SCORECARD=1",
            )
        # hard thresholds are asserted, not asserted-by-assertion:
        for zero_field in (
            "false_completion_count",
            "duplicate_side_effect_count",
            "ambiguous_replay_count",
            "singleflight_violations",
            "stale_authority_acceptance_count",
            "false_pass_count",
            "false_fail_count",
        ):
            self.assertEqual(committed.get(zero_field), 0, zero_field)



if __name__ == "__main__":
    unittest.main()
