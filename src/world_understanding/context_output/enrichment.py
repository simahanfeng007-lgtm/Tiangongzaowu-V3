"""Internal candidates for enriching the existing WorldContextPacket projection.

This is deliberately not a public World Understanding contract. Candidates may
only improve summaries/ranking for records that already exist in the coherent
WorldState snapshot. They cannot add truth, authorization, execution rights, or
a second context packet.
"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.world_understanding._base import WorldRecordRef


@dataclass(frozen=True, slots=True)
class ContextProjectionCandidate:
    ref: WorldRecordRef
    item_kind: str
    summary: str
    task_relevance_milli: int = 800
    impact_milli: int = 700
    freshness_need_milli: int = 800

    def __post_init__(self) -> None:
        if not str(self.item_kind or "").strip():
            raise ValueError("WORLD_CONTEXT_ENRICHMENT_KIND_REQUIRED")
        if not str(self.summary or "").strip():
            raise ValueError("WORLD_CONTEXT_ENRICHMENT_SUMMARY_REQUIRED")
        for value, code in (
            (self.task_relevance_milli, "RELEVANCE"),
            (self.impact_milli, "IMPACT"),
            (self.freshness_need_milli, "FRESHNESS"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1000:
                raise ValueError(f"WORLD_CONTEXT_ENRICHMENT_{code}_INVALID")

    def priority_key(self) -> tuple:
        score = 5 * self.task_relevance_milli + 3 * self.impact_milli + 2 * self.freshness_need_milli
        return (-score, self.ref.sort_key(), self.item_kind, self.summary)


__all__ = ["ContextProjectionCandidate"]
