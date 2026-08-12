from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = runpy.run_path(str(ROOT / "scripts" / "p15-memory-qa-repair-v3.py"))
rewrite = BASE["rewrite"]
base_patch_coordinator = BASE["patch_coordinator"]
base_patch_invalidation = BASE["patch_invalidation"]
patch_gateway = BASE["patch_gateway"]


def patch_coordinator(text: str) -> str:
    text = base_patch_coordinator(text)
    old = '''        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError("correction assertion is missing")
            invalidations = ()
            if self._store.is_derivation_active(target_derivation_id):
                invalidations = invalidate_cascade(
                    self._store,
                    derivation_id=target_derivation_id,
                    reason="corrected",
                    invalidated_at_ms=created_at_ms,
                    source_trigger_ref=user_message_event_id,
                    preserve_derivation_ids=(existing.derivation_id,),
                )
            return assertion, existing, invalidations, False
'''
    new = '''        existing = self._store.get_memory_derivation(derivation_id)
        if existing is not None:
            assertion = self._store.get_memory_assertion(
                existing.memory_id, existing.memory_revision
            )
            if assertion is None:
                raise MemoryCoordinatorError("correction assertion is missing")
            target_was_active = self._store.is_derivation_active(
                target_derivation_id
            )
            invalidations = invalidate_cascade(
                self._store,
                derivation_id=target_derivation_id,
                reason="corrected",
                invalidated_at_ms=created_at_ms,
                source_trigger_ref=user_message_event_id,
                preserve_derivation_ids=(existing.derivation_id,),
            )
            if not target_was_active and not invalidations:
                raise MemoryCoordinatorError(
                    "correction target is already inactive"
                )
            return assertion, existing, invalidations, False
'''
    if new in text:
        return text
    if old not in text:
        raise RuntimeError("correct_claim retry anchor changed")
    return text.replace(old, new, 1)


def patch_invalidation(text: str) -> str:
    text = base_patch_invalidation(text)
    old = '''        for child in store.list_derivation_children(current.derivation_id):
            if child.derivation_id in invalidated or child.derivation_id in preserved:
                continue
            if reason != "privacy_erasure" and _still_supported(
'''
    new = '''        for child in store.list_derivation_children(current.derivation_id):
            if child.derivation_id in invalidated or child.derivation_id in preserved:
                continue
            if (
                reason == "corrected"
                and child.origin == "USER_EXPLICIT"
                and "corrected" in child.promotion_reason_codes
            ):
                continue
            if reason != "privacy_erasure" and _still_supported(
'''
    if new in text:
        return text
    if old not in text:
        raise RuntimeError("invalidation correction-replacement anchor changed")
    return text.replace(old, new, 1)


def main() -> int:
    changed: list[str] = []
    for relative, transform in (
        ("src/life_service/memory_coordinator.py", patch_coordinator),
        ("src/life_service/memory_invalidation.py", patch_invalidation),
        ("src/total_gateway/runtime.py", patch_gateway),
    ):
        if rewrite(relative, transform):
            changed.append(relative)
    print({"ok": True, "changed": changed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
