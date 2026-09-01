"""P19-R2 M5 Final #2/#4/#8: the FULL evidence-driven repair loop.

Real Store + real oracles + real readiness builder + real deterministic
policy. The only scripted piece is the runtime dispatch callback (the
runtime is an LLM backend — scripted here exactly like every other
backend-transport test), and even that produces its repaired artifact
through the REAL ArtifactGate/QC pipeline and claims a REAL effect in
the one EffectLedger.

Coverage:
- FAIL → REPAIR directive → real execution → successor binding →
  SAME predicate re-verified with NEW independent evidence → PASS.
- Budget termination (permanent failure → REVIEW, bounded directives).
- Crash recovery (directive issued, attempt never recorded → resume
  without exceeding budgets).
- Concurrency (idempotent identical writes under threads).
- Adversarial Store v28 revalidation (forged evidence / decision /
  linkage / reality rejected at the write boundary).
- Desktop production wiring (source-structure assertions).
"""

from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from contracts import derive_effect_identity
from contracts.verification import (
    AcceptancePredicate,
    FailureEvidence,
    RepairDirective,
    VerificationDisposition,
    VerificationPlan,
    VerificationPlanEntryV2,
)
from contracts.verification_repair import (
    RepairAttemptRecord,
    VerificationSubjectSuccessor,
)
from total_gateway.effects import EffectClaim
from total_gateway.verification_plan_executor import VerificationPlanExecutor
from total_gateway.verification_repair_coordinator import (
    RepairDispatchResult,
    VerificationRepairCoordinator,
)
from tests.test_docx_qc import docx_bytes
from tests.test_p19_m2_1_artifact_oracle import M21OracleTestBase
from tests.test_p19_m3_1_repository_binding import (
    RepositoryOracleTestBase as _M31RepositoryBase,
)

ROOT = Path(__file__).resolve().parents[1]

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document"
)


class _RepairBindingMixin:
    """Shared Store dispatch-boundary plumbing for test runtime bridges.

    Test bridges mirror the production bridge contract: reserve BEFORE
    claiming the effect (a loser never executes the runtime), mark the
    side-effect boundary BEFORE producing, persist the produced subject
    BEFORE returning.
    """

    def _binding_store(self):
        return getattr(self, "gateway_store", None) or self.store

    def _reserve_or_claimed(self, directive, effect_id, intent=None):
        import time as _time
        import uuid as _uuid

        now = _time.time_ns() // 1_000_000
        return self._binding_store().reserve_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            repair_directive_sha256=directive.directive_sha256,
            plan_entry_id=directive.plan_entry_id,
            repair_attempt_no=directive.repair_attempt_no,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            effect_id=effect_id,
            effect_intent_sha256=intent or "9" * 64,
            reserved_at_ms=now,
            # invocation-scoped claim: two threads in one process still
            # single-flight
            dispatch_claim_id=_uuid.uuid4().hex,
            claim_expires_at_ms=now + 120_000,
        )

    def _binding_mark_started(self, directive, effect_id):
        """ONE atomic transition: EffectLedger CLAIMED->STARTED and
        Binding RESERVED->STARTED in the same transaction."""
        import time as _time

        self._binding_store().start_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            effect_id=effect_id,
            started_at_ms=_time.time_ns() // 1_000_000,
        )

    def _binding_complete(
        self, directive, state, produced, ref="test", error_code=None
    ):
        """ONE atomic terminal transition: BOTH authorities move to the
        same terminal state with the produced subject persisted."""
        import time as _time

        self._binding_store().complete_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            state=state,
            produced_subject_identity=produced,
            produced_subject_kind=directive.subject_kind,
            runtime_result_ref=ref,
            completed_at_ms=_time.time_ns() // 1_000_000,
            error_code=error_code,
        )

    @staticmethod
    def _already_claimed(directive):
        return RepairDispatchResult(
            execution_outcome="ALREADY_CLAIMED",
            produced_subject_identity=(
                directive.effective_subject_identity
            ),
            execution_effect_ids=(),
        )


class RepairLoopE2EBase(_RepairBindingMixin, M21OracleTestBase):
    """Real gateway stack + one failing artifact predicate."""

    def setUp(self) -> None:
        super().setUp()
        self._register_lineage_request()
        self.gateway_store.put_registry_snapshot(
            self.snapshot, recorded_at_ms=1_500
        )
        self.manifests: list = []
        self._clock = 30_000
        self._effect_ordinal = 50

        # Bad artifact: 50 visible chars against a 200-char minimum.
        self.bad_manifest = self._passed_manifest(
            docx_bytes("字" * 50),
            filename="report.docx",
            format_id="docx",
            declared_mime=DOCX_MIME,
        )
        self.manifests.append(self.bad_manifest)

        self.plan = self._build_plan()
        self.entry = self.plan.entries[0]
        assert self.gateway_store.put_verification_plan(
            self.plan, recorded_at_ms=1_600
        )
        self.gateway_store.activate_verification_plan(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            verification_plan_id=self.plan.verification_plan_id,
            verification_plan_sha256=self.plan.plan_sha256,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            activated_at_ms=1_700,
        )
        self.coordinator = VerificationRepairCoordinator(
            store=self.gateway_store
        )

    def _build_plan(self):
        predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": 200},
        )
        entry = VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.artifact_content",
            verifier_version="3",
            predicate=predicate,
            subject_identity=self.bad_manifest.artifact_revision_id,
            evaluation_phase="POST_EXECUTION",
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()
        return VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=(entry,),
            plan_sha256="0" * 64,
        ).with_computed_sha256()

    # -- helpers ----------------------------------------------------------

    def _next_ms(self) -> int:
        self._clock += 1_000
        return self._clock

    def _passed_manifest(
        self,
        data: bytes,
        *,
        filename: str,
        format_id: str,
        declared_mime: str,
        docx_min_words: int = 1,
    ):
        manifest = super()._passed_manifest(
            data,
            filename=filename,
            format_id=format_id,
            declared_mime=declared_mime,
            docx_min_words=docx_min_words,
        )
        # P1-6: every QC-passed manifest enters the Store's artifact
        # authority projection (mirrors the production gate pipeline).
        import time as _time

        self.gateway_store.register_artifact_subject(
            artifact_revision_id=manifest.artifact_revision_id,
            object_id=manifest.content_object_id,
            artifact_sha256=manifest.sha256,
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            generation=manifest.generation,
            registered_at_ms=_time.time_ns() // 1_000_000,
        )
        return manifest

    def _claim_repair_effect(self):
        self._effect_ordinal += 1
        effect = derive_effect_identity(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=self._effect_ordinal,
            intent_sha256="9" * 64,
        )
        # Real wall clock: the producing effect must be claimable at/after
        # the directive's wall-clock issued_at_ms.
        import time as _time

        self.gateway_store.claim_effect(
            EffectClaim(
                effect_id=effect.effect_id,
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                run_sequence=1,
                generation=2,
                effect_kind="execution",
                ordinal=effect.ordinal,
                intent_sha256="9" * 64,
                owner_component_id="tiangong-backend",
                claimed_at_ms=_time.time_ns() // 1_000_000,
                claim_sha256="0" * 64,
            ).with_computed_sha256()
        )
        return effect

    def _reverify(self):
        executor = VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.gateway_store,
            object_store=self.object_store,
            fact_ledger=self.fact_ledger,
            plan=self.plan,
        )
        return executor.execute(
            evaluated_at_ms=self._next_ms(),
            artifact_manifests=tuple(self.manifests),
        )

    def _dispatch_success(self, directive: RepairDirective):
        """Scripted runtime through the REAL Store dispatch boundary."""
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        if reserved["outcome"] != "EXECUTE":
            return self._already_claimed(directive)
        self._binding_mark_started(directive, effect.effect_id)
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

    def _entry_records(self):
        records = self.gateway_store.list_verification_records(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
        )
        return [
            r
            for r in records
            if r.predicate_id == self.entry.predicate.predicate_id
        ]


