"""WorldCut compatibility. Crossed or contradictory source snapshots fail closed."""
from __future__ import annotations
from typing import Literal
from contracts.world_understanding.world_cut import WorldCut, SourceWatermark
from .scope import require_exact_scope, CommonScopeMismatch

CutRelation = Literal["SAME", "LEFT_DOMINATES", "RIGHT_DOMINATES", "DISJOINT", "INCOMPATIBLE"]

class IncompatibleWorldCut(ValueError):
    pass


def _watermark_map(cut: WorldCut) -> dict[tuple[str, str], SourceWatermark]:
    return {(item.source_kind, item.watermark_type): item for item in cut.source_watermarks}


def compare_world_cuts(left: WorldCut, right: WorldCut) -> CutRelation:
    try:
        require_exact_scope(left.scope, right.scope)
    except CommonScopeMismatch:
        return "INCOMPATIBLE"
    lm, rm = _watermark_map(left), _watermark_map(right)
    overlap = sorted(set(lm) & set(rm))
    if not overlap:
        return "DISJOINT"
    directions: set[str] = set()
    for key in overlap:
        a, b = lm[key], rm[key]
        if a.watermark_value == b.watermark_value and a.sequence == b.sequence:
            continue
        if a.sequence is None or b.sequence is None:
            return "INCOMPATIBLE"
        if a.sequence == b.sequence:
            return "INCOMPATIBLE"
        directions.add("LEFT" if a.sequence > b.sequence else "RIGHT")
    if len(directions) > 1:
        return "INCOMPATIBLE"
    if not directions:
        return "SAME"
    return "LEFT_DOMINATES" if "LEFT" in directions else "RIGHT_DOMINATES"


def require_compatible_world_cuts(cuts: tuple[WorldCut, ...]) -> None:
    for index, left in enumerate(cuts):
        for right in cuts[index + 1:]:
            if compare_world_cuts(left, right) == "INCOMPATIBLE":
                raise IncompatibleWorldCut("WORLD_CUT_INCOMPATIBLE")

__all__ = ["CutRelation", "IncompatibleWorldCut", "compare_world_cuts", "require_compatible_world_cuts"]
