"""P15 M3 correction/forgetting cascade over the derivation DAG.

When a parent derivation is corrected or superseded, every descendant that
loses all independent parent support is invalidated (reason ``stale``).
Invalidated derivations stay in the append-only history but stop being
active: they never enter Context/Temperament/WorldCandidate, and an active
head pointing at them is cleared so a replacement can take over.
"""

from __future__ import annotations

from typing import Mapping

from contracts import (
    MemoryDerivationV1,
    MemoryInvalidationRecord,
    canonical_sha256,
)
from .store import LifeShadowStore


def _invalidation_id(
    *,
    life_id: str,
    derivation_id: str,
    invalidated_at_ms: int,
    reason: str,
    source_trigger_ref: str | None,
) -> str:
    return "miv_" + canonical_sha256(
        {
            "domain": "tiangong.life.invalidation-id.v1",
            "life_id": life_id,
            "derivation_id": derivation_id,
            "invalidated_at_ms": invalidated_at_ms,
            "reason": reason,
            "source_trigger_ref": source_trigger_ref,
        }
    )


def _still_supported(
    store: LifeShadowStore,
    child: MemoryDerivationV1,
    *,
    invalidated_ids: Mapping[str, object],
) -> bool:
    """True when the child keeps at least one active parent outside the cascade."""

    for parent_ref in store.list_derivation_parents(child.derivation_id):
        parent_id = parent_ref.parent_derivation_id
        if parent_id is None:
            continue
        if parent_id in invalidated_ids:
            continue
        if store.is_derivation_active(parent_id):
            return True
    return False


def invalidate_cascade(
    store: LifeShadowStore,
    *,
    derivation_id: str,
    reason: str = "corrected",
    invalidated_at_ms: int,
    source_trigger_ref: str | None = None,
) -> tuple[MemoryInvalidationRecord, ...]:
    """Invalidate a derivation and every descendant that loses support.

    The target receives ``reason``; descendants that lose all independent
    parent support receive ``stale``.  Already-invalidated targets are an
    idempotent no-op.  Returns the persisted invalidation records.
    """

    if reason not in {
        "corrected",
        "superseded",
        "stale",
        "privacy_erasure",
        "invalidated",
    }:
        raise ValueError("invalidation reason is invalid")
    root = store.get_memory_derivation(derivation_id)
    if root is None:
        raise ValueError("invalidation target derivation does not exist")
    if not store.is_derivation_active(derivation_id):
        return ()
    invalidated: dict[str, MemoryDerivationV1] = {}
    queue: list[MemoryDerivationV1] = [root]
    while queue:
        current = queue.pop(0)
        if current.derivation_id in invalidated:
            continue
        invalidated[current.derivation_id] = current
        for child in store.list_derivation_children(current.derivation_id):
            if child.derivation_id in invalidated:
                continue
            if reason != "privacy_erasure" and _still_supported(
                store, child, invalidated_ids=invalidated
            ):
                continue
            queue.append(child)

    records: list[MemoryInvalidationRecord] = []
    invalidated_ids = set(invalidated)
    for current_id, derivation in invalidated.items():
        current_reason = (
            reason if current_id == root.derivation_id else "stale"
        )
        descendants = tuple(
            sorted(
                child_id
                for child_id in invalidated_ids
                if child_id != current_id
            )
        )
        record = MemoryInvalidationRecord(
            invalidation_id=_invalidation_id(
                life_id=derivation.life_id,
                derivation_id=derivation.derivation_id,
                invalidated_at_ms=invalidated_at_ms,
                reason=current_reason,
                source_trigger_ref=source_trigger_ref,
            ),
            life_id=derivation.life_id,
            principal_ref=derivation.principal_ref,
            derivation_id=derivation.derivation_id,
            memory_id=derivation.memory_id,
            memory_revision=derivation.memory_revision,
            assertion_sha256=derivation.memory_assertion_sha256,
            reason=current_reason,
            source_trigger_ref=source_trigger_ref,
            invalidated_at_ms=invalidated_at_ms,
            descendant_derivation_ids=descendants,
            invalidation_sha256="0" * 64,
        ).with_computed_invalidation_sha256()
        store.put_memory_invalidation(record)
        store.clear_active_head(
            life_id=derivation.life_id,
            principal_ref=derivation.principal_ref,
            claim_key=derivation.claim_key,
            layer=derivation.layer,
            derivation_id=derivation.derivation_id,
        )
        records.append(record)
    return tuple(records)


__all__ = ["invalidate_cascade"]