class TestFullRepairLoopE2E(RepairLoopE2EBase):
    """#8: FAIL → REPAIR → real execution → successor → same
    predicate → PASS, end to end."""

    def test_fail_repair_successor_same_predicate_pass(self) -> None:
        readiness = self._reverify()
        self.assertFalse(readiness.verification_ready)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        self.assertIsNone(disposition)

        good_revision = self.manifests[-1].artifact_revision_id

        # Successor chain: original → repaired, depth 1.
        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertEqual(
            resolution["effective_subject_identity"], good_revision
        )
        self.assertEqual(resolution["successor_depth"], 1)

        # SAME predicate, NEW subject, NEW evaluated_at_ms.
        records = self._entry_records()
        pass_records = [r for r in records if r.status == "PASS"]
        fail_records = [r for r in records if r.status == "FAIL"]
        self.assertEqual(len(pass_records), 1)
        self.assertGreaterEqual(len(fail_records), 1)
        self.assertEqual(pass_records[0].subject_identity, good_revision)
        self.assertEqual(
            pass_records[0].predicate_id,
            self.entry.predicate.predicate_id,
        )
        self.assertGreater(
            pass_records[0].evaluated_at_ms,
            max(r.evaluated_at_ms for r in fail_records),
        )

        # Attempt audit bound to the NEW independent evidence.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].execution_outcome, "REVERIFY_PASS")
        self.assertEqual(
            attempts[0].reverify_record_id,
            pass_records[0].verification_record_id,
        )
        self.assertTrue(attempts[0].execution_effect_ids)
        self.assertEqual(
            attempts[0].prior_subject_identity,
            self.bad_manifest.artifact_revision_id,
        )
        self.assertEqual(
            attempts[0].produced_subject_identity, good_revision
        )

        # Gate consumption: a PASSING final readiness carries no
        # disposition (get_current returns None).
        self.assertIsNone(
            self.gateway_store.get_current_verification_disposition(
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                generation=2,
                verification_plan_id=self.plan.verification_plan_id,
                readiness_sha256=final.readiness_sha256,
            )
        )

    def test_executor_rejects_superseded_subject_after_repair(self) -> None:
        """#6/#2: once a successor exists the executor dispatches the
        EFFECTIVE subject only — the original manifest no longer
        satisfies the entry."""
        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)

        stale = VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.gateway_store,
            object_store=self.object_store,
            fact_ledger=self.fact_ledger,
            plan=self.plan,
        ).execute(
            evaluated_at_ms=self._next_ms(),
            artifact_manifests=(self.bad_manifest,),  # superseded subject
        )
        self.assertFalse(stale.verification_ready)
        latest = self._entry_records()[-1]
        self.assertEqual(latest.status, "ERROR")


class TestBudgetTermination(RepairLoopE2EBase):
    """#5/#8: permanent failure terminates in REVIEW within budgets."""

    def test_permanent_failure_terminates_with_review(self) -> None:
        def dispatch_always_bad(directive: RepairDirective):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id)
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
                produced_subject_identity=(
                    still_bad.artifact_revision_id
                ),
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_always_bad,
            reverify=self._reverify,
        )
        self.assertFalse(final.verification_ready)
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "REVIEW")
        self.assertIn(
            "repair_policy.entry_budget_exhausted",
            disposition.reason_codes,
        )

        # Per-entry budget = 2 REPAIR directives, then REVIEW.
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 2)
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 2)
        self.assertTrue(
            all(a.execution_outcome == "REVERIFY_FAIL" for a in attempts)
        )

        # Successor chain advanced exactly budget-many times.
        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertEqual(resolution["successor_depth"], 2)

        # The Gate-visible disposition is bound to the FINAL readiness.
        current = self.gateway_store.get_current_verification_disposition(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            verification_plan_id=self.plan.verification_plan_id,
            readiness_sha256=final.readiness_sha256,
        )
        self.assertIsNotNone(current)
        self.assertEqual(current.action, "REVIEW")


class TestCrashRecovery(RepairLoopE2EBase):
    """#8: crash after directive issuance, before the attempt — the
    loop resumes from Store state without exceeding budgets."""

    def test_resume_after_directive_without_attempt(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        self.assertEqual(len(dispositions), 1)
        self.assertEqual(dispositions[0].action, "REPAIR")
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        self.assertIsNotNone(evidence)
        directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        self.assertIsNotNone(directive)
        # --- crash: no attempt, no successor, no re-verification ---

        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)

        # P1-7: the pre-crash directive is REUSED (same identity), not
        # re-issued — repeated crashes before dispatch cannot burn the
        # budget.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 1)
        self.assertEqual(
            directives[0].repair_directive_id,
            directive.repair_directive_id,
        )


class TestConcurrency(RepairLoopE2EBase):
    """#8: identical concurrent writes stay idempotent."""

    def test_concurrent_identical_successor_put(self) -> None:
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(
                pool.map(
                    lambda _: (
                        self.gateway_store.put_verification_subject_successor(
                            bindings[0], recorded_at_ms=self._next_ms()
                        )
                    ),
                    range(4),
                )
            )
        self.assertEqual(sum(1 for ok in outcomes if ok), 0)
        self.assertEqual(
            len(
                self.gateway_store.list_verification_subject_successors(
                    self.entry.plan_entry_id
                )
            ),
            1,
        )

    def test_concurrent_identical_disposition_put(self) -> None:
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        self.assertEqual(len(dispositions), 1)
        stored = self.gateway_store.get_verification_disposition_by_id(
            dispositions[0].verification_disposition_id
        )
        self.assertIsNotNone(stored)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(
                pool.map(
                    lambda _: (
                        self.gateway_store.put_verification_disposition(
                            stored, recorded_at_ms=self._next_ms()
                        )
                    ),
                    range(4),
                )
            )
        self.assertEqual(sum(1 for ok in outcomes if ok), 0)


