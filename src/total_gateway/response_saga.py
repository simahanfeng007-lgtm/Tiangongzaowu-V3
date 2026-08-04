"""G3 response commit saga: frozen plan -> slots -> outcome -> assistant/status.

Crash contract (T27): a marker is persisted before transport dispatch; a
terminal result is never replayed; an AMBIGUOUS slot is never redispatched;
recovery continues with the next frozen slot only.  Model IO happens through
the transport adapter, never under the Gateway store lock.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from contracts import (
    AssistantCommit,
    AssistantMessage,
    AssistantSystemEnvelope,
    ModelAttemptPlan,
    ModelAttemptPlanOutcome,
    ModelAttemptResult,
    SystemStatusRecord,
    derive_assistant_commit_id,
    derive_assistant_message_id,
    derive_model_attempt_id,
    derive_model_attempt_plan_outcome_id,
    derive_model_attempt_receipt_id,
    derive_model_inference_effect_id,
)
from contracts import canonical_sha256

from .store import GatewayStateStore


TransportAdapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]
LifeCommitter = Callable[[Mapping[str, Any]], None]


class ResponseSagaError(RuntimeError):
    pass


class ResponseCommitSaga:
    """Persistence boundary for one immutable model attempt plan lifecycle."""

    def __init__(
        self,
        store: GatewayStateStore,
        *,
        transport_adapter: TransportAdapter,
        life_committer: LifeCommitter,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._adapter = transport_adapter
        self._life_committer = life_committer
        self._now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        self._effect_head_sha256: str | None = None

    def begin(self, plan: ModelAttemptPlan, *, now_ms: int | None = None) -> None:
        """Persist the frozen plan and open its logical MODEL_INFERENCE effect."""
        now = now_ms if now_ms is not None else self._now_fn()
        self._store.put_model_attempt_plan(plan, now_ms=now)
        effect_id = derive_model_inference_effect_id(
            origin_request_id=plan.request_id,
            origin_run_id=plan.run_id,
            root_experience_id=plan.root_experience_id,
            response_episode_id=plan.response_episode_id,
            request_sha256=plan.request_sha256,
        )
        if effect_id != plan.model_effect_id:
            raise ResponseSagaError("model attempt plan effect id is inconsistent")
        head_sha256 = canonical_sha256({"effect": effect_id, "state": "plan_frozen", "at": now})
        self._store.put_effect_outcome_head(
            effect_id=effect_id,
            original_execution_result_ref=plan.model_attempt_plan_id,
            effective_status="FAILED_RETRYABLE",
            head_revision=1,
            head_sha256=head_sha256,
            latest_reconciliation_ref=None,
            updated_at_ms=now,
            expected_head_sha256=None,
        )
        self._effect_head_sha256 = head_sha256

    def execute_slot(self, plan: ModelAttemptPlan, slot: Mapping[str, Any]) -> ModelAttemptResult:
        """Persist marker, dispatch once, persist the immutable result."""
        slot_no = int(slot["slot_no"])
        existing = self._store.get_model_attempt_result(plan_id=plan.model_attempt_plan_id, slot_no=slot_no)
        if existing is not None:
            raise ResponseSagaError("terminal attempt slot cannot be replayed")
        marker = self._store.get_dispatch_marker(plan_id=plan.model_attempt_plan_id, slot_no=slot_no)
        now = self._now_fn()
        attempt_id = derive_model_attempt_id(model_attempt_plan_id=plan.model_attempt_plan_id, slot_no=slot_no)
        if marker is None:
            marker_id = "dm_" + canonical_sha256(
                {"domain": "dispatch-marker", "attempt_id": attempt_id, "at": now}
            )
            if not self._store.create_dispatch_marker(
                marker_id=marker_id, plan_id=plan.model_attempt_plan_id,
                attempt_id=attempt_id, slot_no=slot_no, now_ms=now,
            ):
                raise ResponseSagaError("dispatch marker conflict")
        else:
            marker_id = str(marker["marker_id"])
        self._store.mark_dispatch_marker_dispatched(marker_id=marker_id, now_ms=now)
        outcome = self._adapter(
            {
                "plan_id": plan.model_attempt_plan_id,
                "attempt_id": attempt_id,
                "slot_no": slot_no,
                "provider": slot["provider"],
                "model": slot["model"],
                "request_sha256": plan.request_sha256,
                "transport_profile_sha256": slot["transport_profile_sha256"],
            }
        )
        if not isinstance(outcome, Mapping):
            raise ResponseSagaError("transport adapter result is invalid")
        status = str(outcome.get("status") or "FAILED_FINAL")
        if status == "SUCCEEDED":
            text = str(outcome.get("text") or "")
            if not text:
                raise ResponseSagaError("succeeded transport result has no text")
            text_sha256 = canonical_sha256({"text": text})
            provider_response_id = str(outcome.get("provider_response_id") or "") or None
            result = ModelAttemptResult(
                model_attempt_receipt_id=derive_model_attempt_receipt_id(model_attempt_id=attempt_id),
                model_attempt_plan_id=plan.model_attempt_plan_id,
                model_attempt_plan_sha256=plan.plan_sha256,
                model_effect_id=plan.model_effect_id,
                request_id=plan.request_id, run_id=plan.run_id,
                run_sequence=plan.run_sequence, generation=plan.generation,
                run_life_binding_sha256=plan.run_life_binding_sha256,
                root_experience_id=plan.root_experience_id,
                response_episode_id=plan.response_episode_id,
                attempt_id=attempt_id, slot_no=slot_no,
                provider=str(slot["provider"]), model=str(slot["model"]),
                status="SUCCEEDED", attempt_plan_revision=plan.plan_revision,
                request_sha256=plan.request_sha256, dispatched=True,
                started_at_ms=now, completed_at_ms=self._now_fn(),
                response_schema_valid=True,
                dispatch_marker_ref=marker_id, transport_run_id=str(outcome.get("transport_run_id") or "trn_shadow"),
                provider_response_id=provider_response_id,
                text_object_id="obj_" + text_sha256, output_text_sha256=text_sha256,
                finish_reason=str(outcome.get("finish_reason") or "stop"),
            )
        else:
            error_code = str(outcome.get("error_code") or "") or None
            result = ModelAttemptResult(
                model_attempt_receipt_id=derive_model_attempt_receipt_id(model_attempt_id=attempt_id),
                model_attempt_plan_id=plan.model_attempt_plan_id,
                model_attempt_plan_sha256=plan.plan_sha256,
                model_effect_id=plan.model_effect_id,
                request_id=plan.request_id, run_id=plan.run_id,
                run_sequence=plan.run_sequence, generation=plan.generation,
                run_life_binding_sha256=plan.run_life_binding_sha256,
                root_experience_id=plan.root_experience_id,
                response_episode_id=plan.response_episode_id,
                attempt_id=attempt_id, slot_no=slot_no,
                provider=str(slot["provider"]), model=str(slot["model"]),
                status=status, attempt_plan_revision=plan.plan_revision,
                request_sha256=plan.request_sha256, dispatched=True,
                started_at_ms=now, completed_at_ms=self._now_fn(),
                response_schema_valid=bool(outcome.get("response_schema_valid", False)),
                dispatch_marker_ref=marker_id,
                transport_run_id=str(outcome.get("transport_run_id") or "trn_shadow"),
                error_code=error_code,
                retryable=bool(outcome.get("retryable", status == "FAILED_RETRYABLE")),
            )
        self._store.put_model_attempt_result(result)
        return result

    def run_plan(self, plan: ModelAttemptPlan) -> ModelAttemptPlanOutcome:
        """Execute or reconcile every frozen slot and freeze the machine outcome."""
        results: list[ModelAttemptResult] = []
        winner: ModelAttemptResult | None = None
        for slot in plan.provider_slots:
            result = self._store.get_model_attempt_result(
                plan_id=plan.model_attempt_plan_id, slot_no=slot.slot_no
            )
            if result is None:
                result = self.execute_slot(plan, slot.model_dump(mode="json"))
            results.append(result)
            if result.status == "SUCCEEDED":
                winner = result
                break
            elif result.status == "AMBIGUOUS":
                # Never redispatch; continue with the next frozen slot.
                continue
        if winner is not None:
            for later in plan.provider_slots:
                if later.slot_no <= winner.slot_no:
                    continue
                if (
                    self._store.get_model_attempt_result(
                        plan_id=plan.model_attempt_plan_id, slot_no=later.slot_no
                    )
                    is not None
                ):
                    raise ResponseSagaError("later slot executed after a winner")
        if winner is not None:
            outcome_status = "SUCCEEDED"
            winner_ref = winner.model_attempt_receipt_id
        else:
            outcome_status = "EXHAUSTED"
            winner_ref = None
        outcome = ModelAttemptPlanOutcome(
            model_attempt_plan_outcome_id=derive_model_attempt_plan_outcome_id(
                model_attempt_plan_id=plan.model_attempt_plan_id
            ),
            model_attempt_plan_id=plan.model_attempt_plan_id,
            model_attempt_plan_sha256=plan.plan_sha256,
            status=outcome_status,
            ordered_attempt_refs=tuple(item.model_attempt_receipt_id for item in results),
            winner_attempt_ref=winner_ref,
            completed_at_ms=max(item.completed_at_ms for item in results),
            outcome_sha256="0" * 64,
        ).with_computed_outcome_sha256()
        self._store.put_model_attempt_plan_outcome(outcome)
        expected_head = self._effect_head_sha256
        if expected_head is None:
            existing_head = self._store.get_effect_outcome_head(plan.model_effect_id)
            if existing_head is not None:
                expected_head = str(existing_head["head_sha256"])
        self._store.put_effect_outcome_head(
            effect_id=plan.model_effect_id,
            original_execution_result_ref=outcome.model_attempt_plan_outcome_id,
            effective_status="SUCCEEDED" if winner is not None else "FAILED_FINAL",
            head_revision=2,
            head_sha256=outcome.outcome_sha256,
            latest_reconciliation_ref=None,
            updated_at_ms=self._now_fn(),
            expected_head_sha256=expected_head,
        )
        self._effect_head_sha256 = outcome.outcome_sha256
        return outcome

    def commit_response(
        self,
        plan: ModelAttemptPlan,
        outcome: ModelAttemptPlanOutcome,
        *,
        life_id: str,
        text: str | None = None,
        system_status: str | None = None,
    ) -> AssistantSystemEnvelope:
        """Freeze the RESPONSE_COMMITTED Life stage and the gateway commit."""
        now = self._now_fn()
        if outcome.status == "SUCCEEDED":
            if text is None or outcome.winner_attempt_ref is None:
                raise ResponseSagaError("succeeded outcome requires committed text")
            text_sha256 = canonical_sha256({"text": text})
            assistant_message_id = derive_assistant_message_id(
                life_id=life_id,
                root_experience_id=plan.root_experience_id,
                response_episode_id=plan.response_episode_id,
                model_attempt_receipt_id=outcome.winner_attempt_ref,
                committed_text_sha256=text_sha256,
            )
            commit_id = derive_assistant_commit_id(
                response_episode_id=plan.response_episode_id,
                assistant_message_id=assistant_message_id,
            )
            turn_ref = "tc_" + canonical_sha256(
                {"domain": "life-turn-commit", "response_episode_id": plan.response_episode_id}
            )
            commit = AssistantCommit(
                assistant_commit_id=commit_id,
                assistant_message_id=assistant_message_id,
                life_turn_commit_ref=turn_ref,
                life_turn_commit_sha256=canonical_sha256({"turn": turn_ref}),
                response_episode_id=plan.response_episode_id,
                model_attempt_plan_outcome_ref=outcome.model_attempt_plan_outcome_id,
                model_attempt_receipt_id=outcome.winner_attempt_ref,
                output_text_sha256=text_sha256,
                committed_text_sha256=text_sha256,
                text_object_id="obj_" + text_sha256,
                committed_at_ms=now,
                commit_sha256="0" * 64,
            ).with_computed_commit_sha256()
            self._store.put_assistant_commit(commit)
            winner = outcome.winner_attempt_ref
            self._life_committer(
                {
                    "stage": "RESPONSE_COMMITTED",
                    "response_episode_id": plan.response_episode_id,
                    "plan_ref": plan.model_attempt_plan_id,
                    "plan_outcome_ref": outcome.model_attempt_plan_outcome_id,
                    "attempt_refs": outcome.ordered_attempt_refs,
                    "winner_ref": winner,
                    "assistant_candidate_id": assistant_message_id,
                    "expression_status": "model_available",
                }
            )
            message = AssistantMessage(
                assistant_message_id=assistant_message_id,
                assistant_commit_id=commit_id,
                assistant_commit_sha256=commit.commit_sha256,
                text=text,
                text_object_id="obj_" + text_sha256,
                committed_text_sha256=text_sha256,
                life_id=life_id,
                root_experience_id=plan.root_experience_id,
                response_episode_id=plan.response_episode_id,
                model_attempt_receipt_id=outcome.winner_attempt_ref,
                provider=str(plan.provider_slots[0].provider),
                model=str(plan.provider_slots[0].model),
                committed_at_ms=now,
            )
            status_record = None
            if system_status:
                status_record = self._system_status(plan, code=system_status, severity="warning")
            return AssistantSystemEnvelope(assistant_message=message, system_status=status_record)
        status_record = self._system_status(plan, code=system_status or "all_models_unavailable", severity="error")
        self._life_committer(
            {
                "stage": "RESPONSE_COMMITTED",
                "response_episode_id": plan.response_episode_id,
                "plan_ref": plan.model_attempt_plan_id,
                "plan_outcome_ref": outcome.model_attempt_plan_outcome_id,
                "attempt_refs": outcome.ordered_attempt_refs,
                "winner_ref": None,
                "assistant_candidate_id": None,
                "expression_status": "model_unavailable",
            }
        )
        return AssistantSystemEnvelope(assistant_message=None, system_status=status_record)

    def _system_status(self, plan: ModelAttemptPlan, *, code: str, severity: str) -> SystemStatusRecord:
        status = SystemStatusRecord(
            system_status_id="sys_" + canonical_sha256(
                {"domain": "system-status", "response_episode_id": plan.response_episode_id, "code": code}
            ),
            request_id=plan.request_id, run_id=plan.run_id,
            run_sequence=plan.run_sequence, generation=plan.generation,
            response_episode_id=plan.response_episode_id,
            status_code=code, severity=severity,
            source_component="gateway.response", source_fact_refs=(),
            display_object_ref="obj_status_" + plan.response_episode_id,
            created_at_ms=self._now_fn(),
            system_status_sha256="0" * 64,
        ).with_computed_status_sha256()
        self._store.put_system_status(status)
        return status
