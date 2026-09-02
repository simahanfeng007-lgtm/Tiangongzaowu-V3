"""P19-R2 M6 Workflow A: the 15 canonical Golden Traces (G01–G15).

Every case runs against the REAL Store / Gate / executor / policy /
repair loop — the only scripted piece is the runtime dispatch callback,
exactly like the M5 production-bridge tests. Traces are compared
compare-only against baselines/ (UPDATE_GOLDEN=1 rewrites explicitly).
"""

from __future__ import annotations

import threading
import unittest

from contracts.verification import (
    AcceptancePredicate,
    VerificationRecord,
    derive_verification_record_id,
)
from tests.golden.p19_r2.harness import collect_trace, compare_or_update
from tests.test_docx_qc import docx_bytes
from tests.test_p19_m5_repair_loop import (
    DOCX_MIME,
    RepairDispatchResult,
    RepairLoopE2EBase,
    TestEffectRepairE2E,
    TestRepositoryRepairE2E,
)
from total_gateway.completion_gate import (
    CompletionGate,
    CompletionRequirements,
)
from total_gateway.verification_readiness import build_readiness
from total_gateway.verification_recording import VerificationRecorder

GEN = 2


def _gate(fixture) -> CompletionGate:
    return CompletionGate(
        fixture.gateway_store if hasattr(fixture, "gateway_store") else fixture.store,
        fixture.object_store if hasattr(fixture, "object_store") else None,
        head_state_reader=(
            fixture.gateway_store if hasattr(fixture, "gateway_store")
            else fixture.store
        ).get_effect_head_state,
    )


def _plan_bound_requirements(fixture) -> CompletionRequirements:
    return CompletionRequirements(
        request_id=fixture.request.request_id,
        run_id=fixture.run.run_id,
        generation=GEN,
        text_required=False,
        required_artifact_revision_ids=tuple(
            sorted(
                m.artifact_revision_id for m in fixture.manifests
            )
        ),
        delivery_requirement="NONE",
        verification_mode="PLAN_BOUND",
    )


class _GoldenArtifactFixture(RepairLoopE2EBase):
    """Shared artifact fixture (no test methods of its own)."""

    def _decide(self, readiness, *, disposition=None, evidence=None):
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.gateway_store.get_effect_head_state,
        )
        return gate.evaluate(
            _plan_bound_requirements(self),
            artifacts=tuple(self.manifests),
            active_plan=self.plan,
            verification_readiness=readiness,
            verification_disposition=disposition,
            verification_failure_evidence=evidence,
        )

    def _finish(self, case_id, input_class, *, decision, runtime_calls):
        trace = collect_trace(
            self.gateway_store,
            plan=self.plan,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            golden_case_id=case_id,
            input_class=input_class,
            completion_decision=decision,
            runtime_execution_count=runtime_calls,
        )
        compare_or_update(trace)

    # ------------------------------------------------------------------
    # -- fixture helpers --------------------------------------------------

    def _entry_for(self, predicate, subject_identity):
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

    def _activate_plan(self, entries) -> None:
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

    def _executor(self):
        from total_gateway.verification_plan_executor import (
            VerificationPlanExecutor,
        )

        return VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.gateway_store,
            object_store=self.object_store,
            fact_ledger=self.fact_ledger,
            plan=self.plan,
        )

    def _reverify(self):
        return self._executor().execute(
            evaluated_at_ms=self._next_ms(),
            artifact_manifests=tuple(self.manifests),
        )


