"""Conservative object-retention analysis. P4 deliberately performs no deletion."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import canonical_sha256

from .object_store import ContentAddressedObjectStore, ObjectReference
from .store import GatewayStateStore, ObjectOwnerRecord


class ObjectGcError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectGcCandidate:
    object_id: str
    content_object_id: str
    content_sha256: str
    size_bytes: int
    created_at_ms: int
    age_ms: int
    content_reclaimable_if_applied: bool
    reason_code: str = "object_gc.unowned_expired"

    def canonical(self) -> dict[str, object]:
        return {
            "age_ms": self.age_ms,
            "content_object_id": self.content_object_id,
            "content_reclaimable_if_applied": self.content_reclaimable_if_applied,
            "content_sha256": self.content_sha256,
            "created_at_ms": self.created_at_ms,
            "object_id": self.object_id,
            "reason_code": self.reason_code,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ObjectGcDryRunReport:
    generated_at_ms: int
    minimum_unowned_age_ms: int
    reference_count: int
    active_owner_count: int
    revision_reference_count: int
    legal_hold_count: int
    candidate_count: int
    reclaimable_content_bytes: int
    candidates: tuple[ObjectGcCandidate, ...]
    dry_run: bool
    report_sha256: str

    def canonical(self, *, include_digest: bool = True) -> dict[str, object]:
        value = {
            "active_owner_count": self.active_owner_count,
            "candidate_count": self.candidate_count,
            "candidates": tuple(item.canonical() for item in self.candidates),
            "dry_run": self.dry_run,
            "generated_at_ms": self.generated_at_ms,
            "legal_hold_count": self.legal_hold_count,
            "minimum_unowned_age_ms": self.minimum_unowned_age_ms,
            "reclaimable_content_bytes": self.reclaimable_content_bytes,
            "reference_count": self.reference_count,
            "revision_reference_count": self.revision_reference_count,
        }
        if include_digest:
            value["report_sha256"] = self.report_sha256
        return value

    def has_valid_report_sha256(self) -> bool:
        return self.report_sha256 == canonical_sha256(
            self.canonical(include_digest=False)
        )


def build_object_gc_dry_run(
    object_store: ContentAddressedObjectStore,
    gateway_store: GatewayStateStore,
    *,
    now_ms: int,
    minimum_unowned_age_ms: int = 7 * 24 * 60 * 60 * 1000,
    legal_hold_object_ids: tuple[str, ...] = (),
) -> ObjectGcDryRunReport:
    return analyze_object_gc_dry_run(
        object_store.list_references(),
        object_store.list_revisions(),
        gateway_store.list_all_object_owners(),
        now_ms=now_ms,
        minimum_unowned_age_ms=minimum_unowned_age_ms,
        legal_hold_object_ids=legal_hold_object_ids,
    )


def analyze_object_gc_dry_run(
    references: tuple[ObjectReference, ...],
    revisions,
    owners: tuple[ObjectOwnerRecord, ...],
    *,
    now_ms: int,
    minimum_unowned_age_ms: int,
    legal_hold_object_ids: tuple[str, ...] = (),
) -> ObjectGcDryRunReport:
    if now_ms < 0 or minimum_unowned_age_ms < 0:
        raise ObjectGcError("object GC time policy is invalid")
    reference_by_id = {reference.object_id: reference for reference in references}
    if len(reference_by_id) != len(references):
        raise ObjectGcError("object GC input contains duplicate references")
    owner_ids: set[str] = set()
    for owner in owners:
        reference = reference_by_id.get(owner.object_id)
        if reference is None or reference.sha256 != owner.object_sha256:
            raise ObjectGcError("object owner is not bound to a verified reference")
        owner_ids.add(owner.object_id)
    revision_ids: set[str] = set()
    for revision in revisions:
        reference = reference_by_id.get(revision.object_id)
        if reference is None or reference.sha256 != revision.content_sha256:
            raise ObjectGcError("object revision is not bound to a verified reference")
        revision_ids.add(revision.object_id)
    legal_holds = set(legal_hold_object_ids)
    if len(legal_holds) != len(legal_hold_object_ids) or not legal_holds <= set(reference_by_id):
        raise ObjectGcError("object GC legal-hold set is invalid")

    preliminary: list[ObjectReference] = []
    for reference in sorted(references, key=lambda item: item.object_id):
        if reference.created_at_ms > now_ms:
            raise ObjectGcError("object reference was created in the future")
        if (
            reference.object_id in owner_ids
            or reference.object_id in revision_ids
            or reference.object_id in legal_holds
            or now_ms - reference.created_at_ms < minimum_unowned_age_ms
        ):
            continue
        preliminary.append(reference)
    candidate_ids = {reference.object_id for reference in preliminary}
    references_by_content: dict[str, set[str]] = {}
    for reference in references:
        references_by_content.setdefault(reference.sha256, set()).add(reference.object_id)
    candidates = tuple(
        ObjectGcCandidate(
            object_id=reference.object_id,
            content_object_id=reference.content_object_id,
            content_sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            created_at_ms=reference.created_at_ms,
            age_ms=now_ms - reference.created_at_ms,
            content_reclaimable_if_applied=(
                references_by_content[reference.sha256] <= candidate_ids
            ),
        )
        for reference in preliminary
    )
    reclaimable_by_digest = {
        candidate.content_sha256: candidate.size_bytes
        for candidate in candidates
        if candidate.content_reclaimable_if_applied
    }
    values = {
        "active_owner_count": len(owners),
        "candidate_count": len(candidates),
        "candidates": tuple(item.canonical() for item in candidates),
        "dry_run": True,
        "generated_at_ms": now_ms,
        "legal_hold_count": len(legal_holds),
        "minimum_unowned_age_ms": minimum_unowned_age_ms,
        "reclaimable_content_bytes": sum(reclaimable_by_digest.values()),
        "reference_count": len(references),
        "revision_reference_count": len(revisions),
    }
    return ObjectGcDryRunReport(
        generated_at_ms=now_ms,
        minimum_unowned_age_ms=minimum_unowned_age_ms,
        reference_count=len(references),
        active_owner_count=len(owners),
        revision_reference_count=len(revisions),
        legal_hold_count=len(legal_holds),
        candidate_count=len(candidates),
        reclaimable_content_bytes=sum(reclaimable_by_digest.values()),
        candidates=candidates,
        dry_run=True,
        report_sha256=canonical_sha256(values),
    )


__all__ = [
    "ObjectGcCandidate",
    "ObjectGcDryRunReport",
    "ObjectGcError",
    "analyze_object_gc_dry_run",
    "build_object_gc_dry_run",
]