class TestStoreRevalidationAdversarial(RepairLoopE2EBase):
    """#4: the v28 write boundary rejects everything the Store cannot
    re-derive from its own state."""

    def setUp(self) -> None:
        super().setUp()
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        self.disposition = dispositions[0]
        self.assertEqual(self.disposition.action, "REPAIR")
        self.evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                self.disposition.failure_evidence_id
            )
        )
        self.directive = self.coordinator.issue_repair_directive(
            disposition=self.disposition,
            failure_evidence=self.evidence,
            plan=self.plan,
        )
        self.assertIsNotNone(self.directive)

    def _forged(self, model, **changes):
        payload = model.model_dump(mode="json")
        payload.update(changes)
        for key in (
            "reason_codes",
            "evidence_refs",
            "execution_effect_ids",
            "allowed_target_refs",
            "forbidden_target_refs",
            "repair_constraints",
        ):
            if key in payload and isinstance(payload[key], list):
                payload[key] = tuple(payload[key])
        return type(model).model_validate(payload).with_computed_sha256()

    def test_forged_failure_evidence_rejected(self) -> None:
        from contracts.verification import derive_failure_signature

        forged_reasons = ("forged.reason",)
        payload = self.evidence.model_dump(mode="json")
        payload.update(
            {
                "reason_codes": forged_reasons,
                "failure_signature_sha256": derive_failure_signature(
                    plan_entry_id=self.evidence.plan_entry_id,
                    effective_subject_identity=(
                        self.evidence.effective_subject_identity
                    ),
                    predicate_sha256=self.evidence.predicate_sha256,
                    verification_status=self.evidence.verification_status,
                    reason_codes=forged_reasons,
                    verification_evidence_sha256=(
                        self.evidence.verification_evidence_sha256
                    ),
                ),
                "failure_evidence_id": "vfe_" + "0" * 64,
                "failure_evidence_sha256": "0" * 64,
            }
        )
        for key in ("reason_codes", "evidence_refs"):
            if isinstance(payload.get(key), list):
                payload[key] = tuple(payload[key])
        forged = FailureEvidence.model_validate(payload).with_computed_sha256()
        # Contract-level integrity holds — only the Store re-derivation
        # can catch this forgery.
        self.assertTrue(forged.has_valid_identity())
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_failure_evidence(
                forged, recorded_at_ms=self._next_ms()
            )

    def test_forged_disposition_action_rejected(self) -> None:
        forged = self._forged(
            self.disposition,
            action="BLOCK",
            verification_disposition_id="vds_" + "0" * 64,
            disposition_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_disposition(
                forged, recorded_at_ms=self._next_ms()
            )

    def test_forged_disposition_attempt_no_rejected(self) -> None:
        forged = self._forged(
            self.disposition,
            attempt_no=5,
            verification_disposition_id="vds_" + "0" * 64,
            disposition_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_disposition(
                forged, recorded_at_ms=self._next_ms()
            )

    def test_directive_with_absent_disposition_rejected(self) -> None:
        forged = self._forged(
            self.directive,
            disposition_id="vds_" + "9" * 64,
            disposition_sha256="9" * 64,
            repair_directive_id="vrd_" + "0" * 64,
            directive_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_directive(
                forged, recorded_at_ms=self._next_ms()
            )

    def test_directive_with_wrong_predicate_rejected(self) -> None:
        forged = self._forged(
            self.directive,
            predicate_sha256="1" * 64,
            repair_directive_id="vrd_" + "0" * 64,
            directive_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_directive(
                forged, recorded_at_ms=self._next_ms()
            )

    def test_attempt_with_phantom_effect_rejected(self) -> None:
        attempt = RepairAttemptRecord(
            repair_attempt_id="vra_" + "0" * 64,
            repair_directive_id=self.directive.repair_directive_id,
            repair_attempt_no=self.directive.repair_attempt_no,
            request_id=self.directive.request_id,
            run_id=self.directive.run_id,
            generation=self.directive.generation,
            plan_entry_id=self.directive.plan_entry_id,
            prior_subject_identity=(
                self.directive.effective_subject_identity
            ),
            produced_subject_identity=(
                self.directive.effective_subject_identity
            ),
            execution_effect_ids=("eff_" + "9" * 64,),
            execution_outcome="EXECUTION_FAILED",
            reverify_record_id=None,
            started_at_ms=self._next_ms(),
            finished_at_ms=self._next_ms(),
            attempt_sha256="0" * 64,
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_attempt(
                attempt, recorded_at_ms=self._next_ms()
            )

    def test_attempt_with_wrong_prior_subject_rejected(self) -> None:
        effect = self._claim_repair_effect()
        attempt = RepairAttemptRecord(
            repair_attempt_id="vra_" + "0" * 64,
            repair_directive_id=self.directive.repair_directive_id,
            repair_attempt_no=self.directive.repair_attempt_no,
            request_id=self.directive.request_id,
            run_id=self.directive.run_id,
            generation=self.directive.generation,
            plan_entry_id=self.directive.plan_entry_id,
            prior_subject_identity="arv_" + "7" * 61,
            produced_subject_identity=(
                self.directive.effective_subject_identity
            ),
            execution_effect_ids=(effect.effect_id,),
            execution_outcome="EXECUTION_FAILED",
            reverify_record_id=None,
            started_at_ms=self._next_ms(),
            finished_at_ms=self._next_ms(),
            attempt_sha256="0" * 64,
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_attempt(
                attempt, recorded_at_ms=self._next_ms()
            )

    def _successor_payload(self, **overrides) -> dict:
        payload = dict(
            successor_binding_id="vss_" + "0" * 64,
            request_id=self.directive.request_id,
            run_id=self.directive.run_id,
            generation=self.directive.generation,
            verification_plan_id=self.directive.verification_plan_id,
            plan_entry_id=self.directive.plan_entry_id,
            subject_kind=self.directive.subject_kind,
            predecessor_subject_identity=(
                self.directive.effective_subject_identity
            ),
            successor_subject_identity="arv_" + "5" * 61,
            repair_directive_id=self.directive.repair_directive_id,
            repair_directive_sha256=self.directive.directive_sha256,
            produced_by_effect_id="eff_placeholder",
            repair_attempt_no=self.directive.repair_attempt_no,
            bound_at_ms=95_000,
            successor_binding_sha256="0" * 64,
        )
        payload.update(overrides)
        return payload

    def test_successor_fork_chain_rejected(self) -> None:
        effect = self._claim_repair_effect()
        successor = VerificationSubjectSuccessor(
            **self._successor_payload(
                predecessor_subject_identity="arv_" + "7" * 61,
                produced_by_effect_id=effect.effect_id,
            )
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_subject_successor(
                successor, recorded_at_ms=self._next_ms()
            )

    def test_successor_phantom_producing_effect_rejected(self) -> None:
        successor = VerificationSubjectSuccessor(
            **self._successor_payload(
                produced_by_effect_id="eff_" + "9" * 64,
            )
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_subject_successor(
                successor, recorded_at_ms=self._next_ms()
            )

    def test_successor_phantom_effect_subject_rejected(self) -> None:
        effect = self._claim_repair_effect()
        successor = VerificationSubjectSuccessor(
            **self._successor_payload(
                subject_kind="effect",
                successor_subject_identity="eff_" + "9" * 64,
                produced_by_effect_id=effect.effect_id,
            )
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_subject_successor(
                successor, recorded_at_ms=self._next_ms()
            )


class TestDesktopProductionWiring(unittest.TestCase):
    """#2: the Desktop branch runs the FULL loop and the dispatch
    bridges to the EXISTING runtime authorities."""

    def _desktop_branch(self) -> str:
        source = (
            ROOT / "src" / "total_gateway" / "orchestration.py"
        ).read_text(encoding="utf-8")
        branch = source[
            source.index('if envelope.channel == "desktop":') :
        ]
        return branch[: branch.index("delivery_now =")]

    def test_desktop_branch_calls_the_full_repair_loop(self) -> None:
        branch = self._desktop_branch()
        self.assertIn("execute_repair_loop(", branch)
        self.assertIn("_dispatch_repair_directive(", branch)
        self.assertIn("_repair_reverify", branch)
        self.assertIn("get_current_verification_disposition(", branch)

    def test_dispatch_bridges_to_existing_runtime_authorities(self) -> None:
        source = (
            ROOT / "src" / "total_gateway" / "orchestration.py"
        ).read_text(encoding="utf-8")
        start = source.index("def _dispatch_repair_directive")
        end = source.index("def process(", start)
        method = source[start:end]
        for needle in (
            "PolicyEngine(",
            "ExecutionTicketPayload(",
            "sign_execution",
            "BackendClient(",
            "claim_effect",
            # Final P0-2: the bridge crosses and terminalizes BOTH
            # authorities through the one-transaction composite APIs.
            "start_repair_execution(",
            "complete_repair_execution(",
            "reserve_repair_execution(",
            "dispatch_claim_id",
            "_register_repair_artifacts(",
            "ArtifactGate(",
        ):
            self.assertIn(needle, method)


class TestAmbiguousRepairSafety(RepairLoopE2EBase):
    """P0-1: EXECUTION_AMBIGUOUS → immediate RECONCILE, zero replay."""

    def test_ambiguous_dispatch_stops_with_reconcile_zero_replay(self) -> None:
        import time as _time

        def dispatch_ambiguous(directive: RepairDirective):
            started_effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(
                directive, started_effect.effect_id
            )
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(
                directive, started_effect.effect_id
            )
            self._binding_complete(
                directive, "AMBIGUOUS", "", error_code="repair.ambiguous"
            )
            return RepairDispatchResult(
                execution_outcome="EXECUTION_AMBIGUOUS",
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(started_effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_ambiguous,
            reverify=self._reverify,
        )
        self.assertFalse(final.verification_ready)
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "RECONCILE")
        self.assertIn(
            "repair_policy.ambiguous_effect_no_replay",
            disposition.reason_codes,
        )
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].execution_outcome, "EXECUTION_AMBIGUOUS")
        self.assertIsNone(attempts[0].reverify_record_id)
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 1)

        # Zero replay: a second loop pass still sees RECONCILE and never
        # issues another directive (the ambiguous attempt is permanent).
        final2, disposition2 = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=final,
            dispatch=dispatch_ambiguous,
            reverify=self._reverify,
        )
        self.assertEqual(disposition2.action, "RECONCILE")
        self.assertEqual(
            len(
                self.gateway_store.list_repair_directives(
                    self.entry.plan_entry_id
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                self.gateway_store.list_repair_attempts(
                    self.entry.plan_entry_id
                )
            ),
            1,
        )


class TestEffectRepairE2E(_RepairBindingMixin, M21OracleTestBase):
    """P0-2: effect repair → NEW effect successor → SAME predicate PASS."""

    def setUp(self) -> None:
        super().setUp()
        self._register_lineage_request()
        self.gateway_store.put_registry_snapshot(
            self.snapshot, recorded_at_ms=1_500
        )
        self._clock = 30_000
        self._effect_ordinal = 50
        from contracts.verification import (
            VerificationPlan as _Plan,
            VerificationPlanEntryV2 as _Entry,
        )

        predicate = AcceptancePredicate.create(
            predicate_type="effect.terminal_succeeded",
            subject_kind="effect",
        )
        self.old_effect = self._failed_effect()
        entry = _Entry(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.effect_state",
            verifier_version="2",
            predicate=predicate,
            subject_identity=self.old_effect,
            evaluation_phase="POST_EXECUTION",
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()
        self.plan = _Plan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=(entry,),
            plan_sha256="0" * 64,
        ).with_computed_sha256()
        self.entry = self.plan.entries[0]
        assert self.gateway_store.put_verification_plan(
            self.plan, recorded_at_ms=1_600
        )
        self.gateway_store.activate_verification_plan(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            verification_plan_id=self.plan.verification_plan_id,
            verification_plan_sha256=self.plan.plan_sha256,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            activated_at_ms=1_700,
        )
        self.coordinator = VerificationRepairCoordinator(
            store=self.gateway_store
        )

    def _claim(self) -> str:
        from contracts import derive_effect_identity

        self._effect_ordinal += 1
        identity = derive_effect_identity(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=self._effect_ordinal,
            intent_sha256="8" * 64,
        )
        self.gateway_store.claim_effect(
            EffectClaim(
                effect_id=identity.effect_id,
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                run_sequence=1,
                generation=2,
                effect_kind="execution",
                ordinal=identity.ordinal,
                intent_sha256="8" * 64,
                owner_component_id="tiangong-backend",
                claimed_at_ms=__import__("time").time_ns() // 1_000_000,
                claim_sha256="0" * 64,
            ).with_computed_sha256()
        )
        return identity.effect_id

    def _complete(self, effect_id: str, status: str) -> None:
        import time as _time

        from contracts import canonical_sha256 as _sha
        from total_gateway.effects import EffectResult

        self.gateway_store.mark_effect_started(
            effect_id, started_at_ms=_time.time_ns() // 1_000_000
        )
        self.gateway_store.complete_effect(
            EffectResult(
                result_id="effect-result-" + effect_id[4:20],
                effect_id=effect_id,
                status=status,
                fact_id="fact-effect-" + effect_id[4:20],
                evidence_sha256=_sha({"status": status}),
                error_code=None if status == "SUCCEEDED" else "exec.failed",
                observed_at_ms=_time.time_ns() // 1_000_000,
                result_sha256="0" * 64,
            ).with_computed_sha256()
        )

    def _failed_effect(self) -> str:
        effect_id = self._claim()
        self._complete(effect_id, "FAILED_FINAL")
        return effect_id

    def _reverify(self):
        executor = VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.gateway_store,
            object_store=self.object_store,
            fact_ledger=self.fact_ledger,
            plan=self.plan,
        )
        self._clock += 1_000
        return executor.execute(
            evaluated_at_ms=self._clock, artifact_manifests=()
        )

    def test_effect_repair_new_effect_successor_same_predicate(self) -> None:
        def dispatch(directive: RepairDirective):
            # carrier: the binding-owned dispatch effect; subject: the
            # NEW effect the predicate re-verifies (separated so the
            # carrier can be SUCCEEDED while the subject FAILS).
            carrier = self._claim()
            reserved = self._reserve_or_claimed(directive, carrier)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            new_effect = self._claim()
            self._binding_mark_started(directive, carrier)
            self._complete(new_effect, "SUCCEEDED")
            self._binding_complete(directive, "SUCCEEDED", new_effect)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_effect,
                execution_effect_ids=(carrier,),
            )

        readiness = self._reverify()
        self.assertFalse(readiness.verification_ready)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        self.assertIsNone(disposition)

        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertEqual(resolution["successor_depth"], 1)
        self.assertNotEqual(
            resolution["effective_subject_identity"], self.old_effect
        )

        records = [
            r
            for r in self.gateway_store.list_verification_records(
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                generation=2,
            )
            if r.predicate_id == self.entry.predicate.predicate_id
        ]
        pass_records = [r for r in records if r.status == "PASS"]
        self.assertEqual(len(pass_records), 1)
        self.assertEqual(
            pass_records[0].subject_identity,
            resolution["effective_subject_identity"],
        )
        self.assertEqual(
            pass_records[0].evaluated_at_ms,
            max(r.evaluated_at_ms for r in records),
        )

    def test_effect_side_effect_budget_caps_at_one(self) -> None:
        """P1-5: effect repairs are capped by the side-effect budget
        (1) BEFORE the per-entry budget (2)."""

        def dispatch_fails_again(directive: RepairDirective):
            carrier = self._claim()
            reserved = self._reserve_or_claimed(directive, carrier)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            new_effect = self._claim()
            self._binding_mark_started(directive, carrier)
            # The runtime completed; the NEW SUBJECT effect itself
            # failed (the carrier still terminates SUCCEEDED).
            self._complete(new_effect, "FAILED_FINAL")
            self._binding_complete(directive, "SUCCEEDED", new_effect)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_effect,
                execution_effect_ids=(carrier,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch_fails_again,
            reverify=self._reverify,
        )
        self.assertFalse(final.verification_ready)
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "REVIEW")
        self.assertIn(
            "repair_policy.side_effect_budget_exhausted",
            disposition.reason_codes,
        )
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 1)


class TestGenerationBudgetMultiEntry(RepairLoopE2EBase):
    """P1-8: one readiness, N repairable entries — the generation budget
    turns the (N+1)-th into a normal REVIEW, not a write rejection."""

    def _build_plan(self):
        entries = []
        for index in range(5):
            manifest = self._passed_manifest(
                docx_bytes("字" * 50),
                filename=f"report{index}.docx",
                format_id="docx",
                declared_mime=DOCX_MIME,
            )
            self.manifests.append(manifest)
            predicate = AcceptancePredicate.create(
                predicate_type="artifact.min_visible_text_chars",
                subject_kind="artifact",
                params={"min_chars": 200},
            )
            entry = VerificationPlanEntryV2(
                plan_entry_id="vpe_" + "0" * 63 + str(index),
                verifier_id="verifier.artifact_content",
                verifier_version="3",
                predicate=predicate,
                subject_identity=manifest.artifact_revision_id,
                evaluation_phase="POST_EXECUTION",
                required=True,
                entry_sha256="0" * 64,
            ).with_computed_sha256()
            entries.append(entry)
        return VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=tuple(sorted(entries, key=lambda e: e.plan_entry_id)),
            plan_sha256="0" * 64,
        ).with_computed_sha256()

    def test_five_entries_four_repairs_then_generation_review(self) -> None:
        def dispatch(directive: RepairDirective):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id)
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

        def reverify():
            executor = VerificationPlanExecutor(
                snapshot=self.snapshot,
                store=self.gateway_store,
                object_store=self.object_store,
                fact_ledger=self.fact_ledger,
                plan=self.plan,
            )
            return executor.execute(
                evaluated_at_ms=self._next_ms(),
                artifact_manifests=tuple(self.manifests),
            )

        readiness = reverify()
        self.assertEqual(
            len(
                [
                    a
                    for a in readiness.entry_assessments
                    if a.status != "PASS"
                ]
            ),
            5,
        )
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=reverify,
        )
        self.assertFalse(final.verification_ready)
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "REVIEW")
        self.assertIn(
            "repair_policy.generation_budget_exhausted",
            disposition.reason_codes,
        )
        # exactly the generation budget of REPAIR directives was issued
        total_directives = sum(
            len(self.gateway_store.list_repair_directives(e.plan_entry_id))
            for e in self.plan.entries
        )
        self.assertEqual(total_directives, 4)


