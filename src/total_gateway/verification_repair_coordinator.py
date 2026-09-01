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

import dataclasses
import time
from typing import Any, Callable

from contracts.verification import (
    FailureEvidence,
    RepairDirective,
    VerificationDisposition,
    VerificationPlan,
    VerificationReadiness,
)
from contracts.verification_repair import (
    RepairAttemptRecord,
    VerificationSubjectSuccessor,
)
from total_gateway.verification_failure_evidence import build_failure_evidence
from total_gateway.verification_repair_policy import (
    DEFAULT_POLICY,
    RepairPolicyConfig,
    compute_disposition_action,
)


class RepairCoordinatorError(RuntimeError):
    """Repair loop failure — never falls back to NONE."""


@dataclasses.dataclass(frozen=True)
class RepairDispatchResult:
    """Outcome of one RepairDirective executed by the EXISTING runtime.

    ``execution_outcome`` is the runtime-level verdict: DISPATCHED when
    execution completed; EXECUTION_FAILED / EXECUTION_AMBIGUOUS when the
    runtime itself failed; ALREADY_CLAIMED when the Store dispatch
    boundary denied this worker (another coordinator owns the attempt —
    the caller must not have executed the runtime). The re-verification
    verdict is derived later from the NEW readiness and recorded on the
    attempt.
    """

    execution_outcome: str  # DISPATCHED | EXECUTION_FAILED | EXECUTION_AMBIGUOUS | ALREADY_CLAIMED
    produced_subject_identity: str
    execution_effect_ids: tuple[str, ...] = ()


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
            # M5 Final #2: the FE signature must describe the CURRENT
            # effective subject (Store successor chain), not the original
            # plan subject.
            effective_subject_resolver=(
                lambda plan_entry_id: (
                    self._store.resolve_verification_subject(
                        plan_entry_id
                    ).get("effective_subject_identity")
                    if hasattr(
                        self._store, "resolve_verification_subject"
                    )
                    else None
                )
            ),
            observed_at_ms=now_ms,
        )

        dispositions: list[VerificationDisposition] = []
        # M5 Final Correction #5: generation total budget (across ALL entries)
        generation_repair_count = sum(
            1 for d in self._store.list_all_verification_dispositions(
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
            )
            if d.action == "REPAIR"
        ) if hasattr(self._store, "list_all_verification_dispositions") else 0

        for fe in failure_evidences:
            # P1-7: attempt_no counts REAL executed attempts (Store
            # attempt table), not issued REPAIR dispositions — repeated
            # crashes before dispatch must not burn the budget.
            executed_attempts = self._store.list_repair_attempts(
                fe.plan_entry_id
            )
            attempt_no = len(executed_attempts)
            prev_dispositions = self._store.list_verification_dispositions(
                plan_entry_id=fe.plan_entry_id,
            )

            # Count same failure signature occurrences — de-duplicated
            # by readiness: re-deriving the SAME failure after a crash
            # (same readiness) is not a NEW failure observation. The
            # count INCLUDES the evidence under construction (minus 1),
            # matching the Store revalidation's post-insert view exactly.
            prev_failures = self._store.list_verification_failure_evidence(
                plan_entry_id=fe.plan_entry_id,
            )
            same_sig_count = len(
                {
                    f.readiness_sha256
                    for f in prev_failures
                    if f.failure_signature_sha256 == fe.failure_signature_sha256
                }
                | {fe.readiness_sha256}
            ) - 1

            # M5 Final #5: successor depth budget
            successor_depth = 0
            if hasattr(self._store, "resolve_verification_subject"):
                resolution = self._store.resolve_verification_subject(
                    fe.plan_entry_id
                )
                successor_depth = resolution.get("successor_depth", 0)

            # M5 Final #5: side-effecting repair count (effect/repo
            # subjects) — also counts only REAL executed attempts.
            side_effect_repair_count = (
                attempt_no
                if fe.subject_kind in ("effect", "repository")
                else 0
            )

            # Effect ambiguity check (M5 Final #22: fail-closed).
            # P0-1: an earlier repair attempt whose runtime outcome was
            # AMBIGUOUS counts as ambiguity for THIS entry regardless of
            # the FE subject kind — an artifact repair whose runtime
            # dispatch landed in an ambiguous state must never be
            # blindly replayed either.
            effect_ambiguous = (
                self._check_effect_ambiguity(fe)
                or self._entry_has_ambiguous_attempt(fe.plan_entry_id)
            )

            # M5 Final #4/#5: single source of truth — the same
            # budget-aware decision the Store revalidation recomputes.
            action, reasons = compute_disposition_action(
                predicate_type=fe.predicate_type,
                verification_status=fe.verification_status,
                failure_kind=fe.failure_kind,
                attempt_no=attempt_no,
                same_signature_count=same_sig_count,
                successor_depth=successor_depth,
                generation_repair_count=generation_repair_count,
                side_effect_repair_count=side_effect_repair_count,
                subject_kind=fe.subject_kind,
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
            # P1-8: increment the in-round generation budget so a single
            # readiness with N repairable entries cannot see the same
            # pre-round count N times (the Store recomputes it too, but
            # the policy stop must be a normal REVIEW, not a write
            # rejection).
            if action == "REPAIR":
                generation_repair_count += 1

        return dispositions

    # ------------------------------------------------------------------
    # M5 Final #2: the FULL repair loop — directive → EXISTING runtime →
    # successor → SAME-predicate re-verification with NEW evidence.
    # ------------------------------------------------------------------

    def execute_repair_loop(
        self,
        *,
        plan: VerificationPlan,
        readiness: VerificationReadiness,
        dispatch: Callable[[RepairDirective], RepairDispatchResult],
        reverify: Callable[[], VerificationReadiness],
        dispatchable_subject_kinds: tuple[str, ...] = (
            "artifact",
            "effect",
            "repository",
        ),
        now_ms: int | None = None,
    ) -> tuple[VerificationReadiness, VerificationDisposition | None]:
        """§14-§21 full evidence-driven repair loop.

        ``dispatch`` bridges to the EXISTING runtime (caller-supplied; the
        coordinator never owns a second runtime). ``reverify`` re-runs the
        SAME verification plan producing NEW independent records.

        ``dispatchable_subject_kinds`` gates WHICH subject kinds may be
        auto-repaired in this channel context. Channel finalization runs
        after the delivery boundary, so callers there restrict execution
        (or pass an empty tuple) — a REPAIR disposition that is not
        dispatchable here is returned untouched (the Gate keeps the
        request IN_PROGRESS) and is NEVER blindly replayed.

        P0-1 safety: a dispatch whose runtime outcome is AMBIGUOUS stops
        the loop immediately — the attempt is persisted, the disposition
        flips to RECONCILE (the ambiguous attempt is now visible to the
        deterministic policy), and ZERO further repair attempts run.

        Crash recovery: a persisted-but-never-attempted directive for the
        same attempt number is REUSED, not re-issued, so repeated crashes
        before dispatch cannot burn the repair budget.

        Termination is guaranteed twice over: every REPAIR iteration
        increments Store-persisted attempt counts (policy flips REPAIR →
        REVIEW once any budget is exhausted), and the hard cap bounds the
        loop independently of Store state.

        Returns ``(final_readiness, final_disposition_or_None)`` — the
        disposition, when present, is bound to the final readiness.
        """
        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000
        if readiness.verification_ready:
            return readiness, None

        current = readiness
        final_disposition: VerificationDisposition | None = None
        hard_cap = self._policy.max_total_auto_repairs_per_generation + 1

        for _ in range(hard_cap):
            # Each iteration re-stamps time so dispositions/directives/
            # successor bindings stay monotonically ordered in the Store.
            now_ms = time.time_ns() // 1_000_000
            dispositions = self.process_readiness(
                plan=plan, readiness=current, now_ms=now_ms
            )
            if dispositions:
                final_disposition = dispositions[-1]
            kind_by_entry = {
                e.plan_entry_id: e.predicate.subject_kind
                for e in plan.entries
            }
            repairable = [
                d
                for d in dispositions
                if d.action == "REPAIR"
                and kind_by_entry.get(d.plan_entry_id)
                in dispatchable_subject_kinds
            ]
            not_dispatchable = [
                d
                for d in dispositions
                if d.action == "REPAIR"
                and kind_by_entry.get(d.plan_entry_id)
                not in dispatchable_subject_kinds
            ]
            if not repairable:
                # WAIT / RECONCILE / REVIEW / BLOCK — or REPAIR that this
                # context must not auto-execute (e.g. channel-side after
                # an irreversible external side effect). Not replayed.
                if not_dispatchable:
                    final_disposition = not_dispatchable[0]
                return current, final_disposition

            executed_any = False
            for disposition in repairable:
                evidence = self._find_persisted_failure_evidence(disposition)
                directive = self._directive_for_disposition(
                    disposition=disposition,
                    failure_evidence=evidence,
                    plan=plan,
                    now_ms=now_ms,
                )
                if directive is None:
                    continue
                # Final P0-1: the Store-side dispatch boundary decides
                # whether THIS worker may cross into the runtime. The
                # binding survives crashes, so recovery never re-executes
                # a completed runtime and never replays a started one.
                started_ms = time.time_ns() // 1_000_000
                result = self._resolve_dispatch_result(
                    directive=directive, dispatch=dispatch
                )
                if result is None:
                    # Another worker owns this attempt — hand over
                    # control without recording anything.
                    return current, None
                if result.execution_outcome == "EXECUTION_AMBIGUOUS":
                    # P0-1: unknown whether the side effect happened.
                    # Persist the attempt (the Store revalidation and the
                    # policy both read it), re-derive the disposition —
                    # it is now RECONCILE — and stop. Zero replay.
                    self.record_attempt(
                        directive=directive,
                        execution_outcome="EXECUTION_AMBIGUOUS",
                        produced_subject_identity=(
                            result.produced_subject_identity
                        ),
                        execution_effect_ids=result.execution_effect_ids,
                        reverify_record_id=None,
                        started_at_ms=started_ms,
                        finished_at_ms=time.time_ns() // 1_000_000,
                    )
                    reconcile = self.process_readiness(
                        plan=plan, readiness=current, now_ms=now_ms
                    )
                    return current, (reconcile[-1] if reconcile else None)
                if (
                    not result.execution_effect_ids
                    and result.execution_outcome == "DISPATCHED"
                ):
                    raise RepairCoordinatorError(
                        "repair dispatch claims success without an effect"
                        " ledger binding"
                    )
                # Successor binding FIRST: re-verification must resolve
                # the NEW effective subject for the entry (§17).
                if (
                    result.produced_subject_identity
                    and result.produced_subject_identity
                    != directive.effective_subject_identity
                ):
                    self._bind_successor(
                        directive=directive, result=result, now_ms=now_ms
                    )
                # SAME predicate, NEW independent evidence (§21).
                current = reverify()
                reverify_record_id = self._latest_record_for_entry(
                    plan, directive.plan_entry_id
                )
                self.record_attempt(
                    directive=directive,
                    execution_outcome=self._attempt_outcome(
                        directive=directive,
                        dispatch_result=result,
                        readiness=current,
                    ),
                    produced_subject_identity=result.produced_subject_identity,
                    execution_effect_ids=result.execution_effect_ids,
                    reverify_record_id=reverify_record_id,
                    started_at_ms=started_ms,
                    finished_at_ms=time.time_ns() // 1_000_000,
                )
                executed_any = True
                if current.verification_ready:
                    return current, None

            if not executed_any:
                return current, final_disposition

        # Hard cap reached — the next process_readiness pass would flip
        # every remaining REPAIR to REVIEW; surface the final state.
        return current, final_disposition

    def _directive_for_disposition(
        self,
        *,
        disposition: VerificationDisposition,
        failure_evidence: FailureEvidence,
        plan: VerificationPlan,
        now_ms: int,
    ):
        """Issue a directive, or REUSE a pending one (crash recovery).

        A persisted directive for the same attempt number with no
        recorded attempt and un-expired is executed as-is — repeated
        crashes before dispatch must not consume the repair budget.
        """
        pending = self._find_pending_directive(disposition, now_ms=now_ms)
        if pending is not None:
            return pending
        return self.issue_repair_directive(
            disposition=disposition,
            failure_evidence=failure_evidence,
            plan=plan,
            now_ms=now_ms,
        )

    def _find_pending_directive(
        self, disposition: VerificationDisposition, *, now_ms: int
    ):
        directives = self._store.list_repair_directives(
            disposition.plan_entry_id
        )
        attempted = {
            attempt.repair_directive_id
            for attempt in self._store.list_repair_attempts(
                disposition.plan_entry_id
            )
        }
        expected_no = disposition.attempt_no + 1
        for directive in reversed(directives):
            if (
                directive.repair_attempt_no == expected_no
                and directive.repair_directive_id not in attempted
                and directive.expires_at_ms > now_ms
            ):
                # The disposition may be a re-derived copy (new
                # decided_at) — what must match is the attempt number,
                # the absence of any recorded attempt, and validity.
                return directive
        return None

    def _entry_has_ambiguous_attempt(self, plan_entry_id: str) -> bool:
        """P0-1: once a repair dispatch ended AMBIGUOUS for this entry,
        the deterministic policy must see ambiguity forever after."""
        for attempt in self._store.list_repair_attempts(plan_entry_id):
            if attempt.execution_outcome == "EXECUTION_AMBIGUOUS":
                return True
        return False

    def _resolve_dispatch_result(
        self,
        *,
        directive: RepairDirective,
        dispatch: Callable[[RepairDirective], RepairDispatchResult],
    ) -> RepairDispatchResult | None:
        """Apply the Repair Execution Binding state machine.

        Returns None when another worker owns this attempt (hand over),
        otherwise the dispatch result. Recovery semantics (locked):
        - no binding / RESERVED (same directive) → run the runtime
          bridge (the bridge itself reserves atomically; a loser gets
          ALREADY_CLAIMED and we hand over);
        - SIDE_EFFECT_STARTED / AMBIGUOUS → EXECUTION_AMBIGUOUS result
          WITHOUT calling the runtime (never replay a crossed boundary);
        - SUCCEEDED → rebuild the result from the binding's persisted
          produced subject — no runtime call;
        - FAILED_FINAL → EXECUTION_FAILED result, no runtime call (a
          NEW attempt number may be authorized by the policy later).
        """
        binding = None
        if hasattr(
            self._store, "get_repair_execution_binding_by_attempt"
        ):
            binding = self._store.get_repair_execution_binding_by_attempt(
                directive.plan_entry_id, directive.repair_attempt_no
            )
        if binding is not None:
            state = str(binding["state"])
            effect_id = str(binding["effect_id"])
            if binding["repair_directive_id"] != (
                directive.repair_directive_id
            ):
                # A different directive owns this attempt slot.
                return None
            if state == "RESERVED":
                pass  # continue below — same directive, pre-boundary
            elif state in ("SIDE_EFFECT_STARTED", "AMBIGUOUS"):
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_AMBIGUOUS",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(effect_id,),
                )
            elif state == "SUCCEEDED":
                return RepairDispatchResult(
                    execution_outcome="DISPATCHED",
                    produced_subject_identity=str(
                        binding["produced_subject_identity"]
                    ),
                    execution_effect_ids=(effect_id,),
                )
            elif state == "FAILED_FINAL":
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_FAILED",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(effect_id,),
                )
        result = dispatch(directive)
        if result.execution_outcome == "ALREADY_CLAIMED":
            return None
        return result

    def _find_persisted_failure_evidence(
        self, disposition: VerificationDisposition
    ) -> FailureEvidence:
        stored = self._store.list_verification_failure_evidence(
            plan_entry_id=disposition.plan_entry_id
        )
        for evidence in stored:
            if (
                evidence.failure_evidence_id
                == disposition.failure_evidence_id
            ):
                return evidence
        raise RepairCoordinatorError(
            "disposition references failure evidence absent from the store"
        )

    def _bind_successor(
        self,
        *,
        directive: RepairDirective,
        result: RepairDispatchResult,
        now_ms: int,
    ) -> None:
        # Idempotent short-circuit: a crash-recovery replay of the SAME
        # successor transition (same attempt number already bound) must
        # not re-bind or fork the chain.
        if hasattr(self._store, "list_verification_subject_successors"):
            for existing in self._store.list_verification_subject_successors(
                directive.plan_entry_id
            ):
                if existing.repair_attempt_no == directive.repair_attempt_no:
                    return
        if not result.execution_effect_ids:
            raise RepairCoordinatorError(
                "subject successor requires a produced_by_effect binding"
            )
        successor = VerificationSubjectSuccessor(
            successor_binding_id="vss_" + "0" * 64,
            request_id=directive.request_id,
            run_id=directive.run_id,
            generation=directive.generation,
            verification_plan_id=directive.verification_plan_id,
            plan_entry_id=directive.plan_entry_id,
            subject_kind=directive.subject_kind,
            predecessor_subject_identity=directive.effective_subject_identity,
            successor_subject_identity=result.produced_subject_identity,
            repair_directive_id=directive.repair_directive_id,
            repair_directive_sha256=directive.directive_sha256,
            produced_by_effect_id=result.execution_effect_ids[0],
            repair_attempt_no=directive.repair_attempt_no,
            bound_at_ms=now_ms,
            successor_binding_sha256="0" * 64,
        ).with_computed_sha256()
        self._store.put_verification_subject_successor(
            successor, recorded_at_ms=now_ms
        )

    def _latest_record_for_entry(
        self, plan: VerificationPlan, plan_entry_id: str
    ) -> str | None:
        entry = next(
            (e for e in plan.entries if e.plan_entry_id == plan_entry_id),
            None,
        )
        if entry is None:
            return None
        # Records bind to (predicate_id, subject_identity) — resolve the
        # CURRENT effective subject so post-repair records are found.
        effective_subject = entry.subject_identity
        if hasattr(self._store, "resolve_verification_subject"):
            resolution = self._store.resolve_verification_subject(
                plan_entry_id
            )
            if resolution.get("effective_subject_identity"):
                effective_subject = resolution["effective_subject_identity"]
        records = self._store.list_verification_records(
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
        )
        matching = [
            r
            for r in records
            if r.predicate_id == entry.predicate.predicate_id
            and r.subject_identity == effective_subject
        ]
        if not matching:
            return None
        return max(
            matching,
            key=lambda r: (r.evaluated_at_ms, r.verification_record_id),
        ).verification_record_id

    @staticmethod
    def _attempt_outcome(
        *,
        directive: RepairDirective,
        dispatch_result: RepairDispatchResult,
        readiness: VerificationReadiness,
    ) -> str:
        if dispatch_result.execution_outcome in (
            "EXECUTION_FAILED",
            "EXECUTION_AMBIGUOUS",
        ):
            return dispatch_result.execution_outcome
        assessment = next(
            (
                a
                for a in readiness.entry_assessments
                if a.plan_entry_id == directive.plan_entry_id
            ),
            None,
        )
        if assessment is None:
            return "REVERIFY_ERROR"
        if assessment.status == "PASS":
            return "REVERIFY_PASS"
        if assessment.status == "FAIL":
            return "REVERIFY_FAIL"
        return "REVERIFY_ERROR"

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
        """§22: fail-closed — if Effect authority query fails, treat as
        ambiguous (RECONCILE), never as 'not ambiguous'."""
        if fe.subject_kind != "effect":
            return False
        try:
            record = self._store.get_effect(fe.effective_subject_identity)
            if record is None:
                return False  # effect doesn't exist → not ambiguous, just missing
            return record.state in ("AMBIGUOUS", "SIDE_EFFECT_STARTED")
        except Exception:
            # M5 Final #22: authority query failure → fail-closed → treat
            # as ambiguous → disposition becomes RECONCILE
            return True


__all__ = [
    "RepairCoordinatorError",
    "RepairDispatchResult",
    "VerificationRepairCoordinator",
]
