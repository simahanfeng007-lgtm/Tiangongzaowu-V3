"""P18-M3.8 freshness/revalidation policy for the canonical Known fact authority.

This module owns no source fetcher, tool, Runtime, scheduler, or persistence.
It only decides whether an already-verified Known record may be depended on
again without fresh reality evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from contracts.world_understanding.known import DirectKnownRecord, DerivedKnownRecord
from contracts.world_understanding.source import WorldSourceRef

KnownRecord = DirectKnownRecord | DerivedKnownRecord

# These source kinds represent reality that can change independently of the
# stored Known record. Reuse therefore requires a current source-version proof.
VOLATILE_SOURCE_KINDS = frozenset({
    "RUNTIME_ENVIRONMENT",
    "FACT_EXECUTION",
    "TOOL_RESULT",
    "FILESYSTEM",
    "GIT_CODE",
    "WEB_EXTERNAL",
    "DESKTOP_UI",
    "METRICS",
    "CHAIN_EVENT",
    "EXECUTION_INTEGRITY",
})


@dataclass(frozen=True, slots=True)
class KnownFreshnessDecision:
    reusable: bool
    requires_revalidation: bool
    stale: bool
    source_version_changed: bool
    revalidation_policy: str
    source_versions: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]


def source_key(ref: WorldSourceRef) -> str:
    """Stable source identity; revision/hash are deliberately excluded."""

    return f"{ref.source_kind}:{ref.object_id}"


def source_version(ref: WorldSourceRef) -> str:
    """Canonical version token for a provenance source."""

    if ref.object_revision is not None:
        return f"revision:{ref.object_revision}:{ref.sha256}"
    return f"sha256:{ref.sha256}"


def _direct_source(record: DirectKnownRecord) -> tuple[str, str, str]:
    key = f"{record.source_kind}:{record.source_native_id}"
    return key, f"sha256:{record.source_payload_hash}", record.source_kind


def record_source_versions(record: KnownRecord) -> tuple[tuple[str, str, str], ...]:
    """Return (source_key, stored_version, source_kind) without duplicates."""

    by_key: dict[str, tuple[str, str, str]] = {}
    for ref in record.provenance_refs:
        key = source_key(ref)
        by_key[key] = (key, source_version(ref), ref.source_kind)
    if isinstance(record, DirectKnownRecord):
        key, version, kind = _direct_source(record)
        by_key.setdefault(key, (key, version, kind))
    return tuple(by_key[key] for key in sorted(by_key))


def evaluate_known_freshness(
    record: KnownRecord,
    *,
    now_ms: int,
    current_source_versions: Mapping[str, str] | None = None,
    revalidated_source_keys: frozenset[str] = frozenset(),
) -> KnownFreshnessDecision:
    """Fail closed for stale or volatile dependency reuse.

    ``current_source_versions`` must come from a trusted reality observation.
    Matching a stored version counts as revalidation; a mismatch marks the old
    Known dependency stale. ``revalidated_source_keys`` supports trusted
    control planes that revalidate a source without exposing a version token.
    """

    now = int(now_ms)
    current = {str(key): str(value) for key, value in (current_source_versions or {}).items()}
    revalidated = {str(key) for key in revalidated_source_keys}
    reasons: list[str] = []
    stale = False
    changed = False

    observed_at = record.time.observed_at_ms
    if observed_at is None:
        reasons.append("missing_observed_at")

    if record.time.valid_until_ms is not None and now > int(record.time.valid_until_ms):
        stale = True
        reasons.append("validity_window_expired")

    versions = record_source_versions(record)
    volatile_keys = tuple(key for key, _version, kind in versions if kind in VOLATILE_SOURCE_KINDS)
    policy = "on_reuse" if volatile_keys else "ttl"

    for key, stored_version, _kind in versions:
        if key not in current:
            continue
        if current[key] != stored_version:
            stale = True
            changed = True
            reasons.append(f"source_version_changed:{key}")
        else:
            revalidated.add(key)

    for key in volatile_keys:
        if key not in revalidated:
            reasons.append(f"volatile_source_revalidation_required:{key}")

    # Expired records require fresh source evidence even for otherwise stable
    # sources. A fact with no source version proof cannot silently become fresh
    # merely because time passed.
    if stale and not versions:
        reasons.append("revalidation_evidence_unavailable")
    elif stale and versions:
        for key, _stored_version, _kind in versions:
            if key not in revalidated:
                reasons.append(f"stale_source_revalidation_required:{key}")

    # Deduplicate while keeping deterministic order.
    reasons = list(dict.fromkeys(reasons))
    requires = bool(reasons)
    return KnownFreshnessDecision(
        reusable=not requires,
        requires_revalidation=requires,
        stale=stale,
        source_version_changed=changed,
        revalidation_policy=policy,
        source_versions=tuple((key, version) for key, version, _kind in versions),
        reasons=tuple(reasons),
    )


__all__ = [
    "VOLATILE_SOURCE_KINDS",
    "KnownFreshnessDecision",
    "source_key",
    "source_version",
    "record_source_versions",
    "evaluate_known_freshness",
]