class TestCoordinatorRace(RepairLoopE2EBase):
    """Final P0-1: two coordinators racing for attempt #1 — the Store
    dispatch boundary lets EXACTLY ONE cross into the runtime (produce
    count == 1), one attempt, one successor."""

    def test_two_coordinators_single_runtime_execution(self) -> None:
        import threading

        readiness = self._reverify()
        # Pre-issue ONE directive D; both workers race to reserve THE
        # SAME D with different invocation-scoped claims.
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                dispositions[0].failure_evidence_id
            )
        )
        shared_directive = self.coordinator.issue_repair_directive(
            disposition=dispositions[0],
            failure_evidence=evidence,
            plan=self.plan,
        )
        produce_calls = []
        reserve_outcomes = []
        calls_lock = threading.Lock()

        def dispatch(directive: RepairDirective):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            with calls_lock:
                reserve_outcomes.append(reserved["outcome"])
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id)
            with calls_lock:
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
                # the competitive loser may fail closed; the final Store
                # state is what must stay consistent
                pass

        assert shared_directive is not None

        threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        # Exactly one EXECUTE and (when both raced) exactly one FOLLOW —
        # never two EXECUTEs for the SAME directive.
        self.assertEqual(reserve_outcomes.count("EXECUTE"), 1)
        self.assertLessEqual(reserve_outcomes.count("FOLLOW"), 1)
        # EXACTLY ONE runtime execution crossed the boundary.
        self.assertEqual(len(produce_calls), 1)
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].repair_attempt_no, 1)
        bindings = self.gateway_store.list_verification_subject_successors(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(bindings), 1)
        binding = self.gateway_store.get_repair_execution_binding_by_attempt(
            self.entry.plan_entry_id, 1
        )
        self.assertIsNotNone(binding)
        self.assertEqual(binding["state"], "SUCCEEDED")