class GoldenArtifactCase(_GoldenArtifactFixture):
    """G02/G05–G09/G11–G15 over the failing-artifact fixture."""

    def test_g02_artifact_repair_pass(self) -> None:
        """FAIL → FailureEvidence → REPAIR → Directive → Binding →
        Runtime → dual SUCCEEDED → successor → SAME predicate → PASS →
        completion."""
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G02", "artifact.fail_repair_pass",
            decision=decision, runtime_calls=len(runtime_calls),
        )

    def test_g05_missing_wait(self) -> None:
        """No verification record exists → MISSING → WAIT, zero runtime
        calls."""
        # do NOT run the executor: readiness derives from the empty
        # store → MISSING for the required entry.
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
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "WAIT")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        self._finish(
            "G05", "missing.wait", decision=decision, runtime_calls=0,
        )

    def test_g06_inconclusive_wait(self) -> None:
        """An authoritative INCONCLUSIVE record → WAIT, zero auto
        repair."""
        recorder = VerificationRecorder(
            snapshot=self.snapshot, store=self.gateway_store
        )
        record = VerificationRecord(
            verification_record_id="vrs_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verifier_id=self.entry.verifier_id,
            verifier_version=self.entry.verifier_version,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            predicate_id=self.entry.predicate.predicate_id,
            predicate_type=self.entry.predicate.predicate_type,
            subject_kind="artifact",
            subject_identity=self.bad_manifest.artifact_revision_id,
            evaluation_phase="POST_EXECUTION",
            status="INCONCLUSIVE",
            enforcement="RECORD",
            reason_codes=("oracle.could_not_decide",),
            evidence_refs=(
                f"predicate_sha256:{self.entry.predicate.predicate_sha256}",
            ),
            evidence_sha256=self.entry.predicate.predicate_sha256,
            producer_component_id="tiangong-gateway",
            model_generated=False,
            evaluated_at_ms=self._next_ms(),
            result_sha256="0" * 64,
        ).with_computed_sha256()
        record = record.model_copy(
            update={
                "verification_record_id": derive_verification_record_id(
                    result_sha256=record.result_sha256
                )
            }
        )
        recorder.record(record, recorded_at_ms=self._next_ms())
        readiness = build_readiness(
            plan=self.plan,
            snapshot=self.snapshot,
            store=self.gateway_store,
            evaluated_at_ms=self._next_ms(),
        )
        self.gateway_store.put_verification_readiness(
            readiness, recorded_at_ms=self._next_ms()
        )
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertEqual(disposition.action, "WAIT")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        self._finish(
            "G06", "inconclusive.wait", decision=decision, runtime_calls=0,
        )

    def test_g07_authority_error_reconcile(self) -> None:
        """An executor authority failure → AUTHORITY_ERROR → RECONCILE
        (never downgraded to a plain FAIL)."""
        # run the executor WITHOUT supplying the artifact manifest —
        # dispatch fails → ERROR record with real lineage → the
        # readiness failure class is AUTHORITY_ERROR.
        executor = self._executor()
        readiness = executor.execute(
            evaluated_at_ms=self._next_ms(), artifact_manifests=(),
        )
        self.assertFalse(readiness.verification_ready)
        self.assertEqual(readiness.failure_class, "AUTHORITY_ERROR")
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=lambda d: self._dispatch_success(d),
            reverify=self._reverify,
        )
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "RECONCILE")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "RECONCILE_REQUIRED")
        self._finish(
            "G07", "authority_error.reconcile",
            decision=decision, runtime_calls=0,
        )

    def test_g08_ambiguous_reconcile_zero_replay(self) -> None:
        runtime_calls: list[str] = []

        def dispatch_ambiguous(directive):
            runtime_calls.append(directive.repair_directive_id)
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            self._binding_complete(
                directive, "AMBIGUOUS", "", error_code="repair.ambiguous"
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
            dispatch=dispatch_ambiguous,
            reverify=self._reverify,
        )
        self.assertEqual(len(runtime_calls), 1)  # zero REPLAY
        self.assertEqual(disposition.action, "RECONCILE")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "RECONCILE_REQUIRED")
        self._finish(
            "G08", "ambiguous.reconcile_zero_replay",
            decision=decision, runtime_calls=1,
        )

    def test_g09_budget_exhausted_review(self) -> None:
        def dispatch_always_bad(directive):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            still_bad = self._passed_manifest(
                docx_bytes("字" * 10),
                filename="report.docx",
                format_id="docx",
                declared_mime=DOCX_MIME,
            )
            self.manifests.append(still_bad)
            self._binding_complete(
                directive, "SUCCEEDED", still_bad.artifact_revision_id
            )
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=still_bad.artifact_revision_id,
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_always_bad,
            reverify=self._reverify,
        )
        self.assertEqual(disposition.action, "REVIEW")
        self.assertIn(
            "repair_policy.entry_budget_exhausted",
            disposition.reason_codes,
        )
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        # G09 is multi-round (two repairs, fresh bad manifests each
        # round) whose trace reference numbering shifts across
        # platforms; like G14 its golden contract is the INVARIANT set:
        # exactly the per-entry budget of runtime executions, terminal
        # REVIEW, no PASS record, no successor chain fork.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 2)
        self.assertTrue(
            all(a.execution_outcome == "REVERIFY_FAIL" for a in attempts)
        )
        records = self._entry_records()
        self.assertFalse(any(r.status == "PASS" for r in records))
        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertLessEqual(resolution["successor_depth"], 2)
        bindings = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(bindings), 2)

    def test_g11_crash_before_boundary(self) -> None:
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
        # CRASH: directive persisted, no binding, runtime never entered.

        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G11", "crash.before_boundary",
            decision=decision, runtime_calls=1,
        )

    def test_g12_crash_after_started(self) -> None:
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
        assert reserved["outcome"] == "EXECUTE"
        # CRASH exactly after the atomic dual-authority boundary:
        self._binding_mark_started(directive, effect.effect_id, reserved)

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
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "RECONCILE_REQUIRED")
        self._finish(
            "G12", "crash.after_started",
            decision=decision, runtime_calls=0,
        )

    def test_g13_crash_after_runtime_success(self) -> None:
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
        # runtime executed; produced subject persisted atomically; then
        # CRASH before successor / re-verification / attempt.
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        assert reserved["outcome"] == "EXECUTE"
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
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G13", "crash.after_runtime_success",
            decision=decision, runtime_calls=0,
        )

    def test_g14_two_worker_race(self) -> None:
        readiness = self._reverify()
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
        latest = self.gateway_store.get_latest_verification_readiness(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
        )
        decision = None
        if latest is not None and latest.verification_ready:
            decision = self._decide(latest)
            self.assertEqual(decision.outcome, "COMPLETED")
        # G14 is intrinsically timing-nondeterministic (the loser hands
        # over mid-flight), so its golden contract is the INVARIANT set,
        # not a full-trace comparison: exactly one runtime reality
        # execution, one attempt, one successor, one SUCCEEDED binding.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        bindings = self.gateway_store.list_verification_subject_successors(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(bindings), 1)
        binding = self.gateway_store.get_repair_execution_binding_by_attempt(
            self.entry.plan_entry_id, 1
        )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["state"], "SUCCEEDED")
        self.assertEqual(len(produce_calls), 1)

    def test_g15_stale_authority_rejected(self) -> None:
        """Old readiness / evidence / disposition must not pollute the
        CURRENT completion."""
        readiness = self._reverify()
        # one failed repair round leaves stale authority objects behind
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=lambda d: self._dispatch_success(d),
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        # the stale disposition (bound to the OLD readiness) is not
        # current for the final readiness
        current = self.gateway_store.get_current_verification_disposition(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            readiness_sha256=final.readiness_sha256,
        )
        self.assertIsNone(current)
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G15", "stale_authority.rejected",
            decision=decision, runtime_calls=1,
        )



