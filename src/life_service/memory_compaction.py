"""P15 M8: deterministic memory consolidation with a change-seq watermark.

Mirrors plan section 12: every ~2000 memory changes may trigger one
consolidation pass (``head - watermark >= threshold``), never ``count % 2000``
re-trigger logic.  The pass only folds duplicate L2 diary candidates and keeps
all lineage; L4/L5 are never deleted by a count threshold and the Life Event
ledger is never touched.
"""

from __future__ import annotations

from .memory_invalidation import invalidate_cascade
from .store import LifeShadowStore


COMPACTION_CONSUMER = "p15-memory-compaction"
DEFAULT_COMPACTION_THRESHOLD = 2000


def maybe_consolidate(
    store: LifeShadowStore,
    *,
    life_id: str,
    now_ms: int,
    threshold: int = DEFAULT_COMPACTION_THRESHOLD,
    consumer_id: str = COMPACTION_CONSUMER,
) -> dict[str, object]:
    """Run one consolidation pass when the watermark gap reaches threshold."""

    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int)
        or threshold < 1
    ):
        raise ValueError("memory compaction threshold must be positive")
    head = store.memory_change_head(life_id)
    last = store.get_memory_consumer_offset(consumer_id, life_id)
    pending = head - last
    if pending < threshold:
        return {
            "triggered": False,
            "life_id": life_id,
            "head": head,
            "last_watermark": last,
            "pending": pending,
        }
    consolidated = _consolidation_pass(
        store, life_id=life_id, now_ms=now_ms
    )
    store.advance_memory_consumer_offset(
        consumer_id, life_id, head, updated_at_ms=now_ms
    )
    return {
        "triggered": True,
        "life_id": life_id,
        "head": head,
        "last_watermark": head,
        "pending": pending,
        "consolidated": consolidated,
    }


def _consolidation_pass(
    store: LifeShadowStore, *, life_id: str, now_ms: int
) -> dict[str, int]:
    """Fold duplicate L2 diary candidates; never touch L4/L5 or the ledger."""

    l2s = store.list_memory_derivations(
        life_id=life_id, layer="L2_DIARY", active_only=True, limit=4096
    )
    groups: dict[str, list] = {}
    for derivation in l2s:
        groups.setdefault(derivation.claim_key, []).append(derivation)
    invalidated = 0
    for claim, members in groups.items():
        if len(members) < 2:
            continue
        representative = max(
            members,
            key=lambda item: (item.created_at_ms, item.derivation_id),
        )
        representative_roots = set(
            representative.lineage_root_event_ids
        )
        for member in members:
            if member.derivation_id == representative.derivation_id:
                continue
            if not (
                set(member.lineage_root_event_ids)
                & representative_roots
            ):
                continue
            records = invalidate_cascade(
                store,
                derivation_id=member.derivation_id,
                reason="stale",
                invalidated_at_ms=now_ms,
                source_trigger_ref="consolidation:" + claim,
            )
            invalidated += len(records)
    return {
        "duplicate_l2_invalidated": invalidated,
        "l4_l5_touched": 0,
        "life_event_ledger_touched": 0,
    }


__all__ = [
    "COMPACTION_CONSUMER",
    "DEFAULT_COMPACTION_THRESHOLD",
    "maybe_consolidate",
]