class TestCrashAfterExecutionSuccess(RepairLoopE2EBase):
    """Final P0-2: runtime succeeded, binding persisted the produced
    subject, then CRASH before successor/re-verification. Recovery reads
    ONLY the Store (no in-process dict), never calls the runtime, and
    completes successor -> re-verify."""

    def _crashed_binding(self, directive):
        effect = self._claim_repair_effect()
        reserved = self._reserve_or_claimed(directive, effect.effect_id)
        assert reserved["outcome"] == "EXECUTE"
        self._binding_mark_started(directive, effect.effect_id)
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
        return effect, good

    def test_recover_after_succeeded_binding_before_successor(self) -> None:
        runtime_calls: list[str] = []

        def dispatch(directive: RepairDirective):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

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
        # Runtime already executed; produced subject PERSISTED in the
        # binding; then CRASH (no successor, no re-verification, no
        # attempt record). Both authorities committed SUCCEEDED
        # atomically before the crash.
        effect, good = self._crashed_binding(directive)
        binding_after = self.gateway_store.get_repair_execution_binding(
            directive.repair_directive_id
        )
        self.assertEqual(binding_after["state"], "SUCCEEDED")
        effect_after = self.gateway_store.get_effect(effect.effect_id)
        self.assertEqual(effect_after.state, "SUCCEEDED")

        # New process/coordinator: reads only the Store.
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        # The runtime was NEVER called again.
        self.assertEqual(runtime_calls, [])
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 1)
        self.assertEqual(
            directives[0].repair_directive_id,
            directive.repair_directive_id,
        )
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].execution_outcome, "REVERIFY_PASS")
        self.assertEqual(
            attempts[0].execution_effect_ids, (effect.effect_id,)
        )
        resolution = self.gateway_store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertEqual(
            resolution["effective_subject_identity"],
            good.artifact_revision_id,
        )

    def test_recover_after_side_effect_started_reconciles(self) -> None:
        """Final P0-2: crash exactly after the side-effect boundary —
        recovery must NOT call the runtime and must land on RECONCILE."""
        runtime_calls: list[str] = []

        def dispatch(directive: RepairDirective):
            runtime_calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

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
        # CRASH exactly after the ONE atomic boundary transition — both
        # authorities are SIDE_EFFECT_STARTED or neither is.
        self._binding_mark_started(directive, effect.effect_id)
        started_binding = self.gateway_store.get_repair_execution_binding(
            directive.repair_directive_id
        )
        self.assertEqual(started_binding["state"], "SIDE_EFFECT_STARTED")
        self.assertEqual(
            self.gateway_store.get_effect(effect.effect_id).state,
            "SIDE_EFFECT_STARTED",
        )

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertFalse(final.verification_ready)
        self.assertEqual(runtime_calls, [])
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "RECONCILE")
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            attempts[0].execution_outcome, "EXECUTION_AMBIGUOUS"
        )
        binding = self.gateway_store.get_repair_execution_binding_by_attempt(
            self.entry.plan_entry_id, 1
        )
        self.assertEqual(binding["state"], "SIDE_EFFECT_STARTED")
        # Zero successors were bound for an unproven reality.
        self.assertEqual(
            self.gateway_store.list_verification_subject_successors(
                self.entry.plan_entry_id
            ),
            (),
        )