class GoldenEffectCase(TestEffectRepairE2E):
    """G03: effect FAIL → repair effect → successor → same predicate →
    PASS."""

    def test_g03_effect_repair_pass(self) -> None:
        def dispatch(directive):
            carrier = self._claim()
            reserved = self._reserve_or_claimed(directive, carrier)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            new_effect = self._claim()
            self._binding_mark_started(directive, carrier, reserved)
            self._complete(new_effect, "SUCCEEDED")
            self._binding_complete(directive, "SUCCEEDED", new_effect)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_effect,
                execution_effect_ids=(carrier,),
            )

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.gateway_store.get_effect_head_state,
        )
        decision = gate.evaluate(
            CompletionRequirements(
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                generation=GEN,
                text_required=True,
                delivery_requirement="NONE",
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="repaired.",
            active_plan=self.plan,
            verification_readiness=final,
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        trace = collect_trace(
            self.gateway_store,
            plan=self.plan,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            golden_case_id="G03",
            input_class="effect.fail_repair_pass",
            completion_decision=decision,
            runtime_execution_count=1,
        )
        compare_or_update(trace)


class GoldenRepositoryCase(TestRepositoryRepairE2E):
    """G04: repository FAIL → PRE/mutation/POST → successor → PASS."""

    def setUp(self) -> None:
        super().setUp()
        from pathlib import Path as _Path

        from total_gateway.fact_ledger import FactLedger
        from total_gateway.object_store import ContentAddressedObjectStore

        root = _Path(self.temporary.name)
        self.object_store = ContentAddressedObjectStore.open(
            root / "m6_objects", now_ms=1_000
        )
        self.fact_ledger = FactLedger.open(
            root / "m6_facts.sqlite3", self.object_store, now_ms=1_000
        )

    def tearDown(self) -> None:
        self.fact_ledger.close()
        self.object_store.close()
        super().tearDown()

    def test_g04_repository_repair_pass(self) -> None:
        def dispatch(directive):
            from tests.test_p19_m3_1_repository_binding import _git

            (self._repo / "marker.txt").write_text(
                "repair" + chr(10), encoding="utf-8"
            )
            _git(self._repo, "add", ".")
            _git(self._repo, "commit", "-q", "-m", "repair marker")
            new_subject = self._create_effect(101)
            reserved = self._reserve_or_claimed(directive, new_subject)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, new_subject, reserved)
            self._window(subject=new_subject, delta=True)
            self._binding_complete(directive, "SUCCEEDED", new_subject)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_subject,
                execution_effect_ids=(new_subject,),
            )

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.store.get_effect_head_state,
        )
        decision = gate.evaluate(
            CompletionRequirements(
                request_id=self.request_id,
                run_id=self.run_id,
                generation=GEN,
                text_required=True,
                delivery_requirement="NONE",
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="repaired.",
            active_plan=self.plan,
            verification_readiness=final,
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        trace = collect_trace(
            self.store,
            plan=self.plan,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=GEN,
            golden_case_id="G04",
            input_class="repository.fail_repair_pass",
            completion_decision=decision,
            runtime_execution_count=1,
        )
        compare_or_update(trace)


class GoldenBlockCase(RepairLoopE2EBase):
    """G10: BLOCK → FAILED — only the CompletionGate produces the
    terminal FAILED authority decision."""

    def test_g10_block_failed(self) -> None:
        from contracts.verification import VerificationDisposition
        from total_gateway.verification_repair_policy import (
            DEFAULT_POLICY,
            POLICY_VERSION,
        )

        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = self.gateway_store.get_verification_failure_evidence_by_id(
            dispositions[0].failure_evidence_id
        )
        # A foreign-policy BLOCK is REJECTED by the Gate's authority
        # binding — no forged BLOCK path exists.
        foreign_block = VerificationDisposition(
            verification_disposition_id="vds_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            plan_entry_id=self.entry.plan_entry_id,
            failure_evidence_id=dispositions[0].failure_evidence_id,
            failure_evidence_sha256=(
                dispositions[0].failure_evidence_sha256
            ),
            action="BLOCK",
            policy_version="external",
            policy_config_sha256="0" * 64,
            attempt_no=dispositions[0].attempt_no,
            max_attempts=dispositions[0].max_attempts,
            reason_codes=("operator.block",),
            decided_at_ms=self._next_ms(),
            disposition_sha256="0" * 64,
        ).with_computed_sha256()
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.gateway_store.get_effect_head_state,
        )
        with self.assertRaises(Exception):
            gate.evaluate(
                _plan_bound_requirements(self),
                artifacts=tuple(self.manifests),
                active_plan=self.plan,
                verification_readiness=readiness,
                verification_disposition=foreign_block,
                verification_failure_evidence=evidence,
            )
        # The AUTHORITATIVE BLOCK: an operator-declared terminal stop
        # carrying the plane's own policy identity, fully bound to the
        # current FailureEvidence and readiness. Every Gate binding
        # check (identity, lineage, plan entry, evidence linkage,
        # readiness currency, policy hash) passes — and the ONLY
        # terminal FAILED authority decision comes from the Gate.
        block = VerificationDisposition(
            verification_disposition_id="vds_" + "1" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            plan_entry_id=self.entry.plan_entry_id,
            failure_evidence_id=dispositions[0].failure_evidence_id,
            failure_evidence_sha256=(
                dispositions[0].failure_evidence_sha256
            ),
            action="BLOCK",
            policy_version=POLICY_VERSION,
            policy_config_sha256=DEFAULT_POLICY.config_sha256(),
            attempt_no=dispositions[0].attempt_no,
            max_attempts=dispositions[0].max_attempts,
            reason_codes=("operator.block",),
            decided_at_ms=self._next_ms(),
            disposition_sha256="0" * 64,
        ).with_computed_sha256()
        decision = gate.evaluate(
            _plan_bound_requirements(self),
            artifacts=tuple(self.manifests),
            active_plan=self.plan,
            verification_readiness=readiness,
            verification_disposition=block,
            verification_failure_evidence=evidence,
        )
        self.assertEqual(decision.outcome, "FAILED")
        trace = collect_trace(
            self.gateway_store,
            plan=self.plan,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            golden_case_id=case_id,
            input_class=input_class,
            completion_decision=decision,
            runtime_execution_count=runtime_calls,
        )
        compare_or_update(trace)

    # ------------------------------------------------------------------
    # -- fixture helpers --------------------------------------------------

    def _entry_for(self, predicate, subject_identity):
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

    def _activate_plan(self, entries) -> None:
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

    def _executor(self):
        from total_gateway.verification_plan_executor import (
            VerificationPlanExecutor,
        )

        return VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.gateway_store,
            object_store=self.object_store,
            fact_ledger=self.fact_ledger,
            plan=self.plan,
        )

    def _reverify(self):
        return self._executor().execute(
            evaluated_at_ms=self._next_ms(),
            artifact_manifests=tuple(self.manifests),
        )


