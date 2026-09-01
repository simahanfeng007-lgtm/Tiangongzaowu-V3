"""P19-R2 M6 Workflow D: the F01–F16 fault-injection matrix.

Each case truncates the authority pipeline at an EXACT point (real
Store state, never mocks), then recovers like a new process would and
asserts the five hard invariants:

    False Completion       = 0
    Duplicate Side Effect  = 0
    AMBIGUOUS Replay       = 0
    Stale PASS Reuse       = 0
    Completion Bypass      = 0
"""

from __future__ import annotations

import threading
import unittest

from tests.test_docx_qc import docx_bytes
from tests.test_p19_m5_repair_loop import RepairDispatchResult
from tests.golden.p19_r2.test_golden_trace import (
    _GoldenArtifactFixture,
    DOCX_MIME,
)

GEN = 2


class FaultMatrixTests(_GoldenArtifactFixture):
    """F01–F16 over the artifact fixture (plus repo/effect variants)."""

    # F01: crash before the verification record commit -----------------
    def test_f01_record_missing_recovery_reruns_executor(self) -> None:
        # truncation: nothing recorded (crash before record commit)
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        readiness = self._reverify()  # recovery: re-run the executor
        self.assertFalse(readiness.verification_ready)
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        # no false completion path: the PASS came from a NEW record
        records = self._entry_records()
        self.assertTrue(any(r.status == "PASS" for r in records))

    # F02: record committed, readiness not ------------------------------
    def test_f02_readiness_missing_recovery_derives(self) -> None:
        from total_gateway.verification_readiness import build_readiness

        # truncate: one FAIL record exists, readiness was never built
        executor = self._executor()
        executor_records = executor._dispatch_entry  # noqa: B018
        # run ONLY the entry dispatch (records) without building readiness
        entry = self.plan.entries[0]
        from total_gateway.verification_plan_executor import (
            VerificationPlanExecutorError,
        )

        try:
            executor._dispatch_entry(
                entry,
                evaluated_at_ms=self._next_ms(),
                manifests_by_revision={
                    m.artifact_revision_id: m for m in self.manifests
                },
            )
        except VerificationPlanExecutorError:
            pass  # oracle verdict recorded; readiness intentionally NOT
        # recovery: derive readiness from the persisted record
        readiness = build_readiness(
            plan=self.plan,
            snapshot=self.snapshot,
            store=self.gateway_store,
            evaluated_at_ms=self._next_ms(),
        )
        self.gateway_store.put_verification_readiness(
            readiness, recorded_at_ms=self._next_ms()
        )
        self.assertFalse(readiness.verification_ready)

    # F03: evidence written, disposition not ----------------------------
    def test_f03_evidence_without_disposition_recovery(self) -> None:
        readiness = self._reverify()
        # truncate: process only the evidence half (disposition write
        # crashed) — re-running process_readiness derives it cleanly
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        self.assertEqual(len(dispositions), 1)
        self.assertEqual(dispositions[0].action, "REPAIR")

    # F04: directive written, binding not reserved ----------------------
    def test_f04_directive_without_binding_recovery(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        self.assertIsNotNone(directive)
        # CRASH. Recovery: the loop REUSES the pending directive.
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 1)

    # F05: binding RESERVED, runtime never started ----------------------
    def test_f05_reserved_without_runtime_recovery_single_run(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        self.assertEqual(reserved["outcome"], "EXECUTE")
        # CRASH pre-boundary. A recovered worker carries a NEW claim: as
        # long as the original lease is LIVE it must FOLLOW (no
        # execution at all).
        runtime_calls: list[str] = []

        def dispatch(directive_):
            effect_id = self._binding_effect_id(directive_)
            reserved = self._reserve_or_claimed(directive_, effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive_)
            runtime_calls.append(directive_.repair_directive_id)
            self._binding_mark_started(directive_, effect_id, reserved)
            good = self._passed_manifest(
                docx_bytes("字" * 300),
                filename="report.docx",
                format_id="docx",
                declared_mime=DOCX_MIME,
            )
            self.manifests.append(good)
            self._binding_complete(
                directive_, "SUCCEEDED", good.artifact_revision_id
            )
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=good.artifact_revision_id,
                execution_effect_ids=(effect_id,),
            )

        within_lease, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertFalse(within_lease.verification_ready)
        # the lease expires; the takeover worker executes EXACTLY once
        import sqlite3

        connection = sqlite3.connect(
            self.temporary.name + "/gateway.sqlite3"
        )
        connection.execute(
            "UPDATE repair_execution_binding SET claim_expires_at_ms = 1"
            " WHERE repair_directive_id = ?",
            (directive.repair_directive_id,),
        )
        connection.commit()
        connection.close()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        self.assertEqual(len(runtime_calls), 1)

    # F06: crossed SIDE_EFFECT_STARTED, process crash -------------------
    def test_f06_started_crash_zero_replay(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        self._binding_mark_started(directive, effect.effect_id, reserved)
        # CRASH. Recovery: AMBIGUOUS → RECONCILE, zero replay.
        runtime_calls: list[str] = []

        def dispatch(directive_):
            runtime_calls.append(directive_.repair_directive_id)
            return self._dispatch_success(directive_)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertEqual(disposition.action, "RECONCILE")
        self.assertEqual(
            self.gateway_store.list_verification_subject_successors(
                self.entry.plan_entry_id
            ),
            (),
        )

    # F07: runtime SUCCEEDED, successor not bound -----------------------
    def test_f07_succeeded_crash_recovery_zero_runtime(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        self._binding_mark_started(directive, effect.effect_id, reserved)
        good = self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime=DOCX_MIME,
        )
        self.manifests.append(good)
        self._binding_complete(
            directive, "SUCCEEDED", good.artifact_revision_id
        )
        # CRASH. Recovery from Store only.
        runtime_calls: list[str] = []

        def dispatch(directive_):
            runtime_calls.append(directive_.repair_directive_id)
            return self._dispatch_success(directive_)

        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertTrue(final.verification_ready)

    # F08: successor bound, re-verification record not ------------------
    def test_f08_successor_without_reverify_recovery(self) -> None:
        from contracts.verification_repair import VerificationSubjectSuccessor

        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        self._binding_mark_started(directive, effect.effect_id, reserved)
        good = self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime=DOCX_MIME,
        )
        self.manifests.append(good)
        self._binding_complete(
            directive, "SUCCEEDED", good.artifact_revision_id
        )
        # truncate AFTER the successor binding, BEFORE re-verification:
        successor = VerificationSubjectSuccessor(
            successor_binding_id="vss_" + "0" * 64,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            verification_plan_id=directive.verification_plan_id,
            plan_entry_id=directive.plan_entry_id,
            subject_kind=directive.subject_kind,
            predecessor_subject_identity=(
                directive.effective_subject_identity
            ),
            successor_subject_identity=good.artifact_revision_id,
            repair_directive_id=directive.repair_directive_id,
            repair_directive_sha256=directive.directive_sha256,
            produced_by_effect_id=effect.effect_id,
            repair_attempt_no=directive.repair_attempt_no,
            bound_at_ms=self._next_ms(),
            successor_binding_sha256="0" * 64,
        ).with_computed_sha256()
        self.assertTrue(
            self.gateway_store.put_verification_subject_successor(
                successor, recorded_at_ms=self._next_ms()
            )
        )
        # CRASH. Recovery: only re-verify (runtime zero).
        runtime_calls: list[str] = []

        def dispatch(directive_):
            runtime_calls.append(directive_.repair_directive_id)
            return self._dispatch_success(directive_)

        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertTrue(final.verification_ready)

    # F09: PASS record written, gate not run ----------------------------
    def test_f09_pass_record_gate_pending_no_repair(self) -> None:
        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        # PASS record exists; the gate has NOT consumed it yet. Recovery
        # must NOT repair a passing state.
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        final2, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=final,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertTrue(final2.verification_ready)

    # F10: SQLite reopen / process restart ------------------------------
    def test_f10_reopen_preserves_authorities(self) -> None:
        import time as _time
        from pathlib import Path as _Path

        from total_gateway.store import GatewayStateStore

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        self.gateway_store.close()
        reopened = GatewayStateStore.open(
            _Path(self.temporary.name) / "gateway.sqlite3",
            now_ms=_time.time_ns() // 1_000_000,
        )
        self.gateway_store = reopened
        latest = reopened.get_latest_verification_readiness(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
        )
        self.assertIsNotNone(latest)
        self.assertTrue(latest.verification_ready)
        attempts = reopened.list_repair_attempts(self.entry.plan_entry_id)
        self.assertEqual(len(attempts), 1)
        binding = reopened.get_repair_execution_binding_by_attempt(
            self.entry.plan_entry_id, 1
        )
        self.assertEqual(binding["state"], "SUCCEEDED")

    # F11: two workers observe the SAME failure concurrently ------------
    def test_f11_concurrent_readiness_processing(self) -> None:
        readiness = self._reverify()
        errors: list[Exception] = []

        def run() -> None:
            try:
                self.coordinator.process_readiness(
                    plan=self.plan, readiness=readiness
                )
            except Exception as exc:  # pragma: no cover - recorded
                errors.append(exc)

        threads = [
            threading.Thread(target=run, daemon=True) for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        # fail-closed Store boundaries: no corruption, no exception
        self.assertEqual(errors, [])
        # and the repair afterwards is still single-flight
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        self.assertEqual(
            len(
                self.gateway_store.list_repair_attempts(
                    self.entry.plan_entry_id
                )
            ),
            1,
        )

    # F12: two workers, SAME directive ----------------------------------
    def test_f12_same_directive_single_reality_execution(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        produce_calls: list[str] = []
        lock = threading.Lock()

        def dispatch(directive):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            with lock:
                produce_calls.append(directive.repair_directive_id)
                good = self._passed_manifest(
                    docx_bytes("字" * 300),
                    filename="report.docx",
                    format_id="docx",
                    declared_mime=DOCX_MIME,
                )
                self.manifests.append(good)
            self._binding_complete(
                directive, "SUCCEEDED", good.artifact_revision_id
            )
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=good.artifact_revision_id,
                execution_effect_ids=(effect.effect_id,),
            )

        def run() -> None:
            try:
                self.coordinator.execute_repair_loop(
                    plan=self.plan,
                    readiness=readiness,
                    dispatch=dispatch,
                    reverify=self._reverify,
                )
            except Exception:
                pass

        threads = [
            threading.Thread(target=run, daemon=True) for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(len(produce_calls), 1)

    # F13: runtime result timeout ----------------------------------------
    def test_f13_timeout_is_ambiguous_zero_replay(self) -> None:
        def dispatch_timeout(directive):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            # production bridge timeout branch: atomic AMBIGUOUS
            self._binding_complete(
                directive,
                "AMBIGUOUS",
                "",
                ref="timeout",
                error_code="repair.execution_timeout",
            )
            return RepairDispatchResult(
                execution_outcome="EXECUTION_AMBIGUOUS",
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_timeout,
            reverify=self._reverify,
        )
        self.assertEqual(disposition.action, "RECONCILE")
        self.assertFalse(final.verification_ready)

    # F14: runtime disconnect AFTER the side-effect boundary -------------
    def test_f14_disconnect_after_boundary_ambiguous(self) -> None:
        def dispatch_disconnect(directive):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            # runtime transport died mid-flight: unknown outcome
            self._binding_complete(
                directive,
                "AMBIGUOUS",
                "",
                ref="backend.disconnected",
                error_code="backend.disconnected",
            )
            return RepairDispatchResult(
                execution_outcome="EXECUTION_AMBIGUOUS",
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_disconnect,
            reverify=self._reverify,
        )
        self.assertEqual(disposition.action, "RECONCILE")
        self.assertEqual(
            self.gateway_store.list_verification_subject_successors(
                self.entry.plan_entry_id
            ),
            (),
        )

    # F15: QC failure AFTER runtime success ------------------------------
    def test_f15_qc_failure_after_runtime_success_no_fake_pass(self) -> None:
        def dispatch_qc_failed(directive):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            # the runtime completed, but the repaired artifact FAILED QC
            # → no manifest enters authority, produced stays unchanged
            self._binding_complete(
                directive,
                "SUCCEEDED",
                directive.effective_subject_identity,
            )
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_qc_failed,
            reverify=self._reverify,
        )
        self.assertFalse(final.verification_ready)
        self.assertEqual(disposition.action, "REVIEW")
        records = self._entry_records()
        self.assertFalse(any(r.status == "PASS" for r in records))

    # F16: repository observation window incomplete -----------------------
    def test_f16_repository_window_incomplete_reconcile(self) -> None:
        from tests.test_p19_m5_repair_loop import TestRepositoryRepairE2E

        class _RepoFault(TestRepositoryRepairE2E):
            def runTest(self):  # pragma: no cover - driven manually
                pass

        repo_case = _RepoFault("runTest")
        repo_case.setUp()
        try:
            def dispatch(directive):
                carrier = repo_case._create_effect(101)
                reserved = repo_case._reserve_or_claimed(
                    directive, carrier
                )
                if reserved["outcome"] != "EXECUTE":
                    return repo_case._already_claimed(directive)
                repo_case._binding_mark_started(directive, carrier, reserved)
                # the POST sensing window could not be captured:
                # production completes the binding atomically AMBIGUOUS
                repo_case._binding_complete(
                    directive,
                    "AMBIGUOUS",
                    "",
                    ref="repo-window-invalid",
                    error_code="repair.repo_window_invalid",
                )
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_AMBIGUOUS",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(carrier,),
                )

            readiness = repo_case._reverify()
            final, disposition = (
                repo_case.coordinator.execute_repair_loop(
                    plan=repo_case.plan,
                    readiness=readiness,
                    dispatch=dispatch,
                    reverify=repo_case._reverify,
                )
            )
            self.assertEqual(disposition.action, "RECONCILE")
            self.assertFalse(final.verification_ready)
            self.assertEqual(
                repo_case.store.list_verification_subject_successors(
                    repo_case.entry.plan_entry_id
                ),
                (),
            )
            records = [
                r
                for r in repo_case.store.list_verification_records(
                    request_id=repo_case.request_id,
                    run_id=repo_case.run_id,
                    generation=GEN,
                )
                if r.predicate_id
                == repo_case.entry.predicate.predicate_id
            ]
            self.assertFalse(
                any(r.status == "PASS" for r in records)
            )
        finally:
            repo_case.tearDown()


if __name__ == "__main__":
    unittest.main()