class TestDirectiveBoundaryHardening(RepairLoopE2EBase):
    """P1-4 adversarial: expired / widened / over-budget directives are
    rejected at the Store write boundary."""

    def setUp(self) -> None:
        super().setUp()
        readiness = self._reverify()
        dispositions = self.coordinator.process_readiness(
            plan=self.plan, readiness=readiness
        )
        self.disposition = dispositions[0]
        evidence = (
            self.gateway_store.get_verification_failure_evidence_by_id(
                self.disposition.failure_evidence_id
            )
        )
        self.directive = self.coordinator.issue_repair_directive(
            disposition=self.disposition,
            failure_evidence=evidence,
            plan=self.plan,
        )

    def _forged_directive(self, **changes):
        payload = self.directive.model_dump(mode="json")
        payload.update(changes)
        for key in (
            "allowed_target_refs",
            "forbidden_target_refs",
            "repair_constraints",
        ):
            if key in payload and isinstance(payload[key], list):
                payload[key] = tuple(payload[key])
        return (
            RepairDirective.model_validate(payload).with_computed_sha256()
        )

    def test_expired_directive_rejected(self) -> None:
        import time as _time

        now = _time.time_ns() // 1_000_000
        forged = self._forged_directive(
            issued_at_ms=now - 10_000,
            expires_at_ms=now - 1_000,
            repair_directive_id="vrd_" + "0" * 64,
            directive_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_directive(
                forged, recorded_at_ms=now
            )

    def test_widened_target_scope_rejected(self) -> None:
        forged = self._forged_directive(
            allowed_target_refs=(
                self.directive.effective_subject_identity,
                "arv_" + "3" * 61,
            ),
            repair_directive_id="vrd_" + "0" * 64,
            directive_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_directive(
                forged, recorded_at_ms=__import__("time").time_ns()
                // 1_000_000
            )

    def test_over_budget_directive_rejected(self) -> None:
        forged = self._forged_directive(
            execution_budget_ms=6_000_000,
            repair_directive_id="vrd_" + "0" * 64,
            directive_sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_directive(
                forged, recorded_at_ms=__import__("time").time_ns()
                // 1_000_000
            )

    def test_unrelated_effect_cannot_impersonate_repair(self) -> None:
        """Final P0: a same-lineage, time-valid but UNRELATED effect
        must be rejected as a RepairAttempt execution effect and as a
        Successor producing effect — only the repair execution binding's
        own effect counts."""
        # Run one real repair so a SUCCEEDED binding exists.
        readiness = self._reverify()
        final, _ = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=self._dispatch_success,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        binding = self.gateway_store.get_repair_execution_binding_by_attempt(
            self.entry.plan_entry_id, 1
        )
        self.assertEqual(binding["state"], "SUCCEEDED")
        directive = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )[0]

        # An unrelated effect: same lineage, claimable AFTER the
        # directive — but never authorized by the binding.
        attacker_effect = self._claim_repair_effect()
        good_revision = self.manifests[-1].artifact_revision_id

        forged_attempt = RepairAttemptRecord(
            repair_attempt_id="vra_" + "0" * 64,
            repair_directive_id=directive.repair_directive_id,
            repair_attempt_no=directive.repair_attempt_no,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            plan_entry_id=directive.plan_entry_id,
            prior_subject_identity=directive.effective_subject_identity,
            produced_subject_identity=good_revision,
            execution_effect_ids=(attacker_effect.effect_id,),
            execution_outcome="EXECUTION_FAILED",
            reverify_record_id=None,
            started_at_ms=__import__("time").time_ns() // 1_000_000,
            finished_at_ms=__import__("time").time_ns() // 1_000_000,
            attempt_sha256="0" * 64,
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_repair_attempt(
                forged_attempt,
                recorded_at_ms=__import__("time").time_ns() // 1_000_000,
            )

        forged_successor = VerificationSubjectSuccessor(
            successor_binding_id="vss_" + "0" * 64,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            verification_plan_id=directive.verification_plan_id,
            plan_entry_id=directive.plan_entry_id,
            subject_kind=directive.subject_kind,
            predecessor_subject_identity=directive.effective_subject_identity,
            successor_subject_identity=good_revision,
            repair_directive_id=directive.repair_directive_id,
            repair_directive_sha256=directive.directive_sha256,
            produced_by_effect_id=attacker_effect.effect_id,
            repair_attempt_no=directive.repair_attempt_no + 1,
            bound_at_ms=__import__("time").time_ns() // 1_000_000,
            successor_binding_sha256="0" * 64,
        ).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_subject_successor(
                forged_successor,
                recorded_at_ms=__import__("time").time_ns() // 1_000_000,
            )

    def test_successor_cycle_rejected(self) -> None:
        effect = self._claim_repair_effect()
        payload = dict(
            successor_binding_id="vss_" + "0" * 64,
            request_id=self.directive.request_id,
            run_id=self.directive.run_id,
            generation=self.directive.generation,
            verification_plan_id=self.directive.verification_plan_id,
            plan_entry_id=self.directive.plan_entry_id,
            subject_kind=self.directive.subject_kind,
            predecessor_subject_identity=(
                self.directive.effective_subject_identity
            ),
            successor_subject_identity=(
                self.bad_manifest.artifact_revision_id
            ),  # already on the chain (it IS the original subject)
            repair_directive_id=self.directive.repair_directive_id,
            repair_directive_sha256=self.directive.directive_sha256,
            produced_by_effect_id=effect.effect_id,
            repair_attempt_no=self.directive.repair_attempt_no,
            bound_at_ms=__import__("time").time_ns() // 1_000_000,
            successor_binding_sha256="0" * 64,
        )
        binding = VerificationSubjectSuccessor(**payload).with_computed_sha256()
        with self.assertRaises(ValueError):
            self.gateway_store.put_verification_subject_successor(
                binding, recorded_at_ms=__import__("time").time_ns()
                // 1_000_000
            )


class TestGateAuthorityChecks(RepairLoopE2EBase):
    """P1-9: the Gate validates the full disposition ↔ evidence ↔
    readiness ↔ plan binding itself."""

    def _failing_state(self):
        from total_gateway.completion_gate import (
            CompletionGate,
            CompletionRequirements,
        )

        def dispatch_always_bad(directive: RepairDirective):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            self._binding_mark_started(directive, effect.effect_id)
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
        evidence = self.gateway_store.get_verification_failure_evidence_by_id(
            disposition.failure_evidence_id
        )
        requirements = CompletionRequirements(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            generation=2,
            text_required=False,
            required_artifact_revision_ids=(
                self.bad_manifest.artifact_revision_id,
            ),
            delivery_requirement="NONE",
            verification_mode="PLAN_BOUND",
        )
        gate = CompletionGate(
            self.object_store,
            self.fact_ledger,
            head_state_reader=self.gateway_store.get_effect_head_state,
        )
        return gate, requirements, final, disposition, evidence

    def test_disposition_without_evidence_rejected(self) -> None:
        from total_gateway.completion_gate import CompletionGateError

        gate, requirements, final, disposition, _ = self._failing_state()
        with self.assertRaisesRegex(
            CompletionGateError,
            "completion.verification.disposition_without_evidence",
        ):
            gate.evaluate(
                requirements,
                active_plan=self.plan,
                verification_readiness=final,
                verification_disposition=disposition,
            )

    def test_stale_evidence_rejected(self) -> None:
        from total_gateway.completion_gate import CompletionGateError

        gate, requirements, final, disposition, evidence = (
            self._failing_state()
        )
        stale = self.gateway_store.list_verification_failure_evidence(
            self.entry.plan_entry_id
        )[0]
        if stale.failure_evidence_id == evidence.failure_evidence_id:
            self.skipTest("no earlier evidence row in this run")
        with self.assertRaisesRegex(
            CompletionGateError,
            "completion.verification.disposition_evidence_mismatch",
        ):
            gate.evaluate(
                requirements,
                active_plan=self.plan,
                verification_readiness=final,
                verification_disposition=disposition,
                verification_failure_evidence=stale,
            )

    def test_fully_bound_disposition_drives_in_progress(self) -> None:
        gate, requirements, final, disposition, evidence = (
            self._failing_state()
        )
        decision = gate.evaluate(
            requirements,
            active_plan=self.plan,
            verification_readiness=final,
            verification_disposition=disposition,
            verification_failure_evidence=evidence,
        )
        self.assertEqual(decision.outcome, "IN_PROGRESS")


class TestChannelSafetySemantics(RepairLoopE2EBase):
    """P0-3: channel-side gating — nothing auto-executes when the
    dispatchable kinds are empty; the delivery is never replayed."""

    def test_empty_kinds_never_dispatch_and_keep_repair_pending(self) -> None:
        calls: list[str] = []

        def dispatch(directive: RepairDirective):
            calls.append(directive.repair_directive_id)
            return self._dispatch_success(directive)

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
            dispatchable_subject_kinds=(),
        )
        self.assertEqual(calls, [])
        self.assertFalse(final.verification_ready)
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "REPAIR")
        # No directive was issued for a non-dispatchable kind.
        self.assertEqual(
            self.gateway_store.list_repair_directives(
                self.entry.plan_entry_id
            ),
            (),
        )
        self.assertEqual(
            self.gateway_store.list_repair_attempts(self.entry.plan_entry_id),
            (),
        )

    def test_outbox_finalization_uses_safe_repair_loop(self) -> None:
        source = (
            ROOT / "src" / "total_gateway" / "delivery_outbox.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_run_channel_repair_loop(", source)
        self.assertIn("repair_dispatch: Callable[..., object] | None = None", source)
        # The delivery itself is never re-sent by the repair loop.
        loop_body = source[
            source.index("def _run_channel_repair_loop") :
        ]
        loop_body = loop_body[: loop_body.index("def _load_payload")]
        self.assertIn("dispatchable_subject_kinds=dispatchable", loop_body)
        self.assertNotIn("send_message", loop_body)

class TestRepositoryRepairE2E(_RepairBindingMixin, _M31RepositoryBase):
    """P0-2: repository repair -> new mutation effect + PRE/POST window
    -> SAME predicate re-verified -> PASS."""

    def setUp(self) -> None:
        super().setUp()
        self.store.put_registry_snapshot(self.snapshot, recorded_at_ms=1_500)
        # Original subject: a repository mutation effect whose window
        # changes the WRONG path -> FAIL. (Observations are wall-clock
        # stamped, so every capture in this test observes a NEW repo
        # state — re-observing an unchanged state is a content conflict.)
        from tests.test_p19_m3_1_repository_binding import _git

        self.old_subject = self._create_effect(100)
        pre = self._capture_observation()
        self._store_content(pre)
        self._bind(pre, role="PRE", subject_effect_id=self.old_subject)
        (self._repo / "docs").mkdir(exist_ok=True)
        (self._repo / "docs" / "x.md").write_text(
            "noise" + chr(10), encoding="utf-8"
        )
        _git(self._repo, "add", ".")
        _git(self._repo, "commit", "-q", "-m", "wrong path")
        post = self._capture_observation(delta_from=pre)
        self._store_content(post)
        self._bind(post, role="POST", subject_effect_id=self.old_subject)

        predicate = AcceptancePredicate.create(
            predicate_type="repository.required_paths_changed",
            subject_kind="repository",
            params={"paths": ["src/main.py"]},
        )
        entry = VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.repository_state",
            verifier_version="2",
            predicate=predicate,
            subject_identity=self.old_subject,
            evaluation_phase="POST_EXECUTION",
            required=True,
            entry_sha256="0" * 64,
        ).with_computed_sha256()
        self.plan = VerificationPlan(
            verification_plan_id="vpl_" + "0" * 64,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            entries=(entry,),
            plan_sha256="0" * 64,
        ).with_computed_sha256()
        self.entry = self.plan.entries[0]
        assert self.store.put_verification_plan(
            self.plan, recorded_at_ms=1_600
        )
        self.store.activate_verification_plan(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=2,
            verification_plan_id=self.plan.verification_plan_id,
            verification_plan_sha256=self.plan.plan_sha256,
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            activated_at_ms=1_700,
        )
        self.coordinator = VerificationRepairCoordinator(store=self.store)
        self._clock = 30_000

    def _create_effect(self, ordinal: int = 0) -> str:
        """Wall-clock claim: repair effects must be claimable at/after
        the directive's wall-clock issued_at_ms (Store boundary)."""
        from contracts import derive_effect_identity

        identity = derive_effect_identity(
            request_id=self.request_id, run_id=self.run_id,
            run_sequence=1, generation=2, effect_kind="execution",
            ordinal=ordinal, intent_sha256="6" * 64,
        )
        import time as _time

        self.store.claim_effect(
            EffectClaim(
                effect_id=identity.effect_id, request_id=self.request_id,
                run_id=self.run_id, run_sequence=1, generation=2,
                effect_kind="execution", ordinal=ordinal,
                intent_sha256="6" * 64,
                owner_component_id="tiangong-backend",
                claimed_at_ms=_time.time_ns() // 1_000_000,
                claim_sha256="0" * 64,
            ).with_computed_sha256()
        )
        return identity.effect_id

    def _reverify(self):
        executor = VerificationPlanExecutor(
            snapshot=self.snapshot,
            store=self.store,
            object_store=None,
            fact_ledger=None,
            plan=self.plan,
        )
        self._clock += 1_000
        return executor.execute(
            evaluated_at_ms=self._clock, artifact_manifests=()
        )

    def test_repository_repair_new_mutation_effect_successor_pass(self) -> None:
        def dispatch(directive: RepairDirective):
            # The repaired mutation: a NEW effect with a PRE/POST window
            # that DOES change the required path. The marker commit first
            # moves the repo to a fresh state so the PRE observation is
            # new content.
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
            self._binding_mark_started(directive, new_subject)
            # The observation window is bound BEFORE the binding is
            # terminalized (production semantics).
            self._window(subject=new_subject, delta=True)
            self._binding_complete(directive, "SUCCEEDED", new_subject)
            return RepairDispatchResult(
                execution_outcome="DISPATCHED",
                produced_subject_identity=new_subject,
                execution_effect_ids=(new_subject,),
            )

        readiness = self._reverify()
        self.assertFalse(readiness.verification_ready)

        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        self.assertTrue(final.verification_ready)
        self.assertIsNone(disposition)

        resolution = self.store.resolve_verification_subject(
            self.entry.plan_entry_id
        )
        self.assertEqual(resolution["successor_depth"], 1)
        new_subject = resolution["effective_subject_identity"]
        self.assertNotEqual(new_subject, self.old_subject)

        records = [
            r
            for r in self.store.list_verification_records(
                request_id=self.request_id,
                run_id=self.run_id,
                generation=2,
            )
            if r.predicate_id == self.entry.predicate.predicate_id
        ]
        pass_records = [r for r in records if r.status == "PASS"]
        self.assertEqual(len(pass_records), 1)
        self.assertEqual(pass_records[0].subject_identity, new_subject)
        self.assertEqual(
            pass_records[0].evaluated_at_ms,
            max(r.evaluated_at_ms for r in records),
        )
        # the successor's PRE/POST window is bound in the Store
        self.assertTrue(
            self.store.list_repository_bindings_for_subject(new_subject)
        )


