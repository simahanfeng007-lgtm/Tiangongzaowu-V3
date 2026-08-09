"""P11 deterministic KnowledgeGap generation from one coherent P9 snapshot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.curiosity import KnowledgeGap, derive_knowledge_gap_id
from world_understanding.world_state.store import MaterializedWorldSnapshot


def _state_ref(snapshot: MaterializedWorldSnapshot) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_state",
        record_id=snapshot.state.world_state_id,
        revision=snapshot.state.world_sequence + 1,
        sha256=snapshot.state.state_sha256,
    )


def _unique_refs(values: Iterable[WorldRecordRef]) -> tuple[WorldRecordRef, ...]:
    by_key = {ref.sort_key(): ref for ref in values}
    return tuple(by_key[key] for key in sorted(by_key))


@dataclass(frozen=True, slots=True)
class KnowledgeGapGeneratorConfig:
    max_gaps: int = 256
    stale_uncertainty_milli: int = 800
    conflict_uncertainty_milli: int = 950
    uncertainty_uncertainty_milli: int = 700

    def __post_init__(self) -> None:
        if not 1 <= self.max_gaps <= 4096:
            raise ValueError("WORLD_GAP_LIMIT_INVALID")
        for value in (
            self.stale_uncertainty_milli,
            self.conflict_uncertainty_milli,
            self.uncertainty_uncertainty_milli,
        ):
            if not 0 <= value <= 1000:
                raise ValueError("WORLD_GAP_CONFIG_INVALID")


class KnowledgeGapGenerator:
    """Creates reference-only epistemic gaps; never observations or evidence."""

    def __init__(self, config: KnowledgeGapGeneratorConfig | None = None) -> None:
        self.config = config or KnowledgeGapGeneratorConfig()

    def generate(self, snapshot: MaterializedWorldSnapshot) -> tuple[KnowledgeGap, ...]:
        state = snapshot.state
        if not state.has_valid_hash():
            raise ValueError("WORLD_STATE_HASH_INVALID")
        basis_state = _state_ref(snapshot)
        rows: list[KnowledgeGap] = []

        def add(
            ref: WorldRecordRef,
            *,
            missing: str,
            uncertainty: int,
            conflict: int,
            staleness: int,
            observability: int,
            impact: int,
            relevance: int,
        ) -> None:
            if len(rows) >= self.config.max_gaps:
                return
            subjects = (ref,)
            basis = _unique_refs((basis_state, ref))
            missing_types = (missing,)
            gap_score = min(
                1000,
                max(uncertainty, conflict, staleness, observability)
                * max(impact, relevance, 1)
                // 1000,
            )
            gap_id = derive_knowledge_gap_id(
                world_scope_hash=state.scope.world_scope_hash,
                subject_refs=subjects,
                missing_evidence_types=missing_types,
                basis_refs=basis,
            )
            rows.append(
                KnowledgeGap(
                    gap_id=gap_id,
                    scope=state.scope,
                    subject_refs=subjects,
                    uncertainty_milli=uncertainty,
                    conflict_milli=conflict,
                    observability_gap_milli=observability,
                    staleness_milli=staleness,
                    prediction_error_milli=0,
                    impact_milli=impact,
                    relevance_milli=relevance,
                    gap_score_milli=gap_score,
                    missing_evidence_types=missing_types,
                    basis_refs=basis,
                    gap_sha256="0" * 64,
                ).with_computed_hash()
            )

        for ref in state.unresolved_conflict_refs:
            add(
                ref,
                missing="conflict_discriminating_observation",
                uncertainty=self.config.conflict_uncertainty_milli,
                conflict=1000,
                staleness=0,
                observability=700,
                impact=900,
                relevance=900,
            )
        for ref in state.stale_refs:
            add(
                ref,
                missing="revalidation_observation",
                uncertainty=self.config.stale_uncertainty_milli,
                conflict=0,
                staleness=1000,
                observability=650,
                impact=750,
                relevance=850,
            )
        uncertainty = snapshot.uncertainty.refs if snapshot.uncertainty is not None else ()
        known_keys = {item.subject_refs[0].sort_key() for item in rows if item.subject_refs}
        for ref in uncertainty:
            if ref.sort_key() in known_keys:
                continue
            add(
                ref,
                missing="uncertainty_reducing_observation",
                uncertainty=self.config.uncertainty_uncertainty_milli,
                conflict=0,
                staleness=0,
                observability=600,
                impact=650,
                relevance=700,
            )
        return tuple(rows)


__all__ = ["KnowledgeGapGenerator", "KnowledgeGapGeneratorConfig"]