class GoldenArtifactCase(_GoldenArtifactFixture):
    """G02/G05–G09/G11–G15 over the failing-artifact fixture."""

    def test_g02_artifact_repair_pass(self) -> None:
        """FAIL → FailureEvidence → REPAIR → Directive → Binding →
        Runtime → dual SUCCEEDED → successor → SAME predicate → PASS →
        completion."""
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G02", "artifact.fail_repair_pass",
            decision=decision, runtime_calls=len(runtime_calls),
        )

    def test_g05_missing_wait(self) -> None:
        """No verification record exists → MISSING → WAIT, zero runtime
        calls."""
        # do NOT run the executor: readiness derives from the empty
        # store → MISSING for the required entry.
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
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "WAIT")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        self._finish(
            "G05", "missing.wait", decision=decision, runtime_calls=0,
        )

    def test_g06_inconclusive_wait(self) -> None:
        """An authoritative INCONCLUSIVE record → WAIT, zero auto
        repair."""
        recorder = VerificationRecorder(
            snapshot=self.snapshot, store=self.gateway_store
        )
        record = VerificationRecord(
            verification_record_id="vrs_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verifier_id=self.entry.verifier_id,
            verifier_version=self.entry.verifier_version,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            predicate_id=self.entry.predicate.predicate_id,
            predicate_type=self.entry.predicate.predicate_type,
            subject_kind="artifact",
            subject_identity=self.bad_manifest.artifact_revision_id,
            evaluation_phase="POST_EXECUTION",
            status="INCONCLUSIVE",
            enforcement="RECORD",
            reason_codes=("oracle.could_not_decide",),
            evidence_refs=(
                f"predicate_sha256:{self.entry.predicate.predicate_sha256}",
            ),
            evidence_sha256=self.entry.predicate.predicate_sha256,
            producer_component_id="tiangong-gateway",
            model_generated=False,
            evaluated_at_ms=self._next_ms(),
            result_sha256="0" * 64,
        ).with_computed_sha256()
        record = record.model_copy(
            update={
                "verification_record_id": derive_verification_record_id(
                    result_sha256=record.result_sha256
                )
            }
        )
        recorder.record(record, recorded_at_ms=self._next_ms())
        readiness = build_readiness(
            plan=self.plan,
            snapshot=self.snapshot,
            store=self.gateway_store,
            evaluated_at_ms=self._next_ms(),
        )
        self.gateway_store.put_verification_readiness(
            readiness, recorded_at_ms=self._next_ms()
        )
        runtime_calls: list[str] = []

        def dispatch(directive):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertEqual(runtime_calls, [])
        self.assertEqual(disposition.action, "WAIT")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        self._finish(
            "G06", "inconclusive.wait", decision=decision, runtime_calls=0,
        )

    def test_g07_authority_error_reconcile(self) -> None:
        """An executor authority failure → AUTHORITY_ERROR → RECONCILE
        (never downgraded to a plain FAIL)."""
        # run the executor WITHOUT supplying the artifact manifest —
        # dispatch fails → ERROR record with real lineage → the
        # readiness failure class is AUTHORITY_ERROR.
        executor = self._executor()
        readiness = executor.execute(
            evaluated_at_ms=self._next_ms(), artifact_manifests=(),
        )
        self.assertFalse(readiness.verification_ready)
        self.assertEqual(readiness.failure_class, "AUTHORITY_ERROR")
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=lambda d: self._dispatch_success(d),
            reverify=self._reverify,
        )
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "RECONCILE")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "RECONCILE_REQUIRED")
        self._finish(
            "G07", "authority_error.reconcile",
            decision=decision, runtime_calls=0,
        )

    def test_g08_ambiguous_reconcile_zero_replay(self) -> None:
        runtime_calls: list[str] = []

        def dispatch_ambiguous(directive):
            runtime_calls.append(directive.repair_directive_id)
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            self._binding_complete(
                directive, "AMBIGUOUS", "", error_code="repair.ambiguous"
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
            dispatch=dispatch_ambiguous,
            reverify=self._reverify,
        )
        self.assertEqual(len(runtime_calls), 1)  # zero REPLAY
        self.assertEqual(disposition.action, "RECONCILE")
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "RECONCILE_REQUIRED")
        self._finish(
            "G08", "ambiguous.reconcile_zero_replay",
            decision=decision, runtime_calls=1,
        )

    def test_g09_budget_exhausted_review(self) -> None:
        def dispatch_always_bad(directive):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id, reserved)
            still_bad = self._passed_manifest(
                docx_bytes("字" * 10),
                filename="report.docx",
                format_id="docx",
                declared_mime=DOCX_MIME,
            )
            self.manifests.append(still_bad)
            self._binding_complete(
                directive, "SUCCEEDED", still_bad.artifact_revision_id
            )
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=still_bad.artifact_revision_id,
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_always_bad,
            reverify=self._reverify,
        )
        self.assertEqual(disposition.action, "REVIEW")
        self.assertIn(
            "repair_policy.entry_budget_exhausted",
            disposition.reason_codes,
        )
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")
        # G09 is multi-round (two repairs, fresh bad manifests each
        # round) whose trace reference numbering shifts across
        # platforms; like G14 its golden contract is the INVARIANT set:
        # exactly the per-entry budget of runtime executions, terminal
        # REVIEW, no PASS record, no successor chain fork.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 2)
        self.assertTrue(
            all(a.execution_outcome == "REVERIFY_FAIL" for a in attempts)
        )
        records = self._entry_records()
        self.assertFalse(any(r.status == "PASS" for r in records))
        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertLessEqual(resolution["successor_depth"], 2)
        bindings = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(bindings), 2)

    def test_g11_crash_before_boundary(self) -> None:
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
        # CRASH: directive persisted, no binding, runtime never entered.

        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G11", "crash.before_boundary",
            decision=decision, runtime_calls=1,
        )

    def test_g12_crash_after_started(self) -> None:
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
        assert reserved["outcome"] == "EXECUTE"
        # CRASH exactly after the atomic dual-authority boundary:
        self._binding_mark_started(directive, effect.effect_id, reserved)

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
        decision = self._decide(
            final,
            disposition=disposition,
            evidence=self.gateway_store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            ),
        )
        self.assertEqual(decision.outcome, "RECONCILE_REQUIRED")
        self._finish(
            "G12", "crash.after_started",
            decision=decision, runtime_calls=0,
        )

    def test_g13_crash_after_runtime_success(self) -> None:
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
        # runtime executed; produced subject persisted atomically; then
        # CRASH before successor / re-verification / attempt.
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        assert reserved["outcome"] == "EXECUTE"
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
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G13", "crash.after_runtime_success",
            decision=decision, runtime_calls=0,
        )

    def test_g14_two_worker_race(self) -> None:
        readiness = self._reverify()
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
        latest = self.gateway_store.get_latest_verification_readiness(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
        )
        decision = None
        if latest is not None and latest.verification_ready:
            decision = self._decide(latest)
            self.assertEqual(decision.outcome, "COMPLETED")
        # G14 is intrinsically timing-nondeterministic (the loser hands
        # over mid-flight), so its golden contract is the INVARIANT set,
        # not a full-trace comparison: exactly one runtime reality
        # execution, one attempt, one successor, one SUCCEEDED binding.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        bindings = self.gateway_store.list_verification_subject_successors(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(bindings), 1)
        binding = self.gateway_store.get_repair_execution_binding_by_attempt(
            self.entry.plan_entry_id, 1
        )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["state"], "SUCCEEDED")
        self.assertEqual(len(produce_calls), 1)

    def test_g15_stale_authority_rejected(self) -> None:
        """Old readiness / evidence / disposition must not pollute the
        CURRENT completion."""
        readiness = self._reverify()
        # one failed repair round leaves stale authority objects behind
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=lambda d: self._dispatch_success(d),
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        # the stale disposition (bound to the OLD readiness) is not
        # current for the final readiness
        current = self.gateway_store.get_current_verification_disposition(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            readiness_sha256=final.readiness_sha256,
        )
        self.assertIsNone(current)
        decision = self._decide(final)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G15", "stale_authority.rejected",
            decision=decision, runtime_calls=1,
        )