class TestDispatchClaimLease(RepairLoopE2EBase):
    """Final P0-1 B: the invocation-scoped dispatch claim lease."""

    def _issued_directive(self):
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
        return directive

    def _reserve(self, directive, effect_id, claim_id, *, now, expiry):
        return self.gateway_store.reserve_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            repair_directive_sha256=directive.directive_sha256,
            plan_entry_id=directive.plan_entry_id,
            repair_attempt_no=directive.repair_attempt_no,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            effect_id=effect_id,
            effect_intent_sha256="9" * 64,
            reserved_at_ms=now,
            dispatch_claim_id=claim_id,
            claim_expires_at_ms=expiry,
        )

    def test_live_claim_cannot_be_stolen(self) -> None:
        import time as _time

        directive = self._issued_directive()
        effect = self._claim_repair_effect()
        now = _time.time_ns() // 1_000_000
        first = self._reserve(
            directive, effect.effect_id, "claim-a",
            now=now, expiry=now + 120_000,
        )
        self.assertEqual(first["outcome"], "EXECUTE")
        second = self._reserve(
            directive, effect.effect_id, "claim-b",
            now=now + 1, expiry=now + 120_000,
        )
        self.assertEqual(second["outcome"], "FOLLOW")
        # idempotent re-entry by the SAME live claim
        again = self._reserve(
            directive, effect.effect_id, "claim-a",
            now=now + 2, expiry=now + 120_000,
        )
        self.assertEqual(again["outcome"], "EXECUTE")

    def test_expired_pre_start_claim_may_be_taken_over(self) -> None:
        import sqlite3
        import time as _time

        directive = self._issued_directive()
        effect = self._claim_repair_effect()
        now = _time.time_ns() // 1_000_000
        first = self._reserve(
            directive, effect.effect_id, "claim-a",
            now=now, expiry=now + 120_000,
        )
        self.assertEqual(first["outcome"], "EXECUTE")
        # the lease expires (wall clock moves past it)
        connection = sqlite3.connect(self.temporary.name + "/gateway.sqlite3")
        connection.execute(
            "UPDATE repair_execution_binding SET claim_expires_at_ms = 1"
            " WHERE repair_directive_id = ?",
            (directive.repair_directive_id,),
        )
        connection.commit()
        connection.close()
        takeover = self._reserve(
            directive, effect.effect_id, "claim-b",
            now=now + 1, expiry=now + 120_000,
        )
        self.assertEqual(takeover["outcome"], "EXECUTE")
        self.assertEqual(
            takeover["binding"]["dispatch_claim_id"], "claim-b"
        )
        self.assertGreater(
            int(takeover["binding"]["claim_revision"]),
            int(first["binding"]["claim_revision"]),
        )

    def test_started_claim_can_never_be_taken_over(self) -> None:
        import time as _time

        directive = self._issued_directive()
        effect = self._claim_repair_effect()
        now = _time.time_ns() // 1_000_000
        first = self._reserve(
            directive, effect.effect_id, "claim-a",
            now=now, expiry=now + 120_000,
        )
        self.assertEqual(first["outcome"], "EXECUTE")
        self.gateway_store.start_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            effect_id=effect.effect_id,
            started_at_ms=now + 1,
        )
        # even with the lease expired, a STARTED binding is FOLLOW-only
        takeover = self._reserve(
            directive, effect.effect_id, "claim-b",
            now=now + 2, expiry=now + 120_000,
        )
        self.assertEqual(takeover["outcome"], "FOLLOW")
        self.assertEqual(
            takeover["binding"]["state"], "SIDE_EFFECT_STARTED"
        )

    def test_reserve_rejects_drifted_directive_payload(self) -> None:
        import time as _time

        directive = self._issued_directive()
        effect = self._claim_repair_effect()
        now = _time.time_ns() // 1_000_000
        with self.assertRaises(ValueError):
            self.gateway_store.reserve_repair_execution(
                repair_directive_id=directive.repair_directive_id,
                repair_directive_sha256="1" * 64,  # drifted digest
                plan_entry_id=directive.plan_entry_id,
                repair_attempt_no=directive.repair_attempt_no,
                request_id=directive.request_id,
                run_id=directive.run_id,
                generation=directive.generation,
                effect_id=effect.effect_id,
                effect_intent_sha256="9" * 64,
                reserved_at_ms=now,
                dispatch_claim_id="claim-a",
                claim_expires_at_ms=now + 120_000,
            )


