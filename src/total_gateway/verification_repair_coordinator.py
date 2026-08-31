"""P19-R2 M5 §23: VerificationRepairCoordinator.

Narrow Gateway component that orchestrates the evidence-driven repair
loop. It reads the active plan, derives FailureEvidence, runs the
deterministic RepairPolicy, persists Dispositions, and (when REPAIR)
issues RepairDirectives through the EXISTING execution path.

Architecture boundary: NOT a Runtime, NOT a CompletionGate, NOT a
second execution system. All reality changes go through the existing
Runtime / Tool authority. The coordinator only decides and dispatches.
"""

from __future__ import annotations

import time
from typing import Any

from contracts.verification import (
    FailureEvidence,
    RepairDirective,
    VerificationDisposition,
    VerificationPlan,
    VerificationReadiness,
)
from contracts.verification_repair import RepairAttemptRecord
from total_gateway.verification_failure_evidence import build_failure_evidence
from total_gateway.verification_repair_policy import (
    DEFAULT_POLICY,
    RepairPolicyConfig,
    evaluate_disposition,
)


class RepairCoordinatorError(RuntimeError):
    """Repair loop failure — never falls back to NONE."""


class VerificationRepairCoordinator:
    """Coordinates: plan → readiness → failure evidence → disposition → directive.

    §23: belongs to Total Gateway. Dispatches through the EXISTING
    execution path (the caller provides the dispatch callback).
    """

    def __init__(
        self,
        *,
        store,
        policy: RepairPolicyConfig | None = None,
    ) -> None:
        self._store = store
        self._policy = policy or DEFAULT_POLICY

    def process_readiness(
        self,
        *,
        plan: VerificationPlan,
        readiness: VerificationReadiness,
        now_ms: int | None = None,
    ) -> list[VerificationDisposition]:
        """Derive failure evidence + dispositions for all failed entries.

        Returns the list of dispositions (REPAIR directives are issued
        via ``issue_repair_directive``).
        """
        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000

        if readiness.verification_ready:
            return []  # PASS — no dispositions needed (§10)

        failure_evidences = build_failure_evidence(
            plan=plan,
            readiness=readiness,
            store=self._store,
            observed_at_ms=now_ms,
        )

        dispositions: list[VerificationDisposition] = []
        for fe in failure_evidences:
            # Count previous dispositions for this entry (attempt tracking)
            prev_dispositions = self._store.list_verification_dispositions(
                plan_entry_id=fe.plan_entry_id,
            )
            attempt_no = len(prev_dispositions)

            # Count same failure signature occurrences
            prev_failures = self._store.list_verification_failure_evidence(
                plan_entry_id=fe.plan_entry_id,
            )
            same_sig_count = sum(
                1 for f in prev_failures
                if f.failure_signature_sha256 == fe.failure_signature_sha256
            )

            # Effect ambiguity check
            effect_ambiguous = self._check_effect_ambiguity(fe)

            action, reasons = evaluate_disposition(
                predicate_type=fe.predicate_type,
                verification_status=fe.verification_status,
                failure_kind=fe.failure_kind,
                attempt_no=attempt_no,
                max_attempts=self._policy.max_attempts_per_plan_entry,
                same_signature_count=same_sig_count,
                effect_is_ambiguous=effect_ambiguous,
                policy=self._policy,
            )

            disposition = VerificationDisposition(
                verification_disposition_id="vds_" + "0" * 64,
                request_id=fe.request_id,
                run_id=fe.run_id,
                generation=fe.generation,
                verification_plan_id=fe.verification_plan_id,
                plan_entry_id=fe.plan_entry_id,
                failure_evidence_id=fe.failure_evidence_id,
                failure_evidence_sha256=fe.failure_evidence_sha256,
                action=action,
                policy_version=self._policy.__class__.__name__ and "v1",
                policy_config_sha256=self._policy.config_sha256(),
                attempt_no=attempt_no,
                max_attempts=self._policy.max_attempts_per_plan_entry,
                reason_codes=reasons,
                decided_at_ms=now_ms,
                disposition_sha256="0" * 64,
            ).with_computed_sha256()

            # Persist failure evidence + disposition
            self._store.put_verification_failure_evidence(
                fe, recorded_at_ms=now_ms
            )
            self._store.put_verification_disposition(
                disposition, recorded_at_ms=now_ms
            )
            dispositions.append(disposition)

        return dispositions

    def issue_repair_directive(
        self,
        *,
        disposition: VerificationDisposition,
        failure_evidence: FailureEvidence,
        plan: VerificationPlan,
        now_ms: int | None = None,
        execution_budget_ms: int = 120_000,
        expires_delta_ms: int = 300_000,
    ) -> RepairDirective | None:
        """Issue a RepairDirective only for REPAIR dispositions (§14)."""
        if disposition.action != "REPAIR":
            return None
        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000

        # Find the plan entry
        entry = next(
            (e for e in plan.entries
             if e.plan_entry_id == disposition.plan_entry_id),
            None,
        )
        if entry is None:
            raise RepairCoordinatorError(
                "disposition references unknown plan entry"
            )

        directive = RepairDirective(
            repair_directive_id="vrd_" + "0" * 64,
            request_id=disposition.request_id,
            run_id=disposition.run_id,
            generation=disposition.generation,
            verification_plan_id=plan.verification_plan_id,
            verification_plan_sha256=plan.plan_sha256,
            plan_entry_id=entry.plan_entry_id,
            plan_entry_sha256=entry.entry_sha256,
            failure_evidence_id=failure_evidence.failure_evidence_id,
            failure_evidence_sha256=failure_evidence.failure_evidence_sha256,
            disposition_id=disposition.verification_disposition_id,
            disposition_sha256=disposition.disposition_sha256,
            predicate_id=entry.predicate.predicate_id,
            predicate_sha256=entry.predicate.predicate_sha256,
            subject_kind=entry.predicate.subject_kind,
            original_subject_identity=entry.subject_identity,
            effective_subject_identity=(
                failure_evidence.effective_subject_identity
            ),
            repair_attempt_no=disposition.attempt_no + 1,
            max_attempts=disposition.max_attempts,
            allowed_target_refs=(
                failure_evidence.effective_subject_identity,
            ),
            forbidden_target_refs=(),
            repair_goal_kind=f"repair:{entry.predicate.predicate_type}",
            repair_constraints=(),
            execution_budget_ms=execution_budget_ms,
            requires_reverification=True,
            issued_at_ms=now_ms,
            expires_at_ms=now_ms + expires_delta_ms,
            directive_sha256="0" * 64,
        ).with_computed_sha256()

        self._store.put_repair_directive(directive, recorded_at_ms=now_ms)
        return directive

    def record_attempt(
        self,
        *,
        directive: RepairDirective,
        execution_outcome: str,
        produced_subject_identity: str,
        execution_effect_ids: tuple[str, ...] = (),
        reverify_record_id: str | None = None,
        started_at_ms: int,
        finished_at_ms: int,
    ) -> RepairAttemptRecord:
        """Record one repair attempt outcome (§20)."""
        attempt = RepairAttemptRecord(
            repair_attempt_id="vra_" + "0" * 64,
            repair_directive_id=directive.repair_directive_id,
            repair_attempt_no=directive.repair_attempt_no,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            plan_entry_id=directive.plan_entry_id,
            prior_subject_identity=directive.effective_subject_identity,
            produced_subject_identity=produced_subject_identity,
            execution_effect_ids=execution_effect_ids,
            execution_outcome=execution_outcome,
            reverify_record_id=reverify_record_id,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
            attempt_sha256="0" * 64,
        ).with_computed_sha256()
        self._store.put_repair_attempt(attempt, recorded_at_ms=finished_at_ms)
        return attempt

    def _check_effect_ambiguity(self, fe: FailureEvidence) -> bool:
        """§29: check if the subject effect is in AMBIGUOUS state."""
        if fe.subject_kind != "effect":
            return False
        try:
            record = self._store.get_effect(fe.effective_subject_identity)
            if record is None:
                return False
            return record.state in ("AMBIGUOUS", "SIDE_EFFECT_STARTED")
        except Exception:
            return False  # can't determine → let policy handle by failure_kind


__all__ = [
    "RepairCoordinatorError",
    "VerificationRepairCoordinator",
]
