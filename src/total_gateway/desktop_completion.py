"""Machine completion gate for renderer-pulled desktop results."""

from __future__ import annotations

from contracts import ArtifactManifest

from .completion_gate import (
    CompletionDecision,
    CompletionGate,
    CompletionGateError,
    CompletionRequirements,
)
from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore


class DesktopCompletionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def evaluate_desktop_completion(
    *,
    objects: ContentAddressedObjectStore,
    facts: FactLedger,
    request_id: str,
    run_id: str,
    generation: int,
    execution_effect_id: str,
    candidate_text: str,
    artifacts: tuple[ArtifactManifest, ...],
    head_state_reader=None,
    verification_readiness=None,
    active_plan=None,
    verification_disposition=None,
) -> CompletionDecision:
    requirements = CompletionRequirements(
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        text_required=True,
        required_execution_effect_ids=(execution_effect_id,),
        required_artifact_revision_ids=tuple(
            sorted(item.artifact_revision_id for item in artifacts)
        ),
        delivery_requirement="NONE",
        # M4.1 §1: PLAN_BOUND is decided by the ACTIVE PLAN, not by
        # whether a readiness object happens to exist. Plan exists →
        # PLAN_BOUND, readiness missing → CompletionGateError (the
        # executor should have run before this point).
        verification_mode=(
            "PLAN_BOUND"
            if active_plan is not None
            else "NONE"
        ),
    )
    try:
        decision = CompletionGate(objects, facts, head_state_reader=head_state_reader).evaluate(
            requirements,
            candidate_text=candidate_text,
            artifacts=artifacts,
            verification_readiness=verification_readiness,
            active_plan=active_plan,
            verification_disposition=verification_disposition,
        )
    except CompletionGateError as exc:
        raise DesktopCompletionError(exc.code) from exc
    if not decision.can_transition_request_completed:
        raise DesktopCompletionError(decision.reason_code)
    return decision


__all__ = [
    "DesktopCompletionError",
    "evaluate_desktop_completion",
]