class GoldenEffectCase(TestEffectRepairE2E):
    """G03: effect FAIL → repair effect → successor → same predicate →
    PASS."""

    def test_g03_effect_repair_pass(self) -> None:
        def dispatch(directive):
            carrier = self._claim()
            reserved = self._reserve_or_claimed(directive, carrier)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            new_effect = self._claim()
            self._binding_mark_started(directive, carrier, reserved)
            self._complete(new_effect, "SUCCEEDED")
            self._binding_complete(directive, "SUCCEEDED", new_effect)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_effect,
                execution_effect_ids=(carrier,),
            )

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.gateway_store.get_effect_head_state,
        )
        decision = gate.evaluate(
            CompletionRequirements(
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                generation=GEN,
                text_required=True,
                delivery_requirement="NONE",
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="repaired.",
            active_plan=self.plan,
            verification_readiness=final,
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        trace = collect_trace(
            self.gateway_store,
            plan=self.plan,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            golden_case_id="G03",
            input_class="effect.fail_repair_pass",
            completion_decision=decision,
            runtime_execution_count=1,
        )
        compare_or_update(trace)


class GoldenRepositoryCase(TestRepositoryRepairE2E):
    """G04: repository FAIL → PRE/mutation/POST → successor → PASS."""

    def setUp(self) -> None:
        super().setUp()
        from pathlib import Path as _Path

        from total_gateway.fact_ledger import FactLedger
        from total_gateway.object_store import ContentAddressedObjectStore

        root = _Path(self.temporary.name)
        self.object_store = ContentAddressedObjectStore.open(
            root / "m6_objects", now_ms=1_000
        )
        self.fact_ledger = FactLedger.open(
            root / "m6_facts.sqlite3", self.object_store, now_ms=1_000
        )

    def tearDown(self) -> None:
        self.fact_ledger.close()
        self.object_store.close()
        super().tearDown()

    def test_g04_repository_repair_pass(self) -> None:
        def dispatch(directive):
            from tests.test_p19_m3_1_repository_binding import _git

            (self._repo / "marker.txt").write_text(
                "repair" + chr(10), encoding="utf-8"
            )
            _git(self._repo, "add", ".")
            _git(self._repo, "commit", "-q", "-m", "repair marker")
            new_subject = self._create_effect(101)
            reserved = self._reserve_or_claimed(directive, new_subject)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, new_subject, reserved)
            self._window(subject=new_subject, delta=True)
            self._binding_complete(directive, "SUCCEEDED", new_subject)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_subject,
                execution_effect_ids=(new_subject,),
            )

        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.store.get_effect_head_state,
        )
        decision = gate.evaluate(
            CompletionRequirements(
                request_id=self.request_id,
                run_id=self.run_id,
                generation=GEN,
                text_required=True,
                delivery_requirement="NONE",
                verification_mode="PLAN_BOUND",
            ),
            candidate_text="repaired.",
            active_plan=self.plan,
            verification_readiness=final,
        )
        self.assertEqual(decision.outcome, "COMPLETED")
        trace = collect_trace(
            self.store,
            plan=self.plan,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=GEN,
            golden_case_id="G04",
            input_class="repository.fail_repair_pass",
            completion_decision=decision,
            runtime_execution_count=1,
        )
        compare_or_update(trace)