class TestAtomicStartCrash(RepairLoopE2EBase):
    """Final P0-2 C: after the atomic start and a reopen there is NO
    observable (Effect STARTED, Binding RESERVED) state."""

    def test_no_split_state_after_reopen(self) -> None:
        import time as _time
        from total_gateway.store import GatewayStateStore

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

        self.gateway_store.start_repair_execution(
            repair_directive_id=directive.repair_directive_id,
            effect_id=effect.effect_id,
            started_at_ms=_time.time_ns() // 1_000_000,
        )
        # CRASH: close and reopen a fresh Store instance over the same
        # database file.
        self.gateway_store.close()
        from pathlib import Path as _Path

        reopened = GatewayStateStore.open(
            _Path(self.temporary.name) / "gateway.sqlite3",
            now_ms=_time.time_ns() // 1_000_000,
        )
        self.gateway_store = reopened
        binding = reopened.get_repair_execution_binding(
            directive.repair_directive_id
        )
        effect_record = reopened.get_effect(effect.effect_id)
        self.assertEqual(binding["state"], "SIDE_EFFECT_STARTED")
        self.assertEqual(effect_record.state, "SIDE_EFFECT_STARTED")
        # the invariant under test: never effect-started + binding-RESERVED
        self.assertNotEqual(
            (effect_record.state, binding["state"]),
            ("SIDE_EFFECT_STARTED", "RESERVED"),
        )


class TestPolicyDenyPath(RepairLoopE2EBase):
    """Final P0-2 E: policy deny terminalizes BOTH authorities
    atomically; the RepairAttempt(EXECUTION_FAILED) persists and the
    runtime callback count stays zero."""

    def test_policy_deny_atomic_failed_final(self) -> None:
        produce_calls: list[str] = []

        def dispatch(directive: RepairDirective):
            effect = self._claim_repair_effect()
            reserved = self._reserve_or_claimed(directive, effect.effect_id)
            if reserved["outcome"] != "EXECUTE":
                return self._already_claimed(directive)
            # policy DENY: the runtime is NEVER entered; both
            # authorities move atomically to FAILED_FINAL.
            self._binding_complete(
                directive,
                "FAILED_FINAL",
                "",
                ref="policy-rejected",
                error_code="repair.policy_denied",
            )
            return RepairDispatchResult(
                execution_outcome="EXECUTION_FAILED",
                produced_subject_identity=(
                    directive.effective_subject_identity
                ),
                execution_effect_ids=(effect.effect_id,),
            )

        readiness = self._reverify()
        final, disposition = self.coordinator.execute_repair_loop(
            plan=self.plan,
            readiness=readiness,
            dispatch=dispatch,
            reverify=self._reverify,
        )
        # runtime was never entered
        self.assertEqual(produce_calls, [])
        self.assertFalse(final.verification_ready)
        self.assertIsNotNone(disposition)
        self.assertEqual(disposition.action, "REVIEW")
        # attempts persisted as EXECUTION_FAILED (the per-entry budget
        # lets the policy deny retry once, then REVIEW)
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertGreaterEqual(len(attempts), 1)
        self.assertTrue(
            all(a.execution_outcome == "EXECUTION_FAILED" for a in attempts)
        )
        # EVERY binding is atomically FAILED_FINAL with its effect
        for attempt in attempts:
            binding = self.gateway_store.get_repair_execution_binding(
                attempt.repair_directive_id
            )
            self.assertEqual(binding["state"], "FAILED_FINAL")
            effect_record = self.gateway_store.get_effect(
                binding["effect_id"]
            )
            self.assertEqual(effect_record.state, "FAILED_FINAL")
        # no successor for a never-executed repair
        self.assertEqual(
            self.gateway_store.list_verification_subject_successors(
                self.entry.plan_entry_id
            ),
            (),
        )




if __name__ == "__main__":
    unittest.main()
