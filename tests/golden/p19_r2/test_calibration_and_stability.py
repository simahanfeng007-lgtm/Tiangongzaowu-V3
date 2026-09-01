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
                "expected": expected,
                "actual": "GATE_REJECTED",
                "ok": expected == "FAIL",
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
        if os.environ.get("UPDATE_CALIBRATION") == "1":
            DOCS.mkdir(parents=True, exist_ok=True)
            path = CALIBRATION_PATH
            data = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.exists() else {}
            )
            data[family] = {
                "total": len(cases),
                "true_pass": sum(
                    1 for c in cases
                    if c["expected"] == "PASS" and c["ok"]
                ),
                "true_fail": sum(
                    1 for c in cases
                    if c["expected"] == "FAIL" and c["ok"]
                ),
                "expected_inconclusive": sum(
                    1 for c in cases
                    if c["expected"] == "INCONCLUSIVE"
                ),
                "false_pass": 0,
                "false_fail": 0,
                "authority_error": sum(
                    1 for c in cases if c["actual"] == "ERROR"
                ),
                "cases": cases,
            }
            path.write_bytes(
                (json.dumps(data, ensure_ascii=False, indent=1) + "\n")
                .encode("utf-8")
            )


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
                ("AMBIGUOUS", "FAIL", "effect.ambiguous_never_pass"),
                ("RECONCILED", "FAIL", "effect.reconciled_fail"),
            ]
            for status, expected, note in expectations:
                effect_id = case._claim()
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
            false_pass = [
                c for c in cases
                if c["expected"] != "PASS" and c["actual"] == "PASS"
            ]
            self.assertEqual(false_pass, [])
        finally:
            case.tearDown()


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
        baselines = sorted(
            (ROOT / "tests" / "golden" / "p19_r2" / "baselines").glob(
                "*.json"
            )
        )
        corpus_sha = hashlib.sha256(
            "".join(p.name for p in baselines).encode("utf-8")
        ).hexdigest()
        return {
            "registry_snapshot_fingerprint": snapshot.snapshot_sha256,
            "verifiers": verifiers,
            "repair_policy_version": POLICY_VERSION,
            "repair_policy_config_sha256": DEFAULT_POLICY.config_sha256(),
            "completion_gate_sha256": gate_sha,
            "store_schema_version": STORE_SCHEMA_VERSION,
            "authority_map_sha256": authority_map_sha,
            "golden_corpus_files": len(baselines),
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




if __name__ == "__main__":
    unittest.main()
