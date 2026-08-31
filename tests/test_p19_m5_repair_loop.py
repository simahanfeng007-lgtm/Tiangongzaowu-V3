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

ROOT = Path(__file__).resolve().parents[1]

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document"
)


class RepairLoopE2EBase(M21OracleTestBase):
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
        self.plan = VerificationPlan(
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

    # -- helpers ----------------------------------------------------------

    def _next_ms(self) -> int:
        self._clock += 1_000
        return self._clock

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
                claimed_at_ms=self._next_ms(),
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
        """Scripted runtime: repairs through the REAL gate/QC pipeline."""
        good = self._passed_manifest(
            docx_bytes("字" * 300),
            filename="report.docx",
            format_id="docx",
            declared_mime=DOCX_MIME,
        )
        self.manifests.append(good)
        effect = self._claim_repair_effect()
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
            still_bad = self._passed_manifest(
                docx_bytes("字" * 10),
                filename="report.docx",
                format_id="docx",
                declared_mime=DOCX_MIME,
            )
            self.manifests.append(still_bad)
            effect = self._claim_repair_effect()
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

        # Exactly one executed attempt; the pre-crash directive plus one
        # re-issued directive — per-entry budget respected.
        attempts = self.gateway_store.list_repair_attempts(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(attempts), 1)
        directives = self.gateway_store.list_repair_directives(
            self.entry.plan_entry_id
        )
        self.assertEqual(len(directives), 2)


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
            "complete_effect",
            "_register_repair_artifacts(",
            "ArtifactGate(",
        ):
            self.assertIn(needle, method)


if __name__ == "__main__":
    unittest.main()