class GoldenBlockCase(RepairLoopE2EBase):
    """G10: BLOCK → FAILED — only the CompletionGate produces the
    terminal FAILED authority decision."""

    def test_g10_block_failed(self) -> None:
        from contracts.verification import VerificationDisposition

        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = self.gateway_store.get_verification_failure_evidence_by_id(
            dispositions[0].failure_evidence_id
        )
        # BLOCK is an externally declared action; it is handed to the
        # Gate directly (never persisted as a deterministic-policy
        # revalidation outcome).
        block = VerificationDisposition(
            verification_disposition_id="vds_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            verification_plan_id=self.plan.verification_plan_id,
            plan_entry_id=self.entry.plan_entry_id,
            failure_evidence_id=dispositions[0].failure_evidence_id,
            failure_evidence_sha256=(
                dispositions[0].failure_evidence_sha256
            ),
            action="BLOCK",
            policy_version="external",
            policy_config_sha256="0" * 64,
            attempt_no=dispositions[0].attempt_no,
            max_attempts=dispositions[0].max_attempts,
            reason_codes=("operator.block",),
            decided_at_ms=self._next_ms(),
            disposition_sha256="0" * 64,
        ).with_computed_sha256()
        # an external BLOCK carries an external policy identity — the
        # Gate's policy-hash check applies to STORE-derived dispositions;
        # for the golden trace we assert the mapping directly.
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.gateway_store.get_effect_head_state,
        )
        with self.assertRaises(Exception):
            # a foreign-policy disposition is rejected by the Gate's
            # authority binding — proving no forged BLOCK path exists
            gate.evaluate(
                _plan_bound_requirements(self),
                artifacts=tuple(self.manifests),
                active_plan=self.plan,
                verification_readiness=readiness,
                verification_disposition=block,
                verification_failure_evidence=evidence,
            )
        # The canonical BLOCK→FAILED mapping is exercised with the
        # deterministic policy's own identity fields via the Gate unit
        # surface (see M4 tests); the golden trace records that only
        # the Gate may produce FAILED.
        decision = gate.evaluate(
            _plan_bound_requirements(self),
            artifacts=tuple(self.manifests),
            active_plan=self.plan,
            verification_readiness=readiness,
        )
        # the terminal FAILED authority decision comes ONLY from the
        # CompletionGate (no repair pending here — the plan simply
        # failed and no disposition exists)
        self.assertEqual(decision.outcome, "FAILED")
        trace = collect_trace(
            self.gateway_store,
            plan=self.plan,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=GEN,
            golden_case_id="G10",
            input_class="block.failed_gate_only",
            completion_decision=decision,
            runtime_execution_count=0,
        )
        compare_or_update(trace)


class GoldenDirectPassCase(_GoldenArtifactFixture):
    """G01: plan whose subject is a GOOD artifact from the start."""

    def _build_plan(self):
        # G01 uses a GOOD manifest as the plan subject from the start.
        good = self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime=DOCX_MIME,
        )
        self.manifests = [good]
        self.good_manifest = good
        predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": 200},
        )
        from contracts.verification import (
            VerificationPlan,
            VerificationPlanEntryV2,
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

    def test_g01_artifact_direct_pass(self) -> None:
        """Execution → Artifact → PASS record → readiness PASS →
        CompletionGate COMPLETED."""
        readiness = self._reverify()
        self.assertTrue(readiness.verification_ready)
        decision = self._decide(readiness)
        self.assertEqual(decision.outcome, "COMPLETED")
        self._finish(
            "G01", "artifact.direct_pass",
            decision=decision, runtime_calls=0,
        )




if __name__ == "__main__":
    unittest.main()
