from __future__ import annotations

import concurrent.futures
import threading
from unittest import mock

import pytest

from contracts.verification import (
    AcceptancePredicate,
    VerificationPlan,
    VerificationPlanEntryV2,
    derive_verification_record_id,
)
from tests.test_docx_qc import docx_bytes
from tests.test_p19_m2_1_artifact_oracle import M21OracleTestBase
from total_gateway.completion_gate import (
    CompletionDecision,
    CompletionGate,
    CompletionGateError,
    CompletionRequirements,
)
from total_gateway.store import GatewayStateStore, StoreConflictError
from total_gateway.verification_readiness import build_readiness


DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


class TestVerificationReadinessAtomicP7D2(M21OracleTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._register_lineage_request()
        self.gateway_store.put_registry_snapshot(
            self.snapshot,
            recorded_at_ms=1_500,
        )
        self.manifest = self._passed_manifest(
            docx_bytes("字" * 50),
            filename="stable-clock.docx",
            format_id="docx",
            declared_mime=DOCX_MIME,
        )
        self.predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": 10},
        )
        self.entry = VerificationPlanEntryV2(
            plan_entry_id="vpe_" + "0" * 64,
            verifier_id="verifier.artifact_content",
            verifier_version="3",
            predicate=self.predicate,
            subject_identity=self.manifest.artifact_revision_id,
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
            entries=(self.entry,),
            plan_sha256="0" * 64,
        ).with_computed_sha256()
        self.gateway_store.put_verification_plan(
            self.plan,
            recorded_at_ms=1_600,
        )
        self.gateway_store.activate_verification_plan(
            request_id=self.plan.request_id,
            run_id=self.plan.run_id,
            generation=self.plan.generation,
            verification_plan_id=self.plan.verification_plan_id,
            verification_plan_sha256=self.plan.plan_sha256,
            registry_snapshot_sha256=self.plan.registry_snapshot_sha256,
            activated_at_ms=1_700,
        )

    def _record(self, status: str, evaluated_at_ms: int):
        record = self.oracle.evaluate(
            self.manifest,
            self.predicate,
            evaluated_at_ms=evaluated_at_ms,
            evaluation_phase=self.entry.evaluation_phase,
        )
        if status != record.status:
            record = record.model_copy(
                update={
                    "verification_record_id": "vrs_" + "0" * 64,
                    "status": status,
                    "reason_codes": ("late_authoritative_failure",),
                    "result_sha256": "0" * 64,
                }
            ).with_computed_sha256()
            record = record.model_copy(
                update={
                    "verification_record_id": derive_verification_record_id(
                        result_sha256=record.result_sha256
                    )
                }
            )
        return record

    def _readiness(self, evaluated_at_ms: int, *, exact: bool = False):
        return build_readiness(
            plan=self.plan,
            snapshot=self.snapshot,
            store=self.gateway_store,
            evaluated_at_ms=evaluated_at_ms,
            exact_evaluated_at_ms=exact,
        )

    def _completed_decision(self, readiness) -> CompletionDecision:
        return CompletionDecision(
            request_id=self.plan.request_id,
            run_id=self.plan.run_id,
            generation=self.plan.generation,
            outcome="COMPLETED",
            reason_code="completion.requirements_satisfied",
            text_ready=True,
            execution_ready=True,
            artifacts_ready=True,
            delivery_ready=True,
            can_transition_request_completed=True,
            can_claim_platform_delivered=False,
            needs_reconciliation=False,
            execution_effect_states=(),
            artifact_revision_states=(),
            delivery_parts=(),
            supporting_fact_ids=(),
            candidate_text_sha256="a" * 64,
            verification_mode="PLAN_BOUND",
            verification_ready=readiness.verification_ready,
            verification_plan_sha256=self.plan.plan_sha256,
            verification_readiness_id=readiness.verification_readiness_id,
            verification_readiness_sha256=readiness.readiness_sha256,
            decision_sha256="0" * 64,
        ).with_computed_sha256()

    def test_readiness_never_reads_future_and_exact_clock_rejects_older_pass(
        self,
    ) -> None:
        self.gateway_store.put_verification_record(
            self._record("PASS", 30_000),
            recorded_at_ms=30_000,
        )
        self.gateway_store.put_verification_record(
            self._record("FAIL", 32_000),
            recorded_at_ms=32_000,
        )

        causal = self._readiness(31_000)
        exact = self._readiness(31_000, exact=True)
        current = self._readiness(32_000)

        assert causal.verification_ready
        assert causal.entry_assessments[0].status == "PASS"
        assert not exact.verification_ready
        assert exact.entry_assessments[0].status == "MISSING"
        assert not current.verification_ready
        assert current.entry_assessments[0].status == "FAIL"
        with pytest.raises(
            ValueError,
            match="authoritative derivation|superseded by a later record",
        ):
            self.gateway_store.put_verification_readiness(
                causal,
                recorded_at_ms=32_001,
                require_exact_evaluated_at_ms=True,
            )

    def test_readiness_insert_and_terminal_completion_fence_late_fail(
        self,
    ) -> None:
        stable_at_ms = 30_000
        self.gateway_store.put_verification_record(
            self._record("PASS", stable_at_ms),
            recorded_at_ms=stable_at_ms,
        )
        readiness = self._readiness(stable_at_ms, exact=True)
        late_fail = self._record("FAIL", stable_at_ms + 1)
        second_store = GatewayStateStore.open(
            self.gateway_store.path,
            now_ms=stable_at_ms,
        )
        entered_derivation = threading.Event()
        release_derivation = threading.Event()
        writer_started = threading.Event()
        real_build = build_readiness

        def blocked_build(**kwargs):
            result = real_build(**kwargs)
            entered_derivation.set()
            assert release_derivation.wait(timeout=5)
            return result

        def write_late_fail():
            writer_started.set()
            return second_store.put_verification_record(
                late_fail,
                recorded_at_ms=stable_at_ms + 1,
            )

        try:
            with mock.patch(
                "total_gateway.verification_readiness.build_readiness",
                side_effect=blocked_build,
            ):
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=2
                ) as pool:
                    readiness_write = pool.submit(
                        self.gateway_store.put_verification_readiness,
                        readiness,
                        recorded_at_ms=stable_at_ms,
                        require_exact_evaluated_at_ms=True,
                    )
                    assert entered_derivation.wait(timeout=5)
                    record_write = pool.submit(write_late_fail)
                    assert writer_started.wait(timeout=5)
                    assert not record_write.done()
                    release_derivation.set()
                    assert readiness_write.result(timeout=5)
                    assert record_write.result(timeout=5).created_by_this_call

            assert self.gateway_store.get_latest_verification_readiness(
                request_id=self.plan.request_id,
                run_id=self.plan.run_id,
                generation=self.plan.generation,
                require_authoritative=True,
            ) is None

            with pytest.raises(
                CompletionGateError,
                match="completion.verification.readiness_not_current",
            ):
                CompletionGate(
                    self.object_store,
                    self.fact_ledger,
                ).evaluate(
                    CompletionRequirements(
                        request_id=self.plan.request_id,
                        run_id=self.plan.run_id,
                        generation=self.plan.generation,
                        text_required=True,
                        verification_mode="PLAN_BOUND",
                    ),
                    candidate_text="stable completion",
                    active_plan=self.plan,
                    verification_readiness=readiness,
                    verification_dispositions=(),
                    verification_failure_evidences=(),
                    disposition_authority_reader=lambda _item: None,
                    readiness_authority_reader=(
                        self.gateway_store.get_latest_verification_readiness
                    ),
                )

            with pytest.raises(
                StoreConflictError,
                match="superseded by a later record",
            ):
                self.gateway_store.record_completion_decision(
                    self._completed_decision(readiness),
                    recorded_at_ms=stable_at_ms + 2,
                )
        finally:
            release_derivation.set()
            second_store.close()

    def test_terminal_completion_seals_readiness_materialization(self) -> None:
        stable_at_ms = 30_000
        self.gateway_store.put_verification_record(
            self._record("PASS", stable_at_ms),
            recorded_at_ms=stable_at_ms,
        )
        readiness = self._readiness(stable_at_ms, exact=True)
        assert self.gateway_store.put_verification_readiness(
            readiness,
            recorded_at_ms=stable_at_ms,
            require_exact_evaluated_at_ms=True,
        )
        self.gateway_store.record_completion_decision(
            self._completed_decision(readiness),
            recorded_at_ms=stable_at_ms + 1,
        )

        # Idempotent persistence of the exact sealed readiness remains legal.
        assert not self.gateway_store.put_verification_readiness(
            readiness,
            recorded_at_ms=stable_at_ms + 2,
            require_exact_evaluated_at_ms=True,
        )

        # With no record at the later exact clock this is a valid MISSING
        # materialization, but it may not supersede a terminal decision's
        # readiness identity/hash.
        later = self._readiness(stable_at_ms + 2, exact=True)
        assert not later.verification_ready
        assert later.entry_assessments[0].status == "MISSING"
        with pytest.raises(StoreConflictError, match="sealed by terminal"):
            self.gateway_store.put_verification_readiness(
                later,
                recorded_at_ms=stable_at_ms + 2,
                require_exact_evaluated_at_ms=True,
            )

        latest = self.gateway_store.get_latest_verification_readiness(
            request_id=self.plan.request_id,
            run_id=self.plan.run_id,
            generation=self.plan.generation,
            require_authoritative=True,
        )
        assert latest == readiness
