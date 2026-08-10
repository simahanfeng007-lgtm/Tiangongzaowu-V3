"""Γ Epistemic Integrity Plane V1: deterministic qualification, never reality execution."""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.world_cut import WorldCut
from .scope import require_exact_scope, CommonScopeMismatch
from .provenance import require_provenance, ProvenanceBroken, independent_evidence_count
from .observability import require_negative_evidence_coverage, NegativeEvidenceInsufficient, effective_coverage_milli
from .world_cut import require_compatible_world_cuts, IncompatibleWorldCut

FORBIDDEN_EMPIRICAL_SOURCE_KINDS = frozenset({
    "MODEL_OUTPUT", "AUTONOMY", "CONTEXT_CONTINUITY", "MEMORY", "KNOWLEDGE", "WEB_EXTERNAL"
})
FORBIDDEN_SELF_PROOF_TYPES = frozenset({
    "WorldPrediction", "PredictionOutcome", "WorldHypothesis", "WorldCuriosity", "WorldInquiry", "InquiryOutcome", "WorldContextPacket", "WorldQuery"
})

@dataclass(frozen=True, slots=True)
class GammaDecision:
    admissible: bool
    stable_promotion: bool
    truth_state: str
    epistemic_state: str
    effective_coverage_milli: int
    independent_evidence_count: int
    reason_codes: tuple[str, ...]

class EpistemicIntegrityError(ValueError):
    pass

class EpistemicPlane:
    __slots__ = ("min_negative_coverage_milli",)
    def __init__(self, *, min_negative_coverage_milli: int = 1) -> None:
        if not 0 <= min_negative_coverage_milli <= 1000:
            raise ValueError("min_negative_coverage_milli out of range")
        self.min_negative_coverage_milli = int(min_negative_coverage_milli)

    def evaluate_known(
        self,
        record: object,
        *,
        expected_scope: WorldScope,
        record_cut: WorldCut | None = None,
        compatible_cuts: tuple[WorldCut, ...] = (),
        supporting_records: Iterable[object] = (),
    ) -> GammaDecision:
        reasons: list[str] = []
        admissible = True
        stable = True
        try:
            require_exact_scope(expected_scope, getattr(record, "world_scope"))
        except Exception:
            admissible = False; stable = False; reasons.append("SCOPE_MISMATCH")
        if record_cut is not None:
            try:
                require_exact_scope(expected_scope, record_cut.scope)
                require_compatible_world_cuts((record_cut, *compatible_cuts))
            except (CommonScopeMismatch, IncompatibleWorldCut):
                admissible = False; stable = False; reasons.append("WORLD_CUT_INCOMPATIBLE")
        empirical = int(getattr(record, "empirical_evidence_weight_milli", 0))
        refs = tuple(getattr(record, "provenance_refs", ()))
        try:
            require_provenance(refs, empirical_weight_milli=empirical)
        except ProvenanceBroken:
            stable = False; reasons.append("PROVENANCE_BROKEN")
        source_kind = getattr(record, "source_kind", None)
        if source_kind in FORBIDDEN_EMPIRICAL_SOURCE_KINDS and empirical != 0:
            stable = False; reasons.append("SELF_PROOF_EMPIRICAL_FORBIDDEN")
        try:
            require_negative_evidence_coverage(record, min_coverage_milli=self.min_negative_coverage_milli)
        except NegativeEvidenceInsufficient:
            stable = False; reasons.append("NEGATIVE_EVIDENCE_REQUIRES_COVERAGE")
        support = (record, *tuple(supporting_records))
        independence = independent_evidence_count(tuple(tuple(getattr(item, "provenance_refs", ())) for item in support))
        truth_state = str(getattr(record, "truth_state", "UNKNOWN"))
        epistemic_state = str(getattr(record, "epistemic_state", "CURRENT"))
        if truth_state == "UNKNOWN":
            stable = False; reasons.append("OPEN_WORLD_UNKNOWN")
        if epistemic_state in {"STALE", "CHALLENGED", "REVERIFYING", "RETIRED"}:
            stable = False; reasons.append(f"EPISTEMIC_{epistemic_state}")
        return GammaDecision(admissible, stable and admissible, truth_state, epistemic_state, effective_coverage_milli(record), independence, tuple(sorted(set(reasons))))

    def require_stable_known(self, record: object, *, expected_scope: WorldScope) -> None:
        decision = self.evaluate_known(record, expected_scope=expected_scope)
        if not decision.admissible or not decision.stable_promotion:
            raise EpistemicIntegrityError(decision.reason_codes[0] if decision.reason_codes else "GAMMA_REJECTED")

    def validate_non_evidence_object(self, item: object) -> None:
        if type(item).__name__ in FORBIDDEN_SELF_PROOF_TYPES and int(getattr(item, "empirical_evidence_weight_milli", 0)) != 0:
            raise EpistemicIntegrityError("SELF_PROOF_EMPIRICAL_FORBIDDEN")

@dataclass(frozen=True, slots=True)
class InvalidationResult:
    dirty_record_hashes: tuple[str, ...]
    reason_code: str
    truth_mutated: bool = False
    epistemic_state_mutated: bool = False


def propagate_invalidation(changed_hashes: Iterable[str], descendants_by_parent: Mapping[str, Iterable[str]]) -> InvalidationResult:
    dirty = set(str(item) for item in changed_hashes)
    frontier = list(dirty)
    while frontier:
        parent = frontier.pop()
        for child in descendants_by_parent.get(parent, ()):
            child = str(child)
            if child not in dirty:
                dirty.add(child); frontier.append(child)
    return InvalidationResult(tuple(sorted(dirty)), "UPSTREAM_DIRTY")

__all__ = ["FORBIDDEN_EMPIRICAL_SOURCE_KINDS", "FORBIDDEN_SELF_PROOF_TYPES", "GammaDecision", "EpistemicIntegrityError", "EpistemicPlane", "InvalidationResult", "propagate_invalidation"]
