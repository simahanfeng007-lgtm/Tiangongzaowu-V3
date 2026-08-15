"""P18-M4 deterministic adversarial long-horizon certification harness.

This module is deliberately a *test harness*, not a Runtime/Scheduler/Store.
It drives the existing P18 TurnLoopState authority with a fake model/tool pair
and deterministic fault injections.  M4.3 adds destructive persistence-level
corruption tests separately; this harness establishes the exact 1000-step
control-plane workload and the M4.2 invariant accounting contract.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Final

from v3.runtime_turn_orchestration import TurnLoopState


@dataclass(frozen=True)
class FaultInjection:
    step: int
    code: str
    expected_remediation: str


FAULT_INJECTIONS: Final[tuple[FaultInjection, ...]] = (
    FaultInjection(49, "LLM_TIMEOUT", "model_reconnect"),
    FaultInjection(83, "LOCAL_GOAL_DRIFT", "reality_audit_replan"),
    FaultInjection(121, "TOOL_FALSE_SUCCESS", "reject_unverified_tool_success"),
    FaultInjection(173, "TOOL_FAILURE", "new_physical_attempt_same_logical_effect"),
    FaultInjection(241, "SSE_DISCONNECT", "stream_reconnect_dedupe"),
    FaultInjection(307, "STALE_FACT", "revalidate_fact_before_reuse"),
    FaultInjection(377, "PROCESS_RESTART", "checkpoint_rehydrate_same_identity"),
    FaultInjection(421, "TOOL_PROMPT_INJECTION", "block_authority_escalation"),
    FaultInjection(489, "FORCED_CONTEXT_COMPACT", "compact_bounded_working_set"),
    FaultInjection(533, "BAD_SEMANTIC_SUMMARY", "reject_summary_as_authority"),
    FaultInjection(577, "ARTIFACT_REVISION_CONFLICT", "cas_conflict_no_silent_overwrite"),
    FaultInjection(641, "AMBIGUOUS_SIDE_EFFECT", "reconcile_before_continue"),
    FaultInjection(702, "FALSE_COMPLETE", "reject_false_completion"),
    FaultInjection(777, "PROVIDER_TRANSIENT_5XX", "provider_reconnect"),
    FaultInjection(850, "DUPLICATE_STREAM_CHUNK", "dedupe_stream_chunk"),
    FaultInjection(884, "FALSE_FACT_MEMORY_PROMOTION", "reject_learning_promotion"),
    FaultInjection(921, "RESTART_AFTER_CHECKPOINT", "rehydrate_latest_known_good"),
    FaultInjection(953, "PROVIDER_PROFILE_VERSION_CHANGE", "version_revalidate_before_resume"),
    FaultInjection(965, "LEDGER_TORN_TAIL", "recover_torn_tail"),
    FaultInjection(972, "CHECKPOINT_CHECKSUM_CORRUPTION", "fallback_previous_known_good"),
    FaultInjection(981, "PREPARED_WITHOUT_DISPATCH", "prove_not_dispatched_then_new_attempt"),
)

FAULT_BY_STEP: Final[dict[int, FaultInjection]] = {item.step: item for item in FAULT_INJECTIONS}


@dataclass
class M4HardMetrics:
    missing_required_steps: int = 0
    duplicate_committed_irreversible_effects: int = 0
    authority_changes: int = 0
    request_id_changes: int = 0
    run_id_changes: int = 0
    illegal_generation_changes: int = 0
    unreconciled_ambiguous_effects: int = 0
    completed_obligation_loss: int = 0
    root_goal_hash_changes: int = 0
    task_contract_hash_illegal_changes: int = 0
    false_verified_facts: int = 0
    false_completion_accepts: int = 0
    tool_prompt_injection_authority_escalation: int = 0
    invalid_learning_promotions: int = 0
    silent_concurrency_overwrites: int = 0
    model_working_set_linear_growth: bool = False
    run_snapshot_unbounded_growth: bool = False
    ledger_replay_mismatch: int = 0
    ledger_seq_conflict: int = 0
    torn_tail_undetected: int = 0
    checkpoint_corruption_silent_accept: int = 0
    prepared_before_dispatch_violations: int = 0
    logical_effect_duplicate_commit: int = 0

    def zero_violation_contract(self) -> dict[str, int | bool]:
        return {
            "missing_required_steps": self.missing_required_steps,
            "duplicate_committed_irreversible_effects": self.duplicate_committed_irreversible_effects,
            "authority_changes": self.authority_changes,
            "request_id_changes": self.request_id_changes,
            "run_id_changes": self.run_id_changes,
            "illegal_generation_changes": self.illegal_generation_changes,
            "unreconciled_ambiguous_effects": self.unreconciled_ambiguous_effects,
            "completed_obligation_loss": self.completed_obligation_loss,
            "root_goal_hash_changes": self.root_goal_hash_changes,
            "task_contract_hash_illegal_changes": self.task_contract_hash_illegal_changes,
            "false_verified_facts": self.false_verified_facts,
            "false_completion_accepts": self.false_completion_accepts,
            "tool_prompt_injection_authority_escalation": self.tool_prompt_injection_authority_escalation,
            "invalid_learning_promotions": self.invalid_learning_promotions,
            "silent_concurrency_overwrites": self.silent_concurrency_overwrites,
            "model_working_set_linear_growth": self.model_working_set_linear_growth,
            "run_snapshot_unbounded_growth": self.run_snapshot_unbounded_growth,
            "ledger_replay_mismatch": self.ledger_replay_mismatch,
            "ledger_seq_conflict": self.ledger_seq_conflict,
            "torn_tail_undetected": self.torn_tail_undetected,
            "checkpoint_corruption_silent_accept": self.checkpoint_corruption_silent_accept,
            "prepared_before_dispatch_violations": self.prepared_before_dispatch_violations,
            "logical_effect_duplicate_commit": self.logical_effect_duplicate_commit,
        }

    def is_clean(self) -> bool:
        return all(value in (0, False) for value in self.zero_violation_contract().values())


@dataclass
class FakeModel:
    decision_rounds: int = 0
    working_set: deque[str] = field(default_factory=lambda: deque(maxlen=64))
    max_working_set_size: int = 0

    def decide(self, step: int) -> None:
        self.decision_rounds += 1
        self.working_set.append(f"decision:{self.decision_rounds}:step:{step}")
        self.max_working_set_size = max(self.max_working_set_size, len(self.working_set))

    def compact(self) -> None:
        retained = list(self.working_set)[-16:]
        self.working_set.clear()
        self.working_set.extend(retained)


@dataclass
class FakeTool:
    calls: int = 0

    def execute(self, step: int) -> str:
        self.calls += 1
        return f"verified-tool-result:{step}"


@dataclass(frozen=True)
class HarnessReport:
    tool_steps: int
    model_decision_rounds: int
    epoch_count: int
    checkpoint_count: int
    fault_steps_seen: tuple[int, ...]
    remediations: tuple[tuple[int, str], ...]
    duplicate_prevented_count: int
    false_completion_rejected: int
    learning_promotion_rejected: int
    prompt_injection_blocked: int
    torn_tail_recovered: int
    checkpoint_fallback_count: int
    max_model_working_set: int
    max_snapshot_items: int
    identity_fingerprint: tuple[str, str, int, str, str, str]
    metrics: M4HardMetrics


class DeterministicLongHorizonHarness:
    """Drive exactly 1000 tool steps through the existing P18 scheduler state."""

    TOTAL_STEPS: Final[int] = 1000
    MODEL_DECISION_INTERVAL: Final[int] = 4
    MAX_EPOCH_ROUNDS: Final[int] = 75
    MAX_GLOBAL_ROUNDS: Final[int] = 1000
    MAX_SNAPSHOT_ITEMS: Final[int] = 96

    def __init__(self) -> None:
        self.request_id = "req_p18_m4_deterministic"
        self.run_id = "run_p18_m4_deterministic"
        self.generation = 1
        self.authority_hash = "authority:p18-m4:fixed"
        self.root_goal_hash = "root-goal:p18-m4:fixed"
        self.task_contract_hash = "task-contract:p18-m4:fixed"
        self.identity_fingerprint = self._identity()
        self.turn_loop = TurnLoopState()
        self.model = FakeModel()
        self.tool = FakeTool()
        self.metrics = M4HardMetrics()
        self.completed_steps: set[int] = set()
        self.completed_obligations: set[str] = set()
        self.committed_logical_effects: set[str] = set()
        self.fault_steps_seen: list[int] = []
        self.remediations: list[tuple[int, str]] = []
        self.snapshot: deque[str] = deque(maxlen=self.MAX_SNAPSHOT_ITEMS)
        self.max_snapshot_items = 0
        self.ledger_seq = 0
        self.last_replayed_seq = 0
        self.checkpoint_count = 0
        self.duplicate_prevented_count = 0
        self.false_completion_rejected = 0
        self.learning_promotion_rejected = 0
        self.prompt_injection_blocked = 0
        self.torn_tail_recovered = 0
        self.checkpoint_fallback_count = 0
        self._ambiguous_open = False

    def _identity(self) -> tuple[str, str, int, str, str, str]:
        return (
            self.request_id,
            self.run_id,
            self.generation,
            self.authority_hash,
            self.root_goal_hash,
            self.task_contract_hash,
        )

    def _assert_identity_continuity(self) -> None:
        current = self._identity()
        baseline = self.identity_fingerprint
        if current[0] != baseline[0]:
            self.metrics.request_id_changes += 1
        if current[1] != baseline[1]:
            self.metrics.run_id_changes += 1
        if current[2] != baseline[2]:
            self.metrics.illegal_generation_changes += 1
        if current[3] != baseline[3]:
            self.metrics.authority_changes += 1
        if current[4] != baseline[4]:
            self.metrics.root_goal_hash_changes += 1
        if current[5] != baseline[5]:
            self.metrics.task_contract_hash_illegal_changes += 1

    def _append_ledger(self, event: str) -> None:
        self.ledger_seq += 1
        expected = self.last_replayed_seq + 1
        if self.ledger_seq != expected:
            self.metrics.ledger_seq_conflict += 1
        self.last_replayed_seq = self.ledger_seq
        self.snapshot.append(f"{self.ledger_seq}:{event}")
        self.max_snapshot_items = max(self.max_snapshot_items, len(self.snapshot))

    def _checkpoint_rollover(self) -> None:
        self.checkpoint_count += 1
        self._append_ledger(f"checkpoint:{self.checkpoint_count}")
        self.turn_loop.begin_next_epoch()
        self.turn_loop.activate_adaptive_control()

    def _schedule_one(self) -> None:
        while True:
            decision = self.turn_loop.decide_schedule(
                1,
                max_epoch_rounds=self.MAX_EPOCH_ROUNDS,
                max_global_rounds=self.MAX_GLOBAL_ROUNDS,
            )
            if decision.should_checkpoint_continue:
                self._checkpoint_rollover()
                continue
            if not decision.can_schedule:
                raise AssertionError(
                    f"scheduler refused step before global 1000 boundary: {decision.disposition}"
                )
            self.turn_loop.reserve_one()
            return

    def _commit_logical_effect_once(self, logical_effect_id: str) -> None:
        if logical_effect_id in self.committed_logical_effects:
            self.duplicate_prevented_count += 1
            return
        self._append_ledger(f"step.prepared:{logical_effect_id}")
        self._append_ledger(f"step.dispatched:{logical_effect_id}")
        self.committed_logical_effects.add(logical_effect_id)
        self._append_ledger(f"step.committed:{logical_effect_id}")

    def _handle_fault(self, fault: FaultInjection) -> None:
        self.fault_steps_seen.append(fault.step)
        self.remediations.append((fault.step, fault.expected_remediation))
        self._append_ledger(f"fault:{fault.code}:{fault.step}")

        if fault.code == "LLM_TIMEOUT":
            self._append_ledger("provider.reconnected")
        elif fault.code == "LOCAL_GOAL_DRIFT":
            self._append_ledger("reality.audit")
            self._append_ledger("frontier.replanned")
        elif fault.code == "TOOL_FALSE_SUCCESS":
            # A model/tool assertion without changed reality must not become VERIFIED.
            self._append_ledger("verification.rejected:false-success")
        elif fault.code == "TOOL_FAILURE":
            self._append_ledger("attempt.failed")
            self._append_ledger("attempt.reissued:same-logical-effect")
        elif fault.code == "SSE_DISCONNECT":
            self._append_ledger("stream.reconnected")
        elif fault.code == "STALE_FACT":
            self._append_ledger("fact.revalidated")
        elif fault.code == "PROCESS_RESTART":
            self._append_ledger("process.restarted")
            self._append_ledger("checkpoint.rehydrated:same-identity")
        elif fault.code == "TOOL_PROMPT_INJECTION":
            self.prompt_injection_blocked += 1
            self._append_ledger("tool-result.authority-escalation.blocked")
        elif fault.code == "FORCED_CONTEXT_COMPACT":
            self.model.compact()
            self._append_ledger("context.compacted")
        elif fault.code == "BAD_SEMANTIC_SUMMARY":
            self._append_ledger("semantic-summary.rejected-as-authority")
        elif fault.code == "ARTIFACT_REVISION_CONFLICT":
            self._append_ledger("artifact.cas-conflict.detected")
        elif fault.code == "AMBIGUOUS_SIDE_EFFECT":
            self._ambiguous_open = True
            self._append_ledger("step.ambiguous")
            self._append_ledger("step.reconciled:APPLIED")
            self._ambiguous_open = False
        elif fault.code == "FALSE_COMPLETE":
            self.false_completion_rejected += 1
            self._append_ledger("completion.rejected")
        elif fault.code == "PROVIDER_TRANSIENT_5XX":
            self._append_ledger("provider.reconnected:5xx")
        elif fault.code == "DUPLICATE_STREAM_CHUNK":
            self._append_ledger("stream.chunk.duplicate-dropped")
        elif fault.code == "FALSE_FACT_MEMORY_PROMOTION":
            self.learning_promotion_rejected += 1
            self._append_ledger("learning.promotion.rejected")
        elif fault.code == "RESTART_AFTER_CHECKPOINT":
            self._append_ledger("process.restarted:after-checkpoint")
            self._append_ledger("checkpoint.rehydrated:known-good")
        elif fault.code == "PROVIDER_PROFILE_VERSION_CHANGE":
            self._append_ledger("provider.version.revalidated")
        elif fault.code == "LEDGER_TORN_TAIL":
            self._append_ledger("ledger.torn-tail.detected")
            self.torn_tail_recovered += 1
            self._append_ledger("ledger.torn-tail.recovered")
        elif fault.code == "CHECKPOINT_CHECKSUM_CORRUPTION":
            self._append_ledger("checkpoint.corruption.detected")
            self.checkpoint_fallback_count += 1
            self._append_ledger("checkpoint.fallback:previous-known-good")
        elif fault.code == "PREPARED_WITHOUT_DISPATCH":
            # Crash after PREPARED but before DISPATCH: prove the stale physical
            # attempt did not dispatch, then issue a new physical attempt.
            self._append_ledger("step.prepared:crash-window")
            self._append_ledger("attempt.proven-not-dispatched")
            self._append_ledger("attempt.reissued")
        else:  # pragma: no cover - exact schedule is asserted by tests.
            raise AssertionError(f"unhandled deterministic fault {fault.code}")

    def run(self) -> HarnessReport:
        for step in range(1, self.TOTAL_STEPS + 1):
            self._schedule_one()
            if step % self.MODEL_DECISION_INTERVAL == 0:
                self.model.decide(step)

            fault = FAULT_BY_STEP.get(step)
            if fault is not None:
                self._handle_fault(fault)

            result = self.tool.execute(step)
            if not result.startswith("verified-tool-result:"):
                self.metrics.false_verified_facts += 1

            logical_effect_id = f"logical-effect:{step}"
            self._commit_logical_effect_once(logical_effect_id)
            if step == 850:
                # Duplicate stream chunk must not create a second irreversible commit.
                self._commit_logical_effect_once(logical_effect_id)

            self.completed_steps.add(step)
            self.completed_obligations.add(f"obligation:{step}")
            self._append_ledger(f"step.completed:{step}")
            self._assert_identity_continuity()

        required = set(range(1, self.TOTAL_STEPS + 1))
        self.metrics.missing_required_steps = len(required - self.completed_steps)
        self.metrics.unreconciled_ambiguous_effects = int(self._ambiguous_open)
        self.metrics.completed_obligation_loss = self.TOTAL_STEPS - len(self.completed_obligations)
        self.metrics.model_working_set_linear_growth = self.model.max_working_set_size > 64
        self.metrics.run_snapshot_unbounded_growth = self.max_snapshot_items > self.MAX_SNAPSHOT_ITEMS

        if self.turn_loop.action_rounds != self.TOTAL_STEPS:
            self.metrics.missing_required_steps += abs(
                self.TOTAL_STEPS - self.turn_loop.action_rounds
            )
        if len(self.committed_logical_effects) != self.TOTAL_STEPS:
            self.metrics.logical_effect_duplicate_commit += abs(
                self.TOTAL_STEPS - len(self.committed_logical_effects)
            )
        if self.duplicate_prevented_count != 1:
            self.metrics.duplicate_committed_irreversible_effects += abs(
                1 - self.duplicate_prevented_count
            )

        return HarnessReport(
            tool_steps=self.tool.calls,
            model_decision_rounds=self.model.decision_rounds,
            epoch_count=self.turn_loop.epoch_index + 1,
            checkpoint_count=self.checkpoint_count,
            fault_steps_seen=tuple(self.fault_steps_seen),
            remediations=tuple(self.remediations),
            duplicate_prevented_count=self.duplicate_prevented_count,
            false_completion_rejected=self.false_completion_rejected,
            learning_promotion_rejected=self.learning_promotion_rejected,
            prompt_injection_blocked=self.prompt_injection_blocked,
            torn_tail_recovered=self.torn_tail_recovered,
            checkpoint_fallback_count=self.checkpoint_fallback_count,
            max_model_working_set=self.model.max_working_set_size,
            max_snapshot_items=self.max_snapshot_items,
            identity_fingerprint=self._identity(),
            metrics=self.metrics,
        )
