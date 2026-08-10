"""Re-evaluate existing L5 support after exact evidence-root invalidation using existing P7 math."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from world_understanding.cognition.l5 import CognitionL5View

@dataclass(frozen=True, slots=True)
class CognitionSupportDecision:
    remains_stable: bool
    remaining_support_ids: tuple[str, ...]
    remaining_counter_ids: tuple[str, ...]
    reason_code: str

class CognitionSupportEvaluator(Protocol):
    def evaluate(self, view: CognitionL5View, *, invalidated_evidence_ids: tuple[str, ...], now_ms: int) -> CognitionSupportDecision: ...

class ExistingCognitionSupportEvaluator:
    """Thin read-only adapter over the current P7 store + stability evaluator; performs no writes."""
    def __init__(self, store: object, *, policy: object | None=None) -> None:
        self.store=store
        if policy is None:
            from world_understanding.cognition.stability import StabilityPolicy
            policy=StabilityPolicy()
        self.policy=policy
    def _load_exact(self, ids: tuple[str, ...]) -> list[object]:
        requested=tuple(sorted(set(ids)))
        loaded=list(self.store.get_evidence_many(requested))
        loaded_ids={item.evidence_id for item in loaded}
        if loaded_ids != set(requested):
            raise ValueError("COGNITION_SUPPORT_EVIDENCE_MISSING")
        return loaded
    def evaluate(self, view: CognitionL5View, *, invalidated_evidence_ids: tuple[str, ...], now_ms: int) -> CognitionSupportDecision:
        from world_understanding.cognition.stability import evaluate_evidence, highest_eligible_level
        statement=view.statement
        invalid=set(invalidated_evidence_ids)
        support_all=self._load_exact(tuple(statement.supporting_evidence_ids))
        counter_all=self._load_exact(tuple(statement.counterevidence_ids))
        support=[e for e in support_all if e.evidence_id not in invalid]
        counter=[e for e in counter_all if e.evidence_id not in invalid]
        report=evaluate_evidence(
            cognition_id=statement.cognition_id, life_id=statement.life_id, domain=statement.domain,
            world_scope_hash=statement.world_scope_hash, principal_scope_hash=statement.principal_scope_hash,
            support=support, counter=counter, now_ms=now_ms, policy=self.policy,
        )
        level=highest_eligible_level(report,self.policy)
        order={"C0":0,"C1":1,"C2":2,"C3":3,"C4":4}
        required="C3" if statement.stability_level=="C4" else statement.stability_level
        remains=order[level] >= order[required]
        return CognitionSupportDecision(remains, tuple(sorted(e.evidence_id for e in support)), tuple(sorted(e.evidence_id for e in counter)), "COGNITION_REEVALUATED_STABLE" if remains else "COGNITION_REEVALUATED_INSUFFICIENT")

__all__=["CognitionSupportDecision","CognitionSupportEvaluator","ExistingCognitionSupportEvaluator"]
